"""B1 — PAGroqAdapter: Groq SDK wrapper, OpenAI-compatible chat completions."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import groq

from orchestrator.models import (
    AdapterManifest,
    AdapterParam,
    Caller,
    ErrorCode,
    ErrorDetail,
    Result,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "llama-3.3-70b-versatile"
HARD_MAX_OUTPUT_TOKENS = 4000


class PAGroqAdapter:
    name = "pa_groq"
    allowed_callers: set[Caller] = {
        Caller.PA,
        Caller.MAKER,
        Caller.JOB_RUNNER,
        Caller.CTO_SUBAGENT,
    }

    def __init__(
        self,
        client: groq.AsyncGroq | None = None,
        db: Any = None,
        default_model: str = DEFAULT_MODEL,
        max_output_tokens: int = HARD_MAX_OUTPUT_TOKENS,
    ) -> None:
        self._client = client or groq.AsyncGroq()
        self._db = db
        self._default_model = default_model
        self._max_output_tokens = max_output_tokens

    @property
    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            required=[
                AdapterParam(name="messages", type="list", description="OpenAI-format messages list"),
            ],
            optional=[
                AdapterParam(name="system", type="str", description="system prompt — prepended as system message"),
                AdapterParam(name="max_tokens", type="int", description="max output tokens (clamped to 4000)"),
                AdapterParam(name="temperature", type="float"),
                AdapterParam(name="model", type="str"),
                AdapterParam(name="session_id", type="str", description="for cost_ledger attribution"),
            ],
        )

    async def health(self) -> bool:
        return os.environ.get("GROQ_API_KEY") is not None

    async def invoke(
        self,
        payload: dict,
        deadline_s: float,
        caller: Caller,
    ) -> Result:
        msgs = payload.get("messages")
        if not isinstance(msgs, list) or not msgs:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.BAD_INPUT,
                    message="'messages' must be a non-empty list",
                    retriable=False,
                ),
            )

        model = payload.get("model", self._default_model)
        max_tokens = self._clamp_max_tokens(payload.get("max_tokens", self._max_output_tokens))

        # Groq uses messages list for system; prepend if payload carries a separate system key
        messages = list(msgs)
        if payload.get("system"):
            messages = [{"role": "system", "content": payload["system"]}] + messages

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if "temperature" in payload:
            kwargs["temperature"] = payload["temperature"]

        start = time.monotonic()
        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(**kwargs),
                timeout=deadline_s,
            )
        except Exception as exc:
            return Result(
                ok=False,
                error=self._map_groq_error(exc),
                meta={
                    "tool": self.name,
                    "latency_ms": int((time.monotonic() - start) * 1000),
                    "tokens_in": 0,
                    "tokens_out": 0,
                },
            )

        latency_ms = int((time.monotonic() - start) * 1000)
        text = (response.choices[0].message.content or "") if response.choices else ""
        usage = response.usage
        tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0)
        tokens_out = int(getattr(usage, "completion_tokens", 0) or 0)

        await self._record_cost(
            session_id=payload.get("session_id"),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )

        return Result(
            ok=True,
            data=text,
            cost_usd=0.0,
            meta={
                "tool": self.name,
                "latency_ms": latency_ms,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "model": model,
                "stop_reason": getattr(response.choices[0], "finish_reason", None) if response.choices else None,
            },
        )

    def _clamp_max_tokens(self, requested: Any) -> int:
        try:
            n = int(requested)
        except (TypeError, ValueError):
            n = self._max_output_tokens
        return min(max(n, 1), self._max_output_tokens)

    def _map_groq_error(self, exc: Exception) -> ErrorDetail:
        if isinstance(exc, asyncio.TimeoutError):
            return ErrorDetail(code=ErrorCode.TIMEOUT, message=str(exc), retriable=True)
        if isinstance(exc, groq.APITimeoutError):
            return ErrorDetail(code=ErrorCode.TIMEOUT, message=str(exc), retriable=True)
        if isinstance(exc, groq.RateLimitError):
            return ErrorDetail(code=ErrorCode.RATE_LIMIT, message=str(exc), retriable=True)
        if isinstance(exc, (groq.AuthenticationError, groq.PermissionDeniedError)):
            return ErrorDetail(code=ErrorCode.UNAUTHORIZED, message=str(exc), retriable=False)
        if isinstance(exc, groq.BadRequestError):
            return ErrorDetail(code=ErrorCode.BAD_INPUT, message=str(exc), retriable=False)
        if isinstance(exc, groq.APIStatusError):
            retriable = 500 <= int(getattr(exc, "status_code", 0) or 0) < 600
            return ErrorDetail(code=ErrorCode.TOOL_ERROR, message=str(exc), retriable=retriable)
        if isinstance(exc, groq.APIConnectionError):
            return ErrorDetail(code=ErrorCode.TOOL_ERROR, message=str(exc), retriable=True)
        return ErrorDetail(code=ErrorCode.TOOL_ERROR, message=str(exc), retriable=True)

    async def _record_cost(
        self,
        *,
        session_id: str | None,
        tokens_in: int,
        tokens_out: int,
    ) -> None:
        if self._db is None or not session_id:
            return
        ts = datetime.now(timezone.utc).isoformat()
        try:
            await self._db.execute(
                "INSERT INTO cost_ledger "
                "(session_id, job_id, timestamp, adapter, tokens_in, tokens_out, cost_usd, tier) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, None, ts, self.name, tokens_in, tokens_out, 0.0, "pa-groq"),
            )
            await self._db.commit()
        except Exception as exc:
            logger.error("pa_groq: failed to write cost_ledger row: %s", exc)
