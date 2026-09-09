"""Application state machine: connect, disconnect, error and recovery paths."""

from __future__ import annotations

import time

import pytest

from antlighting.collector import ConfigCollector
from antlighting.controller import AppController, AppState
from antlighting.core.base import CoreError
from antlighting.core.manager import CoreManager
from antlighting.net.system_proxy import SystemProxyController
from antlighting.net.tunnel import TunnelController
from antlighting.storage.settings import Settings
from antlighting.testing.tester import ServerTester
from tests.conftest import (
    FakeCore,
    FakeProbe,
    FakeRegistry,
    FakeSource,
    FakeStore,
    VLESS_TLS,
    TROJAN,
    sample_configs,
)


class RecordingProxy(SystemProxyController):
    """System proxy controller with an injected in-memory registry."""

    def __init__(self, path: str, registry: FakeRegistry, available: bool = True):
        super().__init__(path, reg_reader=registry.read, reg_writer=registry.write)
        self.available = available

    def apply(self, host: str, port: int, bypass: str = "") -> bool:
        if not self.available:
            return False
        import os

        if not self.applied:
            self.capture()
        ok = self._reg_writer(
            {"ProxyEnable": 1, "ProxyServer": f"{host}:{port}", "ProxyOverride": bypass}
        )
        self.applied = ok
        return ok

    def revert(self) -> bool:
        snapshot = self.load_snapshot()
        self.applied = False
        values = {
            "ProxyEnable": snapshot.enabled if snapshot else 0,
            "ProxyServer": snapshot.server if snapshot else "",
            "ProxyOverride": snapshot.override if snapshot else "",
        }
        ok = self._reg_writer(values)
        if ok:
            self._clear_snapshot()
        return ok


class RecordingTunnel(TunnelController):
    """Tunnel controller that records intent instead of touching the OS."""

    def __init__(self, path: str, available: bool = False, fail: bool = False):
        super().__init__(path, runner=lambda argv, timeout=20.0: None)
        self.available = available
        self.fail = fail
        self.up_calls = 0
        self.down_calls = 0

    def supported(self):
        return (True, "") if self.available else (False, "not available in tests")

    def bring_up(self, *, strategy="xray", uplink_host="", wait_seconds=8.0):
        from antlighting.net.tunnel import TunnelState

        self.up_calls += 1
        state = TunnelState(active=not self.fail, strategy=strategy, uplink_ip=uplink_host)
        if self.fail:
            state.error = "simulated tunnel failure"
        self._state = state
        return state

    def tear_down(self):
        from antlighting.net.tunnel import TunnelState

        self.down_calls += 1
        self._state = TunnelState()
        return self._state

    def recover_stale(self):
        return False


@pytest.fixture()
def rig(tmp_path):
    """A fully wired controller with every external dependency faked."""
    settings = Settings(path=str(tmp_path / "settings.json"))
    # Every rig gets its own listener ports so tests cannot collide.
    from tests.conftest import free_port

    settings.set("connection.socks_port", free_port(), save=False)
    settings.set("connection.http_port", 0, save=False)
    store = FakeStore()
    store.upsert_many(sample_configs())
    for cfg in sample_configs():
        store.record_result(cfg.identity, "working", latency_ms=120.0)

    core = FakeCore()
    registry = FakeRegistry({"ProxyEnable": 0, "ProxyServer": "", "ProxyOverride": ""})
    proxy = RecordingProxy(str(tmp_path / "proxy.json"), registry)
    tunnel = RecordingTunnel(str(tmp_path / "tunnel.json"), available=False)
    collector = ConfigCollector(store, [FakeSource("s", VLESS_TLS + "\n" + TROJAN)])
    tester = ServerTester(FakeProbe(default=("working", 120.0, 0.01)), store=store, timeout=2.0)

    states: list[str] = []
    controller = AppController(
        settings=settings,
        store=store,
        core=core,
        tester=tester,
        collector=collector,
        system_proxy=proxy,
        tunnel=tunnel,
        on_state=lambda info: states.append(info.state),
        verify_target="http://127.0.0.1:1/generate_204",
    )
    # The fake core cannot actually proxy, so skip the traffic verification.
    controller._verify_connection = lambda port, attempts=3: True
    return {
        "settings": settings,
        "store": store,
        "core": core,
        "proxy": proxy,
        "tunnel": tunnel,
        "registry": registry,
        "tester": tester,
        "collector": collector,
        "controller": controller,
        "states": states,
    }


