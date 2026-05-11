# PA Orchestrator — Project Vision

> **Canonical reference document.** This supersedes earlier three-agent (PA/CTO/Desktop) thinking.
> When in doubt about scope, architecture, or build decisions, this document is the source of truth.
> Last revised: 2026-05-11

---

## 1. Purpose

A single-node personal assistant orchestrator running on a Windows mini PC. One operator (the user), one machine, lifelong duty: handle email, research, content curation, and arbitrary local automation tasks under conversational direction.

The project's value is **not the chat interface**. The value is a tireless executor (MAKER) that runs deterministic jobs on a schedule, curated and supervised by a lightweight conversational agent (PA).

---

## 2. The Two-Agent Model

### PA — Personal Assistant
- **Model:** Claude Haiku, exclusively.
- **Role:** Conversational entry point. Parses input from Web UI or Telegram, decides what MAKER should do, curates MAKER's output before showing it to the user.
- **Personality:** Direct, concise, helpful. PA is the face — the user only talks to PA.
- **Does not execute jobs directly.** PA dispatches to MAKER and synthesises results.

### MAKER — The Workhorse
- **Role:** Deterministic job executor + skill runner.
- **Substrate:** In-process FastAPI module + hands in scheduler subprocess (APScheduler).
- **What MAKER does:** Internet research, email send/receive, web scraping, file generation, custom app orchestration (Unico), scheduled tasks.
- **How MAKER scales:** New skills are added as markdown files describing capabilities. No code change needed to teach MAKER a new task.

### What we explicitly are NOT building
- ❌ **CTO sub-agent** — redundant with Claude Code on iOS (works against git remotely). No spawner, no claude.exe subprocess, no NDJSON envelope protocol.
- ❌ **Desktop computer-use agent** — out of scope. Mini PC is for jobs, not autonomous desktop control.
- ❌ **Multi-user support** — single operator, single session lineage.

---

## 3. Architecture Principles

1. **PA stays small.** Haiku-only. Conversational, fast, cheap. Never used for bulk processing.
2. **MAKER stays deterministic at the executor layer.** Job runner reads a fixed YAML plan and executes steps in order. Intelligence is injected at specific step boundaries, not at runtime control flow.
3. **Skills, not code.** Adding new capabilities means writing a markdown skill file, not Python. Code changes only when new adapters are needed.
4. **Polyglot adapters.** Each adapter uses the most natural interface for its job. APIs for web services. Python for data work. PowerShell *only* for Unico (bash command execution).
5. **Audit everything.** Every model decision is logged. Every job run is logged. PA can be asked to audit MAKER's behaviour on demand.
6. **No silent failure.** Confidence thresholds, drift detection, escalation to user when models disagree.

---

## 4. Use Cases (Scope)

### Phase 1 — MVP
1. **Internet research jobs** — Google CSE search → article extraction → curation via Haiku → email digest or chat summary.
2. **Email send** — outbound emails composed by Haiku and dispatched.
3. **Custom app orchestration (Unico)** — deterministic PowerShell command sequences invoking bash tools.
4. **Scheduled jobs** — anything above on cron schedule via APScheduler.

### Phase 2 — Post-MVP
5. **Autonomous inbox management** — IMAP/Gmail reader → Groq classification → Haiku reply drafting → conditional auto-send / escalate.
6. **Interactive MAKER loops** — PA curates MAKER's command sequence in real-time (gold standard for custom-app workflows).
7. **Groq triage engine** — bulk classification offloaded to Groq for cost reduction.

---

## 5. Tooling Model — Adapter Map

Each MAKER capability uses the most natural interface:

| Capability | Adapter | Interface | Notes |
|---|---|---|---|
| Google search | `google_cse` | HTTP API | Existing |
| Brave search | `brave_search` | HTTP API | Existing |
| URL fetch | `http_fetch` | HTTP | Existing |
| Article extract | `article_extract` | HTML parsing | Existing |
| Browser automation | `playwright_web` | Playwright | Existing |
| Email send | `email_send` | SMTP / Gmail API | Existing |
| **Email read (inbox)** | `inbox_read` | IMAP / Gmail API | **To build (Phase 2)** |
| File I/O | `file_read`, `file_write` | Python | Existing |
| Template rendering | `template_render` | Jinja2 | Existing |
| PDF extraction | `pdf_extract` | PyMuPDF | Existing |
| Curation / summary | `pa_haiku` | Claude API (Haiku) | Existing |
| Bulk classification | `groq` | Groq API | **Post-MVP experiment** |
| **Custom app (Unico)** | `powershell` | `powershell.exe` subprocess | **To build (MVP)** |

**Rule:** PowerShell is for Unico/bash commands only. It is not a general execution layer. Every other capability uses a purpose-built adapter.

---

## 6. Intelligence Layer

### PA = Haiku, always
- Conversational responses
- Curation and synthesis of MAKER output
- Decision-making within MAKER jobs (when an intelligence step is needed)
- Auditing MAKER's behaviour

