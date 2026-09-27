"""Shared runtime primitives for GUI and headless MCP instances."""

from __future__ import annotations

from .catalog import ToolCatalog, ToolDescriptor
from .gui import GuiAdapter
from .headless import HeadlessSessionManager, WorkerLauncher
from .registry import InstanceRegistry
from .router import InstanceRouter
from .runtime import MultiModeRuntime
from .server import ControlPlane
from .transport import JsonRpcClient, JsonRpcRequestOptions, LocalMcpServer
from .types import (
    Endpoint,
    InstanceRecord,
    JsonObject,
    JsonValue,
    Mode,
    Registration,
    RegistrationDetails,
)

__all__ = [
    "ControlPlane",
    "Endpoint",
    "GuiAdapter",
    "HeadlessSessionManager",
    "InstanceRecord",
    "InstanceRegistry",
    "InstanceRouter",
    "JsonObject",
    "JsonRpcClient",
    "JsonRpcRequestOptions",
    "JsonValue",
    "LocalMcpServer",
    "Mode",
    "MultiModeRuntime",
    "Registration",
    "RegistrationDetails",
    "ToolCatalog",
    "ToolDescriptor",
    "WorkerLauncher",
]
