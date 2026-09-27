"""The object-store interface shared by the local and S3 backends.

Objects are write-once: ``put_if_absent`` never replaces an existing object.
If the key is taken it reports ``created=False`` with the stored object's hash,
and the caller decides what differing bytes mean (the lake-writer keeps the
first landed copy and logs the difference).
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional

from app.pipeline.errors import PipelineError


class LakeError(PipelineError):
    pass


class InvalidKey(LakeError, ValueError):
    pass


class ObjectNotFound(LakeError):
    def __init__(self, uri: str) -> None:
        super().__init__(f"no object at {uri}")
        self.uri = uri


class IntegrityError(LakeError):
    """Stored bytes do not match the hash recorded when they were written."""


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class PutResult:
    key: str
    uri: str
    created: bool
    sha256: str  # of the bytes offered
    size: int
    existing_sha256: Optional[str] = None  # set when created is False

    @property
    def identical(self) -> bool:
        """True when the stored object holds exactly the offered bytes."""
        return self.created or self.existing_sha256 == self.sha256


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    size: int
    sha256: Optional[str] = None  # None in listings; set by head()


class ObjectStore(ABC):
    backend: str

    @abstractmethod
    def put_if_absent(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> PutResult:
        ...

    @abstractmethod
    def get(self, key: str) -> bytes:
        """The object's bytes. Raises ``ObjectNotFound`` or ``IntegrityError``."""

    @abstractmethod
    def head(self, key: str) -> Optional[ObjectInfo]:
        ...

    def exists(self, key: str) -> bool:
        return self.head(key) is not None

    @abstractmethod
    def list(self, prefix: str = "") -> Iterator[ObjectInfo]:
        """Objects whose key starts with ``prefix``, in lexicographic key order."""

    @abstractmethod
    def uri(self, key: str) -> str:
        ...

    @abstractmethod
    def check(self) -> Dict[str, Any]:
        """Connectivity and configuration facts for ``lake check`` / pipeline status."""
