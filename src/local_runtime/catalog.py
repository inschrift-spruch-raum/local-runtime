"""Product-neutral capability catalog with atomic refresh semantics."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .errors import MultiMcpError

if TYPE_CHECKING:
    from .types import JsonObject


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    """Description of a tool supplied by a host adapter."""

    name: str
    input_schema: JsonObject
    output_schema: JsonObject | None = None
    capabilities: tuple[str, ...] = ()
    session_id: str | None = None


ToolProvider = Callable[[], Iterable[ToolDescriptor]]


class ToolCatalog:
    """Publish a whole tool snapshot so readers never observe a half-refresh."""

    def __init__(self) -> None:
        """Create an empty capability snapshot."""
        self._tools: dict[str, ToolDescriptor] = {}
        self._lock = threading.Lock()

    def refresh(self, providers: Iterable[ToolProvider]) -> tuple[ToolDescriptor, ...]:
        """Collect providers and atomically replace the current snapshot."""
        replacement: dict[str, ToolDescriptor] = {}
        for provider in providers:
            for descriptor in provider():
                if descriptor.name in replacement:
                    msg = f"duplicate tool name: {descriptor.name}"
                    raise MultiMcpError(msg)
                replacement[descriptor.name] = descriptor
        with self._lock:
            self._tools = replacement
            return tuple(replacement.values())

    def list(self) -> tuple[ToolDescriptor, ...]:
        """Return an immutable snapshot for a tools/list response."""
        with self._lock:
            return tuple(self._tools.values())
