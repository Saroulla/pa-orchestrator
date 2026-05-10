# Personal Assistant Orchestrator — Architecture Diagram (v3)

```mermaid
graph TB
    %% ── EXTERNAL SERVICES ────────────────────────────────────────────────
    subgraph EXT["External Services"]
        direction TB
        Telegram["Telegram Bot API\n(free)"]
        AnthropicAPI["Anthropic API\nClaude (Sonnet/Opus)\n(paid - per token)"]
        BraveSearch["Brave Search API\n(free tier)"]
        SMTP["SMTP / Email\n[Phase 1.2]"]
        CF["Cloudflare\nZero Trust Tunnel\n(free)"]
    end

    %% ── USER SURFACES ────────────────────────────────────────────────────
    subgraph USERS["User Interfaces"]
        direction LR
        TG["Telegram\n(User's Phone)"]
        Browser["Browser\n127.0.0.1:3000 / :8080"]
    end

    %% ── HOST PROCESSES (Windows 11, no Docker) ──────────────────────────
    subgraph HOST["Mini PC - Windows 11 - native processes"]
        direction TB

        WebUI["Web UI\nReact + Vite\nserved by FastAPI StaticFiles"]

        subgraph PROC1["PROCESS 1: FastAPI Orchestrator :8080 (uvicorn --workers 1)"]
            direction TB
            MAIN["main.py\nPOST /v1/chat\nWS   /v1/stream/{id}\nPOST /v1/jobs/{id}/run\nPOST /webhook/telegram"]
            PARSER["parser.py\n@PA / @CTO / @cost / @Desktop"]
            FSM["fsm.py\nMode FSM per (session,channel)"]
            ESCAL["escalation.py\nTable-backed state machine\nTTL + atomic resolution"]
            DISP["proxy/dispatcher.py\nretry - backoff - budget\ncaller enforcement"]
            STORE["store.py\nSQLite (aiosqlite, WAL)"]
            HIST["history.py + tokens.py\nsliding window + summary anchor\nanthropic count_tokens"]
            SPAWN["spawner.py\nclaude.exe subprocess\nNDJSON envelope\nreaper (idle/cap=2)"]
            EVCONS["events.py\nevents_consumer task\npolls every 500ms\npushes to WS or Telegram"]
            CONFIG["config.py\nguardrails.yaml\nwatchdog hot-reload"]
            TGADAPT["telegram.py\nwebhook + sender\nrate-limit token bucket"]

            subgraph ADP["Adapters (with Caller-scoped allowlists)"]
                direction LR
                AAPI["ClaudeAPI\nstream + cache"]
                ACC["ClaudeCode\nNDJSON pipe"]
                AWS["BraveSearch"]
                AFR["FileRead"]
                AFW["FileWrite\ncaller-scoped"]
                APW["Playwright\n[Phase 1.2]"]
                APD["PDFExtract\n[Phase 1.2]"]
                AEM["Email\n[Phase 1.2]"]
                ATM["Template\n[Phase 1.2]"]
            end
        end

        subgraph PROC2["PROCESS 2: Scheduler [Phase 1.2]"]
            direction TB
            SCHED["scheduler_main.py\nAPScheduler 3.10\nSQLAlchemyJobStore"]
            JR["job_runner.py\nDeterministic\nReads ## Execution Plan YAML\nZero LLM calls per run"]
        end

        DB[("SQLite orchestrator.db\nWAL mode\nsessions - messages - escalations\nevents - jobs - job_runs - cost_ledger")]

        CFTUN["cloudflared.exe\n(Windows service)\ntunnel daemon"]

        subgraph ONDEMAND["On-demand"]
            direction LR
            CCP["claude.exe subprocess\nCTO mode\nstdio NDJSON envelope\nworkspace: sessions/{id}/workspace/"]
        end

        FILES[("File system\njobs/*.md\nconfig/interests.md\nconfig/templates/*.j2\nsessions/{id}/workspace/")]
    end

    %% ── FLOWS ────────────────────────────────────────────────────────────

    %% Telegram path
    TG       <-->|"messages"| Telegram
    Telegram -->|"webhook (POST)"| CF
    CF       --> CFTUN
    CFTUN    -->|"127.0.0.1:8080"| MAIN
    TGADAPT  -.->|"send_message"| Telegram

    %% Web path
    Browser  <-->|"HTTP / WS"| WebUI
    WebUI    -->|"REST + WS"| MAIN

    %% Internal flow
    MAIN --> PARSER --> FSM --> ESCAL --> DISP --> ADP
    MAIN <--> STORE
    MAIN <--> HIST
    HIST  <--> STORE
    STORE <--> DB
    ESCAL <--> DB
    EVCONS <--> DB
    MAIN  --> SPAWN
    SPAWN -.->|"Popen + NDJSON pipe"| CCP
    ACC   <-->|"stdin / stdout (NDJSON)"| CCP

    %% Adapter externals
    AAPI -->|"Messages API SSE"| AnthropicAPI
    AWS  -->|"REST"| BraveSearch
    AFR  <--> FILES
    AFW  --> FILES
    AEM  -.->|"[Phase 1.2]"| SMTP

    %% Config
    CONFIG -.->|"hot-reload"| DISP
    CONFIG -.->|"hot-reload"| ESCAL

    %% Scheduler (separate process)
    SCHED --> JR
    JR <--> FILES
    JR <--> DB
    SCHED <--> DB
    JR -.->|"writes events row"| DB

    %% Cross-process notification: events table is the channel
    EVCONS -.->|"polls every 500ms"| DB
    EVCONS -.->|"WebSocket push"| MAIN
    EVCONS -.->|"telegram_send"| TGADAPT

    %% Style
    classDef ext    fill:#2d4a6e,stroke:#5b8dd9,color:#e8f0fe
    classDef ui     fill:#1e3a2f,stroke:#4caf7d,color:#e8f5e9
    classDef svc    fill:#3b2a1a,stroke:#f0a04b,color:#fff3e0
    classDef core   fill:#1a1a2e,stroke:#7c4dff,color:#ede7f6
    classDef adp    fill:#1a2035,stroke:#29b6f6,color:#e1f5fe
    classDef local  fill:#2a1a2e,stroke:#ce93d8,color:#f3e5f5
    classDef infra  fill:#1a2a1a,stroke:#66bb6a,color:#e8f5e9
    classDef sched  fill:#3a1a1a,stroke:#ef5350,color:#ffebee

    class Telegram,AnthropicAPI,BraveSearch,SMTP,CF ext
    class TG,Browser ui
    class WebUI,CFTUN svc
    class MAIN,PARSER,FSM,ESCAL,DISP,STORE,HIST,SPAWN,EVCONS,CONFIG,TGADAPT core
    class AAPI,ACC,AWS,AFR,AFW,APW,APD,AEM,ATM adp
    class CCP local
    class DB,FILES infra
    class SCHED,JR sched
```

