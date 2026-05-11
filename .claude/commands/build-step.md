# /build-step

Implement one numbered step from the build sequence.

**Usage:** `/build-step <N>` — where N matches the step number in `01.Project_Management/build.md`.

---

## Phase 0 — Status Check (do this before anything else)

1. Read `01.Project_Management/BUILD_STATUS.md`.
2. Find the row for step N. Apply this decision table exactly:

| Row shows | What to do |
|-----------|-----------|
| `done` | **Stop.** Tell the user: "Step N is already marked done in 01.Project_Management/BUILD_STATUS.md." Ask what to do next. |
| `in_progress` | **Stop.** Tell the user: "Step N is already claimed by another agent (claimed: [timestamp]). Do not duplicate work." |
| `todo` and any dependency not `done` | **Stop.** Tell the user exactly which dependencies are not done and which wave they belong to. |
| `todo` and all dependencies `done` | **Claim it.** Edit `01.Project_Management/BUILD_STATUS.md`: change `todo` → `in_progress \| YYYY-MM-DD HH:MM` for step N only. Then continue to Phase 1. |

3. Read step N from `01.Project_Management/build.md` in full.
4. Read any spec files referenced by that step (e.g. `security-model.md`, `sub-agent-pattern.md`, `escalation-model.md`).

---

## Phase 1 — Plan

Output this block and **stop**. Do not proceed until the user says go.

```
STEP <N> PLAN
─────────────────────────────────────────────
Files to create : <list>
Files to modify : <list or "none">
Interfaces      : <what protocol/contract this satisfies>
Constraints     : <relevant rules from CLAUDE.md — e.g. "1 uvicorn worker", "no POSIX signals">
Risks           : <anything that could go wrong or needs a decision>
─────────────────────────────────────────────
Ready to build — reply GO or redirect me.
```

---

## Phase 2 — Build

Implement the step exactly as specified in `build.md`. Apply all constraints from `CLAUDE.md` without exception. Do not add features, abstractions, or error handling beyond what the step requires.

---

## Phase 3 — Test Handoff

When implementation is complete, output the following table. Every command must be **exact PowerShell syntax**. No prose. No code blocks outside the table.

Render this in the chat as a markdown table with three columns:

| # | Run this in PowerShell | Expected output |
|---|------------------------|-----------------|
| 1 | `<exact command>` | `<what you should see — exact string or pattern>` |
| 2 | `<exact command>` | `<what you should see>` |
| ... | | |

Rules for the table:
- Commands are run from `C:\Users\Mini_PC\_REPO\` unless the step requires otherwise — state the directory in the command itself if different.
- Every command is a complete, copy-pasteable PowerShell one-liner.
- Expected output is the exact string, key phrase, or pattern the user should see. If the output is long, give the critical line only (e.g. `4 passed, 0 failed`).
- If a prerequisite must be running before a command (e.g. uvicorn must be up), add it as its own numbered row first.
- If a command should produce NO output on success, write `(no output — silent = pass)`.
- If a command produces a file, the next row should verify the file exists: `Test-Path <path>` → `True`.

---

## Gate + Completion

After the table, state the gate from `build.md` step N verbatim, then write:

```
Gate status: PENDING — run the table above and confirm all rows pass.
```

**When the user confirms all rows pass:**
Edit `01.Project_Management/BUILD_STATUS.md` — change the step N row from `in_progress | ...` → `done`.
Then tell the user: "Step N marked done in 01.Project_Management/BUILD_STATUS.md. Next available steps: [list steps whose dependencies are now all done]."

Do not mark done until the user explicitly confirms the gate passes.

---

## Constraints embedded (do not look these up — they are locked here)

- **1 uvicorn worker only.** Never `--workers 2` or higher. Single worker eliminates cross-worker WebSocket state problem.
- **No Docker. No Redis.** SQLite + aiosqlite is the store. If a step mentions Docker or Redis, it is outdated — flag it.
- **Windows subprocess rules.** Any process spawn uses `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP`. Termination is `proc.terminate()` → 5s wait → `proc.kill()`. Never `SIGTERM`, `SIGKILL`, `prctl`.
- **No POSIX signals.** Windows 11 host. `subprocess` module only.
- **APScheduler 3.10 only.** Not 4.x. Import path: `apscheduler.schedulers.asyncio.AsyncIOScheduler`.
- **SQLite PRAGMAs on every connection.** `journal_mode=WAL; synchronous=NORMAL; busy_timeout=5000`.
- **No `--loop uvloop` on uvicorn.** Not supported on Windows. Use default loop.
- **Secrets never logged.** Audit log has redaction filter. `.env` never committed.
- **CTO subprocess stdout is NDJSON only.** Non-conformant stdout goes to stderr. See `01.Project_Management/sub-agent-pattern.md`.
- **FileWriteAdapter requires `caller` and `scope_id`.** Never instantiate without them. See `01.Project_Management/security-model.md`.
- **session_id regex:** `^[a-zA-Z0-9_-]{8,64}$` — enforce at session creation in `store.py`.
