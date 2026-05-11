# Execution Plan: PA Orchestrator MVP (MAKER-First)

> High-level roadmap. Detailed contracts live elsewhere — this doc tells you which doc to open.
>
> **Sibling docs (all in `01.Project_Management/`):** `AGENT_ONBOARDING.md`, `BUILD_STATUS.md`, `MAKER_spec.md`, `MAKER_build.md`.
>
> **Supersedes:** the prior RAG-first execution plan that previously lived in this file. RAG is now an optional post-MVP capability (item 8 in § Post-MVP), not the primary path.

---

## Where The MVP Stands

| Concern | Status | Detail |
|---|---|---|
| Phase 1 (PA + CTO MVP) | **Archived.** | History at `BUILD_STATUS.phase1.archive.md`. |
| CTO/spawner removal | **Manual prerequisite, not a tracked step.** | List at `AGENT_ONBOARDING.md` § Prerequisite. M0 verifies it. |
| Phase 2 (MAKER iterative-goal engine) | **Active build (M0–M10).** | Status at `BUILD_STATUS.md` § Phase 2. |
| Post-MVP work | **Not started.** | See § Post-MVP below. |

The MVP gate is **M10** in `BUILD_STATUS.md` — a single end-to-end goal-execution test driven through the web UI.

---

## The Iterative-Goal MAKER (What We're Actually Building)

```
Decide (Sonnet)   ──▶  Execute (PowerShell)   ──▶  Analyze (Haiku ×5 in parallel)   ──▶  Synthesize (Sonnet)
       ▲                                                                                       │
       └───────────────────────── next iteration, capped at 10 ─────────────────────────────────┘
```

Full design rationale, file layout, interface contracts, and prompt templates live in `MAKER_spec.md`. Per-step gate tables live in `MAKER_build.md`. Do not re-derive any of that here.

---

## The Build Sequence At A Glance

Eleven steps, seven waves. Two-agent parallelism is the natural cap (W2, W3, W4 each have two independent rows).

| Step | Name | Model | Wave | Depends On |
|------|------|-------|------|------------|
| M0 | Pre-flight verification (confirms manual prereq is complete) | Haiku | W0 | — |
| M1 | `maker/` package skeleton | Haiku | W1 | M0 |
| M2 | State dataclasses | Haiku | W2 | M1 |
| M3 | Safety exceptions | Haiku | W2 | M1 |
| M4 | `MAKERExecutor.run_powershell` (subprocess + Windows kill chain) | Sonnet | W3 | M2, M3 |
| M5 | `PowerShellAdapter` (Tool wrapper) | Sonnet | W4 | M4 |
| M6 | Prompt templates + `format_steps` + `goal_achieved` | Sonnet | W3 | M2 |
| M7 | `IterativeGoalExecutor` 6-phase loop | **Opus** | W5 | M4, M5, M6 |
| M8 | Unit tests | Sonnet | W4 | M4, M6 |
| M9 | Dispatcher + `@goal` parser + `Intent.kind="goal"` | Sonnet | W6 | M5, M7 |
| M10 | E2E gate | **Opus** | W7 | M9 |

Single source of truth for status (`todo` / `in_progress | timestamp` / `done`) is `BUILD_STATUS.md`. Do not duplicate it in this doc.

---

## How To Run A Step

Every step uses the same protocol (mirrors `.claude/commands/build-step.md`):

1. Read `AGENT_ONBOARDING.md` end-to-end.
2. Find your row in `BUILD_STATUS.md`. Stop if status is `done` or `in_progress`, or if any dependency is not `done`.
3. Claim the row (`todo` → `in_progress | YYYY-MM-DD HH:MM`).
4. Build per the card for your step in `MAKER_build.md` — interface contract, files, constraints, gate table.
5. Run the gate table. Every row must pass; do not edit the gate.
6. Mark the row `done`.

### Universal copy-paste prompt for any agent

```
Read 01.Project_Management/AGENT_ONBOARDING.md, then pick up step M<N>
from 01.Project_Management/BUILD_STATUS.md.

All four coordination docs (AGENT_ONBOARDING.md, BUILD_STATUS.md,
MAKER_spec.md, MAKER_build.md) live in 01.Project_Management/. Code lives
at the repo root under orchestrator/, tests/, config/, web-ui/. Run all
PowerShell commands from the repo root.

Claim the row, build it per 01.Project_Management/MAKER_build.md, run the
gate table at the bottom of your card, and mark the row done when all
rows pass. If any dependency is not yet `done`, stop and name it. Do not
edit any row other than your own.
```

Spin up the agent on the model named in the step's `Model` column — Haiku for M0–M3, Sonnet for M4/M5/M6/M8/M9, Opus for M7 and M10.

---

## Pre-Flight (Before M0)

The CTO/spawner pattern is still wired into the repo as of the start of this build. M0's gate fails until each of the following is removed:

