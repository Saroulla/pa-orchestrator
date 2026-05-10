"""Step 9b — ClaudeCodeAdapter (one-shot stream-json mode).

Per-message subprocess invocation:
    claude [--resume <claude-sid>] -p "<text>" \\
        --output-format stream-json --verbose --dangerously-skip-permissions

Each invocation prints one JSON object per line on stdout (Claude's stream-json
wire format) and exits. The CTO's textual response — driven by the spawner-written
``.claude/CLAUDE.md`` — is itself NDJSON envelope lines (``{"phase": ...}``).

Two-layer parser:
  outer: parse Claude's stream-json (system / assistant / result events)
         → extract the assistant text and the ``claude --resume`` session id
  inner: split that text on newlines, parse each line as our envelope JSON

Session continuity is achieved via ``claude --resume <claude-sid>``; the
spawner stores the id on the handle.

Stray (non-envelope) lines are appended to ``sessions/{id}/cto.stray.log``.
Stderr is captured to ``sessions/{id}/cto.err.log``. Neither reaches the user.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any, AsyncIterator

from orchestrator.models import (
    AdapterManifest,
    AdapterParam,
    Caller,
    ErrorCode,
    ErrorDetail,
    Result,
)
from orchestrator.spawner import (
    SESSIONS_DIR,
    SubAgentSpawner,
    _resolve_claude_argv,
    _scrub_env,
)
from orchestrator.proxy.adapters.wrapper_templates import (
    ACTION,
    ASK_TEMPLATE,
    ERROR_TEMPLATE,
    PLAN_CONFIRM,
    PLAN_NO_CONFIRM,
    RESULT_FILES,
    RESULT_SIMPLE,
)

logger = logging.getLogger(__name__)


SYNTHESIS_DEADLINE_S = 20.0
SYNTHESIS_MAX_TOKENS = 400

# Single-line so the value parses cleanly when forwarded to the OS process.
_ENVELOPE_SYSTEM_OVERLAY = (
    "OUTPUT PROTOCOL — STRICT: every line you write to stdout in this "
    "session MUST be a single-line JSON object with a 'phase' key. "
    "Allowed phases: plan, action, result, error, ask. "
    "On EVERY NEW user request — including read-only requests, listings, "
    "or questions — your FIRST output MUST be exactly one envelope: "
    "{\"phase\":\"plan\",\"content\":\"<one-sentence summary>\","
    "\"needs_confirmation\":true} and you MUST STOP after that one line. "
    "Do not list files, do not read files, do not run commands, do not "
    "answer the question yet. Wait for the next turn. After the user "
    "replies (a), proceed with optional action lines and a final "
    "{\"phase\":\"result\",\"content\":\"...\",\"files_changed\":[\"...\"],"
    "\"summary_needed\":false}. There are NO exceptions to the plan-first "
    "rule. NO prose outside envelopes. NO markdown. NO code fences. "
    "ONE JSON object per line."
)


# ---------------------------------------------------------------------------
# File logging helpers
# ---------------------------------------------------------------------------

async def _log_file(session_id: str, filename: str, content: str) -> None:
    path = SESSIONS_DIR / session_id / filename

    def _append() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(content)

    try:
        await asyncio.to_thread(_append)
    except OSError as exc:
        logger.warning("claude_code: log_file %s failed: %s", filename, exc)


async def _log_stray_lines(session_id: str, lines: list[str]) -> None:
    if not lines:
        return
    await _log_file(session_id, "cto.stray.log", "\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Stream-json parsing
# ---------------------------------------------------------------------------

def _parse_stream_json_output(raw: str) -> tuple[str, str | None]:
    """Parse Claude's outer stream-json wrapper.

    Returns (cto_text, claude_session_id_or_None).

    Prefers the ``result`` event's ``result`` field; falls back to concatenating
    all ``assistant`` message text blocks if no result event was emitted.
    """
    accumulated: list[str] = []
    final_result: str | None = None
    final_session_id: str | None = None

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        t = obj.get("type")
        if t == "result":
            r = obj.get("result")
            if isinstance(r, str):
                final_result = r
            sid = obj.get("session_id")
            if isinstance(sid, str):
                final_session_id = sid
        elif t == "assistant":
            msg = obj.get("message") or {}
            for blk in msg.get("content") or []:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    text = blk.get("text", "")
                    if text:
                        accumulated.append(text)
            if final_session_id is None:
                sid = obj.get("session_id")
                if isinstance(sid, str):
                    final_session_id = sid
        elif t == "system":
            if final_session_id is None:
                sid = obj.get("session_id")
                if isinstance(sid, str):
                    final_session_id = sid

    if final_result is not None:
        return final_result, final_session_id
    return "\n".join(accumulated), final_session_id


def _extract_envelopes(cto_text: str, session_id: str) -> list[dict]:
    """Split the CTO's text output into NDJSON envelope dicts.

    Lines that fail JSON parsing or lack a ``phase`` key go to the stray log
    (fire-and-forget). If no valid envelopes are found, synthesise a fallback
    ``result`` envelope with ``summary_needed=True`` so PA can synthesise a
    user-facing summary.
    """
    envelopes: list[dict] = []
    stray_lines: list[str] = []

    for line in cto_text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Tolerate model output wrapped in ```json fences
        if line.startswith("```"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            stray_lines.append(line)
            continue
        if isinstance(obj, dict) and "phase" in obj:
            envelopes.append(obj)
        else:
            stray_lines.append(line)

    if stray_lines:
        try:
            asyncio.create_task(_log_stray_lines(session_id, stray_lines))
        except RuntimeError:
            # No running loop — best effort skip
            pass

    if not envelopes:
        envelopes.append({
            "phase": "result",
            "content": cto_text.strip(),
            "summary_needed": True,
        })
    return envelopes


def _format_result(env: dict) -> str:
    files = env.get("files_changed") or []
    content = env.get("content", "")
    if files:
        return RESULT_FILES.format(content=content, files=", ".join(files))
    return RESULT_SIMPLE.format(content=content)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class ClaudeCodeAdapter:
    name: str = "claude_code"
    allowed_callers: set[Caller] = {Caller.PA, Caller.MAKER}

    def __init__(
        self,
        spawner: SubAgentSpawner,
        claude_api: Any,
        db: Any = None,
    ) -> None:
        self._spawner = spawner
        self._claude_api = claude_api
        self._db = db

    @property
    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            required=[
                AdapterParam(name="session_id", type="str", description="session to route to"),
                AdapterParam(name="text", type="str", description="message to send to CTO"),
            ],
            optional=[
                AdapterParam(
                    name="brief_context",
                    type="list",
                    description="recent messages for brief generation on first call",
                ),
                AdapterParam(
                    name="brief_update",
                    type="bool",
                    description="prefix text with [brief-update]",
                ),
            ],
        )

    async def health(self) -> bool:
        return self._spawner is not None and shutil.which("claude") is not None

    # ------------------------------------------------------------------ invoke

    async def invoke(
        self,
        payload: dict,
        deadline_s: float,
        caller: Caller,
    ) -> Result:
        if not isinstance(payload.get("session_id"), str) or not payload["session_id"]:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.BAD_INPUT,
                    message="'session_id' is required",
                    retriable=False,
                ),
                meta={"tool": self.name},
            )
        if not isinstance(payload.get("text"), str):
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.BAD_INPUT,
                    message="'text' is required",
                    retriable=False,
                ),
                meta={"tool": self.name},
            )

        async for event in self.stream(payload, deadline_s, caller):
            etype = event.get("type")
            if etype == "done":
                return Result(
                    ok=True,
                    data=event.get("text", ""),
                    cost_usd=0.0,
                    meta={"tool": self.name},
                )
            if etype == "error_escalation":
                return Result(
                    ok=False,
                    error=ErrorDetail(
                        code=ErrorCode.TOOL_ERROR,
                        message=event.get("content", ""),
                        retriable=bool(event.get("retriable", False)),
                    ),
                    cost_usd=0.0,
                    meta={"tool": self.name},
                )
            if etype in ("confirmation_needed", "ask"):
                return Result(
                    ok=False,
                    error=ErrorDetail(
                        code=ErrorCode.BAD_INPUT,
                        message="CTO needs confirmation before proceeding",
                        retriable=False,
                    ),
                    cost_usd=0.0,
                    meta={"tool": self.name},
                )

        return Result(
            ok=False,
            error=ErrorDetail(
                code=ErrorCode.TOOL_ERROR,
                message="CTO produced no terminal envelope",
                retriable=True,
            ),
            cost_usd=0.0,
            meta={"tool": self.name},
        )

    # ------------------------------------------------------------------ stream

    async def stream(
        self,
        payload: dict,
        deadline_s: float,
        caller: Caller,
    ) -> AsyncIterator[dict]:
        session_id = payload.get("session_id")
        text = payload.get("text")
        if not isinstance(session_id, str) or not session_id:
            yield {
                "type": "error_escalation",
                "content": "'session_id' is required",
                "code": ErrorCode.BAD_INPUT.value,
                "retriable": False,
            }
            return
        if not isinstance(text, str):
            yield {
                "type": "error_escalation",
                "content": "'text' is required",
                "code": ErrorCode.BAD_INPUT.value,
                "retriable": False,
            }
            return

        try:
            handle = await self._spawner.spawn(
                session_id, payload.get("brief_context", []) or []
            )
        except Exception as exc:
            logger.exception("claude_code: spawner.spawn raised: %s", exc)
            yield {
                "type": "error_escalation",
                "content": f"Failed to prepare CTO session: {exc}",
                "code": ErrorCode.INTERNAL.value,
                "retriable": True,
            }
            return

        outbound = ("[brief-update] " + text) if payload.get("brief_update") else text

        try:
            raw = await self._run_claude(handle, outbound, deadline_s)
        except asyncio.TimeoutError:
            yield {
                "type": "error_escalation",
                "content": "CTO timed out",
                "code": ErrorCode.TIMEOUT.value,
                "retriable": True,
            }
            return
        except Exception as exc:
            logger.exception("claude_code: _run_claude raised: %s", exc)
            yield {
                "type": "error_escalation",
                "content": f"CTO invocation failed: {exc}",
                "code": ErrorCode.INTERNAL.value,
                "retriable": True,
            }
            return

        cto_text, claude_sid = _parse_stream_json_output(raw)
        if claude_sid:
            self._spawner.update_claude_session_id(session_id, claude_sid)

        envelopes = _extract_envelopes(cto_text, session_id)

        for env in envelopes:
            phase = env.get("phase")
            content = env.get("content", "")

            if phase == "plan":
                if env.get("needs_confirmation"):
                    yield {
                        "type": "confirmation_needed",
                        "content": PLAN_CONFIRM.format(content=content),
                        "options": {"a": "yes", "b": "cancel"},
                    }
                    return
                yield {"type": "action", "text": PLAN_NO_CONFIRM.format(content=content)}
                continue

            if phase == "action":
                yield {"type": "action", "text": ACTION.format(content=content)}
                continue

            if phase == "result":
                if env.get("summary_needed"):
                    text_out = await self._synthesise(session_id, content)
                else:
                    text_out = _format_result(env)
                yield {"type": "done", "text": text_out}
                return

            if phase == "error":
                code = env.get("code") or ErrorCode.TOOL_ERROR.value
                yield {
                    "type": "error_escalation",
                    "content": ERROR_TEMPLATE.format(content=content, code=code),
                    "code": code,
                    "retriable": bool(env.get("retriable", False)),
                }
                return

            if phase == "ask":
                yield {
                    "type": "ask",
                    "content": ASK_TEMPLATE.format(content=content),
                    "options": env.get("options") or {},
                }
                return

        yield {
            "type": "error_escalation",
            "content": "CTO response had no terminal phase",
            "code": ErrorCode.TOOL_ERROR.value,
            "retriable": True,
        }

    # --------------------------------------------------------- helpers

    async def _run_claude(
        self,
        handle: Any,
        text: str,
        deadline_s: float,
    ) -> str:
        """Spawn one ``claude -p ... --output-format stream-json`` and return its stdout.

        The spawner-written ``CLAUDE.md`` (sibling of the workspace dir) is loaded
        explicitly via ``--append-system-prompt`` rather than relying on
        auto-discovery — claude's project-memory walk from ``cwd=workspace_dir``
        is not reliable enough to guarantee the envelope protocol is enforced.
        """
        flags: list[str] = []

        # Single-line system-prompt overlay enforcing the envelope contract.
        # The full CLAUDE.md stays in the workspace via project memory; this
        # overlay is the system-level guarantee that the model follows the
        # NDJSON contract even if project memory is missed.
        flags += ["--append-system-prompt", _ENVELOPE_SYSTEM_OVERLAY]

        if handle.claude_session_id:
            flags += ["--resume", handle.claude_session_id]
        flags += [
            "--output-format", "stream-json",
            "--verbose",  # required when --print + --output-format=stream-json
            "--dangerously-skip-permissions",
            "-p", text,
        ]
        argv = _resolve_claude_argv(*flags)

        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(handle.workspace_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_scrub_env(),
            creationflags=creationflags,
        )

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=deadline_s
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            raise

        if stderr_b and stderr_b.strip():
            try:
                await _log_file(
                    handle.session_id,
                    "cto.err.log",
                    stderr_b.decode("utf-8", errors="replace"),
                )
            except Exception as exc:
                logger.warning("claude_code: stderr log failed: %s", exc)

        return stdout_b.decode("utf-8", errors="replace")

    async def _synthesise(self, session_id: str, content: str) -> str:
        """One Claude API call to summarise a result envelope. Falls back to raw content."""
        payload = {
            "operation": "complete",
            "prompt": (
                "Summarise this result for the user in 2-4 sentences:\n\n" + content
            ),
            "max_tokens": SYNTHESIS_MAX_TOKENS,
            "session_id": session_id,
        }
        try:
            result = await self._claude_api.invoke(
                payload, SYNTHESIS_DEADLINE_S, Caller.PA
            )
        except Exception as exc:
            logger.error("claude_code: synthesis raised: %s", exc)
            return content
        if getattr(result, "ok", False) and isinstance(getattr(result, "data", None), str):
            return result.data
        return content
