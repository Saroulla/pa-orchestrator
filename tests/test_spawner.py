"""Unit tests for orchestrator/spawner.py — one-shot CTO session manager.

The spawner no longer holds a persistent claude.exe subprocess; it manages
per-session metadata (workspace, brief, claude --resume id). These tests
exercise that surface only — actual ``claude -p ...`` invocation lives in
``orchestrator/proxy/adapters/claude_code.py``.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.models import Caller, Result
from orchestrator import spawner as spawner_mod
from orchestrator.spawner import (
    HARD_CAP,
    IDLE_MINUTES,
    SubAgentHandle,
    SubAgentSpawner,
    _resolve_claude_argv,
    _scrub_env,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


class FakeDB:
    def __init__(self) -> None:
        self.execute_calls: list[tuple[str, tuple]] = []
        self.commits = 0

    async def execute(self, sql: str, params: tuple = ()):
        self.execute_calls.append((sql, params))
        return MagicMock()

    async def commit(self) -> None:
        self.commits += 1


class FakeDBContext:
    def __init__(self, db: FakeDB) -> None:
        self._db = db

    async def __aenter__(self) -> FakeDB:
        return self._db

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def make_db_getter(db: FakeDB):
    return lambda: FakeDBContext(db)


class FakeClaudeAPI:
    """Mimics ClaudeAPIAdapter.invoke surface used by the spawner."""

    def __init__(self, brief_text: str = "user wants to write hello.py with a print statement.") -> None:
        self.brief_text = brief_text
        self.invoke_calls: list[tuple[dict, float, Caller]] = []

    async def invoke(self, payload: dict, deadline_s: float, caller: Caller) -> Result:
        self.invoke_calls.append((payload, deadline_s, caller))
        return Result(ok=True, data=self.brief_text, cost_usd=0.001)


def make_spawner(tmp_path: Path, *, brief_text: str = "brief") -> tuple[SubAgentSpawner, FakeDB, FakeClaudeAPI]:
    db = FakeDB()
    api = FakeClaudeAPI(brief_text=brief_text)
    spawner = SubAgentSpawner(
        db_getter=make_db_getter(db),
        claude_api_adapter=api,
        sessions_dir=tmp_path,
    )
    return spawner, db, api


# ---------------------------------------------------------------------------
# Test 1 — spawn() / get_or_create_handle creates correct directory structure
# ---------------------------------------------------------------------------


async def test_spawn_creates_directory_structure(tmp_path: Path):
    spawner, _, _ = make_spawner(tmp_path)

    await spawner.spawn("sess-0001", brief_context=[])

    session_root = tmp_path / "sess-0001"
    assert session_root.is_dir()
    assert (session_root / ".claude").is_dir()
    assert (session_root / ".claude" / "skills").is_dir()
    assert (session_root / "workspace").is_dir()


# ---------------------------------------------------------------------------
# Test 2 — spawn() writes CLAUDE.md and skills/code.md
# ---------------------------------------------------------------------------


async def test_spawn_writes_claude_md_and_skill(tmp_path: Path):
    spawner, _, _ = make_spawner(tmp_path, brief_text="USER WANTS A HELLO SCRIPT.")

    await spawner.spawn("sess-0002", brief_context=[
        {"role": "user", "content": "write hello.py"},
    ])

    session_root = tmp_path / "sess-0002"
    claude_md = (session_root / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
    skill_md = (session_root / ".claude" / "skills" / "code.md").read_text(encoding="utf-8")

    assert "USER WANTS A HELLO SCRIPT." in claude_md
    assert str(session_root / "workspace") in claude_md
    assert "Output Protocol" in claude_md
    assert "NDJSON" in claude_md
    assert '"phase": "plan"' in claude_md
    assert "Code Skill" in skill_md


# ---------------------------------------------------------------------------
# Test 3 — spawn() calls Claude API exactly once for brief generation
# ---------------------------------------------------------------------------


async def test_spawn_calls_claude_api_once_for_brief(tmp_path: Path):
    spawner, _, api = make_spawner(tmp_path)

    history = [
        {"role": "user", "content": "write hello.py"},
        {"role": "assistant", "content": "ok"},
    ]

    await spawner.spawn("sess-0003", brief_context=history)

    assert len(api.invoke_calls) == 1
    payload, deadline, caller = api.invoke_calls[0]
    assert caller == Caller.PA
    assert deadline == spawner_mod.BRIEF_DEADLINE_S
    assert payload["messages"][:2] == history
    assert payload["messages"][-1]["role"] == "user"
    assert "task brief" in payload["messages"][-1]["content"].lower()
    assert payload["session_id"] == "sess-0003"
    assert payload["max_tokens"] == spawner_mod.BRIEF_MAX_TOKENS


# ---------------------------------------------------------------------------
# Test 4 — spawn() registers handle with cc_pid sentinel = -1
# ---------------------------------------------------------------------------


async def test_spawn_registers_handle_and_persists_cc_pid_sentinel(tmp_path: Path):
    spawner, db, _ = make_spawner(tmp_path)

    handle = await spawner.spawn("sess-0004", brief_context=[])

    assert isinstance(handle, SubAgentHandle)
    assert handle.session_id == "sess-0004"
    assert handle.workspace_dir == tmp_path / "sess-0004" / "workspace"
    assert handle.claude_session_id is None
    assert spawner._handles["sess-0004"] is handle

    update_calls = [
        (s, p) for s, p in db.execute_calls
        if "UPDATE sessions" in s and "cc_pid = ?" in s
    ]
    assert len(update_calls) == 1
    assert update_calls[0][1] == (-1, "sess-0004")
    assert db.commits >= 1


# ---------------------------------------------------------------------------
# Test 5 — Hard cap: a 3rd handle evicts the oldest
# ---------------------------------------------------------------------------


async def test_hard_cap_evicts_oldest_when_exceeded(tmp_path: Path):
    assert HARD_CAP == 2  # sanity — guards the test's intent
    spawner, _, _ = make_spawner(tmp_path)

    h1 = await spawner.spawn("sess-A", brief_context=[])
    h1.started_at = datetime.utcnow() - timedelta(minutes=10)
    h2 = await spawner.spawn("sess-B", brief_context=[])
    h2.started_at = datetime.utcnow() - timedelta(minutes=5)

    assert set(spawner._handles.keys()) == {"sess-A", "sess-B"}

    with patch.object(spawner, "terminate", new=AsyncMock()) as term_mock:
        # Re-add A and B since terminate is mocked and won't actually pop
        await spawner.spawn("sess-C", brief_context=[])
        term_mock.assert_awaited_once()
        evicted_id = term_mock.await_args.args[0]
        assert evicted_id == "sess-A"


# ---------------------------------------------------------------------------
# Test 6 — terminate() removes handle and clears cc_pid in DB
# ---------------------------------------------------------------------------


async def test_terminate_removes_handle_and_clears_cc_pid(tmp_path: Path):
    spawner, db, _ = make_spawner(tmp_path)
    handle = await spawner.spawn("sess-term", brief_context=[])
    assert "sess-term" in spawner._handles

    db.execute_calls.clear()  # focus the assertion on the terminate path
    db.commits = 0

    await spawner.terminate("sess-term")

    assert "sess-term" not in spawner._handles
    null_updates = [
        (s, p) for s, p in db.execute_calls
        if "UPDATE sessions" in s and "cc_pid = NULL" in s
    ]
    assert null_updates and null_updates[-1][1] == ("sess-term",)
    assert db.commits >= 1


async def test_terminate_unknown_session_is_noop(tmp_path: Path):
    spawner, db, _ = make_spawner(tmp_path)
    await spawner.terminate("does-not-exist")  # must not raise
    assert db.execute_calls == []


# ---------------------------------------------------------------------------
# Test 7 — _reap() evicts idle handles (last_active > IDLE_MINUTES)
# ---------------------------------------------------------------------------


async def test_reap_evicts_idle_handles(tmp_path: Path):
    spawner, _, _ = make_spawner(tmp_path)

    now = datetime.utcnow()
    spawner._handles["sess-fresh"] = SubAgentHandle(
        session_id="sess-fresh",
        workspace_dir=tmp_path / "sess-fresh" / "workspace",
        started_at=now,
        last_active=now,
    )
    spawner._handles["sess-idle"] = SubAgentHandle(
        session_id="sess-idle",
        workspace_dir=tmp_path / "sess-idle" / "workspace",
        started_at=now - timedelta(hours=1),
        last_active=now - timedelta(minutes=IDLE_MINUTES + 1),
    )

    with patch.object(spawner, "terminate", new=AsyncMock()) as term_mock:
        await spawner._reap()

    # Reap calls terminate(sid) for the idle one only
    evicted_ids = [c.args[0] for c in term_mock.await_args_list]
    assert evicted_ids == ["sess-idle"]


# ---------------------------------------------------------------------------
# Test 8 — send() raises NotImplementedError (one-shot mode)
# ---------------------------------------------------------------------------


async def test_send_raises_not_implemented(tmp_path: Path):
    spawner, _, _ = make_spawner(tmp_path)
    with pytest.raises(NotImplementedError):
        await spawner.send("any-session", "any text")


# ---------------------------------------------------------------------------
# Test 9 — _scrub_env removes secrets, keeps ANTHROPIC_API_KEY + Windows essentials
# ---------------------------------------------------------------------------


def test_scrub_env_removes_secrets_keeps_anthropic(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-keep-me")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg-strip")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "1,2,3")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-strip")
    monkeypatch.setenv("CLOUDFLARE_TUNNEL_TOKEN", "cf-strip")
    monkeypatch.setenv("PATH", os.environ.get("PATH", "/tmp"))

    scrubbed = _scrub_env()

    assert scrubbed.get("ANTHROPIC_API_KEY") == "sk-keep-me"
    assert "PATH" in scrubbed
    assert "TELEGRAM_BOT_TOKEN" not in scrubbed
    assert "TELEGRAM_ALLOWED_USER_IDS" not in scrubbed
    assert "BRAVE_SEARCH_API_KEY" not in scrubbed
    assert "CLOUDFLARE_TUNNEL_TOKEN" not in scrubbed


# ---------------------------------------------------------------------------
# Test 10 — spawn() reuses live handle for the same session_id
# ---------------------------------------------------------------------------


async def test_spawn_reuses_live_handle(tmp_path: Path):
    spawner, _, api = make_spawner(tmp_path)

    h1 = await spawner.spawn("sess-reuse", brief_context=[])
    h2 = await spawner.spawn("sess-reuse", brief_context=[])

    assert h1 is h2
    assert len(api.invoke_calls) == 1  # brief generated only on first call


# ---------------------------------------------------------------------------
# Test 11 — update_claude_session_id stores the resume token on the handle
# ---------------------------------------------------------------------------


async def test_update_claude_session_id(tmp_path: Path):
    spawner, _, _ = make_spawner(tmp_path)
    handle = await spawner.spawn("sess-resume", brief_context=[])
    assert handle.claude_session_id is None

    spawner.update_claude_session_id("sess-resume", "abc-claude-uuid")
    assert handle.claude_session_id == "abc-claude-uuid"

    # Idempotent for unknown session
    spawner.update_claude_session_id("does-not-exist", "ignored")  # must not raise


# ---------------------------------------------------------------------------
# Test 12 — _resolve_claude_argv prepends cmd.exe /c on Windows .cmd shims
# ---------------------------------------------------------------------------


def test_resolve_claude_argv_prefixes_cmd_exe_on_windows_cmd(monkeypatch):
    monkeypatch.setattr(spawner_mod.shutil, "which", lambda _: r"C:\Users\me\AppData\Roaming\npm\claude.CMD")
    monkeypatch.setattr(spawner_mod.sys, "platform", "win32")
    argv = _resolve_claude_argv("--output-format", "stream-json")
    assert argv[0] == "cmd.exe"
    assert argv[1] == "/c"
    assert argv[2].lower().endswith("claude.cmd")
    assert argv[-2:] == ["--output-format", "stream-json"]


def test_resolve_claude_argv_passthrough_on_non_windows(monkeypatch):
    monkeypatch.setattr(spawner_mod.shutil, "which", lambda _: "/usr/local/bin/claude")
    monkeypatch.setattr(spawner_mod.sys, "platform", "linux")
    argv = _resolve_claude_argv("-p", "hello")
    assert argv == ["/usr/local/bin/claude", "-p", "hello"]


def test_resolve_claude_argv_unresolved_returns_bare_name(monkeypatch):
    monkeypatch.setattr(spawner_mod.shutil, "which", lambda _: None)
    argv = _resolve_claude_argv("--output-format", "stream-json")
    assert argv[0] == "claude"
    assert argv[-2:] == ["--output-format", "stream-json"]
