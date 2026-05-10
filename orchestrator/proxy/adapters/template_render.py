"""Step 23 — TemplateRenderAdapter: Jinja2-based template rendering."""
import datetime
from pathlib import Path

import jinja2

from orchestrator.models import (
    AdapterManifest,
    AdapterParam,
    Caller,
    ErrorCode,
    ErrorDetail,
    Result,
)


class TemplateRenderAdapter:
    name = "template_render"
    allowed_callers = {Caller.PA, Caller.JOB_RUNNER}
    _templates_dir = Path(__file__).parent.parent.parent.parent / "config" / "templates"

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

        template_name: str = payload.get("template", "")
        context: dict = payload.get("context", {})
        autoescape: bool = payload.get("autoescape", False)
        session_id: str = payload.get("session_id", "")

        if ".." in template_name or template_name.startswith("/"):
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.BAD_INPUT,
                    message=f"template name contains '..' or starts with '/': {template_name!r}",
                    retriable=False,
                ),
            )

        try:
            env = jinja2.Environment(
                loader=jinja2.FileSystemLoader(str(self._templates_dir)),
                autoescape=autoescape,
            )

            merged_context = {
                "today": datetime.date.today().isoformat(),
                "now": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "session_id": session_id,
                **context,
            }

            template = env.get_template(template_name)
            rendered = template.render(merged_context)
            return Result(ok=True, data=rendered, cost_usd=0.0)

        except jinja2.TemplateNotFound as exc:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.BAD_INPUT,
                    message=f"template not found: {exc}",
                    retriable=False,
                ),
            )
        except jinja2.TemplateSyntaxError as exc:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.BAD_INPUT,
                    message=f"template syntax error: {exc}",
                    retriable=False,
                ),
            )
        except Exception as exc:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.TOOL_ERROR,
                    message=f"template rendering failed: {exc}",
                    retriable=False,
                ),
            )

    async def health(self) -> bool:
        try:
            import jinja2 as _
            return self._templates_dir.exists()
        except ImportError:
            return False

    @property
    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            required=[
                AdapterParam(
                    name="template",
                    type="str",
                    description="Template filename in config/templates/ (e.g. 'hn_digest.md.j2')",
                ),
                AdapterParam(
                    name="context",
                    type="dict",
                    description="Variables passed to the template",
                ),
            ],
            optional=[
                AdapterParam(
                    name="autoescape",
                    type="bool",
                    description="Enable autoescape (default False)",
                ),
                AdapterParam(
                    name="session_id",
                    type="str",
                    description="Session ID for context (auto-injected)",
                ),
            ],
        )
