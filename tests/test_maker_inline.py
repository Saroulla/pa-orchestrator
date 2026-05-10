"""R2/R5 — inline.handle tests.

R2-specific tests:
- test_inline_handle_skips_groq_when_none: Verify prefer switches to pa-haiku when pa_groq is None
- test_inline_handle_no_tier_available: Verify error when both pa_groq and pa_haiku are None

R5 will extend with: success, failure, persona threading, deadline coverage.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.maker import inline
from orchestrator.models import Caller, ErrorCode, ErrorDetail, Intent, Mode, Result


class TestInlineHandleR2:
    """R2 tests for pa_groq=None guards."""

    @pytest.mark.asyncio
    async def test_inline_handle_skips_groq_when_none(self) -> None:
        """When pa_groq=None and pa_haiku is available, prefer is set to pa-haiku."""
        mock_result = Result(ok=True, data="ok")
        mock_call_with_promotion = AsyncMock(return_value=mock_result)

        with patch.object(
            inline, "call_with_promotion", new=mock_call_with_promotion
        ):
            result = await inline.handle(
                text="test question",
                session_id="testsess__r2__",
                channel="web",
                db=MagicMock(),
                persona="You are helpful.",
                pa_groq=None,
                pa_haiku=MagicMock(),
            )

        assert result == "ok"
        mock_call_with_promotion.assert_awaited_once()
        call_kwargs = mock_call_with_promotion.call_args.kwargs
        assert call_kwargs["prefer"] == "pa-haiku"

    @pytest.mark.asyncio
    async def test_inline_handle_no_tier_available(self) -> None:
        """When both pa_groq and pa_haiku are None, return error immediately without calling promotion."""
        mock_call_with_promotion = AsyncMock()

        with patch.object(
            inline, "call_with_promotion", new=mock_call_with_promotion
        ):
            result = await inline.handle(
                text="test question",
                session_id="testsess__r2__",
                channel="web",
                db=MagicMock(),
                persona="",
                pa_groq=None,
                pa_haiku=None,
            )

        assert "no LLM tier available" in result
        mock_call_with_promotion.assert_not_awaited()


class TestInlineHandleR5:
    """R5 tests for success, failure, persona threading, and Caller.MAKER."""

    @pytest.mark.asyncio
    async def test_inline_handle_success(self) -> None:
        """Success case: promotion returns ok=True with data."""
        mock_result = Result(ok=True, data="answer")
        mock_call_with_promotion = AsyncMock(return_value=mock_result)

        with patch.object(
            inline, "call_with_promotion", new=mock_call_with_promotion
        ):
            result = await inline.handle(
                text="q",
                session_id="testsess__r5__",
                channel="web",
                db=MagicMock(),
                persona="",
                pa_groq=MagicMock(),
                pa_haiku=MagicMock(),
            )

        assert result == "answer"

    @pytest.mark.asyncio
    async def test_inline_handle_failure(self) -> None:
        """Failure case: promotion returns ok=False with error details."""
        mock_result = Result(
            ok=False,
            error=ErrorDetail(
                code=ErrorCode.TOOL_ERROR, message="boom", retriable=False
            ),
        )
        mock_call_with_promotion = AsyncMock(return_value=mock_result)

        with patch.object(
            inline, "call_with_promotion", new=mock_call_with_promotion
        ):
            result = await inline.handle(
                text="q",
                session_id="testsess__r5__",
                channel="web",
                db=MagicMock(),
                persona="",
                pa_groq=MagicMock(),
                pa_haiku=MagicMock(),
            )

        assert "Sorry, hit an error: boom" in result

    @pytest.mark.asyncio
    async def test_inline_handle_threads_persona(self) -> None:
        """Persona is threaded into Intent payload as 'system' key."""
        captured = []

        async def capture_call(**kwargs):
            captured.append(kwargs)
            return Result(ok=True, data="answer")

        mock_call_with_promotion = AsyncMock(side_effect=capture_call)

        with patch.object(
            inline, "call_with_promotion", new=mock_call_with_promotion
        ):
            await inline.handle(
                text="q",
                session_id="testsess__r5__",
                channel="web",
                db=MagicMock(),
                persona="You are MAKER.",
                pa_groq=MagicMock(),
                pa_haiku=MagicMock(),
            )

        assert len(captured) == 1
        intent = captured[0]["intent"]
        assert intent.payload["system"] == "You are MAKER."

    @pytest.mark.asyncio
    async def test_inline_handle_no_persona_omits_system(self) -> None:
        """When persona is empty, Intent payload has no 'system' key."""
        captured = []

        async def capture_call(**kwargs):
            captured.append(kwargs)
            return Result(ok=True, data="answer")

        mock_call_with_promotion = AsyncMock(side_effect=capture_call)

        with patch.object(
            inline, "call_with_promotion", new=mock_call_with_promotion
        ):
            await inline.handle(
                text="q",
                session_id="testsess__r5__",
                channel="web",
                db=MagicMock(),
                persona="",
                pa_groq=MagicMock(),
                pa_haiku=MagicMock(),
            )

        assert len(captured) == 1
        intent = captured[0]["intent"]
        assert "system" not in intent.payload

    @pytest.mark.asyncio
    async def test_inline_handle_uses_maker_caller(self) -> None:
        """Intent is always created with Caller.MAKER."""
        captured = []

        async def capture_call(**kwargs):
            captured.append(kwargs)
            return Result(ok=True, data="answer")

        mock_call_with_promotion = AsyncMock(side_effect=capture_call)

        with patch.object(
            inline, "call_with_promotion", new=mock_call_with_promotion
        ):
            await inline.handle(
                text="q",
                session_id="testsess__r5__",
                channel="web",
                db=MagicMock(),
                persona="",
                pa_groq=MagicMock(),
                pa_haiku=MagicMock(),
            )

        assert len(captured) == 1
        intent = captured[0]["intent"]
        assert intent.caller == Caller.MAKER
