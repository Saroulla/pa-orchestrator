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
from orchestrator.fsm import transition
from orchestrator.history import build_context, slide_and_summarise
from orchestrator.models import Caller, Channel, Intent, Mode
from orchestrator.parser import parse
from orchestrator.proxy.adapters.brave_search import BraveSearchAdapter
from orchestrator.proxy.adapters.claude_api import ClaudeAPIAdapter
from orchestrator.proxy.adapters.email_send import EmailAdapter
from orchestrator.proxy.adapters.file_read import FileReadAdapter
from orchestrator.proxy.adapters.file_write import FileWriteAdapter
from orchestrator.proxy.adapters.pdf_extract import PDFExtractAdapter
from orchestrator.proxy.adapters.playwright_web import PlaywrightWebAdapter
from orchestrator.maker.iterative_goal import IterativeGoalExecutor
from orchestrator.proxy.adapters.powershell import PowerShellAdapter
from orchestrator.proxy.adapters.template_render import TemplateRenderAdapter
from orchestrator.proxy.dispatcher import Dispatcher
from orchestrator.auth import router as auth_router, verify_session
from orchestrator.telegram import router as telegram_router
from orchestrator.tokens import count as count_tokens

logger = logging.getLogger(__name__)


REPO_ROOT = Path(__file__).resolve().parents[1]
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
# Chat handler factory
# ---------------------------------------------------------------------------

def _format_response(mode: Mode, body: str, mode_msg: str | None) -> str:
    label = f"[{mode.value}]>"
    formatted = f"{label} {body}"
    if mode_msg:
        formatted = f"[PA]> {mode_msg}\n{formatted}"
    return formatted




