"""Resolve a research request to a company security, then keep market facts on it.

The symbol comes from the company row. A model string is not an identity.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import AliasType
from app.domain.models import Company, Entity, EntityAlias
from app.graph.normalize import legal_name_stem, normalize_name, normalize_ticker
from app.market.binding import (
    REJECTED_LIMITATION,
    UNVERIFIED_LIMITATION,
    market_binding_status,
)
from app.research.schemas import ReportItem, ResearchReport

_PHRASE = re.compile(r"(?<!\w){phrase}(?!\w)")
_TICKER = re.compile(r"(?<![A-Za-z0-9]){ticker}(?![A-Za-z0-9])")
_SUFFIXES = frozenset(
    {"incorporated", "corporation", "company", "limited", "inc", "corp", "ltd", "llc", "plc", "co"}
)


def security_for_entity(db: Session, entity_id: uuid.UUID) -> Optional[Dict[str, Any]]:
    company = db.get(Company, entity_id)
    entity = db.get(Entity, entity_id)
    if company is None or entity is None or not company.ticker:
        return None
    return _security_dict(company, entity)


def resolve_securities(
    db: Session, query: str, entity_ids: Sequence[uuid.UUID]
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Resolve explicit entity ids, or names and tickers written in the query.

    Explicit entity ids win. A query is not allowed to add a second company
    once the caller has named the entities. An unresolved id is not replaced.
    """
    if entity_ids:
        found: List[Dict[str, Any]] = []
        missing = False
        for entity_id in entity_ids:
            row = security_for_entity(db, entity_id)
            if row is None:
                missing = True
                continue
            found.append(row)
        notes = [UNVERIFIED_LIMITATION] if missing and not found else []
        return found, notes
    return _from_query(db, query)


def validate_report_market_bindings(report: ResearchReport) -> ResearchReport:
    """Drop market findings whose identity is not the resolved security.

    Reports built before this check have no resolved_securities key and are
    left unchanged. An empty list means resolution failed: market numbers
    are removed and a limitation is recorded.
    """
    metadata = dict(report.metadata or {})
    if "resolved_securities" not in metadata:
        return report
    securities = list(metadata.get("resolved_securities") or [])
    kept: List[Dict[str, Any]] = []
    removed: List[str] = []
    for cite in report.citations:
        if cite.get("kind") != "market_fact":
            kept.append(cite)
            continue
        if market_binding_status(cite.get("market") or {}, securities) != "accepted":
            removed.append(str(cite.get("citation_id") or ""))
            continue
        kept.append(cite)
    removed_ids = {item for item in removed if item}
    notes = list(report.limitations)
    if not securities:
        if UNVERIFIED_LIMITATION not in notes:
            notes.insert(0, UNVERIFIED_LIMITATION)
    elif removed_ids and REJECTED_LIMITATION not in notes:
        notes.insert(0, REJECTED_LIMITATION)
    if not removed_ids and notes == list(report.limitations):
        return report

    def scrub(items: Optional[List[ReportItem]]) -> Optional[List[ReportItem]]:
        if not items:
            return items
        filtered = [item for item in items if not removed_ids.intersection(item.citation_ids)]
        return filtered or None

    summary = [item for item in report.executive_summary if not removed_ids.intersection(item.citation_ids)]
    return report.model_copy(
        update={
            "citations": kept,
            "limitations": list(dict.fromkeys(notes))[:12],
            "executive_summary": summary,
            "key_facts": scrub(report.key_facts),
            "market_observations": scrub(report.market_observations),
        }
    )


def _from_query(db: Session, query: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    rows = list(db.execute(select(Company, Entity).join(Entity, Entity.id == Company.entity_id)))
    if not rows:
        return [], [UNVERIFIED_LIMITATION]
    aliases = list(db.scalars(select(EntityAlias)))
    by_entity: Dict[uuid.UUID, List[EntityAlias]] = {}
    for alias in aliases:
        by_entity.setdefault(alias.entity_id, []).append(alias)
    norm_query = normalize_name(query)
    accepted: Dict[uuid.UUID, Tuple[Company, Entity]] = {}
    ambiguous = False

    ticker_hits: Dict[str, List[Tuple[Company, Entity]]] = {}
    for company, entity in rows:
        for symbol in _match_symbols(company, by_entity.get(company.entity_id, [])):
            if symbol and _ticker_in(query, symbol):
                ticker_hits.setdefault(symbol, []).append((company, entity))
    for symbol, hits in ticker_hits.items():
        ids = {company.entity_id for company, _entity in hits}
        if len(ids) == 1:
            accepted[hits[0][0].entity_id] = hits[0]
        else:
            ambiguous = True

    phrase_hits: Dict[str, List[Tuple[Company, Entity]]] = {}
    for company, entity in rows:
        for phrase in _phrases(company, entity, by_entity.get(company.entity_id, [])):
            if _phrase_in(phrase, norm_query):
                phrase_hits.setdefault(phrase, []).append((company, entity))
    for _phrase, hits in phrase_hits.items():
        ids = {company.entity_id for company, _entity in hits}
        if len(ids) == 1:
            accepted[hits[0][0].entity_id] = hits[0]
        else:
            ambiguous = True

    securities = []
    for company, entity in accepted.values():
        row = _security_dict(company, entity)
        if row is not None:
            securities.append(row)
    securities.sort(key=lambda item: item["symbol"])
    if securities:
        return securities, []
    if ambiguous:
        return [], [UNVERIFIED_LIMITATION]
    return [], [UNVERIFIED_LIMITATION]


def _security_dict(company: Company, entity: Entity) -> Optional[Dict[str, Any]]:
    symbol = normalize_ticker(company.ticker or "")
    if not symbol:
        return None
    stem = legal_name_stem(company.legal_name or "")
    name = stem or entity.canonical_name or company.legal_name
    return {
        "entity_id": str(company.entity_id),
        "symbol": symbol,
        "exchange": company.exchange,
        "security_type": "equity",
        "canonical_name": name,
    }


def _match_symbols(company: Company, aliases: Sequence[EntityAlias]) -> List[str]:
    symbols = []
    if company.ticker:
        symbols.append(normalize_ticker(company.ticker))
    for ticker in company.tickers or []:
        text = normalize_ticker(str(ticker))
        if text:
            symbols.append(text)
    for alias in aliases:
        if alias.alias_type == AliasType.TICKER.value:
            text = normalize_ticker(alias.alias)
            if text:
                symbols.append(text)
    return list(dict.fromkeys(symbols))


def _phrases(company: Company, entity: Entity, aliases: Sequence[EntityAlias]) -> List[str]:
    values = [company.legal_name, entity.canonical_name]
    stem = legal_name_stem(company.legal_name or "")
    if stem:
        values.append(stem)
    for alias in aliases:
        if alias.alias_type == AliasType.TICKER.value:
            continue
        values.append(alias.alias)
    phrases = []
    for value in values:
        phrase = normalize_name(value or "")
        if len(phrase) >= 3 and phrase not in _SUFFIXES:
            phrases.append(phrase)
    return list(dict.fromkeys(phrases))


def _phrase_in(phrase: str, text: str) -> bool:
    if len(phrase) < 3 or not text:
        return False
    return re.search(_PHRASE.pattern.format(phrase=re.escape(phrase)), text) is not None


def _ticker_in(query: str, ticker: str) -> bool:
    if not ticker:
        return False
    return re.search(_TICKER.pattern.format(ticker=re.escape(ticker)), query, flags=re.IGNORECASE) is not None
