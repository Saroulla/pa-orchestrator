"""Tests for orchestrator/plan_author.py — Step 19."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from orchestrator.plan_author import (
    _strip_yaml_fences,
    generate_plan,
    rebuild_plan,
    write_job,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_YAML = """\
version: 1
trigger:
  cron: "0 8 * * *"
  timezone: "UTC"
steps:
  - id: search
    adapter: brave_search
    params:
      query: "hello world"
"""

_VALID_PLAN = yaml.safe_load(_VALID_YAML)


def _make_claude_api(response_text: str) -> MagicMock:
    """Return a mock ClaudeAPIAdapter that always returns response_text."""
    result = MagicMock()
    result.ok = True
    result.data = response_text
    result.error = None

    api = MagicMock()
    api.invoke = AsyncMock(return_value=result)
    return api


def _make_claude_api_sequence(*responses) -> MagicMock:
    """Return a mock that returns different results on successive invoke calls."""
    results = []
    for text in responses:
        r = MagicMock()
        if text is None:
            r.ok = False
            r.data = None
            r.error = MagicMock()
            r.error.message = "API error"
        else:
            r.ok = True
            r.data = text
            r.error = None
        results.append(r)

    api = MagicMock()
    api.invoke = AsyncMock(side_effect=results)
    return api


# ---------------------------------------------------------------------------
# Test 1 — _strip_yaml_fences
# ---------------------------------------------------------------------------

def test_strip_yaml_fence_with_language_tag():
    raw = "```yaml\nkey: value\n```"
    assert _strip_yaml_fences(raw) == "key: value"


def test_strip_yaml_fence_without_language_tag():
    raw = "```\nkey: value\n```"
    assert _strip_yaml_fences(raw) == "key: value"


def test_strip_yaml_fence_no_fences():
    raw = "key: value"
    assert _strip_yaml_fences(raw) == "key: value"


def test_strip_yaml_fence_with_leading_trailing_whitespace():
    raw = "  ```yaml\n  key: value\n  ```  "
    result = _strip_yaml_fences(raw)
    assert "key: value" in result
    assert "```" not in result


# ---------------------------------------------------------------------------
# Test 2 — generate_plan happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_plan_validates():
    """Mock returns valid YAML; generate_plan returns (str, dict) without retries."""
    api = _make_claude_api(_VALID_YAML)

    plan_yaml, plan = await generate_plan(
        session_id="testsession1",
        what_i_want="Search for hello world every morning.",
        claude_api=api,
    )

    assert isinstance(plan_yaml, str)
    assert isinstance(plan, dict)
    assert plan["version"] == 1
    assert plan["trigger"]["cron"] == "0 8 * * *"
    assert len(plan["steps"]) == 1
    assert plan["steps"][0]["adapter"] == "brave_search"
    # invoke called exactly once — no retries needed
    assert api.invoke.call_count == 1


# ---------------------------------------------------------------------------
# Test 3 — generate_plan retries on invalid YAML then succeeds
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_plan_retries_on_invalid():
    """First response is unparseable; second is valid. Exactly 2 invoke calls."""
    bad_yaml = "this: is: not: valid: yaml: [["
    api = _make_claude_api_sequence(bad_yaml, _VALID_YAML)

    plan_yaml, plan = await generate_plan(
        session_id="testsession2",
        what_i_want="Search for hello world every morning.",
        claude_api=api,
    )

    assert plan["version"] == 1
    assert api.invoke.call_count == 2
    # second call prompt must reference the previous error
    second_call_payload = api.invoke.call_args_list[1][0][0]
    assert "Previous attempt had errors" in second_call_payload["prompt"]


# ---------------------------------------------------------------------------
# Test 4 — write_job creates file and DB row
# ---------------------------------------------------------------------------

def test_write_job_creates_file_and_db_row(tmp_path, monkeypatch):
    """write_job writes markdown file and inserts a jobs row."""
    import orchestrator.plan_author as pa_mod

    # Redirect REPO_ROOT and DB_PATH to tmp_path
    monkeypatch.setattr(pa_mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(pa_mod, "DB_PATH", tmp_path / "test.db")

    # Bootstrap the jobs table in the temp DB
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            name TEXT,
            file_path TEXT,
            cron TEXT,
            plan_checksum TEXT,
            enabled INTEGER DEFAULT 1,
            created_by_session_id TEXT,
            last_run TEXT
        )
    """)
    conn.commit()
    conn.close()

    what_i_want = "Search for hello world every morning."
    job_id = write_job(
        session_id="testsession3",
        name="hello world job",
        what_i_want=what_i_want,
        plan_yaml=_VALID_YAML,
        plan=_VALID_PLAN,
    )

    # File created
    job_file = tmp_path / "jobs" / "hello-world-job.md"
    assert job_file.exists()
    content = job_file.read_text(encoding="utf-8")
    assert "## What I want" in content
    assert what_i_want in content
    assert "## Execution Plan" in content
    assert "brave_search" in content

    # DB row inserted
    conn2 = sqlite3.connect(str(tmp_path / "test.db"))
    row = conn2.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn2.close()

    assert row is not None
    assert row[1] == "hello-world-job"       # name (sanitised)
    assert row[2] == "jobs/hello-world-job.md"  # file_path
    assert row[3] == "0 8 * * *"             # cron from plan


# ---------------------------------------------------------------------------
# Test 5 — rebuild_plan missing file
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rebuild_plan_missing_file(tmp_path, monkeypatch):
    """rebuild_plan returns the 'not found' message when the file does not exist."""
    import orchestrator.plan_author as pa_mod

    monkeypatch.setattr(pa_mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(pa_mod, "DB_PATH", tmp_path / "test.db")

    api = _make_claude_api(_VALID_YAML)
    result = await rebuild_plan(
        session_id="testsession4",
        file_path_str="jobs/nonexistent.md",
        claude_api=api,
    )

    assert result.startswith("[PA]> Job file not found")
    assert "nonexistent.md" in result
    api.invoke.assert_not_called()
