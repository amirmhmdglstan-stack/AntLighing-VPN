"""Windows system proxy (the no-administration connection mode).

Setting ``HKCU\\...\\Internet Settings\\ProxyEnable`` covers every application
that honours WinINET/WinHTTP — browsers, the Store, most Win32 apps — and needs
no elevation.  It is *not* a full-device tunnel: raw socket applications bypass
it.  AntLighting uses it automatically when TUN mode is unavailable and says so
plainly in the UI.

The previous registry values are captured before changing anything and restored
on disconnect, and again at startup in case a previous session died hard.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)

REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
DEFAULT_BYPASS = "<local>;localhost;127.0.0.1;::1;192.168.*;10.*;172.16.*;172.17.*;172.18.*"

SNAPSHOT_FILE = "system_proxy.json"


@dataclass(slots=True)
class ProxySnapshot:
    """The registry state before AntLighting touched it."""

    enabled: int = 0
    server: str = ""
    override: str = ""
    auto_config_url: str = ""
    present: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "server": self.server,
            "override": self.override,
            "auto_config_url": self.auto_config_url,
            "present": self.present,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProxySnapshot:
        return cls(
            enabled=int(data.get("enabled", 0)),
            server=str(data.get("server", "")),
            override=str(data.get("override", "")),
            auto_config_url=str(data.get("auto_config_url", "")),
            present=bool(data.get("present", False)),
        )


def _is_windows() -> bool:
    """Single platform check, so tests can simulate Windows safely."""
    return os.name == "nt"


def _winreg():
    if not _is_windows():
        return None
    try:
        import winreg  # type: ignore[import-not-found]

        return winreg
    except ImportError:
        return None


def _notify_wininet() -> None:
    """Tell WinINET the settings changed so browsers pick it up immediately."""
    if os.name != "nt":
        return
    try:
        import ctypes

        INTERNET_OPTION_SETTINGS_CHANGED = 39
        INTERNET_OPTION_REFRESH = 37
        wininet = ctypes.windll.wininet
        for option in (INTERNET_OPTION_SETTINGS_CHANGED, INTERNET_OPTION_REFRESH):
            wininet.InternetSetOptionW(0, option, 0, 0)
    except Exception as exc:  # noqa: BLE001
        log.debug("could not notify WinINET: %s", exc)


class SystemProxyController:
    """Applies and reverts the Windows system proxy."""

    def __init__(
        self,
        snapshot_path: str,
        *,
        reg_reader: Callable[[str], dict[str, Any] | None] | None = None,
        reg_writer: Callable[[dict[str, Any]], bool] | None = None,
    ) -> None:
        self.snapshot_path = snapshot_path
        self._reg_reader = reg_reader or _default_reader
        self._reg_writer = reg_writer or _default_writer
        self.applied = False

    # ------------------------------------------------------------------ capture
    def capture(self) -> ProxySnapshot:
        values = self._reg_reader(REG_PATH) or {}
        snapshot = ProxySnapshot(
            enabled=int(values.get("ProxyEnable", 0) or 0),
            server=str(values.get("ProxyServer", "") or ""),
            override=str(values.get("ProxyOverride", "") or ""),
            auto_config_url=str(values.get("AutoConfigURL", "") or ""),
            present=bool(values),
        )
        try:
            directory = os.path.dirname(os.path.abspath(self.snapshot_path))
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(self.snapshot_path, "w", encoding="utf-8") as handle:
                json.dump(snapshot.to_dict(), handle, indent=2)
        except OSError as exc:
            log.warning("could not save the system proxy snapshot: %s", exc)
        return snapshot

    def load_snapshot(self) -> ProxySnapshot | None:
        try:
            with open(self.snapshot_path, "r", encoding="utf-8") as handle:
                return ProxySnapshot.from_dict(json.load(handle))
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def _clear_snapshot(self) -> None:
        try:
            os.unlink(self.snapshot_path)
        except OSError:
            pass

    # -------------------------------------------------------------------- apply
    def apply(self, host: str, port: int, bypass: str = DEFAULT_BYPASS) -> bool:
        """Point the system proxy at the local core listener."""
        if not _is_windows():
            log.info("system proxy mode is Windows-only; nothing to do on %s", sys.platform)
            return False
        if not self.applied:
            self.capture()
        values = {
            "ProxyEnable": 1,
            "ProxyServer": f"{host}:{port}",
            "ProxyOverride": bypass,
            "AutoConfigURL": "",
        }
        ok = self._reg_writer(values)
        _notify_wininet()
        self.applied = ok
        if not ok:
            log.error("could not write the system proxy settings")
        return ok

    def revert(self) -> bool:
        """Restore the user's previous proxy settings."""
        snapshot = self.load_snapshot()
        self.applied = False
        if not _is_windows():
            self._clear_snapshot()
            return True
        values: dict[str, Any] = {
            "ProxyEnable": snapshot.enabled if snapshot else 0,
            "ProxyServer": snapshot.server if snapshot else "",
            "ProxyOverride": snapshot.override if snapshot else "",
        }
        ok = self._reg_writer(values)
        _notify_wininet()
        if ok:
            self._clear_snapshot()
        return ok

    def recover_stale(self) -> bool:
        """Undo anything left behind by a crashed session."""
        if self.load_snapshot() is None:
            return False
        log.warning("found stale system proxy settings; restoring them")
        return self.revert()

    def current(self) -> dict[str, Any]:
        values = self._reg_reader(REG_PATH) or {}
        return {
            "enabled": int(values.get("ProxyEnable", 0) or 0),
            "server": str(values.get("ProxyServer", "") or ""),
            "override": str(values.get("ProxyOverride", "") or ""),
        }


def _default_reader(path: str) -> dict[str, Any] | None:
    winreg = _winreg()
    if winreg is None:
        return None
    try:
        import winreg  # type: ignore[import-not-found]

        out: dict[str, Any] = {}
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            for name in ("ProxyEnable", "ProxyServer", "ProxyOverride", "AutoConfigURL"):
                try:
                    out[name] = winreg.QueryValueEx(key, name)[0]
                except OSError:
                    out[name] = ""
        return out
    except OSError as exc:
        log.debug("could not read %s: %s", path, exc)
        return None


def _default_writer(values: dict[str, Any]) -> bool:
    winreg = _winreg()
    if winreg is None:
        return False
    try:
        import winreg  # type: ignore[import-not-found]

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            for name, value in values.items():
                if value == "" and name != "ProxyServer":
                    try:
                        winreg.DeleteValue(key, name)
                    except OSError:
                        pass
                    continue
                if name == "ProxyEnable":
                    winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(value))
                else:
                    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))
        return True
    except OSError as exc:
        log.error("could not write %s: %s", REG_PATH, exc)
        return False
