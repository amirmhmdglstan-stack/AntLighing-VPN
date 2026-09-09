"""Application settings.

A single JSON document with typed accessors and safe defaults.  Unknown keys are
preserved so a newer version's settings are not destroyed by an older one.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from typing import Any

from ..sources import default_source_specs

log = logging.getLogger(__name__)

SETTINGS_FILE = "settings.json"


def settings_dir() -> str:
    """Per-user data directory (``%APPDATA%/AntLighting`` on Windows)."""
    override = os.environ.get("ANTLIGHTING_DATA_DIR")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "AntLighting")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "antlighting")


def default_settings() -> dict[str, Any]:
    return {
        "version": 1,
        "ui": {
            "theme": "system",  # system | dark | light
            "animations": True,
            "start_minimized": False,
            "close_to_tray": True,
        },
        "connection": {
            # ``auto`` uses the full-device TUN when elevation is available and
            # falls back to the system proxy otherwise.
            "mode": "auto",  # auto | tun | proxy
            "socks_port": 20808,
            "http_port": 20809,
            "dns_servers": [
                "https://1.1.1.1/dns-query",
                "https://8.8.8.8/dns-query",
                "localhost",
            ],
            "bypass_lan": True,
            "block_ads": True,
            "ipv6": False,
            "fragment": True,
            "mux_enabled": False,
            "mux_concurrency": 8,
            "auto_reconnect": True,
            "set_system_dns": True,
        },
        "core": {
            "backend": "xray",
            "binary_path": "",
            "log_level": "warning",
            "auto_download": True,
            "pinned_version": "",
            "max_restarts": 3,
        },
        "servers": {
            "selection": "auto",  # auto | <identity>
            "last_identity": "",
            "test_timeout": 8.0,
            "test_concurrency": 16,
            "slow_threshold_ms": 900.0,
            "preferred_protocols": ["vless", "trojan", "vmess", "ss", "hysteria2"],
            "auto_test_on_startup": True,
            "max_pool_size": 4000,
        },
        "sources": {
            "specs": default_source_specs(),
            "refresh_interval_minutes": 180,
            "auto_refresh": True,
            "fetch_timeout": 15.0,
        },
        "privacy": {
            "telemetry": False,  # AntLighting has no telemetry at all
            "log_raw_configs": False,
        },
    }


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Settings:
    """JSON-backed settings with defaults and atomic writes."""

    def __init__(self, path: str | None = None, data: dict[str, Any] | None = None) -> None:
        self.path = path or os.path.join(settings_dir(), SETTINGS_FILE)
        self._lock = threading.RLock()
        self._data: dict[str, Any] = default_settings()
        if data:
            self._data = _deep_merge(self._data, data)
        elif os.path.isfile(self.path):
            self.load()

    # --------------------------------------------------------------------- I/O
    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("could not read settings (%s); using defaults", exc)
            return
        if not isinstance(loaded, dict):
            log.warning("settings file is not an object; using defaults")
            return
        with self._lock:
            self._data = _deep_merge(default_settings(), loaded)

    def save(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with self._lock:
            payload = json.dumps(self._data, indent=2, ensure_ascii=False)
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            dir=directory,
            prefix=".settings-",
            suffix=".json",
            delete=False,
            encoding="utf-8",
        )
        try:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            os.replace(handle.name, self.path)
        except OSError as exc:
            log.error("could not write settings: %s", exc)
            try:
                os.unlink(handle.name)
            except OSError:
                pass

    # ----------------------------------------------------------------- accessors
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any, *, save: bool = True) -> None:
        parts = dotted.split(".")
        with self._lock:
            node = self._data
            for part in parts[:-1]:
                child = node.get(part)
                if not isinstance(child, dict):
                    child = {}
                    node[part] = child
                node = child
            node[parts[-1]] = value
        if save:
            self.save()

    def reset(self) -> None:
        with self._lock:
            self._data = default_settings()
        self.save()

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._data))

    # ------------------------------------------------------------ typed helpers
    @property
    def sources(self) -> list[dict[str, Any]]:
        specs = self.get("sources.specs")
        return list(specs) if isinstance(specs, list) else default_source_specs()

    def set_sources(self, specs: list[dict[str, Any]]) -> None:
        self.set("sources.specs", specs)
