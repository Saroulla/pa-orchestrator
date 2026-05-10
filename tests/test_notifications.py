"""Tests for Step 24 — Async job notification."""
import asyncio
import json
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

from orchestrator.events import _dispatch_one, _format_telegram
from orchestrator.job_runner import _insert_event


def _create_events_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            kind TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            delivered INTEGER NOT NULL DEFAULT 0,
            delivered_at TEXT
        )"""
    )
    conn.commit()


def test_insert_event_two_rows():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_events_table(conn)

    _insert_event(conn, "sess-abc123", "job_complete", {"summary": "done"})

    rows = conn.execute("SELECT * FROM events").fetchall()
    assert len(rows) == 2
    channels = {row["channel"] for row in rows}
    assert channels == {"web", "telegram"}
    for row in rows:
        assert row["session_id"] == "sess-abc123"
    conn.close()


def test_insert_event_none_session():
    conn = sqlite3.connect(":memory:")
    _create_events_table(conn)

    _insert_event(conn, None, "job_complete", {"summary": "done"})

    rows = conn.execute("SELECT * FROM events").fetchall()
    assert len(rows) == 0
    conn.close()


def test_events_consumer_wraps_web_payload():
    row = {
        "id": 1,
        "channel": "web",
        "session_id": "websession1",
        "kind": "job_complete",
        "payload": json.dumps({"summary": "done", "cost_usd": 0.01}),
    }

    ws_manager = MagicMock()
    ws_manager.send = AsyncMock(return_value=True)
    db = MagicMock()

    async def _run():
        with patch("orchestrator.store.mark_event_delivered", AsyncMock()):
            await _dispatch_one(db, row, ws_manager, bot=None)

    asyncio.run(_run())

    ws_manager.send.assert_called_once_with(
        "websession1",
        {"event": "job_complete", "data": {"summary": "done", "cost_usd": 0.01}},
    )


def test_format_telegram_job_complete():
    result = _format_telegram(
        "job_complete",
        {"summary": "Job 'hn-daily' complete.", "cost_usd": 0.0},
    )
    assert "✓ Job complete" in result
    assert "Job 'hn-daily' complete." in result


def test_format_telegram_no_chat_id():
    row = {
        "id": 2,
        "channel": "telegram",
        "session_id": "telgsession",
        "kind": "job_complete",
        "payload": json.dumps({"summary": "done", "cost_usd": 0.0}),
    }

    ws_manager = MagicMock()
    bot = MagicMock()
    db = MagicMock()
    mark_delivered = AsyncMock()

    async def fake_get_session(_db, _session_id):
        return {"telegram_chat_id": None}

    async def _run():
        with patch("orchestrator.store.get_session", fake_get_session), \
             patch("orchestrator.store.mark_event_delivered", mark_delivered):
            await _dispatch_one(db, row, ws_manager, bot=bot)

    asyncio.run(_run())

    mark_delivered.assert_called_once_with(db, 2)
