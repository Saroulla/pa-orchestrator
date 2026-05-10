"""Step 11 — FastAPI main: lifespan, chat handler, WS manager, events consumer."""
from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from orchestrator import escalation, store
from orchestrator.config import get_config, start_watcher
from orchestrator.events import events_consumer
from orchestrator.maker import main as maker_main
from orchestrator.proxy.adapters.brave_search import BraveSearchAdapter
from orchestrator.proxy.adapters.claude_api import ClaudeAPIAdapter
from orchestrator.proxy.adapters.claude_code import ClaudeCodeAdapter
from orchestrator.proxy.adapters.email_send import EmailAdapter
from orchestrator.proxy.adapters.file_read import FileReadAdapter
from orchestrator.proxy.adapters.file_write import FileWriteAdapter
from orchestrator.proxy.adapters.pa_groq import PAGroqAdapter
from orchestrator.proxy.adapters.pa_haiku import PAHaikuAdapter
from orchestrator.proxy.adapters.pdf_extract import PDFExtractAdapter
from orchestrator.proxy.adapters.playwright_web import PlaywrightWebAdapter
from orchestrator.proxy.adapters.template_render import TemplateRenderAdapter
from orchestrator.proxy.dispatcher import Dispatcher
from orchestrator.auth import router as auth_router, verify_session
from orchestrator.spawner import SubAgentSpawner
from orchestrator.telegram import router as telegram_router

logger = logging.getLogger(__name__)


REPO_ROOT = Path("C:/Users/Mini_PC/_REPO")
DB_PATH = REPO_ROOT / "orchestrator.db"
GUARDRAILS_PATH = REPO_ROOT / "config" / "guardrails.yaml"


# ---------------------------------------------------------------------------
# WebSocket manager
# ---------------------------------------------------------------------------

