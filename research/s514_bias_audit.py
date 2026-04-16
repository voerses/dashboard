#!/usr/bin/env python3
"""
s514 Look-Ahead Bias Audit
===========================
Checks:
1. L/S daily data timestamp alignment
2. Forward-fill alignment in _get_divergence_aligned
3. Quantified impact: biased vs unbiased (1-day shifted) backtest
"""

import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

os.chdir("/workspace/crypto_backtest")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# engine module lives in v4/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "v4"))

from engine import rolling_zscore
from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens
from v4.simulator import (
    build_unified_index, SimulationState, simulate_portfolio,
    _process_exits, _process_entries, _process_margin_calls,
    _record_equity_snapshot, _close_all_remaining,
)
from v4.report import compute_portfolio_metrics

# ==========================================================================
# PART 1: Inspect raw data timestamps
# ==========================================================================
print("=" * 70)
print("PART 1: DATA TIMESTAMP INSPECTION")
print("=" * 70)

ls = pd.read_parquet("data/alternative/binance_metrics/all_symbols_daily_ls.parquet")
print(f"\nL/S parquet shape: {ls.shape}")
print(f"Columns: {list(ls.columns)}")
print(f"Date dtype: {ls['date'].dtype}")

btc_ls = ls[ls["symbol"] == "BTCUSDT"].sort_values("date")
print(f"\nFirst 5 rows (BTCUSDT):")
print(btc_ls[["date", "count_toptrader_ls_ratio", "count_ls_ratio", "n_records"]].head(5).to_string())
print(f"\nAll dates at midnight? {(btc_ls['date'].dt.hour == 0).all()}")
print(f"Typical n_records per day: {btc_ls['n_records'].median():.0f} (expect 288 for 5-min intervals)")

# Price data
price = pd.read_parquet("data/perp/1h_cache/BTC_1h.parquet")
print(f"\nPrice index dtype: {price.index.dtype}")
print(f"Price timezone: {price.index.tz}")
print(f"First 3 timestamps: {price.index[:3].tolist()}")

# ==========================================================================
# PART 2: Demonstrate the look-ahead in forward-fill alignment
# ==========================================================================
print("\n" + "=" * 70)
print("PART 2: FORWARD-FILL ALIGNMENT ANALYSIS")
print("=" * 70)

btc_ls_clean = btc_ls.drop_duplicates(subset="date", keep="last").sort_values("date")
divergence_series = pd.Series(
    (btc_ls_clean["count_toptrader_ls_ratio"] - btc_ls_clean["count_ls_ratio"]).values,
    index=pd.to_datetime(btc_ls_clean["date"].values).tz_localize(None),
    dtype=np.float64,
)

sample_start = "2025-01-13"
sample_end = "2025-01-17"
idx_1h = pd.date_range(sample_start, sample_end, freq="1h", inclusive="left")

# BIASED alignment (what the strategy currently does)
biased = divergence_series.reindex(idx_1h.normalize(), method="ffill")
biased.index = idx_1h

# UNBIASED alignment (shift daily dates by +1 day)
shifted_series = divergence_series.copy()
shifted_series.index = shifted_series.index + pd.Timedelta(days=1)
unbiased = shifted_series.reindex(idx_1h.normalize(), method="ffill")
unbiased.index = idx_1h

print(f"\nBIASED alignment (current code) vs UNBIASED (1-day shift):")
print(f"{'Hourly bar':>22} | {'Biased val':>12} | {'Biased src':>12} | {'Unbiased val':>12} | {'Unbias src':>12}")
print("-" * 85)
for i in range(0, min(96, len(idx_1h))):
    bar_ts = idx_1h[i]
    if bar_ts.hour not in [0, 12, 23]:
        continue
    bval = biased.iloc[i]
    uval = unbiased.iloc[i]
    # Biased source: daily date == same day
    norm = bar_ts.normalize()
    b_src = norm.date()
    # Unbiased source: daily date == previous day
    u_src = (norm - pd.Timedelta(days=1)).date()
    print(f"{str(bar_ts):>22} | {bval:>12.6f} | {str(b_src):>12} | {uval:>12.6f} | {str(u_src):>12}")

