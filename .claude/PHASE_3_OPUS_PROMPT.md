# Phase 3: Build MAKER Module (Opus Prompt)

## Context: MVP Architecture

PA Orchestrator Phase 1 MVP is **PowerShell Iterative Automation**:
- User gives goal → MAKER orchestrates iteratively until achieved
- Loop: Decide (Sonnet) → Execute (PowerShell) → Analyze (Haiku workers) → Synthesize (Sonnet) → Repeat
- Max 10 iterations per goal; escalate on max reached
- All PowerShell results chunked + embedded (Phase 1.5) for RAG history queries

MAKER is the execution orchestrator layer. It's deterministic (no LLM per execution step, only calls Sonnet for decision/synthesis).

---

## Assignment: Build MAKER Module (orchestrator/maker/)

You are building the core execution engine for PA Orchestrator MVP. This is the most critical Phase 3 deliverable. Get it right; it's the foundation for Phases 4-5.

### Requirements

**Scope:**
- Create `orchestrator/maker/` directory with 5 modules
- Implement iterative goal execution engine
- Handle PowerShell execution, result analysis, synthesis
- Track state across iterations
- Implement safety limits (max iterations, timeouts)
- All async/await patterns; no blocking calls
- Windows-native PowerShell execution

**Files to Create:**
1. `orchestrator/maker/__init__.py` — package entry point
2. `orchestrator/maker/executor.py` — MAKERExecutor class + low-level operations
3. `orchestrator/maker/iterative_goal.py` — IterativeGoalExecutor class + loop logic
4. `orchestrator/maker/state.py` — goal state dataclasses + helpers
5. `orchestrator/maker/safety.py` — timeout, iteration limits, escalation logic

**Non-Goals (Phase 1.5+):**
- Chunking, embedding, vector store, retrieval (Phase 1.5)
- Job persistence, cron scheduling (Phase 2)
- Anything beyond iterative goal execution

---

## Architecture (Detailed)

### High-Level Flow

```
User Goal (string)
    ↓
IterativeGoalExecutor.execute_goal()
    ↓
[Loop: max 10 iterations]
    ↓
ITERATION N:
  1. DECIDE: Sonnet decides next PowerShell action
  2. EXECUTE: MAKERExecutor.run_powershell() captures output
  3. ANALYZE: 5 parallel Haiku worker calls
  4. SYNTHESIZE: Sonnet interprets results + checks goal status
  5. STORE: Append to goal state
  6. CHECK: If goal achieved or max iterations, exit loop
    ↓
Return: {success, final_response, steps[], iterations, cost_usd}
```

### State Management

**GoalState (immutable snapshot per iteration):**
```python
@dataclass
class GoalState:
    goal: str                          # User's original intent
    steps: list[ExecutionStep]         # History of all iterations
    current_iteration: int             # 0-indexed
    max_iterations: int                # Safety limit (10)
    start_time: datetime               # When goal execution started
    total_cost_usd: float              # Accumulated cost

@dataclass
class ExecutionStep:
    iteration: int                     # Which iteration (0-indexed)
    timestamp: datetime                # When step executed
    decision: str                      # What Sonnet decided to do
    ps_script: str                     # PowerShell script executed
    ps_stdout: str                     # Raw output
    ps_stderr: str                     # Raw errors
    ps_exit_code: int                  # Exit code
    ps_duration_ms: float              # Execution time
    worker_analyses: list[str]         # 5 Haiku worker outputs
    synthesis: str                     # Sonnet's interpretation
    goal_achieved: bool                # Did synthesis say goal is done?
    iteration_cost_usd: float          # Cost of this iteration
```

### Component Interfaces

**MAKERExecutor** — Low-level execution:
```python
class MAKERExecutor:
    """Deterministic executor for PowerShell and low-level ops."""
    
    async def run_powershell(
        self,
        script: str,
        timeout_s: int = 300,
        session_id: str = None
    ) -> PowerShellResult:
        """Execute PowerShell script, capture output, enforce timeout.
        
        Returns:
            PowerShellResult with stdout, stderr, exit_code, duration_ms
        
        Raises:
            TimeoutError if execution exceeds timeout_s
            ProcessError if script cannot execute
        """
    
    async def chunk_text(self, text: str, chunk_size: int = 500) -> list[str]:
        """Chunk text into overlapping segments (Phase 1.5)."""
    
    async def embed_text(self, text: str) -> list[float]:
        """Embed text via Voyage API (Phase 1.5)."""
```

