# Project Vision: PA Orchestrator (RAG-First MVP)

> Authoritative architectural vision. Aligns implementation roadmap with user intent. Supersedes earlier adapter-map framing.

---

## Core Purpose

**PA Orchestrator** is a two-tier intelligence system for a single user on a personal mini PC:

- **Tier 1 (Workers):** Many parallel cheap agents (Haiku) process documents, extract facts, run commands
- **Tier 2 (Evaluator):** One expensive agent (Sonnet/Opus) synthesizes worker outputs into coherent answers, reports, decisions

**Pattern:** Map (cheap workers) → Reduce (expensive synthesis) → Intelligence

**Cost Model:** Haiku parallelism drives down per-query cost; Sonnet synthesis ensures quality and coherence.

---

## Phase 1 MVP: RAG Knowledge Base

### The Use Case

User uploads large PDFs, articles, HTML, or Google-scraped content on a subject. PA system:

1. **Ingests** — Chunks text into overlapping 500-token segments
2. **Embeds** — Converts segments to vectors (Voyage AI)
3. **Stores** — Caches embeddings per-session in memory (ephemeral, not persistent)
4. **Retrieves** — On user query, embeds query → cosine similarity search → top-K chunks
5. **Synthesizes** — Spawns parallel Haiku workers to extract answers from each chunk → single Sonnet call to weave answers into coherent response

### Key Architecture

- **Per-Session Ephemeral Storage** — Knowledge base lives only for current session; discarded on logout/session end
- **NumPy In-Memory Vectors** — No external vector DB (Chroma, Pinecone, etc.). Session-scoped, local cosine similarity
- **Async Parallel Execution** — `asyncio.gather()` for concurrent worker calls
- **Worker Hierarchy** — Haiku as knowledge extractors (fast, cheap, parallel); Sonnet as synthesis expert (quality, single-call)

### Example Flow

```
User: "What does this PDF say about machine learning?"
↓
PA retrieves top-5 relevant chunks via cosine similarity
↓
PA spawns 5 parallel Haiku calls:
  Worker 1: "Extract ML insights from chunk 1"
  Worker 2: "Extract ML insights from chunk 2"
  ...
  Worker 5: "Extract ML insights from chunk 5"
↓
PA gathers all 5 results
↓
PA makes 1 Sonnet call: "Synthesize these 5 extracts into a coherent answer"
↓
Response returned to user
```

**Cost:** ~5 Haiku calls (~0.01¢) + 1 Sonnet call (~0.04¢) = ~0.05¢ per query. Scales to hundreds of queries per session for $1-2 total.

---

## Phase 2: PowerShell Orchestration & Automation

### The Use Case

User needs mini PC to run simulations, execute commands, manage tasks autonomously. PA system:

1. **Executes** — Spawns PowerShell scripts via MAKER executor
2. **Monitors** — Captures stdout/logs from command execution
3. **Analyzes** — Spawns parallel Haiku workers to interpret results (simulations, errors, metrics)
4. **Synthesizes** — Single Sonnet call to produce daily report, update files, prepare findings
5. **Feeds Back** — Results stored, queryable like knowledge base chunks

### Key Architecture

- Same worker-hierarchy pattern as RAG
- MAKER executor is deterministic (no LLM per job run)
- Results stored in session context or persistent job log
- Optional: feed PowerShell results + interpretations back into RAG for combined analysis

### Example Flow

```
User: "Run simulation, analyze results, prepare report"
↓
MAKER executes: powershell -File C:\simulations\run.ps1
↓
Captures stdout → 50 pages of metrics
↓
PA retrieves relevant sections (sampling or chunking)
↓
PA spawns parallel Haiku workers to interpret metrics
↓
PA makes 1 Sonnet call: "Synthesize analysis into daily report"
↓
Sonnet writes results to C:\reports\daily.md
```

---

## Architectural Principles

1. **Single User, Single Process** — No multi-tenancy. One FastAPI worker (1 uvicorn), one optional async scheduler, MAKER subprocess work is temporary
2. **Deterministic MAKER** — Job executor (PowerShell, chunking, retrieval) is pure functions; zero LLM calls unless user initiates
3. **Cheap Workers, Expensive Synthesizers** — Haiku parallelism for grunt work; Sonnet for judgment calls
4. **Ephemeral Knowledge** — Per-session vector storage; no lifetime persistence (user can export reports/findings if needed)
5. **Cost Capped** — Hard kill on $5/day breach; per-query budgets enforced
6. **Minimal Dependencies** — No Kubernetes, Docker, Redis, external vector DB. NumPy, aiosqlite, Voyage AI API, PowerShell native

---

## Adapter Map (MVP + Phase 2)

### Input Adapters (Source → PA)

| Source | Adapter | Purpose | Cost |
|--------|---------|---------|------|
| User chat (web/Telegram) | PA (Haiku) | Intent parsing, routing | ~0.001¢ per message |
| PDF file | pdf_extract.py | Text extraction | $0 (local) |
| HTML URL | article_extract.py, http_fetch.py | Web scraping | $0 (local) + network |
| Google Search | google_cse.py | Search queries | API cost (pre-negotiated) |
| PowerShell output | subprocess (native) | Command execution | $0 (local) |

