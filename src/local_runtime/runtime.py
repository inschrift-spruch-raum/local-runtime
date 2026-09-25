"""Runtime lifecycle interfaces and the shared registration owner."""

from __future__ import annotations

import contextlib
import threading
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .registry import InstanceRegistry
    from .types import InstanceRecord, Registration


class RuntimeAdapter(Protocol):
    """A GUI or headless owner that can expose one ready endpoint."""

    def start(self) -> Registration:
        """Start the endpoint and return registration data after readiness."""
        ...

    def stop(self) -> None:
        """Stop the endpoint and release host resources."""
        ...


class MultiModeRuntime:
    """Own one adapter and its registry lease with reverse-order cleanup."""

    def __init__(self, registry: InstanceRegistry, adapter: RuntimeAdapter) -> None:
        """Create a lifecycle owner for one GUI or headless adapter."""
        self.registry = registry
        self.adapter = adapter
        self.record: InstanceRecord | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def start(self, *, heartbeat_interval: float = 60.0) -> InstanceRecord:
        """Start the adapter, publish it only after it reports ready, and lease it."""
        if heartbeat_interval <= 0:
            msg = "heartbeat_interval must be positive"
            raise ValueError(msg)
        with self._lock:
            if self.record is not None:
                return self.record
            registration = self.adapter.start()
            try:
                self.record = self.registry.register(registration)
            except Exception:
                self.adapter.stop()
                raise
            self._stop.clear()
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                args=(heartbeat_interval,),
                daemon=True,
            )
            self._heartbeat_thread.start()
            return self.record

    def stop(self) -> None:
        """Stop heartbeat, release the lease, then stop the adapter."""
        with self._lock:
            record = self.record
            self.record = None
            heartbeat = self._heartbeat_thread
            self._heartbeat_thread = None
            self._stop.set()
        if heartbeat is not None:
            heartbeat.join(timeout=2.0)
        unregister_error: Exception | None = None
        try:
            if record is not None:
                self.registry.unregister(record.session_id, record.lease_id)
        except Exception as exc:  # noqa: BLE001
            unregister_error = exc
        try:
            self.adapter.stop()
        except Exception as exc:  # noqa: BLE001
            if unregister_error is None:
                unregister_error = exc
        if unregister_error is not None:
            raise unregister_error

    def _heartbeat_loop(self, interval: float) -> None:
        """Renew the lease until shutdown or ownership loss."""
        while not self._stop.wait(interval):
            with self._lock:
                record = self.record
            if record is None:
                return
            if self.registry.heartbeat(record.session_id, record.lease_id):
                continue
            with self._lock:
                if self.record is not record:
                    return
                self.record = None
                self._stop.set()
            with contextlib.suppress(Exception):
                self.registry.expire(
                    record.session_id, "lease_lost", lease_id=record.lease_id
                )
            try:
                self.adapter.stop()
            except Exception:  # noqa: BLE001
                return
            return
