from __future__ import annotations

from fastapi import APIRouter

from api.schemas.cycle import CycleLogResponse, RunCycleRequest, RunCycleResponse
from api.services.cycle_service import run_agent_cycle
from core import database as db


router = APIRouter(prefix="/cycles", tags=["cycles"])


@router.post("/run", response_model=RunCycleResponse)
def run_cycle_endpoint(payload: RunCycleRequest) -> RunCycleResponse:
    result = run_agent_cycle(payload)
    return RunCycleResponse(**result)


@router.get("/retailers/{retailer_id}", response_model=CycleLogResponse)
def get_retailer_cycles(retailer_id: int, limit: int = 10) -> CycleLogResponse:
    cycles = db.get_recent_cycles(retailer_id, limit=limit)
    return CycleLogResponse(retailer_id=retailer_id, cycles=cycles)
