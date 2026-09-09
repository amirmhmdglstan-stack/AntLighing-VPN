"""Server testing: reachability, latency, cancellation and ranking input."""

from __future__ import annotations

import abc
import logging
import socket
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from ..configs.models import ServerConfig
from ..configs.ranking import rank_servers, select_best
from ..core.base import CoreBackend
from ..core.config_builder import CoreOptions, LocalEndpoint, TunOptions, build_config
from ..net.socks import SocksError, http_get_via_socks

log = logging.getLogger(__name__)

# Small, content-free endpoints used to measure a real proxied round trip.
PROBE_TARGETS = (
    "http://cp.cloudflare.com/generate_204",
    "http://www.gstatic.com/generate_204",
    "http://connectivitycheck.gstatic.com/generate_204",
)
EXPECTED_STATUSES = (200, 204)


@dataclass(slots=True)
class TestResult:
    identity: str
    status: str  # working | slow | timeout | failed | invalid
    latency_ms: float | None = None
    error: str = ""
    method: str = ""
    duration: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status in ("working", "slow")


class Probe(abc.ABC):
    """Strategy for deciding whether a server actually works."""

    name = "probe"

    @abc.abstractmethod
    def test(self, config: ServerConfig, timeout: float) -> TestResult:
        ...


class TcpProbe(Probe):
    """Cheap TCP handshake test.

    Only proves the endpoint answers TCP; it cannot see through a broken TLS or
    Reality handshake.  Used as a fast pre-filter before the real proxy test.
    """

    name = "tcp"

    def test(self, config: ServerConfig, timeout: float) -> TestResult:
        started = time.monotonic()
        try:
            with socket.create_connection((config.address, config.port), timeout=timeout):
                latency = (time.monotonic() - started) * 1000.0
            return TestResult(
                identity=config.identity,
                status="working",
                latency_ms=round(latency, 1),
                method=self.name,
                duration=time.monotonic() - started,
            )
        except socket.timeout:
            return TestResult(
                identity=config.identity,
                status="timeout",
                error="tcp handshake timed out",
                method=self.name,
                duration=time.monotonic() - started,
            )
        except OSError as exc:
            return TestResult(
                identity=config.identity,
                status="failed",
                error=f"{exc.__class__.__name__}: {str(exc)[:120]}",
                method=self.name,
                duration=time.monotonic() - started,
            )


class ProxyProbe(Probe):
    """Real test: start the core, push an HTTP request through the tunnel.

    ``core_factory`` is injected so the whole path is testable without a real
    ``xray`` binary.
    """

    name = "proxy"

    def __init__(
        self,
        core_factory: Callable[[int], CoreBackend],
        *,
        options: CoreOptions | None = None,
        targets: Sequence[str] = PROBE_TARGETS,
        slow_threshold_ms: float = 900.0,
        port_allocator: Callable[[], int] | None = None,
    ) -> None:
        self.core_factory = core_factory
        self.options = options or CoreOptions(log_level="error", use_geo_data=False)
        self.targets = tuple(targets) or PROBE_TARGETS
        self.slow_threshold_ms = slow_threshold_ms
        self._port_allocator = port_allocator or _default_port_allocator()

    def test(self, config: ServerConfig, timeout: float) -> TestResult:
        if not config.usable:
            return TestResult(
                identity=config.identity,
                status="invalid",
                error=config.unusable_reason or "unsupported configuration",
                method=self.name,
            )
        port = self._port_allocator()
        started = time.monotonic()
        core = None
        try:
            core = self.core_factory(port)
            endpoint = LocalEndpoint(socks_port=port, http_port=0, sniffing=False)
            xray_config = build_config(
                config, endpoint=endpoint, options=self.options, tun=TunOptions(enabled=False)
            )
            status = core.start(xray_config, timeout=max(6.0, timeout))
            if status.state != "running":
                return TestResult(
                    identity=config.identity,
                    status="failed",
                    error=status.error or "core did not start",
                    method=self.name,
                    duration=time.monotonic() - started,
                )

            last_error = "no probe target succeeded"
            for target in self.targets:
                request_started = time.monotonic()
                try:
                    code, _ = http_get_via_socks("127.0.0.1", port, target, timeout=timeout)
                except SocksError as exc:
                    last_error = str(exc)[:160]
                    continue
                except (socket.timeout, TimeoutError):
                    last_error = "probe timed out"
                    continue
                except OSError as exc:
                    last_error = f"{exc.__class__.__name__}: {str(exc)[:120]}"
                    continue

                if code in EXPECTED_STATUSES:
                    latency = (time.monotonic() - request_started) * 1000.0
                    return TestResult(
                        identity=config.identity,
                        status="working" if latency <= self.slow_threshold_ms else "slow",
                        latency_ms=round(latency, 1),
                        method=self.name,
                        duration=time.monotonic() - started,
                    )
                last_error = f"unexpected HTTP {code}"
            return TestResult(
                identity=config.identity,
                status="failed",
                error=last_error,
                method=self.name,
                duration=time.monotonic() - started,
            )
        except Exception as exc:  # noqa: BLE001
            return TestResult(
                identity=config.identity,
                status="failed",
                error=f"{exc.__class__.__name__}: {str(exc)[:160]}",
                method=self.name,
                duration=time.monotonic() - started,
            )
        finally:
            if core is not None:
                try:
                    core.stop(timeout=3.0)
                except Exception:  # noqa: BLE001
                    log.debug("probe core failed to stop cleanly", exc_info=True)


