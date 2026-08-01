"""Auth request/response schemas and watchlist models."""

from datetime import datetime
from typing import List
from typing import Optional as Opt

from pydantic import BaseModel, Field, field_validator


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        email = value.strip().lower()
        if "@" not in email or "." not in email.split("@")[-1]:
            raise ValueError("Enter a valid email address.")
        return email

    @field_validator("password")
    @classmethod
    def password_strength(cls, value: str) -> str:
        if not value or value.isspace():
            raise ValueError("Password cannot be blank.")
        return value


class UserLogin(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class UserOut(BaseModel):
    id: int
    email: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class WatchlistEntry(BaseModel):
    ticker: str
    added_at: str
    notes: str = ""
    shares: Opt[float] = None
    avg_cost: Opt[float] = None
    source: str = "manual"


class WatchlistAddBody(BaseModel):
    notes: str = Field(default="", max_length=500)


class PortfolioSummary(BaseModel):
    id: int
    name: str
    holdings_count: int
    holdings: List[WatchlistEntry] = Field(default_factory=list)
