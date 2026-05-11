import asyncio
import subprocess

from orchestrator.maker.safety import MAKERTimeoutError


class MAKERExecutor:
    """Deterministic PowerShell runner. No LLM calls. Windows-only."""

    async def run_powershell(
        self,
        script: str,
        timeout_s: float,
    ) -> tuple[str, str, int]:
        proc = await asyncio.create_subprocess_exec(
            "powershell", "-NoProfile", "-NonInteractive", "-Command", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            raise MAKERTimeoutError(f"powershell exceeded {timeout_s}s")
        return (
            stdout_b.decode("utf-8", errors="replace"),
            stderr_b.decode("utf-8", errors="replace"),
            proc.returncode,
        )
