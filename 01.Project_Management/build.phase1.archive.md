# Build Sequence — Personal Assistant Orchestrator (v3)

> Authoritative build order. Each step has a gate that must pass before the next.

---

## Phase 1 MVP Core — Parallel Build Plan

> Use this section to dispatch multiple agents simultaneously. Steps within the same wave have no inter-dependencies and can run in parallel. Do not start a wave until all blocking dependencies from prior waves are ✅.

---

### Model Assignment Rationale

| Model | Use when | Cost signal |
|-------|----------|-------------|
| **Haiku** | Mechanical tasks: file/dir creation, prereq checks, CLI config, simple boilerplate | Lowest |
| **Sonnet** | Business logic, async CRUD, state machines, standard adapters, React UI, test writing | Medium |
| **Opus** | Security-critical code, complex async coordination, Windows process management, cross-component assembly, E2E validation | Highest — reserve for where subtlety matters |

---

### Dependency Graph

```
Step 0 (Haiku)
  └── Step 1 (Haiku)
        ├── Step 2 (Sonnet) ──────────────────────────────────┐
        │     └── Step 4 (Sonnet)                             │
        │           ├── Step 5 (Sonnet) ──┐                   │
        │           ├── Step 7 (Sonnet)   │                   │
        │           └── Step 12 (Sonnet)  │                   │
        │                                 │                   │
        ├── Step 3 (Sonnet) ──────────────┼── Step 8 (Sonnet) ┤
        │                                 │         │         │
        ├── Step 6 (Sonnet) ──────────────┘         │         │
        │                                           │         │
        └── Step 14 (Sonnet) [independent]          │         │
                                                    ▼         │
                                          Step 9a (Opus) ◄────┘
                                          Step 9c (Sonnet)
                                          Step 9d (Sonnet)
                                          Step 9e (Sonnet)
                                              │
                                          Step 10 (Opus)
                                              │
                                          Step 9b (Opus)
                                              │
                                     ┌── Step 11 (Opus) ──┐
                                     │   Step 13 (Sonnet) │
                                     └────────────────────┘
                                              │
                                          Step 15 (Haiku)
                                              │
                                          Step 16 (Opus)
```

---

### Critical Path (minimum wall-clock steps)

```
0 → 1 → 2 → 4 → 5 → [8 joins here] → 9a → 10 → 9b → 11 → 15 → 16
```

**10 sequential steps** on the critical path (down from 16 sequential).  
All other steps run in parallel alongside — they are not on the critical path and do not extend total build time if started on time.

---

### Wave Execution Plan

| Wave | Steps (parallel) | Depends on | Agents | Models |
|------|-----------------|------------|--------|--------|
| **W0** | 0 — Prerequisites | — | 1 | Haiku |
| **W1** | 1 — Repo skeleton | W0 ✅ | 1 | Haiku |
| **W2** | 2 — Core models<br>3 — YAML guardrails<br>14 — Web UI | W1 ✅ | 3 | Sonnet, Sonnet, Sonnet |
| **W3** | 4 — SQLite store<br>6 — Intent parser + FSM | W2 step 2 ✅ | 2 | Sonnet, Sonnet |
| **W4** | 5 — Token counting + history<br>7 — Escalation engine<br>8 — Proxy dispatcher<br>12 — Telegram connector | W3 step 4 ✅<br>W2 step 3 ✅ | 4 | Sonnet, Sonnet, Sonnet, Sonnet |
| **W5** | 9a — ClaudeAPIAdapter<br>9c — BraveSearchAdapter<br>9d — FileReadAdapter<br>9e — FileWriteAdapter | W4 step 8 ✅<br>W4 step 5 ✅ | 4 | **Opus**, Sonnet, Sonnet, Sonnet |
| **W6** | 10 — Spawner + reaper + brief | W5 step 9a ✅ | 1 | **Opus** |
| **W7** | 9b — ClaudeCodeAdapter | W6 step 10 ✅ | 1 | **Opus** |
| **W8** | 11 — FastAPI main<br>13 — PA CLAUDE.md wiring | W7 ✅, all adapters ✅, steps 6, 7, 12 ✅ | 2 | **Opus**, Sonnet |
| **W9** | 15 — Cloudflare Tunnel | W8 step 11 ✅ | 1 | Haiku |
| **W10** | 16 — E2E gate test | W9 ✅ | 1 | **Opus** |

