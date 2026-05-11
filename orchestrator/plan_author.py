"""Step 19 — Plan author: generate and write YAML Execution Plans for jobs."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "orchestrator.db"
_MAX_RETRIES = 2
_PLAN_DEADLINE_S = 60.0
_PLAN_MAX_TOKENS = 2000

_PLAN_SCHEMA_DOC = """
## Execution Plan YAML Schema

```
version: 1
trigger:
  cron: "<5-field cron expression>"   # required
  timezone: "<IANA tz>"               # optional, default UTC
steps:
  - id: <unique_step_id>              # required, unique within plan
    adapter: <adapter_name>           # must be from the registry above
    params: { ... }                   # validated against adapter manifest
    on_error: escalate                # escalate | skip | abort (default: escalate)
```

Cross-step references: use `$<step_id>.data` in any param value to pass the
output of a prior step as input to a later step.
Step ids must be unique within the plan.
"""

_PLAN_EXAMPLE = """
## Example (HN daily digest)

version: 1
trigger:
  cron: "0 8 * * *"
  timezone: "Europe/Paris"
steps:
  - id: fetch
    adapter: playwright_web
    params:
      operation: extract_links_top_n
      url: "https://news.ycombinator.com"
      n: 10
      selector: "tr.athing"

  - id: render
    adapter: template_render
    params:
      template: "hn_digest.md.j2"
      context_from: "$fetch.data"

  - id: deliver
    adapter: email_send
    params:
      to: "christophorous@gmail.com"
      subject: "HN Daily"
      body_from: "$render.data"
      content_type: "text/markdown"
