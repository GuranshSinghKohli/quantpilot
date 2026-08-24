"""Phase 10 — intelligence dashboard and company view. Additive."""

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import get_optional_user
from app.db.models import User
from app.db.session import get_db
from app.models.dashboard_schemas import CompanyIntelligenceResponse, DashboardResponse
from app.services import evidence_ledger_store, intelligence_dashboard

router = APIRouter(tags=["dashboard"])


def _require_owner(
    user: Optional[User],
    x_guest_id: Optional[str],
) -> str:
    owner = evidence_ledger_store.resolve_owner(user, x_guest_id)
    if not owner:
        raise HTTPException(
            status_code=400,
            detail="Provide auth or X-Guest-Id to scope the dashboard.",
        )
    return owner


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
    x_guest_id: Optional[str] = Header(default=None, alias="X-Guest-Id"),
) -> DashboardResponse:
    owner_key = _require_owner(user, x_guest_id)
    return intelligence_dashboard.build_dashboard(
        db, owner_key=owner_key, user=user
    )


@router.get(
    "/companies/{ticker}/intelligence",
    response_model=CompanyIntelligenceResponse,
)
async def get_company_intelligence(
    ticker: str,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
    x_guest_id: Optional[str] = Header(default=None, alias="X-Guest-Id"),
) -> CompanyIntelligenceResponse:
    owner_key = _require_owner(user, x_guest_id)
    return intelligence_dashboard.build_company_intelligence(
        db, owner_key=owner_key, ticker=ticker
    )
