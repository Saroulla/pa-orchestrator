"""Unit tests for orchestrator/proxy/adapters/pa_groq.py — Step B1 gate."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import groq
import pytest

from orchestrator.models import Caller, ErrorCode
from orchestrator.proxy.adapters.pa_groq import PAGroqAdapter, DEFAULT_MODEL
from orchestrator.proxy.protocol import Tool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(content: str = "hello", tokens_in: int = 10, tokens_out: int = 5) -> MagicMock:
    usage = MagicMock()
    usage.prompt_tokens = tokens_in
    usage.completion_tokens = tokens_out

    message = MagicMock()
    message.content = content

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = "stop"

    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage
    return resp


def _make_adapter(response=None, exc=None) -> PAGroqAdapter:
    client = MagicMock()
    if exc is not None:
        client.chat.completions.create = AsyncMock(side_effect=exc)
    else:
        client.chat.completions.create = AsyncMock(return_value=response or _mock_response())
    return PAGroqAdapter(client=client)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

def test_satisfies_tool_protocol():
    assert isinstance(_make_adapter(), Tool)


def test_name():
    assert PAGroqAdapter.name == "pa_groq"


def test_allowed_callers():
    assert Caller.PA in PAGroqAdapter.allowed_callers
    assert Caller.MAKER in PAGroqAdapter.allowed_callers
    assert Caller.JOB_RUNNER in PAGroqAdapter.allowed_callers
    assert Caller.CTO_SUBAGENT in PAGroqAdapter.allowed_callers


# ---------------------------------------------------------------------------
# test_pa_groq_adapter_invoke — spec §11
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pa_groq_adapter_invoke_ok():
    adapter = _make_adapter(_mock_response("the answer", tokens_in=20, tokens_out=8))
    result = await adapter.invoke(
        {"messages": [{"role": "user", "content": "hi"}]},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    assert result.ok is True
    assert result.data == "the answer"
    assert result.meta["tokens_in"] == 20
    assert result.meta["tokens_out"] == 8
    assert result.meta["tool"] == "pa_groq"
    assert result.cost_usd == 0.0


@pytest.mark.asyncio
async def test_pa_groq_maps_429_to_rate_limit():
    exc = groq.RateLimitError("rate limited", response=MagicMock(), body={})
    adapter = _make_adapter(exc=exc)
    result = await adapter.invoke(
        {"messages": [{"role": "user", "content": "hi"}]},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    assert result.ok is False
    assert result.error.code == ErrorCode.RATE_LIMIT
    assert result.error.retriable is True


@pytest.mark.asyncio
async def test_pa_groq_timeout_retriable():
    adapter = _make_adapter(exc=asyncio.TimeoutError())
    result = await adapter.invoke(
        {"messages": [{"role": "user", "content": "hi"}]},
        deadline_s=0.001,
        caller=Caller.MAKER,
    )
    assert result.ok is False
    assert result.error.code == ErrorCode.TIMEOUT
    assert result.error.retriable is True


@pytest.mark.asyncio
async def test_pa_groq_bad_input_empty_messages():
    adapter = _make_adapter()
    result = await adapter.invoke(
        {"messages": []},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    assert result.ok is False
    assert result.error.code == ErrorCode.BAD_INPUT
    assert result.error.retriable is False


@pytest.mark.asyncio
async def test_pa_groq_bad_input_missing_messages():
    adapter = _make_adapter()
    result = await adapter.invoke({}, deadline_s=10.0, caller=Caller.PA)
    assert result.ok is False
    assert result.error.code == ErrorCode.BAD_INPUT


@pytest.mark.asyncio
async def test_pa_groq_system_prepended():
    adapter = _make_adapter()
    await adapter.invoke(
        {"messages": [{"role": "user", "content": "hi"}], "system": "be terse"},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    call_kwargs = adapter._client.chat.completions.create.call_args[1]
    msgs = call_kwargs["messages"]
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == "be terse"
    assert msgs[1]["role"] == "user"


@pytest.mark.asyncio
async def test_pa_groq_uses_default_model():
    adapter = _make_adapter()
    await adapter.invoke(
        {"messages": [{"role": "user", "content": "hi"}]},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    call_kwargs = adapter._client.chat.completions.create.call_args[1]
    assert call_kwargs["model"] == DEFAULT_MODEL


@pytest.mark.asyncio
async def test_pa_groq_respects_model_override():
    adapter = _make_adapter()
    await adapter.invoke(
        {"messages": [{"role": "user", "content": "hi"}], "model": "gemma2-9b-it"},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    call_kwargs = adapter._client.chat.completions.create.call_args[1]
    assert call_kwargs["model"] == "gemma2-9b-it"


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_true_when_key_set(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    adapter = _make_adapter()
    assert await adapter.health() is True


@pytest.mark.asyncio
async def test_health_false_when_no_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    adapter = _make_adapter()
    assert await adapter.health() is False


# ---------------------------------------------------------------------------
# cost_ledger write
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cost_ledger_row_written():
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_mock_response(tokens_in=15, tokens_out=7))
    adapter = PAGroqAdapter(client=client, db=db)

    await adapter.invoke(
        {"messages": [{"role": "user", "content": "hi"}], "session_id": "test-sess-01"},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )

    db.execute.assert_called_once()
    sql, params = db.execute.call_args[0]
    assert "cost_ledger" in sql
    assert "pa-groq" in params   # tier value
    assert params[4] == 15       # tokens_in
    assert params[5] == 7        # tokens_out
    assert params[6] == 0.0      # cost_usd
