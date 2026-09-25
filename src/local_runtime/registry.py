"""Durable, loopback-only registry shared by GUI and headless runtimes."""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import shutil
import tempfile
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from .filelock import FileLock
from .types import InstanceRecord, JsonObject, Registration, as_json_object


def default_registry_path() -> Path:
    """Return the registry path, with an environment override for tests."""
    configured = os.environ.get("LOCAL_RUNTIME_REGISTRY_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local-runtime" / "sessions.json"


class InstanceRegistry:
    """
    Store active and expired session records in an atomic JSON file.

    The registry is deliberately unaware of the hosted program. ``identity``
    and ``metadata`` are opaque JSON supplied by the adapter.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        """Create a registry backed by ``path`` or the default user path."""
        self.path = (
            Path(path).expanduser() if path is not None else default_registry_path()
        )
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def register(self, registration: Registration) -> InstanceRecord:
        """Publish a ready endpoint and return its lease-bearing record."""
        now = _now()
        with FileLock(str(self.lock_path)):
            data = self._read()
            sessions = _sessions(data)
            session_id = self._new_id(sessions)
            record = InstanceRecord(
                session_id=session_id,
                mode=registration.mode,
                endpoint=registration.endpoint,
                pid=registration.pid,
                label=registration.label,
                capabilities=registration.capabilities,
                identity=deepcopy(registration.identity),
                metadata=deepcopy(registration.metadata),
                started_at=now,
                last_heartbeat=now,
                lease_id=secrets.token_urlsafe(18),
            )
            sessions[session_id] = record.to_json()
            active_session = data.get("active_session")
            if not isinstance(active_session, str) or active_session not in sessions:
                data["active_session"] = session_id
            self._write(data)
            return record

    def get(self, session_id: str) -> InstanceRecord | None:
        """Read one active record, returning ``None`` when it is absent."""
        with FileLock(str(self.lock_path)):
            value = _sessions(self._read()).get(session_id)
        return _record_or_none(value)

    def list(self) -> dict[str, InstanceRecord]:
        """Return a detached snapshot of all active records."""
        with FileLock(str(self.lock_path)):
            values = _sessions(self._read())
        return {
            session_id: record
            for session_id, raw in values.items()
            if (record := _record_or_none(raw)) is not None
        }

    def active(self) -> InstanceRecord | None:
        """Return the preferred active session, if one is recorded."""
        with FileLock(str(self.lock_path)):
            data = self._read()
            session_id = data.get("active_session")
            sessions = _sessions(data)
            value = sessions.get(session_id) if isinstance(session_id, str) else None
        return _record_or_none(value)

    def heartbeat(self, session_id: str, lease_id: str) -> bool:
        """Renew a lease, refusing stale owners after id reuse."""
        with FileLock(str(self.lock_path)):
            data = self._read()
            sessions = _sessions(data)
            value = sessions.get(session_id)
            record = _record_or_none(value)
            if record is None or record.lease_id != lease_id:
                return False
            updated = record.to_json()
            updated["last_heartbeat"] = _now().isoformat()
            sessions[session_id] = updated
            self._write(data)
            return True

    def unregister(self, session_id: str, lease_id: str | None = None) -> bool:
        """Remove a session only when its lease still owns it."""
        with FileLock(str(self.lock_path)):
            data = self._read()
            sessions = _sessions(data)
            value = sessions.get(session_id)
            record = _record_or_none(value)
            if record is None or lease_id is None or record.lease_id != lease_id:
                return False
            del sessions[session_id]
            if data.get("active_session") == session_id:
                data["active_session"] = next(iter(sessions), None)
            self._write(data)
            return True

    def expire(self, session_id: str, reason: str) -> bool:
        """Move an active session to the bounded expired history."""
        with FileLock(str(self.lock_path)):
            data = self._read()
            sessions = _sessions(data)
            value = sessions.pop(session_id, None)
            if not isinstance(value, dict):
                return False
            expired = _expired(data)
            expired[session_id] = {
                "record": value,
                "reason": reason,
                "expired_at": _now().isoformat(),
            }
            if data.get("active_session") == session_id:
                data["active_session"] = next(iter(sessions), None)
            self._trim_expired(expired)
            self._write(data)
            return True

    def expired(self, session_id: str) -> JsonObject | None:
        """Return opaque expired metadata for replacement-aware errors."""
        with FileLock(str(self.lock_path)):
            value = _expired(self._read()).get(session_id)
            if not isinstance(value, dict):
                return None
            return as_json_object(cast("dict[object, object]", value))

    def cleanup_stale(self, max_age: timedelta = timedelta(minutes=2)) -> list[str]:
        """Expire sessions whose heartbeat lease is older than ``max_age``."""
        cutoff = _now() - max_age
        with FileLock(str(self.lock_path)):
            data = self._read()
            stale: list[str] = []
            sessions = _sessions(data)
            expired = _expired(data)
            for session_id, raw in list(sessions.items()):
                record = _record_or_none(raw)
                if record is not None and record.last_heartbeat < cutoff:
                    stale.append(session_id)
                    del sessions[session_id]
                    expired[session_id] = {
                        "record": raw,
                        "reason": "stale_heartbeat",
                        "expired_at": _now().isoformat(),
                    }
            if data.get("active_session") in stale:
                data["active_session"] = next(iter(sessions), None)
            self._trim_expired(expired)
            if stale:
                self._write(data)
            return stale

    def _new_id(self, sessions: dict[str, object]) -> str:
        """Generate an opaque collision-resistant session id."""
        while True:
            session_id = secrets.token_hex(4)
            if session_id not in sessions:
                return session_id

    def _read(self) -> dict[str, object]:
        """Read and validate the registry, quarantining corrupted JSON."""
        if not self.path.exists():
            return _empty_store()
        try:
            with self.path.open(encoding="utf-8") as stream:
                value = as_json_object(json.load(stream))
            sessions, expired = _collections(value)
            valid = {
                key: raw
                for key, raw in sessions.items()
                if _record_or_none(raw) is not None
            }
            return {
                "sessions": valid,
                "active_session": value.get("active_session"),
                "expired": expired,
            }
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            self._quarantine()
            return _empty_store()

    def _write(self, data: dict[str, object]) -> None:
        """Atomically publish a registry snapshot."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            Path(temp_name).replace(self.path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                Path(temp_name).unlink()

    def _quarantine(self) -> None:
        """Move malformed state aside so a bad write cannot brick startup."""
        if self.path.exists():
            with contextlib.suppress(OSError):
                shutil.move(
                    self.path, self.path.with_suffix(self.path.suffix + ".corrupt")
                )

    @staticmethod
    def _trim_expired(expired: dict[str, object], limit: int = 64) -> None:
        """Keep expired history bounded by insertion order."""
        while len(expired) > limit:
            expired.pop(next(iter(expired)))


def _empty_store() -> dict[str, object]:
    """Return the canonical on-disk shape."""
    return {"sessions": {}, "active_session": None, "expired": {}}


def _collections(value: JsonObject) -> tuple[dict[str, object], dict[str, object]]:
    """Validate the active and expired collection shapes from disk."""
    sessions = value.get("sessions", {})
    expired = value.get("expired", {})
    if not isinstance(sessions, dict) or not isinstance(expired, dict):
        msg = "registry collections are invalid"
        raise TypeError(msg)
    return cast("dict[str, object]", sessions), cast("dict[str, object]", expired)


def _now() -> datetime:
    """Return an aware UTC timestamp."""
    return datetime.now(UTC)


def _record_or_none(value: object) -> InstanceRecord | None:
    """Parse a record while treating malformed external state as absent."""
    try:
        return InstanceRecord.from_json(value)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def _sessions(data: dict[str, object]) -> dict[str, object]:
    """Return the validated active-session map."""
    value = data.get("sessions")
    if not isinstance(value, dict):
        msg = "registry sessions are invalid"
        raise TypeError(msg)
    return cast("dict[str, object]", value)


def _expired(data: dict[str, object]) -> dict[str, object]:
    """Return the validated expired-session map."""
    value = data.get("expired")
    if not isinstance(value, dict):
        msg = "registry expired sessions are invalid"
        raise TypeError(msg)
    return cast("dict[str, object]", value)

