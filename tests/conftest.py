"""Shared fixtures and fake implementations for the test suite."""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from antlighting.configs.models import ServerConfig  # noqa: E402
from antlighting.configs.parser import parse_link  # noqa: E402
from antlighting.core.base import CoreBackend, CoreError, CoreState, CoreStatus  # noqa: E402
from antlighting.testing.tester import Probe, TestResult  # noqa: E402

# --------------------------------------------------------------------- sample data
VLESS_TLS = (
    "vless://2f1c0f4e-1d0a-4a2a-9f5f-2c0f0a1b2c3d@203.0.113.10:443"
    "?security=tls&type=tcp&sni=example.com&fp=chrome#DE%20Germany%20%7C%20%40chan%20%7C%20ABC123"
)
VLESS_REALITY = (
    "vless://aaaa-bbbb-cccc@198.51.100.7:443?security=reality&type=raw"
    "&sni=www.microsoft.com&pbk=PUBKEY123&sid=ab12cd34&fp=chrome&flow=xtls-rprx-vision#US%20fast"
)
VMESS = (
    "vmess://eyJhZGQiOiIyMDMuMC4xMTMuMjAiLCJhaWQiOjAsImhvc3QiOiJleGFtcGxlLmNvbSIsImlkIjoi"
    "MTExMTExMTEtMjIyMi0zMzMzLTQ0NDQtNTU1NTU1NTU1NTU1IiwibmV0Ijoid3MiLCJwYXRoIjoiL3dzIiwi"
    "cG9ydCI6IjQ0MyIsInBzIjoiTkwgTmV0aGVybGFuZHMiLCJzbmkiOiJleGFtcGxlLmNvbSIsInRscyI6InRs"
    "cyIsInR5cGUiOiJub25lIiwidiI6IjIifQ=="
)
TROJAN = "trojan://hunter2@trojan.example.org:443?sni=trojan.example.org&type=tcp#FI%20Finland"
SS = "ss://YWVzLTI1Ni1nY206cGFzc3dvcmQ=@192.0.2.55:8388#SG%20Singapore"
HYSTERIA2 = (
    "hysteria2://secret@192.0.2.99:8443?insecure=1&sni=h2.example.org"
    "&obfs=salamander&obfs-password=obfspw&up=100&down=200#NL"
)
WIREGUARD = (
    "wireguard://PRIVKEY@192.0.2.120:51820"
    "?publicKey=PUBKEY&allowedIPs=0.0.0.0/0&reserved=1,2,3&mtu=1280#WG"
)
SSR = "ssr://MTkyLjAuMi41OjgzODg6YXV0aF9hZXMxMjhfbWQ1OmFlcy0yNTYtY2ZiOnRsczEuMl90aWNrZXRfYXV0aDpZV1J0YVc0PQ=="

SAMPLE_LINKS = [VLESS_TLS, VLESS_REALITY, VMESS, TROJAN, SS, HYSTERIA2, WIREGUARD, SSR]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def sample_configs() -> list[ServerConfig]:
    configs = [parse_link(link) for link in SAMPLE_LINKS]
    return [c for c in configs if c is not None]


