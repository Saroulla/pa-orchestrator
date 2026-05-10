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
| 1 | Repo skeleton | Haiku | W1 | 0 | done |
| 2 | Core models | Sonnet | W2 | 1 | done |
| 3 | YAML guardrails + hot-reload | Sonnet | W2 | 1 | done |
| 4 | SQLite store | Sonnet | W3 | 2 | done |
| 5 | Token counting + history | Sonnet | W4 | 4 | done |
| 6 | Intent parser + Mode FSM | Sonnet | W3 | 2 | done |
| 7 | Escalation engine | Sonnet | W4 | 4 | done |
| 8 | Proxy dispatcher | Sonnet | W4 | 3, 5, 6 | done |
| 9a | ClaudeAPIAdapter | Opus | W5 | 8, 5 | done |
| 9b | ClaudeCodeAdapter | Opus | W7 | 10 | done |
| 9c | BraveSearchAdapter | Sonnet | W5 | 8 | done |
| 9d | FileReadAdapter | Sonnet | W5 | 8 | done |
| 9e | FileWriteAdapter | Sonnet | W5 | 8 | done |
| 10 | Spawner + reaper + brief generator | Opus | W6 | 9a | done |
| 11 | FastAPI main | Opus | W8 | 9b, 9a, 9c, 9d, 9e, 6, 7, 12 | done |
| 12 | Telegram connector | Sonnet | W4 | 4 | done |
| 13 | PA CLAUDE.md wiring | Sonnet | W8 | 11 | done |
| 14 | Web UI | Sonnet | W2 | 1 | done |
| 15 | Cloudflare Tunnel | Haiku | W9 | 11 | done |
| 16 | E2E gate test | Opus | W10 | 15 | done |

---

## Phase 1.2 — Workflow Engine

| Step | Name | Model | Wave | Depends On | Status |
|------|------|-------|------|------------|--------|
| 17 | Scheduler subprocess | Sonnet | W11 | 16 | done |
| 18 | Job runner | Sonnet | W11 | 16 | done |
| 19 | PA plan-author flow + @rebuild-plan | Sonnet | W12 | 17, 18 | done |
| 20 | PlaywrightAdapter | Sonnet | W11 | 16 | done |
| 21 | PDFExtractAdapter | Sonnet | W11 | 16 | done |
| 22 | EmailAdapter | Sonnet | W11 | 16 | done |
| 23 | TemplateAdapter | Sonnet | W11 | 16 | done |
| 24 | Async job notification | Sonnet | W12 | 17, 18 | done |
| 25 | Interest profile read/update flow | Sonnet | W12 | 19 | done |

---

## Phase 2 — MAKER

> Plan: `C:\Users\Mini_PC\.claude\plans\opus-handover-prompt-precious-clarke.md` (full spec). Streams: α (foundations + adapters + MAKER core), β (system messages + promotion), γ (job extension), δ (inversion), ε (writes + e2e). Cap = 2 slot weights; `pa-groq=1`, `pa-haiku=1`, `cto=2`. MAKER is in-process FastAPI module; hands run in scheduler subprocess.

