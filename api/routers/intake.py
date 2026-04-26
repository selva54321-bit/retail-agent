from __future__ import annotations

from fastapi import APIRouter

from api.schemas.cycle import RunCycleResponse
from api.schemas.intake import IntakeChatRunRequest, IntakeFormRunRequest
from api.services.intake_service import run_cycle_from_chat, run_cycle_from_form


router = APIRouter(prefix="/intake", tags=["intake"])


@router.post("/form/run", response_model=RunCycleResponse)
def intake_form_and_run(payload: IntakeFormRunRequest) -> RunCycleResponse:
    result = run_cycle_from_form(payload)
    return RunCycleResponse(**result)


@router.post("/chat/run", response_model=RunCycleResponse)
def intake_chat_and_run(payload: IntakeChatRunRequest) -> RunCycleResponse:
    result = run_cycle_from_chat(payload)
    return RunCycleResponse(**result)
