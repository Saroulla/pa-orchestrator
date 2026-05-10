# CLAUDE.md — Personal Assistant Orchestrator (v3)

> Authoritative spec. Architectural questions answered here; cross-references to deeper docs in `01.Project_Management/`.
> **If a constraint seems wrong or surprising**, read the full rationale before overriding it: `C:\Users\Mini_PC\.claude\plans\this-example-of-pdf-twinkling-kite.md` — every architectural decision is explained there with the reasoning that produced it.

## Communication Style
Caveman mode in this doc: strip filler, keep essential words, technical precision. No explanations unless asked.

---

## Project Overview
Single-node Personal Assistant orchestrator on a mini PC. ONE Python service (PA) parses Web UI + Telegram input, routes intents via in-process Proxy Layer to adapters and on-demand sub-agents, enforces YAML guardrails, maintains per-session mode state. All intelligence via Claude API. No Docker. No Redis. No local LLM.

Single user, personal use. User describes workflows in natural language; PA designs, files, and runs them.

**MVP target:** Phase 1 (16 build steps). Phase 1.2 adds the workflow engine.

---

## Build Coordination — Read This Before Any Work

**Every agent must do this before touching any code:**

1. Read `BUILD_STATUS.md` at the repo root.
2. Find your target step row. Apply this decision table:

| Your step shows | Action |
|-----------------|--------|
| `done` | Stop. Step already complete. Tell the user and ask what to do next. |
| `in_progress` | Stop. Another agent has claimed this step. Tell the user — do not duplicate work. |
| `todo` but a dependency is not `done` | Stop. List which dependencies are blocking. Tell the user what needs to complete first. |
| `todo` and all dependencies are `done` | Claim it: edit the row, change `todo` → `in_progress \| <YYYY-MM-DD HH:MM>`. Then proceed. |

3. When your gate passes: edit your row, change `in_progress \| ...` → `done`. Do this before ending the session.

Agents waiting on a dependency: check `BUILD_STATUS.md` to see when the blocking step moves to `done`. When it does, you are clear to proceed.

---

## Hardware (mini PC — design constraints)

| Spec | Value | Implication |
|------|-------|-------------|
| OS | Windows 11 Pro | `subprocess.terminate()`/`.kill()`. `creationflags=CREATE_NEW_PROCESS_GROUP`. No POSIX signals. |
| CPU | AMD Ryzen 3 4300U — 4 cores / 4 threads @ 2.7 GHz | 1 uvicorn worker + 1 scheduler subprocess = 2 working processes. CPU-heavy tasks must be off main loop. |
| RAM | 16 GB usable ~14.9 GB | Footprint target ~250 MB for PA process. Hard cap 2 concurrent CTO subprocesses (~500 MB-1 GB each). |
| Disk | 477 GB total, 403 GB free | Per-session workspace cap 500 MB. Log rotation at 100 MB. Chromium bundle ~200 MB acceptable. |
| GPU | Radeon integrated | Unused. No local inference. |

### Process model

| Process | What | Why |
|---------|------|-----|
| `uvicorn orchestrator.main:app --workers 1` | FastAPI: web routes, Telegram webhook, WS, events_consumer | Single worker eliminates cross-worker WebSocket push problem. asyncio handles concurrency for one user. |
| `python -m orchestrator.scheduler_main` (Phase 1.2) | APScheduler 3.10 + job_runner | Crash-isolation; long jobs cannot block chat. |
| `cloudflared.exe` (Windows service) | Public ingress for Telegram webhook only | Native binary, lighter than container. |
| `claude.exe` subprocess (on-demand) | CTO sub-agent | Spawned per session; reaped on idle/switch. Hard cap 2 concurrent. |

---

## Repo Structure

