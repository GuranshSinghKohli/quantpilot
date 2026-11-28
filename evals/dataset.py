"""Load and validate the evaluation pack. Ground truth stays in PackedCase.truth."""

from __future__ import annotations

from typing import List

from evals.datasets.catalog import load_pack
from evals.schemas import PackedCase
from evals.temporal import parse_time


class DatasetError(ValueError):
    pass


def load_dataset(version: str = "v1") -> List[PackedCase]:
    packs = load_pack(version)
    errors = []
    for pack in packs:
        errors.extend(validate_case(pack))
    if errors:
        raise DatasetError("Invalid dataset:\n" + "\n".join(errors[:20]))
    return packs


def validate_case(pack: PackedCase) -> List[str]:
    errs: List[str] = []
    case = pack.case
    if not case.case_id:
        errs.append("missing case_id")
    if not case.cutoff_time or parse_time(case.cutoff_time) is None:
        errs.append(f"{case.case_id}: missing/invalid cutoff_time")
    if not case.event_timestamp or parse_time(case.event_timestamp) is None:
        errs.append(f"{case.case_id}: missing/invalid event_timestamp")
    if not case.ticker:
        errs.append(f"{case.case_id}: missing ticker")
    if pack.truth.expected_materiality not in {
        "HIGH_MATERIALITY",
        "MEDIUM_MATERIALITY",
        "LOW_MATERIALITY",
    }:
        errs.append(f"{case.case_id}: malformed expected materiality")
    cutoff = parse_time(case.cutoff_time)
    event_t = parse_time(case.event_timestamp)
    if cutoff and event_t and cutoff < event_t and not case.allow_retrospective:
        errs.append(f"{case.case_id}: cutoff before event without retrospective flag")
    return errs


def public_case_view(pack: PackedCase) -> dict:
    """What an agent is allowed to see. No acceptable answers."""
    return pack.case.model_dump()
