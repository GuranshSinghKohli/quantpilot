from fastapi import APIRouter, HTTPException

from app.models.schemas import StockResponse
from app.services.yahoo_finance import get_stock_data

router = APIRouter(tags=["stocks"])


@router.get("/stocks/{ticker}", response_model=StockResponse)
async def get_stock(ticker: str) -> StockResponse:
    try:
        return await get_stock_data(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch stock data: {exc}") from exc
