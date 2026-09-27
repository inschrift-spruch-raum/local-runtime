"""Smoke tests for the public runtime package."""

from __future__ import annotations

import http.client
import json
from typing import TYPE_CHECKING

import pytest

import local_runtime
from local_runtime import (
    Endpoint,
    GuiAdapter,
    JsonRpcClient,
    JsonRpcRequestOptions,
    RegistrationDetails,
)
from local_runtime.transport import LocalMcpServer

if TYPE_CHECKING:
    from local_runtime.types import JsonObject, JsonValue

HTTP_NO_CONTENT = 204


def test_package_imports() -> None:
    """Verify that the public runtime surface imports without a host program."""
    assert "InstanceRegistry" in local_runtime.__all__
    assert local_runtime.Mode is not None


def test_gui_adapter_publishes_registration_details() -> None:
    """Preserve default and adapter supplied GUI registration values."""

    def handler(method: str, params: JsonObject) -> JsonValue:
        del method, params
        return {"ok": True}

    default_gui = GuiAdapter(handler)
    try:
        assert default_gui.start().label == "gui"
    finally:
        default_gui.stop()

    details = RegistrationDetails(
        label="interactive",
        capabilities=("tools",),
        identity={"host": "example"},
        metadata={"source": "adapter"},
    )
    expected_pid = 123
    gui = GuiAdapter(handler, details=details, pid=expected_pid)
    try:
        registration = gui.start()
        assert registration.label == details.label
        assert registration.capabilities == details.capabilities
        assert registration.identity == details.identity
        assert registration.metadata == details.metadata
        assert registration.pid == expected_pid
    finally:
        gui.stop()


def test_json_rpc_client_uses_request_options() -> None:
    """Send a custom request ID and return the handler's result."""

    def handler(method: str, params: JsonObject) -> JsonValue:
        return {"method": method, "params": params}

    server = LocalMcpServer(handler)
    endpoint = server.start()
    try:
        result = JsonRpcClient().request(
            endpoint,
            "echo",
            {"value": 1},
            options=JsonRpcRequestOptions(
                timeout=2.0, request_id="custom-id", max_response_bytes=1024
            ),
        )
        assert result == {"method": "echo", "params": {"value": 1}}
    finally:
        server.stop()


def test_json_rpc_client_rejects_oversized_response() -> None:
    """Apply the configured response limit before decoding truncated JSON."""

    def handler(method: str, params: JsonObject) -> JsonValue:
        del method, params
        return "x" * 128

    server = LocalMcpServer(handler)
    endpoint = server.start()
    try:
        with pytest.raises(ValueError, match="response is too large"):
            JsonRpcClient().request(
                endpoint,
                "large",
                options=JsonRpcRequestOptions(max_response_bytes=16),
            )
    finally:
        server.stop()


def test_json_rpc_client_rejects_bool_request_id() -> None:
    """Reject a bool ID before attempting to contact the endpoint."""
    with pytest.raises(TypeError, match="request id"):
        JsonRpcClient().request(
            Endpoint("127.0.0.1", 1),
            "ping",
            options=JsonRpcRequestOptions(request_id=True),
        )


def test_notification_does_not_emit_a_response() -> None:
    """Verify notifications produce an empty HTTP response without a JSON-RPC body."""

    def handler(method: str, params: JsonObject) -> JsonValue:
        del params
        return {"method": method}

    server = LocalMcpServer(handler)
    endpoint = server.start()
    try:
        body = json.dumps({"jsonrpc": "2.0", "method": "ping"}).encode()
        connection = http.client.HTTPConnection(endpoint.host, endpoint.port, timeout=2)
        try:
            connection.request(
                "POST",
                endpoint.path,
                body=body,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            assert response.status == HTTP_NO_CONTENT
            assert response.read() == b""
        finally:
            connection.close()
    finally:
        server.stop()


def test_null_request_id_is_preserved_in_response() -> None:
    """Verify a JSON-RPC null id is accepted and echoed by the transport."""

    def handler(method: str, params: JsonObject) -> JsonValue:
        del method, params
        return True

    server = LocalMcpServer(handler)
    endpoint = server.start()
    try:
        body = json.dumps({"jsonrpc": "2.0", "id": None, "method": "ping"}).encode()
        connection = http.client.HTTPConnection(endpoint.host, endpoint.port, timeout=2)
        try:
            connection.request(
                "POST",
                endpoint.path,
                body=body,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            assert json.loads(response.read()) == {
                "jsonrpc": "2.0",
                "id": None,
                "result": True,
            }
        finally:
            connection.close()
    finally:
        server.stop()
