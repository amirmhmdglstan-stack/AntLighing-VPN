"""Share-link parsing.

AntLighting understands the public *share-link* URI formats that the V2Ray /
Xray ecosystem uses, plus a tolerant text extractor that finds them inside HTML
pages, base64 blobs and YAML/JSON profiles.

Nothing here ever evaluates downloaded content: share links are treated strictly
as data, and anything that cannot be parsed is discarded with a reason rather
than raising.

Format references (public, de-facto standards):

* ``vless://uuid@host:port?params#remark``
* ``vmess://base64({json})``
* ``trojan://password@host:port?params#remark``
* ``ss://base64(method:password)@host:port#remark`` (SIP002)
* ``hysteria2://password@host:port?params#remark``
* ``wireguard://privatekey@host:port?params#remark``
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
from typing import Iterable
from urllib.parse import parse_qsl, unquote, urlparse

from .models import SCHEME_INFO, ServerConfig
from .naming import normalise_remark

log = logging.getLogger(__name__)

# Matches a share link inside arbitrary text.  Deliberately generous on the
# allowed character set; callers get a candidate that is then validated.
_LINK_RE = re.compile(
    r"(?P<scheme>vless|vmess|trojan|ss|ssr|hysteria2|hy2|tuic|wireguard)"
    r"://[^\s\"'<>\\)\]}]+",
    re.IGNORECASE,
)

MAX_SCAN_BYTES = 40 * 1024 * 1024  # refuse to scan absurd payloads


class ParseResult:
    """Outcome of parsing a chunk of text."""

    __slots__ = ("configs", "rejected", "duplicates")

    def __init__(self) -> None:
        self.configs: list[ServerConfig] = []
        self.rejected: list[tuple[str, str]] = []  # (snippet, reason)
        self.duplicates: int = 0

    def __len__(self) -> int:
        return len(self.configs)


def _b64_decode_lenient(data: str) -> bytes | None:
    """Decode base64 tolerating missing padding and URL-safe alphabet."""
    cleaned = data.strip().replace("\n", "").replace("\r", "")
    if not cleaned:
        return None
    cleaned = cleaned.replace("-", "+").replace("_", "/")
    cleaned = re.sub(r"[^A-Za-z0-9+/=]", "", cleaned)
    padding = (-len(cleaned)) % 4
    try:
        return base64.b64decode(cleaned + "=" * padding, validate=False)
    except (binascii.Error, ValueError):
        return None


#: ``port`` is ``None`` when the link omits it and ``-1`` when it is present but
#: not a usable port number; callers reject the latter.
def _split_hostport(netloc: str) -> tuple[str, int | None]:
    """Split ``host:port`` from a netloc, honouring userinfo and bracketed IPv6."""
    netloc = (netloc or "").strip()
    # ``urlparse`` keeps the credentials in ``netloc``; they are not part of the
    # host and must be removed before looking for the port separator.
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]
    if not netloc:
        return "", None
    if netloc.startswith("["):
        end = netloc.find("]")
        if end != -1:
            host = netloc[1:end]
            rest = netloc[end + 1 :]
            if not rest:
                return host, None
            if rest.startswith(":"):
                port = _to_port(rest[1:])
                return host, port if port is not None else -1
            return host, -1
    if netloc.count(":") > 1:  # bare IPv6 without brackets
        return netloc, None
    if ":" in netloc:
        host, _, port_s = netloc.partition(":")
        port = _to_port(port_s)
        return host, port if port is not None else -1
    return netloc, None


def _apply_port(cfg: ServerConfig, port: int | None, default: int) -> None:
    """Set the port, keeping the ``-1`` invalid marker for parse_link to reject."""
    cfg.port = default if port is None else port


def _to_port(value: str) -> int | None:
    value = (value or "").strip()
    if not value.isdigit():
        return None
    port = int(value)
    return port if 0 < port < 65536 else None


def _query_dict(query: str) -> dict[str, str]:
    """Parse a query string, lower-casing keys (public links are inconsistent)."""
    out: dict[str, str] = {}
    for key, value in parse_qsl(query, keep_blank_values=True):
        out[key.strip().lower()] = value
    return out


def _clean_remark(fragment: str, limit: int = 90) -> str:
    remark = unquote(fragment).strip() if fragment else ""
    remark = re.sub(r"\s+", " ", remark)
    return remark[:limit]


def _parse_vless(url, remark: str) -> ServerConfig:
    credential = unquote(url.username or "")
    host, port = _split_hostport(url.netloc)
    q = _query_dict(url.query)
    params = dict(q)
    # Xray wants these under their own names.
    if "headertype" in params:
        params.setdefault("headerType", params.pop("headertype"))
    security = params.get("security", "none")
    net = params.get("type") or params.get("net") or "tcp"
    cfg = ServerConfig(
        scheme="vless",
        address=host,
        port=443,
        identity="",
        remark=remark,
        raw=url.geturl(),
        net=net,
        security=security,
        credential=credential,
        params=params,
    )
    _apply_port(cfg, port, 443)
    return cfg


def _parse_vmess(url, remark: str) -> ServerConfig | None:
    payload = url.netloc + (":" + url.query if url.query else "")
    # Some publishers append ?params or #remark after the base64 blob.
    decoded = _b64_decode_lenient(payload.split("#", 1)[0])
    if decoded is None:
        return None
    text = decoded.decode("utf-8", errors="replace").strip()
    # Newer variant: base64 of a vless:// link rather than JSON.
    if text.startswith("vless://") or text.startswith("trojan://"):
        return parse_link(text)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    host = str(data.get("add") or data.get("host_addr") or data.get("server") or "")
    port = _to_port(str(data.get("port", "")))
    credential = str(data.get("id") or data.get("uuid") or "")
    remark = remark or str(data.get("ps") or data.get("name") or "")
    q: dict[str, str] = {}
    for key in (
        "host",
        "path",
        "sni",
        "alpn",
        "fp",
        "pbk",
        "sid",
        "spx",
        "serviceName",
        "mode",
        "flow",
        "scy",
        "v",
        "aid",
        "extra",
    ):
        if data.get(key) not in (None, ""):
            q[str(key).lower()] = str(data[key])
    # Non-standard keys emitted by some panels.
    if data.get("insecure") in ("1", "true", True):
        q["allowinsecure"] = "1"
    if data.get("skip-cert-verify") in ("1", "true", True):
        q["allowinsecure"] = "1"

    net = data.get("net") or "tcp"
    header = data.get("type") or ""
    if header:
        q["headerType"] = str(header)
    return ServerConfig(
        scheme="vmess",
        address=host,
        port=port or 80,
        identity="",
        remark=remark,
        raw=url.geturl(),
        net=str(net),
        security=data.get("tls") or "none",
        credential=credential,
        params=q,
    )


def _parse_trojan(url, remark: str) -> ServerConfig:
    credential = unquote(url.password or url.username or "")
    host, port = _split_hostport(url.netloc)
    q = _query_dict(url.query)
    cfg = ServerConfig(
        scheme="trojan",
        address=host,
        port=443,
        identity="",
        remark=remark,
        raw=url.geturl(),
        net=q.get("type") or q.get("net") or "tcp",
        security=q.get("security") or "tls",
        credential=credential,
        params=q,
    )
    _apply_port(cfg, port, 443)
    return cfg


_SS_METHODS = (
    "aes-128-gcm",
    "aes-192-gcm",
    "aes-256-gcm",
    "chacha20-ietf-poly1305",
    "chacha20-poly1305",
    "xchacha20-poly1305",
    "aes-128-cfb",
    "aes-192-cfb",
    "aes-256-cfb",
    "aes-256-ctr",
    "chacha20-ietf",
    "2022-blake3-aes-128-gcm",
    "2022-blake3-aes-256-gcm",
    "2022-blake3-chacha20-poly1305",
    "none",
    "plain",
)


def _parse_ss(url, remark: str) -> ServerConfig | None:
    q = _query_dict(url.query)
    host: str
    port: int | None
    method: str
    password: str

    raw_body = url.netloc
    decoded = _b64_decode_lenient(raw_body.split("@", 1)[0]) if "@" in raw_body else None
    if decoded is not None:
        text = decoded.decode("utf-8", errors="replace")
        if "@" in text and ":" in text:
            # Whole userinfo@host:port was base64'd (legacy SIP002 variant).
            userinfo, _, hostport = text.rpartition("@")
            host, port = _split_hostport(hostport)
            method, _, password = userinfo.partition(":")
        elif ":" in text and "@" not in text:
            # base64(method:password)@host:port — the common form.
            method, _, password = text.partition(":")
            host, port = _split_hostport(raw_body.rsplit("@", 1)[1])
        else:
            return None
    else:
        # Un-encoded: ss://method:password@host:port
        userinfo, _, hostport = url.netloc.rpartition("@")
        if not userinfo:
            return None
        method, _, password = userinfo.partition(":")
        host, port = _split_hostport(hostport)

    method = method.strip().lower()
    if method not in _SS_METHODS:
        cfg = ServerConfig(
            scheme="ss",
            address=host,
            port=443,
            identity="",
            remark=remark,
            raw=url.geturl(),
            credential=password,
            params={"method": method, **q},
        )
        cfg.core_supported = False
        cfg.unusable_reason = f"unsupported Shadowsocks cipher '{method or 'unknown'}'"
        _apply_port(cfg, port, 443)
        return cfg

    params = {"method": method, **q}
    cfg = ServerConfig(
        scheme="ss",
        address=host,
        port=443,
        identity="",
        remark=remark,
        raw=url.geturl(),
        net="tcp",
        security="none",
        credential=password,
        params=params,
    )
    _apply_port(cfg, port, 443)
    return cfg


def _parse_hysteria2(url, remark: str) -> ServerConfig:
    credential = unquote(url.password or url.username or "")
    host, port = _split_hostport(url.netloc)
    q = _query_dict(url.query)
    cfg = ServerConfig(
        scheme="hysteria2",
        address=host,
        port=443,
        identity="",
        remark=remark,
        raw=url.geturl(),
        net="hysteria2",
        security="tls",
        credential=credential,
        params=q,
    )
    _apply_port(cfg, port, 443)
    return cfg


def _parse_wireguard(url, remark: str) -> ServerConfig:
    private_key = unquote(url.password or url.username or "")
    host, port = _split_hostport(url.netloc)
    q = _query_dict(url.query)
    cfg = ServerConfig(
        scheme="wireguard",
        address=host,
        port=51820,
        identity="",
        remark=remark,
        raw=url.geturl(),
        net="wireguard",
        security="none",
        credential=private_key,
        params=q,
    )
    _apply_port(cfg, port, 51820)
    return cfg


_SCHEME_PARSERS = {
    "vless": _parse_vless,
    "trojan": _parse_trojan,
    "hysteria2": _parse_hysteria2,
    "hy2": _parse_hysteria2,
    "wireguard": _parse_wireguard,
}


def parse_link(link: str) -> ServerConfig | None:
    """Parse one share link.  Returns ``None`` when it cannot be understood."""
    if not link:
        return None
    link = link.strip().strip(".,;:)]}'\"<>")
    if "://" not in link:
        return None
    scheme, _, _ = link.partition("://")
    scheme = scheme.strip().lower()
    if scheme not in SCHEME_INFO:
        return None

    try:
        url = urlparse(link)
    except ValueError:
        return None

    remark = _clean_remark(url.fragment)
    try:
        if scheme == "vmess":
            cfg = _parse_vmess(url, remark)
        elif scheme == "ss":
            cfg = _parse_ss(url, remark)
        elif scheme == "ssr":
            cfg = ServerConfig(
                scheme="ssr",
                address="",
                port=0,
                identity="",
                remark=remark,
                raw=link,
            )
            cfg.core_supported = False
            cfg.unusable_reason = "ShadowsocksR is not supported by Xray-core"
            return cfg
        elif scheme == "tuic":
            cfg = ServerConfig(
                scheme="tuic",
                address="",
                port=0,
                identity="",
                remark=remark,
                raw=link,
            )
            cfg.core_supported = False
            cfg.unusable_reason = "TUIC is not supported by Xray-core"
            return cfg
        else:
            cfg = _SCHEME_PARSERS[scheme](url, remark)
    except Exception:  # noqa: BLE001 - a malformed link must never abort a scan
        log.debug("failed to parse share link", exc_info=True)
        return None

    if cfg is None:
        return None
    cfg.raw = link
    if not cfg.address:
        return None
    if cfg.port == -1:  # explicit but unparseable port
        return None
    if not cfg.port:
        return None
    if not cfg.credential and cfg.scheme not in ("ss",):
        return None

    name, country, country_code, provider = normalise_remark(cfg.remark, cfg.address)
    cfg.name = name
    cfg.country = country
    cfg.country_code = country_code
    cfg.provider = provider
    cfg.set_identity()
    return cfg


def _looks_like_link(text: str) -> bool:
    return "://" in text and text.split("://", 1)[0].lower() in SCHEME_INFO


def extract_links(text: str, max_items: int = 100_000) -> list[str]:
    """Pull every plausible share link out of a blob of text.

    Handles three shapes found in the wild:
      1. plain text / HTML with links inline,
      2. a base64 blob that decodes to a list of links,
      3. quoted strings inside YAML/JSON profiles.
    """
    if not text:
        return []
    if len(text) > MAX_SCAN_BYTES:
        text = text[:MAX_SCAN_BYTES]

    found: list[str] = [m.group(0) for m in _LINK_RE.finditer(text)]
    if found:
        return found[:max_items]

    # Whole payload may be base64.
    decoded = _b64_decode_lenient(text.strip())
    if decoded:
        try:
            inner = decoded.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            inner = ""
        if inner:
            links = [m.group(0) for m in _LINK_RE.finditer(inner)]
            if links:
                return links[:max_items]
    return []


def parse_text(text: str, source_id: str = "") -> ParseResult:
    """Parse every share link found in *text*, de-duplicating within the blob."""
    result = ParseResult()
    if not text:
        return result
    seen: set[str] = set()
    for link in extract_links(text):
        cfg = parse_link(link)
        if cfg is None:
            result.rejected.append((link[:80], "malformed share link"))
            continue
        cfg.source_id = source_id
        if cfg.identity in seen:
            result.duplicates += 1
            continue
        seen.add(cfg.identity)
        result.configs.append(cfg)
    return result


def parse_all(texts: Iterable[tuple[str, str]]) -> ParseResult:
    """Parse several ``(text, source_id)`` blobs into one de-duplicated result."""
    merged = ParseResult()
    seen: set[str] = set()
    for text, source_id in texts:
        sub = parse_text(text, source_id)
        merged.rejected.extend(sub.rejected)
        merged.duplicates += sub.duplicates
        for cfg in sub.configs:
            if cfg.identity in seen:
                merged.duplicates += 1
                continue
            seen.add(cfg.identity)
            merged.configs.append(cfg)
    return merged
