"""E2E test for MAKER iterative-goal execution — M10 gate.

Runs the full Decide → Execute → Analyze → Synthesize loop against the real
Anthropic API and Windows PowerShell. Skipped when ANTHROPIC_API_KEY isn't set.
"""
from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

import os

import pytest

from orchestrator.maker.iterative_goal import IterativeGoalExecutor
from orchestrator.proxy.adapters.claude_api import ClaudeAPIAdapter
from orchestrator.proxy.adapters.powershell import PowerShellAdapter

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set; e2e test requires real API access",
)


async def test_maker_goal_end_to_end():
    claude = ClaudeAPIAdapter()
    ps = PowerShellAdapter()
    executor = IterativeGoalExecutor(claude_adapter=claude, ps_adapter=ps, max_iter=3)

    result = await executor.run(
        "Run the PowerShell command `Write-Output ok` and confirm the output is the literal word ok.",
        "makere2e0001",
    )

    assert result.ok is True, f"Goal failed: {result.error}"

    goal_state = result.data["goal_state"]
    assert goal_state.achieved is True
    assert 1 <= len(goal_state.iterations) <= 3
    assert result.cost_usd > 0
    assert result.cost_usd < 5.00
    assert result.meta["latency_ms"] > 0
    assert result.meta["tool"] == "maker"
    assert result.meta["iterations"] == len(goal_state.iterations)
