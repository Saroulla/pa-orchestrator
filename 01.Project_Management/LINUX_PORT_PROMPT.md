# LINUX_PORT_PROMPT — Brief For The Agent Performing The Windows → Linux Port

> Copy-paste this entire document to the agent running on the Linux box. It contains everything they need: scope, my prior knowledge of the Windows repo, the platform decisions they own, the concerns I want them to investigate, and the verification bar they must clear before declaring the port ready for UAT.

---

## Mission

Port the existing Windows codebase to Linux. Preserve everything that works. The platform-level decisions (shell runtime, service manager, subprocess approach, Cloudflare Tunnel install) are **yours** — you are sitting on the code; you decide. The notes below are a starting map of where the Windows-isms live and where I have high vs low confidence. Validate everything yourself. If you find Windows-specific code I did not flag, surface it in your report.

---

## Scope Guardrails — Read This First

**This port IS:**
- Getting the existing Phase 1 MVP codebase to run on Linux with equivalent behavior to the Windows install.
- Updating docs and constraints that hard-code Windows assumptions so they reflect the Linux reality you create.

**This port IS NOT:**
- Building MAKER (M0–M10 are unimplemented; the scaffolding docs stay scoped to the build sequence).
- Enhancing features, switching architectures, or refactoring code.
- Combining with the CTO/spawner prereq cleanup — that is a separate effort, documented in `AGENT_ONBOARDING.md` § Prerequisite. Check its status (see § Prereq Status Check below), but do not do it as part of this port.
- "Improving" anything you happen to see. Parity migration only. Note suspected smells in your report; do not act on them.

If you finish the port and have spare cycles, stop and report. Do not start adjacent work.

---

## Repo State At Start

- **Phase 1 MVP**: built. 34 done rows archived at `01.Project_Management/BUILD_STATUS.phase1.archive.md`. FastAPI app, SQLite store with WAL, escalation engine, events table, 5 adapters (claude_api, claude_code [being deleted by prereq], brave_search, file_read, file_write), Telegram bot, web UI all working on Windows.
- **MAKER (Phase 2)**: not built. M0–M10 all `todo` in `01.Project_Management/BUILD_STATUS.md`. Only scaffolding docs exist (`MAKER_spec.md`, `MAKER_build.md`, `AGENT_ONBOARDING.md`).
- **CTO/spawner prereq**: status unknown — see § Prereq Status Check.

---

## Prereq Status Check (Do This Before Anything Else)

The CTO/spawner deletion is a manual prerequisite documented in `01.Project_Management/AGENT_ONBOARDING.md` § Prerequisite. It removes `orchestrator/spawner.py`, `orchestrator/proxy/adapters/claude_code.py`, `tests/test_spawner.py`, and several enum/parser branches. Whether it is done affects what you encounter during the port.

**Run these checks before touching anything else:**

```
test -f orchestrator/spawner.py && echo "PREREQ NOT DONE: spawner.py still exists"
test -f orchestrator/proxy/adapters/claude_code.py && echo "PREREQ NOT DONE: claude_code.py still exists"
test -f tests/test_spawner.py && echo "PREREQ NOT DONE: test_spawner.py still exists"
grep -n "Mode.CTO\|CTO_SUBAGENT\|@CTO" orchestrator/models.py orchestrator/parser.py orchestrator/fsm.py
```

If the prereq is **not done**, note it in your report and flag that the user must complete it before MAKER work begins (M0 verifies it). You may still proceed with the Linux port; you will simply also port the spawner/CTO code that is destined for deletion. Do not combine the prereq with the port — they are separate.

If the prereq **is done**, you have fewer files to worry about.

---

## My Knowledge Of The Windows-Specific Surface

Below is what I know about where Windows-isms live in this repo. **HIGH confidence** items I am sure about; **MEDIUM** I expect to see but want you to verify; **LOW** are guesses based on reading the docs, not the code — you must investigate.

### HIGH confidence — these definitely exist

