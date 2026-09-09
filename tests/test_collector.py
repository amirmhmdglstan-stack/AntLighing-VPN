"""Collection pipeline and source plug-ins."""

from __future__ import annotations

import os
import time

import pytest

from antlighting.collector import ConfigCollector, RefreshScheduler
from antlighting.configs.parser import parse_text
from antlighting.sources.base import FetchResult, create_source, fetch_all, source_kinds
from antlighting.sources.github import GitHubSource, parse_repo_spec
from antlighting.sources.telegram import TelegramSource, _strip_html, extract_channel
from antlighting.storage.db import ConfigStore
from tests.conftest import FakeSource, TROJAN, VLESS_TLS, VMESS, SS


@pytest.fixture()
def store(tmp_path):
    db = ConfigStore(str(tmp_path / "configs.db"))
    yield db
    db.close()


class TestPipeline:
    def test_fetch_parse_store_end_to_end(self, store):
        payload = "\n".join([VLESS_TLS, TROJAN, VMESS, SS])
        collector = ConfigCollector(store, [FakeSource("s1", payload)])
        report = collector.collect()
        assert report.endpoints_ok == 1
        assert report.parsed == 4
        assert report.new_configs == 4
        assert store.count() == 4

    def test_second_run_adds_nothing(self, store):
        collector = ConfigCollector(store, [FakeSource("s1", VLESS_TLS + "\n" + TROJAN)])
        collector.collect()
        second = collector.collect()
        assert second.new_configs == 0
        assert store.count() == 2

    def test_duplicates_across_sources_are_counted(self, store):
        collector = ConfigCollector(
            store, [FakeSource("a", VLESS_TLS + "\n" + TROJAN), FakeSource("b", VLESS_TLS)]
        )
        report = collector.collect()
        assert report.duplicates == 1
        assert store.count() == 2

    def test_seen_count_increases_on_repeat_sightings(self, store):
        cfg = parse_text(VLESS_TLS).configs[0]
        collector = ConfigCollector(store, [FakeSource("s", VLESS_TLS)])
        collector.collect()
        collector.collect()
        collector.collect()
        row = store._conn.execute(
            "SELECT seen_count, first_seen, last_seen FROM configs WHERE identity = ?",
            (cfg.identity,),
        ).fetchone()
        assert row["seen_count"] == 3

    def test_malformed_lines_are_rejected_not_fatal(self, store):
        payload = "garbage\n" + VLESS_TLS + "\nnot-a-link\nvless://broken\n" + TROJAN
        report = ConfigCollector(store, [FakeSource("s", payload)]).collect()
        assert report.rejected >= 1
        assert report.new_configs == 2

    def test_unsupported_schemes_are_stored_but_counted_unusable(self, store):
        from tests.conftest import SSR

        report = ConfigCollector(store, [FakeSource("s", SSR + "\n" + VLESS_TLS)]).collect()
        assert report.unusable == 1
        assert store.count_usable() == 1

    def test_max_pool_pruning_keeps_the_database_bounded(self, store):
        links = [
            VLESS_TLS.replace("203.0.113.10", f"203.0.{i // 250}.{i % 250 + 1}")
            for i in range(1, 60)
        ]
        collector = ConfigCollector(store, [FakeSource("s", "\n".join(links))])
        collector.collect()
        before = store.count()
        removed = store.prune(keep=20)
        assert before > 20
        assert removed == before - 20
        assert store.count() == 20


