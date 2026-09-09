"""Core manager: start / stop / restart / watchdog on top of a backend."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from .base import CoreBackend, CoreError, CoreState, CoreStatus

log = logging.getLogger(__name__)


class CoreManager:
    """Owns one core instance and keeps it in the requested state.

    The UI calls :meth:`ensure_running` with a config; the manager starts the
    core if needed, restarts it a bounded number of times when it crashes, and
    guarantees the process is gone after :meth:`shutdown`.
    """

    def __init__(
        self,
        backend: CoreBackend,
        *,
        max_restarts: int = 3,
        restart_window: float = 120.0,
        on_state: Callable[[CoreStatus], None] | None = None,
    ) -> None:
        self.backend = backend
        self.max_restarts = max_restarts
        self.restart_window = restart_window
        self._on_state = on_state
        self._lock = threading.RLock()
        self._desired = False
        self._current_config: dict[str, Any] | None = None
        self._crash_times: list[float] = []
        self._auto_restart = True
        backend.set_crash_handler(self._handle_crash)

    # ------------------------------------------------------------------ control
    def ensure_running(self, config: dict[str, Any], timeout: float = 15.0) -> CoreStatus:
        with self._lock:
            self._desired = True
            self._current_config = config
            if self.backend.is_alive():
                return self.backend.status()
        status = self.backend.start(config, timeout=timeout)
        self._emit(status)
        if status.state not in (CoreState.RUNNING,):
            raise CoreError(status.error or "core failed to start")
        return status

    def stop(self, timeout: float = 6.0) -> CoreStatus:
        with self._lock:
            self._desired = False
        status = self.backend.stop(timeout=timeout)
        self._emit(status)
        return status

    def shutdown(self, timeout: float = 6.0) -> None:
        """Final teardown — also called from the application exit hook."""
        try:
            self.stop(timeout=timeout)
        except Exception:  # noqa: BLE001
            log.exception("error while shutting down the core")

    def restart(self, timeout: float = 15.0) -> CoreStatus:
        with self._lock:
            config = self._current_config
        self.backend.stop(timeout=4.0)
        if config is None:
            raise CoreError("no configuration to restart with")
        return self.ensure_running(config, timeout=timeout)

    def status(self) -> CoreStatus:
        return self.backend.status()

    def is_alive(self) -> bool:
        return self.backend.is_alive()

    def set_auto_restart(self, enabled: bool) -> None:
        with self._lock:
            self._auto_restart = enabled

    # ------------------------------------------------------------------ watchdog
    def _handle_crash(self, status: CoreStatus) -> None:
        with self._lock:
            desired = self._desired
            allowed = self._auto_restart
            config = self._current_config
        log.warning("core crashed: %s", status.error)
        self._emit(status)
        if not desired or not allowed or config is None:
            return
        now = time.monotonic()
        self._crash_times = [t for t in self._crash_times if now - t < self.restart_window]
        if len(self._crash_times) >= self.max_restarts:
            log.error(
                "core crashed %d times in %.0fs; giving up",
                len(self._crash_times),
                self.restart_window,
            )
            with self._lock:
                self._desired = False
            status.error = (
                f"core crashed {len(self._crash_times)} times; automatic restart disabled"
            )
            self._emit(status)
            return
        self._crash_times.append(now)
        log.info("restarting core after crash (%d/%d)", len(self._crash_times), self.max_restarts)
        try:
            if hasattr(self.backend, "bump_restart"):
                self.backend.bump_restart()
            self.backend.start(config, timeout=15.0)
            self._emit(self.backend.status())
        except Exception as exc:  # noqa: BLE001
            log.error("restart failed: %s", exc)
            failed = self.backend.status()
            failed.state = CoreState.FAILED
            failed.error = f"restart failed: {exc}"
            self._emit(failed)

    def _emit(self, status: CoreStatus) -> None:
        if self._on_state is None:
            return
        try:
            self._on_state(status)
        except Exception:  # noqa: BLE001
            log.exception("core state callback failed")
