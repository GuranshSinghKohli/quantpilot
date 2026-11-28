"""v1 historical pack. Public facts + recorded replay inputs.

Ground truth lives on PackedCase.truth and is stripped before production calls.
Price snapshots are detector inputs for the historical day, not live marks.
"""

from __future__ import annotations

from typing import Dict, List

from evals.schemas import (
    EvaluationCase,
    EvidenceFixture,
    FilingFixture,
    GroundTruth,
    HypothesisFixture,
    MemoryFixture,
    MoveFixture,
    PackedCase,
    ReplayFixture,
    ToolCallFixture,
)


def _case(**kwargs) -> EvaluationCase:
    return EvaluationCase(**kwargs)


def _truth(**kwargs) -> GroundTruth:
    return GroundTruth(**kwargs)


def _move(ticker: str, move: float, bench: float, vol: float = 2.0, **extra) -> MoveFixture:
    residual = extra.pop("residual_pct", move - bench)
    z = extra.pop("move_zscore", move / vol if vol else None)
    return MoveFixture(
        ticker=ticker,
        move_pct=move,
        benchmark_move_pct=bench,
        residual_pct=residual,
        realized_vol_pct=vol,
        move_zscore=z,
        **extra,
    )


def _ev(
    title: str,
    url: str,
    published: str,
    *,
    source_type: str = "news",
    excerpt: str = "",
    official: bool = False,
    name: str = "",
) -> EvidenceFixture:
    return EvidenceFixture(
        title=title,
        source_url=url,
        published_at=published,
        retrieved_at=published,
        source_type=source_type,
        excerpt=excerpt or title,
        official=official,
        source_name=name,
    )


def _hyp(statement: str, rank: int, idxs: List[int], **kwargs) -> HypothesisFixture:
    return HypothesisFixture(statement=statement, rank=rank, evidence_indices=idxs, **kwargs)


def load_v1() -> List[PackedCase]:
    return list(_PACKS)


def load_pack(dataset: str = "v1") -> List[PackedCase]:
    if dataset != "v1":
        raise ValueError(f"Unknown dataset {dataset}")
    return load_v1()


def cases_by_id() -> Dict[str, PackedCase]:
    return {p.case.case_id: p for p in load_v1()}


