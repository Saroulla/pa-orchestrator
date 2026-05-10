"""R3 — dispatch persistence tests.

Verifies that when _route raises an exception:
- An error reply is persisted as the assistant message (conversation stays balanced).
- No exception propagates to the caller.
- Even if the assistant-message save itself fails, the function still returns.

R6 will extend this file with full behavioural coverage.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch, call

import aiosqlite
import pytest

from orchestrator import store
import orchestrator.maker.main as maker_main
from orchestrator.maker.main import MakerContext, bind, _reset


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stub_config():
    cfg = MagicMock()
    cfg.budgets.per_session_usd_per_day = 5.0
    cfg.models.summarize = "claude-haiku-4-5-20251001"
    return cfg


# ---------------------------------------------------------------------------
# Fixture: in-memory DB + bound MakerContext
# ---------------------------------------------------------------------------

@pytest.fixture()
async def mem_db():
    """Open an in-memory aiosqlite DB, init schema, yield it, then close."""
    async with aiosqlite.connect(":memory:") as db:
        await store.init_db(db)
        yield db


@pytest.fixture()
def ctx(mem_db):
    """Bind a MakerContext backed by the in-memory DB with mocked adapters."""
    maker_ctx = MakerContext(
        db=mem_db,
        dispatcher=MagicMock(),
        pa_groq=MagicMock(),
        pa_haiku=MagicMock(),
        spawner=MagicMock(),
    )
    bind(maker_ctx)
    yield maker_ctx
    _reset()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_persists_assistant_on_route_failure(mem_db, ctx):
    """When _route raises, dispatch persists error reply and returns normally."""
    session_id = "hardenerr____01"

    with (
        patch.object(maker_main, "get_config", return_value=_make_stub_config()),
        patch.object(maker_main, "count_tokens", return_value=5),
        patch.object(maker_main, "_route", side_effect=RuntimeError("simulated")),
        patch("orchestrator.maker.main.slide_and_summarise", new=AsyncMock()),
    ):
        result = await maker_main.dispatch(
            session_id=session_id,
            text="hello",
            channel="web",
            chat_id=None,
        )

    # Returns a dict with no exception raised
    assert isinstance(result, dict)
    assert "Sorry, hit an error: RuntimeError" in result["response"]

    # Both user and assistant rows exist in the DB
    cursor = await mem_db.execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id",
        (session_id,),
    )
    rows = await cursor.fetchall()
    roles = [r[0] for r in rows]
    contents = [r[1] for r in rows]

    assert "user" in roles
    assert "assistant" in roles

    user_idx = roles.index("user")
    assistant_idx = roles.index("assistant")
    assert contents[user_idx] == "hello"
    assert "Sorry, hit an error: RuntimeError" in contents[assistant_idx]


@pytest.mark.asyncio
async def test_dispatch_swallows_assistant_save_failure(mem_db, ctx):
    """Even when the assistant-message save raises, dispatch still returns a dict."""
    session_id = "hardenerr____02"

    # First call (user save) succeeds; second call (assistant save) raises.
    original_add_message = store.add_message
    call_count = [0]

    async def _patched_add_message(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] >= 2:
            raise RuntimeError("DB write failed")
        return await original_add_message(*args, **kwargs)

    with (
        patch.object(maker_main, "get_config", return_value=_make_stub_config()),
        patch.object(maker_main, "count_tokens", return_value=5),
        patch.object(maker_main, "_route", side_effect=RuntimeError("simulated")),
        patch("orchestrator.maker.main.slide_and_summarise", new=AsyncMock()),
        patch.object(store, "add_message", new=_patched_add_message),
    ):
        result = await maker_main.dispatch(
            session_id=session_id,
            text="hello",
            channel="web",
            chat_id=None,
        )

    # Must return a dict without raising
    assert isinstance(result, dict)
    assert "response" in result
