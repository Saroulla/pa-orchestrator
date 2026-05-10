"""Tests for orchestrator/maker/skills.py (Step C3)."""
import shutil
import time
from pathlib import Path

import pytest

from orchestrator.maker import skills as skills_mod
from orchestrator.maker.skills import SkillNotFound, get_skill_index, load_skill, start_skills_watcher

# Paths to real example files
EXAMPLE_DIR = Path("config/maker/skills")
EXAMPLE_INDEX = EXAMPLE_DIR / "index.yaml.example"


def _setup_tmp_skills(tmp_path: Path) -> Path:
    """Copy example index.yaml + all .md files into tmp_path; return index path."""
    # Copy index.yaml.example as index.yaml
    index_src = Path("config/maker/skills/index.yaml.example")
    index_dst = tmp_path / "index.yaml"
    shutil.copy(index_src, index_dst)
    # Copy all skill .md files
    for md in Path("config/maker/skills").glob("*.md"):
        shutil.copy(md, tmp_path / md.name)
    return index_dst


@pytest.fixture(autouse=True)
def reset_skills_state():
    """Reset module state before and after each test."""
    skills_mod._reset()
    yield
    skills_mod._reset()


@pytest.fixture()
def watcher_and_index(tmp_path):
    """Start skills watcher in tmp_path, yield (observer, index_path); stop on teardown."""
    index_path = _setup_tmp_skills(tmp_path)
    observer = start_skills_watcher(index_path)
    yield observer, index_path
    observer.stop()
    observer.join(timeout=2.0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_get_skill_index_returns_list(watcher_and_index):
    """After start_skills_watcher, get_skill_index() returns a non-empty list of dicts with 'name'."""
    observer, _ = watcher_and_index
    index = get_skill_index()
    assert isinstance(index, list)
    assert len(index) > 0
    for entry in index:
        assert isinstance(entry, dict)
        assert "name" in entry


def test_load_skill_returns_md_content(watcher_and_index):
    """load_skill(name) returns a non-empty string for a known skill."""
    observer, _ = watcher_and_index
    index = get_skill_index()
    first_name = index[0]["name"]
    content = load_skill(first_name)
    assert isinstance(content, str)
    assert len(content) > 0


def test_load_skill_not_found_raises(watcher_and_index):
    """load_skill('nonexistent-skill') raises SkillNotFound."""
    observer, _ = watcher_and_index
    with pytest.raises(SkillNotFound):
        load_skill("nonexistent-skill")


def test_skill_index_hot_reload(tmp_path):
    """After writing a new skill entry, get_skill_index() reflects it within 1.2s."""
    index_path = _setup_tmp_skills(tmp_path)
    observer = start_skills_watcher(index_path)
    try:
        original = get_skill_index()
        original_names = {e["name"] for e in original}

        # Append a new skill entry to the YAML file
        with open(index_path, "a", encoding="utf-8") as f:
            f.write(
                "\n  - name: hot-reload-test-skill\n"
                "    file: research-and-summarise.md\n"
                '    when_to_use: "Hot reload test."\n'
                "    inputs: []\n"
                "    output_kind: test\n"
            )

        # Poll up to 1.2s for the new skill to appear
        deadline = time.monotonic() + 1.2
        found = False
        while time.monotonic() < deadline:
            current = get_skill_index()
            if any(e["name"] == "hot-reload-test-skill" for e in current):
                found = True
                break
            time.sleep(0.05)

        assert found, "Hot-reloaded skill did not appear in index within 1.2s"
    finally:
        observer.stop()
        observer.join(timeout=2.0)


def test_invalid_yaml_keeps_last_good(tmp_path):
    """Writing invalid YAML does not crash; last valid index is preserved."""
    index_path = _setup_tmp_skills(tmp_path)
    observer = start_skills_watcher(index_path)
    try:
        valid_index = get_skill_index()
        assert len(valid_index) > 0

        # Overwrite with garbage YAML
        index_path.write_text("::invalid yaml::", encoding="utf-8")

        # Wait for watcher to attempt reload
        time.sleep(0.8)

        # Index must still be the last valid one
        current = get_skill_index()
        assert current == valid_index, (
            f"Expected last-good index {[e['name'] for e in valid_index]}, "
            f"got {[e.get('name') for e in current]}"
        )
    finally:
        observer.stop()
        observer.join(timeout=2.0)
