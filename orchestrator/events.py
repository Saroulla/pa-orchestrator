"""Step 11 — Cross-process events table consumer.

Polls SQLite ``events`` table every 500ms; for each undelivered row dispatches
to either the in-process WebSocket manager (web channel) or the Telegram bot
(telegram channel). Marks the row delivered on success.

Cancellation: cooperative — ``asyncio.CancelledError`` exits the loop cleanly.
Failures (DB or transport) are logged and left undelivered for the next tick.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiosqlite

from orchestrator import store

logger = logging.getLogger(__name__)

_POLL_INTERVAL_S = 0.5
_BATCH_LIMIT = 50


def _format_telegram(kind: str, payload: dict) -> str:
    if kind == "job_complete":
        summary = payload.get("summary", "")
        cost = payload.get("cost_usd")
        msg = "✓ Job complete"
        if summary:
            msg += f": {summary}"
        if cost is not None:
            msg += f" (${cost:.4f})"
        return msg
    text = payload.get("text") if isinstance(payload, dict) else None
    return text if text else json.dumps(payload)


async def events_consumer(
    db: aiosqlite.Connection,
    ws_manager: Any,
    bot: Any | None,
) -> None:
    try:
        while True:
            await asyncio.sleep(_POLL_INTERVAL_S)
            try:
                rows = await store.get_undelivered_events(db, limit=_BATCH_LIMIT)
            except Exception as exc:
                logger.error("events_consumer: get_undelivered_events failed: %s", exc)
                continue

            for row in rows:
                try:
                    await _dispatch_one(db, row, ws_manager, bot)
                except Exception as exc:
                    logger.error("events_consumer: dispatch failed for row %s: %s", row.get("id"), exc)
    except asyncio.CancelledError:
        return


async def _dispatch_one(
    db: aiosqlite.Connection,
    row: dict,
    ws_manager: Any,
    bot: Any | None,
) -> None:
    channel = row.get("channel")
    session_id = row.get("session_id")

    try:
        payload = json.loads(row["payload"])
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {"raw": row.get("payload")}

    delivered = False

    if channel == "web":
        ws_payload = {"event": row["kind"], "data": payload}
        delivered = await ws_manager.send(session_id, ws_payload)
    elif channel == "telegram":
        if bot is not None:
            text = _format_telegram(row["kind"], payload)
            sess = await store.get_session(db, session_id)
            chat_id = sess.get("telegram_chat_id") if sess else None
            if chat_id:
                from orchestrator.telegram import telegram_send
                try:
                    await telegram_send(bot, chat_id, text)
                    delivered = True
                except Exception as exc:
                    logger.error("events_consumer: telegram_send failed: %s", exc)
            else:
                logger.debug(
                    "events_consumer: no telegram_chat_id for session %s, marking delivered",
                    session_id,
                )
                delivered = True
    else:
        logger.warning("events_consumer: unknown channel %r on event %s", channel, row.get("id"))

    if delivered:
        await store.mark_event_delivered(db, row["id"])
