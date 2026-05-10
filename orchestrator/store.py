"""Step 4 — SQLite store (aiosqlite, WAL)."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

DB_PATH = Path("C:/Users/Mini_PC/_REPO/orchestrator.db")

_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{8,64}$")

# ---------------------------------------------------------------------------
# DDL — one statement per list entry executed separately inside init_db.
# ---------------------------------------------------------------------------

_DDL = [
    """CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'PA',
    cc_pid INTEGER,
    telegram_chat_id INTEGER,
    cost_to_date_usd REAL NOT NULL DEFAULT 0.0,
    summary_anchor TEXT,
    created_at TEXT NOT NULL,
    last_active TEXT NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tokens INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
)""",
    "CREATE INDEX IF NOT EXISTS idx_messages_session_time ON messages(session_id, created_at)",
    """CREATE TABLE IF NOT EXISTS escalations (
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
)""",
    "CREATE INDEX IF NOT EXISTS idx_escalations_session_pending ON escalations(session_id, status)",
    """CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0,
    delivered_at TEXT
)""",
    "CREATE INDEX IF NOT EXISTS idx_events_undelivered ON events(delivered, created_at)",
    """CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    file_path TEXT NOT NULL,
    cron TEXT NOT NULL,
    plan_checksum TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_by_session_id TEXT,
    last_run TEXT,
    next_run TEXT
)""",
    """CREATE TABLE IF NOT EXISTS job_runs (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    result_summary TEXT,
    cost_usd REAL,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
)""",
    """CREATE TABLE IF NOT EXISTS cost_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    job_id TEXT,
    timestamp TEXT NOT NULL,
    adapter TEXT NOT NULL,
    tokens_in INTEGER,
    tokens_out INTEGER,
    cost_usd REAL NOT NULL
)""",
    "CREATE INDEX IF NOT EXISTS idx_cost_session_time ON cost_ledger(session_id, timestamp)",
]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_dict(cursor: aiosqlite.Cursor, row: tuple) -> dict[str, Any]:
    """Convert a sqlite3 row tuple to a plain dict using cursor.description."""
    return {col[0]: row[i] for i, col in enumerate(cursor.description)}


def _validate_session_id(session_id: str) -> None:
    if not _SESSION_ID_RE.match(session_id):
        raise ValueError(
            f"Invalid session_id {session_id!r}: must match ^[a-zA-Z0-9_-]{{8,64}}$"
        )


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


async def init_db(db: aiosqlite.Connection) -> None:
    """Apply WAL PRAGMAs and run all DDL (idempotent CREATE TABLE IF NOT EXISTS)."""
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA synchronous=NORMAL")
    await db.execute("PRAGMA busy_timeout=5000")
    for stmt in _DDL:
        await db.execute(stmt)
    await db.commit()


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


async def get_or_create_session(
    db: aiosqlite.Connection,
    session_id: str,
    channel: str,
) -> dict:
    """Insert a new session row if absent; always return the row as a plain dict."""
    _validate_session_id(session_id)
    now = _now()
    await db.execute(
        "INSERT OR IGNORE INTO sessions "
        "(id, channel, mode, cost_to_date_usd, created_at, last_active) "
        "VALUES (?, ?, 'PA', 0.0, ?, ?)",
        (session_id, channel, now, now),
    )
    await db.commit()
    async with db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)) as cur:
        row = await cur.fetchone()
        return _to_dict(cur, row)


async def get_session(
    db: aiosqlite.Connection,
    session_id: str,
) -> dict | None:
    """Return the sessions row as a dict, or None."""
    async with db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)) as cur:
        row = await cur.fetchone()
        if row is None:
            return None
        return _to_dict(cur, row)


async def update_session_mode(
    db: aiosqlite.Connection,
    session_id: str,
    mode: str,
) -> None:
    await db.execute(
        "UPDATE sessions SET mode = ?, last_active = ? WHERE id = ?",
        (mode, _now(), session_id),
    )
    await db.commit()


async def update_session_cc_pid(
    db: aiosqlite.Connection,
    session_id: str,
    pid: int | None,
) -> None:
    await db.execute(
        "UPDATE sessions SET cc_pid = ? WHERE id = ?",
        (pid, session_id),
    )
    await db.commit()


async def upsert_telegram_chat_id(
    session_id: str,
    chat_id: int,
) -> None:
    """Update telegram_chat_id for a session.

    Opens its own connection — telegram.py calls this without a db argument.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE sessions SET telegram_chat_id = ? WHERE id = ?",
            (chat_id, session_id),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


async def add_message(
    db: aiosqlite.Connection,
    session_id: str,
    role: str,
    content: str,
    tokens: int,
) -> int:
    """Insert a message row; return its autoincrement id."""
    async with db.execute(
        "INSERT INTO messages (session_id, role, content, tokens, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (session_id, role, content, tokens, _now()),
    ) as cur:
        row_id: int = cur.lastrowid  # type: ignore[assignment]
    await db.commit()
    return row_id


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


async def get_session_cost(
    db: aiosqlite.Connection,
    session_id: str,
) -> float:
    async with db.execute(
        "SELECT cost_to_date_usd FROM sessions WHERE id = ?",
        (session_id,),
    ) as cur:
        row = await cur.fetchone()
    return float(row[0]) if row is not None else 0.0


async def increment_session_cost(
    db: aiosqlite.Connection,
    session_id: str,
    delta_usd: float,
) -> None:
    await db.execute(
        "UPDATE sessions SET cost_to_date_usd = cost_to_date_usd + ? WHERE id = ?",
        (delta_usd, session_id),
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


async def get_undelivered_events(
    db: aiosqlite.Connection,
    limit: int = 50,
) -> list[dict]:
    async with db.execute(
        "SELECT * FROM events WHERE delivered = 0 ORDER BY created_at LIMIT ?",
        (limit,),
    ) as cur:
        rows = await cur.fetchall()
        return [_to_dict(cur, row) for row in rows]


async def mark_event_delivered(
    db: aiosqlite.Connection,
    event_id: int,
) -> None:
    await db.execute(
        "UPDATE events SET delivered = 1, delivered_at = ? WHERE id = ?",
        (_now(), event_id),
    )
    await db.commit()


async def insert_event(
    db: aiosqlite.Connection,
    session_id: str,
    channel: str,
    kind: str,
    payload: dict,
) -> None:
    """Insert an event row (async, for use within the FastAPI process)."""
    import json as _json
    await db.execute(
        """INSERT INTO events (session_id, channel, kind, payload, created_at, delivered)
           VALUES (?, ?, ?, ?, ?, 0)""",
        (session_id, channel, kind, _json.dumps(payload), _now()),
    )
    await db.commit()
