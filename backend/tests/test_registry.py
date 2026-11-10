import json

import pytest

from callpilot.tools.registry import (
    AGENT_TOOLS,
    TOOLS_BY_NAME,
    ToolSpec,
    anthropic_tools,
    gemini_function_declarations,
    openai_tools,
)

EXPECTED = {
    "check_availability",
    "lookup_patient",
    "book_appointment",
    "cancel_appointment",
    "escalate_to_human",
}


def test_all_five_tools_are_registered() -> None:
    assert set(TOOLS_BY_NAME) == EXPECTED


@pytest.mark.parametrize("spec", AGENT_TOOLS, ids=lambda s: s.name)
def test_schemas_use_the_portable_subset(spec: ToolSpec) -> None:
    schema = spec.json_schema()
    text = json.dumps(schema)
    for banned in ('"title"', '"anyOf"', '"$defs"', '"default"'):
        assert banned not in text
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) <= set(schema["properties"])


def test_optional_fields_are_not_required() -> None:
    schema = TOOLS_BY_NAME["check_availability"].json_schema()
    assert schema["required"] == ["appointment_type", "date_from"]
    assert schema["properties"]["date_to"]["type"] == "string"
    assert "Default: any." in schema["properties"]["time_of_day"]["description"]


def test_booking_schema_exposes_confirmation_flag() -> None:
    props = TOOLS_BY_NAME["book_appointment"].json_schema()["properties"]
    assert props["caller_confirmed"]["type"] == "boolean"


def test_provider_renderings() -> None:
    assert {t["name"] for t in openai_tools()} == EXPECTED
    assert all(t["type"] == "function" for t in openai_tools())
    assert all("input_schema" in t for t in anthropic_tools())
    assert all(d["behavior"] == "BLOCKING" for d in gemini_function_declarations())
