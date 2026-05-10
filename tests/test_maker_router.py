"""R4 — tests/test_maker_router.py

Coverage:
- parse_at_prefix: full truth table (pure function)
- _dispatch_light: success / failure / None-adapter / adapter-raises
- _dispatch_cto: done / error_escalation / confirmation_needed /
                 confirmed=True prefix / None-dispatcher / action-skipped
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.maker import router
from orchestrator.maker.router import (
    _CONFIRMATION_PROMPT_PREFIX,
    _dispatch_cto,
    _dispatch_light,
    parse_at_prefix,
)
from orchestrator.models import Caller, ErrorCode, ErrorDetail, Mode, Result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(dispatcher=None, pa_groq=None, pa_haiku=None):
    ctx = MagicMock()
    ctx.dispatcher = dispatcher
    ctx.pa_groq = pa_groq
    ctx.pa_haiku = pa_haiku
    ctx.db = MagicMock()
    return ctx


def make_stream(*events):
    """Return an async-generator factory that yields the given events."""
    async def _gen(intent, db):
        for e in events:
            yield e
    return _gen


def make_capturing_stream(captured: list, *events):
    """Like make_stream but records the intent passed in."""
    async def _gen(intent, db):
        captured.append(intent)
        for e in events:
            yield e
    return _gen


# ---------------------------------------------------------------------------
# parse_at_prefix — truth table
# ---------------------------------------------------------------------------

class TestParseAtPrefix:
    @pytest.mark.parametrize("text,expected", [
        ("@maker hi",              ("maker",    "hi")),
        ("@cto rm -rf /",         ("cto",      "rm -rf /")),
        ("@pa-groq ask",          ("pa-groq",  "ask")),
        ("@pa-haiku tell me",     ("pa-haiku", "tell me")),
        ("@CTO upper",            ("cto",      "upper")),
        ("@maker",                ("maker",    "")),
        ("\\@cto literal",        (None,       "\\@cto literal")),
        ("plain text",            (None,       "plain text")),
        ("",                      (None,       "")),
        ("   @cto leading ws",    ("cto",      "leading ws")),
        ("mid-line @cto",         (None,       "mid-line @cto")),
    ])
    def test_parse_at_prefix(self, text, expected):
        assert parse_at_prefix(text) == expected


# ---------------------------------------------------------------------------
# _dispatch_light
# ---------------------------------------------------------------------------

class TestDispatchLight:
    @pytest.mark.asyncio
    async def test_success_returns_data(self):
        adapter = AsyncMock()
        adapter.invoke = AsyncMock(return_value=Result(ok=True, data="answer"))
        result = await _dispatch_light(adapter, "hello", "sess12345678")
        assert result == "answer"

    @pytest.mark.asyncio
    async def test_failure_returns_error_message(self):
        adapter = AsyncMock()
        adapter.invoke = AsyncMock(return_value=Result(
            ok=False,
            error=ErrorDetail(code=ErrorCode.TOOL_ERROR, message="boom", retriable=False),
        ))
        result = await _dispatch_light(adapter, "hello", "sess12345678")
        assert result == "Sorry, hit an error: boom"

    @pytest.mark.asyncio
    async def test_none_adapter_returns_unavailable(self):
        result = await _dispatch_light(None, "hello", "sess12345678")
        assert result == "Tier adapter unavailable."

    @pytest.mark.asyncio
    async def test_adapter_raises_returns_error_string(self):
        adapter = AsyncMock()
        adapter.invoke = AsyncMock(side_effect=RuntimeError("network down"))
        result = await _dispatch_light(adapter, "hello", "sess12345678")
        assert "Sorry, hit an error:" in result
        assert "network down" in result


# ---------------------------------------------------------------------------
# _dispatch_cto
# ---------------------------------------------------------------------------

class TestDispatchCTO:
    @pytest.mark.asyncio
    async def test_done_event_returns_text(self):
        dispatcher = MagicMock()
        dispatcher.stream = make_stream({"type": "done", "text": "all done"})
        ctx = _make_ctx(dispatcher=dispatcher)

        with patch.object(router, "build_context", new=AsyncMock(return_value=[])):
            result = await _dispatch_cto("do task", "sess12345678", "web", ctx, False)

        assert result == "all done"

    @pytest.mark.asyncio
    async def test_error_escalation_returns_content(self):
        dispatcher = MagicMock()
        dispatcher.stream = make_stream({"type": "error_escalation", "content": "failed hard"})
        ctx = _make_ctx(dispatcher=dispatcher)

        with patch.object(router, "build_context", new=AsyncMock(return_value=[])):
            result = await _dispatch_cto("do task", "sess12345678", "web", ctx, False)

        assert result == "failed hard"

    @pytest.mark.asyncio
    async def test_confirmation_needed_calls_escalation_create(self):
        dispatcher = MagicMock()
        dispatcher.stream = make_stream({
            "type": "confirmation_needed",
            "content": "are you sure?",
            "options": {"a": "yes", "b": "no"},
        })
        ctx = _make_ctx(dispatcher=dispatcher)
        mock_create = AsyncMock()

        with (
            patch.object(router, "build_context", new=AsyncMock(return_value=[])),
            patch.object(router.escalation, "create", new=mock_create),
        ):
            result = await _dispatch_cto(
                "write file X", "sess12345678", "web", ctx, False
            )

        assert result == "are you sure?"
        mock_create.assert_awaited_once()
        _db, sid, channel, options, ctx_payload_str = mock_create.call_args.args
        assert sid == "sess12345678"
        assert channel == "web"
        assert options == {"a": "yes", "b": "no"}

        ctx_payload = json.loads(ctx_payload_str)
        # deferred_intent.text must be the ORIGINAL request text, not prefixed
        assert ctx_payload["deferred_intent"]["text"] == "write file X"

    @pytest.mark.asyncio
    async def test_confirmed_true_prefixes_intent_text(self):
        captured = []
        dispatcher = MagicMock()
        dispatcher.stream = make_capturing_stream(
            captured, {"type": "done", "text": "done"}
        )
        ctx = _make_ctx(dispatcher=dispatcher)

        with patch.object(router, "build_context", new=AsyncMock(return_value=[])):
            await _dispatch_cto("my task", "sess12345678", "web", ctx, confirmed=True)

        assert len(captured) == 1
        intent_text = captured[0].payload["text"]
        assert intent_text.startswith(_CONFIRMATION_PROMPT_PREFIX)
        assert "my task" in intent_text

    @pytest.mark.asyncio
    async def test_none_dispatcher_returns_unavailable(self):
        ctx = _make_ctx(dispatcher=None)
        result = await _dispatch_cto("do task", "sess12345678", "web", ctx, False)
        assert result == "CTO dispatcher unavailable."

    @pytest.mark.asyncio
    async def test_action_event_skipped_continues_to_done(self):
        dispatcher = MagicMock()
        dispatcher.stream = make_stream(
            {"type": "action", "text": "working..."},
            {"type": "done", "text": "finished"},
        )
        ctx = _make_ctx(dispatcher=dispatcher)

        with patch.object(router, "build_context", new=AsyncMock(return_value=[])):
            result = await _dispatch_cto("do task", "sess12345678", "web", ctx, False)

        # action event was skipped; done event returned the text
        assert result == "finished"
