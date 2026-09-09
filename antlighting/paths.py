"""Filesystem locations used by AntLighting."""

from __future__ import annotations

import os
import sys

from .storage.settings import settings_dir


def data_dir() -> str:
    return settings_dir()


def cache_dir() -> str:
    override = os.environ.get("ANTLIGHTING_CACHE_DIR")
    if override:
        path = os.path.abspath(os.path.expanduser(override))
    else:
        base = data_dir()
        path = os.path.join(base, "cache")
    os.makedirs(path, exist_ok=True)
    return path


def core_dir() -> str:
    """Where a downloaded core binary lives.

    In a packaged build the core ships inside the bundle, so this is only used
    for a user-installed or auto-downloaded core.
    """
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen and os.path.isfile(os.path.join(str(frozen), "xray.exe")):
        return str(frozen)
    path = os.path.join(data_dir(), "core")
    os.makedirs(path, exist_ok=True)
    return path


def work_dir() -> str:
    path = os.path.join(data_dir(), "run")
    os.makedirs(path, exist_ok=True)
    return path


def database_path() -> str:
    return os.path.join(data_dir(), "configs.db")


def log_path() -> str:
    return os.path.join(data_dir(), "antlighting.log")


def ensure_dirs() -> dict[str, str]:
    dirs = {
        "data": data_dir(),
        "cache": cache_dir(),
        "core": core_dir(),
        "work": work_dir(),
    }
    for path in dirs.values():
        os.makedirs(path, exist_ok=True)
    return dirs
