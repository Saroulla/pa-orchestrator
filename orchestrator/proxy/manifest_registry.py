from __future__ import annotations


def get_manifest_registry() -> dict:
    """
    Returns dict of adapter_name → AdapterManifest.
    Phase 1 adapters always present.
    Phase 1.2 adapters included if importable (fail silently if not installed).
    """
    from orchestrator.proxy.adapters.claude_api import ClaudeAPIAdapter
    from orchestrator.proxy.adapters.brave_search import BraveSearchAdapter
    from orchestrator.proxy.adapters.file_read import FileReadAdapter
    from orchestrator.proxy.adapters.file_write import FileWriteAdapter

    registry = {}
    for cls in [ClaudeAPIAdapter, BraveSearchAdapter, FileReadAdapter, FileWriteAdapter]:
        try:
            inst = cls()
            registry[inst.name] = inst.manifest
        except Exception:
            pass

    # Phase 1.2 — optional
    for mod_path, cls_name in [
        ("orchestrator.proxy.adapters.playwright_web", "PlaywrightWebAdapter"),
        ("orchestrator.proxy.adapters.pdf_extract",    "PDFExtractAdapter"),
        ("orchestrator.proxy.adapters.email_send",     "EmailAdapter"),
        ("orchestrator.proxy.adapters.template_render","TemplateRenderAdapter"),
    ]:
        try:
            import importlib
            mod = importlib.import_module(mod_path)
            inst = getattr(mod, cls_name)()
            registry[inst.name] = inst.manifest
        except Exception:
            pass

    return registry
