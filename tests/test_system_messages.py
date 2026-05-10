"""D1 gate tests — orchestrator.maker.system_messages.emit."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from orchestrator.maker.system_messages import emit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(execute_side_effect=None):
    """Return an AsyncMock db with execute and commit as async callables."""
    db = MagicMock()
    db.execute = AsyncMock(side_effect=execute_side_effect)
    db.commit = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# 1. test_emit_inserts_event_row
# ---------------------------------------------------------------------------


async def test_emit_inserts_event_row():
    """db.execute is called with SQL referencing 'events', kind='system_message',
    and message_type matching the ``type`` argument."""
    db = _make_db()
    await emit(db, "sess-0001", "web", "groq_promoted_to_haiku", {"reason": "429"})

    db.execute.assert_awaited_once()
    sql, params = db.execute.call_args.args

    assert "events" in sql
    # params: (session_id, channel, kind, payload_json, created_at, message_type)
    assert params[2] == "system_message"          # kind
    assert params[5] == "groq_promoted_to_haiku"  # message_type


# ---------------------------------------------------------------------------
# 2. test_emit_commits
# ---------------------------------------------------------------------------


async def test_emit_commits():
    """db.commit is called after db.execute succeeds."""
    db = _make_db()
    await emit(db, "sess-0002", "telegram", "job_complete", {})

    db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# 3. test_emit_never_raises
# ---------------------------------------------------------------------------


async def test_emit_never_raises():
    """If db.execute raises any exception, emit must not propagate it."""
    db = _make_db(execute_side_effect=RuntimeError("disk full"))
    # Must complete without raising
    await emit(db, "sess-0003", "web", "spawn_cap_exceeded", {})


# ---------------------------------------------------------------------------
# 4. test_emit_payload_json_serialised
# ---------------------------------------------------------------------------


async def test_emit_payload_json_serialised():
    """The payload dict is JSON-encoded in the INSERT params."""
    db = _make_db()
    payload = {"tier": "pa-haiku", "original": "pa-groq", "count": 3}
    await emit(db, "sess-0004", "web", "groq_promoted_to_haiku", payload)

    _, params = db.execute.call_args.args
    # params[3] is the payload column value
    assert json.loads(params[3]) == payload


# ---------------------------------------------------------------------------
# 5. test_emit_channel_and_session_in_params
# ---------------------------------------------------------------------------


async def test_emit_channel_and_session_in_params():
    """session_id and channel appear in the INSERT params at the correct positions."""
    db = _make_db()
    await emit(db, "my-session-id", "telegram", "google_quota_warning", {})

    _, params = db.execute.call_args.args
    assert params[0] == "my-session-id"  # session_id
    assert params[1] == "telegram"       # channel