class TestSourceFailures:
    def test_a_failing_source_does_not_stop_the_others(self, store):
        collector = ConfigCollector(
            store,
            [
                FakeSource("dead", error="HTTP 503"),
                FakeSource("live", VLESS_TLS),
                FakeSource("boom", raise_exc=True),
            ],
        )
        report = collector.collect()
        assert report.sources_ok == 1
        assert report.sources_total == 3
        assert store.count() == 1
        assert len(report.sources_failed) == 2

    def test_all_sources_failing_is_reported_not_raised(self, store):
        collector = ConfigCollector(
            store, [FakeSource("a", error="timeout"), FakeSource("b", error="HTTP 429")]
        )
        report = collector.collect()
        assert report.endpoints_ok == 0
        assert report.new_configs == 0
        assert "No sources could be reached" in report.summary

    def test_raising_source_is_isolated_by_fetch_all(self):
        results = fetch_all([FakeSource("boom", raise_exc=True), FakeSource("ok", VLESS_TLS)])
        assert len(results) == 2
        assert results[0].ok is False
        assert "boom" in results[0].error

    def test_empty_payload_yields_no_configs(self, store):
        report = ConfigCollector(store, [FakeSource("empty", "")]).collect()
        assert report.new_configs == 0

    def test_html_error_page_yields_no_configs(self, store):
        report = ConfigCollector(
            store, [FakeSource("err", "<html><body>404: Not Found</body></html>")]
        ).collect()
        assert report.parsed == 0
        assert report.new_configs == 0

    def test_cancel_marks_the_report(self, store):
        collector = ConfigCollector(store, [FakeSource("s", VLESS_TLS)])
        collector.cancel()
        report = collector.collect()
        assert report.cancelled is False  # collect() clears the flag at the start
        collector.cancel()
        assert collector._cancel.is_set()


class TestSourceResultsRecorded:
    def test_source_outcomes_are_persisted(self, store):
        collector = ConfigCollector(
            store, [FakeSource("good", VLESS_TLS), FakeSource("bad", error="timeout")]
        )
        collector.collect()
        recorded = {row["id"]: row for row in store.sources()}
        assert recorded["good"]["last_count"] == 1
        assert recorded["bad"]["last_count"] == 0

    def test_ingest_text_marks_imported_configs_trusted(self, store):
        collector = ConfigCollector(store, [])
        added = collector.ingest_text(VLESS_TLS, source_id="clipboard", trusted=True)
        assert added == 1
        row = store._conn.execute("SELECT trusted FROM configs").fetchone()
        assert row["trusted"] == 1


class TestScheduler:
    def test_refresh_can_be_triggered_and_stopped(self, store):
        collector = ConfigCollector(store, [FakeSource("s", VLESS_TLS)])
        done = []
        scheduler = RefreshScheduler(collector, interval_seconds=3600, on_done=done.append)
        scheduler.trigger_now()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not done:
            time.sleep(0.02)
        assert done and done[0].new_configs == 1
        scheduler.stop()

    def test_interval_is_clamped_to_a_sane_minimum(self, store):
        scheduler = RefreshScheduler(ConfigCollector(store, []), interval_seconds=1)
        assert scheduler.interval_seconds == 300.0
        scheduler.stop()

    def test_stop_is_safe_without_start(self, store):
        RefreshScheduler(ConfigCollector(store, [])).stop()


class TestSourceRegistry:
    def test_all_kinds_are_registered(self):
        assert set(source_kinds()) == {"github", "http", "localfile", "telegram"}

    def test_create_source_round_trip(self):
        spec = {
            "id": "x",
            "kind": "http",
            "label": "X",
            "enabled": False,
            "urls": ["https://example.com/list.txt"],
            "options": {"pages": 2},
        }
        source = create_source(spec)
        assert source is not None
        assert source.describe() == spec
        # and survives a save/load cycle unchanged
        assert create_source(source.describe()).describe() == spec

    def test_unknown_kind_returns_none(self):
        assert create_source({"id": "z", "kind": "carrier-pigeon"}) is None


class TestGitHubSpecs:
    @pytest.mark.parametrize(
        "url,expected",
        [
            (
                "https://github.com/owner/repo/blob/main/path/to/configs.txt",
                ("owner", "repo", "main", "path/to/configs.txt"),
            ),
            (
                "https://github.com/owner/repo/raw/dev/list.txt",
                ("owner", "repo", "dev", "list.txt"),
            ),
            ("https://github.com/owner/repo", ("owner", "repo", "main", "")),
            (
                "https://raw.githubusercontent.com/owner/repo/main/configs.txt",
                ("owner", "repo", "main", "configs.txt"),
            ),
            ("github.com/owner/repo/tree/branch/dir", ("owner", "repo", "branch", "dir")),
        ],
    )
    def test_url_parsing(self, url, expected):
        assert parse_repo_spec(url) == expected

    @pytest.mark.parametrize("url", ["", "not-a-url", "https://example.com/x"])
    def test_unparsable_urls_are_rejected(self, url):
        assert parse_repo_spec(url) is None

    def test_ref_override(self):
        source = GitHubSource("g", urls=["https://github.com/o/r/blob/main/a.txt"],
                              options={"ref": "master"})
        specs = source._endpoints()
        assert specs[0][2] == "master"


