from __future__ import annotations

from pydantic import BaseModel, Field

from api.schemas.retailer import RetailerProfilePayload


class RunCycleRequest(BaseModel):
    retailer_id: int = 0
    stream: bool = False
    provider: str | None = None
    use_demo_profile: bool = False
    profile: RetailerProfilePayload | None = None


class RunCycleResponse(BaseModel):
    retailer_id: int
    cycle_id: str
    provider: str
    model: str
    summary: dict = Field(default_factory=dict)
    final_state: dict = Field(default_factory=dict)


class CycleLogResponse(BaseModel):
    retailer_id: int
    cycles: list[dict] = Field(default_factory=list)
