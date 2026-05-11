# MAKER — Iterative Goal-Execution Spec

> **Authoritative spec.** This document supersedes `01.Project_Management/Execution_Plan.md`. The earlier RAG-first MAKER design is no longer the target.
>
> **How to use this doc:** Read your step's card in `MAKER_build.md` first; come here for the spec details that card references. The section anchors below (`#state`, `#safety`, …) are the deep-link targets used by the build cards.

---

## Purpose

MAKER is the iterative goal-execution engine that lets the user say *"do X"* and have the orchestrator drive a PowerShell process toward X using a tight LLM-in-the-loop strategy:

```
Decide (Sonnet)   ──▶  Execute (PowerShell)   ──▶  Analyze (Haiku ×5 in parallel)   ──▶  Synthesize (Sonnet)
       ▲                                                                                       │
       └───────────────────────── next iteration, capped at 10 ─────────────────────────────────┘
```

- **Decide** — Sonnet picks the next PowerShell action given the goal and history.
- **Execute** — `MAKERExecutor.run_powershell` runs the action, captures stdout/stderr/exit code.
- **Analyze** — Five parallel Haiku calls each independently judge whether the goal looks satisfied based on the new state.
- **Synthesize** — One Sonnet call merges the five analyses into a verdict and a one-line status. If the synthesized verdict matches the goal-achieved predicate, the loop exits. Otherwise, decide the next step.

The loop is hard-capped at 10 iterations. On cap, the executor returns the latest state with `MAKERMaxIterationsError` mapped to `Result.error`.

---

## File Layout

