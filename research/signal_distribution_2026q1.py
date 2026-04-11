"""
Signal Distribution Analysis — 2026 Q1 (s524g_hybrid_gate)
==========================================================
Comprehensive statistical profile of all trades from the 3-month backtest
ending 2026-04-05.

Analyses:
  1. Signal Strength Distribution (by conviction bucket)
  2. IC Quartile x Direction x Outcome
  3. Peak Profit vs Final PnL
  4. Exit Reason x Signal Strength
  5. Token-Level Profitability
  6. Optimal Holding Profile (MTM curves by conviction)
  7. Regime Context (TOTAL2 at entry)
"""

import json
import os
import warnings
import numpy as np
import pandas as pd
from collections import defaultdict

warnings.filterwarnings("ignore")

BASE = "/workspace/crypto_backtest"
TRADES_PATH = "/tmp/bt_2026_deep/s524g_hybrid_gate_3mo_100k_trades.json"
CONFIG_PATH = os.path.join(BASE, "data/alternative/s521_token_config.json")
METRICS_5MIN_DIR = os.path.join(BASE, "data/alternative/binance_metrics/5min")
OHLCV_1H_DIR = os.path.join(BASE, "data/perp/binance/1h_ohlcv")
TOTAL2_PATH = os.path.join(BASE, "data/alternative/total2_total3.parquet")

ZSCORE_WINDOW = 22


# ======================================================================
# HELPERS
# ======================================================================

def fmt_pct(x):
    return f"{x:.1f}%"


def fmt_usd(x):
    return f"${x:,.0f}"


def fmt_float(x, d=2):
    return f"{x:.{d}f}"


def print_table(headers, rows, col_widths=None):
    """Print a formatted ASCII table."""
    if col_widths is None:
        col_widths = []
        for i, h in enumerate(headers):
            w = len(str(h))
            for r in rows:
                w = max(w, len(str(r[i])))
            col_widths.append(w + 2)

    hdr = "".join(str(h).ljust(col_widths[i]) for i, h in enumerate(headers))
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print("".join(str(r[i]).ljust(col_widths[i]) for i in range(len(headers))))
    print()


def profit_factor(pnls):
    gains = sum(p for p in pnls if p > 0)
    losses = abs(sum(p for p in pnls if p < 0))
    return gains / losses if losses > 0 else float("inf")


# ======================================================================
# LOAD DATA
# ======================================================================

print("=" * 80)
print("SIGNAL DISTRIBUTION ANALYSIS — 2026 Q1 (s524g_hybrid_gate)")
print("=" * 80)
print()

# Load trades
with open(TRADES_PATH) as f:
    raw_trades = json.load(f)

# Load token config
with open(CONFIG_PATH) as f:
    token_config = json.load(f)

# Parse trades
trades = []
for t in raw_trades:
    trades.append({
        "token": t["token"],
        "direction": int(t["direction"]),
        "pnl": float(t["pnl"]),
        "margin_usd": float(t["margin_usd"]),
        "entry_price": float(t["entry_price"]),
        "exit_price": float(t["exit_price"]),
        "entry_bar": int(t["entry_bar"]),
        "exit_bar": int(t["exit_bar"]),
        "hold_hours": int(t["hold_hours"]),
        "exit_reason": t["exit_reason"],
    })

print(f"Loaded {len(trades)} trades across {len(set(t['token'] for t in trades))} tokens")
print()


# ======================================================================
# COMPUTE BACKTEST DATE RANGE
# ======================================================================

# The backtest ends 2026-04-05T16:00:00, 3 months = ~2160 1h bars
# entry_bar is the index into the 1h array
# We need the actual datetime for each bar to look up signals
# Approximate: end_date - (max_bar - entry_bar) hours
end_dt = pd.Timestamp("2026-04-05T16:00:00")
max_bar = max(t["exit_bar"] for t in trades)
# Start date ~ end_dt - max_bar hours (this is approximate)
# Actually the bars are 0-indexed from the start of the 3-month window
# 3 months before 2026-04-05 = 2026-01-05
start_dt = pd.Timestamp("2026-01-05T16:00:00")
# Generate the 1h index
idx_1h = pd.date_range(start=start_dt, end=end_dt, freq="1h")
n_bars = len(idx_1h)
print(f"Backtest window: {start_dt} to {end_dt} ({n_bars} bars)")
print()


# ======================================================================
# FEATURE 1: SIGNAL STRENGTH (composite z-score at entry)
# ======================================================================

print("Computing signal strength for each trade...")

# Cache daily composite z-scores per token
_composite_daily_cache = {}


