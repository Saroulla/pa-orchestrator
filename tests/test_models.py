import pytest
from pydantic import ValidationError

from orchestrator.models import (
    AdapterManifest,
    AdapterParam,
    Caller,
    Channel,
    ErrorCode,
    ErrorDetail,
    Escalation,
    EscalationStatus,
    Event,
    EventKind,
    Intent,
    Mode,
    Result,
    Session,
)

TS = "2026-05-09T10:00:00"
SID = "sess-abcd1234"


# ---------------------------------------------------------------------------
# Enum membership
# ---------------------------------------------------------------------------

def test_mode_values():
    assert Mode.PA == "PA"
    assert Mode.DESKTOP == "DESKTOP"


def test_channel_values():
    assert Channel.WEB == "web"
    assert Channel.TELEGRAM == "telegram"


def test_caller_values():
    assert Caller.PA == "pa"
    assert Caller.JOB_RUNNER == "job_runner"


def test_error_code_all_values():
    for code in ("TIMEOUT", "RATE_LIMIT", "TOOL_ERROR", "QUOTA",
                 "BAD_INPUT", "UNAUTHORIZED", "INTERNAL"):
        assert ErrorCode(code) == code


def test_escalation_status_values():
    for s in ("pending", "resolved", "expired", "cancelled"):
        assert EscalationStatus(s) == s


def test_event_kind_values():
    for k in ("job_complete", "job_progress", "escalation_expired"):
        assert EventKind(k) == k


# ---------------------------------------------------------------------------
# Invalid Mode raises ValidationError
# ---------------------------------------------------------------------------

def test_invalid_mode_raises():
    with pytest.raises(ValidationError):
        Intent(
            kind="reason", payload={}, session_id=SID,
            mode="INVALID", caller=Caller.PA, deadline_s=30.0,
        )


def test_invalid_channel_raises():
    with pytest.raises(ValidationError):
        Session(id=SID, channel="sms", mode=Mode.PA,
                created_at=TS, last_active=TS)


def test_invalid_error_code_raises():
    with pytest.raises(ValidationError):
        ErrorDetail(code="UNKNOWN", message="x", retriable=False)


# ---------------------------------------------------------------------------
# session_id validator — accept
# ---------------------------------------------------------------------------

def test_session_id_accept_alphanumeric():
    i = Intent(kind="reason", payload={}, session_id="abcd1234",
               mode=Mode.PA, caller=Caller.PA, deadline_s=30.0)
    assert i.session_id == "abcd1234"


def test_session_id_accept_with_dash_and_underscore():
    i = Intent(kind="reason", payload={}, session_id="sess-id_ok",
               mode=Mode.PA, caller=Caller.PA, deadline_s=30.0)
    assert i.session_id == "sess-id_ok"


def test_session_id_accept_exactly_8_chars():
    i = Intent(kind="reason", payload={}, session_id="12345678",
               mode=Mode.PA, caller=Caller.PA, deadline_s=30.0)
    assert i.session_id == "12345678"


def test_session_id_accept_exactly_64_chars():
    sid = "a" * 64
    i = Intent(kind="reason", payload={}, session_id=sid,
               mode=Mode.PA, caller=Caller.PA, deadline_s=30.0)
    assert i.session_id == sid


# ---------------------------------------------------------------------------
# session_id validator — reject
# ---------------------------------------------------------------------------

def test_session_id_reject_too_short():
    with pytest.raises(ValidationError):
        Intent(kind="reason", payload={}, session_id="abc",
               mode=Mode.PA, caller=Caller.PA, deadline_s=30.0)


def test_session_id_reject_too_long():
    with pytest.raises(ValidationError):
        Intent(kind="reason", payload={}, session_id="a" * 65,
               mode=Mode.PA, caller=Caller.PA, deadline_s=30.0)


def test_session_id_reject_space():
    with pytest.raises(ValidationError):
        Intent(kind="reason", payload={}, session_id="sess id12",
               mode=Mode.PA, caller=Caller.PA, deadline_s=30.0)


def test_session_id_reject_special_chars():
    with pytest.raises(ValidationError):
        Intent(kind="reason", payload={}, session_id="sess@id!###",
               mode=Mode.PA, caller=Caller.PA, deadline_s=30.0)


def test_session_model_invalid_id_raises():
    with pytest.raises(ValidationError):
        Session(id="bad id!", channel=Channel.WEB, mode=Mode.PA,
                created_at=TS, last_active=TS)


# ---------------------------------------------------------------------------
# ErrorDetail
# ---------------------------------------------------------------------------

def test_error_detail_retriable_true():
    e = ErrorDetail(code=ErrorCode.TIMEOUT, message="timed out", retriable=True)
    assert e.retriable is True


