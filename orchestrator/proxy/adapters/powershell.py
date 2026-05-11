"""PowerShellAdapter — Tool-protocol wrapper around MAKERExecutor."""
from __future__ import annotations

import time

from orchestrator.maker.executor import MAKERExecutor
from orchestrator.maker.safety import MAKERTimeoutError
from orchestrator.models import (
    AdapterManifest,
    AdapterParam,
    Caller,
    ErrorCode,
    ErrorDetail,
    Result,
)


class PowerShellAdapter:
    name: str = "powershell"
    allowed_callers: set[Caller] = {Caller.PA, Caller.JOB_RUNNER}

    def __init__(self, executor: MAKERExecutor | None = None) -> None:
        self._executor = executor or MAKERExecutor()

    @property
    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            required=[
                AdapterParam(name="script", type="str", description="PowerShell script body"),
            ],
            optional=[
                AdapterParam(name="timeout_s", type="float", description="Hard timeout; default 60"),
                AdapterParam(name="session_id", type="str", description="for cost_ledger attribution"),
            ],
        )

    async def health(self) -> bool:
        return True

    async def invoke(self, payload: dict, deadline_s: float, caller: Caller) -> Result:
        script = payload["script"]
        timeout_s = float(payload.get("timeout_s", min(60.0, deadline_s)))
        t0 = time.monotonic()
        try:
            stdout, stderr, exit_code = await self._executor.run_powershell(script, timeout_s)
        except MAKERTimeoutError as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            return Result(
                ok=False,
                error=ErrorDetail(code=ErrorCode.TIMEOUT, message=str(exc), retriable=True),
                cost_usd=0.0,
                meta={"tool": "powershell", "latency_ms": latency_ms, "tokens_in": 0, "tokens_out": 0},
            )
        latency_ms = int((time.monotonic() - t0) * 1000)
        return Result(
            ok=(exit_code == 0),
            data={"stdout": stdout, "stderr": stderr, "exit_code": exit_code},
            error=None if exit_code == 0 else ErrorDetail(
                code=ErrorCode.TOOL_ERROR,
                message=f"exit_code={exit_code}",
                retriable=False,
            ),
            cost_usd=0.0,
            meta={"tool": "powershell", "latency_ms": latency_ms, "tokens_in": 0, "tokens_out": 0},
        )