| File | Role |
|---|---|
| `orchestrator/maker/__init__.py` | Package marker; re-exports `MAKERExecutor`, `IterativeGoalExecutor`, `GoalState`. |
| `orchestrator/maker/state.py` | `IterationState`, `GoalState` dataclasses. See [#state](#state). |
| `orchestrator/maker/safety.py` | Exception hierarchy. See [#safety](#safety). |
| `orchestrator/maker/executor.py` | `MAKERExecutor.run_powershell` — subprocess + asyncio + Windows kill chain. See [#powershell-execution](#powershell-execution). |
| `orchestrator/maker/prompts.py` | DECIDE / ANALYZE / SYNTHESIZE templates + `format_steps` + `goal_achieved`. See [#prompts](#prompts). |
| `orchestrator/maker/iterative_goal.py` | `IterativeGoalExecutor` — the 6-phase loop. See [#iterative-loop](#iterative-loop). |
| `orchestrator/proxy/adapters/powershell.py` | `PowerShellAdapter` — Tool wrapper over `MAKERExecutor.run_powershell`. See [#powershell-adapter](#powershell-adapter). |

Modifications outside `orchestrator/maker/`:

| File | Change |
|---|---|
| `orchestrator/models.py` | Add `"goal"` to the `Intent.kind` `Literal`. |
| `orchestrator/parser.py` | Add `@goal` first-token branch (kind `"goal"`). |
| `orchestrator/proxy/dispatcher.py` | Route `Intent.kind == "goal"` to `IterativeGoalExecutor`. |
| `orchestrator/main.py` | Construct `IterativeGoalExecutor` in the lifespan, passing the existing `ClaudeAPIAdapter` and the new `PowerShellAdapter`. |

---

## <a id="state"></a>State (`state.py`)

Two dataclasses. Both `@dataclass(frozen=False, slots=True)`. No methods, no validation logic — pure containers.

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass(slots=True)
class IterationState:
    """One pass through the 4-phase loop."""
    iteration: int                       # 1-indexed
    decided_action: str                  # PowerShell script chosen by Decide
    stdout: str
    stderr: str
    exit_code: int
    analyses: list[str] = field(default_factory=list)   # 5 Haiku verdicts, in invocation order
    synthesis: str = ""                  # Sonnet's merged verdict
    cost_usd: float = 0.0                # Sum of all LLM + executor costs for this iteration
    latency_ms: int = 0                  # Wall-clock from Decide start to Synthesize end

@dataclass(slots=True)
class GoalState:
    """Whole-goal accumulator."""
    goal: str
    session_id: str
    iterations: list[IterationState] = field(default_factory=list)
    achieved: bool = False
    final_summary: str = ""
    cost_usd: float = 0.0                # Sum of all IterationState.cost_usd
    latency_ms: int = 0                  # Sum of all IterationState.latency_ms
```

`GoalState.cost_usd` is the canonical figure surfaced in the outer `Result.cost_usd`. Do not maintain a parallel ledger.

---

## <a id="safety"></a>Safety (`safety.py`)

Three exception classes. All inherit from a common base.

```python
class MAKERError(Exception):
    """Base for all MAKER-raised errors."""

class MAKERSafetyError(MAKERError):
    """Refusal to execute a PowerShell action that fails policy checks."""

class MAKERTimeoutError(MAKERError):
    """PowerShell process exceeded its deadline; kill chain applied."""

class MAKERMaxIterationsError(MAKERError):
    """Loop hit the iteration cap without achieving the goal."""
```

The dispatcher mapping (used by M9):

| Exception | `ErrorCode` | `retriable` |
|---|---|---|
| `MAKERSafetyError` | `UNAUTHORIZED` | False |
| `MAKERTimeoutError` | `TIMEOUT` | True |
| `MAKERMaxIterationsError` | `QUOTA` | False |
| Any other `MAKERError` | `INTERNAL` | False |

---

## <a id="powershell-execution"></a>PowerShell Execution (`executor.py`)

`MAKERExecutor` is a thin async class with one production method.

```python
import asyncio
import subprocess

class MAKERExecutor:
    """Deterministic PowerShell runner. No LLM calls. Windows-only."""

    async def run_powershell(
        self,
        script: str,
        timeout_s: float,
    ) -> tuple[str, str, int]:
        """Run `script` in PowerShell. Returns (stdout, stderr, exit_code).

        Constraints:
        - creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
        - On timeout: proc.terminate() → wait 5s → proc.kill(), then raise MAKERTimeoutError.
        - Stdout/stderr captured in full; no truncation.
        - shell=False; pass script via `-Command` argument: ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
        """
```

Implementation sketch (M4 builds this):

```python
async def run_powershell(self, script: str, timeout_s: float) -> tuple[str, str, int]:
    proc = await asyncio.create_subprocess_exec(
        "powershell", "-NoProfile", "-NonInteractive", "-Command", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        raise MAKERTimeoutError(f"powershell exceeded {timeout_s}s")
    return stdout_b.decode("utf-8", errors="replace"), stderr_b.decode("utf-8", errors="replace"), proc.returncode
```

No retries inside the executor. Retries are an iteration-level concern handled by the outer loop.

---

## <a id="powershell-adapter"></a>PowerShell Adapter (`powershell.py`)

Thin Tool-protocol wrapper. Imitates the manifest pattern at `claude_api.py:102-118`.

```python
class PowerShellAdapter:
    name: str = "powershell"
    allowed_callers: set[Caller] = {Caller.PA, Caller.JOB_RUNNER}

    def __init__(self, executor: MAKERExecutor | None = None):
        self._executor = executor or MAKERExecutor()

    @property
    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            required=[
                AdapterParam(name="script", type="str", description="PowerShell script body"),
            ],
            optional=[
                AdapterParam(name="timeout_s", type="float", description="Hard timeout; default 60"),
                AdapterParam(name="session_id", type="str", description="for cost_ledger attribution"),
            ],
        )

    async def health(self) -> bool:
        # PowerShell available on Windows by default; return True.
        return True

    async def invoke(self, payload: dict, deadline_s: float, caller: Caller) -> Result:
        script = payload["script"]
        timeout_s = float(payload.get("timeout_s", min(60.0, deadline_s)))
        try:
            stdout, stderr, exit_code = await self._executor.run_powershell(script, timeout_s)
        except MAKERTimeoutError as e:
            return Result(
                ok=False,
                error=ErrorDetail(code=ErrorCode.TIMEOUT, message=str(e), retriable=True),
                cost_usd=0.0,
                meta={"tool": "powershell", "latency_ms": int(timeout_s * 1000), "tokens_in": 0, "tokens_out": 0},
            )
        return Result(
            ok=(exit_code == 0),
            data={"stdout": stdout, "stderr": stderr, "exit_code": exit_code},
            error=None if exit_code == 0 else ErrorDetail(
                code=ErrorCode.TOOL_ERROR, message=f"exit_code={exit_code}", retriable=False
            ),
            cost_usd=0.0,
            meta={"tool": "powershell", "latency_ms": 0, "tokens_in": 0, "tokens_out": 0},
        )
```

`cost_usd=0.0` — PowerShell execution has no API cost. The cost surfaced by MAKER is purely the LLM cost of Decide/Analyze/Synthesize.

---

## <a id="prompts"></a>Prompts (`prompts.py`)

Three prompt template constants plus two pure helpers. No I/O.

### `format_steps(state: GoalState) -> str`

Render the iteration history for the Decide / Analyze prompts. Output format:

```
[Iter 1] Action: <decided_action>
[Iter 1] Stdout: <first 800 chars of stdout, "..." truncated>
[Iter 1] Stderr: <first 400 chars of stderr, "..." truncated>
[Iter 1] Exit:   <exit_code>
[Iter 1] Synth:  <synthesis>
[Iter 2] Action: ...
```

If `state.iterations` is empty, return `"(no iterations yet)"`.

### `goal_achieved(synth_text: str) -> bool`

Parse the synthesized verdict. The Synthesize prompt instructs Sonnet to end its response with one of:

- `GOAL_ACHIEVED` — return `True`.
- `GOAL_NOT_ACHIEVED` — return `False`.

The parser is a case-insensitive substring check on the last 200 characters of `synth_text`. `GOAL_ACHIEVED` wins if both appear.

### Prompt templates

```python
DECIDE = """You drive a PowerShell session to achieve a goal.

Goal: {goal}

History so far:
{history}

Decide the next PowerShell command. Return ONLY the PowerShell script body — no markdown fences, no commentary. Keep it to one runnable command or short pipeline.
"""

ANALYZE = """You are one of five independent analysts. Given the goal and the latest iteration, judge whether the goal is now satisfied.

Goal: {goal}

Latest iteration:
Action: {action}
Stdout: {stdout}
Stderr: {stderr}
Exit:   {exit_code}

Respond in 1-3 sentences. End with exactly one of: ACHIEVED or NOT_ACHIEVED.
"""

SYNTHESIZE = """You are the synthesizer. Five analysts gave verdicts on whether a goal is satisfied. Merge them.

Goal: {goal}

Latest action: {action}
Latest stdout: {stdout}
Latest stderr: {stderr}
Latest exit code: {exit_code}

Analyst verdicts:
{verdicts}

Write a one-paragraph summary of where we stand. End with EXACTLY one of these tokens on its own line:
- GOAL_ACHIEVED
- GOAL_NOT_ACHIEVED
"""
```

---

## <a id="iterative-loop"></a>Iterative Loop (`iterative_goal.py`)

Single class with a single public method.

```python
class IterativeGoalExecutor:
    def __init__(
        self,
        claude_adapter,        # ClaudeAPIAdapter instance
        ps_adapter,            # PowerShellAdapter instance
        max_iter: int = 10,
        analyzer_count: int = 5,
    ):
        self._claude = claude_adapter
        self._ps = ps_adapter
        self._max_iter = max_iter
        self._analyzer_count = analyzer_count

    async def run(self, goal: str, session_id: str) -> Result:
        ...
```

### Six phases per iteration

For each iteration `i` in `1..max_iter`:

1. **Decide** — `claude_adapter.invoke({"operation": "complete", "prompt": DECIDE.format(...), "model": "claude-sonnet-4-6", "session_id": session_id}, deadline_s=30, caller=Caller.PA)`. Extract the script from `Result.data`. If `Result.ok is False`, abort with that error.
2. **Execute** — `ps_adapter.invoke({"script": script, "timeout_s": 60.0, "session_id": session_id}, deadline_s=60, caller=Caller.PA)`. Record `stdout`, `stderr`, `exit_code`. If timeout (`Result.error.code == TIMEOUT`), record the iteration and continue (the loop may recover); if it fails three times in a row, raise `MAKERMaxIterationsError` early.
3. **Analyze (parallel)** — Build `analyzer_count` Haiku invocations of `ANALYZE`. Run via `asyncio.gather(*tasks, return_exceptions=True)`. Failed analyses contribute the literal verdict `"ANALYZER_FAILED"`. Always proceed to synthesize, even if some analyses failed — the synthesizer is robust to that.
4. **Synthesize** — One Sonnet call against `SYNTHESIZE`. If it fails, abort with that error.
5. **Record** — Build an `IterationState`, append to `goal_state.iterations`, sum `cost_usd` and `latency_ms` into `goal_state`.
6. **Check** — `if goal_achieved(synthesis): goal_state.achieved = True; goal_state.final_summary = synthesis; break`.

After the loop:
- If `goal_state.achieved`: return `Result(ok=True, data={"goal_state": goal_state}, cost_usd=goal_state.cost_usd, meta={"tool": "maker", "iterations": len(goal_state.iterations), "latency_ms": goal_state.latency_ms, "tokens_in": 0, "tokens_out": 0})`.
- Else: raise `MAKERMaxIterationsError`; the caller (dispatcher) maps it per [#safety](#safety).

### Cost summing rules

- For each adapter `invoke`, accumulate `Result.cost_usd` directly into `IterationState.cost_usd`.
- After each iteration, `goal_state.cost_usd += iteration_state.cost_usd`.
- Do **not** call `claude_api.py`'s internal `_record_cost` or write to `cost_ledger` from MAKER — `claude_api.py` already writes ledger rows on every invoke. MAKER's `cost_usd` is a read-only sum.

### Parallel analyzer failure semantics

`asyncio.gather(*tasks, return_exceptions=True)` returns either a `Result` or an `Exception` for each task. Treat any non-`Result` or any `Result.ok is False` as `"ANALYZER_FAILED"`. Do not retry analyzers within the same iteration — failure budget is the iteration cap itself.

---

## <a id="dispatcher-wiring"></a>Dispatcher Wiring (M9)

Three edits. Each is small.

### 1. `orchestrator/models.py:59-62`

Add `"goal"` to the `Intent.kind` `Literal`:

```python
class Intent(BaseModel):
    kind: Literal[
        "reason", "code", "search", "file_read", "file_write",
        "external_api", "desktop", "plan_step", "goal",
    ]
```

### 2. `orchestrator/parser.py`

Add a new `elif` branch parallel to `@PA`:

```python
elif first == "@goal":
    kind = "goal"
    payload = {"text": remainder}
```

### 3. `orchestrator/proxy/dispatcher.py`

Add a route at the top of the dispatch function (before the existing adapter-routing table):

```python
if intent.kind == "goal":
    try:
        return await iterative_goal_executor.run(intent.payload["text"], intent.session_id)
    except MAKERMaxIterationsError as e:
        return Result(ok=False, error=ErrorDetail(code=ErrorCode.QUOTA, message=str(e), retriable=False), cost_usd=0.0, meta={"tool": "maker"})
    except MAKERSafetyError as e:
        return Result(ok=False, error=ErrorDetail(code=ErrorCode.UNAUTHORIZED, message=str(e), retriable=False), cost_usd=0.0, meta={"tool": "maker"})
    except MAKERError as e:
        return Result(ok=False, error=ErrorDetail(code=ErrorCode.INTERNAL, message=str(e), retriable=False), cost_usd=0.0, meta={"tool": "maker"})
```

### 4. `orchestrator/main.py` (lifespan)

After constructing `ClaudeAPIAdapter` and `PowerShellAdapter`, construct `IterativeGoalExecutor` and pass it to the dispatcher constructor (the dispatcher needs a reference to invoke it on goal intents). The exact wiring follows the existing pattern in `main.py` where adapters are registered.

---

## Out of Scope (Not in M0–M10)

- Resumable iterations (no checkpointing — a crash mid-loop loses progress).
- Multi-goal scheduling (one goal at a time per session).
- Decide-side memory of prior goals (history is per-`run` call).
- Telegram-specific surface (existing Telegram path already routes through the dispatcher, so `@goal` works there for free once M9 is in).
- Tuning the analyzer count or iteration cap (constants, not config).
