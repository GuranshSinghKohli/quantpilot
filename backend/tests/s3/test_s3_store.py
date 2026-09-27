"""The object-store contract on a real S3 API, plus S3-only guarantees."""

import pytest

from tests.unit.pipeline.test_lake_store import (  # noqa: F401
    BODY,
    KEY,
    test_concurrent_writers_create_exactly_once,
    test_empty_object_round_trips,
    test_get_head_exists,
    test_invalid_keys_are_rejected_before_any_io,
    test_list_is_prefix_filtered_and_sorted,
    test_put_creates_then_refuses_to_overwrite,
    test_write_once_probe,
)


@pytest.fixture()
def store(s3_store):
    return s3_store


def test_bucket_is_versioned(store):
    assert store.check()["versioning"] == "Enabled"
    assert store.ensure_bucket()["created"] is False  # idempotent


def test_refused_overwrite_leaves_a_single_version(store):
    store.put_if_absent(KEY, BODY)
    store.put_if_absent(KEY, b"attempted overwrite")
    versions = store.client.list_object_versions(Bucket=store.bucket, Prefix=KEY).get("Versions", [])
    assert len(versions) == 1


def test_objects_carry_sha256_metadata(store):
    result = store.put_if_absent(KEY, BODY, content_type="application/json")
    head = store.client.head_object(Bucket=store.bucket, Key=KEY)
    assert head["Metadata"]["sha256"] == result.sha256
    assert head["ContentType"] == "application/json"
    assert store.uri(KEY) == f"s3://{store.bucket}/{KEY}"


def test_listing_pages_past_1000_keys(store):
    keys = [f"raw/company/year=2026/month=09/day=02/{n:064x}.json" for n in range(1005)]
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(16) as pool:
        list(pool.map(lambda k: store.put_if_absent(k, b"{}"), keys))
    assert [i.key for i in store.list("raw/company/")] == keys
