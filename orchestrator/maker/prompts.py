"""Prompt templates and helpers live here."""
from __future__ import annotations

from orchestrator.maker.state import GoalState


DECIDE = """You drive a PowerShell session to achieve a goal.

Goal: {goal}

History so far:
{history}

Decide the next PowerShell command. Return ONLY the PowerShell script body — no markdown fences, no commentary. Keep it to one runnable command or short pipeline.
"""

ANALYZE = """You are one of five independent analysts. Given the goal and the latest iteration, judge whether the goal is now satisfied.

Goal: {goal}

Latest iteration:
Action: {action}
Stdout: {stdout}
Stderr: {stderr}
Exit:   {exit_code}

Respond in 1-3 sentences. End with exactly one of: ACHIEVED or NOT_ACHIEVED.
"""

SYNTHESIZE = """You are the synthesizer. Five analysts gave verdicts on whether a goal is satisfied. Merge them.

Goal: {goal}

Latest action: {action}
Latest stdout: {stdout}
Latest stderr: {stderr}
Latest exit code: {exit_code}

Analyst verdicts:
{verdicts}

Write a one-paragraph summary of where we stand. End with EXACTLY one of these tokens on its own line:
- GOAL_ACHIEVED
- GOAL_NOT_ACHIEVED
"""


def format_steps(state: GoalState) -> str:
    if not state.iterations:
        return "(no iterations yet)"
    lines = []
    for it in state.iterations:
        n = it.iteration
        stdout = it.stdout[:800] + "..." if len(it.stdout) > 800 else it.stdout
        stderr = it.stderr[:400] + "..." if len(it.stderr) > 400 else it.stderr
        lines.append(f"[Iter {n}] Action: {it.decided_action}")
        lines.append(f"[Iter {n}] Stdout: {stdout}")
        lines.append(f"[Iter {n}] Stderr: {stderr}")
        lines.append(f"[Iter {n}] Exit:   {it.exit_code}")
        lines.append(f"[Iter {n}] Synth:  {it.synthesis}")
    return "\n".join(lines)


def goal_achieved(synth_text: str) -> bool:
    tail = synth_text[-200:].upper()
    return "GOAL_ACHIEVED" in tail
