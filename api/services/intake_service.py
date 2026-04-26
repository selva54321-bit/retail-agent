from __future__ import annotations

from agents.intake_agent import build_profile_from_chat_messages, build_profile_from_transcript
from api.errors import ApiError
from api.schemas.intake import IntakeChatRunRequest, IntakeFormRunRequest
from api.services.cycle_service import run_agent_cycle_with_profile
from core.state import RetailerProfile


def run_cycle_from_form(req: IntakeFormRunRequest) -> dict:
    profile = RetailerProfile(**req.profile.model_dump())
    profile.onboarding_complete = True

    return run_agent_cycle_with_profile(
        retailer_id=req.retailer_id,
        profile=profile,
        stream=req.stream,
        provider=req.provider,
    )


def run_cycle_from_chat(req: IntakeChatRunRequest) -> dict:
    transcript = req.transcript.strip()

    if not transcript and not req.messages:
        raise ApiError("Provide either transcript or messages for chat intake.", status_code=422)

    if not transcript:
        transcript = "\n".join(
            [
                f"{('Retailer' if m.role.lower() in ('user', 'human', 'retailer') else 'Agent')}: {m.content}"
                for m in req.messages
            ]
        )

    if req.messages:
        profile = build_profile_from_chat_messages([m.model_dump() for m in req.messages])
    else:
        profile = build_profile_from_transcript(transcript)

    return run_agent_cycle_with_profile(
        retailer_id=req.retailer_id,
        profile=profile,
        stream=req.stream,
        provider=req.provider,
    )
