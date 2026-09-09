"""Xray configuration generation."""

from __future__ import annotations

import json
import os

import pytest

from antlighting.configs.parser import parse_link
from antlighting.core.config_builder import (
    CoreOptions,
    LocalEndpoint,
    TunOptions,
    build_config,
    validate_structure,
)
from tests.conftest import (
    HYSTERIA2,
    SAMPLE_LINKS,
    SS,
    TROJAN,
    VLESS_REALITY,
    VLESS_TLS,
    VMESS,
    WIREGUARD,
    sample_configs,
)


def _cfg(link):
    cfg = parse_link(link)
    assert cfg is not None
    return cfg


@pytest.fixture()
def options():
    return CoreOptions(use_geo_data=False)


class TestOutbounds:
    def test_vless_tls_outbound(self, options):
        config = build_config(_cfg(VLESS_TLS), options=options)
        proxy = config["outbounds"][0]
        assert proxy["protocol"] == "vless"
        vnext = proxy["settings"]["vnext"][0]
        assert vnext["address"] == "203.0.113.10"
        assert vnext["port"] == 443
        assert vnext["users"][0]["id"].startswith("2f1c0f4e")
        assert vnext["users"][0]["encryption"] == "none"
        stream = proxy["streamSettings"]
        assert stream["security"] == "tls"
        assert stream["tlsSettings"]["serverName"] == "example.com"
        assert stream["tlsSettings"]["fingerprint"] == "chrome"

    def test_vless_reality_outbound(self, options):
        config = build_config(_cfg(VLESS_REALITY), options=options)
        stream = config["outbounds"][0]["streamSettings"]
        assert stream["security"] == "reality"
        reality = stream["realitySettings"]
        assert reality["publicKey"] == "PUBKEY123"
        assert reality["shortId"] == "ab12cd34"
        assert reality["serverName"] == "www.microsoft.com"
        assert config["outbounds"][0]["settings"]["vnext"][0]["users"][0]["flow"] == (
            "xtls-rprx-vision"
        )
        # mux must stay off while an XTLS flow is in use
        assert config["outbounds"][0]["mux"]["enabled"] is False

    def test_tcp_is_normalised_to_raw(self, options):
        config = build_config(_cfg(VLESS_TLS), options=options)
        assert config["outbounds"][0]["streamSettings"]["network"] == "raw"

    def test_websocket_transport(self, options):
        config = build_config(_cfg(VMESS), options=options)
        stream = config["outbounds"][0]["streamSettings"]
        assert stream["network"] == "ws"
        assert stream["wsSettings"]["path"] == "/ws"
        assert stream["wsSettings"]["headers"]["Host"] == "example.com"

    def test_vmess_outbound(self, options):
        config = build_config(_cfg(VMESS), options=options)
        proxy = config["outbounds"][0]
        assert proxy["protocol"] == "vmess"
        user = proxy["settings"]["vnext"][0]["users"][0]
        assert user["alterId"] == 0
        assert user["security"] == "auto"

    def test_trojan_outbound(self, options):
        config = build_config(_cfg(TROJAN), options=options)
        proxy = config["outbounds"][0]
        assert proxy["protocol"] == "trojan"
        server = proxy["settings"]["servers"][0]
        assert server["password"] == "hunter2"
        assert server["address"] == "trojan.example.org"

    def test_shadowsocks_uses_the_xray_protocol_name(self, options):
        config = build_config(_cfg(SS), options=options)
        proxy = config["outbounds"][0]
        assert proxy["protocol"] == "shadowsocks"
        server = proxy["settings"]["servers"][0]
        assert server["method"] == "aes-256-gcm"
        assert server["password"] == "password"

    def test_hysteria2_outbound(self, options):
        config = build_config(_cfg(HYSTERIA2), options=options)
        proxy = config["outbounds"][0]
        assert proxy["protocol"] == "hysteria2"
        assert proxy["settings"]["version"] == 2
        stream = proxy["streamSettings"]
        assert stream["network"] == "hysteria"
        assert stream["hysteriaSettings"]["auth"] == "secret"
        assert stream["hysteriaSettings"]["obfs"] == "salamander"

    def test_wireguard_outbound(self, options):
        config = build_config(_cfg(WIREGUARD), options=options)
        proxy = config["outbounds"][0]
        assert proxy["protocol"] == "wireguard"
        settings = proxy["settings"]
        assert settings["secretKey"] == "PRIVKEY"
        peer = settings["peers"][0]
        assert peer["publicKey"] == "PUBKEY"
        assert peer["endpoint"] == "192.0.2.120:51820"
        assert settings["reserved"] == [1, 2, 3]
        assert settings["mtu"] == 1280

    def test_unsupported_scheme_raises(self, options):
        with pytest.raises(ValueError):
            build_config(_cfg(SAMPLE_LINKS[7]), options=options)  # ssr


