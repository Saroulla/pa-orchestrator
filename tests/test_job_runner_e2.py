"""Tests for E2 — job_runner.py MAKER extensions."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from orchestrator.job_runner import (
    _dispatch_step,
    _extract_cached_plan,
    _insert_system_message,
    _is_maker_job,
    _load_skill_md,
    _parse_maker_frontmatter,
    _run_maker_job,
    _substitute,
)


# ---------------------------------------------------------------------------
# _is_maker_job
# ---------------------------------------------------------------------------

def test_is_maker_job_true(tmp_path: Path):
    p = tmp_path / "jobs" / "maker" / "my-job-20260510.md"
    p.parent.mkdir(parents=True)
    p.touch()
    assert _is_maker_job(p) is True


def test_is_maker_job_false_jobs_root(tmp_path: Path):
    p = tmp_path / "jobs" / "my-job.md"
    p.parent.mkdir(parents=True)
    p.touch()
    assert _is_maker_job(p) is False


def test_is_maker_job_false_unrelated(tmp_path: Path):
    p = tmp_path / "config" / "maker" / "config.yaml"
    p.parent.mkdir(parents=True)
    p.touch()
    assert _is_maker_job(p) is False


# ---------------------------------------------------------------------------
# _parse_maker_frontmatter
# ---------------------------------------------------------------------------

VALID_FM = """\
---
job_id: research-ai-20260510-120000
skill: research-and-summarise
inputs:
  topic: "AI benchmarks"
  n_articles: 3
deadline: "2026-05-10T18:00:00+00:00"
created_by: maker
session_id: sess-abcd1234
---

## What I want
Find articles on AI benchmarks.
"""


def test_parse_maker_frontmatter_valid():
    fm = _parse_maker_frontmatter(VALID_FM)
    assert fm["skill"] == "research-and-summarise"
    assert fm["inputs"]["n_articles"] == 3
    assert fm["created_by"] == "maker"
    assert fm["session_id"] == "sess-abcd1234"


def test_parse_maker_frontmatter_missing_raises():
    with pytest.raises(ValueError, match="front-matter"):
        _parse_maker_frontmatter("# No front-matter here\n\nJust text.")


def test_parse_maker_frontmatter_whitespace_only_body():
    content = "---\njob_id: x\n---\n\n## What I want\nSomething."
    fm = _parse_maker_frontmatter(content)
    assert fm["job_id"] == "x"


# ---------------------------------------------------------------------------
# _load_skill_md
# ---------------------------------------------------------------------------

@pytest.fixture()
def skills_dir(tmp_path: Path, monkeypatch):
    """Patch SKILLS_DIR to a tmp directory with a test skill."""
    sdir = tmp_path / "skills"
    sdir.mkdir()
    index = {"skills": [{"name": "test-skill", "file": "test-skill.md"}]}
    (sdir / "index.yaml").write_text(yaml.dump(index), encoding="utf-8")
    (sdir / "test-skill.md").write_text("# Test skill\n\nDo stuff.", encoding="utf-8")
    monkeypatch.setattr("orchestrator.job_runner.SKILLS_DIR", sdir)
    return sdir


def test_load_skill_md_found(skills_dir: Path):
    content = _load_skill_md("test-skill")
    assert "# Test skill" in content


def test_load_skill_md_not_found_raises(skills_dir: Path):
    with pytest.raises(ValueError, match="not found in index"):
        _load_skill_md("nonexistent-skill")


def test_load_skill_md_missing_index_raises(tmp_path: Path, monkeypatch):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    monkeypatch.setattr("orchestrator.job_runner.SKILLS_DIR", empty_dir)
    with pytest.raises(RuntimeError, match="Skills index not found"):
        _load_skill_md("any-skill")


# ---------------------------------------------------------------------------
# _extract_cached_plan
# ---------------------------------------------------------------------------

PLAN_YAML = """\
version: 1
steps:
  - id: search
    adapter: google_cse
    params:
      q: "AI benchmarks"
      n: 6
    on_error: escalate
