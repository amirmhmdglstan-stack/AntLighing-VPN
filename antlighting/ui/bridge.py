"""QML bridge.

Exposes the Qt-free :class:`AppController` to the QML layer as a QObject with
notifying properties.  State changes arrive from worker threads; Qt's queued
connections marshal them onto the GUI thread automatically.
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from ..configs.naming import flag_for, friendly_name
from ..controller import AppController, AppState

log = logging.getLogger(__name__)


class ServerListModel(QObject):
    """A flat list of servers for the server sheet."""

    serversChanged = Signal()
    testingChanged = Signal()

    def __init__(self, controller: AppController, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._servers: list[dict[str, Any]] = []
        self._testing = False

    def refresh(self) -> None:
        store = self._controller.store
        configs = store.all(include_unusable=True)
        tester = self._controller.tester
        now = __import__("time").time()
        scored = tester.rank(configs, now=now)

        # Group by country so the list reads like a VPN server list, not a dump.
        counters: dict[str, int] = {}
        rows: list[dict[str, Any]] = []
        auto = {
            "identity": "auto",
            "name": "Auto",
            "subtitle": "Best available server",
            "flag": "\U0001F310",
            "scheme": "",
            "latencyText": "--",
            "latencyMs": -1.0,
            "status": "auto",
            "statusText": "Recommended",
            "trusted": False,
            "selected": str(self._controller.settings.get("servers.selection", "auto")) == "auto",
        }
        rows.append(auto)

        selection = str(self._controller.settings.get("servers.selection", "auto"))
        for entry in scored:
            config = entry.config
            country_key = entry.config.country_code or "?"
            counters[country_key] = counters.get(country_key, 0) + 1
            if config.name:
                name = config.name
            else:
                name = friendly_name(
                    config.country, config.country_code, counters[country_key], config.scheme
                )
            rows.append(
                {
                    "identity": config.identity,
                    "name": name,
                    "subtitle": f"{config.scheme_label} · {config.address}",
                    "flag": flag_for(config.country_code),
                    "scheme": config.scheme_label,
                    "latencyText": (
                        f"{entry.latency_ms:.0f} ms" if entry.latency_ms else "--"
                    ),
                    "latencyMs": float(entry.latency_ms or -1.0),
                    "status": entry.status,
                    "statusText": _status_label(entry.status, config),
                    "trusted": bool(config.unusable_reason == ""),
                    "selected": config.identity == selection,
                }
            )
        self._servers = rows
        self.serversChanged.emit()

    @Property("QVariantList", notify=serversChanged)
    def servers(self) -> list[dict[str, Any]]:
        return list(self._servers)

    @Property(bool, notify=testingChanged)
    def testing(self) -> bool:
        return self._testing

    def set_testing(self, value: bool) -> None:
        if value != self._testing:
            self._testing = value
            self.testingChanged.emit()


def _status_label(status: str, config: Any) -> str:
    if not config.usable:
        return "Unsupported"
    return {
        "working": "Excellent",
        "slow": "Slow",
        "unknown": "Untested",
        "testing": "Testing…",
        "failed": "Failed",
        "timeout": "Timeout",
        "invalid": "Invalid",
        "unavailable": "Unavailable",
    }.get(status, status.title())


class AppBridge(QObject):
    """Everything the QML UI reads from or calls into."""

    # --- state ---------------------------------------------------------------
    stateChanged = Signal()
    messageChanged = Signal()
    detailChanged = Signal()
    progressChanged = Signal()
    serverNameChanged = Signal()
    serverFlagChanged = Signal()
    latencyChanged = Signal()
    coreStateChanged = Signal()
    coreVersionChanged = Signal()
    poolChanged = Signal()
    busyChanged = Signal()
    modeChanged = Signal()
    elevatedChanged = Signal()
    errorChanged = Signal()

    def __init__(self, controller: AppController, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.servers = ServerListModel(controller, self)
        self._info = controller.info
        self._busy = False

        controller.set_state_callback(self._on_state)
        controller.set_message_callback(self._on_message)
        controller.refresh_pool_counters()
        # Populate immediately: the QML list binds to this on first frame, so an
        # empty list would flash before anything else refreshed it.
        self.servers.refresh()

        # Keep the pool counters fresh without hammering the database.
        self._poll = QTimer(self)
        self._poll.setInterval(5000)
        self._poll.timeout.connect(self._poll_tick)
        self._poll.start()

    # ------------------------------------------------------------------ plumbing
    def _on_state(self, info: Any) -> None:
        previous_state = self._info.state
        previous_busy = self._busy
        self._info = info
        self.stateChanged.emit()
        self.messageChanged.emit()
        self.detailChanged.emit()
        self.progressChanged.emit()
        self.serverNameChanged.emit()
        self.serverFlagChanged.emit()
        self.latencyChanged.emit()
        self.coreStateChanged.emit()
        self.poolChanged.emit()
        self.modeChanged.emit()
        self.elevatedChanged.emit()
        self.errorChanged.emit()
        busy = info.state in (AppState.CONNECTING, AppState.DISCONNECTING) or info.testing
        if busy != previous_busy:
            self._busy = busy
            self.busyChanged.emit()
        if info.state != previous_state:
            self.servers.refresh()

    def _on_message(self, message: str) -> None:
        log.debug("ui message: %s", message)

    def _poll_tick(self) -> None:
        self.controller.refresh_pool_counters()
        self.coreVersionChanged.emit()

    # ------------------------------------------------------------------ actions
    @Slot()
    def toggle(self) -> None:
        self.controller.toggle()

    @Slot()
    def connectVpn(self) -> None:
        self.controller.connect()

    @Slot()
    def disconnectVpn(self) -> None:
        self.controller.disconnect()

    @Slot()
    def testServers(self) -> None:
        self.controller.test_servers()

    @Slot()
    def refreshSources(self) -> None:
        self.controller.collect_sources()

    @Slot(str)
    def selectServer(self, identity: str) -> None:
        self.controller.select_server(identity)
        self.servers.refresh()

    @Slot(str)
    def setMode(self, mode: str) -> None:
        self.controller.settings.set("connection.mode", mode)
        self.modeChanged.emit()

    @Slot(str, "QVariant")
    def setSetting(self, dotted: str, value: Any) -> None:
        self.controller.settings.set(dotted, value)

    @Slot(str, result="QVariant")
    def setting(self, dotted: str) -> Any:
        return self.controller.settings.get(dotted)

    @Slot()
    def resetSettings(self) -> None:
        self.controller.settings.reset()
        self.stateChanged.emit()

    @Slot(str, result="QVariant")
    def importConfigs(self, text: str) -> int:
        return self.controller.collector.ingest_text(
            text, source_id="clipboard", trusted=True
        )

    @Slot(result="QVariantMap")
    def diagnostics(self) -> dict[str, Any]:
        return self.controller.diagnostics()

    @Slot(result=str)
    def logTail(self) -> str:
        from ..logging_setup import read_log_tail
        from ..storage.settings import settings_dir

        return "".join(read_log_tail(settings_dir(), lines=400))

    @Slot(result=str)
    def coreLogTail(self) -> str:
        return self.controller.manager.backend.read_log(65536)

    @Slot(result="QVariantList")
    def sourceRows(self) -> list[dict[str, Any]]:
        rows = []
        for row in self.controller.store.sources():
            rows.append(
                {
                    "id": row.get("id", ""),
                    "label": row.get("label") or row.get("id", ""),
                    "kind": row.get("kind", ""),
                    "enabled": bool(row.get("enabled")),
                    "lastCount": int(row.get("last_count") or 0),
                    "lastError": row.get("last_error") or "",
                }
            )
        return rows

    @Slot(result="QVariantList")
    def settingSources(self) -> list[dict[str, Any]]:
        return list(self.controller.settings.sources)

    @Slot("QVariantList")
    def saveSources(self, specs: list[dict[str, Any]]) -> None:
        self.controller.settings.set_sources([dict(s) for s in specs])
        self._reload_collector()

    @Slot()
    def restoreDefaultSources(self) -> None:
        from ..sources import default_source_specs

        self.controller.settings.set_sources(default_source_specs())
        self._reload_collector()

    def _reload_collector(self) -> None:
        from ..sources.base import create_source

        sources = []
        for spec in self.controller.settings.sources:
            source = create_source(spec)
            if source is not None:
                sources.append(source)
        self.controller.collector.set_sources(sources)

    @Slot()
    def shutdown(self) -> None:
        self.controller.shutdown()

    # --------------------------------------------------------------- properties
    @Property(str, notify=stateChanged)
    def state(self) -> str:
        return self._info.state

    @Property(str, notify=messageChanged)
    def message(self) -> str:
        return self._info.message

    @Property(str, notify=detailChanged)
    def detail(self) -> str:
        return self._info.detail

    @Property(float, notify=progressChanged)
    def progress(self) -> float:
        return float(self._info.progress)

    @Property(str, notify=serverNameChanged)
    def serverName(self) -> str:
        return self._info.server_name or "Auto"

    @Property(str, notify=serverFlagChanged)
    def serverFlag(self) -> str:
        return flag_for(self._info.server_country_code)

    @Property(str, notify=latencyChanged)
    def latencyText(self) -> str:
        latency = self._info.latency_ms
        return f"{latency:.0f} ms" if latency else "--"

    @Property(str, notify=coreStateChanged)
    def coreState(self) -> str:
        return self._info.core_state

    @Property(str, notify=coreVersionChanged)
    def coreVersion(self) -> str:
        return self.controller.manager.backend.version() or "not installed"

    @Property(int, notify=poolChanged)
    def poolTotal(self) -> int:
        return int(self._info.pool_total)

    @Property(int, notify=poolChanged)
    def poolWorking(self) -> int:
        return int(self._info.pool_working)

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        return self._busy

    @Property(bool, notify=stateChanged)
    def testing(self) -> bool:
        return bool(self._info.testing)

    @Property(bool, notify=stateChanged)
    def collecting(self) -> bool:
        return bool(self._info.collecting)

    @Property(str, notify=modeChanged)
    def mode(self) -> str:
        return self._info.mode

    @Property(bool, notify=elevatedChanged)
    def elevated(self) -> bool:
        return bool(self._info.elevated)

    @Property(str, notify=errorChanged)
    def error(self) -> str:
        return self._info.error

    @Property(str, constant=True)
    def selection(self) -> str:
        return str(self.controller.settings.get("servers.selection", "auto"))
