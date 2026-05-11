# MAKER_build — Per-Step Build Cards

> One card per row in `BUILD_STATUS.md` (same directory) § Phase 2 — MAKER. Read **only the card for your step**. The card has everything you need; consult `MAKER_spec.md` (same directory) only for the spec details the card explicitly references.
>
> Gate-table conventions (same as `.claude/commands/build-step.md` Phase 3):
> - Commands run from `C:\Users\Mini_PC\pa-orchestrator\` unless stated.
> - Every command is a copy-pasteable PowerShell one-liner.
> - Expected output is the exact string, key phrase, or pattern. Long output → critical line only (e.g. `4 passed, 0 failed`).
> - Silent successes → `(no output — silent = pass)`.
> - File-creating steps → next row verifies with `Test-Path <path>` → `True`.

---

### Step M0 — Pre-flight verification
**Model:** Haiku
**Depends on:** — (manual CTO removal prerequisite must be complete; see `AGENT_ONBOARDING.md` (same directory) § Prerequisite)
**Files created/modified:** none (investigation only)
**Interface contract:** none — produce a verification report.
**Spec reference:** none (prereq is documented in `AGENT_ONBOARDING.md`)

Gate (must pass before marking done):

| # | Run in PowerShell | Expected output |
|---|-------------------|-----------------|
| 1 | `Test-Path orchestrator\spawner.py` | `False` |
| 2 | `Test-Path orchestrator\proxy\adapters\claude_code.py` | `False` |
| 3 | `Test-Path tests\test_spawner.py` | `False` |
| 4 | `Select-String -Path orchestrator\models.py -Pattern "CTO"` | `(no output — silent = pass)` |
| 5 | `Select-String -Path orchestrator\parser.py -Pattern "@CTO"` | `(no output — silent = pass)` |
| 6 | `Select-String -Path orchestrator\fsm.py -Pattern "Mode.CTO\|CTO_SUBAGENT"` | `(no output — silent = pass)` |
| 7 | `python -c "from orchestrator.main import app; print('OK')"` | `OK` |

**Done when:** All seven rows pass. Report any failures as a blocker; do not proceed to M1.

---

### Step M1 — `maker/` package skeleton
**Model:** Haiku
**Depends on:** M0
**Files created/modified:**
- `orchestrator/maker/__init__.py` (new — empty, package marker only)
- `orchestrator/maker/executor.py` (new — single line: `"""MAKERExecutor lives here."""`)
- `orchestrator/maker/iterative_goal.py` (new — single line: `"""IterativeGoalExecutor lives here."""`)
- `orchestrator/maker/state.py` (new — single line: `"""State dataclasses live here."""`)
- `orchestrator/maker/safety.py` (new — single line: `"""Exception hierarchy lives here."""`)
- `orchestrator/maker/prompts.py` (new — single line: `"""Prompt templates and helpers live here."""`)

**Interface contract:** none — files are placeholders so M2/M3/M4/M6 can `git diff`-cleanly add content.
**Spec reference:** `MAKER_spec.md` § File Layout

Gate:

| # | Run in PowerShell | Expected output |
|---|-------------------|-----------------|
| 1 | `Test-Path orchestrator\maker\__init__.py` | `True` |
| 2 | `Test-Path orchestrator\maker\executor.py` | `True` |
| 3 | `Test-Path orchestrator\maker\iterative_goal.py` | `True` |
| 4 | `Test-Path orchestrator\maker\state.py` | `True` |
| 5 | `Test-Path orchestrator\maker\safety.py` | `True` |
| 6 | `Test-Path orchestrator\maker\prompts.py` | `True` |
| 7 | `python -c "import orchestrator.maker; print('OK')"` | `OK` |

**Done when:** All six files exist and `import orchestrator.maker` succeeds.

---

### Step M2 — State dataclasses
**Model:** Haiku
**Depends on:** M1
**Files created/modified:** `orchestrator/maker/state.py`
**Interface contract:**

```python
@dataclass(slots=True)
class IterationState:
    iteration: int
    decided_action: str
    stdout: str
    stderr: str
    exit_code: int
    analyses: list[str] = field(default_factory=list)
    synthesis: str = ""
    cost_usd: float = 0.0
    latency_ms: int = 0

