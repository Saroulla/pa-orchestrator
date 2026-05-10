"""Unit tests for FileReadAdapter — Step 9d gate.

All seven required test cases are covered:
1. PA reads file inside config/                  → ok
2. PA reads file with ../../ traversal            → UNAUTHORIZED
3. CTO_SUBAGENT reads its own workspace           → ok
4. CTO_SUBAGENT reads another session's workspace → UNAUTHORIZED
5. File does not exist                            → TOOL_ERROR
6. File over 50 MB (stat mocked)                 → BAD_INPUT
7. Symlink pointing outside allowed root          → UNAUTHORIZED
"""
import asyncio
import os
import unittest.mock as mock
from pathlib import Path

import pytest

from orchestrator.models import Caller, ErrorCode
from orchestrator.proxy.adapters.file_read import FileReadAdapter

OWN_SESSION = "test-sess-01"
OTHER_SESSION = "test-sess-02"


# ── helpers ───────────────────────────────────────────────────────────────────

def run(coro):
    return asyncio.run(coro)


def adapter(tmp_path: Path) -> FileReadAdapter:
    return FileReadAdapter(repo_root=tmp_path)


def invoke(tmp_path: Path, path: str, caller: Caller, session_id: str = OWN_SESSION):
    return run(
        adapter(tmp_path).invoke(
            {"path": path, "session_id": session_id},
            deadline_s=30.0,
            caller=caller,
        )
    )


# ── 1. PA reads file inside config/ ──────────────────────────────────────────

