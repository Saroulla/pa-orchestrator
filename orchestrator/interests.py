"""Step 25 — Interest profile read/update helpers."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INTERESTS_PATH = REPO_ROOT / "config" / "interests.md"

_HEADER = (
    "# Interest Profile\n\n"
    "<!-- PA reads this file as context for all responses.\n"
    "     Add entries manually or use @remember <topic> in chat. -->"
)

_COMMENT_ONLY_SENTINEL = "<!-- PA reads this file"


def read_interests() -> str:
    try:
        return INTERESTS_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""


def update_interests(new_interest: str) -> None:
    INTERESTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = read_interests()
    bullet = f"- {new_interest.strip()}"
    if not existing.strip():
        content = f"{_HEADER}\n\n{bullet}\n"
    else:
        content = existing.rstrip("\n") + f"\n{bullet}\n"
    INTERESTS_PATH.write_text(content, encoding="utf-8")


def build_interests_context() -> str:
    content = read_interests()
    stripped = content.strip()
    if not stripped or _COMMENT_ONLY_SENTINEL not in stripped:
        # No header at all means empty/missing file
        if not stripped:
            return ""
    # Check if there's anything beyond the comment header
    lines = [ln for ln in content.splitlines() if ln.strip()]
    has_bullets = any(ln.startswith("- ") for ln in lines)
    if not has_bullets:
        return ""
    return (
        "\n\n---\n\n## User Interests\n\n"
        "Keep the following context in mind for all research, search, and "
        "recommendation responses:\n\n"
        + content
        + "\n"
    )