| Location | Windows-ism |
|---|---|
| `run.ps1` (repo root) | PowerShell script; launches uvicorn with env setup |
| `setup_tunnel.ps1` (repo root, if present) | PowerShell script; installs Cloudflare Tunnel cert |
| `orchestrator/spawner.py` | Uses `subprocess.CREATE_NEW_PROCESS_GROUP` for the kill-chain. **Note:** prereq deletes this file. If prereq not done, you will hit this. |
| `CLAUDE.md` (repo root) | Hardware table declares Windows 11 Pro; process model assumes NSSM for service management; "Hard Constraints" list includes "No POSIX signals" and `subprocess.terminate()` → 5s → `.kill()` Windows kill-chain; references `C:\Users\Mini_PC\...` paths throughout |
| `01.Project_Management/BUILD_STATUS.md` | MAKER scaffolding doc — references PowerShell in row names and gate commands |
| `01.Project_Management/AGENT_ONBOARDING.md` | References PowerShell in commands and the working directory note |
| `01.Project_Management/MAKER_spec.md` | Names the executor `run_powershell` and the adapter `PowerShellAdapter`; prompt templates instruct the Decide phase to generate PowerShell scripts |
| `01.Project_Management/MAKER_build.md` | Every gate table contains PowerShell one-liners (`Test-Path`, `Get-ChildItem`, etc.) |
| `01.Project_Management/Execution_Plan.md` | References PowerShell in the universal copy-paste agent prompt |
| `.claude/commands/build-step.md` | References PowerShell in Phase 3 gate-table syntax |

### MEDIUM confidence — likely affected; verify

