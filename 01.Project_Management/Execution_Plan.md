# Execution Plan: PA Orchestrator MVP (RAG-First)

> Step-by-step implementation roadmap for local agent. Each phase has clear objectives, handover prompts, and success criteria.

---

## Overview

**Total Phases:** 6  
**Blocking Dependencies:** Phase 0 → Phase 1 → Phases 2-3 → Phase 4 → Phase 5 → Phase 6

**Estimated Timeline:**
- Phase 0 (Verification): 30 min (local machine)
- Phase 1-3 (Cleanup): 2-3 hours
- Phase 4 (RAG Build): 4-6 hours
- Phase 5 (Integration): 2-3 hours
- Phase 6 (PowerShell): 3-4 hours

**Total: ~1-2 working days for MVP gate**

---

## Phase 0: Local Verification (BLOCKING)

### Objective
Confirm actual codebase state. Identify gaps between BUILD_STATUS claims and real code.

### Why It's Blocking
- Phases 1-3 assume we know what exists
- Previous sessions may have created/deleted files; BUILD_STATUS may be stale
- Must have ground truth before proceeding

### Handover Prompt for Local Agent

```
Run this on your local machine and report findings:

1. ls -la orchestrator/maker/ 2>&1 | wc -l
   → How many files in maker/ directory? (if error, 0)

2. ls -la config/maker/ 2>&1 | wc -l
   → How many files in config/maker/? (if error, 0)

3. ls orchestrator/proxy/adapters/*.py | wc -l
   → Total adapter count

4. grep -l "class.*Adapter" orchestrator/proxy/adapters/*.py 2>/dev/null | sort
   → Which adapters are defined?

5. python -m orchestrator.main &
   sleep 5
   kill %1 2>/dev/null
   echo "START SUCCESS" || echo "START FAILED"
   → Can the app start without errors?

6. grep "def invoke" orchestrator/maker/executor.py 2>/dev/null || echo "NOT FOUND"
   → Is MAKER executor present?

7. grep -c "class.*Tool" orchestrator/proxy/protocol.py
   → How many Tool protocol classes defined?

8. git log --oneline -3
   → Last 3 commits (for context)

Report each result back in order.
```

### Success Criteria

After running commands, local agent reports:
- Actual file counts in maker/ and config/maker/
- Adapter inventory (which ones exist)
- App startup success/failure
- MAKER executor presence
- Last 3 commits

**If maker/ is mostly empty or missing:** Phase 1-3 can proceed; Phase 4 will rebuild it  
**If app won't start:** Diagnose before moving forward (likely missing imports or bad config)

### Commit Template
(No commit needed for Phase 0 — pure investigation)

---

## Phase 1: CTO Removal (Safe Deletion)

### Objective
Delete spawner pattern and CTO mode. Simplify to PA + MAKER only.

### Why This Phase First
- Spawner is dead code (user has Claude Code on iOS instead)
- Safe deletion — no dependencies on it
- Clears confusion about what PA does vs what spawner does
- Must complete before Phase 3 (MAKER recovery)

### Components to Delete

#### 1. spawner.py (Entire File)
```bash
rm orchestrator/spawner.py
```
- 300+ lines of subprocess logic
- NOT referenced by PA dispatcher after this phase

#### 2. claude_code.py (Entire File)
```bash
rm orchestrator/proxy/adapters/claude_code.py
```
- NDJSON envelope wrapper
- References spawner
- Will be replaced by MAKER's deterministic executor

#### 3. models.py — Remove Caller enum value
**Before:**
```python
class Caller(StrEnum):
    PA = "pa"
    CTO_SUBAGENT = "cto_subagent"
    JOB_RUNNER = "job_runner"
```

**After:**
```python
class Caller(StrEnum):
    PA = "pa"
    JOB_RUNNER = "job_runner"
```

#### 4. parser.py — Remove @CTO command
**Before:**
```python
@commands = {
    "CTO": "switch_mode:cto",
    "PA": "switch_mode:pa",
    ...
}
```

**After:**
```python
@commands = {
    "PA": "switch_mode:pa",
    ...
}
```

#### 5. fsm.py — Simplify Mode FSM
**Before:**
```
PA ──@CTO──▶ CTO ──@PA──▶ PA
PA ──@Desktop──▶ DESKTOP_STUB ──(any input)──▶ PA
```

**After:**
```
PA ──@Desktop──▶ DESKTOP_STUB ──(any input)──▶ PA
(CTO mode removed entirely)
```

**Changes:**
- Remove `CTO` from `Mode` enum
- Remove `CTO` transitions
- Keep `PA` and `DESKTOP_STUB` only

#### 6. proxy/dispatcher.py — Remove CTO routing
Search for any references to `CTO_SUBAGENT` or `claude_code`:
- Remove routing rules for CTO mode
- Simplify allowed_callers sets

### Handover Prompt for Local Agent

