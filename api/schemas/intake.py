from __future__ import annotations

from pydantic import BaseModel, Field

from api.schemas.retailer import RetailerProfilePayload


class IntakeChatMessage(BaseModel):
    role: str = "user"
    content: str


class IntakeFormRunRequest(BaseModel):
    retailer_id: int = 0
    stream: bool = False
    provider: str | None = None
    profile: RetailerProfilePayload


class IntakeChatRunRequest(BaseModel):
    retailer_id: int = 0
    stream: bool = False
    provider: str | None = None
    transcript: str = ""
    messages: list[IntakeChatMessage] = Field(default_factory=list)
