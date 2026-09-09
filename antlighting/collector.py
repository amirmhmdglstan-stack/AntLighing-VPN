"""The configuration collection pipeline.

::

    SOURCE → FETCH → EXTRACT → PARSE → NORMALISE → DEDUPLICATE → VALIDATE → STORE

Every stage tolerates garbage: a dead channel, an HTML error page or a malformed
share link costs that one item, never the run.  Nothing here executes downloaded
content — share links are data.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .configs.models import ServerConfig
from .configs.parser import parse_text
from .sources.base import ConfigSource, FetchResult, fetch_all
from .storage.db import ConfigStore

log = logging.getLogger(__name__)


@dataclass(slots=True)
class CollectReport:
    """What happened during one collection run."""

    started: float = field(default_factory=time.time)
    finished: float = 0.0
    sources_total: int = 0
    sources_ok: int = 0
    sources_failed: list[str] = field(default_factory=list)
    endpoints: int = 0
    endpoints_ok: int = 0
    bytes_read: int = 0
    links_seen: int = 0
    parsed: int = 0
    rejected: int = 0
    duplicates: int = 0
    new_configs: int = 0
    unusable: int = 0
    duration: float = 0.0
    cancelled: bool = False
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sources_total": self.sources_total,
            "sources_ok": self.sources_ok,
            "sources_failed": list(self.sources_failed),
            "endpoints": self.endpoints,
            "endpoints_ok": self.endpoints_ok,
            "bytes_read": self.bytes_read,
            "links_seen": self.links_seen,
            "parsed": self.parsed,
            "rejected": self.rejected,
            "duplicates": self.duplicates,
            "new_configs": self.new_configs,
            "unusable": self.unusable,
            "duration": round(self.duration, 2),
            "cancelled": self.cancelled,
            "errors": list(self.errors[:8]),
        }

    @property
    def summary(self) -> str:
        if self.cancelled:
            return "Collection cancelled"
        if self.endpoints and not self.endpoints_ok:
            return "No sources could be reached"
        return (
            f"{self.new_configs} new · {self.parsed} parsed · "
            f"{self.duplicates} duplicates · {self.rejected} invalid"
        )


class ConfigCollector:
    """Fetches, parses and stores configurations from every enabled source."""

    def __init__(
        self,
        store: ConfigStore,
        sources: Iterable[ConfigSource] | None = None,
        *,
        fetch_timeout: float = 15.0,
    ) -> None:
        self.store = store
        self._sources: list[ConfigSource] = list(sources or [])
        self.fetch_timeout = fetch_timeout
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self.last_report: CollectReport | None = None

    # ------------------------------------------------------------------ sources
    @property
    def sources(self) -> list[ConfigSource]:
        return list(self._sources)

    def set_sources(self, sources: Iterable[ConfigSource]) -> None:
        with self._lock:
            self._sources = list(sources)

    def cancel(self) -> None:
        self._cancel.set()

    def reset(self) -> None:
        self._cancel.clear()

    # ------------------------------------------------------------------- ingest
    def ingest_text(self, text: str, source_id: str = "manual", trusted: bool = False) -> int:
        """Parse and store a blob of text (used by "import from clipboard")."""
        parsed = parse_text(text, source_id=source_id)
        for config in parsed.configs:
            if not config.usable:
                continue
        added = self.store.upsert_many(parsed.configs)
        if trusted:
            for config in parsed.configs:
                self.store.mark_trusted(config.identity)
        log.info(
            "ingested %d configs from %s (%d rejected)",
            len(parsed.configs),
            source_id,
            len(parsed.rejected),
        )
        return added

    # ------------------------------------------------------------------ pipeline
    def collect(
        self,
        *,
        on_progress: Callable[[str, str], None] | None = None,
        max_items: int | None = None,
    ) -> CollectReport:
        """Run the full pipeline once.  Never raises."""
        report = CollectReport()
        self._cancel.clear()
        with self._lock:
            sources = list(self._sources)
        report.sources_total = len(sources)

        try:
            results: list[FetchResult] = fetch_all(
                sources, timeout=self.fetch_timeout, on_progress=on_progress
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("collection failed")
            report.errors.append(f"{exc.__class__.__name__}: {str(exc)[:160]}")
            report.finished = time.time()
            report.duration = report.finished - report.started
            self.last_report = report
            return report

        ok_source_ids: set[str] = set()
        for item in results:
            report.endpoints += 1
            report.bytes_read += item.bytes_read
            if item.ok:
                report.endpoints_ok += 1
                ok_source_ids.add(item.source_id)
            else:
                report.errors.append(f"{item.source_id}: {item.error}")
                if item.error:
                    log.info("source %s failed: %s", item.source_id, item.error)

        for source in sources:
            if source.id in ok_source_ids:
                report.sources_ok += 1
            else:
                report.sources_failed.append(source.label)
            self.store.record_source_result(
                source.id,
                sum(1 for r in results if r.source_id == source.id and r.ok),
                error="" if source.id in ok_source_ids else "no endpoint succeeded",
            )

        if self._cancel.is_set():
            report.cancelled = True

        # --- extract / parse / normalise / dedupe / validate ------------------
        seen: set[str] = set()
        fresh: list[ServerConfig] = []
        for item in results:
            if not item.ok or not item.text:
                continue
            if self._cancel.is_set():
                report.cancelled = True
                break
            parsed = parse_text(item.text, source_id=item.source_id)
            report.links_seen += len(parsed.configs) + len(parsed.rejected) + parsed.duplicates
            report.parsed += len(parsed.configs)
            report.rejected += len(parsed.rejected)
            for config in parsed.configs:
                if config.identity in seen:
                    report.duplicates += 1
                    continue
                seen.add(config.identity)
                fresh.append(config)

        report.unusable = sum(1 for c in fresh if not c.usable)

        # --- store ------------------------------------------------------------
        if fresh:
            try:
                report.new_configs = self.store.upsert_many(fresh)
            except Exception as exc:  # noqa: BLE001
                log.exception("could not store configs")
                report.errors.append(f"store: {exc.__class__.__name__}: {str(exc)[:140]}")
            try:
                self.store.prune()
            except Exception:  # noqa: BLE001
                log.debug("prune failed", exc_info=True)

        report.finished = time.time()
        report.duration = report.finished - report.started
        self.last_report = report
        log.info(
            "collection: %d endpoints (%d ok), %d parsed, %d new, %d duplicates, %.1fs",
            report.endpoints,
            report.endpoints_ok,
            report.parsed,
            report.new_configs,
            report.duplicates,
            report.duration,
        )
        return report


class RefreshScheduler:
    """Periodic, low-impact refresh.

    Deliberately not aggressive: the default interval is hours, and a run is
    skipped entirely when one is already in flight.
    """

    def __init__(
        self,
        collector: ConfigCollector,
        *,
        interval_seconds: float = 3 * 3600,
        on_done: Callable[[CollectReport], None] | None = None,
    ) -> None:
        self.collector = collector
        self.interval_seconds = max(300.0, float(interval_seconds))
        self.on_done = on_done
        self._timer: threading.Timer | None = None
        self._busy = threading.Event()
        self._stopped = threading.Event()
        self._lock = threading.RLock()

    def start(self) -> None:
        self._stopped.clear()
        self._schedule()

    def stop(self) -> None:
        self._stopped.set()
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        self.collector.cancel()

    def set_interval(self, seconds: float) -> None:
        self.interval_seconds = max(300.0, float(seconds))
        if not self._stopped.is_set():
            self._schedule()

    def _schedule(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            if self._stopped.is_set():
                return
            self._timer = threading.Timer(self.interval_seconds, self._tick)
            self._timer.daemon = True
            self._timer.name = "ant-refresh"
            self._timer.start()

    def _tick(self) -> None:
        if self._stopped.is_set():
            return
        if not self._busy.is_set():
            self._busy.set()
            try:
                report = self.collector.collect()
                if self.on_done is not None:
                    self.on_done(report)
            except Exception:  # noqa: BLE001
                log.exception("scheduled refresh failed")
            finally:
                self._busy.clear()
        self._schedule()

    def trigger_now(self) -> None:
        """Run one refresh immediately on a worker thread."""
        if self._busy.is_set():
            return

        def run() -> None:
            self._busy.set()
            try:
                report = self.collector.collect()
                if self.on_done is not None:
                    self.on_done(report)
            except Exception:  # noqa: BLE001
                log.exception("manual refresh failed")
            finally:
                self._busy.clear()

        thread = threading.Thread(target=run, name="ant-refresh-now", daemon=True)
        thread.start()
