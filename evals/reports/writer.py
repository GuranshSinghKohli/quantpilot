"""Write structured + human-readable reports from an actual run."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from evals.schemas import EvaluationRun


def write_report(run: EvaluationRun, directory: Path) -> Dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    raw = directory / f"{run.run_id}.json"
    md = directory / f"{run.run_id}.md"
    latest_json = directory / "last_run.json"
    latest_md = directory / "last_run.md"
    payload = json.loads(run.model_dump_json())
    raw.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    latest_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    text = render_markdown(run)
    md.write_text(text, encoding="utf-8")
    latest_md.write_text(text, encoding="utf-8")
    return {"json": raw, "md": md}


def render_markdown(run: EvaluationRun) -> str:
    lines: List[str] = [
        f"# QuantPilot evaluation — {run.dataset_version}",
        "",
        f"- run_id: `{run.run_id}`",
        f"- git_commit: `{run.git_commit}`",
        f"- benchmark: `{run.benchmark_version}`",
        f"- model: `{run.model}`",
        f"- timestamp: `{run.timestamp.isoformat()}`",
        f"- configuration: `{json.dumps(run.configuration)}`",
        f"- cases: {len(run.cases)}",
        "",
        "Numbers below are measured. None are placeholders.",
        "",
        "## Aggregate (AUTOMATED)",
        "",
    ]
    for key in sorted(run.metrics):
        lines.append(f"- **{key}**: {run.metrics[key]:.3f}")
    lines.extend(["", "## Failures", ""])
    if not run.failures:
        lines.append("No grouped failures recorded.")
    else:
        by: Dict[str, int] = {}
        for row in run.failures:
            by[str(row.get("type") or "UNKNOWN")] = by.get(str(row.get("type")), 0) + 1
        for kind, n in sorted(by.items()):
            lines.append(f"- {kind}: {n}")
        lines.append("")
        for row in run.failures[:40]:
            lines.append(
                f"- `{row.get('case_id')}` / {row.get('type')}: "
                f"expected={row.get('expected')} actual={row.get('actual')}"
            )
    if run.errors:
        lines.extend(["", "## Errors", ""])
        for err in run.errors:
            lines.append(f"- {err}")
    lines.extend(["", "## Notes", ""])
    for note in run.notes:
        lines.append(f"- {note}")
    lines.extend(
        [
            "",
            "## Human evaluation",
            "",
            "Not collected in this automated run. Rubric 1–5 for hypothesis, "
            "evidence, conclusion, and Devil's Advocate usefulness is reserved "
            "for HUMAN-EVALUATED review.",
            "",
            "## LLM-as-judge",
            "",
            "Disabled unless `QUANTPILOT_EVAL_LLM_JUDGE=1`. Never used for "
            "citation existence, URLs, timestamps, or evidence IDs.",
            "",
        ]
    )
    return "\n".join(lines) + "\n"
