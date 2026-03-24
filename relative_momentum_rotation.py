"""
Relative Momentum Rotation — Cross-Sectional Signal Analysis
=============================================================
Hypothesis: Tokens gaining relative strength (rising in cross-sectional rank)
continue to outperform. Unlike absolute momentum, relative momentum is naturally
mean-zero and may persist.

Signals:
  A) Rank Acceleration: 24h rank minus 168h rank > 0.3
  B) Breadth-Filtered Momentum: ret_24h_rank > 0.8 when market_breadth > 0.5
  C) Momentum + Low BTC Correlation: ret_24h_rank > 0.7, rolling_corr_btc < 0.5
  D) Volume-Confirmed Relative Strength: ret_24h_rank > 0.7, volume_rank > 0.7
"""

import pandas as pd
import numpy as np
import warnings
from pathlib import Path
from collections import defaultdict

warnings.filterwarnings("ignore")

# ============================================================
# STEP 1: Load data — top 30 tokens by file size
# ============================================================
print("=" * 80)
print("STEP 1: Loading Data")
print("=" * 80)

data_dir = Path("/workspace/crypto_backtest/data/perp/1h_cache/")
parquet_files = sorted(data_dir.glob("*.parquet"), key=lambda f: f.stat().st_size, reverse=True)
top30_files = parquet_files[:30]

tokens = {}
for f in top30_files:
    symbol = f.stem.replace("_1h", "")
    df = pd.read_parquet(f, columns=["close", "volume"])
    df.index = pd.to_datetime(df.index)
    tokens[symbol] = df

print(f"Loaded {len(tokens)} tokens: {sorted(tokens.keys())}")

# Build aligned close and volume panels
close_dict = {}
volume_dict = {}
for sym, df in tokens.items():
    close_dict[sym] = df["close"]
    volume_dict[sym] = df["volume"]

close_panel = pd.DataFrame(close_dict)
volume_panel = pd.DataFrame(volume_dict)

# Require at least 15 tokens present at each timestamp
min_tokens = 15
valid_mask = close_panel.notna().sum(axis=1) >= min_tokens
close_panel = close_panel.loc[valid_mask]
volume_panel = volume_panel.loc[valid_mask]

print(f"Panel shape: {close_panel.shape}")
print(f"Date range: {close_panel.index.min()} to {close_panel.index.max()}")
print(f"Tokens with full coverage: {close_panel.notna().all().sum()}")

# ============================================================
# STEP 2: Construct signals
# ============================================================
print("\n" + "=" * 80)
print("STEP 2: Constructing Relative Momentum Signals")
print("=" * 80)

# --- Returns ---
ret_1h = close_panel.pct_change(1)
ret_24h = close_panel.pct_change(24)
ret_168h = close_panel.pct_change(168)

# Forward 24h return (the target we predict)
fwd_24h = close_panel.pct_change(24).shift(-24)

# --- Cross-sectional percentile ranks (0 = worst, 1 = best) ---
ret_24h_rank = ret_24h.rank(axis=1, pct=True)
ret_168h_rank = ret_168h.rank(axis=1, pct=True)

# --- Volume rank ---
# Use 24h rolling volume sum for smoothness
vol_24h = volume_panel.rolling(24, min_periods=12).sum()
vol_rank = vol_24h.rank(axis=1, pct=True)

# --- Market breadth: fraction of tokens with positive 24h return ---
market_breadth = (ret_24h > 0).sum(axis=1) / ret_24h.notna().sum(axis=1)

# --- Rolling correlation to BTC ---
btc_ret_1h = ret_1h["BTC"]
rolling_corr_btc = pd.DataFrame(index=close_panel.index, columns=close_panel.columns, dtype=float)
for sym in close_panel.columns:
    if sym == "BTC":
        rolling_corr_btc[sym] = 1.0
    else:
        rolling_corr_btc[sym] = ret_1h[sym].rolling(168, min_periods=84).corr(btc_ret_1h)

print("Computed: ret_24h_rank, ret_168h_rank, vol_rank, market_breadth, rolling_corr_btc")

# --- Signal Definitions ---
# Signal A: Rank Acceleration > 0.3
rank_acceleration = ret_24h_rank - ret_168h_rank
signal_A = rank_acceleration > 0.3

# Signal B: Breadth-Filtered Momentum
signal_B = (ret_24h_rank > 0.8) & (market_breadth > 0.5).values[:, None]

# Signal C: Momentum + Low BTC Correlation
signal_C = (ret_24h_rank > 0.7) & (rolling_corr_btc < 0.5)

# Signal D: Volume-Confirmed Relative Strength
signal_D = (ret_24h_rank > 0.7) & (vol_rank > 0.7)

signals = {
    "A_RankAccel": signal_A,
    "B_BreadthMom": signal_B,
    "C_LowCorrMom": signal_C,
    "D_VolConfirm": signal_D,
}

for name, sig in signals.items():
    fire_count = sig.sum().sum()
    print(f"  {name}: {fire_count:,} total signal fires")

