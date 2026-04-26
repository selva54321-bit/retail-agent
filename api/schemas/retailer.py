from __future__ import annotations

from pydantic import BaseModel, Field


class CatalogItemPayload(BaseModel):
    name: str
    sku: str
    current_price: float
    cost: float


class RetailerProfilePayload(BaseModel):
    store_name: str = ""
    category: str = ""
    subcategories: list[str] = Field(default_factory=list)
    location: str = ""
    brand_positioning: str = "mid-market"
    known_competitors: list[str] = Field(default_factory=list)
    pricing_strategy: str = "competitive_parity"
    cost_margin_floor: float = 0.1
    max_price_shift_pct: float = 0.15
    auto_apply_prices: bool = False
    alert_threshold_pct: float = 0.05
    scan_frequency: str = "daily"
    catalog: list[CatalogItemPayload] = Field(default_factory=list)
    onboarding_complete: bool = True


class SaveRetailerRequest(BaseModel):
    profile: RetailerProfilePayload


class SaveRetailerResponse(BaseModel):
    retailer_id: int


class RetailerListItem(BaseModel):
    id: int
    store_name: str
    updated_at: str | None = None
