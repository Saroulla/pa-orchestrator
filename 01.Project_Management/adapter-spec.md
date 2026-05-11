# Adapter Specification — Contracts + Manifests

> Every adapter must implement the `Tool` protocol AND publish a manifest used for job plan validation.

## Tool protocol

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class Tool(Protocol):
    name: str
    allowed_callers: set[Caller]
    manifest: AdapterManifest

    async def invoke(
        self,
        payload: dict,
        deadline_s: float,
        caller: Caller,
        scope_id: str | None = None,
    ) -> Result: ...

    async def stream(
        self,
        payload: dict,
        deadline_s: float,
        caller: Caller,
    ) -> AsyncIterator[StreamEvent]:
        """Optional. Used for streaming adapters (claude_api)."""
        ...

    async def health(self) -> bool: ...
```

`AdapterManifest`:
```python
@dataclass
class AdapterManifest:
    adapter_name: str
    operations: dict[str, OperationSpec]
    allowed_callers: set[Caller]
    default_timeout_s: float

@dataclass
class OperationSpec:
    required: dict[str, type]   # param_name -> type
    allowed: dict[str, type]    # param_name -> type
    returns: str                # short description, used by plan author
```

---

## MVP Adapters

### 1. ClaudeAPIAdapter (`claude_api.py`)

| Aspect | Value |
|--------|-------|
| `name` | `"claude_api"` |
| `allowed_callers` | `{PA, JOB_RUNNER}` |
| Operations | `chat` (streaming), `complete` (non-streaming) |
| Streaming | Yes — `stream()` yields tokens |
| Cost | Tokens via `usage` field; `cost_usd` computed per pricing |

Operations:
```python
chat:
  required: { messages: list, max_tokens: int }
  allowed:  { temperature: float, system: str, model: str }
  returns:  "data: full assistant message; cost_usd: token cost"

complete:
  required: { prompt: str, max_tokens: int }
  allowed:  { temperature: float, model: str }
  returns:  "data: completion text"
```

Notes: system prompt + summary_anchor sent with `cache_control: ephemeral` for prompt caching.

---

### 2. BraveSearchAdapter (`brave_search.py`)

| Aspect | Value |
|--------|-------|
| `name` | `"brave_search"` |
| `allowed_callers` | `{PA, JOB_RUNNER}` |
| Operations | `search` |
| Cost | Free tier; counted against API quota |

Operations:
```python
search:
  required: { query: str }
  allowed:  { count: int (max 20), country: str, safesearch: str }
  returns:  "data: list[{title, url, description, age?}]"
```

`fail_silent` per guardrails: errors return empty list, not exception.

---

### 4. FileReadAdapter (`file_read.py`)

| Aspect | Value |
|--------|-------|
| `name` | `"file_read"` |
| `allowed_callers` | `{PA, JOB_RUNNER}` |
| Operations | `read`, `read_chunked` |

Operations:
```python
read:
  required: { path: str }
  allowed:  { encoding: str (default "utf-8") }
  returns:  "data: file contents as string"

read_chunked:
  required: { path: str, chunk_tokens: int }
  allowed:  { encoding: str, start_chunk: int, max_chunks: int }
  returns:  "data: list[chunk_text]"
```

Path scoped per `security-model.md`. Max read size 50 MB.

---

### 5. FileWriteAdapter (`file_write.py`)

| Aspect | Value |
|--------|-------|
| `name` | `"file_write"` |
| `allowed_callers` | `{PA, JOB_RUNNER}` |
| Operations | `write`, `append` |

Operations:
```python
write:
  required: { path: str, content: str }
  allowed:  { encoding: str (default "utf-8"), executable: bool }
  returns:  "data: { bytes_written: int }"

append:
  required: { path: str, content: str }
  allowed:  { encoding: str }
  returns:  "data: { bytes_written: int }"
```

Caller-scoped allowlist + atomic writes per `security-model.md`. Max 10 MB per write.

---

## Phase 1.2 Adapters

### 6. PlaywrightWebAdapter (`playwright_web.py`)

| Aspect | Value |
|--------|-------|
| `name` | `"playwright_web"` |
| `allowed_callers` | `{PA, JOB_RUNNER}` |
| Operations | `fetch_url`, `extract_links_top_n`, `extract_text`, `screenshot`, `submit_form` |

Operations:
```python
fetch_url:
  required: { url: str }
  allowed:  { timeout_s: int (default 30), wait_for: str }
  returns:  "data: html: str"

