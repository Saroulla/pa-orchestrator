# AGENT_ONBOARDING — Cold-Start Guide

> Read this end-to-end if it's your first time on this repo. ~5 minute read. Everything you need to ship a MAKER build step is linked from here.

---

## What You're Here To Do

You're working on the PA Orchestrator MVP. The critical-path module is **MAKER**, an iterative goal-execution engine wrapping PowerShell. You've been assigned a step from `BUILD_STATUS.md`. Your job is to claim that step, build it per `01.Project_Management/MAKER_build.md`, run its gate, and mark it done. Nothing else.

---

## Prerequisite (Manual, Not A Build Step)

**Before any MAKER work, the CTO/spawner pattern must be removed from the repo.** This is not tracked in `BUILD_STATUS.md` and is not claimable as a step. It must be completed manually before M0 can pass.

Required deletions and edits:

| Path | Action |
|---|---|
| `orchestrator/spawner.py` | delete |
| `orchestrator/proxy/adapters/claude_code.py` | delete |
| `tests/test_spawner.py` | delete |
| `orchestrator/models.py:12-15` | remove `Mode.CTO` line from the `Mode` `StrEnum` |
| `orchestrator/models.py:23-26` | remove `CTO_SUBAGENT = "cto_subagent"` line from `Caller` |
| `orchestrator/parser.py:44-46` | remove the `elif first == "@CTO":` branch |
| `orchestrator/fsm.py` | remove any `Mode.CTO` references and the `PA ↔ CTO` transitions |

**M0 (pre-flight verification) confirms each item above before any other step proceeds.** If you've been assigned M0 and the prereq is incomplete, stop and report exactly which items remain.

---

## Read These In Order (5 min)

1. **`CLAUDE.md`** — global constraints (Windows process model, 1 uvicorn worker, no Docker/Redis, SQLite WAL, $5/day cost cap, file-write scoping). Note: this file still describes the legacy PA+CTO pattern in places; those sections are obsolete after the prerequisite above, but the binding constraints listed in this sentence all still hold.
2. **`01.Project_Management/Project_Vision.md`** — architecture (PA Haiku → MAKER → Workers → Synthesis). Skip the RAG-specific sections; they describe a prior design.
3. **`01.Project_Management/MAKER_spec.md`** — authoritative iterative-goal MAKER spec. This is where the design lives.
4. **`BUILD_STATUS.md`** — find your step and confirm its status is `todo` and all its dependencies are `done`.
5. **`01.Project_Management/MAKER_build.md`** — read the card for **your step only**. It contains the interface contract, files to touch, and the gate table.

> **Deprecated:** `01.Project_Management/Execution_Plan.md` describes an earlier RAG-first MAKER design that has been superseded. Do not consult it.

---

## Codebase Map

| Path | What's there | Imitate this file when adding |
|---|---|---|
| `orchestrator/` | FastAPI app, models, parser, dispatcher, stores | n/a (entry points only) |
| `orchestrator/proxy/adapters/` | Tool-protocol adapters (one per external capability) | `claude_api.py` (manifest + cost + invoke shape) |
| `orchestrator/maker/` | **Created in M1.** MAKER package: executor, iterative loop, state, safety, prompts | n/a (you may be the one creating it) |
| `config/` | YAML guardrails + templates | `guardrails.yaml` |
| `tests/` | Pytest suite (unit + integration + e2e) | `test_claude_api.py` (adapter testing pattern) |
| `web-ui/` | Vite + React + TypeScript frontend | n/a (not in scope for MAKER) |
| `01.Project_Management/` | Specs (MAKER_spec, build.md, adapter-spec, security-model, etc.) | n/a (read-only for build steps) |

---

## Tooling You'll Touch

- **`orchestrator/proxy/adapters/claude_api.py`** is the unified Anthropic adapter. The pricing table at `claude_api.py:47-51` covers Opus, Sonnet, and Haiku. Model is selected via `payload["model"]` against that table; default is `claude-sonnet-4-6` (`claude_api.py:53`). **No separate Haiku adapter is needed** — MAKER reuses this one.
- **`orchestrator/proxy/dispatcher.py`** routes `Intent`s to adapters and enforces `allowed_callers`. Adapter calls must go through it; do not call adapters ad-hoc from inside MAKER.
- **`orchestrator/proxy/protocol.py`** defines the `Tool` Protocol (~20 lines). All adapters conform to it.
- **`orchestrator/models.py`** defines `Intent`, `Result`, `Caller`, `Mode`, `ErrorDetail`, `AdapterManifest`. M2 and M9 modify this file.

---

## Claim / Build / Gate Protocol

This mirrors `.claude/commands/build-step.md` so agents not invoking the `/build-step` skill still follow the same flow.

1. **Read `BUILD_STATUS.md`.** Find the row for your step.
   - If it shows `done`: stop and tell the user.
   - If it shows `in_progress`: stop and tell the user (do not duplicate work).
   - If it shows `todo` but any dependency is not `done`: stop and name the missing dependency rows.
   - If `todo` and all dependencies `done`: continue.
2. **Claim the row.** Edit `BUILD_STATUS.md` and change `todo` → `in_progress | YYYY-MM-DD HH:MM` for your row only.
3. **Build the step** per `01.Project_Management/MAKER_build.md` § Step M\<N\>. Follow the interface contract exactly. Do not add features, error handling, or abstractions beyond what the card specifies.
4. **Run the gate table** in the card. Every row must pass. If any row fails, fix the implementation — do not edit the gate.
5. **Mark `done`.** Edit `BUILD_STATUS.md` and change `in_progress | ...` → `done` for your row only. Tell the user the step is complete.

---

## Hard Constraints (Locked — Do Not Look Up)

- **Windows subprocess:** `subprocess.terminate()` → wait 5s → `.kill()`. `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP`. **No POSIX signals.**
- **1 uvicorn worker.** No Docker. No Redis. SQLite WAL only.
- **All adapter calls go via the dispatcher.** Never instantiate an adapter ad-hoc inside MAKER — accept it as a constructor argument.
- **Cost cap:** $5/session/day. The pre-dispatch budget check already lives in `dispatcher.py` — agents implementing M5 / M9 should leave it intact.
- **Model IDs:** `claude-sonnet-4-6` for decide/synthesize, `claude-haiku-4-5-20251001` for workers, `claude-opus-4-7` only if a step is explicitly tagged Opus. All three are routed through `claude_api.py`.
- **`Result.cost_usd`** is the canonical cost field. M7 sums `Result.cost_usd` across adapter calls — do not open a parallel cost ledger.

---

## When You Finish

Edit `BUILD_STATUS.md`, change `in_progress | ...` → `done` for your row, and tell the user the step is complete.