"""Local JSON-RPC transport primitives shared by both runtime modes."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast
from urllib.error import URLError
from urllib.request import Request, urlopen

from .errors import MultiMcpError
from .types import Endpoint, JsonObject, JsonValue, as_json_object

JsonRpcHandler = Callable[[str, JsonObject], JsonValue]


class JsonRpcClient:
    """Send one JSON-RPC request to a loopback endpoint."""

    def request(  # noqa: PLR0913
        self,
        endpoint: Endpoint,
        method: str,
        params: JsonObject | None = None,
        *,
        timeout: float = 30.0,
        request_id: int | str = 1,
        max_response_bytes: int = 10 * 1024 * 1024,
    ) -> JsonValue:
        """Send a request and return its result, raising on protocol errors."""
        if not method:
            msg = "JSON-RPC method must not be empty"
            raise ValueError(msg)
        if isinstance(request_id, bool):
            msg = "JSON-RPC request id must be a string or number"
            raise TypeError(msg)
        payload: JsonObject = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = Request(  # noqa: S310
            endpoint.url, data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310
                raw = response.read(max_response_bytes + 1)
        except (OSError, URLError) as exc:
            msg = "local MCP endpoint is unavailable"
            raise ConnectionError(msg) from exc
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            msg = "local MCP endpoint returned invalid JSON"
            raise ValueError(msg) from exc
        if len(raw) > max_response_bytes:
            msg = "local MCP endpoint response is too large"
            raise ValueError(msg)
        decoded = as_json_object(decoded)
        if decoded.get("jsonrpc") != "2.0":
            msg = "local MCP endpoint returned an invalid JSON-RPC version"
            raise ValueError(msg)
        if decoded.get("id") != request_id:
            msg = "local MCP endpoint returned a mismatched request id"
            raise ValueError(msg)
        if "error" in decoded:
            raise RuntimeError(_safe_error(decoded["error"]))
        return decoded.get("result")


class LocalMcpServer:
    """Small loopback HTTP JSON-RPC server for host adapters and workers."""

    def __init__(
        self, handler: JsonRpcHandler, *, max_body_bytes: int = 10 * 1024 * 1024
    ) -> None:
        """Create a loopback server with a bounded request body size."""
        self.handler = handler
        self.max_body_bytes = max_body_bytes
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._endpoint: Endpoint | None = None
        self._lock = threading.Lock()

    @property
    def endpoint(self) -> Endpoint | None:
        """Return the bound endpoint after ``start``."""
        with self._lock:
            if self._server is None:
                return None
            return self._endpoint

    def start(
        self, *, host: str = "127.0.0.1", port: int = 0, background: bool = True
    ) -> Endpoint:
        """Bind the server and return its OS-assigned loopback endpoint."""
        if not background:
            msg = "LocalMcpServer.start requires background=True"
            raise ValueError(msg)
        with self._lock:
            if self._server is not None:
                if self._endpoint is None:
                    msg = "MCP server state is inconsistent"
                    raise RuntimeError(msg)
                return self._endpoint
            if host not in {"127.0.0.1", "localhost"}:
                msg = "MCP servers may only bind to loopback"
                raise ValueError(msg)
            handler_factory = self._handler_factory()
            bind_host = "127.0.0.1" if host == "localhost" else host
            server = ThreadingHTTPServer((bind_host, port), handler_factory)
            endpoint = Endpoint(bind_host, server.server_address[1])
            try:
                self._thread = threading.Thread(
                    target=server.serve_forever, daemon=True
                )
                self._thread.start()
            except BaseException:
                server.server_close()
                self._thread = None
                raise
            self._server = server
            self._endpoint = endpoint
            return endpoint

    def stop(self) -> None:
        """Stop the server; repeated calls are harmless."""
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
            self._endpoint = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)

    def _handler_factory(self) -> type[BaseHTTPRequestHandler]:  # noqa: C901
        server = self

        class Handler(BaseHTTPRequestHandler):
            """Bound request handler with no product-specific routes."""

            def do_POST(self) -> None:
                if self.path != "/mcp":
                    self.send_error(404)
                    return
                content_type = self.headers.get("Content-Type", "")
                if not content_type.startswith("application/json"):
                    self.send_error(415)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                except ValueError:
                    self.send_error(400)
                    return
                if length < 0 or length > server.max_body_bytes:
                    self.send_error(413)
                    return
                request: JsonObject = {}
                response: JsonObject | None = None
                try:
                    request = as_json_object(
                        json.loads(self.rfile.read(length).decode("utf-8"))
                    )
                    method, params = _request_parts(request)
                    result = server.handler(method, params)
                    if "id" in request:
                        response = {
                            "jsonrpc": "2.0",
                            "id": request["id"],
                            "result": result,
                        }
                except MultiMcpError as exc:
                    response = _error_response(request, -32000, str(exc))
                except (TypeError, ValueError) as exc:
                    response = _error_response(request, -32602, str(exc))
                except Exception as exc:  # noqa: BLE001
                    response = _error_response(request, -32603, type(exc).__name__)
                if response is None:
                    self.send_response(204)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = json.dumps(response, separators=(",", ":")).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                del format, args

        return Handler


def _safe_error(value: object) -> str:
    """Keep remote error text bounded and free of endpoint details."""
    if isinstance(value, dict):
        message = as_json_object(cast("dict[object, object]", value)).get(
            "message", "remote JSON-RPC error"
        )
        return str(message)[:500]
    return "remote JSON-RPC error"


def _error_response(request: JsonObject, code: int, message: str) -> JsonObject | None:
    """Create a bounded JSON-RPC error response for a request with an id."""
    if "id" not in request:
        return None
    return {
        "jsonrpc": "2.0",
        "id": request["id"],
        "error": {"code": code, "message": message[:500]},
    }


def _request_parts(request: JsonObject) -> tuple[str, JsonObject]:
    """Validate and extract method parameters from a decoded request."""
    if request.get("jsonrpc") != "2.0":
        msg = "invalid JSON-RPC version"
        raise ValueError(msg)
    method = request.get("method")
    if not isinstance(method, str) or not method:
        msg = "invalid JSON-RPC request"
        raise TypeError(msg)
    request_id = request.get("id")
    if "id" in request and (
        isinstance(request_id, bool)
        or not isinstance(request_id, (int, float, str, type(None)))
    ):
        msg = "invalid JSON-RPC request id"
        raise TypeError(msg)
    return method, as_json_object(request.get("params", {}))
