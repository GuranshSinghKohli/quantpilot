"""Deterministic keys used for deduplication and idempotency."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src"}


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_url(url: str) -> str:
    """Canonical form so the same document fetched via different links dedups.

    Lowercases scheme and host, drops fragments, default ports, tracking
    parameters (utm_*, gclid, ...), sorts the query, and strips a trailing slash.
    """
    parts = urlsplit(url.strip())
    if not parts.scheme or not parts.netloc:
        raise ValueError(f"Not an absolute URL: {url!r}")
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, host, path, urlencode(query), ""))


def url_hash(url: str) -> str:
    return sha256_hex(normalize_url(url))


def content_hash(text: str) -> str:
    """Whitespace-insensitive hash of document or excerpt text."""
    return sha256_hex(" ".join(text.split()))


def event_dedup_key(
    event_type: str,
    primary_entity_id: Optional[uuid.UUID],
    occurred_at: Optional[datetime],
    primary_source_url_hash: Optional[str],
) -> str:
    """Same kind of event, about the same entity, on the same UTC day, from the same
    primary source, is the same event."""
    day = ""
    if occurred_at is not None:
        ts = occurred_at if occurred_at.tzinfo else occurred_at.replace(tzinfo=timezone.utc)
        day = ts.astimezone(timezone.utc).date().isoformat()
    parts = [event_type, str(primary_entity_id or ""), day, primary_source_url_hash or ""]
    return sha256_hex("|".join(parts))


def event_source_identity(source_key: str, external_id: str, event_type: str) -> str:
    """Stable vendor identity. Title is not part of the key."""
    return sha256_hex("|".join([source_key.strip(), external_id.strip(), event_type.strip()]))


def event_evidence_identity(source_id: uuid.UUID, evidence_id: uuid.UUID, event_type: str) -> str:
    return sha256_hex("|".join([str(source_id), str(evidence_id), event_type.strip()]))


def document_identity(source_key: Optional[str], external_id: Optional[str], url_hash_value: str) -> str:
    """Document identity is never title. Prefer vendor id, else the source URL hash."""
    if source_key and external_id:
        return sha256_hex(f"{source_key.strip()}|{external_id.strip()}")
    return sha256_hex(url_hash_value)


def document_uuid(identity_key: str) -> uuid.UUID:
    return uuid.UUID(bytes=hashlib.sha256(identity_key.encode("utf-8")).digest()[:16])


def chunk_identity(document_id: uuid.UUID, document_content_hash: str, chunk_index: int) -> str:
    return sha256_hex(f"{document_id}:{document_content_hash}:{chunk_index}")
