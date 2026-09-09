"""Server testing: timeouts, failures, successes, cancellation and ranking."""

from __future__ import annotations

import threading
import time

import pytest

from antlighting.testing.tester import ServerTester, TcpProbe, TestRunSummary
from tests.conftest import FakeProbe, FakeStore, free_port, sample_configs


@pytest.fixture()
def store():
    return FakeStore()


class TestOutcomes:
    def test_working_servers_are_recorded_with_latency(self, store):
        configs = sample_configs()[:3]
        probe = FakeProbe(default=("working", 88.0, 0.01))
        tester = ServerTester(probe, store=store, concurrency=4, timeout=3.0)
        results = tester.test_many(configs)
        assert len(results) == 3
        assert all(r.status == "working" for r in results)
        assert all(r.latency_ms == 88.0 for r in results)
        for cfg in configs:
            assert store.get_metrics(cfg.identity)["status"] == "working"
            assert store.get_metrics(cfg.identity)["latency_ms"] == 88.0

    def test_failed_servers_increment_the_failure_counters(self, store):
        configs = sample_configs()[:2]
        probe = FakeProbe(default=("failed", None, 0.01))
        tester = ServerTester(probe, store=store, concurrency=2, timeout=3.0)
        tester.test_many(configs)
        for cfg in configs:
            metrics = store.get_metrics(cfg.identity)
            assert metrics["status"] == "failed"
            assert metrics["failure_count"] == 1
            assert metrics["consecutive_failures"] == 1

    def test_repeated_success_clears_the_failure_streak(self, store):
        cfg = sample_configs()[0]
        store.record_result(cfg.identity, "failed", error="boom")
        store.record_result(cfg.identity, "failed", error="boom")
        assert store.get_metrics(cfg.identity)["consecutive_failures"] == 2
        store.record_result(cfg.identity, "working", latency_ms=50.0)
        assert store.get_metrics(cfg.identity)["consecutive_failures"] == 0

    def test_unsupported_configs_are_marked_invalid_without_a_probe(self, store):
        from antlighting.configs.parser import parse_link
        from tests.conftest import SSR

        ssr = parse_link(SSR)
        assert ssr is not None and ssr.usable is False
        probe = FakeProbe()
        tester = ServerTester(probe, store=store, timeout=2.0)
        results = tester.test_many([ssr])
        assert results[0].status == "invalid"
        assert probe.calls == []  # never touched the network

    def test_summary_counts_every_bucket(self):
        from antlighting.testing.tester import TestResult

        results = [
            TestResult("a", "working", 50.0),
            TestResult("b", "slow", 1500.0),
            TestResult("c", "timeout"),
            TestResult("d", "failed"),
            TestResult("e", "invalid"),
        ]
        summary = TestRunSummary.from_results(results, duration=1.0)
        assert (summary.total, summary.working, summary.slow) == (5, 1, 1)
        assert (summary.timeout, summary.failed, summary.invalid) == (1, 1, 1)


class TestTimeouts:
    def test_a_hung_probe_cannot_stall_the_run(self, store):
        configs = sample_configs()[:3]
        # One server sleeps far longer than the timeout.
        outcomes = {configs[0].identity: ("working", 10.0, 30.0)}
        probe = FakeProbe(
            outcomes=outcomes, default=("working", 90.0, 0.01), ignore_timeout=True
        )
        tester = ServerTester(probe, store=store, concurrency=3, timeout=0.5)
        started = time.monotonic()
        results = tester.test_many(configs)
        elapsed = time.monotonic() - started
        # The hard watchdog must cut the hung probe off well before its 30 s sleep.
        assert elapsed < 10.0
        hung = next(r for r in results if r.identity == configs[0].identity)
        assert hung.status == "timeout"
        assert sum(1 for r in results if r.status == "working") == 2

    def test_progress_is_reported_for_every_server(self, store):
        configs = sample_configs()[:4]
        seen: list[tuple[int, int]] = []
        tester = ServerTester(
            FakeProbe(default=("working", 50.0, 0.01)), store=store, concurrency=2, timeout=3.0
        )
        tester.test_many(
            configs, on_progress=lambda _r, done, total: seen.append((done, total))
        )
        assert len(seen) == 4
        assert seen[-1] == (4, 4)


