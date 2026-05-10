"""Unit tests for orchestrator/proxy/adapters/http_fetch.py — Step B4 gate."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from orchestrator.models import Caller, ErrorCode
from orchestrator.proxy.adapters.http_fetch import HttpFetchAdapter
from orchestrator.proxy.protocol import Tool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(
    status: int = 200,
    content_type: str = "text/html; charset=utf-8",
    text: str = "<html>hello</html>",
    headers: dict | None = None,
) -> MagicMock:
    h = {"content-type": content_type}
    if headers:
        h.update(headers)
    resp = MagicMock()
    resp.status_code = status
    resp.headers = h
    resp.url = httpx.URL("https://example.com/page")
    resp.text = text
    resp.raise_for_status = MagicMock()
    return resp


def _make_adapter(response=None, exc=None) -> HttpFetchAdapter:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.is_closed = False
    if exc is not None:
        client.head = AsyncMock(side_effect=exc)
        client.get = AsyncMock(side_effect=exc)
    else:
        r = response or _mock_response()
        client.head = AsyncMock(return_value=r)
        client.get = AsyncMock(return_value=r)
    return HttpFetchAdapter(client=client)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

def test_satisfies_tool_protocol():
    assert isinstance(HttpFetchAdapter(), Tool)


def test_name():
    assert HttpFetchAdapter.name == "http_fetch"


def test_allowed_callers():
    assert Caller.MAKER in HttpFetchAdapter.allowed_callers
    assert Caller.JOB_RUNNER in HttpFetchAdapter.allowed_callers
    assert Caller.CTO_SUBAGENT in HttpFetchAdapter.allowed_callers
    assert Caller.PA not in HttpFetchAdapter.allowed_callers


# ---------------------------------------------------------------------------
# head operation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_head_ok():
    adapter = _make_adapter(_mock_response(status=200, content_type="text/html"))
    result = await adapter.invoke(
        {"operation": "head", "url": "https://example.com/"},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    assert result.ok is True
    assert result.data["status"] == 200
    assert result.data["content_type"] == "text/html"
    assert result.meta["tool"] == "http_fetch"
    assert result.meta["tokens_in"] == 0
    assert result.meta["tokens_out"] == 0


@pytest.mark.asyncio
async def test_head_pdf_content_type():
    """§11 gate: HEAD returns content-type=application/pdf — caller can branch to PDF download."""
    adapter = _make_adapter(_mock_response(status=200, content_type="application/pdf"))
    result = await adapter.invoke(
        {"operation": "head", "url": "https://example.com/doc.pdf"},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    assert result.ok is True
    assert "application/pdf" in result.data["content_type"]


# ---------------------------------------------------------------------------
# get operation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_ok():
    adapter = _make_adapter(_mock_response(text="<p>article body</p>"))
    result = await adapter.invoke(
        {"operation": "get", "url": "https://example.com/article"},
        deadline_s=10.0,
        caller=Caller.JOB_RUNNER,
    )
    assert result.ok is True
    assert result.data["body"] == "<p>article body</p>"
    assert result.data["status"] == 200


@pytest.mark.asyncio
async def test_get_http_error_maps_to_tool_error():
    response = _mock_response(status=404)
    response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("404", request=MagicMock(), response=response)
    )
    adapter = _make_adapter(response=response)
    result = await adapter.invoke(
        {"operation": "get", "url": "https://example.com/missing"},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    assert result.ok is False
    assert result.error.code == ErrorCode.TOOL_ERROR
    assert result.error.retriable is False


@pytest.mark.asyncio
async def test_get_429_maps_to_rate_limit():
    response = _mock_response(status=429)
    response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("429", request=MagicMock(), response=response)
    )
    adapter = _make_adapter(response=response)
    result = await adapter.invoke(
        {"operation": "get", "url": "https://example.com/"},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    assert result.ok is False
    assert result.error.code == ErrorCode.RATE_LIMIT
    assert result.error.retriable is True


# ---------------------------------------------------------------------------
# download_to_path operation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_download_to_path_ok(tmp_path: Path):
    dest = tmp_path / "doc.pdf"

    # Build a streaming mock
    chunk_data = b"%PDF-1.4 fake pdf content"
    stream_resp = MagicMock()
    stream_resp.status_code = 200
    stream_resp.headers = {"content-type": "application/pdf"}
    stream_resp.url = httpx.URL("https://example.com/doc.pdf")
    stream_resp.raise_for_status = MagicMock()

    async def _aiter_bytes(chunk_size=65536):
        yield chunk_data

    stream_resp.aiter_bytes = _aiter_bytes

    # Context manager protocol
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=stream_resp)
    cm.__aexit__ = AsyncMock(return_value=False)

    client = AsyncMock(spec=httpx.AsyncClient)
    client.is_closed = False
    client.stream = MagicMock(return_value=cm)

    adapter = HttpFetchAdapter(client=client)
    result = await adapter.invoke(
        {"operation": "download_to_path", "url": "https://example.com/doc.pdf", "path": str(dest)},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    assert result.ok is True
    assert result.data["path"] == str(dest)
    assert result.data["size_bytes"] == len(chunk_data)
    assert dest.exists()
    assert dest.read_bytes() == chunk_data


@pytest.mark.asyncio
async def test_download_to_path_missing_path_field():
    adapter = _make_adapter()
    result = await adapter.invoke(
        {"operation": "download_to_path", "url": "https://example.com/doc.pdf"},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    assert result.ok is False
    assert result.error.code == ErrorCode.BAD_INPUT


# ---------------------------------------------------------------------------
# Timeout + request errors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_timeout_maps_to_timeout_error():
    adapter = _make_adapter(exc=httpx.TimeoutException("timed out"))
    result = await adapter.invoke(
        {"operation": "head", "url": "https://slow.example.com/"},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    assert result.ok is False
    assert result.error.code == ErrorCode.TIMEOUT
    assert result.error.retriable is True


@pytest.mark.asyncio
async def test_request_error_retriable():
    adapter = _make_adapter(exc=httpx.ConnectError("connection refused"))
    result = await adapter.invoke(
        {"operation": "head", "url": "https://unreachable.example.com/"},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    assert result.ok is False
    assert result.error.code == ErrorCode.TOOL_ERROR
    assert result.error.retriable is True


# ---------------------------------------------------------------------------
# Bad input
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_operation():
    adapter = _make_adapter()
    result = await adapter.invoke({"url": "https://example.com/"}, deadline_s=10.0, caller=Caller.MAKER)
    assert result.ok is False
    assert result.error.code == ErrorCode.BAD_INPUT


@pytest.mark.asyncio
async def test_invalid_operation():
    adapter = _make_adapter()
    result = await adapter.invoke(
        {"operation": "post", "url": "https://example.com/"},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    assert result.ok is False
    assert result.error.code == ErrorCode.BAD_INPUT


@pytest.mark.asyncio
async def test_missing_url():
    adapter = _make_adapter()
    result = await adapter.invoke({"operation": "head"}, deadline_s=10.0, caller=Caller.MAKER)
    assert result.ok is False
    assert result.error.code == ErrorCode.BAD_INPUT


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_always_true():
    assert await HttpFetchAdapter().health() is True


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------

def test_manifest_has_required_fields():
    m = HttpFetchAdapter().manifest
    names = {p.name for p in m.required}
    assert "operation" in names
    assert "url" in names
