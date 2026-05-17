"""Host-process stats sampler — CPU%, RSS, ingest bitrate.

Surfaces the periodic `HostStatsEvent` consumed by the WS layer for
the dashboard's always-visible header chips + hero ingest/egress pair.

Three measurement layers, kept separate so each can be tested + mocked
independently:

1. **`/proc/<pid>/stat` reader.** Sync, ~µs, no external dep — we don't
   add `psutil` for this. Returns `(utime_ticks, stime_ticks)` and the
   process's RSS in pages. Two consecutive ticks bracketed by a
   wall-clock delta yield CPU%.

2. **CPU% accounting state.** Keeps the last (utime+stime, wall_ns) per
   pid so the next sample computes `dCPU / dWall / SC_CLK_TCK`. The
   first sample for a pid returns 0.0% (no baseline to subtract). This
   matches the operator-honest behavior: a freshly-spawned ffmpeg
   reads as 0% for the first tick, then real values.

3. **Nginx-rtmp `rtmp_stat` parser.** GETs the XML at the loopback
   stat endpoint, extracts `<bw_in>` (bytes/sec) for the `live` app,
   converts to kbits/s. None if HTTP fails or no publisher is active.

The sampler is sync inside (`/proc` parsing is synchronous OS calls)
but exposes an async `sample()` for the supervisor loop's convenience
(the nginx fetch IS async via httpx). The supervisor calls `sample()`
once per tick from its TaskGroup; never compute on the hot path.

`os.cpu_count()` is snapshotted at construction — under cgroup CPU
limits in a container, `cpu_count()` is the LOGICAL count, not the
quota. The operator wants "is Restream_Plus eating my host," so we
divide the sum across all live processes by the logical count. If we
ever need per-cgroup accounting, that's a future change behind the
same `HostStatsEvent` contract.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Protocol

import defusedxml.ElementTree as ET  # noqa: N817 — stdlib idiom matches xml.etree
import httpx
import structlog

from app.fanout.worker import WorkerId

_logger = structlog.get_logger(__name__)

_INGEST_FETCH_TIMEOUT: Final[float] = 1.0
"""Loopback HTTP fetch should be sub-millisecond. 1.0s is a generous
ceiling that still keeps the sampler tick responsive under unexpected
nginx wedge."""


class ProcStatReader(Protocol):
    """Reads `(utime_ticks, stime_ticks, rss_bytes)` for a pid.

    The production implementation parses `/proc/<pid>/stat` directly.
    Tests inject a deterministic fake that returns a scripted
    sequence so CPU% math is verifiable without a real process tree.

    Returns `None` if the pid is gone (race against process exit). The
    sampler treats this as "no contribution this tick" — the next tick
    re-scans the supervisor's live pid set.
    """

    def read(self, pid: int) -> tuple[int, int, int] | None: ...


class IngestStatFetcher(Protocol):
    """Returns ingest-side kbits/s, or None when unavailable.

    Production: GETs nginx-rtmp's `rtmp_stat` XML endpoint and parses
    `<bw_in>` from the first live publisher. Tests inject a
    constant-value fake.
    """

    async def fetch(self) -> float | None: ...


class WorkerPidProvider(Protocol):
    """Enumerates `(worker_id, pid)` for currently-spawned ffmpeg workers.

    The supervisor implements this by walking its `_workers` dict and
    reading each `worker.pid`. A dedicated Protocol keeps the sampler
    independent of the supervisor's internal layout and gives tests a
    natural seam.
    """

    def worker_pids(self) -> tuple[tuple[WorkerId, int], ...]: ...


@dataclass(frozen=True, slots=True)
class HostStatsSample:
    """One sample's worth of measurements. Maps 1:1 to `HostStatsEvent`
    fields the supervisor wraps for the bus.
    """

    cpu_total_pct: float
    cpu_by_target: tuple[tuple[WorkerId, float], ...]
    rss_bytes: int
    ingest_kbps: float | None


class HostStatsSampler:
    """Stateful CPU% accountant + ingest fetcher.

    Construct once per supervisor lifetime, call `sample()` from the
    supervisor's TaskGroup loop. Internal state: a map of
    `pid -> (last_total_ticks, last_wall_ns)` used to compute deltas.
    Pids that disappear are pruned implicitly by the next-tick rescan.
    """

    __slots__ = (
        "_clk_tck",
        "_ingest",
        "_last_samples",
        "_logical_cpus",
        "_monotonic_ns",
        "_pid_provider",
        "_proc",
        "_self_pid",
    )

    def __init__(
        self,
        *,
        proc_reader: ProcStatReader,
        ingest_fetcher: IngestStatFetcher,
        pid_provider: WorkerPidProvider,
        self_pid: int | None = None,
        clk_tck: int | None = None,
        logical_cpus: int | None = None,
        monotonic_clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self._proc = proc_reader
        self._ingest = ingest_fetcher
        self._pid_provider = pid_provider
        self._self_pid = self_pid if self_pid is not None else os.getpid()
        # SC_CLK_TCK is the kernel's clock ticks per second (100 on
        # virtually every Linux). `_sysconf_or` falls back to that
        # default when `os.sysconf` is missing (Windows dev hosts).
        self._clk_tck = clk_tck if clk_tck is not None else _sysconf_or("SC_CLK_TCK", 100)
        if logical_cpus is not None:
            self._logical_cpus = logical_cpus
        else:
            self._logical_cpus = max(os.cpu_count() or 1, 1)
        self._monotonic_ns = monotonic_clock_ns
        # `pid -> (last_total_ticks, last_wall_ns)`. First-tick reads
        # return 0.0% and seed the entry; subsequent reads compute the
        # delta.
        self._last_samples: dict[int, tuple[int, int]] = {}

    async def sample(self) -> HostStatsSample:
        now_ns = self._monotonic_ns()

        # Snapshot the worker pid list ONCE per tick so the prune step
        # below sees the same set the per-target loop iterated over —
        # otherwise a worker that exits mid-tick produces 0% AND looks
        # like a stale pid to prune, which is fine but wastes a lookup.
        worker_pids = self._pid_provider.worker_pids()

        # Walk worker pids → per-target CPU.
        per_target: list[tuple[WorkerId, float]] = []
        worker_total_pct = 0.0
        for worker_id, pid in worker_pids:
            pct = self._read_and_account(pid=pid, now_ns=now_ns)
            per_target.append((worker_id, pct))
            worker_total_pct += pct

        # Control-plane process CPU + RSS.
        self_reading = self._proc.read(self._self_pid)
        self_pct = 0.0
        rss_bytes = 0
        if self_reading is not None:
            self_pct = self._account_pct(
                pid=self._self_pid,
                utime=self_reading[0],
                stime=self_reading[1],
                now_ns=now_ns,
            )
            rss_bytes = self_reading[2]

        cpu_total_pct = worker_total_pct + self_pct

        # Prune accounting state for pids that disappeared from the
        # provider AND aren't `self_pid`. Otherwise dead pids
        # accumulate forever on long runs with frequent reconnects.
        live_pids = {pid for _, pid in worker_pids}
        live_pids.add(self._self_pid)
        for stale in [p for p in self._last_samples if p not in live_pids]:
            del self._last_samples[stale]

        ingest_kbps: float | None
        try:
            ingest_kbps = await self._ingest.fetch()
        except Exception:
            # Don't crash the loop on a transient fetch error; the
            # next tick retries. `None` signals "unavailable" to the
            # UI which renders "—" instead of "0 Mbps".
            _logger.warning("host_stats_ingest_fetch_failed", exc_info=True)
            ingest_kbps = None

        return HostStatsSample(
            cpu_total_pct=cpu_total_pct,
            cpu_by_target=tuple(per_target),
            rss_bytes=rss_bytes,
            ingest_kbps=ingest_kbps,
        )

    def _read_and_account(self, *, pid: int, now_ns: int) -> float:
        reading = self._proc.read(pid)
        if reading is None:
            # Process is gone between provider enumeration and read.
            # Drop the accounting entry so a recycled pid (kernel pid
            # reuse) doesn't compute negative deltas next tick.
            self._last_samples.pop(pid, None)
            return 0.0
        utime, stime, _rss = reading
        return self._account_pct(pid=pid, utime=utime, stime=stime, now_ns=now_ns)

    def _account_pct(self, *, pid: int, utime: int, stime: int, now_ns: int) -> float:
        total = utime + stime
        prev = self._last_samples.get(pid)
        self._last_samples[pid] = (total, now_ns)
        if prev is None:
            # First reading for this pid — no baseline to subtract.
            return 0.0
        prev_total, prev_ns = prev
        d_ticks = total - prev_total
        d_wall_s = (now_ns - prev_ns) / 1_000_000_000
        if d_ticks < 0 or d_wall_s <= 0:
            # pid reuse where the new pid's accumulated ticks are LOWER
            # than the dead pid's baseline, or a clock anomaly. Reset
            # baseline and report 0 this tick.
            return 0.0
        cpu_seconds = d_ticks / self._clk_tck
        # 0-100% of one host: cpu_seconds / wall_seconds * (100 / cpus).
        pct = (cpu_seconds / d_wall_s) * (100.0 / self._logical_cpus)
        # Defense against pid-reuse where the new pid happens to have
        # HIGHER ticks than the dead pid's baseline — produces a
        # large-but-positive delta that the `< 0` guard above misses.
        # Physical ceiling: no process can consume more than 100% * N
        # cores. Anything above that is a bogon; drop the baseline so
        # the next tick re-seeds cleanly.
        max_sane_pct = 100.0 * self._logical_cpus
        if pct > max_sane_pct:
            del self._last_samples[pid]
            return 0.0
        return pct


class ProcfsStatReader:
    """Production `ProcStatReader` — parses `/proc/<pid>/stat`.

    Field offsets (per `man 5 proc`):
      14 — utime (ticks)
      15 — stime (ticks)
      24 — rss (pages)

    Fields are space-separated EXCEPT field 2 (comm) which is
    parenthesized and may itself contain spaces. We split around the
    final `) ` to avoid the comm-parse landmine.
    """

    __slots__ = ("_page_size",)

    def __init__(self, *, page_size: int | None = None) -> None:
        self._page_size = page_size if page_size is not None else _sysconf_or("SC_PAGESIZE", 4096)

    def read(self, pid: int) -> tuple[int, int, int] | None:
        try:
            with open(f"/proc/{pid}/stat", "rb") as fh:
                raw = fh.read()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            return None
        try:
            # Split on the LAST `) ` to skip past the parenthesised comm.
            tail_start = raw.rindex(b") ") + 2
            fields = raw[tail_start:].split()
            # After comm we have field 3 onwards as `fields[0]`-indexed:
            # `state` = fields[0] (field 3), so utime (field 14) is
            # `fields[11]`, stime (field 15) is `fields[12]`, rss
            # (field 24) is `fields[21]`.
            utime = int(fields[11])
            stime = int(fields[12])
            rss_pages = int(fields[21])
        except (ValueError, IndexError):
            return None
        return utime, stime, rss_pages * self._page_size


class NginxRtmpStatFetcher:
    """Production `IngestStatFetcher` — polls nginx-rtmp `rtmp_stat`.

    Loopback-only by nginx config (`allow 127.0.0.1; deny all;`).
    Reads `<application name="live"><live><stream><bw_in>` and converts
    bytes-per-second to kbits/s. Returns the sum across all live
    publishers (in practice there's at most one — `max_streams 1`).

    Returns `None` on any error (network, parse, no `<bw_in>`) so the
    sampler can surface "—" rather than misleading zeros.
    """

    __slots__ = ("_client", "_url")

    def __init__(self, *, url: str, client: httpx.AsyncClient) -> None:
        self._url = url
        self._client = client

    async def fetch(self) -> float | None:
        try:
            resp = await self._client.get(self._url, timeout=_INGEST_FETCH_TIMEOUT)
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        try:
            # Defense-in-depth: defusedxml rejects DTDs / external
            # entities / entity expansion. The source is loopback
            # nginx-rtmp today (allow 127.0.0.1; deny all;), but a
            # future config change or nginx-rtmp CVE that adds entity
            # expansion shouldn't reach this parser silently.
            root = ET.fromstring(resp.content)
        except ET.ParseError:
            return None
        # Find <application name="live"> → <live> → <stream>... → <bw_in>.
        # `bw_in` in nginx-rtmp-module is bits/second (NOT bytes/sec —
        # the field name is a stat-module convention, not a unit hint).
        total_bits_per_sec = 0
        found_any = False
        for app in root.iter("application"):
            name_el = app.find("name")
            if name_el is None or (name_el.text or "").strip() != "live":
                continue
            live_el = app.find("live")
            if live_el is None:
                continue
            for stream in live_el.iter("stream"):
                bw_in = stream.find("bw_in")
                if bw_in is None or bw_in.text is None:
                    continue
                try:
                    total_bits_per_sec += int(bw_in.text.strip())
                    found_any = True
                except ValueError:
                    continue
        if not found_any:
            return None
        return total_bits_per_sec / 1000.0


def _sysconf_or(name: str, default: int) -> int:
    """Read `os.sysconf(name)` or fall back to `default`.

    `os.sysconf` is POSIX-only — absent on Windows, where dev hosts run.
    Any value/OS error from the lookup also falls back. Used for both
    `SC_CLK_TCK` (CPU% math) and `SC_PAGESIZE` (RSS bytes).
    """
    sysconf = getattr(os, "sysconf", None)
    if sysconf is None:
        return default
    try:
        return int(sysconf(name))
    except (ValueError, OSError):
        return default


HOST_STATS_TICK_SECONDS: Final[float] = 2.0
"""The supervisor's `_host_stats_loop` tick.

Two seconds: fast enough to reflect a CPU spike before the operator
has time to read the chip; slow enough that the dCPU / dWall
division produces a stable number even under bursty load."""
