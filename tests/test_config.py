"""Unit tests for orchestrator/config.py — Step 3 gate."""
import shutil
import time
from pathlib import Path

import pytest
import yaml

from orchestrator.config import Guardrails, get_config, start_watcher

YAML_DIR = Path(__file__).parent / "fixtures"
VALID_YAML = Path(__file__).parent.parent / "config" / "guardrails.yaml"


def _make_valid_dict() -> dict:
    return {
        "failure_policy": {
            "defaults": {
                "timeout": "retry_2x_then_escalate",
                "rate_limit": "queue_request",
                "tool_error": "log_and_escalate",
                "quota": "log_and_escalate",
                "bad_input": "log_and_escalate",
            },
            "by_intent": {
                "code": {"timeout": "retry_1x_then_escalate"},
                "search": {"tool_error": "fail_silent"},
            },
        },
        "retry": {"backoff_base_ms": 500, "backoff_factor": 2.0, "max_attempts": 3},
        "budgets": {
            "per_session_usd_per_day": 5.00,
            "max_input_tokens": 12000,
            "max_output_tokens": 4000,
            "hard_kill_on_breach": True,
        },
        "escalation": {
            "default_ttl_seconds": 600,
            "on_expiry": "skip",
            "on_non_matching_reply": "cancel_and_passthrough",
        },
        "tool_access": {
            "claude_api": "enabled",
            "brave_search": "enabled",
            "file_read": "enabled",
            "file_write": "enabled",
            "playwright": "phase_1_2",
            "pdf_extract": "phase_1_2",
            "email_send": "phase_1_2",
            "template": "phase_1_2",
        },
        "file_write": {"max_bytes": 10485760, "enabled_for": ["pa", "job_runner"]},
        "context_switch": {
            "pa_to_desktop": "stub_only",
        },
        "logging": {
            "destination": "file",
            "path": "logs/audit.jsonl",
            "rotate_mb": 100,
            "user_visible": False,
        },
    }


@pytest.fixture()
def tmp_yaml(tmp_path):
    """Yields a writable copy of guardrails.yaml; watcher started against it."""
    dest = tmp_path / "guardrails.yaml"
    dest.write_text(yaml.dump(_make_valid_dict()), encoding="utf-8")
    observer = start_watcher(dest)
    yield dest
    observer.stop()
    observer.join(timeout=2)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_load_valid_yaml(tmp_yaml):
    cfg = get_config()
    assert isinstance(cfg, Guardrails)
    assert cfg.budgets.per_session_usd_per_day == 5.00
    assert cfg.budgets.max_input_tokens == 12000
    assert cfg.retry.max_attempts == 3
    assert cfg.tool_access.claude_api == "enabled"
    assert cfg.tool_access.playwright == "phase_1_2"
    assert cfg.failure_policy.defaults.timeout == "retry_2x_then_escalate"
    assert cfg.failure_policy.by_intent["search"].tool_error == "fail_silent"
    assert cfg.escalation.on_expiry == "skip"
    assert cfg.file_write.max_bytes == 10485760
    assert "pa" in cfg.file_write.enabled_for


def test_hot_reload_on_mutation(tmp_yaml):
    """Mutate the file; get_config() must return new value within 1.2s."""
    cfg_before = get_config()
    assert cfg_before.retry.max_attempts == 3

    data = _make_valid_dict()
    data["retry"]["max_attempts"] = 7
    tmp_yaml.write_text(yaml.dump(data), encoding="utf-8")

    deadline = time.monotonic() + 1.2
    while time.monotonic() < deadline:
        if get_config().retry.max_attempts == 7:
            break
        time.sleep(0.05)

    assert get_config().retry.max_attempts == 7, "Config was not reloaded within 1.2s"


def test_invalid_yaml_keeps_last_good_config(tmp_yaml):
    """Write syntactically invalid YAML; last good config must survive."""
    good_cfg = get_config()
    assert good_cfg.retry.max_attempts == 3

    tmp_yaml.write_text(":::: this is not valid yaml :::: {{{", encoding="utf-8")

    # Wait long enough for debounce + attempted reload
    time.sleep(0.8)

    still_good = get_config()
    assert still_good.retry.max_attempts == 3, "Bad YAML clobbered good config"


def test_reload_after_invalid_then_valid(tmp_yaml):
    """After invalid YAML, restoring a valid file must reload successfully."""
    # Push invalid
    tmp_yaml.write_text("not: valid: yaml: [[[", encoding="utf-8")
    time.sleep(0.8)

    good_val = get_config()
    assert good_val.retry.max_attempts == 3  # still old good

    # Restore with changed value
    data = _make_valid_dict()
    data["retry"]["max_attempts"] = 9
    tmp_yaml.write_text(yaml.dump(data), encoding="utf-8")

    deadline = time.monotonic() + 1.2
    while time.monotonic() < deadline:
        if get_config().retry.max_attempts == 9:
            break
        time.sleep(0.05)

    assert get_config().retry.max_attempts == 9, "Config did not reload after restoring valid YAML"
