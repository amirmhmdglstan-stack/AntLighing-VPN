"""Full-device VPN (TUN) control on Windows.

How AntLighting establishes a real system-wide tunnel:

1. Xray-core is started with a ``tun`` inbound.  On Windows this creates a
   Wintun adapter (``wintun.dll`` ships inside the official Xray release).
2. The adapter is given the address ``198.18.0.1/30`` and DNS ``198.18.0.2``.
   That range is RFC 2544 benchmark space, deliberately *not* RFC 1918, so the
   "bypass private LAN" routing rule cannot send DNS back out through the
   ``direct`` outbound and loop.
3. Routes are installed.  Two strategies are available:

   ``xray`` (default)
       Xray installs the routes itself via ``autoSystemRoutingTable`` and pins
       its own outbounds to the physical NIC with ``autoOutboundsInterface``,
       which is the officially supported way to avoid a routing loop.
   ``managed``
       AntLighting installs **split default routes** (``0.0.0.0/1`` and
       ``128.0.0.0/1``) plus an explicit ``/32`` direct route to the proxy
       server.  Split defaults are chosen over ``0.0.0.0/0`` on purpose: if the
       tunnel dies, the machine's original default route still carries traffic,
       so a failure degrades to "no VPN" instead of "no internet".

TUN mode requires administrator rights.  Every change is recorded and undone on
disconnect, and :meth:`TunnelController.recover_stale` cleans up after a crash.

**Verification note:** the command *selection* logic is unit tested on Linux via
an injected command runner.  Actually executing these commands requires a real
Windows machine and is not exercised by this repository's automated tests.
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

log = logging.getLogger(__name__)

ADAPTER_NAME = "AntLighting"
ADAPTER_DESCRIPTION = "AntLighting VPN"
TUN_GATEWAY = "198.18.0.1/30"
TUN_DNS = "198.18.0.2"
TUN_MTU = 1500
SNAPSHOT_FILE = "tunnel.json"

SPLIT_DEFAULTS = (("0.0.0.0", "128.0.0.0"), ("128.0.0.0", "128.0.0.0"))


@dataclass(slots=True)
class CommandResult:
    argv: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass(slots=True)
class TunnelState:
    """Everything needed to undo a tunnel."""

    active: bool = False
    strategy: str = "xray"
    adapter: str = ADAPTER_NAME
    gateway: str = TUN_GATEWAY
    dns: str = TUN_DNS
    uplink_ip: str = ""
    physical_gateway: str = ""
    routes_added: list[list[str]] = field(default_factory=list)
    dns_changed: bool = False
    previous_dns: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "strategy": self.strategy,
            "adapter": self.adapter,
            "gateway": self.gateway,
            "dns": self.dns,
            "uplink_ip": self.uplink_ip,
            "physical_gateway": self.physical_gateway,
            "routes_added": [list(r) for r in self.routes_added],
            "dns_changed": self.dns_changed,
            "previous_dns": self.previous_dns,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TunnelState:
        return cls(
            active=bool(data.get("active")),
            strategy=str(data.get("strategy", "xray")),
            adapter=str(data.get("adapter", ADAPTER_NAME)),
            gateway=str(data.get("gateway", TUN_GATEWAY)),
            dns=str(data.get("dns", TUN_DNS)),
            uplink_ip=str(data.get("uplink_ip", "")),
            physical_gateway=str(data.get("physical_gateway", "")),
            routes_added=[list(r) for r in (data.get("routes_added") or [])],
            dns_changed=bool(data.get("dns_changed")),
            previous_dns=str(data.get("previous_dns", "")),
            error=str(data.get("error", "")),
        )


def _is_windows() -> bool:
    """Single platform check, so tests can simulate Windows safely."""
    return os.name == "nt"


CommandRunner = Callable[[Sequence[str], float], CommandResult]


def real_runner(argv: Sequence[str], timeout: float = 20.0) -> CommandResult:
    """Execute a command, hiding any console window.  Never raises."""
    kwargs: dict[str, Any] = {"capture_output": True, "timeout": timeout, "text": True}
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore[attr-defined]
        startupinfo.wShowWindow = 0
        kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        completed = subprocess.run(list(argv), **kwargs)
        return CommandResult(
            argv=list(argv),
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
    except FileNotFoundError:
        return CommandResult(list(argv), 127, "", f"command not found: {argv[0]}")
    except subprocess.TimeoutExpired:
        return CommandResult(list(argv), 124, "", "timeout")
    except OSError as exc:
        return CommandResult(list(argv), 1, "", f"{exc.__class__.__name__}: {exc}")


def powershell(script: str) -> list[str]:
    return [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]


class TunnelController:
    """Brings the system-wide tunnel up and, more importantly, back down."""

    def __init__(
        self,
        snapshot_path: str,
        *,
        runner: CommandRunner = real_runner,
        adapter: str = ADAPTER_NAME,
        gateway: str = TUN_GATEWAY,
        dns: str = TUN_DNS,
        mtu: int = TUN_MTU,
    ) -> None:
        self.snapshot_path = snapshot_path
        self.runner = runner
        self.adapter = adapter
        self.gateway = gateway
        self.dns = dns
        self.mtu = mtu
        self._state = TunnelState(adapter=adapter, gateway=gateway, dns=dns)
        self.commands: list[CommandResult] = []

    # ---------------------------------------------------------------- capability
    def supported(self) -> tuple[bool, str]:
        """Report whether full-device TUN mode can be used here."""
        if not _is_windows():
            return False, f"TUN mode is Windows-only (running on {sys.platform})"
        from .elevation import is_admin

        if not is_admin():
            return False, "TUN mode needs administrator rights"
        return True, ""

    def wintun_available(self, core_dirs: Sequence[str]) -> bool:
        for directory in core_dirs:
            if os.path.isfile(os.path.join(directory, "wintun.dll")):
                return True
        return shutil.which("wintun.dll") is not None

    # ---------------------------------------------------------------------- up
    def bring_up(
        self,
        *,
        strategy: str = "xray",
        uplink_host: str = "",
        wait_seconds: float = 8.0,
    ) -> TunnelState:
        """Apply routes/DNS for an already-running TUN adapter."""
        state = TunnelState(
            active=False, strategy=strategy, adapter=self.adapter,
            gateway=self.gateway, dns=self.dns,
        )
        if not _is_windows():
            state.error = f"TUN mode is not available on {sys.platform}"
            self._state = state
            return state

        uplink_ip = _resolve(uplink_host) if uplink_host else ""
        state.uplink_ip = uplink_ip
        state.physical_gateway = self._physical_gateway()

        interface_index = self._wait_for_adapter(wait_seconds)
        if interface_index is None:
            state.error = f"the '{self.adapter}' adapter never appeared"
            self._state = state
            self._save(state)
            return state

        # 1. Never let the proxy's own uplink go through the tunnel.
        if uplink_ip:
            self._add_route(state, uplink_ip, "255.255.255.255", state.physical_gateway,
                            interface_index=None, metric=1)

        if strategy == "managed":
            for destination, mask in SPLIT_DEFAULTS:
                self._add_route(state, destination, mask, _network_of(self.gateway),
                                interface_index=interface_index, metric=6)

        # 2. Point DNS at the tunnel so DNS queries are protected too.
        previous_dns = self._current_dns(self.adapter)
        if previous_dns != self.dns:
            result = self.runner(
                [
                    "netsh", "interface", "ip", "set", "dns",
                    f"name={self.adapter}", "source=static", f"address={self.dns}",
                    "register=none", "validate=no",
                ]
            )
            self.commands.append(result)
            if result.ok:
                state.dns_changed = True
                state.previous_dns = previous_dns

        state.active = True
        self._state = state
        self._save(state)
        log.info(
            "tunnel up: strategy=%s adapter=%s index=%s uplink=%s",
            strategy, self.adapter, interface_index, uplink_ip or "-",
        )
        return state

    # -------------------------------------------------------------------- down
    def tear_down(self) -> TunnelState:
        """Remove everything :meth:`bring_up` did."""
        state = self._state
        if not _is_windows():
            self._state = TunnelState()
            self._clear_snapshot()
            return self._state

        if state.dns_changed and state.previous_dns:
            self.runner(
                [
                    "netsh", "interface", "ip", "set", "dns",
                    f"name={state.adapter}", "source=static",
                    f"address={state.previous_dns}", "validate=no",
                ]
            )
        for route in reversed(state.routes_added):
            self.runner(["route", "delete", *route])

        self._state = TunnelState()
        self._clear_snapshot()
        log.info("tunnel torn down")
        return self._state

    def recover_stale(self) -> bool:
        """Clean up after a session that died without tearing the tunnel down."""
        import json

        try:
            with open(self.snapshot_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError, ValueError):
            return False
        state = TunnelState.from_dict(data)
        if not state.active:
            self._clear_snapshot()
            return False
        log.warning("recovering a stale tunnel left by a previous session")
        self._state = state
        self.tear_down()
        return True

    def status(self) -> TunnelState:
        return self._state

    # ---------------------------------------------------------------- internals
    def _add_route(
        self,
        state: TunnelState,
        destination: str,
        mask: str,
        via: str,
        *,
        interface_index: int | None,
        metric: int,
    ) -> bool:
        argv = ["route", "add", destination, "mask", mask]
        if via:
            argv.append(via)
        argv += ["metric", str(metric)]
        if interface_index is not None:
            argv += ["if", str(interface_index)]
        result = self.runner(argv)
        self.commands.append(result)
        if result.ok:
            # The mirror-image argv needed to remove the route again.
            delete = ["route", "delete", destination, "mask", mask]
            if via:
                delete.append(via)
            if interface_index is not None:
                delete += ["if", str(interface_index)]
            state.routes_added.append([str(part) for part in delete[2:]])
            return True
        log.warning("route add failed (%s): %s", " ".join(argv), result.stderr.strip()[:160])
        return False

    def _wait_for_adapter(self, wait_seconds: float) -> int | None:
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            index = self._adapter_index()
            if index is not None:
                return index
            time.sleep(0.4)
        return None

    def _adapter_index(self) -> int | None:
        script = (
            f"$a = Get-NetAdapter | Where-Object {{ $_.InterfaceDescription -like "
            f"'{ADAPTER_DESCRIPTION}*' -or $_.Name -like '{self.adapter}*' }} | "
            "Select-Object -First 1; if ($a) {{ $a.ifIndex }}"
        )
        result = self.runner(powershell(script), timeout=25.0)
        self.commands.append(result)
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.isdigit():
                return int(line)
        return None

    def _physical_gateway(self) -> str:
        """The gateway of the interface that currently carries the default route."""
        script = (
            "(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -AddressFamily IPv4 | "
            "Sort-Object RouteMetric | Select-Object -First 1).NextHop"
        )
        result = self.runner(powershell(script), timeout=25.0)
        self.commands.append(result)
        for line in result.stdout.splitlines():
            candidate = line.strip()
            if _is_ipv4(candidate):
                return candidate
        return ""

    def _current_dns(self, adapter: str) -> str:
        script = (
            f"(Get-DnsClientServerAddress -InterfaceAlias '{adapter}' "
            "-AddressFamily IPv4 -ErrorAction SilentlyContinue).ServerAddresses -join ','"
        )
        result = self.runner(powershell(script), timeout=25.0)
        self.commands.append(result)
        return result.stdout.strip()

    def _save(self, state: TunnelState) -> None:
        import json

        try:
            directory = os.path.dirname(os.path.abspath(self.snapshot_path))
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(self.snapshot_path, "w", encoding="utf-8") as handle:
                json.dump(state.to_dict(), handle, indent=2)
        except OSError as exc:
            log.warning("could not save the tunnel snapshot: %s", exc)

    def _clear_snapshot(self) -> None:
        try:
            os.unlink(self.snapshot_path)
        except OSError:
            pass


def _resolve(host: str) -> str:
    if not host:
        return ""
    if _is_ipv4(host):
        return host
    try:
        return socket.gethostbyname(host)
    except (socket.gaierror, OSError) as exc:
        log.debug("could not resolve %s: %s", host, exc)
        return ""


def _is_ipv4(value: str) -> bool:
    parts = value.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def _network_of(cidr: str) -> str:
    """``198.18.0.1/30`` -> ``198.18.0.1`` (the tunnel-side gateway address)."""
    return cidr.split("/", 1)[0]
