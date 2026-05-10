"""Tool Protocol — structural interface every adapter must satisfy."""

from typing import AsyncIterator, Protocol, runtime_checkable

from orchestrator.models import AdapterManifest, Caller, Result


@runtime_checkable
class Tool(Protocol):
    name: str
    allowed_callers: set[Caller]

    async def invoke(
        self,
        payload: dict,
        deadline_s: float,
        caller: Caller,
    ) -> Result: ...

    async def health(self) -> bool: ...

    @property
    def manifest(self) -> AdapterManifest: ...
