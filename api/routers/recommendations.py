from __future__ import annotations

from fastapi import APIRouter

from api.schemas.recommendation import (
    RecommendationApprovalRequest,
    RecommendationApprovalResponse,
    RecommendationListResponse,
)
from core import database as db


router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("/retailers/{retailer_id}", response_model=RecommendationListResponse)
def get_recommendations(
    retailer_id: int,
    limit: int = 50,
    pending_only: bool = False,
) -> RecommendationListResponse:
    if pending_only:
        recs = db.get_pending_recommendations(retailer_id)
    else:
        recs = db.get_all_recommendations(retailer_id, limit=limit)
    return RecommendationListResponse(retailer_id=retailer_id, recommendations=recs)


@router.post(
    "/retailers/{retailer_id}/cycles/{cycle_id}/approvals",
    response_model=RecommendationApprovalResponse,
)
def set_recommendation_approvals(
    retailer_id: int,
    cycle_id: str,
    payload: RecommendationApprovalRequest,
) -> RecommendationApprovalResponse:
    modified = db.update_recommendation_approvals(
        retailer_id=retailer_id,
        cycle_id=cycle_id,
        decisions=[d.model_dump() for d in payload.decisions],
    )
    return RecommendationApprovalResponse(modified_count=modified)
