from fastapi import APIRouter, Depends, HTTPException

from app.auth.deps import get_current_user
from app.db.models import User
from app.observability.instrumentation import get_observability_status
from app.observability.workflow_tracker import workflow_tracker

router = APIRouter(prefix="/observability", tags=["observability"])


@router.get("/status")
async def observability_status():
    """PRD v3 Phase 13 — which optional observability backends are active."""
    return {"status": "ok", **get_observability_status()}


@router.get("/runs")
async def list_workflow_runs(_user: User = Depends(get_current_user)):
    return {"runs": workflow_tracker.list_runs(limit=10)}


@router.get("/runs/{run_id}")
async def get_workflow_run(run_id: str, _user: User = Depends(get_current_user)):
    run = workflow_tracker.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Workflow run {run_id} not found.")
    return run
