"""Contract tests for runtime ownership and headless startup."""

from __future__ import annotations

import io
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

from local_runtime import (
    ControlPlane,
    HeadlessSessionManager,
    InstanceRegistry,
    InstanceRouter,
    MultiModeRuntime,
    RegistrationDetails,
    WorkerLauncher,
)
from local_runtime.errors import LifecycleError
from local_runtime.types import Endpoint, InstanceRecord, Registration

if TYPE_CHECKING:
    from pathlib import Path
    from subprocess import Popen


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
        self.record = _record()

    def register(self, registration: Registration) -> InstanceRecord:
        del registration
        return self.record

    def unregister(self, session_id: str, lease_id: str | None = None) -> bool:
        del session_id, lease_id
        return True

    def heartbeat(self, session_id: str, lease_id: str) -> bool:
        del session_id, lease_id
        return False

    def expire(self, session_id: str, reason: str, lease_id: str | None = None) -> bool:
        self.expire_calls.append((session_id, reason, lease_id))
        return True


class _Process:
    """Small process double for the registration race test."""

    pid = 123

    def __init__(self, *, stdout_pipe: bool = True, stderr_pipe: bool = True) -> None:
        self.stdout: io.BytesIO | None = (
            io.BytesIO(
                b"LOCAL_RUNTIME_ENDPOINT "
                b'{"host":"127.0.0.1","port":43210,"path":"/mcp"}\n'
            )
            if stdout_pipe
            else None
        )
        self.stderr: io.BytesIO | None = io.BytesIO() if stderr_pipe else None
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


def _worker_launcher(process: _Process) -> WorkerLauncher:
    """Return a worker process double through the host adapter seam."""

    def launch(input_ref: str, endpoint: Endpoint) -> Popen[bytes]:
        del input_ref
        assert endpoint.port == 0
        return cast("Popen[bytes]", process)

    return launch


def _ready(*_args: object) -> bool:
    """Report readiness for the isolated headless lifecycle test."""
    return True


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


def test_headless_lease_loss_expires_history_and_reaps_worker() -> None:
    """A headless lease loss expires the record before killing its worker."""
    registry = _HeadlessHeartbeatRegistry()
    process = _Process()
    manager = HeadlessSessionManager(
        cast("InstanceRegistry", registry),
        _worker_launcher(process),
        readiness_probe=_ready,
        heartbeat_interval=0.001,
    )
    manager.open("input")
    for _ in range(1000):
        if process.terminated:
            break
        threading.Event().wait(0.001)
    manager.close_all()

    assert registry.expire_calls == [("session", "lease_lost", "lease")]
    assert process.terminated


def test_headless_launcher_receives_input_and_endpoint_hint(tmp_path: Path) -> None:
    """Pass the opaque input reference and port-zero hint to the adapter."""
    launched: list[tuple[str, Endpoint]] = []
    process = _Process()

    def launch(input_ref: str, endpoint: Endpoint) -> Popen[bytes]:
        launched.append((input_ref, endpoint))
        return cast("Popen[bytes]", process)

    manager = HeadlessSessionManager(
        InstanceRegistry(tmp_path / "sessions.json"),
        launch,
        readiness_probe=_ready,
    )
    input_ref = "input with spaces; $(echo injected)"
    try:
        record = manager.open(input_ref)
        assert launched == [(input_ref, Endpoint("127.0.0.1", 0))]
        assert record.label == "headless"
    finally:
        manager.close_all()


def test_headless_open_publishes_registration_details(tmp_path: Path) -> None:
    """Use adapter supplied registration details for a ready worker."""
    registry = InstanceRegistry(tmp_path / "sessions.json")
    manager = HeadlessSessionManager(
        registry, _worker_launcher(_Process()), readiness_probe=_ready
    )
    details = RegistrationDetails(
        label="batch",
        capabilities=("analyze",),
        identity={"owner": "host"},
        metadata={"source": "adapter"},
    )
    try:
        record = manager.open("input", details=details)
        assert record.label == details.label
        assert record.capabilities == details.capabilities
        assert record.identity == details.identity
        assert record.metadata == {"input_ref": "input", **details.metadata}
    finally:
        manager.close_all()


def test_control_plane_open_forwards_session_label(tmp_path: Path) -> None:
    """Keep the control-plane label when opening a headless session."""
    registry = InstanceRegistry(tmp_path / "sessions.json")
    manager = HeadlessSessionManager(
        registry, _worker_launcher(_Process()), readiness_probe=_ready
    )
    control = ControlPlane(registry, InstanceRouter(registry), headless=manager)
    try:
        result = control.dispatch(
            "sessions/open", {"input_ref": "input", "label": "from-control"}
        )
        assert isinstance(result, dict)
        assert result["label"] == "from-control"
    finally:
        manager.close_all()


@pytest.mark.parametrize(("stdout_pipe", "stderr_pipe"), [(False, True), (True, False)])
def test_headless_launcher_requires_output_pipes(
    tmp_path: Path, *, stdout_pipe: bool, stderr_pipe: bool
) -> None:
    """Reject and reap a worker that cannot be supervised through pipes."""
    registry = InstanceRegistry(tmp_path / "sessions.json")
    process = _Process(stdout_pipe=stdout_pipe, stderr_pipe=stderr_pipe)
    manager = HeadlessSessionManager(registry, _worker_launcher(process))
    try:
        with pytest.raises(LifecycleError, match="pipe stdout and stderr"):
            manager.open("input")
        assert process.terminated
        assert registry.list() == {}
    finally:
        manager.close_all()


def test_headless_launcher_failure_does_not_register(tmp_path: Path) -> None:
    """Translate a host startup failure without publishing a session."""
    registry = InstanceRegistry(tmp_path / "sessions.json")

    def failed_launch(input_ref: str, endpoint: Endpoint) -> Popen[bytes]:
        del input_ref, endpoint
        msg = "worker executable missing"
        raise OSError(msg)

    manager = HeadlessSessionManager(registry, failed_launch)
    try:
        with pytest.raises(LifecycleError, match="could not be started"):
            manager.open("input")
        assert registry.list() == {}
    finally:
        manager.close_all()


def test_headless_close_all_cannot_miss_registration() -> None:
    """Closing during registry publication still reaps the newly owned worker."""
    record = _record()
    process = _Process()
    registry = _BlockingRegistry(record)

    manager = HeadlessSessionManager(
        cast("InstanceRegistry", registry),
        _worker_launcher(process),
        readiness_probe=_ready,
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
    manager = HeadlessSessionManager(
        cast("InstanceRegistry", object()), _worker_launcher(_Process())
    )
    manager.close_all()

    with pytest.raises(LifecycleError, match="closed"):
        manager.open("input")
