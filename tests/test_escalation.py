"""Tests for orchestrator/escalation.py — uses a real in-memory aiosqlite DB."""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from orchestrator.escalation import (
    cancel,
    create,
    expire_pending,
    pending_for,
    resolve_atomic,
    resolve_incoming_message,
)

# Full DDL from build.md § Step 4 (only the tables needed for escalation).
# FK enforcement is OFF so tests don't have to pre-create session rows.
_DDL = """\
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=5000;
PRAGMA foreign_keys=OFF;

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'PA',
    cc_pid INTEGER,
    telegram_chat_id INTEGER,
    cost_to_date_usd REAL NOT NULL DEFAULT 0.0,
    summary_anchor TEXT,
    created_at TEXT NOT NULL,
    last_active TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS escalations (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    options TEXT NOT NULL,
    context TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    resolved_with TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_escalations_session_pending
    ON escalations(session_id, status);
"""


async def _make_db() -> aiosqlite.Connection:
    """Open an isolated in-memory DB and apply the schema."""
    db = await aiosqlite.connect(":memory:")
    await db.executescript(_DDL)
    await db.commit()
    return db


async def _seed_session(db: aiosqlite.Connection, session_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT OR IGNORE INTO sessions (id, channel, mode, created_at, last_active) "
        "VALUES (?, 'web', 'PA', ?, ?)",
        (session_id, now, now),
    )
    await db.commit()


# ── Test 1: create + pending_for happy path ───────────────────────────────

async def test_create_and_pending_for():
    db = await _make_db()
    try:
        await _seed_session(db, "session-01aabb")
        esc_id = await create(
            db,
            "session-01aabb",
            "web",
            {"a": "retry", "b": "skip"},
            '{"error_code": "TIMEOUT"}',
        )
        assert isinstance(esc_id, str)
        assert len(esc_id) == 36  # uuid4 canonical form

        pending = await pending_for(db, "session-01aabb")
        assert pending is not None
        assert pending["id"] == esc_id
        assert pending["status"] == "pending"
        assert pending["session_id"] == "session-01aabb"
        assert pending["channel"] == "web"
        assert pending["options"] == {"a": "retry", "b": "skip"}
        assert pending["context"] == '{"error_code": "TIMEOUT"}'
        assert pending["resolved_with"] is None

        # Non-existent session → None
        assert await pending_for(db, "no-such-session") is None
    finally:
        await db.close()


# ── Test 2: resolve_atomic with matching key returns True ─────────────────

async def test_resolve_atomic_matching_key_returns_true():
    db = await _make_db()
    try:
        await _seed_session(db, "session-02aabb")
        esc_id = await create(
            db, "session-02aabb", "web", {"a": "retry", "b": "skip"}, "ctx"
        )

        result = await resolve_atomic(db, esc_id, "a")
        assert result is True

        # Status must now be 'resolved'; no longer pending
        assert await pending_for(db, "session-02aabb") is None

        # Second call on the same escalation must return False (already resolved)
        result2 = await resolve_atomic(db, esc_id, "a")
        assert result2 is False
    finally:
        await db.close()


# ── Test 3: Two concurrent resolve_atomic calls — only one wins ───────────

async def _apply_schema_to_file(db_path: str, session_id: str) -> str:
    """Bootstrap a file-based DB and return the created escalation id."""
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_DDL)
        await db.commit()
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO sessions (id, channel, mode, created_at, last_active) VALUES (?, 'web', 'PA', ?, ?)",
            (session_id, now, now),
        )
        await db.commit()
        return await create(db, session_id, "web", {"a": "retry", "b": "skip"}, "ctx")


async def test_concurrent_resolve_atomic_only_one_wins():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(db_path)  # let SQLite create it fresh via WAL
    try:
        esc_id = await _apply_schema_to_file(db_path, "session-03aabb")

        async def _try_resolve(key: str) -> bool:
            async with aiosqlite.connect(db_path) as db:
                await db.execute("PRAGMA busy_timeout=5000")
                return await resolve_atomic(db, esc_id, key)

        results = await asyncio.gather(
            _try_resolve("a"),
            _try_resolve("a"),
        )

        wins = results.count(True)
        losses = results.count(False)
        assert wins == 1 and losses == 1, (
            f"Expected exactly one winner and one loser, got {results}"
        )
    finally:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(db_path + suffix)
            except OSError:
                pass


# ── Test 4: Non-matching reply → passthrough + escalation cancelled ────────

