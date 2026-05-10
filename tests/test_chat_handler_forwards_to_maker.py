"""F1 — chat_handler thin-forwarder integration tests.

Proves that orchestrator.main._make_chat_handler returns a coroutine that
forwards (session_id, text, channel, chat_id) verbatim to
orchestrator.maker.main.dispatch and returns its result unchanged.
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI

import orchestrator.main as orch_main
from orchestrator.main import _make_chat_handler


# ---------------------------------------------------------------------------
# Forwarding contract
# ---------------------------------------------------------------------------


async def test_chat_handler_forwards_to_maker_dispatch():
    """All /v1/chat traffic flows through maker.main.dispatch."""
    app = FastAPI()
    handler = _make_chat_handler(app)

    expected = {
        "response": "[MAKER]> hello back",
        "mode": "MAKER",
        "cost_usd": 0.0,
        "latency_ms": 12,
    }

    mock_dispatch = AsyncMock(return_value=expected)
    with patch.object(orch_main.maker_main, "dispatch", new=mock_dispatch):
        result = await handler(
            session_id="testsession01",
            text="hello",
            channel="web",
            chat_id=None,
        )

    mock_dispatch.assert_awaited_once_with(
        session_id="testsession01",
        text="hello",
        channel="web",
        chat_id=None,
    )
    assert result == expected


async def test_chat_handler_forwards_telegram_chat_id():
    """chat_id is forwarded for telegram-origin messages."""
    app = FastAPI()
    handler = _make_chat_handler(app)

    expected = {"response": "[MAKER]> ok", "mode": "MAKER", "cost_usd": 0.0, "latency_ms": 1}
    mock_dispatch = AsyncMock(return_value=expected)
    with patch.object(orch_main.maker_main, "dispatch", new=mock_dispatch):
        result = await handler(
            session_id="tgsession_____01",
            text="ping",
            channel="telegram",
            chat_id=456789,
        )

    mock_dispatch.assert_awaited_once_with(
        session_id="tgsession_____01",
        text="ping",
        channel="telegram",
        chat_id=456789,
    )
    assert result == expected


# ---------------------------------------------------------------------------
# Structure assertions — chat_handler is genuinely thin
# ---------------------------------------------------------------------------


def test_chat_handler_is_async_coroutine():
    handler = _make_chat_handler(FastAPI())
    assert inspect.iscoroutinefunction(handler)


def test_chat_handler_signature_matches_telegram_caller():
    """Telegram router calls chat_handler with session_id/text/channel/chat_id kwargs."""
    handler = _make_chat_handler(FastAPI())
    sig = inspect.signature(handler)
    params = sig.parameters
    assert set(params) == {"session_id", "text", "channel", "chat_id"}
    assert params["channel"].default == "web"
    assert params["chat_id"].default is None


def test_chat_handler_does_not_call_parser_or_fsm():
    """F1 requirement: chat_handler no longer invokes parser.parse or fsm.transition."""
    src = inspect.getsource(_make_chat_handler)
    assert "parse(" not in src, "parser.parse must not be called in F1 chat_handler"
    assert "transition(" not in src, "fsm.transition must not be called in F1 chat_handler"
    assert "@Desktop" not in src, "@Desktop branch must be dropped in F1"
