from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4


class WorkflowTracker:
    def __init__(self, max_runs: int = 50):
        self._runs: List[Dict[str, Any]] = []
        self._max_runs = max_runs

    def start_run(self, ticker: str) -> Dict[str, Any]:
        run = {
            "run_id": str(uuid4()),
            "ticker": ticker.upper(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
            "status": "running",
            "agent_steps": [],
            "overall_confidence": None,
        }
        self._runs.insert(0, run)
        if len(self._runs) > self._max_runs:
            self._runs = self._runs[: self._max_runs]
        return run

    def _find_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        for run in self._runs:
            if run["run_id"] == run_id:
                return run
        return None

    def record_step(
        self,
        run_id: str,
        agent_name: str,
        status: str,
        duration_ms: int,
        confidence_score: Optional[float] = None,
        error: Optional[str] = None,
    ) -> None:
        run = self._find_run(run_id)
        if not run:
            return
        run["agent_steps"].append(
            {
                "agent_name": agent_name,
                "status": status,
                "confidence_score": confidence_score,
                "duration_ms": duration_ms,
                "error": error,
            }
        )

    def complete_run(
        self,
        run_id: str,
        status: str = "completed",
        overall_confidence: Optional[float] = None,
    ) -> None:
        run = self._find_run(run_id)
        if not run:
            return
        run["completed_at"] = datetime.now(timezone.utc).isoformat()
        run["status"] = status
        run["overall_confidence"] = overall_confidence

    def list_runs(self, limit: int = 10) -> List[Dict[str, Any]]:
        return self._runs[:limit]

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self._find_run(run_id)


workflow_tracker = WorkflowTracker()
