"""The Python↔QML bridge, and a lint gate for the QML itself.

The bridge only needs ``QCoreApplication``, so the whole surface the QML reads
from is exercised here without a display server.  QML files cannot be
instantiated in this sandbox (no OpenGL/X11 libraries), so they are verified
with Qt's own ``qmllint`` instead.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication  # noqa: E402

from antlighting.collector import ConfigCollector  # noqa: E402
from antlighting.controller import AppController, AppState  # noqa: E402
from antlighting.storage.settings import Settings  # noqa: E402
from antlighting.testing.tester import ServerTester  # noqa: E402
from antlighting.ui.bridge import AppBridge, ServerListModel, _status_label  # noqa: E402
from tests.conftest import (  # noqa: E402
    FakeCore,
    FakeProbe,
    FakeSource,
    FakeStore,
    TROJAN,
    VLESS_TLS,
    sample_configs,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(REPO_ROOT, "antlighting", "ui")


@pytest.fixture(scope="module")
def qapp():
    app = QCoreApplication.instance() or QCoreApplication([])
    return app


@pytest.fixture()
def rig(tmp_path, qapp):
    from tests.test_controller import RecordingProxy, RecordingTunnel
    from tests.conftest import FakeRegistry

    settings = Settings(path=str(tmp_path / "settings.json"))
    from tests.conftest import free_port

    settings.set("connection.socks_port", free_port(), save=False)
    settings.set("connection.http_port", 0, save=False)

    store = FakeStore()
    store.upsert_many(sample_configs())
    for cfg in sample_configs():
        store.record_result(cfg.identity, "working", latency_ms=140.0)

    registry = FakeRegistry({"ProxyEnable": 0, "ProxyServer": "", "ProxyOverride": ""})
    controller = AppController(
        settings=settings,
        store=store,
        core=FakeCore(),
        tester=ServerTester(FakeProbe(default=("working", 140.0, 0.01)), store=store, timeout=2.0),
        collector=ConfigCollector(store, [FakeSource("s", VLESS_TLS + "\n" + TROJAN)]),
        system_proxy=RecordingProxy(str(tmp_path / "p.json"), registry),
        tunnel=RecordingTunnel(str(tmp_path / "t.json"), available=False),
    )
    controller._verify_connection = lambda port, attempts=3: True
    bridge = AppBridge(controller)
    yield {"bridge": bridge, "controller": controller, "store": store, "settings": settings}
    controller.shutdown()


class TestBridgeProperties:
    def test_initial_state(self, rig):
        bridge = rig["bridge"]
        assert bridge.state == AppState.OFFLINE
        assert bridge.serverName == "Auto"
        assert bridge.latencyText == "--"
        assert bridge.busy is False

    def test_pool_counters_are_exposed(self, rig):
        bridge = rig["bridge"]
        assert bridge.poolTotal == len(sample_configs())

    def test_core_version_is_reported(self, rig):
        assert "FakeCore" in rig["bridge"].coreVersion

    def test_flags_are_derived_from_country(self, rig):
        bridge = rig["bridge"]
        controller = rig["controller"]
        german = [c for c in sample_configs() if c.country_code == "DE"]
        if german:
            controller._emit(server_country_code="DE", server_name="Germany #1")
            assert bridge.serverFlag == "\U0001F1E9\U0001F1EA"

    def test_latency_text_is_formatted(self, rig):
        bridge = rig["bridge"]
        rig["controller"]._emit(latency_ms=83.4)
        assert bridge.latencyText == "83 ms"


class TestBridgeSignals:
    def test_state_changes_emit(self, rig):
        bridge = rig["bridge"]
        seen: list[str] = []
        bridge.stateChanged.connect(lambda: seen.append(bridge.state))
        rig["controller"]._emit(state=AppState.CONNECTING)
        assert AppState.CONNECTING in seen

    def test_busy_tracks_the_connecting_states(self, rig):
        bridge = rig["bridge"]
        seen: list[bool] = []
        bridge.busyChanged.connect(lambda: seen.append(bridge.busy))
        rig["controller"]._emit(state=AppState.CONNECTING)
        rig["controller"]._emit(state=AppState.CONNECTED)
        assert True in seen
        assert bridge.busy is False

    def test_servers_list_is_refreshed_on_state_change(self, rig):
        bridge = rig["bridge"]
        rig["controller"]._emit(state=AppState.CONNECTED)
        assert len(bridge.servers.servers) >= 2


class TestBridgeActions:
    def test_select_server_updates_the_selection(self, rig):
        bridge = rig["bridge"]
        wanted = sample_configs()[1]
        bridge.selectServer(wanted.identity)
        assert rig["settings"].get("servers.selection") == wanted.identity

    def test_select_auto(self, rig):
        bridge = rig["bridge"]
        bridge.selectServer("auto")
        assert rig["settings"].get("servers.selection") == "auto"

    def test_set_setting_and_read_back(self, rig):
        bridge = rig["bridge"]
        bridge.setSetting("connection.mode", "tun")
        assert bridge.setting("connection.mode") == "tun"

    def test_import_configs(self, rig):
        bridge = rig["bridge"]
        added = bridge.importConfigs(VLESS_TLS + "\n" + TROJAN)
        assert added >= 0
        assert rig["store"].count() >= len(sample_configs())

    def test_diagnostics_returns_a_map(self, rig):
        data = rig["bridge"].diagnostics()
        assert "core" in data and "pool" in data

    def test_log_tail_is_a_string(self, rig):
        assert isinstance(rig["bridge"].logTail(), str)

    def test_set_mode_persists(self, rig):
        bridge = rig["bridge"]
        bridge.setMode("tun")
        assert rig["settings"].get("connection.mode") == "tun"

    def test_source_round_trip(self, rig):
        bridge = rig["bridge"]
        specs = bridge.settingSources()
        assert specs
        bridge.saveSources([specs[0]])
        assert len(bridge.settingSources()) == 1
        bridge.restoreDefaultSources()
        assert len(bridge.settingSources()) == len(specs)

    def test_connect_and_disconnect_through_the_bridge(self, rig):
        import time

        bridge = rig["bridge"]
        bridge.connectVpn()
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline and bridge.state != AppState.CONNECTED:
            qapp_process(20)
        assert bridge.state == AppState.CONNECTED
        bridge.disconnectVpn()
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline and bridge.state != AppState.OFFLINE:
            qapp_process(20)
        assert bridge.state == AppState.OFFLINE


def qapp_process(ms: int) -> None:
    app = QCoreApplication.instance()
    if app is not None:
        app.processEvents()
    import time

    time.sleep(ms / 1000.0)


class TestServerListModel:
    def test_first_row_is_always_auto(self, rig):
        rows = rig["bridge"].servers.servers
        assert rows[0]["identity"] == "auto"
        assert rows[0]["name"] == "Auto"

    def test_rows_carry_display_fields(self, rig):
        rows = rig["bridge"].servers.servers[1:]
        assert rows
        for row in rows:
            assert row["name"]
            assert row["flag"]
            assert row["statusText"]
            assert "identity" in row

    def test_selected_row_is_marked(self, rig):
        bridge = rig["bridge"]
        target = bridge.servers.servers[1]["identity"]
        bridge.selectServer(target)
        marked = [r for r in bridge.servers.servers if r["selected"]]
        assert len(marked) == 1
        assert marked[0]["identity"] == target

    def test_status_labels(self):
        cfg = sample_configs()[0]
        assert _status_label("working", cfg) == "Excellent"
        assert _status_label("timeout", cfg) == "Timeout"
        from antlighting.configs.parser import parse_link
        from tests.conftest import SSR

        ssr = parse_link(SSR)
        assert _status_label("unknown", ssr) == "Unsupported"


# --------------------------------------------------------------- QML lint gate
def _qmllint_command() -> list[str] | None:
    for candidate in ("pyside6-qmllint", os.path.join(sys.prefix, "bin", "pyside6-qmllint")):
        found = shutil.which(candidate)
        if found:
            return [found]
    try:
        from PySide6 import __path__ as pyside_path

        local = os.path.join(os.path.dirname(pyside_path[0]), "qmllint")
        if os.path.isfile(local):
            return [local]
    except Exception:  # noqa: BLE001
        pass
    return None


@pytest.mark.parametrize("qml", sorted(glob.glob(os.path.join(UI_DIR, "*.qml"))),
                         ids=lambda p: os.path.basename(p))
def test_qml_passes_qmllint_without_errors(qml):
    """Qt's own linter must not report an error in any QML file."""
    command = _qmllint_command()
    if command is None:
        pytest.skip("pyside6-qmllint is not installed")
    completed = subprocess.run(
        command + [qml],
        capture_output=True,
        text=True,
        timeout=180,
        env={**os.environ, "LD_LIBRARY_PATH": os.environ.get("ANTLIGHTING_QT_LIB", "")},
    )
    output = completed.stdout + completed.stderr
    errors = [line for line in output.splitlines() if line.startswith("Error:")]
    assert not errors, "\n".join(errors)
    # Property shadowing is a real bug class, not a style nit.
    shadow = [line for line in output.splitlines() if "property-override" in line]
    assert not shadow, "\n".join(shadow)