class TestInbounds:
    def test_socks_and_http_listeners(self, options):
        config = build_config(
            _cfg(VLESS_TLS),
            endpoint=LocalEndpoint(socks_port=20808, http_port=20809),
            options=options,
        )
        protocols = [i["protocol"] for i in config["inbounds"]]
        assert protocols == ["socks", "http"]
        assert config["inbounds"][0]["port"] == 20808
        assert config["inbounds"][0]["settings"]["udp"] is True

    def test_http_port_zero_omits_the_listener(self, options):
        config = build_config(
            _cfg(VLESS_TLS),
            endpoint=LocalEndpoint(socks_port=1, http_port=0),
            options=options,
        )
        assert len(config["inbounds"]) == 1

    def test_tun_inbound_fields_match_the_core_schema(self, options):
        tun = TunOptions(enabled=True)
        config = build_config(_cfg(VLESS_TLS), options=options, tun=tun)
        tun_inbound = [i for i in config["inbounds"] if i["protocol"] == "tun"]
        assert len(tun_inbound) == 1
        settings = tun_inbound[0]["settings"]
        # Field names verified against Xray-core infra/conf/tun.go
        assert set(settings) <= {
            "name", "desc", "mtu", "gateway", "dns", "userLevel",
            "autoSystemRoutingTable", "autoOutboundsInterface",
        }
        assert settings["gateway"] == ["198.18.0.1/30"]
        assert settings["dns"] == ["198.18.0.2"]

    def test_tun_subnet_is_not_covered_by_the_lan_bypass_rule(self, options):
        """198.18/15 must not be in the direct list or DNS would loop."""
        config = build_config(_cfg(VLESS_TLS), options=options, tun=TunOptions(enabled=True))
        direct_ips = []
        for rule in config["routing"]["rules"]:
            if rule.get("outboundTag") == "direct":
                direct_ips.extend(rule.get("ip", []))
        assert not any(ip.startswith("198.18.") for ip in direct_ips)


class TestRouting:
    def test_catch_all_rule_points_at_the_proxy(self, options):
        config = build_config(_cfg(VLESS_TLS), options=options)
        rules = config["routing"]["rules"]
        assert rules[-1]["outboundTag"] == "proxy"

    def test_uplink_is_always_direct(self, options):
        config = build_config(_cfg(VLESS_TLS), options=options)
        direct = [r for r in config["routing"]["rules"] if r.get("outboundTag") == "direct"]
        flattened = [v for rule in direct for v in (rule.get("ip", []) + rule.get("domain", []))]
        assert any("203.0.113.10" in value for value in flattened)

    def test_hostname_uplink_uses_a_full_domain_rule(self, options):
        config = build_config(_cfg(TROJAN), options=options)
        direct = [r for r in config["routing"]["rules"] if r.get("outboundTag") == "direct"]
        flattened = [v for rule in direct for v in rule.get("domain", [])]
        assert "full:trojan.example.org" in flattened

    def test_wireguard_has_no_uplink_direct_rule(self, options):
        config = build_config(_cfg(WIREGUARD), options=options)
        direct = [r for r in config["routing"]["rules"] if r.get("outboundTag") == "direct"]
        flattened = [v for rule in direct for v in (rule.get("ip", []) + rule.get("domain", []))]
        assert not any("192.0.2.120" in v for v in flattened)

    def test_geo_rules_are_omitted_without_geo_data(self):
        config = build_config(_cfg(VLESS_TLS), options=CoreOptions(use_geo_data=False))
        blob = json.dumps(config)
        assert "geosite:" not in blob
        assert "geoip:" not in blob
        # ...and the private ranges are spelled out instead
        assert "10.0.0.0/8" in blob

    def test_geo_rules_are_used_when_available(self):
        config = build_config(_cfg(VLESS_TLS), options=CoreOptions(use_geo_data=True))
        blob = json.dumps(config)
        assert "geosite:private" in blob

    def test_ad_blocking_needs_geo_data(self):
        without = build_config(
            _cfg(VLESS_TLS), options=CoreOptions(use_geo_data=False, block_ads=True)
        )
        assert "category-ads-all" not in json.dumps(without)
        with_geo = build_config(
            _cfg(VLESS_TLS), options=CoreOptions(use_geo_data=True, block_ads=True)
        )
        assert "category-ads-all" in json.dumps(with_geo)

    def test_every_routing_tag_resolves_to_an_outbound(self, options):
        for link in SAMPLE_LINKS:
            cfg = parse_link(link)
            if cfg is None or not cfg.usable:
                continue
            config = build_config(cfg, options=options)
            tags = {o["tag"] for o in config["outbounds"]}
            for rule in config["routing"]["rules"]:
                assert rule["outboundTag"] in tags


class TestValidation:
    def test_structure_check_passes_for_every_supported_scheme(self, options):
        for link in SAMPLE_LINKS:
            cfg = parse_link(link)
            if cfg is None or not cfg.usable:
                continue
            problems = validate_structure(build_config(cfg, options=options))
            assert problems == [], f"{cfg.scheme}: {problems}"

    def test_structure_check_flags_a_missing_user(self, options):
        config = build_config(_cfg(VLESS_TLS), options=options)
        config["outbounds"][0]["settings"]["vnext"][0]["users"][0]["id"] = ""
        assert validate_structure(config)

    def test_structure_check_flags_a_dangling_routing_tag(self, options):
        config = build_config(_cfg(VLESS_TLS), options=options)
        config["routing"]["rules"].append({"type": "field", "outboundTag": "nowhere"})
        assert validate_structure(config)

    def test_config_is_json_serialisable(self, options):
        for cfg in sample_configs():
            if cfg.usable:
                json.dumps(build_config(cfg, options=options))
