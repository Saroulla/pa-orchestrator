# /new-adapter

Create a new Tool Protocol adapter from scratch.

**Usage:** `/new-adapter <name>` — e.g. `/new-adapter playwright_web`

---

## Before you write a single line of code

1. Read `01.Project_Management/adapter-spec.md` — find whether this adapter is already specced. Use the spec; do not invent a different contract.
2. Read `orchestrator/proxy/protocol.py` — implement exactly the Protocol defined there. Do not extend or modify it.
3. Read `orchestrator/proxy/dispatcher.py` — understand where registration goes.

---

## Phase 1 — Plan

Output this block and **stop**. Do not proceed until the user says go.

```
NEW ADAPTER: <name>
─────────────────────────────────────────────
File           : orchestrator/proxy/adapters/<name>.py
Test file      : tests/unit/test_<name>_adapter.py
Intent kind(s) : <which Intent.kind values route here>
Allowed callers: <pa | job_runner — list all that apply>
External dep   : <library or API this adapter calls, or "none">
scope_id needed: <yes — FileWrite pattern | no>
Manifest params: <list of required + optional params the job runner may pass>
Risks          : <anything needing a decision — e.g. auth, rate limits, Windows compat>
─────────────────────────────────────────────
Ready to build — reply GO or redirect me.
```

---

## Phase 2 — Build

Produce exactly these three outputs in order:

### 1. `orchestrator/proxy/adapters/<name>.py`

Implement the full `Tool` Protocol:

```python
class <Name>Adapter:
    name: str = "<name>"
    allowed_callers: set[Caller] = {Caller.PA, ...}   # only callers from the plan

    async def invoke(self, payload: dict, deadline_s: float, caller: Caller) -> Result:
        # 1. Caller check — raise UNAUTHORIZED if caller not in allowed_callers
        # 2. Validate payload fields — raise BAD_INPUT if required fields missing
        # 3. Execute the work
        # 4. Return Result(ok=True, data=..., error=None, cost_usd=..., meta={...})
        # On any failure → return Result(ok=False, data=None, error=ErrorDetail(...), ...)

    async def health(self) -> bool:
        # Lightweight liveness check — one cheap call or local check
        # Return True/False — never raise

    @property
    def manifest(self) -> AdapterManifest:
        # Declare required + optional params with types
        # Used by job_runner to validate ## Execution Plan YAML before running
        ...
```

Rules:
- Map every failure to exactly one error code: `TIMEOUT | RATE_LIMIT | TOOL_ERROR | QUOTA | BAD_INPUT | UNAUTHORIZED | INTERNAL`
- Every `Result.meta` dict must contain: `tool`, `latency_ms`, `tokens_in`, `tokens_out` (use 0 if not applicable).
- Every `Result.cost_usd` must be a real float (0.0 if this adapter has no API cost).
- `deadline_s` must be enforced — use `asyncio.wait_for(..., timeout=deadline_s)` around the external call.
- If `allowed_callers` does not include the `caller` argument, return `Result(ok=False, error=ErrorDetail(code="UNAUTHORIZED", retriable=False))` immediately.
- Do not log secrets. Do not log payload values that may contain user data at INFO level — use DEBUG.

### 2. Register in `orchestrator/proxy/dispatcher.py`

Add the adapter to the dispatch table keyed by `Intent.kind`. Follow the existing pattern exactly — do not restructure the dispatcher.

### 3. `tests/unit/test_<name>_adapter.py`

Write unit tests using **fixture responses only** — no live API calls, no network, no filesystem writes outside `tmp_path`.

Required test cases:
- Happy path: valid payload → `Result(ok=True)`
- Unauthorized caller: wrong caller → `Result(ok=False, error.code="UNAUTHORIZED")`
- Missing required payload field → `Result(ok=False, error.code="BAD_INPUT")`
- Simulated external failure → `Result(ok=False, error.code="TOOL_ERROR", error.retriable=True/False)`
- `health()` returns `True` when dependency is reachable (mock the dependency)

---

## Phase 3 — Test Handoff

Render this table in the chat. Every command is exact PowerShell, run from `C:\Users\Mini_PC\_REPO\`.

| # | Run this in PowerShell | Expected output |
|---|------------------------|-----------------|
| 1 | `python -m pytest tests/unit/test_<name>_adapter.py -v` | `5 passed, 0 failed` |
| 2 | `python -c "from orchestrator.proxy.adapters.<name> import <Name>Adapter; print('ok')"` | `ok` |
| 3 | `python -c "from orchestrator.proxy.dispatcher import DISPATCH_TABLE; print('<intent_kind>' in DISPATCH_TABLE)"` | `True` |

Add rows for any adapter-specific checks (e.g. import of external library, manifest shape).

After the table:

```
Gate status: PENDING — run the table above and confirm all rows pass.
```

---

## Constraints embedded (locked — do not derive from elsewhere)

- **Protocol lives in `protocol.py` — never copy-paste it.** Import `Tool`, `Result`, `ErrorDetail`, `Caller`, `AdapterManifest` from there.
- **`allowed_callers` is mandatory.** An adapter with no caller restriction is a security hole. Every adapter must declare an explicit set.
- **`deadline_s` must be enforced.** Wrap every external I/O with `asyncio.wait_for`. Map `asyncio.TimeoutError` → `error.code="TIMEOUT"`, `retriable=True`.
- **Unit tests use fixtures, not live calls.** Mock or monkeypatch the external client. Never make a real HTTP call in a unit test.
- **`manifest` property is required even for MVP adapters.** Job runner validates against it in Phase 1.2. Return an empty `AdapterManifest(required=[], optional=[])` at minimum — never omit the property.
- **FileWriteAdapter pattern:** if this adapter writes files, it requires `caller` and `scope_id` at `__init__`. Path validation uses `Path.resolve(strict=False)` + `os.path.realpath` + `Path.is_relative_to(allowed_root)`. See `01.Project_Management/security-model.md`.
- **No `--loop uvloop`.** Windows host. Default asyncio loop only.
- **Cost tracking:** if the adapter calls an external paid API, compute `cost_usd` from response token counts and log a `cost_ledger` row via `store.py`. If free, set `cost_usd=0.0`.
