# Project Vision: PA Orchestrator (PowerShell Iterative MVP)

> Authoritative architectural vision. Aligns implementation roadmap with user intent. Updated 2026-05-11.

---

## Core Purpose

**PA Orchestrator** is a two-tier intelligence system for a single user on a personal mini PC:

- **Execution Canvas:** PowerShell executes all mini PC operations (internet, simulations, local tasks)
- **Intelligence Layer:** PA + MAKER + Worker-Hierarchy orchestrate and interpret PowerShell execution
- **Pattern:** Iterative goal execution (decide → execute → analyze → decide next step)

**Vision:** Everything centralized through PowerShell. AI guides execution iteratively. Results RAGged for history queries.

---

## Phase 1 MVP: PowerShell Iterative Automation

### The Use Case

User gives PA a goal (e.g., "Run daily simulations," "Check inbox and organize," "Monitor system health"). PA system:

1. **Understands Goal** — Parse user intent into executable steps
2. **Decides First Action** — Sonnet: "Given goal, what should PowerShell do first?"
3. **Executes** — MAKER spawns PowerShell script, captures stdout/stderr/exit code
4. **Analyzes** — Haiku workers extract insights from results (5 parallel)
5. **Interprets** — Sonnet: "What does this result mean? Goal achieved? Need more steps?"
6. **Decides Next Action** — If goal not achieved, loop back to step 2
7. **Reports** — After goal completion (or max iterations): provide summary + all results

### Key Architecture

- **Iterative Loop** — Execute → analyze → decide → repeat until goal achieved
- **Stateful Execution** — Track goal, completed steps, current result, iteration count
- **Safety Limits** — Max 10 iterations per goal; escalate to user on max reached
- **PowerShell is the Canvas** — All mini PC operations (internet fetch, file ops, simulations, system commands) via PowerShell
- **Worker Hierarchy** — Haiku workers analyze results in parallel; Sonnet synthesizes and decides next action
- **Zero External Apps** — Everything centralized; no Playwright, no separate email/calendar tools, no external integrations

### Example Flow: Daily Simulations Goal

```
User: "Run daily simulations and give me a summary"
↓
ITERATION 1:
  - Sonnet decides: "Check for input data first"
  - Execute PS: Get-ChildItem C:\simulations\input\
  - Analyze: "Input data exists, 5 files ready"
  - Sonnet: "Good. Next: run simulation script."
↓
ITERATION 2:
  - Execute PS: & C:\simulations\run-sim.ps1
  - Capture: stdout (1000 lines of metrics), exit code 0
  - Analyze (5 Haiku workers parallel):
    - Worker 1: "Extract runtime metrics"
    - Worker 2: "Extract success rate"
    - Worker 3: "Extract anomalies"
    - Worker 4: "Extract resource usage"
    - Worker 5: "Extract comparison to baseline"
  - Sonnet synthesizes: "Simulations complete. 95% success, runtime 2.3hrs, 3 anomalies detected."
  - Sonnet decides: "Goal achieved."
↓
FINAL RESPONSE:
  "Simulations complete. 95% success rate. Runtime 2.3 hours.
   Anomalies detected in [3 areas]. See detailed results [below]."
```

**Cost per goal:** ~3 iterations × (5 Haiku = 0.01¢ + 1 Sonnet decision = 0.02¢) = ~0.10¢. Scales infinitely for <$1/day.

### Execution Workflow (Detailed)

