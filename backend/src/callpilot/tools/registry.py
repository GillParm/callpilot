"""Tool specs, and how each provider wants them described.

One `ToolSpec` per tool. `json_schema()` flattens Pydantic's output into the
small JSON Schema subset that OpenAI, Gemini and Anthropic all accept: no
titles, no `$defs`, no `anyOf` for optionals (optional just means "not in
required") and no defaults (they are spelled out in the description instead).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from callpilot.tools.schemas import (
    AgentBookAppointmentArgs,
    AgentCancelAppointmentArgs,
    CheckAvailabilityArgs,
    EscalateArgs,
    LookupPatientArgs,
)

JSONSchema = dict[str, Any]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]

    def json_schema(self) -> JSONSchema:
        return simplify_schema(self.args_model.model_json_schema())


def _simplify_property(prop: JSONSchema) -> JSONSchema:
    prop = dict(prop)
    if "anyOf" in prop:
        options = [o for o in prop.pop("anyOf") if o.get("type") != "null"]
        if len(options) == 1:
            prop = {**options[0], **prop}
    default = prop.pop("default", None)
    prop.pop("title", None)
    if default is not None and "description" in prop:
        prop["description"] = f"{prop['description']} Default: {default}."
    return prop


def simplify_schema(schema: JSONSchema) -> JSONSchema:
    if "$defs" in schema:
        raise ValueError("tool arguments must be flat (no nested models)")
    return {
        "type": "object",
        "properties": {
            name: _simplify_property(prop) for name, prop in schema.get("properties", {}).items()
        },
        "required": list(schema.get("required", [])),
        "additionalProperties": False,
    }


AGENT_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "check_availability",
        "Find open appointment slots. The only source of truth for availability: "
        "never offer a time that this tool did not return.",
        CheckAvailabilityArgs,
    ),
    ToolSpec(
        "lookup_patient",
        "Verify an existing patient by full name and date of birth. Returns their "
        "patient_id and upcoming appointments. Required before booking or cancelling.",
        LookupPatientArgs,
    ),
    ToolSpec(
        "book_appointment",
        "Book a slot for a verified patient. Two steps: call with caller_confirmed=false "
        "to get a read-back, read it to the caller, and only after they say yes call "
        "again with caller_confirmed=true and the same arguments.",
        AgentBookAppointmentArgs,
    ),
    ToolSpec(
        "cancel_appointment",
        "Cancel one of a verified patient's upcoming appointments. Same two-step "
        "confirmation as book_appointment.",
        AgentCancelAppointmentArgs,
    ),
    ToolSpec(
        "escalate_to_human",
        "Hand the call to clinic staff: dental emergencies, billing or insurance "
        "questions, complaints, anything outside scheduling, or when the caller asks "
        "for a person.",
        EscalateArgs,
    ),
)

TOOLS_BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in AGENT_TOOLS}


def openai_tools(specs: Iterable[ToolSpec] = AGENT_TOOLS) -> list[dict[str, Any]]:
    """Realtime API `session.tools` entries."""
    return [
        {
            "type": "function",
            "name": s.name,
            "description": s.description,
            "parameters": s.json_schema(),
        }
        for s in specs
    ]


def anthropic_tools(specs: Iterable[ToolSpec] = AGENT_TOOLS) -> list[dict[str, Any]]:
    """Messages API `tools` entries."""
    return [
        {"name": s.name, "description": s.description, "input_schema": s.json_schema()}
        for s in specs
    ]


def gemini_function_declarations(
    specs: Iterable[ToolSpec] = AGENT_TOOLS,
) -> list[dict[str, Any]]:
    """Live API `function_declarations`. Tools block the turn until they return,
    because the agent must not speak about a slot it hasn't seen yet."""
    return [
        {
            "name": s.name,
            "description": s.description,
            "parameters_json_schema": s.json_schema(),
            "behavior": "BLOCKING",
        }
        for s in specs
    ]
