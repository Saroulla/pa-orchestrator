"""Step 10 — CTO sub-agent session manager + reaper + brief generator.

ONE-SHOT MODEL (post Step-16 finding): the spawner no longer holds a persistent
``claude.exe`` process. Each user message becomes its own ``claude -p ...
--output-format stream-json`` subprocess invocation, driven by ``claude_code.py``.
The spawner now manages per-session metadata (workspace, brief, ``claude --resume``
session id) and provides ``_scrub_env`` / ``_resolve_claude_argv`` helpers.

Wire format and lifecycle: see ``01.Project_Management/sub-agent-pattern.md``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from orchestrator.models import Caller

logger = logging.getLogger(__name__)


REPO_ROOT = Path("C:/Users/Mini_PC/_REPO")
SESSIONS_DIR = REPO_ROOT / "sessions"

HARD_CAP = 2              # max concurrent CTO session handles
IDLE_MINUTES = 15         # evict idle handles after this many minutes
WORKSPACE_GC_HOURS = 24   # delete idle workspaces after this many hours
REAPER_INTERVAL_S = 60    # _reap() cadence
BRIEF_DEADLINE_S = 30.0
BRIEF_MAX_TOKENS = 500


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

SUBAGENT_CLAUDE_MD = """
# CTO Sub-Agent

## Role
You are a coding sub-agent. You write, edit, and run code.
You do NOT converse — you produce structured output only.

## Output Protocol — MANDATORY
Every response MUST be valid NDJSON (one JSON object per line).
Non-envelope lines are silently discarded. Use these phase types:

{{"phase": "plan", "content": "<what you will do>", "needs_confirmation": true}}
{{"phase": "action", "content": "<doing X>"}}
{{"phase": "result", "content": "<what was done>", "files_changed": ["path"], "summary_needed": false}}
{{"phase": "error", "content": "<what failed>", "code": "INTERNAL"}}

## Conversation flow — STRICT

**Questions and read-only requests** (explain, describe, list, show, what is, how does):
Respond immediately with a `result` envelope. No confirmation needed.
  {{"phase": "result", "content": "<your answer>", "files_changed": [], "summary_needed": false}}

**Mutating requests** (write, create, edit, delete, run, install, execute):
Emit exactly ONE `plan` envelope and STOP:
  {{"phase": "plan", "content": "<one-sentence summary of what you will do>", "needs_confirmation": true}}
Wait for the user to reply (a) yes / (b) cancel before taking any action.
After confirmation, emit optional `action` lines then a final `result` envelope.

DO NOT emit prose outside the envelope. DO NOT use markdown formatting in
`content` — plain sentences only. DO NOT wrap envelopes in code fences.

## Workspace
Your working directory: {workspace_path}
You may read/write ONLY within this directory.

## Task Brief
{brief_text}

## Skills
See .claude/skills/code.md for what you can do.

## Constraints
- Output NDJSON only — no prose outside envelope
- Do not access network except via tools provided
- Do not read files outside your workspace
- Always confirm via `plan` + `needs_confirmation: true` before any file
  write, file delete, or command execution — no matter how small.
""".strip()


SUBAGENT_CODE_SKILL_MD = """
# Code Skill

You can:
- Write new files in your workspace
- Edit existing files in your workspace
- Run Python scripts (python <file>)
- Run PowerShell commands (powershell -Command <cmd>)
- Read files in your workspace

