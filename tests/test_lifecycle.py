"""Contract tests for runtime ownership and headless startup."""

from __future__ import annotations

import io
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

from local_runtime import HeadlessSessionManager, InstanceRegistry, MultiModeRuntime
from local_runtime.errors import LifecycleError
from local_runtime.headless import (
    _parse_endpoint_handshake,  # pyright: ignore[reportPrivateUsage]
)
from local_runtime.types import Endpoint, InstanceRecord, Registration

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Sequence
    from pathlib import Path


def _record(*, session_id: str = "session") -> InstanceRecord:
    """Build a small valid record for lifecycle tests."""
    now = datetime.now(UTC)
    return InstanceRecord(
        session_id=session_id,
        mode="headless",
        endpoint=Endpoint("127.0.0.1", 12345),
        pid=123,
        label="worker",
        capabilities=(),
        identity=None,
        metadata={},
        started_at=now,
        last_heartbeat=now,
        lease_id="lease",
    )


class _HeartbeatRegistry:
    """Minimal registry double for a lease-loss callback test."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.expire_calls: list[tuple[str, str, str | None]] = []

    def register(self, registration: Registration) -> InstanceRecord:
        del registration
        return _record()

    def unregister(self, session_id: str, lease_id: str | None = None) -> bool:
        del session_id, lease_id
        return True

    def heartbeat(self, session_id: str, lease_id: str) -> bool:
        del session_id, lease_id
        return False

    def expire(self, session_id: str, reason: str, lease_id: str | None = None) -> bool:
        self.events.append("expire")
        self.expire_calls.append((session_id, reason, lease_id))
        return True


class _Adapter:
    """Minimal adapter double that records cleanup."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.stop_calls = 0

    def start(self) -> Registration:
        """Return a ready registration for the lifecycle owner."""
        return Registration(mode="headless", endpoint=Endpoint("127.0.0.1", 12345))

    def stop(self) -> None:
        self.events.append("stop")
        self.stop_calls += 1


class _BlockingRegistry:
    """Registry double that pauses publication until shutdown is queued."""

    def __init__(self, record: InstanceRecord) -> None:
        self.record = record
        self.entered = threading.Event()
        self.release = threading.Event()
        self.unregister_calls: list[tuple[str, str | None]] = []

    def register(self, registration: Registration) -> InstanceRecord:
        del registration
        self.entered.set()
        assert self.release.wait(timeout=1)
        return self.record

    def unregister(self, session_id: str, lease_id: str | None = None) -> bool:
        self.unregister_calls.append((session_id, lease_id))
        return True

    def heartbeat(self, session_id: str, lease_id: str) -> bool:
        del session_id, lease_id
        return True

    def expire(self, session_id: str, reason: str, lease_id: str | None = None) -> bool:
        del session_id, reason, lease_id
        return False


class _HeadlessHeartbeatRegistry:
    """Registry double that reports a lost headless lease."""

    def __init__(self) -> None:
        self.expire_calls: list[tuple[str, str, str | None]] = []

    def heartbeat(self, session_id: str, lease_id: str) -> bool:
        del session_id, lease_id
        return False

    def expire(self, session_id: str, reason: str, lease_id: str | None = None) -> bool:
        self.expire_calls.append((session_id, reason, lease_id))
        return True


class _OneShotStop:
    """Stop event double that lets the heartbeat loop execute once."""

    def __init__(self) -> None:
        self.calls = 0

    def wait(self, timeout: float) -> bool:
        del timeout
        self.calls += 1
        return self.calls > 1


class _Process:
    """Small process double for the registration race test."""

    pid = 123

    def __init__(self) -> None:
        self.stdout = io.BytesIO(
            b'LOCAL_RUNTIME_ENDPOINT {"host":"127.0.0.1","port":43210,"path":"/mcp"}\n'
        )
        self.stderr = io.BytesIO()
        self.terminated = False

    def poll(self) -> int | None:
        return None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0

    def kill(self) -> None:
        return None


