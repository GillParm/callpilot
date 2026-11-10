"""REST endpoints used by the web app."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

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
