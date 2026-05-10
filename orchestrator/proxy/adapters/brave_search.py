"""Step 9c — BraveSearchAdapter: REST search with fail_silent error policy."""
import os

import httpx

from orchestrator.models import (
    AdapterManifest,
    AdapterParam,
    Caller,
    ErrorCode,
    ErrorDetail,
    Result,
)


class BraveSearchAdapter:
    name = "brave_search"
    allowed_callers = {Caller.PA, Caller.CTO_SUBAGENT, Caller.JOB_RUNNER}
    _URL = "https://api.search.brave.com/res/v1/web/search"

    async def invoke(
        self,
        payload: dict,
        deadline_s: float,
        caller: Caller,
    ) -> Result:
        if caller not in self.allowed_callers:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.UNAUTHORIZED,
                    message=f"{caller!r} is not permitted to use {self.name}",
                    retriable=False,
                ),
            )

        query: str = payload.get("query", "")
        count: int = int(payload.get("count", 10))
        api_key: str = os.environ.get("BRAVE_SEARCH_API_KEY", "")

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    self._URL,
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": api_key,
                    },
                    params={"q": query, "count": count},
                    timeout=deadline_s,
                )
                response.raise_for_status()
                body = response.json()

            results = [
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "description": item.get("description", ""),
                }
                for item in body.get("web", {}).get("results", [])
            ]
            return Result(ok=True, data={"results": results}, cost_usd=0.0)

        except Exception as exc:
            # fail_silent: search errors never surface as ok=False
            return Result(
                ok=True,
                data={"results": []},
                error=ErrorDetail(
                    code=ErrorCode.TOOL_ERROR,
                    message=str(exc),
                    retriable=True,
                ),
                cost_usd=0.0,
            )

    async def health(self) -> bool:
        api_key = os.environ.get("BRAVE_SEARCH_API_KEY", "")
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    self._URL,
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": api_key,
                    },
                    params={"q": "test", "count": 1},
                    timeout=5.0,
                )
                return response.status_code == 200
        except Exception:
            return False

    @property
    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            required=[
                AdapterParam(name="query", type="str", description="Search query string"),
            ],
            optional=[
                AdapterParam(name="count", type="int", description="Max results to return (default 10, max 20)"),
            ],
        )
