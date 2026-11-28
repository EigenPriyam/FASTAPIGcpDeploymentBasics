"""Check read models."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CheckRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    link_id: int
    status_code: int | None
    latency_ms: int | None
    error: str | None
    checked_at: datetime


class CheckResult(BaseModel):
    link_id: int
    last_status: str
    check: CheckRead
    archive_url: str | None


class CheckAllResult(BaseModel):
    total: int
    alive: int
    dead: int
    blocked: int
    results: list[CheckResult]
