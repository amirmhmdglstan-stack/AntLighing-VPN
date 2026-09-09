"""GitHub-hosted configuration sources.

Public config repositories publish plain ``.txt`` lists of share links.  Two
retrieval paths are used, in order:

1. ``raw.githubusercontent.com`` — fast, unauthenticated, what everyone uses.
2. The GitHub *contents* API with ``Accept: application/vnd.github.raw`` —
   slower and rate limited, but works behind networks that block the raw CDN.

Either path returning a list is fine; the parser ignores anything that is not a
share link, so a README or an HTML error page simply yields zero configs.
"""

from __future__ import annotations

import json
import logging

from .base import ConfigSource, FetchResult, http_get, register

log = logging.getLogger(__name__)

RAW_TEMPLATE = "https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
API_TEMPLATE = "https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={ref}"
API_LIST_TEMPLATE = "https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={ref}"


GITHUB_HOSTS = ("github.com", "raw.githubusercontent.com", "www.github.com")


def parse_repo_spec(url: str) -> tuple[str, str, str, str] | None:
    """Split a GitHub URL into ``(owner, repo, ref, path)``.

    Returns ``None`` for anything that is not recognisably a GitHub repository
    URL, so a mistyped source cannot be silently treated as one.
    """
    if not url:
        return None
    text = url.strip()
    for scheme in ("https://", "http://"):
        if text.lower().startswith(scheme):
            text = text[len(scheme) :]
            break
    host, _, remainder = text.partition("/")
    host = host.strip().lower()
    if host not in GITHUB_HOSTS:
        return None
    text = remainder.strip("/")
    if host == "raw.githubusercontent.com":
        parts = text.split("/", 3)
        if len(parts) < 4:
            return None
        owner, repo, ref, path = parts
        return owner, repo, ref, path
    parts = text.split("/")
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    ref = "main"
    path = ""
    if len(parts) >= 4 and parts[2] in ("blob", "raw", "tree"):
        ref = parts[3]
        path = "/".join(parts[4:])
    elif len(parts) >= 3:
        ref = parts[2]
        path = "/".join(parts[3:])
    return owner, repo, ref, path


@register
class GitHubSource(ConfigSource):
    """Fetches config lists from a GitHub repository.

    ``urls`` may contain repository file URLs.  ``options``:

    ``ref``
        Branch/tag override used when a URL does not specify one.
    ``paths``
        Extra repository-relative paths to fetch in addition to ``urls``.
    """

    kind = "github"

    def _endpoints(self) -> list[tuple[str, str, str, str]]:
        specs: list[tuple[str, str, str, str]] = []
        ref_override = str(self.options.get("ref") or "")
        for url in self.urls:
            parsed = parse_repo_spec(url)
            if parsed is None:
                log.warning("source %s: cannot understand GitHub URL %r", self.id, url)
                continue
            owner, repo, ref, path = parsed
            if ref_override:
                ref = ref_override
            specs.append((owner, repo, ref, path))
        return specs

    def _fetch_one(self, spec: tuple[str, str, str, str], timeout: float) -> list[FetchResult]:
        owner, repo, ref, path = spec
        out: list[FetchResult] = []

        raw_url = RAW_TEMPLATE.format(owner=owner, repo=repo, ref=ref, path=path)
        result = http_get(raw_url, timeout=timeout)
        result.source_id = self.id
        if result.ok and result.text.strip():
            return [result]

        # Fall back to the contents API (also how directory listings are read).
        api_url = API_TEMPLATE.format(owner=owner, repo=repo, ref=ref, path=path)
        listing = http_get(api_url, timeout=timeout)
        listing.source_id = self.id
        if listing.ok and listing.text.strip().startswith("["):
            try:
                entries = json.loads(listing.text)
            except (json.JSONDecodeError, ValueError):
                entries = []
            for entry in entries:
                if not isinstance(entry, dict) or entry.get("type") != "file":
                    continue
                name = str(entry.get("name") or "")
                if not name.endswith((".txt", ".cfg", ".conf", ".list")):
                    continue
                download = str(entry.get("download_url") or "")
                if not download:
                    continue
                sub = http_get(download, timeout=timeout)
                sub.source_id = self.id
                out.append(sub)
            if out:
                return out

        api_result = http_get(
            api_url,
            timeout=timeout,
            headers={"Accept": "application/vnd.github.raw"},
        )
        api_result.source_id = self.id
        if api_result.ok:
            return [api_result]

        # Report the most informative failure.
        if listing.error and result.error:
            result.error = f"{result.error}; api: {listing.error}"
        out.append(result)
        return out

    def fetch(self, timeout: float = 15.0) -> list[FetchResult]:
        results: list[FetchResult] = []
        specs = self._endpoints()
        for spec in specs:
            try:
                results.extend(self._fetch_one(spec, timeout))
            except Exception as exc:  # noqa: BLE001
                log.warning("github source %s failed: %s", self.id, exc)
                results.append(
                    FetchResult(
                        source_id=self.id,
                        url=RAW_TEMPLATE.format(
                            owner=spec[0], repo=spec[1], ref=spec[2], path=spec[3]
                        ),
                        ok=False,
                        error=f"{exc.__class__.__name__}: {str(exc)[:160]}",
                    )
                )
        return results
