"""Normalised data model for a single proxy server configuration.

A :class:`ServerConfig` is the single internal representation used by every other
layer of AntLighting (storage, testing, ranking, core configuration).  It is
deliberately *core agnostic*: the Xray-specific JSON is generated from it by
:mod:`antlighting.core.config_builder`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

# Schemes AntLighting can parse.  ``core_supported`` marks the ones the bundled
# Xray-core build can actually dial; the rest are parsed (so they can be shown,
# counted and explained to the user) but are never selected for a connection.
SCHEME_INFO: dict[str, dict[str, Any]] = {
    "vless": {"core_supported": True, "label": "VLESS"},
    "vmess": {"core_supported": True, "label": "VMess"},
    "trojan": {"core_supported": True, "label": "Trojan"},
    "ss": {"core_supported": True, "label": "Shadowsocks"},
    "hysteria2": {"core_supported": True, "label": "Hysteria2"},
    "wireguard": {"core_supported": True, "label": "WireGuard"},
    "ssr": {"core_supported": False, "label": "ShadowsocksR"},
    "tuic": {"core_supported": False, "label": "TUIC"},
    "hy2": {"core_supported": True, "label": "Hysteria2"},
}

# Transport names as they appear in share links -> transport names Xray expects.
# Xray renamed ``tcp`` to ``raw`` in newer releases but still accepts both, and
# public configs are emitted with either spelling, so normalise to Xray's
# current vocabulary while remembering what the source used.
TRANSPORT_ALIASES: dict[str, str] = {
    "tcp": "raw",
    "raw": "raw",
    "kcp": "kcp",
    "mkcp": "kcp",
    "ws": "ws",
    "websocket": "ws",
    "http": "http",
    "h2": "http",
    "grpc": "grpc",
    "gun": "grpc",
    "multi": "grpc",
    "xhttp": "xhttp",
    "splithttp": "splithttp",
    "httpupgrade": "httpupgrade",
    "quic": "quic",
    "meek": "meek",
    "domainsocket": "domainsocket",
}

SECURITY_ALIASES: dict[str, str] = {
    "": "none",
    "none": "none",
    "tls": "tls",
    "reality": "reality",
    "xtls": "xtls",
    "external": "external",
}

# Query parameters that are part of a server's *identity*: two share links that
# differ in any of these dial something different.  Everything else (remarks,
# telegram channel tags, cosmetic flags) is ignored when de-duplicating.
IDENTITY_PARAMS = frozenset(
    {
        "security",
        "type",
        "net",
        "sni",
        "host",
        "path",
        "pbk",
        "sid",
        "spx",
        "flow",
        "fp",
        "alpn",
        "headerType",
        "serviceName",
        "mode",
        "encryption",
        "packetEncoding",
        "seed",
        "extra",
        "publicKey",
        "shortId",
    }
)


def _norm_transport(value: str | None) -> str:
    key = (value or "tcp").strip().lower()
    return TRANSPORT_ALIASES.get(key, key)


def _norm_security(value: str | None) -> str:
    key = (value or "none").strip().lower()
    return SECURITY_ALIASES.get(key, key)


@dataclass(slots=True)
class ServerConfig:
    """One parsed proxy server.

    Attributes
    ----------
    scheme:
        ``vless`` / ``vmess`` / ``trojan`` / ``ss`` / ``hysteria2`` / ``wireguard``.
    address, port:
        The remote endpoint.
    identity:
        Stable hex digest used as the primary key and for de-duplication.
    params:
        Normalised (lower-cased key) transport/security parameters.
    raw:
        The original share link.  Never logged.
    """

    scheme: str
    address: str
    port: int
    identity: str
    name: str = ""
    remark: str = ""
    raw: str = ""
    country: str | None = None
    country_code: str | None = None
    provider: str | None = None
    net: str = "tcp"
    security: str = "none"
    # Protocol secret (uuid for vless/vmess, password for trojan/ss).
    credential: str = ""
    params: dict[str, str] = field(default_factory=dict)
    source_id: str = ""
    core_supported: bool = True
    # Set when the share link was structurally parseable but not usable.
    unusable_reason: str = ""

    def __post_init__(self) -> None:
        self.net = _norm_transport(self.net)
        self.security = _norm_security(self.security)
        self.params = {str(k).lower(): str(v) for k, v in self.params.items()}
        if not self.core_supported and not self.unusable_reason:
            self.unusable_reason = f"{self.scheme} is not supported by the bundled core"

    # ------------------------------------------------------------------ display
    @property
    def scheme_label(self) -> str:
        return SCHEME_INFO.get(self.scheme, {}).get("label", self.scheme.upper())

    @property
    def usable(self) -> bool:
        return bool(self.core_supported) and not self.unusable_reason

    @property
    def host_label(self) -> str:
        return self.address

    def display_name(self) -> str:
        """A short, friendly name.  Falls back through remark -> scheme+address."""
        if self.name:
            return self.name
        if self.remark:
            return self.remark.strip()
        return f"{self.scheme_label} {self.address}"

    # ------------------------------------------------------------------ identity
    @staticmethod
    def make_identity(
        scheme: str,
        address: str,
        port: int,
        credential: str,
        net: str,
        security: str,
        params: dict[str, str],
    ) -> str:
        """Derive the stable identity hash.

        The remark is deliberately excluded: the same server republished by five
        Telegram channels with five different remarks is still one server.
        """
        selected = {
            k: params.get(k, "")
            for k in sorted(IDENTITY_PARAMS)
            if params.get(k)
        }
        selected["credential"] = credential
        selected["net"] = net
        selected["security"] = security
        payload = json.dumps(
            {"scheme": scheme, "address": address, "port": port, **selected},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def set_identity(self) -> None:
        self.identity = self.make_identity(
            self.scheme,
            self.address,
            self.port,
            self.credential,
            self.net,
            self.security,
            self.params,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "scheme": self.scheme,
            "address": self.address,
            "port": self.port,
            "identity": self.identity,
            "name": self.name,
            "remark": self.remark,
            "raw": self.raw,
            "country": self.country,
            "country_code": self.country_code,
            "provider": self.provider,
            "net": self.net,
            "security": self.security,
            "credential": self.credential,
            "params": dict(self.params),
            "source_id": self.source_id,
            "core_supported": self.core_supported,
            "unusable_reason": self.unusable_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ServerConfig:
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})
