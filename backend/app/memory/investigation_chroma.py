"""Chroma collection for Evidence Ledger semantic search (FR-10)."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.utils import embedding_functions

from app.memory.chroma_store import CHROMA_DIR, DATA_DIR, _normalize_metadata
from app.observability.logger import get_logger, log_event

logger = get_logger("investigation_chroma")

COLLECTION_NAME = "investigation_ledger"
_collection = None


def _get_embedding_collection():
    global _collection
    if _collection is not None:
        return _collection

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY required for investigation embeddings")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    embedding_fn = embedding_functions.OpenAIEmbeddingFunction(
        api_key=api_key,
        model_name="text-embedding-3-small",
    )
    _collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=embedding_fn,
    )
    return _collection


def upsert_investigation(
    *,
    owner_key: str,
    investigation_id: int,
    ticker: str,
    document: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    collection = _get_embedding_collection()
    doc_id = f"inv_{investigation_id}"
    meta = _normalize_metadata(
        {
            "owner_key": owner_key,
            "investigation_id": investigation_id,
            "ticker": ticker.upper(),
            **(metadata or {}),
        }
    )
    try:
        collection.upsert(ids=[doc_id], documents=[document], metadatas=[meta])
        log_event(
            logger,
            logging.INFO,
            "Indexed investigation for search",
            investigation_id=investigation_id,
            ticker=ticker.upper(),
        )
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "Investigation upsert failed",
            investigation_id=investigation_id,
            error=str(exc),
        )
        raise
    return doc_id


def search_investigation_ids(
    query_text: str,
    *,
    owner_key: str,
    n_results: int = 8,
) -> List[int]:
    collection = _get_embedding_collection()
    results = collection.query(
        query_texts=[query_text],
        n_results=max(1, min(20, n_results)),
        where={"owner_key": owner_key},
    )
    metadatas = (results.get("metadatas") or [[]])[0]
    ids: List[int] = []
    for meta in metadatas:
        if not meta:
            continue
        raw = meta.get("investigation_id")
        try:
            inv_id = int(raw)
        except (TypeError, ValueError):
            continue
        if inv_id not in ids:
            ids.append(inv_id)
    return ids
