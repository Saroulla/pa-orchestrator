"""Integration tests for orchestrator/maker/job_creator.py (step E1)."""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite
import pytest

_JOBS_DDL = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    file_path TEXT NOT NULL,
    cron TEXT NOT NULL,
    plan_checksum TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_by_session_id TEXT,
    last_run TEXT,
    next_run TEXT
)
"""


async def _make_db(tmp_path):
    db_path = tmp_path / "test.db"
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute(_JOBS_DDL)
        await db.commit()
    return db_path


async def test_job_creator_writes_md(tmp_path, monkeypatch):
    """File at jobs/maker/<topic>-<ts>.md contains expected frontmatter fields."""
    import orchestrator.maker.job_creator as jc

    db_path = await _make_db(tmp_path)
    jobs_dir = tmp_path / "jobs" / "maker"
    monkeypatch.setattr(jc, "DB_PATH", db_path)
    monkeypatch.setattr(jc, "MAKER_JOBS_DIR", jobs_dir)
    monkeypatch.setattr(jc, "REPO_ROOT", tmp_path)

    deadline = datetime(2026, 5, 10, 18, 0, 0, tzinfo=timezone.utc)
    job_id = await jc.create_job(
        skill="research-and-summarise",
        params={"topic": "mistral benchmarks", "n_articles": 3},
        deadline=deadline,
        session_id="testsession1",
    )

    files = list(jobs_dir.glob("*.md"))
    assert len(files) == 1
    content = files[0].read_text()
    assert "skill: research-and-summarise" in content
    assert "mistral benchmarks" in content
    assert "created_by: maker" in content
    assert "## What I want" in content
    assert job_id in content


async def test_job_creator_db_row(tmp_path, monkeypatch):
    """DB row has cron='@once', next_run=deadline-5min, correct session_id."""
    import orchestrator.maker.job_creator as jc

    db_path = await _make_db(tmp_path)
    monkeypatch.setattr(jc, "DB_PATH", db_path)
    monkeypatch.setattr(jc, "MAKER_JOBS_DIR", tmp_path / "jobs" / "maker")
    monkeypatch.setattr(jc, "REPO_ROOT", tmp_path)

    deadline = datetime(2026, 5, 10, 18, 0, 0, tzinfo=timezone.utc)
    job_id = await jc.create_job(
        skill="research-and-summarise",
        params={"topic": "test topic"},
        deadline=deadline,
        session_id="testsession1",
    )

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)) as cur:
            row = await cur.fetchone()

    assert row is not None
    assert row["cron"] == "@once"
    assert row["enabled"] == 1
    assert row["created_by_session_id"] == "testsession1"
    assert "2026-05-10T17:55" in row["next_run"]


async def test_job_creator_slug_from_topic(tmp_path, monkeypatch):
    """job_id starts with a slug derived from the topic param."""
    import orchestrator.maker.job_creator as jc

    db_path = await _make_db(tmp_path)
    monkeypatch.setattr(jc, "DB_PATH", db_path)
    monkeypatch.setattr(jc, "MAKER_JOBS_DIR", tmp_path / "jobs" / "maker")
    monkeypatch.setattr(jc, "REPO_ROOT", tmp_path)

    job_id = await jc.create_job(
        skill="research-and-summarise",
        params={"topic": "Mistral AI News"},
        deadline=None,
        session_id="testsession1",
    )

    assert job_id.startswith("mistral-ai-news-")


async def test_job_creator_description_in_what_i_want(tmp_path, monkeypatch):
    """_description param populates ## What I want; is absent from frontmatter."""
    import orchestrator.maker.job_creator as jc

    db_path = await _make_db(tmp_path)
    jobs_dir = tmp_path / "jobs" / "maker"
    monkeypatch.setattr(jc, "DB_PATH", db_path)
    monkeypatch.setattr(jc, "MAKER_JOBS_DIR", jobs_dir)
    monkeypatch.setattr(jc, "REPO_ROOT", tmp_path)

    await jc.create_job(
        skill="extract-article",
        params={"url": "https://example.com", "_description": "Extract this article for me"},
        deadline=None,
        session_id="testsession1",
    )

    content = list(jobs_dir.glob("*.md"))[0].read_text()
    assert "Extract this article for me" in content
    assert "_description" not in content


async def test_job_creator_no_deadline_defaults(tmp_path, monkeypatch):
    """No deadline → run_at is computed (roughly 18:00 UTC - 5min today/tomorrow)."""
    import orchestrator.maker.job_creator as jc

    db_path = await _make_db(tmp_path)
    monkeypatch.setattr(jc, "DB_PATH", db_path)
    monkeypatch.setattr(jc, "MAKER_JOBS_DIR", tmp_path / "jobs" / "maker")
    monkeypatch.setattr(jc, "REPO_ROOT", tmp_path)

    job_id = await jc.create_job(
        skill="research-and-summarise",
        params={"topic": "no deadline test"},
        deadline=None,
        session_id="testsession1",
    )

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT next_run FROM jobs WHERE id=?", (job_id,)) as cur:
            row = await cur.fetchone()

    assert row is not None
    run_at = datetime.fromisoformat(row["next_run"])
    assert run_at.hour == 17 and run_at.minute == 55