**Peak parallel agents: 4** (waves W4 and W5).  
**Opus slots required: 5** (steps 9a, 10, 9b, 11, 16) — never concurrent with each other (all on critical path).

---

### Per-Step Model Assignment

| Step | Name | Model | Reason |
|------|------|-------|--------|
| 0 | Prerequisites | **Haiku** | Version checks, no logic |
| 1 | Repo skeleton | **Haiku** | mkdir + empty stubs + .gitignore |
| 2 | Core models | **Sonnet** | Pydantic models, enums — foundational, moderate complexity |
| 3 | YAML guardrails + hot-reload | **Sonnet** | watchdog + Pydantic validation + debounce logic |
| 4 | SQLite store | **Sonnet** | DDL + async CRUD + WAL config |
| 5 | Token counting + history | **Sonnet** | Sliding window + summary anchor — complex but well-specified |
| 6 | Intent parser + Mode FSM | **Sonnet** | State machine + parsing — well-bounded |
| 7 | Escalation engine | **Sonnet** | Table CRUD + atomic resolution — complex but spec is tight |
| 8 | Proxy dispatcher | **Sonnet** | Retry/backoff + caller routing — well-specified |
| 9a | ClaudeAPIAdapter | **Opus** | Streaming SSE + prompt caching + cost tracking — subtle async + SDK specifics |
| 9b | ClaudeCodeAdapter | **Opus** | NDJSON envelope parsing + PA-voice wrappers + async queue — most complex adapter |
| 9c | BraveSearchAdapter | **Sonnet** | Simple REST + fail_silent |
| 9d | FileReadAdapter | **Sonnet** | Path traversal protection — security-model.md specifies exactly what to do |
| 9e | FileWriteAdapter | **Sonnet** | Caller-scoped allowlist — security-model.md specifies exactly what to do |
| 10 | Spawner + reaper | **Opus** | Windows process management + CREATE_NEW_PROCESS_GROUP + brief generation — cross-cutting, platform-specific |
| 11 | FastAPI main | **Opus** | Full assembly: lifespan, events_consumer, budget enforcer, all routes, WS manager — most complex step |
| 12 | Telegram connector | **Sonnet** | Webhook router + rate limiter + outbound sender |
| 13 | PA CLAUDE.md wiring | **Sonnet** | Load file at startup, set as prompt-cached system block |
| 14 | Web UI | **Sonnet** | React terminal + WS client + @-command parser + mode indicator |
| 15 | Cloudflare Tunnel | **Haiku** | CLI commands + YAML config file |
| 16 | E2E gate test | **Opus** | Cross-component integration script — subtle failure modes |

---

### Dispatching Instructions for Wave Agents

Each agent dispatched for a step must:
1. Read `_REPO/CLAUDE.md` (v3) — full system spec
2. Read `_REPO/01.Project_Management/build.md` — this file, their specific step
3. Read any referenced spec files (e.g. `security-model.md` for steps 9d, 9e; `sub-agent-pattern.md` for steps 9b, 10; `escalation-model.md` for step 7)
4. Implement only their assigned step — do not bleed into adjacent steps
5. Write unit tests for their step before declaring done
6. Confirm gate condition passes before reporting complete

**Agents must not modify files outside their step's scope** — e.g. step 9c must not touch `store.py`.

---


## Step 0 — Prerequisites (verify before any code)

