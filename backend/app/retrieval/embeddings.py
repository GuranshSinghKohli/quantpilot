"""Embedding providers. Tests use FakeEmbeddingProvider; no network in unit tests."""

from __future__ import annotations

import hashlib
import math
import time
from typing import List, Protocol, Sequence

from app.core.settings import get_settings
from app.domain.models import EMBEDDING_DIMENSION
from app.retrieval.metrics import RETRIEVAL_METRICS


class EmbeddingError(RuntimeError):
    """Provider failed after bounded retries, or the failure is permanent."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class EmbeddingProvider(Protocol):
    model_name: str
    model_version: str
    dimension: int

    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]: ...

    def embed_query(self, query: str) -> List[float]: ...


def _l2_normalize(values: List[float]) -> List[float]:
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


class FakeEmbeddingProvider:
    """Lexical hashing trick. Deterministic; not a semantic-quality claim."""

    def __init__(
        self,
        *,
        dimension: int = EMBEDDING_DIMENSION,
        model_name: str = "fake-lexical-hash",
        model_version: str = "v1",
    ) -> None:
        self.dimension = dimension
        self.model_name = model_name
        self.model_version = model_version

    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        return [self._embed_one(text) for text in texts]

    def embed_query(self, query: str) -> List[float]:
        return self._embed_one(query)

    def _embed_one(self, text: str) -> List[float]:
        vec = [0.0] * self.dimension
        tokens = [tok for tok in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split() if tok]
        if not tokens:
            tokens = ["empty"]
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dimension
            vec[idx] += 1.0
            vec[(idx + 17) % self.dimension] += 0.25
        return _l2_normalize(vec)


class OpenAIEmbeddingProvider:
    def __init__(self) -> None:
        settings = get_settings()
        key = settings.OPENAI_API_KEY.get_secret_value().strip()
        if not key:
            raise EmbeddingError("OPENAI_API_KEY is not configured", retryable=False)
        from openai import OpenAI

        self._client = OpenAI(api_key=key, timeout=settings.EMBEDDING_TIMEOUT_SECONDS)
        self.model_name = settings.EMBEDDING_MODEL
        self.model_version = settings.EMBEDDING_MODEL_VERSION
        self.dimension = settings.EMBEDDING_DIMENSION
        self._retries = settings.EMBEDDING_MAX_RETRIES

    def embed_query(self, query: str) -> List[float]:
        return self.embed_texts([query])[0]

    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []
        last: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                response = self._client.embeddings.create(model=self.model_name, input=list(texts))
                rows = sorted(response.data, key=lambda item: item.index)
                vectors = [list(item.embedding) for item in rows]
                if any(len(vec) != self.dimension for vec in vectors):
                    raise EmbeddingError("embedding dimension mismatch", retryable=False)
                return [_l2_normalize(vec) for vec in vectors]
            except Exception as exc:
                last = exc
                retryable = _is_retryable(exc)
                RETRIEVAL_METRICS.inc(
                    "embedding_failures_total",
                    1,
                    operation="embed",
                    status="retry" if retryable else "failed",
                    provider="openai",
                )
                if not retryable or attempt >= self._retries:
                    raise EmbeddingError(str(exc), retryable=retryable) from exc
                time.sleep(min(8.0, 0.5 * (2**attempt)))
        raise EmbeddingError(str(last), retryable=True)


def _is_retryable(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "401" in text or "403" in text or "invalid" in name:
        return False
    return any(token in name or token in text for token in ("rate", "timeout", "connect", "503", "502", "429", "500"))


def get_embedding_provider() -> EmbeddingProvider:
    settings = get_settings()
    if settings.EMBEDDING_PROVIDER == "fake":
        return FakeEmbeddingProvider(
            dimension=settings.EMBEDDING_DIMENSION,
            model_version=settings.EMBEDDING_MODEL_VERSION,
        )
    return OpenAIEmbeddingProvider()
