"""S3 backend error handling against botocore's Stubber (no network, no server).
Behaviour against a real server is covered by the ``s3`` tier on MinIO."""

from __future__ import annotations

import base64
import io

import boto3
import pytest
from botocore.stub import ANY, Stubber

from app.pipeline.errors import RetryExhausted
from app.pipeline.lake import IntegrityError, ObjectNotFound, build_object_store, sha256_hex
from app.pipeline.lake.s3 import S3ObjectStore
from app.pipeline.retry import RetryPolicy

KEY = "raw/news/year=2026/month=09/day=02/" + "c3" * 32 + ".json"
BODY = b'{"synthetic": true}'
SHA = sha256_hex(BODY)


@pytest.fixture()
def stubbed():
    # Placeholder credentials: the Stubber intercepts every call before signing matters.
    client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="stub", aws_secret_access_key="stub")
    sleeps = []
    store = S3ObjectStore(
        "quantpilot-test-stub", client=client, policy=RetryPolicy(max_attempts=3, base_delay=0.01, max_delay=0.02),
        sleep=sleeps.append,
    )
    with Stubber(client) as stub:
        yield store, stub, sleeps
        stub.assert_no_pending_responses()


def _put_params():
    return {
        "Bucket": "quantpilot-test-stub", "Key": KEY, "Body": ANY, "ContentType": "application/json",
        "IfNoneMatch": "*", "ChecksumSHA256": base64.b64encode(bytes.fromhex(SHA)).decode(),
        "Metadata": {"sha256": SHA},
    }


def test_put_sends_conditional_write_with_checksum(stubbed):
    store, stub, _ = stubbed
    stub.add_response("put_object", {"ETag": '"x"'}, _put_params())
    result = store.put_if_absent(KEY, BODY, content_type="application/json")
    assert result.created and result.uri == f"s3://quantpilot-test-stub/{KEY}"


def test_412_means_exists_and_reports_stored_hash(stubbed):
    store, stub, _ = stubbed
    stub.add_client_error("put_object", "PreconditionFailed", http_status_code=412, expected_params=_put_params())
    stub.add_response("head_object", {"ContentLength": 3, "Metadata": {"sha256": "f" * 64}}, {"Bucket": ANY, "Key": KEY})
    result = store.put_if_absent(KEY, BODY, content_type="application/json")
    assert not result.created and not result.identical and result.existing_sha256 == "f" * 64


def test_conditional_conflict_and_5xx_are_retried(stubbed):
    store, stub, sleeps = stubbed
    stub.add_client_error("put_object", "ConditionalRequestConflict", http_status_code=409)
    stub.add_client_error("put_object", "ServiceUnavailable", http_status_code=503)
    stub.add_response("put_object", {"ETag": '"x"'})
    assert store.put_if_absent(KEY, BODY).created
    assert len(sleeps) == 2


def test_retry_budget_is_bounded(stubbed):
    store, stub, sleeps = stubbed
    for _ in range(3):
        stub.add_client_error("put_object", "SlowDown", http_status_code=503)
    with pytest.raises(RetryExhausted) as info:
        store.put_if_absent(KEY, BODY)
    assert info.value.attempts == 3 and len(sleeps) == 2


def test_permanent_errors_are_not_retried(stubbed):
    from botocore.exceptions import ClientError

    store, stub, sleeps = stubbed
    stub.add_client_error("put_object", "AccessDenied", http_status_code=403)
    with pytest.raises(ClientError):
        store.put_if_absent(KEY, BODY)
    assert sleeps == []


def test_get_missing_and_corrupted(stubbed):
    store, stub, _ = stubbed
    stub.add_client_error("get_object", "NoSuchKey", http_status_code=404)
    with pytest.raises(ObjectNotFound):
        store.get(KEY)
    stub.add_response("get_object", {"Body": io.BytesIO(b"tampered"), "Metadata": {"sha256": SHA}})
    with pytest.raises(IntegrityError):
        store.get(KEY)


def test_head_missing_is_none(stubbed):
    store, stub, _ = stubbed
    stub.add_client_error("head_object", "404", http_status_code=404)
    assert store.head(KEY) is None


def test_list_follows_continuation_tokens(stubbed):
    store, stub, _ = stubbed
    stub.add_response("list_objects_v2", {"Contents": [{"Key": "raw/a", "Size": 1}], "IsTruncated": True,
                                          "NextContinuationToken": "t1"}, {"Bucket": ANY, "Prefix": "raw/"})
    stub.add_response("list_objects_v2", {"Contents": [{"Key": "raw/b", "Size": 2}], "IsTruncated": False},
                      {"Bucket": ANY, "Prefix": "raw/", "ContinuationToken": "t1"})
    assert [(i.key, i.size) for i in store.list("raw/")] == [("raw/a", 1), ("raw/b", 2)]


def test_factory_reads_settings(monkeypatch):
    from app.core.settings import Settings

    settings = Settings(LAKE_BACKEND="s3", LAKE_BUCKET="quantpilot-test-factory",
                        LAKE_ENDPOINT_URL="http://localhost:9999/", LAKE_REGION="us-east-1")
    store = build_object_store(settings)
    assert isinstance(store, S3ObjectStore)
    assert (store.bucket, store.endpoint_url) == ("quantpilot-test-factory", "http://localhost:9999")
    assert store.client.meta.endpoint_url == "http://localhost:9999"


@pytest.mark.parametrize("bucket", ["Quantpilot", "ab", "-lake", "lake-", "a..b", "under_score"])
def test_bucket_names_are_validated(bucket):
    from pydantic import ValidationError

    from app.core.settings import Settings

    with pytest.raises(ValidationError):
        Settings(LAKE_BUCKET=bucket)
