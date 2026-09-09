"""Configuration parsing tests: valid, malformed and duplicate inputs."""

from __future__ import annotations

import base64
import json

import pytest

from antlighting.configs.models import ServerConfig
from antlighting.configs.naming import country_from_flag, flag_for, normalise_remark
from antlighting.configs.parser import (
    extract_links,
    parse_link,
    parse_text,
)
from tests.conftest import (
    HYSTERIA2,
    SSR,
    SS,
    TROJAN,
    VLESS_REALITY,
    VLESS_TLS,
    VMESS,
    WIREGUARD,
)


class TestValidLinks:
    def test_vless_tls(self):
        cfg = parse_link(VLESS_TLS)
        assert cfg is not None
        assert cfg.scheme == "vless"
        assert cfg.address == "203.0.113.10"
        assert cfg.port == 443
        assert cfg.credential == "2f1c0f4e-1d0a-4a2a-9f5f-2c0f0a1b2c3d"
        assert cfg.security == "tls"
        assert cfg.net == "raw"  # 'tcp' is normalised to Xray's current name
        assert cfg.params["sni"] == "example.com"
        assert cfg.params["fp"] == "chrome"
        assert cfg.usable

    def test_vless_reality(self):
        cfg = parse_link(VLESS_REALITY)
        assert cfg is not None
        assert cfg.security == "reality"
        assert cfg.params["pbk"] == "PUBKEY123"
        assert cfg.params["sid"] == "ab12cd34"
        assert cfg.params["flow"] == "xtls-rprx-vision"
        assert cfg.net == "raw"

    def test_vmess_base64_json(self):
        cfg = parse_link(VMESS)
        assert cfg is not None
        assert cfg.scheme == "vmess"
        assert cfg.address == "203.0.113.20"
        assert cfg.port == 443
        assert cfg.credential == "11111111-2222-3333-4444-555555555555"
        assert cfg.net == "ws"
        assert cfg.params["path"] == "/ws"
        assert cfg.params["host"] == "example.com"
        assert cfg.security == "tls"
        assert cfg.remark.startswith("NL")

    def test_vmess_of_a_vless_link(self):
        inner = "vless://aaaa-bbbb@198.51.100.9:443?security=tls&type=ws&path=/x#inner"
        blob = base64.b64encode(inner.encode()).decode()
        cfg = parse_link(f"vmess://{blob}")
        assert cfg is not None
        assert cfg.scheme == "vless"
        assert cfg.address == "198.51.100.9"

    def test_trojan(self):
        cfg = parse_link(TROJAN)
        assert cfg is not None
        assert cfg.scheme == "trojan"
        assert cfg.credential == "hunter2"
        assert cfg.address == "trojan.example.org"
        assert cfg.security == "tls"

    def test_shadowsocks(self):
        cfg = parse_link(SS)
        assert cfg is not None
        assert cfg.scheme == "ss"
        assert cfg.credential == "password"
        assert cfg.params["method"] == "aes-256-gcm"
        assert cfg.port == 8388

    def test_hysteria2(self):
        cfg = parse_link(HYSTERIA2)
        assert cfg is not None
        assert cfg.scheme == "hysteria2"
        assert cfg.credential == "secret"
        assert cfg.port == 8443
        assert cfg.params["obfs"] == "salamander"
        assert cfg.params["obfs-password"] == "obfspw"
        assert cfg.params["insecure"] == "1"

    def test_wireguard(self):
        cfg = parse_link(WIREGUARD)
        assert cfg is not None
        assert cfg.scheme == "wireguard"
        assert cfg.credential == "PRIVKEY"
        assert cfg.params["publickey"] == "PUBKEY"
        assert cfg.params["reserved"] == "1,2,3"

    def test_unsupported_scheme_is_parsed_but_marked_unusable(self):
        cfg = parse_link(SSR)
        assert cfg is not None
        assert cfg.scheme == "ssr"
        assert cfg.usable is False
        assert "ShadowsocksR" in cfg.unusable_reason

    def test_unsupported_shadowsocks_cipher(self):
        blob = base64.b64encode(b"rc4-md5:pw").decode()
        cfg = parse_link(f"ss://{blob}@192.0.2.7:8388#x")
        assert cfg is not None
        assert cfg.usable is False
        assert "rc4-md5" in cfg.unusable_reason

    def test_ipv6_address_in_brackets(self):
        cfg = parse_link("vless://aaaa-bbbb@[2001:db8::42]:443?security=tls&type=tcp#v6")
        assert cfg is not None
        assert cfg.address == "2001:db8::42"
        assert cfg.port == 443

    def test_url_encoded_uuid(self):
        cfg = parse_link("vless://aaaa%2Dbbbb@198.51.100.3:443?security=tls&type=tcp#enc")
        assert cfg is not None
        assert cfg.credential == "aaaa-bbbb"

    def test_mixed_case_query_keys_are_normalised(self):
        cfg = parse_link(
            "vless://aaaa-bbbb@198.51.100.4:443?Security=TLS&Type=WS&Host=h.example"
            "&headerType=none#case"
        )
        assert cfg is not None
        assert cfg.security == "tls"
        assert cfg.net == "ws"
        assert cfg.params["host"] == "h.example"

    def test_default_ports(self):
        vless = parse_link("vless://aaaa-bbbb@198.51.100.5?security=tls&type=tcp#p")
        assert vless is not None and vless.port == 443
        wg = parse_link("wireguard://pk@198.51.100.6?publicKey=x#wg")
        assert wg is not None and wg.port == 51820


