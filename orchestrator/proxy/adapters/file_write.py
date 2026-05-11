"""Step 9e — FileWriteAdapter: caller-scoped allowlist + atomic write."""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from orchestrator.models import (
    AdapterManifest,
    AdapterParam,
    Caller,
    ErrorCode,
    ErrorDetail,
    Result,
)

# Hardcoded repo root — tests monkeypatch this module attribute.
REPO_ROOT: Path = Path("C:/Users/Mini_PC/_REPO")

MAX_WRITE_BYTES: int = 10_485_760  # 10 MB

_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{8,64}$")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_session_id(scope_id: str) -> None:
    if not _SESSION_ID_RE.match(scope_id):
        raise PermissionError(f"invalid session_id/job_id: {scope_id!r}")


def _compute_allowed_roots(caller: Caller, scope_id: str | None) -> list[Path]:
    """Return the list of write-allowed roots for this caller + scope."""
    sessions_root = REPO_ROOT / "sessions"

    if caller == Caller.PA:
        roots: list[Path] = [
            REPO_ROOT / "jobs",
            REPO_ROOT / "config" / "interests.md",  # exact-file entry
            REPO_ROOT / "config" / "templates",
        ]
        if scope_id:
            _validate_session_id(scope_id)
            roots.append(sessions_root / scope_id / "workspace")
        return roots

    if caller == Caller.JOB_RUNNER:
        if not scope_id:
            raise PermissionError(f"{caller} requires a session_id/job_id scope")
        _validate_session_id(scope_id)
        return [sessions_root / scope_id / "workspace"]

    raise ValueError(f"unknown caller: {caller!r}")


def _validate_write_path(raw: Path, allowed_roots: list[Path]) -> Path:
    """Resolve *raw* against symlinks/junctions and verify it is inside an
    allowed root.  Raises PermissionError if the resolved path escapes.

    The file itself may not exist yet — we resolve the **parent** directory
    (which must exist for an atomic write via mkstemp) and reconstruct the
    final path from the real parent + the base filename.
    """
    # Resolve parent so that ".." and junctions are followed on Windows.
    real_parent = Path(os.path.realpath(str(raw.parent.resolve(strict=False))))
    resolved = real_parent / raw.name

    for root in allowed_roots:
        root_real = Path(os.path.realpath(str(root.resolve(strict=False))))
        # Exact match — covers file-type entries like config/interests.md.
        if root_real == resolved:
            return resolved
        # Directory containment check.
        try:
            resolved.relative_to(root_real)
            return resolved
        except ValueError:
            continue

    raise PermissionError(
        f"write to {raw!r} → resolved {resolved!r} is outside allowed roots; "
        f"roots: {[str(r) for r in allowed_roots]}"
    )


def _write_atomic(target: Path, content: bytes) -> None:
    """Write *content* to *target* atomically via a sibling temp file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
        os.replace(tmp_path, str(target))
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class FileWriteAdapter:
    name = "file_write"
    allowed_callers = {Caller.PA, Caller.JOB_RUNNER}

    async def invoke(
        self,
        payload: dict,
        deadline_s: float,
        caller: Caller,
    ) -> Result:
        path_str: str | None = payload.get("path")
        content: str = payload.get("content", "")
        scope_id: str | None = payload.get("session_id")

        # ── 1. Basic payload check ─────────────────────────────────────────
        if not path_str:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.BAD_INPUT,
                    message="missing required payload key: 'path'",
                    retriable=False,
                ),
            )

        # ── 2. scope_id required for JOB_RUNNER caller ────────────────────
        if caller == Caller.JOB_RUNNER and not scope_id:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.UNAUTHORIZED,
                    message=f"{caller} requires 'session_id' in payload",
                    retriable=False,
                ),
            )

        # ── 3. Size cap — fail fast before any path work ──────────────────
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > MAX_WRITE_BYTES:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.BAD_INPUT,
                    message=(
                        f"content size {len(content_bytes):,} B exceeds "
                        f"{MAX_WRITE_BYTES:,} B limit"
                    ),
                    retriable=False,
                ),
            )

        # ── 4. Compute caller-scoped allowed roots ─────────────────────────
        try:
            allowed_roots = _compute_allowed_roots(caller, scope_id)
        except PermissionError as exc:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.UNAUTHORIZED, message=str(exc), retriable=False
                ),
            )

        # ── 5. Resolve and validate the target path ────────────────────────
        raw = Path(path_str)
        if not raw.is_absolute():
            raw = REPO_ROOT / raw

        try:
            resolved = _validate_write_path(raw, allowed_roots)
        except PermissionError as exc:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.UNAUTHORIZED, message=str(exc), retriable=False
                ),
            )

        # ── 6. Atomic write ────────────────────────────────────────────────
        try:
            _write_atomic(resolved, content_bytes)
        except OSError as exc:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.TOOL_ERROR, message=str(exc), retriable=True
                ),
            )

        return Result(
            ok=True,
            data={"path": str(resolved), "bytes_written": len(content_bytes)},
            cost_usd=0.0,
            meta={
                "tool": self.name,
                "latency_ms": 0,
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
                    description="Destination file path (absolute, or relative to repo root)",
                ),
                AdapterParam(
                    name="content",
                    type="str",
                    description="UTF-8 text content to write",
                ),
            ],
            optional=[
                AdapterParam(
                    name="session_id",
                    type="str",
                    description=(
                        "Session/job id scope — required for JOB_RUNNER; "
                        "optional for PA (grants access to its workspace)"
                    ),
                ),
            ],
        )