**IterativeGoalExecutor** — High-level orchestration:
```python
class IterativeGoalExecutor:
    """Orchestrate iterative goal execution with AI guidance."""
    
    async def execute_goal(
        self,
        user_intent: str,
        session_id: str,
        adapters: dict  # {sonnet_adapter, powershell_adapter, haiku_adapter}
    ) -> GoalExecutionResult:
        """Execute a user goal iteratively until achieved or max iterations.
        
        Args:
            user_intent: User's goal (e.g., "Run daily simulations")
            session_id: Session ID for cost tracking
            adapters: {
                "sonnet": SonnetAdapter,
                "powershell": PowerShellAdapter,
                "haiku": HaikuAdapter
            }
        
        Returns:
            GoalExecutionResult with success, final_response, steps, cost
        
        Raises:
            MaxIterationsError if goal not achieved after 10 iterations
            AdapterError if any adapter fails critically
        """
    
    async def _decide_next_action(
        self,
        goal: str,
        state: GoalState,
        sonnet_adapter
    ) -> str:
        """Ask Sonnet: 'Given goal and prior steps, what should PS do next?'"""
    
    async def _analyze_result(
        self,
        ps_result: PowerShellResult,
        goal: str,
        state: GoalState,
        haiku_adapter
    ) -> list[str]:
        """Spawn 5 parallel Haiku workers to analyze PS output."""
    
    async def _synthesize_and_decide(
        self,
        ps_result: PowerShellResult,
        worker_analyses: list[str],
        goal: str,
        state: GoalState,
        sonnet_adapter
    ) -> tuple[str, bool]:
        """Ask Sonnet: 'Goal achieved? What happens next?'
        
        Returns: (synthesis_text, goal_is_achieved_bool)
        """
```

---

## Implementation Details

### 1. PowerShell Execution (MAKERExecutor.run_powershell)

**Requirements:**
- Use `subprocess.Popen` with PowerShell binary
- Async wrapper via `asyncio.get_event_loop().run_in_executor()`
- Timeout via `asyncio.wait_for()`
- On timeout: `terminate()` → 5s wait → `kill()`
- Capture stdout, stderr, exit code
- Track duration

**Pseudo-code:**
```python
async def run_powershell(self, script: str, timeout_s: int = 300):
    start = time.time()
    
    try:
        proc = subprocess.Popen(
            ["powershell", "-Command", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP  # Windows
        )
        
        loop = asyncio.get_event_loop()
        stdout, stderr = await asyncio.wait_for(
            loop.run_in_executor(None, proc.communicate),
            timeout=timeout_s
        )
        
        duration_ms = (time.time() - start) * 1000
        return PowerShellResult(
            ok=(proc.returncode == 0),
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode,
            duration_ms=duration_ms
        )
    
    except asyncio.TimeoutError:
        proc.terminate()
        await asyncio.sleep(5)
        if proc.poll() is None:
            proc.kill()
        raise TimeoutError(f"PowerShell timeout after {timeout_s}s")
```

### 2. Iterative Loop (IterativeGoalExecutor.execute_goal)

**6-Phase Per-Iteration Algorithm:**

