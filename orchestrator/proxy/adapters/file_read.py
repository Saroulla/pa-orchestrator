"""Step 9d — FileReadAdapter: path traversal protection + caller-scoped roots. Implemented by Sonnet in Wave W5."""
from __future__ import annotations

import os
import time
from pathlib import Path

from orchestrator.models import (
    AdapterManifest,
    AdapterParam,
    Caller,
    ErrorCode,
    ErrorDetail,
    Result,
)

REPO_ROOT = Path("C:/Users/Mini_PC/_REPO")


class FileReadAdapter:
    name = "file_read"
    allowed_callers = {Caller.PA, Caller.JOB_RUNNER}
    MAX_READ_BYTES = 52_428_800  # 50 MB

    def __init__(self, repo_root: Path = REPO_ROOT) -> None:
        # repo_root is injectable so tests can point at a tmp_path.
        self._repo_root = repo_root

    # ── internal helpers ───────────────────────────────────────────────────────

    def _roots_for(self, caller: Caller, session_id: str | None) -> list[Path]:
        """Return the list of allowed read roots for this caller."""
        # PA and JOB_RUNNER share the same broad read roots.
        return [
            self._repo_root / "config",
            self._repo_root / "jobs",
            self._repo_root / "sessions",
        ]

    @staticmethod
    def _resolve_and_check(raw_path: str, roots: list[Path]) -> Path | None:
        """
        Resolve raw_path with os.path.realpath (follows symlinks + normalises ..)
        and verify it sits inside one of the caller-scoped roots.

        Returns the resolved Path on success, None if it is outside all roots.
        """
        resolved = Path(os.path.realpath(raw_path))
        for root in roots:
            # Also realpath the root so junction/symlink roots are handled.
            root_real = Path(os.path.realpath(str(root.resolve(strict=False))))
            if resolved.is_relative_to(root_real):
                return resolved
        return None

    # ── Tool Protocol ──────────────────────────────────────────────────────────

    async def invoke(self, payload: dict, deadline_s: float, caller: Caller) -> Result:
        t0 = time.monotonic()

        def _err(code: ErrorCode, msg: str, retriable: bool = False) -> Result:
            return Result(
                ok=False,
                error=ErrorDetail(code=code, message=msg, retriable=retriable),
                meta={
                    "tool": self.name,
                    "latency_ms": int((time.monotonic() - t0) * 1000),
                    "tokens_in": 0,
                    "tokens_out": 0,
                },
            )

        raw_path = payload.get("path")
        if not raw_path:
            return _err(ErrorCode.BAD_INPUT, "payload missing required key 'path'")

        session_id: str | None = payload.get("session_id")

        try:
            roots = self._roots_for(caller, session_id)
        except ValueError as exc:
            return _err(ErrorCode.BAD_INPUT, str(exc))

        # ── 1. Path authorisation (catches traversal + symlink escapes) ────────
        resolved = self._resolve_and_check(str(raw_path), roots)
        if resolved is None:
            return _err(
                ErrorCode.UNAUTHORIZED,
                f"Path {raw_path!r} resolves outside allowed read roots for caller '{caller}'",
            )

        # ── 2. Existence + size in one stat call (avoids TOCTOU) ──────────────
        try:
            stat = resolved.stat()
        except OSError as exc:
            return _err(ErrorCode.TOOL_ERROR, f"Cannot stat {resolved}: {exc}")

        if stat.st_size > self.MAX_READ_BYTES:
            return _err(
                ErrorCode.BAD_INPUT,
                f"File size {stat.st_size} B exceeds the {self.MAX_READ_BYTES} B read limit",
            )

        # ── 3. Read ───────────────────────────────────────────────────────────
        try:
            content = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return _err(ErrorCode.TOOL_ERROR, f"Cannot read {resolved}: {exc}", retriable=True)

        return Result(
            ok=True,
            data={"content": content, "path": str(resolved)},
            meta={
                "tool": self.name,
                "latency_ms": int((time.monotonic() - t0) * 1000),
                "tokens_in": 0,
                "tokens_out": 0,
            },
        )

    async def health(self) -> bool:
        return True

    @property
    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            required=[
                AdapterParam(
                    name="path",
                    type="str",
                    description="Absolute path of the file to read",
                ),
            ],
            optional=[
                AdapterParam(
                    name="session_id",
                    type="str",
                    description="Session/job workspace context (reserved for future use)",
                ),
            ],
        )
