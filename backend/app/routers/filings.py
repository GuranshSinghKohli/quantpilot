import httpx
from fastapi import APIRouter, HTTPException

from app.models.schemas import FilingsListResponse
from app.services.sec_edgar import get_recent_filings

router = APIRouter(tags=["filings"])


@router.get("/filings/{ticker}", response_model=FilingsListResponse)
async def get_filings(ticker: str) -> FilingsListResponse:
    try:
        return await get_recent_filings(ticker, limit=3)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"SEC EDGAR API error: {exc.response.status_code}",
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch filings: {exc}") from exc
