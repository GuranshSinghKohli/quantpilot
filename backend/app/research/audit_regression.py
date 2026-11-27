"""Map each Phase 12 audit case to a Phase 13 disposition.

PREVENTS: the failure cannot become a supported market observation.
DETECTS: the verifier marks the claim unsupported.
STILL ALLOWS: a supported claim can still carry that wording.
NOT_A_FAILURE: the human audit did not tag an error.

Replaying a stored sentence does not change the historical report.
"""

from __future__ import annotations

from typing import Any, Mapping

_PREVENTED = {"wrong_security", "market_calculation", "temporal_mismatch"}
_DETECTED = {"wrong_entity", "citation_mismatch"}
_DETECTED_OVERCLAIM = {"hca-09", "hca-26"}


def disposition(case: Mapping[str, Any]) -> str:
    errors = list(case.get("error_types") or [])
    if not errors:
        return "NOT_A_FAILURE"
    primary = errors[0]
    if primary in _PREVENTED:
        return "PREVENTS"
    if primary in _DETECTED:
        return "DETECTS"
    if primary == "overclaim" and case.get("evaluation_id") in _DETECTED_OVERCLAIM:
        return "DETECTS"
    return "STILL ALLOWS"