class TestCancellation:
    def test_cancel_stops_the_run_early(self, store):
        configs = sample_configs()
        probe = FakeProbe(default=("working", 50.0, 0.2))
        tester = ServerTester(probe, store=store, concurrency=1, timeout=5.0)
        finished = threading.Event()

        def run():
            tester.test_many(configs)
            finished.set()

        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(0.35)
        tester.cancel()
        assert finished.wait(timeout=10.0)
        assert tester.cancelled is True
        # Far fewer servers were probed than were supplied.
        assert len(probe.calls) < len(configs)

    def test_cancelled_tester_can_be_reused(self, store):
        tester = ServerTester(FakeProbe(default=("working", 50.0, 0.01)), store=store, timeout=3.0)
        tester.cancel()
        tester.reset()
        results = tester.test_many(sample_configs()[:2])
        assert len(results) == 2
        assert all(r.status == "working" for r in results)

    def test_progress_reports_the_cancelled_flag(self, store):
        tester = ServerTester(FakeProbe(), store=store, timeout=3.0)
        tester.test_many(sample_configs()[:1])
        tester.cancel()
        assert tester.progress()["cancelled"] is True


class TestRankingIntegration:
    def test_ranking_uses_stored_metrics(self, store):
        configs = sample_configs()[:3]
        store.record_result(configs[0].identity, "failed", error="nope")
        store.record_result(configs[1].identity, "working", latency_ms=400.0)
        store.record_result(configs[2].identity, "working", latency_ms=90.0)
        tester = ServerTester(FakeProbe(), store=store, timeout=3.0)
        ranked = tester.rank(configs)
        assert ranked[0].identity == configs[2].identity
        assert ranked[-1].identity == configs[0].identity

    def test_select_best_skips_already_tried_servers(self, store):
        configs = sample_configs()[:3]
        for cfg in configs:
            store.record_result(cfg.identity, "working", latency_ms=100.0)
        tester = ServerTester(FakeProbe(), store=store, timeout=3.0)
        first = tester.select_best(configs)
        second = tester.select_best(configs, skip=[first.identity])
        assert first.identity != second.identity

    def test_select_best_refuses_a_pool_where_everything_failed(self, store):
        configs = sample_configs()[:3]
        for cfg in configs:
            store.record_result(cfg.identity, "failed", error="nope")
        tester = ServerTester(FakeProbe(), store=store, timeout=3.0)
        assert tester.select_best(configs) is None


class TestTcpProbe:
    def test_open_port_reports_working(self):
        import socket

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(4)
            port = listener.getsockname()[1]
            cfg = sample_configs()[0]
            cfg.address, cfg.port = "127.0.0.1", port
            result = TcpProbe().test(cfg, timeout=2.0)
        assert result.status == "working"
        assert result.latency_ms is not None

    def test_closed_port_reports_failed(self):
        port = free_port()
        cfg = sample_configs()[0]
        cfg.address, cfg.port = "127.0.0.1", port
        result = TcpProbe().test(cfg, timeout=2.0)
        assert result.status == "failed"

    def test_unresolvable_host_reports_failed(self):
        cfg = sample_configs()[0]
        cfg.address, cfg.port = "no-such-host.invalid", 443
        result = TcpProbe().test(cfg, timeout=3.0)
        assert result.status == "failed"
        assert result.latency_ms is None

    def test_probe_that_ignores_its_timeout_is_cut_off(self, store):
        """The watchdog exists for probes wedged in a blocking socket read."""
        cfg = sample_configs()[0]
        probe = FakeProbe(
            outcomes={cfg.identity: ("working", 10.0, 30.0)},
            default=("working", 50.0, 0.01),
            ignore_timeout=True,
        )
        tester = ServerTester(probe, store=store, concurrency=1, timeout=0.4)
        started = time.monotonic()
        results = tester.test_many([cfg])
        assert time.monotonic() - started < 15.0
        assert results[0].status == "timeout"