def _wait(predicate, timeout=6.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class TestConnect:
    def test_connect_reaches_connected(self, rig):
        controller = rig["controller"]
        controller.connect()
        assert _wait(lambda: controller.info.state == AppState.CONNECTED), controller.info
        assert controller.info.server_identity
        assert controller.info.core_state == "running"
        assert rig["core"].starts == 1
        assert rig["proxy"].applied is True

    def test_connecting_state_is_observable(self, rig):
        controller = rig["controller"]
        controller.connect()
        assert AppState.CONNECTING in rig["states"]
        _wait(lambda: controller.info.state == AppState.CONNECTED)

    def test_progress_advances_to_one(self, rig):
        controller = rig["controller"]
        controller.connect()
        _wait(lambda: controller.info.state == AppState.CONNECTED)
        assert controller.info.progress == 1.0

    def test_selected_server_is_recorded_as_connected(self, rig):
        controller = rig["controller"]
        controller.connect()
        _wait(lambda: controller.info.state == AppState.CONNECTED)
        assert rig["store"].connected
        assert rig["settings"].get("servers.last_identity")

    def test_connect_is_idempotent(self, rig):
        controller = rig["controller"]
        controller.connect()
        controller.connect()
        _wait(lambda: controller.info.state == AppState.CONNECTED)
        assert rig["core"].starts == 1


class TestDisconnect:
    def test_disconnect_returns_to_offline_and_cleans_up(self, rig):
        controller = rig["controller"]
        controller.connect()
        _wait(lambda: controller.info.state == AppState.CONNECTED)
        controller.disconnect()
        assert _wait(lambda: controller.info.state == AppState.OFFLINE)
        assert rig["core"].is_alive() is False
        assert rig["proxy"].applied is False
        assert controller.info.server_identity == ""

    def test_proxy_settings_are_restored(self, rig):
        rig["registry"].values.update({"ProxyEnable": 1, "ProxyServer": "old.proxy:8080"})
        controller = rig["controller"]
        controller.connect()
        _wait(lambda: controller.info.state == AppState.CONNECTED)
        assert rig["registry"].values["ProxyServer"].startswith("127.0.0.1:")
        controller.disconnect()
        _wait(lambda: controller.info.state == AppState.OFFLINE)
        assert rig["registry"].values["ProxyServer"] == "old.proxy:8080"

    def test_shutdown_from_connected_disconnects(self, rig):
        controller = rig["controller"]
        controller.connect()
        _wait(lambda: controller.info.state == AppState.CONNECTED)
        controller.shutdown()
        assert rig["core"].is_alive() is False
        assert rig["proxy"].applied is False


class TestFailures:
    def test_missing_core_reports_an_error_state(self, rig):
        rig["core"].locate_binary = lambda: None
        controller = rig["controller"]
        controller.connect()
        assert _wait(lambda: controller.info.state == AppState.ERROR)
        assert "not found" in controller.info.error.lower()

    def test_core_start_failure_surfaces_an_error(self, rig):
        rig["core"].fail_to_start = True
        controller = rig["controller"]
        controller.connect()
        assert _wait(lambda: controller.info.state == AppState.ERROR)
        assert controller.info.error
        assert rig["core"].is_alive() is False

    def test_every_server_failing_reports_no_working_server(self, rig):
        rig["store"].configs.clear()
        rig["store"].upsert_many(sample_configs())
        for cfg in sample_configs():
            rig["store"].record_result(cfg.identity, "failed", error="dead")
        rig["tester"].probe = FakeProbe(default=("failed", None, 0.01))
        controller = rig["controller"]
        controller.connect()
        assert _wait(lambda: controller.info.state == AppState.ERROR)
        assert "working server" in controller.info.message.lower()

    def test_empty_pool_after_collection_reports_no_servers(self, rig):
        rig["store"].configs.clear()
        rig["collector"].set_sources([FakeSource("dead", error="timeout")])
        controller = rig["controller"]
        controller.connect()
        assert _wait(lambda: controller.info.state == AppState.ERROR)
        assert "servers" in controller.info.message.lower()

    def test_proxy_failure_produces_an_error_and_cleans_up(self, rig):
        rig["proxy"].available = False
        controller = rig["controller"]
        controller.connect()
        assert _wait(lambda: controller.info.state == AppState.ERROR)
        assert rig["core"].is_alive() is False

    def test_tunnel_failure_produces_an_error(self, rig):
        rig["settings"].set("connection.mode", "tun")
        rig["tunnel"].available = True
        rig["tunnel"].fail = True
        controller = rig["controller"]
        controller.connect()
        assert _wait(lambda: controller.info.state == AppState.ERROR)
        assert rig["core"].is_alive() is False

    def test_connect_after_error_works(self, rig):
        rig["core"].fail_to_start = True
        controller = rig["controller"]
        controller.connect()
        _wait(lambda: controller.info.state == AppState.ERROR)
        rig["core"].fail_to_start = False
        controller.connect()
        assert _wait(lambda: controller.info.state == AppState.CONNECTED)


class TestCoreCrashRecovery:
    def test_crash_while_connected_drops_to_error_and_cleans_up(self, rig):
        rig["core"].crash_after = 0.3
        rig["core"].crash_once = True
        controller = rig["controller"]
        controller.manager.set_auto_restart(False)
        controller.connect()
        _wait(lambda: controller.info.state == AppState.CONNECTED)
        assert _wait(lambda: controller.info.state == AppState.ERROR)
        assert rig["proxy"].applied is False
        assert "lost" in controller.info.message.lower()


class TestServerSelection:
    def test_auto_mode_picks_a_working_server(self, rig):
        controller = rig["controller"]
        controller.connect()
        _wait(lambda: controller.info.state == AppState.CONNECTED)
        identities = {c.identity for c in sample_configs()}
        assert controller.info.server_identity in identities

    def test_manual_selection_is_used(self, rig):
        wanted = sample_configs()[2]
        rig["settings"].set("servers.selection", wanted.identity)
        controller = rig["controller"]
        controller.connect()
        _wait(lambda: controller.info.state == AppState.CONNECTED)
        assert controller.info.server_identity == wanted.identity

    def test_select_server_persists_the_choice(self, rig):
        controller = rig["controller"]
        wanted = sample_configs()[1]
        controller.select_server(wanted.identity)
        assert rig["settings"].get("servers.selection") == wanted.identity
        controller.select_server("auto")
        assert rig["settings"].get("servers.selection") == "auto"

    def test_empty_pool_triggers_collection_on_connect(self, rig):
        rig["store"].configs.clear()
        controller = rig["controller"]
        controller.connect()
        _wait(lambda: controller.info.state == AppState.CONNECTED, timeout=10.0)
        assert rig["store"].count() >= 2


class TestPortCollisions:
    def test_a_busy_port_is_worked_around(self, rig):
        import socket

        settings = rig["settings"]
        blocker = socket.socket()
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", int(settings.get("connection.socks_port"))))
        blocker.listen(4)
        controller = rig["controller"]
        try:
            controller.connect()
            assert _wait(lambda: controller.info.state == AppState.CONNECTED)
            # The core ended up on a different, working port.
            proxy_server = rig["registry"].values["ProxyServer"]
            assert proxy_server != ""
            used = int(proxy_server.rsplit(":", 1)[1])
            assert used != blocker.getsockname()[1]
        finally:
            blocker.close()
            controller.disconnect()
            _wait(lambda: controller.info.state == AppState.OFFLINE)


class TestDiagnostics:
    def test_diagnostics_contains_no_credentials(self, rig):
        controller = rig["controller"]
        controller.connect()
        _wait(lambda: controller.info.state == AppState.CONNECTED)
        import json

        blob = json.dumps(controller.diagnostics())
        assert "2f1c0f4e-1d0a-4a2a-9f5f-2c0f0a1b2c3d" not in blob
        assert "hunter2" not in blob

    def test_diagnostics_reports_pool_and_core(self, rig):
        controller = rig["controller"]
        data = controller.diagnostics()
        assert data["pool"]["total"] == len(sample_configs())
        assert "state" in data["core"]
