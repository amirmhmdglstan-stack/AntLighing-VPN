"""Privilege handling.

AntLighting runs unelevated by default.  Only the full-device TUN mode needs
administrator rights, and the app asks for them explicitly instead of shipping
an ``app.manifest`` that forces a UAC prompt on every launch.
"""

from __future__ import annotations

import logging
import os
import sys

log = logging.getLogger(__name__)


def is_admin() -> bool:
    """True when the current process has administrator/root rights."""
    if os.name == "nt":
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:  # noqa: BLE001
            return False
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def relaunch_elevated(extra_args: list[str] | None = None) -> bool:
    """Relaunch the application with a UAC prompt.  Returns True on success.

    Only meaningful on Windows.  Never raises — the caller falls back to the
    non-elevated system-proxy mode when this returns False.
    """
    if os.name != "nt":
        log.debug("elevation is a no-op on this platform")
        return False
    try:
        import ctypes

        if getattr(sys, "frozen", False):
            target = sys.executable
            params = " ".join(f'"{a}"' for a in (extra_args or []))
        else:
            target = sys.executable
            script = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
            params = " ".join(
                [f'"{script}"', *(f'"{a}"' for a in (extra_args or []))]
            )
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", target, params, None, 1
        )
        # ShellExecuteW returns > 32 on success.
        return int(result) > 32
    except Exception as exc:  # noqa: BLE001
        log.warning("could not relaunch elevated: %s", exc)
        return False


def describe_privileges() -> dict[str, object]:
    return {
        "platform": sys.platform,
        "elevated": is_admin(),
        "frozen": bool(getattr(sys, "frozen", False)),
    }
