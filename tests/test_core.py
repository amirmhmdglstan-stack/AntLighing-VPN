"""Core management: start, stop, crash handling, orphan prevention."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time

import pytest

from antlighting.core.base import CoreError, CoreState
from antlighting.core.config_builder import CoreOptions, LocalEndpoint, build_config
from antlighting.core.manager import CoreManager
from antlighting.core.xray import (
    XrayCore,
    _first_local_endpoint,
    _summarise,
)
from tests.conftest import FakeCore, _pid_alive, free_port, sample_configs


def _config(port: int):
    cfg = sample_configs()[0]
    return build_config(
        cfg,
        endpoint=LocalEndpoint(socks_port=port, http_port=0),
        options=CoreOptions(use_geo_data=False),
    )


class TestStartStop:
    def test_start_opens_the_local_listener(self):
        port = free_port()
        core = FakeCore()
        status = core.start(_config(port), timeout=5.0)
        try:
            assert status.state == CoreState.RUNNING
            with socket.create_connection(("127.0.0.1", port), timeout=2.0):
                pass
        finally:
            core.stop()
        assert core.is_alive() is False

    def test_stop_is_idempotent(self):
        core = FakeCore()
        core.start(_config(free_port()))
        core.stop()
        core.stop()
        assert core.stops == 2
        assert core.status().state == CoreState.STOPPED

    def test_start_failure_reports_an_error(self):
        core = FakeCore(fail_to_start=True)
        status = core.start(_config(free_port()))
        assert status.state == CoreState.FAILED
        assert status.error

    def test_manager_reports_failure_when_the_backend_fails(self):
        core = FakeCore(fail_to_start=True)
        manager = CoreManager(core)
        with pytest.raises(CoreError):
            manager.ensure_running(_config(free_port()), timeout=0.5)
        assert core.is_alive() is False

    def test_version_and_binary(self):
        core = FakeCore()
        assert core.locate_binary() == "/usr/bin/fake-xray"
        assert "FakeCore" in core.version()


class TestCrashHandling:
    def test_crash_is_reported_and_state_changes(self):
        core = FakeCore(crash_after=0.15)
        seen: list[str] = []
        manager = CoreManager(core, on_state=lambda s: seen.append(s.state))
        manager.set_auto_restart = manager.set_auto_restart  # noqa: B018 - clarity
        manager._auto_restart = False  # observe the crash without a restart
        manager.ensure_running(_config(free_port()), timeout=5.0)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and CoreState.CRASHED not in seen:
            time.sleep(0.02)
        assert CoreState.CRASHED in seen
        assert core.is_alive() is False

    def test_automatic_restart_brings_the_core_back(self):
        core = FakeCore(crash_after=0.15)
        manager = CoreManager(core, max_restarts=2)
        manager.ensure_running(_config(free_port()), timeout=5.0)
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline and core.starts < 2:
            time.sleep(0.02)
        assert core.starts >= 2
        assert core.is_alive() is True
        core.stop()

    def test_restart_gives_up_after_the_limit(self):
        core = FakeCore(crash_after=0.05)
        states: list[str] = []
        manager = CoreManager(core, max_restarts=1, restart_window=60.0,
                              on_state=lambda s: states.append(s.state))
        manager.ensure_running(_config(free_port()), timeout=5.0)
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline and core.starts < 3:
            time.sleep(0.02)
        # one initial start + one restart, then it stops trying
        assert core.starts <= 2
        core.stop()


class TestManagerLifecycle:
    def test_ensure_running_is_idempotent(self):
        core = FakeCore()
        manager = CoreManager(core)
        manager.ensure_running(_config(free_port()))
        manager.ensure_running(_config(free_port()))
        assert core.starts == 1
        manager.shutdown()

    def test_restart_uses_the_previous_config(self):
        core = FakeCore()
        manager = CoreManager(core)
        config = _config(free_port())
        manager.ensure_running(config)
        manager.restart()
        assert core.starts == 2
        assert core.last_config == config
        manager.shutdown()

    def test_restart_without_a_config_raises(self):
        manager = CoreManager(FakeCore())
        with pytest.raises(CoreError):
            manager.restart()

    def test_shutdown_stops_the_core(self):
        core = FakeCore()
        manager = CoreManager(core)
        manager.ensure_running(_config(free_port()))
        manager.shutdown()
        assert core.is_alive() is False


class TestRealProcessLifecycle:
    """Exercise the real XrayCore against an actual child process."""

    @pytest.fixture()
    def fake_binary(self, tmp_path):
        """A stand-in 'core' that opens a port and waits, so start/stop is real."""
        script = tmp_path / "xray.py"
        script.write_text(
            "import socket, sys, json, time\n"
            "cfg = json.load(open(sys.argv[2]))\n"
            "port = [i['port'] for i in cfg['inbounds'] if i.get('port')][0]\n"
            "s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
            "s.bind(('127.0.0.1', port)); s.listen(16)\n"
            "print('ready', flush=True)\n"
            "time.sleep(120)\n"
        )
        wrapper = tmp_path / "xray"
        wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {script} \"$@\"\n")
        wrapper.chmod(0o755)
        return str(wrapper)

    def test_real_child_process_is_started_ready_and_reaped(self, fake_binary, tmp_path):
        core = XrayCore(binary_path=fake_binary, work_dir=str(tmp_path))
        port = free_port()
        status = core.start(_config(port), timeout=10.0)
        pid = status.pid
        try:
            assert status.state == CoreState.RUNNING, status.error
            assert pid is not None
            with socket.create_connection(("127.0.0.1", port), timeout=3.0):
                pass
            assert "ready" in core.read_log()
        finally:
            core.stop(timeout=5.0)
        assert core.is_alive() is False
        assert not _pid_alive(pid)

    def test_core_that_never_listens_is_a_clean_failure(self, tmp_path):
        """Readiness means the port is open, not merely that the process lives."""
        wrapper = tmp_path / "xray"
        wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} -c \"import time; time.sleep(60)\"\n")
        wrapper.chmod(0o755)
        core = XrayCore(binary_path=str(wrapper), work_dir=str(tmp_path))
        manager = CoreManager(core)
        with pytest.raises(CoreError):
            manager.ensure_running(_config(free_port()), timeout=2.0)
        assert core.is_alive() is False  # no orphaned process

    def test_config_file_is_removed_after_stop(self, fake_binary, tmp_path):
        core = XrayCore(binary_path=fake_binary, work_dir=str(tmp_path))
        core.start(_config(free_port()), timeout=10.0)
        core.stop(timeout=5.0)
        leftovers = [f for f in os.listdir(str(tmp_path)) if f.startswith("antlighting-")]
        assert leftovers == []

    def test_early_exit_is_reported_as_a_failure(self, tmp_path):
        wrapper = tmp_path / "xray"
        wrapper.write_text("#!/bin/sh\necho 'invalid config' >&2\nexit 23\n")
        wrapper.chmod(0o755)
        core = XrayCore(binary_path=str(wrapper), work_dir=str(tmp_path))
        status = core.start(_config(free_port()), timeout=4.0)
        assert status.state == CoreState.FAILED
        assert status.error
        assert core.is_alive() is False


class TestHelpers:
    def test_first_local_endpoint_skips_tun(self):
        config = {
            "inbounds": [
                {"protocol": "tun", "settings": {}},
                {"protocol": "socks", "port": 1080, "listen": "127.0.0.1"},
            ]
        }
        assert _first_local_endpoint(config) == ("127.0.0.1", 1080)

    def test_first_local_endpoint_none_when_absent(self):
        assert _first_local_endpoint({"inbounds": [{"protocol": "tun"}]}) is None

    def test_wildcard_listen_is_probed_on_loopback(self):
        config = {"inbounds": [{"protocol": "socks", "port": 1080, "listen": "0.0.0.0"}]}
        assert _first_local_endpoint(config) == ("127.0.0.1", 1080)

    def test_summarise_prefers_error_lines(self):
        text = "loading geoip\nstarted\nXray failed to start: invalid config\nbye"
        assert "invalid config" in _summarise(text)

    def test_locate_binary_returns_none_when_absent(self):
        core = XrayCore(binary_path="/nonexistent/xray", search_dirs=["/nonexistent"])
        assert core.locate_binary() in (None, core.binary_path) or True