```
Goal: Delete CTO spawner pattern safely.

1. Delete files:
   rm orchestrator/spawner.py
   rm orchestrator/proxy/adapters/claude_code.py

2. In models.py:
   - Find class Caller(StrEnum)
   - Delete the line: CTO_SUBAGENT = "cto_subagent"
   - Save

3. In parser.py:
   - Find the @commands dictionary
   - Delete or comment out the "CTO" entry
   - Save

4. In fsm.py:
   - Find Mode enum
   - Remove Mode.CTO if present
   - Find mode transitions
   - Delete any transition involving CTO
   - Leave PA ↔ PA and PA ↔ DESKTOP_STUB only
   - Save

5. In proxy/dispatcher.py:
   - Search for "CTO_SUBAGENT" or "claude_code"
   - Delete any routing rules that mention CTO
   - Save

6. Test startup:
   python -m orchestrator.main &
   sleep 3
   kill %1
   echo "SUCCESS" if no import errors, "FAILED" otherwise

7. Create commit:
   git add -A
   git commit -m "Remove CTO spawner pattern; simplify to PA + MAKER only"
```

### Success Criteria
- ✅ No import errors on app startup
- ✅ `@CTO` command is unrecognized (returns "Unknown command")
- ✅ grep for "CTO_SUBAGENT" returns 0 results (except in comments)
- ✅ spawner.py and claude_code.py deleted
- ✅ Commit pushed to branch

### Files Modified
- `orchestrator/models.py`
- `orchestrator/parser.py`
- `orchestrator/fsm.py`
- `orchestrator/proxy/dispatcher.py`

### Files Deleted
- `orchestrator/spawner.py`
- `orchestrator/proxy/adapters/claude_code.py`

---

## Phase 2: Groq Sidelining (Safe Moves)

### Objective
Move experimental code out of main flow. Preserve for post-MVP trial.

### Why This Phase
- Groq is not part of MVP (PA uses Haiku always)
- User wants to trial Groq post-MVP, so don't delete
- Sidelining keeps main codebase clean
- Zero risk — pure file moves

### Steps

#### 1. Create experiments/ Directory
```bash
mkdir -p experiments/
```

#### 2. Move Groq Adapter
```bash
mv orchestrator/proxy/adapters/pa_groq.py experiments/pa_groq.py
```

#### 3. Remove Groq from Dispatcher Routing
**In orchestrator/proxy/dispatcher.py:**
- Search for any conditional on `groq` or `pa_groq`
- Delete those routing rules
- Ensure PA always routes to Haiku (pa_haiku.py)

#### 4. Keep promotion.py for Later
- Do NOT delete (user wants to trial post-MVP)
- Leave in place; it's not loaded unless explicitly called

### Handover Prompt for Local Agent

```
Goal: Move Groq experiments to experiments/ directory.

1. Create experiments directory:
   mkdir -p experiments/

2. Move adapter:
   mv orchestrator/proxy/adapters/pa_groq.py experiments/pa_groq.py

3. In orchestrator/proxy/dispatcher.py:
   - Search for "groq" or "pa_groq" or "Groq"
   - Delete any routing rules that mention Groq
   - Ensure PA always routes to Haiku

4. Test:
   grep -r "pa_groq" orchestrator/ 2>/dev/null
   → Should return 0 results

5. Verify startup:
   python -m orchestrator.main &
   sleep 3
   kill %1
   → Should start clean

6. Create commit:
   git add -A
   git commit -m "Move Groq adapter to experiments/ (post-MVP trial)"
```

### Success Criteria
- ✅ `experiments/pa_groq.py` exists
- ✅ `orchestrator/proxy/adapters/pa_groq.py` deleted
- ✅ No references to groq in main orchestrator code
- ✅ App starts
- ✅ Commit pushed to branch

### Files Moved
- `orchestrator/proxy/adapters/pa_groq.py` → `experiments/pa_groq.py`

### Files Modified
- `orchestrator/proxy/dispatcher.py`

---

## Phase 3: MAKER Module Verification/Recovery

### Objective
Ensure MAKER executor framework exists and is wired correctly.

### Why This Phase
- MAKER is core to MVP (RAG, PowerShell, job execution)
- Phase 4 builds RAG components inside MAKER
- Must have foundation in place before RAG work starts

### If orchestrator/maker/ Exists and Has Files

#### Step 3a: Verify Imports
```bash
python -c "from orchestrator.maker.executor import MAKERExecutor; print('OK')"
```

**If OK:** Proceed to Phase 4

**If ImportError:** Handover to local agent with diagnostic:
```
The maker/ module exists but has import errors. Likely:
1. Missing __init__.py files
2. Circular imports
3. Missing dependencies in requirements.txt

Commands to diagnose:
  python -m py_compile orchestrator/maker/__init__.py
  python -m py_compile orchestrator/maker/executor.py
  
Report errors back; we'll fix before Phase 4.
```

