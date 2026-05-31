from typing import Any, Dict, List, Tuple, Type

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.models.agent_schemas import (
    FinalReportOutput,
    FinancialMetricsAgentOutput,
    NewsAgentOutput,
    RiskAgentOutput,
    SECFilingAgentOutput,
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


class ValidatedFinalReport(FinalReportOutput):
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


def validate_report(data: Dict[str, Any], ticker: str) -> Tuple[Dict[str, Any], List[str]]:
    defaults = {
        "ticker": ticker,
        "report_title": f"{ticker} — QuantPilot Research Report",
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
