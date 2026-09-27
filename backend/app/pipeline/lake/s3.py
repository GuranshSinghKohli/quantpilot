"""S3 backend (AWS S3 or MinIO) via boto3.

Write-once comes from a conditional put (``If-None-Match: *``): the server
rejects the write with 412 if the key exists, so an object is never replaced.
Bucket versioning is a second guard, not the mechanism. Each object carries its
SHA-256 as user metadata (``x-amz-meta-sha256``) and as the S3 checksum, so
reads are verified and a refused duplicate can be compared cheaply.

Credentials come from the standard AWS chain (environment variables, shared
config/profile, or an instance role); nothing here reads or stores secrets.
"""

from __future__ import annotations

import base64
import time
from typing import Any, Callable, Dict, Iterator, Optional

from app.pipeline.lake.base import (
    IntegrityError,
    ObjectInfo,
    ObjectNotFound,
    ObjectStore,
    PutResult,
    sha256_hex,
)
from app.pipeline.lake.keys import validate_key, validate_prefix
from app.pipeline.retry import RetryPolicy, call_with_retry, is_transient

_META_SHA = "sha256"
_EXISTS_CODES = {"PreconditionFailed"}
_MISSING_CODES = {"NoSuchKey", "404", "NotFound"}
# Another conditional write to the same key is in flight; retrying resolves it.
_RETRYABLE_CODES = {"ConditionalRequestConflict", "SlowDown", "RequestTimeout", "InternalError"}


def error_code(error: BaseException) -> Optional[str]:
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        return (response.get("Error") or {}).get("Code")
    return None


def _status(error: BaseException) -> Optional[int]:
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        return (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    return None


def classify(error: BaseException) -> bool:
    return error_code(error) in _RETRYABLE_CODES or is_transient(error)


def make_client(*, endpoint_url: Optional[str], region: str):
    import boto3
    from botocore.config import Config

    config = Config(
        # call_with_retry owns retries so attempts and backoff are logged once.
        retries={"total_max_attempts": 1},
        connect_timeout=5,
        read_timeout=30,
        s3={"addressing_style": "path" if endpoint_url else "auto"},
    )
    return boto3.client("s3", endpoint_url=endpoint_url or None, region_name=region, config=config)


class S3ObjectStore(ObjectStore):
    backend = "s3"

    def __init__(
        self,
        bucket: str,
        *,
        client=None,
        endpoint_url: Optional[str] = None,
        region: str = "us-east-1",
        policy: Optional[RetryPolicy] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.bucket = bucket
        self.region = region
        self.endpoint_url = endpoint_url or None
        self.client = client or make_client(endpoint_url=self.endpoint_url, region=region)
        self.policy = policy or RetryPolicy()
        self._sleep = sleep

    def _call(self, operation: str, fn: Callable[[], Any]) -> Any:
        return call_with_retry(fn, operation=f"lake.s3.{operation}", policy=self.policy, classify=classify, sleep=self._sleep)

    def put_if_absent(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> PutResult:
        from botocore.exceptions import ClientError

        validate_key(key)
        digest = sha256_hex(data)
        checksum = base64.b64encode(bytes.fromhex(digest)).decode("ascii")
        try:
            self._call(
                "put",
                lambda: self.client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=data,
                    ContentType=content_type,
                    IfNoneMatch="*",
                    ChecksumSHA256=checksum,
                    Metadata={_META_SHA: digest},
                ),
            )
        except ClientError as exc:
            if error_code(exc) not in _EXISTS_CODES and _status(exc) != 412:
                raise
            return PutResult(key, self.uri(key), False, digest, len(data), self._stored_sha(key))
        return PutResult(key, self.uri(key), True, digest, len(data))

    def _stored_sha(self, key: str) -> str:
        info = self.head(key)
        if info is None:
            raise ObjectNotFound(self.uri(key))
        return info.sha256 or sha256_hex(self.get(key))

    def get(self, key: str) -> bytes:
        from botocore.exceptions import ClientError

        validate_key(key)

        def fetch():
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            return response, response["Body"].read()

        try:
            response, data = self._call("get", fetch)
        except ClientError as exc:
            if error_code(exc) in _MISSING_CODES:
                raise ObjectNotFound(self.uri(key)) from None
            raise
        recorded = (response.get("Metadata") or {}).get(_META_SHA)
        if recorded and recorded != sha256_hex(data):
            raise IntegrityError(f"{self.uri(key)}: stored sha256 {recorded} does not match the bytes read")
        return data

    def head(self, key: str) -> Optional[ObjectInfo]:
        from botocore.exceptions import ClientError

        validate_key(key)
        try:
            response = self._call("head", lambda: self.client.head_object(Bucket=self.bucket, Key=key))
        except ClientError as exc:
            if error_code(exc) in _MISSING_CODES or _status(exc) == 404:
                return None
            raise
        return ObjectInfo(key, int(response["ContentLength"]), (response.get("Metadata") or {}).get(_META_SHA))

    def list(self, prefix: str = "") -> Iterator[ObjectInfo]:
        validate_prefix(prefix)
        token: Optional[str] = None
        while True:
            params: Dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                params["ContinuationToken"] = token
            page = self._call("list", lambda: self.client.list_objects_v2(**params))
            for item in page.get("Contents", []):
                yield ObjectInfo(item["Key"], int(item["Size"]))
            if not page.get("IsTruncated"):
                return
            token = page["NextContinuationToken"]

    def uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"

    def ensure_bucket(self) -> Dict[str, Any]:
        """Create the bucket if missing and enable versioning. Idempotent."""
        from botocore.exceptions import ClientError

        try:
            self._call("head_bucket", lambda: self.client.head_bucket(Bucket=self.bucket))
            created = False
        except ClientError as exc:
            if _status(exc) != 404 and error_code(exc) not in {"404", "NoSuchBucket", "NotFound"}:
                raise
            params: Dict[str, Any] = {"Bucket": self.bucket}
            if self.region != "us-east-1":
                params["CreateBucketConfiguration"] = {"LocationConstraint": self.region}
            self._call("create_bucket", lambda: self.client.create_bucket(**params))
            created = True
        self._call(
            "put_bucket_versioning",
            lambda: self.client.put_bucket_versioning(
                Bucket=self.bucket, VersioningConfiguration={"Status": "Enabled"}
            ),
        )
        return {"bucket": self.bucket, "created": created, "versioning": "Enabled"}

    def check(self) -> Dict[str, Any]:
        self._call("head_bucket", lambda: self.client.head_bucket(Bucket=self.bucket))
        versioning = self._call("get_bucket_versioning", lambda: self.client.get_bucket_versioning(Bucket=self.bucket))
        return {
            "backend": self.backend,
            "bucket": self.bucket,
            "endpoint_url": self.endpoint_url or "aws",
            "region": self.region,
            "versioning": versioning.get("Status", "Disabled"),
        }
