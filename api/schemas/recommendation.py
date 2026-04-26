from __future__ import annotations

from pydantic import BaseModel, Field


class RecommendationApprovalDecision(BaseModel):
    retailer_sku: str
    approved: bool


class RecommendationApprovalRequest(BaseModel):
    decisions: list[RecommendationApprovalDecision] = Field(default_factory=list)


class RecommendationApprovalResponse(BaseModel):
    modified_count: int


class RecommendationListResponse(BaseModel):
    retailer_id: int
    recommendations: list[dict] = Field(default_factory=list)
