"""Minimal cross-process lock used by the JSON session registry."""

from __future__ import annotations

import contextlib
import os
import sys
import time
from pathlib import Path
from threading import Lock
from typing import Self

if sys.platform == "win32":
    import msvcrt as _msvcrt
else:
    _msvcrt = None
if sys.platform != "win32":
    import fcntl as _fcntl
else:
    _fcntl = None


class FileLockTimeoutError(TimeoutError):
    """The lock could not be acquired before its deadline."""


_locks: dict[str, Lock] = {}
_guard = Lock()


def _thread_lock(path: str) -> Lock:
    """Return the process-local lock for a normalized path."""
    key = str(Path(path).resolve())
    with _guard:
        return _locks.setdefault(key, Lock())


class FileLock:
    """Serialize access across threads and processes."""

    def __init__(self, path: str, timeout: float = 5.0) -> None:
        """Create a lock for ``path`` with a bounded acquisition timeout."""
        self.path = path
        self.timeout = timeout
        self._lock = _thread_lock(path)
        self._fd: int | None = None

    def __enter__(self) -> Self:
        """Acquire the process-local and operating-system locks."""
        if not self._lock.acquire(timeout=self.timeout):
            msg = f"could not lock {self.path}"
            raise FileLockTimeoutError(msg)
        try:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(self.path, os.O_CREAT | os.O_RDWR)
            if sys.platform == "win32":
                self._acquire_windows()
            else:
                self._acquire_unix()
        except BaseException:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            self._lock.release()
            raise
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        """Release both lock layers."""
        try:
            if self._fd is not None:
                if sys.platform == "win32":
                    if _msvcrt is None:
                        msg = "Windows lock support is unavailable"
                        raise RuntimeError(msg)
                    with contextlib.suppress(OSError):
                        _msvcrt.locking(self._fd, _msvcrt.LK_UNLCK, 1)
                else:
                    if _fcntl is None:
                        msg = "POSIX lock support is unavailable"
                        raise RuntimeError(msg)
                    _fcntl.flock(self._fd, _fcntl.LOCK_UN)
                os.close(self._fd)
                self._fd = None
        finally:
            self._lock.release()

    def _acquire_unix(self) -> None:
        if self._fd is None:
            msg = "lock file is not open"
            raise RuntimeError(msg)
        fd = self._fd
        deadline = time.monotonic() + self.timeout
        if _fcntl is None:
            msg = "POSIX lock support is unavailable"
            raise RuntimeError(msg)
        while True:
            try:
                _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            except OSError as exc:
                if time.monotonic() >= deadline:
                    msg = f"could not lock {self.path}"
                    raise FileLockTimeoutError(msg) from exc
                time.sleep(0.05)
            else:
                return

    def _acquire_windows(self) -> None:
        if self._fd is None:
            msg = "lock file is not open"
            raise RuntimeError(msg)
        fd = self._fd
        deadline = time.monotonic() + self.timeout
        if _msvcrt is None:
            msg = "Windows lock support is unavailable"
            raise RuntimeError(msg)
        while True:
            try:
                _msvcrt.locking(fd, _msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                if time.monotonic() >= deadline:
                    msg = f"could not lock {self.path}"
                    raise FileLockTimeoutError(msg) from exc
                time.sleep(0.05)
            else:
                return
