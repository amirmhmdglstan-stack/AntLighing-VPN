"""Generate Xray-core JSON from a normalised :class:`ServerConfig`.

This is the single place that knows Xray's configuration vocabulary, so the rest
of AntLighting stays core agnostic.  Field names are taken from the Xray-core
source (``infra/conf/*.go``) rather than from third-party examples.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..configs.models import ServerConfig

# Local listeners AntLighting opens for the operating system to use.
DEFAULT_SOCKS_PORT = 20808
DEFAULT_HTTP_PORT = 20809

# Traffic that must never enter the tunnel, otherwise Windows loses its route to
# the internet (and Xray loops on its own uplink).
DIRECT_DOMAINS = ["geosite:private", "geosite:category-ir"]
DIRECT_IPS = ["geoip:private", "geoip:cn"]

# Explicit private/reserved ranges, used when geoip.dat is unavailable.
PRIVATE_CIDRS = (
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.168.0.0/16",
    "224.0.0.0/4",
    "240.0.0.0/4",
    "255.255.255.255/32",
)

PRIVATE_V4 = (
    "10.",
    "127.",
    "169.254.",
    "172.16.",
    "172.17.",
    "172.18.",
    "172.19.",
    "172.20.",
    "172.21.",
    "172.22.",
    "172.23.",
    "172.24.",
    "172.25.",
    "172.26.",
    "172.27.",
    "172.28.",
    "172.29.",
    "172.30.",
    "172.31.",
    "192.168.",
    "100.64.",
    "0.",
)


@dataclass(slots=True)
class LocalEndpoint:
    """Where the core should expose the tunnel to the local machine."""

    socks_port: int = DEFAULT_SOCKS_PORT
    http_port: int = DEFAULT_HTTP_PORT
    listen: str = "127.0.0.1"
    sniffing: bool = True
    udp: bool = True


@dataclass(slots=True)
class TunOptions:
    """Options for the Xray ``tun`` inbound (full-device VPN mode)."""

    enabled: bool = False
    name: str = "AntLighting"
    desc: str = "AntLighting VPN"
    mtu: int = 1500
    # 198.18.0.0/15 (RFC 2544 benchmarking space) is used for the tunnel
    # endpoints on purpose: it is *not* covered by the "bypass private/LAN"
    # routing rule, so DNS queries addressed to the adapter cannot be sent back
    # out through the ``direct`` outbound and loop.
    gateway: list[str] = field(default_factory=lambda: ["198.18.0.1/30"])
    dns: list[str] = field(default_factory=lambda: ["198.18.0.2"])
    # Let Xray install the system routes itself.  AntLighting can also manage
    # them; see antlighting.net.tunnel.
    auto_system_routing_table: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CoreOptions:
    log_level: str = "warning"
    dns_servers: list[str] = field(
        default_factory=lambda: ["https://1.1.1.1/dns-query", "https://8.8.8.8/dns-query", "localhost"]
    )
    block_ads: bool = True
    bypass_lan: bool = True
    ipv6: bool = False
    mux_enabled: bool = False
    mux_concurrency: int = 8
    # Public proxies frequently need TLS-in-TLS fragmentation; on by default.
    fragment: bool = True
    noisec: list[dict[str, Any]] = field(default_factory=list)
    # ``geosite:*`` / ``geoip:*`` rules need geoip.dat + geosite.dat beside the
    # core binary.  The official Xray release ships them, but AntLighting must
    # not refuse to start when they are missing, so the core manager turns this
    # off if the files are absent.
    use_geo_data: bool = True


def _param(cfg: ServerConfig, *keys: str, default: str = "") -> str:
    for key in keys:
        value = cfg.params.get(key.lower())
        if value not in (None, ""):
            return value
    return default


def _as_bool(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _alpn(cfg: ServerConfig) -> list[str]:
    raw = _param(cfg, "alpn")
    if not raw:
        return []
    parts = [p for p in raw.replace(" ", ",").split(",") if p]
    return parts or []


def _stream_settings(cfg: ServerConfig, options: CoreOptions) -> dict[str, Any]:
    stream: dict[str, Any] = {"network": cfg.net}
    security = cfg.security or "none"

    if security == "tls":
        tls: dict[str, Any] = {}
        sni = _param(cfg, "sni", "servername", "servername") or (
            cfg.address if not _is_ip(cfg.address) else ""
        )
        if sni:
            tls["serverName"] = sni
        fp = _param(cfg, "fp")
        if fp:
            tls["fingerprint"] = fp
        alpn = _alpn(cfg)
        if alpn:
            tls["alpn"] = alpn
        if _as_bool(_param(cfg, "allowinsecure", "allowInsecure", "insecure", "skip-cert-verify")):
            tls["allowInsecure"] = True
        stream["security"] = "tls"
        stream["tlsSettings"] = tls
    elif security == "reality":
        reality: dict[str, Any] = {"show": False}
        sni = _param(cfg, "sni", "sname") or cfg.address
        reality["serverName"] = sni
        public_key = _param(cfg, "pbk", "publickey")
        if public_key:
            reality["publicKey"] = public_key
        short_id = _param(cfg, "sid", "shortid")
        if short_id:
            reality["shortId"] = short_id
        spider_x = _param(cfg, "spx", "spiderx")
        if spider_x:
            reality["spiderX"] = spider_x
        fp = _param(cfg, "fp") or "chrome"
        reality["fingerprint"] = fp
        stream["security"] = "reality"
        stream["realitySettings"] = reality
    elif security == "xtls":
        # Legacy; keep the SNI but drop to TLS — modern Xray removed xtls.
        stream["security"] = "tls"
        sni = _param(cfg, "sni") or cfg.address
        stream["tlsSettings"] = {"serverName": sni, "allowInsecure": True}
    else:
        stream["security"] = "none"

    net = cfg.net
    if net == "ws":
        ws: dict[str, Any] = {}
        path = _param(cfg, "path")
        if path:
            ws["path"] = path
        host = _param(cfg, "host")
        if host:
            ws["headers"] = {"Host": host}
        ed = _param(cfg, "ed")
        if ed.isdigit():
            ws["maxEarlyData"] = int(ed)
        eh = _param(cfg, "eh")
        if eh:
            ws["earlyDataHeaderName"] = eh
        stream["wsSettings"] = ws
    elif net == "grpc":
        grpc: dict[str, Any] = {}
        service = _param(cfg, "servicename", "serviceName", "authority")
        if service:
            grpc["serviceName"] = service
        mode = _param(cfg, "mode")
        if mode.lower() == "multi":
            grpc["multiMode"] = True
        stream["grpcSettings"] = grpc
    elif net == "xhttp":
        xhttp: dict[str, Any] = {}
        path = _param(cfg, "path")
        if path:
            xhttp["path"] = path
        host = _param(cfg, "host")
        if host:
            xhttp["host"] = host
        mode = _param(cfg, "mode")
        if mode:
            xhttp["mode"] = mode
        extra = _param(cfg, "extra")
        if extra:
            try:
                xhttp["extra"] = json.loads(extra)
            except (json.JSONDecodeError, ValueError):
                pass
        stream["xhttpSettings"] = xhttp
    elif net == "splithttp":
        sp: dict[str, Any] = {}
        path = _param(cfg, "path")
        if path:
            sp["path"] = path
        host = _param(cfg, "host")
        if host:
            sp["host"] = host
        stream["splithttpSettings"] = sp
    elif net == "httpupgrade":
        hu: dict[str, Any] = {}
        path = _param(cfg, "path")
        if path:
            hu["path"] = path
        host = _param(cfg, "host")
        if host:
            hu["host"] = host
        stream["httpupgradeSettings"] = hu
    elif net == "http":
        http: dict[str, Any] = {}
        path = _param(cfg, "path")
        if path:
            http["path"] = path
        host = _param(cfg, "host")
        if host:
            http["host"] = [host]
        alpn = _alpn(cfg)
        if alpn:
            http["alpn"] = alpn
        stream["httpSettings"] = http
    elif net == "kcp":
        kcp: dict[str, Any] = {
            "mtu": 1350,
            "tti": 50,
            "uplinkCapacity": 12,
            "downlinkCapacity": 100,
            "congestion": False,
            "readBufferSize": 2,
            "writeBufferSize": 2,
        }
        header = _param(cfg, "headertype", "headerType") or "none"
        kcp["header"] = {"type": header}
        seed = _param(cfg, "seed")
        if seed:
            kcp["seed"] = seed
        stream["kcpSettings"] = kcp
    elif net == "quic":
        quic: dict[str, Any] = {"security": "none", "key": ""}
        header = _param(cfg, "headertype", "headerType")
        if header and header != "none":
            quic["header"] = {"type": header}
        stream["quicSettings"] = quic
    else:  # raw / tcp
        header = _param(cfg, "headertype", "headerType")
        if header and header.lower() != "none":
            stream["rawSettings"] = {"header": {"type": header}}

    sockopt: dict[str, Any] = {}
    if options.fragment and security in ("tls", "reality"):
        sockopt["tcpSettings"] = {
            "header": {
                "type": "http",
                "request": {"headers": {"Connection": ["keep-alive"]}},
            }
        }
        sockopt["tcpNoDelay"] = True
    # Bind the physical NIC so the TUN inbound cannot route through itself.
    iface = _param(cfg, "interface")
    if iface:
        sockopt["interface"] = iface
    if sockopt:
        stream["sockopt"] = sockopt

    return stream


def _is_ip(address: str) -> bool:
    if not address:
        return False
    if address.count(":") >= 2:
        return True
    parts = address.split(".")
    if len(parts) != 4:
        return False
    return all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def _proxy_outbound(cfg: ServerConfig, options: CoreOptions) -> dict[str, Any]:
    """Build the dial-out ``outbounds`` entry for a single server."""
    if cfg.scheme == "vless":
        user: dict[str, Any] = {"id": cfg.credential, "level": 0}
        encryption = _param(cfg, "encryption")
        user["encryption"] = encryption or "none"
        flow = _param(cfg, "flow")
        if flow:
            user["flow"] = flow
        return {
            "protocol": "vless",
            "tag": "proxy",
            "settings": {
                "vnext": [{"address": cfg.address, "port": cfg.port, "users": [user]}]
            },
            "streamSettings": _stream_settings(cfg, options),
            "mux": {
                "enabled": bool(options.mux_enabled) and not flow,
                "concurrency": options.mux_concurrency,
            },
        }

    if cfg.scheme == "vmess":
        security = _param(cfg, "scy", "security") or "auto"
        user = {"id": cfg.credential, "level": 0, "security": security}
        aid = _param(cfg, "aid", "alterid")
        if aid.isdigit():
            user["alterId"] = int(aid)
        else:
            user["alterId"] = 0
        return {
            "protocol": "vmess",
            "tag": "proxy",
            "settings": {
                "vnext": [{"address": cfg.address, "port": cfg.port, "users": [user]}]
            },
            "streamSettings": _stream_settings(cfg, options),
            "mux": {"enabled": bool(options.mux_enabled), "concurrency": options.mux_concurrency},
        }

    if cfg.scheme == "trojan":
        server: dict[str, Any] = {
            "address": cfg.address,
            "port": cfg.port,
            "password": cfg.credential,
            "level": 0,
        }
        flow = _param(cfg, "flow")
        if flow:
            server["flow"] = flow
        return {
            "protocol": "trojan",
            "tag": "proxy",
            "settings": {"servers": [server]},
            "streamSettings": _stream_settings(cfg, options),
            "mux": {"enabled": bool(options.mux_enabled), "concurrency": options.mux_concurrency},
        }

    if cfg.scheme == "ss":
        method = _param(cfg, "method") or "aes-256-gcm"
        server = {
            "address": cfg.address,
            "port": cfg.port,
            "method": method,
            "password": cfg.credential,
            "level": 0,
        }
        if _as_bool(_param(cfg, "uot")):
            server["uot"] = True
        return {
            "protocol": "shadowsocks",
            "tag": "proxy",
            "settings": {"servers": [server]},
            "streamSettings": {"network": "tcp", "security": "none"},
            "mux": {"enabled": bool(options.mux_enabled), "concurrency": options.mux_concurrency},
        }

    if cfg.scheme == "hysteria2":
        cfg_out: dict[str, Any] = {
            "protocol": "hysteria2",
            "tag": "proxy",
            "settings": {"version": 2, "address": cfg.address, "port": cfg.port},
            "streamSettings": {"network": "hysteria", "security": "tls"},
        }
        stream = cfg_out["streamSettings"]
        hysteria: dict[str, Any] = {}
        auth = cfg.credential or _param(cfg, "auth", "password")
        if auth:
            hysteria["auth"] = auth
        obfs = _param(cfg, "obfs")
        if obfs:
            hysteria["obfs"] = obfs
            obfs_pw = _param(cfg, "obfs-password", "obfspassword")
            if obfs_pw:
                hysteria["obfsPassword"] = obfs_pw
        bw_up = _param(cfg, "up", "upmbps")
        if bw_up.isdigit():
            hysteria["up"] = f"{int(bw_up)} mbps"
        bw_down = _param(cfg, "down", "downmbps")
        if bw_down.isdigit():
            hysteria["down"] = f"{int(bw_down)} mbps"
        stream["hysteriaSettings"] = hysteria

        tls: dict[str, Any] = {}
        sni = _param(cfg, "sni", "peer") or cfg.address
        tls["serverName"] = sni
        if _as_bool(_param(cfg, "insecure", "allowinsecure")):
            tls["allowInsecure"] = True
        alpn = _alpn(cfg)
        tls["alpn"] = alpn or ["h3"]
        stream["tlsSettings"] = tls
        return cfg_out

    if cfg.scheme == "wireguard":
        public_key = _param(cfg, "publickey", "pbk")
        allowed = _param(cfg, "allowedips")
        allowed_ips = [a for a in allowed.split(",") if a] if allowed else ["0.0.0.0/0", "::/0"]
        reserved_raw = _param(cfg, "reserved")
        reserved: list[int] = []
        if reserved_raw:
            try:
                parsed = json.loads(reserved_raw)
                if isinstance(parsed, list) and len(parsed) == 3:
                    reserved = [int(x) for x in parsed]
            except (json.JSONDecodeError, ValueError, TypeError):
                parts = [p for p in reserved_raw.split(",") if p.strip()]
                if len(parts) == 3 and all(p.strip().isdigit() for p in parts):
                    reserved = [int(p) for p in parts]

        address_raw = _param(cfg, "address", "localaddress")
        addresses = (
            [a for a in address_raw.split(",") if a]
            if address_raw
            else ["172.16.0.2/32", "2606:4700:110:8a0e:1a3:2a8b:5d7b:4c31/128"]
        )
        keep_alive = _param(cfg, "keepalive")
        settings: dict[str, Any] = {
            "secretKey": cfg.credential,
            "address": addresses,
            "peers": [
                {
                    "publicKey": public_key,
                    "allowedIPs": allowed_ips,
                    "endpoint": f"{cfg.address}:{cfg.port}",
                    "keepAlive": int(keep_alive) if keep_alive.isdigit() else 0,
                    "level": 0,
                }
            ],
            "mtu": int(_param(cfg, "mtu")) if _param(cfg, "mtu").isdigit() else 1420,
        }
        if reserved:
            settings["reserved"] = reserved
        if not options.ipv6:
            settings["domainStrategy"] = "ForceIPv4"
        return {"protocol": "wireguard", "tag": "proxy", "settings": settings}

    raise ValueError(f"cannot build an outbound for scheme '{cfg.scheme}'")


def _routing(server: ServerConfig, options: CoreOptions) -> dict[str, Any]:
    rules: list[dict[str, Any]] = []

    # AntLighting's own traffic (the collector, latency tests) must stay direct,
    # otherwise refreshing servers would itself go through the tunnel.
    rules.append(
        {
            "type": "field",
            "domain": ["domain:antlighting.invalid"],
            "outboundTag": "direct",
        }
    )

    if options.bypass_lan:
        if options.use_geo_data:
            rules.append({"type": "field", "domain": DIRECT_DOMAINS, "outboundTag": "direct"})
            rules.append({"type": "field", "ip": DIRECT_IPS, "outboundTag": "direct"})
        else:
            # No geoip.dat/geosite.dat available: spell the private ranges out.
            rules.append({"type": "field", "ip": list(PRIVATE_CIDRS), "outboundTag": "direct"})
            rules.append(
                {
                    "type": "field",
                    "domain": ["domain:local", "domain:localdomain", "domain:lan", "domain:home"],
                    "outboundTag": "direct",
                }
            )

    # Always keep the proxy server itself reachable directly.
    if server.scheme not in ("wireguard",):
        target = f"{server.address}/32" if _is_ip(server.address) else server.address
        if _is_ip(server.address):
            rules.append({"type": "field", "ip": [target], "outboundTag": "direct"})
        else:
            rules.append({"type": "field", "domain": [f"full:{target}"], "outboundTag": "direct"})

    if options.block_ads and options.use_geo_data:
        rules.append(
            {"type": "field", "domain": ["geosite:category-ads-all"], "outboundTag": "block"}
        )

    rules.append(
        {
            "type": "field",
            "network": "tcp,udp",
            "outboundTag": "proxy",
        }
    )

    return {
        "domainStrategy": "AsIs",
        "domainMatcher": "mph",
        "rules": rules,
        "balancers": [],
    }


def _dns(options: CoreOptions) -> dict[str, Any]:
    servers: list[Any] = []
    for entry in options.dns_servers:
        if entry == "localhost":
            servers.append(entry)
            continue
        if entry.startswith(("https://", "https+", "quic+")) and options.use_geo_data:
            # Prefer remote DoH for foreign names, fall back to the resolver
            # configured by the OS for local names.
            servers.append(
                {
                    "address": entry,
                    "domains": ["geosite:geolocation-!cn"],
                    "expectIPs": ["geoip:!private"],
                }
            )
        else:
            servers.append(entry)
    return {"servers": servers or ["localhost"], "queryStrategy": "UseIP"}


def _inbounds(endpoint: LocalEndpoint, tun: TunOptions) -> list[dict[str, Any]]:
    inbounds: list[dict[str, Any]] = []
    sniff = (
        {"enabled": True, "destOverride": ["http", "tls", "quic", "fakedns"], "metadataOnly": False}
        if endpoint.sniffing
        else {"enabled": False}
    )
    inbounds.append(
        {
            "tag": "socks-in",
            "port": endpoint.socks_port,
            "listen": endpoint.listen,
            "protocol": "socks",
            "settings": {"auth": "noauth", "udp": bool(endpoint.udp), "ip": endpoint.listen},
            "sniffing": sniff,
        }
    )
    if endpoint.http_port:
        inbounds.append(
            {
                "tag": "http-in",
                "port": endpoint.http_port,
                "listen": endpoint.listen,
                "protocol": "http",
                "settings": {"allowTransparent": False},
                "sniffing": sniff,
            }
        )
    if tun.enabled:
        settings: dict[str, Any] = {
            "name": tun.name,
            "desc": tun.desc,
            "mtu": tun.mtu,
        }
        if tun.gateway:
            settings["gateway"] = list(tun.gateway)
        if tun.dns:
            settings["dns"] = list(tun.dns)
        if tun.auto_system_routing_table:
            settings["autoSystemRoutingTable"] = list(tun.auto_system_routing_table)
            settings["autoOutboundsInterface"] = "auto"
        inbounds.append({"tag": "tun-in", "protocol": "tun", "settings": settings})
    return inbounds


def build_config(
    server: ServerConfig,
    endpoint: LocalEndpoint | None = None,
    options: CoreOptions | None = None,
    tun: TunOptions | None = None,
) -> dict[str, Any]:
    """Return a complete Xray-core JSON configuration for *server*."""
    endpoint = endpoint or LocalEndpoint()
    options = options or CoreOptions()
    tun = tun or TunOptions()

    config: dict[str, Any] = {
        "log": {"loglevel": options.log_level, "access": ""},
        "api": {"tag": "api", "services": ["StatsService"]},
        "stats": {},
        "inbounds": _inbounds(endpoint, tun),
        "outbounds": [
            _proxy_outbound(server, options),
            {
                "protocol": "freedom",
                "tag": "direct",
                "settings": {"domainStrategy": "UseIP" if options.ipv6 else "UseIPv4"},
            },
            {"protocol": "blackhole", "tag": "block", "settings": {"response": {"type": "http"}}},
        ],
        "routing": _routing(server, options),
        "dns": _dns(options),
        "policy": {
            "levels": {"0": {"handshake": 4, "connIdle": 300, "uplinkOnly": 2, "downlinkOnly": 5}},
            "system": {"statsInboundUplink": False, "statsInboundDownlink": False},
        },
    }
    return config


def build_config_json(server: ServerConfig, **kwargs: Any) -> str:
    return json.dumps(build_config(server, **kwargs), indent=2, ensure_ascii=False)


def validate_structure(config: dict[str, Any]) -> list[str]:
    """Cheap structural sanity check used before handing the JSON to the core."""
    problems: list[str] = []
    if not isinstance(config, dict):
        return ["config is not an object"]
    if not config.get("inbounds"):
        problems.append("no inbounds defined")
    outbounds = config.get("outbounds") or []
    if not outbounds:
        problems.append("no outbounds defined")
    tags = {o.get("tag") for o in outbounds if isinstance(o, dict)}
    for rule in (config.get("routing") or {}).get("rules", []):
        tag = rule.get("outboundTag")
        if tag and tag not in tags:
            problems.append(f"routing rule points at unknown outbound '{tag}'")
    for outbound in outbounds:
        if not isinstance(outbound, dict):
            problems.append("outbound is not an object")
            continue
        settings = outbound.get("settings") or {}
        if outbound.get("protocol") == "vless":
            users = (settings.get("vnext") or [{}])[0].get("users") or []
            if not users or not users[0].get("id"):
                problems.append("vless outbound has no user id")
        elif outbound.get("protocol") == "shadowsocks":
            servers = settings.get("servers") or []
            if not servers or not servers[0].get("password"):
                problems.append("shadowsocks outbound has no password")
    return problems
