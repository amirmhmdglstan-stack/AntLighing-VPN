"""Server ranking: latency ordering, failure handling, reliability scoring."""

from __future__ import annotations

import time

from antlighting.configs.parser import parse_link
from antlighting.configs.ranking import (
    DEFAULT_PROTOCOL_PREFERENCE,
    rank_servers,
    score_server,
    select_best,
)
from tests.conftest import SAMPLE_LINKS, VLESS_TLS, sample_configs


def _config(link: str):
    cfg = parse_link(link)
    assert cfg is not None
    return cfg


def _metrics(status="unknown", latency=None, success=0, failure=0, consecutive=0, age=None):
    entry = {
        "status": status,
        "latency_ms": latency,
        "success_count": success,
        "failure_count": failure,
        "consecutive_failures": consecutive,
        "last_error": "",
    }
    if age is not None:
        entry["last_tested"] = time.time() - age
    return entry


class TestLatencyOrdering:
    def test_faster_working_server_ranks_first(self):
        configs = sample_configs()
        a, b = configs[0], configs[1]
        lookup = {
            a.identity: _metrics("working", latency=80.0, success=3),
            b.identity: _metrics("working", latency=300.0, success=3),
        }
        ranked = rank_servers(configs[:2], lookup)
        assert ranked[0].identity == a.identity
        assert ranked[0].latency_ms < ranked[1].latency_ms

    def test_latency_decides_among_equally_reliable_same_protocol_servers(self):
        # Same protocol so the protocol tie-breaker cannot interfere.
        configs = [
            _config(VLESS_TLS),
            _config(VLESS_TLS.replace(":443?", ":8443?")),
            _config(VLESS_TLS.replace("203.0.113.10", "203.0.113.99")),
        ]
        assert len({c.identity for c in configs}) == 3
        lookup = {
            c.identity: _metrics("working", latency=lat, success=5)
            for c, lat in zip(configs, [250.0, 90.0, 160.0])
        }
        ranked = rank_servers(configs, lookup)
        assert [r.latency_ms for r in ranked] == [90.0, 160.0, 250.0]

    def test_protocol_preference_never_overrides_a_real_latency_gap(self):
        # ss is the *least* preferred protocol but is 200 ms faster.
        ss = _config(SAMPLE_LINKS[4])
        vless = _config(VLESS_TLS)
        lookup = {
            ss.identity: _metrics("working", latency=90.0, success=5),
            vless.identity: _metrics("working", latency=290.0, success=5),
        }
        ranked = rank_servers([vless, ss], lookup)
        assert ranked[0].config.scheme == "ss"


class TestStatusOrdering:
    def test_working_beats_unknown_beats_failed(self):
        configs = sample_configs()[:3]
        lookup = {
            configs[0].identity: _metrics("failed", latency=10.0, failure=4, consecutive=4),
            configs[1].identity: _metrics("unknown"),
            configs[2].identity: _metrics("working", latency=400.0, success=1),
        }
        ranked = rank_servers(configs, lookup)
        assert ranked[0].status == "working"
        assert ranked[1].status == "unknown"
        assert ranked[2].status == "failed"

    def test_slow_server_ranks_below_working(self):
        configs = sample_configs()[:2]
        lookup = {
            configs[0].identity: _metrics("slow", latency=1500.0, success=2),
            configs[1].identity: _metrics("working", latency=700.0, success=2),
        }
        ranked = rank_servers(configs, lookup)
        assert ranked[0].status == "working"

    def test_invalid_configs_are_excluded_by_default(self):
        ssr = _config(SAMPLE_LINKS[7])
        assert ssr.usable is False
        ranked = rank_servers([ssr, sample_configs()[0]], {})
        assert len(ranked) == 1


