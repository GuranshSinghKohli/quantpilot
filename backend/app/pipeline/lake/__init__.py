"""Lake storage: one write-once object-store interface, two backends.

``S3ObjectStore`` (boto3) targets AWS S3 or MinIO; ``LocalObjectStore`` keeps
the same key layout on disk for tests and MinIO-less runs. Pick one with
``LAKE_BACKEND``; ``build_object_store`` reads the rest from settings.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from app.pipeline.lake.base import (
    IntegrityError,
    InvalidKey,
    LakeError,
    ObjectInfo,
    ObjectNotFound,
    ObjectStore,
    PutResult,
    sha256_hex,
)
from app.pipeline.lake.local import LocalObjectStore

__all__ = [
    "IntegrityError",
    "InvalidKey",
    "LakeError",
    "LocalObjectStore",
    "ObjectInfo",
    "ObjectNotFound",
    "ObjectStore",
    "PutResult",
    "build_object_store",
    "probe_write_once",
    "sha256_hex",
]

PROBE_KEY = "_health/write-once-probe.json"


def build_object_store(settings=None) -> ObjectStore:
    from app.core.settings import get_settings
    from app.pipeline.retry import RetryPolicy

    settings = settings or get_settings()
    if settings.LAKE_BACKEND == "local":
        return LocalObjectStore(settings.LAKE_LOCAL_ROOT)
    from app.pipeline.lake.s3 import S3ObjectStore

    return S3ObjectStore(
        settings.LAKE_BUCKET,
        endpoint_url=settings.LAKE_ENDPOINT_URL or None,
        region=settings.LAKE_REGION,
        policy=RetryPolicy.from_settings(settings),
    )


def probe_write_once(store: ObjectStore) -> Dict[str, Any]:
    """Prove the backend refuses to overwrite: write a fixed probe object, then
    try to replace it with different bytes and confirm the original survives.
    Some S3-compatible servers silently ignore ``If-None-Match``; this catches that."""
    original = json.dumps({"probe": "write-once", "version": 1}, sort_keys=True).encode()
    first = store.put_if_absent(PROBE_KEY, original, content_type="application/json")
    replacement = store.put_if_absent(PROBE_KEY, b'{"probe": "overwrite-attempt"}', content_type="application/json")
    stored = store.get(PROBE_KEY)
    ok = (not replacement.created) and stored == original and first.identical
    return {
        "key": PROBE_KEY,
        "uri": store.uri(PROBE_KEY),
        "first_write_created": first.created,
        "overwrite_refused": not replacement.created,
        "original_intact": stored == original,
        "ok": ok,
    }