---

## Component Index

| Component | Path | Process | Role |
|-----------|------|---------|------|
| **Web UI** | `web-ui/src/` | served by FastAPI | React terminal, WS client |
| **FastAPI app** | `orchestrator/main.py` | Process 1 (uvicorn --workers 1) | Routes, lifespan, events_consumer |
| **Intent parser** | `orchestrator/parser.py` | Process 1 | `@command` detection, intent kind |
| **Mode FSM** | `orchestrator/fsm.py` | Process 1 | PA / CTO / DESKTOP per (session, channel) |
| **Escalation engine** | `orchestrator/escalation.py` | Process 1 | Table-backed state machine, TTL, atomic resolve |
| **Dispatcher** | `orchestrator/proxy/dispatcher.py` | Process 1 | Adapter routing, retry, budget, caller enforcement |
| **SQLite store** | `orchestrator/store.py` | Process 1 (and 2 read-only) | aiosqlite, WAL, busy_timeout=5s |
| **History/tokens** | `orchestrator/history.py`, `tokens.py` | Process 1 | Sliding window + summary anchor + count_tokens |
| **Spawner/reaper** | `orchestrator/spawner.py` | Process 1 | claude.exe lifecycle, hard cap 2 |
| **Telegram** | `orchestrator/telegram.py` | Process 1 | Webhook + sender + rate limit |
| **events_consumer** | `orchestrator/events.py` | Process 1 (asyncio task) | Polls events table, pushes to WS/Telegram |
| **Scheduler** | `orchestrator/scheduler_main.py` | Process 2 (Phase 1.2) | APScheduler 3.10 + SQLAlchemyJobStore |
| **Job runner** | `orchestrator/job_runner.py` | Process 2 (Phase 1.2) | Deterministic Execution Plan executor |
| **Adapters** | `orchestrator/proxy/adapters/*.py` | invoked from either process | Caller-scoped tools |
| **claude.exe** | system PATH | spawned per CTO session | Sub-agent (NDJSON envelope) |
| **cloudflared.exe** | Windows service | independent | Public ingress for Telegram webhook |
| **SQLite DB** | `orchestrator.db` | shared (WAL) | All persistent state + cross-process events channel |

