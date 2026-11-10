"""REST endpoints used by the web app."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from callpilot.calllog.store import get_call, list_calls
from callpilot.clinic.profile import CLINIC
from callpilot.db.seed import DEMO_PATIENTS
from callpilot.providers.factory import resolve_provider_name

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/config")
async def config(request: Request) -> dict[str, Any]:
    """What the UI needs to render the call console."""
    settings = request.app.state.settings
    return {
        "clinic": {
            "name": CLINIC.name,
            "address": CLINIC.address,
            "phone": CLINIC.phone,
            "hours": list(CLINIC.hours),
        },
        "providers": {
            "voice": resolve_provider_name(settings, "voice"),
            "text": resolve_provider_name(settings, "text"),
        },
        # Made-up patients you can use to try the demo.
        "demo_patients": [
            {"name": f"{first} {last}", "date_of_birth": dob}
            for _, first, last, dob in DEMO_PATIENTS
        ],
    }


@router.get("/calls")
async def calls(
    request: Request, limit: int = Query(default=50, ge=1, le=200)
) -> list[dict[str, Any]]:
    """Recent calls, newest first, with headline stats."""
    return list_calls(request.app.state.db, limit=limit)


@router.get("/calls/{call_id}")
async def call_detail(request: Request, call_id: str) -> dict[str, Any]:
    """One call with its full event stream: transcript, tools, timeline and latency."""
    call = get_call(request.app.state.db, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="No call with that id.")
    return call
