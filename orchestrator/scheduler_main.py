"""Step 17 — Scheduler subprocess entrypoint [Phase 1.2].

Runs as a separate OS process alongside uvicorn:
    python -m orchestrator.scheduler_main

Manages APScheduler 3.10 with SQLAlchemyJobStore (same orchestrator.db).
Reads the `jobs` table every 30 s and syncs enabled jobs into the scheduler.
Each job invokes job_runner.run(job_id) as an asyncio coroutine.

Cross-process notifications reach the uvicorn process via the `events` table
(inserted by job_runner); the events_consumer task in main.py polls and delivers.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

import yaml
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from orchestrator import job_runner
from orchestrator.maker import browser_context as _browser_context
from orchestrator.maker import daily_digest as _daily_digest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

REPO_ROOT = Path("C:/Users/Mini_PC/_REPO")
DB_PATH = REPO_ROOT / "orchestrator.db"
DB_URL = f"sqlite:///{DB_PATH}"

SYNC_INTERVAL_S = 30
BROWSER_IDLE_TICK_S = 60
MAKER_CONFIG_PATH = REPO_ROOT / "config" / "maker" / "config.yaml"


# ---------------------------------------------------------------------------
# Cron parsing
# ---------------------------------------------------------------------------

def _parse_cron(cron_str: str) -> dict:
    """Convert a 5-field cron string to APScheduler CronTrigger kwargs."""
    parts = cron_str.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron string: {cron_str!r}")
    keys = ["minute", "hour", "day", "month", "day_of_week"]
    return dict(zip(keys, parts))


# ---------------------------------------------------------------------------
# Job wrapper
# ---------------------------------------------------------------------------

async def _run_job(job_id: str) -> None:
    """Coroutine invoked by APScheduler for each scheduled job execution."""
    logger.info("scheduler: starting job %s", job_id)
    try:
        await job_runner.run(job_id)
    except Exception as exc:
        logger.exception("scheduler: job %s raised: %s", job_id, exc)


# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------

async def _sync_jobs(scheduler: AsyncIOScheduler) -> None:
    """Read the jobs table and reconcile with the live APScheduler instance.

    - Enabled jobs missing from the scheduler are added.
    - Disabled or deleted jobs still in the scheduler are removed.
    - Already-registered jobs are left untouched (APScheduler tracks next-run in DB).
    """
    def _read_jobs() -> list[dict]:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT id, name, cron, enabled FROM jobs"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    rows = await asyncio.to_thread(_read_jobs)

    wanted: dict[str, dict] = {}
    for row in rows:
        if row["enabled"]:
            wanted[row["id"]] = row

    # Add jobs not yet scheduled
    for job_id, row in wanted.items():
        if scheduler.get_job(job_id) is None:
            if row["cron"] == "@once":
                run_at_str = row.get("next_run") or ""
                if not run_at_str:
                    logger.warning("scheduler: @once job %s has no next_run, skipping", row["name"])
                    continue
                try:
                    run_at = datetime.fromisoformat(run_at_str)
                except ValueError as exc:
                    logger.warning("scheduler: @once job %s bad next_run %r: %s", row["name"], run_at_str, exc)
                    continue
                scheduler.add_job(
                    _run_job,
                    trigger="date",
                    run_date=run_at,
                    id=job_id,
                    name=row["name"],
                    args=[job_id],
                    replace_existing=True,
                )
                logger.info("scheduler: registered @once job %s (run_at=%s)", row["name"], run_at_str)
            else:
                try:
                    cron_kwargs = _parse_cron(row["cron"])
                except ValueError as exc:
                    logger.warning("scheduler: skipping job %s — %s", row["name"], exc)
                    continue
                scheduler.add_job(
                    _run_job,
                    trigger="cron",
                    id=job_id,
                    name=row["name"],
                    args=[job_id],
                    replace_existing=True,
                    **cron_kwargs,
                )
                logger.info(
                    "scheduler: registered job %s (cron=%s)", row["name"], row["cron"]
                )

    # Remove jobs that are no longer enabled / present
    for job in scheduler.get_jobs():
        if job.id not in wanted:
            scheduler.remove_job(job.id)
            logger.info("scheduler: removed job %s", job.id)


async def _sync_loop(scheduler: AsyncIOScheduler) -> None:
    """Periodically re-sync jobs table into the scheduler."""
    while True:
        await asyncio.sleep(SYNC_INTERVAL_S)
        try:
            await _sync_jobs(scheduler)
        except Exception as exc:
            logger.exception("scheduler: sync_loop error: %s", exc)


def _load_digest_config() -> tuple[int, int, str]:
    """Return (hour, minute, tz_str) from config/maker/config.yaml. Defaults: 18:00 UTC."""
    try:
        raw = yaml.safe_load(MAKER_CONFIG_PATH.read_text(encoding="utf-8"))
        run_cfg = raw.get("run", {})
        time_str = run_cfg.get("daily_report_send_at_local", "18:00")
        tz_str = run_cfg.get("daily_report_timezone", "UTC")
        h, m = (int(x) for x in time_str.split(":", 1))
        return h, m, tz_str
    except Exception as exc:
        logger.warning("scheduler: digest config unreadable: %s — using 18:00 UTC", exc)
        return 18, 0, "UTC"


def _register_daily_digest(scheduler: AsyncIOScheduler) -> None:
    """Register the daily email digest as a CronTrigger. Idempotent."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    from apscheduler.triggers.cron import CronTrigger

    hour, minute, tz_str = _load_digest_config()
    try:
        tz = ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, KeyError) as exc:
        logger.warning("scheduler: unknown timezone %r: %s — falling back to UTC", tz_str, exc)
        tz = ZoneInfo("UTC")

    scheduler.add_job(
        _daily_digest.run_digest,
        trigger=CronTrigger(hour=hour, minute=minute, timezone=tz),
        id="maker_daily_digest",
        name="MAKER daily digest",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    logger.info("scheduler: daily digest registered at %02d:%02d %s", hour, minute, tz_str)


async def _browser_idle_loop() -> None:
    """Periodically close the persistent Chromium context if it has been idle."""
    while True:
        await asyncio.sleep(BROWSER_IDLE_TICK_S)
        try:
            ctx = _browser_context.get_browser_context()
            closed = await ctx.close_if_idle()
            if closed:
                logger.info("scheduler: browser context idle-closed")
        except Exception as exc:
            logger.exception("scheduler: browser_idle_loop error: %s", exc)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

async def main() -> None:
    jobstore = SQLAlchemyJobStore(url=DB_URL)
    scheduler = AsyncIOScheduler(
        jobstores={"default": jobstore},
        job_defaults={
            "coalesce": True,
            "misfire_grace_time": 300,
            "max_instances": 1,
        },
    )
    scheduler.start()
    logger.info("scheduler: APScheduler started (jobstore=%s)", DB_URL)

    _register_daily_digest(scheduler)
    _browser_context.init_browser_context()
    logger.info("scheduler: browser context registered (lazy-start)")

    await _sync_jobs(scheduler)
    asyncio.create_task(_sync_loop(scheduler))
    asyncio.create_task(_browser_idle_loop())

    try:
        await asyncio.Event().wait()  # run forever
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        scheduler.shutdown(wait=False)
        try:
            await _browser_context.get_browser_context().aclose()
        except Exception as exc:
            logger.warning("scheduler: browser shutdown raised: %s", exc)
        logger.info("scheduler: shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
