"""Application controller — the orchestration brain.

Deliberately Qt-free: everything here is plain Python driven by callbacks, so
the whole connect/disconnect state machine is unit-testable without a display
server.  :mod:`antlighting.ui.bridge` adapts it to QML signals.

Connection state machine::

    OFFLINE ──connect──> CONNECTING ──ok──> CONNECTED
       ^                      │                │
       └────── error ─────────┴── disconnect ──┘
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .collector import CollectReport, ConfigCollector, RefreshScheduler
from .configs.models import ServerConfig
from .configs.ranking import ScoredServer
from .core.base import CoreBackend, CoreError, CoreState, CoreStatus
from .core.config_builder import CoreOptions, LocalEndpoint, TunOptions, build_config
from .core.manager import CoreManager
from .net.elevation import is_admin
from .net.socks import SocksError, http_get_via_socks
from .net.system_proxy import SystemProxyController
from .net.tunnel import TunnelController
from .storage.db import ConfigStore
from .storage.settings import Settings
from .testing.tester import ServerTester, TestRunSummary

log = logging.getLogger(__name__)


def _port_free(port: int, host: str = "127.0.0.1") -> bool:
    """True when nothing is listening on *port* right now."""
    import socket

    try:
        with socket.create_connection((host, port), timeout=0.15):
            return False
    except OSError:
        return True


def _free_port(host: str = "127.0.0.1") -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


class AppState:
    OFFLINE = "offline"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTING = "disconnecting"
    ERROR = "error"

    ALL = (OFFLINE, CONNECTING, CONNECTED, DISCONNECTING, ERROR)


@dataclass(slots=True)
class ConnectionInfo:
    """Everything the main screen needs to know, in one object."""

    state: str = AppState.OFFLINE
    message: str = ""
    detail: str = ""
    server_identity: str = ""
    server_name: str = ""
    server_country: str | None = None
    server_country_code: str | None = None
    server_scheme: str = ""
    server_address: str = ""
    latency_ms: float | None = None
    mode: str = "proxy"  # proxy | tun
    elevated: bool = False
    core_state: str = CoreState.STOPPED
    core_version: str = ""
    progress: float = 0.0
    error: str = ""
    pool_total: int = 0
    pool_working: int = 0
    testing: bool = False
    test_progress: float = 0.0
    collecting: bool = False
    uptime_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for name in self.__dataclass_fields__:  # type: ignore[attr-defined]
            data[name] = getattr(self, name)
        return data


Callback = Callable[[ConnectionInfo], None]
MessageCallback = Callable[[str], None]


class AppController:
    """Coordinates collection, testing, the core and the tunnel."""

    def __init__(
        self,
        *,
        settings: Settings,
        store: ConfigStore,
        core: CoreBackend,
        tester: ServerTester,
        collector: ConfigCollector,
        system_proxy: SystemProxyController,
        tunnel: TunnelController,
        on_state: Callback | None = None,
        on_message: MessageCallback | None = None,
        verify_target: str = "http://cp.cloudflare.com/generate_204",
    ) -> None:
        self.settings = settings
        self.store = store
        self.tester = tester
        self.collector = collector
        self.system_proxy = system_proxy
        self.tunnel = tunnel
        self.verify_target = verify_target
        self._on_state = on_state
        self._on_message = on_message
        self.info = ConnectionInfo()
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()
        self._connected_at: float | None = None
        self._selected: ScoredServer | None = None
        self._tried: list[str] = []

        self.manager = CoreManager(
            core,
            max_restarts=int(settings.get("core.max_restarts", 3)),
            on_state=self._handle_core_state,
        )
        self.refresh = RefreshScheduler(
            collector,
            interval_seconds=float(settings.get("sources.refresh_interval_minutes", 180)) * 60,
            on_done=self._handle_collect_done,
        )

    # ------------------------------------------------------------------ plumbing
    def set_state_callback(self, callback: Callback | None) -> None:
        self._on_state = callback

    def set_message_callback(self, callback: MessageCallback | None) -> None:
        self._on_message = callback

    def _emit(self, **updates: Any) -> ConnectionInfo:
        with self._lock:
            for key, value in updates.items():
                if hasattr(self.info, key):
                    setattr(self.info, key, value)
            snapshot = ConnectionInfo(**{
                name: getattr(self.info, name)
                for name in self.info.__dataclass_fields__  # type: ignore[attr-defined]
            })
        if self._on_state is not None:
            try:
                self._on_state(snapshot)
            except Exception:  # noqa: BLE001
                log.exception("state callback failed")
        return snapshot

    def _say(self, message: str, detail: str = "") -> None:
        log.info("%s%s", message, f" — {detail}" if detail else "")
        self._emit(message=message, detail=detail)
        if self._on_message is not None:
            try:
                self._on_message(message)
            except Exception:  # noqa: BLE001
                log.debug("message callback failed", exc_info=True)

    def refresh_pool_counters(self) -> None:
        stats = self.store.stats()
        self._emit(pool_total=stats["total"], pool_working=stats["working"])

    # ------------------------------------------------------------------- startup
    def startup(self) -> None:
        """Recover from a crashed session and refresh the pool counters."""
        try:
            self.system_proxy.recover_stale()
        except Exception:  # noqa: BLE001
            log.exception("could not recover stale system proxy settings")
        try:
            self.tunnel.recover_stale()
        except Exception:  # noqa: BLE001
            log.exception("could not recover a stale tunnel")
        self.refresh_pool_counters()
        status = self.manager.status()
        self._emit(
            core_state=status.state,
            core_version=status.version,
            elevated=is_admin(),
            state=AppState.OFFLINE,
            message="Ready",
        )
        if self.settings.get("sources.auto_refresh", True):
            self.refresh.start()

    def shutdown(self) -> None:
        """Final teardown — must leave no processes and no routes behind."""
        self.refresh.stop()
        self.tester.cancel()
        try:
            if self.info.state == AppState.CONNECTED:
                self._teardown_connection()
        except Exception:  # noqa: BLE001
            log.exception("error during shutdown teardown")
        self.manager.shutdown()

    # ------------------------------------------------------------------ connect
    def connect(self) -> None:
        """Start connecting on a worker thread (never blocks the UI)."""
        with self._lock:
            if self.info.state in (AppState.CONNECTING, AppState.CONNECTED):
                return
            if self._worker is not None and self._worker.is_alive():
                return
        self._cancel.clear()
        self._tried = []
        self._emit(state=AppState.CONNECTING, error="", progress=0.05)
        self._worker = threading.Thread(
            target=self._connect_worker, name="ant-connect", daemon=True
        )
        self._worker.start()

    def disconnect(self) -> None:
        with self._lock:
            if self.info.state == AppState.OFFLINE:
                return
        self._cancel.set()
        self.tester.cancel()
        self._emit(state=AppState.DISCONNECTING, message="Disconnecting…")
        self._worker = threading.Thread(
            target=self._disconnect_worker, name="ant-disconnect", daemon=True
        )
        self._worker.start()

    def toggle(self) -> None:
        if self.info.state == AppState.CONNECTED:
            self.disconnect()
        elif self.info.state == AppState.OFFLINE or self.info.state == AppState.ERROR:
            self.connect()

    def _connect_worker(self) -> None:
        try:
            self._connect_sequence()
        except Exception as exc:  # noqa: BLE001
            log.exception("connection failed")
            self._safe_teardown()
            self._emit(
                state=AppState.ERROR,
                error=f"{exc.__class__.__name__}: {exc}",
                message="Something went wrong",
                detail=str(exc),
                progress=0.0,
            )

    def _connect_sequence(self) -> None:
        self._say("Checking the core…", "Making sure Xray-core is available")
        self._emit(progress=0.1)
        binary = self.manager.backend.locate_binary()
        if not binary:
            self._emit(
                state=AppState.ERROR,
                error="Xray-core was not found",
                message="Core not found",
                detail="Install or download Xray-core, then try again.",
            )
            return
        if not self.info.core_version:
            self._emit(core_version=self.manager.backend.version())

        self._say("Choosing a server…")
        self._emit(progress=0.2)
        scored = self._ensure_pool()
        if scored is None:
            return

        mode = self._choose_mode()
        self._emit(mode=mode)

        attempts = 0
        max_attempts = 3
        while attempts < max_attempts and not self._cancel.is_set():
            attempts += 1
            if self._cancel.is_set():
                return
            config = scored.config
            self._say(
                f"Connecting to {config.display_name()}…",
                f"{config.scheme_label} · {config.address}:{config.port}",
            )
            self._emit(progress=0.4)
            try:
                if self._start_with(config, mode):
                    self._selected = scored
                    self.store.record_connected(config.identity)
                    self._connected_at = time.monotonic()
                    self.settings.set("servers.last_identity", config.identity, save=True)
                    self._emit(
                        state=AppState.CONNECTED,
                        server_identity=config.identity,
                        server_name=config.display_name(),
                        server_country=config.country,
                        server_country_code=config.country_code,
                        server_scheme=config.scheme,
                        server_address=config.address,
                        latency_ms=scored.latency_ms,
                        message="Connected",
                        detail="Your traffic is protected",
                        progress=1.0,
                        error="",
                    )
                    log.info("connected via %s (%s)", config.identity, mode)
                    return
            except CoreError as exc:
                log.warning("core failed for %s: %s", config.identity, exc)
                self._tried.append(config.identity)
                self.store.record_result(config.identity, "failed", error=str(exc)[:200])
                self._safe_teardown()

            if self._cancel.is_set():
                return
            self._say("That server did not work, trying another…")
            self._emit(progress=0.35)
            scored = self._pick_server(skip=self._tried)
            if scored is None:
                break

        self._safe_teardown()
        self._emit(
            state=AppState.ERROR,
            error="No working server could be reached",
            message="Couldn't find a working server",
            detail="Try testing the servers again, or refresh the list.",
            progress=0.0,
        )

    def _start_with(self, config: ServerConfig, mode: str) -> bool:
        socks_port, http_port = self._pick_ports()
        options = self._core_options()
        endpoint = LocalEndpoint(socks_port=socks_port, http_port=http_port)
        tun = TunOptions(enabled=(mode == "tun"))
        xray_config = build_config(config, endpoint=endpoint, options=options, tun=tun)

        self.manager.ensure_running(xray_config, timeout=15.0)
        self._emit(core_state=CoreState.RUNNING, progress=0.6)

        if mode == "tun":
            self._say("Opening the system tunnel…", "Full-device VPN")
            state = self.tunnel.bring_up(strategy="xray", uplink_host=config.address)
            if not state.active:
                log.warning("tunnel could not be established: %s", state.error)
                raise CoreError(f"tunnel failed: {state.error}")
            self._emit(progress=0.85)
        else:
            self._say("Setting the system proxy…", "Browser and most apps")
            if not self.system_proxy.apply("127.0.0.1", socks_port):
                raise CoreError("could not apply the system proxy settings")
            self._emit(progress=0.85)

        if not self._verify_connection(socks_port):
            raise CoreError("the tunnel is up but no traffic is flowing")
        return True

    def _pick_ports(self) -> tuple[int, int]:
        """Return listener ports, moving on if another app already holds them.

        Public VPN clients collide on the well-known ports often enough that
        refusing to start is the wrong answer; the chosen SOCKS port is passed
        straight to the system proxy so the two always agree.
        """
        socks = int(self.settings.get("connection.socks_port", 20808))
        http = int(self.settings.get("connection.http_port", 20809))
        # http_port == 0 means "do not open an HTTP listener at all".
        http_ok = http == 0 or _port_free(http)
        if _port_free(socks) and http_ok:
            return socks, http
        log.warning("configured ports %d/%d are busy; choosing free ones", socks, http)
        socks = _free_port()
        http = 0 if http == 0 else _free_port()
        return socks, http

    def _verify_connection(self, socks_port: int, attempts: int = 3) -> bool:
        """Push one small HTTP request through the tunnel."""
        self._say("Checking the connection…")
        for attempt in range(attempts):
            if self._cancel.is_set():
                return False
            try:
                code, _ = http_get_via_socks(
                    "127.0.0.1", socks_port, self.verify_target, timeout=8.0
                )
                if code in (200, 204):
                    return True
                log.info("verification returned HTTP %s (attempt %d)", code, attempt + 1)
            except (SocksError, OSError, TimeoutError) as exc:
                log.info("verification attempt %d failed: %s", attempt + 1, exc)
            time.sleep(0.6)
        return False

    def _choose_mode(self) -> str:
        mode = str(self.settings.get("connection.mode", "auto"))
        if mode == "proxy":
            return "proxy"
        if mode == "tun":
            return "tun"
        # auto: use the real VPN when we can, otherwise the system proxy.
        ok, reason = self.tunnel.supported()
        if not ok:
            log.info("TUN mode unavailable (%s); using the system proxy", reason)
            return "proxy"
        return "tun"

    def _core_options(self) -> CoreOptions:
        backend = self.manager.backend
        use_geo = True
        if hasattr(backend, "has_geo_data"):
            try:
                use_geo = bool(backend.has_geo_data())
            except Exception:  # noqa: BLE001
                use_geo = False
        return CoreOptions(
            log_level=str(self.settings.get("core.log_level", "warning")),
            dns_servers=list(self.settings.get("connection.dns_servers") or ["localhost"]),
            block_ads=bool(self.settings.get("connection.block_ads", True)),
            bypass_lan=bool(self.settings.get("connection.bypass_lan", True)),
            ipv6=bool(self.settings.get("connection.ipv6", False)),
            mux_enabled=bool(self.settings.get("connection.mux_enabled", False)),
            mux_concurrency=int(self.settings.get("connection.mux_concurrency", 8)),
            fragment=bool(self.settings.get("connection.fragment", True)),
            use_geo_data=use_geo,
        )

    # -------------------------------------------------------------- disconnect
    def _disconnect_worker(self) -> None:
        try:
            self._teardown_connection()
        except Exception:  # noqa: BLE001
            log.exception("error while disconnecting")
        finally:
            self._emit(
                state=AppState.OFFLINE,
                message="Disconnected",
                detail="The VPN is off",
                server_identity="",
                server_name="",
                latency_ms=None,
                progress=0.0,
                error="",
            )

    def _teardown_connection(self) -> None:
        self._say("Closing the tunnel…")
        try:
            self.tunnel.tear_down()
        except Exception:  # noqa: BLE001
            log.exception("tunnel teardown failed")
        try:
            self.system_proxy.revert()
        except Exception:  # noqa: BLE001
            log.exception("system proxy revert failed")
        try:
            self.manager.stop()
        except Exception:  # noqa: BLE001
            log.exception("core stop failed")
        self._connected_at = None
        self._selected = None

    def _safe_teardown(self) -> None:
        try:
            self._teardown_connection()
        except Exception:  # noqa: BLE001
            log.exception("safe teardown failed")

    # ------------------------------------------------------------- server choice
    def _ensure_pool(self) -> ScoredServer | None:
        configs = self.store.all(include_unusable=False)
        if not configs:
            self._say("No servers yet — collecting…", "This runs once and then refreshes itself")
            self._emit(collecting=True, progress=0.25)
            try:
                report = self.collector.collect(on_progress=self._collect_progress)
                self._emit(collecting=False)
                self.refresh_pool_counters()
                self._say(
                    "Servers collected",
                    f"{report.new_configs} new servers from {report.sources_ok} sources",
                )
            except Exception as exc:  # noqa: BLE001
                self._emit(collecting=False)
                log.exception("initial collection failed")
                self._emit(
                    state=AppState.ERROR,
                    error=str(exc),
                    message="No servers found",
                    detail="Could not reach any configuration source.",
                )
                return None
            configs = self.store.all(include_unusable=False)

        if not configs:
            self._emit(
                state=AppState.ERROR,
                error="empty pool",
                message="No servers found",
                detail="Every configuration source failed. Check your internet connection.",
            )
            return None

        scored = self._pick_server(configs=configs)
        if scored is None:
            self._say("Testing servers to find a working one…")
            self._emit(progress=0.3, testing=True)
            try:
                self.tester.test_many(configs, on_progress=self._test_progress, limit=120)
            finally:
                self._emit(testing=False)
            self.refresh_pool_counters()
            scored = self._pick_server(configs=configs)
        if scored is None:
            self._emit(
                state=AppState.ERROR,
                error="no usable server",
                message="Couldn't find a working server",
                detail="Every server in the list failed the test.",
            )
        return scored

    def _pick_server(
        self, configs: list[ServerConfig] | None = None, skip: list[str] | None = None
    ) -> ScoredServer | None:
        if configs is None:
            configs = self.store.all(include_unusable=False)
        selection = str(self.settings.get("servers.selection", "auto"))
        if selection and selection != "auto":
            for config in configs:
                if config.identity == selection:
                    metrics = self.store.get_metrics(config.identity) or {}
                    return ScoredServer(
                        config=config,
                        score=1e9,
                        latency_ms=metrics.get("latency_ms"),
                        status=metrics.get("status", "unknown"),
                        reliability=0.5,
                        tested_count=0,
                        consecutive_failures=0,
                        age_seconds=None,
                        reason="manually selected",
                    )
        preferred = list(
            self.settings.get("servers.preferred_protocols")
            or ["vless", "trojan", "vmess", "ss"]
        )
        return self.tester.select_best(
            configs,
            skip=skip or [],
            preferred_protocols=preferred,
        )

    def select_server(self, identity: str) -> None:
        """``"auto"`` or a config identity."""
        self.settings.set("servers.selection", identity, save=True)
        if identity == "auto":
            self._emit(server_name="", server_identity="")
        else:
            config = self.store.get(identity)
            if config is not None:
                self._emit(server_name=config.display_name(), server_identity=identity)
        if self.info.state == AppState.CONNECTED:
            log.info("reconnecting to apply the new server selection")
            self.disconnect()

    # ------------------------------------------------------------------ testing
    def test_servers(self, limit: int | None = None) -> None:
        if self.info.testing:
            self.tester.cancel()
            return
        configs = self.store.all(include_unusable=False)
        if not configs:
            self.collect_sources()
            return

        def run() -> None:
            self._emit(testing=True, test_progress=0.0)
            started = time.monotonic()
            try:
                results = self.tester.test_many(
                    configs, on_progress=self._test_progress, limit=limit
                )
                summary = TestRunSummary.from_results(
                    results,
                    duration=time.monotonic() - started,
                    cancelled=self.tester.cancelled,
                )
                self.refresh_pool_counters()
                self._say(
                    "Testing finished",
                    f"{summary.working} working · {summary.slow} slow · "
                    f"{summary.timeout} timed out · {summary.failed} failed",
                )
            except Exception:  # noqa: BLE001
                log.exception("test run failed")
            finally:
                self.tester.reset()
                self._emit(testing=False, test_progress=0.0)

        threading.Thread(target=run, name="ant-test-run", daemon=True).start()

    def _test_progress(self, _result: Any, done: int, total: int) -> None:
        self._emit(test_progress=(done / total) if total else 0.0)

    def collect_sources(self) -> None:
        if self.info.collecting:
            return

        def run() -> None:
            self._emit(collecting=True)
            try:
                report = self.collector.collect(on_progress=self._collect_progress)
                self.refresh_pool_counters()
                self._say("Servers refreshed", report.summary)
            except Exception:  # noqa: BLE001
                log.exception("collection failed")
            finally:
                self._emit(collecting=False)

        threading.Thread(target=run, name="ant-collect", daemon=True).start()

    def _collect_progress(self, label: str, detail: str) -> None:
        self._emit(detail=f"{label}: {detail}")

    def _handle_collect_done(self, report: CollectReport) -> None:
        self.refresh_pool_counters()
        log.info("scheduled refresh: %s", report.summary)

    # ------------------------------------------------------------- core watchdog
    def _handle_core_state(self, status: CoreStatus) -> None:
        self._emit(core_state=status.state)
        if status.state in (CoreState.CRASHED, CoreState.FAILED):
            if self.info.state == AppState.CONNECTED:
                if self.manager.is_alive():
                    log.info("core recovered after a crash")
                    return
                log.error("core died while connected; dropping the connection")
                self._safe_teardown()
                self._emit(
                    state=AppState.ERROR,
                    error=status.error or "core stopped",
                    message="Connection lost",
                    detail=status.error or "The core stopped unexpectedly.",
                )

    # ---------------------------------------------------------------- diagnostics
    def diagnostics(self) -> dict[str, Any]:
        core = self.manager.status()
        return {
            "app": {
                "state": self.info.state,
                "mode": self.info.mode,
                "elevated": is_admin(),
                "uptime_seconds": (time.monotonic() - self._connected_at)
                if self._connected_at
                else 0.0,
            },
            "core": core.as_dict(),
            "tunnel": self.tunnel.status().to_dict(),
            "system_proxy": self.system_proxy.current(),
            "pool": self.store.stats(),
            "sources": self.store.sources(),
            "selection": str(self.settings.get("servers.selection", "auto")),
        }
