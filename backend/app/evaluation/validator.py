from typing import Any, Dict, List, Tuple, Type

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.models.agent_schemas import (
    EarningsAgentOutput,
    FinalReportOutput,
    FinancialMetricsAgentOutput,
    InvestmentMemoOutput,
    MacroAgentOutput,
    NewsAgentOutput,
    RiskAgentOutput,
    SECFilingAgentOutput,
    VerificationAgentOutput,
)


class ValidatedNewsOutput(NewsAgentOutput):
    confidence_score: float = Field(default=0.5, ge=0.0, le=1.0)
    validation_warning: str = ""
    error_message: str = ""

    @field_validator("summary")
    @classmethod
    def summary_min_length(cls, v: str) -> str:
        if len(v) < 20:
            return v + " (auto-extended for validation)"
        return v

    @field_validator("key_themes")
    @classmethod
    def themes_not_empty(cls, v: List[str]) -> List[str]:
        if not v:
            return ["General market coverage"]
        return v


class ValidatedFinancialOutput(FinancialMetricsAgentOutput):
    confidence_score: float = Field(default=0.5, ge=0.0, le=1.0)
    validation_warning: str = ""
    error_message: str = ""


class ValidatedSECOutput(SECFilingAgentOutput):
    confidence_score: float = Field(default=0.5, ge=0.0, le=1.0)
    validation_warning: str = ""
    error_message: str = ""


class ValidatedRiskOutput(RiskAgentOutput):
    validation_warning: str = ""
    error_message: str = ""


class ValidatedEarningsOutput(EarningsAgentOutput):
    confidence_score: float = Field(default=0.5, ge=0.0, le=1.0)
    validation_warning: str = ""
    error_message: str = ""


class ValidatedMacroOutput(MacroAgentOutput):
    confidence_score: float = Field(default=0.5, ge=0.0, le=1.0)
    validation_warning: str = ""
    error_message: str = ""


class ValidatedVerificationOutput(VerificationAgentOutput):
    confidence_score: float = Field(default=0.5, ge=0.0, le=1.0)
    validation_warning: str = ""
    error_message: str = ""


class ValidatedFinalReport(FinalReportOutput):
    confidence_score: float = Field(default=0.5, ge=0.0, le=1.0)
    validation_warning: str = ""
    error_message: str = ""


class ValidatedInvestmentMemo(InvestmentMemoOutput):
    confidence_score: float = Field(default=0.5, ge=0.0, le=1.0)
    validation_warning: str = ""
    error_message: str = ""


def _normalize_recommendation(rec: str) -> str:
    upper = (rec or "").upper()
    if "BUY" in upper and "SELL" not in upper:
        return "BUY"
    if "SELL" in upper:
        return "SELL"
    return "HOLD"


def _normalize_memo_decision(rec: str) -> str:
    upper = (rec or "").upper()
    if "BUY" in upper and "SELL" not in upper:
        return "BUY"
    if "SELL" in upper:
        return "SELL"
    if "WATCH" in upper:
        return "WATCH"
    return "HOLD"


def _normalize_conviction(value: str) -> str:
    lower = (value or "").strip().lower()
    if lower in ("low", "medium", "high"):
        return lower
    return "medium"


def validate_output(
    model_cls: Type[BaseModel],
    data: Dict[str, Any],
    defaults: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[str]]:
    warnings: List[str] = []
    merged = {**defaults, **(data or {})}

    if model_cls is FinalReportOutput or model_cls is ValidatedFinalReport:
        merged["recommendation"] = _normalize_recommendation(
            merged.get("recommendation", "HOLD")
        )
        if len(merged.get("executive_summary", "").split()) < 50:
            merged["executive_summary"] = (
                merged.get("executive_summary", "")
                + " Further analysis is recommended before making investment decisions."
            )
            warnings.append("Executive summary was too short and was extended.")
        if len(merged.get("sections") or []) < 2:
            merged["sections"] = merged.get("sections") or [
                {"title": "Overview", "content": merged.get("executive_summary", "")},
                {"title": "Risk Factors", "content": "See risk assessment for details."},
            ]
            warnings.append("Report sections below minimum; default sections added.")

    if model_cls is InvestmentMemoOutput or model_cls is ValidatedInvestmentMemo:
        merged["decision"] = _normalize_memo_decision(merged.get("decision", "HOLD"))
        merged["conviction"] = _normalize_conviction(merged.get("conviction", "medium"))
        if not merged.get("one_liner"):
            merged["one_liner"] = merged.get("investment_thesis", "")[:280]
        if not merged.get("key_numbers"):
            merged["key_numbers"] = ["Limited quantitative inputs available."]
        if not merged.get("risks"):
            merged["risks"] = ["Review full report risk section."]
        if len(merged.get("investment_thesis", "").split()) < 20:
            merged["investment_thesis"] = (
                (merged.get("investment_thesis") or "")
                + " Memo generated with limited validated inputs; treat as educational only."
            ).strip()
            warnings.append("Investment thesis was too short and was extended.")

    try:
        validated = model_cls.model_validate(merged)
        return validated.model_dump(), warnings
    except ValidationError as exc:
        warnings.append(f"Validation auto-corrected: {exc.errors()[0]['msg']}")
        for err in exc.errors():
            loc = err.get("loc", ())
            if loc:
                field = loc[0]
                if field in defaults:
                    merged[field] = defaults[field]
        try:
            validated = model_cls.model_validate(merged)
            return validated.model_dump(), warnings
        except ValidationError:
            return {**defaults, "validation_warning": "; ".join(warnings)}, warnings


