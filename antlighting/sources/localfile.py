"""Plain HTTP(S) and local-file configuration sources."""

from __future__ import annotations

import logging
import os

from .base import MAX_BYTES, ConfigSource, FetchResult, http_get, register

log = logging.getLogger(__name__)


@register
class HTTPSource(ConfigSource):
    """Any URL that serves a list of share links (or a page containing them)."""

    kind = "http"

    def fetch(self, timeout: float = 15.0) -> list[FetchResult]:
        results: list[FetchResult] = []
        for url in self.urls:
            try:
                results.append(http_get(url, timeout=timeout))
            except Exception as exc:  # noqa: BLE001
                results.append(
                    FetchResult(
                        source_id=self.id,
                        url=url,
                        ok=False,
                        error=f"{exc.__class__.__name__}: {str(exc)[:160]}",
                    )
                )
        return results


@register
class LocalFileSource(ConfigSource):
    """Imports configs from files the user chose themselves.

    These are the only *trusted* configs: they came from the user, not from a
    public channel.  That distinction is kept in the database (``trusted``).
    """

    kind = "localfile"

    def fetch(self, timeout: float = 15.0) -> list[FetchResult]:
        results: list[FetchResult] = []
        for path in self.urls:
            result = FetchResult(source_id=self.id, url=path)
            try:
                if not os.path.isfile(path):
                    result.error = "file not found"
                    results.append(result)
                    continue
                size = os.path.getsize(path)
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    result.text = handle.read(MAX_BYTES)
                result.bytes_read = min(size, MAX_BYTES)
                result.ok = True
            except OSError as exc:
                result.error = f"{exc.__class__.__name__}: {str(exc)[:160]}"
            results.append(result)
        return results
