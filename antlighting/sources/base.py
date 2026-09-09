"""Configuration sources.

A source knows how to turn *somewhere on the internet* (or on disk) into raw
text that :mod:`antlighting.configs.parser` can scan for share links.  Sources
never parse, validate or store anything themselves, which keeps adding a new one
to a single small class.

All network access here uses the standard library only, so the packaged
executable has no extra native dependencies.
"""

from __future__ import annotations

import abc
import gzip
import io
import logging
import socket
import ssl
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 AntLighting/1.0"
)
MAX_BYTES = 8 * 1024 * 1024  # 8 MiB per document is far more than any sane list
DEFAULT_TIMEOUT = 15.0


@dataclass(slots=True)
class FetchResult:
    """Raw text retrieved from one source endpoint."""

    source_id: str
    url: str
    ok: bool = False
    text: str = ""
    error: str = ""
    duration: float = 0.0
    bytes_read: int = 0
    http_status: int = 0


def _ssl_context(verify: bool = True) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def http_get(
    url: str,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = MAX_BYTES,
    headers: dict[str, str] | None = None,
    verify: bool = True,
) -> FetchResult:
    """GET a URL with a hard size cap and a bounded read.

    Never raises: network problems become a failed :class:`FetchResult`.
    """
    started = time.monotonic()
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            **(headers or {}),
        },
    )
    result = FetchResult(source_id="", url=url)
    try:
        with urllib.request.urlopen(
            request, timeout=timeout, context=_ssl_context(verify)
        ) as response:
            result.http_status = getattr(response, "status", 200)
            if result.http_status >= 400:
                result.error = f"HTTP {result.http_status}"
                result.duration = time.monotonic() - started
                return result
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raw = raw[:max_bytes]
                log.debug("truncated oversized response from %s", url)
            encoding = (response.headers.get("Content-Encoding") or "").lower()
            if encoding == "gzip":
                raw = gzip.decompress(raw)
            elif encoding == "deflate":
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
            charset = response.headers.get_content_charset() or "utf-8"
            result.text = raw.decode(charset, errors="replace")
            result.bytes_read = len(raw)
            result.ok = True
    except urllib.error.HTTPError as exc:
        result.error = f"HTTP {exc.code}"
        result.http_status = exc.code
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, socket.timeout):
            result.error = "timeout"
        else:
            result.error = str(reason)[:200] or exc.__class__.__name__
    except (ssl.SSLError, TimeoutError) as exc:
        result.error = f"tls/timeout: {str(exc)[:160]}"
    except (OSError, ValueError, zlib.error, EOFError) as exc:
        result.error = f"{exc.__class__.__name__}: {str(exc)[:160]}"
    result.duration = time.monotonic() - started
    return result


def http_get_bytes(
    url: str,
    timeout: float = 60.0,
    max_bytes: int = 200 * 1024 * 1024,
    headers: dict[str, str] | None = None,
) -> tuple[bool, bytes, str]:
    """GET a URL and return ``(ok, raw_bytes, error)``.

    Used for binary payloads (the core archive), where the text decoding in
    :func:`http_get` would corrupt the data.
    """
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(
            request, timeout=timeout, context=_ssl_context(True)
        ) as response:
            status = getattr(response, "status", 200)
            if status >= 400:
                return False, b"", f"HTTP {status}"
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                return False, b"", "response too large"
            return True, data, ""
    except urllib.error.HTTPError as exc:
        return False, b"", f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return False, b"", str(exc.reason)[:200] or exc.__class__.__name__
    except (ssl.SSLError, TimeoutError) as exc:
        return False, b"", f"tls/timeout: {str(exc)[:160]}"
    except (OSError, ValueError) as exc:
        return False, b"", f"{exc.__class__.__name__}: {str(exc)[:160]}"


class ConfigSource(abc.ABC):
    """Base class for configuration sources."""

    kind: str = "base"

    def __init__(
        self,
        source_id: str,
        label: str = "",
        enabled: bool = True,
        urls: list[str] | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        self.id = source_id
        self.label = label or source_id
        self.enabled = enabled
        self.urls: list[str] = list(urls or [])
        self.options: dict[str, Any] = dict(options or {})

    @property
    def url(self) -> str:
        return self.urls[0] if self.urls else ""

    def describe(self) -> dict[str, Any]:
        """Serialise back to a spec that :func:`create_source` accepts."""
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "enabled": self.enabled,
            "urls": list(self.urls),
            "options": dict(self.options),
        }

    @abc.abstractmethod
    def fetch(self, timeout: float = DEFAULT_TIMEOUT) -> list[FetchResult]:
        """Return one :class:`FetchResult` per endpoint, never raising."""


# --------------------------------------------------------------------- registry
_REGISTRY: dict[str, type[ConfigSource]] = {}


def register(cls: type[ConfigSource]) -> type[ConfigSource]:
    _REGISTRY[cls.kind] = cls
    return cls


def source_kinds() -> list[str]:
    return sorted(_REGISTRY)


def create_source(spec: dict[str, Any]) -> ConfigSource | None:
    """Build a source from a plain dict (settings file / API payload)."""
    kind = spec.get("kind")
    cls = _REGISTRY.get(kind or "")
    if cls is None:
        log.warning("unknown configuration source kind %r", kind)
        return None
    return cls(
        source_id=spec.get("id") or f"{kind}-{len(_REGISTRY)}",
        label=spec.get("label", ""),
        enabled=bool(spec.get("enabled", True)),
        urls=list(spec.get("urls") or []),
        options=dict(spec.get("options") or {}),
    )


def fetch_all(
    sources: Iterable[ConfigSource],
    timeout: float = DEFAULT_TIMEOUT,
    on_progress: Callable[[str, str], None] | None = None,
) -> list[FetchResult]:
    """Fetch every enabled source, isolating failures to their own source."""
    results: list[FetchResult] = []
    for source in sources:
        if not source.enabled:
            continue
        try:
            batch = source.fetch(timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - one bad source must not stop the rest
            log.warning("source %s raised: %s", source.id, exc)
            batch = [
                FetchResult(
                    source_id=source.id,
                    url=source.url,
                    ok=False,
                    error=f"{exc.__class__.__name__}: {str(exc)[:160]}",
                )
            ]
        for item in batch:
            item.source_id = source.id
        if on_progress:
            ok = sum(1 for item in batch if item.ok)
            on_progress(source.label, f"{ok}/{len(batch)} endpoints")
        results.extend(batch)
    return results