@dataclass(slots=True)
class GoalState:
    goal: str
    session_id: str
    iterations: list[IterationState] = field(default_factory=list)
    achieved: bool = False
    final_summary: str = ""
    cost_usd: float = 0.0
    latency_ms: int = 0
```

No methods, no validation. Pure containers.

**Spec reference:** `MAKER_spec.md#state`

Gate:

| # | Run in PowerShell | Expected output |
|---|-------------------|-----------------|
| 1 | `python -c "from orchestrator.maker.state import IterationState, GoalState; s = GoalState(goal='g', session_id='s'); print(s.cost_usd)"` | `0.0` |
| 2 | `python -c "from orchestrator.maker.state import IterationState; i = IterationState(iteration=1, decided_action='a', stdout='', stderr='', exit_code=0); print(i.analyses)"` | `[]` |

**Done when:** Both rows pass. Field defaults match the spec.

---

### Step M3 — Safety exceptions
**Model:** Haiku
**Depends on:** M1
**Files created/modified:** `orchestrator/maker/safety.py`
**Interface contract:**

```python
class MAKERError(Exception):
    ...

class MAKERSafetyError(MAKERError):
    ...

class MAKERTimeoutError(MAKERError):
    ...

class MAKERMaxIterationsError(MAKERError):
    ...
```

No constructor logic. No custom `__str__`. Pure subclasses.

**Spec reference:** `MAKER_spec.md#safety`

Gate:

| # | Run in PowerShell | Expected output |
|---|-------------------|-----------------|
| 1 | `python -c "from orchestrator.maker.safety import MAKERError, MAKERSafetyError, MAKERTimeoutError, MAKERMaxIterationsError; print(issubclass(MAKERTimeoutError, MAKERError))"` | `True` |
| 2 | `python -c "from orchestrator.maker.safety import MAKERMaxIterationsError; raise MAKERMaxIterationsError('cap')" 2>&1 \| Select-String "MAKERMaxIterationsError: cap"` | matches |

**Done when:** Both rows pass.

---

### Step M4 — `MAKERExecutor.run_powershell`
**Model:** Sonnet
**Depends on:** M2, M3
**Files created/modified:** `orchestrator/maker/executor.py`
**Interface contract:**

```python
class MAKERExecutor:
    async def run_powershell(
        self,
        script: str,
        timeout_s: float,
    ) -> tuple[str, str, int]:
        """Returns (stdout, stderr, exit_code).
        On timeout: terminate() → wait 5s → kill(), raise MAKERTimeoutError.
        """
```

**Constraints (from CLAUDE.md, locked):**
- `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP`
- `subprocess.terminate()` then 5-second wait then `subprocess.kill()` (no POSIX signals)
- `shell=False`; pass via `["powershell", "-NoProfile", "-NonInteractive", "-Command", script]`
- Decode stdout/stderr with `errors="replace"`

**Spec reference:** `MAKER_spec.md#powershell-execution`

Gate:

| # | Run in PowerShell | Expected output |
|---|-------------------|-----------------|
| 1 | `python -c "import asyncio; from orchestrator.maker.executor import MAKERExecutor; e=MAKERExecutor(); o,err,ec=asyncio.run(e.run_powershell('Write-Output hello', 10.0)); print(o.strip(), ec)"` | `hello 0` |
| 2 | `python -c "import asyncio; from orchestrator.maker.executor import MAKERExecutor; e=MAKERExecutor(); o,err,ec=asyncio.run(e.run_powershell('exit 7', 10.0)); print(ec)"` | `7` |
| 3 | `python -c "import asyncio; from orchestrator.maker.safety import MAKERTimeoutError; from orchestrator.maker.executor import MAKERExecutor; e=MAKERExecutor()`<br/>`try: asyncio.run(e.run_powershell('Start-Sleep 30', 1.0))`<br/>`except MAKERTimeoutError: print('TIMED_OUT')"` | `TIMED_OUT` |

**Done when:** All three rows pass; timeout case kills the child process (no orphans — verify with `Get-Process powershell` afterward returning no stuck child).

---

### Step M5 — `PowerShellAdapter`
**Model:** Sonnet
**Depends on:** M4
**Files created/modified:**
- `orchestrator/proxy/adapters/powershell.py` (new)
- `orchestrator/main.py` (register adapter in lifespan)

