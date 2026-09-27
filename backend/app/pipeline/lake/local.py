"""Filesystem backend with the same key layout as S3. Used by unit and Spark
tests and for running the pipeline without MinIO.

Write-once comes from ``os.link``: the bytes are staged in a ``.tmp-*`` file in
the target directory, fsynced, then hard-linked to the final name. ``link``
fails if the name exists, so concurrent writers cannot replace each other and
readers never see a partial object. Final files are made read-only.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Union

from app.pipeline.lake.base import ObjectInfo, ObjectNotFound, ObjectStore, PutResult, sha256_hex
from app.pipeline.lake.keys import validate_key, validate_prefix

_STAGING = ".tmp-"


class LocalObjectStore(ObjectStore):
    backend = "local"

    def __init__(self, root: Union[str, Path]) -> None:
        self.root = Path(root).expanduser().resolve()

    def _path(self, key: str) -> Path:
        return self.root.joinpath(*validate_key(key).split("/"))

    def put_if_absent(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> PutResult:
        path = self._path(key)
        digest = sha256_hex(data)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, staging = tempfile.mkstemp(prefix=_STAGING, dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(staging, 0o444)
            try:
                os.link(staging, path)
            except FileExistsError:
                return PutResult(key, self.uri(key), False, digest, len(data), sha256_hex(path.read_bytes()))
        finally:
            os.unlink(staging)
        return PutResult(key, self.uri(key), True, digest, len(data))

    def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return path.read_bytes()
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            raise ObjectNotFound(self.uri(key)) from None

    def head(self, key: str) -> Optional[ObjectInfo]:
        path = self._path(key)
        if not path.is_file():
            return None
        data = path.read_bytes()
        return ObjectInfo(key, len(data), sha256_hex(data))

    def list(self, prefix: str = "") -> Iterator[ObjectInfo]:
        validate_prefix(prefix)
        directory = prefix.rsplit("/", 1)[0] if "/" in prefix else ""
        base = self.root.joinpath(*directory.split("/")) if directory else self.root
        if not base.is_dir():
            return iter(())
        found = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if name.startswith("."):
                    continue
                full = Path(dirpath, name)
                key = full.relative_to(self.root).as_posix()
                if key.startswith(prefix):
                    found.append(ObjectInfo(key, full.stat().st_size))
        return iter(sorted(found, key=lambda info: info.key))

    def uri(self, key: str) -> str:
        return self._path(key).as_uri()

    def check(self) -> Dict[str, Any]:
        return {
            "backend": self.backend,
            "root": self.root.as_posix(),
            "exists": self.root.is_dir(),
            "writable": os.access(self.root if self.root.is_dir() else self.root.parent, os.W_OK),
        }