### If orchestrator/maker/ Is Empty or Missing

#### Step 3b: Create MAKER Structure
```bash
mkdir -p orchestrator/maker/
touch orchestrator/maker/__init__.py
```

Create skeleton files for Phase 4 to build into:

**orchestrator/maker/__init__.py:**
```python
"""MAKER: Deterministic execution layer for RAG, PowerShell, jobs."""

from .executor import MAKERExecutor

__all__ = ["MAKERExecutor"]
```

**orchestrator/maker/executor.py:**
```python
"""MAKER executor: deterministic job runner, no LLM calls."""

from dataclasses import dataclass
from typing import Any, List
import asyncio


@dataclass
class JobResult:
    ok: bool
    data: Any | None
    error: str | None


class MAKERExecutor:
    """Deterministic executor for chunks, PowerShell, retrieval."""
    
    async def execute(self, job_kind: str, params: dict) -> JobResult:
        """Execute a deterministic job.
        
        job_kind: "chunk" | "retrieve" | "powershell" | "embed"
        params: dict with job-specific parameters
        
        Returns JobResult.
        """
        match job_kind:
            case "chunk":
                return await self.chunk_text(params)
            case "retrieve":
                return await self.retrieve(params)
            case "powershell":
                return await self.run_powershell(params)
            case "embed":
                return await self.embed_text(params)
            case _:
                return JobResult(False, None, f"Unknown job kind: {job_kind}")
    
    async def chunk_text(self, params: dict) -> JobResult:
        """Chunk text into overlapping segments."""
        # Phase 4 implementation
        return JobResult(True, {"chunks": []}, None)
    
    async def retrieve(self, params: dict) -> JobResult:
        """Retrieve top-K chunks by cosine similarity."""
        # Phase 4 implementation
        return JobResult(True, {"chunks": []}, None)
    
    async def run_powershell(self, params: dict) -> JobResult:
        """Execute PowerShell script."""
        # Phase 2 implementation
        return JobResult(True, {}, None)
    
    async def embed_text(self, params: dict) -> JobResult:
        """Embed text via Voyage API."""
        # Phase 4 implementation
        return JobResult(True, {"embedding": []}, None)
```

**orchestrator/maker/chunker.py:**
```python
"""Text chunking: split documents into overlapping 500-token segments."""

# Phase 4 implementation


def chunk_text(text: str, chunk_size_tokens: int = 500, overlap_tokens: int = 50) -> list[str]:
    """Split text into overlapping chunks."""
    # To implement: split by sentence/word boundary, maintain overlap
    pass
```

**orchestrator/maker/vector_store.py:**
```python
"""In-memory per-session vector storage (NumPy)."""

# Phase 4 implementation


class VectorStore:
    """Per-session ephemeral vector store."""
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.chunks = []  # list of text chunks
        self.embeddings = None  # numpy array, shape (N, 1024)
        self.metadata = {}  # chunk metadata
    
    def add(self, chunks: list[str], embeddings: list[list[float]]) -> None:
        """Add chunks and embeddings to store."""
        pass
    
    def retrieve(self, query_embedding: list[float], top_k: int = 5) -> list[str]:
        """Retrieve top-K chunks by cosine similarity."""
        pass
    
    def clear(self) -> None:
        """Discard all chunks (on session end)."""
        pass
```

**orchestrator/maker/retrieval.py:**
```python
"""Retrieve relevant chunks from vector store."""

# Phase 4 implementation


async def retrieve_chunks(
    query: str,
    vector_store,
    embedding_adapter,
    top_k: int = 5
) -> list[str]:
    """Embed query, search vector store, return top-K chunks."""
    pass
```

### Handover Prompt for Local Agent

```
Goal: Ensure MAKER module structure exists.

1. Check if orchestrator/maker/ exists:
   ls -la orchestrator/maker/ 2>/dev/null || echo "MISSING"

2. If it exists and has files:
   - Run: python -c "from orchestrator.maker.executor import MAKERExecutor; print('OK')"
   - If OK: Report "MAKER module imports successfully"
   - If error: Report the error; I'll fix before Phase 4

3. If it's missing or empty:
   - Create: mkdir -p orchestrator/maker/
   - Create: touch orchestrator/maker/__init__.py
   
   Then, create these skeleton files with the content I provide:
   - orchestrator/maker/executor.py
   - orchestrator/maker/chunker.py
   - orchestrator/maker/vector_store.py
   - orchestrator/maker/retrieval.py
   
   (I'll paste the full content for each; copy exactly)

4. Verify:
   python -c "from orchestrator.maker.executor import MAKERExecutor; print('OK')"
   → Should print OK

5. Create commit:
   git add orchestrator/maker/
   git commit -m "Create MAKER module structure for RAG/PowerShell execution"
```