def _patch_headless_start(monkeypatch: pytest.MonkeyPatch, process: _Process) -> None:
    """Patch process startup and readiness for a deterministic race test."""

    def fake_popen(*_args: object, **_kwargs: object) -> _Process:
        return process

    def fake_wait_ready(*_args: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr("local_runtime.headless.subprocess.Popen", fake_popen)
    monkeypatch.setattr("local_runtime.headless._wait_ready", fake_wait_ready)


def _command_factory(input_ref: str, endpoint: Endpoint) -> Sequence[str]:
    """Build a minimal worker command for manager tests."""
    assert endpoint.port == 0
    return ("worker", input_ref, str(endpoint.port))


def test_registry_rejects_unresolved_worker_endpoint(tmp_path: Path) -> None:
    """Only a worker's announced, nonzero port may enter the registry."""
    registry = InstanceRegistry(tmp_path / "sessions.json")
    with pytest.raises(ValueError, match="bound port"):
        registry.register(
            Registration(mode="headless", endpoint=Endpoint("127.0.0.1", 0))
        )


def test_registry_expire_requires_the_current_lease(tmp_path: Path) -> None:
    """A stale owner cannot expire a replacement session."""
    registry = InstanceRegistry(tmp_path / "sessions.json")
    record = registry.register(
        Registration(mode="headless", endpoint=Endpoint("127.0.0.1", 43210))
    )

    assert not registry.expire(record.session_id, "lease_lost", "old-lease")
    assert registry.get(record.session_id) == record
    assert registry.expire(record.session_id, "lease_lost", record.lease_id)
    assert registry.get(record.session_id) is None
    expired = registry.expired(record.session_id)
    assert expired is not None
    assert expired["record"] == record.to_json()
    assert expired["reason"] == "lease_lost"
    assert isinstance(expired["expired_at"], str)


def test_lease_loss_expires_history_before_stopping_adapter() -> None:
    """A lost lease leaves a replacement-aware expired record."""
    events: list[str] = []
    registry = _HeartbeatRegistry(events)
    adapter = _Adapter(events)
    runtime = MultiModeRuntime(cast("InstanceRegistry", registry), adapter)
    runtime.start(heartbeat_interval=0.001)
    for _ in range(1000):
        if adapter.stop_calls:
            break
        threading.Event().wait(0.001)

    assert registry.expire_calls == [("session", "lease_lost", "lease")]
    assert events == ["expire", "stop"]
    assert adapter.stop_calls == 1
    assert runtime.record is None


def test_endpoint_handshake_returns_the_worker_bound_endpoint() -> None:
    """The startup handshake parses a real endpoint instead of a guessed port."""
    line = b'LOCAL_RUNTIME_ENDPOINT {"host":"127.0.0.1","port":43210,"path":"/mcp"}\n'
    assert _parse_endpoint_handshake(line) == Endpoint("127.0.0.1", 43210)
    assert _parse_endpoint_handshake(b"worker log\n") is None


def test_headless_lease_loss_expires_history_and_reaps_worker() -> None:
    """A headless lease loss expires the record before killing its worker."""
    registry = _HeadlessHeartbeatRegistry()
    manager = HeadlessSessionManager(
        cast("InstanceRegistry", registry), _command_factory
    )
    manager.close_all()
    process = _Process()
    with manager._lock:  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        manager._processes["session"] = (  # noqa: SLF001  # pyright: ignore[reportPrivateUsage, reportArgumentType]
            cast("subprocess.Popen[bytes]", process),
            "lease",
        )
    manager._heartbeat_stop = cast(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        "threading.Event", _OneShotStop()
    )

    manager._heartbeat_loop()  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    manager._heartbeat_stop = threading.Event()  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]

    assert registry.expire_calls == [("session", "lease_lost", "lease")]
    assert process.terminated


def test_headless_close_all_cannot_miss_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing during registry publication still reaps the newly owned worker."""
    record = _record()
    process = _Process()
    registry = _BlockingRegistry(record)
    _patch_headless_start(monkeypatch, process)

    manager = HeadlessSessionManager(
        cast("InstanceRegistry", registry),
        _command_factory,
    )
    opened: list[InstanceRecord] = []
    open_error: list[LifecycleError] = []

    def open_worker() -> None:
        try:
            opened.append(manager.open("input"))
        except LifecycleError as exc:  # pragma: no cover - assertion aid
            open_error.append(exc)

    opening = threading.Thread(target=open_worker)
    opening.start()
    assert registry.entered.wait(timeout=1)
    closing = threading.Thread(target=manager.close_all)
    closing.start()
    registry.release.set()
    opening.join(timeout=1)
    closing.join(timeout=1)

    assert not open_error
    assert opened == [record]
    assert not opening.is_alive()
    assert not closing.is_alive()
    assert registry.unregister_calls == [(record.session_id, record.lease_id)]
    assert process.terminated


def test_headless_open_rejects_manager_after_close() -> None:
    """A manager that has begun shutdown cannot create an orphan worker."""

    def command_factory(input_ref: str, endpoint: Endpoint) -> Sequence[str]:
        return ("worker", input_ref, str(endpoint.port))

    manager = HeadlessSessionManager(
        cast("InstanceRegistry", object()), command_factory
    )
    manager.close_all()

    with pytest.raises(LifecycleError, match="closed"):
        manager.open("input")