def _make_chat_handler(app: FastAPI):
    async def chat_handler(
        session_id: str,
        text: str,
        channel: str = "web",
        chat_id: int | None = None,
    ) -> dict:
        db = app.state.db
        dispatcher: Dispatcher = app.state.dispatcher
        pa_system_prompt = app.state.pa_system_prompt

        t0 = time.monotonic()
        config = get_config()

        # 1. Ensure session exists
        session = await store.get_or_create_session(db, session_id, channel)
        current_mode = Mode(session["mode"])

        # 2. @cost meta-command — inline, no LLM
        if text.strip().lower() == "@cost":
            cost = await store.get_session_cost(db, session_id)
            return {
                "response": f"[PA]> Session cost so far: ${cost:.4f}",
                "mode": current_mode.value,
                "cost_usd": 0.0,
                "latency_ms": int((time.monotonic() - t0) * 1000),
            }

        # 3. Escalation interception
        try:
            esc_outcome, esc_key = await escalation.resolve_incoming_message(
                db, session_id, text
            )
        except Exception as exc:
            logger.warning("chat_handler: escalation lookup failed: %s", exc)
            esc_outcome, esc_key = "passthrough", None

        if esc_outcome == "resolved":
            return {
                "response": f"[PA]> Got it — proceeding with option ({esc_key}).",
                "mode": current_mode.value,
                "cost_usd": 0.0,
                "latency_ms": int((time.monotonic() - t0) * 1000),
            }

        # 4. Parse + FSM
        intent = parse(text, session_id, current_mode, Caller.PA)
        new_mode, mode_msg = transition(current_mode, intent.kind, Channel(channel))
        if new_mode != current_mode:
            await store.update_session_mode(db, session_id, new_mode.value)
            current_mode = new_mode

        # 5. Mode-switch-only message (e.g. bare "@PA")
        if not intent.payload.get("text", "").strip() and mode_msg:
            return {
                "response": f"[PA]> {mode_msg}",
                "mode": current_mode.value,
                "cost_usd": 0.0,
                "latency_ms": int((time.monotonic() - t0) * 1000),
            }

        # 6. Desktop stub
        if intent.kind == "desktop":
            return {
                "response": "[PA]> @Desktop is coming in Phase 1.2.",
                "mode": current_mode.value,
                "cost_usd": 0.0,
                "latency_ms": int((time.monotonic() - t0) * 1000),
            }

        # 6b. Bare @goal — return usage rather than dispatching an empty-goal LLM run
        if intent.kind == "goal" and not intent.payload.get("text", "").strip():
            return {
                "response": "[PA]> Usage: @goal <what you want done>",
                "mode": current_mode.value,
                "cost_usd": 0.0,
                "latency_ms": int((time.monotonic() - t0) * 1000),
            }

        # 7. Budget check
        cost_so_far = await store.get_session_cost(db, session_id)
        if cost_so_far >= config.budgets.per_session_usd_per_day:
            return {
                "response": (
                    f"[PA]> Daily budget "
                    f"(${config.budgets.per_session_usd_per_day:.2f}) reached."
                ),
                "mode": current_mode.value,
                "cost_usd": 0.0,
                "latency_ms": int((time.monotonic() - t0) * 1000),
            }

        # @remember intercept
        if intent.payload.get("meta_command") == "remember_interest":
            interest_text = intent.payload.get("text", "").strip()
            if not interest_text:
                return {
                    "response": "[PA]> Usage: @remember <what you're interested in>",
                    "mode": current_mode.value,
                    "cost_usd": 0.0,
                    "latency_ms": int((time.monotonic() - t0) * 1000),
                }
            from orchestrator.interests import update_interests, build_interests_context
            from orchestrator.pa_prompt import build_pa_system_prompt
            update_interests(interest_text)
            # Rebuild and cache the system prompt so the new interest takes effect immediately.
            app.state.pa_system_prompt = build_pa_system_prompt()
            return {
                "response": f"[PA]> Got it — I'll remember you're interested in: {interest_text}",
                "mode": current_mode.value,
                "cost_usd": 0.0,
                "latency_ms": int((time.monotonic() - t0) * 1000),
            }

        # @rebuild-plan intercept
        if intent.payload.get("meta_command") == "rebuild_plan":
            from orchestrator.plan_author import rebuild_plan
            file_arg = intent.payload.get("text", "").strip()
            if not file_arg:
                return {
                    "response": "[PA]> Usage: @rebuild-plan jobs/<name>.md",
                    "mode": current_mode.value,
                    "cost_usd": 0.0,
                    "latency_ms": int((time.monotonic() - t0) * 1000),
                }
            claude_api_adapter = app.state.dispatcher._tools.get("reason")
            result_msg = await rebuild_plan(session_id, file_arg, claude_api_adapter)
            return {
                "response": result_msg,
                "mode": current_mode.value,
                "cost_usd": 0.0,
                "latency_ms": int((time.monotonic() - t0) * 1000),
            }

        # 8. Save user message
        try:
            user_tokens = count_tokens(text)
        except Exception as exc:
            logger.warning("chat_handler: count_tokens failed, using char-fallback: %s", exc)
            user_tokens = max(1, len(text) // 4)
        await store.add_message(db, session_id, "user", text, user_tokens)

        response_text = ""

        # 9. Dispatch
        try:
            messages = await build_context(
                db,
                session_id,
                config.budgets.max_input_tokens,
                config.budgets.max_output_tokens,
            )
        except Exception as exc:
            logger.warning("chat_handler: build_context failed: %s", exc)
            messages = [{"role": "user", "content": text}]

        if not messages:
            messages = [{"role": "user", "content": text}]

        intent.payload["messages"] = messages
        intent.payload["max_tokens"] = config.budgets.max_output_tokens
        intent.payload["model"] = config.models.pa_chat
        intent.payload["system"] = pa_system_prompt
        intent.payload["session_id"] = session_id
        summary = session.get("summary_anchor")
        if summary:
            intent.payload["summary_anchor"] = summary

        result = await dispatcher.dispatch(intent, db)
        if result.ok:
            if isinstance(result.data, dict) and "goal_state" in result.data:
                gs = result.data["goal_state"]
                iter_count = len(gs.iterations)
                goal_latency_ms = result.meta.get("latency_ms", 0)
                response_text = (
                    f"Goal {'achieved' if gs.achieved else 'NOT achieved'} "
                    f"in {iter_count} iteration{'s' if iter_count != 1 else ''} "
                    f"(cost ${result.cost_usd:.4f}, {goal_latency_ms} ms).\n\n"
                    f"{gs.final_summary or '(no summary)'}"
                )
            else:
                response_text = result.data or ""
            if result.cost_usd:
                await store.increment_session_cost(db, session_id, result.cost_usd)
        else:
            err = result.error
            response_text = f"Sorry, hit an error: {err.message if err else 'unknown'}"

        # 10. Format
        formatted = _format_response(current_mode, response_text, mode_msg)

        # 11. Save assistant message
        try:
            assistant_tokens = count_tokens(response_text)
        except Exception:
            assistant_tokens = max(1, len(response_text) // 4)
        await store.add_message(db, session_id, "assistant", response_text, assistant_tokens)

        # 12. Slide/summarise — best effort, don't let failures bubble
        async def _safe_summarise():
            try:
                await slide_and_summarise(db, session_id, summarize_model=config.models.summarize)
            except Exception as exc:
                logger.warning("chat_handler: slide_and_summarise failed: %s", exc)

        asyncio.create_task(_safe_summarise())

        latency_ms = int((time.monotonic() - t0) * 1000)
        return {
            "response": formatted,
            "mode": current_mode.value,
            "cost_usd": result.cost_usd,
            "latency_ms": latency_ms,
        }

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
    powershell = PowerShellAdapter()

    ige = IterativeGoalExecutor(claude_adapter=claude_api, ps_adapter=powershell)

    dispatcher = Dispatcher(config_getter=get_config, escalation_module=escalation, goal_executor=ige)
    dispatcher.register(claude_api,   kind="reason")
    dispatcher.register(brave_search, kind="search")
    dispatcher.register(file_read,    kind="file_read")
    dispatcher.register(file_write,   kind="file_write")
    dispatcher.register(powershell,   kind="powershell")

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
    app.state.bot = bot
    app.state.pa_system_prompt = pa_system_prompt

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

        try:
            observer.stop()
            observer.join(timeout=2.0)
        except Exception as exc:
            logger.warning("guardrails observer shutdown failed: %s", exc)

        try:
            await db.close()
        except Exception as exc:
            logger.warning("db close failed: %s", exc)


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
