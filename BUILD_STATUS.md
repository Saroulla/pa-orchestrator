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

## Phase 1 — MVP Core

| Step | Name | Model | Wave | Depends On | Status |
|------|------|-------|------|------------|--------|
| 0 | Prerequisites | Haiku | W0 | — | todo |
| 1 | Repo skeleton | Haiku | W1 | 0 | todo |
| 2 | Core models | Sonnet | W2 | 1 | todo |
| 3 | YAML guardrails + hot-reload | Sonnet | W2 | 1 | todo |
| 4 | SQLite store | Sonnet | W3 | 2 | todo |
| 5 | Token counting + history | Sonnet | W4 | 4 | todo |
| 6 | Intent parser + Mode FSM | Sonnet | W3 | 2 | todo |
| 7 | Escalation engine | Sonnet | W4 | 4 | todo |
| 8 | Proxy dispatcher | Sonnet | W4 | 3, 5, 6 | todo |
| 9a | ClaudeAPIAdapter | Opus | W5 | 8, 5 | todo |
| 9b | ClaudeCodeAdapter | Opus | W7 | 10 | todo |
| 9c | BraveSearchAdapter | Sonnet | W5 | 8 | todo |
| 9d | FileReadAdapter | Sonnet | W5 | 8 | todo |
| 9e | FileWriteAdapter | Sonnet | W5 | 8 | todo |
| 10 | Spawner + reaper + brief generator | Opus | W6 | 9a | todo |
| 11 | FastAPI main | Opus | W8 | 9b, 9a, 9c, 9d, 9e, 6, 7, 12 | todo |
| 12 | Telegram connector | Sonnet | W4 | 4 | todo |
| 13 | PA CLAUDE.md wiring | Sonnet | W8 | 11 | todo |
| 14 | Web UI | Sonnet | W2 | 1 | todo |
| 15 | Cloudflare Tunnel | Haiku | W9 | 11 | todo |
| 16 | E2E gate test | Opus | W10 | 15 | todo |

---

## Phase 1.2 — Workflow Engine

| Step | Name | Model | Wave | Depends On | Status |
|------|------|-------|------|------------|--------|
| 17 | Scheduler subprocess | Sonnet | W11 | 16 | todo |
| 18 | Job runner | Sonnet | W11 | 16 | todo |
| 19 | PA plan-author flow + @rebuild-plan | Sonnet | W12 | 17, 18 | todo |
| 20 | PlaywrightAdapter | Sonnet | W11 | 16 | todo |
| 21 | PDFExtractAdapter | Sonnet | W11 | 16 | todo |
| 22 | EmailAdapter | Sonnet | W11 | 16 | todo |
| 23 | TemplateAdapter | Sonnet | W11 | 16 | todo |
| 24 | Async job notification | Sonnet | W12 | 17, 18 | todo |
| 25 | Interest profile read/update flow | Sonnet | W12 | 19 | todo |

---

## Wave Summary (parallel execution groups)

| Wave | Steps | Gate to start |
|------|-------|---------------|
| W0 | 0 | — |
| W1 | 1 | Step 0 done |
| W2 | 2, 3, 14 | Step 1 done |
| W3 | 4, 6 | Step 2 done |
| W4 | 5, 7, 8, 12 | Steps 3, 4, 6 all done |
| W5 | 9a, 9c, 9d, 9e | Steps 5, 8 all done |
| W6 | 10 | Step 9a done |
| W7 | 9b | Step 10 done |
| W8 | 11, 13 | Steps 9a, 9b, 9c, 9d, 9e, 6, 7, 12 all done |
| W9 | 15 | Step 11 done |
| W10 | 16 | Step 15 done |
| W11 | 17, 18, 20, 21, 22, 23 | Step 16 done |
| W12 | 19, 24, 25 | Steps 17, 18 done |
