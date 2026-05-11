# BUILD_STATUS — PA Orchestrator

> **Agent coordination file.** Read this before touching any code.
> This file is the single source of truth for what is built, what is in progress, and what is blocked.

---

## Update Protocol

| Action | What to do |
|--------|-----------|
| **Claiming a step** | Change `todo` → `in_progress` and add timestamp: `in_progress \| 2026-05-09 10:00` |
| **Completing a step** | Change `in_progress \| ...` → `done` |
| **Blocked** | Change to `blocked \| waiting: step N` and leave a note |

**Rules:**
- Never claim a step whose dependencies are not all `done`.
- Never claim a step already marked `in_progress` — contact the user if you believe it is stale (>2h old with no update).
- After your gate passes, mark `done` immediately before ending your session.
- Only modify the row(s) for your assigned step. Do not edit other rows.

---

## Manual Prerequisite (Not Tracked Here)

Before M0, the CTO/spawner pattern must be removed manually. This work is **out of scope** for this status board and is not claimable as a step. See `AGENT_ONBOARDING.md` § Prerequisite for the exact list of deletions and edits. M0 (pre-flight verification) confirms the prereq is complete before any other MAKER step proceeds.

---

## Phase 2 — MAKER (Active)

MAKER is an iterative goal-execution engine wrapping PowerShell: `Decide (Sonnet) → Execute (PowerShell) → Analyze (Haiku ×5) → Synthesize (Sonnet)`, capped at 10 iterations. Full spec at `01.Project_Management/MAKER_spec.md`. Per-step contracts at `01.Project_Management/MAKER_build.md`.

| Step | Name | Model | Wave | Depends On | Status |
|------|------|-------|------|------------|--------|
| M0 | Pre-flight verification (confirms manual prereq is complete) | Haiku | W0 | — | todo |
| M1 | `maker/` package skeleton | Haiku | W1 | M0 | todo |
| M2 | State dataclasses (`state.py`) | Haiku | W2 | M1 | todo |
| M3 | Safety exceptions (`safety.py`) | Haiku | W2 | M1 | todo |
| M4 | `MAKERExecutor.run_powershell` (subprocess + asyncio + Windows kill chain) | Sonnet | W3 | M2, M3 | todo |
| M5 | `PowerShellAdapter` (Tool wrapper around MAKERExecutor) | Sonnet | W4 | M4 | todo |
| M6 | Prompt templates + `format_steps` + `goal_achieved` parser | Sonnet | W3 | M2 | todo |
| M7 | `IterativeGoalExecutor` — 6-phase loop with `asyncio.gather`, cost tracking, max-iter | Opus | W5 | M4, M5, M6 | todo |
| M8 | Unit tests for executor / state / prompts | Sonnet | W4 | M4, M6 | todo |
| M9 | Dispatcher wiring + `@goal` parser + `Intent.kind="goal"` | Sonnet | W6 | M5, M7 | todo |
| M10 | E2E gate: user goal → 1–3 iterations → goal-achieved → Result with cost & latency | Opus | W7 | M9 | todo |

---

## Wave Summary (parallel execution groups)

| Wave | Steps | Gate to start |
|------|-------|---------------|
| W0 | M0 | Manual prereq complete |
| W1 | M1 | M0 done |
| W2 | M2, M3 | M1 done |
| W3 | M4, M6 | M2 done (M4 also needs M3) |
| W4 | M5, M8 | M4 done (M8 also needs M6) |
| W5 | M7 | M4, M5, M6 all done |
| W6 | M9 | M5, M7 done |
| W7 | M10 | M9 done |

---

## Phase 1 — Archived

Phase 1 (PA + CTO MVP) is archived at `BUILD_STATUS.phase1.archive.md`. Do not edit the archive.
