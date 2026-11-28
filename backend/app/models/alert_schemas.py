"""In-app notification schemas (material investigations)."""

from typing import Literal, Optional

from pydantic import BaseModel


class AlertEventOut(BaseModel):
    id: int
    ticker: str
    alert_type: str
    title: str
    message: str
    observed_value: Optional[float] = None
    threshold: Optional[float] = None
    investigation_id: Optional[int] = None
    materiality_score: Optional[float] = None
    created_at: str
    read_at: Optional[str] = None
    is_read: bool = False
    feedback: str = ""


class AlertFeedbackRequest(BaseModel):
    feedback: Literal["", "useful", "not_useful"] = ""
