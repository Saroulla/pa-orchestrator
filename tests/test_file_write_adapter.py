"""Tests for FileWriteAdapter — Step 9e gate.

All 9 required test cases plus a few extras for edge cases.
Uses tmp_path so no real repo directories are touched.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

import orchestrator.proxy.adapters.file_write as fw_mod
from orchestrator.models import Caller, ErrorCode
from orchestrator.proxy.adapters.file_write import FileWriteAdapter

adapter = FileWriteAdapter()


# ---------------------------------------------------------------------------
# Shared fixture — a mini-repo rooted at tmp_path with REPO_ROOT patched.
# ---------------------------------------------------------------------------

@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a minimal repo layout and redirect REPO_ROOT to tmp_path."""
    monkeypatch.setattr(fw_mod, "REPO_ROOT", tmp_path)

    (tmp_path / "jobs").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "templates").mkdir()
    (tmp_path / "sessions").mkdir()

    return tmp_path


# ---------------------------------------------------------------------------
# 1. PA writes to jobs/foo.md — ok, file contains correct content
# ---------------------------------------------------------------------------

async def test_pa_writes_to_jobs(repo: Path) -> None:
    target = repo / "jobs" / "foo.md"
    result = await adapter.invoke(
        {"path": str(target), "content": "# Hello world", "session_id": None},
        deadline_s=10.0,
        caller=Caller.PA,
    )
    assert result.ok, result.error
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "# Hello world"
    assert result.data["bytes_written"] == len("# Hello world".encode())
    assert result.data["path"] == str(target.resolve())


# ---------------------------------------------------------------------------
# 2. PA writes to arbitrary path outside repo — UNAUTHORIZED
# ---------------------------------------------------------------------------

async def test_pa_unauthorized_arbitrary_path(repo: Path) -> None:
    # Use a path clearly outside tmp_path (its grandparent).
    evil = str(repo.parent.parent / "evil.exe")
    result = await adapter.invoke(
        {"path": evil, "content": "pwned", "session_id": None},
        deadline_s=10.0,
        caller=Caller.PA,
    )
    assert not result.ok
    assert result.error.code == ErrorCode.UNAUTHORIZED


# ---------------------------------------------------------------------------
# 3. CTO_SUBAGENT writes inside its own workspace — ok
# ---------------------------------------------------------------------------

async def test_cto_writes_own_workspace(repo: Path) -> None:
    session_id = "sess-aabb1234"
    workspace = repo / "sessions" / session_id / "workspace"
    workspace.mkdir(parents=True)

    target = workspace / "output.py"
    result = await adapter.invoke(
        {
            "path": str(target),
            "content": "print('hello')",
            "session_id": session_id,
        },
        deadline_s=10.0,
        caller=Caller.CTO_SUBAGENT,
    )
    assert result.ok, result.error
    assert target.read_text(encoding="utf-8") == "print('hello')"


# ---------------------------------------------------------------------------
# 4. CTO_SUBAGENT writes to another session's workspace — UNAUTHORIZED
# ---------------------------------------------------------------------------

async def test_cto_cannot_write_other_session(repo: Path) -> None:
    own_session = "sess-myown1234"
    other_session = "sess-other5678"

    (repo / "sessions" / own_session / "workspace").mkdir(parents=True)
    (repo / "sessions" / other_session / "workspace").mkdir(parents=True)

    other_target = repo / "sessions" / other_session / "workspace" / "evil.txt"
    result = await adapter.invoke(
        {
            "path": str(other_target),
            "content": "evil",
            "session_id": own_session,  # CTO presents its OWN session_id
        },
        deadline_s=10.0,
        caller=Caller.CTO_SUBAGENT,
    )
    assert not result.ok
    assert result.error.code == ErrorCode.UNAUTHORIZED


# ---------------------------------------------------------------------------
# 5. Content exceeding 10 MB — BAD_INPUT
# ---------------------------------------------------------------------------

async def test_oversized_content_rejected(repo: Path) -> None:
    big = "x" * (10 * 1024 * 1024 + 1)  # 10 MiB + 1 byte
    result = await adapter.invoke(
        {"path": str(repo / "jobs" / "big.md"), "content": big, "session_id": None},
        deadline_s=10.0,
        caller=Caller.PA,
    )
    assert not result.ok
    assert result.error.code == ErrorCode.BAD_INPUT
    assert "limit" in result.error.message


# ---------------------------------------------------------------------------
# 6. Path with ".." traversal — UNAUTHORIZED
# ---------------------------------------------------------------------------