| Step | Name | Model | Wave | Depends On | Status |
|------|------|-------|------|------------|--------|
| A1 | Caller enum + guardrails YAML schema extension | Sonnet | W13 | 25 | done |
| A2 | Scaffold config/maker/ + .example files | Haiku | W13 | 25 | done |
| A3 | Update requirements.txt + .env.example | Haiku | W13 | 25 | done |
| A4 | Extend models.py (events.message_type, cost_ledger.tier) | Sonnet | W13 | 25 | done |
| A5 | Extend store.py (helpers + migrations) | Sonnet | W13 | A4 | done |
| B1 | PAGroqAdapter | Sonnet | W14 | A1, A3 | done |
| B2 | PAHaikuAdapter | Sonnet | W14 | A1 | done |
| B3 | GoogleCSEAdapter | Sonnet | W14 | A1, A3 | done |
| B4 | HttpFetchAdapter | Sonnet | W14 | A1, A3 | done |
| B5 | ArticleExtractAdapter | Sonnet | W14 | A1, A3 | done |
| D1 | system_messages.py emit + persist | Sonnet | W14 | A4, A5 | done |
| D2 | Inline ⚙️ [SYSTEM/...] rendering in events.py | Sonnet | W14 | D1 | done |
| C1 | MAKER classifier.py (PA-groq) | Sonnet | W15 | B1 | done |
| C2 | MAKER persona.py (loader + watchdog) | Sonnet | W15 | A2 | done |
| C3 | MAKER skills.py (index + on-demand md load) | Sonnet | W15 | A2 | done |
| C4 | MAKER tools.py registry | Sonnet | W15 | A2 | done |
| C5 | MAKER admin.py (10 handlers) | Sonnet | W15 | A4, A5 | done |
| C6 | MAKER url_log.py | Sonnet | W15 | A2 | done |
| C7 | MAKER quota.py + over-quota approval flow | Sonnet | W15 | A5, B3 | done |
| D3 | promotion.py (Groq → Haiku) | Sonnet | W15 | B1, B2, D1 | done |
| E1 | jobs/maker/ namespace + job_creator.py | Sonnet | W16 | C1, C2, C3, C4, C5, C6, C7 | done |
| E2 | job_runner.py extension (skill loader + invocation) | Sonnet | W16 | E1, C3 | done |
| E3 | browser_context.py lazy-start in scheduler | Opus | W16 | E2 | done |
| E4 | Daily email job + Jinja2 template | Sonnet | W16 | E2, D1 | done |
| F1 | chat_handler thin-forwarder rewrite | Opus | W17 | E1, E2, E3, E4 | done |
| R1 | ClaudeCodeAdapter + spawner accept Caller.MAKER | Haiku | W17.5 | F1 | done |
| R2 | inline.handle guards pa_groq=None (fall through to Haiku) | Haiku | W17.5 | R1 | done |
| R3 | dispatch persistence try/except (balance user+assistant on route failure) | Sonnet | W17.5 | R2 | done |
| R4 | tests/test_maker_router.py — parse_at_prefix + direct_dispatch | Sonnet | W17.5 | R3 | done |
| R5 | tests/test_maker_inline.py — promotion + persona + None-Groq fallback | Haiku | W17.5 | R4 | done |
| R6 | tests/test_maker_dispatch.py — escalation / budget / routing / persistence | Sonnet | W17.5 | R5 | done |
| R7 | F1-remediation sweep + targeted test smoke check | Haiku | W17.5 | R6 | todo |
| F2 | parser.py @-prefix dispatch + drop @desktop. **NB: F2 must NOT edit models.py** | Sonnet | W17 | R7 | todo |
| F3 | fsm.py reduce to MAKER-default stub. **NB: F3 owns the Mode.MAKER addition to models.py** | Sonnet | W17 | F2 | todo |
| F4 | Web UI + Telegram entry-point integration | Sonnet | W17 | F3 | todo |
| G1 | CLAUDE.md full rewrite + Phase-1 archive | Opus | W18 | F1, F2, F3, F4 | todo |
| G2 | BUILD_STATUS.md Phase 2 update (post-build reconciliation) | Haiku | W18 | G1 | todo |
| G3 | Starter persona.md content | Sonnet | W18 | G1 | todo |
| G4 | Three starter skills (research-and-summarise, extract-article, compose-daily-digest) | Sonnet | W18 | C3 | todo |
| H1 | E2E gate test (Google search → drill → summary → email) | Opus | W19 | G1, G2, G3, G4 | todo |
| H2 | Smoke test on real mini PC | Haiku | W19 | H1 | todo |

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
| W13 | A1, A2, A3, A4, A5 | Step 25 done |
| W14 | B1, B2, B3, B4, B5, D1, D2 | A1, A3, A4, A5 all done |
| W15 | C1, C2, C3, C4, C5, C6, C7, D3 | W14 done |
| W16 | E1, E2, E3, E4 | W15 done |
| W17 | F1 | W16 done |
| W17.5 | R1, R2, R3, R4, R5, R6, R7 | F1 done — strict sequential remediation per 2026-05-10 decision |
| W17 (resume) | F2, F3, F4 | R7 done — F2 then F3 then F4 strictly sequential |
| W18 | G1, G2, G3, G4 | F4 done (G4 only needs C3 done) |
| W19 | H1, H2 | W18 done |