**Interface contract:** Imitate the manifest pattern at `claude_api.py:102-118`. Use the existing Tool protocol shape.

```python
class PowerShellAdapter:
    name: str = "powershell"
    allowed_callers: set[Caller] = {Caller.PA, Caller.JOB_RUNNER}

    def __init__(self, executor: MAKERExecutor | None = None): ...

    @property
    def manifest(self) -> AdapterManifest: ...
    async def health(self) -> bool: ...
    async def invoke(self, payload: dict, deadline_s: float, caller: Caller) -> Result: ...
```

Payload: `{"script": str, "timeout_s": float (optional, default min(60, deadline_s)), "session_id": str (optional)}`.
Result on success: `Result(ok=True, data={"stdout": str, "stderr": str, "exit_code": int}, cost_usd=0.0, meta={"tool": "powershell", "latency_ms": int, "tokens_in": 0, "tokens_out": 0})`.
Result on timeout: `Result(ok=False, error=ErrorDetail(code=TIMEOUT, retriable=True, message=...), ...)`.
Result on non-zero exit: `Result(ok=False, error=ErrorDetail(code=TOOL_ERROR, retriable=False, message=f"exit_code={code}"), ...)`.

Wire in `orchestrator/main.py` lifespan: construct `PowerShellAdapter()` and pass to the dispatcher (same pattern as `ClaudeAPIAdapter`).

**Spec reference:** `MAKER_spec.md#powershell-adapter`

Gate:

| # | Run in PowerShell | Expected output |
|---|-------------------|-----------------|
| 1 | `python -c "from orchestrator.proxy.adapters.powershell import PowerShellAdapter; a=PowerShellAdapter(); print(a.name, a.allowed_callers)"` | `powershell {<Caller.PA: 'pa'>, <Caller.JOB_RUNNER: 'job_runner'>}` (order may vary) |
| 2 | `python -c "import asyncio; from orchestrator.models import Caller; from orchestrator.proxy.adapters.powershell import PowerShellAdapter; r=asyncio.run(PowerShellAdapter().invoke({'script':'Write-Output ok'}, 10.0, Caller.PA)); print(r.ok, r.data['stdout'].strip())"` | `True ok` |
| 3 | `python -c "import asyncio; from orchestrator.models import Caller; from orchestrator.proxy.adapters.powershell import PowerShellAdapter; r=asyncio.run(PowerShellAdapter().invoke({'script':'exit 3'}, 10.0, Caller.PA)); print(r.ok, r.error.code)"` | `False ErrorCode.TOOL_ERROR` |
| 4 | `python -m uvicorn orchestrator.main:app --port 8123 &`<br/>`Start-Sleep 3; Stop-Process -Name uvicorn -Force; echo done` | `done` (no exceptions in stderr) |

**Done when:** All four rows pass and the app boots with the adapter registered.

---

### Step M6 — Prompt templates + helpers
**Model:** Sonnet
**Depends on:** M2
**Files created/modified:** `orchestrator/maker/prompts.py`
**Interface contract:**

```python
DECIDE: str       # see MAKER_spec.md#prompts
ANALYZE: str
SYNTHESIZE: str

def format_steps(state: GoalState) -> str: ...
def goal_achieved(synth_text: str) -> bool: ...
```

- `format_steps` returns `"(no iterations yet)"` when `state.iterations == []`; otherwise renders each iteration on five lines (`Action`, `Stdout`, `Stderr`, `Exit`, `Synth`) with stdout truncated to 800 chars and stderr to 400 chars (append `"..."` when truncating).
- `goal_achieved` does a case-insensitive substring check on the last 200 characters of `synth_text`. `GOAL_ACHIEVED` wins if both tokens appear.

**Spec reference:** `MAKER_spec.md#prompts`

Gate:

| # | Run in PowerShell | Expected output |
|---|-------------------|-----------------|
| 1 | `python -c "from orchestrator.maker.prompts import DECIDE, ANALYZE, SYNTHESIZE; print('{goal}' in DECIDE, '{stdout}' in ANALYZE, 'GOAL_ACHIEVED' in SYNTHESIZE)"` | `True True True` |
| 2 | `python -c "from orchestrator.maker.prompts import format_steps; from orchestrator.maker.state import GoalState; print(format_steps(GoalState(goal='g',session_id='s')))"` | `(no iterations yet)` |
| 3 | `python -c "from orchestrator.maker.prompts import goal_achieved; print(goal_achieved('blah blah\nGOAL_ACHIEVED'), goal_achieved('blah\nGOAL_NOT_ACHIEVED'))"` | `True False` |

