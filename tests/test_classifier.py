"""Unit tests for orchestrator/maker/classifier.py (step C1)."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from orchestrator.maker.classifier import (
    Classification,
    _detect_admin,
    _parse_groq_response,
    classify,
)
from orchestrator.models import ErrorCode, ErrorDetail, Result


# ── pure-function tests (no I/O) ──────────────────────────────────────────────

def test_detect_admin_status():
    is_admin, cmd, _ = _detect_admin("status")
    assert is_admin and cmd == "status"


def test_detect_admin_quota_with_args():
    is_admin, cmd, extra = _detect_admin("quota approve $5")
    assert is_admin and cmd == "quota" and extra == {"args": "approve $5"}


def test_detect_admin_not_matching():
    is_admin, _, _ = _detect_admin("what is a vector database")
    assert not is_admin


def test_parse_groq_response_inline():
    r = _parse_groq_response('{"bucket":"INLINE_LLM","skill":null,"params":{},"deadline":null}')
    assert r.bucket == "INLINE_LLM"
    assert r.skill is None


def test_parse_groq_response_async_job():
    raw = '{"bucket":"ASYNC_JOB","skill":"research-and-summarise","params":{"topic":"mistral"},"deadline":"2026-05-10T18:00:00"}'
    r = _parse_groq_response(raw)
    assert r.bucket == "ASYNC_JOB"
    assert r.skill == "research-and-summarise"
    assert r.params["topic"] == "mistral"
    assert isinstance(r.deadline, datetime)


def test_parse_groq_response_bad_json():
    r = _parse_groq_response("not valid json")
    assert r.bucket == "INLINE_LLM"


def test_parse_groq_response_unknown_bucket():
    r = _parse_groq_response('{"bucket":"MYSTERY","skill":null,"params":{}}')
    assert r.bucket == "INLINE_LLM"


def test_parse_groq_response_strips_markdown_fence():
    raw = '```json\n{"bucket":"INLINE_LLM","skill":null,"params":{},"deadline":null}\n```'
    r = _parse_groq_response(raw)
    assert r.bucket == "INLINE_LLM"


# ── async tests ───────────────────────────────────────────────────────────────

async def test_classifier_admin_match():
    """'status' → ADMIN_SYNC, command=status — zero LLM calls."""
    r = await classify("status", persona="", skill_index=[])
    assert r.bucket == "ADMIN_SYNC"
    assert r.params["command"] == "status"


async def test_classifier_admin_quota():
    r = await classify("quota", persona="", skill_index=[])
    assert r.bucket == "ADMIN_SYNC"
    assert r.params["command"] == "quota"


async def test_classifier_inline_match():
    mock_adapter = MagicMock()
    mock_adapter.invoke = AsyncMock(return_value=Result(
        ok=True,
        data='{"bucket":"INLINE_LLM","skill":null,"params":{},"deadline":null}',
    ))
    with patch("orchestrator.maker.classifier._get_adapter", return_value=mock_adapter):
        r = await classify(
            "what's a vector database",
            persona="",
            skill_index=[{"name": "research-and-summarise"}],
        )
    assert r.bucket == "INLINE_LLM"
    assert r.skill is None


async def test_classifier_async_match():
    mock_adapter = MagicMock()
    mock_adapter.invoke = AsyncMock(return_value=Result(
        ok=True,
        data='{"bucket":"ASYNC_JOB","skill":"research-and-summarise","params":{"topic":"mistral benchmarks","n_articles":5},"deadline":"2026-05-10T17:00:00"}',
    ))
    with patch("orchestrator.maker.classifier._get_adapter", return_value=mock_adapter):
        r = await classify(
            "find articles on mistral benchmarks by 5pm",
            persona="",
            skill_index=[{"name": "research-and-summarise"}],
        )
    assert r.bucket == "ASYNC_JOB"
    assert r.skill == "research-and-summarise"
    assert r.params["topic"] == "mistral benchmarks"
    assert r.deadline is not None


async def test_classifier_groq_failure_defaults_to_inline():
    mock_adapter = MagicMock()
    mock_adapter.invoke = AsyncMock(return_value=Result(
        ok=False,
        error=ErrorDetail(code=ErrorCode.RATE_LIMIT, message="429 rate limit", retriable=True),
    ))
    with patch("orchestrator.maker.classifier._get_adapter", return_value=mock_adapter):
        r = await classify("do something complex", persona="", skill_index=[])
    assert r.bucket == "INLINE_LLM"
