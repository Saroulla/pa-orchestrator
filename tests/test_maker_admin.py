"""Tests for orchestrator/maker/admin.py (step C5)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.maker.admin import (
    ADMIN_HANDLERS,
    handle_cancel,
    handle_config,
    handle_help,
    handle_job_detail,
    handle_jobs,
    handle_logs,
    handle_quota,
    handle_skills,
    handle_status,
    handle_tools,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(fetchone_return=None, fetchall_return=None, rowcount=1):
    """Build an AsyncMock db connection with cursor mocks configured."""
    cursor = MagicMock()
    cursor.fetchone = AsyncMock(return_value=fetchone_return)
    cursor.fetchall = AsyncMock(return_value=fetchall_return or [])
    cursor.rowcount = rowcount
    cursor.__aenter__ = AsyncMock(return_value=cursor)
    cursor.__aexit__ = AsyncMock(return_value=False)

    db = MagicMock()
    db.execute = MagicMock(return_value=cursor)
    db.commit = AsyncMock()
    return db, cursor


# ---------------------------------------------------------------------------
# 1. test_handle_status_ok
# ---------------------------------------------------------------------------

async def test_handle_status_ok():
    """DB returns session/job counts → formatted string contains 'MAKER online'."""
    # Two calls: sessions then jobs; each returns a row with a count.
    call_count = 0
    results = [(3,), (2,)]

    cursor = MagicMock()
    cursor.__aenter__ = AsyncMock(return_value=cursor)
    cursor.__aexit__ = AsyncMock(return_value=False)

    async def _fetchone():
        nonlocal call_count
        val = results[call_count]
        call_count += 1
        return val

    cursor.fetchone = _fetchone

    db = MagicMock()
    db.execute = MagicMock(return_value=cursor)

    result = await handle_status({}, db)
    assert "MAKER online" in result
    assert "Sessions: 3" in result
    assert "Jobs in progress: 2" in result


# ---------------------------------------------------------------------------
# 2. test_handle_jobs_no_table
# ---------------------------------------------------------------------------

async def test_handle_jobs_no_table():
    """DB raises OperationalError (table missing) → returns 'No jobs found.'"""
    db = MagicMock()
    db.execute = MagicMock(side_effect=Exception("no such table: jobs"))

    result = await handle_jobs({}, db)
    assert "No jobs found." in result


# ---------------------------------------------------------------------------
# 3. test_handle_job_not_found
# ---------------------------------------------------------------------------

async def test_handle_job_not_found():
    """DB returns no row → 'not found' in response."""
    db, cursor = _make_db(fetchone_return=None)

    result = await handle_job_detail({"id": "abc123"}, db)
    assert "not found" in result.lower()


# ---------------------------------------------------------------------------
# 4. test_handle_cancel_success
# ---------------------------------------------------------------------------

async def test_handle_cancel_success():
    """db.execute called with UPDATE; rowcount=1 → returns 'cancelled'."""
    db, cursor = _make_db(rowcount=1)

    result = await handle_cancel({"id": "job-42"}, db)
    # Verify the execute was called
    db.execute.assert_called_once()
    call_args = db.execute.call_args[0]
    assert "UPDATE" in call_args[0]
    assert "job-42" in call_args[1]

    assert "cancelled" in result


# ---------------------------------------------------------------------------
# 5. test_handle_skills_missing_file
# ---------------------------------------------------------------------------

async def test_handle_skills_missing_file():
    """config file absent → 'not found' in response."""
    with patch("orchestrator.maker.admin._yaml_load", return_value=None):
        result = await handle_skills({}, None)
    assert "not found" in result.lower()


# ---------------------------------------------------------------------------
# 6. test_handle_tools_missing_file
# ---------------------------------------------------------------------------

async def test_handle_tools_missing_file():
    """config file absent → 'not found' in response."""
    with patch("orchestrator.maker.admin._yaml_load", return_value=None):
        result = await handle_tools({}, None)
    assert "not found" in result.lower()


# ---------------------------------------------------------------------------
# 7. test_handle_help_lists_commands
# ---------------------------------------------------------------------------

async def test_handle_help_lists_commands():
    """Response contains all 10 command names."""
    result = await handle_help({}, None)
    expected_commands = [
        "status", "jobs", "job", "cancel", "skills",
        "tools", "config", "quota", "help", "logs",
    ]
    for cmd in expected_commands:
        assert cmd in result, f"command '{cmd}' not found in help output"


# ---------------------------------------------------------------------------
# 8. test_handle_logs_no_file
# ---------------------------------------------------------------------------

async def test_handle_logs_no_file():
    """url-access.md absent → 'No URL log yet.'"""
    with patch("orchestrator.maker.admin._REPO_ROOT", Path("/nonexistent/path/that/does/not/exist")):
        result = await handle_logs({}, None)
    assert "No URL log yet." in result


# ---------------------------------------------------------------------------
# 9. test_handle_quota_returns_stub
# ---------------------------------------------------------------------------

async def test_handle_quota_returns_stub():
    """Quota handler returns a non-empty string."""
    result = await handle_quota({}, None)
    assert isinstance(result, str)
    assert len(result) > 0


# ---------------------------------------------------------------------------
# Bonus: test ADMIN_HANDLERS registry completeness
# ---------------------------------------------------------------------------

def test_admin_handlers_has_all_10():
    expected = {"status", "jobs", "job", "cancel", "skills", "tools", "config", "quota", "help", "logs"}
    assert set(ADMIN_HANDLERS.keys()) == expected


# ---------------------------------------------------------------------------
# Bonus: test handle_cancel not_found (rowcount=0)
# ---------------------------------------------------------------------------

async def test_handle_cancel_not_found():
    """rowcount=0 → 'not found or already done'."""
    db, cursor = _make_db(rowcount=0)

    result = await handle_cancel({"id": "ghost-job"}, db)
    assert "not found" in result.lower() or "already done" in result.lower()


# ---------------------------------------------------------------------------
# Bonus: test handle_status db=None
# ---------------------------------------------------------------------------

async def test_handle_status_db_none():
    result = await handle_status({}, None)
    assert "DB unavailable" in result


# ---------------------------------------------------------------------------
# Bonus: test handle_jobs returns table when rows exist
# ---------------------------------------------------------------------------

async def test_handle_jobs_with_rows():
    """DB returns rows → markdown table returned."""
    rows = [
        ("id1", "myjob", 1, "sess-abc"),
        ("id2", "otherjob", 0, None),
    ]
    db, cursor = _make_db(fetchall_return=rows)

    result = await handle_jobs({}, db)
    assert "id1" in result
    assert "myjob" in result
    assert "enabled" in result


# ---------------------------------------------------------------------------
# Bonus: test handle_skills with valid YAML
# ---------------------------------------------------------------------------

async def test_handle_skills_with_data():
    fake_data = {
        "skills": [
            {"name": "research", "when_to_use": "Find articles"},
            {"name": "extract", "when_to_use": "Get article content"},
        ]
    }
    with patch("orchestrator.maker.admin._yaml_load", return_value=fake_data):
        result = await handle_skills({}, None)
    assert "research" in result
    assert "Find articles" in result


# ---------------------------------------------------------------------------
# Bonus: test handle_tools with valid YAML
# ---------------------------------------------------------------------------

async def test_handle_tools_with_data():
    fake_data = {
        "tools": {
            "google_cse": {"enabled": True},
            "http_fetch": {"enabled": True},
            "disabled_tool": {"enabled": False},
        }
    }
    with patch("orchestrator.maker.admin._yaml_load", return_value=fake_data):
        result = await handle_tools({}, None)
    assert "google_cse" in result
    assert "http_fetch" in result
    assert "disabled_tool" not in result