**Done when:** All three rows pass.

---

### Step M7 — `IterativeGoalExecutor`
**Model:** Opus
**Depends on:** M4, M5, M6
**Files created/modified:** `orchestrator/maker/iterative_goal.py`
**Interface contract:**

```python
class IterativeGoalExecutor:
    def __init__(self, claude_adapter, ps_adapter, max_iter: int = 10, analyzer_count: int = 5): ...
    async def run(self, goal: str, session_id: str) -> Result: ...
```

The 6-phase loop body is specified in `MAKER_spec.md#iterative-loop`. Build it exactly as described:

1. Decide → `claude_adapter.invoke` with `model="claude-sonnet-4-6"`.
2. Execute → `ps_adapter.invoke({"script": ..., "timeout_s": 60.0, "session_id": ...})`.
3. Analyze → `asyncio.gather(*[claude_adapter.invoke(model="claude-haiku-4-5-20251001") for _ in range(analyzer_count)], return_exceptions=True)`.
4. Synthesize → `claude_adapter.invoke` with `model="claude-sonnet-4-6"`.
5. Record `IterationState`; accumulate `goal_state.cost_usd` and `goal_state.latency_ms`.
6. Check `goal_achieved(synthesis)`; if True, break.

After the loop: success → `Result(ok=True, data={"goal_state": goal_state}, cost_usd=goal_state.cost_usd, meta={"tool": "maker", "iterations": N, "latency_ms": M, "tokens_in": 0, "tokens_out": 0})`. Cap-hit → raise `MAKERMaxIterationsError`.

**Constraints:**
- Use `asyncio.gather(..., return_exceptions=True)`. Failed analyses contribute the literal `"ANALYZER_FAILED"`.
- Do **not** call `claude_api.py`'s `_record_cost` — the adapter already writes `cost_ledger` rows. MAKER only sums `Result.cost_usd`.
- If Decide or Synthesize fails, abort with that error (do not retry).
- If Execute returns a `TIMEOUT` `Result`, record the iteration and continue. If three consecutive Execute timeouts occur, raise `MAKERMaxIterationsError` early.

**Spec reference:** `MAKER_spec.md#iterative-loop`

Gate:

| # | Run in PowerShell | Expected output |
|---|-------------------|-----------------|
| 1 | `python -c "from orchestrator.maker.iterative_goal import IterativeGoalExecutor; import inspect; print(inspect.iscoroutinefunction(IterativeGoalExecutor.run))"` | `True` |
| 2 | `pytest tests/test_maker_iterative_goal.py -q` (test file from M8 must already exist and cover: happy-path single iteration, cap hit, analyzer failure) | `3 passed` |
| 3 | `python -c "from orchestrator.maker.iterative_goal import IterativeGoalExecutor; e=IterativeGoalExecutor(None,None); print(e._max_iter, e._analyzer_count)"` | `10 5` |

**Done when:** All three rows pass.

---

### Step M8 — Unit tests
**Model:** Sonnet
**Depends on:** M4, M6
**Files created/modified:**
- `tests/test_maker_executor.py` (new)
- `tests/test_maker_state.py` (new)
- `tests/test_maker_prompts.py` (new)

Each test file follows the style of `tests/test_claude_api.py`. Use `pytest-asyncio` (already in `requirements.txt`) for async tests.

Coverage requirements:
- `test_maker_state.py` — default values, slots prevent unknown attributes, list defaults are independent across instances.
- `test_maker_safety.py` is NOT required — the assertions in M3's gate suffice.
- `test_maker_executor.py` — happy path (stdout/stderr/exit), non-zero exit, timeout raises `MAKERTimeoutError`, terminate-then-kill path (mock the subprocess).
- `test_maker_prompts.py` — `format_steps` truncation, `goal_achieved` true / false / both-tokens (achieved wins) / neither (false).

**Spec reference:** `MAKER_spec.md#state`, `#prompts`, `#powershell-execution`

