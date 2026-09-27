"""Object-store contract, run here on ``LocalObjectStore`` (tmp dir) and re-run
on MinIO by ``tests/s3/test_s3_store.py`` through the ``store`` fixture."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.pipeline.lake import (
    InvalidKey,
    LocalObjectStore,
    ObjectNotFound,
    probe_write_once,
    sha256_hex,
)

KEY = "raw/market/year=2026/month=09/day=02/" + "a1" * 32 + ".json"
BODY = b'{"synthetic": true, "n": 1}'


@pytest.fixture()
def store(tmp_path):
    return LocalObjectStore(tmp_path / "lake")


def test_put_creates_then_refuses_to_overwrite(store):
    first = store.put_if_absent(KEY, BODY, content_type="application/json")
    assert first.created and first.identical
    assert (first.sha256, first.size) == (sha256_hex(BODY), len(BODY))

    again = store.put_if_absent(KEY, BODY)
    assert not again.created and again.identical and again.existing_sha256 == sha256_hex(BODY)

    changed = store.put_if_absent(KEY, b'{"synthetic": true, "n": 2}')
    assert not changed.created and not changed.identical
    assert changed.existing_sha256 == sha256_hex(BODY)
    assert store.get(KEY) == BODY


def test_get_head_exists(store):
    assert store.head(KEY) is None and not store.exists(KEY)
    with pytest.raises(ObjectNotFound):
        store.get(KEY)
    store.put_if_absent(KEY, BODY)
    info = store.head(KEY)
    assert (info.key, info.size, info.sha256) == (KEY, len(BODY), sha256_hex(BODY))
    assert store.exists(KEY)
    assert store.get(KEY) == BODY


def test_empty_object_round_trips(store):
    key = "raw/news/year=2026/month=09/day=02/" + "0e" * 32 + ".json"
    assert store.put_if_absent(key, b"").created
    assert store.get(key) == b""


def test_list_is_prefix_filtered_and_sorted(store):
    keys = [
        "raw/news/year=2026/month=09/day=02/" + "02" * 32 + ".json",
        "raw/news/year=2026/month=09/day=01/" + "01" * 32 + ".json",
        "raw/market/year=2026/month=09/day=01/" + "03" * 32 + ".json",
        "_manifests/processing/00000000-0000-4000-8000-000000000001.json",
    ]
    for key in keys:
        store.put_if_absent(key, key.encode())
    assert [i.key for i in store.list("raw/news/")] == sorted(keys[:2])
    assert [i.key for i in store.list("raw/ne")] == sorted(keys[:2])
    assert [i.key for i in store.list("raw/")] == sorted(keys[:3])
    assert [i.key for i in store.list()] == sorted(keys)
    assert list(store.list("processed/")) == []
    assert {i.size for i in store.list("raw/market/")} == {len(keys[2])}


def test_invalid_keys_are_rejected_before_any_io(store):
    for bad in ("../escape.json", "/abs.json", "raw/.hidden"):
        with pytest.raises(InvalidKey):
            store.put_if_absent(bad, BODY)
        with pytest.raises(InvalidKey):
            store.get(bad)


def test_concurrent_writers_create_exactly_once(store):
    barrier = threading.Barrier(8)

    def write(n: int):
        barrier.wait()
        return store.put_if_absent(KEY, BODY if n % 2 else BODY + b" ")

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(write, range(8)))
    assert sum(r.created for r in results) == 1
    winner = next(r for r in results if r.created)
    assert store.get(KEY) == (BODY if winner.sha256 == sha256_hex(BODY) else BODY + b" ")
    assert all(r.existing_sha256 == winner.sha256 for r in results if not r.created)


def test_write_once_probe(store):
    assert probe_write_once(store)["ok"]
    assert probe_write_once(store)["ok"]  # repeatable


# --- local backend specifics ---------------------------------------------------


def test_local_objects_are_read_only_and_leave_no_staging_files(tmp_path):
    store = LocalObjectStore(tmp_path)
    store.put_if_absent(KEY, BODY)
    store.put_if_absent(KEY, b"other")
    path = tmp_path.joinpath(*KEY.split("/"))
    assert not os.access(path, os.W_OK) or os.geteuid() == 0
    assert [p.name for p in path.parent.iterdir()] == [path.name]
    assert store.uri(KEY) == path.resolve().as_uri()


def test_local_list_ignores_foreign_dotfiles(tmp_path):
    store = LocalObjectStore(tmp_path)
    store.put_if_absent(KEY, BODY)
    (tmp_path / "raw" / ".DS_Store").write_bytes(b"x")
    assert [i.key for i in store.list()] == [KEY]
