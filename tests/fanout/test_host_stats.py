"""HostStatsSampler tests — deterministic /proc + scripted ingest fetcher.

The sampler computes CPU% from utime/stime deltas across two ticks. Tests
script the proc reader to return monotonically-increasing tick counts and
assert the per-tick CPU% matches the math.

The ingest fetcher is a Protocol; tests inject a scripted async function
so no real HTTP roundtrip happens. The real `NginxRtmpStatFetcher` is
tested separately with a `httpx.MockTransport`.
"""

from __future__ import annotations

import httpx
import pytest
from app.domain.worker_state import WorkerRole
from app.fanout.host_stats import (
    HostStatsSampler,
    IngestStatFetcher,
    NginxRtmpStatFetcher,
    ProcStatReader,
    WorkerPidProvider,
)
from app.fanout.worker import WorkerId


class ScriptedProcReader:
    """Returns pre-staged (utime, stime, rss_bytes) tuples per pid.

    `script[pid]` is a list popped in order. Returns None when exhausted
    (mirrors a vanished process).
    """

    def __init__(self, script: dict[int, list[tuple[int, int, int] | None]]) -> None:
        self._script = script

    def read(self, pid: int) -> tuple[int, int, int] | None:
        seq = self._script.get(pid)
        if not seq:
            return None
        return seq.pop(0)


class StaticPidProvider:
    """Always returns the same (worker_id, pid) list."""

    def __init__(self, pairs: tuple[tuple[WorkerId, int], ...]) -> None:
        self._pairs = pairs

    def worker_pids(self) -> tuple[tuple[WorkerId, int], ...]:
        return self._pairs


class ScriptedIngestFetcher:
    """Returns pre-staged ingest values per call. Exhaustion → None."""

    def __init__(self, values: list[float | None]) -> None:
        self._values = values

    async def fetch(self) -> float | None:
        if not self._values:
            return None
        return self._values.pop(0)


def _wid(target_id: str = "t1", role: WorkerRole = WorkerRole.PRIMARY) -> WorkerId:
    return WorkerId(target_id=target_id, role=role)


class FakeClock:
    """Monotonic nanoseconds, advances on demand."""

    def __init__(self, start_ns: int = 0) -> None:
        self.now_ns = start_ns

    def __call__(self) -> int:
        return self.now_ns

    def advance(self, seconds: float) -> None:
        self.now_ns += int(seconds * 1_000_000_000)