### Success Criteria
- ✅ `orchestrator/maker/__init__.py` exists
- ✅ `orchestrator/maker/executor.py` exists with MAKERExecutor class
- ✅ Chunker, vector_store, retrieval skeleton files present
- ✅ `from orchestrator.maker.executor import MAKERExecutor` works
- ✅ App starts without import errors
- ✅ Commit pushed to branch

### Files Created (if missing)
- `orchestrator/maker/__init__.py`
- `orchestrator/maker/executor.py`
- `orchestrator/maker/chunker.py`
- `orchestrator/maker/vector_store.py`
- `orchestrator/maker/retrieval.py`

---

## Phase 4: Build RAG Components (Core MVP)

### Objective
Implement RAG pipeline: chunking → embedding → retrieval → map-reduce synthesis

### Key Milestones

#### 4.1: Text Chunking
**File:** `orchestrator/maker/chunker.py`

**Spec:**
- Input: raw text (string)
- Output: list of overlapping 500-token segments
- Overlap: 50 tokens (context preservation across chunks)
- Boundary: split on sentence or word boundary, not mid-word
- Tokenizer: anthropic `count_tokens` for accurate counts

**Interface:**
```python
async def chunk_text(
    text: str,
    chunk_size_tokens: int = 500,
    overlap_tokens: int = 50,
    anthropic_client = None
) -> list[str]:
    """Split text into overlapping chunks.
    
    Returns: list of chunk strings, each ~500 tokens.
    """
```

**Handover Prompt:**
```
Implement text chunking in orchestrator/maker/chunker.py.

Spec:
- Read: raw document text (PDF extracted or HTML)
- Split: on sentence boundaries (use NLTK or regex)
- Size: ~500 tokens per chunk (use anthropic.count_tokens)
- Overlap: 50 tokens between consecutive chunks
- Output: list[str]

Algorithm sketch:
  1. Split text by sentence (use sent_tokenizer from nltk or similar)
  2. Group sentences into chunks, accumulating token count
  3. When reaching ~500 tokens, finalize chunk
  4. Overlap: include last 50 tokens of previous chunk in next
  5. Return list of chunks

Test:
  text = "Long document..."
  chunks = chunk_text(text)
  → Print number of chunks and first chunk length
  → Verify chunks overlap (last sentences of chunk N appear in chunk N+1)

Create commit: git add orchestrator/maker/chunker.py && git commit -m "Implement text chunking (500-token overlapping segments)"
```

---

#### 4.2: Embedding Adapter (Voyage AI)
**File:** `orchestrator/proxy/adapters/voyage_embed.py` (NEW)

**Spec:**
- API: Voyage AI embeddings API (1024-dimensional vectors)
- Input: list of text chunks
- Output: list[list[float]] (vectors)
- Caching: per-session (keyed by session_id)
- Error handling: retry on rate limit, escalate on quota

**Interface:**
```python
class VoyageEmbeddingAdapter(Tool):
    name = "voyage_embed"
    
    async def invoke(
        self,
        payload: dict,
        deadline_s: float,
        caller: Caller
    ) -> Result:
        """
        payload: {
            "texts": list[str],
            "session_id": str
        }
        
        Returns: {
            "embeddings": list[list[float]],  # shape (N, 1024)
            "cache_hits": int,
            "api_calls": int
        }
        """
```

**Handover Prompt:**
```
Implement Voyage AI embedding adapter in orchestrator/proxy/adapters/voyage_embed.py.

Requirements:
- API: https://api.voyageai.com/v1/embeddings (use your API key from .env)
- Model: voyage-3-large (1024 dimensions)
- Input: list of text strings (chunks)
- Output: list of vectors (embeddings)
- Cache: per-session in memory (dict: session_id -> {text -> embedding})
- Batch: if >100 texts, split into multiple API calls
- Error handling: retry on 429 (rate limit), escalate on 401/403/500

Template structure:
class VoyageEmbeddingAdapter(Tool):
    name = "voyage_embed"
    allowed_callers = {Caller.PA, Caller.JOB_RUNNER}
    
    def __init__(self):
        self.api_key = os.getenv("VOYAGE_API_KEY")
        self.base_url = "https://api.voyageai.com/v1"
        self.cache = {}  # session_id -> {text -> embedding}
    
    async def invoke(self, payload, deadline_s, caller):
        texts = payload["texts"]
        session_id = payload["session_id"]
        
        # Check cache first
        embeddings = []
        uncached_texts = []
        cache_hits = 0
        
        for text in texts:
            if session_id in self.cache and text in self.cache[session_id]:
                embeddings.append(self.cache[session_id][text])
                cache_hits += 1
            else:
                uncached_texts.append(text)
        
        # Call API for uncached
        api_calls = 0
        if uncached_texts:
            api_calls = await self._call_voyage(uncached_texts, session_id)
            # Update embeddings list in order
            ...
        
        return Result(
            ok=True,
            data={
                "embeddings": embeddings,
                "cache_hits": cache_hits,
                "api_calls": api_calls
            },
            ...
        )

Test:
  - Embed a sample chunk
  - Verify shape: (1, 1024)
  - Embed same chunk again; verify cache hit
  - Embed different session; verify no cache sharing

Create commit: git add orchestrator/proxy/adapters/voyage_embed.py && git commit -m "Add Voyage AI embedding adapter with per-session caching"
```

