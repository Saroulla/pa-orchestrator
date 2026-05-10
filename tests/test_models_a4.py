import pytest
from pydantic import ValidationError

from orchestrator.models import (
    Channel,
    CostLedgerRow,
    Event,
    EventKind,
)

TS = "2026-05-10T15:45:00"
SID = "sess-abcd1234"


# ---------------------------------------------------------------------------
# EventKind.SYSTEM_MESSAGE
# ---------------------------------------------------------------------------

def test_event_kind_system_message_value():
    assert EventKind.SYSTEM_MESSAGE == "system_message"


def test_event_kind_system_message_roundtrip():
    assert EventKind("system_message") is EventKind.SYSTEM_MESSAGE


def test_event_kind_system_message_in_enum():
    assert "system_message" in [k.value for k in EventKind]


# ---------------------------------------------------------------------------
# Event.message_type field
# ---------------------------------------------------------------------------

def test_event_message_type_defaults_none():
    ev = Event(
        session_id=SID, channel=Channel.WEB,
        kind=EventKind.JOB_COMPLETE,
        payload={"job_id": "j-1"},
        created_at=TS,
    )
    assert ev.message_type is None


def test_event_system_message_with_message_type():
    ev = Event(
        session_id=SID, channel=Channel.WEB,
        kind=EventKind.SYSTEM_MESSAGE,
        payload={"detail": "Groq unavailable"},
        created_at=TS,
        message_type="groq_promoted_to_haiku",
    )
    assert ev.kind == EventKind.SYSTEM_MESSAGE
    assert ev.message_type == "groq_promoted_to_haiku"


def test_event_system_message_roundtrip():
    ev = Event(
        session_id=SID, channel=Channel.TELEGRAM,
        kind=EventKind.SYSTEM_MESSAGE,
        payload={},
        created_at=TS,
        message_type="job_complete",
    )
    restored = Event(**ev.model_dump())
    assert restored.message_type == "job_complete"
    assert restored.kind == EventKind.SYSTEM_MESSAGE


def test_event_existing_kinds_message_type_still_none():
    for kind in (EventKind.JOB_COMPLETE, EventKind.JOB_PROGRESS, EventKind.ESCALATION_EXPIRED):
        ev = Event(
            session_id=SID, channel=Channel.WEB,
            kind=kind, payload={}, created_at=TS,
        )
        assert ev.message_type is None


# ---------------------------------------------------------------------------
# CostLedgerRow
# ---------------------------------------------------------------------------

def test_cost_ledger_row_with_tier():
    row = CostLedgerRow(
        adapter="pa_haiku",
        tokens=500,
        cost_usd=0.0025,
        tier="pa-haiku",
        timestamp=TS,
    )
    assert row.tier == "pa-haiku"
    assert row.cost_usd == pytest.approx(0.0025)


def test_cost_ledger_row_tier_default_empty():
    row = CostLedgerRow(adapter="claude_api", timestamp=TS)
    assert row.tier == ""


def test_cost_ledger_row_optional_fields():
    row = CostLedgerRow(adapter="pa_groq", timestamp=TS)
    assert row.id is None
    assert row.session_id is None
    assert row.job_id is None
    assert row.tokens == 0
    assert row.cost_usd == 0.0


def test_cost_ledger_row_full():
    row = CostLedgerRow(
        id=42,
        session_id=SID,
        job_id="job-xyz",
        adapter="google_cse",
        tokens=0,
        cost_usd=0.005,
        tier="pa-groq",
        timestamp=TS,
    )
    assert row.id == 42
    assert row.job_id == "job-xyz"
    assert row.tier == "pa-groq"


def test_cost_ledger_row_roundtrip():
    row = CostLedgerRow(
        session_id=SID,
        adapter="pa_haiku",
        tokens=1200,
        cost_usd=0.006,
        tier="pa-haiku",
        timestamp=TS,
    )
    restored = CostLedgerRow(**row.model_dump())
    assert restored.tier == "pa-haiku"
    assert restored.tokens == 1200
