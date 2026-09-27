"""Process-local HTTP metrics. Route families, never ticker/id paths."""

from __future__ import annotations

from app.pipeline.metrics import Metrics

HTTP_METRICS = Metrics()

_FIXED = {"/health", "/ready", "/metrics", "/docs", "/redoc", "/openapi.json"}


def route_family(path: str) -> str:
    if path in _FIXED:
        return path
    if path.startswith("/api/"):
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2:
            return f"/api/{parts[1]}"
    return "/other"


def observe_request(path: str, method: str, status_code: int, duration_s: float) -> None:
    if path == "/metrics":
        return
    route = route_family(path)
    status = f"{status_code // 100}xx"
    HTTP_METRICS.inc("http_requests_total", route=route, method=method, status=status)
    HTTP_METRICS.observe("http_request_duration_seconds", duration_s, route=route, method=method)