```
_REPO/
├── orchestrator/
│   ├── main.py              # FastAPI app + lifespan + events_consumer
│   ├── scheduler_main.py    # Separate scheduler process [Phase 1.2]
│   ├── config.py            # YAML guardrails loader + watchdog hot-reload
│   ├── models.py            # Mode, Channel, Intent, Result, Session, Caller, Event
│   ├── parser.py            # Intent parser + @command detection
│   ├── fsm.py               # Mode FSM per (session_id, channel)
│   ├── store.py             # SQLite store (aiosqlite, WAL)
│   ├── escalation.py        # Escalation table CRUD + resolution
│   ├── events.py            # Cross-process events table consumer
│   ├── tokens.py            # Anthropic count_tokens
│   ├── history.py           # Sliding window + summary anchor
│   ├── job_runner.py        # Deterministic job executor [Phase 1.2]
│   ├── spawner.py           # Claude Code subprocess + reaper
│   ├── telegram.py          # Telegram bot router + sender
│   └── proxy/
│       ├── protocol.py      # Tool ABC + Caller enum + Result/Intent
│       ├── dispatcher.py    # Route intent → adapter + retry/backoff + Caller check
│       └── adapters/
│           ├── claude_api.py        # Streaming SSE + prompt caching
│           ├── claude_code.py       # NDJSON envelope on stdio + PA-voice wrappers
│           ├── brave_search.py
│           ├── file_read.py         # Path traversal protection
│           ├── file_write.py        # Caller-scoped allowlist + atomic
│           ├── playwright_web.py    # [Phase 1.2]
│           ├── pdf_extract.py       # [Phase 1.2]
│           ├── email_send.py        # [Phase 1.2]
│           └── template_render.py   # [Phase 1.2]
├── web-ui/
│   └── src/
│       ├── App.tsx
│       ├── Terminal.tsx
│       ├── ws.ts
│       └── parser.ts
├── config/
│   ├── guardrails.yaml
│   ├── interests.md         # [Phase 1.2]
│   └── templates/           # [Phase 1.2]
├── jobs/                    # [Phase 1.2]
├── sessions/                # gitignored — sub-agent workdirs
├── logs/                    # gitignored
├── orchestrator.db          # gitignored — SQLite, WAL
├── requirements.txt
├── run.ps1
├── .env / .env.example
├── CLAUDE.md                # this file
├── 01.Project_Management/
│   ├── build.md             # Build sequence
│   ├── arch_diagram.md      # Mermaid
│   ├── job-system.md        # Job file format + executor [Phase 1.2]
│   ├── adapter-spec.md      # All adapter contracts
│   ├── sub-agent-pattern.md # CTO NDJSON envelope spec
│   ├── security-model.md    # Path security + caller restrictions
│   └── escalation-model.md  # Escalation table state machine
└── .gitignore
```

---

## Tech Stack

| Layer | Choice | Notes |
|-------|--------|-------|
| Runtime | Python 3.14, FastAPI, uvicorn (1 worker) | `--loop uvloop` not on Windows; default loop. |
| Session store | SQLite + aiosqlite | WAL mode, busy_timeout=5s |
| Scheduler | APScheduler 3.10 + SQLAlchemyJobStore | Stable on Windows; 4.x is beta |
| Mobile | Telegram Bot API, python-telegram-bot 21+ | Webhook via Cloudflare Tunnel |
| Search | Brave Search API | REST |
| Web automation | Playwright (Chromium headless) [Phase 1.2] | No Xvfb on Windows |
| PDF | PyMuPDF (fitz) [Phase 1.2] | Text + image extract |
| Email | aiosmtplib [Phase 1.2] | HTML + text |
| Templates | Jinja2 [Phase 1.2] | `config/templates/` |
| Token counting | anthropic SDK `count_tokens` | Real counts; not character estimates |
| Public ingress | cloudflared.exe (Windows service) | Telegram webhook only |
| Sub-agent | claude CLI subprocess | NDJSON envelope on stdout |
| Web UI | Vite + React + TypeScript | Bound to 127.0.0.1 |
| Cost cap | $5 USD / session / day, hard kill | Enforced pre-dispatch |

---

## @ Command Map

| Command | Behaviour | LLM call? |
|---------|-----------|-----------|
| `@CTO` | Switch to CTO mode; next message = first request | One Claude call to write task brief at spawn |
| `@PA` | Switch back to PA | None |
| `@cost` | Instant SQLite cost lookup | None |
| `@Desktop` | Stub: "Coming in Phase 1.2" | None |
| `@rebuild-plan <path>` [Phase 1.2] | Regenerate `## Execution Plan` for a job file | One Claude call |

