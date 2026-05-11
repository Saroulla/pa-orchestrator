"""Step 6 — Mode FSM per (session_id, channel). Implemented by Sonnet in Wave W3."""
from __future__ import annotations

from orchestrator.models import Channel, Mode


def transition(
    current_mode: Mode,
    intent_kind: str,
    channel: Channel,
) -> tuple[Mode, str | None]:
    """Return (new_mode, notification_message | None).

    intent_kind is normally Intent.kind, except when the dispatcher detects
    meta_command=="cost" — in that case pass "cost" so FSM stays a no-op.

    Channel is accepted for future per-channel state differentiation but does
    not affect transition logic in Phase 1.
    """
    # @cost is handled inline; FSM must not trigger a mode change for it.
    if intent_kind == "cost":
        return (current_mode, None)

    # Any message while in DESKTOP stub returns immediately to PA.
    if current_mode == Mode.DESKTOP:
        return (Mode.PA, "Returning to PA mode.")

    # @Desktop from any non-DESKTOP mode → stub.
    if intent_kind == "desktop":
        return (Mode.DESKTOP, "Coming in Phase 1.2.")

    # All remaining cases are no-ops (e.g. @PA while already in PA).
    return (current_mode, None)
