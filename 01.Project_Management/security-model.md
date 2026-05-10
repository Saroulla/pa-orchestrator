# Security Model — Path Restrictions + Caller Enforcement

> Resolves Audit Item H. Defense-in-depth for the file system.

## Threat model

Single-user system, but with autonomous agents writing files. The threats to prevent:

1. **Path traversal** — adapter writes outside intended directory via `../../etc/passwd`
2. **Symlink/junction escape** — Windows junction points pointing outside the sandbox
3. **Caller confusion** — CTO sub-agent writes to `jobs/` (which only PA should touch)
4. **Cross-session contamination** — CTO from session A writes into session B's workspace
5. **Disk fill** — runaway adapter writes 100GB file
6. **Race / partial writes** — file appears half-written and downstream tools choke

---

## Caller enforcement

Every adapter invocation carries a `caller: Caller` parameter.

```python
class Caller(StrEnum):
    PA = "pa"
    CTO_SUBAGENT = "cto_subagent"
    JOB_RUNNER = "job_runner"
```

Each adapter declares `allowed_callers: set[Caller]`. The dispatcher checks `intent.caller in adapter.allowed_callers` before invoking — any mismatch returns `Result(ok=False, error=UNAUTHORIZED)`.

`caller` is set by the dispatcher based on context:
- `main.py` chat handler → `Caller.PA`
- `claude_code.py` adapter (when CTO requests a tool) → `Caller.CTO_SUBAGENT`
- `job_runner.py` → `Caller.JOB_RUNNER`

Callers cannot be spoofed by the user — they are set by trusted code paths.

---

## FileWriteAdapter — caller-scoped allowlist

```python
def compute_allowed_roots(caller: Caller, scope_id: str | None) -> list[Path]:
    repo_root = Path.cwd()  # set at startup
    sessions_root = repo_root / "sessions"

    if caller == Caller.PA:
        roots = [
            repo_root / "jobs",
            repo_root / "config" / "interests.md",
            repo_root / "config" / "templates",
        ]
        # PA can also write to its active session's workspace
        if scope_id:
            _validate_session_id(scope_id)
            roots.append(sessions_root / scope_id / "workspace")
        return roots

    if caller == Caller.CTO_SUBAGENT:
        if not scope_id:
            raise ValueError("CTO_SUBAGENT caller requires scope_id (session_id)")
        _validate_session_id(scope_id)
        return [sessions_root / scope_id / "workspace"]

    if caller == Caller.JOB_RUNNER:
        if not scope_id:
            raise ValueError("JOB_RUNNER caller requires scope_id (job_id)")
        _validate_session_id(scope_id)  # job_id uses same regex
        return [sessions_root / scope_id / "workspace"]

    raise ValueError(f"Unknown caller: {caller}")
```

---

## Path validation

```python
import os
from pathlib import Path

SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{8,64}$")

def _validate_session_id(s: str) -> None:
    if not SESSION_ID_RE.match(s):
        raise PermissionError(f"Invalid session_id format: {s!r}")

def validate_write_path(path: Path, caller: Caller, scope_id: str | None) -> Path:
    # 1. Resolve normalises ./, ../, but does not require the file to exist
    resolved = path.resolve(strict=False)

    # 2. On Windows, also follow junctions/symlinks
    real = Path(os.path.realpath(str(resolved)))

    # 3. Compute caller-scoped roots and resolve them too
    allowed_roots = compute_allowed_roots(caller, scope_id)

    for root in allowed_roots:
        root_resolved = Path(os.path.realpath(str(root.resolve(strict=False))))
        # Special case: if root is a file (not a dir), allow exact match
        if root_resolved == real:
            return real
        # General case: real path must be inside root dir
        try:
            real.relative_to(root_resolved)  # raises ValueError if not inside
            return real
        except ValueError:
            continue

    raise PermissionError(
        f"{caller}: write to {path} (resolved {real}) is outside allowed roots: "
        f"{[str(r) for r in allowed_roots]}"
    )
```

