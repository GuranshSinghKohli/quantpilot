from typing import Any, Dict, List, Optional


def separate_facts_and_insights(
    final_report: Dict[str, Any],
    metrics_output: Dict[str, Any],
    sec_output: Dict[str, Any],
    filings_data: Optional[Dict[str, Any]] = None,
    news_headlines: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    facts: List[Dict[str, str]] = []
    insights: List[Dict[str, str]] = []

    key_metrics = metrics_output.get("key_metrics") or {}
    for key, value in key_metrics.items():
        if value is not None:
            facts.append(
                {
                    "content": f"{key}: {value}",
                    "source": "yfinance / MCP get_stock_fundamentals",
                }
            )

    if filings_data:
        cik = filings_data.get("cik")
        if cik:
            facts.append({"content": f"CIK: {cik}", "source": "SEC EDGAR"})
        company = filings_data.get("company_name")
        if company:
            facts.append({"content": f"Company: {company}", "source": "SEC EDGAR"})

    latest_type = sec_output.get("latest_filing_type")
    if latest_type and latest_type != "N/A":
        facts.append(
            {
                "content": f"Latest filing type: {latest_type}",
                "source": "SEC EDGAR / MCP get_recent_filings",
            }
        )

    if filings_data:
        for filing in (filings_data.get("filings") or [])[:3]:
            form = filing.get("form_type", "")
            date = filing.get("filing_date", "")
            if form:
                facts.append(
                    {
                        "content": f"{form} filed on {date}",
                        "source": "SEC EDGAR",
                    }
                )

    if news_headlines:
        for headline in news_headlines[:5]:
            title = headline.get("title") or headline.get("headline")
            if title:
                facts.append(
                    {
                        "content": title,
                        "source": headline.get("publisher", "yfinance news"),
                    }
                )

    exec_summary = final_report.get("executive_summary", "")
    if exec_summary:
        insights.append(
            {
                "content": exec_summary,
                "generated_by": "report_agent / LLM",
            }
        )

    for section in final_report.get("sections") or []:
        title = section.get("title", "Section")
        content = section.get("content", "")
        if content:
            insights.append(
                {
                    "content": f"{title}: {content}",
                    "generated_by": "report_agent / LLM",
                }
            )

    rec = final_report.get("recommendation", "")
    if rec:
        insights.append(
            {
                "content": f"Recommendation: {rec}",
                "generated_by": "report_agent / LLM",
            }
        )

    metrics_summary = metrics_output.get("analysis_summary")
    if metrics_summary:
        insights.append(
            {
                "content": metrics_summary,
                "generated_by": "financial_metrics_agent / LLM",
            }
        )

    sec_summary = sec_output.get("filing_summary")
    if sec_summary:
        insights.append(
            {
                "content": sec_summary,
                "generated_by": "sec_filing_agent / LLM",
            }
        )

    for signal in sec_output.get("risk_signals") or []:
        insights.append(
            {
                "content": signal,
                "generated_by": "sec_filing_agent / LLM",
            }
        )

    return {"facts": facts, "insights": insights}
