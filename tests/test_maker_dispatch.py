"""R6 — tests/test_maker_dispatch.py (behavioural coverage).

Covers every major branch in maker.main.dispatch:
  @cost shortcut, escalation interception (resolved-b / resolved-a-with-deferred),
  budget gate, @-prefix direct routing, classifier paths (ADMIN_SYNC / ASYNC_JOB /
  INLINE_LLM), message persistence, bind() guard, and mode field.

R3's persistence-on-failure tests remain in test_maker_dispatch_persistence.py.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from orchestrator import escalation, store
import orchestrator.maker.main as maker_main
from orchestrator.maker import classifier as maker_classifier
from orchestrator.maker import inline as maker_inline
from orchestrator.maker import router as maker_router
from orchestrator.maker.admin import ADMIN_HANDLERS
from orchestrator.maker.classifier import Classification
from orchestrator.maker.main import MakerContext, _reset, bind


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stub_config():
    cfg = MagicMock()
    cfg.budgets.per_session_usd_per_day = 5.0
    cfg.models.summarize = "claude-haiku-4-5-20251001"
    return cfg


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
async def mem_db():
    async with aiosqlite.connect(":memory:") as db:
        await store.init_db(db)
        yield db


@pytest.fixture()
def ctx(mem_db):
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
# Common patch helpers — applied per-test as needed
# ---------------------------------------------------------------------------

_COMMON_PATCHES = dict(
    get_config=lambda: patch.object(maker_main, "get_config", return_value=_stub_config()),
    count_tokens=lambda: patch.object(maker_main, "count_tokens", return_value=1),
    summarise=lambda: patch("orchestrator.maker.main.slide_and_summarise", new=AsyncMock()),
)


# ---------------------------------------------------------------------------
# @cost shortcut
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_cost_shortcut(mem_db, ctx):
    """@cost returns session cost without calling the classifier."""
    mock_classify = AsyncMock()

    with (
        _COMMON_PATCHES["get_config"](),
        patch.object(maker_classifier, "classify", new=mock_classify),
    ):
        result = await maker_main.dispatch(
            session_id="costtest_1234",
            text="@cost",
            channel="web",
            chat_id=None,
        )

    assert "cost" in result["response"].lower()
    mock_classify.assert_not_awaited()


# ---------------------------------------------------------------------------
# Escalation — resolved with "b" (no deferred)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_escalation_resolved_b(mem_db, ctx):
    """Sending 'b' when an escalation is pending resolves it without calling classifier."""
    session_id = "escaltest_b01"
    await store.get_or_create_session(mem_db, session_id, "web")
    await escalation.create(
        mem_db, session_id, "web",
        options={"a": "proceed", "b": "cancel"},
        context=json.dumps({"prompt": "Confirm?"}),
    )

    mock_classify = AsyncMock()

    with (
        _COMMON_PATCHES["get_config"](),
        patch.object(maker_classifier, "classify", new=mock_classify),
    ):
        result = await maker_main.dispatch(
            session_id=session_id,
            text="b",
            channel="web",
            chat_id=None,
        )

    assert "proceeding with option (b)" in result["response"]
    mock_classify.assert_not_awaited()


# ---------------------------------------------------------------------------
# Escalation — resolved with "a" + deferred intent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_escalation_resolved_a_with_deferred(mem_db, ctx):
    """Sending 'a' with a deferred CTO intent replays it via direct_dispatch."""
    session_id = "escaltest_a01"
    await store.get_or_create_session(mem_db, session_id, "web")
    await escalation.create(
        mem_db, session_id, "web",
        options={"a": "proceed", "b": "cancel"},
        context=json.dumps({
            "prompt": "Confirm write file X?",
            "deferred_intent": {"kind": "code", "text": "write file X"},
        }),
    )

    mock_direct = AsyncMock(return_value="done")

    with (
        _COMMON_PATCHES["get_config"](),
        _COMMON_PATCHES["count_tokens"](),
        _COMMON_PATCHES["summarise"](),
        patch.object(maker_router, "direct_dispatch", new=mock_direct),
    ):
        await maker_main.dispatch(
            session_id=session_id,
            text="a",
            channel="web",
            chat_id=None,
        )

    mock_direct.assert_awaited_once()
    kwargs = mock_direct.call_args.kwargs
    assert kwargs["tier"] == "cto"
    assert kwargs["text"] == "write file X"
    assert kwargs["confirmed"] is True


# ---------------------------------------------------------------------------
# Budget breach
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_budget_breach(mem_db, ctx):
    """Session over budget returns Daily budget message without calling classifier."""
    session_id = "budgettest_01"
    await store.get_or_create_session(mem_db, session_id, "web")
    await mem_db.execute(
        "UPDATE sessions SET cost_to_date_usd = 999.0 WHERE id = ?", (session_id,)
    )
    await mem_db.commit()

    mock_classify = AsyncMock()

    with (
        _COMMON_PATCHES["get_config"](),
        patch.object(maker_classifier, "classify", new=mock_classify),
    ):
        result = await maker_main.dispatch(
            session_id=session_id,
            text="do something",
            channel="web",
            chat_id=None,
        )

    assert "Daily budget" in result["response"]
    mock_classify.assert_not_awaited()


# ---------------------------------------------------------------------------
# @-prefix routing — pa-groq
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_at_prefix_pa_groq(mem_db, ctx):
    """@pa-groq <text> is forwarded to direct_dispatch(tier='pa-groq', text=<text>)."""
    mock_direct = AsyncMock(return_value="groq reply")

    with (
        _COMMON_PATCHES["get_config"](),
        _COMMON_PATCHES["count_tokens"](),
        _COMMON_PATCHES["summarise"](),
        patch.object(maker_router, "direct_dispatch", new=mock_direct),
    ):
        result = await maker_main.dispatch(
            session_id="prefixtest_001",
            text="@pa-groq hi",
            channel="web",
            chat_id=None,
        )

    mock_direct.assert_awaited_once()
    kwargs = mock_direct.call_args.kwargs
    assert kwargs["tier"] == "pa-groq"
    assert kwargs["text"] == "hi"


# ---------------------------------------------------------------------------
# Classifier — ADMIN_SYNC
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_admin_sync(mem_db, ctx):
    """ADMIN_SYNC bucket routes to the registered handler and returns its output."""
    mock_handler = AsyncMock(return_value="status ok")
    mock_classify = AsyncMock(
        return_value=Classification(bucket="ADMIN_SYNC", params={"command": "status"})
    )

    with (
        _COMMON_PATCHES["get_config"](),
        _COMMON_PATCHES["count_tokens"](),
        _COMMON_PATCHES["summarise"](),
        patch.object(maker_classifier, "classify", new=mock_classify),
        patch.dict(ADMIN_HANDLERS, {"status": mock_handler}),
    ):
        result = await maker_main.dispatch(
            session_id="adminsync_001",
            text="status",
            channel="web",
            chat_id=None,
        )

    mock_handler.assert_awaited_once()
    assert "status ok" in result["response"]


# ---------------------------------------------------------------------------
# Classifier — ASYNC_JOB
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_async_job(mem_db, ctx):
    """ASYNC_JOB bucket creates a job and returns the job id in the response."""
    mock_classify = AsyncMock(
        return_value=Classification(
            bucket="ASYNC_JOB",
            skill="research-and-summarise",
            params={"topic": "x"},
            deadline=None,
        )
    )
    mock_create_job = AsyncMock(return_value="job-123")

    with (
        _COMMON_PATCHES["get_config"](),
        _COMMON_PATCHES["count_tokens"](),
        _COMMON_PATCHES["summarise"](),
        patch.object(maker_classifier, "classify", new=mock_classify),
        patch.object(maker_main.job_creator, "create_job", new=mock_create_job),
    ):
        result = await maker_main.dispatch(
            session_id="asyncjob__001",
            text="research topic x",
            channel="web",
            chat_id=None,
        )

    assert "job-123" in result["response"]


# ---------------------------------------------------------------------------
# Classifier — INLINE_LLM
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_inline_llm(mem_db, ctx):
    """INLINE_LLM bucket calls maker_inline.handle."""
    mock_classify = AsyncMock(
        return_value=Classification(bucket="INLINE_LLM")
    )
    mock_handle = AsyncMock(return_value="llm reply")

    with (
        _COMMON_PATCHES["get_config"](),
        _COMMON_PATCHES["count_tokens"](),
        _COMMON_PATCHES["summarise"](),
        patch.object(maker_classifier, "classify", new=mock_classify),
        patch.object(maker_inline, "handle", new=mock_handle),
    ):
        await maker_main.dispatch(
            session_id="inlinellm_001",
            text="hello",
            channel="web",
            chat_id=None,
        )

    mock_handle.assert_awaited_once()


# ---------------------------------------------------------------------------
# Persistence — both messages written on happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_persists_both_messages(mem_db, ctx):
    """Happy-path INLINE_LLM: messages table contains one user and one assistant row."""
    session_id = "persist__both1"
    mock_classify = AsyncMock(
        return_value=Classification(bucket="INLINE_LLM")
    )
    mock_handle = AsyncMock(return_value="the answer")

    with (
        _COMMON_PATCHES["get_config"](),
        _COMMON_PATCHES["count_tokens"](),
        _COMMON_PATCHES["summarise"](),
        patch.object(maker_classifier, "classify", new=mock_classify),
        patch.object(maker_inline, "handle", new=mock_handle),
    ):
        await maker_main.dispatch(
            session_id=session_id,
            text="my question",
            channel="web",
            chat_id=None,
        )

    cursor = await mem_db.execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id",
        (session_id,),
    )
    rows = await cursor.fetchall()
    roles = [r[0] for r in rows]
    contents = [r[1] for r in rows]

    assert "user" in roles
    assert "assistant" in roles
    assert contents[roles.index("user")] == "my question"
    assert "the answer" in contents[roles.index("assistant")]


# ---------------------------------------------------------------------------
# bind() guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_requires_bind():
    """Calling dispatch without bind() raises RuntimeError mentioning bind()."""
    _reset()
    with pytest.raises(RuntimeError, match="bind"):
        await maker_main.dispatch(
            session_id="nobind___001",
            text="hello",
            channel="web",
            chat_id=None,
        )


# ---------------------------------------------------------------------------
# mode field
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_returns_mode_maker(mem_db, ctx):
    """Every successful dispatch response has mode == 'MAKER'."""
    mock_classify = AsyncMock(return_value=Classification(bucket="INLINE_LLM"))
    mock_handle = AsyncMock(return_value="answer")

    with (
        _COMMON_PATCHES["get_config"](),
        _COMMON_PATCHES["count_tokens"](),
        _COMMON_PATCHES["summarise"](),
        patch.object(maker_classifier, "classify", new=mock_classify),
        patch.object(maker_inline, "handle", new=mock_handle),
    ):
        result = await maker_main.dispatch(
            session_id="modecheck_001",
            text="hello",
            channel="web",
            chat_id=None,
        )

    assert result["mode"] == "MAKER"