`Path.is_relative_to()` is used in newer Python; we use `relative_to(...)` + try/except for the same effect.

---

## Size cap

From `guardrails.yaml`:
```yaml
file_write:
  max_bytes: 10485760              # 10 MB per write
  enabled_for: [pa, cto_subagent, job_runner]
```

`FileWriteAdapter.invoke` rejects writes exceeding `max_bytes` with `Result(ok=False, error=BAD_INPUT, message="size cap exceeded")`.

---

## Atomic writes

```python
import tempfile, os
from pathlib import Path

async def write_atomic(target: Path, content: bytes) -> None:
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmppath = tempfile.mkstemp(dir=parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.replace(tmppath, target)   # atomic on Windows when on same volume
    except Exception:
        try:
            os.unlink(tmppath)
        except FileNotFoundError:
            pass
        raise
```

Guarantees:
- File never appears partially written to readers
- Crash mid-write leaves only the temp file (cleaned up on next start)
- Same-volume rename on Windows is atomic at the file system level

---

## FileReadAdapter — same caller scoping (read side)

Read scope is broader than write but still caller-restricted:

| Caller | Read roots |
|--------|------------|
| PA | `jobs/`, `config/`, `sessions/{active_id}/`, `logs/` (read-only audit) |
| CTO_SUBAGENT | `sessions/{their_id}/` only |
| JOB_RUNNER | `jobs/{their_job_id}.md`, `config/templates/`, `sessions/{their_job_id}/` |

Same path validation algorithm; just a different allowlist function.

Max read size: 50 MB (configurable). Larger files return `BAD_INPUT`; chunked read can be added if needed.

---

## CTO sub-agent file scope

CTO's `FileWriteAdapter` is initialised with `caller=CTO_SUBAGENT, scope_id=its_session_id` — set when the spawner instantiates the adapter for that subprocess context. CTO physically cannot write outside its own workspace because the allowlist excludes everything else.

This is enforced both by the adapter and by the spawner: the per-session `.claude/CLAUDE.md` tells CTO "your workspace is `{path}`; do not write elsewhere", and the adapter refuses any path that resolves outside that root.

---

## Outbound network restrictions

Not directly path security but related:

- Brave Search adapter: only `https://api.search.brave.com/`
- Anthropic API: only `https://api.anthropic.com/`
- Telegram outbound: only `https://api.telegram.org/`
- Email (Phase 1.2): SMTP host configured in env; no other outbound mail
- Playwright (Phase 1.2): allowed domain list per job? — to be designed in Phase 1.2 if needed

---

## Secrets handling

- `.env` is the only place secrets live; `.gitignore` excludes it
- Spawner scrubs subprocess env: only `PATH`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, and an explicit allowlist passed (no API keys unless the sub-agent needs them)
- Audit logs apply a redaction filter for known secret patterns (Anthropic keys begin with `sk-ant-`, Telegram tokens are digits + `:` + 35 alphanumerics, etc.)

---

## Test plan

- Unit: each invalid path (`../`, absolute outside repo, junction pointing outside) rejected with PermissionError
- Unit: each valid path (PA writes `jobs/foo.md`, CTO writes own workspace) accepted
- Unit: cross-session attack — CTO from session A writes to session B's workspace → rejected
- Unit: caller mismatch — CTO calls FileWrite with `Caller.PA` (spoofed) — dispatcher rejects before adapter sees it
- Unit: session_id with `/` or `..` rejected at validation
- Unit: 12 MB write rejected with BAD_INPUT
- Integration: write `jobs/test.md` via real FileWriteAdapter; assert `os.replace` semantics (no partial file appears)
- Integration: simulate crash mid-write (kill process); assert only `.tmp` file remains
- Windows-specific: create a junction from `sessions/junction` → `C:\Windows`, attempt write through it, assert rejected by realpath check
