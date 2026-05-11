# Execution Plan: PA Orchestrator MVP (PowerShell Iterative)

> Step-by-step implementation roadmap for PowerShell-first MVP. Each phase has clear objectives, handover prompts, and success criteria.

---

## Overview

**Total Phases:** 7 (0-6)  
**MVP Gate:** Phase 6 (full iterative goal execution)  
**Phase 1.5:** RAG PowerShell history (post-MVP, before Phase 2)

**Blocking Dependencies:** Phase 0 → Phase 1 → Phases 2-3 → Phase 4 → Phase 5 → Phase 6 → Phase 1.5

**Estimated Timeline:**
- Phase 0 (Verification): 30 min (local machine)
- Phase 1-3 (Cleanup): 2-3 hours
- Phase 4 (PowerShell Adapter): 2-3 hours
- Phase 5 (Iterative Loop): 4-6 hours
- Phase 6 (E2E MVP Gate): 2-3 hours
- **Total to MVP: ~1-2 working days**
- Phase 1.5 (RAG): 3-4 hours (post-MVP)

---

## Phase 0: Local Verification (BLOCKING)

### Objective
Confirm actual codebase state. Identify gaps between BUILD_STATUS claims and real code.

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

6. grep "async def invoke" orchestrator/proxy/adapters/*.py | grep -c "def invoke"
   → How many adapters have invoke methods?

7. git log --oneline -3
   → Last 3 commits (for context)

8. find orchestrator -name "*.py" | wc -l
   → Total Python files in orchestrator/

Report each result back in order.
```

### Success Criteria

Local agent reports:
- File counts in maker/ and config/maker/
- Adapter inventory
- App startup success/failure
- Total invoke methods
- Last 3 commits

**Result determines whether Phase 3 creates MAKER from scratch or recovers existing code.**

---

## Phase 1: CTO Removal (Safe Deletion)

### Objective
Delete spawner pattern and CTO mode. Simplify to PA + MAKER only.

### Components to Delete

#### 1. spawner.py (Entire File)
```bash
rm orchestrator/spawner.py
```

#### 2. claude_code.py (Entire File)
```bash
rm orchestrator/proxy/adapters/claude_code.py
```

#### 3. models.py — Remove Caller enum value
Remove: `CTO_SUBAGENT = "cto_subagent"`

#### 4. parser.py — Remove @CTO command
Remove the "CTO" entry from @commands dictionary

#### 5. fsm.py — Simplify Mode FSM
Remove `Mode.CTO` and all CTO transitions. Keep only PA ↔ PA and PA ↔ DESKTOP_STUB.

#### 6. proxy/dispatcher.py — Remove CTO routing
Delete any routing rules for `CTO_SUBAGENT` or `claude_code`.

### Handover Prompt for Local Agent

```
Goal: Delete CTO spawner pattern safely.

1. Delete files:
   rm orchestrator/spawner.py
   rm orchestrator/proxy/adapters/claude_code.py

2. In models.py:
   - Find class Caller(StrEnum)
   - Delete: CTO_SUBAGENT = "cto_subagent"
   - Save

3. In parser.py:
   - Find @commands dictionary
   - Delete the "CTO" entry
   - Save

4. In fsm.py:
   - Find Mode enum
   - Remove Mode.CTO if present
   - Delete any CTO transitions
   - Keep PA ↔ PA and PA ↔ DESKTOP_STUB only
   - Save

5. In proxy/dispatcher.py:
   - Search for "CTO_SUBAGENT" or "claude_code"
   - Delete any routing rules
   - Save

6. Test startup:
   python -m orchestrator.main &
   sleep 3
   kill %1
   → Should succeed without import errors

7. Verify:
   grep -r "CTO_SUBAGENT" orchestrator/ 2>/dev/null | wc -l
   → Should be 0

8. Create commit:
   git add -A
   git commit -m "Remove CTO spawner pattern; simplify to PA + MAKER only"
```

### Success Criteria
- ✅ No import errors on app startup
- ✅ `@CTO` command is unrecognized
- ✅ grep for "CTO_SUBAGENT" returns 0 results
- ✅ spawner.py and claude_code.py deleted
- ✅ Commit pushed to branch

---

## Phase 2: Groq Sidelining (Safe Moves)

### Objective
Move experimental code out of main flow. Preserve for post-MVP trial.

### Steps

#### 1. Create experiments/ Directory
```bash
mkdir -p experiments/
```

#### 2. Move Groq Adapter
```bash
mv orchestrator/proxy/adapters/pa_groq.py experiments/pa_groq.py
```

#### 3. Remove Groq from Dispatcher
In `orchestrator/proxy/dispatcher.py`, delete any routing rules for Groq.

### Handover Prompt for Local Agent

```
Goal: Move Groq experiments to experiments/ directory.

1. Create:
   mkdir -p experiments/

2. Move:
   mv orchestrator/proxy/adapters/pa_groq.py experiments/pa_groq.py

3. In orchestrator/proxy/dispatcher.py:
   - Search for "groq" or "pa_groq"
   - Delete any routing rules
   - Save

4. Verify:
   grep -r "pa_groq" orchestrator/ 2>/dev/null | wc -l
   → Should be 0

5. Test startup:
   python -m orchestrator.main &
   sleep 3
   kill %1
   → Should start cleanly

6. Create commit:
   git add -A
   git commit -m "Move Groq adapter to experiments/ (post-MVP trial)"
```

### Success Criteria
- ✅ `experiments/pa_groq.py` exists
- ✅ `orchestrator/proxy/adapters/pa_groq.py` deleted
- ✅ No references to groq in main code
- ✅ App starts
- ✅ Commit pushed

---

## Phase 3: MAKER Module Verification/Recovery

### Objective
Ensure MAKER executor framework exists and is wired correctly.

### If orchestrator/maker/ Exists and Has Files

#### Step 3a: Verify Imports
```bash
python -c "from orchestrator.maker.executor import MAKERExecutor; print('OK')"
```

**If OK:** Proceed to Phase 4

**If ImportError:** Diagnose and fix before Phase 4

### If orchestrator/maker/ Is Empty or Missing

#### Step 3b: Create MAKER Structure
```bash
mkdir -p orchestrator/maker/
touch orchestrator/maker/__init__.py
```

**Create skeleton files:**

**orchestrator/maker/__init__.py:**
```python
"""MAKER: Deterministic execution layer for PowerShell, RAG, jobs."""

from .executor import MAKERExecutor
from .iterative_goal import IterativeGoalExecutor

__all__ = ["MAKERExecutor", "IterativeGoalExecutor"]
```

**orchestrator/maker/executor.py:**
```python
"""MAKER executor: deterministic execution, no LLM calls per step."""

from dataclasses import dataclass
from typing import Any


@dataclass
class ExecutionResult:
    ok: bool
    stdout: str
    stderr: str
    exit_code: int
    error: str | None


class MAKERExecutor:
    """Execute deterministic operations (PowerShell, retrieval, etc)."""
    
    async def execute_powershell(self, script: str, timeout_s: int = 300) -> ExecutionResult:
        """Execute PowerShell script, capture output."""
        # Phase 4 implementation
        return ExecutionResult(True, "", "", 0, None)
    
    async def chunk_text(self, text: str) -> list[str]:
        """Chunk text into overlapping segments."""
        # Phase 1.5 implementation
        return []
    
    async def embed_text(self, text: str) -> list[float]:
        """Embed text via Voyage API."""
        # Phase 1.5 implementation
        return []
```

**orchestrator/maker/iterative_goal.py:**
```python
"""Iterative goal execution: decide → execute → analyze → repeat."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GoalState:
    goal: str
    steps: list = field(default_factory=list)
    current_iteration: int = 0
    max_iterations: int = 10


class IterativeGoalExecutor:
    """Execute user goals iteratively until complete."""
    
    async def execute_goal(
        self,
        user_intent: str,
        session_id: str,
        sonnet_adapter,
        powershell_adapter,
        haiku_adapter
    ) -> dict:
        """Execute goal iteratively until achieved or max iterations."""
        # Phase 5 implementation
        pass
```

### Handover Prompt for Local Agent

```
Goal: Ensure MAKER module structure exists.

1. Check if orchestrator/maker/ exists:
   ls -la orchestrator/maker/ 2>/dev/null || echo "MISSING"

2. If it exists and has files:
   python -c "from orchestrator.maker.executor import MAKERExecutor; print('OK')"
   → If OK: proceed to Phase 4
   → If error: report the error

3. If missing or empty:
   mkdir -p orchestrator/maker/
   touch orchestrator/maker/__init__.py
   
   Then create these skeleton files with the content I provide:
   - orchestrator/maker/executor.py
   - orchestrator/maker/iterative_goal.py

4. Verify:
   python -c "from orchestrator.maker.executor import MAKERExecutor; print('OK')"
   → Should print OK

5. Test startup:
   python -m orchestrator.main &
   sleep 3
   kill %1
   → Should start without import errors

6. Create commit:
   git add orchestrator/maker/
   git commit -m "Create MAKER module structure for iterative goal execution"
```

### Success Criteria
- ✅ `orchestrator/maker/__init__.py` exists
- ✅ `orchestrator/maker/executor.py` exists with MAKERExecutor
- ✅ `orchestrator/maker/iterative_goal.py` exists with IterativeGoalExecutor
- ✅ Imports work without errors
- ✅ App starts cleanly
- ✅ Commit pushed

---

## Phase 4: PowerShell Adapter (Core MVP)

### Objective
Build the execution canvas: reliable PowerShell script execution with output capture.

### File: `orchestrator/proxy/adapters/powershell.py` (NEW)

**Spec:**
- Input: PowerShell script or command string
- Output: stdout, stderr, exit code
- Timeout: configurable (default 300s)
- Error handling: capture exceptions, timeout kills process
- Windows-native: use `subprocess.Popen`

**Interface:**
```python
class PowerShellAdapter(Tool):
    name = "powershell"
    allowed_callers = {Caller.PA, Caller.JOB_RUNNER, Caller.MAKER}
    
    async def invoke(self, payload: dict, deadline_s: float, caller: Caller) -> Result:
        """
        payload: {
            "script": str,  # PowerShell code to execute
            "timeout_s": int  # timeout (default 300)
        }
        
        Returns: Result with data = {
            "stdout": str,
            "stderr": str,
            "exit_code": int
        }
        """
```

**Implementation Details:**
- Use `subprocess.Popen` with `stdout=PIPE, stderr=PIPE`
- Wrap with `asyncio.wait_for()` for timeout
- On timeout: `proc.terminate()` → wait 5s → `proc.kill()`
- Capture all output; strip sensitive patterns (if any)
- Return exit code even on error

**Error Handling:**
- Timeout → `Result(ok=False, error="Timeout after Xs")`
- Bad script syntax → `Result(ok=True, data={..., exit_code=1}, error=stderr)`
- Process killed → `Result(ok=False, error="Process killed")`

### Handover Prompt for Local Agent

```
Goal: Build PowerShell adapter for MVP execution canvas.

Requirements:
- Execute PowerShell script or command
- Capture stdout, stderr, exit code
- Timeout support (default 300s, kill on breach)
- Windows native subprocess

File: orchestrator/proxy/adapters/powershell.py

Template structure:
class PowerShellAdapter(Tool):
    name = "powershell"
    allowed_callers = {Caller.PA, Caller.JOB_RUNNER, Caller.MAKER}
    
    async def invoke(self, payload, deadline_s, caller):
        script = payload["script"]
        timeout_s = payload.get("timeout_s", 300)
        
        try:
            # Use subprocess.Popen with PowerShell
            proc = subprocess.Popen(
                ["powershell", "-Command", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP  # Windows
            )
            
            # Wrap with asyncio.wait_for for timeout
            loop = asyncio.get_event_loop()
            stdout, stderr = await asyncio.wait_for(
                loop.run_in_executor(None, proc.communicate),
                timeout=timeout_s
            )
            
            return Result(
                ok=proc.returncode == 0,
                data={
                    "stdout": stdout,
                    "stderr": stderr,
                    "exit_code": proc.returncode
                },
                error=None if proc.returncode == 0 else stderr,
                cost_usd=0
            )
        
        except asyncio.TimeoutError:
            # Kill process on timeout
            proc.terminate()
            await asyncio.sleep(5)
            if proc.poll() is None:
                proc.kill()
            
            return Result(
                ok=False,
                data=None,
                error=f"PowerShell timeout after {timeout_s}s",
                cost_usd=0
            )
        
        except Exception as e:
            return Result(
                ok=False,
                data=None,
                error=str(e),
                cost_usd=0
            )

Test:
  1. Simple command: "Write-Output 'hello'"
     → stdout should be "hello"
  
  2. Exit code: "$LASTEXITCODE = 42; exit"
     → exit_code should be 42
  
  3. Timeout: "Start-Sleep -Seconds 100" with timeout_s=2
     → Should return error "Timeout after 2s"

Create commit: git add orchestrator/proxy/adapters/powershell.py && git commit -m "Implement PowerShell adapter for iterative goal execution"
```

### Success Criteria
- ✅ PowerShell scripts execute
- ✅ Stdout/stderr captured correctly
- ✅ Exit codes returned accurately
- ✅ Timeout + process kill works
- ✅ Simple commands tested (Get-Date, Write-Output, etc.)
- ✅ Error handling works
- ✅ Commit pushed

---

## Phase 5: Iterative Goal Executor (Core MVP Logic)

### Objective
Build the iterative loop: decide → execute → analyze → decide next step → repeat until goal achieved.

### File: `orchestrator/maker/iterative_goal.py` (EXPAND)

**Core Algorithm:**

```python
async def execute_goal_iteratively(
    user_intent: str,
    session_id: str,
    sonnet_adapter,
    powershell_adapter,
    haiku_adapter
) -> dict:
    """Execute a goal iteratively until achieved or max iterations reached."""
    
    state = {
        "goal": user_intent,
        "steps": [],
        "max_iterations": 10,
        "current_iteration": 0
    }
    
    while state["current_iteration"] < state["max_iterations"]:
        # PHASE 1: DECIDE NEXT ACTION
        # Ask Sonnet: "Given goal and completed steps, what should PowerShell do next?"
        
        decision_prompt = f"""Goal: {state['goal']}

Completed steps so far:
{format_steps(state['steps'])}

What should PowerShell execute next? Be specific and executable.
Respond with ONLY the PowerShell command/script, no explanation."""
        
        decision = await sonnet_adapter.invoke({
            "prompt": decision_prompt,
            "session_id": session_id,
            "max_tokens": 500
        }, deadline_s=20, caller=Caller.MAKER)
        
        if not decision.ok:
            return {
                "success": False,
                "error": f"Failed to decide next action: {decision.error}",
                "steps": state["steps"]
            }
        
        next_action = decision.data.get("text", "")
        
        # PHASE 2: EXECUTE POWERSHELL
        ps_result = await powershell_adapter.invoke({
            "script": next_action,
            "timeout_s": 300
        }, deadline_s=350, caller=Caller.MAKER)
        
        # PHASE 3: ANALYZE RESULT (Parallel Haiku Workers)
        worker_tasks = []
        analysis_focuses = [
            "Extract key metrics and success indicators",
            "Identify errors, warnings, or anomalies",
            "Assess whether goal is progressing",
            "Note resource usage (CPU, memory, time)",
            "Summarize outcome and next logical step"
        ]
        
        for focus in analysis_focuses:
            prompt = f"""Goal: {state['goal']}

PowerShell output:
{ps_result.data['stdout']}

stderr:
{ps_result.data['stderr']}

Analysis focus: {focus}

Provide a brief, specific insight."""
            
            task = haiku_adapter.invoke({
                "prompt": prompt,
                "session_id": session_id,
                "max_tokens": 200
            }, deadline_s=15, caller=Caller.MAKER)
            
            worker_tasks.append(task)
        
        worker_results = await asyncio.gather(*worker_tasks, return_exceptions=True)
        
        worker_insights = []
        for result in worker_results:
            if isinstance(result, Exception):
                worker_insights.append(f"Error: {result}")
            elif result.ok:
                worker_insights.append(result.data.get("text", ""))
            else:
                worker_insights.append(f"Error: {result.error}")
        
        # PHASE 4: SYNTHESIZE AND DECIDE
        synthesis_prompt = f"""Goal: {state['goal']}

Completed steps:
{format_steps(state['steps'])}

Latest PowerShell execution:
  Command: {next_action}
  Exit code: {ps_result.data['exit_code']}
  Output: {ps_result.data['stdout'][:1000]}

Worker analysis:
{format_insights(worker_insights)}

Questions:
1. Has the goal been achieved?
2. Are there errors or blockers?
3. If goal not achieved, what should we do next iteration?

Respond concisely."""
        
        synthesis = await sonnet_adapter.invoke({
            "prompt": synthesis_prompt,
            "session_id": session_id,
            "max_tokens": 300
        }, deadline_s=20, caller=Caller.MAKER)
        
        if not synthesis.ok:
            return {
                "success": False,
                "error": f"Synthesis failed: {synthesis.error}",
                "steps": state["steps"]
            }
        
        synthesis_text = synthesis.data.get("text", "")
        
        # PHASE 5: STORE STEP
        state["steps"].append({
            "iteration": state["current_iteration"],
            "action": next_action,
            "ps_stdout": ps_result.data["stdout"],
            "ps_stderr": ps_result.data["stderr"],
            "ps_exit_code": ps_result.data["exit_code"],
            "worker_insights": worker_insights,
            "synthesis": synthesis_text,
            "cost_usd": decision.cost_usd + ps_result.cost_usd + sum(r.cost_usd for r in worker_results if hasattr(r, 'cost_usd')) + synthesis.cost_usd
        })
        
        state["current_iteration"] += 1
        
        # PHASE 6: CHECK GOAL ACHIEVEMENT
        if goal_is_achieved(synthesis_text, state["goal"]):
            return {
                "success": True,
                "final_response": synthesis_text,
                "steps": state["steps"],
                "iterations": state["current_iteration"],
                "total_cost_usd": sum(s["cost_usd"] for s in state["steps"])
            }
    
    # Max iterations reached
    return {
        "success": False,
        "error": "Max iterations reached without goal completion",
        "final_response": "Goal not achieved after 10 attempts. Manual intervention required.",
        "steps": state["steps"],
        "total_cost_usd": sum(s["cost_usd"] for s in state["steps"])
    }


def goal_is_achieved(synthesis_text: str, goal: str) -> bool:
    """Simple heuristic: check if synthesis contains success indicators."""
    success_words = ["complete", "achieved", "success", "done", "finished"]
    return any(word in synthesis_text.lower() for word in success_words)


def format_steps(steps: list) -> str:
    """Format step history for prompt context."""
    return "\n".join([
        f"Step {s['iteration']}: {s['action'][:100]}... → {s['ps_exit_code']}"
        for s in steps
    ])


def format_insights(insights: list) -> str:
    """Format worker insights for prompt context."""
    return "\n".join([f"- {i}" for i in insights])
```

### Handover Prompt for Local Agent

```
Goal: Implement iterative goal executor in orchestrator/maker/iterative_goal.py.

Core loop (6 phases per iteration):
1. DECIDE: Ask Sonnet what PowerShell should do next
2. EXECUTE: Run PowerShell script, capture output
3. ANALYZE: Spawn 5 parallel Haiku workers to analyze result
4. SYNTHESIZE: Ask Sonnet to interpret and decide if goal is achieved
5. STORE: Record all data for this iteration
6. CHECK: Has goal been achieved? If yes, exit. If no, loop.

Safety:
- Max 10 iterations per goal
- Timeout: 300s per PowerShell execution
- Escalate if max iterations reached

Implement:
- execute_goal_iteratively() main function
- goal_is_achieved() helper
- format_steps() and format_insights() helpers
- Class IterativeGoalExecutor with async execute() method

Key prompts to craft:
1. Decision prompt: "Goal: X. Completed: [steps]. What next?"
2. Analysis prompts (5x): Focus on different aspects of PS output
3. Synthesis prompt: "Goal: X. Latest output: Y. Insights: Z. Achieved?"

Test:
  goal = "Write 'Hello World' to C:\\test.txt"
  result = await executor.execute_goal(goal, ...)
  → Should:
    - Iteration 1: Decide PS should write file
    - Execute PS: New-Item -Path C:\\test.txt -Value "Hello World"
    - Analyze results (5 workers)
    - Synthesis detects success
    - Return success=True, iterations=1

Create commit: git add orchestrator/maker/iterative_goal.py && git commit -m "Implement iterative goal executor (decide → execute → analyze → repeat)"
```

### Success Criteria
- ✅ Goal loop executes iteratively
- ✅ Sonnet decides next action correctly
- ✅ PowerShell executes and captures output
- ✅ 5 Haiku workers analyze in parallel
- ✅ Sonnet synthesizes and detects goal achievement
- ✅ Loop exits on goal achieved
- ✅ Loop exits on max iterations with graceful error
- ✅ Cost tracking works (sum of all iterations)
- ✅ Simple goal (write file, list directory) completes in 1-2 iterations

---

## Phase 6: E2E MVP Gate (Full Iterative Execution)

### Objective
Verify end-to-end iterative PowerShell execution with goal completion.

### Test Scenarios

#### Scenario 1: Simple File Operation
```
Goal: "Create a file C:\test.txt with content 'Hello World'"

Expected:
  - Iteration 1: PS creates file
  - Synthesis detects success
  - Return: success=True, iterations=1, cost<$0.10
```

#### Scenario 2: Multi-Step Operation
```
Goal: "Get the current date, add 7 days, and save to C:\date.txt"

Expected:
  - Iteration 1: PS gets current date
  - Iteration 2: PS adds 7 days
  - Iteration 3: PS saves to file
  - Synthesis detects success
  - Return: success=True, iterations=3, cost<$0.30
```

#### Scenario 3: Directory Listing & Analysis
```
Goal: "List all .log files in C:\logs, count them, and report total size"

Expected:
  - Iteration 1: PS lists .log files
  - Iteration 2: PS counts files
  - Iteration 3: PS calculates total size
  - Synthesis creates summary
  - Return: success=True, summary includes count and size
```

### Handover Prompt for Local Agent

```
Goal: Run E2E tests for PowerShell Iterative MVP.

Test 1: Simple File Creation
  goal = "Create C:\\mvp-test.txt with text 'MVP works'"
  → Wait for execution
  → Verify: file exists, iterations=1, success=True, cost<$0.15

Test 2: Multi-Step Goal
  goal = "List files in C:\\, count .txt files, report count"
  → Wait for execution
  → Verify: response mentions file count, iterations=2-3, success=True

Test 3: Goal with Timeout Safety
  goal = "Run a PowerShell script that takes 5 seconds"
  → Verify: completes successfully within timeout

Success Criteria for MVP Gate:
  ✅ Simple goal completes in 1 iteration
  ✅ Multi-step goal completes in <5 iterations
  ✅ Results are accurate and goal is achieved
  ✅ Cost per goal <$0.50
  ✅ Latency <30s per iteration
  ✅ Error handling gracefully escalates on max iterations

Report:
  - Test 1 pass/fail
  - Test 2 pass/fail
  - Test 3 pass/fail
  - Total cost for all tests
  - Any errors encountered
```

### Success Criteria for MVP Gate

**All must pass to proceed:**
- ✅ Simple goal (1-step) completes successfully
- ✅ Multi-step goal completes iteratively
- ✅ PowerShell executes reliably
- ✅ Haiku workers analyze correctly
- ✅ Sonnet decides and synthesizes
- ✅ Iterations loop until goal achieved
- ✅ Max iteration limit enforced
- ✅ Cost tracking accurate
- ✅ Latency reasonable (<30s/iteration)
- ✅ No unhandled exceptions

**If all pass:** MVP is complete. Phase 1.5 (RAG) can begin.

**If any fail:** Debug, fix, re-test before proceeding.

---

## Phase 1.5: RAG PowerShell History (Post-MVP)

### Objective
Make PowerShell execution history queryable via RAG.

### Components

#### 1.5.1: Text Chunking
**File:** `orchestrator/maker/chunker.py`

Chunk PowerShell results into overlapping 500-token segments:
- Input: PowerShell stdout (can be 1000+ lines)
- Output: list of ~500-token chunks
- Overlap: 50 tokens (context preservation)

#### 1.5.2: Voyage AI Embedding
**File:** `orchestrator/proxy/adapters/voyage_embed.py`

Embed chunks via Voyage API:
- Input: list of text chunks
- Output: (N, 1024) numpy array of vectors
- Cache: per-session

#### 1.5.3: Vector Store
**File:** `orchestrator/maker/vector_store.py`

In-memory ephemeral storage:
- Store: chunks + embeddings + metadata (when executed, what goal, etc.)
- Retrieve: cosine similarity search
- Cleanup: on session end

#### 1.5.4: Retrieval Pattern
**File:** `orchestrator/maker/retrieval.py`

Query the PowerShell history:
- Embed user query
- Search vector store
- Return top-K relevant chunks

#### 1.5.5: RAG on Results
Wire into PA dispatcher:
- User queries history: "What happened during simulations?"
- Retrieve relevant chunks
- Spawn Haiku workers to analyze chunks
- Sonnet synthesizes history

### Handover Prompt for Phase 1.5

```
Goal: Make PowerShell execution history RAG-able.

Implementation plan (same as before, but now for PowerShell results):
1. Chunker: split PS output into overlapping 500-token segments
2. Embedding: call Voyage API, cache per-session
3. Vector store: in-memory NumPy, cosine similarity search
4. Retrieval: query embedding → top-K chunks
5. RAG: retrieve → analyze (Haiku) → synthesize (Sonnet)

Files:
  - orchestrator/maker/chunker.py
  - orchestrator/proxy/adapters/voyage_embed.py
  - orchestrator/maker/vector_store.py
  - orchestrator/maker/retrieval.py

Integration:
  - On each iterative goal completion, chunk + embed results
  - Store in session vector store
  - User can query: "What simulations ran? Show me the metrics."

Test:
  goal = "Run a command that produces 100+ lines of output"
  → Execute goal
  → Chunk results
  → Query: "What was the output?"
  → Retrieve relevant chunks
  → Synthesize answer
  → Verify accuracy
```

---

## Decision Checklist

Before starting each phase:

| Phase | Pre-Condition | Status |
|-------|--------------|--------|
| 0 | None | Ready ✓ |
| 1 | Phase 0 complete | Blocked until Phase 0 ✓ |
| 2 | Phase 1 complete | Blocked until Phase 1 ✓ |
| 3 | Phase 1-2 complete | Blocked until Phase 1 ✓ |
| 4 | Phase 3 complete | Blocked until Phase 3 ✓ |
| 5 | Phase 4 complete | Blocked until Phase 4 ✓ |
| 6 | Phase 5 complete | **MVP Gate** — Blocked until Phase 5 ✓ |
| 1.5 | Phase 6 complete | Post-MVP, Blocked until Phase 6 ✓ |

---

## Commit Messages

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
Create MAKER module structure for iterative goal execution
```

**Phase 4:**
```
Implement PowerShell adapter for iterative goal execution
```

**Phase 5:**
```
Implement iterative goal executor (decide → execute → analyze → repeat)
```

**Phase 6:**
```
Verify PowerShell iterative MVP end-to-end (MVP Gate complete)
```

**Phase 1.5:**
```
Implement RAG on PowerShell execution history (query what was done)
```

---

## Timeline Estimate

| Phase | Time | Blocker |
|-------|------|---------|
| 0 | 30 min | Phase 0 → Phase 1 |
| 1-3 | 2-3 hrs | Phase 3 → Phase 4 |
| 4 | 2-3 hrs | Phase 4 → Phase 5 |
| 5 | 4-6 hrs | Phase 5 → Phase 6 |
| 6 | 2-3 hrs | **MVP Gate** |
| **Total to MVP: 1-2 days** | | |
| 1.5 | 3-4 hrs | (Post-MVP) |

---

## Architecture Summary

```
User Intent → PA (Haiku routing)
              ↓
           MAKER (orchestrator)
              ↓
        [Iterative Loop: 1-10 iterations]
            ↓
        1. DECIDE (Sonnet): What should PS do?
            ↓
        2. EXECUTE (PowerShell): Run command
            ↓
        3. ANALYZE (5 Haiku workers): What happened?
            ↓
        4. SYNTHESIZE (Sonnet): Goal achieved?
            ↓
        5. LOOP or EXIT
            ↓
        Final Response + Full Execution History
            ↓
        [Phase 1.5: RAG History]
        Users can query: "What did the system do?"
```

---

## Resources

- **Project Vision:** `01.Project_Management/Project_Vision.md`
- **Build Roadmap:** `BUILD_STATUS.md` (root)
- **Adapter Spec:** `01.Project_Management/adapter-spec.md`
- **Architecture Diagram:** `01.Project_Management/arch_diagram.md`