def validate_news(data: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    defaults = {
        "sentiment": "neutral",
        "summary": "Insufficient news data available for detailed sentiment analysis.",
        "key_themes": ["Limited news coverage"],
        "confidence_score": data.get("confidence_score", 0.5),
        "error_message": data.get("error_message", ""),
    }
    return validate_output(ValidatedNewsOutput, data, defaults)


def validate_financial(data: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    defaults = {
        "valuation_rating": "fairly valued",
        "analysis_summary": "Financial metrics unavailable; using conservative default assessment.",
        "key_metrics": {},
        "confidence_score": data.get("confidence_score", 0.5),
        "error_message": data.get("error_message", ""),
    }
    return validate_output(ValidatedFinancialOutput, data, defaults)


def validate_sec(data: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    defaults = {
        "filing_summary": "No recent SEC filings available for review.",
        "risk_signals": ["SEC filing data unavailable"],
        "latest_filing_type": "N/A",
        "confidence_score": data.get("confidence_score", 0.5),
        "error_message": data.get("error_message", ""),
    }
    return validate_output(ValidatedSECOutput, data, defaults)


def validate_risk(data: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    defaults = {
        "risk_level": "MEDIUM",
        "risk_factors": ["Insufficient data for granular risk scoring"],
        "confidence_score": data.get("confidence_score", 0.5),
        "error_message": data.get("error_message", ""),
    }
    return validate_output(ValidatedRiskOutput, data, defaults)


def validate_earnings(data: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    defaults = {
        "earnings_summary": "Earnings context unavailable from current data feeds.",
        "tone": "unknown",
        "key_points": ["Limited earnings signals"],
        "next_catalyst": "Monitor upcoming earnings calendar and filings.",
        "sources": data.get("sources") or [],
        "confidence_score": data.get("confidence_score", 0.5),
        "error_message": data.get("error_message", ""),
    }
    return validate_output(ValidatedEarningsOutput, data, defaults)


def validate_macro(data: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    defaults = {
        "macro_summary": "No strong macro linkage identified from available headlines.",
        "relevance": "none",
        "themes": ["limited macro signal"],
        "portfolio_implications": ["Revisit after major CPI/FOMC prints."],
        "confidence_score": data.get("confidence_score", 0.5),
        "error_message": data.get("error_message", ""),
    }
    return validate_output(ValidatedMacroOutput, data, defaults)


def validate_verification(data: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    defaults = {
        "verified_claims": ["Core market/filing inputs present where available."],
        "unsupported_claims": [],
        "coverage_notes": ["Verification completed with limited inputs."],
        "groundedness_score": data.get("groundedness_score", 0.5),
        "confidence_score": data.get("confidence_score", 0.5),
        "error_message": data.get("error_message", ""),
    }
    return validate_output(ValidatedVerificationOutput, data, defaults)


def validate_report(data: Dict[str, Any], ticker: str) -> Tuple[Dict[str, Any], List[str]]:
    defaults = {
        "ticker": ticker,
        "report_title": f"{ticker}: QuantPilot Research Report",
        "executive_summary": (
            "Automated research report generated with limited validated inputs. "
            "Review all metrics and filings before acting on this analysis."
        ),
        "sections": [
            {"title": "Summary", "content": "See executive summary."},
            {"title": "Risk", "content": "Risk assessment pending additional data."},
        ],
        "recommendation": "HOLD",
        "disclaimer": (
            "This report is generated by AI for educational purposes only "
            "and is not financial advice."
        ),
        "confidence_score": data.get("confidence_score", 0.5),
        "error_message": data.get("error_message", ""),
    }
    return validate_output(ValidatedFinalReport, data, defaults)


def validate_memo(data: Dict[str, Any], ticker: str) -> Tuple[Dict[str, Any], List[str]]:
    defaults = {
        "ticker": ticker,
        "memo_title": f"{ticker} Investment Memo",
        "one_liner": f"Automated investment memo for {ticker}.",
        "investment_thesis": (
            f"Multi-agent research memo for {ticker} generated with limited "
            "validated inputs. Review the full report before deciding."
        ),
        "key_numbers": ["Limited quantitative inputs available."],
        "catalysts": [],
        "risks": ["Review full report risk section."],
        "bull_case_summary": "Bull case unavailable.",
        "bear_case_summary": "Bear case unavailable.",
        "decision": "HOLD",
        "conviction": "medium",
        "time_horizon": "3-12 months",
        "what_would_change_my_mind": [
            "Material change in fundamentals or filings.",
        ],
        "disclaimer": (
            "This investment memo is generated by AI for educational purposes only "
            "and is not financial advice."
        ),
        "confidence_score": data.get("confidence_score", 0.5),
        "error_message": data.get("error_message", ""),
    }
    return validate_output(ValidatedInvestmentMemo, data, defaults)
