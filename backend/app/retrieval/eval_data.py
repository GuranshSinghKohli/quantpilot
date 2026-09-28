"""Development retrieval evaluation fixture. Not a published benchmark."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

NOW = datetime(2026, 3, 1, 15, 0, tzinfo=timezone.utc)

# title -> body. Tickers in the third field drive entity links when companies exist.
DOCUMENTS: List[Dict[str, object]] = [
    {"title": "NVIDIA partners with Meta on inference", "summary": "NVIDIA and Meta announced a multi-year AI inference partnership covering GPUs and software.", "tickers": ["NVDA", "META"], "topic": "partnership"},
    {"title": "AMD expands data center GPU line", "summary": "AMD launched new Instinct accelerators aimed at training clusters competing with NVIDIA.", "tickers": ["AMD"], "topic": "product"},
    {"title": "Microsoft acquires cybersecurity startup Halo", "summary": "Microsoft agreed to acquire Halo Security to expand Azure defender coverage.", "tickers": ["MSFT"], "topic": "acquisition"},
    {"title": "Apple supplier TSMC adds Arizona capacity", "summary": "TSMC said its Arizona fab will add packaging lines used by Apple silicon.", "tickers": ["TSM", "AAPL"], "topic": "supply"},
    {"title": "Open-source vector database compared to pgvector", "summary": "Engineers compared pgvector cosine search to a local Chroma collection on 10k chunks.", "tickers": [], "topic": "unrelated"},
    {"title": "Federal reserve minutes mention inflation", "summary": "The Federal Reserve minutes discussed sticky services inflation and the policy rate.", "tickers": [], "topic": "unrelated"},
    {"title": "NVIDIA quarterly earnings beat", "summary": "NVIDIA reported data-center revenue above consensus after strong inference demand.", "tickers": ["NVDA"], "topic": "earnings"},
    {"title": "Meta open-sources Llama tooling", "summary": "Meta released additional Llama inference tooling for on-prem clusters.", "tickers": ["META"], "topic": "product"},
    {"title": "Amazon invests in anthropic-style safety lab", "summary": "Amazon Web Services announced a strategic investment in a model-safety laboratory.", "tickers": ["AMZN"], "topic": "investment"},
    {"title": "Intel partners with Arm on foundry", "summary": "Intel Foundry and Arm described a partnership to tape out mobile SoCs on Intel process nodes.", "tickers": ["INTC"], "topic": "partnership"},
    {"title": "Broadcom custom ASIC win with Google", "summary": "Broadcom said Google selected its custom accelerators for a new TPU generation.", "tickers": ["AVGO"], "topic": "partnership"},
    {"title": "Tesla robotaxi software update", "summary": "Tesla described unsupervised FSD changes. This is product news, not a chip partnership.", "tickers": ["TSLA"], "topic": "product"},
    {"title": "Local bakery opens second shop", "summary": "A neighborhood bakery opened a second location downtown. No public companies involved.", "tickers": [], "topic": "unrelated"},
    {"title": "AMD and Meta rack-scale design", "summary": "AMD and Meta published a joint rack-scale reference design for MI300 inference.", "tickers": ["AMD", "META"], "topic": "partnership"},
    {"title": "NVIDIA acquires small networking firm", "summary": "NVIDIA agreed to acquire a privately held networking silicon company to tighten GPU fabrics.", "tickers": ["NVDA"], "topic": "acquisition"},
    {"title": "SEC comment letter on climate disclosure", "summary": "The SEC published a sample comment letter on climate risk disclosure. Not a company event.", "tickers": [], "topic": "unrelated"},
    {"title": "Microsoft Azure OpenAI capacity expansion", "summary": "Microsoft said Azure reserved additional NVIDIA GPU capacity for OpenAI workloads.", "tickers": ["MSFT", "NVDA"], "topic": "partnership"},
    {"title": "Apple intelligence on-device models", "summary": "Apple described on-device foundation models that do not require cloud GPUs for many tasks.", "tickers": ["AAPL"], "topic": "product"},
    {"title": "TSMC earthquake update", "summary": "TSMC reported brief tool downtime after an earthquake; no change to Apple allocations.", "tickers": ["TSM"], "topic": "event"},
    {"title": "Weather in Lisbon is mild", "summary": "Tourism officials said Lisbon weather was mild this week. Completely unrelated.", "tickers": [], "topic": "unrelated"},
    {"title": "Amazon grocery robotics", "summary": "Amazon described warehouse robotics used in grocery fulfillment, unrelated to chips.", "tickers": ["AMZN"], "topic": "product"},
    {"title": "Intel foundry customer pipeline", "summary": "Intel listed additional foundry customers without naming a Meta partnership.", "tickers": ["INTC"], "topic": "product"},
    {"title": "Market holiday calendar reminder", "summary": "US markets are closed on the observed federal holiday. No issuer-specific news.", "tickers": [], "topic": "unrelated"},
    {"title": "Broadcom VMware licensing follow-up", "summary": "Broadcom restated VMware licensing after last year's acquisition close.", "tickers": ["AVGO"], "topic": "acquisition"},
]

# Expected relevant document titles for each query (ground truth for this fixture only).
QUERIES: List[Dict[str, object]] = [
    {
        "query": "NVIDIA partnership with Meta on inference",
        "relevant": ["NVIDIA partners with Meta on inference", "Microsoft Azure OpenAI capacity expansion"],
    },
    {
        "query": "AMD Meta rack scale partnership",
        "relevant": ["AMD and Meta rack-scale design", "AMD expands data center GPU line"],
    },
    {
        "query": "Microsoft acquisition cybersecurity",
        "relevant": ["Microsoft acquires cybersecurity startup Halo"],
    },
    {
        "query": "NVIDIA earnings data center revenue",
        "relevant": ["NVIDIA quarterly earnings beat"],
    },
    {
        "query": "TSMC Arizona Apple supplier capacity",
        "relevant": ["Apple supplier TSMC adds Arizona capacity", "TSMC earthquake update"],
    },
    {
        "query": "Intel Arm foundry partnership",
        "relevant": ["Intel partners with Arm on foundry"],
    },
    {
        "query": "unrelated bakery downtown",
        "relevant": ["Local bakery opens second shop"],
    },
    {
        "query": "Broadcom Google ASIC accelerators",
        "relevant": ["Broadcom custom ASIC win with Google"],
    },
]


def companies() -> List[Dict[str, str]]:
    return [
        {"name": "NVIDIA Corporation", "ticker": "NVDA", "cik": "0001045810"},
        {"name": "Meta Platforms Inc", "ticker": "META", "cik": "0001326801"},
        {"name": "Advanced Micro Devices", "ticker": "AMD", "cik": "0000002488"},
        {"name": "Microsoft Corporation", "ticker": "MSFT", "cik": "0000789019"},
        {"name": "Apple Inc", "ticker": "AAPL", "cik": "0000320193"},
        {"name": "Taiwan Semiconductor", "ticker": "TSM", "cik": "0001046179"},
        {"name": "Amazon.com Inc", "ticker": "AMZN", "cik": "0001018724"},
        {"name": "Intel Corporation", "ticker": "INTC", "cik": "0000050863"},
        {"name": "Broadcom Inc", "ticker": "AVGO", "cik": "0001730168"},
        {"name": "Tesla Inc", "ticker": "TSLA", "cik": "0001318605"},
    ]
