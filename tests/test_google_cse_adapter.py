"""Unit tests for GoogleCSEAdapter — Phase 2 B3 gate.

All httpx network calls are mocked; no real API calls are made.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from orchestrator.models import Caller, ErrorCode
from orchestrator.proxy.adapters.google_cse import GoogleCSEAdapter
from orchestrator.proxy.protocol import Tool

# ---------------------------------------------------------------------------
# CSE item fixture
# ---------------------------------------------------------------------------

_CSE_ITEMS = [
    {"title": "Title 1", "link": "https://example.com/1", "snippet": "Snippet 1"},
    {"title": "Title 2", "link": "https://example.com/2", "snippet": "Snippet 2"},
]
_CSE_RESPONSE_BODY = {"items": _CSE_ITEMS}


def _mock_response(status: int = 200, json_body: dict | None = None) -> MagicMock:
    """Build a fake httpx response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = json_body if json_body is not None else {}
    return resp


def _make_adapter(
    *,
    status: int = 200,
    json_body: dict | None = None,
    exc: Exception | None = None,
) -> tuple[GoogleCSEAdapter, MagicMock]:
    """Return (adapter, mock_client) where mock_client.get is inspectable."""
    mock_client = MagicMock(spec=httpx.AsyncClient)
    if exc is not None:
        mock_client.get = AsyncMock(side_effect=exc)
    else:
        mock_client.get = AsyncMock(
            return_value=_mock_response(status, json_body if json_body is not None else _CSE_RESPONSE_BODY)
        )
    adapter = GoogleCSEAdapter(client=mock_client)
    return adapter, mock_client


# ---------------------------------------------------------------------------
# 1. Protocol conformance
# ---------------------------------------------------------------------------

def test_satisfies_tool_protocol():
    adapter, _ = _make_adapter()
    assert isinstance(adapter, Tool)


# ---------------------------------------------------------------------------
# 2. Successful 200 response
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invoke_ok():
    adapter, _ = _make_adapter(json_body=_CSE_RESPONSE_BODY)
    result = await adapter.invoke(
        {"q": "python asyncio"},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    assert result.ok is True
    assert result.error is None
    assert isinstance(result.data, list)
    assert len(result.data) == 2
    assert result.data[0] == {
        "title": "Title 1",
        "link": "https://example.com/1",
        "snippet": "Snippet 1",
    }
    assert result.data[1]["title"] == "Title 2"
    assert result.cost_usd == 0.0


# ---------------------------------------------------------------------------
# 3. HTTP 429 → RATE_LIMIT, retriable=True
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invoke_429_rate_limit():
    adapter, _ = _make_adapter(status=429, json_body={})
    result = await adapter.invoke(
        {"q": "rate limited query"},
        deadline_s=10.0,
        caller=Caller.JOB_RUNNER,
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == ErrorCode.RATE_LIMIT
    assert result.error.retriable is True


# ---------------------------------------------------------------------------
# 4. HTTP 403 → UNAUTHORIZED, retriable=False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invoke_403_unauthorized():
    adapter, _ = _make_adapter(status=403, json_body={})
    result = await adapter.invoke(
        {"q": "forbidden query"},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == ErrorCode.UNAUTHORIZED
    assert result.error.retriable is False


# ---------------------------------------------------------------------------
# 5. Missing 'q' → BAD_INPUT
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invoke_missing_q():
    adapter, mock_client = _make_adapter()
    result = await adapter.invoke(
        {"n": 5},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == ErrorCode.BAD_INPUT
    assert result.error.retriable is False
    # No HTTP call should have been made
    mock_client.get.assert_not_called()


# ---------------------------------------------------------------------------
# 6. Default params (n=10, safe="off") when not supplied
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_default_params(monkeypatch):
    monkeypatch.setenv("GOOGLE_CSE_API_KEY", "test_key")
    monkeypatch.setenv("GOOGLE_CSE_CX", "test_cx")
    adapter, mock_client = _make_adapter()

    await adapter.invoke(
        {"q": "default params test"},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )

    mock_client.get.assert_called_once()
    call_kwargs = mock_client.get.call_args
    params = call_kwargs.kwargs.get("params") or call_kwargs.args[1] if len(call_kwargs.args) > 1 else call_kwargs.kwargs["params"]
    assert params["num"] == 10
    assert params["safe"] == "off"


# ---------------------------------------------------------------------------
# 7. site param → siteSearch in request params
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_site_param_passed(monkeypatch):
    monkeypatch.setenv("GOOGLE_CSE_API_KEY", "test_key")
    monkeypatch.setenv("GOOGLE_CSE_CX", "test_cx")
    adapter, mock_client = _make_adapter()

    await adapter.invoke(
        {"q": "site search test", "site": "example.com"},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )

    mock_client.get.assert_called_once()
    call_kwargs = mock_client.get.call_args
    params = call_kwargs.kwargs["params"]
    assert params.get("siteSearch") == "example.com"


# ---------------------------------------------------------------------------
# 8. health() → True when both env vars set
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_true(monkeypatch):
    monkeypatch.setenv("GOOGLE_CSE_API_KEY", "some_key")
    monkeypatch.setenv("GOOGLE_CSE_CX", "some_cx")
    adapter = GoogleCSEAdapter()
    assert await adapter.health() is True


# ---------------------------------------------------------------------------
# 9. health() → False when env vars missing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_false(monkeypatch):
    monkeypatch.delenv("GOOGLE_CSE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CSE_CX", raising=False)
    adapter = GoogleCSEAdapter()
    assert await adapter.health() is False


@pytest.mark.asyncio
async def test_health_false_only_api_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_CSE_API_KEY", "some_key")
    monkeypatch.delenv("GOOGLE_CSE_CX", raising=False)
    adapter = GoogleCSEAdapter()
    assert await adapter.health() is False


@pytest.mark.asyncio
async def test_health_false_only_cx(monkeypatch):
    monkeypatch.delenv("GOOGLE_CSE_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_CSE_CX", "some_cx")
    adapter = GoogleCSEAdapter()
    assert await adapter.health() is False
