"""Step 20 — PlaywrightWebAdapter: headless Chromium browser automation."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from orchestrator.models import (
    AdapterManifest,
    AdapterParam,
    Caller,
    ErrorCode,
    ErrorDetail,
    Result,
)

try:
    from playwright.async_api import TimeoutError as _PlaywrightTimeoutError
except ImportError:
    _PlaywrightTimeoutError = None  # type: ignore[assignment,misc]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SESSIONS_DIR = _REPO_ROOT / "sessions"


class PlaywrightWebAdapter:
    name = "playwright_web"
    allowed_callers = {Caller.PA, Caller.CTO_SUBAGENT, Caller.JOB_RUNNER}

    async def invoke(
        self,
        payload: dict,
        deadline_s: float,
        caller: Caller,
        scope_id: str | None = None,
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

        if scope_id is None:
            scope_id = payload.get("scope_id")

        operation = payload.get("operation")
        if not operation:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.BAD_INPUT,
                    message="payload must include 'operation'",
                    retriable=False,
                ),
            )

        t0 = time.monotonic()
        try:
            data = await self._dispatch(operation, payload, deadline_s, scope_id)
            latency_ms = int((time.monotonic() - t0) * 1000)
            return Result(
                ok=True,
                data=data,
                cost_usd=0.0,
                meta={"tool": self.name, "latency_ms": latency_ms, "tokens_in": 0, "tokens_out": 0},
            )
        except Exception as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            if _PlaywrightTimeoutError and isinstance(exc, _PlaywrightTimeoutError):
                return Result(
                    ok=False,
                    error=ErrorDetail(
                        code=ErrorCode.TIMEOUT,
                        message=str(exc),
                        retriable=True,
                    ),
                    meta={"tool": self.name, "latency_ms": latency_ms, "tokens_in": 0, "tokens_out": 0},
                )
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.TOOL_ERROR,
                    message=str(exc),
                    retriable=True,
                ),
                meta={"tool": self.name, "latency_ms": latency_ms, "tokens_in": 0, "tokens_out": 0},
            )

    async def _get_context(self, pw, scope_id: str | None):
        browser = await pw.chromium.launch(headless=True)
        if scope_id:
            auth_dir = _SESSIONS_DIR / scope_id / ".playwright-auth"
            auth_dir.mkdir(parents=True, exist_ok=True)
            state_file = auth_dir / "state.json"
            if state_file.exists():
                context = await browser.new_context(storage_state=str(state_file))
            else:
                context = await browser.new_context()
        else:
            context = await browser.new_context()
        return browser, context

    async def _save_state(self, context, scope_id: str | None) -> None:
        if scope_id:
            auth_dir = _SESSIONS_DIR / scope_id / ".playwright-auth"
            auth_dir.mkdir(parents=True, exist_ok=True)
            await context.storage_state(path=str(auth_dir / "state.json"))

    async def _dispatch(
        self,
        operation: str,
        payload: dict,
        deadline_s: float,
        scope_id: str | None,
    ) -> Any:
        from playwright.async_api import async_playwright

        timeout_ms = int(payload.get("timeout_s", 30) * 1000)

        if operation == "fetch_url":
            url: str = payload["url"]
            wait_for: str | None = payload.get("wait_for")
            async with async_playwright() as pw:
                browser, context = await self._get_context(pw, scope_id)
                try:
                    page = await context.new_page()
                    await page.goto(url, timeout=timeout_ms)
                    if wait_for:
                        await page.wait_for_selector(wait_for, timeout=timeout_ms)
                    html = await page.content()
                    await self._save_state(context, scope_id)
                    return {"html": html}
                finally:
                    await browser.close()

        elif operation == "extract_text":
            url = payload["url"]
            selector: str | None = payload.get("selector")
            t_ms = int(payload.get("timeout_s", 30) * 1000)
            async with async_playwright() as pw:
                browser, context = await self._get_context(pw, scope_id)
                try:
                    page = await context.new_page()
                    await page.goto(url, timeout=t_ms)
                    target = selector if selector else "body"
                    text = await page.inner_text(target, timeout=t_ms)
                    await self._save_state(context, scope_id)
                    return {"text": text}
                finally:
                    await browser.close()

        elif operation == "extract_links_top_n":
            url = payload["url"]
            n = int(payload["n"])
            selector = payload.get("selector", "a")
            attribute = payload.get("attribute", "href")
            title_selector: str | None = payload.get("title_selector")
            async with async_playwright() as pw:
                browser, context = await self._get_context(pw, scope_id)
                try:
                    page = await context.new_page()
                    await page.goto(url, timeout=timeout_ms)
                    links = await page.evaluate(
                        """([sel, attr, titleSel, maxN]) => {
                            const els = Array.from(document.querySelectorAll(sel));
                            return els.slice(0, maxN).map((el, i) => {
                                const titleEl = titleSel ? el.querySelector(titleSel) : null;
                                return {
                                    title: (titleEl || el).textContent.trim(),
                                    url: el.getAttribute(attr) || '',
                                    position: i,
                                };
                            });
                        }""",
                        [selector, attribute, title_selector, n],
                    )
                    await self._save_state(context, scope_id)
                    return links
                finally:
                    await browser.close()

        elif operation == "screenshot":
            url = payload["url"]
            full_page = bool(payload.get("full_page", False))
            viewport: dict | None = payload.get("viewport")
            save_path: str | None = payload.get("save_path")
            async with async_playwright() as pw:
                browser, context = await self._get_context(pw, scope_id)
                try:
                    page = await context.new_page()
                    if viewport:
                        await page.set_viewport_size(viewport)
                    await page.goto(url, timeout=timeout_ms)
                    kwargs: dict[str, Any] = {"full_page": full_page}
                    if save_path:
                        kwargs["path"] = save_path
                    shot = await page.screenshot(**kwargs)
                    await self._save_state(context, scope_id)
                    result: dict[str, Any] = {"bytes": len(shot)}
                    if save_path:
                        result["path"] = save_path
                    return result
                finally:
                    await browser.close()

        elif operation == "submit_form":
            url = payload["url"]
            form_selector: str = payload["form_selector"]
            fields: dict = payload["fields"]
            submit_selector: str | None = payload.get("submit_selector")
            wait_after_s = float(payload.get("wait_after_s", 0))
            async with async_playwright() as pw:
                browser, context = await self._get_context(pw, scope_id)
                try:
                    page = await context.new_page()
                    await page.goto(url, timeout=timeout_ms)
                    for field_sel, value in fields.items():
                        await page.fill(field_sel, str(value))
                    if submit_selector:
                        await page.click(submit_selector)
                    else:
                        await page.locator(form_selector).press("Enter")
                    if wait_after_s > 0:
                        await asyncio.sleep(wait_after_s)
                    final_url = page.url
                    response_text = await page.inner_text("body", timeout=timeout_ms)
                    await self._save_state(context, scope_id)
                    return {"final_url": final_url, "response_text": response_text}
                finally:
                    await browser.close()

        else:
            raise ValueError(f"Unknown operation: {operation!r}")

    async def health(self) -> bool:
        try:
            from playwright.async_api import async_playwright as _  # noqa: F401
            return True
        except Exception:
            return False

    @property
    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            required=[
                AdapterParam(name="operation", type="str", description="fetch_url | extract_text | extract_links_top_n | screenshot | submit_form"),
                AdapterParam(name="url", type="str", description="Target URL"),
            ],
            optional=[
                AdapterParam(name="n", type="int", description="Number of links to return (extract_links_top_n)"),
                AdapterParam(name="selector", type="str", description="CSS selector for element targeting"),
                AdapterParam(name="attribute", type="str", description="Element attribute to extract (default: href)"),
                AdapterParam(name="title_selector", type="str", description="Sub-selector within each link for title text"),
                AdapterParam(name="timeout_s", type="int", description="Navigation timeout in seconds (default 30)"),
                AdapterParam(name="wait_for", type="str", description="CSS selector to wait for after page load"),
                AdapterParam(name="full_page", type="bool", description="Capture full-page screenshot (default False)"),
                AdapterParam(name="viewport", type="dict", description="Viewport size dict with width and height keys"),
                AdapterParam(name="save_path", type="str", description="Filesystem path to save screenshot bytes"),
                AdapterParam(name="form_selector", type="str", description="CSS selector for the form element (submit_form)"),
                AdapterParam(name="fields", type="dict", description="Map of CSS selector to value for form fields"),
                AdapterParam(name="submit_selector", type="str", description="CSS selector for the submit button"),
                AdapterParam(name="wait_after_s", type="int", description="Seconds to wait after form submission"),
                AdapterParam(name="scope_id", type="str", description="Session ID for persisting auth cookies across calls"),
            ],
        )
