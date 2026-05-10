"""Step 9a — ClaudeAPIAdapter: streaming SSE + prompt caching + cost tracking.

Implements the Tool protocol for the Anthropic Messages API.

Operations (selected via ``payload['operation']``):
- ``chat`` (default): non-streaming request. Returns the full assistant text in
  ``Result.data``.
- ``complete``: convenience wrapper that wraps ``payload['prompt']`` into a
  single user message and routes through ``chat``.

Streaming is exposed via ``stream(payload, deadline_s, caller)`` which yields
``{"type": "token", "text": ...}``, ``{"type": "done", "result": ...}``, and
``{"type": "error", "error": ...}`` events.

Prompt caching: ``system`` and ``summary_anchor`` payload fields, when present,
are wrapped as text blocks with ``cache_control={"type": "ephemeral"}``.

Cost tracking: each call computes ``cost_usd`` from ``response.usage`` against
the static price table below and, if a SQLite connection was supplied, inserts
a ``cost_ledger`` row and increments ``sessions.cost_to_date_usd``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import anthropic

from orchestrator.models import (
    AdapterManifest,
    AdapterParam,
    Caller,
    ErrorCode,
    ErrorDetail,
    Result,
)

logger = logging.getLogger(__name__)


# Anthropic public pricing — USD per 1M tokens. Update when pricing changes.
PRICING_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-opus-4-7":          {"input": 15.00, "output": 75.00, "cache_write": 18.75, "cache_read": 1.50},
    "claude-sonnet-4-6":        {"input":  3.00, "output": 15.00, "cache_write":  3.75, "cache_read": 0.30},
    "claude-haiku-4-5-20251001":{"input":  1.00, "output":  5.00, "cache_write":  1.25, "cache_read": 0.10},
}

DEFAULT_MODEL = "claude-sonnet-4-6"
HARD_MAX_OUTPUT_TOKENS = 4000  # Mirrors guardrails.budgets.max_output_tokens default.


def _price_for(model: str) -> dict[str, float]:
    return PRICING_PER_MTOK.get(model, PRICING_PER_MTOK[DEFAULT_MODEL])


def _calc_cost(
    model: str,
    tokens_in: int,
    tokens_out: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    """Cost in USD. Treats cache_creation/cache_read as separate billing buckets."""
    p = _price_for(model)
    cost = (
        tokens_in            * p["input"]       / 1_000_000
        + tokens_out         * p["output"]      / 1_000_000
        + cache_creation_tokens * p["cache_write"] / 1_000_000
        + cache_read_tokens     * p["cache_read"]  / 1_000_000
    )
    return max(cost, 0.0)


def _usage_field(usage: Any, name: str) -> int:
    val = getattr(usage, name, 0)
    return int(val) if val is not None else 0


class ClaudeAPIAdapter:
    name: str = "claude_api"
    allowed_callers: set[Caller] = {Caller.PA, Caller.JOB_RUNNER}

    def __init__(
        self,
        client: anthropic.AsyncAnthropic | None = None,
        db: Any = None,
        max_output_tokens: int = HARD_MAX_OUTPUT_TOKENS,
        default_model: str = DEFAULT_MODEL,
    ) -> None:
        self._client = client or anthropic.AsyncAnthropic()
        self._db = db
        self._max_output_tokens = max_output_tokens
        self._default_model = default_model

    # ------------------------------------------------------------------ Tool

    @property
    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            required=[
                AdapterParam(name="messages", type="list", description="Anthropic messages format"),
                AdapterParam(name="max_tokens", type="int", description="Max output tokens (clamped)"),
            ],
            optional=[
                AdapterParam(name="operation", type="str", description="'chat' (default) or 'complete'"),
                AdapterParam(name="prompt", type="str", description="user prompt for operation=complete"),
                AdapterParam(name="system", type="str", description="system prompt — sent as cached block"),
                AdapterParam(name="summary_anchor", type="str", description="conversation summary — sent as cached block"),
                AdapterParam(name="temperature", type="float"),
                AdapterParam(name="model", type="str"),
                AdapterParam(name="session_id", type="str", description="for cost_ledger attribution"),
            ],
        )

    async def health(self) -> bool:
        return os.environ.get("ANTHROPIC_API_KEY") is not None

    async def invoke(
        self,
        payload: dict,
        deadline_s: float,
        caller: Caller,
    ) -> Result:
        operation = payload.get("operation", "chat")
        if operation == "chat":
            return await self._invoke_chat(payload, deadline_s)
        if operation == "complete":
            return await self._invoke_complete(payload, deadline_s)
        return Result(
            ok=False,
            error=ErrorDetail(
                code=ErrorCode.BAD_INPUT,
                message=f"Unknown operation {operation!r}; expected 'chat' or 'complete'",
                retriable=False,
            ),
        )

    async def stream(
        self,
        payload: dict,
        deadline_s: float,
        caller: Caller,
    ) -> AsyncIterator[dict]:
        err = self._validate_chat_payload(payload)
        if err is not None:
            yield {"type": "error", "error": err.model_dump()}
            return

        kwargs = self._build_kwargs(payload)
        start = time.monotonic()
        accumulated: list[str] = []
        final_message: Any = None

        try:
            stream_mgr = self._client.messages.stream(**kwargs)
            async with stream_mgr as event_iter:
                async for event in event_iter:
                    et = getattr(event, "type", "")
                    if et == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        text = getattr(delta, "text", None) if delta is not None else None
                        if text:
                            accumulated.append(text)
                            yield {"type": "token", "text": text}
                final_message = await event_iter.get_final_message()
        except Exception as exc:
            yield {
                "type": "error",
                "error": self._map_anthropic_error(exc).model_dump(),
            }
            return

        result = await self._build_result(
            payload=payload,
            text="".join(accumulated),
            response=final_message,
            start=start,
        )
        yield {"type": "done", "result": result.model_dump()}

    # -------------------------------------------------------------- internals

    async def _invoke_chat(self, payload: dict, deadline_s: float) -> Result:
        err = self._validate_chat_payload(payload)
        if err is not None:
            return Result(ok=False, error=err)

        kwargs = self._build_kwargs(payload)
        start = time.monotonic()

        try:
            response = await asyncio.wait_for(
                self._client.messages.create(**kwargs),
                timeout=deadline_s,
            )
        except Exception as exc:
            return Result(
                ok=False,
                error=self._map_anthropic_error(exc),
                meta={
                    "tool": self.name,
                    "latency_ms": int((time.monotonic() - start) * 1000),
                    "tokens_in": 0,
                    "tokens_out": 0,
                },
            )

        text_parts = [
            getattr(b, "text", "") for b in response.content
            if getattr(b, "type", "") == "text"
        ]
        return await self._build_result(
            payload=payload,
            text="".join(text_parts),
            response=response,
            start=start,
        )

    async def _invoke_complete(self, payload: dict, deadline_s: float) -> Result:
        err = self._validate_complete_payload(payload)
        if err is not None:
            return Result(ok=False, error=err)

        chat_payload = dict(payload)
        chat_payload["messages"] = [{"role": "user", "content": payload["prompt"]}]
        chat_payload["operation"] = "chat"
        chat_payload.pop("prompt", None)
        return await self._invoke_chat(chat_payload, deadline_s)

    def _build_kwargs(self, payload: dict) -> dict[str, Any]:
        model = payload.get("model", self._default_model)
        max_tokens = self._clamp_max_tokens(payload["max_tokens"])
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": payload["messages"],
        }
        system_blocks = self._build_system_blocks(
            payload.get("system"), payload.get("summary_anchor")
        )
        if system_blocks is not None:
            kwargs["system"] = system_blocks
        if "temperature" in payload:
            kwargs["temperature"] = payload["temperature"]
        return kwargs

    def _build_system_blocks(
        self,
        system: str | None,
        summary_anchor: str | None,
    ) -> list[dict] | None:
        blocks: list[dict] = []
        if system:
            blocks.append({
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            })
        if summary_anchor:
            blocks.append({
                "type": "text",
                "text": summary_anchor,
                "cache_control": {"type": "ephemeral"},
            })
        return blocks or None

    def _clamp_max_tokens(self, requested: Any) -> int:
        try:
            n = int(requested)
        except (TypeError, ValueError):
            n = self._max_output_tokens
        return min(max(n, 1), self._max_output_tokens)

    def _validate_chat_payload(self, payload: dict) -> ErrorDetail | None:
        msgs = payload.get("messages")
        if not isinstance(msgs, list) or not msgs:
            return ErrorDetail(
                code=ErrorCode.BAD_INPUT,
                message="'messages' must be a non-empty list",
                retriable=False,
            )
        if "max_tokens" not in payload:
            return ErrorDetail(
                code=ErrorCode.BAD_INPUT,
                message="'max_tokens' is required",
                retriable=False,
            )
        return None

    def _validate_complete_payload(self, payload: dict) -> ErrorDetail | None:
        if not isinstance(payload.get("prompt"), str) or not payload["prompt"]:
            return ErrorDetail(
                code=ErrorCode.BAD_INPUT,
                message="'prompt' must be a non-empty string for operation='complete'",
                retriable=False,
            )
        if "max_tokens" not in payload:
            return ErrorDetail(
                code=ErrorCode.BAD_INPUT,
                message="'max_tokens' is required",
                retriable=False,
            )
        return None

    def _map_anthropic_error(self, exc: Exception) -> ErrorDetail:
        if isinstance(exc, (anthropic.APITimeoutError, asyncio.TimeoutError)):
            return ErrorDetail(code=ErrorCode.TIMEOUT, message=str(exc), retriable=True)
        if isinstance(exc, anthropic.RateLimitError):
            return ErrorDetail(code=ErrorCode.RATE_LIMIT, message=str(exc), retriable=True)
        if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
            return ErrorDetail(code=ErrorCode.UNAUTHORIZED, message=str(exc), retriable=False)
        if isinstance(exc, anthropic.BadRequestError):
            return ErrorDetail(code=ErrorCode.BAD_INPUT, message=str(exc), retriable=False)
        if isinstance(exc, anthropic.APIStatusError):
            retriable = 500 <= int(getattr(exc, "status_code", 0) or 0) < 600
            return ErrorDetail(code=ErrorCode.TOOL_ERROR, message=str(exc), retriable=retriable)
        if isinstance(exc, anthropic.APIConnectionError):
            return ErrorDetail(code=ErrorCode.TOOL_ERROR, message=str(exc), retriable=True)
        return ErrorDetail(code=ErrorCode.TOOL_ERROR, message=str(exc), retriable=True)

    async def _build_result(
        self,
        *,
        payload: dict,
        text: str,
        response: Any,
        start: float,
    ) -> Result:
        latency_ms = int((time.monotonic() - start) * 1000)
        usage = getattr(response, "usage", None)
        tokens_in = _usage_field(usage, "input_tokens")
        tokens_out = _usage_field(usage, "output_tokens")
        cache_creation = _usage_field(usage, "cache_creation_input_tokens")
        cache_read = _usage_field(usage, "cache_read_input_tokens")
        model = payload.get("model", self._default_model)
        cost = _calc_cost(model, tokens_in, tokens_out, cache_creation, cache_read)

        await self._record_cost(
            session_id=payload.get("session_id"),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
        )

        return Result(
            ok=True,
            data=text,
            cost_usd=cost,
            meta={
                "tool": self.name,
                "latency_ms": latency_ms,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cache_creation_input_tokens": cache_creation,
                "cache_read_input_tokens": cache_read,
                "model": model,
                "stop_reason": getattr(response, "stop_reason", None),
            },
        )

    async def _record_cost(
        self,
        *,
        session_id: str | None,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
    ) -> None:
        if self._db is None or not session_id:
            return
        ts = datetime.now(timezone.utc).isoformat()
        try:
            await self._db.execute(
                "INSERT INTO cost_ledger "
                "(session_id, job_id, timestamp, adapter, tokens_in, tokens_out, cost_usd) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, None, ts, self.name, tokens_in, tokens_out, cost_usd),
            )
            await self._db.execute(
                "UPDATE sessions SET cost_to_date_usd = cost_to_date_usd + ? "
                "WHERE id = ?",
                (cost_usd, session_id),
            )
            await self._db.commit()
        except Exception as exc:
            logger.error("claude_api: failed to write cost_ledger row: %s", exc)
