"""Tests for orchestrator/maker/tools.py — Step C4."""
import importlib
import shutil
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EXAMPLE_YAML = Path(__file__).parent.parent / "config" / "maker" / "tools.yaml.example"

# We must reset the module-level singletons between tests.
import orchestrator.maker.tools as _tools_mod


def _reset_module():
    """Reset the module singleton so tests are independent."""
    import orchestrator.maker.tools as m
    with m._lock:
        m._registry = None


def _start(tmp_yaml: Path):
    """Copy example yaml to tmp path, start watcher, return observer."""
    _reset_module()
    import orchestrator.maker.tools as m
    observer = m.start_tools_watcher(tmp_yaml)
    return observer


def _stop(observer):
    observer.stop()
    observer.join(timeout=2)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_yaml(tmp_path):
    """Copy the example tools.yaml to a temp dir and return the path."""
    dst = tmp_path / "tools.yaml"
    shutil.copy(EXAMPLE_YAML, dst)
    return dst


@pytest.fixture()
def running_registry(tmp_yaml):
    """Start the watcher and yield (registry, observer). Stop on teardown."""
    import orchestrator.maker.tools as m
    observer = _start(tmp_yaml)
    yield m.get_registry(), observer, tmp_yaml
    _stop(observer)
    _reset_module()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_registry_list_returns_all_tools(running_registry):
    registry, _obs, _path = running_registry
    items = registry.list()
    names = {i["name"] for i in items}
    expected = {
        "google_cse",
        "http_fetch",
        "article_extract",
        "playwright_web",
        "pdf_extract",
        "email_send",
        "file_write",
    }
    assert expected.issubset(names)
    for item in items:
        assert "name" in item
        assert "enabled" in item
        assert "defaults" in item


def test_registry_is_enabled_true(running_registry):
    registry, _obs, _path = running_registry
    # google_cse is enabled: true in the example yaml
    assert registry.is_enabled("google_cse") is True


def test_registry_is_enabled_false(tmp_path, tmp_yaml):
    """Write yaml with one tool disabled, reload, check is_enabled returns False."""
    import orchestrator.maker.tools as m
    _reset_module()

    disabled_yaml = (
        "tools:\n"
        "  google_cse:\n"
        "    enabled: false\n"
        "    default_n: 10\n"
    )
    dst = tmp_path / "tools2.yaml"
    dst.write_text(disabled_yaml, encoding="utf-8")

    observer = m.start_tools_watcher(dst)
    try:
        assert m.get_registry().is_enabled("google_cse") is False
    finally:
        _stop(observer)
        _reset_module()


def test_registry_get_defaults(running_registry):
    registry, _obs, _path = running_registry
    defaults = registry.get_defaults("http_fetch")
    assert "default_timeout_s" in defaults
    assert defaults["default_timeout_s"] == 15
    # 'enabled' must NOT appear in defaults
    assert "enabled" not in defaults


def test_registry_tool_not_found_raises(running_registry):
    from orchestrator.maker.tools import ToolNotFound
    registry, _obs, _path = running_registry
    with pytest.raises(ToolNotFound):
        registry.is_enabled("nonexistent_tool_xyz")


def test_hot_reload_disables_tool(tmp_yaml):
    """Flip a tool to disabled on disk; watcher should reload within 1.2s."""
    import orchestrator.maker.tools as m
    _reset_module()
    observer = m.start_tools_watcher(tmp_yaml)
    try:
        # Confirm enabled before change
        assert m.get_registry().is_enabled("google_cse") is True

        # Overwrite with google_cse disabled
        new_content = (
            "tools:\n"
            "  google_cse:\n"
            "    enabled: false\n"
            "    default_n: 10\n"
            "    default_safe: 'off'\n"
        )
        tmp_yaml.write_text(new_content, encoding="utf-8")

        # Wait up to 1.2s for the debounce + reload
        deadline = time.monotonic() + 1.2
        while time.monotonic() < deadline:
            if not m.get_registry().is_enabled("google_cse"):
                break
            time.sleep(0.05)

        assert m.get_registry().is_enabled("google_cse") is False
    finally:
        _stop(observer)
        _reset_module()


def test_invalid_yaml_keeps_last_good(tmp_yaml):
    """Write invalid YAML to file; after debounce the registry is still the last good."""
    import orchestrator.maker.tools as m
    _reset_module()
    observer = m.start_tools_watcher(tmp_yaml)
    try:
        good_list = m.get_registry().list()
        good_names = {i["name"] for i in good_list}
        assert len(good_names) > 0

        # Write garbage — truly unparseable YAML (unclosed flow sequence)
        tmp_yaml.write_text("{unclosed: [", encoding="utf-8")

        # Wait past the debounce period
        time.sleep(0.8)

        # Registry must still be intact
        still_list = m.get_registry().list()
        assert {i["name"] for i in still_list} == good_names
    finally:
        _stop(observer)
        _reset_module()
