import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.utils import embedding_functions

from app.observability.logger import get_logger, log_event

chroma_logger = get_logger("chroma_store")

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
CHROMA_DIR = DATA_DIR / "chroma_db"
COLLECTION_NAME = "stock_analyses"

_client: Optional[chromadb.PersistentClient] = None
_collection = None


def _get_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return _client


def _get_embedding_collection():
    """Collection with OpenAI embeddings — required for save and semantic search."""
    global _collection
    if _collection is not None:
        return _collection

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY is required for ChromaDB embeddings (text-embedding-3-small)."
        )

    embedding_fn = embedding_functions.OpenAIEmbeddingFunction(
        api_key=api_key,
        model_name="text-embedding-3-small",
    )

    _collection = _get_client().get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=embedding_fn,
    )
    return _collection


def _get_read_collection():
    """Read existing collection without requiring embedding function."""
    client = _get_client()
    try:
        return client.get_collection(name=COLLECTION_NAME)
    except Exception:
        return None


def _normalize_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    for key, value in metadata.items():
        if value is None:
            normalized[key] = ""
        elif isinstance(value, (str, int, float, bool)):
            normalized[key] = value
        else:
            normalized[key] = str(value)
    return normalized


def save_report(ticker: str, report_dict: Dict[str, Any], metadata: Dict[str, Any]) -> str:
    collection = _get_embedding_collection()
    timestamp = metadata.get("timestamp") or datetime.now(timezone.utc).isoformat()
    doc_id = f"{ticker.upper()}_{uuid.uuid4().hex[:12]}"

    meta = _normalize_metadata(
        {
            "ticker": ticker.upper(),
            "timestamp": timestamp,
            "recommendation": metadata.get("recommendation", ""),
            "risk_level": metadata.get("risk_level", ""),
        }
    )

    document = json.dumps(report_dict, default=str)
    try:
        collection.add(ids=[doc_id], documents=[document], metadatas=[meta])
        log_event(
            chroma_logger,
            logging.INFO,
            "ChromaDB operation",
            operation="save",
            ticker=ticker.upper(),
            success=True,
        )
        return doc_id
    except Exception as exc:
        log_event(
            chroma_logger,
            logging.ERROR,
            "ChromaDB operation",
            operation="save",
            ticker=ticker.upper(),
            success=False,
            error=str(exc),
        )
        raise


def search_similar(query_text: str, n_results: int = 3) -> List[Dict[str, Any]]:
    collection = _get_embedding_collection()
    try:
        results = collection.query(query_texts=[query_text], n_results=n_results)
        log_event(
            chroma_logger,
            logging.INFO,
            "ChromaDB operation",
            operation="search",
            ticker="",
            success=True,
        )
        return _format_results(results)
    except Exception as exc:
        log_event(
            chroma_logger,
            logging.ERROR,
            "ChromaDB operation",
            operation="search",
            ticker="",
            success=False,
            error=str(exc),
        )
        raise


def get_by_ticker(ticker: str) -> List[Dict[str, Any]]:
    collection = _get_read_collection()
    if collection is None:
        log_event(
            chroma_logger,
            logging.INFO,
            "ChromaDB operation",
            operation="retrieve",
            ticker=ticker.upper(),
            success=True,
        )
        return []
    try:
        results = collection.get(where={"ticker": ticker.upper()})
        log_event(
            chroma_logger,
            logging.INFO,
            "ChromaDB operation",
            operation="retrieve",
            ticker=ticker.upper(),
            success=True,
        )
        return _format_get_results(results)
    except Exception as exc:
        log_event(
            chroma_logger,
            logging.ERROR,
            "ChromaDB operation",
            operation="retrieve",
            ticker=ticker.upper(),
            success=False,
            error=str(exc),
        )
        raise


def list_all_tickers() -> List[str]:
    collection = _get_read_collection()
    if collection is None:
        return []
    results = collection.get()
    tickers = set()
    for meta in results.get("metadatas") or []:
        if meta and meta.get("ticker"):
            tickers.add(meta["ticker"])
    return sorted(tickers)


def _format_results(results: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    ids = results.get("ids") or [[]]
    documents = results.get("documents") or [[]]
    metadatas = results.get("metadatas") or [[]]
    distances = results.get("distances") or [[]]

    for i, doc_id in enumerate(ids[0] if ids else []):
        doc = documents[0][i] if documents and documents[0] else ""
        meta = metadatas[0][i] if metadatas and metadatas[0] else {}
        distance = distances[0][i] if distances and distances[0] else None
        report = _parse_report(doc)
        items.append(
            {
                "id": doc_id,
                "metadata": meta,
                "distance": distance,
                "report": report,
            }
        )
    return items


def _format_get_results(results: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    ids = results.get("ids") or []
    documents = results.get("documents") or []
    metadatas = results.get("metadatas") or []

    for i, doc_id in enumerate(ids):
        doc = documents[i] if i < len(documents) else ""
        meta = metadatas[i] if i < len(metadatas) else {}
        items.append(
            {
                "id": doc_id,
                "metadata": meta,
                "report": _parse_report(doc),
            }
        )

    items.sort(
        key=lambda x: x.get("metadata", {}).get("timestamp", ""),
        reverse=True,
    )
    return items


def _parse_report(document: str) -> Dict[str, Any]:
    if not document:
        return {}
    try:
        return json.loads(document)
    except json.JSONDecodeError:
        return {"raw": document}
