#!/usr/bin/env python3
"""
Final audit: Simulate the v4 signals path (no common-range alignment)
to reproduce the impossible basis values.

Key finding: v3/engine.py:backtest_token_combined() aligns to common_start/common_end
BEFORE building contexts. v4/signals.py:precompute_strategy_signals() does NOT —
it loads spot and perp independently, building contexts with different time ranges.
When the strategy does `n = min(len(spot), len(perp))` and slices `[:n]`,
it compares bars from DIFFERENT dates if one series starts earlier.
"""

import pandas as pd
import numpy as np
from pathlib import Path
import warnings
warnings.filterwarnings("ignore")

SPOT_DIR = Path("/workspace/crypto_backtest/data/spot/1h_cache")
PERP_DIR = Path("/workspace/crypto_backtest/data/perp/1h_cache")

FLAGGED = ["INJ", "FET", "AGLD", "ICP"]
CONTROLS = ["BTC", "ETH", "SOL"]
ALL_TOKENS = FLAGGED + CONTROLS


def load_pair(token):
    spot = pd.read_parquet(SPOT_DIR / f"{token}_1h.parquet")
    perp = pd.read_parquet(PERP_DIR / f"{token}_1h.parquet")
    if spot.index.name != "datetime":
        spot.index.name = "datetime"
    if perp.index.name != "datetime":
        perp.index.name = "datetime"
    return spot, perp


def simulate_misaligned_basis(token):
    """
    Simulate what happens when spot and perp are NOT aligned by timestamp
    before computing basis (the v4 bug path).

    The strategy does:
        n = min(len(spot_close), len(perp_close))
        spot_close = ctx_spot.ind_1h['close'][:n]
        perp_close = ctx_perp.ind_1h['close'][:n]
        basis = (perp_close - spot_close) / spot_close

    If spot has 47067 bars starting 2020-10-21 and perp has 31124 bars starting
    2022-08-17, then with n=31124:
        spot_close[:31124] = spot bars from 2020-10-21 to ~2024-03
        perp_close[:31124] = perp bars from 2022-08-17 to 2026-03

    This compares spot prices from 2020 with perp prices from 2022 = huge mismatch.
    """
    spot, perp = load_pair(token)

    n = min(len(spot), len(perp))

    # Misaligned (v4 bug path)
    spot_close_mis = spot["close"].values[:n]
    perp_close_mis = perp["close"].values[:n]
    spot_dates_mis = spot.index[:n]
    perp_dates_mis = perp.index[:n]

    basis_mis = (perp_close_mis - spot_close_mis) / np.where(spot_close_mis > 0, spot_close_mis, 1.0)

    # Aligned (v3 correct path)
    common_start = max(spot.index[0], perp.index[0])
    common_end = min(spot.index[-1], perp.index[-1])
    spot_aligned = spot[common_start:common_end]
    perp_aligned = perp[common_start:common_end]
    n_aligned = min(len(spot_aligned), len(perp_aligned))

    spot_close_aligned = spot_aligned["close"].values[:n_aligned]
    perp_close_aligned = perp_aligned["close"].values[:n_aligned]

    basis_aligned = (perp_close_aligned - spot_close_aligned) / np.where(spot_close_aligned > 0, spot_close_aligned, 1.0)

    return {
        "token": token,
        "spot_start": spot.index[0],
        "perp_start": perp.index[0],
        "spot_bars": len(spot),
        "perp_bars": len(perp),
        "n_misaligned": n,
        "n_aligned": n_aligned,
        # Misaligned stats
        "mis_basis_max": basis_mis.max() * 100,
        "mis_basis_min": basis_mis.min() * 100,
        "mis_basis_mean": basis_mis.mean() * 100,
        "mis_abnormal_count": int((np.abs(basis_mis) > 10).sum()),
        "mis_abnormal_pct": (np.abs(basis_mis) > 10).sum() / n * 100,
        # Aligned stats
        "aligned_basis_max": basis_aligned.max() * 100,
        "aligned_basis_min": basis_aligned.min() * 100,
        "aligned_basis_mean": basis_aligned.mean() * 100,
        "aligned_abnormal_count": int((np.abs(basis_aligned) > 10).sum()),
        # Date comparison
        "mis_spot_date_start": spot_dates_mis[0],
        "mis_spot_date_end": spot_dates_mis[-1],
        "mis_perp_date_start": perp_dates_mis[0],
        "mis_perp_date_end": perp_dates_mis[-1],
        # Worst example
        "worst_idx": int(np.argmax(np.abs(basis_mis))),
        "worst_basis": basis_mis[np.argmax(np.abs(basis_mis))] * 100,
        "worst_spot_price": spot_close_mis[np.argmax(np.abs(basis_mis))],
        "worst_perp_price": perp_close_mis[np.argmax(np.abs(basis_mis))],
        "worst_spot_date": spot_dates_mis[np.argmax(np.abs(basis_mis))],
        "worst_perp_date": perp_dates_mis[np.argmax(np.abs(basis_mis))],
    }


