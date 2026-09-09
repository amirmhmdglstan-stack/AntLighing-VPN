"""Networking helpers: elevation, system proxy and the TUN tunnel."""

from .elevation import describe_privileges, is_admin, relaunch_elevated
from .socks import SocksError, connect, http_get_via_socks
from .system_proxy import SystemProxyController
from .tunnel import TunnelController, TunnelState

__all__ = [
    "SocksError",
    "SystemProxyController",
    "TunnelController",
    "TunnelState",
    "connect",
    "describe_privileges",
    "http_get_via_socks",
    "is_admin",
    "relaunch_elevated",
]
