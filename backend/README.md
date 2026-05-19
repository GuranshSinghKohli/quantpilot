# QuantPilot — Backend

FastAPI service for market data, filings, and (later) agent orchestration.

## Layout

```
backend/
├── app/
│   ├── main.py              # FastAPI app entry
│   ├── api/routes/          # HTTP route handlers
│   ├── core/                # Config, dependencies, security
│   ├── services/            # External API clients & business logic
│   │   ├── yahoo_finance/
│   │   └── sec_edgar/
│   ├── schemas/             # Pydantic request/response models
│   └── models/              # Domain models (if needed)
└── tests/
```

## Phase 1 tasks

- [ ] FastAPI app + health route
- [ ] Yahoo Finance integration
- [ ] SEC Edgar integration
- [ ] Response parsing & validation
