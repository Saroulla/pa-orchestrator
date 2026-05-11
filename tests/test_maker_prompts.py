"""Unit tests for orchestrator/maker/prompts.py — M8 gate."""

from __future__ import annotations

from orchestrator.maker.prompts import (
    ANALYZE,
    DECIDE,
    SYNTHESIZE,
    format_steps,
    goal_achieved,
)
from orchestrator.maker.state import GoalState, IterationState


def _make_iter(n: int, stdout: str = "", stderr: str = "", synthesis: str = "") -> IterationState:
    return IterationState(
        iteration=n,
        decided_action=f"action_{n}",
        stdout=stdout,
        stderr=stderr,
        exit_code=0,
        synthesis=synthesis,
    )


class TestFormatSteps:
    def test_empty_iterations_returns_sentinel(self):
        gs = GoalState(goal="g", session_id="s")
        assert format_steps(gs) == "(no iterations yet)"

    def test_single_iteration_renders_five_lines(self):
        gs = GoalState(goal="g", session_id="s")
        gs.iterations.append(_make_iter(1, stdout="out", stderr="err", synthesis="synth"))
        result = format_steps(gs)
        assert "[Iter 1] Action: action_1" in result
        assert "[Iter 1] Stdout: out" in result
        assert "[Iter 1] Stderr: err" in result
        assert "[Iter 1] Exit:   0" in result
        assert "[Iter 1] Synth:  synth" in result

    def test_stdout_truncated_at_800_chars(self):
        long_out = "x" * 900
        gs = GoalState(goal="g", session_id="s")
        gs.iterations.append(_make_iter(1, stdout=long_out))
        result = format_steps(gs)
        assert "[Iter 1] Stdout: " + "x" * 800 + "..." in result

    def test_stdout_not_truncated_when_exactly_800(self):
        out = "y" * 800
        gs = GoalState(goal="g", session_id="s")
        gs.iterations.append(_make_iter(1, stdout=out))
        result = format_steps(gs)
        assert "..." not in result.split("[Iter 1] Stdout:")[1].split("\n")[0]

    def test_stderr_truncated_at_400_chars(self):
        long_err = "e" * 500
        gs = GoalState(goal="g", session_id="s")
        gs.iterations.append(_make_iter(1, stderr=long_err))
        result = format_steps(gs)
        assert "[Iter 1] Stderr: " + "e" * 400 + "..." in result

    def test_multiple_iterations_rendered(self):
        gs = GoalState(goal="g", session_id="s")
        gs.iterations.append(_make_iter(1))
        gs.iterations.append(_make_iter(2))
        result = format_steps(gs)
        assert "[Iter 1]" in result
        assert "[Iter 2]" in result


class TestGoalAchieved:
    def test_goal_achieved_true(self):
        assert goal_achieved("all good\nGOAL_ACHIEVED") is True

    def test_goal_not_achieved_false(self):
        assert goal_achieved("not done\nGOAL_NOT_ACHIEVED") is False

    def test_neither_token_returns_false(self):
        assert goal_achieved("unclear verdict") is False

    def test_both_tokens_achieved_wins(self):
        # GOAL_ACHIEVED wins if both appear in the tail
        assert goal_achieved("GOAL_NOT_ACHIEVED and then GOAL_ACHIEVED") is True

    def test_case_insensitive(self):
        assert goal_achieved("goal_achieved") is True

    def test_checks_last_200_chars_only(self):
        # Put GOAL_ACHIEVED far before the tail — should NOT be found
        prefix = "GOAL_ACHIEVED" + " " * 300
        assert goal_achieved(prefix + "GOAL_NOT_ACHIEVED") is False


class TestTemplateConstants:
    def test_decide_contains_placeholders(self):
        assert "{goal}" in DECIDE
        assert "{history}" in DECIDE

    def test_analyze_contains_placeholders(self):
        assert "{goal}" in ANALYZE
        assert "{stdout}" in ANALYZE

    def test_synthesize_contains_goal_achieved_token(self):
        assert "GOAL_ACHIEVED" in SYNTHESIZE
