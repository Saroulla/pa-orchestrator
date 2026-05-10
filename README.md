# Personal Assistant Orchestrator

A single-node AI orchestrator running on a Windows mini PC. One Python service parses input from a terminal Web UI and Telegram, routes requests through an adapter layer to Claude, and executes scheduled workflows — all without Docker, Redis, or a local LLM.

## What it does

- **Chat** — talk to the PA via a browser terminal or Telegram. The PA reasons using the Claude API with per-session conversation history, context compression, and cost tracking.
- **CTO mode** — type `@CTO` to spawn a Claude Code sub-agent that can write, read, and execute code inside a sandboxed session workspace. Output streams back through the PA.
- **Scheduled jobs** — describe a recurring task in plain English; the PA generates a deterministic YAML execution plan and runs it on a cron schedule with zero LLM calls at runtime.
- **Adapters** — Brave Search, file read/write (caller-scoped), Playwright web automation, PDF extraction, email sending, and Jinja2 templates.

## Architecture

```
Browser / Telegram
       │
  FastAPI (uvicorn, 1 worker)
       │
  Proxy Layer → Intent → Dispatcher → Adapters
                                    ├── Claude API (streaming SSE)
                                    ├── Claude Code (NDJSON subprocess)
                                    ├── Brave Search
                                    ├── File Read / Write
                                    ├── Playwright
                                    ├── PDF Extract
                                    ├── Email
                                    └── Template Render
       │
  APScheduler (separate subprocess)
       │
  SQLite (WAL) — sessions, messages, jobs, cost ledger, escalations, events
```

Public ingress via Cloudflare Tunnel — Telegram webhook on `pa.khoury.uk`, Web UI on `khoury.uk`. The mini PC never opens an inbound port.

## Stack

| Layer | Choice |
|-------|--------|
| Runtime | Python 3.14, FastAPI, uvicorn |
| Session store | SQLite + aiosqlite (WAL) |
| Scheduler | APScheduler 3.10 + SQLAlchemyJobStore |
| AI | Anthropic Claude API (Haiku for chat, Sonnet for planning) |
| Telegram | python-telegram-bot 21+ via Cloudflare Tunnel webhook |
| Search | Brave Search API |
| Web automation | Playwright (Chromium headless) |
| Web UI | Vite + React + TypeScript |
| Auth | scrypt password + signed session cookie (itsdangerous) |
| Service | NSSM Windows service, auto-start on boot |

## Setup

### Prerequisites

- Python 3.14
- Node.js 20+
- [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
- [NSSM](https://nssm.cc/) (for running as a Windows service)

### Environment

Copy `.env.example` to `.env` and fill in:

```
LOGIN_USERNAME=your_username
LOGIN_PASSWORD_HASH=   # generate with the command below
SESSION_SECRET=        # random hex string: python -c "import secrets; print(secrets.token_hex(32))"
ANTHROPIC_API_KEY=
BRAVE_SEARCH_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USER_IDS=
CLOUDFLARE_TUNNEL_HOST=
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASS=
SMTP_FROM=
```

Generate a password hash:
```powershell
python -c "import hashlib, os, getpass; p=getpass.getpass('Password: ').encode(); s=os.urandom(16); print(s.hex()+'$'+hashlib.scrypt(p,salt=s,n=16384,r=8,p=1).hex())"
```

### Install

```powershell
pip install -r requirements.txt
cd web-ui; npm install; npm run build; cd ..
```

### Run (development)

```powershell
.\run.ps1
```

### Run (production — Windows service)

```powershell
nssm install PA-Orchestrator powershell.exe
nssm set PA-Orchestrator AppParameters "-NonInteractive -ExecutionPolicy Bypass -File C:\path\to\_REPO\run.ps1"
nssm set PA-Orchestrator AppDirectory C:\path\to\_REPO
nssm set PA-Orchestrator Start SERVICE_AUTO_START
nssm start PA-Orchestrator
```

## Model cost control

Edit `config/guardrails.yaml` — changes hot-reload without restart:

```yaml
models:
  pa_chat: "claude-haiku-4-5-20251001"   # regular conversation
  summarize: "claude-haiku-4-5-20251001" # history compression
  cto_brief: "claude-sonnet-4-6"         # CTO task planning
  plan_author: "claude-sonnet-4-6"       # job plan generation
```

Daily spend cap is also in `guardrails.yaml` under `budgets.per_session_usd_per_day`.

## Commands

| Command | Effect |
|---------|--------|
| `@CTO` | Switch to CTO sub-agent mode |
| `@PA` | Switch back to PA |
| `@cost` | Check today's API spend |
| `@rebuild-plan jobs/<name>.md` | Regenerate execution plan for a job |
| `@remember <text>` | Update interest profile |
