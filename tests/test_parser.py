"""Unit tests for orchestrator/parser.py — Step 6 gate."""
import pytest

from orchestrator.models import Caller, Channel, Mode
from orchestrator.parser import parse

SESSION = "test-sess-01"


def test_cto_command_kind_and_payload():
    """@CTO write a script → kind=code, payload text = remainder."""
    intent = parse("@CTO write a script", SESSION, Mode.PA)
    assert intent.kind == "code"
    assert intent.payload["text"] == "write a script"


def test_pa_command_kind():
    """@PA hello → kind=reason (switch signal to PA)."""
    intent = parse("@PA hello", SESSION, Mode.CTO)
    assert intent.kind == "reason"
    assert intent.payload["text"] == "hello"


def test_cost_meta_command():
    """@cost → meta_command=cost in payload."""
    intent = parse("@cost", SESSION, Mode.PA)
    assert intent.payload.get("meta_command") == "cost"


def test_cost_no_switch_in_cto_mode():
    """@cost from CTO mode must not produce kind=code (no-op via meta_command)."""
    intent = parse("@cost", SESSION, Mode.CTO)
    assert intent.payload.get("meta_command") == "cost"


def test_escaped_at_no_switch():
    r"""\\@CTO literal → no switch; payload text is "@CTO literal" (backslash stripped)."""
    intent = parse(r"\@CTO literal", SESSION, Mode.PA)
    # kind is determined by current mode, not by the escaped token
    assert intent.kind == "reason"
    assert intent.payload["text"] == "@CTO literal"


def test_mid_message_at_is_literal():
    """tell me about @CTO patterns → @ not first token, treated as literal."""
    intent = parse("tell me about @CTO patterns", SESSION, Mode.PA)
    assert intent.kind == "reason"
    assert "@CTO" in intent.payload["text"]


def test_empty_string_pa_mode():
    """Empty string in PA mode → kind=reason."""
    intent = parse("", SESSION, Mode.PA)
    assert intent.kind == "reason"


def test_empty_string_cto_mode():
    """Empty string in CTO mode → kind=code."""
    intent = parse("", SESSION, Mode.CTO)
    assert intent.kind == "code"


def test_desktop_command():
    """@Desktop → kind=desktop."""
    intent = parse("@Desktop", SESSION, Mode.PA)
    assert intent.kind == "desktop"


def test_rebuild_plan_command():
    """@rebuild-plan path/to/job.md → kind=file_write, meta_command=rebuild_plan."""
    intent = parse("@rebuild-plan jobs/morning.md", SESSION, Mode.PA)
    assert intent.kind == "file_write"
    assert intent.payload["meta_command"] == "rebuild_plan"
    assert intent.payload["text"] == "jobs/morning.md"


def test_session_id_propagated():
    intent = parse("hello", SESSION, Mode.PA)
    assert intent.session_id == SESSION


def test_caller_propagated():
    intent = parse("hello", SESSION, Mode.PA, caller=Caller.JOB_RUNNER)
    assert intent.caller == Caller.JOB_RUNNER


def test_mode_propagated():
    intent = parse("hello", SESSION, Mode.CTO)
    assert intent.mode == Mode.CTO


def test_cto_no_remainder():
    """@CTO with no text after it → payload text is empty string."""
    intent = parse("@CTO", SESSION, Mode.PA)
    assert intent.kind == "code"
    assert intent.payload["text"] == ""


def test_escaped_at_no_remainder():
    r"""\\@PA with no remainder → text is just "@PA"."""
    intent = parse(r"\@PA", SESSION, Mode.PA)
    assert intent.payload["text"] == "@PA"
