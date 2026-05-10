"""Unit tests for orchestrator/proxy/adapters/claude_api.py — Step 9a gate."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import anthropic
import httpx
import pytest

from orchestrator.models import (
    AdapterManifest,
    Caller,
    ErrorCode,
    Result,
)
from orchestrator.proxy.adapters.claude_api import (
    ClaudeAPIAdapter,
    DEFAULT_MODEL,
    HARD_MAX_OUTPUT_TOKENS,
    PRICING_PER_MTOK,
    _calc_cost,
)
from orchestrator.proxy.protocol import Tool


# ---------------------------------------------------------------------------
# Fixtures / mocks
# ---------------------------------------------------------------------------


def _usage(
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_creation: int | None = None,
    cache_read: int | None = None,
) -> MagicMock:
    u = MagicMock()
    u.input_tokens = input_tokens
    u.output_tokens = output_tokens
    u.cache_creation_input_tokens = cache_creation
    u.cache_read_input_tokens = cache_read
    return u


def _text_block(text: str) -> MagicMock:
    b = MagicMock()
    b.type = "text"
    b.text = text
    return b


def _message_response(
    text: str = "hello world",
    usage: MagicMock | None = None,
    stop_reason: str = "end_turn",
) -> MagicMock:
    m = MagicMock()
    m.content = [_text_block(text)]
    m.usage = usage if usage is not None else _usage()
    m.stop_reason = stop_reason
    return m


class FakeMessages:
    """Stub for ``client.messages``. Captures kwargs and returns a fixed message."""

    def __init__(self, response: Any | None = None, raises: Exception | None = None) -> None:
        self.response = response or _message_response()
        self.raises = raises
        self.create_calls: list[dict] = []
        self.stream_calls: list[dict] = []
        self.stream_events: list[Any] = []
        self.stream_final: Any = None

    async def create(self, **kwargs):
        self.create_calls.append(kwargs)
        if self.raises:
            raise self.raises
        return self.response

    def stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        return _FakeStreamManager(self.stream_events, self.stream_final, raises=self.raises)


class _FakeStreamManager:
    def __init__(self, events: list[Any], final: Any, raises: Exception | None = None) -> None:
        self._events = events
        self._final = final
        self._raises = raises

    async def __aenter__(self):
        if self._raises:
            raise self._raises
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def __aiter__(self):
        async def gen():
            for e in self._events:
                yield e
        return gen()

    async def get_final_message(self):
        return self._final


class FakeClient:
    def __init__(self, messages: FakeMessages) -> None:
        self.messages = messages


def _make_event(type_: str, text: str | None = None) -> MagicMock:
    e = MagicMock()
    e.type = type_
    if text is not None:
        delta = MagicMock()
        delta.text = text
        e.delta = delta
    return e


# ---------------------------------------------------------------------------
# Structural tests
# ---------------------------------------------------------------------------


def test_satisfies_tool_protocol():
    adapter = ClaudeAPIAdapter(client=FakeClient(FakeMessages()))
    assert isinstance(adapter, Tool)


def test_name_and_allowed_callers():
    adapter = ClaudeAPIAdapter(client=FakeClient(FakeMessages()))
    assert adapter.name == "claude_api"
    assert adapter.allowed_callers == {Caller.PA, Caller.JOB_RUNNER}


def test_manifest_shape():
    adapter = ClaudeAPIAdapter(client=FakeClient(FakeMessages()))
    m = adapter.manifest
    assert isinstance(m, AdapterManifest)
    required_names = {p.name for p in m.required}
    optional_names = {p.name for p in m.optional}
    assert {"messages", "max_tokens"} <= required_names
    assert {"system", "summary_anchor", "model", "operation", "session_id"} <= optional_names


# ---------------------------------------------------------------------------
# Cost calculation
# ---------------------------------------------------------------------------


def test_calc_cost_uses_published_rates():
    p = PRICING_PER_MTOK[DEFAULT_MODEL]
    cost = _calc_cost(DEFAULT_MODEL, tokens_in=1_000_000, tokens_out=1_000_000)
    assert cost == pytest.approx(p["input"] + p["output"])


def test_calc_cost_includes_cache_buckets():
    p = PRICING_PER_MTOK[DEFAULT_MODEL]
    cost = _calc_cost(
        DEFAULT_MODEL,
        tokens_in=0,
        tokens_out=0,
        cache_creation_tokens=1_000_000,
        cache_read_tokens=1_000_000,
    )
    assert cost == pytest.approx(p["cache_write"] + p["cache_read"])


def test_calc_cost_unknown_model_falls_back_to_default():
    a = _calc_cost("unknown-model-xyz", tokens_in=1000, tokens_out=1000)
    b = _calc_cost(DEFAULT_MODEL, tokens_in=1000, tokens_out=1000)
    assert a == b


# ---------------------------------------------------------------------------
# invoke — chat (non-streaming)
# ---------------------------------------------------------------------------


async def test_invoke_chat_happy_path_returns_text_and_meta():
    fake = FakeMessages(response=_message_response(
        text="hi there",
        usage=_usage(input_tokens=200, output_tokens=80),
    ))
    adapter = ClaudeAPIAdapter(client=FakeClient(fake))

    result = await adapter.invoke(
        payload={
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 100,
        },
        deadline_s=10.0,
        caller=Caller.PA,
    )

    assert isinstance(result, Result)
    assert result.ok is True
    assert result.data == "hi there"
    assert result.meta["tool"] == "claude_api"
    assert result.meta["tokens_in"] == 200
    assert result.meta["tokens_out"] == 80
    assert result.meta["model"] == DEFAULT_MODEL
    assert "latency_ms" in result.meta
    assert result.cost_usd > 0
    # Cost matches the formula
    expected = _calc_cost(DEFAULT_MODEL, 200, 80)
    assert result.cost_usd == pytest.approx(expected)


async def test_invoke_chat_attaches_cache_control_to_system_and_summary():
    fake = FakeMessages()
    adapter = ClaudeAPIAdapter(client=FakeClient(fake))

    await adapter.invoke(
        payload={
            "messages": [{"role": "user", "content": "x"}],
            "max_tokens": 50,
            "system": "You are PA.",
            "summary_anchor": "earlier convo summary",
        },
        deadline_s=5.0,
        caller=Caller.PA,
    )

    assert len(fake.create_calls) == 1
    call = fake.create_calls[0]
    system = call["system"]
    assert isinstance(system, list) and len(system) == 2
    for block in system:
        assert block["type"] == "text"
        assert block["cache_control"] == {"type": "ephemeral"}
    assert system[0]["text"] == "You are PA."
    assert system[1]["text"] == "earlier convo summary"


async def test_invoke_chat_omits_system_when_no_system_or_anchor():
    fake = FakeMessages()
    adapter = ClaudeAPIAdapter(client=FakeClient(fake))

    await adapter.invoke(
        payload={
            "messages": [{"role": "user", "content": "x"}],
            "max_tokens": 50,
        },
        deadline_s=5.0,
        caller=Caller.PA,
    )

    assert "system" not in fake.create_calls[0]


async def test_invoke_chat_clamps_max_tokens_to_hard_cap():
    fake = FakeMessages()
    adapter = ClaudeAPIAdapter(client=FakeClient(fake), max_output_tokens=1000)

    await adapter.invoke(
        payload={
            "messages": [{"role": "user", "content": "x"}],
            "max_tokens": 999_999,
        },
        deadline_s=5.0,
        caller=Caller.PA,
    )

    assert fake.create_calls[0]["max_tokens"] == 1000


async def test_invoke_chat_passes_temperature_and_model():
    fake = FakeMessages()
    adapter = ClaudeAPIAdapter(client=FakeClient(fake))

    await adapter.invoke(
        payload={
            "messages": [{"role": "user", "content": "x"}],
            "max_tokens": 50,
            "temperature": 0.2,
            "model": "claude-opus-4-7",
        },
        deadline_s=5.0,
        caller=Caller.PA,
    )

    call = fake.create_calls[0]
    assert call["temperature"] == 0.2
    assert call["model"] == "claude-opus-4-7"


async def test_invoke_chat_bad_input_missing_messages():
    adapter = ClaudeAPIAdapter(client=FakeClient(FakeMessages()))

    result = await adapter.invoke(
        payload={"max_tokens": 50},
        deadline_s=5.0,
        caller=Caller.PA,
    )

    assert result.ok is False
    assert result.error.code == ErrorCode.BAD_INPUT
    assert result.error.retriable is False


async def test_invoke_chat_bad_input_missing_max_tokens():
    adapter = ClaudeAPIAdapter(client=FakeClient(FakeMessages()))

    result = await adapter.invoke(
        payload={"messages": [{"role": "user", "content": "x"}]},
        deadline_s=5.0,
        caller=Caller.PA,
    )

    assert result.ok is False
    assert result.error.code == ErrorCode.BAD_INPUT


async def test_invoke_chat_unknown_operation_returns_bad_input():
    adapter = ClaudeAPIAdapter(client=FakeClient(FakeMessages()))

    result = await adapter.invoke(
        payload={"operation": "telepathy"},
        deadline_s=5.0,
        caller=Caller.PA,
    )

    assert result.ok is False
    assert result.error.code == ErrorCode.BAD_INPUT


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def _http_response_for_status(status: int) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    )


@pytest.mark.parametrize(
    "raise_exc, expected_code, expected_retriable",
    [
        (anthropic.RateLimitError(
            message="rl",
            response=_http_response_for_status(429),
            body=None,
        ), ErrorCode.RATE_LIMIT, True),
        (anthropic.AuthenticationError(
            message="auth",
            response=_http_response_for_status(401),
            body=None,
        ), ErrorCode.UNAUTHORIZED, False),
        (anthropic.BadRequestError(
            message="bad",
            response=_http_response_for_status(400),
            body=None,
        ), ErrorCode.BAD_INPUT, False),
    ],
)
async def test_invoke_maps_known_anthropic_errors(raise_exc, expected_code, expected_retriable):
    fake = FakeMessages(raises=raise_exc)
    adapter = ClaudeAPIAdapter(client=FakeClient(fake))

    result = await adapter.invoke(
        payload={
            "messages": [{"role": "user", "content": "x"}],
            "max_tokens": 50,
        },
        deadline_s=5.0,
        caller=Caller.PA,
    )

    assert result.ok is False
    assert result.error.code == expected_code
    assert result.error.retriable is expected_retriable


async def test_invoke_maps_generic_exception_to_tool_error():
    fake = FakeMessages(raises=RuntimeError("boom"))
    adapter = ClaudeAPIAdapter(client=FakeClient(fake))

    result = await adapter.invoke(
        payload={
            "messages": [{"role": "user", "content": "x"}],
            "max_tokens": 50,
        },
        deadline_s=5.0,
        caller=Caller.PA,
    )

    assert result.ok is False
    assert result.error.code == ErrorCode.TOOL_ERROR
    assert result.error.retriable is True


# ---------------------------------------------------------------------------
# invoke — complete
# ---------------------------------------------------------------------------


async def test_invoke_complete_wraps_prompt_into_user_message():
    fake = FakeMessages(response=_message_response(text="ok"))
    adapter = ClaudeAPIAdapter(client=FakeClient(fake))

    result = await adapter.invoke(
        payload={"operation": "complete", "prompt": "Tell me a joke", "max_tokens": 64},
        deadline_s=5.0,
        caller=Caller.JOB_RUNNER,
    )

    assert result.ok is True
    assert fake.create_calls[0]["messages"] == [{"role": "user", "content": "Tell me a joke"}]


async def test_invoke_complete_bad_input_missing_prompt():
    adapter = ClaudeAPIAdapter(client=FakeClient(FakeMessages()))

    result = await adapter.invoke(
        payload={"operation": "complete", "max_tokens": 50},
        deadline_s=5.0,
        caller=Caller.PA,
    )

    assert result.ok is False
    assert result.error.code == ErrorCode.BAD_INPUT


# ---------------------------------------------------------------------------
# Cost ledger writes
# ---------------------------------------------------------------------------


class FakeDB:
    def __init__(self) -> None:
        self.execute_calls: list[tuple[str, tuple]] = []
        self.commits = 0

    async def execute(self, sql: str, params: tuple):
        self.execute_calls.append((sql, params))
        return MagicMock()

    async def commit(self) -> None:
        self.commits += 1


async def test_invoke_writes_cost_ledger_when_session_id_and_db_present():
    fake = FakeMessages(response=_message_response(
        usage=_usage(input_tokens=300, output_tokens=120),
    ))
    db = FakeDB()
    adapter = ClaudeAPIAdapter(client=FakeClient(fake), db=db)

    result = await adapter.invoke(
        payload={
            "messages": [{"role": "user", "content": "x"}],
            "max_tokens": 50,
            "session_id": "abc12345",
        },
        deadline_s=5.0,
        caller=Caller.PA,
    )

    assert result.ok is True
    insert_sql = next(s for s, _ in db.execute_calls if s.startswith("INSERT INTO cost_ledger"))
    insert_params = next(p for s, p in db.execute_calls if s.startswith("INSERT INTO cost_ledger"))
    update_params = next(p for s, p in db.execute_calls if s.startswith("UPDATE sessions"))
    assert "cost_ledger" in insert_sql
    assert insert_params[0] == "abc12345"
    assert insert_params[3] == "claude_api"
    assert insert_params[4] == 300
    assert insert_params[5] == 120
    assert insert_params[6] == pytest.approx(result.cost_usd)
    assert update_params[1] == "abc12345"
    assert update_params[0] == pytest.approx(result.cost_usd)
    assert db.commits >= 1


async def test_invoke_skips_cost_ledger_when_no_session_id():
    fake = FakeMessages()
    db = FakeDB()
    adapter = ClaudeAPIAdapter(client=FakeClient(fake), db=db)

    await adapter.invoke(
        payload={
            "messages": [{"role": "user", "content": "x"}],
            "max_tokens": 50,
        },
        deadline_s=5.0,
        caller=Caller.PA,
    )

    assert db.execute_calls == []
    assert db.commits == 0


async def test_invoke_skips_cost_ledger_when_no_db():
    fake = FakeMessages()
    adapter = ClaudeAPIAdapter(client=FakeClient(fake), db=None)

    result = await adapter.invoke(
        payload={
            "messages": [{"role": "user", "content": "x"}],
            "max_tokens": 50,
            "session_id": "abc12345",
        },
        deadline_s=5.0,
        caller=Caller.PA,
    )

    assert result.ok is True


async def test_invoke_swallows_db_failure_and_still_returns_ok():
    fake = FakeMessages()
    db = FakeDB()

    async def boom(*args, **kwargs):
        raise RuntimeError("disk on fire")
    db.execute = boom  # type: ignore[assignment]

    adapter = ClaudeAPIAdapter(client=FakeClient(fake), db=db)

    result = await adapter.invoke(
        payload={
            "messages": [{"role": "user", "content": "x"}],
            "max_tokens": 50,
            "session_id": "abc12345",
        },
        deadline_s=5.0,
        caller=Caller.PA,
    )

    assert result.ok is True


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


async def test_stream_yields_tokens_then_done():
    final = _message_response(
        text="hello world",
        usage=_usage(input_tokens=10, output_tokens=2),
    )
    fake = FakeMessages()
    fake.stream_events = [
        _make_event("message_start"),
        _make_event("content_block_delta", text="hello "),
        _make_event("content_block_delta", text="world"),
        _make_event("message_stop"),
    ]
    fake.stream_final = final
    adapter = ClaudeAPIAdapter(client=FakeClient(fake))

    events: list[dict] = []
    async for ev in adapter.stream(
        payload={
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 64,
        },
        deadline_s=10.0,
        caller=Caller.PA,
    ):
        events.append(ev)

    token_events = [e for e in events if e["type"] == "token"]
    done_events = [e for e in events if e["type"] == "done"]
    assert [e["text"] for e in token_events] == ["hello ", "world"]
    assert len(done_events) == 1
    final_result = done_events[0]["result"]
    assert final_result["ok"] is True
    assert final_result["data"] == "hello world"
    assert final_result["meta"]["tokens_in"] == 10
    assert final_result["meta"]["tokens_out"] == 2


async def test_stream_emits_error_on_exception():
    fake = FakeMessages(raises=anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    ))
    adapter = ClaudeAPIAdapter(client=FakeClient(fake))

    events: list[dict] = []
    async for ev in adapter.stream(
        payload={
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 64,
        },
        deadline_s=10.0,
        caller=Caller.PA,
    ):
        events.append(ev)

    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert events[0]["error"]["code"] == ErrorCode.TOOL_ERROR
    assert events[0]["error"]["retriable"] is True


async def test_stream_validates_payload_before_calling_client():
    fake = FakeMessages()
    adapter = ClaudeAPIAdapter(client=FakeClient(fake))

    events: list[dict] = []
    async for ev in adapter.stream(
        payload={"max_tokens": 50},
        deadline_s=5.0,
        caller=Caller.PA,
    ):
        events.append(ev)

    assert len(fake.stream_calls) == 0
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert events[0]["error"]["code"] == ErrorCode.BAD_INPUT


async def test_stream_attaches_cache_control_to_system():
    fake = FakeMessages()
    fake.stream_events = []
    fake.stream_final = _message_response(text="", usage=_usage(0, 0))
    adapter = ClaudeAPIAdapter(client=FakeClient(fake))

    async for _ in adapter.stream(
        payload={
            "messages": [{"role": "user", "content": "x"}],
            "max_tokens": 50,
            "system": "be brief",
            "summary_anchor": "prior summary",
        },
        deadline_s=5.0,
        caller=Caller.PA,
    ):
        pass

    assert len(fake.stream_calls) == 1
    system = fake.stream_calls[0]["system"]
    assert len(system) == 2
    for block in system:
        assert block["cache_control"] == {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


async def test_health_true_when_api_key_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    adapter = ClaudeAPIAdapter(client=FakeClient(FakeMessages()))
    assert await adapter.health() is True


async def test_health_false_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    adapter = ClaudeAPIAdapter(client=FakeClient(FakeMessages()))
    assert await adapter.health() is False
