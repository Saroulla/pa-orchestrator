# Escalation Model — State Machine + Resolution Algorithm

> Resolves Audit Item C. Replaces the `pending_escalation` column with a dedicated table.

## Why a table not a column

Plan v2 stored escalation state as `sessions.pending_escalation TEXT`. Problems:

1. **No TTL** — stuck escalations forever
2. **No stacking** — only one escalation at a time even if logically two are needed
3. **Race condition** — although v3 reduces to single uvicorn worker, the scheduler subprocess can also create escalations
4. **Poor typing** — string field with no schema for options/context

A dedicated table fixes all four.

---

## Schema

```sql
CREATE TABLE escalations (
    id TEXT PRIMARY KEY,              -- uuid4
    session_id TEXT NOT NULL,
    channel TEXT NOT NULL,            -- 'web' | 'telegram'
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,         -- created_at + ttl
    options TEXT NOT NULL,            -- JSON: {"a": "retry", "b": "skip", "c": "use_brave"}
    context TEXT NOT NULL,            -- JSON: {error_code, original_intent, attempt, ...}
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | resolved | expired | cancelled
    resolved_with TEXT,               -- option key chosen, or 'expired' / 'cancelled'
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE INDEX idx_escalations_session_pending
    ON escalations(session_id, status);
```

---

## State machine

```
                 create()
                    │
                    ▼
              ┌──────────┐
              │ pending  │ ───── TTL expires ─────▶ ┌──────────┐
              └────┬─────┘                          │ expired  │
                   │                                └──────────┘
   user replies    │
   matching key    ▼
              ┌──────────┐
              │ resolved │
              └──────────┘
                   ▲
                   │
   user replies    │
   non-matching ───┴───▶ ┌─────────────┐
   text                  │  cancelled  │
                         └─────────────┘
```

---

## Resolution algorithm

Executed in `orchestrator/escalation.py`. Called from `main.py` chat handler at the very top of every inbound message processing path.

```python
async def intercept(session_id: str, channel: Channel, text: str) -> InterceptResult:
    """
    Returns:
      - PassThrough(text) — no pending escalation, or non-matching reply that auto-cancels
      - Resolved(escalation, key, branch_data) — matched and atomically claimed
    """
    pending = await store.fetch_one(
        "SELECT * FROM escalations "
        "WHERE session_id=? AND status='pending' "
        "ORDER BY created_at DESC LIMIT 1",
        (session_id,)
    )
    if pending is None:
        return PassThrough(text)

    # Expiry check
    if datetime.utcnow() > parse_iso(pending.expires_at):
        await _expire(pending.id)
        await store.insert_event(
            session_id=session_id, channel=channel,
            kind="escalation_expired",
            payload={"escalation_id": pending.id}
        )
        # User sees the expiry event AND we treat this message as a fresh one
        return PassThrough(text)

    # Match candidate
    candidate = text.strip().lower()
    options = json.loads(pending.options)
    if candidate in options:
        # Atomic resolve
        async with store.transaction("IMMEDIATE"):
            cur = await store.execute(
                "UPDATE escalations SET status='resolved', resolved_with=? "
                "WHERE id=? AND status='pending'",
                (candidate, pending.id)
            )
            if cur.rowcount == 1:
                return Resolved(pending, candidate, branch_data=options[candidate])
            else:
                # Someone else won the race
                return PassThrough(text)
    else:
        # Non-matching reply: auto-cancel and pass through
        await store.execute(
            "UPDATE escalations SET status='cancelled', resolved_with='cancelled' "
            "WHERE id=? AND status='pending'",
            (pending.id,)
        )
        await emit_user_notice(
            session_id, channel,
            "(Cancelled previous prompt — handling your new message.)"
        )
        return PassThrough(text)
```

---

## TTL expiration

`expires_at = created_at + escalation.default_ttl_seconds` (10 min default per `guardrails.yaml`).

Two paths trigger expiry:

1. **Lazy** — checked on next inbound message (above)
2. **Eager** — periodic background sweep (every 60s) inside `main.py`:
   ```python
   async def expire_sweep():
       while True:
           expired = await store.fetch_all(
               "SELECT * FROM escalations "
               "WHERE status='pending' AND expires_at < ?",
               (utcnow_iso(),)
           )
           for e in expired:
               await _expire(e.id)
               await store.insert_event(
                   session_id=e.session_id, channel=e.channel,
                   kind="escalation_expired", payload={"escalation_id": e.id}
               )
           await asyncio.sleep(60)
   ```

The eager sweep ensures even silent users (who don't message back) get notified that their prompt expired.

`escalation.on_expiry` in guardrails decides default behaviour (currently: `skip` — i.e. abort the original action, do not retry).

---

## Race condition guarantees

Even with two writers (uvicorn + scheduler), only one resolution wins:

- `BEGIN IMMEDIATE` acquires the SQLite write lock
- `UPDATE … WHERE status='pending'` is atomic
- `rowcount == 1` proves we won; `rowcount == 0` means another writer already resolved
- The loser falls back to `PassThrough` — the user's message is processed normally as if there had been no escalation

WAL mode + `busy_timeout=5000` means contention is handled gracefully without surfacing errors.

---

## Stacked escalations

Rare but possible — e.g. a job notification arrives while a goal-execution confirmation is pending. We keep the model simple:

- `ORDER BY created_at DESC LIMIT 1` — newest pending wins
- Older pending ones remain until they expire or get explicitly resolved (PA can resolve them programmatically when context is clear)

For Phase 1 we accept this. If users hit confusion in practice, Phase 2 can add a "pending escalations" command that lists all active prompts.

---

## Event emission

| Event | When | Payload |
|-------|------|---------|
| `escalation_created` | On creation | `{id, options, context_summary}` — user sees the prompt |
| `escalation_resolved` | On resolution | `{id, resolved_with, branch_data}` — usually subsumed by the action result |
| `escalation_expired` | On expiry | `{id}` — user notified that prompt timed out |
| `escalation_cancelled` | On non-matching reply | usually subsumed by the inline notice |

Notification routing (web vs telegram) follows the standard events_consumer flow.

---

## Edge cases

- **User sends sticker on Telegram while escalation pending** — no text, no match → auto-cancel + passthrough (which will then not produce useful response since there's no text; PA replies "I didn't catch that").
- **User sends a new `@command` while escalation pending** — full message has no whitespace-trimmed match against `a`/`b`, so it auto-cancels and the new command is processed normally. This is the right behaviour: explicit new commands take precedence.
- **User types `A` (uppercase)** — case-folded to `a`, matches.
- **User types `a.` or `a ` with trailing punctuation** — strip trims whitespace; trailing `.` causes mismatch and auto-cancel. Documented quirk; could be relaxed in Phase 2 (regex `^[a-z]\b`).
- **Escalation created by job_runner (Process 2) while user has live WS to FastAPI (Process 1)** — escalation_created event surfaces via events_consumer; user sees the prompt; replies on the same WS; FastAPI resolves atomically.
- **Two escalations with the same option key created back-to-back** — newest wins via `ORDER BY created_at DESC`. Older one remains pending until TTL.

---

## Test plan

- Unit: state machine transitions cover all four end states
- Unit: option matching is case-insensitive, whitespace-trimmed, single-token only
- Unit: stacked-escalation order works (newest wins)
- Concurrency: two coroutines call `intercept` simultaneously with the matching reply — only one resolves, other passes through
- Integration: TTL expiry triggers event; event delivered to user
- Integration: user types non-matching long sentence — auto-cancel, passthrough, response comes back as if no escalation