- Python 3.14 on PATH
- Node 24 on PATH (for Web UI build)
- `claude` CLI on PATH (`npm install -g @anthropic-ai/claude-code`) — version 2.1.138+
- Git on PATH; repo at `C:\Users\Mini_PC\_REPO`
- `cloudflared.exe` downloaded (Cloudflare Tunnel) — Windows binary
- API keys ready: Anthropic, Brave Search, Telegram bot token
- Telegram account created, bot created via @BotFather, bot token in hand

**Gate:** all binaries respond to `--version`.

---

## Step 1 — Repo skeleton

Create directory tree per CLAUDE.md § Repo Structure. All `__init__.py` files. Empty stubs for every module listed. `requirements.txt`, `.env.example`, `run.ps1` skeleton (echoes intent), `.gitignore` covering `.env`, `*.db`, `sessions/`, `logs/`, `web-ui/node_modules/`, `web-ui/dist/`, `__pycache__/`, `.pytest_cache/`.

`requirements.txt` (initial):
```
fastapi
uvicorn
aiosqlite
sqlalchemy
anthropic
python-telegram-bot>=21
httpx
pydantic>=2
pyyaml
watchdog
apscheduler==3.10.*
```

**Gate:** `git status` clean after first commit. `python -m orchestrator.main --help` does not error (even if it does nothing).

---

## Step 2 — Core models

`orchestrator/models.py`:

- `Mode` enum: `PA | CTO | DESKTOP`
- `Channel` enum: `web | telegram`
- `Caller` StrEnum: `pa | cto_subagent | job_runner`
- `Intent` Pydantic model
- `Result` Pydantic model
- `ErrorDetail` with `retriable: bool`
- `ErrorCode` enum: `TIMEOUT | RATE_LIMIT | TOOL_ERROR | QUOTA | BAD_INPUT | UNAUTHORIZED | INTERNAL`
- `Session` Pydantic model (matches DB schema)
- `Escalation` Pydantic model
- `Event` Pydantic model
- `AdapterManifest` Pydantic model (for job plan validation)

**Gate:** unit tests instantiate every model; field constraints enforced (e.g. invalid Mode value rejected).

---

## Step 3 — YAML guardrails loader + hot-reload

`orchestrator/config.py`:

- Pydantic schema for `guardrails.yaml` (failure_policy, retry, budgets, escalation, tool_access, file_write, sub_agent, context_switch, logging)
- Load on import; expose `get_config()`
- watchdog file observer reloads on file mutation; debounce 500ms; on validation error, KEEP previous config and log error

Write `config/guardrails.yaml` with full content from CLAUDE.md.

**Gate:** unit test — load YAML, mutate file, observe `get_config()` returns new value within 1s. Inject invalid YAML, observe error logged but `get_config()` returns last good config.

---

## Step 4 — SQLite store (aiosqlite, WAL)

`orchestrator/store.py`:

- DDL execution at startup (idempotent `CREATE TABLE IF NOT EXISTS`)
- PRAGMAs: `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`
- Async CRUD for: sessions, messages, escalations, events, cost_ledger
- (jobs / job_runs DDL present but no helpers until Phase 1.2)
- session_id regex validator: `^[a-zA-Z0-9_-]{8,64}$`

Full DDL:

```sql
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'PA',
    cc_pid INTEGER,
    telegram_chat_id INTEGER,
    cost_to_date_usd REAL NOT NULL DEFAULT 0.0,
    summary_anchor TEXT,
    created_at TEXT NOT NULL,
    last_active TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tokens INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_messages_session_time ON messages(session_id, created_at);

CREATE TABLE IF NOT EXISTS escalations (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    options TEXT NOT NULL,
    context TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    resolved_with TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_escalations_session_pending
    ON escalations(session_id, status);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0,
    delivered_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_undelivered
    ON events(delivered, created_at);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    file_path TEXT NOT NULL,
    cron TEXT NOT NULL,
    plan_checksum TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_by_session_id TEXT,
    last_run TEXT,
    next_run TEXT
);

CREATE TABLE IF NOT EXISTS job_runs (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    result_summary TEXT,
    cost_usd REAL,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS cost_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    job_id TEXT,
    timestamp TEXT NOT NULL,
    adapter TEXT NOT NULL,
    tokens_in INTEGER,
    tokens_out INTEGER,
    cost_usd REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cost_session_time ON cost_ledger(session_id, timestamp);
```

