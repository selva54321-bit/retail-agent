from __future__ import annotations

from fastapi import APIRouter

from api.schemas.dashboard import DashboardReportResponse
from api.services.dashboard_service import get_cycle_dashboard, get_latest_cycle_dashboard


router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/retailers/{retailer_id}/latest", response_model=DashboardReportResponse)
def latest_dashboard(retailer_id: int) -> DashboardReportResponse:
    report = get_latest_cycle_dashboard(retailer_id)
    return DashboardReportResponse(**report)


@router.get("/retailers/{retailer_id}/cycles/{cycle_id}", response_model=DashboardReportResponse)
def cycle_dashboard(retailer_id: int, cycle_id: str) -> DashboardReportResponse:
    report = get_cycle_dashboard(retailer_id, cycle_id)
    return DashboardReportResponse(**report)
