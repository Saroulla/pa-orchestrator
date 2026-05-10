"""Tests for orchestrator.maker.url_log."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from orchestrator.maker.url_log import append


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_append_creates_file_with_header(tmp_path):
    """First append creates the file and writes the markdown table header."""
    log_file = tmp_path / "url-access.md"
    _run(
        append(
            job_id="j1",
            url="https://example.com",
            content_type="text/html",
            status=200,
            tool="httpx",
            output_path=None,
            _log_path=log_file,
        )
    )
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "| timestamp | job_id | url | content_type | status | tool | output_path | notes |" in content
    assert "| --- | --- | --- | --- | --- | --- | --- | --- |" in content


def test_append_adds_row(tmp_path):
    """After a single append the file contains the URL in a table row."""
    log_file = tmp_path / "url-access.md"
    _run(
        append(
            job_id="j2",
            url="https://example.org/page",
            content_type="application/json",
            status=200,
            tool="playwright",
            output_path="/tmp/out.html",
            notes="test note",
            _log_path=log_file,
        )
    )
    content = log_file.read_text(encoding="utf-8")
    assert "https://example.org/page" in content


def test_append_second_call_does_not_duplicate_header(tmp_path):
    """Two successive appends should produce exactly one header row."""
    log_file = tmp_path / "url-access.md"
    for _ in range(2):
        _run(
            append(
                job_id="j3",
                url="https://example.com",
                content_type="text/plain",
                status=200,
                tool="httpx",
                output_path=None,
                _log_path=log_file,
            )
        )
    content = log_file.read_text(encoding="utf-8")
    header_line = "| timestamp | job_id | url | content_type | status | tool | output_path | notes |"
    assert content.count(header_line) == 1


def test_append_none_job_id_renders_empty(tmp_path):
    """job_id=None should appear as an empty cell, not the string 'None'."""
    log_file = tmp_path / "url-access.md"
    _run(
        append(
            job_id=None,
            url="https://example.com",
            content_type="text/html",
            status=200,
            tool="httpx",
            output_path=None,
            _log_path=log_file,
        )
    )
    content = log_file.read_text(encoding="utf-8")
    assert "None" not in content


def test_append_none_output_path_renders_empty(tmp_path):
    """output_path=None should appear as an empty cell, not the string 'None'."""
    log_file = tmp_path / "url-access.md"
    _run(
        append(
            job_id="j5",
            url="https://example.com",
            content_type="text/html",
            status=200,
            tool="httpx",
            output_path=None,
            _log_path=log_file,
        )
    )
    content = log_file.read_text(encoding="utf-8")
    assert "None" not in content


def test_append_never_raises(tmp_path):
    """If the path is unwritable/invalid the function must return without raising."""
    # Pass a path inside a non-existent deeply nested unwritable location.
    # On Windows a path with illegal characters triggers an OS error.
    bad_path = Path("Z:\\does\\not\\exist\\url-access.md")
    # Should not raise
    _run(
        append(
            job_id="j6",
            url="https://example.com",
            content_type="text/html",
            status=200,
            tool="httpx",
            output_path=None,
            _log_path=bad_path,
        )
    )


def test_url_log_append_row_count(tmp_path):
    """3 appends → header + separator + 3 data rows = 5 non-empty lines total."""
    log_file = tmp_path / "url-access.md"
    for i in range(3):
        _run(
            append(
                job_id=f"job-{i}",
                url=f"https://example.com/{i}",
                content_type="text/html",
                status=200,
                tool="httpx",
                output_path=None,
                _log_path=log_file,
            )
        )
    lines = [ln for ln in log_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 5, f"Expected 5 lines, got {len(lines)}: {lines}"