class WebSocketManager:
    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}

    async def connect(self, session_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._connections[session_id] = ws

    def disconnect(self, session_id: str) -> None:
        self._connections.pop(session_id, None)

    async def send(self, session_id: str, data: dict) -> bool:
        ws = self._connections.get(session_id)
        if ws is None:
            return False
        try:
            await ws.send_json(data)
            return True
        except Exception:
            self.disconnect(session_id)
            return False


ws_manager = WebSocketManager()


# ---------------------------------------------------------------------------
# Chat handler — thin forwarder to maker.main.dispatch (F1)
# ---------------------------------------------------------------------------

def _make_chat_handler(app: FastAPI):
    async def chat_handler(
        session_id: str,
        text: str,
        channel: str = "web",
        chat_id: int | None = None,
    ) -> dict:
        return await maker_main.dispatch(
            session_id=session_id,
            text=text,
            channel=channel,
            chat_id=chat_id,
        )

    return chat_handler


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- STARTUP ----
    observer = start_watcher(GUARDRAILS_PATH)

    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    await store.init_db(db)

    from orchestrator.pa_prompt import build_pa_system_prompt
    pa_system_prompt = build_pa_system_prompt()

    claude_api = ClaudeAPIAdapter(db=db)
    brave_search = BraveSearchAdapter()
    file_read = FileReadAdapter()
    file_write = FileWriteAdapter()
    try:
        pa_groq = PAGroqAdapter(db=db)
    except Exception as exc:
        logger.warning("PAGroqAdapter init failed (GROQ_API_KEY missing?): %s", exc)
        pa_groq = None
    pa_haiku = PAHaikuAdapter(db=db)

    @asynccontextmanager
    async def db_getter():
        yield db

    spawner = SubAgentSpawner(db_getter=db_getter, claude_api_adapter=claude_api)
    claude_code = ClaudeCodeAdapter(spawner=spawner, claude_api=claude_api, db=db)

    dispatcher = Dispatcher(config_getter=get_config, escalation_module=escalation)
    dispatcher.register(claude_api,   kind="reason")
    dispatcher.register(claude_code,  kind="code")
    dispatcher.register(brave_search, kind="search")
    dispatcher.register(file_read,    kind="file_read")
    dispatcher.register(file_write,   kind="file_write")

    playwright = PlaywrightWebAdapter()
    pdf_extract = PDFExtractAdapter()
    email = EmailAdapter()
    template = TemplateRenderAdapter()
    dispatcher.register(playwright,  kind="playwright_web")
    dispatcher.register(pdf_extract, kind="pdf_extract")
    dispatcher.register(email,       kind="email_send")
    dispatcher.register(template,    kind="template_render")

    bot = None
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if tg_token:
        try:
            from telegram import Bot
            bot = Bot(token=tg_token)
        except Exception as exc:
            logger.warning("Telegram bot init failed: %s", exc)
            bot = None

    app.state.db = db
    app.state.dispatcher = dispatcher
    app.state.spawner = spawner
    app.state.bot = bot
    app.state.pa_system_prompt = pa_system_prompt
    app.state.pa_groq = pa_groq
    app.state.pa_haiku = pa_haiku

    maker_main.bind(maker_main.MakerContext(
        db=db,
        dispatcher=dispatcher,
        pa_groq=pa_groq,
        pa_haiku=pa_haiku,
        spawner=spawner,
    ))

    await spawner.start_reaper()
    consumer_task = asyncio.create_task(
        events_consumer(db=db, ws_manager=ws_manager, bot=bot)
    )

    cf_host = os.getenv("CLOUDFLARE_TUNNEL_HOST")
    if bot and cf_host:
        webhook_url = f"https://{cf_host}/webhook/telegram"
        try:
            await bot.set_webhook(url=webhook_url)
            logger.info("Telegram webhook set: %s", webhook_url)
        except Exception as exc:
            logger.warning("Could not set Telegram webhook: %s", exc)

    app.state.chat_handler = _make_chat_handler(app)

    try:
        yield
    finally:
        # ---- SHUTDOWN ----
        consumer_task.cancel()
        try:
            await consumer_task
        except (asyncio.CancelledError, Exception):
            pass

        await spawner.stop_reaper()

        try:
            observer.stop()
            observer.join(timeout=2.0)
        except Exception as exc:
            logger.warning("guardrails observer shutdown failed: %s", exc)

        try:
            await db.close()
        except Exception as exc:
            logger.warning("db close failed: %s", exc)

        maker_main._reset()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="PA Orchestrator", version="0.1.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(telegram_router)


class _AuthMiddleware:
    """Pure ASGI middleware — works with both HTTP and WebSocket scopes."""

    def __init__(self, app) -> None:
        self._app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path.startswith("/v1/"):
                cookie_header = dict(scope.get("headers", [])).get(b"cookie", b"").decode("latin-1")
                session = self._parse_cookie(cookie_header, "session")
                if not verify_session(session):
                    response = JSONResponse({"error": "Unauthorized"}, status_code=401)
                    await response(scope, receive, send)
                    return
        await self._app(scope, receive, send)

    @staticmethod
    def _parse_cookie(header: str, name: str) -> str | None:
        for part in header.split(";"):
            part = part.strip()
            if part.startswith(name + "="):
                return part[len(name) + 1:]
        return None


app.add_middleware(_AuthMiddleware)


@app.post("/v1/chat")
async def chat_endpoint(request: Request):
    body = await request.json()
    session_id = body.get("session_id") or uuid.uuid4().hex[:16]
    text = body.get("text", "")
    channel = body.get("channel", "web")
    handler = request.app.state.chat_handler
    result = await handler(session_id=session_id, text=text, channel=channel)
    return JSONResponse(result)


@app.websocket("/v1/stream/{session_id}")
async def ws_endpoint(websocket: WebSocket, session_id: str):
    if not verify_session(websocket.cookies.get("session")):
        await websocket.close(code=1008)
        return
    await ws_manager.connect(session_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(session_id)


@app.get("/v1/session/{session_id}")
async def session_info(session_id: str, request: Request):
    db = request.app.state.db
    session = await store.get_session(db, session_id)
    if session is None:
        return JSONResponse({"error": "session not found"}, status_code=404)
    return JSONResponse({
        "mode": session["mode"],
        "cost_to_date_usd": session["cost_to_date_usd"],
        "created_at": session["created_at"],
        "last_active": session["last_active"],
    })


@app.post("/v1/jobs/create")
async def create_job(request: Request):
    body = await request.json()
    session_id = body.get("session_id", "system00000000")
    name = body.get("name", "").strip()
    what_i_want = body.get("what_i_want", "").strip()
    if not name or not what_i_want:
        return JSONResponse({"error": "name and what_i_want required"}, status_code=400)
    claude_api_adapter = request.app.state.dispatcher._tools.get("reason")
    from orchestrator.plan_author import generate_plan, write_job
    try:
        plan_yaml, plan = await generate_plan(session_id, what_i_want, claude_api_adapter)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    job_id = write_job(session_id, name, what_i_want, plan_yaml, plan)
    return JSONResponse({"job_id": job_id, "file_path": f"jobs/{name}.md", "status": "created"})


@app.post("/v1/jobs/{job_id}/run")
async def run_job_endpoint(job_id: str, request: Request):
    from orchestrator import job_runner
    asyncio.create_task(job_runner.run(job_id))
    return JSONResponse({"status": "accepted", "job_id": job_id})


@app.get("/health")
async def health():
    return {"status": "ok"}


_DIST = REPO_ROOT / "web-ui" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="static")
