"""B5 — ArticleExtractAdapter: trafilatura-based clean article extractor."""
from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Any

import trafilatura
import trafilatura.metadata

from orchestrator.models import (
    AdapterManifest,
    AdapterParam,
    Caller,
    ErrorCode,
    ErrorDetail,
    Result,
)

logger = logging.getLogger(__name__)


def _extract_sync(html: str, url: str) -> dict:
    """Run trafilatura extraction synchronously (called in executor)."""
    meta = trafilatura.extract_metadata(html, default_url=url)
    title = (getattr(meta, "title", None) or "") if meta else ""
    byline = (getattr(meta, "author", None) or "") if meta else ""
    lang = (getattr(meta, "language", None) or "") if meta else ""

    body_text = trafilatura.extract(
        html,
        url=url,
        include_formatting=False,
        output_format="txt",
        no_fallback=False,
    ) or ""

    body_md = trafilatura.extract(
        html,
        url=url,
        include_formatting=True,
        output_format="markdown",
        no_fallback=False,
    ) or ""

    # Fallback: favor_recall extracts more aggressively when standard pass returns empty
    if not body_text:
        body_text = trafilatura.extract(
            html,
            url=url,
            favor_recall=True,
            output_format="txt",
        ) or ""
        body_md = trafilatura.extract(
            html,
            url=url,
            favor_recall=True,
            include_formatting=True,
            output_format="markdown",
        ) or ""

    return {
        "title": title,
        "byline": byline,
        "body_text": body_text,
        "body_md": body_md,
        "lang": lang,
    }


class ArticleExtractAdapter:
    name = "article_extract"
    allowed_callers: set[Caller] = {Caller.MAKER, Caller.JOB_RUNNER}

    @property
    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            required=[
                AdapterParam(name="html", type="str", description="raw HTML content to extract from"),
                AdapterParam(name="url", type="str", description="source URL for metadata resolution"),
            ],
            optional=[],
        )

    async def health(self) -> bool:
        return True

    async def invoke(
        self,
        payload: dict,
        deadline_s: float,
        caller: Caller,
    ) -> Result:
        html = payload.get("html")
        url = payload.get("url", "")

        if not isinstance(html, str) or not html:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.BAD_INPUT,
                    message="'html' must be a non-empty string",
                    retriable=False,
                ),
            )

        loop = asyncio.get_event_loop()
        try:
            data = await asyncio.wait_for(
                loop.run_in_executor(None, partial(_extract_sync, html, url)),
                timeout=deadline_s,
            )
        except asyncio.TimeoutError:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.TIMEOUT,
                    message="article extraction timed out",
                    retriable=True,
                ),
            )
        except Exception as exc:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.TOOL_ERROR,
                    message=str(exc),
                    retriable=False,
                ),
            )

        return Result(
            ok=True,
            data=data,
            cost_usd=0.0,
            meta={"tool": self.name},
        )