# ============================================================
# STEP 3: OOS Validation
# ============================================================
print("\n" + "=" * 80)
print("STEP 3: Out-of-Sample Validation")
print("=" * 80)

oos_start = pd.Timestamp("2025-07-01")
is_oos = close_panel.index >= oos_start

# Base rates (OOS only)
fwd_24h_oos = fwd_24h.loc[is_oos]
all_fwd_flat = fwd_24h_oos.values.flatten()
all_fwd_flat = all_fwd_flat[~np.isnan(all_fwd_flat)]
base_rate = np.mean(all_fwd_flat) * 100  # in pct

# Top quintile base rate
ret_24h_rank_oos = ret_24h_rank.loc[is_oos]
top_quintile_mask = ret_24h_rank_oos > 0.8
top_q_fwd = fwd_24h_oos.where(top_quintile_mask).values.flatten()
top_q_fwd = top_q_fwd[~np.isnan(top_q_fwd)]
top_q_base = np.mean(top_q_fwd) * 100

print(f"Base rate (all tokens, OOS): {base_rate:+.4f}%")
print(f"Top quintile base rate (OOS): {top_q_base:+.4f}%")
print(f"OOS period: {close_panel.index[is_oos].min()} to {close_panel.index[is_oos].max()}")
print(f"OOS hours: {is_oos.sum()}")

results = {}
print(f"\n{'Signal':<20} {'Avg Fwd 24h%':>14} {'Excess vs Base':>16} {'Excess vs TopQ':>16} {'Count':>8} {'Verdict':>10}")
print("-" * 90)

for name, sig in signals.items():
    sig_oos = sig.loc[is_oos]
    selected_fwd = fwd_24h_oos.where(sig_oos).values.flatten()
    selected_fwd = selected_fwd[~np.isnan(selected_fwd)]

    if len(selected_fwd) == 0:
        print(f"{name:<20} {'N/A':>14} {'N/A':>16} {'N/A':>16} {0:>8}")
        continue

    avg_ret = np.mean(selected_fwd) * 100
    excess_base = avg_ret - base_rate
    excess_topq = avg_ret - top_q_base
    count = len(selected_fwd)

    # Verdict
    if excess_base > 0.30 and count > 200:
        verdict = "EDGE"
    elif excess_base > 0.10 and count > 200:
        verdict = "MARGINAL"
    else:
        verdict = "NO EDGE"

    results[name] = {
        "avg_fwd_24h_pct": avg_ret,
        "excess_vs_base": excess_base,
        "excess_vs_topq": excess_topq,
        "count": count,
        "verdict": verdict,
    }

    print(f"{name:<20} {avg_ret:>+14.4f}% {excess_base:>+15.4f}% {excess_topq:>+15.4f}% {count:>8,} {verdict:>10}")

# ============================================================
# STEP 4: Monthly Stability (OOS only)
# ============================================================
print("\n" + "=" * 80)
print("STEP 4: Monthly Stability Analysis (OOS)")
print("=" * 80)

# Add month column
oos_idx = close_panel.index[is_oos]
month_series = oos_idx.to_period("M")

for name, sig in signals.items():
    print(f"\n--- {name} ---")
    sig_oos = sig.loc[is_oos]
    fwd_oos = fwd_24h.loc[is_oos]

    # Get month for each OOS timestamp
    months = close_panel.index[is_oos].to_period("M")

    month_stats = []
    for m in sorted(months.unique()):
        m_mask = months == m
        sig_m = sig_oos.loc[m_mask]
        fwd_m = fwd_oos.loc[m_mask]

        # Signal returns this month
        sel = fwd_m.where(sig_m).values.flatten()
        sel = sel[~np.isnan(sel)]

        # Base rate this month
        all_m = fwd_m.values.flatten()
        all_m = all_m[~np.isnan(all_m)]

        if len(sel) > 0 and len(all_m) > 0:
            sig_ret = np.mean(sel) * 100
            base_m = np.mean(all_m) * 100
            excess_m = sig_ret - base_m
            month_stats.append({
                "month": str(m),
                "sig_ret": sig_ret,
                "base_ret": base_m,
                "excess": excess_m,
                "count": len(sel),
            })

    if month_stats:
        print(f"  {'Month':<10} {'Sig Ret%':>10} {'Base Ret%':>11} {'Excess%':>10} {'Count':>8}")
        print(f"  {'-'*55}")
        positive_months = 0
        for ms in month_stats:
            flag = " *" if ms["excess"] > 0 else ""
            if ms["excess"] > 0:
                positive_months += 1
            print(f"  {ms['month']:<10} {ms['sig_ret']:>+10.4f} {ms['base_ret']:>+11.4f} {ms['excess']:>+10.4f} {ms['count']:>8,}{flag}")
        total_months = len(month_stats)
        print(f"  Positive excess months: {positive_months}/{total_months} ({positive_months/total_months*100:.0f}%)")
    else:
        print("  No data")