def test_pa_reads_config_file(tmp_path: Path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    target = cfg / "guardrails.yaml"
    target.write_text("key: value", encoding="utf-8")

    result = invoke(tmp_path, str(target), Caller.PA)

    assert result.ok
    assert result.data["content"] == "key: value"
    assert "path" in result.data
    assert result.meta["tool"] == "file_read"


def test_pa_reads_file_in_jobs(tmp_path: Path):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    target = jobs / "morning.md"
    target.write_text("# Morning job")

    result = invoke(tmp_path, str(target), Caller.PA)

    assert result.ok
    assert result.data["content"] == "# Morning job"


# ── 2. Traversal attempt → UNAUTHORIZED ───────────────────────────────────────

def test_traversal_rejected(tmp_path: Path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    # File actually exists at the traversal destination — authorization is checked first
    evil = tmp_path / "evil.txt"
    evil.write_text("evil content")

    # config/../evil.txt resolves to tmp_path/evil.txt — outside any allowed root
    traversal = str(cfg / ".." / "evil.txt")
    result = invoke(tmp_path, traversal, Caller.PA)

    assert not result.ok
    assert result.error.code == ErrorCode.UNAUTHORIZED


def test_absolute_path_outside_roots_rejected(tmp_path: Path):
    # Absolute path to a system file — definitively outside allowed roots
    result = invoke(tmp_path, r"C:\Windows\System32\drivers\etc\hosts", Caller.PA)

    assert not result.ok
    assert result.error.code == ErrorCode.UNAUTHORIZED


# ── 3. CTO_SUBAGENT reads inside its own workspace ───────────────────────────

def test_cto_reads_own_workspace(tmp_path: Path):
    workspace = tmp_path / "sessions" / OWN_SESSION / "workspace"
    workspace.mkdir(parents=True)
    target = workspace / "code.py"
    target.write_text("print('hello')")

    result = invoke(tmp_path, str(target), Caller.CTO_SUBAGENT, session_id=OWN_SESSION)

    assert result.ok
    assert result.data["content"] == "print('hello')"


# ── 4. CTO_SUBAGENT reads another session's workspace → UNAUTHORIZED ──────────

def test_cto_cannot_read_other_session(tmp_path: Path):
    # Victim's workspace
    other_ws = tmp_path / "sessions" / OTHER_SESSION / "workspace"
    other_ws.mkdir(parents=True)
    (other_ws / "secret.py").write_text("secret")

    # CTO identifies as OWN_SESSION but tries to read OTHER_SESSION's file
    result = invoke(
        tmp_path,
        str(other_ws / "secret.py"),
        Caller.CTO_SUBAGENT,
        session_id=OWN_SESSION,
    )

    assert not result.ok
    assert result.error.code == ErrorCode.UNAUTHORIZED


def test_cto_missing_session_id_rejected(tmp_path: Path):
    result = run(
        adapter(tmp_path).invoke(
            {"path": str(tmp_path / "config" / "x.txt")},  # no session_id
            30.0,
            Caller.CTO_SUBAGENT,
        )
    )

    assert not result.ok
    assert result.error.code == ErrorCode.BAD_INPUT


# ── 5. File does not exist → TOOL_ERROR ──────────────────────────────────────

def test_file_not_found(tmp_path: Path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    missing = cfg / "nonexistent.yaml"  # path is inside allowed root, but absent

    result = invoke(tmp_path, str(missing), Caller.PA)

    assert not result.ok
    assert result.error.code == ErrorCode.TOOL_ERROR


# ── 6. File over 50 MB → BAD_INPUT ───────────────────────────────────────────

def test_large_file_rejected(tmp_path: Path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    target = cfg / "large.bin"
    target.write_text("small real content")  # real file; size is mocked below

    big_stat = mock.MagicMock()
    big_stat.st_size = FileReadAdapter.MAX_READ_BYTES + 1

    # Patch Path.stat at the class level; _resolve_and_check never calls stat(),
    # so path validation still executes against real paths.
    with mock.patch("pathlib.Path.stat", return_value=big_stat):
        result = invoke(tmp_path, str(target), Caller.PA)

    assert not result.ok
    assert result.error.code == ErrorCode.BAD_INPUT


def test_file_exactly_at_limit_passes(tmp_path: Path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    target = cfg / "edge.bin"
    target.write_text("x")

    at_limit_stat = mock.MagicMock()
    at_limit_stat.st_size = FileReadAdapter.MAX_READ_BYTES  # exactly at limit → allowed

    with mock.patch("pathlib.Path.stat", return_value=at_limit_stat):
        # read_text is called after stat; mock stat but let read_text run normally
        with mock.patch.object(Path, "read_text", return_value="x"):
            result = invoke(tmp_path, str(target), Caller.PA)

    assert result.ok


# ── 7. Symlink pointing outside allowed root → UNAUTHORIZED ───────────────────

def test_symlink_escape_rejected(tmp_path: Path):
    """
    A symlink inside the allowed root that resolves (via os.path.realpath) to a
    location outside the allowed roots must be rejected with UNAUTHORIZED.

    On systems where symlinks can be created (Linux, macOS, Windows+DevMode) we
    create a real symlink.  On Windows without Developer Mode we mock realpath to
    simulate the same code path — the security property under test lives in the
    adapter, not in the OS.
    """
    cfg = tmp_path / "config"
    cfg.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside content")
    link = cfg / "evil_link.txt"

    try:
        link.symlink_to(outside)
        # Real symlink: os.path.realpath follows it → outside tmp_path/config/
        result = invoke(tmp_path, str(link), Caller.PA)
    except OSError:
        # No symlink privilege (Windows without Developer Mode).
        # Create a regular file at the link path and override realpath so the
        # adapter sees the same resolved-outside-root scenario.
        link.write_text("placeholder")
        original_rp = os.path.realpath

        def fake_realpath(path, **kw):
            if os.path.normcase(str(path)) == os.path.normcase(str(link)):
                return str(outside)
            return original_rp(path, **kw)

        with mock.patch("os.path.realpath", side_effect=fake_realpath):
            result = invoke(tmp_path, str(link), Caller.PA)

    assert not result.ok
    assert result.error.code == ErrorCode.UNAUTHORIZED


# ── meta checks ───────────────────────────────────────────────────────────────

def test_meta_keys_present_on_success(tmp_path: Path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "x.txt").write_text("hi")

    result = invoke(tmp_path, str(cfg / "x.txt"), Caller.PA)

    assert result.ok
    for key in ("tool", "latency_ms", "tokens_in", "tokens_out"):
        assert key in result.meta


def test_meta_keys_present_on_failure(tmp_path: Path):
    result = invoke(tmp_path, r"C:\Windows\win.ini", Caller.PA)

    assert not result.ok
    for key in ("tool", "latency_ms", "tokens_in", "tokens_out"):
        assert key in result.meta


def test_job_runner_reads_config(tmp_path: Path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "template.md").write_text("template content")

    result = invoke(tmp_path, str(cfg / "template.md"), Caller.JOB_RUNNER)

    assert result.ok


def test_health_returns_true(tmp_path: Path):
    assert run(adapter(tmp_path).health()) is True


def test_manifest_shape():
    a = FileReadAdapter()
    m = a.manifest
    required_names = {p.name for p in m.required}
    optional_names = {p.name for p in m.optional}
    assert "path" in required_names
    assert "session_id" in optional_names
