from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{8,64}$")


class Mode(StrEnum):
    PA = "PA"
    CTO = "CTO"
    DESKTOP = "DESKTOP"


class Channel(StrEnum):
    WEB = "web"
    TELEGRAM = "telegram"


class Caller(StrEnum):
    PA = "pa"
    CTO_SUBAGENT = "cto_subagent"
    JOB_RUNNER = "job_runner"
    MAKER = "maker"


class ErrorCode(StrEnum):
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"
    TOOL_ERROR = "TOOL_ERROR"
    QUOTA = "QUOTA"
    BAD_INPUT = "BAD_INPUT"
    UNAUTHORIZED = "UNAUTHORIZED"
    INTERNAL = "INTERNAL"


class EscalationStatus(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class EventKind(StrEnum):
    JOB_COMPLETE = "job_complete"
    JOB_PROGRESS = "job_progress"
    ESCALATION_EXPIRED = "escalation_expired"
    SYSTEM_MESSAGE = "system_message"


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str
    retriable: bool


class Intent(BaseModel):
    kind: Literal[
        "reason", "code", "search", "file_read", "file_write",
        "external_api", "desktop", "plan_step",
    ]
    payload: dict[str, Any]
    session_id: str
    mode: Mode
    caller: Caller
    deadline_s: float
    attempt: int = 0

    @field_validator("session_id")
    @classmethod
    def _validate_session_id(cls, v: str) -> str:
        if not SESSION_ID_RE.match(v):
            raise ValueError(
                f"session_id must match ^[a-zA-Z0-9_-]{{8,64}}$, got: {v!r}"
            )
        return v


class Result(BaseModel):
    ok: bool
    data: Any | None = None
    error: ErrorDetail | None = None
    cost_usd: float = 0.0
    # Required keys: tool, latency_ms, tokens_in, tokens_out
    meta: dict[str, Any] = Field(default_factory=dict)


class Session(BaseModel):
    id: str
    channel: Channel
    mode: Mode = Mode.PA
    cc_pid: int | None = None
    telegram_chat_id: int | None = None
    cost_to_date_usd: float = 0.0
    summary_anchor: str | None = None
    created_at: str
    last_active: str

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not SESSION_ID_RE.match(v):
            raise ValueError(
                f"session id must match ^[a-zA-Z0-9_-]{{8,64}}$, got: {v!r}"
            )
        return v


class Escalation(BaseModel):
    id: str
    session_id: str
    channel: Channel
    created_at: str
    expires_at: str
    options: dict[str, str]
    context: dict[str, Any]
    status: EscalationStatus = EscalationStatus.PENDING
    resolved_with: str | None = None


class Event(BaseModel):
    id: int | None = None
    session_id: str
    channel: Channel
    kind: EventKind
    payload: dict[str, Any]
    created_at: str
    delivered: bool = False
    delivered_at: str | None = None
    message_type: str | None = None


class AdapterParam(BaseModel):
    name: str
    type: str
    description: str = ""


class AdapterManifest(BaseModel):
    required: list[AdapterParam] = Field(default_factory=list)
    optional: list[AdapterParam] = Field(default_factory=list)


class CostLedgerRow(BaseModel):
    id: int | None = None
    session_id: str | None = None
    job_id: str | None = None
    adapter: str
    tokens: int = 0
    cost_usd: float = 0.0
    tier: str = ""
    timestamp: str
