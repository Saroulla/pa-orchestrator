"""Phase 2 B3 — GoogleCSEAdapter: Google Custom Search Engine REST adapter.

Implements the Tool protocol. Callers: MAKER, JOB_RUNNER.

payload keys:
  q       (str, required)  — search query
  n       (int, optional)  — max results, default 10
  safe    (str, optional)  — safe-search setting, default "off"
  site    (str, optional)  — restrict results to this domain via siteSearch param

Returns Result.data = list of {title, link, snippet} dicts.

Error mapping:
  HTTP 429 → ErrorCode.RATE_LIMIT (retriable=True)
  HTTP 403 → ErrorCode.UNAUTHORIZED (retriable=False)
  asyncio.TimeoutError → ErrorCode.TIMEOUT (retriable=True)
  other     → ErrorCode.TOOL_ERROR (retriable=True)

API key is never logged.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

import httpx

from orchestrator.models import (
    AdapterManifest,
    AdapterParam,
    Caller,
    ErrorCode,
    ErrorDetail,
    Result,
)

logger = logging.getLogger(__name__)

_CSE_URL = "https://www.googleapis.com/customsearch/v1"


class GoogleCSEAdapter:
    name = "google_cse"
    allowed_callers: set[Caller] = {Caller.MAKER, Caller.JOB_RUNNER}

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None

    # ------------------------------------------------------------------ Tool

    @property
    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            required=[
                AdapterParam(name="q", type="str", description="Search query"),
            ],
            optional=[
                AdapterParam(name="n", type="int", description="Max results (default 10)"),
                AdapterParam(name="safe", type="str", description="Safe-search mode (default 'off')"),
                AdapterParam(name="site", type="str", description="Restrict results to this domain"),
            ],
        )

    async def health(self) -> bool:
        return (
            bool(os.environ.get("GOOGLE_CSE_API_KEY"))
            and bool(os.environ.get("GOOGLE_CSE_CX"))
        )

    async def invoke(
        self,
        payload: dict,
        deadline_s: float,
        caller: Caller,
    ) -> Result:
        # Validate required param
        q = payload.get("q")
        if not q or not isinstance(q, str):
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.BAD_INPUT,
                    message="'q' is required and must be a non-empty string",
                    retriable=False,
                ),
            )

        n: int = int(payload.get("n", 10))
        safe: str = payload.get("safe", "off")
        site: str | None = payload.get("site")

        api_key: str = os.environ.get("GOOGLE_CSE_API_KEY", "")
        cx: str = os.environ.get("GOOGLE_CSE_CX", "")

        params: dict = {
            "key": api_key,
            "cx": cx,
            "q": q,
            "num": n,
            "safe": safe,
        }
        if site:
            params["siteSearch"] = site

        start = time.monotonic()

        try:
            if self._client is not None:
                response = await asyncio.wait_for(
                    self._client.get(_CSE_URL, params=params),
                    timeout=deadline_s,
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await asyncio.wait_for(
                        client.get(_CSE_URL, params=params),
                        timeout=deadline_s,
                    )
        except asyncio.TimeoutError:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.TIMEOUT,
                    message=f"Google CSE request timed out after {deadline_s}s",
                    retriable=True,
                ),
                meta={
                    "tool": self.name,
                    "latency_ms": int((time.monotonic() - start) * 1000),
                },
            )
        except Exception as exc:
            # Never log the api_key value
            logger.error("google_cse: HTTP error: %s", type(exc).__name__)
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.TOOL_ERROR,
                    message=str(exc),
                    retriable=True,
                ),
                meta={
                    "tool": self.name,
                    "latency_ms": int((time.monotonic() - start) * 1000),
                },
            )

        latency_ms = int((time.monotonic() - start) * 1000)

        if response.status_code == 429:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.RATE_LIMIT,
                    message="Google CSE rate limit exceeded (HTTP 429)",
                    retriable=True,
                ),
                meta={"tool": self.name, "latency_ms": latency_ms},
            )

        if response.status_code == 403:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.UNAUTHORIZED,
                    message="Google CSE access denied (HTTP 403) — check API key and CX",
                    retriable=False,
                ),
                meta={"tool": self.name, "latency_ms": latency_ms},
            )

        if response.status_code >= 400:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.TOOL_ERROR,
                    message=f"Google CSE returned HTTP {response.status_code}",
                    retriable=response.status_code >= 500,
                ),
                meta={"tool": self.name, "latency_ms": latency_ms},
            )

        body = response.json()
        items = body.get("items", [])
        data = [
            {
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            }
            for item in items
        ]

        return Result(
            ok=True,
            data=data,
            cost_usd=0.0,
            meta={
                "tool": self.name,
                "latency_ms": latency_ms,
                "result_count": len(data),
            },
        )
