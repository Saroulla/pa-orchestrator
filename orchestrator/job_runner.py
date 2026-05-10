import asyncio, hashlib, json, logging, re, sqlite3, uuid, yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPO_ROOT = Path("C:/Users/Mini_PC/_REPO")
DB_PATH   = REPO_ROOT / "orchestrator.db"
JOBS_DIR  = REPO_ROOT / "jobs"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_job_file(content: str) -> tuple[str, str]:
    """
    Extract ## What I want and ## Execution Plan blocks.
    Returns (what_i_want_text, execution_plan_yaml).
    Raises ValueError if either block is missing.
    """
    m_what = re.search(
        r"##\s+What I want\s*\n(.*?)(?=\n##|\Z)", content, re.DOTALL
    )
    if not m_what:
        raise ValueError("Job file missing '## What I want' block")
    what_i_want = m_what.group(1).strip()

    m_plan = re.search(
        r"##\s+Execution Plan.*?```yaml\s*\n(.*?)```", content, re.DOTALL
    )
    if not m_plan:
        raise ValueError("Job file missing '## Execution Plan' yaml block")
    plan_yaml = m_plan.group(1).strip()

    return what_i_want, plan_yaml


def _validate_plan(plan: dict, registry: dict) -> list[str]:
    """
    Validate Execution Plan YAML against adapter manifests.
    Returns list of error strings (empty = valid).
    """
    errors = []
    steps = plan.get("steps", [])
    if not isinstance(steps, list):
        return ["'steps' must be a list"]

    for step in steps:
        adapter = step.get("adapter")
        if not adapter:
            errors.append(f"Step {step.get('id','?')} missing 'adapter'")
            continue
        if adapter not in registry:
            errors.append(f"Step {step.get('id','?')}: unknown adapter '{adapter}'")
            continue
        manifest = registry[adapter]
        required_names = {p.name for p in manifest.required}
        provided = set((step.get("params") or {}).keys())
        missing = required_names - provided
        if missing:
            errors.append(
                f"Step {step.get('id','?')} ({adapter}): missing required params {missing}"
            )
    return errors


def _substitute(value: Any, results: dict[str, Any]) -> Any:
    """
    Replace $step_id.data references in string values with previous step results.
    """
    if isinstance(value, str):
        def _replace(m):
            ref = m.group(1)
            parts = ref.split(".", 1)
            step_id = parts[0]
            field = parts[1] if len(parts) > 1 else "data"
            step_result = results.get(step_id, {})
            return str(step_result.get(field, m.group(0)))
        return re.sub(r"\$([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)?)", _replace, value)
    if isinstance(value, dict):
        return {k: _substitute(v, results) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(item, results) for item in value]
    return value