**Gate:** integration test — create session, write message, read back; create escalation, resolve atomically; insert event, mark delivered. Round-trip values match.

---

## Step 5 — Token counting + history sliding window

`orchestrator/tokens.py`:
- `count(text: str) -> int` using `anthropic.Anthropic().messages.count_tokens` (server-side)
- Tested via mock; real call reserved for integration

`orchestrator/history.py`:
- `build_context(session_id) -> list[message]` — pulls newest backwards from `messages` table until `sum(tokens) ≤ max_input_tokens - max_output_tokens`
- `slide_and_summarise(session_id)` — when older messages fall out, append to compress buffer; if buffer ≥ 4000 tokens, ONE Claude call to compress into `summary_anchor`
- System prompt + summary_anchor are emitted as cached blocks (Anthropic prompt caching)

**Gate:** unit test with seeded messages — assert window selection respects token budget; assert summary compression path triggers at threshold.

---

## Step 6 — Intent parser + Mode FSM + @cost

`orchestrator/parser.py`:
- Tokenise input (whitespace-aware, preserve original)
- First-token check for `@PA`, `@CTO`, `@cost`, `@Desktop`, `@rebuild-plan`
- Escape: leading `\@` → literal
- Map (mode, text) → Intent.kind

`orchestrator/fsm.py`:
- Per (session_id, channel) — state stored in `sessions.mode`
- Transitions: PA↔CTO, PA↔DESKTOP_STUB
- `@cost` is mode-agnostic — handled inline in the dispatcher path; returns SQLite `SELECT SUM(cost_usd)` for the session, NO LLM call

**Gate:** unit tests for all transitions, escape handling, mid-message `@` literal, `@cost` returns within 50ms.

---

## Step 7 — Escalation engine

`orchestrator/escalation.py`:
- `create(session_id, channel, options, context, ttl_seconds=600) -> Escalation`
- `pending_for(session_id) -> Escalation | None`
- `resolve_atomic(escalation_id, with_key) -> bool` (UPDATE … WHERE status='pending'; returns rowcount==1)
- `cancel(escalation_id, reason)`
- `expire_pending() -> list[expired]` (called periodically; emits events for notification)
- Resolution algorithm per `escalation-model.md`

**Gate:** unit tests:
- Create + resolve happy path
- Two concurrent resolves: only one wins
- Non-matching reply auto-cancels and returns "passthrough" signal
- TTL expiry returns "expired" signal and emits event
- Stacked escalation case (rare): newest pending wins

---

## Step 8 — Proxy dispatcher + Tool protocol

`orchestrator/proxy/protocol.py`:
- `Tool` Protocol with `name`, `allowed_callers`, `invoke`, `health`, `manifest`

`orchestrator/proxy/dispatcher.py`:
- `dispatch(intent) -> Result` — selects adapter by `intent.kind`; verifies `intent.caller in adapter.allowed_callers`; applies retry/backoff per guardrails; on terminal failure, creates an escalation and returns Result with `ok=False`
- `stream(intent) -> AsyncIterator[Event]` — for streaming adapters
- Backoff: `base_ms * factor^attempt`, cap at `max_attempts`
- Pre-dispatch budget check: `cost_to_date_usd + estimated_cost > $5/day` → reject with QUOTA error and hard kill flag

**Gate:** unit tests for retry counts, backoff timing, escalation creation on terminal failure, caller rejection (CTO calling FileWrite outside its scope returns UNAUTHORIZED), budget breach handling.

---

## Step 9 — Adapters (MVP — 5)

Build and unit test each independently. All adapters publish a `manifest` for plan validation.

