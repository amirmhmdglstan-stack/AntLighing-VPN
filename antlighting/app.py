"""Application bootstrap: wires every component together and starts Qt."""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

log = logging.getLogger(__name__)


def build_controller(settings_dir_override: str | None = None) -> tuple[Any, Any, Any]:
    """Create the controller and its collaborators.

    Kept separate from :func:`run` so the whole application can be assembled
    without a display server — that is how the headless test suite drives it.
    """
    from .collector import ConfigCollector
    from .controller import AppController
    from .core.xray import XrayCore
    from .logging_setup import setup_logging
    from .net.system_proxy import SystemProxyController
    from .net.tunnel import TunnelController
    from .paths import database_path, data_dir, ensure_dirs, work_dir
    from .sources.base import create_source
    from .storage.db import ConfigStore
    from .storage.settings import Settings
    from .testing.tester import ProxyProbe, ServerTester

    if settings_dir_override:
        os.environ["ANTLIGHTING_DATA_DIR"] = settings_dir_override

    dirs = ensure_dirs()
    settings = Settings()
    setup_logging(
        dirs["data"],
        level=str(settings.get("core.log_level", "warning")).upper()
        if settings.get("core.log_level") in ("debug", "info")
        else "INFO",
        to_console=os.environ.get("ANTLIGHTING_CONSOLE_LOG") == "1",
    )
    log.info("starting AntLighting in %s", dirs["data"])

    store = ConfigStore(database_path())

    core = XrayCore(
        binary_path=str(settings.get("core.binary_path") or "") or None,
        search_dirs=[dirs["core"], dirs["data"]],
        work_dir=work_dir(),
    )

    # If no core is present, offer to fetch the official release.
    if core.locate_binary() is None and settings.get("core.auto_download", True):
        _maybe_download_core(core, dirs["core"], settings)

    def core_factory(port: int) -> XrayCore:
        """A throwaway core for a single probe, on its own port."""
        return XrayCore(
            binary_path=core.binary_path,
            search_dirs=core._search_dirs,
            work_dir=work_dir(),
        )

    probe = ProxyProbe(
        core_factory,
        slow_threshold_ms=float(settings.get("servers.slow_threshold_ms", 900.0)),
    )
    tester = ServerTester(
        probe,
        concurrency=int(settings.get("servers.test_concurrency", 16)),
        timeout=float(settings.get("servers.test_timeout", 8.0)),
        slow_threshold_ms=float(settings.get("servers.slow_threshold_ms", 900.0)),
        store=store,
    )

    sources = []
    for spec in settings.sources:
        source = create_source(spec)
        if source is not None:
            sources.append(source)
    collector = ConfigCollector(
        store, sources, fetch_timeout=float(settings.get("sources.fetch_timeout", 15.0))
    )

    system_proxy = SystemProxyController(os.path.join(dirs["data"], "system_proxy.json"))
    tunnel = TunnelController(os.path.join(dirs["data"], "tunnel.json"))

    controller = AppController(
        settings=settings,
        store=store,
        core=core,
        tester=tester,
        collector=collector,
        system_proxy=system_proxy,
        tunnel=tunnel,
    )
    return controller, settings, store


def _maybe_download_core(core: Any, core_dir: str, settings: Any) -> None:
    """Fetch the official Xray-core release when none is installed.

    Best effort: a failed download leaves the app running, and the UI explains
    that the core is missing rather than crashing.
    """
    from .core.downloader import DownloadError, download_core

    log.info("no core binary found; attempting an automatic download")
    try:
        path = download_core(
            core_dir,
            tag=str(settings.get("core.pinned_version") or ""),
            on_progress=lambda message: log.info("core: %s", message),
        )
        core.binary_path = path
        settings.set("core.binary_path", path, save=True)
    except DownloadError as exc:
        log.warning("could not download the core automatically: %s", exc)
    except Exception:  # noqa: BLE001
        log.exception("unexpected error while downloading the core")


