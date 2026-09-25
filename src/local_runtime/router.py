"""Session selection and forwarding for the control plane."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from .errors import RoutingError
from .transport import JsonRpcClient

if TYPE_CHECKING:
    from .registry import InstanceRegistry
    from .types import InstanceRecord, JsonObject, JsonValue


class IdentityVerifier(Protocol):
    """Optional product supplied identity check."""

    def __call__(self, record: InstanceRecord) -> bool:
        """Return whether the opaque identity is still valid."""
        ...


class InstanceRouter:
    """Route a request to one registered session without knowing its domain."""

    def __init__(
        self,
        registry: InstanceRegistry,
        client: JsonRpcClient | None = None,
        verifier: IdentityVerifier | None = None,
    ) -> None:
        """Create a router with injectable transport and identity verification."""
        self.registry = registry
        self.client = client or JsonRpcClient()
        self.verifier = verifier

    def route(
        self,
        method: str,
        params: JsonObject | None = None,
        *,
        timeout: float = 30.0,
    ) -> JsonValue:
        """Select a session, strip ``session_id``, and forward the request."""
        arguments = dict(params or {})
        requested = arguments.pop("session_id", None)
        if requested is not None and not isinstance(requested, str):
            msg = "session_id must be a string"
            raise RoutingError(msg)
        record = self._select(requested)
        if self.verifier is not None and not self.verifier(record):
            msg = "session identity could not be verified"
            raise RoutingError(msg)
        return self.client.request(record.endpoint, method, arguments, timeout=timeout)

    def _select(self, requested: str | None) -> InstanceRecord:
        if requested:
            record = self.registry.get(requested)
            if record is not None:
                return record
            expired = self.registry.expired(requested)
            if expired is not None:
                msg = f"session '{requested}' expired; choose a current session"
                raise RoutingError(msg)
            msg = f"session '{requested}' was not found"
            raise RoutingError(msg)
        records = self.registry.list()
        if len(records) == 1:
            return next(iter(records.values()))
        if not records:
            msg = "no sessions are registered; start a GUI or headless runtime"
            raise RoutingError(msg)
        available = ", ".join(sorted(records))
        msg = f"session_id is required when multiple sessions exist ({available})"
        raise RoutingError(msg)
