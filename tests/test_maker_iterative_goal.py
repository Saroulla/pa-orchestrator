"""Unit tests for orchestrator/maker/iterative_goal.py — M7 gate."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from orchestrator.maker.iterative_goal import IterativeGoalExecutor
from orchestrator.maker.safety import MAKERMaxIterationsError
from orchestrator.models import Result


def _claude_meta() -> dict:
    return {"tool": "claude_api", "latency_ms": 10, "tokens_in": 1, "tokens_out": 1}


def _ps_meta() -> dict:
    return {"tool": "powershell", "latency_ms": 5, "tokens_in": 0, "tokens_out": 0}


def _make_claude(synth_text: str):
    async def invoke(payload, *, deadline_s, caller):
        prompt = payload.get("prompt", "")
        model = payload["model"]
        if model == "claude-sonnet-4-6":
            if "Analyst verdicts" in prompt:
                return Result(ok=True, data=synth_text, cost_usd=0.002, meta=_claude_meta())
            return Result(ok=True, data="Write-Output ok", cost_usd=0.001, meta=_claude_meta())
        return Result(ok=True, data="ACHIEVED", cost_usd=0.0001, meta=_claude_meta())

    return SimpleNamespace(invoke=invoke)


def _make_ps_ok():
    async def invoke(payload, *, deadline_s, caller):
        return Result(
            ok=True,
            data={"stdout": "ok\n", "stderr": "", "exit_code": 0},
            cost_usd=0.0,
            meta=_ps_meta(),
        )

    return SimpleNamespace(invoke=invoke)


async def test_happy_path_single_iteration():
    claude = _make_claude(synth_text="Looks good.\nGOAL_ACHIEVED")
    ps = _make_ps_ok()
    executor = IterativeGoalExecutor(claude, ps)

    result = await executor.run("trivial goal", "session1")

    assert result.ok is True
    goal_state = result.data["goal_state"]
    assert goal_state.achieved is True
    assert len(goal_state.iterations) == 1
    assert "GOAL_ACHIEVED" in goal_state.final_summary
    assert result.cost_usd == pytest.approx(goal_state.cost_usd)
    assert result.meta["iterations"] == 1
    assert result.meta["tool"] == "maker"


async def test_cap_hit_raises_max_iterations():
    claude = _make_claude(synth_text="Still working.\nGOAL_NOT_ACHIEVED")
    ps = _make_ps_ok()
    executor = IterativeGoalExecutor(claude, ps, max_iter=3)

    with pytest.raises(MAKERMaxIterationsError):
        await executor.run("unachievable goal", "session2")


async def test_analyzer_failure_records_failed_marker():
    haiku_calls = {"n": 0}

    async def invoke(payload, *, deadline_s, caller):
        model = payload["model"]
        prompt = payload.get("prompt", "")
        if model == "claude-haiku-4-5-20251001":
            haiku_calls["n"] += 1
            if haiku_calls["n"] == 1:
                raise RuntimeError("analyzer crashed")
            return Result(ok=True, data="ACHIEVED", cost_usd=0.0001, meta=_claude_meta())
        if "Analyst verdicts" in prompt:
            return Result(
                ok=True, data="All good.\nGOAL_ACHIEVED", cost_usd=0.002, meta=_claude_meta()
            )
        return Result(ok=True, data="Write-Output ok", cost_usd=0.001, meta=_claude_meta())

    claude = SimpleNamespace(invoke=invoke)
    ps = _make_ps_ok()
    executor = IterativeGoalExecutor(claude, ps)

    result = await executor.run("goal", "session3")

    assert result.ok is True
    goal_state = result.data["goal_state"]
    assert "ANALYZER_FAILED" in goal_state.iterations[0].analyses
    assert goal_state.achieved is True