def selftest(argv: list[str] | None = None) -> int:
    """Build the app, load the interface, verify it, and exit.

    Used by CI to prove the *packaged* executable really works: it exercises the
    controller wiring, the QML engine and every import PyInstaller had to find.
    Prints a short report and returns 0 on success.
    """
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine

    from . import APP_NAME, __version__
    from .ui.bridge import AppBridge

    argv = list(sys.argv if argv is None else argv)
    QGuiApplication.setApplicationName(APP_NAME)
    app = QGuiApplication(argv)

    controller, _settings, store = build_controller()
    bridge = AppBridge(controller)

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("app", bridge)
    engine.rootContext().setContextProperty("appVersion", __version__)
    qml_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "main.qml")
    engine.load(QUrl.fromLocalFile(qml_path))

    problems: list[str] = []
    rows: list[dict] = []
    if not engine.rootObjects():
        problems.append("main.qml failed to instantiate")
    if not bridge.state:
        problems.append("bridge reported no state")
    try:
        rows = list(bridge.servers.servers)
        if not rows:
            problems.append("server model is empty (expected the Auto row)")
        elif rows[0]["identity"] != "auto":
            problems.append("server model does not start with the Auto row")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"server model raised: {exc}")

    core = controller.manager.backend
    lines = [
        f"AntLighting {__version__} self-test",
        f"  Qt interface : {'OK' if engine.rootObjects() else 'FAILED'}",
        f"  bridge state : {bridge.state}",
        f"  server rows  : {len(rows)}",
        f"  core binary  : {core.locate_binary() or 'not found'}",
        f"  core version : {core.version() or 'n/a'}",
        f"  geo data     : {core.has_geo_data()}",
        f"  wintun       : {core.has_wintun()}",
        f"  servers in db: {store.count()}",
    ] + [f"  PROBLEM: {problem}" for problem in problems]
    report = "\n".join(lines) + ("\nSELFTEST OK" if not problems else "\nSELFTEST FAILED")

    # A windowed (console=False) build has no stdout on Windows, so the report is
    # also written to a file when CI asks for one.
    print(report)
    report_path = os.environ.get("ANTLIGHTING_SELFTEST_REPORT")
    if report_path:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(report_path)) or ".", exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as handle:
                handle.write(report + "\n")
        except OSError as exc:  # noqa: BLE001
            print(f"  could not write report: {exc}")

    controller.shutdown()
    return 1 if problems else 0


def run(argv: list[str] | None = None) -> int:
    """Start the GUI.  Returns the process exit code."""
    from PySide6.QtCore import Qt, QUrl
    from PySide6.QtGui import QGuiApplication, QIcon
    from PySide6.QtQml import QQmlApplicationEngine

    from . import APP_NAME, __version__
    from .ui.bridge import AppBridge

    argv = list(sys.argv if argv is None else argv)
    if "--selftest" in argv:
        return selftest([a for a in argv if a != "--selftest"])
    QGuiApplication.setApplicationName(APP_NAME)
    QGuiApplication.setOrganizationName("AntLighting")
    QGuiApplication.setApplicationVersion(__version__)
    QGuiApplication.setDesktopFileName("io.antlighting.vpn")

    app = QGuiApplication(argv)
    app.setQuitOnLastWindowClosed(True)

    controller, settings, _store = build_controller()
    bridge = AppBridge(controller)

    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "icon.png")
    if os.path.isfile(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("app", bridge)
    engine.rootContext().setContextProperty("appVersion", __version__)

    qml_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "main.qml")
    engine.load(QUrl.fromLocalFile(qml_path))
    if not engine.rootObjects():
        print("AntLighting could not load its interface.", file=sys.stderr)
        return 1

    controller.startup()

    # A first run with an empty pool should collect in the background so the
    # server list is not empty by the time the user opens it.
    if controller.store.count() == 0:
        controller.collect_sources()

    exit_code = app.exec()
    controller.shutdown()
    return exit_code


def main() -> int:
    """Console entry point (``python -m antlighting``)."""
    return run()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
