from __future__ import annotations

from pydantic import BaseModel, Field


class DashboardAlert(BaseModel):
    severity: str = "medium"
    message: str
    source: str = "analytics"


class DashboardReportResponse(BaseModel):
    retailer_id: int
    cycle_id: str
    cycle_log: dict = Field(default_factory=dict)
    analytics: list[dict] = Field(default_factory=list)
    recommendations: list[dict] = Field(default_factory=list)
    market_intelligence: list[dict] = Field(default_factory=list)
    drop_patterns: list[dict] = Field(default_factory=list)
    competitor_catalog: list[dict] = Field(default_factory=list)
    alerts: list[DashboardAlert] = Field(default_factory=list)
    briefing: str = ""