class TestMalformedLinks:
    @pytest.mark.parametrize(
        "link",
        [
            "",
            "not-a-link",
            "vless://",
            "vless://@:443",
            "http://example.com",
            "ssh://user@host",
            "vless://aaaa@1.2.3.4:99999?security=tls",  # port out of range
            "vless://aaaa@1.2.3.4:notaport?security=tls",
            "vmess://not-valid-base64!!!",
            "vmess://" + base64.b64encode(b"{}").decode(),
            "ss://no-base64-no-colon@1.2.3.4:443",
            "vless://onlyuuidnohost?security=tls",
            "trojan://@host.example:443",  # empty password
        ],
    )
    def test_rejected_without_raising(self, link):
        assert parse_link(link) is None

    def test_trailing_punctuation_is_stripped(self):
        cfg = parse_link(VLESS_TLS + ".,")
        assert cfg is not None
        assert cfg.identity == parse_link(VLESS_TLS).identity

    def test_garbage_text_yields_nothing(self):
        result = parse_text("<html><body>404 Not Found</body></html>")
        assert result.configs == []
        assert result.rejected == []

    def test_mixed_valid_and_garbage(self):
        text = "\n".join(["garbage line", VLESS_TLS, "!!!", TROJAN, ""])
        result = parse_text(text)
        assert len(result.configs) == 2


class TestDuplicates:
    def test_same_server_different_remark_is_one_server(self):
        a = parse_link(VLESS_TLS)
        b = parse_link(VLESS_TLS.split("#")[0] + "#completely%20different%20name")
        assert a is not None and b is not None
        assert a.identity == b.identity

    def test_different_port_is_a_different_server(self):
        a = parse_link(VLESS_TLS)
        b = parse_link(VLESS_TLS.replace(":443?", ":8443?"))
        assert a is not None and b is not None
        assert a.identity != b.identity

    def test_different_credential_is_a_different_server(self):
        a = parse_link(VLESS_TLS)
        b = parse_link(VLESS_TLS.replace("2f1c0f4e-1d0a-4a2a-9f5f-2c0f0a1b2c3d", "ffffffff-ffff-ffff-ffff-ffffffffffff"))
        assert a is not None and b is not None
        assert a.identity != b.identity

    def test_different_sni_is_a_different_server(self):
        a = parse_link(VLESS_TLS)
        b = parse_link(VLESS_TLS.replace("sni=example.com", "sni=other.example"))
        assert a is not None and b is not None
        assert a.identity != b.identity

    def test_dedupe_within_one_blob(self):
        text = "\n".join([VLESS_TLS, VLESS_TLS, VLESS_TLS])
        result = parse_text(text)
        assert len(result.configs) == 1
        assert result.duplicates == 2

    def test_dedupe_across_blobs(self):
        from antlighting.configs.parser import parse_all

        merged = parse_all([(VLESS_TLS + "\n" + TROJAN, "a"), (VLESS_TLS + "\n" + SS, "b")])
        identities = {c.identity for c in merged.configs}
        assert len(identities) == 3
        assert merged.duplicates == 1


class TestExtraction:
    def test_extract_from_html(self):
        html = f'<div class="msg"><a href="{VLESS_TLS}">link</a><br>{TROJAN}</div>'
        links = extract_links(html)
        assert len(links) == 2

    def test_extract_from_base64_blob(self):
        blob = base64.b64encode(f"{VLESS_TLS}\n{TROJAN}\n".encode()).decode()
        links = extract_links(blob)
        assert len(links) == 2

    def test_plain_text_blob_is_detected(self):
        links = extract_links(f"{VLESS_TLS}\n{TROJAN}")
        assert len(links) == 2

    def test_comments_are_ignored(self):
        text = "# support-url: https://t.me/x\n# criterion: something\n" + VLESS_TLS
        result = parse_text(text)
        assert len(result.configs) == 1


class TestNaming:
    def test_flag_emoji_to_country(self):
        assert country_from_flag("\U0001F1E9\U0001F1EA") == "DE"
        assert country_from_flag("\U0001F1FA\U0001F1F8") == "US"

    def test_flag_for(self):
        assert flag_for("DE") == "\U0001F1E9\U0001F1EA"
        assert flag_for(None) == "\U0001F310"

    @pytest.mark.parametrize(
        "remark,expected_code",
        [
            ("US \U0001F1FA\U0001F1F8 | @Raydikalx | B5554D", "US"),
            ("\U0001F1E9\U0001F1EA Germany #3", "DE"),
            ("Netherlands - Amsterdam", "NL"),
            ("🇫🇮 Suomi", "FI"),
            ("JP Tokyo 01", "JP"),
            ("no country here at all", None),
        ],
    )
    def test_country_detection(self, remark, expected_code):
        _name, _country, code, _provider = normalise_remark(remark, "1.2.3.4")
        assert code == expected_code

    def test_provider_extraction(self):
        _name, _country, _code, provider = normalise_remark("US | @Raydikalx | B5554D", "1.2.3.4")
        assert provider == "Raydikalx"

    def test_display_name_falls_back(self):
        cfg = parse_link("vless://aaaa-bbbb@198.51.100.77:443?security=tls&type=tcp#")
        assert cfg is not None
        assert cfg.display_name().startswith("VLESS ")

    def test_server_config_round_trip(self):
        cfg = parse_link(VLESS_TLS)
        assert cfg is not None
        restored = ServerConfig.from_dict(cfg.to_dict())
        assert restored.identity == cfg.identity
        assert restored.params == cfg.params
        assert restored.credential == cfg.credential
