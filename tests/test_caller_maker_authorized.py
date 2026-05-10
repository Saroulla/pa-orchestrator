"""R1 — ClaudeCodeAdapter + spawner accept Caller.MAKER.

Tests that:
1. ClaudeCodeAdapter allows Caller.MAKER in its allowed_callers set.
2. Dispatcher auth correctly permits Caller.MAKER code intents.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.models import Caller, ErrorCode, Intent, Mode
from orchestrator.proxy.adapters.claude_code import ClaudeCodeAdapter
from orchestrator.proxy.dispatcher import Dispatcher


class TestClaudeCodeAdapterAllowsMaker:
    def test_claude_code_adapter_allows_maker_caller(self) -> None:
        """ClaudeCodeAdapter.allowed_callers includes Caller.MAKER."""
        adapter = ClaudeCodeAdapter(
            spawner=MagicMock(),
            claude_api=MagicMock(),
            db=MagicMock(),
        )
        assert Caller.MAKER in adapter.allowed_callers


class TestDispatcherAuthPermitsMaker:
    @pytest.mark.asyncio
    async def test_dispatcher_auth_permits_maker_code_intent(self) -> None:
        """Dispatcher._auth_and_budget returns None (no rejection) for MAKER-originated code intent."""

        # Stub config with per_session_usd_per_day budget
        stub_config = MagicMock()
        stub_config.budgets.per_session_usd_per_day = 5.0

        def config_getter():
            return stub_config

        # Create dispatcher
        escalation_module = MagicMock()
        dispatcher = Dispatcher(
            config_getter=config_getter,
            escalation_module=escalation_module,
        )

        # Register ClaudeCodeAdapter
        adapter = ClaudeCodeAdapter(
            spawner=MagicMock(),
            claude_api=MagicMock(),
            db=MagicMock(),
        )
        dispatcher.register(adapter, kind="code")

        # Build intent with Caller.MAKER
        intent = Intent(
            kind="code",
            caller=Caller.MAKER,
            mode=Mode.PA,  # Mode doesn't affect auth check
            deadline_s=30.0,
            session_id="testsess__r1__",
            payload={},
        )

        # Mock DB for cost lookup
        mock_db = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=(0.0,))  # cost_to_date_usd = 0.0
        mock_db.execute = AsyncMock(return_value=mock_cursor)

        # Call _auth_and_budget
        result = await dispatcher._auth_and_budget(intent, db=mock_db)

        # Should return None (no rejection)
        assert result is None
