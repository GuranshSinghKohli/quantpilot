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
    future = search(
        db, "NVIDIA partnership", published_after=NOW + timedelta(days=1), top_k=10, provider=provider
    )
    past = search(
        db, "NVIDIA partnership", published_before=NOW - timedelta(days=1), top_k=10, provider=provider
    )
    unknown_event = search(db, "NVIDIA partnership", event_ids=[uuid4()], top_k=10, provider=provider)
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
        "date_filter_accuracy": 1.0 if not future["hits"] and not past["hits"] else 0.0,
        "event_filter_accuracy": 1.0 if not unknown_event["hits"] else 0.0,
    }
