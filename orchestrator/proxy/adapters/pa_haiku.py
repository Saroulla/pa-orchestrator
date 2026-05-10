"""B2 — PAHaikuAdapter: thin wrapper on ClaudeAPIAdapter pinned to claude-haiku-4-5-20251001."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from orchestrator.models import Caller
from orchestrator.proxy.adapters.claude_api import ClaudeAPIAdapter

logger = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5-20251001"


class PAHaikuAdapter(ClaudeAPIAdapter):
    name = "pa_haiku"
    allowed_callers: set[Caller] = {
        Caller.PA,
        Caller.MAKER,
        Caller.JOB_RUNNER,
        Caller.CTO_SUBAGENT,
    }

    def __init__(self, client: Any = None, db: Any = None) -> None:
        super().__init__(client=client, db=db, default_model=HAIKU_MODEL)

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
                "(session_id, job_id, timestamp, adapter, tokens_in, tokens_out, cost_usd, tier) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, None, ts, self.name, tokens_in, tokens_out, cost_usd, "pa-haiku"),
            )
            await self._db.execute(
                "UPDATE sessions SET cost_to_date_usd = cost_to_date_usd + ? WHERE id = ?",
                (cost_usd, session_id),
            )
            await self._db.commit()
        except Exception as exc:
            logger.error("pa_haiku: failed to write cost_ledger row: %s", exc)