### 9a — ClaudeAPIAdapter (`claude_api.py`)
- Streaming SSE via `anthropic` SDK
- `messages[]` from `history.build_context()`
- System prompt + summary_anchor sent with `cache_control: {type: "ephemeral"}` (prompt caching)
- Track `tokens_in`, `tokens_out`, `cost_usd` per call → write `cost_ledger`
- Enforce `max_output_tokens`
- Allowed callers: `{PA, JOB_RUNNER}`
- **Gate:** mock fixture test for streaming, cost calculation, cache-control on system prompt.

### 9b — ClaudeCodeAdapter (`claude_code.py`)
- Sends user request via stdin to existing claude.exe subprocess (managed by spawner)
- Reads stdout line-by-line, parses each as JSON envelope per `sub-agent-pattern.md`
- For each phase emits an event into a per-session async queue → main loop translates to PA-voice via `wrapper_templates.py` and streams to user
- `summary_needed=true` triggers ONE Claude API call (via `claude_api.py`) to synthesise
- Allowed callers: `{PA}`
- Stderr captured to `sessions/{id}/cto.err.log`
- **Gate:** mock subprocess; feed envelope sequences; assert wrapped output is PA-voice and matches templates; non-envelope stdout goes to log not user.

### 9c — BraveSearchAdapter (`brave_search.py`)
- REST call to Brave Search API
- Returns top-N results structured
- `fail_silent` per guardrails — error returns empty result list, not exception
- Allowed callers: `{PA, CTO_SUBAGENT, JOB_RUNNER}`
- **Gate:** mock response; assert structured data, fail_silent path returns empty.

### 9d — FileReadAdapter (`file_read.py`)
- Reads file contents within caller-scoped read roots
- Path traversal protection: `Path.resolve()` + `os.path.realpath` + `is_relative_to(allowed_root)`
- Max read size 50 MB
- Allowed callers: `{PA, CTO_SUBAGENT, JOB_RUNNER}` — each with their own scope
- **Gate:** unit test traversal attempt rejected (`../../etc/passwd` → UNAUTHORIZED); junction point bypass test on Windows.

### 9e — FileWriteAdapter (`file_write.py`)
- Caller-scoped allowlist per `security-model.md`
- Atomic write via tempfile + `os.replace`
- Size cap 10 MB (configurable)
- Validates session_id regex when scope is session-based
- Allowed callers: `{PA, CTO_SUBAGENT, JOB_RUNNER}` — each with their own root
- **Gate:** unit tests:
  - PA writes to `jobs/foo.md` → ok
  - PA writes to `C:\Windows\evil.exe` → UNAUTHORIZED
  - CTO writes inside its own workspace → ok
  - CTO writes inside another session's workspace → UNAUTHORIZED
  - 12MB write → BAD_INPUT (size cap)
  - Path with `..` traversal → UNAUTHORIZED

---

## Step 10 — Spawner + reaper + brief generator

`orchestrator/spawner.py`:
- `spawn(session_id, brief_context: list[message]) -> SubAgentHandle`
  1. Create `sessions/{session_id}/.claude/` and `sessions/{session_id}/workspace/`
  2. Generate `.claude/CLAUDE.md`:
     - Static section: agent role, NDJSON envelope spec, allowed adapters list, workspace path, completion protocol, env restrictions, instruction to use `[brief-update]` prefix for context refreshes
     - Task brief: ONE Claude API call summarising `brief_context` into 3-5 sentences
  3. Spawn `claude.exe` with `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP`, scrubbed env (allowlist only), stdin/stdout/stderr pipes
  4. Register PID in `sessions.cc_pid`
- `reaper()` async task: every 60s, check all CTO procs; idle > 15 min OR session is in PA mode → `proc.terminate()` → 5s grace → `proc.kill()`. Hard cap 2 concurrent — kill oldest on breach.
- Cleanup workspace after 24h idle.

