from __future__ import annotations

from api.errors import ApiError
from api.schemas.retailer import RetailerProfilePayload
from core import database as db


def list_retailers() -> list[dict]:
    return db.list_retailer_profiles()


def get_retailer_profile(retailer_id: int) -> dict:
    profile = db.load_retailer_profile(retailer_id)
    if not profile:
        raise ApiError(f"Retailer {retailer_id} not found", status_code=404)
    return profile


def save_retailer_profile(payload: RetailerProfilePayload) -> int:
    return db.save_retailer_profile(payload.model_dump())