_PACKS: List[PackedCase] = [
    PackedCase(
        case=_case(
            case_id="meta-2022-02-03-earnings",
            ticker="META",
            event_timestamp="2022-02-03T00:00:00+00:00",
            cutoff_time="2022-02-03T23:59:59+00:00",
            event_type="EARNINGS",
            event_description="Meta Q4 2021: daily active users declined; shares fell sharply after the print.",
            track="investigation",
            difficulty="easy",
            tags=["earnings", "company-specific"],
            source_notes="Public Q4 2021 results; investor.fb.com / Meta IR.",
        ),
        truth=_truth(
            expected_event_types=["PRICE_MOVE", "IDIOSYNCRATIC_MOVE", "EARNINGS"],
            expected_materiality="HIGH_MATERIALITY",
            should_investigate=True,
            should_notify=True,
            acceptable_hypothesis_phrases=["daily active users declined", "user growth", "guidance"],
            unacceptable_hypothesis_phrases=["export restrictions", "bank failure"],
            required_source_domains=["sec.gov", "facebook.com", "investor"],
            expected_conclusion_strengths=["SUPPORTED", "PARTIALLY_SUPPORTED"],
            primary_source_required=True,
        ),
        fixture=ReplayFixture(
            move=_move("META", -26.4, -1.8, vol=2.4, current_price=237.0),
            fundamentals={"earnings_date": "2022-02-02", "ticker": "META"},
            evidence=[
                _ev(
                    "Meta Reports Fourth Quarter and Full Year 2021 Results",
                    "https://investor.fb.com/investor-news/press-release-details/2022/Meta-Reports-Fourth-Quarter-and-Full-Year-2021-Results/",
                    "2022-02-02T21:05:00+00:00",
                    source_type="ir_page",
                    excerpt="Daily active users declined year over year. Reality Labs losses widened.",
                    official=True,
                    name="Meta Investor Relations",
                ),
                _ev(
                    "Form 8-K earnings release",
                    "https://www.sec.gov/Archives/edgar/data/1326801/000132680122000018/fb-8k.htm",
                    "2022-02-02T21:10:00+00:00",
                    source_type="filing",
                    official=True,
                    name="SEC EDGAR",
                ),
            ],
            hypotheses=[
                _hyp("Daily active users declined and advertising outlook weakened, which is the strongest supported explanation for the decline.", 1, [0, 1], citations=["https://investor.fb.com/investor-news/press-release-details/2022/Meta-Reports-Fourth-Quarter-and-Full-Year-2021-Results/"]),
                _hyp("A broad technology selloff may have contributed but does not fully explain the idiosyncratic move.", 2, []),
            ],
            tool_trace=[ToolCallFixture(tool="get_stock_fundamentals", latency_ms=80), ToolCallFixture(tool="get_recent_filings", latency_ms=90)],
            latency_ms={"total": 4200, "verify": 40},
            llm_calls=4,
            exposure=0.12,
            filings=[FilingFixture(form_type="8-K", filing_date="2022-02-02", document_url="https://www.sec.gov/Archives/edgar/data/1326801/000132680122000018/fb-8k.htm")],
        ),
    ),
    PackedCase(
        case=_case(
            case_id="nflx-2022-04-19-subscribers",
            ticker="NFLX",
            event_timestamp="2022-04-19T00:00:00+00:00",
            cutoff_time="2022-04-19T23:59:59+00:00",
            event_type="EARNINGS",
            event_description="Netflix reported a first-quarter subscriber loss; shares dropped after hours.",
            difficulty="easy",
            tags=["earnings", "guidance"],
            source_notes="Netflix Q1 2022 letter.",
        ),
        truth=_truth(
            expected_event_types=["PRICE_MOVE", "IDIOSYNCRATIC_MOVE", "EARNINGS"],
            expected_materiality="HIGH_MATERIALITY",
            should_investigate=True,
            should_notify=True,
            acceptable_hypothesis_phrases=["subscriber loss", "membership", "outlook"],
            unacceptable_hypothesis_phrases=["oil embargo"],
            expected_conclusion_strengths=["SUPPORTED", "PARTIALLY_SUPPORTED"],
            primary_source_required=True,
        ),
        fixture=ReplayFixture(
            move=_move("NFLX", -35.1, -0.8, vol=2.6),
            fundamentals={"earnings_date": "2022-04-19"},
            evidence=[
                _ev(
                    "Q1 2022 Letter to Shareholders",
                    "https://s22.q4cdn.com/959853165/files/doc_financials/2022/q1/FINAL-Q1-22-Shareholder-Letter.pdf",
                    "2022-04-19T20:05:00+00:00",
                    source_type="ir_page",
                    excerpt="Paid net memberships declined by 200,000 in the quarter. Forecast includes further losses.",
                    official=True,
                )
            ],
            hypotheses=[_hyp("A reported subscriber (membership) loss and weaker outlook are the strongest supported explanation.", 1, [0], citations=["https://s22.q4cdn.com/959853165/files/doc_financials/2022/q1/FINAL-Q1-22-Shareholder-Letter.pdf"])],
            exposure=0.08,
        ),
    ),
    PackedCase(
        case=_case(
            case_id="svb-2023-03-10-failure",
            ticker="SIVB",
            event_timestamp="2023-03-10T00:00:00+00:00",
            cutoff_time="2023-03-10T23:59:59+00:00",
            event_type="REGULATORY",
            event_description="California DFPI closed Silicon Valley Bank; FDIC appointed receiver.",
            difficulty="easy",
            tags=["regulatory", "company-specific"],
            source_notes="FDIC PR-16-2023, 10 March 2023.",
        ),
        truth=_truth(
            expected_event_types=["PRICE_MOVE", "IDIOSYNCRATIC_MOVE"],
            expected_materiality="HIGH_MATERIALITY",
            should_investigate=True,
            should_notify=True,
            acceptable_hypothesis_phrases=["bank closed", "fdic", "receiver", "run"],
            unacceptable_hypothesis_phrases=["iphone demand"],
            expected_conclusion_strengths=["SUPPORTED", "PARTIALLY_SUPPORTED"],
            required_source_domains=["fdic.gov"],
        ),
        fixture=ReplayFixture(
            move=_move("SIVB", -60.0, -1.5, vol=3.0),
            evidence=[
                _ev(
                    "FDIC Creates Deposit Insurance National Bank of Santa Clara to Protect Insured Depositors of Silicon Valley Bank",
                    "https://www.fdic.gov/news/press-releases/2023/pr23016.html",
                    "2023-03-10T18:00:00+00:00",
                    source_type="ir_page",
                    excerpt="Silicon Valley Bank, Santa Clara, California, was closed today by the California DFPI. The FDIC was named receiver.",
                    official=True,
                    name="FDIC",
                )
            ],
            hypotheses=[_hyp("The bank was closed and placed into FDIC receivership after a deposit run.", 1, [0], citations=["https://www.fdic.gov/news/press-releases/2023/pr23016.html"])],
            exposure=0.05,
        ),
    ),
    PackedCase(
        case=_case(
            case_id="ba-2024-01-05-door-plug",
            ticker="BA",
            event_timestamp="2024-01-06T00:00:00+00:00",
            cutoff_time="2024-01-06T23:59:59+00:00",
            event_type="NEWS",
            event_description="Alaska Airlines 1282 737-9 door-plug blowout; FAA grounding of 737-9 MAX.",
            difficulty="medium",
            tags=["regulatory", "company-specific"],
            source_notes="NTSB / FAA / Boeing statements 6–7 Jan 2024.",
        ),
        truth=_truth(
            expected_event_types=["PRICE_MOVE", "IDIOSYNCRATIC_MOVE"],
            expected_materiality="HIGH_MATERIALITY",
            should_investigate=True,
            should_notify=True,
            acceptable_hypothesis_phrases=["737-9", "grounding", "door plug", "faa"],
            unacceptable_hypothesis_phrases=["subscriber loss"],
            expected_conclusion_strengths=["SUPPORTED", "PARTIALLY_SUPPORTED"],
        ),
        fixture=ReplayFixture(
            move=_move("BA", -8.0, -0.4, vol=1.8),
            evidence=[
                _ev(
                    "Boeing Statement on 737-9",
                    "https://boeing.mediaroom.com/news-releases-statements",
                    "2024-01-06T16:00:00+00:00",
                    source_type="ir_page",
                    excerpt="Boeing recommended the temporary suspension of 737-9 operations following the Alaska Airlines accident.",
                    official=True,
                )
            ],
            hypotheses=[_hyp("The 737-9 door-plug accident and subsequent grounding are the strongest supported explanation.", 1, [0])],
            exposure=0.06,
        ),
    ),
    PackedCase(
        case=_case(
            case_id="smci-2024-08-27-hindenburg",
            ticker="SMCI",
            event_timestamp="2024-08-27T00:00:00+00:00",
            cutoff_time="2024-08-27T23:59:59+00:00",
            event_type="NEWS",
            event_description="Hindenburg Research published a short report on Super Micro Computer.",
            difficulty="medium",
            tags=["company-specific", "unusual"],
            source_notes="Hindenburg report 27 Aug 2024 (secondary). Official SMCI response if any after cutoff is withheld.",
        ),
        truth=_truth(
            expected_event_types=["PRICE_MOVE", "IDIOSYNCRATIC_MOVE"],
            expected_materiality="HIGH_MATERIALITY",
            should_investigate=True,
            should_notify=True,
            acceptable_hypothesis_phrases=["hindenburg", "short report", "accounting"],
            unacceptable_hypothesis_phrases=["federal reserve rate cut"],
            expected_conclusion_strengths=["PARTIALLY_SUPPORTED", "UNCERTAIN", "SUPPORTED"],
            known_contradictions=["short-seller report is not a primary filing"],
        ),
        fixture=ReplayFixture(
            move=_move("SMCI", -20.0, -0.3, vol=4.0),
            evidence=[
                _ev(
                    "Hindenburg Research: Super Micro Computer",
                    "https://hindenburgresearch.com/smci/",
                    "2024-08-27T13:00:00+00:00",
                    source_type="news",
                    excerpt="Short report alleging accounting and other issues at Super Micro Computer.",
                )
            ],
            hypotheses=[
                _hyp("A newly published short report is the strongest contemporaneous explanation for the decline.", 1, [0]),
                _hyp("The allegations are unproven; the report itself is secondary evidence.", 2, [0]),
            ],
            exposure=0.03,
        ),
    ),
    PackedCase(
        case=_case(
            case_id="nvda-2024-08-28-earnings",
            ticker="NVDA",
            event_timestamp="2024-08-28T00:00:00+00:00",
            cutoff_time="2024-08-28T23:59:59+00:00",
            event_type="EARNINGS",
            event_description="NVIDIA Q2 FY2025 results: data-center revenue and outlook.",
            difficulty="easy",
            tags=["earnings", "guidance"],
            source_notes="NVIDIA Q2 FY2025 release 28 Aug 2024.",
        ),
        truth=_truth(
            expected_event_types=["PRICE_MOVE", "EARNINGS"],
            expected_materiality="HIGH_MATERIALITY",
            should_investigate=True,
            should_notify=True,
            acceptable_hypothesis_phrases=["data center", "guidance", "revenue"],
            unacceptable_hypothesis_phrases=["bank failure"],
            primary_source_required=True,
            required_source_domains=["nvidia.com", "sec.gov"],
            expected_conclusion_strengths=["SUPPORTED", "PARTIALLY_SUPPORTED"],
        ),
        fixture=ReplayFixture(
            move=_move("NVDA", 6.5, 0.4, vol=2.2),
            fundamentals={"earnings_date": "2024-08-28"},
            filings=[FilingFixture(form_type="8-K", filing_date="2024-08-28")],
            evidence=[
                _ev(
                    "NVIDIA Announces Financial Results for Second Quarter Fiscal 2025",
                    "https://investor.nvidia.com/news/press-release-details/2024/NVIDIA-Announces-Financial-Results-for-Second-Quarter-Fiscal-2025/",
                    "2024-08-28T20:20:00+00:00",
                    source_type="ir_page",
                    excerpt="Data center revenue grew. The company provided outlook for the third quarter.",
                    official=True,
                )
            ],
            hypotheses=[_hyp("Data-center results and forward guidance are the strongest supported explanation.", 1, [0], citations=["https://investor.nvidia.com/news/press-release-details/2024/NVIDIA-Announces-Financial-Results-for-Second-Quarter-Fiscal-2025/"])],
            tool_trace=[ToolCallFixture(tool="get_recent_filings"), ToolCallFixture(tool="get_ir_materials")],
            browser_found_url="https://investor.nvidia.com/news/press-release-details/2024/NVIDIA-Announces-Financial-Results-for-Second-Quarter-Fiscal-2025/",
            exposure=0.24,
        ),
    ),
    PackedCase(
        case=_case(
            case_id="intc-2024-08-01-foundry",
            ticker="INTC",
            event_timestamp="2024-08-01T00:00:00+00:00",
            cutoff_time="2024-08-01T23:59:59+00:00",
            event_type="EARNINGS",
            event_description="Intel Q2 2024: foundry losses and outlook; shares dropped.",
            difficulty="medium",
            tags=["earnings", "guidance"],
            source_notes="Intel Q2 2024 release 1 Aug 2024.",
        ),
        truth=_truth(
            expected_event_types=["PRICE_MOVE", "IDIOSYNCRATIC_MOVE", "EARNINGS"],
            expected_materiality="HIGH_MATERIALITY",
            should_investigate=True,
            should_notify=True,
            acceptable_hypothesis_phrases=["foundry", "outlook", "guidance", "loss"],
            expected_conclusion_strengths=["SUPPORTED", "PARTIALLY_SUPPORTED"],
        ),
        fixture=ReplayFixture(
            move=_move("INTC", -26.0, -0.5, vol=2.5),
            fundamentals={"earnings_date": "2024-08-01"},
            evidence=[
                _ev(
                    "Intel Reports Second-Quarter 2024 Financial Results",
                    "https://www.intc.com/news-events/press-releases",
                    "2024-08-01T20:05:00+00:00",
                    source_type="ir_page",
                    excerpt="Foundry losses and a weaker outlook were disclosed with the quarter.",
                    official=True,
                )
            ],
            hypotheses=[_hyp("Foundry losses and a weaker outlook are the strongest supported explanation.", 1, [0])],
            exposure=0.07,
        ),
    ),
    PackedCase(
        case=_case(
            case_id="spy-2022-09-13-cpi",
            ticker="SPY",
            event_timestamp="2022-09-13T00:00:00+00:00",
            cutoff_time="2022-09-13T23:59:59+00:00",
            event_type="MACRO",
            event_description="CPI morning where the tape tracks the benchmark (small residual).",
            difficulty="medium",
            tags=["macro", "sector"],
            source_notes="BLS CPI Aug 2022 released 13 Sep 2022. Snapshot is a market-wide residual the current gate treats as skip; a larger beta print can still investigate.",
        ),
        truth=_truth(
            expected_event_types=["PRICE_MOVE"],
            expected_materiality="LOW_MATERIALITY",
            should_investigate=False,
            should_notify=False,
            acceptable_hypothesis_phrases=["cpi", "inflation", "market-wide"],
            notes="Skip when residual and absolute move both sit under the idiosyncratic/noise bars.",
        ),
        fixture=ReplayFixture(
            move=_move("SPY", -2.4, -2.3, vol=1.4, residual_pct=-0.1),
            evidence=[
                _ev(
                    "Consumer Price Index August 2022",
                    "https://www.bls.gov/news.release/archives/cpi_09132022.htm",
                    "2022-09-13T12:30:00+00:00",
                    source_type="macro",
                    excerpt="CPI rose 8.3 percent over the year.",
                    official=True,
                )
            ],
            hypotheses=[_hyp("The print is market-wide inflation news, not an idiosyncratic company event.", 1, [0])],
            exposure=1.0,
        ),
    ),
    PackedCase(
        case=_case(
            case_id="nvda-2023-10-17-export",
            ticker="NVDA",
            event_timestamp="2023-10-17T00:00:00+00:00",
            cutoff_time="2023-10-18T23:59:59+00:00",
            event_type="REGULATORY",
            event_description="BIS updated advanced-computing export controls affecting NVIDIA chips.",
            difficulty="medium",
            tags=["regulatory"],
            source_notes="BIS 17 Oct 2023 advanced computing rule.",
        ),
        truth=_truth(
            expected_event_types=["PRICE_MOVE", "IDIOSYNCRATIC_MOVE"],
            expected_materiality="HIGH_MATERIALITY",
            should_investigate=True,
            should_notify=True,
            acceptable_hypothesis_phrases=["export", "bis", "china", "restriction"],
            expected_conclusion_strengths=["SUPPORTED", "PARTIALLY_SUPPORTED"],
        ),
        fixture=ReplayFixture(
            move=_move("NVDA", -4.7, -0.6, vol=2.0),
            evidence=[
                _ev(
                    "Commerce Strengthens Restrictions on Advanced Computing Semiconductors",
                    "https://www.bis.gov/press-release/commerce-strengthens-restrictions-advanced-computing-semiconductors-add-additional",
                    "2023-10-17T14:00:00+00:00",
                    source_type="ir_page",
                    excerpt="Updated export controls on advanced computing ICs and semiconductor manufacturing.",
                    official=True,
                    name="BIS",
                )
            ],
            hypotheses=[_hyp("Updated US export restrictions on advanced computing chips are the strongest supported explanation.", 1, [0])],
            memory=[
                MemoryFixture(
                    memory_type="RISK",
                    statement="Export restrictions are a recurring regulatory risk for NVIDIA China shipments.",
                    theme_key="export_restrictions",
                    observed_at="2023-10-01T00:00:00+00:00",
                    source_url="https://www.bis.gov/",
                    source_type="ir_page",
                )
            ],
            exposure=0.20,
        ),
    ),
    PackedCase(
        case=_case(
            case_id="coin-2023-06-06-sec",
            ticker="COIN",
            event_timestamp="2023-06-06T00:00:00+00:00",
            cutoff_time="2023-06-06T23:59:59+00:00",
            event_type="REGULATORY",
            event_description="SEC sued Coinbase alleging unregistered securities offerings.",
            difficulty="medium",
            tags=["regulatory"],
            source_notes="SEC litigation release 6 Jun 2023.",
        ),
        truth=_truth(
            expected_event_types=["PRICE_MOVE", "IDIOSYNCRATIC_MOVE"],
            expected_materiality="HIGH_MATERIALITY",
            should_investigate=True,
            should_notify=True,
            acceptable_hypothesis_phrases=["sec", "lawsuit", "securities"],
            expected_conclusion_strengths=["SUPPORTED", "PARTIALLY_SUPPORTED"],
        ),
        fixture=ReplayFixture(
            move=_move("COIN", -12.0, -0.4, vol=3.2),
            evidence=[
                _ev(
                    "SEC Charges Coinbase for Operating as an Unregistered Securities Exchange",
                    "https://www.sec.gov/newsroom/press-releases/2023-102",
                    "2023-06-06T16:00:00+00:00",
                    source_type="ir_page",
                    excerpt="The SEC charged Coinbase with operating as an unregistered national securities exchange, broker, and clearing agency.",
                    official=True,
                )
            ],
            hypotheses=[_hyp("The SEC lawsuit alleging unregistered securities activity is the strongest supported explanation.", 1, [0])],
            exposure=0.04,
        ),
    ),
    PackedCase(
        case=_case(
            case_id="gme-2021-01-27-squeeze",
            ticker="GME",
            event_timestamp="2021-01-27T00:00:00+00:00",
            cutoff_time="2021-01-27T23:59:59+00:00",
            event_type="PRICE_MOVE",
            event_description="GameStop short squeeze; extreme price and volume.",
            difficulty="hard",
            tags=["unusual", "company-specific"],
            source_notes="Exchange prints 27 Jan 2021. Causal attribution remains contested.",
        ),
        truth=_truth(
            expected_event_types=["PRICE_MOVE", "VOLATILITY_SPIKE", "IDIOSYNCRATIC_MOVE"],
            expected_materiality="HIGH_MATERIALITY",
            should_investigate=True,
            should_notify=True,
            acceptable_hypothesis_phrases=["short squeeze", "retail", "volume"],
            expected_conclusion_strengths=["PARTIALLY_SUPPORTED", "UNCERTAIN"],
            notes="Do not require a single causal story.",
        ),
        fixture=ReplayFixture(
            move=_move("GME", 134.0, 0.2, vol=8.0),
            evidence=[
                _ev(
                    "GameStop Form 8-K",
                    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001326380",
                    "2021-01-27T21:00:00+00:00",
                    source_type="filing",
                    excerpt="No company operating release fully accounts for the intraday squeeze.",
                    official=True,
                )
            ],
            hypotheses=[
                _hyp("Extreme short covering and retail volume are plausible; the company did not disclose a matching operating event.", 1, [0], confidence="LOW"),
            ],
            exposure=0.02,
        ),
    ),
    PackedCase(
        case=_case(
            case_id="amzn-2023-02-02-aws",
            ticker="AMZN",
            event_timestamp="2023-02-02T00:00:00+00:00",
            cutoff_time="2023-02-02T23:59:59+00:00",
            event_type="EARNINGS",
            event_description="Amazon Q4 2022: AWS growth slowed.",
            difficulty="medium",
            tags=["earnings"],
            source_notes="Amazon Q4 2022 release 2 Feb 2023.",
        ),
        truth=_truth(
            expected_event_types=["PRICE_MOVE", "EARNINGS"],
            expected_materiality="HIGH_MATERIALITY",
            should_investigate=True,
            should_notify=True,
            acceptable_hypothesis_phrases=["aws", "cloud growth", "guidance"],
            expected_conclusion_strengths=["SUPPORTED", "PARTIALLY_SUPPORTED"],
        ),
        fixture=ReplayFixture(
            move=_move("AMZN", -7.9, -1.1, vol=2.1),
            fundamentals={"earnings_date": "2023-02-02"},
            evidence=[
                _ev(
                    "Amazon.com Announces Fourth Quarter Results",
                    "https://ir.aboutamazon.com/news-release/news-release-details/2023/Amazon.com-Announces-Fourth-Quarter-Results/",
                    "2023-02-02T21:05:00+00:00",
                    source_type="ir_page",
                    excerpt="AWS growth decelerated versus prior periods.",
                    official=True,
                )
            ],
            hypotheses=[_hyp("Slower AWS growth in the print is the strongest supported explanation.", 1, [0])],
            exposure=0.10,
        ),
    ),
    PackedCase(
        case=_case(
            case_id="pypl-2023-02-guidance",
            ticker="PYPL",
            event_timestamp="2023-02-09T00:00:00+00:00",
            cutoff_time="2023-02-09T23:59:59+00:00",
            event_type="EARNINGS",
            event_description="PayPal issued 2023 guidance below expectations.",
            difficulty="medium",
            tags=["guidance", "earnings"],
            source_notes="PayPal Q4 2022 / FY2023 outlook.",
        ),
        truth=_truth(
            expected_event_types=["PRICE_MOVE", "IDIOSYNCRATIC_MOVE", "EARNINGS"],
            expected_materiality="HIGH_MATERIALITY",
            should_investigate=True,
            should_notify=True,
            acceptable_hypothesis_phrases=["guidance", "outlook"],
            expected_conclusion_strengths=["SUPPORTED", "PARTIALLY_SUPPORTED"],
        ),
        fixture=ReplayFixture(
            move=_move("PYPL", -13.5, -0.6, vol=2.3),
            fundamentals={"earnings_date": "2023-02-09"},
            evidence=[
                _ev(
                    "PayPal Reports Fourth Quarter and Full Year 2022",
                    "https://investor.pypl.com/",
                    "2023-02-09T21:05:00+00:00",
                    source_type="ir_page",
                    excerpt="2023 guidance was issued below widely discussed expectations.",
                    official=True,
                )
            ],
            hypotheses=[_hyp("Weaker 2023 guidance is the strongest supported explanation.", 1, [0])],
            exposure=0.05,
        ),
    ),
    PackedCase(
        case=_case(
            case_id="wfc-2023-03-13-sector",
            ticker="WFC",
            event_timestamp="2023-03-13T00:00:00+00:00",
            cutoff_time="2023-03-13T23:59:59+00:00",
            event_type="SECTOR",
            event_description="Large US banks sold off after SVB; WFC declined with the group.",
            difficulty="hard",
            tags=["sector", "macro"],
            source_notes="Post-SVB session 13 Mar 2023.",
        ),
        truth=_truth(
            expected_event_types=["PRICE_MOVE"],
            expected_materiality="MEDIUM_MATERIALITY",
            should_investigate=True,
            should_notify=False,
            acceptable_hypothesis_phrases=["sector", "regional bank", "svb", "contagion"],
            expected_conclusion_strengths=["PARTIALLY_SUPPORTED", "UNCERTAIN"],
            notes="Investigate possible contagion but do not force a WFC-specific cause.",
        ),
        fixture=ReplayFixture(
            move=_move("WFC", -6.1, -2.2, vol=1.9, residual_pct=-3.9),
            evidence=[
                _ev(
                    "Treasury and Fed actions after SVB",
                    "https://home.treasury.gov/news/press-releases",
                    "2023-03-12T22:00:00+00:00",
                    source_type="macro",
                    excerpt="Authorities announced systemic facilities after SVB and Signature.",
                )
            ],
            hypotheses=[
                _hyp("Sector-wide deposit-flight fears after SVB are a stronger explanation than a WFC-specific disclosure.", 1, [0]),
                _hyp("A WFC-only operational failure is not supported by a contemporaneous company filing.", 2, []),
            ],
            exposure=0.09,
        ),
    ),
    PackedCase(
        case=_case(
            case_id="xom-2022-03-08-oil",
            ticker="XOM",
            event_timestamp="2022-03-08T00:00:00+00:00",
            cutoff_time="2022-03-08T23:59:59+00:00",
            event_type="MACRO",
            event_description="Crude spiked after the invasion of Ukraine; energy equities rose.",
            difficulty="medium",
            tags=["macro"],
            source_notes="March 2022 oil shock. Company-specific filings not required.",
        ),
        truth=_truth(
            expected_event_types=["PRICE_MOVE"],
            expected_materiality="MEDIUM_MATERIALITY",
            should_investigate=True,
            should_notify=False,
            acceptable_hypothesis_phrases=["oil", "crude", "macro", "ukraine"],
            expected_conclusion_strengths=["PARTIALLY_SUPPORTED", "SUPPORTED"],
        ),
        fixture=ReplayFixture(
            move=_move("XOM", 7.8, 2.1, vol=2.0, residual_pct=5.7),
            evidence=[
                _ev(
                    "Energy prices after the Ukraine invasion",
                    "https://www.eia.gov/todayinenergy/",
                    "2022-03-08T15:00:00+00:00",
                    source_type="macro",
                    excerpt="Crude prices moved sharply higher on supply-risk headlines.",
                )
            ],
            hypotheses=[_hyp("A macro oil shock, not an Exxon operating surprise, is the strongest supported explanation.", 1, [0])],
            exposure=0.06,
        ),
    ),
    PackedCase(
        case=_case(
            case_id="aapl-2024-09-09-event",
            ticker="AAPL",
            event_timestamp="2024-09-09T00:00:00+00:00",
            cutoff_time="2024-09-09T23:59:59+00:00",
            event_type="NEWS",
            event_description="Apple iPhone 16 event; session move was modest versus the benchmark.",
            difficulty="easy",
            tags=["company-specific"],
            source_notes="Product event days are often not material idiosyncratic prints.",
        ),
        truth=_truth(
            expected_event_types=["PRICE_MOVE"],
            expected_materiality="LOW_MATERIALITY",
            should_investigate=False,
            should_notify=False,
            acceptable_hypothesis_phrases=["product event", "iphone"],
        ),
        fixture=ReplayFixture(
            move=_move("AAPL", 0.9, 0.6, vol=1.3, residual_pct=0.3),
            evidence=[
                _ev(
                    "Apple Event September 2024",
                    "https://www.apple.com/newsroom/",
                    "2024-09-09T17:00:00+00:00",
                    source_type="ir_page",
                    excerpt="Apple announced iPhone 16.",
                    official=True,
                )
            ],
            hypotheses=[_hyp("A product announcement without a material idiosyncratic move does not clear the investigation bar.", 1, [0])],
            exposure=0.15,
        ),
    ),
    PackedCase(
        case=_case(
            case_id="lly-2023-08-demand",
            ticker="LLY",
            event_timestamp="2023-08-08T00:00:00+00:00",
            cutoff_time="2023-08-08T23:59:59+00:00",
            event_type="EARNINGS",
            event_description="Eli Lilly raised outlook on incretin demand.",
            difficulty="medium",
            tags=["earnings", "guidance"],
            source_notes="Lilly Q2 2023 commentary on Mounjaro / incretin demand.",
        ),
        truth=_truth(
            expected_event_types=["PRICE_MOVE", "EARNINGS"],
            expected_materiality="HIGH_MATERIALITY",
            should_investigate=True,
            should_notify=True,
            acceptable_hypothesis_phrases=["demand", "outlook", "mounjaro", "incretin"],
            expected_conclusion_strengths=["SUPPORTED", "PARTIALLY_SUPPORTED"],
        ),
        fixture=ReplayFixture(
            move=_move("LLY", 8.2, 0.3, vol=1.8),
            fundamentals={"earnings_date": "2023-08-08"},
            evidence=[
                _ev(
                    "Lilly Reports Q2 2023 Results",
                    "https://investor.lilly.com/",
                    "2023-08-08T11:00:00+00:00",
                    source_type="ir_page",
                    excerpt="Management cited strong incretin demand and raised components of the outlook.",
                    official=True,
                )
            ],
            hypotheses=[_hyp("Raised outlook on incretin demand is the strongest supported explanation.", 1, [0])],
            exposure=0.05,
        ),
    ),
    PackedCase(
        case=_case(
            case_id="nvda-2024-01-24-hard",
            ticker="NVDA",
            event_timestamp="2024-01-24T00:00:00+00:00",
            cutoff_time="2024-01-24T23:59:59+00:00",
            event_type="MIXED",
            event_description="Hard case: mixed secondary commentary plus a post-event article after cutoff.",
            difficulty="hard",
            tags=["ambiguous", "temporal"],
            source_notes="Cutoff is 24 Jan 2024 23:59 UTC. Later recaps must be dropped.",
        ),
        truth=_truth(
            expected_event_types=["PRICE_MOVE"],
            expected_materiality="HIGH_MATERIALITY",
            should_investigate=True,
            should_notify=False,
            acceptable_hypothesis_phrases=["guidance", "data center", "insufficient"],
            known_contradictions=["sector declined"],
            expected_conclusion_strengths=["PARTIALLY_SUPPORTED", "UNCERTAIN", "INSUFFICIENT_EVIDENCE"],
            planted_flaws=["TEMPORAL_LEAK"],
        ),
        fixture=ReplayFixture(
            move=_move("NVDA", -6.8, -2.4, vol=2.0, residual_pct=-4.4),
            evidence=[
                _ev(
                    "NVIDIA investor presentation",
                    "https://investor.nvidia.com/",
                    "2024-01-22T18:00:00+00:00",
                    source_type="ir_page",
                    excerpt="Prior-quarter data-center commentary remains on the IR site.",
                    official=True,
                ),
                _ev(
                    "Sector semiconductors also declined",
                    "https://www.example-news.test/semis-sold-off",
                    "2024-01-24T20:00:00+00:00",
                    source_type="news",
                    excerpt="SOX declined 4.1 percent. Competitor stocks also declined.",
                ),
                _ev(
                    "Week-later recap blaming the entire move on guidance",
                    "https://www.example-news.test/nvda-recap-jan-30",
                    "2024-01-30T12:00:00+00:00",
                    source_type="news",
                    excerpt="With a week of hindsight, guidance caused the entire decline.",
                ),
            ],
            hypotheses=[
                _hyp("Guidance may explain part of the move, but sector weakness is a live alternative.", 1, [0, 1], contradicting_indices=[1], confidence="LOW"),
                _hyp("The entire decline was caused by guidance, per the January 30 recap.", 2, [2], citations=["https://www.example-news.test/nvda-recap-jan-30"]),
            ],
            exposure=0.22,
            claims_grounded=False,
        ),
    ),
    PackedCase(
        case=_case(
            case_id="halluc-no-evidence",
            ticker="XYZ",
            event_timestamp="2024-06-01T00:00:00+00:00",
            cutoff_time="2024-06-01T23:59:59+00:00",
            event_type="PRICE_MOVE",
            event_description="Price moved; no contemporaneous primary source exists in the fixture.",
            track="hallucination",
            difficulty="hard",
            tags=["insufficient"],
        ),
        truth=_truth(
            expected_event_types=["PRICE_MOVE"],
            expected_materiality="MEDIUM_MATERIALITY",
            should_investigate=True,
            should_notify=False,
            acceptable_hypothesis_phrases=["insufficient evidence"],
            expected_conclusion_strengths=["INSUFFICIENT_EVIDENCE", "UNCERTAIN"],
            planted_flaws=["UNSUPPORTED"],
            notes="Correct behavior is to refuse a causal story.",
        ),
        fixture=ReplayFixture(
            move=_move("XYZ", -5.2, -0.4, vol=1.6),
            evidence=[],
            hypotheses=[
                _hyp("Management secretly guided down in an unpublished meeting that caused the entire decline.", 1, [], citations=["https://not-a-real-source.invalid/secret"]),
            ],
            claims_grounded=False,
            exposure=0.04,
        ),
    ),
    PackedCase(
        case=_case(
            case_id="verify-causal-overreach",
            ticker="MSFT",
            event_timestamp="2023-01-24T00:00:00+00:00",
            cutoff_time="2023-01-24T23:59:59+00:00",
            event_type="EARNINGS",
            event_description="Planted flaw: revenue beat is used to explain a decline.",
            track="verification",
            difficulty="medium",
            tags=["verification"],
            source_notes="Synthetic planted-flaw case on a real earnings date.",
        ),
        truth=_truth(
            expected_event_types=["PRICE_MOVE", "EARNINGS"],
            expected_materiality="HIGH_MATERIALITY",
            should_investigate=True,
            should_notify=False,
            acceptable_hypothesis_phrases=["does not establish", "insufficient", "cloud"],
            planted_flaws=["CAUSAL_OVERREACH"],
            expected_conclusion_strengths=["PARTIALLY_SUPPORTED", "UNCERTAIN", "INSUFFICIENT_EVIDENCE"],
        ),
        fixture=ReplayFixture(
            move=_move("MSFT", -3.4, -0.5, vol=1.5, residual_pct=-2.9),
            fundamentals={"earnings_date": "2023-01-24"},
            evidence=[
                _ev(
                    "Microsoft earnings: revenue exceeded consensus",
                    "https://www.microsoft.com/en-us/Investor/earnings/",
                    "2023-01-24T21:05:00+00:00",
                    source_type="ir_page",
                    excerpt="Revenue beat expectations. Azure growth remained solid.",
                    official=True,
                )
            ],
            hypotheses=[
                _hyp("Revenue beat expectations caused the stock decline.", 1, [0], claim_type="CAUSAL_EXPLANATION"),
            ],
            claims_grounded=False,
            exposure=0.11,
        ),
    ),
    PackedCase(
        case=_case(
            case_id="memory-demand-shift",
            ticker="NVDA",
            event_timestamp="2026-02-20T00:00:00+00:00",
            cutoff_time="2026-02-20T23:59:59+00:00",
            event_type="EARNINGS",
            event_description="Management commentary on AI demand shifted from strong to selective.",
            track="company",
            difficulty="medium",
            tags=["memory"],
            source_notes="Synthetic memory timeline for Phase 8 retrieval, not a live quote.",
        ),
        truth=_truth(
            expected_event_types=["PRICE_MOVE", "EARNINGS"],
            expected_materiality="HIGH_MATERIALITY",
            should_investigate=True,
            should_notify=False,
            expected_memory_change=True,
            acceptable_hypothesis_phrases=["demand", "selective", "changed"],
            expected_conclusion_strengths=["PARTIALLY_SUPPORTED", "SUPPORTED"],
        ),
        fixture=ReplayFixture(
            move=_move("NVDA", -4.1, -0.3, vol=2.0),
            fundamentals={"earnings_date": "2026-02-20"},
            evidence=[
                _ev(
                    "NVIDIA earnings: demand becoming more selective",
                    "https://investor.nvidia.com/q1-fy27",
                    "2026-02-20T21:00:00+00:00",
                    source_type="ir_page",
                    excerpt="Management said AI demand is becoming more selective versus prior-year strong commentary.",
                    official=True,
                )
            ],
            hypotheses=[_hyp("Management demand commentary changed from strong to more selective.", 1, [0])],
            memory=[
                MemoryFixture(
                    memory_type="MANAGEMENT_STATEMENT",
                    statement="Management expects continued strong AI infrastructure demand.",
                    theme_key="ai_demand",
                    polarity="POSITIVE",
                    observed_at="2025-08-28T00:00:00+00:00",
                    source_url="https://investor.nvidia.com/q2-fy26",
                    source_type="ir_page",
                ),
                MemoryFixture(
                    memory_type="MANAGEMENT_STATEMENT",
                    statement="Management now describes AI demand as becoming more selective.",
                    theme_key="ai_demand",
                    polarity="NEGATIVE",
                    observed_at="2026-02-20T21:00:00+00:00",
                    source_url="https://investor.nvidia.com/q1-fy27",
                    source_type="ir_page",
                ),
            ],
            exposure=0.18,
        ),
    ),
    PackedCase(
        case=_case(
            case_id="browser-ir-pdf",
            ticker="NVDA",
            event_timestamp="2024-08-28T00:00:00+00:00",
            cutoff_time="2024-08-28T23:59:59+00:00",
            event_type="EARNINGS",
            event_description="Recorded OpenClaw-style retrieval of the official earnings PDF.",
            track="browser",
            difficulty="medium",
            tags=["openclaw", "primary-source"],
            source_notes="Recorded browser fixture. CI does not hit the live site.",
        ),
        truth=_truth(
            expected_event_types=["EARNINGS", "PRICE_MOVE"],
            expected_materiality="HIGH_MATERIALITY",
            should_investigate=True,
            primary_source_required=True,
            expected_browser_domain="investor.nvidia.com",
            should_use_browser=True,
            acceptable_hypothesis_phrases=["data center", "guidance"],
        ),
        fixture=ReplayFixture(
            move=_move("NVDA", 6.5, 0.4, vol=2.2),
            fundamentals={"earnings_date": "2024-08-28"},
            evidence=[
                _ev(
                    "Q2 FY2025 earnings presentation PDF",
                    "https://investor.nvidia.com/financials/quarterly-results/default.aspx",
                    "2024-08-28T20:30:00+00:00",
                    source_type="ir_page",
                    excerpt="Official quarterly results materials.",
                    official=True,
                )
            ],
            hypotheses=[_hyp("Official data-center results and guidance explain the move.", 1, [0])],
            tool_trace=[
                ToolCallFixture(tool="get_recent_filings", latency_ms=70),
                ToolCallFixture(tool="browser_find_document", latency_ms=900),
            ],
            browser_found_url="https://investor.nvidia.com/financials/quarterly-results/default.aspx",
            exposure=0.2,
        ),
    ),
    PackedCase(
        case=_case(
            case_id="mcp-sec-no-browser",
            ticker="AAPL",
            event_timestamp="2024-05-02T00:00:00+00:00",
            cutoff_time="2024-05-03T23:59:59+00:00",
            event_type="FILING",
            event_description="8-K is already in SEC MCP; browser should not be required.",
            track="mcp",
            difficulty="easy",
            tags=["mcp", "routing"],
            source_notes="Routing case. Filing present in MCP payload.",
        ),
        truth=_truth(
            expected_event_types=["FILING"],
            expected_materiality="HIGH_MATERIALITY",
            should_investigate=True,
            should_use_browser=False,
            acceptable_hypothesis_phrases=["8-k", "filing"],
            required_source_domains=["sec.gov"],
        ),
        fixture=ReplayFixture(
            move=_move("AAPL", -2.8, -0.4, vol=1.4, residual_pct=-2.4),
            filings=[
                FilingFixture(
                    form_type="8-K",
                    filing_date="2024-05-02",
                    document_url="https://www.sec.gov/Archives/edgar/data/320193/000032019324000069/aapl-20240502.htm",
                    accession_number="0000320193-24-000069",
                )
            ],
            evidence=[
                _ev(
                    "Apple 8-K",
                    "https://www.sec.gov/Archives/edgar/data/320193/000032019324000069/aapl-20240502.htm",
                    "2024-05-02T21:00:00+00:00",
                    source_type="filing",
                    official=True,
                )
            ],
            hypotheses=[_hyp("The 8-K filing is the material event; SEC MCP already has the document.", 1, [0])],
            tool_trace=[ToolCallFixture(tool="get_recent_filings", latency_ms=60)],
            exposure=0.16,
        ),
    ),
    PackedCase(
        case=_case(
            case_id="alert-noise-small-move",
            ticker="MSFT",
            event_timestamp="2024-03-12T00:00:00+00:00",
            cutoff_time="2024-03-12T23:59:59+00:00",
            event_type="PRICE_MOVE",
            event_description="1.1% session move tracking SPY; should not alert.",
            track="alert",
            difficulty="easy",
            tags=["alert", "noise"],
        ),
        truth=_truth(
            expected_event_types=["PRICE_MOVE"],
            expected_materiality="LOW_MATERIALITY",
            should_investigate=False,
            should_notify=False,
        ),
        fixture=ReplayFixture(
            move=_move("MSFT", -1.1, -1.0, vol=1.2, residual_pct=-0.1),
            evidence=[],
            hypotheses=[],
            exposure=0.20,
            trigger_reason="scheduled",
        ),
    ),
    PackedCase(
        case=_case(
            case_id="alert-material-nvda",
            ticker="NVDA",
            event_timestamp="2024-08-28T00:00:00+00:00",
            cutoff_time="2024-08-28T23:59:59+00:00",
            event_type="EARNINGS",
            event_description="Material completed investigation should be eligible to notify.",
            track="alert",
            difficulty="easy",
            tags=["alert"],
        ),
        truth=_truth(
            expected_event_types=["PRICE_MOVE", "EARNINGS"],
            expected_materiality="HIGH_MATERIALITY",
            should_investigate=True,
            should_notify=True,
            acceptable_hypothesis_phrases=["data center", "guidance"],
        ),
        fixture=ReplayFixture(
            move=_move("NVDA", -8.7, -0.6, vol=2.0, residual_pct=-8.1),
            fundamentals={"earnings_date": "2024-08-28"},
            evidence=[
                _ev(
                    "NVIDIA Q2 FY2025 release",
                    "https://investor.nvidia.com/",
                    "2024-08-28T20:20:00+00:00",
                    source_type="ir_page",
                    excerpt="Official results and outlook.",
                    official=True,
                )
            ],
            hypotheses=[_hyp("Guidance and data-center results are the strongest supported explanation.", 1, [0])],
            exposure=0.24,
            claims_grounded=True,
        ),
    ),
]
