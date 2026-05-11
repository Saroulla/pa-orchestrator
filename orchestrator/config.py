"""Step 3 — YAML guardrails loader + watchdog hot-reload."""
import logging
import threading
import time
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from watchdog.events import FileSystemEventHandler, FileModifiedEvent
from watchdog.observers import Observer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic schema
# ---------------------------------------------------------------------------

class FailurePolicyDefaults(BaseModel):
    timeout: str
    rate_limit: str
    tool_error: str
    quota: str
    bad_input: str


class FailurePolicyByIntent(BaseModel):
    timeout: str | None = None
    rate_limit: str | None = None
    tool_error: str | None = None
    quota: str | None = None
    bad_input: str | None = None


class FailurePolicy(BaseModel):
    defaults: FailurePolicyDefaults
    by_intent: dict[str, FailurePolicyByIntent] = Field(default_factory=dict)


class RetryConfig(BaseModel):
    backoff_base_ms: int
    backoff_factor: float
    max_attempts: int


class Budgets(BaseModel):
    per_session_usd_per_day: float
    max_input_tokens: int
    max_output_tokens: int
    hard_kill_on_breach: bool


class EscalationConfig(BaseModel):
    default_ttl_seconds: int
    on_expiry: Literal["skip", "retry", "cancel"]
    on_non_matching_reply: str


class ToolAccess(BaseModel):
    claude_api: str
    brave_search: str
    file_read: str
    file_write: str
    playwright: str
    pdf_extract: str
    email_send: str
    template: str


class FileWriteConfig(BaseModel):
    max_bytes: int
    enabled_for: list[str]


class ContextSwitch(BaseModel):
    pa_to_desktop: str


class LoggingConfig(BaseModel):
    destination: str
    path: str
    rotate_mb: int
    user_visible: bool


class ModelsConfig(BaseModel):
    pa_chat: str = "claude-haiku-4-5-20251001"
    summarize: str = "claude-haiku-4-5-20251001"
    plan_author: str = "claude-sonnet-4-6"


class Guardrails(BaseModel):
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    failure_policy: FailurePolicy
    retry: RetryConfig
    budgets: Budgets
    escalation: EscalationConfig
    tool_access: ToolAccess
    file_write: FileWriteConfig
    context_switch: ContextSwitch
    logging: LoggingConfig


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_config: Guardrails | None = None
_config_path: Path | None = None


def _load(path: Path) -> Guardrails:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Guardrails.model_validate(raw)


def _try_reload(path: Path) -> None:
    global _config
    try:
        new_cfg = _load(path)
        with _lock:
            _config = new_cfg
        logger.info("guardrails reloaded from %s", path)
    except Exception as exc:
        logger.error("guardrails reload failed — keeping previous config: %s", exc)


def get_config() -> Guardrails:
    with _lock:
        if _config is None:
            raise RuntimeError("Config not initialised — call start_watcher() first")
        return _config


# ---------------------------------------------------------------------------
# Watchdog hot-reload
# ---------------------------------------------------------------------------

class _GuardrailsHandler(FileSystemEventHandler):
    def __init__(self, path: Path, debounce_s: float = 0.5) -> None:
        super().__init__()
        self._path = path.resolve()
        self._debounce_s = debounce_s
        self._timer: threading.Timer | None = None
        self._timer_lock = threading.Lock()

    def _schedule_reload(self) -> None:
        with self._timer_lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_s, _try_reload, args=(self._path,))
            self._timer.daemon = True
            self._timer.start()

    def on_modified(self, event: FileModifiedEvent) -> None:
        if not event.is_directory and Path(event.src_path).resolve() == self._path:
            self._schedule_reload()

    # watchdog may emit created instead of modified on some editors/platforms
    def on_created(self, event) -> None:
        if not event.is_directory and Path(event.src_path).resolve() == self._path:
            self._schedule_reload()


def start_watcher(path: str | Path) -> Observer:
    global _config, _config_path
    path = Path(path).resolve()
    _config_path = path

    # Initial load — must succeed
    with _lock:
        _config = _load(path)

    handler = _GuardrailsHandler(path)
    observer = Observer()
    observer.schedule(handler, str(path.parent), recursive=False)
    observer.daemon = True
    observer.start()
    logger.info("guardrails watcher started for %s", path)
    return observer