**Gate:** integration — spawn real claude.exe, send envelope-conformant request, reap on idle, workspace cleanup, hard cap enforcement.

---

## Step 11 — FastAPI main

`orchestrator/main.py`:

- FastAPI app with lifespan:
  - Startup: open SQLite, run DDL, load config, start watchdog, start spawner, start reaper task, start `events_consumer` task, register Telegram webhook URL with Telegram API
  - Shutdown: cancel tasks, terminate live CTO procs, close SQLite
- Routes:
  - `POST /v1/chat` — parse → escalation interception → FSM → dispatcher → format → return
  - `WS /v1/stream/{session_id}` — registers connection in in-process `ws_manager`; relays streamed events
  - `GET /v1/session/{id}` — store summary
  - `POST /v1/jobs/{id}/run` — manual trigger (Phase 1.2 will populate)
  - `POST /webhook/telegram` — see Step 12
- Response formatter: prepends mode label `[PA]>` or `[CTO]>`, appends cost + latency footer
- Pre-dispatch budget enforcer
- `events_consumer`:
  - asyncio task loop, every 500ms `SELECT * FROM events WHERE delivered=0 LIMIT 50`
  - For each: dispatch to ws_manager (web channel) or telegram_send (telegram channel)
  - Mark delivered=1 on success
- Run with `uvicorn orchestrator.main:app --host 127.0.0.1 --port 8080 --workers 1`

**Gate:** boot via `uvicorn`; `POST /v1/chat {text:"@PA hello"}` returns 200 with PA response; WS connects and receives events; `events_consumer` ticks visible in logs.

---

## Step 12 — Telegram connector

`orchestrator/telegram.py`:

- APIRouter mounted at `/webhook/telegram`
- Inbound: parse Telegram update; verify origin (request must come via Cloudflare Tunnel hostname; check `X-Forwarded-Host`); extract `chat_id`, `text`, `user_id`; map `user_id` → `session_id` via deterministic hash; persist `telegram_chat_id` on sessions row; dispatch to chat handler (same path as web)
- Outbound: `telegram_send(chat_id, text)` via python-telegram-bot Application; long output (>4000 chars) sent as `.md` document; rate-limited via `aiolimiter` token bucket (30/sec global, 1/sec per chat)
- User allowlist: `TELEGRAM_ALLOWED_USER_IDS` env var; reject unknown with silent ignore (Telegram convention)

**Gate:** integration — POST a real Telegram update payload to `/webhook/telegram`; assert allowlisted user dispatches, non-allowlisted user is ignored. Send proactive `telegram_send` and observe message arrives.

---

## Step 13 — PA's CLAUDE.md (system prompt)

`_REPO/CLAUDE.md` (the in-repo one — already exists at v3 from this audit). The runtime PA system prompt is generated from this file plus a tool inventory section. `orchestrator/main.py` loads `CLAUDE.md` on startup and serves it as the cached system prompt block in every Claude API call.

**Gate:** PA correctly references its tools in conversation; mentions `@cost`, `@CTO`, `@Desktop` when asked what it can do.

---

## Step 14 — Web UI

`web-ui/`:
- `npm create vite@latest web-ui -- --template react-ts`
- `Terminal.tsx` — monospace component with scrollback + input line
- `ws.ts` — WebSocket client to `/v1/stream/{session_id}`; handles event types `token | status | done | error | escalation | job_complete`
- `parser.ts` — strips/recognises @ commands, sends raw text to `POST /v1/chat`
- Mode indicator in prompt: `[PA]>`, `[CTO]>`, `[DESKTOP]>`
- Error event → red-tinted line
- Escalation event → highlighted block with options
- Production build served by FastAPI StaticFiles at `/`; dev via Vite at `:3000` proxying `/v1/*` → `:8080`

**Gate:** open `http://localhost:3000`, type `@PA hello`, see streaming response.

---

## Step 15 — Cloudflare Tunnel