| Path | Action |
|---|---|
| `orchestrator/spawner.py` | delete |
| `orchestrator/proxy/adapters/claude_code.py` | delete |
| `tests/test_spawner.py` | delete |
| `orchestrator/models.py` | remove `Mode.CTO` from `Mode` enum |
| `orchestrator/models.py` | remove `CTO_SUBAGENT` from `Caller` enum |
| `orchestrator/parser.py` | remove the `elif first == "@CTO":` branch |
| `orchestrator/fsm.py` | remove `Mode.CTO` references and PA↔CTO transitions |

This work is **out of scope** for the M0–M10 build sequence and is not claimable as a `BUILD_STATUS.md` row. Do it manually, then run M0 to confirm.

---

## Post-MVP (After M10)

Wrap-up and capability tracks, in roughly the order you'd want to tackle them. Each row also names which model to spin the agent up on.

| # | Activity | Model | Notes |
|---|---|---|---|
| 1 | Merge PR(s) to `master` | Haiku | Mechanical close-out. |
| 2 | Surgical `CLAUDE.md` cleanup (delete dead CTO/sub-agent sections) | Sonnet | Keep the Windows process model, $5/day cap, FileWriteAdapter scoping. Delete NDJSON envelope, CTO mode FSM, spawner reaper. |
| 3 | `git rm 01.Project_Management/Execution_Plan.md` (this file) once it stops being useful as a roadmap | Haiku | Optional. |
| 4 | Run real goals through the web UI | (user-driven) | Drives every subsequent priority. Spin up Sonnet only if you want help analysing failure modes. |
| 5 | Tune `max_iter` and `analyzer_count` based on real traces | Sonnet | Read traces, identify the bottleneck, propose new constants, make the edit. |
| 6 | Iteration-trace panel in web UI | Sonnet | React + WebSocket payload extension. |
| 7 | Per-goal soft cost cap inside the loop | Sonnet | Small edit to `iterative_goal.py`; abort if `goal_state.cost_usd > $0.50` mid-loop. |
| 8 | RAG / knowledge base track (new subproject) | Opus for planning, Sonnet for steps | Plan the same way MAKER was planned. New `RAG_spec.md` + `RAG_build.md` + rows in `BUILD_STATUS.md`. Most build rows Sonnet; one Opus row for the MAKER `Decide` integration that lets the loop choose "search docs" vs. "run powershell". |
| 9 | `@Desktop` computer-use track (new subproject) | Opus for planning, Sonnet/Opus mix for steps | Security-sensitive (sandboxing, allowlists). Don't start until items 1, 2, 4, 5 are done. |
| 10 | Telegram smoke test for `@goal` | Haiku | Send `@goal <thing>` via Telegram, verify round-trip and long-output attachment behaviour. If smoke reveals a routing bug, fix is Sonnet. |

---

## Timeline (Indicative, Not Binding)

| Block | Effort | Blocker on |
|---|---|---|
| Manual CTO prereq | 30–60 min | — |
| M0–M3 (Haiku skeleton + types) | 1 hr | prereq complete |
| M4–M6 (executor + adapter + prompts) | 2–3 hrs | M3 done |
| M7 (Opus iterative loop) | 1–2 hrs | M4 + M5 + M6 done |
| M8 (Sonnet unit tests, in parallel with M5) | 1 hr | M4 + M6 done |
| M9 (dispatcher + parser wiring) | 30–60 min | M5 + M7 done |
| M10 (Opus E2E gate) | 1–2 hrs | M9 done |

End-to-end MVP: **roughly half a working day to a full day**, including the manual prereq. The post-MVP track is open-ended.

---

## Decision Checklist (Per Step)

Before claiming any row in `BUILD_STATUS.md`:

| Check | Where to verify |
|---|---|
| All my dependencies are `done` | `BUILD_STATUS.md` § Phase 2 |
| My row is currently `todo` | same |
| I've read `AGENT_ONBOARDING.md` and the card for my step in `MAKER_build.md` | open the docs |
| I know what model to spin up on | `BUILD_STATUS.md` § Phase 2 `Model` column |
| I know the gate I need to pass | `MAKER_build.md` § Step M\<N\> § Gate |

---

## Resources

- `AGENT_ONBOARDING.md` — cold-start guide for any agent claiming a step
- `BUILD_STATUS.md` — live status board (M0–M10)
- `MAKER_spec.md` — authoritative iterative-goal spec
- `MAKER_build.md` — per-step interface contracts + gate tables
- `Project_Vision.md` — architecture (PA Haiku → MAKER → Workers → Synthesis)
- `/CLAUDE.md` (repo root) — global constraints (Windows process model, 1 uvicorn worker, no Docker/Redis, SQLite WAL, $5/day cost cap, file-write scoping). Some sections still describe legacy CTO patterns — they are scheduled for cleanup as post-MVP item #2.
- `adapter-spec.md`, `arch_diagram.md`, `security-model.md` — older Phase-1 specs; still useful for adapter and security context.
- `.env.example` (repo root) — required API keys (Anthropic for MAKER; Voyage only if/when item 8 is built).
