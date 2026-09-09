#!/usr/bin/env python3
"""Validate AntLighting-generated Xray configs against the *real* Xray-core.

The PyPI ``Xray-core`` wheel (github.com/LorenEteval/Xray-core-python) embeds
the genuine Xray-core Go runtime compiled as a Python extension.  Calling
``startFromJSON`` runs the actual configuration loader, so a config that the
extension accepts is a config the core accepts.

``startFromJSON`` blocks while the core runs and calls ``os.Exit(23)`` when the
config is rejected, so each config is validated in a forked child:

* child still alive after the grace period  -> config accepted
* child exited                              -> config rejected

Each child gets its own local listener ports so parallel/back-to-back runs
cannot collide, and a failure is retried once (a port still in TIME_WAIT looks
exactly like a config error).

The parent process never imports the extension, which keeps ``fork`` safe.

Usage:
    python3 tools/validate_configs_against_xray.py <configs.txt> [--limit N]
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from antlighting.configs.parser import parse_text  # noqa: E402
from antlighting.core.config_builder import (  # noqa: E402
    CoreOptions,
    LocalEndpoint,
    build_config,
)

DEFAULT_GRACE = 2.0
_port_counter = itertools.count(21000)


def _next_ports() -> tuple[int, int]:
    base = next(_port_counter)
    if base > 60000:  # wrap around, very long runs
        _port_counter.__init__(21000)  # type: ignore[misc]
        base = next(_port_counter)
    return base, base + 1


def _child(config_json: bytes, log_fd: int) -> None:
    """Runs in the forked child.  Never returns normally."""
    try:
        os.dup2(log_fd, 1)
        os.dup2(log_fd, 2)
        os.close(log_fd)
        import xray  # noqa: PLC0415 - intentionally child-only

        xray.startFromJSON(config_json)
    except BaseException as exc:  # noqa: BLE001
        try:
            os.write(2, f"exception: {exc}\n".encode())
        except OSError:
            pass
    finally:
        os._exit(0)


def validate_one(config_json: bytes, grace: float = DEFAULT_GRACE) -> tuple[bool, str]:
    """Return ``(accepted, stderr_tail)``."""
    log_path = f"/tmp/antlighting-xray-check-{os.getpid()}-{next(_port_counter)}.log"
    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    pid = os.fork()
    if pid == 0:  # pragma: no cover - child
        _child(config_json, log_fd)
        os._exit(0)

    deadline = time.monotonic() + grace
    status: int | None = None
    while time.monotonic() < deadline:
        done, code = os.waitpid(pid, os.WNOHANG)
        if done:
            status = code
            break
        time.sleep(0.02)

    if status is None:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        os.waitpid(pid, 0)
        accepted = True
    else:
        accepted = os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0

    output = ""
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
            output = handle.read()[-800:]
    except OSError:
        pass
    finally:
        try:
            os.unlink(log_path)
        except OSError:
            pass
    return accepted, output


def validate_with_retry(
    server, grace: float = DEFAULT_GRACE, attempts: int = 2, **build_kwargs
) -> tuple[bool, str]:
    """Validate one server config, retrying transient port-bind failures."""
    last = ("", False)
    for _ in range(attempts):
        socks, http = _next_ports()
        endpoint = LocalEndpoint(socks_port=socks, http_port=http)
        payload = json.dumps(build_config(server, endpoint=endpoint, **build_kwargs)).encode()
        accepted, err = validate_one(payload, grace=grace)
        last = (accepted, err)
        if accepted:
            return True, err
        if "address already in use" not in err:
            return False, err
        time.sleep(0.05)
    return last


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("configs")
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--grace", type=float, default=DEFAULT_GRACE)
    parser.add_argument("--show-failures", type=int, default=10)
    parser.add_argument(
        "--schemes",
        default="",
        help="comma separated list of schemes to include (default: all)",
    )
    args = parser.parse_args()

    try:
        import importlib.util

        if importlib.util.find_spec("xray") is None:
            print("SKIP: PyPI 'Xray-core' extension is not installed.")
            return 0
    except Exception as exc:  # noqa: BLE001
        print(f"SKIP: cannot check for xray extension ({exc})")
        return 0

    with open(args.configs, "r", encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    parsed = parse_text(text, source_id="cli")
    wanted = {s.strip() for s in args.schemes.split(",") if s.strip()}
    usable = [
        c
        for c in parsed.configs
        if c.usable and (not wanted or c.scheme in wanted)
    ]
    print(
        f"parsed={len(parsed.configs)} usable={len(usable)} "
        f"rejected={len(parsed.rejected)} duplicates={parsed.duplicates}"
    )

    # The embedded core has no geoip.dat/geosite.dat next to it, so validate the
    # configuration path that does not depend on geo data files.
    options = CoreOptions(use_geo_data=False, fragment=True)
    failures: list[tuple[str, str]] = []
    accepted = 0
    checked = 0
    total = min(args.limit, len(usable))
    for cfg in usable[: args.limit]:
        ok, err = validate_with_retry(cfg, grace=args.grace, options=options)
        checked += 1
        if ok:
            accepted += 1
        else:
            tail = err.strip().splitlines()[-1] if err.strip() else "(no output)"
            failures.append((f"{cfg.scheme}/{cfg.net}/{cfg.security} {cfg.raw[:80]}", tail))
        if checked % 50 == 0:
            print(f"  {checked}/{total} checked, {len(failures)} rejected", flush=True)

    print(f"\naccepted by real Xray-core: {accepted}/{checked}")
    for raw, err in failures[: args.show_failures]:
        print(f"  FAIL {raw}\n       {err}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
