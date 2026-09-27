"""In-process pipeline metrics with Prometheus naming.

No client library: counters, gauges and summaries (count/sum/max) keyed by
name + labels. ``snapshot()`` is logged; ``render_prometheus()`` produces the
text exposition format for a future /metrics endpoint or push gateway.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any, Dict, List, Tuple

Labels = Tuple[Tuple[str, str], ...]


def _labels(labels: Dict[str, Any]) -> Labels:
    return tuple(sorted((k, str(v)) for k, v in labels.items()))


def _fmt(name: str, labels: Labels) -> str:
    if not labels:
        return name
    inner = ",".join(f'{k}="{v}"' for k, v in labels)
    return f"{name}{{{inner}}}"


class Metrics:
    def __init__(self, namespace: str = "quantpilot") -> None:
        self.namespace = namespace
        self._lock = threading.Lock()
        self._counters: Dict[Tuple[str, Labels], float] = defaultdict(float)
        self._gauges: Dict[Tuple[str, Labels], float] = {}
        self._summaries: Dict[Tuple[str, Labels], List[float]] = {}

    def _name(self, name: str) -> str:
        return f"{self.namespace}_{name}"

    def inc(self, name: str, amount: float = 1, **labels: Any) -> None:
        with self._lock:
            self._counters[(self._name(name), _labels(labels))] += amount

    def set(self, name: str, value: float, **labels: Any) -> None:
        with self._lock:
            self._gauges[(self._name(name), _labels(labels))] = value

    def observe(self, name: str, value: float, **labels: Any) -> None:
        with self._lock:
            key = (self._name(name), _labels(labels))
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
