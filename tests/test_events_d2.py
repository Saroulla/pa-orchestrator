"""Unit tests for D2 — system_message inline rendering in events.py."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.events import _format_system_message, _dispatch_one


# ---------------------------------------------------------------------------
# _format_system_message
# ---------------------------------------------------------------------------

def test_format_with_inline_text():
    result = _format_system_message("groq_promoted_to_haiku", {"inline_text": "Groq unavailable — promoted to Haiku."})
    assert result == "[SYSTEM/groq_promoted_to_haiku] Groq unavailable — promoted to Haiku."


def test_format_without_inline_text():
    result = _format_system_message("spawn_cap_exceeded", {})
    assert result == "[SYSTEM/spawn_cap_exceeded]"


def test_format_unknown_type():
    result = _format_system_message("unknown", {"inline_text": "something"})
    assert "[SYSTEM/unknown]" in result


# ---------------------------------------------------------------------------
# _dispatch_one with kind=system_message
# ---------------------------------------------------------------------------

def _make_row(channel: str, message_type: str = "job_complete", inline_text: str = "Job done.") -> dict:
    return {
        "id": 1,
        "session_id": "test-sess-01",
        "channel": channel,
        "kind": "system_message",
        "message_type": message_type,
        "payload": json.dumps({"inline_text": inline_text}),
    }


@pytest.mark.asyncio
async def test_system_message_web_sends_to_ws():
    row = _make_row("web", "groq_promoted_to_haiku", "Groq unavailable.")
    ws_manager = MagicMock()
    ws_manager.send = AsyncMock(return_value=True)
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    with patch("orchestrator.store.mark_event_delivered", new_callable=AsyncMock) as mock_mark:
        await _dispatch_one(db, row, ws_manager, bot=None)

    ws_manager.send.assert_called_once()
    call_args = ws_manager.send.call_args[0]
    assert call_args[0] == "test-sess-01"
    ws_data = call_args[1]
    assert ws_data["event"] == "system_message"
    assert "groq_promoted_to_haiku" in ws_data["data"]["message_type"]
    assert "[SYSTEM/groq_promoted_to_haiku]" in ws_data["data"]["text"]
    mock_mark.assert_called_once_with(db, 1)


@pytest.mark.asyncio
async def test_system_message_inline_prefix_format():
    """The text sent to web includes [SYSTEM/<type>] prefix."""
    row = _make_row("web", "spawn_cap_exceeded", "Spawn cap reached.")
    ws_manager = MagicMock()
    ws_manager.send = AsyncMock(return_value=True)
    db = MagicMock()

    with patch("orchestrator.store.mark_event_delivered", new_callable=AsyncMock):
        await _dispatch_one(db, row, ws_manager, bot=None)

    sent = ws_manager.send.call_args[0][1]
    assert sent["data"]["text"].startswith("[SYSTEM/spawn_cap_exceeded]")


@pytest.mark.asyncio
async def test_system_message_not_delivered_skips_mark():
    """If ws_manager.send returns False, mark_event_delivered is NOT called."""
    row = _make_row("web", "job_complete", "Job done.")
    ws_manager = MagicMock()
    ws_manager.send = AsyncMock(return_value=False)
    db = MagicMock()

    with patch("orchestrator.store.mark_event_delivered", new_callable=AsyncMock) as mock_mark:
        await _dispatch_one(db, row, ws_manager, bot=None)

    mock_mark.assert_not_called()


@pytest.mark.asyncio
async def test_system_message_returns_early_does_not_fall_through():
    """system_message branch returns early — ws_manager not called a second time."""
    row = _make_row("web", "google_quota_warning", "Quota low.")
    ws_manager = MagicMock()
    ws_manager.send = AsyncMock(return_value=True)
    db = MagicMock()

    with patch("orchestrator.store.mark_event_delivered", new_callable=AsyncMock):
        await _dispatch_one(db, row, ws_manager, bot=None)

    assert ws_manager.send.call_count == 1