class ServerTester:
    """Runs a probe across many servers with bounded concurrency.

    Guarantees:
      * one dead server cannot stall the run (hard per-server timeout),
      * ``cancel()`` stops scheduling new work promptly,
      * results are written back to the store as they arrive.
    """

    def __init__(
        self,
        probe: Probe,
        *,
        concurrency: int = 16,
        timeout: float = 8.0,
        store: Any = None,
        slow_threshold_ms: float = 900.0,
    ) -> None:
        self.probe = probe
        self.concurrency = max(1, int(concurrency))
        self.timeout = float(timeout)
        self.store = store
        self.slow_threshold_ms = slow_threshold_ms
        self._cancel = threading.Event()
        self._lock = threading.RLock()
        self._running = False
        self._completed = 0
        self._total = 0
        self._working = 0

    # ------------------------------------------------------------------ control
    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def reset(self) -> None:
        self._cancel.clear()

    def progress(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self._running,
                "completed": self._completed,
                "total": self._total,
                "working": self._working,
                "cancelled": self._cancel.is_set(),
            }

    # ------------------------------------------------------------------ running
    def test_one(self, config: ServerConfig) -> TestResult:
        if self._cancel.is_set():
            return TestResult(
                identity=config.identity, status="failed", error="cancelled", method="cancelled"
            )
        if not config.usable:
            return TestResult(
                identity=config.identity,
                status="invalid",
                error=config.unusable_reason or "unsupported",
                method=self.probe.name,
            )
        # A worker-side watchdog: a probe that ignores its own timeout still
        # cannot block a worker slot forever.
        result = _run_with_watchdog(self.probe.test, config, self.timeout * 1.75, self.timeout)
        return result

    def test_many(
        self,
        configs: Iterable[ServerConfig],
        *,
        on_progress: Callable[[TestResult, int, int], None] | None = None,
        limit: int | None = None,
    ) -> list[TestResult]:
        items = list(configs)
        if limit:
            items = items[:limit]
        self._cancel.clear()
        results: list[TestResult] = []
        with self._lock:
            self._running = True
            self._total = len(items)
            self._completed = 0
            self._working = 0
        try:
            if not items:
                return results
            with ThreadPoolExecutor(
                max_workers=self.concurrency, thread_name_prefix="ant-test"
            ) as pool:
                futures: list[Future[TestResult]] = [
                    pool.submit(self.test_one, config) for config in items
                ]
                for future in futures:
                    if self._cancel.is_set():
                        future.cancel()
                    try:
                        result = future.result(timeout=self.timeout * 3 + 30)
                    except TimeoutError:
                        result = TestResult(
                            identity="", status="timeout", error="test harness timeout"
                        )
                    except Exception as exc:  # noqa: BLE001
                        result = TestResult(
                            identity="",
                            status="failed",
                            error=f"{exc.__class__.__name__}: {str(exc)[:140]}",
                        )
                    results.append(result)
                    with self._lock:
                        self._completed += 1
                        if result.status in ("working", "slow"):
                            self._working += 1
                        done, total = self._completed, self._total
                    if result.identity and self.store is not None:
                        try:
                            self.store.record_result(
                                result.identity,
                                result.status,
                                latency_ms=result.latency_ms,
                                error=result.error[:200],
                            )
                        except Exception:  # noqa: BLE001
                            log.exception("could not store test result")
                    if on_progress is not None:
                        try:
                            on_progress(result, done, total)
                        except Exception:  # noqa: BLE001
                            log.debug("progress callback failed", exc_info=True)
            return results
        finally:
            with self._lock:
                self._running = False

    # ------------------------------------------------------------------ ranking
    def rank(
        self,
        configs: Iterable[ServerConfig],
        *,
        now: float | None = None,
        preferred_protocols: Sequence[str] | None = None,
    ) -> list[Any]:
        metrics = self._metrics_lookup()
        kwargs: dict[str, Any] = {
            "now": now if now is not None else time.time(),
            "test_timeout": self.timeout,
            "slow_threshold_ms": self.slow_threshold_ms,
        }
        if preferred_protocols is not None:
            kwargs["preferred_protocols"] = preferred_protocols
        return rank_servers(configs, metrics, **kwargs)

    def select_best(
        self,
        configs: Iterable[ServerConfig],
        *,
        skip: Iterable[str] = (),
        preferred_protocols: Sequence[str] | None = None,
    ) -> Any:
        metrics = self._metrics_lookup()
        kwargs: dict[str, Any] = {
            "now": time.time(),
            "test_timeout": self.timeout,
            "slow_threshold_ms": self.slow_threshold_ms,
        }
        if preferred_protocols is not None:
            kwargs["preferred_protocols"] = preferred_protocols
        return select_best(configs, metrics, skip_identities=skip, **kwargs)

    def _metrics_lookup(self) -> Callable[[str], dict[str, Any]]:
        store = self.store
        if store is None:
            return lambda _identity: {}
        return lambda identity: store.get_metrics(identity) or {}


