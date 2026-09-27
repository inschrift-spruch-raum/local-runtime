"""Generic subprocess supervision for headless MCP workers."""

from __future__ import annotations

import atexit
import contextlib
import json
import queue
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING, BinaryIO

from .errors import LifecycleError
from .transport import JsonRpcClient, JsonRpcRequestOptions
from .types import Endpoint, InstanceRecord, Registration, RegistrationDetails

if TYPE_CHECKING:
    from .registry import InstanceRegistry

WorkerLauncher = Callable[[str, Endpoint], subprocess.Popen[bytes]]
ReadinessProbe = Callable[[Endpoint, float], bool]
_ENDPOINT_HANDSHAKE = "LOCAL_RUNTIME_ENDPOINT "


class HeadlessSessionManager:
    """Own one isolated worker process per headless session."""

    def __init__(
        self,
        registry: InstanceRegistry,
        worker_launcher: WorkerLauncher,
        *,
        readiness_probe: ReadinessProbe | None = None,
        heartbeat_interval: float = 30.0,
    ) -> None:
        """
        Create a supervisor that delegates process startup to an adapter.

        Args:
            registry: Store for ready worker sessions and their leases.
            worker_launcher: Start a worker with ``stdin=DEVNULL`` and piped
                stdout and stderr, using a trusted executable without a shell.
            readiness_probe: Check whether an announced endpoint is ready.
            heartbeat_interval: Seconds between lease renewals.

        """
        if heartbeat_interval <= 0:
            msg = "heartbeat_interval must be positive"
            raise ValueError(msg)
        self.registry = registry
        self.worker_launcher = worker_launcher
        self.readiness_probe = readiness_probe or _default_readiness_probe
        self.heartbeat_interval = heartbeat_interval
        self._processes: dict[str, tuple[subprocess.Popen[bytes], str]] = {}
        self._lock = threading.Lock()
        self._closed = False
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True
        )
        self._heartbeat_thread.start()
        atexit.register(self.close_all)

    def open(
        self,
        input_ref: str,
        *,
        details: RegistrationDetails | None = None,
        timeout: float = 60.0,
    ) -> InstanceRecord:
        """
        Spawn, await readiness, then publish one headless session.

        Args:
            input_ref: Opaque input reference passed to the worker launcher.
            details: Session data to publish; defaults to a headless label.
            timeout: Maximum seconds for the endpoint handshake and ping.

        Returns:
            The registered record for the ready worker.

        """
        if timeout <= 0:
            msg = "headless worker timeout must be positive"
            raise LifecycleError(msg)
        details = details or RegistrationDetails(label="headless")
        with self._lock:
            self._ensure_open()
        endpoint_hint = Endpoint("127.0.0.1", 0)
        stderr_chunks: deque[bytes] = deque(maxlen=64)
        stdout_chunks: deque[bytes] = deque(maxlen=64)
        try:
            process = self.worker_launcher(input_ref, endpoint_hint)
        except OSError as exc:
            msg = "headless worker could not be started"
            raise LifecycleError(msg) from exc
        if process.stdout is None or process.stderr is None:
            _terminate(process)
            msg = "headless worker launcher must pipe stdout and stderr"
            raise LifecycleError(msg)

        drain = threading.Thread(
            target=_drain_stderr, args=(process, stderr_chunks), daemon=True
        )
        drain.start()
        deadline = time.monotonic() + timeout
        endpoint = _wait_for_endpoint(
            process,
            max(0.0, deadline - time.monotonic()),
            stdout_chunks,
        )
        if endpoint is None or not _wait_ready(
            process,
            endpoint,
            max(0.0, deadline - time.monotonic()),
            self.readiness_probe,
        ):
            _terminate(process)
            drain.join(timeout=1)
            diagnostics = b"".join((*stderr_chunks, *stdout_chunks)).decode(
                errors="replace"
            )[-500:]
            msg = f"headless worker did not become ready: {diagnostics}"
            raise LifecycleError(msg)

        record: InstanceRecord | None = None
        try:
            with self._lock:
                self._ensure_open()
                record = self.registry.register(
                    Registration(
                        mode="headless",
                        endpoint=endpoint,
                        pid=process.pid,
                        label=details.label,
                        capabilities=details.capabilities,
                        identity=details.identity,
                        metadata={"input_ref": input_ref, **details.metadata},
                    )
                )
                self._processes[record.session_id] = (process, record.lease_id)
        except Exception:
            if record is not None:
                self.registry.unregister(record.session_id, record.lease_id)
            _terminate(process)
            raise
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
        with self._lock:
            self._closed = True
            self._heartbeat_stop.set()
        if self._heartbeat_thread is not threading.current_thread():
            self._heartbeat_thread.join(timeout=2)
        with self._lock:
            session_ids = list(self._processes)
        for session_id in session_ids:
            self.close(session_id)

    def _heartbeat_loop(self) -> None:
        """Renew every owned lease while the manager remains alive."""
        while not self._heartbeat_stop.wait(self.heartbeat_interval):
            with self._lock:
                leases = tuple(self._processes.items())
            for session_id, (_process, lease_id) in leases:
                if not self.registry.heartbeat(session_id, lease_id):
                    with self._lock:
                        owned = self._processes.pop(session_id, None)
                    if owned is None:
                        continue
                    with contextlib.suppress(Exception):
                        self.registry.expire(
                            session_id, "lease_lost", lease_id=lease_id
                        )
                    _terminate(owned[0])

    def _ensure_open(self) -> None:
        """Reject new registrations after shutdown has started."""
        if self._closed:
            msg = "headless session manager is closed"
            raise LifecycleError(msg)


def _parse_endpoint_handshake(line: bytes) -> Endpoint | None:
    """Parse the worker's single endpoint announcement line."""
    try:
        text = line.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    if not text.startswith(_ENDPOINT_HANDSHAKE):
        return None
    try:
        return Endpoint.from_json(json.loads(text[len(_ENDPOINT_HANDSHAKE) :]))
    except TypeError, ValueError:
        return None


def _wait_for_endpoint(
    process: subprocess.Popen[bytes],
    timeout: float,
    chunks: deque[bytes],
) -> Endpoint | None:
    """Wait for the worker to announce the endpoint it bound atomically."""
    stdout = process.stdout
    if stdout is None or timeout <= 0:
        return None
    announced: queue.Queue[Endpoint | None] = queue.Queue(maxsize=1)
    reader = threading.Thread(
        target=_consume_stdout, args=(stdout, announced, chunks), daemon=True
    )
    reader.start()
    try:
        return announced.get(timeout=timeout)
    except queue.Empty:
        return None


def _consume_stdout(
    stdout: BinaryIO,
    announced: queue.Queue[Endpoint | None],
    chunks: deque[bytes],
) -> None:
    """Drain worker stdout while extracting its first endpoint announcement."""
    signaled = False
    try:
        for line in stdout:
            if not signaled:
                endpoint = _parse_endpoint_handshake(line)
                if endpoint is not None:
                    announced.put_nowait(endpoint)
                    signaled = True
                    continue
            chunks.append(line)
    except OSError, ValueError:
        pass
    finally:
        if not signaled:
            with contextlib.suppress(queue.Full):
                announced.put_nowait(None)


def _default_readiness_probe(endpoint: Endpoint, timeout: float) -> bool:
    """Use the protocol ping method as the generic readiness handshake."""
    try:
        JsonRpcClient().request(
            endpoint, "ping", options=JsonRpcRequestOptions(timeout=timeout)
        )
    except ConnectionError, RuntimeError, ValueError, OSError:
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