# -------------------------------------------------------------------- fake core
class FakeCore(CoreBackend):
    """A core double that binds a real TCP port so readiness checks are real."""

    name = "fake"

    def __init__(
        self,
        *,
        fail_to_start: bool = False,
        crash_after: float | None = None,
        crash_once: bool = True,
        never_ready: bool = False,
        version: str = "FakeCore 1.2.3",
    ) -> None:
        self.fail_to_start = fail_to_start
        self.crash_after = crash_after
        self.crash_once = crash_once
        self.never_ready = never_ready
        self._version = version
        self._state = CoreState.STOPPED
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop_serving = threading.Event()
        self._crash_timer: threading.Timer | None = None
        self.starts = 0
        self.stops = 0
        self.last_config: dict[str, Any] | None = None
        self.lines: list[str] = []

    def locate_binary(self) -> str | None:
        return "/usr/bin/fake-xray"

    def version(self) -> str:
        return self._version

    def start(self, config: dict[str, Any], timeout: float = 15.0) -> CoreStatus:
        self.starts += 1
        self.last_config = config
        if self.fail_to_start:
            self._state = CoreState.FAILED
            status = self.status()
            status.error = "core refused to start"
            return status
        self._state = CoreState.STARTING
        if not self.never_ready:
            self._stop_serving.clear()
            self._listener = _bind_with_retry(_config_port(config))
            self._thread = threading.Thread(target=self._serve, daemon=True)
            self._thread.start()
        self._state = CoreState.RUNNING
        self.lines.append("[fake] started")
        if self.crash_after is not None and not (self.crash_once and self.starts > 1):
            self._crash_timer = threading.Timer(self.crash_after, self._crash)
            self._crash_timer.daemon = True
            self._crash_timer.start()
        return self.status()

    def _serve(self) -> None:
        listener = self._listener
        if listener is None:
            return
        # A timed accept loop: blocking in accept() indefinitely would keep the
        # listening fd alive after close(), so the port could not be rebound.
        listener.settimeout(0.2)
        while not self._stop_serving.is_set():
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                conn.recv(1024)
                conn.close()
            except OSError:
                pass

    def _crash(self) -> None:
        self._close_listener()
        self._state = CoreState.CRASHED
        status = self.status()
        status.error = "core exited unexpectedly with code 1"
        self._notify_crash(status)

    def _close_listener(self) -> None:
        self._stop_serving.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
            self._listener = None

    def stop(self, timeout: float = 6.0) -> CoreStatus:
        self.stops += 1
        if self._crash_timer is not None:
            self._crash_timer.cancel()
            self._crash_timer = None
        self._close_listener()
        self._state = CoreState.STOPPED
        self.lines.append("[fake] stopped")
        return self.status()

    def is_alive(self) -> bool:
        return self._state == CoreState.RUNNING

    def status(self) -> CoreStatus:
        return CoreStatus(
            state=self._state,
            pid=4242 if self._state == CoreState.RUNNING else None,
            version=self._version,
            binary_path="/usr/bin/fake-xray",
            log_tail=list(self.lines[-5:]),
        )

    def read_log(self, max_bytes: int = 65536) -> str:
        return "\n".join(self.lines)[-max_bytes:]

    def has_geo_data(self) -> bool:
        return True

    def has_wintun(self) -> bool:
        return False


def _bind_with_retry(port: int, attempts: int = 20) -> socket.socket:
    """Bind, tolerating a socket that the kernel has not fully released yet."""
    last: OSError | None = None
    for _ in range(attempts):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            sock.listen(16)
            return sock
        except OSError as exc:
            last = exc
            sock.close()
            time.sleep(0.05)
    raise OSError(f"could not bind 127.0.0.1:{port}") from last


def _config_port(config: dict[str, Any]) -> int:
    for inbound in config.get("inbounds", []):
        port = inbound.get("port")
        if isinstance(port, int) and port > 0:
            return port
    return free_port()


# ------------------------------------------------------------------- fake probe
class FakeProbe(Probe):
    """Deterministic probe driven by an identity -> outcome mapping."""

    name = "fake"

    def __init__(
        self,
        outcomes: dict[str, tuple[str, float | None, float]] | None = None,
        default: tuple[str, float | None, float] = ("working", 120.0, 0.05),
        ignore_timeout: bool = False,
    ) -> None:
        # identity -> (status, latency_ms, sleep_seconds)
        self.outcomes = dict(outcomes or {})
        self.default = default
        # A real probe can wedge in a socket read and ignore its timeout; this
        # reproduces that so the hard watchdog is genuinely exercised.
        self.ignore_timeout = ignore_timeout
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def test(self, config: ServerConfig, timeout: float) -> TestResult:
        with self._lock:
            self.calls.append(config.identity)
        status, latency, delay = self.outcomes.get(config.identity, self.default)
        if delay:
            time.sleep(delay if self.ignore_timeout else min(delay, timeout))
        error = "" if status in ("working", "slow") else f"simulated {status}"
        return TestResult(
            identity=config.identity,
            status=status,
            latency_ms=latency,
            error=error,
            method=self.name,
            duration=delay,
        )