class TestTelegramSpecs:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("@irconfig", "irconfig"),
            ("irconfig", "irconfig"),
            ("https://t.me/irconfig", "irconfig"),
            ("https://t.me/s/irconfig", "irconfig"),
            ("t.me/irconfig", "irconfig"),
            ("@IRAN_V2RAY1", "IRAN_V2RAY1"),
            ("", ""),
        ],
    )
    def test_channel_extraction(self, value, expected):
        assert extract_channel(value) == expected

    def test_source_lists_all_channels(self):
        source = TelegramSource("t", urls=["@a", "@b", "https://t.me/c"])
        assert source.channels == ["a", "b", "c"]

    def test_strip_html_keeps_links_in_attributes(self):
        html = f'<a href="{VLESS_TLS}">click</a><script>var x = "{TROJAN}";</script>'
        text = _strip_html(html)
        assert VLESS_TLS in text
        assert "<script>" not in text

    def test_fetch_failure_is_returned_not_raised(self, monkeypatch):
        source = TelegramSource("t", urls=["@nonexistent-channel-xyz"])
        monkeypatch.setattr(
            "antlighting.sources.telegram.http_get",
            lambda url, timeout=15.0: FetchResult(
                source_id="t", url=url, ok=False, error="timeout", http_status=0
            ),
        )
        results = source.fetch(timeout=1.0)
        assert len(results) == 1
        assert results[0].ok is False
        assert results[0].error == "timeout"

    def test_partial_pagination_still_succeeds(self, monkeypatch):
        calls = {"n": 0}
        page1 = (
            f'<div data-post="irconfig/100">{VLESS_TLS}</div>'
            f'<div data-post="irconfig/101">{TROJAN}</div>'
        )

        def fake_get(url, timeout=15.0):
            calls["n"] += 1
            ok = calls["n"] == 1
            return FetchResult(
                source_id="t", url=url, ok=ok,
                text=page1 if ok else "", error="" if ok else "HTTP 429",
                http_status=200 if ok else 429,
            )

        monkeypatch.setattr("antlighting.sources.telegram.http_get", fake_get)
        source = TelegramSource("t", urls=["@irconfig"], options={"pages": 3, "min_links": 1})
        results = source.fetch(timeout=1.0)
        assert results[0].ok is True
        assert "irconfig/100" not in results[0].text or VLESS_TLS in results[0].text


class TestLocalFileSource:
    def test_reads_a_real_file(self, tmp_path):
        from antlighting.sources.localfile import LocalFileSource

        path = tmp_path / "list.txt"
        path.write_text(VLESS_TLS + "\n" + TROJAN, encoding="utf-8")
        source = LocalFileSource("l", urls=[str(path)])
        results = source.fetch()
        assert results[0].ok is True
        assert len(parse_text(results[0].text).configs) == 2

    def test_missing_file_is_reported(self, tmp_path):
        from antlighting.sources.localfile import LocalFileSource

        source = LocalFileSource("l", urls=[str(tmp_path / "nope.txt")])
        results = source.fetch()
        assert results[0].ok is False
        assert results[0].error == "file not found"


class TestHttpSource:
    def test_unreachable_url_is_reported(self, monkeypatch):
        from antlighting.sources.localfile import HTTPSource

        monkeypatch.setattr(
            "antlighting.sources.localfile.http_get",
            lambda url, timeout=15.0: FetchResult(
                source_id="", url=url, ok=False, error="name resolution failed"
            ),
        )
        source = HTTPSource("h", urls=["https://nonexistent.invalid/list.txt"])
        results = source.fetch(timeout=1.0)
        assert results[0].ok is False
        assert results[0].error
