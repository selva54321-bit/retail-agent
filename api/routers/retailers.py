from __future__ import annotations

from fastapi import APIRouter

from api.schemas.retailer import (
    RetailerListItem,
    SaveRetailerRequest,
    SaveRetailerResponse,
)
from api.services.retailer_service import (
    get_retailer_profile,
    list_retailers,
    save_retailer_profile,
)


router = APIRouter(prefix="/retailers", tags=["retailers"])


@router.get("", response_model=list[RetailerListItem])
def list_retailers_endpoint() -> list[RetailerListItem]:
    return [RetailerListItem(**r) for r in list_retailers()]


@router.get("/{retailer_id}")
def get_retailer_endpoint(retailer_id: int) -> dict:
    return get_retailer_profile(retailer_id)


@router.post("", response_model=SaveRetailerResponse)
def create_or_update_retailer(payload: SaveRetailerRequest) -> SaveRetailerResponse:
    rid = save_retailer_profile(payload.profile)
    return SaveRetailerResponse(retailer_id=rid)