```python
async def execute_goal(self, user_intent, session_id, adapters):
    state = GoalState(
        goal=user_intent,
        steps=[],
        current_iteration=0,
        max_iterations=10,
        start_time=datetime.now(),
        total_cost_usd=0
    )
    
    while state.current_iteration < state.max_iterations:
        # PHASE 1: DECIDE
        decision = await self._decide_next_action(
            user_intent, state, adapters["sonnet"]
        )
        
        # PHASE 2: EXECUTE
        try:
            ps_result = await adapters["powershell"].run_powershell(
                script=decision,
                timeout_s=300,
                session_id=session_id
            )
        except TimeoutError as e:
            # Store failed iteration and continue or escalate
            ...
        
        # PHASE 3: ANALYZE (5 parallel Haiku workers)
        worker_outputs = await self._analyze_result(
            ps_result, user_intent, state, adapters["haiku"]
        )
        
        # PHASE 4: SYNTHESIZE
        synthesis, goal_achieved = await self._synthesize_and_decide(
            ps_result, worker_outputs, user_intent, state, adapters["sonnet"]
        )
        
        # PHASE 5: STORE
        step = ExecutionStep(
            iteration=state.current_iteration,
            timestamp=datetime.now(),
            decision=decision,
            ps_script=decision,
            ps_stdout=ps_result.stdout,
            ps_stderr=ps_result.stderr,
            ps_exit_code=ps_result.exit_code,
            ps_duration_ms=ps_result.duration_ms,
            worker_analyses=worker_outputs,
            synthesis=synthesis,
            goal_achieved=goal_achieved,
            iteration_cost_usd=...  # sum of adapter costs
        )
        state.steps.append(step)
        state.current_iteration += 1
        state.total_cost_usd += step.iteration_cost_usd
        
        # PHASE 6: CHECK
        if goal_achieved:
            return GoalExecutionResult(
                success=True,
                final_response=synthesis,
                steps=state.steps,
                iterations=state.current_iteration,
                cost_usd=state.total_cost_usd
            )
    
    # Max iterations reached
    return GoalExecutionResult(
        success=False,
        final_response="Goal not achieved after 10 iterations. Manual intervention required.",
        steps=state.steps,
        iterations=state.current_iteration,
        cost_usd=state.total_cost_usd,
        error="MaxIterationsReached"
    )
```

### 3. Decision Prompts (Crafted for Sonnet)

**Decision Prompt (Phase 1 of iteration):**
```
Goal: {user_intent}

Completed steps so far:
{format_steps(state.steps)}

Current PowerShell working directory: {cwd}

What should PowerShell execute next to make progress on this goal?
Respond with ONLY the PowerShell command/script. No explanation, no markdown blocks.
Be specific and executable. Assume PowerShell 5+ on Windows 10/11.

Example responses:
  Get-ChildItem C:\logs\ -Filter *.txt
  $files = Get-ChildItem C:\data\; $files | Measure-Object
```

**Analysis Prompt (Phase 3 of iteration, one per Haiku worker):**
```
Goal: {user_intent}

PowerShell output:
{ps_result.stdout}
{ps_result.stderr}
Exit code: {ps_result.exit_code}

Analysis focus: {focus_area}

Provide a brief, specific insight (1-2 sentences).

Focus areas:
  1. "Extract key metrics and success indicators"
  2. "Identify errors, warnings, or anomalies"
  3. "Assess whether goal is progressing"
  4. "Note resource usage and performance"
  5. "Summarize outcome and suggest next step"
```

**Synthesis Prompt (Phase 4 of iteration):**
```
Goal: {user_intent}

Completed steps:
{format_steps(state.steps)}

Latest PowerShell execution:
  Command: {decision}
  Exit code: {ps_exit_code}
  Output: {ps_stdout[:1000]}

Worker insights:
{format_insights(worker_analyses)}

Questions:
1. Has the goal been achieved? (Answer: YES or NO)
2. Are there errors or blockers?
3. If goal not achieved, what should PowerShell do next?

Keep response concise (3-5 sentences).
```

### 4. Goal Achievement Detection

**Simple Heuristic (for now):**
```python
def goal_is_achieved(synthesis_text: str) -> bool:
    success_words = ["complete", "achieved", "success", "done", "finished", "yes"]
    return any(word in synthesis_text.lower() for word in success_words)
```

**Better: Extract explicit YES/NO from Sonnet response** (structured output):
- Sonnet's synthesis starts with "YES" or "NO"
- Parser extracts decision with confidence

### 5. Error Handling & Escalation

**Recoverable Errors:**
- PowerShell timeout → log, continue to next iteration (Sonnet may recover)
- Adapter rate limit → retry with backoff
- Bad PS syntax → Sonnet will learn and try different syntax

