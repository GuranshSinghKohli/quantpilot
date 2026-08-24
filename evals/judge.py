"""Optional LLM-as-judge for subjective dimensions only.

Never used for citation existence, URL validity, timestamps, or evidence IDs.
Default is off. Results are labeled HUMAN-EVALUATED / LLM-JUDGE, not AUTOMATED.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

RUBRIC = {
    "hypothesis_quality": "1=poor … 5=excellent plausible explanations",
    "evidence_relevance": "1=poor … 5=excellent contemporaneous sources",
    "conclusion_quality": "1=poor … 5=excellent calibrated conclusion",
    "devils_advocate_usefulness": "1=generic … 5=specific counterargument",
}


def judge_enabled() -> bool:
    return os.getenv("QUANTPILOT_EVAL_LLM_JUDGE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    } and bool(os.getenv("OPENAI_API_KEY"))


def judge_limitations() -> List[str]:
    return [
        "LLM-as-judge is subjective and may disagree with a human reviewer.",
        "It is not used for citation existence, URLs, timestamps, or IDs.",
        "Scores are labeled LLM-JUDGE, never mixed into AUTOMATED aggregates.",
    ]


def score_subjective(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Structured judge. Returns empty dict when disabled or on failure."""
    if not judge_enabled():
        return {}
    try:
        from openai import OpenAI

        client = OpenAI()
        model = os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
        prompt = (
            "Score 1-5 integers for: hypothesis_quality, evidence_relevance, "
            "conclusion_quality, devils_advocate_usefulness. "
            "Return JSON only. Rubric: "
            + json.dumps(RUBRIC)
            + "\nInput:\n"
            + json.dumps(payload)[:6000]
        )
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        text = resp.choices[0].message.content or "{}"
        data = json.loads(text)
        return {
            "label": "LLM-JUDGE",
            "judge_model": model,
            "rubric": RUBRIC,
            "scores": data,
        }
    except Exception as exc:
        return {"label": "LLM-JUDGE", "error": str(exc)[:300]}