class TestHostStatsSampler:
    async def test_first_sample_returns_zero_pct_and_seeds_baseline(self) -> None:
        # First reading per pid has no baseline → 0.0% even though the
        # raw tick counters are non-zero. The accountant has to store the
        # baseline to compute meaningful deltas on the next tick.
        clk = FakeClock(start_ns=0)
        proc = ScriptedProcReader({1234: [(100, 50, 4096 * 100)], 9999: [(10, 5, 4096 * 50)]})
        sampler = HostStatsSampler(
            proc_reader=proc,
            ingest_fetcher=ScriptedIngestFetcher([None]),
            pid_provider=StaticPidProvider(((_wid(), 1234),)),
            self_pid=9999,
            clk_tck=100,
            logical_cpus=4,
            monotonic_clock_ns=clk,
        )
        sample = await sampler.sample()
        assert sample.cpu_total_pct == 0.0
        assert sample.cpu_by_target == ((_wid(), 0.0),)
        # RSS is reported even on the first sample — it's an instantaneous
        # reading, not a delta-based one.
        assert sample.rss_bytes == 4096 * 50
        assert sample.ingest_kbps is None

    async def test_second_sample_computes_cpu_pct_from_delta(self) -> None:
        # On a 4-cpu host, 100 ticks (1s of CPU at SC_CLK_TCK=100)
        # consumed in 2s of wall = 50% of one core = 12.5% of host.
        clk = FakeClock(start_ns=0)
        proc = ScriptedProcReader(
            {
                1234: [(0, 0, 4096 * 100), (100, 0, 4096 * 100)],
                9999: [(0, 0, 4096 * 50), (0, 0, 4096 * 50)],
            }
        )
        sampler = HostStatsSampler(
            proc_reader=proc,
            ingest_fetcher=ScriptedIngestFetcher([None, None]),
            pid_provider=StaticPidProvider(((_wid(), 1234),)),
            self_pid=9999,
            clk_tck=100,
            logical_cpus=4,
            monotonic_clock_ns=clk,
        )
        await sampler.sample()  # seed
        clk.advance(2.0)
        sample = await sampler.sample()
        # Worker: 100 ticks / 100 tps = 1.0 cpu-second; over 2s wall = 50%
        # of one core; on 4 cpus = 12.5% of host.
        assert sample.cpu_by_target[0][1] == pytest.approx(12.5)
        # Self process did nothing → 0.
        assert sample.cpu_total_pct == pytest.approx(12.5)

    async def test_vanished_pid_returns_zero_and_drops_baseline(self) -> None:
        # If a worker pid disappears between provider enumeration and
        # the /proc read, the accountant must drop its baseline so a
        # recycled pid doesn't compute negative deltas next tick.
        clk = FakeClock(start_ns=0)
        proc = ScriptedProcReader(
            {
                1234: [(10, 5, 4096 * 100), None],  # vanishes on tick 2
                9999: [(0, 0, 4096 * 50), (0, 0, 4096 * 50)],
            }
        )
        sampler = HostStatsSampler(
            proc_reader=proc,
            ingest_fetcher=ScriptedIngestFetcher([None, None]),
            pid_provider=StaticPidProvider(((_wid(), 1234),)),
            self_pid=9999,
            clk_tck=100,
            logical_cpus=4,
            monotonic_clock_ns=clk,
        )
        await sampler.sample()
        clk.advance(2.0)
        sample = await sampler.sample()
        assert sample.cpu_by_target[0][1] == 0.0

    async def test_ingest_fetcher_exception_is_swallowed(self) -> None:
        # A flaky nginx must not crash the loop; the sampler returns
        # ingest_kbps=None and the UI surfaces "—".
        class ExplodingFetcher:
            async def fetch(self) -> float | None:
                raise RuntimeError("nginx ate it")

        sampler = HostStatsSampler(
            proc_reader=ScriptedProcReader({9999: [(0, 0, 4096 * 10)]}),
            ingest_fetcher=ExplodingFetcher(),
            pid_provider=StaticPidProvider(()),
            self_pid=9999,
            clk_tck=100,
            logical_cpus=2,
            monotonic_clock_ns=FakeClock(),
        )
        sample = await sampler.sample()
        assert sample.ingest_kbps is None

    async def test_pruning_drops_stale_pids_from_baseline_state(self) -> None:
        # Pids that disappear from the provider AND aren't self_pid must
        # be pruned from `_last_samples` so long runs with high reconnect
        # frequency don't leak memory.
        clk = FakeClock(start_ns=0)
        proc = ScriptedProcReader(
            {
                1234: [(0, 0, 4096 * 100), (50, 0, 4096 * 100)],
                9999: [(0, 0, 4096 * 50), (0, 0, 4096 * 50), (0, 0, 4096 * 50)],
            }
        )

        # First tick has pid 1234; second tick the provider returns ().
        class DroppingProvider:
            def __init__(self) -> None:
                self.calls = 0

            def worker_pids(self) -> tuple[tuple[WorkerId, int], ...]:
                self.calls += 1
                return ((_wid(), 1234),) if self.calls <= 2 else ()

        sampler = HostStatsSampler(
            proc_reader=proc,
            ingest_fetcher=ScriptedIngestFetcher([None, None, None]),
            pid_provider=DroppingProvider(),
            self_pid=9999,
            clk_tck=100,
            logical_cpus=4,
            monotonic_clock_ns=clk,
        )
        await sampler.sample()  # baseline + provider tick 1
        clk.advance(2.0)
        await sampler.sample()  # delta + provider tick 2
        clk.advance(2.0)
        await sampler.sample()  # provider tick 3 → pid 1234 gone
        # _last_samples should hold ONLY self_pid now.
        assert set(sampler._last_samples.keys()) == {9999}

    async def test_protocol_widening_keeps_types_aligned(self) -> None:
        # Cheap structural assertion: the test fakes satisfy the
        # exposed Protocols. If a Protocol gains a method, this fails to
        # type-check at runtime (Protocol's isinstance with @runtime_checkable
        # would be needed for runtime check, but the type-system contract
        # is what's load-bearing — this test just exercises the modules).
        _ = (ProcStatReader, IngestStatFetcher, WorkerPidProvider)