**Unrecoverable Errors:**
- Adapter critical failure (e.g., API key invalid) → escalate immediately
- Max iterations reached → return with failure status
- Session cost exceeds budget → hard kill

**Escalation Pattern (to PA):**
```python
# If critical error:
escalation = Escalation(
    session_id=session_id,
    kind="goal_execution_failed",
    options={
        "a": "retry with modified goal",
        "b": "abort goal",
        "c": "escalate to manual"
    },
    context={
        "goal": user_intent,
        "iterations": state.current_iteration,
        "last_error": str(e)
    }
)
```

---

## File Structure & Imports

**orchestrator/maker/__init__.py:**
```python
from .executor import MAKERExecutor
from .iterative_goal import IterativeGoalExecutor
from .state import GoalState, ExecutionStep, GoalExecutionResult, PowerShellResult

__all__ = [
    "MAKERExecutor",
    "IterativeGoalExecutor",
    "GoalState",
    "ExecutionStep",
    "GoalExecutionResult",
    "PowerShellResult",
]
```

**Imports in each file:**
```python
# iterative_goal.py
import asyncio
from datetime import datetime
from typing import Any, Dict, Tuple
from dataclasses import dataclass

from orchestrator.proxy.protocol import Tool, Caller, Result
from orchestrator.models import ...  # as needed
from .executor import MAKERExecutor, PowerShellResult
from .state import GoalState, ExecutionStep, GoalExecutionResult
from .safety import TimeoutError, MaxIterationsError
```

---

## Testing & Verification

**Unit Tests (after implementation):**
```python
# Test 1: Simple PowerShell execution
result = await executor.run_powershell("Write-Output 'test'")
assert result.stdout.strip() == "test"
assert result.exit_code == 0

# Test 2: PowerShell with error
result = await executor.run_powershell("Write-Error 'oops' -ErrorAction Continue; exit 1")
assert result.exit_code == 1
assert "oops" in result.stderr

# Test 3: Timeout
try:
    result = await executor.run_powershell("Start-Sleep 100", timeout_s=2)
    assert False, "Should have timed out"
except TimeoutError:
    pass  # Expected

# Test 4: Simple goal iteration
goal = "Write 'MVP' to C:\\test.txt"
result = await executor.execute_goal(goal, ...)
assert result.success
assert result.iterations == 1
```

**Integration Test (Phase 6):**
- User gives goal
- System executes iteratively
- Verifies file creation / output / exit codes
- Confirms goal_achieved detection works
- Measures cost and latency

---

## Quality Checklist

Before finishing Phase 3:
- ✅ All async/await patterns correct (no blocking calls)
- ✅ PowerShell execution robust (timeout, process kill, output capture)
- ✅ State tracking accurate (all steps stored, cost accumulated)
- ✅ Error handling clear (escalation vs recovery)
- ✅ Prompt engineering sound (Sonnet decisions are reasonable)
- ✅ Goal achievement detection works (simple heuristic is OK for MVP)
- ✅ Max iterations enforced (loop exits after 10)
- ✅ Cost tracking per iteration and total
- ✅ Code is readable, well-commented
- ✅ No hardcoded paths or secrets

---

## Success Criteria

**Phase 3 is complete when:**
1. ✅ MAKER module created with all 5 files
2. ✅ IterativeGoalExecutor executes a simple goal end-to-end
3. ✅ PowerShell execution works (Get-Date, Write-Output, file creation)
4. ✅ Iterative loop executes (2-3 iterations for multi-step goal)
5. ✅ Sonnet makes reasonable decisions on next action
6. ✅ Haiku workers analyze results correctly
7. ✅ Goal achievement detection works (loop exits)
8. ✅ Max iterations enforced (loop exits on iteration 10)
9. ✅ Cost tracking accurate (Haiku ~0.002¢, Sonnet ~0.02¢ per iteration)
10. ✅ App imports and starts cleanly: `from orchestrator.maker import IterativeGoalExecutor`

---

## What Happens Next

After Phase 3 (MAKER complete):
- **Phase 4:** PowerShell adapter implementation (if not exists)
- **Phase 5:** Wire iterative executor into PA dispatcher
- **Phase 6:** E2E testing (user gives goal → system executes → reports)