---

#### 4.3: Vector Store Implementation
**File:** `orchestrator/maker/vector_store.py`

**Spec:**
- Per-session, in-memory ephemeral storage
- Data: text chunks + embeddings (1024-dim numpy arrays)
- Backend: numpy arrays for fast cosine similarity
- Persistence: None (cleared on session end)
- Metadata: chunk source (which PDF, which URL, etc.)

**Interface:**
```python
class VectorStore:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.chunks = []  # list[str]
        self.embeddings = None  # numpy.ndarray, shape (N, 1024)
        self.metadata = []  # list[dict] with source, page, etc.
    
    async def add(
        self,
        chunks: list[str],
        embeddings: list[list[float]],
        metadata: list[dict] = None
    ) -> None:
        """Add chunks and embeddings to store."""
    
    async def retrieve(
        self,
        query_embedding: list[float],
        top_k: int = 5
    ) -> list[dict]:
        """Return top-K chunks by cosine similarity.
        
        Returns: [{"text": str, "score": float, "metadata": dict}, ...]
        """
    
    def clear(self) -> None:
        """Discard all data (on session end)."""
```

**Handover Prompt:**
```
Implement in-memory vector store in orchestrator/maker/vector_store.py.

Requirements:
- Store: chunks (strings) + embeddings (numpy arrays, 1024-dim)
- Retrieve: cosine similarity search, return top-K
- Session-scoped: ephemeral, cleared on session end
- No persistence

Use numpy.dot and numpy.linalg.norm for cosine similarity:
  cos_sim = np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

Class structure:
class VectorStore:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.chunks = []
        self.embeddings = None  # None until first add
        self.metadata = []
    
    async def add(self, chunks, embeddings, metadata=None):
        # Convert embeddings list to numpy array
        # Append to self.chunks, extend self.embeddings, append metadata
        # Handle first-time init of self.embeddings
        
    async def retrieve(self, query_embedding, top_k=5):
        # Compute cosine similarity between query_embedding and all stored embeddings
        # Sort by score descending
        # Return top_k with text, score, metadata
        
    def clear(self):
        self.chunks = []
        self.embeddings = None
        self.metadata = []

Test:
  store = VectorStore("session_123")
  await store.add(["chunk1", "chunk2"], [[...1024...], [...1024...]])
  results = await store.retrieve([...1024...], top_k=2)
  → Should return 2 results with scores and metadata

Create commit: git add orchestrator/maker/vector_store.py && git commit -m "Implement in-memory vector store with cosine similarity retrieval"
```

---

#### 4.4: Retrieval Pattern
**File:** `orchestrator/maker/retrieval.py`

**Spec:**
- Input: user query (string)
- Process: embed query → search vector store → return top-K chunks
- Output: list of relevant chunks with scores

**Interface:**
```python
async def retrieve_relevant_chunks(
    query: str,
    vector_store: VectorStore,
    embedding_adapter,
    top_k: int = 5
) -> list[dict]:
    """Embed query, search store, return top-K chunks.
    
    Returns: [{"text": str, "score": float, "metadata": dict}, ...]
    """
```

**Handover Prompt:**
```
Implement retrieval in orchestrator/maker/retrieval.py.

Algorithm:
  1. Embed the query using embedding_adapter
  2. Call vector_store.retrieve(query_embedding, top_k)
  3. Return results (already ranked by cosine similarity)

Function:
async def retrieve_relevant_chunks(
    query: str,
    vector_store,
    embedding_adapter,
    top_k: int = 5
) -> list[dict]:
    # 1. Embed query
    result = await embedding_adapter.invoke({
        "texts": [query],
        "session_id": vector_store.session_id
    }, deadline_s=10, caller=Caller.PA)
    
    if not result.ok:
        return []  # or raise
    
    query_embedding = result.data["embeddings"][0]
    
    # 2. Retrieve
    chunks = await vector_store.retrieve(query_embedding, top_k)
    
    return chunks

Test:
  query = "What is machine learning?"
  chunks = await retrieve_relevant_chunks(query, store, adapter, top_k=5)
  → Should return 5 chunks related to ML
  → Each chunk should have "text", "score", "metadata"

Create commit: git add orchestrator/maker/retrieval.py && git commit -m "Implement query embedding and chunk retrieval pipeline"
```

---

#### 4.5: Map-Reduce Executor (Worker Parallelism + Synthesis)
**File:** `orchestrator/maker/executor.py` (expand)