# ------------------------------------------------------------------ fake source
class FakeSource:
    """Duck-typed stand-in for :class:`ConfigSource`."""

    kind = "fake"

    def __init__(self, source_id: str, payload: str = "", error: str = "", raise_exc: bool = False):
        self.id = source_id
        self.label = source_id
        self.enabled = True
        self.urls = [f"fake://{source_id}"]
        self.options: dict[str, Any] = {}
        self.payload = payload
        self.error = error
        self.raise_exc = raise_exc
        self.fetch_calls = 0

    @property
    def url(self) -> str:
        """Mirror ``ConfigSource.url`` so failure paths behave identically."""
        return self.urls[0] if self.urls else ""

    def fetch(self, timeout: float = 15.0):
        from antlighting.sources.base import FetchResult

        self.fetch_calls += 1
        if self.raise_exc:
            raise RuntimeError("boom")
        return [
            FetchResult(
                source_id=self.id,
                url=self.urls[0],
                ok=not self.error,
                text=self.payload,
                error=self.error,
                bytes_read=len(self.payload),
                http_status=200 if not self.error else 500,
            )
        ]


# ------------------------------------------------------------------- fake store
class FakeStore:
    """In-memory replacement for :class:`ConfigStore`."""

    def __init__(self) -> None:
        self.configs: dict[str, ServerConfig] = {}
        self.metrics: dict[str, dict[str, Any]] = {}
        self._sources: list[dict[str, Any]] = []
        self.connected: list[str] = []

    def upsert_many(self, configs, now=None) -> int:
        added = 0
        for config in configs:
            if config.identity not in self.configs:
                added += 1
            self.configs[config.identity] = config
        return added

    def all(self, include_unusable: bool = True):
        items = list(self.configs.values())
        if not include_unusable:
            items = [c for c in items if c.usable]
        return items

    def get(self, identity: str):
        return self.configs.get(identity)

    def get_metrics(self, identity: str):
        return self.metrics.get(identity, {})

    def record_result(self, identity, status, latency_ms=None, error="", now=None):
        entry = self.metrics.setdefault(
            identity,
            {
                "status": "unknown",
                "latency_ms": None,
                "success_count": 0,
                "failure_count": 0,
                "consecutive_failures": 0,
                "last_tested": None,
                "last_error": "",
            },
        )
        entry["status"] = status
        entry["latency_ms"] = latency_ms
        entry["last_error"] = error
        entry["last_tested"] = now or time.time()
        if status == "working":
            entry["success_count"] += 1
            entry["consecutive_failures"] = 0
        else:
            entry["failure_count"] += 1
            entry["consecutive_failures"] += 1

    def record_connected(self, identity: str, now: float | None = None) -> None:
        self.connected.append(identity)

    def count(self) -> int:
        return len(self.configs)

    def count_usable(self) -> int:
        return sum(1 for c in self.configs.values() if c.usable)

    def stats(self):
        return {
            "total": len(self.configs),
            "usable": self.count_usable(),
            "working": sum(1 for m in self.metrics.values() if m["status"] == "working"),
            "failed": sum(1 for m in self.metrics.values() if m["status"] == "failed"),
            "untested": sum(1 for m in self.metrics.values() if m["status"] == "unknown"),
            "by_scheme": {},
            "by_country": {},
        }

    def prune(self, keep: int = 4000) -> int:
        return 0

    def mark_trusted(self, identity: str, trusted: bool = True) -> None:
        return None

    def record_source_result(self, source_id, count, error="", now=None) -> None:
        self._sources.append({"id": source_id, "count": count, "error": error})

    def sources(self):
        """Method, matching ``ConfigStore.sources()``."""
        return list(self._sources)


# ----------------------------------------------------------------- fake registry
class FakeRegistry:
    """Read/write stand-in for the Windows Internet Settings registry key."""

    def __init__(self, initial: dict[str, Any] | None = None):
        self.values: dict[str, Any] = dict(initial or {})
        self.writes: list[dict[str, Any]] = []

    def read(self, path: str):
        return dict(self.values) if self.values else {}

    def write(self, values: dict[str, Any]) -> bool:
        self.writes.append(dict(values))
        self.values.update(values)
        return True


@pytest.fixture()
def tmp_paths(tmp_path):
    return {
        "data": str(tmp_path),
        "db": str(tmp_path / "configs.db"),
        "settings": str(tmp_path / "settings.json"),
        "proxy_snapshot": str(tmp_path / "system_proxy.json"),
        "tunnel_snapshot": str(tmp_path / "tunnel.json"),
    }


@pytest.fixture()
def configs():
    return sample_configs()


def _pid_alive(pid: int | None) -> bool:
    """True while *pid* exists (POSIX); used to assert no orphaned processes."""
    if pid is None:
        return False
    if os.name == "nt":  # pragma: no cover - exercised only on Windows
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
