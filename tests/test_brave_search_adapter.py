"""Unit tests for BraveSearchAdapter — Step 9c gate.

All httpx network calls are mocked; no real API calls are made.
"""
import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from orchestrator.models import Caller, ErrorCode
from orchestrator.proxy.adapters.brave_search import BraveSearchAdapter

ADAPTER = BraveSearchAdapter()
_MODULE = "orchestrator.proxy.adapters.brave_search.httpx.AsyncClient"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BRAVE_RESULTS = [
    {"title": "Result 1", "url": "https://example.com/1", "description": "Desc 1"},
    {"title": "Result 2", "url": "https://example.com/2", "description": "Desc 2"},
]
_BRAVE_JSON = {"web": {"results": _BRAVE_RESULTS}}


def _make_mock(*, status: int = 200, json_body: dict | None = None, net_exc: Exception | None = None):
    """Build a patched AsyncClient that returns the requested response.

    Returns (mock_instance, mock_client).
    - mock_instance  is what httpx.AsyncClient() returns (the context manager).
    - mock_client    is what `async with ... as client` yields; inspect its .get calls.
    """
    mock_client = AsyncMock()

    if net_exc is not None:
        # Raise before a response is received (timeout, DNS failure, etc.)
        mock_client.get.side_effect = net_exc
    else:
        mock_response = MagicMock()
        mock_response.status_code = status
        mock_response.json.return_value = json_body or {}
        if status >= 400:
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                f"HTTP {status}", request=MagicMock(), response=mock_response
            )
        else:
            mock_response.raise_for_status.return_value = None
        mock_client.get.return_value = mock_response

    mock_instance = AsyncMock()
    mock_instance.__aenter__.return_value = mock_client
    return mock_instance, mock_client


# ---------------------------------------------------------------------------
# Test 1 — Successful search returns structured results
# ---------------------------------------------------------------------------

async def test_successful_search_returns_structured_results():
    mock_instance, _ = _make_mock(json_body=_BRAVE_JSON)
    with patch(_MODULE, return_value=mock_instance):
        result = await ADAPTER.invoke(
            {"query": "climate change", "count": 5},
            deadline_s=10.0,
            caller=Caller.PA,
        )

    assert result.ok is True
    assert result.error is None
    assert result.cost_usd == 0.0
    assert len(result.data["results"]) == 2
    assert result.data["results"][0] == {
        "title": "Result 1",
        "url": "https://example.com/1",
        "description": "Desc 1",
    }
    assert result.data["results"][1]["title"] == "Result 2"


# ---------------------------------------------------------------------------
# Test 2 — Network timeout → fail_silent (ok=True, results=[])
# ---------------------------------------------------------------------------

async def test_network_timeout_is_fail_silent():
    mock_instance, _ = _make_mock(net_exc=httpx.TimeoutException("timed out"))
    with patch(_MODULE, return_value=mock_instance):
        result = await ADAPTER.invoke(
            {"query": "test"},
            deadline_s=1.0,
            caller=Caller.PA,
        )

    assert result.ok is True, "fail_silent: ok must be True even on network timeout"
    assert result.data == {"results": []}
    assert result.error is not None
    assert result.error.code == ErrorCode.TOOL_ERROR
    assert result.error.retriable is True


# ---------------------------------------------------------------------------
# Test 3 — 429 rate limit → fail_silent
# ---------------------------------------------------------------------------

async def test_rate_limit_429_is_fail_silent():
    mock_instance, _ = _make_mock(status=429)
    with patch(_MODULE, return_value=mock_instance):
        result = await ADAPTER.invoke(
            {"query": "test"},
            deadline_s=10.0,
            caller=Caller.JOB_RUNNER,
        )

    assert result.ok is True, "fail_silent: ok must be True even on 429"
    assert result.data == {"results": []}
    assert result.error is not None
    assert result.error.code == ErrorCode.TOOL_ERROR


# ---------------------------------------------------------------------------
# Test 4 — 500 server error → fail_silent
# ---------------------------------------------------------------------------

async def test_server_error_500_is_fail_silent():
    mock_instance, _ = _make_mock(status=500)
    with patch(_MODULE, return_value=mock_instance):
        result = await ADAPTER.invoke(
            {"query": "test"},
            deadline_s=10.0,
            caller=Caller.JOB_RUNNER,
        )

    assert result.ok is True, "fail_silent: ok must be True even on 500"
    assert result.data == {"results": []}
    assert result.error is not None
    assert result.error.code == ErrorCode.TOOL_ERROR


# ---------------------------------------------------------------------------
# Test 5 — Caller not in allowed_callers → UNAUTHORIZED (ok=False)
# ---------------------------------------------------------------------------

async def test_unauthorized_caller_returns_unauthorized():
    # BraveSearch allows PA and JOB_RUNNER by default.
    # Override allowed_callers on this instance to make PA unauthorized.
    adapter = BraveSearchAdapter()
    adapter.allowed_callers = {Caller.JOB_RUNNER}

    result = await adapter.invoke(
        {"query": "test"},
        deadline_s=10.0,
        caller=Caller.PA,
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == ErrorCode.UNAUTHORIZED
    assert result.error.retriable is False


# ---------------------------------------------------------------------------
# Test 6 — count param is forwarded to the API call
# ---------------------------------------------------------------------------

async def test_count_param_forwarded_to_api():
    mock_instance, mock_client = _make_mock(json_body={"web": {"results": []}})
    with patch(_MODULE, return_value=mock_instance):
        await ADAPTER.invoke(
            {"query": "python async", "count": 15},
            deadline_s=10.0,
            caller=Caller.PA,
        )

    mock_client.get.assert_called_once()
    params = mock_client.get.call_args.kwargs["params"]
    assert params["q"] == "python async"
    assert params["count"] == 15
