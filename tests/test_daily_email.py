"""E4 gate tests — daily email template render + query_jobs_for_day store helper."""
from pathlib import Path

import aiosqlite
import jinja2
import pytest

from orchestrator.store import init_db, query_jobs_for_day

REPO_ROOT = Path("C:/Users/Mini_PC/_REPO")
TEMPLATE_PATH = REPO_ROOT / "config" / "templates" / "maker-daily-report.md.j2"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def db():
    async with aiosqlite.connect(":memory:") as conn:
        await init_db(conn)
        yield conn


# ---------------------------------------------------------------------------
# Template — file exists
# ---------------------------------------------------------------------------


def test_template_file_exists():
    assert TEMPLATE_PATH.exists(), f"Template not found: {TEMPLATE_PATH}"


# ---------------------------------------------------------------------------
# Template — renders expected sections with mock data
# ---------------------------------------------------------------------------


def test_daily_email_template_render():
    """Mock day's data → rendered Jinja template matches expected sections."""
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATE_PATH.parent)),
        autoescape=False,
    )
    template = env.get_template(TEMPLATE_PATH.name)

    data = {
        "date": "2026-05-10",
        "completed_jobs": [
            {
                "name": "research-mistral",
                "cost_usd": 0.0012,
                "result_summary": "3 articles summarised",
            }
        ],
        "failed_jobs": [
            {
                "name": "extract-broken",
                "result_summary": "HTTP 403 on all URLs",
            }
        ],
        "system_messages": [
            {
                "created_at": "2026-05-10T09:00:00+00:00",
                "message_type": "groq_promoted_to_haiku",
            }
        ],
        "cost_by_tier": {"pa-haiku": 0.0012, "pa-groq": 0.0},
        "quota": {"used_today": 5, "free_remaining": 95, "over_quota_cap_usd": 0.0},
    }

    rendered = template.render(**data)

    assert "## Jobs Completed" in rendered
    assert "2026-05-10" in rendered
    assert "research-mistral" in rendered
    assert "3 articles summarised" in rendered
    assert "## Jobs Failed" in rendered
    assert "extract-broken" in rendered
    assert "HTTP 403" in rendered
    assert "## System Events" in rendered
    assert "groq_promoted_to_haiku" in rendered
    assert "## Costs by Tier" in rendered
    assert "pa-haiku" in rendered
    assert "## Google CSE Quota" in rendered
    assert "Used today: 5" in rendered
    assert "Free remaining: 95" in rendered


# ---------------------------------------------------------------------------
# Template — empty data renders without error
# ---------------------------------------------------------------------------


def test_daily_email_template_render_empty():
    """Template renders gracefully when all lists are empty."""
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATE_PATH.parent)),
        autoescape=False,
    )
    template = env.get_template(TEMPLATE_PATH.name)

    rendered = template.render(
        date="2026-05-10",
        completed_jobs=[],
        failed_jobs=[],
        system_messages=[],
        cost_by_tier={},
        quota={"used_today": 0, "free_remaining": 100, "over_quota_cap_usd": 0.0},
    )

    assert "## Jobs Completed (0)" in rendered
    assert "## Jobs Failed (0)" in rendered
    assert "No spend today." in rendered
    assert "Used today: 0" in rendered


# ---------------------------------------------------------------------------
# store helper — query_jobs_for_day
# ---------------------------------------------------------------------------


async def test_query_jobs_for_day_empty(db):
    """Returns empty buckets when no job_runs exist for the date."""
    result = await query_jobs_for_day(db, "2026-05-10")
    assert result == {"completed": [], "failed": [], "scheduled": []}


async def test_query_jobs_for_day_buckets(db):
    """Completed and failed runs land in the right buckets."""
    import uuid
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    today = now[:10]

    # Insert a job row first
    job_id = str(uuid.uuid4())
    await db.execute(
        """INSERT INTO jobs (id, name, file_path, cron, plan_checksum, enabled)
           VALUES (?, ?, ?, ?, ?, 1)""",
        (job_id, "test-research", "jobs/maker/test-research.md", "0 18 * * *", "abc123"),
    )

    # Insert a successful run
    run_id_ok = str(uuid.uuid4())
    await db.execute(
        """INSERT INTO job_runs (id, job_id, started_at, completed_at, status, result_summary, cost_usd)
           VALUES (?, ?, ?, ?, 'success', 'Done', 0.0012)""",
        (run_id_ok, job_id, now, now),
    )

    # Insert a failed run (same job, second run)
    run_id_fail = str(uuid.uuid4())
    await db.execute(
        """INSERT INTO job_runs (id, job_id, started_at, completed_at, status, result_summary, cost_usd)
           VALUES (?, ?, ?, ?, 'failed', 'HTTP 403', 0.0)""",
        (run_id_fail, job_id, now, now),
    )
    await db.commit()

    result = await query_jobs_for_day(db, today)

    assert len(result["completed"]) == 1
    assert result["completed"][0]["name"] == "test-research"
    assert result["completed"][0]["cost_usd"] == pytest.approx(0.0012)

    assert len(result["failed"]) == 1
    assert result["failed"][0]["result_summary"] == "HTTP 403"

    assert result["scheduled"] == []