```python
async def execute_goal_iteratively(user_intent: str, session_id: str):
    state = {
        "goal": user_intent,
        "steps": [],
        "max_iterations": 10,
        "current_iteration": 0
    }
    
    while state["current_iteration"] < state["max_iterations"]:
        # STEP 1: Decide what PowerShell should do next
        next_action = await sonnet_adapter.invoke({
            "prompt": f"Goal: {state['goal']}\n\nCompleted steps: {state['steps']}\n\nWhat should PowerShell do next? Be specific and terse.",
            "session_id": session_id
        })
        
        # STEP 2: Execute PowerShell
        ps_result = await powershell_adapter.invoke({
            "script": next_action,
            "timeout_s": 300
        })
        
        # STEP 3: Analyze result (parallel Haiku workers)
        worker_tasks = []
        for i in range(5):
            task = haiku_adapter.invoke({
                "prompt": f"Goal: {state['goal']}\n\nPowerShell output:\n{ps_result['stdout']}\n\nAnalyze aspect {i}: [specific focus]",
                "session_id": session_id
            })
            worker_tasks.append(task)
        
        worker_outputs = await asyncio.gather(*worker_tasks)
        
        # STEP 4: Synthesize and decide
        synthesis = await sonnet_adapter.invoke({
            "prompt": f"Goal: {state['goal']}\n\nPowerShell output:\n{ps_result['stdout']}\n\nWorker insights:\n{[w for w in worker_outputs]}\n\nIs the goal achieved? What should we do next?",
            "session_id": session_id
        })
        
        # STEP 5: Store step and check goal
        state["steps"].append({
            "iteration": state["current_iteration"],
            "action": next_action,
            "ps_output": ps_result,
            "worker_insights": worker_outputs,
            "synthesis": synthesis
        })
        
        state["current_iteration"] += 1
        
        # STEP 6: Exit condition
        if "goal achieved" in synthesis.lower() or "complete" in synthesis.lower():
            break
    
    return {
        "final_response": synthesis,
        "steps": state["steps"],
        "iterations": state["current_iteration"],
        "success": state["current_iteration"] < state["max_iterations"]
    }
```

---

## Phase 1.5: RAG PowerShell Execution History

### The Use Case (Post-MVP)

User queries the history of what PowerShell has done:
- "What did the system do yesterday?"
- "Show me all simulation runs and their metrics"
- "What anomalies were detected this week?"

PA system:
1. **Chunks PowerShell Results** — Each iteration's output → overlapping segments
2. **Embeds** — Voyage AI vectors (1024-dim)
3. **Stores** — Per-session in-memory vector store
4. **Retrieves** — User query → embedding → cosine similarity → top-K results
5. **Synthesizes** — Sonnet weaves chunks into coherent historical narrative

### Example Flow

```
User: "What happened with the simulations?"
↓
PA embeds query
↓
PA retrieves top-5 relevant chunks from PowerShell history
↓
PA spawns 5 Haiku workers: "Extract key fact from this chunk"
↓
PA calls Sonnet: "Synthesize simulation history"
↓
Response: "Simulations ran 3 times. First run: 95% success. Second run: 98% success. Detected memory leak in iteration 2. Fixed in iteration 3."
```

**Key:** RAG is not a separate knowledge base. It's the queryable history of PowerShell execution.

---

## Architectural Principles

1. **PowerShell as Execution Canvas** — Everything mini PC does flows through PowerShell
2. **Iterative Goal Execution** — Goals loop until achieved; max 10 iterations for safety
3. **Cheap Workers, Expensive Synthesizers** — Haiku parallel analysis; Sonnet decisions
4. **Centralized Intelligence** — PA + MAKER + Workers handle all orchestration
5. **Zero External Apps** — No Playwright, no email SDKs, no separate tools
6. **Deterministic MAKER** — Execution is purely logical; no LLM calls per step (only synthesis)
7. **Queryable History** — All PowerShell results RAGged for later queries
8. **Cost Capped** — Hard kill on $5/day; per-query budgets enforced

---

## Phases Summary

| Phase | Focus | Canvas | Worker Count | Cost |
|-------|-------|--------|--------------|------|
| **Phase 1 (MVP)** | Iterative PowerShell automation | PowerShell | 5 Haiku + 1 Sonnet per iteration | ~$0.10 per goal |
| **Phase 1.5** | RAG PowerShell history | Vector store | 5 Haiku + 1 Sonnet per query | ~$0.05 per query |
| **Phase 2+** | Extended automation (calendar, git, email via PS) | PowerShell | Same | Same |

---

## Architectural Principles

1. **Single User, Single Process** — No multi-tenancy. One FastAPI worker (1 uvicorn), one optional async scheduler, spawned sub-agents are temporary
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
- CTO mode / code-generation sub-agent (Claude Code on iOS handles this separately)

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