def get_daily_composite(token):
    """Replicate the strategy's composite z-score computation."""
    if token in _composite_daily_cache:
        return _composite_daily_cache[token]

    symbol = token + "USDT"
    if token not in token_config:
        _composite_daily_cache[token] = None
        return None

    parquet_path = os.path.join(METRICS_5MIN_DIR, f"{symbol}_5min.parquet")
    if not os.path.exists(parquet_path):
        _composite_daily_cache[token] = None
        return None

    try:
        df = pd.read_parquet(
            parquet_path,
            columns=["create_time", "sum_open_interest_value",
                      "sum_toptrader_long_short_ratio",
                      "sum_taker_long_short_vol_ratio"],
        )
        df["create_time"] = pd.to_datetime(df["create_time"])
        df = df.set_index("create_time").sort_index()
        daily = df.resample("1D").last().dropna(how="all")

        cfg = token_config[token]
        w_oi = cfg["oi_weight"]
        w_pos = cfg["pos_weight"]
        w_flow = cfg["flow_weight"]
        oi_sign = cfg["oi_sign"]
        pos_sign = cfg["pos_sign"]
        flow_sign = cfg["flow_sign"]

        def daily_zscore(arr, window):
            s = pd.Series(arr, dtype=np.float64)
            mu = s.rolling(window, min_periods=window // 2).mean()
            sd = s.rolling(window, min_periods=window // 2).std(ddof=1)
            z = (s - mu) / sd.replace(0, np.nan)
            return z.values

        n_days = len(daily)
        if n_days < ZSCORE_WINDOW:
            _composite_daily_cache[token] = None
            return None

        oi_z = daily_zscore(daily["sum_open_interest_value"].values, ZSCORE_WINDOW)
        pos_z = daily_zscore(daily["sum_toptrader_long_short_ratio"].values, ZSCORE_WINDOW)
        flow_z = daily_zscore(daily["sum_taker_long_short_vol_ratio"].values, ZSCORE_WINDOW)

        oi_z = np.nan_to_num(oi_z, nan=0.0)
        pos_z = np.nan_to_num(pos_z, nan=0.0)
        flow_z = np.nan_to_num(flow_z, nan=0.0)

        composite = (w_oi * oi_z * oi_sign +
                     w_pos * pos_z * pos_sign +
                     w_flow * flow_z * flow_sign)

        # Shift by 1 day (signal from day D used on day D+1)
        composite_shifted = np.empty_like(composite)
        composite_shifted[0] = np.nan
        composite_shifted[1:] = composite[:-1]

        result = pd.Series(composite_shifted, index=daily.index, dtype=np.float64)
        _composite_daily_cache[token] = result
        return result

    except Exception as exc:
        print(f"  WARNING: Failed for {token}: {exc}")
        _composite_daily_cache[token] = None
        return None


# For each trade, get the composite at entry date
for t in trades:
    entry_idx = t["entry_bar"]
    if entry_idx < len(idx_1h):
        entry_dt = idx_1h[entry_idx]
    else:
        entry_dt = end_dt
    t["entry_dt"] = entry_dt
    t["exit_dt"] = idx_1h[min(t["exit_bar"], len(idx_1h) - 1)]

    comp_series = get_daily_composite(t["token"])
    if comp_series is not None:
        entry_date = entry_dt.normalize()
        # Find the closest date <= entry_date
        valid = comp_series.index[comp_series.index <= entry_date]
        if len(valid) > 0:
            t["composite"] = float(comp_series.loc[valid[-1]])
        else:
            t["composite"] = np.nan
    else:
        t["composite"] = np.nan

    t["signal_strength"] = abs(t["composite"]) if not np.isnan(t["composite"]) else np.nan
    t["conviction"] = min(1.0, t["signal_strength"] / 3.0) if not np.isnan(t["signal_strength"]) else np.nan


# Conviction buckets
def conviction_bucket(c):
    if np.isnan(c):
        return "Unknown"
    if c < 0.33:
        return "Low"
    elif c < 0.66:
        return "Medium"
    else:
        return "High"


for t in trades:
    t["conv_bucket"] = conviction_bucket(t["conviction"])


# ======================================================================
# FEATURE 3: TOKEN IC QUARTILE
# ======================================================================

ic_values = [token_config[tk]["best_ic"] for tk in token_config]
ic_q1 = np.percentile(ic_values, 25)  # ~0.403
ic_q2 = np.percentile(ic_values, 50)  # ~0.499
ic_q3 = np.percentile(ic_values, 75)  # ~0.576
print(f"IC quartile boundaries: Q1<{ic_q1:.3f}, Q2<{ic_q2:.3f}, Q3<{ic_q3:.3f}")


def ic_quartile(token):
    if token not in token_config:
        return "Unknown"
    ic = token_config[token]["best_ic"]
    if ic < ic_q1:
        return "Q1 (low)"
    elif ic < ic_q2:
        return "Q2"
    elif ic < ic_q3:
        return "Q3"
    else:
        return "Q4 (high)"


for t in trades:
    t["ic_quartile"] = ic_quartile(t["token"])
    t["token_type"] = token_config.get(t["token"], {}).get("direction", "unknown")


# ======================================================================
# FEATURE 7: HOLD DURATION BUCKET
# ======================================================================

def hold_bucket(h):
    if h <= 120:
        return "0-120h"
    elif h <= 360:
        return "120-360h"
    elif h <= 540:
        return "360-540h"
    else:
        return "540-720h"


for t in trades:
    t["hold_bucket"] = hold_bucket(t["hold_hours"])


# ======================================================================
# FEATURE 8: PNL CATEGORY
# ======================================================================

def pnl_category(pnl):
    if pnl > 2000:
        return "big_win"
    elif pnl > 0:
        return "small_win"
    elif pnl > -2000:
        return "small_loss"
    else:
        return "big_loss"


for t in trades:
    t["pnl_cat"] = pnl_category(t["pnl"])


# ======================================================================
# FEATURE 9-11: PEAK PROFIT / TIME TO PEAK / DRAWDOWN FROM PEAK
# ======================================================================

print("Computing peak profit for each trade (from 1h price data)...")

_price_cache = {}


def load_1h_prices(token):
    if token in _price_cache:
        return _price_cache[token]
    path = os.path.join(OHLCV_1H_DIR, f"{token}_perp_1h.csv")
    if not os.path.exists(path):
        _price_cache[token] = None
        return None
    try:
        df = pd.read_csv(path, parse_dates=["datetime"])
        df = df.set_index("datetime").sort_index()
        if df.index.tz is not None:
            df.index = df.index.tz_convert(None)
        _price_cache[token] = df["close"]
        return df["close"]
    except Exception:
        _price_cache[token] = None
        return None


for t in trades:
    prices = load_1h_prices(t["token"])
    if prices is None:
        t["peak_profit_pct"] = np.nan
        t["time_to_peak_hrs"] = np.nan
        t["dd_from_peak_pct"] = np.nan
        continue

    entry_dt = t["entry_dt"]
    exit_dt = t["exit_dt"]

    # Get price slice from entry to exit
    mask = (prices.index >= entry_dt) & (prices.index <= exit_dt)
    price_slice = prices[mask]

    if len(price_slice) < 2:
        t["peak_profit_pct"] = np.nan
        t["time_to_peak_hrs"] = np.nan
        t["dd_from_peak_pct"] = np.nan
        continue

    entry_price = t["entry_price"]
    direction = t["direction"]

    # MTM at each hour
    if direction == 1:  # long
        mtm_pct = (price_slice.values / entry_price - 1) * 100
    else:  # short
        mtm_pct = (1 - price_slice.values / entry_price) * 100

    peak_idx = np.argmax(mtm_pct)
    peak_pct = mtm_pct[peak_idx]
    final_pct = mtm_pct[-1]

    t["peak_profit_pct"] = float(peak_pct)
    t["time_to_peak_hrs"] = float(peak_idx)  # hours from entry
    t["dd_from_peak_pct"] = float(peak_pct - final_pct)  # how much given back

    # Also store MTM at specific hours for Analysis 6
    t["mtm_curve"] = {}
    checkpoints = [0, 12, 24, 48, 72, 120, 168, 240, 336, 480, 600, 720]
    for h in checkpoints:
        if h < len(mtm_pct):
            t["mtm_curve"][h] = float(mtm_pct[h])


# ======================================================================
# FEATURE: TOTAL2 REGIME AT ENTRY
# ======================================================================

print("Loading TOTAL2 regime data...")

total2_df = pd.read_parquet(TOTAL2_PATH)
if total2_df.index.tz is not None:
    total2_df.index = total2_df.index.tz_localize(None)

total2_close = total2_df["total2_close"]
total2_ret_30d = total2_close.pct_change(30)

for t in trades:
    entry_date = t["entry_dt"].normalize()
    valid = total2_ret_30d.index[total2_ret_30d.index <= entry_date]
    if len(valid) > 0:
        ret = total2_ret_30d.loc[valid[-1]]
        t["total2_ret_30d"] = float(ret) if not np.isnan(ret) else 0.0
    else:
        t["total2_ret_30d"] = 0.0

    if t["total2_ret_30d"] > 0.05:
        t["total2_regime"] = "Rising (>+5%)"
    elif t["total2_ret_30d"] < -0.05:
        t["total2_regime"] = "Falling (<-5%)"
    else:
        t["total2_regime"] = "Flat (-5% to +5%)"


# ======================================================================
# ANALYSIS FUNCTIONS
# ======================================================================

def group_stats(group):
    """Compute standard stats for a group of trades."""
    n = len(group)
    if n == 0:
        return {"N": 0, "WR": "N/A", "Avg PnL": "N/A", "Total PnL": "$0",
                "Avg Hold": "N/A", "PF": "N/A"}
    pnls = [t["pnl"] for t in group]
    wins = sum(1 for p in pnls if p > 0)
    return {
        "N": n,
        "WR": fmt_pct(wins / n * 100),
        "Avg PnL": fmt_usd(np.mean(pnls)),
        "Total PnL": fmt_usd(sum(pnls)),
        "Avg Hold": f"{np.mean([t['hold_hours'] for t in group]):.0f}h",
        "PF": fmt_float(profit_factor(pnls)),
    }


# ======================================================================
# ANALYSIS 1: SIGNAL STRENGTH DISTRIBUTION
# ======================================================================

print()
print("=" * 80)
print("ANALYSIS 1: SIGNAL STRENGTH DISTRIBUTION")
print("=" * 80)
print()

# Overall by conviction bucket
buckets = ["Low", "Medium", "High", "Unknown"]
print("--- Overall by Conviction Bucket ---")
headers = ["Bucket", "N", "WR", "Avg PnL", "Total PnL", "Avg Hold", "PF"]
rows = []
for b in buckets:
    grp = [t for t in trades if t["conv_bucket"] == b]
    if len(grp) == 0:
        continue
    s = group_stats(grp)
    rows.append([b, s["N"], s["WR"], s["Avg PnL"], s["Total PnL"], s["Avg Hold"], s["PF"]])
print_table(headers, rows)

# By conviction + direction
print("--- Conviction x Direction ---")
headers = ["Bucket", "Dir", "N", "WR", "Avg PnL", "Total PnL", "PF"]
rows = []
for b in buckets:
    for d, dname in [(1, "Long"), (-1, "Short")]:
        grp = [t for t in trades if t["conv_bucket"] == b and t["direction"] == d]
        if len(grp) == 0:
            continue
        s = group_stats(grp)
        rows.append([b, dname, s["N"], s["WR"], s["Avg PnL"], s["Total PnL"], s["PF"]])
print_table(headers, rows)

# Signal strength stats
valid_ss = [t["signal_strength"] for t in trades if not np.isnan(t["signal_strength"])]
if valid_ss:
    print(f"Signal Strength Stats: mean={np.mean(valid_ss):.2f}, "
          f"median={np.median(valid_ss):.2f}, "
          f"std={np.std(valid_ss):.2f}, "
          f"min={np.min(valid_ss):.2f}, max={np.max(valid_ss):.2f}")
    print(f"  Pct > 1.0: {sum(1 for s in valid_ss if s > 1.0)/len(valid_ss)*100:.1f}%")
    print(f"  Pct > 2.0: {sum(1 for s in valid_ss if s > 2.0)/len(valid_ss)*100:.1f}%")
    print(f"  Pct > 3.0: {sum(1 for s in valid_ss if s > 3.0)/len(valid_ss)*100:.1f}%")
print()


# ======================================================================
# ANALYSIS 2: IC QUARTILE x DIRECTION x OUTCOME
# ======================================================================

print("=" * 80)
print("ANALYSIS 2: IC QUARTILE x DIRECTION x OUTCOME")
print("=" * 80)
print()

ic_qs = ["Q1 (low)", "Q2", "Q3", "Q4 (high)", "Unknown"]
headers = ["IC Quartile", "Dir", "N", "WR", "Avg PnL", "Total PnL", "PF"]
rows = []
for q in ic_qs:
    for d, dname in [(1, "Long"), (-1, "Short")]:
        grp = [t for t in trades if t["ic_quartile"] == q and t["direction"] == d]
        if len(grp) == 0:
            continue
        s = group_stats(grp)
        rows.append([q, dname, s["N"], s["WR"], s["Avg PnL"], s["Total PnL"], s["PF"]])
print_table(headers, rows)

# Also by token type
print("--- By Token Type ---")
headers = ["Type", "N", "WR", "Avg PnL", "Total PnL", "PF"]
rows = []
for tt in ["contrarian", "momentum", "unknown"]:
    grp = [t for t in trades if t["token_type"] == tt]
    if len(grp) == 0:
        continue
    s = group_stats(grp)
    rows.append([tt, s["N"], s["WR"], s["Avg PnL"], s["Total PnL"], s["PF"]])
print_table(headers, rows)

# Best and worst combos
print("--- Best/Worst IC Quartile x Direction Combos ---")
combos = []
for q in ic_qs:
    for d, dname in [(1, "Long"), (-1, "Short")]:
        grp = [t for t in trades if t["ic_quartile"] == q and t["direction"] == d]
        if len(grp) >= 3:  # minimum sample
            avg_pnl = np.mean([t["pnl"] for t in grp])
            combos.append((q, dname, len(grp), avg_pnl, sum(t["pnl"] for t in grp)))

combos.sort(key=lambda x: x[3], reverse=True)
print("Best combos (by avg PnL):")
for q, d, n, avg, total in combos[:3]:
    print(f"  {q} {d}: N={n}, Avg={fmt_usd(avg)}, Total={fmt_usd(total)}")
print("Worst combos (by avg PnL):")
for q, d, n, avg, total in combos[-3:]:
    print(f"  {q} {d}: N={n}, Avg={fmt_usd(avg)}, Total={fmt_usd(total)}")
print()


# ======================================================================
# ANALYSIS 3: PEAK PROFIT vs FINAL PnL
# ======================================================================

print("=" * 80)
print("ANALYSIS 3: PEAK PROFIT vs FINAL PnL")
print("=" * 80)
print()

valid_peak = [t for t in trades if not np.isnan(t["peak_profit_pct"])]
n_valid = len(valid_peak)
print(f"Trades with valid peak data: {n_valid}/{len(trades)}")
print()

# What % achieve peak thresholds
thresholds = [2, 5, 10, 15, 20]
print("--- Peak Profit Achievement ---")
headers = ["Peak >=", "N trades", "% of total", "Avg Final PnL", "Avg Giveback %"]
rows = []
for th in thresholds:
    grp = [t for t in valid_peak if t["peak_profit_pct"] >= th]
    if len(grp) == 0:
        rows.append([f"+{th}%", 0, "0.0%", "N/A", "N/A"])
        continue
    avg_final = np.mean([t["pnl"] for t in grp])
    avg_giveback = np.mean([t["dd_from_peak_pct"] for t in grp])
    rows.append([f"+{th}%", len(grp), fmt_pct(len(grp) / n_valid * 100),
                 fmt_usd(avg_final), fmt_pct(avg_giveback)])
print_table(headers, rows)

# For trades peaking at +10%, what was final outcome?
peaked_10 = [t for t in valid_peak if t["peak_profit_pct"] >= 10]
if peaked_10:
    final_pnls = [t["pnl"] for t in peaked_10]
    print(f"Trades that peaked >= +10% ({len(peaked_10)} trades):")
    print(f"  Final: avg PnL={fmt_usd(np.mean(final_pnls))}, "
          f"WR={fmt_pct(sum(1 for p in final_pnls if p > 0)/len(final_pnls)*100)}")
    print(f"  Avg peak={fmt_pct(np.mean([t['peak_profit_pct'] for t in peaked_10]))}")
    print(f"  Avg giveback={fmt_pct(np.mean([t['dd_from_peak_pct'] for t in peaked_10]))}")
    print(f"  Avg time to peak={np.mean([t['time_to_peak_hrs'] for t in peaked_10]):.0f}h")
print()

# For losers: did they ever have positive MTM?
losers = [t for t in valid_peak if t["pnl"] < 0]
winners = [t for t in valid_peak if t["pnl"] >= 0]
if losers:
    losers_peaked_pos = [t for t in losers if t["peak_profit_pct"] > 0]
    print(f"LOSERS ({len(losers)} trades):")
    print(f"  Had positive peak: {len(losers_peaked_pos)} ({fmt_pct(len(losers_peaked_pos)/len(losers)*100)})")
    if losers_peaked_pos:
        print(f"  Avg peak before reversal: +{np.mean([t['peak_profit_pct'] for t in losers_peaked_pos]):.1f}%")
        print(f"  Avg time to peak: {np.mean([t['time_to_peak_hrs'] for t in losers_peaked_pos]):.0f}h")

if winners:
    print(f"\nWINNERS ({len(winners)} trades):")
    print(f"  Avg peak: +{np.mean([t['peak_profit_pct'] for t in winners]):.1f}%")
    print(f"  Avg time to peak: {np.mean([t['time_to_peak_hrs'] for t in winners]):.0f}h")
    print(f"  Avg giveback: {np.mean([t['dd_from_peak_pct'] for t in winners]):.1f}%")

# Time to peak: winners vs losers
if winners and losers:
    print(f"\nTime to peak comparison:")
    print(f"  Winners: {np.mean([t['time_to_peak_hrs'] for t in winners]):.0f}h (median {np.median([t['time_to_peak_hrs'] for t in winners]):.0f}h)")
    print(f"  Losers:  {np.mean([t['time_to_peak_hrs'] for t in losers]):.0f}h (median {np.median([t['time_to_peak_hrs'] for t in losers]):.0f}h)")
print()


# ======================================================================
# ANALYSIS 4: EXIT REASON x SIGNAL STRENGTH
# ======================================================================

print("=" * 80)
print("ANALYSIS 4: EXIT REASON x SIGNAL STRENGTH")
print("=" * 80)
print()

exit_reasons = sorted(set(t["exit_reason"] for t in trades))
headers = ["Exit Reason", "N", "WR", "Avg PnL", "Total PnL", "Avg Signal", "Avg Hold"]
rows = []
for er in exit_reasons:
    grp = [t for t in trades if t["exit_reason"] == er]
    s = group_stats(grp)
    avg_sig = np.nanmean([t["signal_strength"] for t in grp])
    rows.append([er, s["N"], s["WR"], s["Avg PnL"], s["Total PnL"],
                 fmt_float(avg_sig), s["Avg Hold"]])
print_table(headers, rows)

# Exit reason by conviction bucket
print("--- Exit Reason x Conviction Bucket ---")
headers = ["Conviction", "stop", "max_hold", "regime", "liquidation", "data_end"]
rows = []
for b in ["Low", "Medium", "High"]:
    grp = [t for t in trades if t["conv_bucket"] == b]
    if len(grp) == 0:
        continue
    row = [b]
    for er in ["stop", "max_hold", "regime", "liquidation", "data_end"]:
        cnt = sum(1 for t in grp if t["exit_reason"] == er)
        row.append(f"{cnt} ({fmt_pct(cnt/len(grp)*100)})")
    rows.append(row)
print_table(headers, rows)

# Which exit has best avg PnL?
print("Exit reason ranking by avg PnL:")
exit_stats = []
for er in exit_reasons:
    grp = [t for t in trades if t["exit_reason"] == er]
    if grp:
        exit_stats.append((er, np.mean([t["pnl"] for t in grp]), len(grp)))
exit_stats.sort(key=lambda x: x[1], reverse=True)
for er, avg, n in exit_stats:
    print(f"  {er}: avg={fmt_usd(avg)} (N={n})")
print()


# ======================================================================
# ANALYSIS 5: TOKEN-LEVEL PROFITABILITY
# ======================================================================

print("=" * 80)
print("ANALYSIS 5: TOKEN-LEVEL PROFITABILITY")
print("=" * 80)
print()

token_stats = {}
for t in trades:
    tk = t["token"]
    if tk not in token_stats:
        token_stats[tk] = {"pnls": [], "trades": []}
    token_stats[tk]["pnls"].append(t["pnl"])
    token_stats[tk]["trades"].append(t)

token_summary = []
for tk, data in token_stats.items():
    pnls = data["pnls"]
    n = len(pnls)
    total = sum(pnls)
    wr = sum(1 for p in pnls if p > 0) / n * 100
    ic = token_config.get(tk, {}).get("best_ic", 0)
    tt = token_config.get(tk, {}).get("direction", "unknown")
    token_summary.append({
        "token": tk, "total_pnl": total, "n": n, "wr": wr,
        "avg_pnl": total / n, "ic": ic, "type": tt,
    })

token_summary.sort(key=lambda x: x["total_pnl"], reverse=True)

print("--- Top 10 Most Profitable Tokens ---")
headers = ["Token", "Total PnL", "N", "WR", "Avg PnL", "IC", "Type"]
rows = []
for ts in token_summary[:10]:
    rows.append([ts["token"], fmt_usd(ts["total_pnl"]), ts["n"],
                 fmt_pct(ts["wr"]), fmt_usd(ts["avg_pnl"]),
                 fmt_float(ts["ic"], 3), ts["type"]])
print_table(headers, rows)

print("--- Bottom 10 Most Losing Tokens ---")
rows = []
for ts in token_summary[-10:]:
    rows.append([ts["token"], fmt_usd(ts["total_pnl"]), ts["n"],
                 fmt_pct(ts["wr"]), fmt_usd(ts["avg_pnl"]),
                 fmt_float(ts["ic"], 3), ts["type"]])
print_table(headers, rows)

# Pattern analysis
top10_ics = [ts["ic"] for ts in token_summary[:10] if ts["ic"] > 0]
bot10_ics = [ts["ic"] for ts in token_summary[-10:] if ts["ic"] > 0]
if top10_ics and bot10_ics:
    print(f"IC comparison: Top10 avg={np.mean(top10_ics):.3f}, Bottom10 avg={np.mean(bot10_ics):.3f}")
top10_types = [ts["type"] for ts in token_summary[:10]]
bot10_types = [ts["type"] for ts in token_summary[-10:]]
print(f"Type composition: Top10={dict(pd.Series(top10_types).value_counts())}, "
      f"Bottom10={dict(pd.Series(bot10_types).value_counts())}")
print()


# ======================================================================
# ANALYSIS 6: OPTIMAL HOLDING PROFILE (MTM curves by conviction)
# ======================================================================

print("=" * 80)
print("ANALYSIS 6: OPTIMAL HOLDING PROFILE")
print("=" * 80)
print()

checkpoints = [0, 12, 24, 48, 72, 120, 168, 240, 336, 480, 600, 720]

for bucket in ["Low", "Medium", "High"]:
    grp = [t for t in trades if t["conv_bucket"] == bucket and "mtm_curve" in t and t["mtm_curve"]]
    if len(grp) == 0:
        continue

    print(f"--- {bucket} Conviction (N={len(grp)}) ---")
    headers = ["Hour"] + [str(h) for h in checkpoints]
    avg_row = ["Avg MTM%"]
    med_row = ["Med MTM%"]
    pct_pos = ["% Positive"]
    n_row = ["N available"]

    for h in checkpoints:
        vals = [t["mtm_curve"].get(h) for t in grp if h in t.get("mtm_curve", {})]
        vals = [v for v in vals if v is not None]
        if vals:
            avg_row.append(fmt_pct(np.mean(vals)))
            med_row.append(fmt_pct(np.median(vals)))
            pct_pos.append(fmt_pct(sum(1 for v in vals if v > 0) / len(vals) * 100))
            n_row.append(str(len(vals)))
        else:
            avg_row.append("N/A")
            med_row.append("N/A")
            pct_pos.append("N/A")
            n_row.append("0")

    print_table(headers, [avg_row, med_row, pct_pos, n_row])

# Also by direction
for dir_name, dir_val in [("Long", 1), ("Short", -1)]:
    grp = [t for t in trades if t["direction"] == dir_val and "mtm_curve" in t and t["mtm_curve"]]
    if len(grp) == 0:
        continue
    print(f"--- {dir_name} trades (N={len(grp)}) ---")
    headers = ["Hour"] + [str(h) for h in checkpoints]
    avg_row = ["Avg MTM%"]

    for h in checkpoints:
        vals = [t["mtm_curve"].get(h) for t in grp if h in t.get("mtm_curve", {})]
        vals = [v for v in vals if v is not None]
        if vals:
            avg_row.append(fmt_pct(np.mean(vals)))
        else:
            avg_row.append("N/A")

    print_table(headers, [avg_row])


# ======================================================================
# ANALYSIS 7: REGIME CONTEXT (TOTAL2 at entry)
# ======================================================================

print("=" * 80)
print("ANALYSIS 7: REGIME CONTEXT (TOTAL2 at entry)")
print("=" * 80)
print()

regimes = ["Rising (>+5%)", "Flat (-5% to +5%)", "Falling (<-5%)"]
headers = ["TOTAL2 Regime", "N", "WR", "Avg PnL", "Total PnL", "Avg Hold", "PF"]
rows = []
for regime in regimes:
    grp = [t for t in trades if t["total2_regime"] == regime]
    if len(grp) == 0:
        continue
    s = group_stats(grp)
    rows.append([regime, s["N"], s["WR"], s["Avg PnL"], s["Total PnL"], s["Avg Hold"], s["PF"]])
print_table(headers, rows)

# Regime x Direction
print("--- TOTAL2 Regime x Direction ---")
headers = ["Regime", "Dir", "N", "WR", "Avg PnL", "Total PnL", "PF"]
rows = []
for regime in regimes:
    for d, dname in [(1, "Long"), (-1, "Short")]:
        grp = [t for t in trades if t["total2_regime"] == regime and t["direction"] == d]
        if len(grp) == 0:
            continue
        s = group_stats(grp)
        rows.append([regime, dname, s["N"], s["WR"], s["Avg PnL"], s["Total PnL"], s["PF"]])
print_table(headers, rows)

# TOTAL2 30d return distribution at entry
t2_rets = [t["total2_ret_30d"] for t in trades]
print(f"TOTAL2 30d return at entry: mean={np.mean(t2_rets):.3f}, "
      f"median={np.median(t2_rets):.3f}, std={np.std(t2_rets):.3f}")
print(f"  Range: [{np.min(t2_rets):.3f}, {np.max(t2_rets):.3f}]")
print()


# ======================================================================
# SUMMARY: KEY FINDINGS
# ======================================================================

print("=" * 80)
print("SUMMARY: KEY FINDINGS")
print("=" * 80)
print()

total_pnl = sum(t["pnl"] for t in trades)
total_wins = sum(1 for t in trades if t["pnl"] > 0)
print(f"Total: {len(trades)} trades, WR={fmt_pct(total_wins/len(trades)*100)}, "
      f"Total PnL={fmt_usd(total_pnl)}, PF={fmt_float(profit_factor([t['pnl'] for t in trades]))}")
print()

# Direction split
for d, dname in [(1, "Long"), (-1, "Short")]:
    grp = [t for t in trades if t["direction"] == d]
    s = group_stats(grp)
    print(f"{dname}: N={s['N']}, WR={s['WR']}, Total={s['Total PnL']}, PF={s['PF']}")

print()

# Biggest winners and losers
trades_sorted = sorted(trades, key=lambda x: x["pnl"], reverse=True)
print("Top 5 winners:")
for t in trades_sorted[:5]:
    print(f"  {t['token']} {'L' if t['direction']==1 else 'S'}: "
          f"PnL={fmt_usd(t['pnl'])}, hold={t['hold_hours']}h, "
          f"signal={fmt_float(t.get('signal_strength', 0))}, "
          f"exit={t['exit_reason']}")

print("\nTop 5 losers:")
for t in trades_sorted[-5:]:
    print(f"  {t['token']} {'L' if t['direction']==1 else 'S'}: "
          f"PnL={fmt_usd(t['pnl'])}, hold={t['hold_hours']}h, "
          f"signal={fmt_float(t.get('signal_strength', 0))}, "
          f"exit={t['exit_reason']}")

print()

# Actionable insights
print("--- ACTIONABLE INSIGHTS ---")
print()

# 1. Does higher conviction = better outcome?
conv_avgs = {}
for b in ["Low", "Medium", "High"]:
    grp = [t for t in trades if t["conv_bucket"] == b]
    if grp:
        conv_avgs[b] = np.mean([t["pnl"] for t in grp])

if conv_avgs:
    print("1. CONVICTION vs OUTCOME:")
    for b in ["Low", "Medium", "High"]:
        if b in conv_avgs:
            print(f"   {b}: avg PnL = {fmt_usd(conv_avgs[b])}")
    if "High" in conv_avgs and "Low" in conv_avgs:
        if conv_avgs["High"] > conv_avgs["Low"]:
            print("   => Higher conviction DOES produce better outcomes")
        else:
            print("   => Higher conviction does NOT produce better outcomes (investigate!)")
    print()

# 2. Giveback analysis
if valid_peak:
    avg_giveback = np.mean([t["dd_from_peak_pct"] for t in valid_peak if not np.isnan(t["dd_from_peak_pct"])])
    print(f"2. GIVEBACK: Average trade gives back {avg_giveback:.1f}% from peak")
    winners_gb = [t["dd_from_peak_pct"] for t in winners if not np.isnan(t["dd_from_peak_pct"])]
    if winners_gb:
        print(f"   Winners give back {np.mean(winners_gb):.1f}% on average")
    losers_gb = [t["dd_from_peak_pct"] for t in losers if not np.isnan(t["dd_from_peak_pct"])]
    if losers_gb:
        print(f"   Losers give back {np.mean(losers_gb):.1f}% on average")
    print()

# 3. Stop vs max_hold
stop_trades = [t for t in trades if t["exit_reason"] == "stop"]
maxhold_trades = [t for t in trades if t["exit_reason"] == "max_hold"]
if stop_trades and maxhold_trades:
    print(f"3. STOP vs MAX_HOLD:")
    print(f"   Stop exits: N={len(stop_trades)}, avg PnL={fmt_usd(np.mean([t['pnl'] for t in stop_trades]))}")
    print(f"   Max hold exits: N={len(maxhold_trades)}, avg PnL={fmt_usd(np.mean([t['pnl'] for t in maxhold_trades]))}")
    print()

# 4. Regime insight
for regime in regimes:
    grp = [t for t in trades if t["total2_regime"] == regime]
    if grp:
        avg = np.mean([t["pnl"] for t in grp])
        if avg < 0:
            print(f"4. WARNING: Trades entered during {regime} TOTAL2 regime avg PnL = {fmt_usd(avg)} (N={len(grp)})")

print()
print("=" * 80)
print("END OF ANALYSIS")
print("=" * 80)