Rules:
- First-token only. `tell me about @CTO patterns` → `@` is literal.
- `\@CTO` → escape, literal text.
- Mode persists across messages until explicitly switched.
- PA always the face. Sub-agent output routes through PA's wrapper layer before reaching the user (see § Sub-agent pattern).

---

## Mode FSM (per session)

```
PA ──@CTO──▶ CTO ──@PA──▶ PA
PA ──@Desktop──▶ DESKTOP_STUB ──(any input)──▶ PA
```

- Per `(session_id, channel)`. Web and Telegram maintain independent state for the same logical session.
- Telegram `@Desktop` is the stub same as web during Phase 1; built out in Phase 2.

---

## Intent / Result / Caller schema

```python
class Caller(StrEnum):
    PA = "pa"
    CTO_SUBAGENT = "cto_subagent"
    JOB_RUNNER = "job_runner"

@dataclass
class Intent:
    kind: Literal["reason","code","search","file_read","file_write",
                  "external_api","desktop","plan_step"]
    payload: dict
    session_id: str
    mode: Literal["PA","CTO","DESKTOP"]
    caller: Caller
    deadline_s: float
    attempt: int

@dataclass
class Result:
    ok: bool
    data: Any | None
    error: ErrorDetail | None
    cost_usd: float
    meta: dict   # tool, latency_ms, tokens_in, tokens_out
```

## Tool Protocol

```python
class Tool(Protocol):
    name: str
    allowed_callers: set[Caller]
    async def invoke(self, payload: dict, deadline_s: float, caller: Caller) -> Result: ...
    async def health(self) -> bool: ...
    @property
    def manifest(self) -> AdapterManifest: ...   # for job plan validation
```

Adapters:
- **MVP:** ClaudeAPIAdapter, ClaudeCodeAdapter, BraveSearchAdapter, FileReadAdapter, FileWriteAdapter (caller-scoped)
- **Phase 1.2:** PlaywrightAdapter, PDFExtractAdapter, EmailAdapter, TemplateRenderAdapter

Full manifests: `01.Project_Management/adapter-spec.md`.

---

## Error Codes

`TIMEOUT | RATE_LIMIT | TOOL_ERROR | QUOTA | BAD_INPUT | UNAUTHORIZED | INTERNAL`

All errors carry `retriable: bool`.

---

## Sub-Agent Pattern (CTO)

Output protocol: **NDJSON envelope on stdout** — one JSON object per line:

```json
{"phase": "plan",   "content": "...", "needs_confirmation": true}
{"phase": "action", "content": "..."}
{"phase": "result", "content": "...", "files_changed": [...], "summary_needed": false}
{"phase": "error",  "content": "...", "code": "..."}
```

Free-text on stdout that is not envelope-conformant goes to stderr (logged, never to user). Stderr is captured to `sessions/{id}/cto.err.log`.

PA-side wrappers (deterministic, NO LLM call) translate phases to user-facing PA voice:
- `plan + needs_confirmation` → templated confirmation prompt + escalation row
- `action` → streamed `→ ...` line
- `result + summary_needed=false` → templated done message
- `result + summary_needed=true` → ONE Claude call to synthesise (rare path)
- `error` → escalation row with `(a) retry (b) abort`

Sub-agent CLAUDE.md (written by spawner) instructs CTO to use this envelope. Spec: `01.Project_Management/sub-agent-pattern.md`.

Per-session workspace:
```
sessions/{session_id}/
├── .claude/
│   ├── CLAUDE.md         ← spawner-written, includes task brief (1 Claude call)
│   └── skills/
│       └── code.md       ← what this agent can do
└── workspace/            ← FileWriteAdapter scope for this CTO instance
```

Cleanup: `proc.terminate()` → 5s → `proc.kill()`. Workspace GC after 24h. Hard cap 2 concurrent CTO procs; reaper kills oldest on breach.

Env: scrubbed of host secrets, only explicit allowlist injected.

---

## Job System (Phase 1.2)

**Three job types:**
- **Option A** — Recurring job. File at `jobs/{name}.md`. PA creates via FileWriteAdapter. Cron-scheduled.
- **Option B** — One-off. Direct adapter execution, no file. Cost in `cost_ledger`.
- **Option C** — Interest profile at `config/interests.md`. Read by PA before research jobs to calibrate relevance.

