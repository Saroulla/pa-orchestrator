"""Unit tests for orchestrator/history.py — Step 5 gate.

All tests use an in-memory SQLite DB seeded with known token counts.
The Claude API call inside slide_and_summarise is mocked throughout;
no real API calls are made.
"""
import pytest
import aiosqlite
from unittest.mock import patch, MagicMock

from orchestrator.history import build_context, slide_and_summarise

# Minimal DDL matching Step 4 schema (only tables needed here).
_DDL = """
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

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tokens INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
"""

SID = "test-session-1"

# Tests use a small budget to avoid seeding thousands of tokens.
# max_input_tokens=600, max_output_tokens=100  →  budget = 500
_MAX_IN = 600
_MAX_OUT = 100
_BUDGET = _MAX_IN - _MAX_OUT  # 500


@pytest.fixture
async def db():
    conn = await aiosqlite.connect(":memory:")
    await conn.executescript(_DDL)
    await conn.execute(
        "INSERT INTO sessions (id, channel, mode, created_at, last_active) "
        "VALUES (?, 'web', 'PA', '2026-01-01T00:00:00', '2026-01-01T00:00:00')",
        (SID,),
    )
    await conn.commit()
    yield conn
    await conn.close()


async def _insert(db, role: str, content: str, tokens: int, ts: str) -> None:
    await db.execute(
        "INSERT INTO messages (session_id, role, content, tokens, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (SID, role, content, tokens, ts),
    )
    await db.commit()


def _mock_response(text: str = "Mock summary") -> MagicMock:
    resp = MagicMock()
    resp.content[0].text = text
    return resp


# ---------------------------------------------------------------------------
# Test 1 — build_context respects budget
# ---------------------------------------------------------------------------

async def test_build_context_budget_respected(db):
    # 3 × 200 = 600 tokens total; budget = 500 → only 2 newest fit.
    await _insert(db, "user",      "old", 200, "2026-01-01T01:00:00")
    await _insert(db, "assistant", "mid", 200, "2026-01-01T02:00:00")
    await _insert(db, "user",      "new", 200, "2026-01-01T03:00:00")

    ctx = await build_context(db, SID, max_input_tokens=_MAX_IN, max_output_tokens=_MAX_OUT)

    assert len(ctx) == 2, f"Expected 2 messages in budget, got {len(ctx)}"
    contents = [m["content"] for m in ctx]
    # Oldest must be dropped; result must be chronological.
    assert contents == ["mid", "new"]


# ---------------------------------------------------------------------------
# Test 2 — newest messages preferred; oldest dropped first
# ---------------------------------------------------------------------------

async def test_build_context_drops_oldest_first(db):
    # Budget = 500.
    # Tokens: msg1=100, msg2=200, msg3=200, msg4=200.  Total = 700 > 500.
    # Newest-first accumulation: msg4(200)→200, msg3(200)→400, msg2 would hit 600>500 → break.
    # In window: [msg3, msg4].  msg1 and msg2 are out.
    await _insert(db, "user",      "msg1", 100, "2026-01-01T01:00:00")
    await _insert(db, "assistant", "msg2", 200, "2026-01-01T02:00:00")
    await _insert(db, "user",      "msg3", 200, "2026-01-01T03:00:00")
    await _insert(db, "assistant", "msg4", 200, "2026-01-01T04:00:00")

    ctx = await build_context(db, SID, max_input_tokens=_MAX_IN, max_output_tokens=_MAX_OUT)

    contents = [m["content"] for m in ctx]
    assert "msg1" not in contents, "Oldest message should have been dropped"
    assert "msg2" not in contents, "Second-oldest message should have been dropped"
    assert contents == ["msg3", "msg4"], f"Unexpected context order: {contents}"


# ---------------------------------------------------------------------------
# Test 3 — slide_and_summarise triggers the Claude call at threshold
# ---------------------------------------------------------------------------

async def test_slide_and_summarise_triggers_at_threshold(db):
    # 5 × 200 = 1000 tokens.
    # Budget = 500: newest-first gives msg5(200)+msg4(200)=400, msg3 would hit 600 → break.
    # In window: [msg4, msg5].  Out-of-window: [msg1, msg2, msg3] = 600 tokens.
    # compress_threshold = 400: 600 ≥ 400 → Claude call must fire.
    await _insert(db, "user",      "msg1", 200, "2026-01-01T01:00:00")
    await _insert(db, "assistant", "msg2", 200, "2026-01-01T02:00:00")
    await _insert(db, "user",      "msg3", 200, "2026-01-01T03:00:00")
    await _insert(db, "assistant", "msg4", 200, "2026-01-01T04:00:00")
    await _insert(db, "user",      "msg5", 200, "2026-01-01T05:00:00")

    with patch("orchestrator.history.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = _mock_response()
        await slide_and_summarise(
            db, SID,
            compress_threshold_tokens=400,
            max_input_tokens=_MAX_IN,
            max_output_tokens=_MAX_OUT,
        )
        mock_cls.return_value.messages.create.assert_called_once()


# ---------------------------------------------------------------------------
# Test 4 — summary_anchor written to sessions; compressed rows deleted
# ---------------------------------------------------------------------------

async def test_slide_and_summarise_writes_anchor_and_deletes_rows(db):
    await _insert(db, "user",      "msg1", 200, "2026-01-01T01:00:00")
    await _insert(db, "assistant", "msg2", 200, "2026-01-01T02:00:00")
    await _insert(db, "user",      "msg3", 200, "2026-01-01T03:00:00")
    await _insert(db, "assistant", "msg4", 200, "2026-01-01T04:00:00")
    await _insert(db, "user",      "msg5", 200, "2026-01-01T05:00:00")

    summary_text = "Compressed history summary"

    with patch("orchestrator.history.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = _mock_response(summary_text)
        await slide_and_summarise(
            db, SID,
            compress_threshold_tokens=400,
            max_input_tokens=_MAX_IN,
            max_output_tokens=_MAX_OUT,
        )

    # summary_anchor must be written to the sessions row.
    async with db.execute(
        "SELECT summary_anchor FROM sessions WHERE id = ?", (SID,)
    ) as cursor:
        row = await cursor.fetchone()
    assert row is not None
    assert row[0] == summary_text, f"summary_anchor mismatch: {row[0]!r}"

    # Out-of-window rows [msg1, msg2, msg3] must be deleted; [msg4, msg5] remain.
    async with db.execute(
        "SELECT content FROM messages WHERE session_id = ? ORDER BY created_at",
        (SID,),
    ) as cursor:
        remaining = [r[0] for r in await cursor.fetchall()]

    assert remaining == ["msg4", "msg5"], f"Remaining rows: {remaining}"
    assert "msg1" not in remaining
    assert "msg2" not in remaining
    assert "msg3" not in remaining
