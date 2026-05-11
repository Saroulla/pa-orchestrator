"""Unit tests for orchestrator/fsm.py — Step 6 gate."""
import pytest

from orchestrator.models import Channel, Mode
from orchestrator.fsm import transition

WEB = Channel.WEB
TG = Channel.TELEGRAM


# ── valid transitions ──────────────────────────────────────────────────────────

def test_pa_to_pa_noop():
    """Plain message in PA mode → no-op, no notification."""
    mode, msg = transition(Mode.PA, "reason", WEB)
    assert mode == Mode.PA
    assert msg is None


# ── @Desktop stub ──────────────────────────────────────────────────────────────

def test_desktop_stub_from_pa():
    """@Desktop from PA → DESKTOP mode with stub message."""
    mode, msg = transition(Mode.PA, "desktop", WEB)
    assert mode == Mode.DESKTOP
    assert msg == "Coming in Phase 1.2."


# ── @cost does not change mode ─────────────────────────────────────────────────

def test_cost_no_mode_change_from_pa():
    """@cost from PA → stays PA (mode-agnostic no-op)."""
    mode, msg = transition(Mode.PA, "cost", WEB)
    assert mode == Mode.PA
    assert msg is None


# ── DESKTOP + any message → back to PA ────────────────────────────────────────

def test_desktop_reason_returns_to_pa():
    """Any reason message while in DESKTOP → PA with return message."""
    mode, msg = transition(Mode.DESKTOP, "reason", WEB)
    assert mode == Mode.PA
    assert msg == "Returning to PA mode."


def test_desktop_code_returns_to_pa():
    """Code message while in DESKTOP → PA."""
    mode, msg = transition(Mode.DESKTOP, "code", WEB)
    assert mode == Mode.PA
    assert msg == "Returning to PA mode."


def test_desktop_desktop_returns_to_pa():
    """@Desktop while already in DESKTOP → PA (any input exits stub)."""
    mode, msg = transition(Mode.DESKTOP, "desktop", WEB)
    assert mode == Mode.PA
    assert msg == "Returning to PA mode."


def test_desktop_cost_short_circuits():
    """@cost from DESKTOP → cost no-op fires before DESKTOP exit (implementation detail)."""
    mode, msg = transition(Mode.DESKTOP, "cost", WEB)
    # cost no-op takes precedence over DESKTOP exit
    assert mode == Mode.DESKTOP
    assert msg is None


# ── channel is accepted but does not change logic ─────────────────────────────

def test_channel_does_not_affect_transition():
    """Same transition produces same result on both channels."""
    mode_web, msg_web = transition(Mode.PA, "desktop", WEB)
    mode_tg, msg_tg = transition(Mode.PA, "desktop", TG)
    assert mode_web == mode_tg
    assert msg_web == msg_tg
