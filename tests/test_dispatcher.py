"""Unit tests for orchestrator/proxy/dispatcher.py — Step 8 gate."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from orchestrator.config import (
    Budgets,
    ContextSwitch,
    EscalationConfig,
    FailurePolicy,
    FailurePolicyDefaults,
    FileWriteConfig,
    Guardrails,
    LoggingConfig,
    RetryConfig,
    ToolAccess,
)
from orchestrator.models import (
    AdapterManifest,
    Caller,
    ErrorCode,
    Intent,
    Mode,
    Result,
    ErrorDetail,
)
from orchestrator.proxy.dispatcher import Dispatcher

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(
    *,
    backoff_base_ms: int = 100,
    backoff_factor: float = 2.0,
    max_attempts: int = 3,
    budget_usd: float = 5.0,
) -> Guardrails:
    return Guardrails(
        failure_policy=FailurePolicy(
            defaults=FailurePolicyDefaults(
                timeout="retry_2x_then_escalate",
                rate_limit="queue_request",
                tool_error="log_and_escalate",
                quota="log_and_escalate",
                bad_input="log_and_escalate",
            )
        ),
        retry=RetryConfig(
            backoff_base_ms=backoff_base_ms,
            backoff_factor=backoff_factor,
            max_attempts=max_attempts,
        ),
        budgets=Budgets(
            per_session_usd_per_day=budget_usd,
            max_input_tokens=12000,
            max_output_tokens=4000,
            hard_kill_on_breach=True,
        ),
        escalation=EscalationConfig(
            default_ttl_seconds=600,
            on_expiry="skip",
            on_non_matching_reply="cancel_and_passthrough",
        ),
        tool_access=ToolAccess(
            claude_api="enabled",
            brave_search="enabled",
            file_read="enabled",
            file_write="enabled",
            playwright="phase_1_2",
            pdf_extract="phase_1_2",
            email_send="phase_1_2",
            template="phase_1_2",
        ),
        file_write=FileWriteConfig(
            max_bytes=10485760,
            enabled_for=["pa", "job_runner"],
        ),
        context_switch=ContextSwitch(
            pa_to_desktop="stub_only",
        ),
        logging=LoggingConfig(
            destination="file",
            path="logs/audit.jsonl",
            rotate_mb=100,
            user_visible=False,
        ),
    )


class MockCursor:
    """aiosqlite-compatible cursor mock."""

    def __init__(self, row) -> None:
        self._row = row

    async def fetchone(self):
        return self._row


class MockDB:
    """aiosqlite-compatible connection mock."""

    def __init__(self, session_cost: float = 0.0) -> None:
        self._session_cost = session_cost

    async def execute(self, sql: str, params: tuple):
        return MockCursor((self._session_cost,))


class MockEscalation:
    """Tracks create() calls."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs) -> MagicMock:
        self.calls.append(kwargs)
        m = MagicMock()
        m.id = "esc-test-id"
        return m


class MockTool:
    """Configurable mock tool for dispatch tests."""

    def __init__(
        self,
        name: str,
        allowed_callers: set[Caller],
        *,
        fail_times: int = 0,
        retriable: bool = True,
    ) -> None:
        self.name = name
        self.allowed_callers = allowed_callers
        self._fail_times = fail_times
        self._retriable = retriable
        self.invoke_count = 0
        self._manifest = AdapterManifest()

    async def invoke(self, payload: dict, deadline_s: float, caller: Caller) -> Result:
        self.invoke_count += 1
        if self.invoke_count <= self._fail_times:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.TOOL_ERROR,
                    message=f"simulated failure #{self.invoke_count}",
                    retriable=self._retriable,
                ),
            )
        return Result(ok=True, data={"answer": 42})

    async def health(self) -> bool:
        return True

    @property
    def manifest(self) -> AdapterManifest:
        return self._manifest


def make_intent(
    kind: str = "search",
    caller: Caller = Caller.PA,
    session_id: str = "test-sess-01",
) -> Intent:
    return Intent(
        kind=kind,
        payload={"query": "hello"},
        session_id=session_id,
        mode=Mode.PA,
        caller=caller,
        deadline_s=10.0,
    )


def make_dispatcher(
    *,
    budget_usd: float = 5.0,
    max_attempts: int = 3,
    backoff_base_ms: int = 100,
    backoff_factor: float = 2.0,
    escalation: MockEscalation | None = None,
) -> tuple[Dispatcher, MockEscalation]:
    esc = escalation or MockEscalation()
    cfg = make_config(
        budget_usd=budget_usd,
        max_attempts=max_attempts,
        backoff_base_ms=backoff_base_ms,
        backoff_factor=backoff_factor,
    )
    dispatcher = Dispatcher(config_getter=lambda: cfg, escalation_module=esc)
    return dispatcher, esc


