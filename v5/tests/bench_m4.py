"""M4 Performance + memory budget validation bench (AC23/AC35/AC36).

Standalone benchmark script — NOT a pytest file. Run with:

    python v5/tests/bench_m4.py

Emits a markdown report at ``v5/tests/bench_m4_report.md`` with runtime +
projected-RSS statistics.

This script exists as *infrastructure* — the default inputs are intentionally
tiny so the bench finishes in a few seconds and can be wired into CI. Users
can re-run with larger inputs (``--tokens``, ``--duration-hours``) to exercise
the 3x / 10-minute targets directly.

Targets (for visibility only — this script does NOT assert them):
  - AC23: full v5 suite <= 3x pre-M4 runtime for hourly strategies;
          1m MTF 1-month backtest <= 10 minutes.
  - AC35: memory-budget assertion trips when projected RSS > 1.2 GB.
  - AC36: demand-driven materialization activates correctly.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# --- path bootstrap (script entrypoint; mirrors the pytest fixtures) -------

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


# --- result container ------------------------------------------------------

@dataclass
class BenchResult:
    label: str
    runtime_s: float
    bar_count: int
    n_tokens: int
    n_strategies: int
    target_s: float | None
    target_label: str


# --- RSS probe (psutil optional) ------------------------------------------

def _current_rss_mb() -> float | None:
    """Return current process RSS in MB, or None when psutil is unavailable."""
    try:
        import psutil  # type: ignore[import]
    except Exception:
        return None
    try:
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        return None


# --- hourly backtest (tiny: 100 bars x 5 tokens) ---------------------------

def bench_hourly_backtest(n_bars: int = 100, n_tokens: int = 5) -> BenchResult:
    """Hourly backtest runtime: 100 bars x 5 tokens by default (AC23).

    The simulator's MTF entrypoint ``run_backtest_mtf`` fires per-bar callbacks
    across subscribed strategies. We build the smallest workload that exercises
    the triple-subscription dispatch path (1h signal / 1h entry / 1h exit).
    """
    from v5.bar_spec import BarSpec
    from v5.simulator import run_backtest_mtf
    from v5.strategy_spec import StrategySpec

    hour_ns = 3600 * 1_000_000_000
    start_ts = 1_770_000_000 * 1_000_000_000
    end_ts = start_ts + n_bars * hour_ns

    # One triple-subscription strategy + a minimal counter callback bag so
    # the simulator's dispatch loop has real work to do (not just grid emit).
    counters = {"signal": 0, "stage_3": 0, "stage_1": 0, "on_scale": 0}

    class _HourlyStrategy:
        strategy_id = "bench_hourly"
        bar_subscriptions = {
            "signal": BarSpec.from_minutes(60),
            "entry": BarSpec.from_minutes(60),
            "exit": BarSpec.from_minutes(60),
        }

        def on_signal(self, bar_ctx):
            counters["signal"] += 1

        def on_stage_3(self, bar_ctx):
            counters["stage_3"] += 1

        def on_stage_1(self, bar_ctx):
            counters["stage_1"] += 1

        def on_scale(self, bar_ctx):
            counters["on_scale"] += 1

    strategy = _HourlyStrategy()
    tokens = [f"TOK{i}" for i in range(n_tokens)]

    t0 = time.perf_counter()
    result = run_backtest_mtf(
        base_resolution=BarSpec.from_minutes(60),
        start_ts_ns=start_ts,
        end_ts_ns=end_ts,
        strategies=[strategy],
        tokens=tokens,
    )
    elapsed = time.perf_counter() - t0

    return BenchResult(
        label="hourly (1h base, 1h/1h/1h subs)",
        runtime_s=elapsed,
        bar_count=int(result.bar_count),
        n_tokens=n_tokens,
        n_strategies=1,
        target_s=None,  # AC23 target is *relative* (3x pre-M4) — no absolute.
        target_label="<= 3x pre-M4 (relative, user-measured)",
    )


# --- 1m MTF backtest (1440 bars x 3 tokens = 1 day of 1m) ------------------

def bench_mtf_1m(n_bars: int = 1440, n_tokens: int = 3) -> BenchResult:
    """1m base resolution MTF runtime: 1440 bars x 3 tokens by default.

    AC23 target: 1-month (43200 bars) under 10 minutes. Here we run 1 day and
    report the runtime so a linear projection sets expectations.
    """
    from v5.bar_spec import BarSpec
    from v5.simulator import run_backtest_mtf
    from v5.strategy_spec import StrategySpec

    min_ns = 60 * 1_000_000_000
    start_ts = 1_770_000_000 * 1_000_000_000
    end_ts = start_ts + n_bars * min_ns

    counters = {"signal": 0, "stage_3": 0, "stage_1": 0, "on_scale": 0}

    class _MTFStrategy:
        strategy_id = "bench_mtf_1m"
        # Triple subscription: hourly signal+entry, 1m exit — the canonical
        # MTF shape exercised by AC23.
        bar_subscriptions = {
            "signal": BarSpec.from_minutes(60),
            "entry": BarSpec.from_minutes(60),
            "exit": BarSpec.from_minutes(1),
        }

        def on_signal(self, bar_ctx):
            counters["signal"] += 1

        def on_stage_3(self, bar_ctx):
            counters["stage_3"] += 1

        def on_stage_1(self, bar_ctx):
            counters["stage_1"] += 1

        def on_scale(self, bar_ctx):
            counters["on_scale"] += 1

    strategy = _MTFStrategy()
    tokens = [f"TOK{i}" for i in range(n_tokens)]

    t0 = time.perf_counter()
    result = run_backtest_mtf(
        base_resolution=BarSpec.from_minutes(1),
        start_ts_ns=start_ts,
        end_ts_ns=end_ts,
        strategies=[strategy],
        tokens=tokens,
    )
    elapsed = time.perf_counter() - t0

    # Project runtime for a 1-month 1m backtest (AC23's stated target).
    projected_month_s = elapsed * (43200.0 / max(n_bars, 1))

    return BenchResult(
        label=f"1m MTF (1m base, 1h/1h/1m subs) -- projected 1mo: {projected_month_s:.1f}s",
        runtime_s=elapsed,
        bar_count=int(result.bar_count),
        n_tokens=n_tokens,
        n_strategies=1,
        target_s=600.0,  # 10 minutes = 600s target for full 1 month
        target_label="1mo 1m MTF <= 600s (10 min)",
    )


# --- memory budget + demand-driven probes ---------------------------------

@dataclass
class MemoryProbeResult:
    trips_at_over_limit: bool
    projected_over_mb: float
    ok_under_limit: bool
    projected_under_mb: float
    chunked_above_2gb: bool
    eager_below_2gb: bool


def probe_memory_budget() -> MemoryProbeResult:
    """AC35 + AC36: verify the budget assertion trips and demand-driven
    materialization routes between eager / chunked modes correctly.

    No simulator invocation — these are pure projection calls.
    """
    from v5.bar_spec import BarSpec
    from v5.data_resampler import DataResampler
    from v5.rolling_cache import assert_memory_budget, compute_projected_memory_mb
    from v5.strategy_spec import StrategySpec

    # ---- AC35: over-limit must raise MemoryError ----
    over_limit_strategies = [
        StrategySpec(
            strategy_id=f"oversub_{i}",
            bar_subscriptions={
                "signal": BarSpec.from_minutes(1),
                "entry": BarSpec.from_minutes(1),
                "exit": BarSpec.from_minutes(1),
            },
        )
        for i in range(3)
    ]
    over_tokens = [f"TOK{i}" for i in range(500)]
    projected_over = compute_projected_memory_mb(
        strategies=over_limit_strategies, tokens=over_tokens,
    )
    trips = False
    try:
        assert_memory_budget(over_limit_strategies, over_tokens)
    except MemoryError:
        trips = True

    # ---- AC35: within-limit must pass ----
    under_limit_strategies = [
        StrategySpec(
            strategy_id=f"ok_{i}",
            bar_subscriptions={
                "signal": BarSpec.from_minutes(60),
                "entry": BarSpec.from_minutes(60),
                "exit": BarSpec.from_minutes(60),
            },
        )
        for i in range(3)
    ]
    under_tokens = [f"TOK{i}" for i in range(50)]
    projected_under = compute_projected_memory_mb(
        strategies=under_limit_strategies, tokens=under_tokens,
    )
    ok_under = False
    try:
        assert_memory_budget(under_limit_strategies, under_tokens)
        ok_under = True
    except MemoryError:
        ok_under = False

    # ---- AC36: chunked above 2 GB, eager below ----
    resampler_big = DataResampler()
    resampler_big.declare_subscriptions(
        {BarSpec.from_minutes(1)},
        tokens=[f"TOK{i}" for i in range(500)],
        duration_days=365,
    )
    chunked = (resampler_big.iteration_mode() == "chunked")

    resampler_small = DataResampler()
    resampler_small.declare_subscriptions(
        {BarSpec.from_minutes(60)},
        tokens=["BTC", "ETH"],
        duration_days=30,
    )
    eager = (resampler_small.iteration_mode() == "eager")

    return MemoryProbeResult(
        trips_at_over_limit=trips,
        projected_over_mb=projected_over,
        ok_under_limit=ok_under,
        projected_under_mb=projected_under,
        chunked_above_2gb=chunked,
        eager_below_2gb=eager,
    )


# --- report writer ---------------------------------------------------------

def write_report(
    report_path: Path,
    hourly: BenchResult,
    mtf: BenchResult,
    mem: MemoryProbeResult,
    rss_before_mb: float | None,
    rss_after_mb: float | None,
) -> None:
    rss_line = (
        f"RSS before={rss_before_mb:.1f} MB, after={rss_after_mb:.1f} MB"
        if (rss_before_mb is not None and rss_after_mb is not None)
        else "RSS probe unavailable (psutil not installed)"
    )
    lines: list[str] = [
        "# M4 bench report — AC23 / AC35 / AC36",
        "",
        f"- generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
        f"- {rss_line}",
        "",
        "## AC23 — runtime",
        "",
        "| bench | bars | tokens | runtime (s) | target |",
        "|-------|-----:|-------:|------------:|--------|",
        (
            f"| {hourly.label} | {hourly.bar_count} | {hourly.n_tokens} "
            f"| {hourly.runtime_s:.4f} | {hourly.target_label} |"
        ),
        (
            f"| {mtf.label} | {mtf.bar_count} | {mtf.n_tokens} "
            f"| {mtf.runtime_s:.4f} | {mtf.target_label} |"
        ),
        "",
        "## AC35 — memory budget",
        "",
        f"- projected memory (over-limit config, 3 strats x 1m x 500 tokens): "
        f"**{mem.projected_over_mb:.1f} MB** (ceiling 1200 MB)",
        f"- assertion trips on over-limit config: "
        f"**{'YES' if mem.trips_at_over_limit else 'NO'}**",
        f"- projected memory (under-limit config, 3 strats x 1h x 50 tokens): "
        f"**{mem.projected_under_mb:.1f} MB**",
        f"- under-limit config passes: "
        f"**{'YES' if mem.ok_under_limit else 'NO'}**",
        "",
        "## AC36 — demand-driven materialization",
        "",
        f"- chunked mode activates for 1m x 500 tokens x 365d: "
        f"**{'YES' if mem.chunked_above_2gb else 'NO'}**",
        f"- eager mode for 1h x 2 tokens x 30d: "
        f"**{'YES' if mem.eager_below_2gb else 'NO'}**",
        "",
    ]
    report_path.write_text("\n".join(lines))


# --- entrypoint ------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M4 AC23/AC35/AC36 bench")
    parser.add_argument(
        "--hourly-bars", type=int, default=100,
        help="Hourly bench: number of 1h bars (default 100)",
    )
    parser.add_argument(
        "--hourly-tokens", type=int, default=5,
        help="Hourly bench: number of tokens (default 5)",
    )
    parser.add_argument(
        "--mtf-bars", type=int, default=1440,
        help="1m MTF bench: number of 1m bars (default 1440 = 1 day)",
    )
    parser.add_argument(
        "--mtf-tokens", type=int, default=3,
        help="1m MTF bench: number of tokens (default 3)",
    )
    parser.add_argument(
        "--report", type=str,
        default=str(Path(__file__).parent / "bench_m4_report.md"),
        help="Output markdown report path",
    )
    args = parser.parse_args(argv)

    rss_before = _current_rss_mb()

    print("[bench_m4] hourly backtest...", flush=True)
    hourly = bench_hourly_backtest(
        n_bars=args.hourly_bars, n_tokens=args.hourly_tokens,
    )
    print(
        f"  runtime={hourly.runtime_s:.4f}s "
        f"(bars={hourly.bar_count}, tokens={hourly.n_tokens})",
        flush=True,
    )

    print("[bench_m4] 1m MTF backtest...", flush=True)
    mtf = bench_mtf_1m(n_bars=args.mtf_bars, n_tokens=args.mtf_tokens)
    print(
        f"  runtime={mtf.runtime_s:.4f}s "
        f"(bars={mtf.bar_count}, tokens={mtf.n_tokens})",
        flush=True,
    )

    print("[bench_m4] memory budget + demand-driven probe...", flush=True)
    mem = probe_memory_budget()
    print(
        f"  AC35 over-limit projected={mem.projected_over_mb:.1f} MB "
        f"trips={mem.trips_at_over_limit}",
        flush=True,
    )
    print(
        f"  AC35 under-limit projected={mem.projected_under_mb:.1f} MB "
        f"ok={mem.ok_under_limit}",
        flush=True,
    )
    print(
        f"  AC36 chunked_above_2gb={mem.chunked_above_2gb} "
        f"eager_below_2gb={mem.eager_below_2gb}",
        flush=True,
    )

    rss_after = _current_rss_mb()

    report_path = Path(args.report)
    write_report(
        report_path=report_path,
        hourly=hourly,
        mtf=mtf,
        mem=mem,
        rss_before_mb=rss_before,
        rss_after_mb=rss_after,
    )
    print(f"[bench_m4] report written: {report_path}", flush=True)

    # Return non-zero if the AC35/AC36 invariants don't hold — these are
    # regressions the bench exists to catch even when run manually.
    invariants_ok = (
        mem.trips_at_over_limit
        and mem.ok_under_limit
        and mem.chunked_above_2gb
        and mem.eager_below_2gb
    )
    return 0 if invariants_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
