"""S3 tier: the object-store contract against a real S3 API (MinIO locally).

Set TEST_S3_ENDPOINT (e.g. http://localhost:9000) plus TEST_S3_ACCESS_KEY_ID /
TEST_S3_SECRET_ACCESS_KEY (falling back to the standard AWS_* variables).
Without TEST_S3_ENDPOINT every test here is skipped. Each test gets its own
``quantpilot-test-<uuid>`` bucket, emptied (all versions) and deleted afterwards;
nothing touches LAKE_BUCKET.
"""

from __future__ import annotations

import os
import uuid

import pytest

TEST_S3_ENDPOINT = os.getenv("TEST_S3_ENDPOINT", "").strip()
TEST_BUCKET_PREFIX = "quantpilot-test-"


def pytest_collection_modifyitems(config, items):
    here = os.path.dirname(__file__)
    skip = pytest.mark.skip(reason="TEST_S3_ENDPOINT not set")
    for item in items:
        if str(item.fspath).startswith(here):
            item.add_marker(pytest.mark.s3)
            if not TEST_S3_ENDPOINT:
                item.add_marker(skip)


@pytest.fixture(scope="session")
def s3_client():
    import boto3

    from app.pipeline.lake.s3 import make_client

    key_id = os.getenv("TEST_S3_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY_ID")
    secret = os.getenv("TEST_S3_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY")
    if not (key_id and secret):
        pytest.fail("TEST_S3_ENDPOINT is set but no credentials: set TEST_S3_ACCESS_KEY_ID / TEST_S3_SECRET_ACCESS_KEY")
    client = make_client(endpoint_url=TEST_S3_ENDPOINT, region="us-east-1")
    return boto3.client(
        "s3",
        endpoint_url=TEST_S3_ENDPOINT,
        region_name="us-east-1",
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        config=client.meta.config,
    )


def _drop_bucket(client, bucket: str) -> None:
    assert bucket.startswith(TEST_BUCKET_PREFIX), f"refusing to delete non-test bucket {bucket}"
    paginator = client.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=bucket):
        doomed = [
            {"Key": v["Key"], "VersionId": v["VersionId"]}
            for v in page.get("Versions", []) + page.get("DeleteMarkers", [])
        ]
        if doomed:
            client.delete_objects(Bucket=bucket, Delete={"Objects": doomed, "Quiet": True})
    client.delete_bucket(Bucket=bucket)


@pytest.fixture()
def s3_store(s3_client):
    from app.pipeline.lake.s3 import S3ObjectStore
    from app.pipeline.retry import RetryPolicy

    bucket = f"{TEST_BUCKET_PREFIX}{uuid.uuid4().hex[:20]}"
    store = S3ObjectStore(
        bucket,
        client=s3_client,
        endpoint_url=TEST_S3_ENDPOINT,
        policy=RetryPolicy(max_attempts=5, base_delay=0.05, max_delay=0.5),
    )
    store.ensure_bucket()
    try:
        yield store
    finally:
        _drop_bucket(s3_client, bucket)
