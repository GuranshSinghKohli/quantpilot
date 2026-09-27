"""Data-quality checks: the catalogue of reject codes and how rules become a reject reason.

Each domain transformer lists ``Rule(code, failed, message)`` in a fixed order;
``first_failure`` turns them into two Spark columns, the code and message of the
first rule a row fails (null when it passes all of them). A rejected row is
never dropped: it becomes a reject object, and the counts per code go to the
manifest. The catalogue is pure data so docs and tests can read it without a JVM.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple


@dataclass(frozen=True)
class Check:
    code: str
    description: str


CHECKS: Dict[str, Check] = {
    check.code: check
    for check in (
        # object level
        Check("malformed_json", "not a JSON document with the envelope structure (wrong JSON or wrong types)"),
        Check("unreadable_object", "the object produced no JSON document (empty or unreadable)"),
        Check("multiple_documents", "the object holds more than one JSON document"),
        # envelope
        Check("missing_required_field", "a required envelope or record field is null or empty"),
        Check("unsupported_schema_version", "schema_version is not one the contracts support"),
        Check("domain_mismatch", "the envelope domain differs from the job's domain"),
        Check("invalid_identifier", "event_id/content_hash not 64 lowercase hex, run_id not a UUID, or an id too long"),
        Check("event_id_key_mismatch", "event_id differs from the event id in the raw object key"),
        Check("unknown_source", "source is not a registered source for this domain"),
        Check("source_type_mismatch", "source_type differs from the registered source's type"),
        Check("record_envelope_mismatch", "record.source or record.content_hash differs from the envelope"),
        Check("identity_mismatch", "event_id != sha256(source|external_id|content_hash), or external_id does not match the record"),
        Check("invalid_timestamp", "not ISO 8601 with an explicit offset, or not a real date/time"),
        Check("timestamp_out_of_range", "before 1970-01-01 or more than one day after the job started"),
        # domain
        Check("invalid_text", "text longer than the contract allows"),
        Check("invalid_url", "url is not an absolute http(s) URL of at most 2048 characters"),
        Check("invalid_symbol", "a ticker/symbol does not match the contract's symbol rule"),
        Check("invalid_interval", "bar interval is not 1d"),
        Check("invalid_numeric", "a price is not a plain decimal that fits DECIMAL(20,6)"),
        Check("non_positive_price", "a price is <= 0 (the contract requires > 0)"),
        Check("invalid_volume", "volume is not a non-negative whole number"),
        Check("high_below_low", "high < low"),
        Check("ohlc_inconsistent", "open or close outside [low, high]"),
        Check("trading_date_mismatch", "bar timestamp more than one calendar day from trading_date"),
        Check("invalid_currency", "currency present but not a 3-letter upper-case code"),
        Check("invalid_cik", "CIK is not exactly 10 digits"),
        Check("ticker_mismatch", "ticker is not the first entry of tickers"),
    )
}


@dataclass(frozen=True)
class Rule:
    code: str
    failed: Any  # pyspark Column: true when the row fails; null counts as passing
    message: Any  # pyspark Column or str


def first_failure(rules: Sequence[Rule]) -> Tuple[Any, Any]:
    from pyspark.sql import functions as F

    unknown = [r.code for r in rules if r.code not in CHECKS]
    if unknown:
        raise KeyError(f"rules use undocumented check codes: {unknown}")
    def message(rule: Rule):
        # A null message must not let coalesce fall through to a later rule's text.
        text = F.lit(rule.message) if isinstance(rule.message, str) else rule.message
        return F.coalesce(text, F.lit(CHECKS[rule.code].description))

    code = F.coalesce(*[F.when(r.failed, F.lit(r.code)) for r in rules])
    text = F.coalesce(*[F.when(r.failed, message(r)) for r in rules])
    return code, text


def describe_checks(codes: Sequence[str]) -> List[Dict[str, str]]:
    return [{"code": c, "description": CHECKS[c].description} for c in codes]
