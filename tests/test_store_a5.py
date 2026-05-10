"""A5 gate tests — store.py helpers: add_system_message, query_system_messages_by_day,
cost_by_tier_for_day; plus column migration idempotency."""
import json
from datetime import datetime, timezone

import aiosqlite
import pytest

from orchestrator.store import (
    add_system_message,
    cost_by_tier_for_day,
    init_db,
    query_system_messages_by_day,
)

SID = "sess-a5test1"
CHAN = "web"


@pytest.fixture()
async def db():
    async with aiosqlite.connect(":memory:") as conn:
        await init_db(conn)
        yield conn


# ---------------------------------------------------------------------------
# Schema — new columns exist
# ---------------------------------------------------------------------------


async def test_events_has_message_type_column(db):
    async with db.execute("PRAGMA table_info(events)") as cur:
        cols = {row[1] async for row in cur}
    assert "message_type" in cols


async def test_cost_ledger_has_tier_column(db):
    async with db.execute("PRAGMA table_info(cost_ledger)") as cur:
        cols = {row[1] async for row in cur}
    assert "tier" in cols


# ---------------------------------------------------------------------------
# Migration idempotency — calling init_db twice must not raise
# ---------------------------------------------------------------------------


async def test_init_db_idempotent(db):
    await init_db(db)  # second call — migrations swallow duplicate-column errors


# ---------------------------------------------------------------------------
# add_system_message
# ---------------------------------------------------------------------------


async def test_add_system_message_returns_id(db):
    row_id = await add_system_message(db, SID, CHAN, "groq_promoted_to_haiku", {"reason": "429"})
    assert isinstance(row_id, int)
    assert row_id > 0


async def test_add_system_message_kind_is_system_message(db):
    await add_system_message(db, SID, CHAN, "job_complete", {"job_id": "j-1"})
    async with db.execute("SELECT kind, message_type FROM events WHERE kind = 'system_message'") as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == "system_message"
    assert row[1] == "job_complete"


async def test_add_system_message_payload_stored(db):
    payload = {"tier": "pa-haiku", "original": "pa-groq"}
    await add_system_message(db, SID, CHAN, "groq_promoted_to_haiku", payload)
    async with db.execute(
        "SELECT payload FROM events WHERE message_type = 'groq_promoted_to_haiku'"
    ) as cur:
        row = await cur.fetchone()
    assert json.loads(row[0]) == payload


async def test_add_system_message_delivered_is_zero(db):
    await add_system_message(db, SID, CHAN, "spawn_cap_exceeded", {})
    async with db.execute(
        "SELECT delivered FROM events WHERE message_type = 'spawn_cap_exceeded'"
    ) as cur:
        row = await cur.fetchone()
    assert row[0] == 0


# ---------------------------------------------------------------------------
# query_system_messages_by_day
# ---------------------------------------------------------------------------


async def test_query_system_messages_by_day_returns_matching_rows(db):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    await add_system_message(db, SID, CHAN, "job_complete", {"job_id": "j-2"})
    await add_system_message(db, SID, CHAN, "google_quota_warning", {})
    rows = await query_system_messages_by_day(db, today)
    assert len(rows) == 2
    types = {r["message_type"] for r in rows}
    assert types == {"job_complete", "google_quota_warning"}


async def test_query_system_messages_by_day_empty_for_other_date(db):
    await add_system_message(db, SID, CHAN, "job_complete", {})
    rows = await query_system_messages_by_day(db, "2000-01-01")
    assert rows == []


async def test_query_system_messages_by_day_excludes_non_system_events(db):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    await db.execute(
        "INSERT INTO events (session_id, channel, kind, payload, created_at, delivered)"
        " VALUES (?, ?, 'job_complete', '{}', datetime('now'), 0)",
        (SID, CHAN),
    )
    await db.commit()
    await add_system_message(db, SID, CHAN, "job_failed", {})
    rows = await query_system_messages_by_day(db, today)
    assert all(r["kind"] == "system_message" for r in rows)


# ---------------------------------------------------------------------------
# cost_by_tier_for_day
# ---------------------------------------------------------------------------


async def _insert_cost(db, tier: str, cost: float, timestamp: str) -> None:
    await db.execute(
        "INSERT INTO cost_ledger (timestamp, adapter, tokens_in, tokens_out, cost_usd, tier)"
        " VALUES (?, 'pa_groq', 100, 50, ?, ?)",
        (timestamp, cost, tier),
    )
    await db.commit()


async def test_cost_by_tier_for_day_aggregates(db):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ts = datetime.now(timezone.utc).isoformat()
    await _insert_cost(db, "pa-groq", 0.001, ts)
    await _insert_cost(db, "pa-groq", 0.002, ts)
    await _insert_cost(db, "pa-haiku", 0.005, ts)
    result = await cost_by_tier_for_day(db, today)
    assert result["pa-groq"] == pytest.approx(0.003)
    assert result["pa-haiku"] == pytest.approx(0.005)


async def test_cost_by_tier_for_day_empty_for_other_date(db):
    ts = datetime.now(timezone.utc).isoformat()
    await _insert_cost(db, "cto", 0.10, ts)
    result = await cost_by_tier_for_day(db, "2000-01-01")
    assert result == {}


async def test_cost_by_tier_for_day_empty_tier_key(db):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    await db.execute(
        "INSERT INTO cost_ledger (timestamp, adapter, tokens_in, tokens_out, cost_usd)"
        " VALUES (datetime('now'), 'claude_api', 100, 50, 0.003)",
    )
    await db.commit()
    result = await cost_by_tier_for_day(db, today)
    assert "" in result
    assert result[""] == pytest.approx(0.003)