print(f"\nCRITICAL: On 2025-01-15 00:00, biased uses Jan 15 daily mean (not yet available).")
print(f"          Unbiased correctly uses Jan 14 daily mean (known at Jan 14 23:55).")

# ==========================================================================
# PART 3: Signal comparison across all tokens
# ==========================================================================
print("\n" + "=" * 70)
print("PART 3: SIGNAL COMPARISON (BIASED vs UNBIASED)")
print("=" * 70)

# Build divergence cache
df = pd.read_parquet(
    "data/alternative/binance_metrics/all_symbols_daily_ls.parquet",
    columns=["date", "symbol", "count_toptrader_ls_ratio", "count_ls_ratio"],
)
df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)

ls_cache = {}
for symbol, grp in df.groupby("symbol"):
    grp = grp.sort_values("date").drop_duplicates(subset="date", keep="last")
    div = grp["count_toptrader_ls_ratio"].values - grp["count_ls_ratio"].values
    ls_cache[symbol] = pd.Series(div, index=grp["date"].values, dtype=np.float64)

ALLOWED_TOKENS = {
    'EIGEN', 'PENGU', 'INJ', 'LTC', 'ARB', 'SAND', 'AAVE', 'GUN',
    'RAYSOL', 'LINK', 'HBAR', 'RENDER', 'ETH', 'PIXEL', 'DOGE',
    'WLD', 'VANRY', 'ADA', 'AIOT', 'UNI', 'BTC', 'IP', 'NEAR',
    'ETC', 'MYX', 'PIPPIN', 'ZEN', 'RESOLV',
}

ZSCORE_WINDOW = 30 * 24
THRESHOLD = 2.5
WARMUP = 800

total_b = total_u = total_overlap = 0
token_results = []

for ticker in sorted(ALLOWED_TOKENS):
    symbol = ticker + "USDT"
    if symbol not in ls_cache:
        continue
    price_file = f"data/perp/1h_cache/{ticker}_1h.parquet"
    if not os.path.exists(price_file):
        continue

    px = pd.read_parquet(price_file)
    idx = px.index
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    n = len(idx)

    # Biased
    series_b = ls_cache[symbol]
    aligned_b = series_b.reindex(idx.normalize(), method="ffill")
    aligned_b.index = idx
    div_b = aligned_b.values.astype(np.float64)

    # Unbiased
    series_u = series_b.copy()
    series_u.index = series_u.index + pd.Timedelta(days=1)
    aligned_u = series_u.reindex(idx.normalize(), method="ffill")
    aligned_u.index = idx
    div_u = aligned_u.values.astype(np.float64)

    z_b = np.nan_to_num(rolling_zscore(div_b, ZSCORE_WINDOW), nan=0.0)
    z_u = np.nan_to_num(rolling_zscore(div_u, ZSCORE_WINDOW), nan=0.0)

    dates = idx.normalize()
    day_change = np.zeros(n, dtype=bool)
    day_change[0] = True
    day_change[1:] = dates[1:] != dates[:-1]

    def make_signals(z):
        sl = z > THRESHOLD; sp = np.roll(sl, 1); sp[0] = False
        ss = sl & (~sp | day_change) & day_change
        ll = z < -THRESHOLD; lp = np.roll(ll, 1); lp[0] = False
        ls_sig = ll & (~lp | day_change) & day_change
        entry = ls_sig | ss
        entry[:WARMUP] = False
        return entry

    eb = make_signals(z_b)
    eu = make_signals(z_u)

    nb, nu, ov = int(eb.sum()), int(eu.sum()), int((eb & eu).sum())
    total_b += nb; total_u += nu; total_overlap += ov
    if nb > 0 or nu > 0:
        token_results.append({"token": ticker, "b": nb, "u": nu, "ov": ov})

print(f"\n{'Token':>8} | {'Biased':>8} | {'Unbiased':>8} | {'Overlap':>8}")
print("-" * 45)
for r in token_results:
    print(f"{r['token']:>8} | {r['b']:>8} | {r['u']:>8} | {r['ov']:>8}")
