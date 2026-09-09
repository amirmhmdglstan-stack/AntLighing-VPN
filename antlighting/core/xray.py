"""Xray-core backend.

Lifecycle rules that matter:

* The core always runs as a **child process** with its config in a temp file, so
  a crash can never take AntLighting with it.
* Startup is confirmed by the local listener actually accepting connections,
  not by "the process did not exit immediately".
* Shutdown terminates the whole process tree; a watcher thread detects
  unexpected death and reports it.
* The core's stdout/stderr are drained into a bounded ring buffer, so a chatty
  core cannot exhaust memory or block on a full pipe.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from typing import Any

from .base import CoreBackend, CoreError, CoreState, CoreStatus

log = logging.getLogger(__name__)

BINARY_NAMES = ("xray.exe", "xray") if os.name == "nt" else ("xray",)
LOG_BUFFER_LINES = 400
LOG_LINE_LIMIT = 2000


class XrayCore(CoreBackend):
    """Controls a standalone ``xray`` executable."""

    name = "xray"

    def __init__(
        self,
        binary_path: str | None = None,
        search_dirs: list[str] | None = None,
        work_dir: str | None = None,
        ready_probe=None,
    ) -> None:
        self.binary_path = binary_path
        self._search_dirs = list(search_dirs or [])
        self._work_dir = work_dir or tempfile.gettempdir()
        self._ready_probe = ready_probe  # callable(host, port, timeout) -> bool
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.RLock()
        self._log: deque[str] = deque(maxlen=LOG_BUFFER_LINES)
        self._reader: threading.Thread | None = None
        self._watcher: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._config_path: str | None = None
        self._started_at: float | None = None
        self._restarts = 0
        self._last_error = ""
        self._version = ""
        self._local_endpoint: tuple[str, int] | None = None
        os.makedirs(self._work_dir, exist_ok=True)

    # ---------------------------------------------------------------- discovery
    @staticmethod
    def candidate_dirs(extra: list[str] | None = None) -> list[str]:
        """Directories searched for the core binary, most specific first."""
        here = os.path.dirname(os.path.abspath(__file__))
        app_root = os.path.dirname(os.path.dirname(here))
        frozen = getattr(sys, "_MEIPASS", None)  # PyInstaller unpack dir
        dirs: list[str] = []
        if frozen:
            dirs.append(str(frozen))
        dirs.extend(
            [
                os.path.join(app_root, "bin"),
                os.path.join(app_root, "core"),
                os.path.join(app_root, "resources"),
                app_root,
                os.getcwd(),
            ]
        )
        env = os.environ.get("ANTLIGHTING_CORE_DIR")
        if env:
            dirs.insert(0, env)
        dirs.extend(extra or [])
        seen: set[str] = set()
        unique: list[str] = []
        for item in dirs:
            if item and item not in seen:
                seen.add(item)
                unique.append(item)
        return unique

    def locate_binary(self) -> str | None:
        if self.binary_path and os.path.isfile(self.binary_path):
            return self.binary_path
        for directory in self.candidate_dirs(self._search_dirs):
            for name in BINARY_NAMES:
                candidate = os.path.join(directory, name)
                if os.path.isfile(candidate):
                    self.binary_path = candidate
                    return candidate
        found = shutil.which("xray")
        if found:
            self.binary_path = found
            return found
        return None

    def version(self) -> str:
        if self._version:
            return self._version
        binary = self.locate_binary()
        if not binary:
            return ""
        try:
            completed = subprocess.run(
                [binary, "version"],
                capture_output=True,
                timeout=10,
                **_no_window(),
            )
            text = (completed.stdout or b"").decode("utf-8", errors="replace")
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("Xray"):
                    self._version = line
                    break
            else:
                self._version = text.splitlines()[0].strip() if text.strip() else ""
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("could not read xray version: %s", exc)
            self._version = ""
        return self._version

    def has_geo_data(self) -> bool:
        """True when geoip.dat and geosite.dat sit next to the core binary."""
        binary = self.locate_binary()
        if not binary:
            return False
        base = os.path.dirname(os.path.abspath(binary))
        for directory in {base, *self.candidate_dirs(self._search_dirs)}:
            if os.path.isfile(os.path.join(directory, "geoip.dat")) and os.path.isfile(
                os.path.join(directory, "geosite.dat")
            ):
                return True
        return False

    def has_wintun(self) -> bool:
        """True when wintun.dll is available for the TUN inbound (Windows)."""
        if os.name != "nt":
            return False
        binary = self.locate_binary()
        base = os.path.dirname(os.path.abspath(binary)) if binary else os.getcwd()
        for directory in {base, *self.candidate_dirs(self._search_dirs)}:
            if os.path.isfile(os.path.join(directory, "wintun.dll")):
                return True
        return False

    # ------------------------------------------------------------------ lifecycle
    def start(self, config: dict[str, Any], timeout: float = 15.0) -> CoreStatus:
        with self._lock:
            if self.is_alive():
                log.debug("core already running (pid=%s)", self._pid())
                return self.status()

            binary = self.locate_binary()
            if not binary:
                self._last_error = "xray binary not found"
                return self._fail(CoreState.FAILED, self._last_error)

            self._stop_event.clear()
            config_path = self._write_config(config)
            self._config_path = config_path

            endpoint = _first_local_endpoint(config)
            self._local_endpoint = endpoint

            try:
                process = subprocess.Popen(
                    [binary, "-config", config_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    cwd=os.path.dirname(binary),
                    **_no_window(),
                )
            except OSError as exc:
                self._last_error = f"failed to launch core: {exc}"
                return self._fail(CoreState.FAILED, self._last_error)

            self._process = process
            self._started_at = time.monotonic()
            self._append_log(f"[antlighting] started {binary} (pid {process.pid})")
            self._reader = threading.Thread(
                target=self._drain_output, args=(process,), name="xray-stdout", daemon=True
            )
            self._reader.start()
            self._watcher = threading.Thread(
                target=self._watch, args=(process,), name="xray-watch", daemon=True
            )
            self._watcher.start()

        # Wait for readiness outside the lock so stop() stays responsive.
        if not self._wait_ready(timeout):
            details = self.read_log(4096) or self._last_error or "core did not become ready"
            self.stop(timeout=3.0)
            return self._fail(CoreState.FAILED, _summarise(details))

        with self._lock:
            return self.status()

    def stop(self, timeout: float = 6.0) -> CoreStatus:
        with self._lock:
            process = self._process
            if process is None:
                return self.status()
            self._stop_event.set()
            self._append_log("[antlighting] stopping core")
            if os.name == "nt":
                # A Windows launcher (cmd / batch wrapper) spawns the real worker
                # as a child; terminating only the top process would orphan it.
                # Always kill the whole tree.
                _kill_tree(process)
            else:
                _terminate(process)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and process.poll() is None:
                time.sleep(0.05)
            if process.poll() is None:
                _kill_tree(process)
            try:
                process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                log.error("core process %s refused to die", process.pid)
            self._append_log(f"[antlighting] core exited with code {process.returncode}")
            self._process = None
            self._started_at = None
            self._cleanup_config()
            return self.status()

    def is_alive(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def _pid(self) -> int | None:
        return self._process.pid if self._process else None

    def status(self) -> CoreStatus:
        with self._lock:
            process = self._process
            if process is None:
                state = CoreState.STOPPED
            elif process.poll() is None:
                state = CoreState.RUNNING
            else:
                state = CoreState.CRASHED
            return CoreStatus(
                state=state,
                pid=process.pid if process else None,
                exit_code=process.returncode if process else None,
                version=self._version,
                binary_path=self.binary_path or "",
                error=self._last_error,
                uptime_seconds=(time.monotonic() - self._started_at)
                if (self._started_at and process and process.poll() is None)
                else 0.0,
                restarts=self._restarts,
                log_tail=list(self._log)[-25:],
            )

    def read_log(self, max_bytes: int = 65536) -> str:
        with self._lock:
            text = "\n".join(self._log)
        return text[-max_bytes:]

    def bump_restart(self) -> None:
        with self._lock:
            self._restarts += 1

    # ------------------------------------------------------------------ internals
    def _write_config(self, config: dict[str, Any]) -> str:
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            prefix="antlighting-",
            dir=self._work_dir,
            delete=False,
            encoding="utf-8",
        )
        try:
            json.dump(config, handle, indent=2, ensure_ascii=False)
        finally:
            handle.close()
        return handle.name

    def _cleanup_config(self) -> None:
        path = self._config_path
        self._config_path = None
        if path and os.path.isfile(path):
            try:
                os.unlink(path)
            except OSError:
                pass

    def _wait_ready(self, timeout: float) -> bool:
        endpoint = self._local_endpoint
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            process = self._process
            if process is not None and process.poll() is not None:
                self._last_error = f"core exited early with code {process.returncode}"
                return False
            if endpoint is None:
                # Nothing to probe; a live process is the best signal available.
                return self.is_alive()
            if self._ready_probe is not None:
                if self._ready_probe(endpoint[0], endpoint[1], 0.5):
                    self._append_log(f"[antlighting] core ready on {endpoint[0]}:{endpoint[1]}")
                    return True
            elif _port_open(endpoint[0], endpoint[1], 0.5):
                self._append_log(f"[antlighting] core ready on {endpoint[0]}:{endpoint[1]}")
                return True
            time.sleep(0.1)
        self._last_error = f"core did not open {endpoint} within {timeout:.0f}s"
        return False

    def _drain_output(self, process: subprocess.Popen[bytes]) -> None:
        stream = process.stdout
        if stream is None:
            return
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    self._append_log(line[:LOG_LINE_LIMIT])
        except (ValueError, OSError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def _append_log(self, line: str) -> None:
        with self._lock:
            self._log.append(line)

    def _watch(self, process: subprocess.Popen[bytes]) -> None:
        code = process.wait()
        if self._stop_event.is_set():
            return
        with self._lock:
            self._last_error = f"core exited unexpectedly with code {code}"
            self._started_at = None
            if self._process is process:
                self._process = None
            snapshot = self.status()
            snapshot.state = CoreState.CRASHED
        self._append_log(f"[antlighting] core crashed with exit code {code}")
        self._notify_crash(snapshot)

    def _fail(self, state: str, error: str) -> CoreStatus:
        with self._lock:
            self._last_error = error
            self._cleanup_config()
            status = self.status()
            status.state = state
            status.error = error
        return status


def _no_window() -> dict[str, Any]:
    """Hide the console window on Windows; nothing elsewhere."""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore[attr-defined]
    startupinfo.wShowWindow = 0  # SW_HIDE
    return {
        "startupinfo": startupinfo,
        "creationflags": 0x08000000,  # CREATE_NO_WINDOW
    }


def _terminate(process: subprocess.Popen[bytes]) -> None:
    try:
        if os.name == "nt":
            process.terminate()
        else:
            process.send_signal(signal.SIGTERM)
    except (OSError, subprocess.SubprocessError):
        pass


def _kill_tree(process: subprocess.Popen[bytes]) -> None:
    """Kill the process and, on Windows, its whole tree."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                **_no_window(),
            )
        else:
            process.kill()
    except (OSError, subprocess.SubprocessError):
        try:
            process.kill()
        except (OSError, subprocess.SubprocessError):
            pass


def _first_local_endpoint(config: dict[str, Any]) -> tuple[str, int] | None:
    """Find the first TCP listener in a config, used as the readiness probe."""
    for inbound in config.get("inbounds", []):
        if not isinstance(inbound, dict):
            continue
        if inbound.get("protocol") == "tun":
            continue
        port = inbound.get("port")
        if isinstance(port, int) and port > 0:
            listen = str(inbound.get("listen") or "127.0.0.1")
            if listen in ("0.0.0.0", "::", ""):
                listen = "127.0.0.1"
            return listen, port
    return None


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _summarise(text: str, limit: int = 400) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    interesting = [line for line in lines if any(
        token in line.lower() for token in ("error", "failed", "invalid", "cannot", "refused")
    )]
    chosen = interesting[-3:] or lines[-3:]
    return " | ".join(chosen)[:limit] or text[-limit:]