- `cloudflared.exe service install` — registers Windows service
- `cloudflared tunnel login`
- `cloudflared tunnel create pa-tunnel`
- Config (`%USERPROFILE%\.cloudflared\config.yml`):
  ```yaml
  tunnel: <tunnel-id>
  credentials-file: ...
  ingress:
    - hostname: pa-mini.<your-domain>
      path: /webhook/telegram
      service: http://127.0.0.1:8080
    - service: http_status:404
  ```
- Set Telegram webhook: `https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://pa-mini.<your-domain>/webhook/telegram`

**Gate:** send a Telegram message from allowlisted user → PA replies through the round trip.

---

## Step 16 — E2E gate test (MVP must pass)

Scripted automation:

1. `.\run.ps1` — starts cloudflared (Windows service ensure-running) + uvicorn
2. `POST /v1/chat {session_id: "test-mvp", text: "@CTO write hello.py with print('hello')"}`
3. Assert: response includes plan + escalation prompt; an `escalations` row exists with `status=pending`
4. `POST /v1/chat {session_id: "test-mvp", text: "a"}`
5. Assert: `sessions/test-mvp/workspace/hello.py` exists with correct content
6. Assert: response is PA-voiced (no raw NDJSON visible)
7. `POST /v1/chat {session_id: "test-mvp", text: "@PA"}`
8. `POST /v1/chat {session_id: "test-mvp", text: "what did you just do?"}`
9. Assert: response references `hello.py`
10. `POST /v1/chat {session_id: "test-mvp", text: "@cost"}`
11. Assert: numeric spend returned in <50ms; no Claude API call recorded for this turn
12. Assert non-a/b passthrough: trigger another escalation, reply with full sentence; observe escalation cancelled and message processed normally
13. Send Telegram message `@CTO write world.py` from allowlisted user
14. Reply `a` on Telegram → assert `world.py` created in same/new session

**Tag MVP only after all 14 sub-checks pass.**

---

## ──────── Phase 1.2 — Workflow Engine ────────

## Step 17 — Scheduler subprocess + APScheduler 3.10

`orchestrator/scheduler_main.py`:
- Standalone Python entrypoint; opens its own SQLAlchemy engine on `orchestrator.db`
- `AsyncIOScheduler` with `SQLAlchemyJobStore(url="sqlite:///orchestrator.db")`
- `job_defaults={coalesce: True, misfire_grace_time: 300, max_instances: 1}`
- Reads `jobs` table; for each enabled job, schedules `job_runner.run(job_id)` per cron
- On startup, syncs scheduled jobs with `jobs` table (add new, remove deleted)
- Update `run.ps1` to start this process alongside uvicorn; restart on crash

**Gate:** start scheduler, insert a job in SQLite with cron `* * * * *`, observe it fires once per minute and writes `job_runs` rows.

---

## Step 18 — Job runner (deterministic)

`orchestrator/job_runner.py`:
- `run(job_id)`:
  1. Load job row; verify `enabled=1`
  2. Load `jobs/{name}.md`; parse `## What I want` and `## Execution Plan` blocks
  3. Compute SHA256 of `## What I want` → compare with `jobs.plan_checksum`
     - Mismatch → create escalation `(a) regenerate plan now (b) run with old plan (c) skip`; insert event for user; return
  4. Validate `## Execution Plan` YAML against adapter manifests
  5. Execute steps in order; carry results forward via `$step_id.data` substitution
  6. Write `job_runs` row with status, summary, cost
  7. Insert `events` row (kind: `job_complete`) for the user notification
- ZERO Claude API calls in the happy path

**Gate:** unit test — given a fixture job file with mock adapters, runner executes steps in order, propagates step outputs, writes correct DB rows. Mismatched checksum triggers escalation.

---

## Step 19 — PA plan-author flow + @rebuild-plan

