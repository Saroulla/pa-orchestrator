from __future__ import annotations

import anthropic
import aiosqlite

_DEFAULT_MAX_INPUT = 12000
_DEFAULT_MAX_OUTPUT = 4000


async def build_context(
    db: aiosqlite.Connection,
    session_id: str,
    max_input_tokens: int = _DEFAULT_MAX_INPUT,
    max_output_tokens: int = _DEFAULT_MAX_OUTPUT,
) -> list[dict]:
    """Return recent messages that fit within token budget, newest preserved.

    Queries newest-first, accumulates greedily until sum(tokens) would exceed
    max_input_tokens - max_output_tokens, then returns in chronological order.
    """
    budget = max_input_tokens - max_output_tokens

    async with db.execute(
        "SELECT role, content, tokens FROM messages "
        "WHERE session_id = ? ORDER BY created_at DESC, id DESC",
        (session_id,),
    ) as cursor:
        rows = await cursor.fetchall()

    selected: list[tuple[str, str]] = []
    total = 0
    for role, content, tokens in rows:
        if total + tokens > budget:
            break
        selected.append((role, content))
        total += tokens

    selected.reverse()
    return [{"role": role, "content": content} for role, content in selected]


async def slide_and_summarise(
    db: aiosqlite.Connection,
    session_id: str,
    compress_threshold_tokens: int = 4000,
    max_input_tokens: int = _DEFAULT_MAX_INPUT,
    max_output_tokens: int = _DEFAULT_MAX_OUTPUT,
    summarize_model: str = "claude-haiku-4-5-20251001",
) -> None:
    """Compress out-of-window messages into sessions.summary_anchor when threshold is met.

    Uses the same greedy window logic as build_context. When the accumulated
    token count of messages that fall outside the window reaches
    compress_threshold_tokens, makes ONE Claude API call to produce a summary,
    stores it in sessions.summary_anchor, and deletes the compressed rows.
    """
    budget = max_input_tokens - max_output_tokens

    async with db.execute(
        "SELECT id, role, content, tokens FROM messages "
        "WHERE session_id = ? ORDER BY created_at DESC, id DESC",
        (session_id,),
    ) as cursor:
        rows = await cursor.fetchall()

    # Mirror build_context's greedy window selection.
    in_window_ids: set[int] = set()
    total = 0
    for id_, role, content, tokens in rows:
        if total + tokens > budget:
            break
        in_window_ids.add(id_)
        total += tokens

    # Out-of-window rows in chronological order (oldest first).
    out_of_window = [
        (id_, role, content, tokens)
        for id_, role, content, tokens in reversed(rows)
        if id_ not in in_window_ids
    ]

    if not out_of_window:
        return

    buffer_tokens = sum(tokens for _, _, _, tokens in out_of_window)
    if buffer_tokens < compress_threshold_tokens:
        return

    compress_text = "\n".join(
        f"{role}: {content}" for _, role, content, _ in out_of_window
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=summarize_model,
        max_tokens=500,
        messages=[
            {
                "role": "user",
                "content": "Summarise this conversation history concisely:\n\n" + compress_text,
            }
        ],
    )
    summary: str = response.content[0].text

    await db.execute(
        "UPDATE sessions SET summary_anchor = ? WHERE id = ?",
        (summary, session_id),
    )

    ids_to_delete = [id_ for id_, _, _, _ in out_of_window]
    placeholders = ",".join("?" * len(ids_to_delete))
    await db.execute(
        f"DELETE FROM messages WHERE id IN ({placeholders})",
        ids_to_delete,
    )
    await db.commit()