class TestReliability:
    def test_stable_server_beats_faster_flaky_one(self):
        """The headline requirement: reliability outranks raw speed."""
        configs = sample_configs()[:2]
        fast_flaky, slow_stable = configs[0], configs[1]
        lookup = {
            # 60 ms but fails constantly
            fast_flaky.identity: _metrics(
                "working", latency=60.0, success=2, failure=18, consecutive=4
            ),
            # 220 ms but rock solid
            slow_stable.identity: _metrics(
                "working", latency=220.0, success=20, failure=0, consecutive=0
            ),
        }
        ranked = rank_servers(configs, lookup)
        assert ranked[0].identity == slow_stable.identity
        assert "fails in a row" in ranked[1].reason

    def test_consecutive_failures_are_penalised(self):
        cfg = sample_configs()[0]
        clean = score_server(
            cfg, _metrics("working", latency=100.0, success=10, consecutive=0), now=time.time()
        )
        broken = score_server(
            cfg, _metrics("working", latency=100.0, success=10, failure=3, consecutive=3),
            now=time.time(),
        )
        assert broken.score < clean.score

    def test_few_samples_do_not_dominate(self):
        """One lucky success should not outrank a well-tested server."""
        configs = sample_configs()[:2]
        lucky, proven = configs[0], configs[1]
        lookup = {
            lucky.identity: _metrics("working", latency=90.0, success=1, failure=0),
            proven.identity: _metrics("working", latency=130.0, success=30, failure=0),
        }
        ranked = rank_servers(configs, lookup)
        assert ranked[0].identity == proven.identity

    def test_reliability_ratio_is_exposed(self):
        cfg = sample_configs()[0]
        scored = score_server(cfg, _metrics("working", latency=100.0, success=3, failure=1))
        assert abs(scored.reliability - 0.75) < 1e-9
        assert scored.tested_count == 4


class TestFreshness:
    def test_stale_results_shrink_towards_unknown(self):
        cfg = sample_configs()[0]
        fresh = score_server(
            cfg, _metrics("working", latency=80.0, success=5, age=60), now=time.time()
        )
        stale = score_server(
            cfg, _metrics("working", latency=80.0, success=5, age=10 * 24 * 3600),
            now=time.time(),
        )
        assert stale.score < fresh.score
        assert "stale" in stale.reason

    def test_untested_servers_have_no_age(self):
        cfg = sample_configs()[0]
        scored = score_server(cfg, _metrics("unknown"), now=time.time())
        assert scored.age_seconds is None


class TestProtocolPreference:
    def test_preferred_protocol_wins_a_tie(self):
        vless = _config(VLESS_TLS)
        ss = _config(SAMPLE_LINKS[4])
        lookup = {
            vless.identity: _metrics("working", latency=100.0, success=5),
            ss.identity: _metrics("working", latency=100.0, success=5),
        }
        ranked = rank_servers([ss, vless], lookup)
        assert ranked[0].config.scheme == "vless"
        assert DEFAULT_PROTOCOL_PREFERENCE.index("vless") < DEFAULT_PROTOCOL_PREFERENCE.index("ss")

    def test_custom_preference_is_honoured(self):
        vless = _config(VLESS_TLS)
        ss = _config(SAMPLE_LINKS[4])
        lookup = {
            vless.identity: _metrics("working", latency=100.0, success=5),
            ss.identity: _metrics("working", latency=100.0, success=5),
        }
        ranked = rank_servers([ss, vless], lookup, preferred_protocols=["ss", "vless"])
        assert ranked[0].config.scheme == "ss"


class TestSelection:
    def test_select_best_skips_excluded_identities(self):
        configs = sample_configs()
        lookup = {c.identity: _metrics("working", latency=100.0, success=5) for c in configs}
        first = select_best(configs, lookup)
        assert first is not None
        second = select_best(configs, lookup, skip_identities=[first.identity])
        assert second is not None
        assert second.identity != first.identity

    def test_select_best_returns_none_when_everything_failed(self):
        configs = sample_configs()
        lookup = {c.identity: _metrics("failed", failure=3, consecutive=3) for c in configs}
        best = select_best(configs, lookup, allow_unknown=False)
        assert best is None

    def test_select_best_falls_back_to_unknown(self):
        configs = sample_configs()
        best = select_best(configs, {})
        assert best is not None
        assert best.status == "unknown"

    def test_ordering_is_deterministic(self):
        configs = sample_configs()
        first = [s.identity for s in rank_servers(configs, {})]
        second = [s.identity for s in rank_servers(configs, {})]
        assert first == second

    def test_scored_server_dict_is_json_safe(self):
        import json

        cfg = sample_configs()[0]
        scored = score_server(cfg, _metrics("working", latency=123.4, success=2))
        json.dumps(scored.as_dict())