---

## Data Flow Summaries

### User chat (web)
```
Browser ──→ WebUI ──→ FastAPI /v1/chat
                          │
                          ▼
                   parser → fsm → escalation_check
                          │
                          ▼
                   (if pending escalation matches reply → resolve atomic)
                   (if not → dispatcher → adapter)
                          │
                          ▼
                   ClaudeAPIAdapter (stream) ──→ AnthropicAPI
                          │
                          ▼
                   tokens written to messages table
                   cost written to cost_ledger
                          │
                          ▼
                   stream back via WS to Browser
```

### User chat (Telegram)
```
TG client → Telegram Bot API → cloudflared → /webhook/telegram
                                                  │
                                                  ▼
                                          (same chain as web)
                                                  │
                                                  ▼
                                          telegram.send_message → Telegram → TG client
```

### CTO sub-agent invocation
```
@CTO write hello.py
        │
        ▼
spawner.spawn(session_id, brief_context):
   - generate brief via ONE Claude call
   - write sessions/{id}/.claude/CLAUDE.md
   - Popen claude.exe
        │
        ▼
First user message → stdin
        │
        ▼
claude.exe writes NDJSON envelope on stdout:
   {"phase": "plan", "needs_confirmation": true, ...}   ← creates escalation
   {"phase": "action", ...}                              ← streamed to user
   {"phase": "result", "summary_needed": false, ...}    ← templated wrap-up
        │
        ▼
PA wrapper templates → user (deterministic, no extra LLM call)
        │
        ▼
(only if summary_needed=true → ONE additional Claude call via ClaudeAPIAdapter)
```

### Scheduled job (Phase 1.2)
```
APScheduler (Process 2) fires cron
        │
        ▼
job_runner.run(job_id):
   - Load jobs/{name}.md
   - Verify plan_checksum (mismatch → escalation event)
   - Validate ## Execution Plan against adapter manifests
   - Execute steps deterministically (NO Claude call)
        │
        ▼
job_runs row written
events row inserted (kind: job_complete)
        │
        ▼
Process 1 events_consumer (polls every 500ms)
        │
        ▼
Web UI WebSocket push  +  Telegram send_message
```

### Error escalation
```
Adapter returns Result(ok=False, retriable=...)
        │
        ▼
After max_attempts:
   - escalation.create(session_id, options={a: retry, b: skip})
   - emit prompt to user
        │
        ▼
Next inbound message:
   - escalation.pending_for(session) → match candidate key
        │
        ├─ match → atomic resolve → execute branch
        ├─ non-match → cancel + passthrough as new message
        └─ TTL expired → auto-skip + event notification
```

---

## Notes on the v3 architecture (vs v1 with Docker)

| Concern | v1 (Docker) | v3 (native) |
|---------|-------------|-------------|
| Process model | 6 containers via Docker Compose | 2 native Windows processes (uvicorn + scheduler) |
| Session store | Redis 7 (container) | SQLite + WAL |
| Mobile surface | Twilio + WhatsApp connector container | Telegram Bot API directly |
| Cross-worker pubsub | Redis pub/sub | SQLite events table polled by single uvicorn worker |
| Sub-agent output | Free-text + sentinel | NDJSON envelope, parsed deterministically |
| Job execution | LLM call per fire | One LLM call at create; deterministic at fire |
| Escalation state | Single column | Dedicated table with TTL and atomic resolve |
| File write security | Plan v2 had simple allowlist | v3: caller-scoped allowlists + atomic writes |
| Memory footprint | ~1.8 GB | ~250 MB |
| Startup | `docker compose up` | `run.ps1` |
| Worker count | 2 uvicorn workers per service | 1 uvicorn worker (eliminates cross-worker push problem) |
