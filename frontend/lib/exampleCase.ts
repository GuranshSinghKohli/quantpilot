import type { InvestigationDetail } from "@/types";

/* A real case, captured verbatim from a live pipeline run on the ticker below.
   It ships with the app so a first-time visitor lands on something to read
   instead of three empty columns, and it is labelled as an example everywhere
   it appears so it can never be mistaken for a fresh reading of the market. */

export const EXAMPLE_CASE_ID = -1;

export const EXAMPLE_CASE: InvestigationDetail = {
  "id": -1,
  "ticker": "TSLA",
  "trigger_reason": "on_demand",
  "status": "complete",
  "move_pct": 2.8579,
  "window_label": "1w",
  "summary": "TSLA rose 2.9% over 1 week. Leading cause: Broader market trends or macroeconomic factors influencing TSLA's price. The recent 2.86% price increase in TSLA appears to be driven by a combination of positive news regarding partnerships and favorable market sentiment towards electric vehicle stocks, alongside some analyst activity. However, broader market trends and macroeconomic factors also play a role. Devil's Advocate demoted the leading hypothesis; a competitor was promoted.",
  "created_at": "2026-08-18T22:17:15.828236",
  "completed_at": "2026-08-18T22:17:40.031056",
  "claims_count": 4,
  "evidence_count": 11,
  "error_message": "",
  "verification_notes": "Linked weakly supported claim to available evidence.; Rejected 1 unsupported claim(s).",
  "da_outcome": {
    "outcome": "demoted",
    "counterargument": "The price move may not be solely attributed to positive news regarding Tesla's production or sales figures, as the partnership with Einride could have been anticipated by the market, and other factors such as broader market trends or macroeconomic conditions could be influencing TSLA's price.",
    "leading_weakened": true,
    "reversal": true,
    "notes": [
      "The evidence of the partnership with Einride may not be enough to drive the stock price alone, especially if market conditions are unfavorable.",
      "Analyst sentiment appears mixed, with some analysts dropping price targets or calling the stock a 'sell', indicating that investor confidence may not be as strong as suggested.",
      "The broader market trends could be impacting TSLA's price independently of company-specific news."
    ],
    "confidence_delta": -0.3,
    "citation_coverage": 0.8
  },
  "claims": [
    {
      "id": 74,
      "statement": "Broader market trends or macroeconomic factors influencing TSLA's price.",
      "stance": "unknown",
      "confidence_score": 0.2525,
      "rank": 1,
      "devil_advocate_notes": "Promoted after Devil's Advocate overturned prior lead. The price move may not be solely attributed to positive news regarding Tesla's production or sales figures, as the partnership with Einride could have been anticipated by the market, and other factors such as broader market trends or macroeconomic conditions could be influencing TSLA's price.",
      "created_at": "2026-08-18T22:17:30.164005",
      "evidence_links": [
        {
          "id": 87,
          "evidence_id": 181,
          "relation": "supports",
          "note": ""
        }
      ]
    },
    {
      "id": 75,
      "statement": "Positive news regarding Tesla's production or sales figures.",
      "stance": "supports_move",
      "confidence_score": 0.1414,
      "rank": 2,
      "devil_advocate_notes": "The price move may not be solely attributed to positive news regarding Tesla's production or sales figures, as the partnership with Einride could have been anticipated by the market, and other factors such as broader market trends or macroeconomic conditions could be influencing TSLA's price.",
      "created_at": "2026-08-18T22:17:30.165162",
      "evidence_links": [
        {
          "id": 88,
          "evidence_id": 183,
          "relation": "supports",
          "note": ""
        },
        {
          "id": 89,
          "evidence_id": 186,
          "relation": "supports",
          "note": ""
        }
      ]
    },
    {
      "id": 76,
      "statement": "Analyst upgrades or positive earnings forecasts impacting investor confidence.",
      "stance": "supports_move",
      "confidence_score": 0.4041,
      "rank": 3,
      "devil_advocate_notes": "Recent analyst reports have been mixed, with some downgrades that could counteract positive sentiment.",
      "created_at": "2026-08-18T22:17:30.166551",
      "evidence_links": [
        {
          "id": 90,
          "evidence_id": 188,
          "relation": "supports",
          "note": ""
        },
        {
          "id": 91,
          "evidence_id": 190,
          "relation": "supports",
          "note": ""
        }
      ]
    },
    {
      "id": 77,
      "statement": "Recent developments in Tesla's technology or product offerings attracting investor interest.",
      "stance": "supports_move",
      "confidence_score": 0.202,
      "rank": 4,
      "devil_advocate_notes": "Technological developments can generate interest, but may not have immediate financial implications.",
      "created_at": "2026-08-18T22:17:30.168390",
      "evidence_links": [
        {
          "id": 92,
          "evidence_id": 184,
          "relation": "supports",
          "note": ""
        },
        {
          "id": 93,
          "evidence_id": 185,
          "relation": "supports",
          "note": ""
        }
      ]
    }
  ],
  "evidence_items": [
    {
      "id": 181,
      "source_type": "price",
      "retrieval_method": "mcp",
      "title": "TSLA price move (1w)",
      "excerpt": "TSLA rose 2.86% over 1 week (noise threshold 1.5%).",
      "source_url": "",
      "created_at": "2026-08-18T22:17:21.111446"
    },
    {
      "id": 182,
      "source_type": "ir_page",
      "retrieval_method": "httpx",
      "title": "EDGAR Search Results",
      "excerpt": "EDGAR Search Results SEC.gov EDGAR Latest Filings Filings search tools EDGAR Company Search Results SEC Home &#187; Company Search &#187; Current Page Mailing Address 1 TESLA ROAD AUSTIN TX 78725 Business Address 1 TESLA ROAD AUSTIN TX 78725 512-516-8177 Tesla, Inc. CIK #: 0001318605 (see all company filings) SIC : 3711 - MOTOR VEHICLES & PASSENGER CAR BODIES State location: TX | State of Inc.: TX | Fiscal Year End: 1231 formerly: TESLA MOTORS INC (filings through 2017-01-27) (CF Office: 04 Manufacturing) Get insider transactions for this issuer . Filter Results Search Within Files Filing Type: Prior to: (YYYYMMDD) Ownership? include exclude only Limit Results Per Page 10 Entries 20 Entries 40 Entries 80 Entries 100 Entries Enter keywords Search Items 1 - 10 RSS Feed Filings Format Description Filing Date File/Film Number 8-K Documents Interactive Data Current report, items 2.02 and 9.01 Acc-no: 0001628280-26-049213 (34 Act) Size: 5 MB 2026-07-22 001-34756 261194091 8-K Documents Interactive Data Current report, items 2.02 and 9.01 Acc-no: 0001628280-26-046717 (34 Act) Size: 156 KB 2026-07-02 001-34756 261148032 8-K Documents Interactive Data Current report, items 2.02 and 9.01 Acc-no: 0001628280-26-026551 (34 Act) Size: 5 MB 2026-04-22 001-34756 26883889 8-K Documents Interactive Data Current report, items 2.02 and 9.01 Acc-no: 0001628280-26-022956 (34 Act) Size: 156 KB 2026-04-02 001-34756 26830913 8-K Documents Interactive Data Current report, items 2.02 and 9.01 Acc-no: 0001628280-26-003837 (34 Act) Size: 5 MB 2026-01-28 001-34756 26572582 8-K Documents Interactive Data Current report, items 2.02 and 9.01 Acc-no: 0001628280-26-000016 (34 Act) Size: 161 KB 2026-01-02 001-34756 26500435 8-K Documents Interactive Data Current report, items 5.02, 5.07, and 9.01 Acc-no: 0001104659-25-108507 (34 Act) Size: 691 KB 2025-11-07 001-34756 251463044 8-K Documents Interactive Data Current report, items 2.02 and 9.01 Acc-no: 0001628280-25-045861 (34 Act) Size: 4 MB 2025-10-22 001-34756 251410180 8-K Documents Interactive Data Current report, items 2.02 and 9.01 Acc-no: 0001628280-25-043530 (34 Act) Size: 157 KB 2025-10-02 001-34756 251367164 8-K Documents Interactive Data Current report, items 1.01 and 9.01 Acc-no: 0001104659-25-087862 (34 Act) Size: 266 KB 2025-09-05 001-34756 251297260 https://www.sec.gov/cgi-bin/browse-edgar Home | Search the Next-Generation EDGAR System | Previous Page Modified 07/18/2014",
      "source_url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001318605&type=8-K&count=5",
      "created_at": "2026-08-18T22:17:21.112693"
    },
    {
      "id": 183,
      "source_type": "news",
      "retrieval_method": "mcp",
      "title": "Tesla's new partnership: Swedish company Einride to deploy 500 Tesla semi-truck EVs",
      "excerpt": "News: Tesla's new partnership: Swedish company Einride to deploy 500 Tesla semi-truck EVs",
      "source_url": "https://finance.yahoo.com/video/teslas-new-partnership-swedish-company-einride-to-deploy-500-tesla-semi-truck-evs-145522754.html",
      "created_at": "2026-08-18T22:17:21.113416"
    },
    {
      "id": 184,
      "source_type": "news",
      "retrieval_method": "mcp",
      "title": "What does Tesla's Cybercab launch mean for SpaceX's own ambitions?",
      "excerpt": "News: What does Tesla's Cybercab launch mean for SpaceX's own ambitions?",
      "source_url": "https://finance.yahoo.com/video/does-teslas-cybercab-launch-mean-141500236.html",
      "created_at": "2026-08-18T22:17:21.114037"
    },
    {
      "id": 185,
      "source_type": "news",
      "retrieval_method": "mcp",
      "title": "Tesla's Cybercab Could Hit Austin Roads This Month",
      "excerpt": "News: Tesla's Cybercab Could Hit Austin Roads This Month",
      "source_url": "https://finance.yahoo.com/technology/articles/teslas-cybercab-could-hit-austin-212807468.html",
      "created_at": "2026-08-18T22:17:21.114525"
    },
    {
      "id": 186,
      "source_type": "news",
      "retrieval_method": "mcp",
      "title": "Tesla Just Landed a Major Win for Long-Delayed Semi",
      "excerpt": "News: Tesla Just Landed a Major Win for Long-Delayed Semi",
      "source_url": "https://finance.yahoo.com/markets/stocks/articles/tesla-just-landed-major-win-212212407.html",
      "created_at": "2026-08-18T22:17:21.115013"
    },
    {
      "id": 187,
      "source_type": "news",
      "retrieval_method": "mcp",
      "title": "Tesla Stock Slips as 312-Times Valuation Faces Yield Shock",
      "excerpt": "News: Tesla Stock Slips as 312-Times Valuation Faces Yield Shock",
      "source_url": "https://finance.yahoo.com/markets/stocks/articles/tesla-stock-slips-312-times-211213449.html",
      "created_at": "2026-08-18T22:17:21.115694"
    },
    {
      "id": 188,
      "source_type": "news",
      "retrieval_method": "mcp",
      "title": "Analyst Drops Stark Tesla Stock Price Target",
      "excerpt": "News: Analyst Drops Stark Tesla Stock Price Target",
      "source_url": "https://finance.yahoo.com/markets/stocks/articles/analyst-drops-stark-tesla-stock-210738265.html",
      "created_at": "2026-08-18T22:17:21.116305"
    },
    {
      "id": 189,
      "source_type": "news",
      "retrieval_method": "mcp",
      "title": "Elon Musk Admits There Is Likely to Be Short-Term Pain for SpaceX Investors",
      "excerpt": "News: Elon Musk Admits There Is Likely to Be Short-Term Pain for SpaceX Investors",
      "source_url": "https://www.fool.com/investing/2026/08/18/elon-musk-admits-there-is-likely-to-be-short-term/",
      "created_at": "2026-08-18T22:17:21.116922"
    },
    {
      "id": 190,
      "source_type": "news",
      "retrieval_method": "mcp",
      "title": "Veteran Analyst Firm Just Calls Tesla Stock a 'Sell' on Robotaxi Risks",
      "excerpt": "News: Veteran Analyst Firm Just Calls Tesla Stock a 'Sell' on Robotaxi Risks",
      "source_url": "https://finance.yahoo.com/markets/stocks/articles/veteran-analyst-firm-just-calls-202652386.html",
      "created_at": "2026-08-18T22:17:21.117499"
    },
    {
      "id": 191,
      "source_type": "other",
      "retrieval_method": "mcp",
      "title": "TSLA fundamentals snapshot",
      "excerpt": "{\"ticker\": \"TSLA\", \"market_cap\": 1330484084736, \"pe_ratio\": 309.05502, \"eps\": 1.09, \"dividend_yield\": null, \"fifty_two_week_high\": 498.83, \"fifty_two_week_low\": 297.38, \"sector\": \"Consumer Cyclical\", \"industry\": \"Auto Manufacturers\"}",
      "source_url": "https://finance.yahoo.com/quote/TSLA/key-statistics",
      "created_at": "2026-08-18T22:17:21.118008"
    }
  ]
} as InvestigationDetail;
