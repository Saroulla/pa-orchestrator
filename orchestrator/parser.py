"""Step 6 — Intent parser + @command detection. Implemented by Sonnet in Wave W3."""
from __future__ import annotations

from orchestrator.models import Caller, Intent, Mode

_MODE_KIND: dict[Mode, str] = {
    Mode.PA: "reason",
    Mode.DESKTOP: "reason",
}

_DEFAULT_DEADLINE_S = 30.0


def parse(
    text: str,
    session_id: str,
    mode: Mode,
    caller: Caller = Caller.PA,
) -> Intent:
    """Parse raw input text into an Intent.

    Only the very first whitespace-delimited token is examined for @ commands.
    Mid-message @ is always literal.  Leading \\@ escapes the command prefix.
    """
    parts = text.split(None, 1)
    first = parts[0] if parts else ""
    remainder = parts[1] if len(parts) > 1 else ""

    kind: str
    payload: dict

    if first.startswith("\\@"):
        # Escaped @: strip the leading backslash, treat whole thing as literal text.
        stripped_first = first[1:]
        literal_text = f"{stripped_first} {remainder}" if remainder else stripped_first
        kind = _MODE_KIND[mode]
        payload = {"text": literal_text}

    elif first == "@PA":
        kind = "reason"
        payload = {"text": remainder}

    elif first == "@Desktop":
        kind = "desktop"
        payload = {"text": remainder}

    elif first == "@cost":
        # Handled inline by dispatcher; meta_command signals no FSM transition.
        kind = "reason"
        payload = {"text": remainder, "meta_command": "cost"}

    elif first == "@remember":
        kind = "reason"
        payload = {"text": remainder, "meta_command": "remember_interest"}

    elif first == "@rebuild-plan":
        kind = "file_write"
        payload = {"text": remainder, "meta_command": "rebuild_plan"}

    elif first == "@goal":
        kind = "goal"
        payload = {"text": remainder}

    else:
        # Not an @command (or @ appears mid-message — treat as literal).
        kind = _MODE_KIND[mode]
        payload = {"text": text}

    return Intent(
        kind=kind,
        payload=payload,
        session_id=session_id,
        mode=mode,
        caller=caller,
        deadline_s=_DEFAULT_DEADLINE_S,
        attempt=0,
    )