async def run(job_id: str) -> None:
    """Main entry point called by scheduler."""
    from orchestrator.proxy.manifest_registry import get_manifest_registry

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    run_id = str(uuid.uuid4())
    started_at = _utcnow()

    try:
        job = conn.execute(
            "SELECT * FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        if job is None:
            logger.error("job_runner: job %s not found", job_id)
            return
        if not job["enabled"]:
            logger.info("job_runner: job %s disabled, skipping", job_id)
            return

        notify_sid = job["created_by_session_id"]

        file_path = REPO_ROOT / job["file_path"]
        if not file_path.exists():
            _fail(conn, run_id, job_id, started_at, f"Job file not found: {file_path}",
                  notify_sid=notify_sid)
            return

        content = file_path.read_text(encoding="utf-8")

        try:
            what_i_want, plan_yaml = _parse_job_file(content)
        except ValueError as exc:
            _fail(conn, run_id, job_id, started_at, str(exc), notify_sid=notify_sid)
            return

        current_checksum = _sha256(what_i_want)
        if current_checksum != job["plan_checksum"]:
            logger.warning("job_runner: checksum mismatch for job %s", job_id)
            _create_checksum_escalation(conn, job_id, job["name"], notify_sid=notify_sid)
            _insert_event(conn, notify_sid, "job_complete",
                          {"summary": f"Job '{job['name']}' skipped — plan is stale (checksum mismatch). Reply (a) to regenerate."})
            conn.commit()
            return

        try:
            plan = yaml.safe_load(plan_yaml)
        except yaml.YAMLError as exc:
            _fail(conn, run_id, job_id, started_at, f"Invalid plan YAML: {exc}", notify_sid=notify_sid)
            return

        registry = get_manifest_registry()
        errors = _validate_plan(plan, registry)
        if errors:
            _fail(conn, run_id, job_id, started_at,
                  "Plan validation failed: " + "; ".join(errors), notify_sid=notify_sid)
            return

        step_results: dict[str, dict] = {}
        total_cost = 0.0
        steps = plan.get("steps", [])

        for step in steps:
            step_id = step.get("id", str(uuid.uuid4())[:8])
            adapter_name = step["adapter"]
            raw_params = step.get("params", {})
            params = _substitute(raw_params, step_results)

            try:
                result = await _dispatch_step(adapter_name, params)
                step_results[step_id] = {"data": result.get("data"), "ok": True}
                total_cost += result.get("cost_usd", 0.0)
            except Exception as exc:
                on_error = step.get("on_error", "escalate")
                if on_error == "skip":
                    logger.warning("job_runner: step %s failed, skipping: %s", step_id, exc)
                    step_results[step_id] = {"data": None, "ok": False, "error": str(exc)}
                    continue
                elif on_error == "abort":
                    _fail(conn, run_id, job_id, started_at,
                          f"Step {step_id} failed (abort): {exc}", cost=total_cost,
                          notify_sid=notify_sid)
                    return
                else:  # escalate
                    _fail(conn, run_id, job_id, started_at,
                          f"Step {step_id} failed: {exc}", cost=total_cost,
                          notify_sid=notify_sid)
                    return

        summary = f"Job '{job['name']}' completed. {len(steps)} steps, ${total_cost:.4f}."
        conn.execute(
            """INSERT INTO job_runs (id,job_id,started_at,completed_at,status,result_summary,cost_usd)
               VALUES (?,?,?,?,?,?,?)""",
            (run_id, job_id, started_at, _utcnow(), "success", summary, total_cost)
        )
        conn.execute(
            "UPDATE jobs SET last_run=? WHERE id=?", (_utcnow(), job_id)
        )
        _insert_event(conn, notify_sid, "job_complete", {"summary": summary, "cost_usd": total_cost})
        conn.commit()
        logger.info("job_runner: %s", summary)

    finally:
        conn.close()


async def _dispatch_step(adapter_name: str, params: dict) -> dict:
    """
    Minimal direct adapter invocation for job runner.
    Imports adapter lazily. Returns {"data": ..., "cost_usd": ...}.
    """
    from orchestrator.models import Caller
    deadline_s = params.pop("_deadline_s", 60.0)

    adapter_map = {
        "brave_search": ("orchestrator.proxy.adapters.brave_search", "BraveSearchAdapter"),
        "file_read":    ("orchestrator.proxy.adapters.file_read",    "FileReadAdapter"),
        "file_write":   ("orchestrator.proxy.adapters.file_write",   "FileWriteAdapter"),
        "playwright_web":    ("orchestrator.proxy.adapters.playwright_web",  "PlaywrightWebAdapter"),
        "pdf_extract":       ("orchestrator.proxy.adapters.pdf_extract",     "PDFExtractAdapter"),
        "email_send":        ("orchestrator.proxy.adapters.email_send",      "EmailAdapter"),
        "template_render":   ("orchestrator.proxy.adapters.template_render", "TemplateRenderAdapter"),
    }
    if adapter_name not in adapter_map:
        raise ValueError(f"Unsupported adapter in job runner: {adapter_name!r}")

    import importlib
    mod_path, cls_name = adapter_map[adapter_name]
    mod = importlib.import_module(mod_path)
    adapter = getattr(mod, cls_name)()
    result = await adapter.invoke(params, deadline_s, Caller.JOB_RUNNER)
    if not result.ok:
        raise RuntimeError(result.error.message if result.error else "adapter error")
    return {"data": result.data, "cost_usd": result.cost_usd}


def _fail(conn, run_id, job_id, started_at, reason, cost=0.0, notify_sid=None):
    conn.execute(
        """INSERT INTO job_runs (id,job_id,started_at,completed_at,status,result_summary,cost_usd)
           VALUES (?,?,?,?,?,?,?)""",
        (run_id, job_id, started_at, _utcnow(), "failed", reason, cost)
    )
    _insert_event(conn, notify_sid, "job_complete",
                  {"summary": f"Job failed: {reason}", "cost_usd": cost})
    conn.commit()
    logger.error("job_runner: job %s failed: %s", job_id, reason)


def _insert_event(conn, session_id: str | None, kind: str, payload: dict) -> None:
    if not session_id:
        logger.warning("job_runner: _insert_event called with no session_id, skipping")
        return
    for channel in ("web", "telegram"):
        conn.execute(
            """INSERT INTO events (session_id,channel,kind,payload,created_at,delivered)
               VALUES (?,?,?,?,?,0)""",
            (session_id, channel, kind, json.dumps(payload), _utcnow())
        )


def _create_checksum_escalation(conn, job_id, job_name, notify_sid=None):
    import uuid as _uuid
    now = datetime.now(timezone.utc)
    from datetime import timedelta
    esc_id = str(_uuid.uuid4())
    esc_session_id = notify_sid if notify_sid else job_id
    conn.execute(
        """INSERT INTO escalations (id,session_id,channel,created_at,expires_at,options,context,status)
           VALUES (?,?,?,?,?,?,?,'pending')""",
        (esc_id, esc_session_id, "web",
         now.isoformat(),
         (now + timedelta(seconds=600)).isoformat(),
         json.dumps({"a": "regenerate plan", "b": "run with old plan", "c": "skip"}),
         json.dumps({"job_name": job_name, "reason": "checksum mismatch"}))
    )