In `orchestrator/main.py` chat handler:
- When PA detects user wants a recurring job (intent classification or explicit creation request), PA:
  1. Confirms the trigger (cron) and steps with the user (back-and-forth)
  2. Calls Claude API with the adapter manifest registry: "Convert this English request into a valid Execution Plan YAML"
  3. Validates returned YAML against manifests
  4. Writes `jobs/{name}.md` with both blocks via FileWriteAdapter
  5. Inserts `jobs` row with `plan_checksum`
  6. Scheduler subprocess picks up the new job within 30s (next sync tick)

`@rebuild-plan <path>`:
- Loads file, regenerates `## Execution Plan` block via one Claude call, updates `plan_checksum`, rewrites file atomically

**Gate:** end-to-end: tell PA "check HN top 10 daily, email me at 8am" → assert file created with both blocks → assert `jobs` row exists → trigger via `POST /v1/jobs/{id}/run` → email arrives.

---

## Step 20 — PlaywrightAdapter

- `playwright install chromium` at first run
- `async_playwright` integration; headless mode default
- Adapter operations (declared in manifest):
  - `fetch_url` (returns HTML)
  - `extract_links_top_n` (custom selector or domain heuristic)
  - `screenshot` (PNG bytes)
  - `extract_text`
- Auth session storage at `sessions/{job_id}/.playwright-auth/` for sites that need login
- Allowed callers: `{PA, CTO_SUBAGENT, JOB_RUNNER}`

**Gate:** fetch HN top 10, return structured links.

---

## Step 21 — PDFExtractAdapter

- PyMuPDF (`fitz`) text extraction
- Operations: `extract_text`, `extract_text_chunked` (for long docs, chunk by page count or tokens)
- Allowed callers: `{PA, CTO_SUBAGENT, JOB_RUNNER}`

**Gate:** extract text from a sample PDF; chunked output respects token budget.

---

## Step 22 — EmailAdapter

- `aiosmtplib` (initially); env vars for SMTP host/user/pass
- HTML + plain text alternative
- Operations: `send` (to, subject, body, attachments?)
- Allowed callers: `{PA, JOB_RUNNER}`

**Gate:** send a test email to user; verify delivery.

---

## Step 23 — TemplateRenderAdapter

- Jinja2 environment loading from `config/templates/`
- Operations: `render` (template name + context dict → string)
- Allowed callers: `{PA, JOB_RUNNER}`

**Gate:** render `hn_digest.md.j2` with mock context; output matches expected.

---

## Step 24 — Async job notification

Already covered structurally by `events_consumer` (Step 11). Step 18 inserts `events` rows on job completion.

End-to-end:
- Long-running job runs in scheduler subprocess
- On completion, scheduler inserts `events` row
- FastAPI's `events_consumer` picks it up
- If user has active WS → push event
- If user is on Telegram → `telegram_send` with summary

**Gate:** kick off a job that sleeps 30s; observe Telegram message arrives within 1s of completion.

---

## Step 25 — Interest profile (Option C)

- `config/interests.md` — free-form English
- PA writes via FileWriteAdapter when user says e.g. "remember I'm interested in AI agent papers"
- PA reads as plain context before any research-type job (prepended to system prompt under a `## Your interests` heading)

**Gate:** ask PA to remember a preference; observe `config/interests.md` updated; subsequent research output reflects the preference.

---

## ──────── Phase 2 — Out of scope here ────────

Computer use (real `@Desktop`), additional adapters (Calendar, GitHub), audit log + rotation, smoke suite, multi-user RBAC. Each requires its own design pass before building.

---

## run.ps1 (final shape)

```powershell
# Ensure cloudflared service is running (installed once at Step 15)
Start-Service cloudflared -ErrorAction SilentlyContinue

# Phase 1: just uvicorn
Start-Process python -ArgumentList "-m","uvicorn","orchestrator.main:app","--host","127.0.0.1","--port","8080","--workers","1"

# Phase 1.2: also scheduler
Start-Process python -ArgumentList "-m","orchestrator.scheduler_main"
```

(Production version uses NSSM or schtasks to install both as Windows services with auto-restart.)
