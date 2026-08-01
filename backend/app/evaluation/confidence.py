from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, round(value, 3)))


def score_news_agent(
    output: Dict[str, Any],
    headlines: Optional[List[Dict[str, Any]]] = None,
) -> float:
    score = 1.0
    articles = headlines if headlines is not None else []
    count = len(articles)

    if count == 0:
        score -= 0.3
    elif count < 3:
        score -= 0.2

    if output.get("sentiment") == "neutral":
        score -= 0.1

    return _clamp(score)


def score_financial_agent(output: Dict[str, Any]) -> float:
    score = 1.0
    metrics = output.get("key_metrics") or {}

    if metrics.get("pe_ratio") is None:
        score -= 0.2
    if metrics.get("market_cap") is None:
        score -= 0.2

    key_fields = [
        "current_price",
        "pe_ratio",
        "market_cap",
        "fifty_two_week_high",
        "fifty_two_week_low",
        "eps",
    ]
    present = sum(1 for k in key_fields if metrics.get(k) is not None)
    if present < 3:
        score -= 0.1

    return _clamp(score)


def _parse_filing_date(date_str: str) -> Optional[datetime]:
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(date_str[:10], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def score_sec_agent(
    output: Dict[str, Any],
    filings_data: Optional[Dict[str, Any]] = None,
) -> float:
    score = 1.0
    filings = []
    if filings_data:
        filings = filings_data.get("filings") or []

    if not filings:
        score -= 0.3
    else:
        latest_date = _parse_filing_date(filings[0].get("filing_date", ""))
        if latest_date:
            age_days = (datetime.now(timezone.utc) - latest_date).days
            if age_days > 90:
                score -= 0.2

    if not output.get("risk_signals"):
        score -= 0.1

    return _clamp(score)


def score_earnings_agent(output: Dict[str, Any]) -> float:
    score = 0.7
    summary = output.get("earnings_summary") or ""
    points = output.get("key_points") or []
    if len(summary.split()) < 20:
        score -= 0.15
    if len(points) < 2:
        score -= 0.1
    if output.get("tone") == "unknown":
        score -= 0.1
    return _clamp(score)


def score_macro_agent(output: Dict[str, Any]) -> float:
    score = 0.65
    themes = output.get("themes") or []
    if not themes or themes == ["limited macro signal"]:
        score -= 0.15
    if (output.get("relevance") or "none") in ("none", "low"):
        score -= 0.1
    if len(output.get("macro_summary") or "") < 40:
        score -= 0.1
    return _clamp(score)


def score_verification_agent(output: Dict[str, Any]) -> float:
    grounded = output.get("groundedness_score")
    if isinstance(grounded, (int, float)):
        score = float(grounded)
    else:
        score = 0.5
    unsupported = output.get("unsupported_claims") or []
    if unsupported:
        score -= 0.05 * min(4, len(unsupported))
    verified = output.get("verified_claims") or []
    if not verified:
        score -= 0.1
    return _clamp(score)


def score_risk_agent(
    news_confidence: float,
    financial_confidence: float,
    sec_confidence: float,
    earnings_confidence: float = 0.5,
    macro_confidence: float = 0.5,
) -> float:
    score = (
        news_confidence
        + financial_confidence
        + sec_confidence
        + earnings_confidence
        + macro_confidence
    ) / 5.0
    if min(news_confidence, financial_confidence, sec_confidence) < 0.5:
        score -= 0.1
    return _clamp(score)


def score_debate_agent(output: Dict[str, Any]) -> float:
    score = 0.7
    thesis = output.get("thesis") or ""
    points = output.get("key_points") or []
    if len(thesis.split()) < 15:
        score -= 0.15
    if len(points) < 2:
        score -= 0.15
    return _clamp(score)


def score_report_agent(
    output: Dict[str, Any],
    risk_confidence: float,
) -> float:
    score = risk_confidence
    summary = output.get("executive_summary") or ""
    if len(summary.split()) < 50:
        score -= 0.1
    sections = output.get("sections") or []
    if len(sections) < 3:
        score -= 0.1
    return _clamp(score)


def score_memo_agent(
    output: Dict[str, Any],
    report_confidence: float = 0.5,
) -> float:
    score = report_confidence
    thesis = output.get("investment_thesis") or ""
    if len(thesis.split()) < 25:
        score -= 0.1
    if not output.get("key_numbers"):
        score -= 0.1
    if not output.get("risks"):
        score -= 0.1
    if (output.get("decision") or "").upper() not in {"BUY", "HOLD", "SELL", "WATCH"}:
        score -= 0.1
    return _clamp(score)


def overall_confidence(per_agent: Dict[str, float]) -> float:
    weights = {
        "news": 0.09,
        "financial": 0.14,
        "sec": 0.11,
        "earnings": 0.07,
        "macro": 0.05,
        "risk": 0.12,
        "bull": 0.04,
        "bear": 0.04,
        "verification": 0.09,
        "report": 0.13,
        "memo": 0.12,
    }
    total = 0.0
    weight_sum = 0.0
    for key, weight in weights.items():
        if key in per_agent:
            total += per_agent[key] * weight
            weight_sum += weight
    if weight_sum == 0:
        return 0.0
    return _clamp(total / weight_sum)
