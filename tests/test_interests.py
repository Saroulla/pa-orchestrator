"""Tests for orchestrator/interests.py — Step 25 gate."""
from __future__ import annotations

import pytest

from orchestrator.models import Caller, Mode
from orchestrator.parser import parse

SESSION = "test-sess-01"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_interests_path(monkeypatch, path):
    import orchestrator.interests as mod
    monkeypatch.setattr(mod, "INTERESTS_PATH", path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_read_interests_missing(tmp_path, monkeypatch):
    _patch_interests_path(monkeypatch, tmp_path / "interests.md")
    from orchestrator.interests import read_interests
    assert read_interests() == ""


def test_update_interests_creates_file(tmp_path, monkeypatch):
    target = tmp_path / "interests.md"
    _patch_interests_path(monkeypatch, target)
    from orchestrator.interests import update_interests
    update_interests("Python async patterns")
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert "- Python async patterns" in content


def test_update_interests_appends(tmp_path, monkeypatch):
    target = tmp_path / "interests.md"
    _patch_interests_path(monkeypatch, target)
    from orchestrator.interests import update_interests
    update_interests("First interest")
    update_interests("Second interest")
    content = target.read_text(encoding="utf-8")
    assert "- First interest" in content
    assert "- Second interest" in content


def test_build_interests_context_empty(tmp_path, monkeypatch):
    _patch_interests_path(monkeypatch, tmp_path / "interests.md")
    from orchestrator.interests import build_interests_context
    assert build_interests_context() == ""


def test_build_interests_context_header_only(tmp_path, monkeypatch):
    """File with only the comment header (no bullets) returns empty string."""
    target = tmp_path / "interests.md"
    target.write_text(
        "# Interest Profile\n\n"
        "<!-- PA reads this file as context for all responses.\n"
        "     Add entries manually or use @remember <topic> in chat. -->\n",
        encoding="utf-8",
    )
    _patch_interests_path(monkeypatch, target)
    from orchestrator.interests import build_interests_context
    assert build_interests_context() == ""


def test_build_interests_context_with_content(tmp_path, monkeypatch):
    target = tmp_path / "interests.md"
    _patch_interests_path(monkeypatch, target)
    from orchestrator.interests import update_interests, build_interests_context
    update_interests("HN tech news")
    result = build_interests_context()
    assert "## User Interests" in result
    assert "HN tech news" in result


def test_parser_remember_command():
    intent = parse("@remember HN tech news", SESSION, Mode.PA, Caller.PA)
    assert intent.payload["meta_command"] == "remember_interest"
    assert intent.payload["text"] == "HN tech news"


def test_parser_remember_no_args():
    intent = parse("@remember", SESSION, Mode.PA, Caller.PA)
    assert intent.payload["meta_command"] == "remember_interest"
    assert intent.payload["text"] == ""