### Processing Adapters (PA → Intelligence)

| Process | Adapter | Purpose | Cost |
|---------|---------|---------|------|
| Chunking | chunker.py | Text → segments | $0 (local) |
| Embedding | voyage_embed.py | Segments → vectors | Voyage API (~0.02¢ per embed) |
| Retrieval | vector_store.py, retrieval.py | Query → top-K chunks | $0 (local cosine sim) |
| Worker Extraction | pa_haiku.py (parallel) | Chunk → insights | ~0.002¢ per Haiku call |
| Synthesis | claude_api.py (Sonnet) | Insights → answer | ~0.04¢ per Sonnet call |
| PowerShell | powershell.py | PC commands | $0 (local) |

### Output Adapters (PA → User)

| Channel | Adapter | Format |
|---------|---------|--------|
| Web UI | WebSocket | JSON streaming; Markdown |
| Telegram | python-telegram-bot | Text messages; `.md` files for long content |
| Local Files | file_write.py | Markdown, CSV, JSON (session-scoped safety) |
| Daily Reports | email_send.py (Phase 2) | HTML email with inline charts |

---

## Intelligence Layers

### Layer 1: PA (Haiku, Always Streaming)
- Interprets user intent
- Routes to MAKER or worker-hierarchy
- Decides: cheap workers or expensive synthesis?
- Streams responses in real-time

### Layer 2: MAKER (Deterministic, No LLM)
- Chunks documents
- Embeds via Voyage
- Retrieves relevant chunks
- Executes PowerShell scripts
- Stores results

### Layer 3: Worker Hierarchy (Map-Reduce)
- Spawns N cheap Haiku calls in parallel (workers)
- Gathers results
- Calls Sonnet once for synthesis (evaluator)
- Returns high-quality, low-cost answer

### Layer 4: Specialized Adapters (As Needed)
- Playwright for browser automation (Phase 2)
- Email for outbound notifications (Phase 2)
- Calendar/GitHub (Phase 2)

---

## Security Boundaries

- **Web UI:** Bound to `127.0.0.1` only (never exposed externally)
- **Telegram:** Webhook via Cloudflare Tunnel hostname only
- **File I/O:** Caller-scoped allowlists (PA can write to `jobs/`, `config/`, session workspace; MAKER cannot escape session workspace)
- **Sub-Agent Env:** Scrubbed of host secrets; explicit allowlist only
- **Cost Cap:** Hard kill on $5/day breach
- **Session Isolation:** Each session has independent vector store, workspace, cost ledger

---

## Success Criteria (Phase 1)

- ✅ User uploads 10-page PDF or article
- ✅ PA chunks, embeds, stores vectors
- ✅ User asks 5 questions; PA retrieves relevant chunks and synthesizes answers
- ✅ Answers are accurate and coherent (not hallucinations)
- ✅ Cost is <$1 for entire session
- ✅ Latency is <10s per query (including embedding + retrieval + worker parallelism + synthesis)
- ✅ Telegram and web UI both work

---

## Non-Goals (MVP)

- Lifetime knowledge base persistence (user must re-upload per session)
- Computer use / autonomous desktop control (Phase 2)
- Multi-user collaboration (single-user system)
- Real-time streaming of intermediate worker results (synthesis happens after workers complete)
- Groq integration (post-MVP experiment in `experiments/`)
- Code-generation sub-agent (Claude Code on iOS handles this separately)

---

## Roadmap Summary

| Phase | Focus | Worker Count | Synthesis | Adapter Count |
|-------|-------|--------------|-----------|---------------|
| **Phase 1 (MVP)** | RAG knowledge base | 1-5 Haiku | 1 Sonnet | 8-10 |
| **Phase 1.5** | PowerShell executor | 1-5 Haiku | 1 Sonnet | 10-12 |
| **Phase 2** | Autonomy + reporting | 1-10 Haiku | 1 Sonnet | 14-16 |

---

## Why This Model Works

1. **Cost Efficiency** — Haiku parallelism reduces cost per query by 10-100x vs calling Sonnet on full document
2. **Quality** — Sonnet synthesis ensures coherent, factual responses (not scattered worker outputs)
3. **Speed** — Parallel Haiku calls are faster than sequential reads; Sonnet synthesis is single-pass
4. **Scalability** — Can add more workers without increasing per-worker cost
5. **Interpretability** — User sees intermediate worker outputs; can debug/audit why synthesis said X
6. **Hardware Fit** — 4-core mini PC can run 5-10 parallel Haiku calls easily (no GPU needed)

---

## References

- Execution Plan: `01.Project_Management/Execution_Plan.md`
- Build Status: `BUILD_STATUS.md` (root)
- Adapter Contracts: `01.Project_Management/adapter-spec.md`
- Architecture Diagram: `01.Project_Management/arch_diagram.md`