| Location | What I expect | What to verify |
|---|---|---|
| `orchestrator/proxy/adapters/file_read.py` | Cross-platform via `pathlib` | Confirm `resolve()` + `realpath` + `is_relative_to` correctly rejects traversal on POSIX |
| `orchestrator/proxy/adapters/file_write.py` | Cross-platform via `pathlib` | Same as above. Critically: Linux symlink semantics differ from Windows junctions. Test the symlink-escape case explicitly. |
| `orchestrator/store.py` | SQLite + aiosqlite — cross-platform | Confirm PRAGMAs apply on Linux; `-wal` and `-shm` files appear; `busy_timeout=5000` works under concurrent access |
| `orchestrator/main.py` | FastAPI app + lifespan + events_consumer | Confirm no hardcoded Windows paths; uvicorn boot works |
| `requirements.txt` | Cross-platform Python deps | Confirm every wheel resolves on Linux + Python 3.14. `pywin32`, if present, will fail — flag it. |
| `tests/` (entire directory) | pytest is cross-platform | Path assertions may use `\` or `C:\...`. Run the suite and fix what breaks. |
| `.env` (not committed) | May contain Windows paths | If the user copies it from Windows, audit for `C:\` paths |

### LOW confidence — investigate; might or might not bite

| Concern | Why I am uncertain |
|---|---|
| Line endings | If files were `git clone`d on Linux you are fine. If raw-copied from Windows (USB, scp without conversion), shell scripts and Python files may have CRLF — will silently break shell scripts |
| File permissions | Linux requires executable bit on scripts; Windows does not have one. Files copied raw will lack `+x` |
| Locale / encoding | Windows defaults to `cp1252` for subprocess stdout; Linux defaults UTF-8. Anywhere code decodes subprocess output without explicit `encoding=`, behavior may change |
| `sessions/` workspaces | Existing CTO workspaces from Windows may have Windows-flavored metadata or paths inside files. Recommend wiping `sessions/` on Linux and starting fresh — but verify nothing in the code path assumes their presence |
| `cloudflared` install | Was installed as a Windows service via the `.exe`. Needs full reinstall on Linux. Tunnel cert can be reused but install path differs. |
| `.gitignore` | Currently covers `__pycache__`, `.env`, `orchestrator.db` — likely fine. Verify it does not have Windows-only patterns (Thumbs.db, *.lnk) that imply missing Linux-equivalent coverage. Low-risk either way. |
| Web UI dev server | Vite/React/TS — should be fully cross-platform. Verify `npm install` resolves and the dev server binds to `127.0.0.1` as the security model requires. |
| Anthropic SDK + python-telegram-bot | Both cross-platform officially. Verify on Python 3.14 specifically. |

---

## Decisions YOU Make

These are decisions I will not make for you. You sit on the code, you run the tests, you pick.

1. **Shell runtime.** The Windows repo uses PowerShell for `run.ps1`, `setup_tunnel.ps1`, and gate-table commands in MAKER scaffolding docs. Options:
   - Install `pwsh` (PowerShell 7) on Linux — scripts and docs stay untouched; adds a runtime dependency.
   - Migrate to a native Linux shell (`bash`, `sh`) — rewrite the two `.ps1` scripts and all gate-table commands in the MAKER scaffolding docs to match.
   - Either is valid. Choose what is **lowest-risk for THIS port** (not what you think is best for future MAKER development — that is out of scope).

2. **Service manager.** Phase 1 architecture requires two long-running processes: the FastAPI uvicorn worker and (in Phase 1.2) a scheduler subprocess. Cloudflare Tunnel is a third. Options:
   - `systemd` unit files — declarative, restart-on-failure, journald logs.
   - `supervisord` — simpler unit syntax, less Linux-native.
   - Foreground processes via a launcher script — fine for development, fragile for an always-on personal-use box.
   - Pick whichever supports both the FastAPI process and the scheduler subprocess with appropriate restart and dependency semantics.

3. **Subprocess approach.** `subprocess.CREATE_NEW_PROCESS_GROUP` is the Windows flag for process-group isolation. `start_new_session=True` is the Linux equivalent and creates a new session/process group via `setsid`. Verify:
   - It preserves the existing `.terminate()` → wait 5s → `.kill()` semantics the codebase depends on.
   - The process group can be cleanly reaped on Linux (e.g., killing the parent terminates children).
   - Anywhere the existing code is currently doing something specifically because `CREATE_NEW_PROCESS_GROUP` was set (e.g., handling `CTRL_BREAK_EVENT`) is replaced with the Linux-equivalent behavior, not silently dropped.

4. **Cloudflare Tunnel install.** Existing Windows install used `cloudflared.exe` as a Windows service. Options:
   - Native Linux binary + systemd unit (Cloudflare provides packages for Debian/Ubuntu/RHEL).
   - Docker container (against the project's "No Docker" constraint — do not pick this without explicit user approval).
   - Manual `cloudflared tunnel run` for now; install as service later (acceptable if user accepts the gap).

5. **`CLAUDE.md` constraint updates.** Once you have made the four decisions above, update `CLAUDE.md` to reflect them. Specifically: the OS line, the process model table (NSSM → your choice), the "Hard Constraints" subprocess line, and the "No POSIX signals" entry (which becomes obsolete on Linux). Preserve all other constraints (1 uvicorn worker, no Docker, no Redis, SQLite WAL, $5/day cost cap, FileWriteAdapter caller scoping).

Record each decision with a one-line rationale in your final report.

---

## Starting Greps (Run These, Then Extend)

These are starting points, not exhaustive. After running them, extend the search based on what you find.

```
grep -rn "CREATE_NEW_PROCESS_GROUP" .
grep -rn "creationflags" .
grep -rn "winreg\|msvcrt\|win32\|pywin32" --include="*.py" .
grep -rn "NSSM\|nssm" .
grep -rni "powershell\|pwsh" .
find . -name "*.ps1"
grep -rn "taskkill\|wmic" .
grep -rn "C:\\\\" --include="*.py" --include="*.md" --include="*.yaml" .
grep -rn "C:/" --include="*.py" --include="*.md" --include="*.yaml" .
file orchestrator/*.py | grep CRLF
file *.ps1 2>/dev/null | grep CRLF
grep -rn "\\\\" --include="*.py" .   # hardcoded backslash paths inside Python strings — review hits manually
grep -rn "encoding=" orchestrator/   # places that handle text encoding explicitly; flag any without explicit utf-8
```

Extend the list with any of these when warranted:
- adapter-specific Windows assumptions (e.g., spawn flags inside any adapter)
- references to drive letters or `Program Files`
- `os.name == 'nt'` branches
- `platform.system()` calls
- `sys.platform` checks
- imports of `subprocess.STARTUPINFO`, `subprocess.STARTF_USESHOWWINDOW`

---

## Verification — 9 Layers, All Must Pass Before UAT

This is the bar. Every layer either passes with command output recorded in your report, or fails (and the port is NOT ready). No "looks fine to me." Every claim backed by a command.

### L1 — Static checks

Greps from the previous section return clean **relative to the shell choice you made**. If you kept `pwsh`, `.ps1` and `pwsh` references are expected and fine — confirm they are intentional. If you migrated, none should remain.

**Pass criteria:** zero unexpected hits.

### L2 — Import & parse

Every module imports without `ImportError`. YAML guardrails parses.

```
python -c "from orchestrator.main import app; print('ok')"
python -c "import yaml; yaml.safe_load(open('config/guardrails.yaml')); print('ok')"
python -m compileall orchestrator/ tests/
```

**Pass criteria:** all three commands print `ok` / complete without error.

### L3 — Unit tests

```
pytest tests/ -q
```

**Pass criteria:** every test passes. Zero skips or xfails marked as "Windows-only." If any test is genuinely platform-irrelevant (e.g., a test specifically for Windows-only behavior that no longer exists), delete it or mark it `pytest.mark.skip(reason="...")` with explicit reasoning in your report.

### L4 — SQLite verification

Boot the app once, then inspect the database:

```
sqlite3 orchestrator.db "PRAGMA journal_mode;"   # expect: wal
sqlite3 orchestrator.db "PRAGMA synchronous;"    # expect: 1 (NORMAL)
sqlite3 orchestrator.db "PRAGMA busy_timeout;"   # expect: 5000
ls -la orchestrator.db*                          # expect: orchestrator.db, orchestrator.db-wal, orchestrator.db-shm
```

**Pass criteria:** all PRAGMAs match; WAL and SHM files exist.

### L5 — App boot

Start the service using the launch method you chose (run script, systemd unit, foreground):

```
curl -sS localhost:8000/health      # or whatever the health endpoint is — check main.py
```

Wait 30 seconds after boot, then:

```
ps aux | grep uvicorn               # confirm 1 worker only
ps -o pid,rss,cmd -p <uvicorn pid>  # capture RSS for baseline; expect < ~350 MB at idle
journalctl -u pa-orchestrator -n 50 # or equivalent — confirm no ERROR/CRITICAL log lines
```

**Pass criteria:** health endpoint returns 200, exactly 1 uvicorn worker, no error-level logs, RSS in a reasonable range (record the number — flag if it is unexpectedly high).

### L6 — API smoke

With the service running:

```
# Trivial chat (real Claude call, real cost — expect a small charge)
curl -X POST localhost:8000/v1/chat -H "Content-Type: application/json" \
  -d '{"session_id":"test-port-001","text":"reply with the word OK","channel":"web"}'

# WS stream test (use websocat or a tiny Python script)
# Confirm tokens stream in, then "done" event arrives

# Session lookup
curl -sS localhost:8000/v1/session/test-port-001
```

Mode FSM transitions: send `@Desktop` then any next message, verify Desktop stub triggered and mode reverts to PA. Send `@cost`, verify SQLite cost lookup returns a number.

**Pass criteria:** chat returns a response with `cost_usd > 0`; WS streams; session lookup returns expected JSON; mode transitions correct; `@cost` returns a number.

### L7 — Subprocess kill-chain

This is the layer most likely to surface Windows-vs-Linux differences. Test it directly.

Write a small Python test that:
1. Spawns a long-running subprocess (e.g., `python -c "import time; time.sleep(30)"`) using the same flags the codebase uses (`start_new_session=True` or your chosen replacement).
2. Calls `.terminate()`. Confirms the process is gone within 5 seconds.
3. Spawns another subprocess that ignores SIGTERM (`python -c "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"`). Calls `.terminate()`, waits 5 seconds, calls `.kill()`. Confirms gone.
4. Confirms `ps -o pgid <pid>` shows a process group distinct from the parent's.
5. Spawns a subprocess that itself spawns a child. Kills the parent's process group with `os.killpg(os.getpgid(pid), signal.SIGTERM)`. Confirms both parent and child are gone.

**Pass criteria:** all four scenarios behave as expected. Record the test script in your report (or commit it under `tests/` if it adds value beyond the port — but do not over-engineer).

### L8 — Security regressions

The path-validation logic in `file_read.py` and `file_write.py` was a deliberate audit point. Linux symlinks have different semantics from Windows junctions. Test:

1. Path traversal: attempt to write to `../../etc/passwd` via FileWriteAdapter. Must fail with `BAD_INPUT` or `UNAUTHORIZED`.
2. Symlink escape: create a symlink inside an allowed scope pointing to a path outside it. Attempt to write to the symlink. Must fail.
3. Caller scoping: with the prereq either done or not done, confirm that a write requested as `caller=Caller.PA` cannot land in a CTO subagent workspace path, and vice versa. (If prereq deleted CTO entirely, this scenario is N/A — mark it so.)
4. Session ID regex: attempt to create a session with `id="../../../etc"`. Must fail at session-creation time.
5. Cost cap: with `cost_to_date_usd` mocked at $5.01, attempt a `claude_api` invocation. Dispatcher must reject pre-dispatch.

**Pass criteria:** every scenario behaves correctly. Record each command + outcome.

### L9 — Cross-process events

The Phase 1.2 scheduler subprocess and the FastAPI worker communicate via the SQLite `events` table. Confirm this still works on Linux:

1. With the FastAPI app running, open a second Python process:
   ```
   python -c "import sqlite3; c=sqlite3.connect('orchestrator.db'); c.execute(\"INSERT INTO events(session_id, channel, kind, payload, delivered) VALUES('test-port-001','web','smoke','{}',0)\"); c.commit()"
   ```
2. Within 1–2 seconds, the FastAPI `events_consumer` task should pick the row up and mark `delivered=1`.
3. If a WebSocket is open for `test-port-001`, the smoke event should arrive on it.

**Pass criteria:** the row's `delivered` flag flips to 1 within 2 seconds. If WS is connected, the event arrives.

If Phase 1.2 scheduler is not yet ported (it may be deferred — check), mark L9 as "N/A — scheduler not ported in this pass; events_consumer side validated via direct DB insert."

---

## Documentation Accuracy Check

After all 9 verification layers pass, walk through `01.Project_Management/AGENT_ONBOARDING.md`, `01.Project_Management/MAKER_spec.md`, and `01.Project_Management/MAKER_build.md`. For every shell command quoted in those docs (gate tables especially), confirm the command runs successfully on the Linux box as written. If any command does not, update the doc to match the platform reality you created.

**Pass criteria:** every documented command executes successfully when copy-pasted on the Linux box.

---

## Don't Touch

These are out of scope. Do not modify:

- `.env` — secrets; user copies manually.
- `orchestrator.db` — if porting a live DB, back it up before any test that mutates it.
- `web-ui/` source code — pure JS/TS, cross-platform by construction.
- `sessions/` — recommend wiping; do not modify contents.
- `logs/` — recommend wiping.
- `01.Project_Management/BUILD_STATUS.md` row count or structure — rows stay M0–M10. Update terminology inside row names only if your shell decision requires it.
- The architectural decisions in `MAKER_spec.md` — the iterative-goal loop, the 6-phase structure, the cost-summing semantics, the analyzer count, the dispatcher wiring strategy. These stay frozen. Only swap shell terminology if your decision requires it.
- The 4 coordination docs' overall structure — only platform-specific terminology changes inside them.

---

## Mandatory Report Format

When all 9 verification layers pass (or are explicitly marked N/A), produce a report with these sections, in this order. Do not skip any. Do not declare READY FOR UAT until every L-layer either passes or is explicitly N/A with reasoning.

```markdown
## Linux Port Report — [date]

### Decisions Made
- Shell runtime: <choice> — <one-line rationale>
- Service manager: <choice> — <one-line rationale>
- Subprocess approach: <choice> — <one-line rationale>
- Cloudflare Tunnel install: <choice> — <one-line rationale>

### Prereq Status At Start
- CTO/spawner deletion: <done | not done | partially done> — <observation>

### Files Modified
| Path | Reason |
|---|---|
| ... | ... |

### Files Investigated And Left Alone
| Path | Reason |
|---|---|
| ... | ... |

### Verification Results
| Layer | Pass/Fail/N/A | Command output / reasoning |
|---|---|---|
| L1 Static | ... | ... |
| L2 Import & parse | ... | ... |
| L3 Unit tests | ... | ... |
| L4 SQLite | ... | ... |
| L5 App boot | ... | ... |
| L6 API smoke | ... | ... |
| L7 Subprocess kill-chain | ... | ... |
| L8 Security regressions | ... | ... |
| L9 Cross-process events | ... | ... |
| Doc accuracy | ... | ... |

### Windows-Isms Found Outside My Brief
- ...

### Judgment Calls Made
- ...

### Known-Flaky Or Skipped Tests
- ...

### Memory / Performance Baseline
- uvicorn RSS at idle: <MB>
- uvicorn RSS during simple chat round-trip: <MB peak>
- `ps aux` snapshot: <attach>
- Chat round-trip latency (first-token, total): <ms / ms>

### Recommended UAT Scenarios (3–5 high-risk user-facing flows)
1. ...
2. ...
3. ...

### Verdict
READY FOR UAT
or
NOT READY: <specific blockers listed>
```

---

## Hard Rules

- Do not declare READY FOR UAT if any L1–L9 layer fails.
- Do not combine the CTO/spawner prereq with this port. They are separate efforts.
- Do not add features, refactor, or "improve" code. Parity migration only.
- If a verification layer is genuinely N/A, mark it explicitly with reasoning. Do not silently skip.
- If you find Windows-isms outside my concern list, fix them as part of the port and surface them in the report.
- If you are unsure about a judgment call, document it explicitly and proceed — do not hide it.
- Do not push to a branch other than the one you were given.