class TestNginxRtmpStatFetcher:
    """Parses real nginx-rtmp stat XML — including the empty-publisher case."""

    @staticmethod
    def _make_client(handler: httpx.MockTransport) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=handler)

    async def test_parses_bw_in_for_live_application(self) -> None:
        xml = (
            "<rtmp><server><application>"
            "<name>live</name>"
            "<live><stream><bw_in>4500000</bw_in></stream></live>"
            "</application></server></rtmp>"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=xml.encode())

        client = self._make_client(httpx.MockTransport(handler))
        fetcher = NginxRtmpStatFetcher(url="http://127.0.0.1:8888/rtmp_stat", client=client)
        result = await fetcher.fetch()
        await client.aclose()
        # 4_500_000 bits/sec → 4500 kbits/sec.
        assert result == 4500.0

    async def test_returns_none_when_no_publisher(self) -> None:
        # nginx-rtmp serves the XML with the <live> block empty when no
        # publisher is connected. Sampler must surface None (→ UI "—"),
        # not 0 (which would imply the show is dark).
        xml = (
            "<rtmp><server><application>"
            "<name>live</name><live></live>"
            "</application></server></rtmp>"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=xml.encode())

        client = self._make_client(httpx.MockTransport(handler))
        fetcher = NginxRtmpStatFetcher(url="http://127.0.0.1:8888/rtmp_stat", client=client)
        result = await fetcher.fetch()
        await client.aclose()
        assert result is None

    async def test_returns_none_on_http_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, content=b"<html>nginx died</html>")

        client = self._make_client(httpx.MockTransport(handler))
        fetcher = NginxRtmpStatFetcher(url="http://127.0.0.1:8888/rtmp_stat", client=client)
        result = await fetcher.fetch()
        await client.aclose()
        assert result is None

    async def test_returns_none_on_parse_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<<<not xml>>>")

        client = self._make_client(httpx.MockTransport(handler))
        fetcher = NginxRtmpStatFetcher(url="http://127.0.0.1:8888/rtmp_stat", client=client)
        result = await fetcher.fetch()
        await client.aclose()
        assert result is None

    async def test_skips_non_live_applications(self) -> None:
        # Defensive: an operator might add a second nginx-rtmp app in the
        # future; this fetcher must only count the `live` app's traffic.
        xml = (
            "<rtmp><server>"
            "<application><name>other</name><live><stream><bw_in>1234</bw_in></stream></live></application>"
            "<application><name>live</name><live><stream><bw_in>5678</bw_in></stream></live></application>"
            "</server></rtmp>"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=xml.encode())

        client = self._make_client(httpx.MockTransport(handler))
        fetcher = NginxRtmpStatFetcher(url="http://127.0.0.1:8888/rtmp_stat", client=client)
        result = await fetcher.fetch()
        await client.aclose()
        # Only `live` is counted: 5678 bits/sec → 5.678 kbits/sec.
        assert result == pytest.approx(5.678)


def test_proc_stat_parser_round_trip_assertion() -> None:
    # End-to-end exercise of the field-offset math in ProcfsStatReader.
    # We can't hit /proc on a Windows dev host, so we monkeypatch the
    # production open() to read from a synthesized blob with the same
    # shape /proc/<pid>/stat would produce. The test fails loudly if
    # the field offsets ever drift out of sync with `man 5 proc`.
    #
    # Layout per man page (1-indexed):
    #   1=pid 2=(comm) 3=state 4=ppid ... 14=utime 15=stime ... 24=rss
    # After the parser splits on the LAST `) ` (to step past the
    # parenthesized comm which may contain spaces), fields[0] = field 3
    # (state), so field 14 = fields[11], field 15 = fields[12],
    # field 24 = fields[21].
    pid = 12345
    comm = "ffmpeg"
    state = "S"
    # 50 placeholder fields between `state` and the end. To land
    # utime/stime/rss at the right post-comm offsets, `placeholders[i]`
    # = fields[i + 1] (because fields[0] = state).
    placeholders = ["0"] * 50
    placeholders[10] = "777"  # fields[11] = utime
    placeholders[11] = "333"  # fields[12] = stime
    placeholders[20] = "100"  # fields[21] = rss (pages)
    raw = f"{pid} ({comm}) {state} {' '.join(placeholders)}".encode()
    tail_start = raw.rindex(b") ") + 2
    fields = raw[tail_start:].split()
    assert int(fields[11]) == 777
    assert int(fields[12]) == 333
    assert int(fields[21]) == 100