**Job file format:**

```markdown
# jobs/<name>.md

## What I want
<plain English>

## Execution Plan
<!-- machine-generated; regenerate with @rebuild-plan -->
```yaml
version: 1
trigger: { cron: "...", timezone: "..." }
steps: [ {id, adapter, params}, ... ]
```

## Last Run
<auto-updated>
```

**Determinism:** runtime executor reads only the YAML block. Zero LLM calls per scheduled run. Plan is generated once at job creation (one Claude call) and validated against adapter manifests. `jobs.plan_checksum` (SHA256 of `## What I want`) detects user edits and triggers a regeneration escalation.

Full spec: `01.Project_Management/job-system.md`.

---

## SQLite Schema

See `01.Project_Management/build.md` § Step 4 for the full DDL. Tables:

- `sessions` (id, channel, mode, cc_pid, telegram_chat_id, cost_to_date_usd, summary_anchor, timestamps)
- `messages` (id, session_id, role, content, tokens, created_at) — replaces in-row history JSON
- `escalations` (id, session_id, channel, options, context, status, expires_at)
- `events` (id, session_id, channel, kind, payload, delivered, timestamps) — cross-process push channel
- `jobs` (id, name, file_path, cron, plan_checksum, enabled, ...) [Phase 1.2]
- `job_runs` (id, job_id, status, result_summary, cost_usd, timestamps) [Phase 1.2]
- `cost_ledger` (id, session_id, job_id, adapter, tokens, cost_usd, timestamp)

PRAGMAs: `journal_mode=WAL; synchronous=NORMAL; busy_timeout=5000`.

---

## API Contracts

### Web UI ↔ PA
```
POST /v1/chat
  { session_id, text, channel: "web" }
  → { response, mode, attachments, cost_usd, latency_ms }

WS /v1/stream/{session_id}
  ← {event: "token"|"status"|"done"|"error"|"escalation"|"job_complete", data}

GET /v1/session/{id}  → { mode, message_count, started_at, last_active }

POST /v1/jobs/{id}/run  → { run_id, status }   [Phase 1.2]
```

### Telegram ↔ PA
- Inbound: webhook `POST /webhook/telegram` (Cloudflare Tunnel only)
- Outbound: `bot.send_message(chat_id, text)` via python-telegram-bot
- Long output (>4000 chars) → send as attached `.md` file
- Telegram user → `session_id` via deterministic hash of chat_id; `telegram_chat_id` stored on sessions row for proactive messages

---

## Token Budget / History

- Each `messages` row carries `tokens` from `anthropic.count_tokens` (real counts).
- Build context per request: cached system prompt + cached `summary_anchor` + recent K turns (newest backward) until `sum(tokens) ≤ max_input_tokens - max_output_tokens`.
- When turns fall out of window, append to compress buffer; when buffer ≥ 4000 tokens, ONE Claude call compresses → new `summary_anchor`.
- Anthropic prompt caching used for system prompt + summary anchor (~10% cost on cached portions).

Guardrails fields:
```yaml
budgets:
  per_session_usd_per_day: 5.00
  max_input_tokens: 12000
  max_output_tokens: 4000
  hard_kill_on_breach: true
```

---

## YAML Guardrails (config/guardrails.yaml)

```yaml
failure_policy:
  defaults:
    timeout: retry_2x_then_escalate
    rate_limit: queue_request
    tool_error: log_and_escalate
    quota: log_and_escalate
    bad_input: log_and_escalate
  by_intent:
    code:    { timeout: retry_1x_then_escalate }
    search:  { tool_error: fail_silent }

retry:
  backoff_base_ms: 500
  backoff_factor: 2.0
  max_attempts: 3

budgets:
  per_session_usd_per_day: 5.00
  max_input_tokens: 12000
  max_output_tokens: 4000
  hard_kill_on_breach: true

escalation:
  default_ttl_seconds: 600        # 10 min
  on_expiry: skip                 # skip | retry | cancel
  on_non_matching_reply: cancel_and_passthrough

tool_access:
  claude_api:    enabled
  claude_code:   enabled
  brave_search:  enabled
  file_read:     enabled
  file_write:    enabled          # Item H — caller-scoped allowlist enforces safety
  playwright:    phase_1_2
  pdf_extract:   phase_1_2
  email_send:    phase_1_2
  template:      phase_1_2

file_write:
  max_bytes: 10485760             # 10 MB per write
  enabled_for: [pa, cto_subagent, job_runner]

context_switch:
  pa_to_cto: allowed
  cto_to_pa: allowed
  pa_to_desktop: stub_only        # Phase 1; built in Phase 2

sub_agent:
  hard_cap_concurrent: 2
  idle_kill_minutes: 15
  workspace_size_mb: 500

logging:
  destination: file
  path: logs/audit.jsonl
  rotate_mb: 100
  user_visible: false
```

