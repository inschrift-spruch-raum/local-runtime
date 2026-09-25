"""Generic subprocess supervision for headless MCP workers."""

from __future__ import annotations

import atexit
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from .errors import LifecycleError
from .transport import JsonRpcClient
from .types import Endpoint, InstanceRecord, JsonObject, Registration

if TYPE_CHECKING:
    from .registry import InstanceRegistry

WorkerCommandFactory = Callable[[str, Endpoint], Sequence[str]]
ReadinessProbe = Callable[[Endpoint, float], bool]


class HeadlessSessionManager:
    """Own one isolated worker process per headless session."""

    def __init__(
        self,
        registry: InstanceRegistry,
        command_factory: WorkerCommandFactory,
        *,
        readiness_probe: ReadinessProbe | None = None,
    ) -> None:
        """Create a supervisor that delegates worker construction to a factory."""
        self.registry = registry
        self.command_factory = command_factory
        self.readiness_probe = readiness_probe or _default_readiness_probe
        self._processes: dict[str, tuple[subprocess.Popen[bytes], str]] = {}
        self._lock = threading.Lock()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True
        )
        self._heartbeat_thread.start()
        atexit.register(self.close_all)

    def open(  # noqa: PLR0913
        self,
        input_ref: str,
        *,
        label: str = "headless",
        capabilities: tuple[str, ...] = (),
        identity: JsonObject | None = None,
        metadata: JsonObject | None = None,
        timeout: float = 60.0,
    ) -> InstanceRecord:
        """Spawn, await readiness, then publish one headless session."""
        endpoint = Endpoint("127.0.0.1", _free_port())
        command = list(self.command_factory(input_ref, endpoint))
        if not command:
            msg = "worker command factory returned an empty command"
            raise LifecycleError(msg)
        stderr_chunks: deque[bytes] = deque(maxlen=64)
        try:
            process = subprocess.Popen(  # noqa: S603
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=_creation_flags(),
            )
        except OSError as exc:
            msg = "headless worker could not be started"
            raise LifecycleError(msg) from exc

        drain = threading.Thread(
            target=_drain_stderr, args=(process, stderr_chunks), daemon=True
        )
        drain.start()
        if timeout <= 0 or not _wait_ready(
            process, endpoint, timeout, self.readiness_probe
        ):
            _terminate(process)
            drain.join(timeout=1)
            details = b"".join(stderr_chunks).decode(errors="replace")[-500:]
            msg = f"headless worker did not become ready: {details}"
            raise LifecycleError(msg)

        try:
            record = self.registry.register(
                Registration(
                    mode="headless",
                    endpoint=endpoint,
                    pid=process.pid,
                    label=label,
                    capabilities=capabilities,
                    identity=identity,
                    metadata={"input_ref": input_ref, **(metadata or {})},
                )
            )
        except Exception:
            _terminate(process)
            raise
        with self._lock:
            self._processes[record.session_id] = (process, record.lease_id)
        return record

    def close(self, session_id: str) -> bool:
        """Stop one managed process and release its registry lease."""
        with self._lock:
            owned = self._processes.pop(session_id, None)
        if owned is None:
            return False
        _terminate(owned[0])
        return self.registry.unregister(session_id, owned[1])

    def close_all(self) -> None:
        """Best-effort cleanup used during interpreter shutdown."""
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not threading.current_thread():
            self._heartbeat_thread.join(timeout=2)
        with self._lock:
            session_ids = list(self._processes)
        for session_id in session_ids:
            self.close(session_id)

    def _heartbeat_loop(self) -> None:
        """Renew every owned lease while the manager remains alive."""
        while not self._heartbeat_stop.wait(30.0):
            with self._lock:
                leases = tuple(self._processes.items())
            for session_id, (_process, lease_id) in leases:
                if not self.registry.heartbeat(session_id, lease_id):
                    with self._lock:
                        owned = self._processes.pop(session_id, None)
                    if owned is not None:
                        _terminate(owned[0])


def _free_port() -> int:
    """Ask the OS for an available local port."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _default_readiness_probe(endpoint: Endpoint, timeout: float) -> bool:
    """Use the protocol ping method as the generic readiness handshake."""
    try:
        JsonRpcClient().request(endpoint, "ping", timeout=timeout)
    except (ConnectionError, RuntimeError, ValueError, OSError):
        return False
    return True


def _wait_ready(
    process: subprocess.Popen[bytes],
    endpoint: Endpoint,
    timeout: float,
    probe: ReadinessProbe,
) -> bool:
    """Poll readiness without confusing a busy worker with a dead one."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if probe(endpoint, min(2.0, max(0.1, deadline - time.monotonic()))):
            return True
        time.sleep(0.25)
    return False


def _drain_stderr(process: subprocess.Popen[bytes], chunks: deque[bytes]) -> None:
    """Consume bounded worker diagnostics so the child cannot block on stderr."""
    stderr = process.stderr
    if stderr is None:
        return
    try:
        for chunk in iter(lambda: stderr.read(4096), b""):
            chunks.append(chunk)
    except OSError:
        return


def _creation_flags() -> int:
    """Create a detached process group on Windows when available."""
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    return 0


def _terminate(process: subprocess.Popen[bytes]) -> None:
    """Prefer graceful termination, then bound the fallback."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
