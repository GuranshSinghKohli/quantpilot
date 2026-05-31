from fastapi import APIRouter, HTTPException

from app.observability.workflow_tracker import workflow_tracker

router = APIRouter(prefix="/observability", tags=["observability"])


@router.get("/runs")
async def list_workflow_runs():
    return {"runs": workflow_tracker.list_runs(limit=10)}


@router.get("/runs/{run_id}")
async def get_workflow_run(run_id: str):
    run = workflow_tracker.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Workflow run {run_id} not found.")
    return run
