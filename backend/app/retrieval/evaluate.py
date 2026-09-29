"""Development-set retrieval metrics. Not a capacity or quality claim."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Sequence, Set
from uuid import uuid4

from sqlalchemy.orm import Session

from app.domain.enums import SourceType
from app.domain.schemas import CompanyIn, SourceIn
from app.repositories import entities, sources
from app.retrieval.documents import index_documents
from app.retrieval.embeddings import FakeEmbeddingProvider
from app.retrieval.eval_data import DOCUMENTS, NOW, QUERIES, companies
from app.retrieval.package import ResearchRequest, assemble_package
from app.retrieval.search import search
from app.retrieval.store import embed_documents
from app.retrieval.time import as_stored_utc, day_window


def _seed(db: Session) -> Dict[str, Any]:
    ids: Dict[str, Any] = {}
    for row in companies():
        company, _ = entities.get_or_create_company(
            db, CompanyIn(legal_name=row["name"], ticker=row["ticker"], cik=row["cik"])
        )
        ids[row["ticker"]] = company.entity_id
    for index, doc in enumerate(DOCUMENTS):
        slug = str(doc["title"]).lower().replace(" ", "-")[:40]
        sources.upsert_source(
            db,
            SourceIn(
                source_type=SourceType.NEWS,
                url=f"https://example.com/eval/{index}-{slug}",
                retrieved_at=NOW,
                content_text=str(doc["summary"]),
                title=str(doc["title"]),
                published_at=NOW,
                metadata={"summary": str(doc["summary"]), "tickers": list(doc["tickers"])},
            ),
        )
    return ids


def _mrr(retrieved: Sequence[str], relevant: Set[str]) -> float:
    for index, title in enumerate(retrieved, start=1):
        if title in relevant:
            return 1.0 / index
    return 0.0


def _precision_recall(retrieved: Sequence[str], relevant: Set[str], k: int) -> tuple[float, float]:
    top = list(retrieved)[:k]
    if not top:
        return 0.0, 0.0
    hit = sum(1 for title in top if title in relevant)
    return hit / len(top), hit / len(relevant) if relevant else 0.0


def _inside(hit: Any, start: Any, end: Any) -> bool:
    stamp = as_stored_utc(hit.published_at)
    if stamp is None:
        return False
    if start is not None and stamp < start:
        return False
    if end is not None and stamp >= end:
        return False
    return True


def _window_ok(db: Session, provider: Any, start: Any, end: Any, *, expect_now: bool) -> bool:
    result = search(
        db,
        "NVIDIA partnership",
        published_after=start,
        published_before=end,
        top_k=10,
        provider=provider,
    )
    hits = result["hits"]
    if not all(_inside(hit, start, end) for hit in hits):
        return False
    found = any(as_stored_utc(hit.published_at) == as_stored_utc(NOW) for hit in hits)
    return found if expect_now else not found


def _date_windows_hold(db: Session, provider: Any) -> bool:
    """Closed probes. Later rows in the same session cannot satisfy an earlier window."""
    instant = as_stored_utc(NOW)
    assert instant is not None
    day_start, day_end = day_window(instant.date(), instant.date())
    next_start, next_end = day_window(instant.date() + timedelta(days=1), instant.date() + timedelta(days=1))
    probes = (
        _window_ok(db, provider, instant, instant + timedelta(microseconds=1), expect_now=True),
        _window_ok(db, provider, instant + timedelta(microseconds=1), instant + timedelta(days=1), expect_now=False),
        _window_ok(db, provider, instant - timedelta(days=1), instant, expect_now=False),
        _window_ok(db, provider, day_start, day_end, expect_now=True),
        _window_ok(db, provider, next_start, next_end, expect_now=False),
    )
    return all(probes)


def evaluate_retrieval(db: Session) -> Dict[str, Any]:
    ids = _seed(db)
    index_documents(db)
    provider = FakeEmbeddingProvider()
    embed_documents(db, provider=provider)
    p5: List[float] = []
    r5: List[float] = []
    p10: List[float] = []
    r10: List[float] = []
    reciprocal: List[float] = []
    empty = 0
    dupes = 0
    coverages: List[float] = []
    for item in QUERIES:
        result = search(db, str(item["query"]), top_k=10, provider=provider)
        titles = [hit.title or "" for hit in result["hits"]]
        if len(titles) != len(set(titles)):
            dupes += 1
        if not titles:
            empty += 1
        relevant = set(item["relevant"])
        prec5, rec5 = _precision_recall(titles, relevant, 5)
        prec10, rec10 = _precision_recall(titles, relevant, 10)
        p5.append(prec5)
        r5.append(rec5)
        p10.append(prec10)
        r10.append(rec10)
        reciprocal.append(_mrr(titles, relevant))
        package = assemble_package(db, ResearchRequest(query=str(item["query"])), provider=provider)
        coverages.append(float(package["retrieval_metadata"]["citation_coverage"]))
    nvda = ids.get("NVDA")
    entity_hits = search(db, "NVIDIA partnership", entity_ids=[nvda] if nvda else None, top_k=10, provider=provider)
    entity_ok = bool(entity_hits["hits"]) and all(nvda in hit.entity_ids for hit in entity_hits["hits"])
    unknown_event = search(db, "NVIDIA partnership", event_ids=[uuid4()], top_k=10, provider=provider)
    date_ok = _date_windows_hold(db, provider)
    n = len(QUERIES)
    return {
        "set": "development_lexical_hash",
        "limitation": (
            "DEVELOPMENT-ONLY. LEXICAL/FIXTURE EMBEDDINGS. NOT HUMAN-JUDGED. "
            "NOT PRODUCTION MODEL QUALITY. Fake embeddings are lexical hashes, not OpenAI semantic quality."
        ),
        "embedding_provider": "FakeEmbeddingProvider",
        "query_count": n,
        "recall_at_5": round(sum(r5) / n, 4),
        "recall_at_10": round(sum(r10) / n, 4),
        "precision_at_5": round(sum(p5) / n, 4),
        "precision_at_10": round(sum(p10) / n, 4),
        "mrr": round(sum(reciprocal) / n, 4),
        "citation_coverage": round(sum(coverages) / n, 4),
        "empty_rate": round(empty / n, 4),
        "duplicate_rate": round(dupes / n, 4),
        "entity_filter_accuracy": 1.0 if entity_ok else 0.0,
        "date_filter_accuracy": 1.0 if date_ok else 0.0,
        "date_filter_note": (
            "Half-open windows on published_at. Phase 4.5 scored 0.0 because an open-ended "
            "published_after bound also matched later research-fixture rows in the same session."
        ),
        "event_filter_accuracy": 1.0 if not unknown_event["hits"] else 0.0,
    }
