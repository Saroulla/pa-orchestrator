"""Unit tests for orchestrator/maker/state.py — M8 gate."""

from __future__ import annotations

import pytest

from orchestrator.maker.state import GoalState, IterationState


class TestIterationState:
    def test_required_fields_and_defaults(self):
        it = IterationState(
            iteration=1,
            decided_action="Write-Output hi",
            stdout="hi\n",
            stderr="",
            exit_code=0,
        )
        assert it.iteration == 1
        assert it.decided_action == "Write-Output hi"
        assert it.stdout == "hi\n"
        assert it.stderr == ""
        assert it.exit_code == 0
        assert it.analyses == []
        assert it.synthesis == ""
        assert it.cost_usd == 0.0
        assert it.latency_ms == 0

    def test_list_defaults_are_independent(self):
        a = IterationState(iteration=1, decided_action="x", stdout="", stderr="", exit_code=0)
        b = IterationState(iteration=2, decided_action="y", stdout="", stderr="", exit_code=0)
        a.analyses.append("verdict")
        assert b.analyses == []

    def test_slots_reject_unknown_attribute(self):
        it = IterationState(iteration=1, decided_action="x", stdout="", stderr="", exit_code=0)
        with pytest.raises(AttributeError):
            it.unknown_field = "bad"


class TestGoalState:
    def test_required_fields_and_defaults(self):
        gs = GoalState(goal="install git", session_id="sess0001")
        assert gs.goal == "install git"
        assert gs.session_id == "sess0001"
        assert gs.iterations == []
        assert gs.achieved is False
        assert gs.final_summary == ""
        assert gs.cost_usd == 0.0
        assert gs.latency_ms == 0

    def test_list_defaults_are_independent(self):
        a = GoalState(goal="g1", session_id="s1")
        b = GoalState(goal="g2", session_id="s2")
        a.iterations.append(
            IterationState(iteration=1, decided_action="x", stdout="", stderr="", exit_code=0)
        )
        assert b.iterations == []

    def test_slots_reject_unknown_attribute(self):
        gs = GoalState(goal="g", session_id="s")
        with pytest.raises(AttributeError):
            gs.extra = "bad"
