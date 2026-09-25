"""Shared runtime primitives for GUI and headless MCP instances."""

from __future__ import annotations

from .catalog import ToolCatalog, ToolDescriptor
from .gui import GuiAdapter
from .headless import HeadlessSessionManager, WorkerCommandFactory
from .registry import InstanceRegistry
from .router import InstanceRouter
from .runtime import MultiModeRuntime
from .server import ControlPlane
from .transport import JsonRpcClient, LocalMcpServer
from .types import Endpoint, InstanceRecord, JsonObject, JsonValue, Mode, Registration

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
    "JsonValue",
    "LocalMcpServer",
    "Mode",
    "MultiModeRuntime",
    "Registration",
    "ToolCatalog",
    "ToolDescriptor",
    "WorkerCommandFactory",
]

