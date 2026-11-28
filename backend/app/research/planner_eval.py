"""Labeled planner queries. These labels are not production routing rules."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

PLANNER_EVAL_CASES: List[Dict[str, Any]] = [
    {
        "request": "What recent NVIDIA news affected its stock?",
        "required": ["news", "market"],
        "optional": [],
        "forbidden": ["company", "technology"],
    },
    {
        "request": "What technologies is AMD developing?",
        "required": ["technology", "company"],
        "optional": ["news"],
        "forbidden": ["market"],
    },
    {
        "request": "What companies has Microsoft partnered with?",
        "required": ["company"],
        "optional": ["news"],
        "forbidden": ["technology", "market"],
    },
    {
        "request": "What does Apple do?",
        "required": ["company"],
        "optional": [],
        "forbidden": ["news", "technology", "market"],
    },
    {
        "request": "Show me recent NVIDIA developments and their market reaction.",
        "required": ["news", "market"],
        "optional": ["technology", "company"],
        "forbidden": [],
    },
    {
        "request": "What recent developments involving Microsoft were reported by the collected sources?",
        "required": ["news"],
        "optional": ["company"],
        "forbidden": ["market", "technology"],
    },
    {
        "request": "What technology-related developments involving AMD appear in the corpus?",
        "required": ["technology"],
        "optional": ["news", "company"],
        "forbidden": ["market"],
    },
    {
        "request": "What happened around a selected NVIDIA event and how did the stock behave afterward?",
        "required": ["news", "market"],
        "optional": ["company"],
        "forbidden": ["technology"],
    },
    {
        "request": "What recent developments involving Apple appear in the collected news?",
        "required": ["news"],
        "optional": ["company"],
        "forbidden": ["market", "technology"],
    },
    {
        "request": "What relationships between companies are explicitly supported by the collected evidence?",
        "required": ["company"],
        "optional": ["news"],
        "forbidden": ["technology", "market"],
    },
    {
        "request": "How did NVDA trade over the last five sessions?",
        "required": ["market"],
        "optional": [],
        "forbidden": ["news", "company", "technology"],
    },
    {
        "request": "What is the canonical identity of Advanced Micro Devices?",
        "required": ["company"],
        "optional": [],
        "forbidden": ["news", "technology", "market"],
    },
    {
        "request": "Which articles mention Meta Platforms?",
        "required": ["news"],
        "optional": ["company"],
        "forbidden": ["technology", "market"],
    },
    {
        "request": "Did any collected source describe an AMD chip or accelerator?",
        "required": ["technology"],
        "optional": ["news", "company"],
        "forbidden": ["market"],
    },
    {
        "request": "Compare the session returns of NVDA and SPY after a documented event.",
        "required": ["market"],
        "optional": ["news"],
        "forbidden": ["technology"],
    },
    {
        "request": "Tell me about the situation.",
        "required": [],
        "optional": [],
        "forbidden": ["news", "company", "technology", "market"],
    },
    {
        "request": "No evidence exists for this query.",
        "required": [],
        "optional": [],
        "forbidden": ["news", "company", "technology", "market"],
    },
    {
        "request": "What product architecture did NVIDIA release?",
        "required": ["technology", "news"],
        "optional": ["company"],
        "forbidden": ["market"],
    },
    {
        "request": "Who acquired a company in the collected articles?",
        "required": ["company", "news"],
        "optional": [],
        "forbidden": ["technology", "market"],
    },
    {
        "request": "What was the market reaction to the acquisition?",
        "required": ["news", "company", "market"],
        "optional": [],
        "forbidden": ["technology"],
    },
    {
        "request": "Summarize Apple headlines from the staged corpus.",
        "required": ["news"],
        "optional": ["company"],
        "forbidden": ["technology", "market"],
    },
    {
        "request": "Is there an evidenced partnership between NVIDIA and Microsoft?",
        "required": ["company"],
        "optional": ["news"],
        "forbidden": ["technology", "market"],
    },
    {
        "request": "Report NVDA volume and volatility around the event window.",
        "required": ["market"],
        "optional": ["news"],
        "forbidden": ["technology"],
    },
    {
        "request": "What GPU or accelerator does the corpus attribute to AMD?",
        "required": ["technology"],
        "optional": ["company", "news"],
        "forbidden": ["market"],
    },
]


def score_selection(selected: Sequence[str], case: Dict[str, Any]) -> Dict[str, Any]:
    chosen = list(selected)
    required = set(case["required"])
    optional = set(case["optional"])
    forbidden = set(case["forbidden"])
    picked = set(chosen)
    return {
        "required_hit": required <= picked,
        "missing": sorted(required - picked),
        "forbidden": sorted(picked & forbidden),
        "unnecessary": sorted(picked - required - optional),
    }


def evaluate_provider(provider) -> Dict[str, Any]:
    """One planner call per labeled query. Does not run specialists."""
    from app.research.graph import create_research_plan_node
    from app.research.runtime import research_llm
    from app.research.schemas import empty_package

    token = research_llm.set(provider)
    rows = []
    try:
        for case in PLANNER_EVAL_CASES:
            state = {
                "request": {"query": case["request"], "entity_ids": [], "event_ids": []},
                "evidence_package": empty_package(case["request"]),
                "errors": [],
                "metadata": {},
            }
            out = create_research_plan_node(state)
            plan = out["plan"]
            master = next(item for item in out["metadata"]["agent_runs"] if item["agent"] == "master")
            scored = score_selection(plan["selected_agents"], case)
            rows.append(
                {
                    "request": case["request"],
                    "source": plan["source"],
                    "selected": plan["selected_agents"],
                    "error_type": master.get("error_type"),
                    "retries": master.get("retry_count") or 0,
                    **scored,
                }
            )
    finally:
        research_llm.reset(token)
    return {"summary": summarize(rows), "rows": rows}


def summarize(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    count = len(rows) or 1
    valid = sum(1 for row in rows if row["source"] == "llm")
    fallback = sum(1 for row in rows if row["source"] == "fallback")
    malformed = sum(1 for row in rows if row.get("error_type") == "malformed")
    retried = sum(1 for row in rows if int(row.get("retries") or 0) > 0)
    required_hits = sum(1 for row in rows if row["required_hit"])
    forbidden = sum(len(row["forbidden"]) for row in rows)
    unnecessary = sum(len(row["unnecessary"]) for row in rows)
    selected = sum(len(row["selected"]) for row in rows)
    return {
        "queries": len(rows),
        "valid_plans": valid,
        "fallback_plans": fallback,
        "malformed_errors": malformed,
        "retries": retried,
        "required_domain_hits": required_hits,
        "required_domain_rate": round(required_hits / count, 4),
        "forbidden_domain_count": forbidden,
        "unnecessary_domain_count": unnecessary,
        "unnecessary_domain_rate": round(unnecessary / selected, 4) if selected else 0.0,
    }


def main() -> None:
    import json

    from app.research.llm import get_llm_provider

    report = evaluate_provider(get_llm_provider())
    print(json.dumps(report["summary"], indent=2))
    for row in report["rows"]:
        print(
            f"{row['source']:8} retry={row['retries']} err={row['error_type']} "
            f"selected={row['selected']} missing={row['missing']} forbidden={row['forbidden']} "
            f"extra={row['unnecessary']} :: {row['request']}"
        )


if __name__ == "__main__":
    main()
