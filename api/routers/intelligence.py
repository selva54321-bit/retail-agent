from __future__ import annotations

from fastapi import APIRouter

from api.schemas.intelligence import (
    CatalogSpySnapshotResponse,
    CompetitorCatalogResponse,
    DemandForecastResponse,
    DropPatternResponse,
    MarketIntelligenceResponse,
)
from api.services.intelligence_service import (
    get_catalog_spy_snapshot,
    get_competitor_catalog,
    get_drop_patterns,
    get_latest_demand_forecasts,
    get_market_intelligence,
)


router = APIRouter(prefix="/intelligence", tags=["intelligence"])


@router.get("/retailers/{retailer_id}", response_model=MarketIntelligenceResponse)
def market_intelligence(retailer_id: int, limit_per_competitor: int = 10) -> MarketIntelligenceResponse:
    items = get_market_intelligence(retailer_id, limit_per_competitor=limit_per_competitor)
    return MarketIntelligenceResponse(retailer_id=retailer_id, items=items)


@router.get("/retailers/{retailer_id}/drop-patterns", response_model=DropPatternResponse)
def drop_patterns(retailer_id: int, competitor_name: str | None = None) -> DropPatternResponse:
    patterns = get_drop_patterns(retailer_id, competitor_name=competitor_name)
    return DropPatternResponse(retailer_id=retailer_id, patterns=patterns)


@router.get("/retailers/{retailer_id}/catalog", response_model=CompetitorCatalogResponse)
def competitor_catalog(retailer_id: int, competitor_name: str | None = None) -> CompetitorCatalogResponse:
    items = get_competitor_catalog(retailer_id, competitor_name=competitor_name)
    return CompetitorCatalogResponse(
        retailer_id=retailer_id,
        competitor_name=competitor_name,
        items=items,
    )


@router.get("/retailers/{retailer_id}/demand-forecasts", response_model=DemandForecastResponse)
def demand_forecasts(retailer_id: int, limit: int = 50) -> DemandForecastResponse:
    forecasts = get_latest_demand_forecasts(retailer_id, limit=limit)
    return DemandForecastResponse(retailer_id=retailer_id, forecasts=forecasts)


@router.get("/retailers/{retailer_id}/catalog-spy", response_model=CatalogSpySnapshotResponse)
def catalog_spy_snapshot(retailer_id: int) -> CatalogSpySnapshotResponse:
    snapshot = get_catalog_spy_snapshot(retailer_id)
    return CatalogSpySnapshotResponse(**snapshot)