---

## Security Rules

- `.env` never logged, never committed (`.gitignore`).
- Audit log redaction filter on for known secret patterns.
- Telegram webhook accepts only via Cloudflare Tunnel hostname; reject direct hits.
- Web UI bound to `127.0.0.1` only — never exposed externally.
- Outbound: no inbound rules from local machine; outbound 443 only via tunnel + APIs.
- FileWriteAdapter: caller-scoped allowlist (`security-model.md`). PA cannot write outside `jobs/`, `config/interests.md`, `config/templates/`, active session workspace. CTO subagent cannot escape its own session workspace. Job runner cannot escape job-scoped workspace.
- Path validation: `Path.resolve(strict=False) + os.path.realpath + Path.is_relative_to(allowed_root)` — handles `..`, symlinks, junctions on Windows.
- session_id regex: `^[a-zA-Z0-9_-]{8,64}$`.
- Sub-agent env: scrubbed; explicit allowlist only.
- Cost: hard kill on $5/day breach.

Full model: `01.Project_Management/security-model.md`.

---

## Escalation Pattern

When an error or confirmation is needed, PA:
1. Writes an `escalations` row with options `{a: ..., b: ..., c: ...}` and TTL.
2. Sends user the prompt: `"Tried X, hit error Y. (a) retry (b) skip"`.
3. Waits for next user message.

On next message:
- Match against option keys (case-insensitive, trimmed, single token) → resolve.
- Non-matching reply → escalation auto-cancels with notice; new message processed normally.
- TTL expiry → auto-resolve as `skip`; user notified.

Race condition (rare with single uvicorn worker, possible with scheduler subprocess): `BEGIN IMMEDIATE` + `WHERE status='pending'` + rowcount check. Loser proceeds as if no escalation.

Full state machine: `01.Project_Management/escalation-model.md`.

---

## Cross-Process Notification (Item F)

Single uvicorn worker means web/Telegram traffic and WS connections share one process. The scheduler runs in a separate process. They communicate via the SQLite `events` table:

- Producer: scheduler/job_runner inserts row.
- Consumer: `events_consumer` asyncio task inside FastAPI, polling every 500ms, dispatches to:
  - WebSocket (if connected for that session_id)
  - Telegram `bot.send_message` (using `telegram_chat_id`)
- Marks `delivered=1` on success. Failures stay `delivered=0` for retry.
- Telegram outbound rate-limited via token bucket (30/sec global, 1/sec per chat).

---

## Phased Rollout

### Phase 1 — MVP (16 build steps; see build.md)
- Repo + run.ps1 + cloudflared service
- Models, store, escalation engine, events table
- 5 MVP adapters (claude_api, claude_code, brave_search, file_read, file_write)
- Spawner with NDJSON envelope handling + brief generator
- FastAPI app: chat, WS, telegram webhook, events_consumer
- Web UI terminal
- E2E gate: @CTO write file → confirm via escalation → @PA describes → @cost works → Telegram round-trip works

### Phase 1.2 — Workflow Engine (8 build steps)
- Scheduler subprocess (APScheduler 3.10 + SQLAlchemyJobStore)
- Job runner (deterministic execution of `## Execution Plan`)
- PA's plan-author flow + `@rebuild-plan`
- 4 Phase 1.2 adapters (playwright, pdf_extract, email, template)
- Async job notification through events table
- Interest profile read/update flow

### Phase 2 — Autonomy + Observability
- @Desktop computer use (separate design phase before building)
- Calendar, GitHub adapters
- JSON audit log + loguru rotation
- Smoke suite (20 scripted intents)

---

## Key Risks

