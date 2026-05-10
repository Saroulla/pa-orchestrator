"""Unit tests for orchestrator/proxy/adapters/article_extract.py — Step B5 gate."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.models import Caller, ErrorCode
from orchestrator.proxy.adapters.article_extract import ArticleExtractAdapter, _extract_sync
from orchestrator.proxy.protocol import Tool

SAMPLE_HTML = """
<html><head><title>Test Article</title></head>
<body><article><h1>Test Article</h1><p>This is the article body with enough text to extract.</p>
<p>Author: Jane Doe</p></article></body></html>
"""


def _make_adapter() -> ArticleExtractAdapter:
    return ArticleExtractAdapter()


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

def test_satisfies_tool_protocol():
    assert isinstance(_make_adapter(), Tool)


def test_name():
    assert ArticleExtractAdapter.name == "article_extract"


def test_allowed_callers():
    assert Caller.MAKER in ArticleExtractAdapter.allowed_callers
    assert Caller.JOB_RUNNER in ArticleExtractAdapter.allowed_callers
    assert Caller.PA not in ArticleExtractAdapter.allowed_callers


# ---------------------------------------------------------------------------
# test_article_extract_trafilatura — spec §11
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invoke_ok_returns_expected_keys():
    adapter = _make_adapter()
    result = await adapter.invoke(
        {"html": SAMPLE_HTML, "url": "https://example.com/article"},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    assert result.ok is True
    assert isinstance(result.data, dict)
    for key in ("title", "byline", "body_text", "body_md", "lang"):
        assert key in result.data


@pytest.mark.asyncio
async def test_invoke_extracts_body_text():
    adapter = _make_adapter()
    result = await adapter.invoke(
        {"html": SAMPLE_HTML, "url": "https://example.com/article"},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    assert result.ok is True
    # body_text should contain some content from the HTML
    assert isinstance(result.data["body_text"], str)
    assert isinstance(result.data["body_md"], str)


@pytest.mark.asyncio
async def test_fallback_to_recall_on_empty(monkeypatch):
    """When standard extraction returns empty body, favor_recall fallback is tried."""
    call_count = {"n": 0}

    def fake_extract(html, **kwargs):
        call_count["n"] += 1
        if kwargs.get("favor_recall"):
            return "fallback content"
        return None  # standard pass returns empty

    monkeypatch.setattr("trafilatura.extract", fake_extract)
    monkeypatch.setattr("trafilatura.extract_metadata", lambda *a, **kw: None)

    adapter = _make_adapter()
    result = await adapter.invoke(
        {"html": "<html><body>x</body></html>", "url": "https://example.com"},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    assert result.ok is True
    assert result.data["body_text"] == "fallback content"
    assert call_count["n"] >= 3  # standard txt + standard md + at least one fallback


@pytest.mark.asyncio
async def test_missing_html_bad_input():
    adapter = _make_adapter()
    result = await adapter.invoke(
        {"url": "https://example.com"},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    assert result.ok is False
    assert result.error.code == ErrorCode.BAD_INPUT
    assert result.error.retriable is False


@pytest.mark.asyncio
async def test_empty_html_bad_input():
    adapter = _make_adapter()
    result = await adapter.invoke(
        {"html": "", "url": "https://example.com"},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    assert result.ok is False
    assert result.error.code == ErrorCode.BAD_INPUT


@pytest.mark.asyncio
async def test_timeout_returns_timeout_error(monkeypatch):
    def slow_extract(html, url):
        import time
        time.sleep(5)
        return {}

    with patch(
        "orchestrator.proxy.adapters.article_extract._extract_sync",
        side_effect=asyncio.TimeoutError(),
    ):
        adapter = _make_adapter()
        result = await adapter.invoke(
            {"html": SAMPLE_HTML, "url": "https://example.com"},
            deadline_s=0.001,
            caller=Caller.MAKER,
        )
    assert result.ok is False
    assert result.error.code == ErrorCode.TIMEOUT
    assert result.error.retriable is True


@pytest.mark.asyncio
async def test_extraction_exception_returns_tool_error(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.proxy.adapters.article_extract._extract_sync",
        MagicMock(side_effect=RuntimeError("trafilatura exploded")),
    )
    adapter = _make_adapter()
    result = await adapter.invoke(
        {"html": SAMPLE_HTML, "url": "https://example.com"},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    assert result.ok is False
    assert result.error.code == ErrorCode.TOOL_ERROR


@pytest.mark.asyncio
async def test_cost_is_zero():
    adapter = _make_adapter()
    result = await adapter.invoke(
        {"html": SAMPLE_HTML, "url": "https://example.com"},
        deadline_s=10.0,
        caller=Caller.MAKER,
    )
    assert result.cost_usd == 0.0


@pytest.mark.asyncio
async def test_health():
    assert await _make_adapter().health() is True