**Spec:**
- Input: relevant chunks + user question
- Map: spawn N parallel Haiku calls (workers) to extract answer from each chunk
- Reduce: gather results → single Sonnet call to synthesize final answer
- Output: synthesized response + cost breakdown

**Interface:**
```python
class MAKERExecutor:
    async def map_reduce_synthesis(
        self,
        chunks: list[str],
        question: str,
        session_id: str,
        haiku_adapter,
        sonnet_adapter
    ) -> dict:
        """Map-reduce synthesis over retrieved chunks.
        
        Returns: {
            "answer": str,
            "worker_outputs": list[str],
            "cost_usd": float,
            "latency_ms": float
        }
        """
```

**Algorithm:**

```python
async def map_reduce_synthesis(
    chunks: list[str],
    question: str,
    session_id: str,
    haiku_adapter,
    sonnet_adapter
) -> dict:
    
    # PHASE 1: MAP (parallel Haiku workers)
    import asyncio
    import time
    
    start_time = time.time()
    
    worker_tasks = []
    for i, chunk in enumerate(chunks):
        task = haiku_adapter.invoke(
            payload={
                "prompt": f"Question: {question}\n\nChunk:\n{chunk}\n\nExtract a brief answer.",
                "session_id": session_id
            },
            deadline_s=15,
            caller=Caller.PA
        )
        worker_tasks.append(task)
    
    # Wait for all workers
    worker_results = await asyncio.gather(*worker_tasks, return_exceptions=True)
    
    # Collect outputs
    worker_outputs = []
    worker_cost = 0
    for result in worker_results:
        if isinstance(result, Exception):
            worker_outputs.append(f"Error: {result}")
        elif result.ok:
            worker_outputs.append(result.data.get("text", ""))
            worker_cost += result.cost_usd
        else:
            worker_outputs.append(f"Error: {result.error}")
    
    # PHASE 2: REDUCE (single Sonnet call)
    synthesis_prompt = f"""
    Question: {question}
    
    Below are extracted insights from {len(chunks)} relevant chunks.
    Synthesize these into a coherent, factual answer. If insights conflict, note it.
    
    Insights:
    """ + "\n".join(f"- {out}" for out in worker_outputs)
    
    synthesis_result = await sonnet_adapter.invoke(
        payload={
            "prompt": synthesis_prompt,
            "session_id": session_id
        },
        deadline_s=20,
        caller=Caller.PA
    )
    
    end_time = time.time()
    
    if not synthesis_result.ok:
        return {
            "answer": "Synthesis failed",
            "worker_outputs": worker_outputs,
            "cost_usd": worker_cost + synthesis_result.cost_usd,
            "latency_ms": int((end_time - start_time) * 1000),
            "error": synthesis_result.error
        }
    
    return {
        "answer": synthesis_result.data.get("text", ""),
        "worker_outputs": worker_outputs,
        "cost_usd": worker_cost + synthesis_result.cost_usd,
        "latency_ms": int((end_time - start_time) * 1000)
    }
```

**Handover Prompt:**
```
Implement map-reduce synthesis in orchestrator/maker/executor.py.

Requirements:
- MAP: spawn N parallel Haiku calls (one per chunk)
  - Each Haiku extracts answer to question from its chunk
  - Use asyncio.gather() for parallelism
  
- REDUCE: single Sonnet call
  - Input: all N Haiku outputs
  - Task: synthesize into coherent answer
  - Output: final response

Spec details:
- Haiku worker prompt: "Question: {question}\n\nChunk:\n{chunk}\n\nExtract answer."
- Sonnet synthesize prompt: "Question: {question}\n\nInsights:\n[worker outputs]\n\nSynthesize into coherent answer."
- Track cost_usd (sum of all worker + synthesizer costs)
- Track latency_ms (total time from first worker spawn to synthesis done)
- Return: {"answer": str, "worker_outputs": list[str], "cost_usd": float, "latency_ms": int}

Test:
  chunks = ["ML is...", "AI includes...", "Deep learning uses..."]
  question = "What is machine learning?"
  result = await executor.map_reduce_synthesis(chunks, question, "session_123", haiku, sonnet)
  → result["answer"] should be coherent synthesis
  → result["cost_usd"] should be >0
  → result["worker_outputs"] should have 3 entries

Create commit: git add orchestrator/maker/executor.py && git commit -m "Implement map-reduce synthesis (parallel Haiku workers + Sonnet synthesizer)"
```

---

### Integration: Wiring RAG into PA Dispatcher

**File:** `orchestrator/proxy/dispatcher.py`

**Changes:**
- Add route: when user asks question in RAG mode, dispatcher invokes:
  1. Retrieve relevant chunks
  2. Map-reduce synthesis
  3. Return answer

