"""D3 gate tests — orchestrator.maker.promotion.call_with_promotion."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.models import Caller, ErrorCode, ErrorDetail, Intent, Mode, Result
from orchestrator.maker.promotion import call_with_promotion


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _intent(session_id: str = "sess-promo1") -> Intent:
    return Intent(
        kind="reason",
        payload={"messages": [{"role": "user", "content": "hello"}]},
        session_id=session_id,
        mode=Mode.PA,
        caller=Caller.MAKER,
        deadline_s=10.0,
        attempt=0,
    )


def _ok_result(data: str = "answer") -> Result:
    return Result(ok=True, data=data, meta={"tool": "pa_groq", "latency_ms": 100,
                                             "tokens_in": 5, "tokens_out": 10})


def _err_result(code: ErrorCode, retriable: bool = True) -> Result:
    return Result(
        ok=False,
        error=ErrorDetail(code=code, message=str(code), retriable=retriable),
        meta={"tool": "pa_groq", "latency_ms": 50, "tokens_in": 0, "tokens_out": 0},
    )


def _make_db() -> MagicMock:
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    return db


def _make_groq(result: Result) -> MagicMock:
    adapter = MagicMock()
    adapter.invoke = AsyncMock(return_value=result)
    return adapter


def _make_haiku(result: Result) -> MagicMock:
    adapter = MagicMock()
    adapter.invoke = AsyncMock(return_value=result)
    return adapter


# ---------------------------------------------------------------------------
# 1. test_groq_ok_returns_immediately
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_groq_ok_returns_immediately():
    """pa_groq returns ok=True → result returned immediately.
    pa_haiku must never be called and no system_message emitted."""
    ok = _ok_result()
    pa_groq = _make_groq(ok)
    pa_haiku = _make_haiku(_ok_result("haiku-answer"))
    db = _make_db()
    intent = _intent()

    with patch("orchestrator.maker.promotion.system_messages.emit", new_callable=AsyncMock) as mock_emit:
        result = await call_with_promotion(intent, db, pa_groq, pa_haiku, "web")

    assert result.ok is True
    assert result.data == "answer"
    pa_groq.invoke.assert_awaited_once()
    pa_haiku.invoke.assert_not_awaited()
    mock_emit.assert_not_awaited()


# ---------------------------------------------------------------------------
# 2. test_groq_rate_limit_promotes_to_haiku
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_groq_rate_limit_promotes_to_haiku():
    """pa_groq returns RATE_LIMIT → pa_haiku called, system_message emitted."""
    rate_limit = _err_result(ErrorCode.RATE_LIMIT)
    haiku_ok = _ok_result("haiku-answer")
    pa_groq = _make_groq(rate_limit)
    pa_haiku = _make_haiku(haiku_ok)
    db = _make_db()
    intent = _intent()

    with patch("orchestrator.maker.promotion.system_messages.emit", new_callable=AsyncMock) as mock_emit:
        result = await call_with_promotion(intent, db, pa_groq, pa_haiku, "web")

    pa_groq.invoke.assert_awaited_once()
    pa_haiku.invoke.assert_awaited_once()
    mock_emit.assert_awaited_once()
    # Verify emit type argument
    _, _, _, msg_type, _ = mock_emit.call_args.args
    assert msg_type == "groq_promoted_to_haiku"


# ---------------------------------------------------------------------------
# 3. test_groq_tool_error_promotes_to_haiku
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_groq_tool_error_promotes_to_haiku():
    """pa_groq returns ok=False with TOOL_ERROR → pa_haiku called, system_message emitted."""
    tool_err = _err_result(ErrorCode.TOOL_ERROR)
    haiku_ok = _ok_result("haiku-answer")
    pa_groq = _make_groq(tool_err)
    pa_haiku = _make_haiku(haiku_ok)
    db = _make_db()
    intent = _intent()

    with patch("orchestrator.maker.promotion.system_messages.emit", new_callable=AsyncMock) as mock_emit:
        result = await call_with_promotion(intent, db, pa_groq, pa_haiku, "telegram")

    pa_groq.invoke.assert_awaited_once()
    pa_haiku.invoke.assert_awaited_once()
    mock_emit.assert_awaited_once()
    _, _, _, msg_type, _ = mock_emit.call_args.args
    assert msg_type == "groq_promoted_to_haiku"


# ---------------------------------------------------------------------------
# 4. test_haiku_result_returned_after_promotion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_haiku_result_returned_after_promotion():
    """After promotion, haiku's Result (ok=True) is the final return value."""
    tool_err = _err_result(ErrorCode.TOOL_ERROR)
    haiku_ok = _ok_result("haiku-data")
    pa_groq = _make_groq(tool_err)
    pa_haiku = _make_haiku(haiku_ok)
    db = _make_db()
    intent = _intent()

    with patch("orchestrator.maker.promotion.system_messages.emit", new_callable=AsyncMock):
        result = await call_with_promotion(intent, db, pa_groq, pa_haiku, "web")

    assert result.ok is True
    assert result.data == "haiku-data"


# ---------------------------------------------------------------------------
# 5. test_prefer_haiku_skips_groq
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prefer_haiku_skips_groq():
    """prefer='pa-haiku' → only pa_haiku called, pa_groq never invoked, no system_message."""
    pa_groq = _make_groq(_ok_result())
    pa_haiku = _make_haiku(_ok_result("haiku-direct"))
    db = _make_db()
    intent = _intent()

    with patch("orchestrator.maker.promotion.system_messages.emit", new_callable=AsyncMock) as mock_emit:
        result = await call_with_promotion(intent, db, pa_groq, pa_haiku, "web", prefer="pa-haiku")

    pa_groq.invoke.assert_not_awaited()
    pa_haiku.invoke.assert_awaited_once()
    mock_emit.assert_not_awaited()
    assert result.ok is True
    assert result.data == "haiku-direct"


# ---------------------------------------------------------------------------
# 6. test_prefer_haiku_failure_returned_directly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prefer_haiku_failure_returned_directly():
    """prefer='pa-haiku', haiku fails → failure Result returned, no further promotion."""
    haiku_err = _err_result(ErrorCode.TOOL_ERROR, retriable=False)
    pa_groq = _make_groq(_ok_result())
    pa_haiku = _make_haiku(haiku_err)
    db = _make_db()
    intent = _intent()

    with patch("orchestrator.maker.promotion.system_messages.emit", new_callable=AsyncMock) as mock_emit:
        result = await call_with_promotion(intent, db, pa_groq, pa_haiku, "web", prefer="pa-haiku")

    pa_groq.invoke.assert_not_awaited()
    pa_haiku.invoke.assert_awaited_once()
    mock_emit.assert_not_awaited()
    assert result.ok is False
    assert result.error.code == ErrorCode.TOOL_ERROR