extract_links_top_n:
  required: { url: str, n: int }
  allowed:  { selector: str, attribute: str (default 'href'), title_selector: str }
  returns:  "data: list[{title, url, position}]"

extract_text:
  required: { url: str }
  allowed:  { selector: str, timeout_s: int }
  returns:  "data: text: str"

screenshot:
  required: { url: str }
  allowed:  { full_page: bool, viewport: dict, save_path: str }
  returns:  "data: { bytes: int, path: str }"

submit_form:
  required: { url: str, form_selector: str, fields: dict }
  allowed:  { submit_selector: str, wait_after_s: int }
  returns:  "data: { final_url: str, response_text: str }"
```

Auth state: `sessions/{scope_id}/.playwright-auth/state.json` for sites needing login.

---

### 7. PDFExtractAdapter (`pdf_extract.py`)

| Aspect | Value |
|--------|-------|
| `name` | `"pdf_extract"` |
| `allowed_callers` | `{PA, JOB_RUNNER}` |
| Operations | `extract_text`, `extract_text_chunked`, `extract_metadata` |

Operations:
```python
extract_text:
  required: { path: str }
  allowed:  { page_range: list[int] }
  returns:  "data: full text: str"

extract_text_chunked:
  required: { path: str, max_tokens_per_chunk: int }
  allowed:  { page_range: list[int] }
  returns:  "data: list[chunk_text]"

extract_metadata:
  required: { path: str }
  allowed:  {}
  returns:  "data: { title, author, pages, created_at, ... }"
```

PyMuPDF (`fitz`) backend.

---

### 8. EmailAdapter (`email_send.py`)

| Aspect | Value |
|--------|-------|
| `name` | `"email_send"` |
| `allowed_callers` | `{PA, JOB_RUNNER}` |
| Operations | `send` |

Operations:
```python
send:
  required: { to: str, subject: str, body: str }
  allowed:  { from: str (default env), cc: list[str], bcc: list[str],
              content_type: str (default 'text/plain'), attachments: list[{path, mimetype}] }
  returns:  "data: { message_id: str }"
```

aiosmtplib backend; env vars `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`.

---

### 9. TemplateRenderAdapter (`template_render.py`)

| Aspect | Value |
|--------|-------|
| `name` | `"template_render"` |
| `allowed_callers` | `{PA, JOB_RUNNER}` |
| Operations | `render` |

Operations:
```python
render:
  required: { template: str, context: dict }
  allowed:  { autoescape: bool }
  returns:  "data: rendered: str"
```

Jinja2 environment loading from `config/templates/`. Special context vars always available: `today`, `now`, `session_id`.

---

## Manifest registry

`orchestrator/proxy/manifest_registry.py`:

```python
ADAPTER_MANIFESTS: dict[str, AdapterManifest] = {
    "claude_api":      ClaudeAPIAdapter.manifest,
    "brave_search":    BraveSearchAdapter.manifest,
    "file_read":       FileReadAdapter.manifest,
    "file_write":      FileWriteAdapter.manifest,
    # Phase 1.2
    "playwright_web":  PlaywrightWebAdapter.manifest,
    "pdf_extract":     PDFExtractAdapter.manifest,
    "email_send":      EmailAdapter.manifest,
    "template_render": TemplateRenderAdapter.manifest,
}
```

PA's plan-author flow exports this registry as JSON to the Claude API system prompt when generating an Execution Plan, so Claude knows exactly which adapters and operations exist + their parameter schemas.

---

## Failure semantics

| Adapter | Default `on_error` policy in job plans |
|---------|-----------------------------------------|
| claude_api | retry then escalate |
| brave_search | fail_silent (per guardrails) |
| file_read | escalate |
| file_write | escalate |
| playwright_web | retry once then escalate |
| pdf_extract | escalate |
| email_send | retry then escalate |
| template_render | escalate |

Plans can override with `on_error: skip | abort | escalate` per step.

---

## Adding a new adapter (checklist)

1. Implement `Tool` protocol in `orchestrator/proxy/adapters/<name>.py`
2. Define `manifest` with all operations + param specs
3. Set `allowed_callers` correctly (default: PA only; expand only if safe)
4. Add to `ADAPTER_MANIFESTS` registry
5. Add to `dispatcher.py` routing table
6. Add to `guardrails.yaml` `tool_access` (default `enabled` or `phase_1_2`)
7. Unit tests: success path, failure path, caller rejection
8. Integration test against real service (or recorded fixture)
9. Document in this file
