"""Small, product-neutral value types used by the runtime."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, cast

type JsonScalar = None | bool | int | float | str
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
type JsonObject = dict[str, JsonValue]
type Mode = Literal["gui", "headless"]

MAX_PORT = 65_535


def as_json_object(value: object) -> JsonObject:
    """Validate an arbitrary decoded value as a JSON object."""
    if not isinstance(value, dict):
        msg = "JSON value must be an object"
        raise TypeError(msg)
    raw = cast("dict[object, object]", value)
    result: JsonObject = {}
    for key, item in raw.items():
        if not isinstance(key, str):
            msg = "JSON object keys must be strings"
            raise TypeError(msg)
        result[key] = as_json_value(item)
    return result


def as_json_value(value: object) -> JsonValue:
    """Validate and return a recursively typed JSON value."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            msg = "JSON numbers must be finite"
            raise ValueError(msg)
        return value
    if isinstance(value, list):
        return [as_json_value(item) for item in cast("list[object]", value)]
    if isinstance(value, dict):
        return as_json_object(cast("dict[object, object]", value))
    msg = "value is not JSON serializable"
    raise TypeError(msg)


@dataclass(frozen=True, slots=True)
class Endpoint:
    """
    A local HTTP endpoint or an unbound worker endpoint hint.

    Port ``0`` is reserved for the headless launch handshake. It is never a
    routable endpoint and therefore cannot be persisted in an
    :class:`InstanceRecord`.
    """

    host: str
    port: int
    path: str = "/mcp"

    def __post_init__(self) -> None:
        """Reject endpoints that cannot be used safely by the local router."""
        if self.host not in {"127.0.0.1", "localhost"}:
            msg = "MCP endpoints must use a loopback host"
            raise TypeError(msg)
        if not 0 <= self.port <= MAX_PORT:
            msg = "MCP endpoint port must be between 0 and 65535"
            raise TypeError(msg)
        if not self.path.startswith("/"):
            msg = "MCP endpoint path must start with '/'"
            raise TypeError(msg)

    @property
    def url(self) -> str:
        """Return the endpoint URL for diagnostics and clients."""
        if self.port == 0:
            msg = "an unbound endpoint has no URL"
            raise ValueError(msg)
        return f"http://{self.host}:{self.port}{self.path}"

    def to_json(self) -> JsonObject:
        """Serialize the endpoint without exposing implementation objects."""
        return {"host": self.host, "port": self.port, "path": self.path}

    @classmethod
    def from_json(cls, value: object) -> Endpoint:
        """Build an endpoint from persisted JSON, validating its shape."""
        value = as_json_object(value)
        host = value.get("host")
        port = value.get("port")
        path = value.get("path", "/mcp")
        if (
            not isinstance(host, str)
            or not isinstance(port, int)
            or isinstance(port, bool)
            or port == 0
        ):
            msg = "endpoint host and port have invalid types"
            raise TypeError(msg)
        if not isinstance(path, str):
            msg = "endpoint path has an invalid type"
            raise TypeError(msg)
        return cls(host, port, path)


@dataclass(frozen=True, slots=True)
class Registration:
    """Data supplied by a GUI or headless adapter when it becomes ready."""

    mode: Mode
    endpoint: Endpoint
    pid: int | None = None
    label: str = ""
    capabilities: tuple[str, ...] = ()
    identity: JsonObject | None = None
    metadata: JsonObject = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InstanceRecord:
    """Persisted identity and lease information for one runtime instance."""

    session_id: str
    mode: Mode
    endpoint: Endpoint
    pid: int | None
    label: str
    capabilities: tuple[str, ...]
    identity: JsonObject | None
    metadata: JsonObject
    started_at: datetime
    last_heartbeat: datetime
    lease_id: str

    def to_json(self) -> JsonObject:
        """Serialize the record for the file registry."""
        return {
            "session_id": self.session_id,
            "mode": self.mode,
            "endpoint": self.endpoint.to_json(),
            "pid": self.pid,
            "label": self.label,
            "capabilities": list(self.capabilities),
            "identity": self.identity,
            "metadata": self.metadata,
            "started_at": self.started_at.isoformat(),
            "last_heartbeat": self.last_heartbeat.isoformat(),
            "lease_id": self.lease_id,
        }

    @classmethod
    def from_json(cls, value: object) -> InstanceRecord:
        """Build and validate a record read from disk."""
        value = as_json_object(value)
        session_id = value.get("session_id")
        mode = value.get("mode")
        pid = value.get("pid")
        label = value.get("label", "")
        capabilities = value.get("capabilities", [])
        identity = value.get("identity")
        metadata = value.get("metadata", {})
        lease_id = value.get("lease_id")
        if mode == "gui":
            mode_value: Mode = "gui"
        elif mode == "headless":
            mode_value = "headless"
        else:
            msg = "session mode is invalid"
            raise ValueError(msg)
        if not isinstance(session_id, str) or not session_id:
            msg = "session id is invalid"
            raise ValueError(msg)
        if pid is not None and (
            not isinstance(pid, int) or isinstance(pid, bool) or pid < 0
        ):
            msg = "session pid is invalid"
            raise ValueError(msg)
        if not isinstance(label, str) or not isinstance(capabilities, list):
            msg = "session labels are invalid"
            raise TypeError(msg)
        capability_values: list[str] = []
        for item in capabilities:
            if not isinstance(item, str):
                msg = "session capabilities are invalid"
                raise TypeError(msg)
            capability_values.append(item)
        identity_value = as_json_object(identity) if identity is not None else None
        metadata_value = as_json_object(metadata)
        if not isinstance(lease_id, str) or not lease_id:
            msg = "session metadata or lease is invalid"
            raise TypeError(msg)
        started_raw = value.get("started_at")
        heartbeat_raw = value.get("last_heartbeat")
        if not isinstance(started_raw, str) or not isinstance(heartbeat_raw, str):
            msg = "session timestamps are invalid"
            raise TypeError(msg)
        started_at = _aware_datetime(started_raw)
        last_heartbeat = _aware_datetime(heartbeat_raw)
        return cls(
            session_id=session_id,
            mode=mode_value,
            endpoint=Endpoint.from_json(value.get("endpoint")),
            pid=pid,
            label=label,
            capabilities=tuple(capability_values),
            identity=identity_value,
            metadata=metadata_value,
            started_at=started_at,
            last_heartbeat=last_heartbeat,
            lease_id=lease_id,
        )


def _aware_datetime(value: str) -> datetime:
    """Parse a persisted timestamp and require explicit timezone information."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        msg = "session timestamps must include a timezone"
        raise ValueError(msg)
    return parsed