def _run_with_watchdog(
    func: Callable[[ServerConfig, float], TestResult],
    config: ServerConfig,
    hard_timeout: float,
    soft_timeout: float,
) -> TestResult:
    """Run *func* with a hard ceiling so a wedged probe cannot block a worker."""
    holder: dict[str, TestResult] = {}

    def runner() -> None:
        try:
            holder["result"] = func(config, soft_timeout)
        except Exception as exc:  # noqa: BLE001
            holder["result"] = TestResult(
                identity=config.identity,
                status="failed",
                error=f"{exc.__class__.__name__}: {str(exc)[:140]}",
                method="probe",
            )

    thread = threading.Thread(target=runner, name="ant-probe", daemon=True)
    thread.start()
    thread.join(hard_timeout)
    if thread.is_alive():
        return TestResult(
            identity=config.identity,
            status="timeout",
            error=f"probe exceeded {hard_timeout:.0f}s",
            method="probe",
        )
    return holder.get("result") or TestResult(
        identity=config.identity, status="failed", error="probe returned nothing"
    )


_PORT_LOCK = threading.Lock()
_port_state = {"next": 30000}


def _default_port_allocator() -> Callable[[], int]:
    def allocate() -> int:
        with _PORT_LOCK:
            port = _port_state["next"]
            _port_state["next"] = port + 2 if port < 60000 else 30000
            return port

    return allocate


@dataclass(slots=True)
class TestRunSummary:
    """Compact outcome of a test run, safe to show in the UI."""

    __test__ = False  # not a pytest test case, despite the name

    total: int = 0
    working: int = 0
    slow: int = 0
    timeout: int = 0
    failed: int = 0
    invalid: int = 0
    cancelled: bool = False
    duration: float = 0.0
    best: dict[str, Any] | None = None
    results: list[TestResult] = field(default_factory=list)

    @classmethod
    def from_results(
        cls, results: Sequence[TestResult], duration: float = 0.0, cancelled: bool = False
    ) -> TestRunSummary:
        summary = cls(total=len(results), duration=duration, cancelled=cancelled, results=list(results))
        for item in results:
            if item.status == "working":
                summary.working += 1
            elif item.status == "slow":
                summary.slow += 1
            elif item.status == "timeout":
                summary.timeout += 1
            elif item.status == "invalid":
                summary.invalid += 1
            else:
                summary.failed += 1
        return summary