def main():
    print("=" * 100)
    print(" SPOT vs PERP BASIS AUDIT — MISALIGNMENT BUG REPRODUCTION")
    print("=" * 100)

    print("""
  BACKGROUND:
  - v3/engine.py:backtest_token_combined() correctly aligns spot and perp to
    common_start/common_end BEFORE building contexts (lines 1685-1688)
  - v4/signals.py:precompute_strategy_signals() loads spot and perp independently
    with only a date floor (load_from), but does NOT align to common time range
  - Strategies do: n = min(len(spot), len(perp)); basis = perp[:n] - spot[:n]
  - When spot starts years before perp, this compares bars from DIFFERENT dates
""")

    # ---- SUMMARY TABLE ----
    print("-" * 100)
    print(f"  {'Token':<6} {'Spot Start':<12} {'Perp Start':<12} {'Gap':>6} "
          f"{'Misaligned Max':>16} {'Aligned Max':>14} {'Misaligned |>10%|':>18} {'Aligned |>10%|':>16}")
    print("-" * 100)

    all_results = []
    for token in ALL_TOKENS:
        r = simulate_misaligned_basis(token)
        all_results.append(r)

        gap_days = (r["perp_start"] - r["spot_start"]).total_seconds() / 86400

        print(f"  {r['token']:<6} {str(r['spot_start'].date()):<12} {str(r['perp_start'].date()):<12} "
              f"{gap_days:>5.0f}d "
              f"{r['mis_basis_max']:>+15.1f}% "
              f"{r['aligned_basis_max']:>+13.1f}% "
              f"{r['mis_abnormal_count']:>17,} "
              f"{r['aligned_abnormal_count']:>15,}")

    # ---- DETAILED MISALIGNMENT EXAMPLES ----
    print(f"\n{'=' * 100}")
    print(" DETAILED MISALIGNMENT EXAMPLES FOR FLAGGED TOKENS")
    print("=" * 100)

    for r in all_results:
        if r["token"] not in FLAGGED:
            continue

        print(f"\n  {r['token']}:")
        print(f"    Spot range: {r['spot_start']} to {r['mis_spot_date_end']} ({r['spot_bars']} bars)")
        print(f"    Perp range: {r['perp_start']} to {r['mis_perp_date_end']} ({r['perp_bars']} bars)")
        print(f"    Start date gap: {(r['perp_start'] - r['spot_start']).days} days")
        print()
        print(f"    MISALIGNED (v4 bug): spot[:n] vs perp[:n] where n={r['n_misaligned']}")
        print(f"      spot[:n] covers: {r['mis_spot_date_start']} to {r['mis_spot_date_end']}")
        print(f"      perp[:n] covers: {r['mis_perp_date_start']} to {r['mis_perp_date_end']}")
        print(f"      Max basis: {r['mis_basis_max']:+.1f}%")
        print(f"      Min basis: {r['mis_basis_min']:+.1f}%")
        print(f"      |Basis| > 10%: {r['mis_abnormal_count']:,} bars ({r['mis_abnormal_pct']:.1f}%)")
        print()
        print(f"    WORST EXAMPLE:")
        print(f"      Spot bar date:  {r['worst_spot_date']}  price={r['worst_spot_price']:.6f}")
        print(f"      Perp bar date:  {r['worst_perp_date']}  price={r['worst_perp_price']:.6f}")
        print(f"      Computed basis: {r['worst_basis']:+.1f}%")
        print(f"      These bars are from DIFFERENT DATES — {(r['worst_perp_date'] - r['worst_spot_date']).days} days apart!")
        print()
        print(f"    ALIGNED (v3 correct): inner join on datetime")
        print(f"      Max basis: {r['aligned_basis_max']:+.1f}%")
        print(f"      Min basis: {r['aligned_basis_min']:+.1f}%")
        print(f"      |Basis| > 10%: {r['aligned_abnormal_count']} bars")

    # ---- Show bar-by-bar misalignment for INJ ----
    print(f"\n{'=' * 100}")
    print(" BAR-BY-BAR MISALIGNMENT DEMO: INJ (first 10 and worst 10 bars)")
    print("=" * 100)

    spot, perp = load_pair("INJ")
    n = min(len(spot), len(perp))

    print(f"\n  First 10 bars (misaligned comparison):")
    print(f"  {'Bar':>4} {'Spot Date':<22} {'Spot Price':>12} {'Perp Date':<22} {'Perp Price':>12} {'Basis':>10}")
    print(f"  {'─'*4} {'─'*22} {'─'*12} {'─'*22} {'─'*12} {'─'*10}")

    for i in range(10):
        s_date = spot.index[i]
        s_price = spot["close"].iloc[i]
        p_date = perp.index[i]
        p_price = perp["close"].iloc[i]
        basis = (p_price - s_price) / s_price * 100
        print(f"  {i:>4} {str(s_date):<22} {s_price:>12.4f} {str(p_date):<22} {p_price:>12.4f} {basis:>+9.1f}%")

    # Find worst bars
    spot_close = spot["close"].values[:n]
    perp_close = perp["close"].values[:n]
    basis_arr = (perp_close - spot_close) / np.where(spot_close > 0, spot_close, 1.0) * 100
    worst_indices = np.argsort(np.abs(basis_arr))[-10:][::-1]

    print(f"\n  Worst 10 bars by |basis| (misaligned comparison):")
    print(f"  {'Bar':>6} {'Spot Date':<22} {'Spot Price':>12} {'Perp Date':<22} {'Perp Price':>12} {'Basis':>10}")
    print(f"  {'─'*6} {'─'*22} {'─'*12} {'─'*22} {'─'*12} {'─'*10}")

    for i in worst_indices:
        s_date = spot.index[i]
        s_price = spot["close"].iloc[i]
        p_date = perp.index[i]
        p_price = perp["close"].iloc[i]
        print(f"  {i:>6} {str(s_date):<22} {s_price:>12.4f} {str(p_date):<22} {p_price:>12.4f} {basis_arr[i]:>+9.1f}%")

    # ---- ROOT CAUSE ----
    print(f"\n{'=' * 100}")
    print(" ROOT CAUSE ANALYSIS")
    print("=" * 100)
    print("""
  STATUS: The raw parquet data is CLEAN. Spot and perp prices match closely
  when compared at the same timestamps. The impossible basis values are caused
  by a TIMESTAMP MISALIGNMENT BUG in the code path.

  BUG LOCATION: v4/signals.py:precompute_strategy_signals() (lines 239-250)

  MECHANISM:
    1. Spot and perp parquets start on different dates for many tokens
       (perp markets launched months/years after spot markets)
    2. v4/signals.py loads each independently with a date floor but does NOT
       align them to a common start date
    3. _build_context() builds indicators from each DataFrame independently
    4. Strategies do n=min(len(spot),len(perp)) and compare bar[0] of spot
       with bar[0] of perp — but these bars are from DIFFERENT DATES
    5. Result: spot price from 2020 compared with perp price from 2022+ = huge "basis"

  AFFECTED TOKENS (data start gap > 30 days):
    INJ:  spot 2020-10-21, perp 2022-08-17 (665 days gap)
    FET:  spot 2020-01-01, perp 2023-01-17 (1112 days gap)
    AGLD: spot 2021-10-05, perp 2023-07-29 (662 days gap)
    ICP:  spot 2021-05-11, perp 2022-09-27 (504 days gap)

  NOT AFFECTED BY THIS BUG:
    BTC:  spot and perp both start 2020-01-01 (0 days gap)
    ETH:  spot and perp both start 2020-01-01 (0 days gap)
    SOL:  spot 2020-08-11, perp 2020-09-14 (34 days gap — minor)

  v3 PATH (CORRECT):
    v3/engine.py:backtest_token_combined() lines 1685-1688:
      common_start = max(df_1h_spot.index[0], df_1h_perp.index[0])
      common_end = min(df_1h_spot.index[-1], df_1h_perp.index[-1])
      df_1h_spot = df_1h_spot[common_start:common_end]
      df_1h_perp = df_1h_perp[common_start:common_end]

  v4 PATH (MISSING ALIGNMENT):
    v4/signals.py lines 239-250:
      df_spot = pd.read_parquet(spot_pq)
      df_spot = df_spot[df_spot.index >= load_from]  # only date floor
      df_perp = pd.read_parquet(perp_pq)
      df_perp = df_perp[df_perp.index >= load_from]  # only date floor
      # NO common_start/common_end alignment!
      ctx_spot = eng_spot._build_context(token, df_spot, ...)
      ctx_perp = eng_perp._build_context(token, df_perp, ...)

  FIX: Add common-range alignment after loading, before building contexts:
      if is_combined:
          common_start = max(df_spot.index[0], df_perp.index[0])
          common_end = min(df_spot.index[-1], df_perp.index[-1])
          df_spot = df_spot[common_start:common_end]
          df_perp = df_perp[common_start:common_end]

  NOTE: Even with the v4 bug, the reported "1020% INJ basis" suggests the
  backtest/dashboard is computing the MAXIMUM basis across all bars for each
  token. With the misalignment, comparing INJ spot at $0.78 (Oct 2020) with
  INJ perp at $8-$9 (2023) yields a basis of ~1000%, matching the reported value.
""")

    # Verify the 1020% INJ claim
    print("  VERIFICATION: Can we reproduce the ~1020% INJ basis?")
    spot_inj, perp_inj = load_pair("INJ")
    n_inj = min(len(spot_inj), len(perp_inj))
    mis_basis = (perp_inj["close"].values[:n_inj] - spot_inj["close"].values[:n_inj]) / \
                np.where(spot_inj["close"].values[:n_inj] > 0, spot_inj["close"].values[:n_inj], 1.0)
    max_basis_pct = mis_basis.max() * 100
    max_idx = np.argmax(mis_basis)
    print(f"    Max misaligned basis for INJ: {max_basis_pct:.1f}%")
    print(f"    At bar {max_idx}: spot date={spot_inj.index[max_idx]} price={spot_inj['close'].iloc[max_idx]:.4f}")
    print(f"                      perp date={perp_inj.index[max_idx]} price={perp_inj['close'].iloc[max_idx]:.4f}")
    print()

    # Check all tokens for max misaligned basis
    print(f"\n{'=' * 100}")
    print(" ALL TOKENS: Maximum Misaligned Basis")
    print("=" * 100)
    print()

    # Check ALL tokens (not just our test set)
    import os
    spot_tokens = {f.replace("_1h.parquet", "") for f in os.listdir(SPOT_DIR) if f.endswith(".parquet")}
    perp_tokens = {f.replace("_1h.parquet", "") for f in os.listdir(PERP_DIR) if f.endswith(".parquet")}
    both_tokens = sorted(spot_tokens & perp_tokens)

    results_all = []
    for token in both_tokens:
        try:
            spot, perp = load_pair(token)
            n = min(len(spot), len(perp))
            if n < 100:
                continue

            gap_days = (perp.index[0] - spot.index[0]).total_seconds() / 86400

            spot_c = spot["close"].values[:n]
            perp_c = perp["close"].values[:n]
            mis_basis = (perp_c - spot_c) / np.where(spot_c > 0, spot_c, 1.0) * 100

            results_all.append({
                "token": token,
                "gap_days": gap_days,
                "max_mis_basis": mis_basis.max(),
                "min_mis_basis": mis_basis.min(),
                "abnormal_count": int((np.abs(mis_basis) > 10).sum()),
            })
        except Exception as e:
            pass

    # Sort by max absolute basis
    results_all.sort(key=lambda x: max(abs(x["max_mis_basis"]), abs(x["min_mis_basis"])), reverse=True)

    print(f"  {'Token':<10} {'Gap (days)':>10} {'Max Basis':>12} {'Min Basis':>12} {'|>10%| bars':>12}")
    print(f"  {'─'*10} {'─'*10} {'─'*12} {'─'*12} {'─'*12}")
    for r in results_all[:30]:  # Show top 30
        print(f"  {r['token']:<10} {r['gap_days']:>+10.0f} {r['max_mis_basis']:>+11.1f}% {r['min_mis_basis']:>+11.1f}% {r['abnormal_count']:>11,}")

    print(f"\n  Total tokens with both spot+perp data: {len(both_tokens)}")
    affected = [r for r in results_all if r["abnormal_count"] > 0]
    print(f"  Tokens with |basis| > 10% under misalignment: {len(affected)}")
    clean = [r for r in results_all if r["abnormal_count"] == 0]
    print(f"  Tokens clean even with misalignment: {len(clean)} (same start dates)")

    print(f"\n{'=' * 100}")
    print(" AUDIT COMPLETE")
    print("=" * 100)


if __name__ == "__main__":
    main()