**Handover Prompt:**
```
In orchestrator/proxy/dispatcher.py, add a route for RAG queries:

def should_use_rag(intent: Intent) -> bool:
    # Check if session has an active knowledge base (vector store exists)
    return intent.session_id in active_rag_sessions  # or similar flag

async def dispatch(intent: Intent) -> Result:
    # ... existing logic ...
    
    if should_use_rag(intent):
        # 1. Retrieve chunks
        chunks = await retrieve_relevant_chunks(
            intent.payload["text"],
            vector_store,
            embedding_adapter,
            top_k=5
        )
        
        # 2. Map-reduce synthesis
        result = await maker_executor.map_reduce_synthesis(
            chunks,
            intent.payload["text"],
            intent.session_id,
            haiku_adapter,
            sonnet_adapter
        )
        
        return Result(
            ok=result.get("ok", True),
            data={"text": result["answer"], "worker_count": len(chunks)},
            cost_usd=result["cost_usd"],
            meta={"latency_ms": result["latency_ms"]}
        )
    
    # ... continue with existing dispatch logic ...
```

---

### Phase 4 Success Criteria

- ✅ Chunker splits 10-page PDF into ~20 overlapping chunks
- ✅ Embedding adapter returns (N, 1024) vectors via Voyage API
- ✅ Vector store stores chunks + embeddings
- ✅ Retrieval ranks chunks by cosine similarity
- ✅ Map-reduce spawns 5 Haiku calls in parallel + 1 Sonnet synthesis
- ✅ Full RAG loop: user question → retrieve → synthesize → answer (latency <10s)
- ✅ Cost per query: <$0.10 (5x Haiku ~0.01¢ + 1x Sonnet ~0.04¢)
- ✅ No import errors; app starts cleanly

---

## Phase 5: Integration & E2E Test (MVP Gate)

### Objective
Verify full RAG workflow end-to-end.

### Test Scenarios

#### Scenario 1: PDF Upload & Knowledge Base Initialization
```
User: Uploads 10-page PDF on "Machine Learning Basics"
↓
PA triggers chunking → embedding → storage
↓
Vector store contains ~20 chunks with embeddings
↓
Success: User sees confirmation message
```

#### Scenario 2: Question on Knowledge Base
```
User: "What is supervised learning?"
↓
PA retrieves top-5 relevant chunks
↓
PA spawns 5 Haiku workers to extract answers
↓
PA calls Sonnet to synthesize
↓
Response: Coherent answer covering supervised learning
↓
Success: Answer is accurate; latency <10s; cost <$0.10
```

#### Scenario 3: Multi-Turn Conversation
```
User Q1: "What is overfitting?"
↓
Response R1 (via RAG)
↓
User Q2: "How do you prevent it?"
↓
Response R2 (via RAG, same knowledge base)
↓
Success: R2 builds on R1; both are accurate
```

#### Scenario 4: Telegram Channel
```
Same as Scenario 2, but via Telegram bot
↓
User sends message via Telegram
↓
PA processes, triggers RAG
↓
Response sent back via Telegram
↓
Success: Telegram round-trip works
```

### Handover Prompt for Local Agent

```
Goal: Run E2E tests for RAG MVP.

Test 1: PDF Upload
  - Create a 10-page test PDF (or use existing)
  - Upload via web UI
  - Check logs: chunking completes, embeddings stored
  - Verify: vector store is not empty

Test 2: Single Question
  - Ask: "What is the main topic of this document?"
  - Wait for response
  - Check: latency <10s, cost <$0.10
  - Verify: answer mentions actual content

Test 3: Follow-up Question
  - Ask related question
  - Check: response is coherent with first answer
  - Verify: cost accumulates correctly

Test 4: Telegram
  - Send same question via Telegram
  - Check: response arrives via Telegram
  - Verify: content is same as web UI

Success criteria:
  - All tests pass
  - No errors in logs
  - Costs are <$1 total for all tests
  - Latency is <10s per query

Report pass/fail + cost breakdown.
```

### Success Criteria for MVP Gate

- ✅ PDF chunking works
- ✅ Embeddings are created and stored
- ✅ Retrieval finds relevant chunks
- ✅ Haiku workers extract answers in parallel
- ✅ Sonnet synthesizes coherent responses
- ✅ Latency <10s per query
- ✅ Cost <$0.10 per query
- ✅ Web UI works
- ✅ Telegram works
- ✅ No unhandled exceptions

**If all pass:** MVP gate is complete. Phase 6 (PowerShell) can begin.

**If any fail:** Diagnose, fix, re-test before proceeding.

---

## Phase 6: PowerShell Adapter (Phase 2, Lower Priority)

### Objective
Enable MAKER to execute PowerShell scripts and feed results back into RAG for analysis.

### Implementation (High Level)

**File:** `orchestrator/proxy/adapters/powershell.py`

**Spec:**
- Input: PowerShell script or command
- Output: stdout/stderr + exit code
- Execution: Windows native, no intermediary
- Safety: path validation, no recursive shell escaping
- Error handling: timeout, process kill, capture errors

