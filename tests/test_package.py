"""Smoke tests for the public runtime package."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from urllib.request import Request, urlopen

import local_runtime
from local_runtime.transport import LocalMcpServer

if TYPE_CHECKING:
    from local_runtime.types import JsonObject, JsonValue

HTTP_NO_CONTENT = 204


def test_package_imports() -> None:
    """Verify that the public runtime surface imports without a host program."""
    assert "InstanceRegistry" in local_runtime.__all__
    assert local_runtime.Mode is not None


def test_notification_does_not_emit_a_response() -> None:
    """Verify notifications produce an empty HTTP response without a JSON-RPC body."""

    def handler(method: str, params: JsonObject) -> JsonValue:
        del params
        return {"method": method}

    server = LocalMcpServer(handler)
    endpoint = server.start()
    try:
        body = json.dumps({"jsonrpc": "2.0", "method": "ping"}).encode()
        request = Request(  # noqa: S310
            endpoint.url,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=2) as response:  # noqa: S310
            assert response.status == HTTP_NO_CONTENT
            assert response.read() == b""
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
        request = Request(  # noqa: S310
            endpoint.url,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=2) as response:  # noqa: S310
            assert json.loads(response.read()) == {
                "jsonrpc": "2.0",
                "id": None,
                "result": True,
            }
    finally:
        server.stop()