Gate:

| # | Run in PowerShell | Expected output |
|---|-------------------|-----------------|
| 1 | `pytest tests/test_maker_state.py tests/test_maker_executor.py tests/test_maker_prompts.py -q` | `N passed` (no failures, no errors) |
| 2 | `Get-ChildItem tests\test_maker_*.py \| Measure-Object \| Select-Object -ExpandProperty Count` | `3` |

**Done when:** Both rows pass.

---

### Step M9 — Dispatcher wiring + PA surface
**Model:** Sonnet
**Depends on:** M5, M7
**Files created/modified:**
- `orchestrator/models.py` — add `"goal"` to `Intent.kind` `Literal` (`models.py:59-62`)
- `orchestrator/parser.py` — add `@goal` branch
- `orchestrator/proxy/dispatcher.py` — route `kind == "goal"` to `IterativeGoalExecutor`, map MAKER exceptions per `MAKER_spec.md#safety`
- `orchestrator/main.py` — construct `IterativeGoalExecutor` in lifespan, pass to dispatcher

**Interface contract:**
- `Intent.kind` now accepts `"goal"`.
- `parser.parse("@goal install git", ...)` returns `Intent(kind="goal", payload={"text": "install git"}, ...)`.
- Dispatcher: `Intent(kind="goal")` → `IterativeGoalExecutor.run(intent.payload["text"], intent.session_id)`; exceptions mapped per spec.
- `main.py`: a single `IterativeGoalExecutor(claude_adapter, ps_adapter)` instance lives for the app lifetime and is registered on the dispatcher.

**Spec reference:** `MAKER_spec.md#dispatcher-wiring`

Gate:

| # | Run in PowerShell | Expected output |
|---|-------------------|-----------------|
| 1 | `python -c "from orchestrator.models import Intent; Intent(kind='goal', payload={'text':'x'}, session_id='abcdefgh', mode='PA', caller='pa', deadline_s=30.0); print('OK')"` | `OK` |
| 2 | `python -c "from orchestrator.parser import parse; from orchestrator.models import Mode, Caller; i=parse('@goal install git','abcdefgh',Mode.PA,Caller.PA); print(i.kind, i.payload['text'])"` | `goal install git` |
| 3 | `pytest tests/test_parser.py -q` (extend test file to cover `@goal`) | `N passed` |
| 4 | `python -m uvicorn orchestrator.main:app --port 8124 &`<br/>`Start-Sleep 3; Stop-Process -Name uvicorn -Force; echo done` | `done` (no exceptions) |

**Done when:** All four rows pass.

---

### Step M10 — E2E gate
**Model:** Opus
**Depends on:** M9
**Files created/modified:** `tests/test_maker_e2e.py` (new) + manual smoke run via web UI
**Interface contract:** an end-to-end test that posts a goal to the running orchestrator and asserts:
- 1–3 iterations occurred.
- Final `Result.ok is True` and `data["goal_state"].achieved is True`.
- `Result.cost_usd > 0` and within the per-session daily cap.
- `Result.meta["latency_ms"] > 0`.

The test uses the FastAPI app via `httpx.AsyncClient(app=app)` (already used by other e2e tests in `tests/e2e_mvp.py`). Use a goal that is trivially achievable on a Windows machine: e.g. `"Create a file named maker_smoke.txt with the word ok"`.

**Smoke run (manual, after the automated test passes):**
1. Start `python -m uvicorn orchestrator.main:app` (port 8000).
2. In the web UI, send: `@goal Create a file named maker_smoke.txt in the workspace with the word ok`.
3. Observe iteration count, cost, and final state in the response.
4. Verify the file exists on disk.

**Spec reference:** entire `MAKER_spec.md`

Gate:

| # | Run in PowerShell | Expected output |
|---|-------------------|-----------------|
| 1 | `pytest tests/test_maker_e2e.py -q` | `1 passed` |
| 2 | `Test-Path .\sessions\*\workspace\maker_smoke.txt` | `True` (manual smoke artefact) |
| 3 | `Get-Content (Get-ChildItem .\sessions\*\workspace\maker_smoke.txt \| Select-Object -First 1).FullName` | `ok` |

**Done when:** All three rows pass and the smoke run round-trips through the web UI.
