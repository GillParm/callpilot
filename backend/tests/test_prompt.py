from datetime import datetime

from callpilot.agent.prompt import GREETING, build_instructions


def test_prompt_anchors_the_current_date(now: datetime) -> None:
    text = build_instructions(now, "voice")
    assert "Tuesday, November 10, 2026" in text
    assert "2026-11-10" in text


def test_prompt_contains_clinic_facts_and_rules(now: datetime) -> None:
    text = build_instructions(now, "voice")
    assert "Brightside Dental" in text
    assert "Dr. Amara Okafor" in text
    assert "caller_confirmed=false" in text
    assert "911" in text
    assert GREETING in text


def test_style_depends_on_mode(now: datetime) -> None:
    assert "read aloud" in build_instructions(now, "voice")
    assert "typing" in build_instructions(now, "text")
