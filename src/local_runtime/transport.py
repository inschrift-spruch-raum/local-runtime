"""Local JSON-RPC transport primitives shared by both runtime modes."""

from __future__ import annotations

import http.client
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

from .errors import MultiMcpError
from .types import Endpoint, JsonObject, JsonValue, as_json_object

JsonRpcHandler = Callable[[str, JsonObject], JsonValue]
_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class JsonRpcRequestOptions:
    """Timeout, request ID, and response limit for one client call."""

    timeout: float = 30.0
    request_id: int | str = 1
    max_response_bytes: int = 10 * 1024 * 1024


class JsonRpcClient:
    """Send one JSON-RPC request to a loopback endpoint."""

    def request(
        self,
        endpoint: Endpoint,
        method: str,
        params: JsonObject | None = None,
        *,
        options: JsonRpcRequestOptions | None = None,
    ) -> JsonValue:
        """
        Send a request and return its result, raising on protocol errors.

        Args:
            endpoint: Bound loopback endpoint to contact.
            method: Nonempty JSON-RPC method name.
            params: Object parameters, or an empty object when omitted.
            options: Per-request timeout, ID, and response size limit.

        Returns:
            The JSON-RPC result value.

        """
        options = options or JsonRpcRequestOptions()
        if not method:
            msg = "JSON-RPC method must not be empty"
            raise ValueError(msg)
        if isinstance(options.request_id, bool):
            msg = "JSON-RPC request id must be a string or number"
            raise TypeError(msg)
        payload: JsonObject = {
            "jsonrpc": "2.0",
            "id": options.request_id,
            "method": method,
            "params": params or {},
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        connection = http.client.HTTPConnection(
            endpoint.host, endpoint.port, options.timeout
        )
        try:
            connection.request(
                "POST",
                endpoint.path,
                body=body,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            raw = response.read(options.max_response_bytes + 1)
        except OSError as exc:
            msg = "local MCP endpoint is unavailable"
            raise ConnectionError(msg) from exc
        finally:
            connection.close()
        if len(raw) > options.max_response_bytes:
            msg = "local MCP endpoint response is too large"
            raise ValueError(msg)
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            msg = "local MCP endpoint returned invalid JSON"
            raise ValueError(msg) from exc
        decoded = as_json_object(decoded)
        if decoded.get("jsonrpc") != "2.0":
            msg = "local MCP endpoint returned an invalid JSON-RPC version"
            raise ValueError(msg)
        if decoded.get("id") != options.request_id:
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

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        if handler.path != "/mcp":
            handler.send_error(404)
            return
        if not handler.headers.get("Content-Type", "").startswith("application/json"):
            handler.send_error(415)
            return
        length = _content_length(handler)
        if length is None:
            handler.send_error(400)
            return
        if length < 0 or length > self.max_body_bytes:
            handler.send_error(413)
            return
        request = _decode_request(handler.rfile.read(length))
        response = self._dispatch(request)
        _send_response(handler, response)

    def _dispatch(self, request: JsonObject) -> JsonObject | None:
        try:
            method, params = _request_parts(request)
            result = self.handler(method, params)
            if "id" not in request:
                return None
            return {"jsonrpc": "2.0", "id": request["id"], "result": result}
        except MultiMcpError as exc:
            return _error_response(request, -32000, str(exc))
        except (TypeError, ValueError) as exc:
            return _error_response(request, -32602, str(exc))
        except Exception as exc:
            _logger.exception("Unexpected JSON-RPC handler failure")
            return _error_response(request, -32603, type(exc).__name__)

    def _handler_factory(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            """Bound request handler with no product-specific routes."""

            def do_POST(self) -> None:
                server._handle_post(self)

            def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
                """Keep normal JSON-RPC requests out of stderr."""
                del code, size

        return Handler


def _safe_error(value: object) -> str:
    """Keep remote error text bounded and free of endpoint details."""
    if isinstance(value, dict):
        message = as_json_object(cast("dict[object, object]", value)).get(
            "message", "remote JSON-RPC error"
        )
        return str(message)[:500]
    return "remote JSON-RPC error"


def _content_length(handler: BaseHTTPRequestHandler) -> int | None:
    """Read a request length, returning ``None`` for malformed headers."""
    try:
        return int(handler.headers.get("Content-Length", "-1"))
    except ValueError:
        return None


def _decode_request(raw: bytes) -> JsonObject:
    """Decode one request body, using an empty object for malformed JSON."""
    try:
        return as_json_object(json.loads(raw.decode("utf-8")))
    except UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError:
        return {}


def _send_response(
    handler: BaseHTTPRequestHandler, response: JsonObject | None
) -> None:
    """Write a JSON-RPC response or an empty notification response."""
    if response is None:
        handler.send_response(204)
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return
    body = json.dumps(response, separators=(",", ":")).encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


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
