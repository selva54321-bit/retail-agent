from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    details: dict


class MessageResponse(BaseModel):
    message: str
