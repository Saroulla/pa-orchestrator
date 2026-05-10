"""Unit tests for orchestrator/proxy/adapters/pa_haiku.py — Step B2 gate."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import anthropic
import pytest

from orchestrator.models import Caller, ErrorCode
from orchestrator.proxy.adapters.pa_haiku import PAHaikuAdapter, HAIKU_MODEL
from orchestrator.proxy.adapters.claude_api import ClaudeAPIAdapter
from orchestrator.proxy.protocol import Tool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(text: str = "answer", tokens_in: int = 10, tokens_out: int = 5) -> MagicMock:
    usage = MagicMock()
    usage.input_tokens = tokens_in
    usage.output_tokens = tokens_out
    usage.cache_creation_input_tokens = None
    usage.cache_read_input_tokens = None

    block = MagicMock()
    block.type = "text"
    block.text = text

    resp = MagicMock()
    resp.content = [block]
    resp.usage = usage
    resp.stop_reason = "end_turn"
    return resp


def _make_adapter(response=None, exc=None) -> PAHaikuAdapter:
    client = MagicMock()
    if exc is not None:
        client.messages.create = AsyncMock(side_effect=exc)
    else:
        client.messages.create = AsyncMock(return_value=response or _mock_response())
    return PAHaikuAdapter(client=client)


# ---------------------------------------------------------------------------
# Protocol + inheritance
# ---------------------------------------------------------------------------

def test_satisfies_tool_protocol():
    assert isinstance(_make_adapter(), Tool)


def test_is_subclass_of_claude_api_adapter():
    assert issubclass(PAHaikuAdapter, ClaudeAPIAdapter)


def test_name():
    assert PAHaikuAdapter.name == "pa_haiku"


def test_allowed_callers():
    assert Caller.PA in PAHaikuAdapter.allowed_callers
    assert Caller.MAKER in PAHaikuAdapter.allowed_callers
    assert Caller.JOB_RUNNER in PAHaikuAdapter.allowed_callers
    assert Caller.CTO_SUBAGENT in PAHaikuAdapter.allowed_callers


def test_default_model_is_haiku():
    adapter = _make_adapter()
    assert adapter._default_model == HAIKU_MODEL


# ---------------------------------------------------------------------------
# invoke
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invoke_ok():
    adapter = _make_adapter(_mock_response("haiku reply", tokens_in=12, tokens_out=6))
    result = await adapter.invoke(
        {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    assert result.ok is True
    assert result.data == "haiku reply"
    assert result.meta["tool"] == "pa_haiku"


@pytest.mark.asyncio
async def test_model_pinned_to_haiku():
    adapter = _make_adapter()
    await adapter.invoke(
        {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    call_kwargs = adapter._client.messages.create.call_args[1]
    assert call_kwargs["model"] == HAIKU_MODEL


@pytest.mark.asyncio
async def test_model_override_respected():
    adapter = _make_adapter()
    await adapter.invoke(
        {
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 100,
            "model": "claude-sonnet-4-6",
        },
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    call_kwargs = adapter._client.messages.create.call_args[1]
    assert call_kwargs["model"] == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_rate_limit_retriable():
    exc = anthropic.RateLimitError("429", response=MagicMock(), body={})
    adapter = _make_adapter(exc=exc)
    result = await adapter.invoke(
        {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    assert result.ok is False
    assert result.error.code == ErrorCode.RATE_LIMIT
    assert result.error.retriable is True


# ---------------------------------------------------------------------------
# cost_ledger with tier="pa-haiku"
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cost_ledger_tier_pa_haiku():
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    client = MagicMock()
    client.messages.create = AsyncMock(return_value=_mock_response(tokens_in=10, tokens_out=5))
    adapter = PAHaikuAdapter(client=client, db=db)

    await adapter.invoke(
        {
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 100,
            "session_id": "test-sess-01",
        },
        deadline_s=10.0,
        caller=Caller.MAKER,
    )

    # First execute call is the cost_ledger INSERT
    first_call_sql, first_call_params = db.execute.call_args_list[0][0]
    assert "cost_ledger" in first_call_sql
    assert "pa-haiku" in first_call_params