async def test_path_traversal_rejected(repo: Path) -> None:
    # Construct a path that uses ".." to escape jobs/ into the repo root.
    # str() preserves the ".." literals; validation must resolve them.
    traversal = str(repo / "jobs" / ".." / "evil.txt")
    result = await adapter.invoke(
        {"path": traversal, "content": "pwned", "session_id": None},
        deadline_s=10.0,
        caller=Caller.PA,
    )
    assert not result.ok
    assert result.error.code == ErrorCode.UNAUTHORIZED


# ---------------------------------------------------------------------------
# 7. Atomic write — verify os.replace is called (not a direct open/write)
# ---------------------------------------------------------------------------

async def test_atomic_write_uses_os_replace(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replace_calls: list[tuple[str, str]] = []
    _real_replace = os.replace

    def _spy_replace(src: str, dst: str) -> None:
        replace_calls.append((src, dst))
        _real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _spy_replace)

    target = repo / "jobs" / "atomic.md"
    result = await adapter.invoke(
        {"path": str(target), "content": "atomic content", "session_id": None},
        deadline_s=10.0,
        caller=Caller.PA,
    )
    assert result.ok, result.error
    assert len(replace_calls) == 1
    # The destination of the replace must be our target file.
    assert Path(replace_calls[0][1]) == target
    assert target.read_text(encoding="utf-8") == "atomic content"


# ---------------------------------------------------------------------------
# 8. PA writes config/interests.md — ok (exact-file allowed root)
# ---------------------------------------------------------------------------

async def test_pa_writes_interests_md(repo: Path) -> None:
    interests = repo / "config" / "interests.md"
    result = await adapter.invoke(
        {
            "path": str(interests),
            "content": "I like AI safety papers.",
            "session_id": None,
        },
        deadline_s=10.0,
        caller=Caller.PA,
    )
    assert result.ok, result.error
    assert interests.read_text(encoding="utf-8") == "I like AI safety papers."


# ---------------------------------------------------------------------------
# 9. PA writes config/interests_other.md — UNAUTHORIZED (exact file only)
# ---------------------------------------------------------------------------

async def test_pa_cannot_write_other_config_file(repo: Path) -> None:
    other = repo / "config" / "interests_other.md"
    result = await adapter.invoke(
        {"path": str(other), "content": "nope", "session_id": None},
        deadline_s=10.0,
        caller=Caller.PA,
    )
    assert not result.ok
    assert result.error.code == ErrorCode.UNAUTHORIZED


# ---------------------------------------------------------------------------
# Extra: relative path resolved against REPO_ROOT
# ---------------------------------------------------------------------------

async def test_relative_path_resolved_to_repo_root(repo: Path) -> None:
    result = await adapter.invoke(
        {"path": "jobs/relative.md", "content": "via relative", "session_id": None},
        deadline_s=10.0,
        caller=Caller.PA,
    )
    assert result.ok, result.error
    assert (repo / "jobs" / "relative.md").read_text(encoding="utf-8") == "via relative"


# ---------------------------------------------------------------------------
# Extra: CTO_SUBAGENT without session_id — UNAUTHORIZED
# ---------------------------------------------------------------------------

async def test_cto_without_session_id_unauthorized(repo: Path) -> None:
    result = await adapter.invoke(
        {"path": str(repo / "jobs" / "x.txt"), "content": "x", "session_id": None},
        deadline_s=10.0,
        caller=Caller.CTO_SUBAGENT,
    )
    assert not result.ok
    assert result.error.code == ErrorCode.UNAUTHORIZED


# ---------------------------------------------------------------------------
# Extra: PA writes into config/templates/ — ok
# ---------------------------------------------------------------------------

async def test_pa_writes_into_templates(repo: Path) -> None:
    tpl = repo / "config" / "templates" / "digest.md.j2"
    result = await adapter.invoke(
        {"path": str(tpl), "content": "{{ title }}", "session_id": None},
        deadline_s=10.0,
        caller=Caller.PA,
    )
    assert result.ok, result.error
    assert tpl.read_text(encoding="utf-8") == "{{ title }}"


# ---------------------------------------------------------------------------
# Extra: adapter creates subdirectory inside workspace automatically
# ---------------------------------------------------------------------------

async def test_cto_creates_subdir_in_workspace(repo: Path) -> None:
    session_id = "sess-subdir12"
    (repo / "sessions" / session_id / "workspace").mkdir(parents=True)

    target = repo / "sessions" / session_id / "workspace" / "sub" / "file.py"
    result = await adapter.invoke(
        {"path": str(target), "content": "# sub", "session_id": session_id},
        deadline_s=10.0,
        caller=Caller.CTO_SUBAGENT,
    )
    assert result.ok, result.error
    assert target.read_text(encoding="utf-8") == "# sub"
