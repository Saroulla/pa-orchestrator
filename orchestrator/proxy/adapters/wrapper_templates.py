"""PA-voice wrapper templates for CTO sub-agent NDJSON envelopes.

Pure constants. No I/O. Used only by ``claude_code.py``. Each template is a
``str.format`` string; ``{content}`` is always the envelope's ``content`` field.

Spec: ``01.Project_Management/sub-agent-pattern.md`` § PA-side wrappers.
"""

PLAN_CONFIRM    = "I want to {content}\n\nProceed?\n  (a) yes\n  (b) cancel"
PLAN_NO_CONFIRM = "Working on it: {content}"
ACTION          = "  → {content}"
RESULT_SIMPLE   = "Done. {content}"
RESULT_FILES    = "Done. {content}  Files: {files}"
ERROR_TEMPLATE  = "Hit an error: {content} ({code})\n  (a) retry\n  (b) abort"
ASK_TEMPLATE    = "{content}"
