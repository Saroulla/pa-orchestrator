"""Proxy dispatcher — route Intent → Tool with auth, budget, and retry logic."""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Callable

from orchestrator.models import Caller, ErrorCode, ErrorDetail, Intent, Result
from orchestrator.proxy.protocol import Tool

logger = logging.getLogger(__name__)


class Dispatcher:
    def __init__(self, config_getter: Callable, escalation_module) -> None:
        # config_getter: () -> Guardrails
        # escalation_module: module/object with async create(...)
        self._config_getter = config_getter
        self._escalation = escalation_module
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool, kind: str | None = None) -> None:
        key = kind if kind is not None else tool.name
        self._tools[key] = tool

    async def _get_session_cost(self, session_id: str, db) -> float:
        cursor = await db.execute(
            "SELECT cost_to_date_usd FROM sessions WHERE id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        return float(row[0]) if row is not None else 0.0

    async def _auth_and_budget(
        self, intent: Intent, db
    ) -> Result | None:
        """Return an error Result if auth or budget fails, else None."""
        tool = self._tools.get(intent.kind)
        if tool is None:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.INTERNAL,
                    message=f"No tool registered for kind={intent.kind!r}",
                    retriable=False,
                ),
            )

        if intent.caller not in tool.allowed_callers:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.UNAUTHORIZED,
                    message=(
                        f"Caller {intent.caller!r} is not allowed "
                        f"to invoke tool {tool.name!r}"
                    ),
                    retriable=False,
                ),
            )

        config = self._config_getter()
        limit = config.budgets.per_session_usd_per_day
        session_cost = await self._get_session_cost(intent.session_id, db)
        if session_cost >= limit:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.QUOTA,
                    message=(
                        f"Session cost ${session_cost:.4f} has reached "
                        f"the daily limit ${limit:.2f}"
                    ),
                    retriable=False,
                ),
            )

        return None

    async def dispatch(self, intent: Intent, db) -> Result:
        # 1. Unregistered tool → INTERNAL
        tool = self._tools.get(intent.kind)
        if tool is None:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.INTERNAL,
                    message=f"No tool registered for kind={intent.kind!r}",
                    retriable=False,
                ),
            )

        # 2. Caller authorization → UNAUTHORIZED (no retry)
        if intent.caller not in tool.allowed_callers:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.UNAUTHORIZED,
                    message=(
                        f"Caller {intent.caller!r} is not allowed "
                        f"to invoke tool {tool.name!r}"
                    ),
                    retriable=False,
                ),
            )

        # 3. Pre-dispatch budget check → QUOTA (no retry)
        config = self._config_getter()
        limit = config.budgets.per_session_usd_per_day
        session_cost = await self._get_session_cost(intent.session_id, db)
        if session_cost >= limit:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.QUOTA,
                    message=(
                        f"Session cost ${session_cost:.4f} has reached "
                        f"the daily limit ${limit:.2f}"
                    ),
                    retriable=False,
                ),
            )

        # 4. Retry loop
        retry = config.retry
        last_result: Result | None = None

        for attempt in range(retry.max_attempts):
            if attempt > 0:
                # backoff = base_ms * factor^(attempt-1); sleep between retries
                backoff_s = (
                    retry.backoff_base_ms
                    * (retry.backoff_factor ** (attempt - 1))
                ) / 1000.0
                await asyncio.sleep(backoff_s)

            try:
                result = await tool.invoke(
                    intent.payload,
                    intent.deadline_s,
                    intent.caller,
                )
            except Exception as exc:
                logger.exception(
                    "Tool %r raised on attempt %d: %s", tool.name, attempt, exc
                )
                last_result = Result(
                    ok=False,
                    error=ErrorDetail(
                        code=ErrorCode.TOOL_ERROR,
                        message=str(exc),
                        retriable=True,
                    ),
                )
                continue

            if result.ok:
                return result

            last_result = result
            # Non-retriable error: stop immediately without further attempts
            if result.error and not result.error.retriable:
                break

        # 5. Terminal failure — create escalation row, return ok=False
        assert last_result is not None
        await self._create_escalation(intent, last_result)
        error = last_result.error
        return Result(
            ok=False,
            error=ErrorDetail(
                code=error.code if error else ErrorCode.TOOL_ERROR,
                message=error.message if error else "Unknown error after all retries",
                retriable=False,
            ),
        )

    async def _create_escalation(self, intent: Intent, result: Result) -> None:
        try:
            await self._escalation.create(
                session_id=intent.session_id,
                channel=None,
                options={"a": "retry", "b": "skip"},
                context={
                    "kind": intent.kind,
                    "error": result.error.model_dump() if result.error else {},
                },
            )
        except Exception as exc:
            logger.error("Failed to create escalation: %s", exc)

    async def stream(self, intent: Intent, db) -> AsyncIterator[dict]:
        # Same auth/budget checks before streaming
        tool = self._tools.get(intent.kind)
        if tool is None:
            yield {
                "error": ErrorCode.INTERNAL,
                "message": f"No tool registered for kind={intent.kind!r}",
            }
            return

        if intent.caller not in tool.allowed_callers:
            yield {
                "error": ErrorCode.UNAUTHORIZED,
                "message": f"Caller {intent.caller!r} not allowed for {tool.name!r}",
            }
            return

        config = self._config_getter()
        limit = config.budgets.per_session_usd_per_day
        session_cost = await self._get_session_cost(intent.session_id, db)
        if session_cost >= limit:
            yield {
                "error": ErrorCode.QUOTA,
                "message": f"Budget limit ${limit:.2f} reached",
            }
            return

        if hasattr(tool, "stream"):
            async for event in tool.stream(  # type: ignore[attr-defined]
                intent.payload, intent.deadline_s, intent.caller
            ):
                yield event
        else:
            result = await self.dispatch(intent, db)
            yield {"ok": result.ok, "data": result.data}
