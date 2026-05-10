"""Step 13 — PA system prompt builder: CLAUDE.md base + live tool inventory."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path("C:/Users/Mini_PC/_REPO")
CLAUDE_MD_PATH = REPO_ROOT / "CLAUDE.md"

_TOOL_INVENTORY_HEADER = """

---

## Available Adapters (runtime inventory)

The following tools are registered and callable via the dispatcher.
Use this section to answer questions about your capabilities accurately.

"""


def build_pa_system_prompt() -> str:
    """Read CLAUDE.md and append a live tool inventory section."""
    try:
        base = CLAUDE_MD_PATH.read_text(encoding="utf-8")
    except OSError:
        base = "You are a helpful Personal Assistant."

    from orchestrator.interests import build_interests_context
    return base + _TOOL_INVENTORY_HEADER + _build_inventory() + build_interests_context()


def _build_inventory() -> str:
    """Return a Markdown table of registered adapters, their operations, and allowed callers."""
    # Deferred imports prevent circular imports at module load time.
    from orchestrator.proxy.adapters.brave_search import BraveSearchAdapter
    from orchestrator.proxy.adapters.claude_api import ClaudeAPIAdapter
    from orchestrator.proxy.adapters.claude_code import ClaudeCodeAdapter
    from orchestrator.proxy.adapters.file_read import FileReadAdapter
    from orchestrator.proxy.adapters.file_write import FileWriteAdapter

    # ClaudeAPIAdapter and ClaudeCodeAdapter have non-trivial __init__ args
    # (API client, spawner …) but manifest is a pure property — __new__ is safe.
    adapters = [
        ClaudeAPIAdapter.__new__(ClaudeAPIAdapter),
        ClaudeCodeAdapter.__new__(ClaudeCodeAdapter),
        BraveSearchAdapter(),
        FileReadAdapter(),
        FileWriteAdapter(),
    ]

    try:
        from orchestrator.proxy.adapters.playwright_web import PlaywrightWebAdapter
        adapters.append(PlaywrightWebAdapter())
    except Exception:
        pass

    try:
        from orchestrator.proxy.adapters.pdf_extract import PDFExtractAdapter
        adapters.append(PDFExtractAdapter())
    except Exception:
        pass

    try:
        from orchestrator.proxy.adapters.email_send import EmailAdapter
        adapters.append(EmailAdapter())
    except Exception:
        pass

    try:
        from orchestrator.proxy.adapters.template_render import TemplateRenderAdapter
        adapters.append(TemplateRenderAdapter())
    except Exception:
        pass

    _SKIP = {"session_id", "messages", "max_tokens"}

    lines = [
        "| Adapter | Operations / Parameters | Allowed callers |",
        "|---------|------------------------|-----------------|",
    ]
    for a in adapters:
        try:
            m = a.manifest
            ops = ", ".join(
                p.name
                for p in (m.required + m.optional)
                if p.name not in _SKIP
            ) or "—"
            callers = ", ".join(sorted(c.value for c in a.allowed_callers))
            lines.append(f"| `{a.name}` | {ops} | {callers} |")
        except Exception:
            lines.append(f"| `{a.name}` | (manifest unavailable) | — |")

    return "\n".join(lines)
