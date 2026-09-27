"""Generic GUI host adapter; concrete applications supply the callbacks."""

from __future__ import annotations

from collections.abc import Callable

from .errors import LifecycleError
from .transport import JsonRpcClient, JsonRpcRequestOptions, LocalMcpServer
from .types import JsonObject, JsonValue, Registration, RegistrationDetails

ExecutionGate = Callable[[Callable[[], JsonValue]], JsonValue]


class GuiAdapter:
    """Adapt an interactive host's main-thread callbacks to the shared runtime."""

    def __init__(
        self,
        handler: Callable[[str, JsonObject], JsonValue],
        *,
        details: RegistrationDetails | None = None,
        pid: int | None = None,
        execution_gate: ExecutionGate | None = None,
    ) -> None:
        """
        Create an adapter with an explicit host-thread execution seam.

        Args:
            handler: Process one JSON-RPC method and its object parameters.
            details: Session data to publish after readiness; defaults to a
                GUI label when omitted.
            pid: Optional host process ID for the registry.
            execution_gate: Marshal handler calls onto the host's main thread.

        """
        self._server = LocalMcpServer(
            _json_handler(handler, execution_gate or _direct_execution)
        )
        self._details = details or RegistrationDetails(label="gui")
        self._pid = pid

    def start(self) -> Registration:
        """Start the loopback endpoint and return generic registration data."""
        endpoint = self._server.start(background=True)
        try:
            JsonRpcClient().request(
                endpoint, "ping", options=JsonRpcRequestOptions(timeout=2.0)
            )
        except (ConnectionError, RuntimeError, ValueError, OSError) as exc:
            self._server.stop()
            msg = "GUI endpoint did not become ready"
            raise LifecycleError(msg) from exc
        return Registration(
            mode="gui",
            endpoint=endpoint,
            label=self._details.label,
            capabilities=self._details.capabilities,
            metadata=self._details.metadata,
            identity=self._details.identity,
            pid=self._pid,
        )

    def stop(self) -> None:
        """Stop the endpoint; host lifecycle hooks call this on close."""
        self._server.stop()


def _json_handler(
    handler: Callable[[str, JsonObject], JsonValue],
    execution_gate: ExecutionGate,
) -> Callable[[str, JsonObject], JsonValue]:
    """Marshal transport callbacks through the host's execution gate."""

    def dispatch(method: str, params: JsonObject) -> JsonValue:
        return execution_gate(lambda: handler(method, params))

    return dispatch


def _direct_execution(call: Callable[[], JsonValue]) -> JsonValue:
    """Run a callback directly for hosts without a thread-affinity rule."""
    return call()
