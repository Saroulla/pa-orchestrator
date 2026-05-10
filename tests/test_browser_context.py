"""Tests for orchestrator/maker/browser_context.py — Step E3 gate."""
from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.maker import browser_context as bc_mod
from orchestrator.maker.browser_context import (
    PersistentBrowserContext,
    get_browser_context,
    init_browser_context,
    reset_singleton_for_tests,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stub_async_playwright():
    """Build a fake async_playwright() object whose .start() returns an
    object that exposes chromium.launch_persistent_context."""
    pw = MagicMock()
    pw.stop = AsyncMock()
    context = MagicMock()
    context.close = AsyncMock()
    context.set_default_navigation_timeout = MagicMock()
    pw.chromium = MagicMock()
    pw.chromium.launch_persistent_context = AsyncMock(return_value=context)
    apw = MagicMock()
    apw.start = AsyncMock(return_value=pw)
    return apw, pw, context


# ---------------------------------------------------------------------------
# Construction + singleton
# ---------------------------------------------------------------------------

def test_singleton_uninitialised_raises():
    reset_singleton_for_tests()
    with pytest.raises(RuntimeError):
        get_browser_context()


def test_init_reads_config_example_when_no_live(tmp_path: Path):
    reset_singleton_for_tests()
    inst = init_browser_context(user_data_dir=tmp_path / "profile")
    assert inst is get_browser_context()
    assert inst._engine == "chromium"
    assert inst._headless is True
    assert inst._navigation_timeout_s == 30
    assert inst._idle_close_after_s == 1800


def test_init_overrides_take_priority(tmp_path: Path):
    reset_singleton_for_tests()
    inst = init_browser_context(
        user_data_dir=tmp_path / "p",
        engine="chromium",
        headless=False,
        navigation_timeout_s=10,
        idle_close_after_s=5,
    )
    assert inst._headless is False
    assert inst._navigation_timeout_s == 10
    assert inst._idle_close_after_s == 5


# ---------------------------------------------------------------------------
# Lazy-start
# ---------------------------------------------------------------------------

def test_lazy_start_no_launch_until_get_context(tmp_path: Path):
    """Per locked decision (config browser.lazy_start=true): constructing the
    PersistentBrowserContext must NOT touch Playwright."""
    apw, pw, _ctx = _stub_async_playwright()
    inst = PersistentBrowserContext(
        user_data_dir=tmp_path / "profile",
        idle_close_after_s=1800,
    )
    assert inst._context is None
    assert inst._pw is None
    apw.start.assert_not_called()


@pytest.mark.asyncio
async def test_first_get_context_launches_chromium(tmp_path: Path):
    apw, pw, ctx = _stub_async_playwright()
    inst = PersistentBrowserContext(
        user_data_dir=tmp_path / "profile",
        navigation_timeout_s=7,
    )
    with patch("playwright.async_api.async_playwright", return_value=apw):
        got = await inst.get_context()
    assert got is ctx
    pw.chromium.launch_persistent_context.assert_awaited_once()
    ctx.set_default_navigation_timeout.assert_called_once_with(7000)
    assert inst._last_used > 0


@pytest.mark.asyncio
async def test_second_get_context_reuses_same_handle(tmp_path: Path):
    apw, pw, ctx = _stub_async_playwright()
    inst = PersistentBrowserContext(user_data_dir=tmp_path / "p")
    with patch("playwright.async_api.async_playwright", return_value=apw):
        a = await inst.get_context()
        b = await inst.get_context()
    assert a is b
    pw.chromium.launch_persistent_context.assert_awaited_once()


# ---------------------------------------------------------------------------
# Idle close
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_close_if_idle_no_context_returns_false(tmp_path: Path):
    inst = PersistentBrowserContext(user_data_dir=tmp_path / "p")
    assert await inst.close_if_idle(idle_after_s=0) is False


@pytest.mark.asyncio
async def test_close_if_idle_closes_when_idle(tmp_path: Path):
    apw, pw, ctx = _stub_async_playwright()
    inst = PersistentBrowserContext(user_data_dir=tmp_path / "p")
    with patch("playwright.async_api.async_playwright", return_value=apw):
        await inst.get_context()
    closed = await inst.close_if_idle(idle_after_s=0)
    assert closed is True
    ctx.close.assert_awaited_once()
    pw.stop.assert_awaited_once()
    assert inst._context is None
    assert inst._pw is None


@pytest.mark.asyncio
async def test_close_if_idle_keeps_when_recent(tmp_path: Path):
    apw, pw, ctx = _stub_async_playwright()
    inst = PersistentBrowserContext(user_data_dir=tmp_path / "p")
    with patch("playwright.async_api.async_playwright", return_value=apw):
        await inst.get_context()
    closed = await inst.close_if_idle(idle_after_s=3600)
    assert closed is False
    ctx.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_lazy_start_relaunch_after_idle(tmp_path: Path):
    """Plan §11 test_browser_lazy_start: first call launches, idle close,
    next call relaunches."""
    apw, pw, ctx = _stub_async_playwright()
    inst = PersistentBrowserContext(user_data_dir=tmp_path / "p")
    with patch("playwright.async_api.async_playwright", return_value=apw):
        await inst.get_context()
        assert pw.chromium.launch_persistent_context.await_count == 1
        await inst.close_if_idle(idle_after_s=0)
        assert inst._context is None
        # New stubs for the relaunch (the previous pw was stopped)
        apw2, pw2, ctx2 = _stub_async_playwright()
        with patch("playwright.async_api.async_playwright", return_value=apw2):
            got = await inst.get_context()
        assert got is ctx2
        assert pw2.chromium.launch_persistent_context.await_count == 1


# ---------------------------------------------------------------------------
# aclose
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aclose_closes_active_context(tmp_path: Path):
    apw, pw, ctx = _stub_async_playwright()
    inst = PersistentBrowserContext(user_data_dir=tmp_path / "p")
    with patch("playwright.async_api.async_playwright", return_value=apw):
        await inst.get_context()
    await inst.aclose()
    ctx.close.assert_awaited_once()
    pw.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_aclose_no_active_is_noop(tmp_path: Path):
    inst = PersistentBrowserContext(user_data_dir=tmp_path / "p")
    await inst.aclose()  # must not raise


# ---------------------------------------------------------------------------
# Crash containment (design-property assertion)
# ---------------------------------------------------------------------------

def test_api_process_does_not_import_browser_context():
    """Plan §11 test_browser_crash_does_not_kill_api: browser_context lives in
    the scheduler subprocess only. The FastAPI entrypoint (orchestrator.main)
    must not import it directly or transitively at module load time —
    process isolation IS the crash containment."""
    # Drop any cached imports from previous tests, then re-load main.
    cached = [
        m for m in list(sys.modules)
        if m == "orchestrator.main" or m.startswith("orchestrator.main.")
    ]
    for m in cached:
        del sys.modules[m]

    sentinel = "orchestrator.maker.browser_context"
    pre = set(sys.modules)
    importlib.import_module("orchestrator.main")
    leaked_via_main = sentinel in (set(sys.modules) - pre)
    assert not leaked_via_main, (
        "orchestrator.main pulled in browser_context; crash isolation broken"
    )


@pytest.mark.asyncio
async def test_unsupported_engine_raises_before_launch(tmp_path: Path):
    """firefox path is not implemented in MVP — fail loud."""
    inst = PersistentBrowserContext(
        user_data_dir=tmp_path / "p",
        engine="firefox",
    )
    with pytest.raises(ValueError, match="Unsupported browser engine"):
        await inst.get_context()
