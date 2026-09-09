"""Public Telegram channel source.

Telegram renders the last ~20 messages of a public channel as HTML at
``https://t.me/s/<channel>``; older messages are reached with ``?before=<id>``.
No API key, login or third-party library is needed, which is exactly what a
desktop client should use for *public* channels.

Telegram is frequently rate limited or blocked.  Every failure here is
non-fatal: the source reports an error and the collector carries on with the
other sources.
"""

from __future__ import annotations

import html
import logging
import re
import time

from .base import ConfigSource, FetchResult, http_get, register

log = logging.getLogger(__name__)

PREVIEW_TEMPLATE = "https://t.me/s/{channel}"
MESSAGE_ID_RE = re.compile(r'data-post="[^"]*/(\d+)"')
DEFAULT_PAGES = 3
DEFAULT_MIN_LINKS = 1
REQUEST_SPACING = 0.8  # seconds between page requests, be polite


def extract_channel(value: str) -> str:
    """Normalise ``@channel``, ``t.me/channel`` or a full URL to a bare name."""
    text = (value or "").strip()
    if not text:
        return ""
    text = text.lstrip("@")
    for prefix in (
        "https://t.me/s/",
        "http://t.me/s/",
        "https://t.me/",
        "http://t.me/",
        "t.me/s/",
        "t.me/",
    ):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :]
            break
    text = text.split("/", 1)[0]
    text = text.split("?", 1)[0]
    return text.strip()


def _message_ids(page: str) -> list[int]:
    ids = {int(m) for m in MESSAGE_ID_RE.findall(page)}
    return sorted(ids)


@register
class TelegramSource(ConfigSource):
    """Scrapes public Telegram channel web previews.

    ``urls`` holds channel handles (``@irconfig``, ``https://t.me/irconfig``…).
    ``options``:

    ``pages``
        How many pages of history to walk per channel (default 3).
    ``min_links``
        Stop paging once this many links were found (default 1).
    """

    kind = "telegram"

    @property
    def channels(self) -> list[str]:
        return [c for c in (extract_channel(u) for u in self.urls) if c]

    def _fetch_channel(self, channel: str, timeout: float) -> FetchResult:
        pages = max(1, int(self.options.get("pages", DEFAULT_PAGES)))
        min_links = max(0, int(self.options.get("min_links", DEFAULT_MIN_LINKS)))

        collected: list[str] = []
        error = ""
        status = 0
        started = time.monotonic()
        before: int | None = None
        total_bytes = 0

        for page_index in range(pages):
            url = PREVIEW_TEMPLATE.format(channel=channel)
            if before is not None:
                url = f"{url}?before={before}"
            result = http_get(url, timeout=timeout)
            status = result.http_status or status
            total_bytes += result.bytes_read
            if not result.ok:
                error = result.error or f"HTTP {result.http_status}"
                if page_index == 0:
                    break
                # Partial success is still a success.
                break

            collected.append(result.text)
            found = sum(text.count("://") for text in collected)
            if found >= min_links and page_index >= 1:
                break

            ids = _message_ids(result.text)
            if not ids:
                break
            next_before = min(ids) - 1
            if next_before <= 0 or next_before == before:
                break
            before = next_before
            if page_index + 1 < pages:
                time.sleep(REQUEST_SPACING)

        outcome = FetchResult(
            source_id=self.id,
            url=PREVIEW_TEMPLATE.format(channel=channel),
            ok=bool(collected),
            text="\n".join(collected),
            error="" if collected else (error or "no messages"),
            duration=time.monotonic() - started,
            bytes_read=total_bytes,
            http_status=status,
        )
        if outcome.ok:
            # Drop the HTML tags; the parser only needs the text content.
            outcome.text = _strip_html(outcome.text)
        return outcome

    def fetch(self, timeout: float = 15.0) -> list[FetchResult]:
        results: list[FetchResult] = []
        for channel in self.channels:
            try:
                results.append(self._fetch_channel(channel, timeout))
            except Exception as exc:  # noqa: BLE001
                log.info("telegram channel %s unavailable: %s", channel, exc)
                results.append(
                    FetchResult(
                        source_id=self.id,
                        url=PREVIEW_TEMPLATE.format(channel=channel),
                        ok=False,
                        error=f"{exc.__class__.__name__}: {str(exc)[:160]}",
                    )
                )
        return results


_TAG_RE = re.compile(r"<script.*?</script>|<style.*?</style>", re.DOTALL | re.IGNORECASE)
_ANY_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(page: str) -> str:
    """Reduce the preview HTML to text, decoding entities.

    Share links appear inside ``href``/``data-`` attributes as well as in text
    nodes, so attributes must survive the strip rather than vanish with the tag.
    """
    page = _TAG_RE.sub(" ", page)
    page = re.sub(r'<[^>]*href="([^"]+)"[^>]*>', r" \1 ", page, flags=re.IGNORECASE)
    page = _ANY_TAG_RE.sub(" ", page)
    return html.unescape(page)
