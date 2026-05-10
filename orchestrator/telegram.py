"""Step 12 — Telegram Bot router (APIRouter) + outbound sender."""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os

logger = logging.getLogger(__name__)

from aiolimiter import AsyncLimiter
from fastapi import APIRouter, Request
from fastapi.responses import Response
from telegram import Bot, Update

router = APIRouter()

# Rate limiters: 30/sec global, 1/sec per chat
_global_limiter = AsyncLimiter(30, 1)
_chat_limiters: dict[int, AsyncLimiter] = {}


def get_session_id(user_id: int) -> str:
    """Deterministic: sha256(str(user_id))[:16]."""
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:16]


async def telegram_send(bot: Bot, chat_id: int, text: str) -> None:
    """Rate-limited send. If len(text) > 4000: send as .md file attachment."""
    if chat_id not in _chat_limiters:
        _chat_limiters[chat_id] = AsyncLimiter(1, 1)
    chat_limiter = _chat_limiters[chat_id]

    async with _global_limiter:
        async with chat_limiter:
            if len(text) > 4000:
                await bot.send_document(
                    chat_id=chat_id,
                    document=io.BytesIO(text.encode("utf-8")),
                    filename="response.md",
                )
            else:
                await bot.send_message(chat_id=chat_id, text=text)


@router.post("/webhook/telegram")
async def webhook(request: Request) -> Response:
    # 1. Verify Cloudflare Tunnel origin (skip check in dev if env var not set).
    # cloudflared forwards the original Host header, not X-Forwarded-Host.
    cf_host = os.getenv("CLOUDFLARE_TUNNEL_HOST")
    if cf_host:
        host_header = request.headers.get("host", "")
        if host_header != cf_host:
            return Response(status_code=200)

    # 2. Parse Telegram Update from request body
    try:
        body = await request.json()
        update = Update.de_json(body, bot=None)
    except Exception:
        return Response(status_code=200)

    message = update.message or update.edited_message
    if not message:
        return Response(status_code=200)

    # 3. Check TELEGRAM_ALLOWED_USER_IDS allowlist; silent 200 if not permitted
    allowed_ids_str = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
    allowed_ids: set[int] = {
        int(x.strip()) for x in allowed_ids_str.split(",") if x.strip().isdigit()
    }
    user = message.from_user
    if user is None or user.id not in allowed_ids:
        return Response(status_code=200)

    user_id: int = user.id
    chat_id: int = message.chat.id
    text: str = message.text or ""
    session_id = get_session_id(user_id)

    # 4. Persist telegram_chat_id to sessions table (best-effort; store may be a stub)
    try:
        from orchestrator import store  # noqa: PLC0415
        await store.upsert_telegram_chat_id(session_id, chat_id)
    except Exception:  # best-effort: swallow DB-not-ready, stub, and any other error
        pass

    # 5. Fire-and-forget to chat handler; sends reply via telegram_send after completion
    bot = getattr(request.app.state, "bot", None)
    handler = getattr(request.app.state, "chat_handler", None)
    if handler is not None:
        async def _handle_and_reply() -> None:
            try:
                result = await handler(
                    session_id=session_id,
                    channel="telegram",
                    chat_id=chat_id,
                    text=text,
                )
                if bot and result:
                    reply = result.get("response", "")
                    if reply:
                        await telegram_send(bot, chat_id, reply)
            except Exception as exc:
                logger.error("telegram _handle_and_reply failed: %s", exc, exc_info=True)
                if bot:
                    try:
                        await telegram_send(bot, chat_id, f"[PA]> Error: {exc}")
                    except Exception:
                        pass
        asyncio.create_task(_handle_and_reply())

    # 6. Return 200 immediately (Telegram requires fast ack)
    return Response(status_code=200)