# ---------------------------------------------------------------------------
# Test 1 — Successful dispatch returns Result(ok=True)
# ---------------------------------------------------------------------------

async def test_successful_dispatch_returns_ok():
    d, esc = make_dispatcher()
    tool = MockTool("search", {Caller.PA}, fail_times=0)
    d.register(tool)

    result = await d.dispatch(make_intent(kind="search", caller=Caller.PA), MockDB())

    assert result.ok is True
    assert result.data == {"answer": 42}
    assert tool.invoke_count == 1
    assert len(esc.calls) == 0


# ---------------------------------------------------------------------------
# Test 2 — Caller not in allowed_callers → UNAUTHORIZED, no retry
# ---------------------------------------------------------------------------

async def test_unauthorized_caller_no_retry():
    d, esc = make_dispatcher()
    # Tool only allows PA
    tool = MockTool("search", {Caller.PA}, fail_times=0)
    d.register(tool)

    with patch(
        "orchestrator.proxy.dispatcher.asyncio.sleep", new_callable=AsyncMock
    ) as mock_sleep:
        result = await d.dispatch(
            make_intent(kind="search", caller=Caller.JOB_RUNNER), MockDB()
        )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == ErrorCode.UNAUTHORIZED
    assert result.error.retriable is False
    assert tool.invoke_count == 0       # tool never called
    mock_sleep.assert_not_called()      # no retry
    assert len(esc.calls) == 0          # no escalation


# ---------------------------------------------------------------------------
# Test 3 — Tool fails twice then succeeds → 3 attempts, correct backoff
# ---------------------------------------------------------------------------

async def test_retry_twice_then_succeed_backoff_timing():
    d, esc = make_dispatcher(
        backoff_base_ms=100,
        backoff_factor=2.0,
        max_attempts=3,
    )
    tool = MockTool("search", {Caller.PA}, fail_times=2, retriable=True)
    d.register(tool)

    with patch(
        "orchestrator.proxy.dispatcher.asyncio.sleep", new_callable=AsyncMock
    ) as mock_sleep:
        result = await d.dispatch(make_intent(), MockDB())

    assert result.ok is True
    assert tool.invoke_count == 3

    # backoff before attempt 1: 100ms * 2^0 = 0.1 s
    # backoff before attempt 2: 100ms * 2^1 = 0.2 s
    assert mock_sleep.call_count == 2
    actual_delays = [c.args[0] for c in mock_sleep.call_args_list]
    assert actual_delays == pytest.approx([0.1, 0.2])

    assert len(esc.calls) == 0  # success path — no escalation


# ---------------------------------------------------------------------------
# Test 4 — All attempts fail → escalation created, Result(ok=False)
# ---------------------------------------------------------------------------

async def test_all_attempts_fail_creates_escalation():
    esc = MockEscalation()
    d, _ = make_dispatcher(max_attempts=3, escalation=esc)
    tool = MockTool("search", {Caller.PA}, fail_times=99, retriable=True)
    d.register(tool)

    with patch(
        "orchestrator.proxy.dispatcher.asyncio.sleep", new_callable=AsyncMock
    ) as mock_sleep:
        result = await d.dispatch(make_intent(), MockDB())

    assert result.ok is False
    assert result.error is not None
    assert result.error.retriable is False

    assert tool.invoke_count == 3          # exactly max_attempts
    assert mock_sleep.call_count == 2      # sleeps before attempt 1 and 2

    assert len(esc.calls) == 1             # one escalation row created
    assert esc.calls[0]["session_id"] == "test-sess-01"
    assert "a" in esc.calls[0]["options"]  # retry option present


# ---------------------------------------------------------------------------
# Test 5 — Budget breach → QUOTA result, tool never called
# ---------------------------------------------------------------------------

async def test_budget_breach_returns_quota():
    d, esc = make_dispatcher(budget_usd=5.0)
    tool = MockTool("search", {Caller.PA})
    d.register(tool)

    # session already at $5.00 — exactly at limit
    db = MockDB(session_cost=5.0)

    with patch(
        "orchestrator.proxy.dispatcher.asyncio.sleep", new_callable=AsyncMock
    ) as mock_sleep:
        result = await d.dispatch(make_intent(), db)

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == ErrorCode.QUOTA
    assert result.error.retriable is False
    assert tool.invoke_count == 0       # tool never reached
    mock_sleep.assert_not_called()
    assert len(esc.calls) == 0


# ---------------------------------------------------------------------------
# Test 6 — Unregistered tool kind → INTERNAL error
# ---------------------------------------------------------------------------

async def test_unregistered_tool_kind_returns_internal():
    d, esc = make_dispatcher()
    # register nothing

    result = await d.dispatch(make_intent(kind="search"), MockDB())

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == ErrorCode.INTERNAL
    assert result.error.retriable is False
    assert len(esc.calls) == 0