| Risk | Mitigation |
|------|-----------|
| Cloudflare Tunnel flap | Telegram retries failed webhook; cloudflared restarts via Windows service |
| Claude Code zombie procs | Reaper every 60s; PID in sessions; `CREATE_NEW_PROCESS_GROUP` |
| Token cost runaway | YAML hard-kill on $5/day breach; pre-dispatch budget check |
| Session loss on reboot | SQLite WAL persists everything |
| Sub-agent context bleed | Per-session workspace; scrubbed env; CTO FileWriteAdapter scoped to its workspace |
| RAM exhaustion (2x CTO procs) | Hard cap 2 concurrent in spawner; kill oldest on breach |
| Scheduler crash | Separate subprocess; restart loop in run.ps1; missed runs skipped via `misfire_grace_time=300` |
| WebSocket cross-worker push | Solved by single-uvicorn-worker + events table polling |
| FileWrite path escape | Resolve + realpath + is_relative_to, plus caller-scoped allowlists |
| Plan staleness after user edit | `plan_checksum` mismatch triggers `@rebuild-plan` escalation |
| Telegram rate limit | Token-bucket on outbound sender |

---

## Testing Strategy

- **Unit:** intent parser, mode FSM, YAML loader, error mapping, escalation resolution algorithm, path validation, NDJSON envelope parser, plan validation against adapter manifests. Pure functions, no I/O.
- **Integration:** each adapter against real service or recorded fixture. SQLite round-trip. Spawner with real claude.exe. CTO FileWrite scope enforcement (assert session A cannot write to session B workspace). Cross-process events delivery (write event in scheduler process → consume in API process).
- **E2E (MVP gate):** see Verification Plan in plan v3.
- **Smoke (Phase 2):** 20 scripted intents through both channels.

---

## Do Not

- Do not expose Web UI externally.
- Do not log secrets.
- Do not run more than 1 uvicorn worker (breaks cross-worker WS push).
- Do not run more than 2 concurrent CTO subprocesses.
- Do not use Kubernetes or Docker.
- Do not use Redis.
- Do not call Claude API on every scheduled job execution (job runner is deterministic).
- Do not use the two-call sub-agent pattern (CTO output → second Claude call to rephrase). Use NDJSON envelope + templated wrappers; LLM synthesis only when CTO sets `summary_needed=true`.
- Do not allow CTO subagent to write outside its own workspace.
- Do not allow PA to write outside `jobs/`, `config/interests.md`, `config/templates/`, or its own session workspace.
- Do not silently retry past `max_attempts` — escalate to user.
- Do not use POSIX-only process APIs (`prctl`, `SIGTERM`, `SIGKILL`). Windows: `subprocess.terminate()` / `.kill()`.
- Do not build `@Desktop` open shell. Phase 1.2 is stub; Phase 2 is allowlisted computer-use only.
- Do not use APScheduler 4.x (beta). Use 3.10.
- Do not store history as JSON in sessions row. Use `messages` table with per-row token counts.
- Do not put `pending_escalation` on sessions row. Use `escalations` table.

---

## Skills — Agent Slash Commands

Before starting any implementation task, check this table. Skills are in `.claude/commands/`. Invoke with `/skill-name`.

| You are about to… | Run first |
|---|---|
| Implement a numbered build step from `build.md` | `/build-step <N>` |
| Create a new Tool Protocol adapter | `/new-adapter <name>` |

**How skills work:** each skill file contains the full procedure, embedded constraints, and the required output format. Read the skill before writing any code — it overrides your defaults for that task.

**`BUILD_STATUS.md`** is checked automatically by `/build-step` — you do not need to manage it manually.

---

## Cross-References

- Build sequence: `01.Project_Management/build.md`
- Architecture diagram: `01.Project_Management/arch_diagram.md`
- Job system spec: `01.Project_Management/job-system.md`
- Adapter contracts: `01.Project_Management/adapter-spec.md`
- Sub-agent envelope spec: `01.Project_Management/sub-agent-pattern.md`
- Security model: `01.Project_Management/security-model.md`
- Escalation state machine: `01.Project_Management/escalation-model.md`
- Original audit plan (with rationale per item): `.claude/plans/this-example-of-pdf-twinkling-kite.md`