You cannot:
- Access the internet directly
- Read files outside your workspace
- Install packages without confirming first
""".strip()


# ---------------------------------------------------------------------------
# Handle
# ---------------------------------------------------------------------------

@dataclass
class SubAgentHandle:
    session_id: str                          # PA session id
    workspace_dir: Path
    started_at: datetime
    claude_session_id: str | None = None     # ``claude --resume`` UUID, set after first call
    last_active: datetime = field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Env scrubbing
# ---------------------------------------------------------------------------

_ENV_ALLOWLIST = {
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "ANTHROPIC_API_KEY",
    "COMSPEC",
    "WINDIR",
}


def _scrub_env() -> dict[str, str]:
    """Return ``os.environ`` filtered to the explicit allowlist.

    Removes BRAVE_SEARCH_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_IDS,
    CLOUDFLARE_TUNNEL_TOKEN, and anything else not in the allowlist. CTO keeps
    ANTHROPIC_API_KEY because it needs to call Claude.
    """
    return {k: v for k, v in os.environ.items() if k in _ENV_ALLOWLIST}


def _resolve_claude_argv(*extra_args: str) -> list[str]:
    """Resolve ``claude`` to an executable argv plus the supplied flags.

    On Windows, ``shutil.which("claude")`` returns the npm-installed
    ``claude.CMD`` shim. ``CreateProcessW`` cannot execute ``.cmd`` files
    directly, and routing through ``cmd.exe /c`` mangles arguments that
    contain ``{`` ``}`` ``"`` or other shell-meaningful characters (they get
    treated as command separators / quoting). The shim itself just calls
    a sibling ``bin/claude.exe`` — find and use that directly when present.
    """
    path = shutil.which("claude")
    if path is None:
        return ["claude", *extra_args]
    if sys.platform == "win32" and path.lower().endswith((".cmd", ".bat")):
        # Probe for the bundled .exe next to the .cmd shim
        shim = Path(path).resolve()
        candidates = [
            shim.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe",
            shim.with_suffix(".exe"),
        ]
        for cand in candidates:
            if cand.is_file():
                return [str(cand), *extra_args]
        # Fall back to cmd.exe /c — works for simple flag sets but may mangle
        # complex string arguments.
        return ["cmd.exe", "/c", path, *extra_args]
    return [path, *extra_args]


# ---------------------------------------------------------------------------
# Spawner
# ---------------------------------------------------------------------------

class SubAgentSpawner:
    def __init__(
        self,
        db_getter: Callable[[], Any],
        claude_api_adapter: Any,
        sessions_dir: Path | None = None,
    ) -> None:
        # db_getter(): returns an async context manager yielding an aiosqlite-style connection
        # claude_api_adapter: ClaudeAPIAdapter instance, used for one-shot brief generation
        self._handles: dict[str, SubAgentHandle] = {}
        self._db_getter = db_getter
        self._claude_api = claude_api_adapter
        self._sessions_dir = Path(sessions_dir) if sessions_dir is not None else SESSIONS_DIR
        self._reaper_task: asyncio.Task | None = None

    # ---- Public API ----

    async def get_or_create_handle(
        self,
        session_id: str,
        brief_context: list[dict],
    ) -> SubAgentHandle:
        # 1. Reuse existing handle for this session — but make sure the
        #    on-disk workspace still exists; an external process (e.g. the
        #    e2e wipe) may have rmtree'd it between calls.
        existing = self._handles.get(session_id)
        if existing is not None:
            existing.workspace_dir.mkdir(parents=True, exist_ok=True)
            existing.last_active = datetime.utcnow()
            return existing

        # 2. Hard cap — evict oldest. No proc to kill in one-shot mode.
        if len(self._handles) >= HARD_CAP:
            oldest_id = min(
                self._handles, key=lambda sid: self._handles[sid].started_at
            )
            await self.terminate(oldest_id)

        # 3. Workspace
        session_root = self._sessions_dir / session_id
        claude_dir = session_root / ".claude"
        skills_dir = claude_dir / "skills"
        workspace_dir = session_root / "workspace"
        skills_dir.mkdir(parents=True, exist_ok=True)
        workspace_dir.mkdir(parents=True, exist_ok=True)

        # 4. Brief — ONE Claude API call
        brief_text = await self._generate_brief(session_id, brief_context)

        # 5/6. Static instructions + skill
        (claude_dir / "CLAUDE.md").write_text(
            SUBAGENT_CLAUDE_MD.format(
                workspace_path=str(workspace_dir),
                brief_text=brief_text,
            ),
            encoding="utf-8",
        )
        (skills_dir / "code.md").write_text(
            SUBAGENT_CODE_SKILL_MD,
            encoding="utf-8",
        )

        # 7. Register handle (no subprocess; one-shot mode)
        now = datetime.utcnow()
        handle = SubAgentHandle(
            session_id=session_id,
            workspace_dir=workspace_dir,
            started_at=now,
            last_active=now,
            claude_session_id=None,
        )
        self._handles[session_id] = handle

        # 8. Persist sentinel cc_pid = -1 ("CTO session active, one-shot mode")
        try:
            async with self._db_getter() as db:
                await db.execute(
                    "UPDATE sessions SET cc_pid = ? WHERE id = ?",
                    (-1, session_id),
                )
                await db.commit()
        except Exception as exc:
            logger.error("spawner: failed to persist cc_pid for %s: %s", session_id, exc)

        return handle

    async def spawn(
        self,
        session_id: str,
        brief_context: list[dict],
    ) -> SubAgentHandle:
        """Alias kept so existing callers (claude_code, main) need no changes."""
        return await self.get_or_create_handle(session_id, brief_context)

    async def send(self, session_id: str, text: str) -> None:
        raise NotImplementedError(
            "send() is unused in one-shot mode; use claude_code.invoke() / stream() instead"
        )

    async def terminate(self, session_id: str) -> None:
        handle = self._handles.pop(session_id, None)
        if handle is None:
            return
        try:
            async with self._db_getter() as db:
                await db.execute(
                    "UPDATE sessions SET cc_pid = NULL WHERE id = ?",
                    (session_id,),
                )
                await db.commit()
        except Exception as exc:
            logger.error("spawner.terminate: db clear failed for %s: %s", session_id, exc)

    def update_claude_session_id(self, session_id: str, claude_sid: str) -> None:
        handle = self._handles.get(session_id)
        if handle is not None:
            handle.claude_session_id = claude_sid
            handle.last_active = datetime.utcnow()

    async def start_reaper(self) -> None:
        if self._reaper_task is not None and not self._reaper_task.done():
            return
        self._reaper_task = asyncio.create_task(self._reaper_loop())

    async def stop_reaper(self) -> None:
        task = self._reaper_task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        self._reaper_task = None

    # ---- Internals ----

    async def _generate_brief(
        self,
        session_id: str,
        brief_context: list[dict],
    ) -> str:
        """One Claude API call to summarise prior context as a task brief."""
        ctx = list(brief_context) if brief_context else []
        ctx.append({
            "role": "user",
            "content": (
                "Summarise this conversation in 3-5 sentences as a task brief "
                "for a coding sub-agent. Focus on what the user wants done."
            ),
        })
        payload = {
            "messages": ctx,
            "max_tokens": BRIEF_MAX_TOKENS,
            "session_id": session_id,
        }
        try:
            result = await self._claude_api.invoke(
                payload, BRIEF_DEADLINE_S, Caller.PA
            )
        except Exception as exc:
            logger.error("spawner: brief generation raised: %s", exc)
            return "(brief generation failed — proceed using stdin requests)"

        if not getattr(result, "ok", False):
            return "(brief generation failed — proceed using stdin requests)"
        data = getattr(result, "data", None)
        if isinstance(data, str) and data.strip():
            return data.strip()
        return "(no brief produced)"

    async def _reaper_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(REAPER_INTERVAL_S)
                try:
                    await self._reap()
                except Exception as exc:
                    logger.exception("spawner: reaper iteration failed: %s", exc)
        except asyncio.CancelledError:
            return

    async def _reap(self) -> None:
        now = datetime.utcnow()
        idle_cutoff = now - timedelta(minutes=IDLE_MINUTES)
        to_evict = [
            sid for sid, h in self._handles.items()
            if h.last_active < idle_cutoff
        ]
        for session_id in to_evict:
            await self.terminate(session_id)
        await self._gc_workspaces(now)

    async def _gc_workspaces(self, now: datetime) -> None:
        gc_cutoff = now - timedelta(hours=WORKSPACE_GC_HOURS)
        if not self._sessions_dir.exists():
            return
        try:
            entries = list(self._sessions_dir.iterdir())
        except OSError as exc:
            logger.warning("spawner: cannot iterate sessions dir: %s", exc)
            return
        for entry in entries:
            if not entry.is_dir():
                continue
            if entry.name in self._handles:
                continue
            try:
                mtime = datetime.utcfromtimestamp(entry.stat().st_mtime)
            except OSError:
                continue
            if mtime < gc_cutoff:
                try:
                    shutil.rmtree(entry, ignore_errors=True)
                except Exception as exc:
                    logger.warning("spawner: workspace GC failed for %s: %s", entry, exc)
