"""Tests for orchestrator/maker/persona.py (step C2)."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

import orchestrator.maker.persona as persona_mod
from orchestrator.maker.persona import _reset, get_persona, start_watcher


@pytest.fixture(autouse=True)
def reset_persona_state():
    """Reset module-level state before and after every test."""
    _reset()
    yield
    _reset()


# ---------------------------------------------------------------------------
# Initial load
# ---------------------------------------------------------------------------

def test_initial_load(tmp_path: Path):
    p = tmp_path / "persona.md"
    p.write_text("Hello from persona", encoding="utf-8")
    obs = start_watcher(p)
    try:
        assert get_persona() == "Hello from persona"
    finally:
        obs.stop()
        obs.join()


def test_initial_load_multiline(tmp_path: Path):
    content = "# MAKER persona\n\n## Identity\nName: MAKER\n"
    p = tmp_path / "persona.md"
    p.write_text(content, encoding="utf-8")
    obs = start_watcher(p)
    try:
        assert get_persona() == content
    finally:
        obs.stop()
        obs.join()


def test_start_watcher_missing_file_raises(tmp_path: Path):
    p = tmp_path / "nonexistent.md"
    with pytest.raises(Exception):
        start_watcher(p)


# ---------------------------------------------------------------------------
# Not initialised
# ---------------------------------------------------------------------------

def test_get_persona_before_start_raises():
    with pytest.raises(RuntimeError, match="not initialised"):
        get_persona()


# ---------------------------------------------------------------------------
# Hot-reload
# ---------------------------------------------------------------------------

def test_persona_hot_reload(tmp_path: Path):
    p = tmp_path / "persona.md"
    p.write_text("original content", encoding="utf-8")
    obs = start_watcher(p)
    try:
        assert get_persona() == "original content"
        p.write_text("updated content", encoding="utf-8")
        # debounce is 0.5 s; wait generously
        time.sleep(1.2)
        assert get_persona() == "updated content"
    finally:
        obs.stop()
        obs.join()


def test_persona_reload_failure_keeps_previous(tmp_path: Path):
    p = tmp_path / "persona.md"
    p.write_text("good content", encoding="utf-8")
    obs = start_watcher(p)
    try:
        assert get_persona() == "good content"
        # Simulate a reload error by patching _load to raise
        original_load = persona_mod._load
        persona_mod._load = lambda path: (_ for _ in ()).throw(OSError("disk error"))
        try:
            # Trigger a reload by touching the file
            p.write_text("new content", encoding="utf-8")
            time.sleep(1.2)
            # Should still return the old content
            assert get_persona() == "good content"
        finally:
            persona_mod._load = original_load
    finally:
        obs.stop()
        obs.join()


# ---------------------------------------------------------------------------
# _reset isolation
# ---------------------------------------------------------------------------

def test_reset_clears_state(tmp_path: Path):
    p = tmp_path / "persona.md"
    p.write_text("content", encoding="utf-8")
    obs = start_watcher(p)
    obs.stop()
    obs.join()
    assert get_persona() == "content"
    _reset()
    with pytest.raises(RuntimeError):
        get_persona()
