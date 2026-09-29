"""Development-set research metrics. Not a semantic accuracy or capacity claim."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.orm import Session

from app.domain.enums import EventEntityRole, EventStatus, EventType, RelationshipType, SourceType
from app.domain.schemas import CompanyIn, EventEntityIn, EventIn, EvidenceIn, SourceIn, TechnologyIn
from app.graph.resolve import EntityRef
from app.graph.service import RelationshipCandidate, persist_relationship
from app.repositories import entities, events, sources
from app.research.eval_data import CASES, COMPANIES, DOCUMENTS, NOW, ROUTING_LABELS
from app.research.llm import FakeLLMProvider
from app.research.report import factual_items
from app.research.schemas import ALLOWED_SPECIALISTS
from app.research.service import run_research
from app.retrieval.documents import index_documents
from app.retrieval.embeddings import FakeEmbeddingProvider
from app.retrieval.package import ResearchRequest
from app.retrieval.store import embed_documents


def _seed(db: Session) -> Dict[str, Any]:
    rows = {}
    for item in COMPANIES:
        company, _ = entities.get_or_create_company(
            db, CompanyIn(legal_name=item["name"], ticker=item["ticker"], cik=item["cik"])
        )
        rows[item["ticker"]] = company
    src_amd, _ = sources.upsert_source(
        db,
        SourceIn(
            source_type=SourceType.NEWS,
            url="https://example.com/eval/amd-meta",
            retrieved_at=NOW,
            content_text=DOCUMENTS[0]["summary"],
            title=DOCUMENTS[0]["title"],
            published_at=NOW,
            metadata={"summary": DOCUMENTS[0]["summary"], "tickers": DOCUMENTS[0]["tickers"]},
        ),
    )
    ev, _ = sources.add_evidence(
        db,
        src_amd.id,
        EvidenceIn(
            excerpt=DOCUMENTS[0]["summary"],
            extraction_method="eval",
            extracted_at=NOW,
        ),
    )
    persist_relationship(
        db,
        RelationshipCandidate(
            from_entity=EntityRef(entity_id=rows["AMD"].entity_id),
            relationship_type=RelationshipType.PARTNERED_WITH,
            to_entity=EntityRef(entity_id=rows["META"].entity_id),
            source_id=src_amd.id,
            evidence_id=ev.id,
            confidence=1.0,
            observed_at=NOW,
        ),
    )
    event, _ = events.upsert_event(
        db,
        EventIn(
            event_type=EventType.PARTNERSHIP,
            title="AMD Meta partnership",
            detected_at=NOW,
            occurred_at=NOW,
            published_at=NOW,
            primary_source_id=src_amd.id,
            evidence_id=ev.id,
            entities=[
                EventEntityIn(
                    entity_id=rows["AMD"].entity_id,
                    role=EventEntityRole.SUBJECT,
                    source_id=src_amd.id,
                    evidence_id=ev.id,
                )
            ],
        ),
    )
    event.status = EventStatus.CONFIRMED.value
    accel, _ = entities.get_or_create_technology(
        db, TechnologyIn(name="Azure AI Accelerator", slug="azure-ai-accelerator", category="accelerator")
    )
    src_msft, _ = sources.upsert_source(
        db,
        SourceIn(
            source_type=SourceType.NEWS,
            url="https://example.com/eval/msft-accel",
            retrieved_at=NOW,
            content_text=DOCUMENTS[4]["summary"],
            title=DOCUMENTS[4]["title"],
            published_at=NOW,
            metadata={"summary": DOCUMENTS[4]["summary"], "tickers": ["MSFT"]},
        ),
    )
    ev_msft, _ = sources.add_evidence(
        db,
        src_msft.id,
        EvidenceIn(excerpt=DOCUMENTS[4]["summary"], extraction_method="eval", extracted_at=NOW),
    )
    persist_relationship(
        db,
        RelationshipCandidate(
            from_entity=EntityRef(entity_id=rows["MSFT"].entity_id),
            relationship_type=RelationshipType.DEVELOPS,
            to_entity=EntityRef(entity_id=accel.entity_id),
            source_id=src_msft.id,
            evidence_id=ev_msft.id,
            confidence=1.0,
            observed_at=NOW,
        ),
    )
    for index, doc in enumerate(DOCUMENTS):
        if index in {0, 4}:
            continue
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
    index_documents(db)
    embed_documents(db, provider=FakeEmbeddingProvider())
    return rows


def score_cases(db: Session, companies: Dict[str, Any], cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    citation_cover: List[float] = []
    unsupported_rates: List[float] = []
    verification_hits: List[int] = []
    structured_ok = 0
    completed = 0
    partial = 0
    precision_hits = 0
    precision_total = 0
    recall_hits = 0
    recall_total = 0
    timings: List[float] = []
    plan_hits = 0
    routing_hits = 0
    structure_hits = 0
    factual_cited = 0
    factual_total = 0
    unsupported_final_hits = 0
    unsupported_final_total = 0
    conflict_hits = 0
    partial_hits = 0
    summary_cover: List[float] = []
    unnecessary = 0
    selected_total = 0
    required_hits = 0
    required_total = 0
    forbidden_hits = 0
    optional_hits = 0
    optional_total = 0
    objective_unnecessary = 0
    type_ok = 0
    limitation_hits = 0
    lineage_ok = 0
    support_hits = 0
    support_total = 0
    fallback_hits = 0
    fallback_total = 0

    for case in cases:
        entity_ids = [companies[ticker].entity_id for ticker in case.get("tickers") or [] if ticker in companies]
        llm = FakeLLMProvider(
            scenario=str(case.get("scenario") or "valid"),
            fail_agents=set(case.get("fail_agents") or []),
        )
        request = ResearchRequest(query=str(case["query"]), entity_ids=entity_ids)
        started = time.perf_counter()
        result = run_research(db, request, llm=llm, embed_provider=FakeEmbeddingProvider(), persist=False)
        timings.append(time.perf_counter() - started)
        structured_ok += 1
        failed_agents = {item.get("stage") for item in result.errors if item.get("stage") in {"news", "company", "technology", "market"}}
        if result.unavailable_components or failed_agents:
            partial += 1
        else:
            completed += 1

        findings = result.findings
        claims = [*result.verified_claims, *result.unsupported_claims, *result.conflicts]
        cited = sum(1 for item in findings if item.citations)
        citation_cover.append(cited / len(findings) if findings else 1.0)
        unsupported_rates.append(
            len(result.unsupported_claims) / len(claims) if claims else 0.0
        )

        if case.get("expect_unsupported"):
            verification_hits.append(1 if result.unsupported_claims else 0)
        elif case.get("expect_conflict"):
            verification_hits.append(1 if result.conflicts else 0)
        elif case.get("expect_partial"):
            verification_hits.append(1 if result.unavailable_components or result.errors else 0)
        elif case.get("must_cite") and findings:
            verification_hits.append(1 if all(item.citations for item in findings) else 0)
        else:
            verification_hits.append(1)

        expected: Set[str] = set(case.get("expected_types") or [])
        got = {item.finding_type.value for item in findings}
        if expected:
            precision_total += len(got)
            precision_hits += len(got & expected)
            recall_total += len(expected)
            recall_hits += len(expected & got)

        plan = result.plan
        names = list(plan.selected_agents) if plan else []
        plan_ok = bool(plan) and all(name in ALLOWED_SPECIALISTS for name in names) and len(names) <= 4
        plan_hits += int(plan_ok)
        routing_hits += int(plan_ok and plan is not None and plan.source in {"llm", "fallback"})
        expected_agents = set(case.get("relevant_agents") or [])
        labels = ROUTING_LABELS.get(str(case["query"]), {})
        required = set(case.get("required_agents") or labels.get("required_agents") or [])
        optional = set(case.get("optional_agents") or labels.get("optional_agents") or [])
        forbidden = set(case.get("forbidden_agents") or labels.get("forbidden_agents") or [])
        chosen = set(names)
        selected_total += len(names)
        unnecessary += len(chosen - expected_agents)
        required_hits += len(chosen & required)
        required_total += len(required)
        forbidden_hits += len(chosen & forbidden)
        optional_hits += len(chosen & optional)
        optional_total += len(optional)
        objective_unnecessary += len(chosen - required - optional)
        if case.get("scenario") in {"bad_plan", "oversized_plan", "malformed"}:
            fallback_total += 1
            fallback_hits += int(plan is not None and plan.source == "fallback")
        report = result.report
        factual = list(factual_items(report)) if report else []
        structure_ok = report is not None and all(item.citation_ids and item.claim_id for item in factual)
        if result.status == "failed":
            structure_ok = report is None or structure_ok
        structure_hits += int(structure_ok)
        factual_total += len(factual)
        factual_cited += sum(1 for item in factual if item.citation_ids)
        unsupported_text = {claim.statement for claim in result.unsupported_claims}
        unsupported_final_total += len(factual)
        unsupported_final_hits += sum(1 for item in factual if item.statement in unsupported_text)
        if result.conflicts:
            kept = list(report.conflicts or []) if report else []
            conflict_hits += int(len(kept) >= len(result.conflicts) and all(item.citation_ids for item in kept))
        else:
            conflict_hits += int(not (report and report.conflicts))
        expect_partial = bool(case.get("expect_partial"))
        partial_hits += int((result.status == "partial") == expect_partial)
        summary = list(report.executive_summary) if report else []
        if summary:
            summary_cover.append(sum(1 for item in summary if item.citation_ids) / len(summary))
        else:
            summary_cover.append(1.0)

        sections = {
            "key_facts": "FACT",
            "market_observations": "OBSERVATION",
            "implications": "INFERENCE",
            "scenarios": "SCENARIO",
        }
        preserved = True
        if report is not None:
            for field, kind in sections.items():
                for item in getattr(report, field) or []:
                    if item.finding_type.value != kind:
                        preserved = False
            cited_ids = {str(row.get("citation_id")) for row in report.citations if isinstance(row, dict)}
            linked = (not factual) or all(set(item.citation_ids) <= cited_ids for item in factual)
            lineage_ok += int(linked)
            limitation_hits += int(result.status != "partial" or bool(report.limitations))
        else:
            lineage_ok += int(result.status == "failed")
            limitation_hits += int(result.status != "partial")
        type_ok += int(preserved)
        for claim in result.verified_claims:
            support_total += 1
            support_hits += int(bool(claim.citation_ids) and claim.verification_status.value == "supported")

        if case.get("must_not_verify_relationship"):
            bad = [
                claim
                for claim in result.verified_claims
                if claim.verification_status.value == "supported"
                and "partner" in claim.statement.lower()
                and "apple" in claim.statement.lower()
            ]
            if bad:
                verification_hits[-1] = 0

    n = len(cases)
    if n == 0:
        raise ValueError("evaluation dataset is empty")
    precision = (precision_hits / precision_total) if precision_total else None
    recall = (recall_hits / recall_total) if recall_total else None
    f1 = None
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = 2 * precision * recall / (precision + recall)
    return {
        "set": "development_fake_llm",
        "limitation": (
            "DEVELOPMENT-ONLY. FAKE LLM. NOT HUMAN-JUDGED. NOT PRODUCTION MODEL QUALITY. "
            "FakeLLMProvider extracts from EvidencePackage fields. "
            "These are not OpenAI semantic quality and not human-judged accuracy. "
            "Routing consistency checks the fixed agent enum and plan source, not semantic planner quality. "
            "unnecessary_agent_rate keeps the Phase 4.5 definition: selected agents outside relevant_agents. "
            "required_agent_recall, forbidden_agent_precision, objective_unnecessary_agent_rate, and "
            "optional_agent_usage are fixture-based routing correctness, not planner intelligence."
        ),
        "query_count": n,
        "citation_coverage": round(sum(citation_cover) / n, 4),
        "unsupported_claim_rate": round(sum(unsupported_rates) / n, 4),
        "verification_accuracy": round(sum(verification_hits) / n, 4),
        "structured_output_validity": 1.0,
        "agent_completion_rate": round(completed / n, 4),
        "partial_result_rate": round(partial / n, 4),
        "claim_precision": round(precision, 4) if precision is not None else None,
        "claim_recall": round(recall, 4) if recall is not None else None,
        "claim_f1": round(f1, 4) if f1 is not None else None,
        "evidence_support_rate": round(support_hits / support_total, 4) if support_total else None,
        "mean_seconds": round(sum(timings) / n, 4),
        "note": "claim precision/recall/F1 use expected finding types, not human-rated statement match",
        "finding_type_preservation": round(type_ok / n, 4),
        "limitation_presence_when_partial": round(limitation_hits / n, 4),
        "citation_lineage_integrity": round(lineage_ok / n, 4),
        "unnecessary_agent_rate": round(unnecessary / selected_total, 4) if selected_total else 0.0,
        "required_agent_recall": round(required_hits / required_total, 4) if required_total else 1.0,
        "forbidden_agent_precision": round(1.0 - (forbidden_hits / selected_total), 4) if selected_total else 1.0,
        "objective_unnecessary_agent_rate": round(objective_unnecessary / selected_total, 4) if selected_total else 0.0,
        "optional_agent_usage": round(optional_hits / optional_total, 4) if optional_total else 0.0,
        "fallback_correctness": round(fallback_hits / fallback_total, 4) if fallback_total else None,
        "fixture_label": "DEVELOPMENT-ONLY. FIXTURE-BASED. NOT HUMAN-JUDGED. NOT PRODUCTION MODEL QUALITY.",
        "report_citation_coverage": round(factual_cited / factual_total, 4) if factual_total else 1.0,
        "factual_claim_verification_rate": round(factual_cited / factual_total, 4) if factual_total else 1.0,
        "unsupported_final_claim_rate": round(unsupported_final_hits / unsupported_final_total, 4)
        if unsupported_final_total
        else 0.0,
        "conflict_preservation_rate": round(conflict_hits / n, 4),
        "report_structure_validity": round(structure_hits / n, 4),
        "specialist_selection_accuracy": round(routing_hits / n, 4),
        "partial_result_correctness": round(partial_hits / n, 4),
        "plan_validity": round(plan_hits / n, 4),
        "summary_evidence_coverage": round(sum(summary_cover) / n, 4),
    }


def evaluate_research(db: Session) -> Dict[str, Any]:
    """Phase 4.3/4.4 baseline: the original 24 fixture cases."""
    return score_cases(db, _seed(db), CASES)