def test_error_detail_retriable_false():
    e = ErrorDetail(code=ErrorCode.UNAUTHORIZED, message="denied", retriable=False)
    assert e.retriable is False


# ---------------------------------------------------------------------------
# Intent — all kinds
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", [
    "reason", "code", "search", "file_read", "file_write",
    "external_api", "desktop", "plan_step",
])
def test_intent_all_kinds(kind):
    i = Intent(kind=kind, payload={"k": "v"}, session_id=SID,
               mode=Mode.PA, caller=Caller.JOB_RUNNER, deadline_s=10.0)
    assert i.kind == kind


def test_intent_invalid_kind_raises():
    with pytest.raises(ValidationError):
        Intent(kind="invalid_kind", payload={}, session_id=SID,
               mode=Mode.PA, caller=Caller.PA, deadline_s=30.0)


def test_intent_default_attempt():
    i = Intent(kind="search", payload={}, session_id=SID,
               mode=Mode.PA, caller=Caller.PA, deadline_s=5.0)
    assert i.attempt == 0


# ---------------------------------------------------------------------------
# Result roundtrip
# ---------------------------------------------------------------------------

def test_result_ok_roundtrip():
    r = Result(
        ok=True,
        data={"answer": 42},
        cost_usd=0.001,
        meta={"tool": "claude_api", "latency_ms": 200,
              "tokens_in": 100, "tokens_out": 50},
    )
    restored = Result(**r.model_dump())
    assert restored.ok is True
    assert restored.data == {"answer": 42}
    assert restored.cost_usd == pytest.approx(0.001)
    assert restored.meta["tool"] == "claude_api"


def test_result_error_roundtrip():
    r = Result(
        ok=False,
        error=ErrorDetail(code=ErrorCode.TOOL_ERROR, message="boom", retriable=True),
        cost_usd=0.0,
        meta={},
    )
    restored = Result(**r.model_dump())
    assert restored.ok is False
    assert restored.error.code == ErrorCode.TOOL_ERROR
    assert restored.error.retriable is True


def test_result_defaults():
    r = Result(ok=True)
    assert r.data is None
    assert r.error is None
    assert r.cost_usd == 0.0
    assert r.meta == {}


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

def test_session_defaults():
    s = Session(id=SID, channel=Channel.WEB, created_at=TS, last_active=TS)
    assert s.mode == Mode.PA
    assert s.cc_pid is None
    assert s.telegram_chat_id is None
    assert s.cost_to_date_usd == 0.0
    assert s.summary_anchor is None


def test_session_full():
    s = Session(
        id=SID, channel=Channel.TELEGRAM, mode=Mode.DESKTOP,
        cc_pid=1234, telegram_chat_id=999,
        cost_to_date_usd=1.5, summary_anchor="prev summary",
        created_at=TS, last_active=TS,
    )
    assert s.cc_pid == 1234
    assert s.telegram_chat_id == 999


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------

def test_escalation_defaults():
    e = Escalation(
        id="esc-00000001", session_id=SID, channel=Channel.WEB,
        created_at=TS, expires_at=TS,
        options={"a": "retry", "b": "skip"},
        context={"error_code": "TIMEOUT"},
    )
    assert e.status == EscalationStatus.PENDING
    assert e.resolved_with is None


def test_escalation_resolved():
    e = Escalation(
        id="esc-00000002", session_id=SID, channel=Channel.TELEGRAM,
        created_at=TS, expires_at=TS,
        options={"a": "retry"}, context={},
        status=EscalationStatus.RESOLVED, resolved_with="a",
    )
    assert e.status == EscalationStatus.RESOLVED
    assert e.resolved_with == "a"


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------

def test_event_defaults():
    ev = Event(
        session_id=SID, channel=Channel.WEB,
        kind=EventKind.JOB_COMPLETE,
        payload={"job_id": "j-1", "status": "ok"},
        created_at=TS,
    )
    assert ev.id is None
    assert ev.delivered is False
    assert ev.delivered_at is None


def test_event_delivered():
    ev = Event(
        session_id=SID, channel=Channel.TELEGRAM,
        kind=EventKind.ESCALATION_EXPIRED,
        payload={}, created_at=TS,
        delivered=True, delivered_at=TS,
    )
    assert ev.delivered is True


# ---------------------------------------------------------------------------
# AdapterManifest
# ---------------------------------------------------------------------------

def test_adapter_manifest_empty():
    m = AdapterManifest()
    assert m.required == []
    assert m.optional == []


def test_adapter_manifest_with_params():
    m = AdapterManifest(
        required=[AdapterParam(name="url", type="str", description="target")],
        optional=[AdapterParam(name="timeout", type="int")],
    )
    assert m.required[0].name == "url"
    assert m.optional[0].type == "int"
    assert m.optional[0].description == ""