async def test_non_matching_reply_passes_through_and_cancels():
    db = await _make_db()
    try:
        await _seed_session(db, "session-04aabb")
        esc_id = await create(
            db, "session-04aabb", "web", {"a": "retry", "b": "skip"}, "ctx"
        )

        outcome, key = await resolve_incoming_message(
            db, "session-04aabb", "Please just retry the whole thing automatically"
        )
        assert outcome == "passthrough"
        assert key is None

        # Escalation must be in 'cancelled' state
        async with db.execute(
            "SELECT status FROM escalations WHERE id=?", (esc_id,)
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == "cancelled"

        # No pending escalation remains for this session
        assert await pending_for(db, "session-04aabb") is None
    finally:
        await db.close()


# ── Test 5: expire_pending marks expired rows, returns session_ids ─────────

async def test_expire_pending_marks_and_returns_affected_session_ids():
    db = await _make_db()
    try:
        await _seed_session(db, "session-05aabb")
        await _seed_session(db, "session-05bbcc")

        now = datetime.now(timezone.utc)
        past = (now - timedelta(seconds=1)).isoformat()
        future = (now + timedelta(seconds=600)).isoformat()
        created_at = now.isoformat()

        # Already-expired escalation (expires_at in the past)
        expired_id = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO escalations "
            "(id, session_id, channel, created_at, expires_at, options, context, status) "
            "VALUES (?, 'session-05aabb', 'web', ?, ?, '{\"a\":\"retry\"}', 'ctx', 'pending')",
            (expired_id, created_at, past),
        )
        # Still-valid escalation (expires_at in the future)
        valid_id = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO escalations "
            "(id, session_id, channel, created_at, expires_at, options, context, status) "
            "VALUES (?, 'session-05bbcc', 'web', ?, ?, '{\"a\":\"retry\"}', 'ctx', 'pending')",
            (valid_id, created_at, future),
        )
        await db.commit()

        affected = await expire_pending(db)

        assert "session-05aabb" in affected
        assert "session-05bbcc" not in affected

        async with db.execute(
            "SELECT status FROM escalations WHERE id=?", (expired_id,)
        ) as cur:
            assert (await cur.fetchone())[0] == "expired"

        async with db.execute(
            "SELECT status FROM escalations WHERE id=?", (valid_id,)
        ) as cur:
            assert (await cur.fetchone())[0] == "pending"
    finally:
        await db.close()


# ── Test 6: resolve_incoming_message end-to-end for all branches ──────────

async def test_rim_no_pending_escalation_returns_passthrough():
    db = await _make_db()
    try:
        await _seed_session(db, "session-06aabb")
        outcome, key = await resolve_incoming_message(db, "session-06aabb", "a")
        assert outcome == "passthrough"
        assert key is None
    finally:
        await db.close()


async def test_rim_matching_key_case_insensitive_and_stripped():
    db = await _make_db()
    try:
        await _seed_session(db, "session-06bbcc")
        await create(db, "session-06bbcc", "web", {"a": "retry", "b": "skip"}, "ctx")

        # "  A  " → stripped to "a" → matches option key "a"
        outcome, key = await resolve_incoming_message(db, "session-06bbcc", "  A  ")
        assert outcome == "resolved"
        assert key == "a"
    finally:
        await db.close()


async def test_rim_non_matching_text_cancels_and_passes_through():
    db = await _make_db()
    try:
        await _seed_session(db, "session-06ccdd")
        await create(db, "session-06ccdd", "web", {"a": "retry", "b": "skip"}, "ctx")

        outcome, key = await resolve_incoming_message(
            db, "session-06ccdd", "please handle this differently"
        )
        assert outcome == "passthrough"
        assert key is None

        # Confirm escalation is now cancelled
        assert await pending_for(db, "session-06ccdd") is None
    finally:
        await db.close()


async def test_rim_newest_pending_wins_when_stacked():
    """ORDER BY created_at DESC LIMIT 1 — newest pending is the one resolved."""
    db = await _make_db()
    try:
        await _seed_session(db, "session-06ddee")
        older_id = await create(
            db, "session-06ddee", "web", {"a": "retry"}, "older ctx", ttl_seconds=600
        )
        newer_id = await create(
            db, "session-06ddee", "web", {"a": "skip"}, "newer ctx", ttl_seconds=600
        )

        outcome, key = await resolve_incoming_message(db, "session-06ddee", "a")
        assert outcome == "resolved"
        assert key == "a"

        # The *newer* escalation must have been resolved
        async with db.execute(
            "SELECT status FROM escalations WHERE id=?", (newer_id,)
        ) as cur:
            assert (await cur.fetchone())[0] == "resolved"

        # The older one must still be pending (untouched)
        async with db.execute(
            "SELECT status FROM escalations WHERE id=?", (older_id,)
        ) as cur:
            assert (await cur.fetchone())[0] == "pending"
    finally:
        await db.close()
