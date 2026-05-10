"""Tests for orchestrator/telegram.py (Step 12)."""
from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import AsyncMock, patch

import pytest
from aiolimiter import AsyncLimiter
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import orchestrator.telegram as tg
from orchestrator.telegram import get_session_id, telegram_send

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALLOWED_USER_ID = 111222333
_OTHER_USER_ID   = 999888777
_CHAT_ID         = 456789

def _make_update(user_id: int, chat_id: int = _CHAT_ID, text: str = "hello") -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
            "chat": {"id": chat_id, "type": "private"},
            "date": 1620000000,
            "text": text,
        },
    }

def _make_app(handler: AsyncMock | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(tg.router)
    if handler is not None:
        app.state.chat_handler = handler
    return app

# ---------------------------------------------------------------------------
# Test 1 — allowlisted user dispatches to chat_handler and returns 200
# ---------------------------------------------------------------------------

async def test_valid_user_returns_200_and_calls_handler():
    handler = AsyncMock()
    app = _make_app(handler)

    payload = _make_update(_ALLOWED_USER_ID)
    env = {"TELEGRAM_ALLOWED_USER_IDS": str(_ALLOWED_USER_ID)}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        with patch.dict(os.environ, env, clear=False):
            response = await client.post("/webhook/telegram", json=payload)

    assert response.status_code == 200

    # Yield to event loop so the fire-and-forget task completes
    await asyncio.sleep(0.05)

    handler.assert_called_once()
    call_kwargs = handler.call_args.kwargs
    assert call_kwargs["session_id"] == get_session_id(_ALLOWED_USER_ID)
    assert call_kwargs["chat_id"] == _CHAT_ID
    assert call_kwargs["text"] == "hello"

# ---------------------------------------------------------------------------
# Test 2 — non-allowlisted user returns 200 silently, handler NOT called
# ---------------------------------------------------------------------------

async def test_non_allowlisted_user_returns_200_no_handler():
    handler = AsyncMock()
    app = _make_app(handler)

    payload = _make_update(_OTHER_USER_ID)
    env = {"TELEGRAM_ALLOWED_USER_IDS": str(_ALLOWED_USER_ID)}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        with patch.dict(os.environ, env, clear=False):
            response = await client.post("/webhook/telegram", json=payload)

    assert response.status_code == 200
    await asyncio.sleep(0.05)
    handler.assert_not_called()

# ---------------------------------------------------------------------------
# Test 3 — get_session_id is deterministic
# ---------------------------------------------------------------------------

def test_get_session_id_deterministic():
    sid_a = get_session_id(12345)
    sid_b = get_session_id(12345)
    assert sid_a == sid_b, "Same user_id must always produce same session_id"

def test_get_session_id_length_is_16():
    assert len(get_session_id(12345)) == 16

def test_get_session_id_different_users_differ():
    assert get_session_id(12345) != get_session_id(99999)

def test_get_session_id_is_hex():
    sid = get_session_id(42)
    assert all(c in "0123456789abcdef" for c in sid)

# ---------------------------------------------------------------------------
# Test 4 — telegram_send with text > 4000 chars sends as .md file
# ---------------------------------------------------------------------------

async def test_send_long_text_uses_send_document():
    mock_bot = AsyncMock()
    long_text = "A" * 4001

    # Use very permissive limiters so the test doesn't block
    fast = AsyncLimiter(10_000, 1)
    with patch.object(tg, "_global_limiter", fast), \
         patch.dict(tg._chat_limiters, {_CHAT_ID: fast}):
        await telegram_send(mock_bot, chat_id=_CHAT_ID, text=long_text)

    mock_bot.send_document.assert_called_once()
    mock_bot.send_message.assert_not_called()

    call_kwargs = mock_bot.send_document.call_args.kwargs
    assert call_kwargs["chat_id"] == _CHAT_ID
    assert call_kwargs.get("filename") == "response.md"


async def test_send_short_text_uses_send_message():
    mock_bot = AsyncMock()
    short_text = "Hello"

    fast = AsyncLimiter(10_000, 1)
    with patch.object(tg, "_global_limiter", fast), \
         patch.dict(tg._chat_limiters, {_CHAT_ID: fast}):
        await telegram_send(mock_bot, chat_id=_CHAT_ID, text=short_text)

    mock_bot.send_message.assert_called_once()
    mock_bot.send_document.assert_not_called()


async def test_send_exactly_4000_chars_uses_send_message():
    mock_bot = AsyncMock()
    text = "B" * 4000

    fast = AsyncLimiter(10_000, 1)
    with patch.object(tg, "_global_limiter", fast), \
         patch.dict(tg._chat_limiters, {_CHAT_ID: fast}):
        await telegram_send(mock_bot, chat_id=_CHAT_ID, text=text)

    mock_bot.send_message.assert_called_once()
    mock_bot.send_document.assert_not_called()

# ---------------------------------------------------------------------------
# Test 5 — rate limiting: 31 rapid calls are all completed but delayed > 1s
# ---------------------------------------------------------------------------

async def test_rate_limiting_delays_31_calls():
    mock_bot = AsyncMock()

    # Patch global limiter: 30 burst capacity, 31s fill period.
    # First 30 tasks proceed instantly; 31st waits ~1.033s → elapsed > 1s.
    slow_global = AsyncLimiter(30, 31)

    # Clear per-chat state so each chat_id gets a fresh limiter (no prior usage)
    saved = dict(tg._chat_limiters)
    tg._chat_limiters.clear()

    try:
        with patch.object(tg, "_global_limiter", slow_global):
            start = time.monotonic()
            # Use distinct chat_ids so per-chat (1/sec) limiter is never the bottleneck
            tasks = [
                telegram_send(mock_bot, chat_id=i, text=f"msg {i}")
                for i in range(31)
            ]
            await asyncio.gather(*tasks)
            elapsed = time.monotonic() - start
    finally:
        tg._chat_limiters.clear()
        tg._chat_limiters.update(saved)

    assert elapsed > 1.0, (
        f"Rate limiter should delay 31 calls past 1s, got {elapsed:.3f}s"
    )
    assert mock_bot.send_message.call_count == 31
