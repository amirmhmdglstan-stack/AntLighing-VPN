"""Core backend abstraction.

AntLighting talks to a *core* (Xray-core today, something else tomorrow) through
this interface only.  Nothing above this layer imports ``xray``-specific code.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Callable


class CoreState:
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    CRASHED = "crashed"
    FAILED = "failed"

    ALL = (STOPPED, STARTING, RUNNING, STOPPING, CRASHED, FAILED)


@dataclass(slots=True)
class CoreStatus:
    state: str = CoreState.STOPPED
    pid: int | None = None
    exit_code: int | None = None
    version: str = ""
    binary_path: str = ""
    error: str = ""
    uptime_seconds: float = 0.0
    restarts: int = 0
    log_tail: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "pid": self.pid,
            "exit_code": self.exit_code,
            "version": self.version,
            "binary_path": self.binary_path,
            "error": self.error,
            "uptime_seconds": round(self.uptime_seconds, 2),
            "restarts": self.restarts,
            "log_tail": list(self.log_tail[-25:]),
        }


class CoreError(RuntimeError):
    """Raised when the core cannot be started or stops unexpectedly."""


class CoreBackend(abc.ABC):
    """A controllable proxy core."""

    name: str = "core"

    @abc.abstractmethod
    def locate_binary(self) -> str | None:
        """Return the path of the core executable, or ``None`` if not found."""

    @abc.abstractmethod
    def version(self) -> str:
        """Return the core version string (empty when unavailable)."""

    @abc.abstractmethod
    def start(self, config: dict[str, Any], timeout: float = 15.0) -> CoreStatus:
        """Start the core with *config*; raise :class:`CoreError` on failure."""

    @abc.abstractmethod
    def stop(self, timeout: float = 6.0) -> CoreStatus:
        """Stop the core and make sure the process is gone."""

    @abc.abstractmethod
    def status(self) -> CoreStatus:
        """Current status snapshot (cheap; safe to poll from the UI thread)."""

    @abc.abstractmethod
    def is_alive(self) -> bool:
        """True while the core process is running."""

    @abc.abstractmethod
    def read_log(self, max_bytes: int = 65536) -> str:
        """Recent core log output, most recent last."""

    def set_crash_handler(self, handler: Callable[[CoreStatus], None] | None) -> None:
        """Register a callback invoked when the core dies unexpectedly."""
        self._crash_handler = handler

    _crash_handler: Callable[[CoreStatus], None] | None = None

    def _notify_crash(self, status: CoreStatus) -> None:
        handler = getattr(self, "_crash_handler", None)
        if handler is not None:
            try:
                handler(status)
            except Exception:  # noqa: BLE001 - never let a callback kill the watcher
                pass