print("-" * 45)
print(f"{'TOTAL':>8} | {total_b:>8} | {total_u:>8} | {total_overlap:>8}")
if total_b > 0:
    print(f"Overlap: {total_overlap/total_b*100:.1f}% of biased signals also fire unbiased")


# ==========================================================================
# PART 4: Full backtest comparison using V4 engine
# ==========================================================================
print("\n" + "=" * 70)
print("PART 4: FULL BACKTEST COMPARISON (L12M)")
print("=" * 70)

import importlib
from v4.engine import _STRATEGY_MODULE_CACHE

STRATEGY_ID = "s514"
LOOKBACK = 14
START = "2025-04-01"
END = pd.Timestamp("2026-04-01")

def run_backtest_variant(label, patch_fn=None):
    """Run s514 backtest, optionally patching the alignment function."""
    # Clear the engine's module cache so strategy gets reloaded fresh
    _STRATEGY_MODULE_CACHE.clear()

    spec = StrategySpec(
        strategy_id=STRATEGY_ID,
        weight=1.0,
        max_positions=15,
        market="perp",
    )
    config = PortfolioConfig(capital=100_000)

    tokens = discover_tokens("perp")

    if patch_fn:
        # Pre-load the module so we can patch before signal computation
        from v4.engine import _load_strategy_fn
        _load_strategy_fn(STRATEGY_ID)  # Loads into cache
        cached_mod = _STRATEGY_MODULE_CACHE[STRATEGY_ID]
        # Reset caches so fresh data is loaded
        cached_mod._ls_loaded = False
        cached_mod._ls_cache = {}
        cached_mod._aligned_cache = {}
        # Apply the alignment patch (closure captures the module)
        cached_mod._get_divergence_aligned = patch_fn(cached_mod)

    all_signals = {
        STRATEGY_ID: precompute_strategy_signals(
            spec, tokens, config, LOOKBACK, end_date=END,
        )
    }

    n_tokens = len(all_signals[STRATEGY_ID])
    print(f"  [{label}] {n_tokens} tokens with signals")

    strategy_specs = {STRATEGY_ID: spec}
    state = simulate_portfolio(all_signals, strategy_specs, config)

    # Extract equity curve
    if not state.equity_snapshots:
        print(f"  [{label}] No equity snapshots!")
        return None

    ts_list = [s[0] for s in state.equity_snapshots]
    eq_list = [s[1] for s in state.equity_snapshots]
    eq_series = pd.Series(eq_list, index=pd.DatetimeIndex(ts_list))
    eq_daily = eq_series.resample("D").last().ffill()

    # Filter to OOS period
    eq_oos = eq_daily.loc[START:END]
    if len(eq_oos) < 2:
        print(f"  [{label}] Not enough data in OOS window")
        return None

    total_ret = (eq_oos.iloc[-1] / eq_oos.iloc[0] - 1) * 100
    daily_rets = eq_oos.pct_change().dropna()
    sharpe = daily_rets.mean() / daily_rets.std() * np.sqrt(365) if daily_rets.std() > 0 else 0
    max_dd = ((eq_oos / eq_oos.cummax()) - 1).min() * 100
    n_trades = len(state.position_manager.closed_trades)

    print(f"  [{label}] Return: {total_ret:+.1f}%, Sharpe: {sharpe:.2f}, MaxDD: {max_dd:.1f}%, Trades: {n_trades}")
    return {
        "label": label,
        "return": total_ret,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "trades": n_trades,
        "eq_series": eq_oos,
    }


def make_unbiased_alignment(module):
    """Create a closure that uses the module's _ls_cache but shifts by 1 day."""
    def unbiased_alignment(symbol, idx_1h):
        cache_key = (symbol, len(idx_1h), idx_1h[0], idx_1h[-1])
        if cache_key in module._aligned_cache:
            return module._aligned_cache[cache_key]
        if symbol not in module._ls_cache:
            result = np.full(len(idx_1h), np.nan, dtype=np.float64)
        else:
            series = module._ls_cache[symbol]
            shifted = series.copy()
            shifted.index = shifted.index + pd.Timedelta(days=1)
            aligned = shifted.reindex(idx_1h.normalize(), method="ffill")
            aligned.index = idx_1h
            result = aligned.values.astype(np.float64)
        module._aligned_cache[cache_key] = result
        return result
    return unbiased_alignment


