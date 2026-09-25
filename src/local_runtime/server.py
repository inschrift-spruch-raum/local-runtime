"""Product-neutral control-plane dispatcher for MCP-style JSON-RPC calls."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .catalog import ToolCatalog, ToolDescriptor

if TYPE_CHECKING:
    from collections.abc import Callable

    from .headless import HeadlessSessionManager
    from .registry import InstanceRegistry
    from .router import InstanceRouter
    from .types import InstanceRecord, JsonObject, JsonValue


class ControlPlane:
    """Expose management and proxied tool calls behind one JSON-RPC handler."""

    def __init__(
        self,
        registry: InstanceRegistry,
        router: InstanceRouter,
        *,
        catalog: ToolCatalog | None = None,
        headless: HeadlessSessionManager | None = None,
        local_methods: dict[str, Callable[[JsonObject], JsonValue]] | None = None,
    ) -> None:
        """Create a control plane with local management and remote routing."""
        self.registry = registry
        self.router = router
        self.catalog = catalog or ToolCatalog()
        self.headless = headless
        self.local_methods = local_methods or {}

    def dispatch(self, method: str, params: JsonObject) -> JsonValue:
        """Dispatch management methods locally and all other methods remotely."""
        if method == "ping":
            return {"ok": True}
        if method.startswith("sessions/"):
            return self._dispatch_session(method, params)
        if method == "tools/list":
            return {"tools": [_tool_json(tool) for tool in self.catalog.list()]}
        if method in self.local_methods:
            return self.local_methods[method](params)
        if method == "tools/call":
            return self._dispatch_tool_call(params)
        return self.router.route(method, params)

    def _dispatch_session(self, method: str, params: JsonObject) -> JsonValue:
        """Dispatch lifecycle operations kept local to the control plane."""
        if method == "sessions/list":
            return {
                "sessions": [
                    _record_json(record) for record in self.registry.list().values()
                ]
            }
        if method == "sessions/cleanup":
            return {"expired": list(self.registry.cleanup_stale())}
        if method == "sessions/open":
            if self.headless is None:
                msg = "headless sessions are not configured"
                raise RuntimeError(msg)
            input_ref = params.get("input_ref")
            if not isinstance(input_ref, str) or not input_ref:
                msg = "input_ref is required"
                raise ValueError(msg)
            record = self.headless.open(
                input_ref, label=str(params.get("label", "headless"))
            )
            return _record_json(record)
        if method == "sessions/close":
            session_id = params.get("session_id")
            if not isinstance(session_id, str):
                msg = "session_id is required"
                raise ValueError(msg)
            if self.headless is None or not self.headless.close(session_id):
                msg = "session is not owned by the headless manager"
                raise ValueError(msg)
            return {"ok": True}
        msg = f"unsupported session method: {method}"
        raise ValueError(msg)

    def _dispatch_tool_call(self, params: JsonObject) -> JsonValue:
        """Validate the control-plane tool envelope before routing it."""
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(tool_name, str) or not isinstance(arguments, dict):
            msg = "tools/call requires name and object arguments"
            raise TypeError(msg)
        return self.router.route(tool_name, arguments)


def _record_json(record: InstanceRecord) -> JsonObject:
    """Keep management output entirely JSON serializable."""
    return record.to_json()


def _tool_json(tool: ToolDescriptor) -> JsonObject:
    """Serialize a capability descriptor."""
    return {
        "name": tool.name,
        "inputSchema": tool.input_schema,
        "outputSchema": tool.output_schema,
        "capabilities": list(tool.capabilities),
        "session_id": tool.session_id,
    }
