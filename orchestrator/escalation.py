"""Step 7 — Escalation engine: table CRUD + atomic resolution."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import aiosqlite


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def create(
    db: aiosqlite.Connection,
    session_id: str,
    channel: str,
    options: dict[str, str],
    context: str,
    ttl_seconds: int = 600,
) -> str:
    """Insert an escalations row and return the new escalation id (uuid4)."""
    esc_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    created_at = now.isoformat()
    expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()
    await db.execute(
        """
        INSERT INTO escalations
            (id, session_id, channel, created_at, expires_at, options, context, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (esc_id, session_id, str(channel), created_at, expires_at, json.dumps(options), context),
    )
    await db.commit()
    return esc_id


async def pending_for(db: aiosqlite.Connection, session_id: str) -> dict | None:
    """Return the newest pending escalation for session_id, or None."""
    async with db.execute(
        """
        SELECT id, session_id, channel, created_at, expires_at, options, context, status, resolved_with
        FROM escalations
        WHERE session_id = ? AND status = 'pending'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (session_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "session_id": row[1],
        "channel": row[2],
        "created_at": row[3],
        "expires_at": row[4],
        "options": json.loads(row[5]),
        "context": row[6],
        "status": row[7],
        "resolved_with": row[8],
    }


async def resolve_atomic(db: aiosqlite.Connection, escalation_id: str, with_key: str) -> bool:
    """
    Atomically resolve an escalation via BEGIN IMMEDIATE.

    Returns True if this caller won the race (rowcount == 1).
    Returns False if another writer already resolved it, or if a concurrent
    BEGIN IMMEDIATE on the same connection is already active.
    """
    try:
        await db.execute("BEGIN IMMEDIATE")
    except Exception:
        # Another transaction is already active on this connection (concurrent
        # asyncio caller) or the row is locked by a separate process.
        return False
    try:
        cur = await db.execute(
            "UPDATE escalations SET status='resolved', resolved_with=? "
            "WHERE id=? AND status='pending'",
            (with_key, escalation_id),
        )
        await db.commit()
        return cur.rowcount == 1
    except Exception:
        await db.rollback()
        raise


async def cancel(db: aiosqlite.Connection, escalation_id: str, reason: str) -> None:
    """Cancel a pending escalation; store reason in the context field."""
    await db.execute(
        "UPDATE escalations SET status='cancelled', resolved_with='cancelled', context=? "
        "WHERE id=? AND status='pending'",
        (json.dumps({"cancelled_reason": reason}), escalation_id),
    )
    await db.commit()


async def expire_pending(db: aiosqlite.Connection) -> list[str]:
    """
    Mark all pending escalations whose expires_at < now as 'expired'.
    Returns the list of session_ids that were affected.
    """
    now = _utcnow_iso()
    async with db.execute(
        "SELECT id, session_id FROM escalations WHERE status='pending' AND expires_at < ?",
        (now,),
    ) as cur:
        rows = await cur.fetchall()

    session_ids: list[str] = []
    for esc_id, session_id in rows:
        await db.execute(
            "UPDATE escalations SET status='expired', resolved_with='expired' "
            "WHERE id=? AND status='pending'",
            (esc_id,),
        )
        session_ids.append(session_id)
    if rows:
        await db.commit()
    return session_ids


async def resolve_incoming_message(
    db: aiosqlite.Connection,
    session_id: str,
    text: str,
) -> tuple[str, str | None]:
    """
    Intercept an inbound message and resolve any pending escalation.

    Returns:
      ("resolved",     key)  — text matched an option key; this caller won the atomic race
      ("passthrough",  None) — no pending escalation, or non-matching reply (escalation cancelled)
    """
    pending = await pending_for(db, session_id)
    if pending is None:
        return ("passthrough", None)

    candidate = text.strip().lower()
    options: dict[str, str] = pending["options"]

    if candidate in options:
        won = await resolve_atomic(db, pending["id"], candidate)
        if won:
            return ("resolved", candidate)
        # Lost the race — treat original message as a fresh pass-through
        return ("passthrough", None)

    # Non-matching reply: auto-cancel and let the original message be processed normally
    await cancel(db, pending["id"], f"non-matching reply: {text!r}")
    return ("passthrough", None)