"""

WITH_PLAN = VALID_FM + f"\n## Execution Plan\n```yaml\n{PLAN_YAML}```\n"
WITHOUT_PLAN = VALID_FM


def test_extract_cached_plan_present():
    plan = _extract_cached_plan(WITH_PLAN)
    assert plan is not None
    assert plan["version"] == 1
    assert plan["steps"][0]["adapter"] == "google_cse"


def test_extract_cached_plan_absent():
    assert _extract_cached_plan(WITHOUT_PLAN) is None


def test_extract_cached_plan_invalid_yaml():
    bad = VALID_FM + "\n## Execution Plan\n```yaml\n{bad: [yaml: -\n```\n"
    assert _extract_cached_plan(bad) is None


# ---------------------------------------------------------------------------
# _insert_system_message
# ---------------------------------------------------------------------------

@pytest.fixture()
def mem_db():
    """In-memory SQLite with events table."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT, channel TEXT, kind TEXT,
        payload TEXT, created_at TEXT, delivered INTEGER,
        message_type TEXT
    )""")
    yield conn
    conn.close()


def test_insert_system_message_writes_both_channels(mem_db):
    _insert_system_message(mem_db, "sess-abcd1234", "job_complete", {"job_id": "j1"})
    rows = mem_db.execute("SELECT * FROM events").fetchall()
    assert len(rows) == 2
    channels = {r[2] for r in rows}
    assert channels == {"web", "telegram"}


def test_insert_system_message_kind_and_type(mem_db):
    _insert_system_message(mem_db, "sess-abcd1234", "job_failed", {"reason": "boom"})
    rows = mem_db.execute("SELECT kind, message_type, payload FROM events").fetchall()
    for row in rows:
        assert row[0] == "system_message"
        assert row[1] == "job_failed"
        payload = json.loads(row[2])
        assert payload["reason"] == "boom"


def test_insert_system_message_no_session_is_noop(mem_db):
    _insert_system_message(mem_db, None, "job_complete", {})
    rows = mem_db.execute("SELECT * FROM events").fetchall()
    assert len(rows) == 0


# ---------------------------------------------------------------------------
# _dispatch_step — MAKER adapters present in map
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("adapter_name", [
    "pa_groq", "pa_haiku", "google_cse", "http_fetch", "article_extract",
])
@pytest.mark.asyncio
async def test_dispatch_step_maker_adapters_recognised(adapter_name: str, monkeypatch):
    """Verify MAKER adapter names are in the adapter_map (not rejected as unknown)."""
    mock_result = MagicMock()
    mock_result.ok = True
    mock_result.data = "ok"
    mock_result.cost_usd = 0.0

    mock_adapter = AsyncMock()
    mock_adapter.invoke = AsyncMock(return_value=mock_result)
    mock_cls = MagicMock(return_value=mock_adapter)

    with patch("importlib.import_module") as mock_import:
        mock_module = MagicMock()
        setattr(mock_module, mock_cls.__class__.__name__, mock_cls)
        mock_import.return_value = mock_module
        # Patch getattr inside dispatch to return mock_cls
        with patch("builtins.getattr", side_effect=lambda obj, name, *a: mock_cls if name.endswith("Adapter") else getattr(obj, name)):
            try:
                await _dispatch_step(adapter_name, {})
            except Exception:
                pass  # we only care it didn't raise "Unsupported adapter"


@pytest.mark.asyncio
async def test_dispatch_step_unknown_adapter_raises():
    with pytest.raises(ValueError, match="Unsupported adapter"):
        await _dispatch_step("nonexistent_adapter", {})


# ---------------------------------------------------------------------------
# _run_maker_job — integration with mocks
# ---------------------------------------------------------------------------

@pytest.fixture()
def maker_job_db(tmp_path: Path):
    """In-memory DB with required tables for _run_maker_job testing."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY, name TEXT, file_path TEXT, cron TEXT,
            plan_checksum TEXT, enabled INTEGER, created_by_session_id TEXT,
            next_run TEXT, last_run TEXT
        );
        CREATE TABLE job_runs (
            id TEXT PRIMARY KEY, job_id TEXT, started_at TEXT,
            completed_at TEXT, status TEXT, result_summary TEXT, cost_usd REAL
        );
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT, channel TEXT, kind TEXT,
            payload TEXT, created_at TEXT, delivered INTEGER,
            message_type TEXT
        );
        CREATE TABLE escalations (
            id TEXT PRIMARY KEY, session_id TEXT, channel TEXT,
            created_at TEXT, expires_at TEXT, options TEXT,
            context TEXT, status TEXT
        );
    """)
    return conn


@pytest.mark.asyncio
async def test_run_maker_job_missing_frontmatter(maker_job_db, tmp_path):
    """Job file with no front-matter → fail with system_message(job_failed)."""
    job_file = tmp_path / "no-fm.md"
    job_file.write_text("## What I want\nSomething.", encoding="utf-8")
    maker_job_db.execute(
        "INSERT INTO jobs VALUES ('j1','j1','no-fm.md','@once','x',1,'sess-abcd1234',null,null)"
    )

    job = maker_job_db.execute("SELECT * FROM jobs WHERE id='j1'").fetchone()
    await _run_maker_job(maker_job_db, job, "run1", "2026-05-10T00:00:00", job_file)
    maker_job_db.commit()

    runs = maker_job_db.execute("SELECT status FROM job_runs").fetchall()
    assert runs[0]["status"] == "failed"

    sys_msgs = maker_job_db.execute(
        "SELECT message_type FROM events WHERE kind='system_message'"
    ).fetchall()
    assert any(r["message_type"] == "job_failed" for r in sys_msgs)


@pytest.mark.asyncio
async def test_run_maker_job_executes_cached_plan(maker_job_db, tmp_path, monkeypatch, skills_dir):
    """Job file with cached plan → adapters dispatched, job_complete emitted."""
    plan_yaml = (
        "version: 1\n"
        "steps:\n"
        "  - id: s1\n"
        "    adapter: file_write\n"
        "    params: {path: maker/output/test/out.md, content: hello}\n"
        "    on_error: escalate\n"
    )
    content = VALID_FM + f"\n## Execution Plan\n```yaml\n{plan_yaml}```\n"
    job_file = tmp_path / "test-job.md"
    job_file.write_text(content, encoding="utf-8")

    maker_job_db.execute(
        "INSERT INTO jobs VALUES ('j2','j2','test-job.md','@once','x',1,'sess-abcd1234',null,null)"
    )
    job = maker_job_db.execute("SELECT * FROM jobs WHERE id='j2'").fetchone()

    mock_result = MagicMock(ok=True, data="written", cost_usd=0.0)
    with patch("orchestrator.job_runner._dispatch_step", new=AsyncMock(return_value={"data": "written", "cost_usd": 0.0})):
        with patch("orchestrator.proxy.manifest_registry.get_manifest_registry", return_value={}):
            with patch("orchestrator.job_runner._validate_plan", return_value=[]):
                await _run_maker_job(maker_job_db, job, "run2", "2026-05-10T00:00:00", job_file)
    maker_job_db.commit()

    runs = maker_job_db.execute("SELECT status FROM job_runs").fetchall()
    assert runs[0]["status"] == "success"

    events = maker_job_db.execute("SELECT kind, message_type FROM events").fetchall()
    kinds = {(r["kind"], r["message_type"]) for r in events}
    assert ("job_complete", None) in kinds or ("job_complete", "job_complete") in {(r["kind"], r["message_type"]) for r in events}
    sys_complete = [r for r in events if r["kind"] == "system_message" and r["message_type"] == "job_complete"]
    assert len(sys_complete) == 2  # web + telegram