print("\nRunning BIASED backtest (original, look-ahead)...")
t0 = time.time()
result_biased = run_backtest_variant("BIASED")
print(f"  Time: {time.time()-t0:.1f}s")

print("\nRunning UNBIASED backtest (1-day shift, no look-ahead)...")
t0 = time.time()
result_unbiased = run_backtest_variant("UNBIASED", patch_fn=make_unbiased_alignment)
print(f"  Time: {time.time()-t0:.1f}s")


# ==========================================================================
# SUMMARY
# ==========================================================================
print("\n" + "=" * 70)
print("AUDIT SUMMARY")
print("=" * 70)

print("""
1. LOOK-AHEAD BIAS: YES -- CONFIRMED

   The Binance daily L/S metric is the MEAN of ~288 five-minute snapshots
   spanning 00:00 to 23:55 UTC of that calendar day. This value cannot be
   known until the day is COMPLETE (after 23:55 UTC).

   The strategy code in _get_divergence_aligned() does:
       series.reindex(idx_1h.normalize(), method="ffill")

   This assigns the daily value for date D to ALL hourly bars on date D,
   starting at D 00:00 UTC. The strategy "knows" the full day's L/S
   positioning data at the START of that day -- classic look-ahead bias.

2. DATA ALIGNMENT:
   - L/S dates: midnight timestamps (00:00 UTC), representing full-day means
   - Price data: UTC hourly bars, no timezone object
   - Timezones: consistent (both naive UTC)
   - PROBLEM: not timezone mismatch but temporal availability --
     daily aggregates assigned to the same day they represent

3. Z-SCORE COMPUTATION:
   - rolling_zscore uses pd.Series.rolling() -- causal (uses data up to
     current bar). Correct in isolation, but input has look-ahead, so
     z-score inherits the bias.

4. ENTRY EXECUTION:
   - V4 simulator: entry at close[local_bar] on the signal bar (same-bar)
   - Signals fire at 00:00 UTC (first bar of day)
   - This is standard for backtesting; no additional bias IF signal data
     were available. The bias comes from the data, not the execution.

5. RECOMMENDED FIX:
   In _get_divergence_aligned(), shift daily dates by +1 day:

   BEFORE (biased):
       aligned = series.reindex(idx_1h.normalize(), method="ffill")

   AFTER (unbiased):
       shifted = series.copy()
       shifted.index = shifted.index + pd.Timedelta(days=1)
       aligned = shifted.reindex(idx_1h.normalize(), method="ffill")
""")

if result_biased and result_unbiased:
    print(f"6. QUANTIFIED IMPACT (OOS: {START} to {END}):")
    print(f"   {'Variant':>10} | {'Return':>10} | {'Sharpe':>8} | {'MaxDD':>8} | {'Trades':>7}")
    print(f"   {'-'*55}")
    print(f"   {'BIASED':>10} | {result_biased['return']:>+9.1f}% | {result_biased['sharpe']:>8.2f} | {result_biased['max_dd']:>+7.1f}% | {result_biased['trades']:>7}")
    print(f"   {'UNBIASED':>10} | {result_unbiased['return']:>+9.1f}% | {result_unbiased['sharpe']:>8.2f} | {result_unbiased['max_dd']:>+7.1f}% | {result_unbiased['trades']:>7}")
    ret_diff = result_biased['return'] - result_unbiased['return']
    print(f"\n   Return inflation from look-ahead: {ret_diff:+.1f} percentage points")
    if result_unbiased['return'] != 0:
        inflation_pct = (result_biased['return'] / result_unbiased['return'] - 1) * 100
        print(f"   Relative inflation: {inflation_pct:+.1f}%")
else:
    print("6. Full backtest comparison could not complete.")
