"""Step 21 — PDFExtractAdapter: PDF text + metadata extraction via PyMuPDF."""
from pathlib import Path

import fitz

from orchestrator.models import (
    AdapterManifest,
    AdapterParam,
    Caller,
    ErrorCode,
    ErrorDetail,
    Result,
)


class PDFExtractAdapter:
    name = "pdf_extract"
    allowed_callers = {Caller.PA, Caller.JOB_RUNNER}

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

        operation: str = payload.get("operation", "")

        if operation == "extract_text":
            return await self._extract_text(payload)
        elif operation == "extract_text_chunked":
            return await self._extract_text_chunked(payload)
        elif operation == "extract_metadata":
            return await self._extract_metadata(payload)
        else:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.BAD_INPUT,
                    message=f"Unknown operation: {operation!r}",
                    retriable=False,
                ),
            )

    async def _extract_text(self, payload: dict) -> Result:
        path: str = payload.get("path", "")
        page_range: list[int] | None = payload.get("page_range")

        path_obj = self._validate_path(path)
        if path_obj is None:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.BAD_INPUT,
                    message=f"Invalid or non-existent PDF path: {path!r}",
                    retriable=False,
                ),
            )

        try:
            doc = fitz.open(str(path_obj))
            pages_to_extract = page_range if page_range else range(doc.page_count)
            texts = []
            for page_num in pages_to_extract:
                if 0 <= page_num < doc.page_count:
                    page = doc[page_num]
                    texts.append(page.get_text())
            doc.close()

            full_text = "\n\n".join(texts)
            return Result(ok=True, data=full_text, cost_usd=0.0)
        except FileNotFoundError:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.BAD_INPUT,
                    message=f"PDF file not found: {path!r}",
                    retriable=False,
                ),
            )
        except Exception as exc:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.TOOL_ERROR,
                    message=str(exc),
                    retriable=False,
                ),
            )

    async def _extract_text_chunked(self, payload: dict) -> Result:
        path: str = payload.get("path", "")
        max_tokens_per_chunk: int = payload.get("max_tokens_per_chunk", 0)
        page_range: list[int] | None = payload.get("page_range")

        if max_tokens_per_chunk <= 0:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.BAD_INPUT,
                    message="max_tokens_per_chunk must be > 0",
                    retriable=False,
                ),
            )

        path_obj = self._validate_path(path)
        if path_obj is None:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.BAD_INPUT,
                    message=f"Invalid or non-existent PDF path: {path!r}",
                    retriable=False,
                ),
            )

        try:
            doc = fitz.open(str(path_obj))
            pages_to_extract = page_range if page_range else range(doc.page_count)

            chunks = []
            current_chunk = ""
            for page_num in pages_to_extract:
                if 0 <= page_num < doc.page_count:
                    page = doc[page_num]
                    page_text = page.get_text()
                    estimated_tokens = len(page_text) // 4

                    if current_chunk and (
                        len(current_chunk) // 4 + estimated_tokens > max_tokens_per_chunk
                    ):
                        chunks.append(current_chunk)
                        current_chunk = ""

                    current_chunk += page_text
                    if page_num < (pages_to_extract[-1] if pages_to_extract else doc.page_count - 1):
                        current_chunk += "\n\n"

            if current_chunk:
                chunks.append(current_chunk)

            doc.close()
            return Result(ok=True, data=chunks, cost_usd=0.0)
        except FileNotFoundError:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.BAD_INPUT,
                    message=f"PDF file not found: {path!r}",
                    retriable=False,
                ),
            )
        except Exception as exc:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.TOOL_ERROR,
                    message=str(exc),
                    retriable=False,
                ),
            )

    async def _extract_metadata(self, payload: dict) -> Result:
        path: str = payload.get("path", "")

        path_obj = self._validate_path(path)
        if path_obj is None:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.BAD_INPUT,
                    message=f"Invalid or non-existent PDF path: {path!r}",
                    retriable=False,
                ),
            )

        try:
            doc = fitz.open(str(path_obj))
            metadata = doc.metadata
            page_count = doc.page_count
            doc.close()

            result_metadata = {
                "title": metadata.get("title", "") if metadata else "",
                "author": metadata.get("author", "") if metadata else "",
                "pages": page_count,
                "created_at": metadata.get("creationDate", "") if metadata else "",
            }

            return Result(ok=True, data=result_metadata, cost_usd=0.0)
        except FileNotFoundError:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.BAD_INPUT,
                    message=f"PDF file not found: {path!r}",
                    retriable=False,
                ),
            )
        except Exception as exc:
            return Result(
                ok=False,
                error=ErrorDetail(
                    code=ErrorCode.TOOL_ERROR,
                    message=str(exc),
                    retriable=False,
                ),
            )

    def _validate_path(self, path: str) -> Path | None:
        try:
            resolved = Path(path).resolve()
            if not resolved.exists() or not resolved.suffix.lower() == ".pdf":
                return None
            return resolved
        except Exception:
            return None

    async def health(self) -> bool:
        try:
            import fitz  # noqa: F401
            return True
        except Exception:
            return False

    @property
    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            required=[
                AdapterParam(name="operation", type="str", description="Operation: extract_text, extract_text_chunked, or extract_metadata"),
                AdapterParam(name="path", type="str", description="Path to PDF file"),
            ],
            optional=[
                AdapterParam(name="page_range", type="list[int]", description="List of 0-indexed page numbers to extract"),
                AdapterParam(name="max_tokens_per_chunk", type="int", description="Max tokens per chunk (required for extract_text_chunked)"),
            ],
        )


