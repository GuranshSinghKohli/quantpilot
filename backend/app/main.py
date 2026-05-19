from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.models.schemas import HealthResponse
from app.routers import filings, stocks

load_dotenv()

app = FastAPI(
    title="QuantPilot API",
    description="Phase 1 — Foundation & Architecture",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stocks.router, prefix="/api")
app.include_router(filings.router, prefix="/api")


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")