# ============================================================
# STEP 5: Turnover Analysis
# ============================================================
print("\n" + "=" * 80)
print("STEP 5: Turnover Analysis")
print("=" * 80)

for name, sig in signals.items():
    sig_oos = sig.loc[is_oos]

    # Signals per day per token
    total_fires = sig_oos.sum().sum()
    oos_days = (sig_oos.index.max() - sig_oos.index.min()).days
    n_tokens = sig_oos.shape[1]
    fires_per_day = total_fires / max(oos_days, 1)
    fires_per_day_per_token = fires_per_day / n_tokens

    # Average number of tokens selected at each timestamp
    tokens_per_ts = sig_oos.sum(axis=1)
    avg_tokens_selected = tokens_per_ts.mean()

    # Holding overlap: fraction of positions that persist from t to t+1
    sig_bool = sig_oos.astype(float)
    overlap = (sig_bool.shift(1) * sig_bool).sum(axis=1)
    current = sig_bool.sum(axis=1)
    # Avoid division by zero
    overlap_frac = overlap / current.replace(0, np.nan)
    avg_overlap = overlap_frac.dropna().mean()

    # Signal frequency bucket
    if fires_per_day_per_token < 0.05:
        freq_label = "INFREQUENT"
    elif fires_per_day_per_token < 0.3:
        freq_label = "MODERATE"
    else:
        freq_label = "FREQUENT"

    print(f"\n--- {name} ---")
    print(f"  Total OOS fires:          {total_fires:,}")
    print(f"  OOS days:                 {oos_days}")
    print(f"  Fires/day (all tokens):   {fires_per_day:.1f}")
    print(f"  Fires/day/token:          {fires_per_day_per_token:.3f}")
    print(f"  Avg tokens selected/bar:  {avg_tokens_selected:.1f}")
    print(f"  Avg holding overlap (t→t+1): {avg_overlap:.1%}")
    print(f"  Frequency assessment:     {freq_label}")
    if avg_overlap > 0.85:
        print(f"  Tradeable: YES (high overlap = low churn)")
    elif avg_overlap > 0.60:
        print(f"  Tradeable: MODERATE (some churn)")
    else:
        print(f"  Tradeable: QUESTIONABLE (high churn)")

# ============================================================
# FINAL SUMMARY
# ============================================================
print("\n" + "=" * 80)
print("FINAL SUMMARY — Relative Momentum Rotation Signals")
print("=" * 80)

print(f"\nKill metric: excess return > +0.30% per trade with >200 occurrences OOS")
print(f"Base rate (all tokens, OOS): {base_rate:+.4f}%")
print(f"Top quintile base rate (OOS): {top_q_base:+.4f}%\n")

print(f"{'Signal':<22} {'Description':<40} {'Excess%':>10} {'Count':>8} {'PosMonth%':>10} {'Verdict':>10}")
print("-" * 105)

signal_descriptions = {
    "A_RankAccel": "24h rank - 168h rank > 0.3",
    "B_BreadthMom": "24h rank>0.8 & breadth>0.5",
    "C_LowCorrMom": "24h rank>0.7 & BTC corr<0.5",
    "D_VolConfirm": "24h rank>0.7 & vol rank>0.7",
}

for name in signals:
    if name in results:
        r = results[name]
        desc = signal_descriptions.get(name, "")
        # Calculate positive month percentage from stored data
        sig = signals[name]
        sig_oos = sig.loc[is_oos]
        fwd_oos = fwd_24h.loc[is_oos]
        months = close_panel.index[is_oos].to_period("M")
        pos_months = 0
        tot_months = 0
        for m in sorted(months.unique()):
            m_mask = months == m
            sig_m = sig_oos.loc[m_mask]
            fwd_m = fwd_oos.loc[m_mask]
            sel = fwd_m.where(sig_m).values.flatten()
            sel = sel[~np.isnan(sel)]
            all_m = fwd_m.values.flatten()
            all_m = all_m[~np.isnan(all_m)]
            if len(sel) > 0 and len(all_m) > 0:
                tot_months += 1
                if np.mean(sel) > np.mean(all_m):
                    pos_months += 1

        pm_pct = f"{pos_months}/{tot_months}" if tot_months > 0 else "N/A"
        print(f"{name:<22} {desc:<40} {r['excess_vs_base']:>+10.4f} {r['count']:>8,} {pm_pct:>10} {r['verdict']:>10}")

print("\n" + "=" * 80)
print("INTERPRETATION")
print("=" * 80)
for name, r in results.items():
    v = r["verdict"]
    ex = r["excess_vs_base"]
    c = r["count"]
    if v == "EDGE":
        print(f"  {name}: EDGE confirmed. +{ex:.4f}% excess on {c:,} trades. Actionable signal.")
    elif v == "MARGINAL":
        print(f"  {name}: MARGINAL. +{ex:.4f}% excess on {c:,} trades. Needs refinement or combination.")
    else:
        print(f"  {name}: NO EDGE. {ex:+.4f}% excess on {c:,} trades. Does not meet kill threshold.")
