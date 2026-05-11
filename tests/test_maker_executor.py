"""Unit tests for orchestrator/maker/executor.py — M8 gate."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.maker.executor import MAKERExecutor
from orchestrator.maker.safety import MAKERTimeoutError


class TestMAKERExecutorHappyPath:
    async def test_stdout_stderr_exit_zero(self):
        e = MAKERExecutor()
        stdout, stderr, ec = await e.run_powershell("Write-Output hello", 10.0)
        assert stdout.strip() == "hello"
        assert ec == 0

    async def test_stderr_captured(self):
        e = MAKERExecutor()
        stdout, stderr, ec = await e.run_powershell(
            "[Console]::Error.WriteLine('oops')", 10.0
        )
        assert "oops" in stderr

    async def test_nonzero_exit_code(self):
        e = MAKERExecutor()
        stdout, stderr, ec = await e.run_powershell("exit 7", 10.0)
        assert ec == 7

    async def test_exit_code_3(self):
        e = MAKERExecutor()
        _, _, ec = await e.run_powershell("exit 3", 10.0)
        assert ec == 3


class TestMAKERExecutorTimeout:
    async def test_timeout_raises_maker_timeout_error(self):
        e = MAKERExecutor()
        with pytest.raises(MAKERTimeoutError):
            await e.run_powershell("Start-Sleep 30", 1.0)

    async def test_timeout_message_contains_duration(self):
        e = MAKERExecutor()
        with pytest.raises(MAKERTimeoutError, match="1.0s"):
            await e.run_powershell("Start-Sleep 30", 1.0)


class TestMAKERExecutorTerminateKillPath:
    """Mock the subprocess so terminate() succeeds within 5s — no real sleep."""

    async def test_terminate_called_on_timeout(self):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock(return_value=None)

        with patch(
            "orchestrator.maker.executor.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            e = MAKERExecutor()
            with pytest.raises(MAKERTimeoutError):
                await e.run_powershell("Start-Sleep 99", 0.01)

        mock_proc.terminate.assert_called_once()

    async def test_kill_called_when_terminate_hangs(self):
        """If proc.wait() itself times out, kill() is invoked."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()

        wait_call_count = 0

        async def flaky_wait():
            nonlocal wait_call_count
            wait_call_count += 1
            if wait_call_count == 1:
                raise asyncio.TimeoutError
            return None

        mock_proc.wait = flaky_wait

        with patch(
            "orchestrator.maker.executor.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            e = MAKERExecutor()
            with pytest.raises(MAKERTimeoutError):
                await e.run_powershell("Start-Sleep 99", 0.01)

        mock_proc.kill.assert_called_once()