def test_qml_files_reference_only_existing_helpers():
    """Guard against a renamed theme helper silently breaking every screen."""
    import re

    theme_path = os.path.join(UI_DIR, "theme.js")
    assert os.path.isfile(theme_path)
    theme = open(theme_path, encoding="utf-8").read()
    helpers = set(re.findall(r"^function (\w+)", theme, re.MULTILINE))
    assert {"theme", "statusColor", "stateAccent", "fontSizes"} <= helpers

    used = set()
    for qml in glob.glob(os.path.join(UI_DIR, "*.qml")):
        text = open(qml, encoding="utf-8").read()
        used |= set(re.findall(r"Theme\.(\w+)\(", text))
    missing = {u for u in used if u not in helpers}
    assert not missing, f"QML calls missing theme helpers: {missing}"


def test_mascot_does_not_shadow_item_properties():
    """`state`/`palette` are QQuickItem members; the mascot must not reuse them."""
    text = open(os.path.join(UI_DIR, "AntMascot.qml"), encoding="utf-8").read()
    assert "property string state:" not in text
    assert "property var palette:" not in text


def test_no_uppercase_custom_property_names():
    """QML rejects custom properties starting with a capital at *runtime* even
    though qmllint tolerates them, so guard it statically."""
    import re

    pattern = re.compile(r"property\s+(?:readonly\s+)?\w+\s+([A-Z][A-Za-z0-9_]*)\s*:")
    offenders = []
    for qml in glob.glob(os.path.join(UI_DIR, "*.qml")):
        for lineno, line in enumerate(
            open(qml, encoding="utf-8").read().splitlines(), 1
        ):
            m = pattern.search(line)
            if m:
                offenders.append(f"{os.path.basename(qml)}:{lineno}: {m.group(1)}")
    assert not offenders, f"uppercase property names: {offenders}"
