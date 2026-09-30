"""LLM provider abstraction. Tests use FakeLLMProvider; no network in unit tests."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Protocol, Type

from pydantic import BaseModel, ValidationError

from app.core.settings import get_settings
from app.research.schemas import AgentFinding, AgentFindingsPayload


class LLMError(RuntimeError):
    """Provider failed after bounded retries, or the failure is permanent."""

    def __init__(self, message: str, *, retryable: bool = False, error_type: str = "provider"):
        super().__init__(message)
        self.retryable = retryable
        self.error_type = error_type


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: Optional[float] = None


class LLMResponse(BaseModel):
    data: Any
    model: str
    model_version: str
    usage: TokenUsage
    latency_ms: int


class LLMProvider(Protocol):
    model_name: str
    model_version: str

    def generate_structured(
        self,
        *,
        system: str,
        user: str,
        schema: Type[BaseModel],
        prompt_name: str,
        prompt_version: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> LLMResponse: ...


def _estimate_cost(input_tokens: int, output_tokens: int) -> Optional[float]:
    settings = get_settings()
    inp = settings.LLM_INPUT_PRICE_PER_MTOK
    out = settings.LLM_OUTPUT_PRICE_PER_MTOK
    if inp is None or out is None:
        return None
    return round((input_tokens / 1_000_000) * inp + (output_tokens / 1_000_000) * out, 8)


_FINDING_FIELDS = frozenset(AgentFinding.model_fields)


def _coerce_structured(parsed: Any, schema: Type[BaseModel], prompt_name: str) -> Any:
    """Rename fields the model commonly emits before schema validation.

    This does not invent statements or citation ids. Unknown finding keys are
    dropped so extra=forbid does not reject an otherwise usable object.
    """
    if not isinstance(parsed, dict):
        return parsed
    if schema.__name__ == "MasterPlanPayload":
        notes = parsed.get("reasoning_notes")
        if isinstance(notes, list):
            text = " ".join(str(item).strip() for item in notes if str(item).strip())
            return {**parsed, "reasoning_notes": text[:500]}
        return parsed
    if schema.__name__ != "AgentFindingsPayload":
        return parsed
    findings = parsed.get("findings")
    if not isinstance(findings, list):
        return parsed
    cleaned: List[Any] = []
    for item in findings:
        if not isinstance(item, dict):
            cleaned.append(item)
            continue
        row = dict(item)
        if not str(row.get("statement") or "").strip() and isinstance(row.get("finding"), str):
            row["statement"] = row["finding"]
        if "citations" not in row:
            single = row.get("citation_id")
            many = row.get("citation_ids")
            if isinstance(single, str) and single.strip():
                row["citations"] = [single.strip()]
            elif isinstance(many, list):
                row["citations"] = [str(cid).strip() for cid in many if str(cid).strip()]
        if not str(row.get("agent") or "").strip() and prompt_name:
            row["agent"] = prompt_name
        if row.get("finding_id") is not None and not isinstance(row.get("finding_id"), str):
            row["finding_id"] = str(row["finding_id"]).strip()
        if isinstance(row.get("citations"), str) and row["citations"].strip():
            row["citations"] = [row["citations"].strip()]
        statement = str(row.get("statement") or "").strip()
        if not str(row.get("finding_id") or "").strip() and statement:
            cites = row.get("citations") if isinstance(row.get("citations"), list) else []
            row["finding_id"] = _deterministic_id(str(row.get("agent") or prompt_name), statement, [str(cid) for cid in cites])
        cleaned.append({key: value for key, value in row.items() if key in _FINDING_FIELDS})
    return {"findings": cleaned}


def _validation_hint(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:3]:
        loc = ".".join(str(item) for item in err.get("loc") or ())
        parts.append(f"{loc}:{err.get('type')}")
    return "; ".join(parts)[:180]


def _deterministic_id(agent: str, statement: str, citations: List[str]) -> str:
    import hashlib

    key = f"{agent}|{statement}|{','.join(sorted(citations))}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


class FakeLLMProvider:
    """Scripted findings from package fields. Deterministic; not semantic intelligence."""

    def __init__(
        self,
        *,
        scenario: str = "valid",
        fail_agents: Optional[set[str]] = None,
        fail_times: int = 1,
        model_name: str = "fake-research",
        model_version: str = "v1",
    ) -> None:
        self.scenario = scenario
        self.fail_agents = fail_agents or set()
        self.fail_times = fail_times
        self.model_name = model_name
        self.model_version = model_version
        self.calls = 0
        self._failures: Dict[str, int] = {}

    def generate_structured(
        self,
        *,
        system: str,
        user: str,
        schema: Type[BaseModel],
        prompt_name: str,
        prompt_version: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> LLMResponse:
        del system, user, prompt_version
        self.calls += 1
        started = time.perf_counter()
        agent = prompt_name
        self._maybe_fail(agent)
        if self.scenario == "malformed":
            raise LLMError("malformed structured output", retryable=False, error_type="malformed")
        payload = self._payload(agent, context or {})
        try:
            data = schema.model_validate(payload)
        except ValidationError as exc:
            raise LLMError(f"structured output invalid: {exc}", retryable=False, error_type="malformed") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)
        tokens_in = max(1, len(json.dumps(context or {}, default=str)) // 4)
        tokens_out = max(1, len(json.dumps(payload, default=str)) // 4)
        return LLMResponse(
            data=data,
            model=self.model_name,
            model_version=self.model_version,
            usage=TokenUsage(
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                total_tokens=tokens_in + tokens_out,
                estimated_cost=_estimate_cost(tokens_in, tokens_out),
            ),
            latency_ms=latency_ms,
        )

    def _maybe_fail(self, agent: str) -> None:
        if agent in self.fail_agents:
            raise LLMError(f"{agent} forced failure", retryable=False, error_type="provider")
        scenario = self.scenario
        count = self._failures.get(agent, 0)
        transient = scenario in {"transient", "rate_limit", "timeout"}
        if transient and count < self.fail_times:
            self._failures[agent] = count + 1
            error_type = {"rate_limit": "rate_limit", "timeout": "timeout"}.get(scenario, "timeout")
            raise LLMError(f"{scenario} failure", retryable=True, error_type=error_type)
        if scenario == "permanent":
            raise LLMError("authentication failure", retryable=False, error_type="auth")

    def _payload(self, agent: str, context: Dict[str, Any]) -> Dict[str, Any]:
        if agent == "master":
            return self._master_payload(context)
        if agent == "report":
            return {"bullets": list(context.get("allowed_statements") or [])[:3]}
        if self.scenario == "missing_citation":
            return {
                "findings": [
                    {
                        "finding_id": _deterministic_id(agent, "Unsupported bare statement", []),
                        "agent": agent,
                        "statement": "The company secretly acquired an unnamed rival.",
                        "finding_type": "FACT",
                        "evidence_refs": [],
                        "citations": [],
                        "confidence": 0.99,
                        "reasoning_summary": "missing citation fixture",
                    }
                ]
            }
        if self.scenario == "unsupported":
            cites = list(context.get("citation_ids") or [])[:1]
            return {
                "findings": [
                    {
                        "finding_id": _deterministic_id(agent, "Meta partnered with AMD", cites),
                        "agent": agent,
                        "statement": "Meta partnered with AMD.",
                        "finding_type": "FACT",
                        "evidence_refs": cites,
                        "citations": cites,
                        "confidence": 0.91,
                        "reasoning_summary": "names co-occur; not a partnership record",
                    }
                ]
            }
        if self.scenario == "conflicting":
            cites = list(context.get("citation_ids") or [])
            first = cites[:1]
            second = cites[1:2] or first
            return {
                "findings": [
                    {
                        "finding_id": _deterministic_id(agent, "Company X acquired Company Y.", first),
                        "agent": agent,
                        "statement": "Company X acquired Company Y.",
                        "finding_type": "FACT",
                        "evidence_refs": first,
                        "citations": first,
                        "confidence": 0.7,
                        "reasoning_summary": "conflict fixture A",
                    },
                    {
                        "finding_id": _deterministic_id(agent, "Company X did not acquire Company Y.", second),
                        "agent": agent,
                        "statement": "Company X did not acquire Company Y.",
                        "finding_type": "FACT",
                        "evidence_refs": second,
                        "citations": second,
                        "confidence": 0.7,
                        "reasoning_summary": "conflict fixture B",
                    },
                ]
            }
        return {"findings": _valid_findings(agent, context)}

    def _master_payload(self, context: Dict[str, Any]) -> Dict[str, Any]:
        if self.scenario == "bad_plan":
            return {
                "objective": "invalid specialist",
                "selected_agents": ["sql_agent"],
                "evidence_requirements": [],
                "requested_sections": [],
                "reasoning_notes": "rejected",
            }
        if self.scenario == "oversized_plan":
            return {
                "objective": "too many agents",
                "selected_agents": ["news", "company", "technology", "market", "news"],
                "evidence_requirements": [],
                "requested_sections": [],
                "reasoning_notes": "oversized",
            }
        agents = list(context.get("candidate_agents") or [])
        return {
            "objective": str(context.get("query") or "research")[:500] or "research",
            "selected_agents": agents,
            "evidence_requirements": ["citation_required_for_facts"],
            "requested_sections": [
                "key_facts",
                "market_observations",
                "company_relationships",
                "technology_findings",
                "implications",
                "scenarios",
                "conflicts",
                "limitations",
            ],
            "reasoning_notes": "documented router",
        }


def _valid_findings(agent: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    if agent == "news":
        for doc in context.get("documents") or []:
            cites = list(doc.get("citation_ids") or [])
            if not cites:
                continue
            title = doc.get("title") or "document"
            excerpt = doc.get("excerpt") or title
            statement = f"Source reports: {excerpt}"
            findings.append(_finding(agent, "FACT", statement, cites, "news document"))
        for event in context.get("events") or []:
            cites = list(event.get("citation_ids") or [])
            if not cites:
                continue
            statement = f"Documented event: {event.get('title')}"
            findings.append(_finding(agent, "FACT", statement, cites, "event record"))
    elif agent == "company":
        for entity in context.get("entities") or []:
            cites = list(entity.get("citation_ids") or [])
            if not cites:
                continue
            name = entity.get("name") or "entity"
            ticker = entity.get("ticker")
            statement = f"{name} is a canonical company" + (f" ticker {ticker}" if ticker else "") + "."
            findings.append(_finding(agent, "FACT", statement, cites, "entity record"))
        for edge in context.get("graph_facts") or []:
            cites = list(edge.get("citation_ids") or [])
            if not cites:
                continue
            statement = (
                f"Canonical relationship {edge.get('relationship')} "
                f"from {edge.get('from_entity')} to {edge.get('to_entity')}."
            )
            findings.append(_finding(agent, "FACT", statement, cites, "graph fact"))
        for event in context.get("events") or []:
            cites = list(event.get("citation_ids") or [])
            if not cites:
                continue
            findings.append(_finding(agent, "FACT", f"Company event: {event.get('title')}", cites, "event"))
    elif agent == "technology":
        for entity in context.get("entities") or []:
            if str(entity.get("entity_type") or "") not in {"technology", "product"}:
                continue
            cites = list(entity.get("citation_ids") or [])
            if not cites:
                continue
            findings.append(
                _finding(agent, "FACT", f"{entity.get('name')} is a documented technology entity.", cites, "tech entity")
            )
        for edge in context.get("graph_facts") or []:
            rel = str(edge.get("relationship") or "")
            if rel not in {"USES", "DEVELOPS", "DEPENDS_ON", "SUPPLIES"}:
                continue
            cites = list(edge.get("citation_ids") or [])
            if not cites:
                continue
            statement = f"Documented {rel} between {edge.get('from_entity')} and {edge.get('to_entity')}."
            findings.append(_finding(agent, "FACT", statement, cites, "tech relationship"))
        for doc in context.get("documents") or []:
            excerpt = str(doc.get("excerpt") or "")
            if not _explicit_technology_excerpt(excerpt):
                continue
            cites = list(doc.get("citation_ids") or [])
            if not cites:
                continue
            findings.append(_finding(agent, "FACT", f"Source describes technology: {excerpt}", cites, "doc"))
    elif agent == "market":
        for fact in context.get("market_facts") or []:
            cites = list(fact.get("citation_ids") or [])
            if not cites:
                continue
            symbol = fact.get("symbol") or "asset"
            windows = fact.get("windows") or {}
            window = windows.get("+5D") or windows.get("+3D") or next(iter(windows.values()), None)
            returned = None
            if isinstance(window, dict):
                returned = window.get("return")
            elif window is not None:
                returned = window
            statement = (
                f"The stock {symbol} returned {returned} over the documented trading-session window "
                f"(calculation_version={fact.get('calculation_version')})."
            )
            findings.append(_finding(agent, "OBSERVATION", statement, cites, "market window"))
    return findings[:12]


def _explicit_technology_excerpt(excerpt: str) -> bool:
    """Quote a source only when it names a technology. Do not infer USES."""
    text = excerpt.lower()
    return any(term in text for term in ("technolog", "chip", "gpu", "accelerator"))


def _finding(agent: str, kind: str, statement: str, citations: List[str], reason: str) -> Dict[str, Any]:
    return {
        "finding_id": _deterministic_id(agent, statement, citations),
        "agent": agent,
        "statement": statement,
        "finding_type": kind,
        "evidence_refs": list(citations),
        "citations": list(citations),
        "confidence": 0.6,
        "reasoning_summary": reason,
    }


class OpenAILLMProvider:
    def __init__(self) -> None:
        settings = get_settings()
        key = settings.OPENAI_API_KEY.get_secret_value().strip()
        if not key:
            raise LLMError("OPENAI_API_KEY is not configured", retryable=False, error_type="auth")
        from openai import OpenAI

        self._client = OpenAI(api_key=key, timeout=settings.LLM_TIMEOUT_SECONDS)
        self.model_name = settings.LLM_MODEL
        self.model_version = settings.LLM_MODEL_VERSION
        self._timeout = settings.LLM_TIMEOUT_SECONDS
        self._retries = settings.LLM_MAX_RETRIES

    def generate_structured(
        self,
        *,
        system: str,
        user: str,
        schema: Type[BaseModel],
        prompt_name: str,
        prompt_version: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> LLMResponse:
        del prompt_version
        last: Optional[LLMError] = None
        for attempt in range(self._retries + 1):
            started = time.perf_counter()
            try:
                response = self._client.chat.completions.create(
                    model=self.model_name,
                    temperature=0.2,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
            except Exception as exc:
                last = _classify_openai(exc)
                if not last.retryable or attempt >= self._retries:
                    raise last from exc
                time.sleep(min(2**attempt, 8))
                continue
            content = (response.choices[0].message.content or "").strip()
            try:
                parsed = _coerce_structured(json.loads(content), schema, prompt_name)
                data = schema.model_validate(parsed)
            except (json.JSONDecodeError, ValidationError) as exc:
                hint = _validation_hint(exc) if isinstance(exc, ValidationError) else "json"
                last = LLMError(
                    f"invalid structured output ({hint})",
                    retryable=attempt < self._retries,
                    error_type="malformed",
                )
                if not last.retryable:
                    raise last from exc
                time.sleep(min(2**attempt, 8))
                continue
            usage = getattr(response, "usage", None)
            tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0)
            tokens_out = int(getattr(usage, "completion_tokens", 0) or 0)
            return LLMResponse(
                data=data,
                model=self.model_name,
                model_version=self.model_version,
                usage=TokenUsage(
                    input_tokens=tokens_in,
                    output_tokens=tokens_out,
                    total_tokens=tokens_in + tokens_out,
                    estimated_cost=_estimate_cost(tokens_in, tokens_out),
                ),
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        raise last or LLMError("provider failed", retryable=False, error_type="provider")


def _classify_openai(exc: Exception) -> LLMError:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "timeout" in name or "timeout" in text:
        return LLMError("llm timeout", retryable=True, error_type="timeout")
    if "rate" in name or "429" in text or "rate limit" in text:
        return LLMError("llm rate limit", retryable=True, error_type="rate_limit")
    if "auth" in name or "401" in text or "403" in text:
        return LLMError("llm authentication failure", retryable=False, error_type="auth")
    if "connect" in name or "temporary" in text or "503" in text or "502" in text:
        return LLMError("temporary provider error", retryable=True, error_type="provider")
    return LLMError(str(exc)[:200], retryable=False, error_type="provider")


def get_llm_provider() -> LLMProvider:
    settings = get_settings()
    if settings.LLM_PROVIDER == "fake":
        return FakeLLMProvider()
    return OpenAILLMProvider()


# Imported by agents for finding IDs when they construct output without the fake helper.
finding_id = _deterministic_id
AgentFindingsPayload.model_rebuild()
AgentFinding.model_rebuild()
