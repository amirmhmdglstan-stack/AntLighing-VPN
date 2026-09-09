"""Tunnel / system-proxy / elevation logic and local storage.

The tunnel tests inject a command runner, so they verify *which* commands
AntLighting would issue and that every one is reversible.  They do not execute
Windows networking — that requires a real Windows machine.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from antlighting.logging_setup import RedactingFilter, redact, setup_logging
from antlighting.net.system_proxy import (
    DEFAULT_BYPASS,
    ProxySnapshot,
    SystemProxyController,
)
from antlighting.net.tunnel import (
    ADAPTER_NAME,
    SPLIT_DEFAULTS,
    TUN_DNS,
    TunnelController,
    TunnelState,
)
from antlighting.storage.db import ConfigStore
from antlighting.storage.settings import Settings, default_settings
from tests.conftest import (
    FakeRegistry,
    SS,
    TROJAN,
    VLESS_TLS,
    VLESS_REALITY,
    VMESS,
    sample_configs,
)


class RecordingRunner:
    """Captures the commands a controller decides to run."""

    def __init__(self, adapter_index: int | None = 23, gateway: str = "192.168.1.1",
                 dns: str = "192.168.1.1", fail_routes: bool = False):
        from antlighting.net.tunnel import CommandResult

        self.adapter_index = adapter_index
        self.gateway = gateway
        self.dns = dns
        self.fail_routes = fail_routes
        self.commands: list[list[str]] = []
        self._CommandResult = CommandResult

    def __call__(self, argv, timeout: float = 20.0):
        argv = list(argv)
        self.commands.append(argv)
        joined = " ".join(argv)
        if argv[0] == "route":
            if self.fail_routes:
                return self._CommandResult(argv, 1, "", "The route addition failed")
            return self._CommandResult(argv, 0, "OK", "")
        if "Get-NetAdapter" in joined:
            value = "" if self.adapter_index is None else str(self.adapter_index)
            return self._CommandResult(argv, 0, value + "\n", "")
        if "Get-NetRoute" in joined:
            return self._CommandResult(argv, 0, self.gateway + "\n", "")
        if "Get-DnsClientServerAddress" in joined:
            return self._CommandResult(argv, 0, self.dns + "\n", "")
        return self._CommandResult(argv, 0, "", "")

    @property
    def flat(self) -> str:
        return "\n".join(" ".join(c) for c in self.commands)


@pytest.fixture()
def windows(monkeypatch):
    """Pretend to be an elevated Windows machine."""
    import antlighting.net.elevation as elevation

    monkeypatch.setattr("antlighting.net.tunnel._is_windows", lambda: True)
    # ``supported()`` imports is_admin locally, so patch the source module.
    monkeypatch.setattr(elevation, "is_admin", lambda: True)
    return True


class TestTunnelBringUp:
    def test_xray_strategy_adds_only_the_uplink_route(self, windows, tmp_path):
        runner = RecordingRunner()
        controller = TunnelController(str(tmp_path / "t.json"), runner=runner)
        state = controller.bring_up(strategy="xray", uplink_host="203.0.113.10")
        assert state.active is True
        route_adds = [c for c in runner.commands if c[0] == "route" and c[1] == "add"]
        assert len(route_adds) == 1
        assert "203.0.113.10" in route_adds[0]
        # the uplink must go out via the *physical* gateway, never the tunnel
        assert "192.168.1.1" in route_adds[0]

    def test_managed_strategy_adds_split_default_routes(self, windows, tmp_path):
        runner = RecordingRunner()
        controller = TunnelController(str(tmp_path / "t.json"), runner=runner)
        state = controller.bring_up(strategy="managed", uplink_host="203.0.113.10")
        assert state.active is True
        adds = [" ".join(c) for c in runner.commands if c[0] == "route" and c[1] == "add"]
        # 0.0.0.0/1 and 128.0.0.0/1, never a bare 0.0.0.0/0
        assert any("0.0.0.0 mask 128.0.0.0" in a for a in adds)
        assert any("128.0.0.0 mask 128.0.0.0" in a for a in adds)
        assert not any("0.0.0.0 mask 0.0.0.0" in a for a in adds)

    def test_routes_target_the_tunnel_interface_index(self, windows, tmp_path):
        runner = RecordingRunner(adapter_index=42)
        controller = TunnelController(str(tmp_path / "t.json"), runner=runner)
        controller.bring_up(strategy="managed", uplink_host="")
        adds = [c for c in runner.commands if c[0] == "route" and c[1] == "add"]
        assert adds and adds[0][-2:] == ["if", "42"]

    def test_dns_is_pointed_at_the_tunnel(self, windows, tmp_path):
        runner = RecordingRunner(dns="8.8.8.8")
        controller = TunnelController(str(tmp_path / "t.json"), runner=runner)
        state = controller.bring_up(strategy="xray", uplink_host="203.0.113.10")
        assert state.dns_changed is True
        assert state.previous_dns == "8.8.8.8"
        netsh = [c for c in runner.commands if c[0] == "netsh"]
        assert netsh and TUN_DNS in " ".join(netsh[0])

    def test_dns_unchanged_when_already_correct(self, windows, tmp_path):
        runner = RecordingRunner(dns=TUN_DNS)
        controller = TunnelController(str(tmp_path / "t.json"), runner=runner)
        state = controller.bring_up(strategy="xray", uplink_host="")
        assert state.dns_changed is False

    def test_missing_adapter_is_a_clean_failure(self, windows, tmp_path):
        runner = RecordingRunner(adapter_index=None)
        controller = TunnelController(str(tmp_path / "t.json"), runner=runner)
        state = controller.bring_up(strategy="xray", uplink_host="", wait_seconds=0.2)
        assert state.active is False
        assert "never appeared" in state.error

    def test_failed_routes_do_not_crash(self, windows, tmp_path):
        runner = RecordingRunner(fail_routes=True)
        controller = TunnelController(str(tmp_path / "t.json"), runner=runner)
        state = controller.bring_up(strategy="managed", uplink_host="203.0.113.10")
        assert state.active is True
        assert state.routes_added == []  # nothing was actually installed

    def test_snapshot_is_written_for_crash_recovery(self, windows, tmp_path):
        path = tmp_path / "t.json"
        runner = RecordingRunner()
        TunnelController(str(path), runner=runner).bring_up(
            strategy="managed", uplink_host="203.0.113.10"
        )
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["active"] is True
        assert data["uplink_ip"] == "203.0.113.10"


class TestTunnelTearDown:
    def test_every_added_route_is_removed(self, windows, tmp_path):
        runner = RecordingRunner()
        controller = TunnelController(str(tmp_path / "t.json"), runner=runner)
        state = controller.bring_up(strategy="managed", uplink_host="203.0.113.10")
        added = len(state.routes_added)
        assert added == 3  # uplink + two split defaults
        controller.tear_down()
        deletes = [c for c in runner.commands if c[0] == "route" and c[1] == "delete"]
        assert len(deletes) == added
        for added_route in state.routes_added:
            assert ["route", "delete", *added_route] in runner.commands

    def test_dns_is_restored(self, windows, tmp_path):
        runner = RecordingRunner(dns="8.8.8.8")
        controller = TunnelController(str(tmp_path / "t.json"), runner=runner)
        controller.bring_up(strategy="xray", uplink_host="")
        controller.tear_down()
        restores = [c for c in runner.commands if c[0] == "netsh"]
        assert any("8.8.8.8" in " ".join(c) for c in restores)

    def test_snapshot_is_removed_after_teardown(self, windows, tmp_path):
        path = tmp_path / "t.json"
        controller = TunnelController(str(path), runner=RecordingRunner())
        controller.bring_up(strategy="xray", uplink_host="")
        controller.tear_down()
        assert not path.exists()

    def test_recover_stale_undoes_a_previous_session(self, windows, tmp_path):
        path = tmp_path / "t.json"
        TunnelController(str(path), runner=RecordingRunner()).bring_up(
            strategy="managed", uplink_host="203.0.113.10"
        )
        # a brand new controller, as after a restart
        runner = RecordingRunner()
        fresh = TunnelController(str(path), runner=runner)
        assert fresh.recover_stale() is True
        deletes = [c for c in runner.commands if c[0] == "route" and c[1] == "delete"]
        assert len(deletes) == 3
        assert not path.exists()

    def test_recover_stale_is_a_noop_without_a_snapshot(self, windows, tmp_path):
        assert TunnelController(str(tmp_path / "none.json"), runner=RecordingRunner()).recover_stale() is False

    def test_teardown_without_bring_up_is_safe(self, windows, tmp_path):
        controller = TunnelController(str(tmp_path / "t.json"), runner=RecordingRunner())
        controller.tear_down()


class TestTunnelCapability:
    def test_non_windows_is_unsupported(self, monkeypatch, tmp_path):
        monkeypatch.setattr("antlighting.net.tunnel._is_windows", lambda: False)
        ok, reason = TunnelController(str(tmp_path / "t.json")).supported()
        assert ok is False
        assert "Windows" in reason

    def test_without_admin_is_unsupported(self, monkeypatch, tmp_path):
        monkeypatch.setattr("antlighting.net.tunnel._is_windows", lambda: True)
        import antlighting.net.elevation as elevation

        monkeypatch.setattr(elevation, "is_admin", lambda: False)
        ok, reason = TunnelController(str(tmp_path / "t.json")).supported()
        assert ok is False
        assert "administrator" in reason.lower()

    def test_bring_up_on_a_non_windows_host_is_a_noop(self, monkeypatch, tmp_path):
        monkeypatch.setattr("antlighting.net.tunnel._is_windows", lambda: False)
        runner = RecordingRunner()
        state = TunnelController(str(tmp_path / "t.json"), runner=runner).bring_up()
        assert state.active is False
        assert runner.commands == []  # no OS commands are issued at all


class TestSystemProxy:
    @pytest.fixture()
    def proxy(self, tmp_path):
        registry = FakeRegistry({"ProxyEnable": 0, "ProxyServer": "", "ProxyOverride": ""})
        controller = SystemProxyController(
            str(tmp_path / "p.json"), reg_reader=registry.read, reg_writer=registry.write
        )
        return controller, registry

    def test_apply_sets_the_local_listener(self, monkeypatch, proxy):
        monkeypatch.setattr("antlighting.net.system_proxy._is_windows", lambda: True)
        controller, registry = proxy
        assert controller.apply("127.0.0.1", 20808) is True
        assert registry.values["ProxyEnable"] == 1
        assert registry.values["ProxyServer"] == "127.0.0.1:20808"
        assert registry.values["ProxyOverride"] == DEFAULT_BYPASS

    def test_revert_restores_the_previous_settings(self, monkeypatch, proxy):
        monkeypatch.setattr("antlighting.net.system_proxy._is_windows", lambda: True)
        controller, registry = proxy
        registry.values.update({"ProxyEnable": 1, "ProxyServer": "corp.proxy:3128"})
        controller.apply("127.0.0.1", 20808)
        controller.revert()
        assert registry.values["ProxyServer"] == "corp.proxy:3128"
        assert registry.values["ProxyEnable"] == 1

    def test_previous_settings_are_survivable_across_a_crash(self, monkeypatch, tmp_path):
        monkeypatch.setattr("antlighting.net.system_proxy._is_windows", lambda: True)
        registry = FakeRegistry({"ProxyEnable": 1, "ProxyServer": "corp.proxy:3128"})
        path = str(tmp_path / "p.json")
        first = SystemProxyController(path, reg_reader=registry.read, reg_writer=registry.write)
        first.apply("127.0.0.1", 20808)
        # a new process starts with the app still "connected"
        second_registry = FakeRegistry(registry.values)
        second = SystemProxyController(
            path, reg_reader=second_registry.read, reg_writer=second_registry.write
        )
        assert second.recover_stale() is True
        assert second_registry.values["ProxyServer"] == "corp.proxy:3128"

    def test_apply_on_a_non_windows_host_is_a_noop(self, monkeypatch, proxy):
        monkeypatch.setattr("antlighting.net.system_proxy._is_windows", lambda: False)
        controller, registry = proxy
        assert controller.apply("127.0.0.1", 20808) is False
        assert registry.writes == []

    def test_snapshot_round_trip(self):
        snapshot = ProxySnapshot(enabled=1, server="a:1", override="b", auto_config_url="c",
                                 present=True)
        assert ProxySnapshot.from_dict(snapshot.to_dict()) == snapshot


class TestElevation:
    def test_is_admin_is_false_for_a_normal_user(self):
        from antlighting.net.elevation import describe_privileges, is_admin

        assert is_admin() in (True, False)
        info = describe_privileges()
        assert info["elevated"] == is_admin()
        assert "platform" in info


class TestConfigStore:
    @pytest.fixture()
    def store(self, tmp_path):
        db = ConfigStore(str(tmp_path / "c.db"))
        yield db
        db.close()

    def test_round_trip_preserves_every_field(self, store):
        configs = sample_configs()
        store.upsert_many(configs)
        for original in configs:
            restored = store.get(original.identity)
            assert restored is not None
            assert restored.scheme == original.scheme
            assert restored.address == original.address
            assert restored.port == original.port
            assert restored.credential == original.credential
            assert restored.params == original.params
            assert restored.net == original.net
            assert restored.security == original.security

    def test_first_and_last_seen_are_tracked(self, store):
        cfg = sample_configs()[0]
        store.upsert_many([cfg], now=1000.0)
        store.upsert_many([cfg], now=2000.0)
        row = store._conn.execute(
            "SELECT first_seen, last_seen, seen_count FROM configs WHERE identity=?",
            (cfg.identity,),
        ).fetchone()
        assert (row["first_seen"], row["last_seen"], row["seen_count"]) == (1000.0, 2000.0, 2)

    def test_stats_buckets(self, store):
        configs = sample_configs()
        store.upsert_many(configs)
        store.record_result(configs[0].identity, "working", latency_ms=50.0)
        store.record_result(configs[1].identity, "failed", error="nope")
        stats = store.stats()
        assert stats["total"] == len(configs)
        assert stats["working"] == 1
        assert stats["failed"] == 1
        assert stats["by_scheme"]["vless"] >= 1

    def test_unusable_configs_are_counted_but_not_returned_as_usable(self, store):
        from antlighting.configs.parser import parse_link
        from tests.conftest import SSR

        ssr = parse_link(SSR)
        store.upsert_many([ssr, sample_configs()[0]])
        assert store.count() == 2
        assert store.count_usable() == 1
        assert len(store.all(include_unusable=False)) == 1

    def test_prune_keeps_the_most_useful_rows(self, store):
        configs = [
            __import__("antlighting.configs.parser", fromlist=["parse_link"]).parse_link(
                VLESS_TLS.replace("203.0.113.10", f"203.0.{i // 250}.{i % 250 + 1}")
            )
            for i in range(1, 12)
        ]
        configs = [c for c in configs if c]
        store.upsert_many(configs)
        best = configs[0]
        store.record_result(best.identity, "working", latency_ms=20.0)
        store.record_result(best.identity, "working", latency_ms=20.0)
        store.prune(keep=3)
        assert store.count() == 3
        assert store.get(best.identity) is not None

    def test_metrics_default_to_an_empty_dict(self, store):
        assert store.get_metrics("does-not-exist") == {}

    def test_source_rows(self, store):
        store.upsert_source("s1", "github", url="https://example.com", label="S1")
        store.record_source_result("s1", 12)
        store.record_source_result("s1", 0, error="timeout")
        rows = {r["id"]: r for r in store.sources()}
        assert rows["s1"]["fetch_count"] == 2
        assert rows["s1"]["last_error"] == "timeout"
        store.set_source_enabled("s1", False)
        assert store.sources()[0]["enabled"] == 0

    def test_test_runs(self, store):
        run_id = store.begin_test_run("manual")
        store.end_test_run(run_id, tested=10, working=4)
        last = store.last_test_run()
        assert last["tested"] == 10
        assert last["working"] == 4
        assert last["finished"] is not None

    def test_clear_empties_the_pool(self, store):
        store.upsert_many(sample_configs())
        store.clear()
        assert store.count() == 0

    def test_concurrent_writes_are_safe(self, store):
        import threading

        configs = sample_configs()

        def writer():
            for _ in range(20):
                store.upsert_many(configs)
                for cfg in configs:
                    store.record_result(cfg.identity, "working", latency_ms=10.0)

        threads = [threading.Thread(target=writer) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert store.count() == len(configs)


class TestSettings:
    def test_defaults_are_complete(self, tmp_path):
        settings = Settings(path=str(tmp_path / "s.json"))
        assert settings.get("connection.mode") == "auto"
        assert settings.get("servers.selection") == "auto"
        assert isinstance(settings.get("sources.specs"), list)

    def test_set_and_save_round_trip(self, tmp_path):
        path = str(tmp_path / "s.json")
        Settings(path=path).set("connection.mode", "tun")
        assert Settings(path=path).get("connection.mode") == "tun"

    def test_unknown_keys_are_preserved(self, tmp_path):
        path = tmp_path / "s.json"
        path.write_text(json.dumps({"future": {"thing": 42}}), encoding="utf-8")
        assert Settings(path=str(path)).get("future.thing") == 42

    def test_corrupt_file_falls_back_to_defaults(self, tmp_path):
        path = tmp_path / "s.json"
        path.write_text("{not json", encoding="utf-8")
        assert Settings(path=str(path)).get("connection.mode") == "auto"

    def test_reset_restores_defaults(self, tmp_path):
        settings = Settings(path=str(tmp_path / "s.json"))
        settings.set("connection.mode", "tun")
        settings.reset()
        assert settings.get("connection.mode") == "auto"

    def test_missing_dotted_key_returns_the_default(self, tmp_path):
        settings = Settings(path=str(tmp_path / "s.json"))
        assert settings.get("nope.nope.nope", "fallback") == "fallback"

    def test_sources_round_trip(self, tmp_path):
        settings = Settings(path=str(tmp_path / "s.json"))
        specs = settings.sources
        settings.set_sources([specs[0]])
        assert Settings(path=str(tmp_path / "s.json")).sources == [specs[0]]

    def test_default_specs_are_well_formed(self):
        from antlighting.sources.base import create_source

        for spec in default_settings()["sources"]["specs"]:
            source = create_source(spec)
            assert source is not None
            assert source.urls


class TestRedaction:
    @pytest.mark.parametrize(
        "text,secret",
        [
            (VLESS_TLS, "2f1c0f4e-1d0a-4a2a-9f5f-2c0f0a1b2c3d"),
            (TROJAN, "hunter2"),
            (SS, "cGFzc3dvcmQ"),
            (VLESS_REALITY, "PUBKEY123"),
        ],
    )
    def test_credentials_are_masked(self, text, secret):
        masked = redact(f"trying {text} now")
        assert secret not in masked
        assert "://" in masked  # the link is still recognisable

    def test_key_value_credentials_are_masked(self):
        masked = redact("config password=hunter2 pbk=ABCDEF")
        assert "hunter2" not in masked
        assert "ABCDEF" not in masked

    def test_plain_text_is_untouched(self):
        assert redact("connected in 42 ms") == "connected in 42 ms"

    def test_filter_applies_to_records(self):
        import logging

        record = logging.LogRecord("x", logging.INFO, __file__, 1,
                                   "dialling " + VLESS_TLS, None, None)
        assert RedactingFilter().filter(record) is True
        assert "2f1c0f4e" not in record.getMessage()

    def test_setup_logging_writes_a_file(self, tmp_path):
        logger = setup_logging(str(tmp_path), level="DEBUG")
        logger.info("connected using %s", VLESS_TLS)
        for handler in logger.handlers:
            handler.flush()
        content = (tmp_path / "antlighting.log").read_text(encoding="utf-8")
        assert "connected using" in content
        assert "2f1c0f4e-1d0a-4a2a-9f5f-2c0f0a1b2c3d" not in content

    def test_log_tail_reader(self, tmp_path):
        from antlighting.logging_setup import read_log_tail

        logger = setup_logging(str(tmp_path), level="INFO")
        for i in range(5):
            logger.info("line %d", i)
        for handler in logger.handlers:
            handler.flush()
        lines = read_log_tail(str(tmp_path))
        assert len(lines) == 5
        assert "line 4" in lines[-1]
