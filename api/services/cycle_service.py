from __future__ import annotations

from agents.intake_agent import load_demo_profile
from api.errors import ApiError
from api.schemas.cycle import RunCycleRequest
from api.services.serializer import to_plain
from core import database as db
from core.graph import run_cycle
from core.llm import get_active_model_name, get_active_provider, set_provider
from core.state import RetailerProfile


def _to_profile(payload) -> RetailerProfile:
    if payload is None:
        return None
    return RetailerProfile(**payload.model_dump())


def _resolve_retailer_id_after_cycle(input_id: int) -> int:
    if input_id > 0:
        return input_id
    profiles = db.list_retailer_profiles()
    return profiles[0]["id"] if profiles else 0


def run_agent_cycle_with_profile(
    retailer_id: int,
    profile: RetailerProfile | None,
    stream: bool = False,
    provider: str | None = None,
) -> dict:
    if provider:
        set_provider(provider)

    if retailer_id == 0 and profile is None:
        raise ApiError(
            "For a new retailer, provide profile data to avoid interactive intake.",
            status_code=422,
        )

    final = run_cycle(retailer_id=retailer_id, profile=profile, stream=stream)
    final_state = to_plain(final)
    resolved_retailer_id = int(
        final_state.get("retailer_id")
        or _resolve_retailer_id_after_cycle(retailer_id)
    )

    return {
        "retailer_id": resolved_retailer_id,
        "cycle_id": final_state.get("cycle_id", ""),
        "provider": get_active_provider(),
        "model": get_active_model_name(),
        "summary": {
            "scraped_records": len(final_state.get("scraped_records", [])),
            "matches": len(final_state.get("product_matches", [])),
            "recommendations": len(final_state.get("recommendations", [])),
            "alerts": len(final_state.get("alerts", [])),
            "errors": len(final_state.get("errors", [])),
        },
        "final_state": final_state,
    }


def run_agent_cycle(req: RunCycleRequest) -> dict:
    if req.retailer_id == 0 and req.profile is None and not req.use_demo_profile:
        raise ApiError(
            "For a new retailer, provide profile or set use_demo_profile=true to avoid interactive intake.",
            status_code=422,
        )

    profile = load_demo_profile() if req.use_demo_profile else _to_profile(req.profile)
    return run_agent_cycle_with_profile(
        retailer_id=req.retailer_id,
        profile=profile,
        stream=req.stream,
        provider=req.provider,
    )
