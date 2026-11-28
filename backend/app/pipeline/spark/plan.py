"""Which raw objects a processing job reads, decided on the driver via the ObjectStore.

Selection is one of: an ingestion-date window (default: today, UTC), a raw key
prefix under ``raw/<domain>/``, or an explicit list of raw keys. Listing goes
through ``ObjectStore.list`` per day partition, so a small window never scans
the lake. Spark then reads exactly the listed objects by URI; routing the bytes
through the Python store would funnel every object through the driver.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Sequence, Tuple

from app.domain.enums import IngestionDomain
from app.pipeline.lake import InvalidKey, LocalObjectStore, ObjectStore
from app.pipeline.lake.keys import (
    PROCESSED,
    parse_raw_key,
    processed_prefix,
    raw_prefix,
    validate_prefix,
)
from app.pipeline.spark.config import ProcessingConfigError

DOMAINS = tuple(d.value for d in IngestionDomain)


class PlanError(ProcessingConfigError):
    pass


@dataclass(frozen=True)
class ProcessingRequest:
    domain: str
    start: Optional[date] = None
    end: Optional[date] = None
    prefix: Optional[str] = None
    objects: Tuple[str, ...] = ()
    output_prefix: Optional[str] = None


@dataclass(frozen=True)
class InputPlan:
    domain: str
    mode: str  # window | prefix | objects
    keys: Tuple[str, ...]
    output_prefix: str
    since: Optional[date] = None
    until: Optional[date] = None
    prefix: Optional[str] = None
    # Objects under the selection that are not raw event keys (never written by the lake writer).
    ignored: Tuple[str, ...] = field(default_factory=tuple)

    def describe(self) -> dict:
        return {
            "mode": self.mode,
            "since": self.since.isoformat() if self.since else None,
            "until": self.until.isoformat() if self.until else None,
            "prefix": self.prefix,
            "object_count": len(self.keys),
            "ignored_count": len(self.ignored),
        }


def plan_inputs(
    store: ObjectStore,
    request: ProcessingRequest,
    *,
    max_window_days: int,
    max_objects: int,
    today: Optional[date] = None,
) -> InputPlan:
    domain = request.domain
    if domain not in DOMAINS:
        raise PlanError(f"unknown domain {domain!r}; expected one of {list(DOMAINS)}")
    output_prefix = _output_prefix(domain, request.output_prefix)
    window = request.start is not None or request.end is not None
    if sum([bool(request.objects), request.prefix is not None, window]) > 1:
        raise PlanError("choose one input selection: a date window, --prefix, or --objects")

    ignored: List[str] = []
    if request.objects:
        keys = _explicit(store, domain, request.objects)
        plan = InputPlan(domain, "objects", keys, output_prefix)
    elif request.prefix is not None:
        prefix = _raw_prefix(domain, request.prefix)
        keys = _listed(store, domain, [prefix], ignored, max_objects)
        plan = InputPlan(domain, "prefix", keys, output_prefix, prefix=prefix)
    else:
        today = today or datetime.now(timezone.utc).date()
        end = request.end or (request.start if request.start and request.start > today else today)
        start = request.start or end
        if start > end:
            raise PlanError(f"start {start} is after end {end}")
        days = (end - start).days + 1
        if days > max_window_days:
            raise PlanError(f"window of {days} days exceeds PROCESS_MAX_WINDOW_DAYS={max_window_days}")
        prefixes = [raw_prefix(domain, start + timedelta(days=i)) for i in range(days)]
        keys = _listed(store, domain, prefixes, ignored, max_objects)
        plan = InputPlan(domain, "window", keys, output_prefix, since=start, until=end)
    if len(plan.keys) > max_objects:
        raise PlanError(f"{len(plan.keys)} input objects exceed PROCESS_MAX_INPUT_OBJECTS={max_objects}; narrow the selection")
    return replace(plan, ignored=tuple(ignored))


def _output_prefix(domain: str, value: Optional[str]) -> str:
    if value is None:
        return processed_prefix(domain)
    try:
        validate_prefix(value)
    except InvalidKey as exc:
        raise PlanError(str(exc)) from None
    if not value.startswith(f"{PROCESSED}/") or not value.endswith("/") or value == f"{PROCESSED}/":
        raise PlanError(f"--output-prefix must be a folder under {PROCESSED}/ ending in '/', got {value!r}")
    return value


def _raw_prefix(domain: str, value: str) -> str:
    try:
        validate_prefix(value)
    except InvalidKey as exc:
        raise PlanError(str(exc)) from None
    if not value.startswith(raw_prefix(domain)):
        raise PlanError(f"--prefix must start with {raw_prefix(domain)!r}, got {value!r}")
    return value


def _explicit(store: ObjectStore, domain: str, objects: Sequence[str]) -> Tuple[str, ...]:
    keys = sorted(set(objects))
    for key in keys:
        try:
            key_domain, _, _ = parse_raw_key(key)
        except InvalidKey as exc:
            raise PlanError(str(exc)) from None
        if key_domain != domain:
            raise PlanError(f"{key} is a {key_domain} object, not {domain}")
        if not store.exists(key):
            raise PlanError(f"no object at {store.uri(key)}")
    return tuple(keys)


def _listed(store: ObjectStore, domain: str, prefixes: Sequence[str], ignored: List[str], max_objects: int) -> Tuple[str, ...]:
    keys: List[str] = []
    for prefix in prefixes:
        for info in store.list(prefix):
            try:
                key_domain, _, _ = parse_raw_key(info.key)
            except InvalidKey:
                ignored.append(info.key)
                continue
            if key_domain == domain:
                keys.append(info.key)
            if len(keys) > max_objects:  # stop listing; plan_inputs reports the cap
                return tuple(sorted(keys))
    return tuple(sorted(keys))


def spark_path(store: ObjectStore, key: str) -> str:
    """URI Spark/Hadoop reads for a store key (``file://`` or ``s3a://``)."""
    if isinstance(store, LocalObjectStore):
        # Unescaped on purpose: Hadoop's Path escapes itself, and a percent-encoded
        # "year%3D2026" would name a different file.
        return "file://" + store.root.joinpath(*key.rstrip("/").split("/")).as_posix()
    bucket = getattr(store, "bucket", None)
    if store.backend == "s3" and bucket:
        return f"s3a://{bucket}/{key}"
    raise PlanError(f"Spark cannot read the {store.backend!r} object store")


def uri_prefix(store: ObjectStore) -> Tuple[str, bool]:
    """``(prefix, encode)`` such that ``store.uri(key) == prefix + (encode_key(key) if encode else key)``.

    Local stores return ``Path.as_uri()``, which percent-encodes ``=``; lake keys
    use no other character that it encodes.
    """
    if isinstance(store, LocalObjectStore):
        return store.root.as_uri() + "/", True
    bucket = getattr(store, "bucket", None)
    if store.backend == "s3" and bucket:
        return f"s3://{bucket}/", False
    raise PlanError(f"Spark cannot read the {store.backend!r} object store")


def encode_key(key: str) -> str:
    return key.replace("=", "%3D")
