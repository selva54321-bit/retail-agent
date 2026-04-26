from __future__ import annotations

from pydantic import BaseModel, Field


class MarketIntelligenceResponse(BaseModel):
    retailer_id: int
    items: list[dict] = Field(default_factory=list)


class DropPatternResponse(BaseModel):
    retailer_id: int
    patterns: list[dict] = Field(default_factory=list)


class CompetitorCatalogResponse(BaseModel):
    retailer_id: int
    competitor_name: str | None = None
    items: list[dict] = Field(default_factory=list)


class DemandForecastResponse(BaseModel):
    retailer_id: int
    forecasts: list[dict] = Field(default_factory=list)