"""


def _build_system_prompt_extension(registry: dict) -> str:
    adapter_info: dict[str, dict] = {}
    for name, manifest in registry.items():
        adapter_info[name] = {
            "required": [p.name for p in manifest.required],
            "optional": [p.name for p in manifest.optional],
        }

    adapter_json = json.dumps(adapter_info, indent=2)

    return (
        "## Plan Author — Available Adapters\n\n"
        "```json\n"
        + adapter_json
        + "\n```\n"
        + _PLAN_SCHEMA_DOC
        + "\n"
        + _PLAN_EXAMPLE
        + "\n\n## Rules\n"
        "- Output ONLY valid YAML. No prose, no markdown fences, no explanations.\n"
        "- All adapter names must come from the registry above.\n"
        "- Step ids must be unique within the plan.\n"
        "- Use `$step_id.data` for cross-step references.\n"
        "- The cron expression must be a valid 5-field cron string.\n"
    )


def _strip_yaml_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:yaml)?\s*\n?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


async def generate_plan(
    session_id: str,
    what_i_want: str,
    claude_api,
) -> tuple[str, dict]:
    """Generate and validate a YAML Execution Plan. Returns (plan_yaml_str, parsed_dict)."""
    from orchestrator.job_runner import _validate_plan
    from orchestrator.models import Caller
    from orchestrator.proxy.manifest_registry import get_manifest_registry

    registry = get_manifest_registry()
    system_ext = _build_system_prompt_extension(registry)
    base_prompt = (
        system_ext
        + "\n\nConvert this workflow description into an Execution Plan YAML:\n\n"
        + what_i_want
    )

    last_error: str | None = None

    for attempt in range(_MAX_RETRIES + 1):
        prompt = base_prompt
        if attempt > 0 and last_error:
            prompt = base_prompt + f"\n\nPrevious attempt had errors: {last_error}"

        result = await claude_api.invoke(
            {
                "operation": "complete",
                "prompt": prompt,
                "max_tokens": _PLAN_MAX_TOKENS,
                "session_id": session_id,
            },
            _PLAN_DEADLINE_S,
            Caller.PA,
        )

        if not result.ok:
            last_error = result.error.message if result.error else "API error"
            continue

        raw = result.data or ""
        plan_yaml = _strip_yaml_fences(raw)

        try:
            parsed = yaml.safe_load(plan_yaml)
        except yaml.YAMLError as exc:
            last_error = f"YAML parse error: {exc}"
            continue

        if not isinstance(parsed, dict):
            last_error = "YAML did not produce a mapping"
            continue

        errors = _validate_plan(parsed, registry)
        if errors:
            last_error = "; ".join(errors)
            continue

        return plan_yaml, parsed

    raise ValueError(f"Plan generation failed after 3 attempts: {last_error}")


def write_job(
    session_id: str,
    name: str,
    what_i_want: str,
    plan_yaml: str,
    plan: dict,
) -> str:
    """Write job markdown file and insert/replace jobs DB row. Returns job_id."""
    safe_name = re.sub(r"[^\w-]", "-", name.lower()).strip("-")

    jobs_dir = REPO_ROOT / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)

    file_path = jobs_dir / f"{safe_name}.md"
    file_content = (
        f"# jobs/{safe_name}.md\n\n"
        f"## What I want\n{what_i_want}\n\n"
        f"## Execution Plan\n"
        f"<!-- Generated by PA. Do not edit by hand."
        f" To regenerate: @rebuild-plan jobs/{safe_name}.md -->\n"
        f"```yaml\n{plan_yaml}\n```\n\n"
        f"## Last Run\n(not yet run)\n"
    )
    file_path.write_text(file_content, encoding="utf-8")

    trigger = plan.get("trigger") or {}
    cron = trigger.get("cron", "0 8 * * *") if isinstance(trigger, dict) else "0 8 * * *"
    job_id = str(uuid.uuid4())
    plan_checksum = hashlib.sha256(what_i_want.encode("utf-8")).hexdigest()
    file_path_str = f"jobs/{safe_name}.md"

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(
            """INSERT OR REPLACE INTO jobs
               (id, name, file_path, cron, plan_checksum, enabled, created_by_session_id)
               VALUES (?, ?, ?, ?, ?, 1, ?)""",
            (job_id, safe_name, file_path_str, cron, plan_checksum, session_id),
        )
        conn.commit()
    finally:
        conn.close()

    return job_id


async def rebuild_plan(
    session_id: str,
    file_path_str: str,
    claude_api,
) -> str:
    """Regenerate the Execution Plan block in an existing job file."""
    from orchestrator.job_runner import _parse_job_file

    resolved = REPO_ROOT / file_path_str.lstrip("/\\")
    if not resolved.exists():
        return f"[PA]> Job file not found: {file_path_str}"

    content = resolved.read_text(encoding="utf-8")
    try:
        what_i_want, _ = _parse_job_file(content)
    except ValueError as exc:
        return f"[PA]> Could not parse job file: {exc}"

    try:
        plan_yaml, plan = await generate_plan(session_id, what_i_want, claude_api)
    except ValueError as exc:
        return f"[PA]> Plan generation failed: {exc}"

    safe_name = resolved.stem
    new_content = (
        f"# jobs/{safe_name}.md\n\n"
        f"## What I want\n{what_i_want}\n\n"
        f"## Execution Plan\n"
        f"<!-- Generated by PA. Do not edit by hand."
        f" To regenerate: @rebuild-plan jobs/{safe_name}.md -->\n"
        f"```yaml\n{plan_yaml}\n```\n\n"
        f"## Last Run\n(not yet run)\n"
    )
    resolved.write_text(new_content, encoding="utf-8")

    trigger = plan.get("trigger") or {}
    cron = trigger.get("cron", "0 8 * * *") if isinstance(trigger, dict) else "0 8 * * *"
    plan_checksum = hashlib.sha256(what_i_want.encode("utf-8")).hexdigest()
    file_path_db = file_path_str.replace("\\", "/").lstrip("/")

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(
            "UPDATE jobs SET plan_checksum=?, cron=? WHERE file_path=?",
            (plan_checksum, cron, file_path_db),
        )
        conn.commit()
    finally:
        conn.close()

    return f"[PA]> Plan rebuilt for `{file_path_str}`. Execution plan updated."
