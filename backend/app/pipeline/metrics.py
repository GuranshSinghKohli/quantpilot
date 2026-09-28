"""In-process pipeline metrics with Prometheus naming and a hard label policy.

Counters, gauges and summaries (count/sum) keyed by name + labels.
``snapshot()`` is logged; ``render_prometheus()`` is the text exposition
format for ``GET /metrics``. No client library: the process starts even if
nothing scrapes it.

Labels are a closed set (see ``ALLOWED_LABELS``). High-cardinality keys such
as event_id, job_id, URL, ticker or CIK are rejected. A name may have at most
``max_series`` series so a bug cannot explode Prometheus.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any, Dict, List, Tuple

Labels = Tuple[Tuple[str, str], ...]

ALLOWED_LABELS = frozenset({
    "domain", "source", "operation", "status", "component",
    "topic", "partition", "result", "kind", "provider", "reason",
    "method", "route", "entity_type", "relationship_type",
    "event_type", "calculation_version", "model", "document_type",
})

FORBIDDEN_LABELS = frozenset({
    "event_id", "job_id", "request_id", "offset", "url", "ticker", "cik",
    "name", "title", "error", "user_id", "raw_uri", "article", "payload",
    "correlation_id", "investigation_id", "entity_id", "relationship_id",
    "candidate_id", "evidence_id", "symbol", "source_id",
    "document_id", "chunk_id", "query",
})

DEFAULT_MAX_SERIES = 64


class MetricLabelError(ValueError):
    """A label key is not in the allowlist (or is explicitly forbidden)."""


class MetricCardinalityError(ValueError):
    """A metric grew past the per-name series cap."""


def _labels(labels: Dict[str, Any]) -> Labels:
    bad = [k for k in labels if k in FORBIDDEN_LABELS or k not in ALLOWED_LABELS]
    if bad:
        raise MetricLabelError(
            f"illegal Prometheus label(s) {sorted(bad)}; allowed {sorted(ALLOWED_LABELS)}"
        )
    return tuple(sorted((k, str(v)) for k, v in labels.items()))


def _fmt(name: str, labels: Labels) -> str:
    if not labels:
        return name
    inner = ",".join(f'{k}="{v}"' for k, v in labels)
    return f"{name}{{{inner}}}"


class Metrics:
    def __init__(self, namespace: str = "quantpilot", max_series: int = DEFAULT_MAX_SERIES) -> None:
        self.namespace = namespace
        self.max_series = max_series
        self._lock = threading.Lock()
        self._counters: Dict[Tuple[str, Labels], float] = defaultdict(float)
        self._gauges: Dict[Tuple[str, Labels], float] = {}
        self._summaries: Dict[Tuple[str, Labels], List[float]] = {}
        self._series_count: Dict[str, int] = defaultdict(int)

    def _name(self, name: str) -> str:
        return f"{self.namespace}_{name}"

    def _admit(self, name: str, labels: Labels, store: Dict) -> None:
        if (name, labels) in store:
            return
        used = self._series_count[name]
        if used >= self.max_series:
            raise MetricCardinalityError(
                f"{name} already has {used} series (cap {self.max_series}); "
                "refusing a new label set"
            )
        self._series_count[name] = used + 1

    def inc(self, name: str, amount: float = 1, **labels: Any) -> None:
        key_labels = _labels(labels)
        full = self._name(name)
        with self._lock:
            self._admit(full, key_labels, self._counters)
            self._counters[(full, key_labels)] += amount

    def set(self, name: str, value: float, **labels: Any) -> None:
        key_labels = _labels(labels)
        full = self._name(name)
        with self._lock:
            self._admit(full, key_labels, self._gauges)
            self._gauges[(full, key_labels)] = value

    def observe(self, name: str, value: float, **labels: Any) -> None:
        key_labels = _labels(labels)
        full = self._name(name)
        with self._lock:
            self._admit(full, key_labels, self._summaries)
            key = (full, key_labels)
            count, total, peak = self._summaries.get(key, [0, 0.0, 0.0])
            self._summaries[key] = [count + 1, total + value, max(peak, value)]

    def value(self, name: str, **labels: Any) -> float:
        key = (self._name(name), _labels(labels))
        with self._lock:
            if key in self._counters:
                return self._counters[key]
            if key in self._gauges:
                return self._gauges[key]
            return self._summaries.get(key, [0])[0]

    def total(self, name: str) -> float:
        """Sum of a counter across all label sets."""
        full = self._name(name)
        with self._lock:
            return sum(v for (n, _), v in self._counters.items() if n == full)

    def label_keys(self) -> List[str]:
        keys = set()
        with self._lock:
            for series in (self._counters, self._gauges, self._summaries):
                for (_, labels) in series:
                    keys.update(k for k, _ in labels)
        return sorted(keys)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            out: Dict[str, Any] = {}
            for (name, labels), value in sorted(self._counters.items()):
                out[_fmt(name, labels)] = value
            for (name, labels), value in sorted(self._gauges.items()):
                out[_fmt(name, labels)] = value
            for (name, labels), (count, total, peak) in sorted(self._summaries.items()):
                out[_fmt(name, labels)] = {"count": count, "sum": round(total, 6), "max": round(peak, 6)}
            return out

    def render_prometheus(self) -> str:
        lines: List[str] = []
        with self._lock:
            for kind, series in (("counter", self._counters), ("gauge", self._gauges)):
                seen = set()
                for (name, labels), value in sorted(series.items()):
                    if name not in seen:
                        lines.append(f"# TYPE {name} {kind}")
                        seen.add(name)
                    lines.append(f"{_fmt(name, labels)} {value:g}")
            seen = set()
            for (name, labels), (count, total, _) in sorted(self._summaries.items()):
                if name not in seen:
                    lines.append(f"# TYPE {name} summary")
                    seen.add(name)
                lines.append(f"{_fmt(name + '_count', labels)} {count:g}")
                lines.append(f"{_fmt(name + '_sum', labels)} {total:g}")
        return "\n".join(lines) + ("\n" if lines else "")

    def merge_from(self, other: "Metrics") -> None:
        """Copy series from another registry (used to join HTTP + DB gauges)."""
        with other._lock:
            counters = list(other._counters.items())
            gauges = list(other._gauges.items())
            summaries = list(other._summaries.items())
        with self._lock:
            for (name, labels), value in counters:
                self._admit(name, labels, self._counters)
                self._counters[(name, labels)] += value
            for (name, labels), value in gauges:
                self._admit(name, labels, self._gauges)
                self._gauges[(name, labels)] = value
            for (name, labels), triple in summaries:
                self._admit(name, labels, self._summaries)
                self._summaries[(name, labels)] = list(triple)