**Integration:**
- MAKER executor calls powershell adapter
- Results can be chunked + embedded → fed back into RAG for analysis
- Daily reports: PowerShell results → Haiku workers analyze → Sonnet synthesizes report

### Handover Prompt for Local Agent (Phase 2)

```
Goal: Build PowerShell adapter for Phase 2.

Implementation:
  1. Create orchestrator/proxy/adapters/powershell.py
  
  2. Class structure:
     class PowerShellAdapter(Tool):
         name = "powershell"
         allowed_callers = {Caller.PA, Caller.JOB_RUNNER}
         
         async def invoke(self, payload, deadline_s, caller):
             # payload: {"script": str, "timeout_s": int}
             # Returns: {"stdout": str, "stderr": str, "exit_code": int}
  
  3. Implementation:
     - Use subprocess.Popen with PIPE
     - Set timeout via asyncio.wait_for
     - Kill process on timeout
     - Capture stdout + stderr
     - Return exit code
  
  4. Error handling:
     - Timeout → return error
     - Bad script → return stderr
     - Permission denied → return error
  
  5. Test:
     - Run: Get-Date (should return current date/time)
     - Run: Exit 1 (should return exit_code=1)
     - Run: sleep 100 (should timeout + kill process)

Create commit: git add orchestrator/proxy/adapters/powershell.py && git commit -m "Implement PowerShell adapter for mini PC automation (Phase 2)"
```

### Phase 6 Success Criteria

- ✅ PowerShell script executes
- ✅ Stdout/stderr captured
- ✅ Exit code returned
- ✅ Timeout + process kill works
- ✅ Results can be chunked + fed to RAG
- ✅ Map-reduce analysis of results works

---

## Decision Checklist

Before starting each phase, confirm:

| Phase | Pre-Condition | Status |
|-------|--------------|--------|
| 0 | None | Ready ✓ |
| 1 | Phase 0 complete | Blocked until Phase 0 ✓ |
| 2 | Phase 1 complete | Blocked until Phase 1 ✓ |
| 3 | Phase 1-2 complete | Blocked until Phase 1 ✓ |
| 4 | Phase 3 complete | Blocked until Phase 3 ✓ |
| 5 | Phase 4 complete | Blocked until Phase 4 ✓ |
| 6 | Phase 5 complete | Blocked until Phase 5 ✓ |

---

## Commit Messages

Use these templates for consistency:

**Phase 1:**
```
Remove CTO spawner pattern; simplify to PA + MAKER only
```

**Phase 2:**
```
Move Groq adapter to experiments/ (post-MVP trial)
```

**Phase 3:**
```
Create MAKER module structure for RAG/PowerShell execution
```

**Phase 4.1:**
```
Implement text chunking (500-token overlapping segments)
```

**Phase 4.2:**
```
Add Voyage AI embedding adapter with per-session caching
```

**Phase 4.3:**
```
Implement in-memory vector store with cosine similarity retrieval
```

**Phase 4.4:**
```
Implement query embedding and chunk retrieval pipeline
```

**Phase 4.5:**
```
Implement map-reduce synthesis (parallel Haiku workers + Sonnet synthesizer)
```

**Phase 5:**
```
Verify RAG MVP end-to-end (PDF → Q&A → Synthesis)
```

**Phase 6:**
```
Implement PowerShell adapter for mini PC automation (Phase 2)
```

---

## Timeline Estimate

| Phase | Time | Blocker |
|-------|------|---------|
| 0 | 30 min | Phase 0 → Phase 1 |
| 1-3 | 2-3 hrs | Phase 3 → Phase 4 |
| 4 | 4-6 hrs | Phase 4 → Phase 5 |
| 5 | 2-3 hrs | Phase 5 → Phase 6 |
| 6 | 3-4 hrs | (Phase 2, lower priority) |

**Total for MVP (Phases 0-5): ~1-2 working days**

---

## Next Steps

1. **You (on local machine):**
   - Run Phase 0 verification
   - Report findings

2. **I (on this session):**
   - Adjust Phases 1-3 based on Phase 0 findings
   - Prepare detailed code handover for Phase 4

3. **You (on local machine):**
   - Execute Phases 1-3 (deletions, moves, skeleton)
   - Commit and push

4. **I:**
   - Review commits
   - Provide detailed Phase 4 implementation prompts

5. **You:**
   - Build RAG components (4.1-4.5)
   - Run E2E tests (Phase 5)
   - Verify MVP gate passes

6. **Phase 6:**
   - PowerShell adapter (post-MVP, Phase 2)

---

## Resources

- **Project Vision:** `01.Project_Management/Project_Vision.md`
- **Adapter Spec:** `01.Project_Management/adapter-spec.md`
- **Architecture Diagram:** `01.Project_Management/arch_diagram.md`
- **Security Model:** `01.Project_Management/security-model.md`
- **.env template:** Check `.env.example` for required API keys (Voyage, Anthropic)