### Groq (Phase 2 experiment) = MAKER's bulk triage engine
- Email classification (reply / archive / escalate / ignore)
- Search result relevance scoring
- URL filtering (worth fetching?)
- Duplicate / already-seen detection
- Content category tagging

**Rule of thumb:** Groq handles inputs → outputs with a fixed schema. Haiku handles anything user-facing or requiring judgement. **Confidence-low Groq decisions auto-escalate to Haiku — never silently guess.**

---

## 7. Cost Control & Audit

### Cost discipline
- Hard daily budget cap enforced in `guardrails.yaml`
- Per-skill token budgets prevent runaway jobs
- Bulk operations batched, never looped (one Haiku call with 20 items > 20 calls with 1 item each)
- Groq carries the high-volume repetitive load post-MVP

### Audit system
Every Groq decision logged to `groq_decisions` table:

```
groq_decisions: id, job_id, skill_name, task_type,
                input_summary, decision, confidence,
                model, tokens_in, tokens_out, cost_usd, created_at
```

**On-demand audit:** User asks PA `@audit inbox last 50` → PA pulls rows → Haiku reviews each decision → reports flagged items.

**Scheduled drift detection:** Weekly job tracks Haiku's override rate on Groq decisions. If overrides exceed 10%, user is notified for manual review.

---

## 8. Skill Model

A skill is a markdown file in `config/maker/skills/` describing a capability.

Example structure:
```markdown
# Inbox Triage Skill

## What
Read unread email, classify, draft replies for important ones,
archive newsletters, escalate ambiguous to user.

## Execution Plan
1. inbox_read — fetch unread
2. groq — classify each (reply/archive/escalate/ignore)
3. pa_haiku — draft replies for "reply" items
4. email_send — dispatch drafts
5. inbox_read — apply archive labels
6. file_write — log summary

## Audit Criteria
A "reply" classification is correct when the email is from
a known contact AND contains a direct question.

## Budget
Max 0.50 USD per run. Daily cap: 5 runs.
```

**To teach MAKER something new, write a new skill file.** PA reads the skill index on demand to know what MAKER can do.

---

## 9. The Gold Standard (Future State)

MVP is deterministic. Gold standard is **interactive MAKER**:

```
PA (Haiku): "To classify this inbox, run: unico --fetch-email"
MAKER: Executes, captures output
MAKER → PA: "Output: 42 emails fetched"
PA: "Now run: unico --classify"
...etc, until PA says "done"
```

MVP's PowerShell adapter and deterministic job format **are the foundation for this**. Same components, looser orchestration. We are not throwing anything away — we are growing into the interactive pattern step by step.

---

## 10. Operating Boundaries

### Single user, single machine
- Web UI bound to 127.0.0.1
- Telegram via Cloudflare Tunnel (the only external surface)
- No multi-tenant assumptions anywhere

### Cost discipline
- $5/day hard kill
- Pre-dispatch budget check
- Groq experiment must demonstrate cost reduction OR get pulled

### Safety for arbitrary command execution
- PowerShell adapter: command allowlist (Unico calls + safe builtins only)
- Timeout per command (30–60s default)
- Output size cap (100KB default)
- All commands logged

### What MAKER never does without explicit user approval
- Send email (drafts are auto-approved only if rule allows)
- Delete files outside its scoped workspace
- Run PowerShell commands not on the allowlist
- Spend more than per-skill budget cap

---

## 11. The North Star

> **An assistant that genuinely takes work off the operator's plate.**
>
> Not a chatbot. Not a coding sidekick. A tireless executor that handles the recurring grind — research, triage, scheduled tasks, inbox management — supervised by a small, cheap, always-on conversational layer (PA) and audited continuously for silent failure.
>
> Everything is built toward this. If a proposed feature does not advance this, it does not ship.

---

## 12. Cross-References

- `01.Project_Management/Build_Status_Verification_Handover.md` — investigation prompt for resolving BUILD_STATUS vs. actual code discrepancies
- `01.Project_Management/Mobile_claude_code_confirmation.md` — handover prompt for verifying agent access to correct branch
- `BUILD_STATUS.md` — execution tracking (to be reconciled with this vision)
- `CLAUDE.md` — operational spec (to be rewritten to align with this vision)

---

## 13. Decisions Log

| Date | Decision | Why |
|---|---|---|
| 2026-05-11 | Drop CTO sub-agent entirely | Redundant with Claude Code on iOS |
| 2026-05-11 | PA = Haiku exclusively | Cost, simplicity, fit-for-purpose |
| 2026-05-11 | Groq as MAKER triage engine (post-MVP) | Bulk classification cost reduction |
| 2026-05-11 | PowerShell adapter scoped to Unico only | Each adapter uses its natural interface |
| 2026-05-11 | MVP = deterministic MAKER, Phase 2 = interactive MAKER | Foundation first, evolve in place |
