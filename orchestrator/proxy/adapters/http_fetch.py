"""B4 — HttpFetchAdapter: shared httpx.AsyncClient; head/get/download_to_path."""
from __future__ import annotations

import time
from pathlib import Path

import httpx

from orchestrator.models import (
    AdapterManifest,
    AdapterParam,
    Caller,
    ErrorCode,
    ErrorDetail,
    Result,
)

_DEFAULT_TIMEOUT_S = 15.0
_MAX_REDIRECTS = 5
_USER_AGENT = "MAKER/0.1 (mini-pc)"


class HttpFetchAdapter:
    name = "http_fetch"
    allowed_callers: set[Caller] = {Caller.MAKER, Caller.JOB_RUNNER, Caller.CTO_SUBAGENT}

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        # Injectable for tests; lazy-create singleton otherwise.
        self._injected_client = client

    def _client(self) -> httpx.AsyncClient:
        if self._injected_client is not None:
            return self._injected_client
        if HttpFetchAdapter._shared_client is None or HttpFetchAdapter._shared_client.is_closed:
            HttpFetchAdapter._shared_client = httpx.AsyncClient(
                follow_redirects=True,
                max_redirects=_MAX_REDIRECTS,
                headers={"User-Agent": _USER_AGENT},
            )
        return HttpFetchAdapter._shared_client

    _shared_client: httpx.AsyncClient | None = None

    async def invoke(self, payload: dict, deadline_s: float, caller: Caller) -> Result:
        t0 = time.monotonic()

        def _err(code: ErrorCode, msg: str, retriable: bool = False) -> Result:
            return Result(
                ok=False,
                error=ErrorDetail(code=code, message=msg, retriable=retriable),
                meta={
                    "tool": self.name,
                    "latency_ms": int((time.monotonic() - t0) * 1000),
                    "tokens_in": 0,
                    "tokens_out": 0,
                },
            )

        operation = payload.get("operation")
        if operation not in ("head", "get", "download_to_path"):
            return _err(
                ErrorCode.BAD_INPUT,
                f"payload.operation must be head|get|download_to_path, got {operation!r}",
            )

        url = payload.get("url")
        if not url:
            return _err(ErrorCode.BAD_INPUT, "payload missing required key 'url'")

        timeout_s = min(float(payload.get("timeout_s", _DEFAULT_TIMEOUT_S)), deadline_s)
        timeout = httpx.Timeout(timeout_s)
        client = self._client()

        try:
            if operation == "head":
                response = await client.head(url, timeout=timeout)
                return Result(
                    ok=True,
                    data={
                        "status": response.status_code,
                        "content_type": response.headers.get("content-type", ""),
                        "url": str(response.url),
                        "headers": dict(response.headers),
                    },
                    meta={
                        "tool": self.name,
                        "latency_ms": int((time.monotonic() - t0) * 1000),
                        "tokens_in": 0,
                        "tokens_out": 0,
                    },
                )

            elif operation == "get":
                response = await client.get(url, timeout=timeout)
                response.raise_for_status()
                return Result(
                    ok=True,
                    data={
                        "status": response.status_code,
                        "content_type": response.headers.get("content-type", ""),
                        "url": str(response.url),
                        "body": response.text,
                    },
                    meta={
                        "tool": self.name,
                        "latency_ms": int((time.monotonic() - t0) * 1000),
                        "tokens_in": 0,
                        "tokens_out": 0,
                    },
                )

            else:  # download_to_path
                dest_path = payload.get("path")
                if not dest_path:
                    return _err(ErrorCode.BAD_INPUT, "download_to_path requires 'path' in payload")

                dest = Path(dest_path)
                dest.parent.mkdir(parents=True, exist_ok=True)

                async with client.stream("GET", url, timeout=timeout) as response:
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "")
                    final_url = str(response.url)
                    status = response.status_code
                    with dest.open("wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            f.write(chunk)

                return Result(
                    ok=True,
                    data={
                        "status": status,
                        "content_type": content_type,
                        "url": final_url,
                        "path": str(dest),
                        "size_bytes": dest.stat().st_size,
                    },
                    meta={
                        "tool": self.name,
                        "latency_ms": int((time.monotonic() - t0) * 1000),
                        "tokens_in": 0,
                        "tokens_out": 0,
                    },
                )

        except httpx.TimeoutException:
            return _err(ErrorCode.TIMEOUT, f"Request to {url!r} timed out after {timeout_s}s", retriable=True)
        except httpx.HTTPStatusError as exc:
            code = ErrorCode.RATE_LIMIT if exc.response.status_code == 429 else ErrorCode.TOOL_ERROR
            retriable = exc.response.status_code in (429, 502, 503, 504)
            return _err(code, f"HTTP {exc.response.status_code} from {url!r}", retriable=retriable)
        except httpx.RequestError as exc:
            return _err(ErrorCode.TOOL_ERROR, f"Request error for {url!r}: {exc}", retriable=True)

    async def health(self) -> bool:
        return True

    @property
    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            required=[
                AdapterParam(name="operation", type="str", description="head | get | download_to_path"),
                AdapterParam(name="url", type="str", description="Target URL"),
            ],
            optional=[
                AdapterParam(name="timeout_s", type="float", description="Per-request timeout in seconds (default 15)"),
                AdapterParam(name="path", type="str", description="Required for download_to_path: destination file path"),
            ],
        )
