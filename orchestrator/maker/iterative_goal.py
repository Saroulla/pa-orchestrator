"""IterativeGoalExecutor lives here."""
from __future__ import annotations

import asyncio
import time

from orchestrator.maker.prompts import (
    ANALYZE,
    DECIDE,
    SYNTHESIZE,
    format_steps,
    goal_achieved,
)
from orchestrator.maker.safety import MAKERMaxIterationsError
from orchestrator.maker.state import GoalState, IterationState
from orchestrator.models import Caller, ErrorCode, Result


_DECIDE_DEADLINE_S = 30.0
_EXECUTE_DEADLINE_S = 60.0
_EXECUTE_TIMEOUT_S = 60.0
_ANALYZE_DEADLINE_S = 30.0
_SYNTHESIZE_DEADLINE_S = 30.0
_DECIDE_MAX_TOKENS = 1024
_ANALYZE_MAX_TOKENS = 512
_SYNTHESIZE_MAX_TOKENS = 1024
_DECIDE_MODEL = "claude-sonnet-4-6"
_ANALYZE_MODEL = "claude-haiku-4-5-20251001"
_SYNTHESIZE_MODEL = "claude-sonnet-4-6"
_CONSECUTIVE_TIMEOUT_LIMIT = 3


class IterativeGoalExecutor:
    def __init__(
        self,
        claude_adapter,
        ps_adapter,
        max_iter: int = 10,
        analyzer_count: int = 5,
    ) -> None:
        self._claude = claude_adapter
        self._ps = ps_adapter
        self._max_iter = max_iter
        self._analyzer_count = analyzer_count

    async def run(self, goal: str, session_id: str) -> Result:
        goal_state = GoalState(goal=goal, session_id=session_id)
        consecutive_timeouts = 0

        for i in range(1, self._max_iter + 1):
            iter_start = time.monotonic()
            iter_cost = 0.0

            decide_result = await self._claude.invoke(
                {
                    "operation": "complete",
                    "prompt": DECIDE.format(goal=goal, history=format_steps(goal_state)),
                    "model": _DECIDE_MODEL,
                    "max_tokens": _DECIDE_MAX_TOKENS,
                    "session_id": session_id,
                },
                deadline_s=_DECIDE_DEADLINE_S,
                caller=Caller.PA,
            )
            if not decide_result.ok:
                return decide_result
            iter_cost += decide_result.cost_usd
            script = decide_result.data or ""

            exec_result = await self._ps.invoke(
                {
                    "script": script,
                    "timeout_s": _EXECUTE_TIMEOUT_S,
                    "session_id": session_id,
                },
                deadline_s=_EXECUTE_DEADLINE_S,
                caller=Caller.PA,
            )
            iter_cost += exec_result.cost_usd

            if exec_result.error is not None and exec_result.error.code == ErrorCode.TIMEOUT:
                consecutive_timeouts += 1
                iter_state = IterationState(
                    iteration=i,
                    decided_action=script,
                    stdout="",
                    stderr=exec_result.error.message,
                    exit_code=-1,
                    analyses=[],
                    synthesis="",
                    cost_usd=iter_cost,
                    latency_ms=int((time.monotonic() - iter_start) * 1000),
                )
                goal_state.iterations.append(iter_state)
                goal_state.cost_usd += iter_state.cost_usd
                goal_state.latency_ms += iter_state.latency_ms
                if consecutive_timeouts >= _CONSECUTIVE_TIMEOUT_LIMIT:
                    raise MAKERMaxIterationsError(
                        f"{_CONSECUTIVE_TIMEOUT_LIMIT} consecutive PowerShell timeouts"
                    )
                continue

            consecutive_timeouts = 0
            stdout = exec_result.data["stdout"]
            stderr = exec_result.data["stderr"]
            exit_code = exec_result.data["exit_code"]

            analyze_tasks = [
                self._claude.invoke(
                    {
                        "operation": "complete",
                        "prompt": ANALYZE.format(
                            goal=goal,
                            action=script,
                            stdout=stdout,
                            stderr=stderr,
                            exit_code=exit_code,
                        ),
                        "model": _ANALYZE_MODEL,
                        "max_tokens": _ANALYZE_MAX_TOKENS,
                        "session_id": session_id,
                    },
                    deadline_s=_ANALYZE_DEADLINE_S,
                    caller=Caller.PA,
                )
                for _ in range(self._analyzer_count)
            ]
            analyze_outcomes = await asyncio.gather(*analyze_tasks, return_exceptions=True)

            analyses: list[str] = []
            for outcome in analyze_outcomes:
                if isinstance(outcome, Result) and outcome.ok:
                    analyses.append(outcome.data or "")
                    iter_cost += outcome.cost_usd
                else:
                    analyses.append("ANALYZER_FAILED")
                    if isinstance(outcome, Result):
                        iter_cost += outcome.cost_usd

            synth_result = await self._claude.invoke(
                {
                    "operation": "complete",
                    "prompt": SYNTHESIZE.format(
                        goal=goal,
                        action=script,
                        stdout=stdout,
                        stderr=stderr,
                        exit_code=exit_code,
                        verdicts="\n".join(f"- {a}" for a in analyses),
                    ),
                    "model": _SYNTHESIZE_MODEL,
                    "max_tokens": _SYNTHESIZE_MAX_TOKENS,
                    "session_id": session_id,
                },
                deadline_s=_SYNTHESIZE_DEADLINE_S,
                caller=Caller.PA,
            )
            if not synth_result.ok:
                return synth_result
            iter_cost += synth_result.cost_usd
            synthesis = synth_result.data or ""

            iter_state = IterationState(
                iteration=i,
                decided_action=script,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                analyses=analyses,
                synthesis=synthesis,
                cost_usd=iter_cost,
                latency_ms=int((time.monotonic() - iter_start) * 1000),
            )
            goal_state.iterations.append(iter_state)
            goal_state.cost_usd += iter_state.cost_usd
            goal_state.latency_ms += iter_state.latency_ms

            if goal_achieved(synthesis):
                goal_state.achieved = True
                goal_state.final_summary = synthesis
                break

        if not goal_state.achieved:
            raise MAKERMaxIterationsError(
                f"goal not achieved after {self._max_iter} iterations"
            )

        return Result(
            ok=True,
            data={"goal_state": goal_state},
            cost_usd=goal_state.cost_usd,
            meta={
                "tool": "maker",
                "iterations": len(goal_state.iterations),
                "latency_ms": goal_state.latency_ms,
                "tokens_in": 0,
                "tokens_out": 0,
            },
        )
