"""
On-Chain + Mempool Signal Discovery
====================================
Compute predictive IC (Spearman) for blockchain_info and mempool_space metrics
against forward BTC returns at 1d, 3d, 7d, 14d horizons.

Kill criteria:
  - |IC| < 0.02 -> KILL
  - |t-stat| < 2.0 -> KILL
  - IS->OOS sign flip -> KILL
"""
import json
import warnings
import numpy as np
import pandas as pd
from datetime import datetime
from scipy.stats import spearmanr

warnings.filterwarnings("ignore")

# ============================================================
# 1. Helper functions
# ============================================================

def compute_ic(signal, forward_ret, min_obs=30):
    """Compute Spearman IC, t-stat, p-value."""
    # Ensure float dtype to avoid scipy type issues with large ints
    sig = signal.astype(float)
    fret = forward_ret.astype(float)
    mask = sig.notna() & fret.notna() & np.isfinite(sig) & np.isfinite(fret)
    n = mask.sum()
    if n < min_obs:
        return np.nan, np.nan, np.nan, int(n)
    ic, pval = spearmanr(sig[mask].values, fret[mask].values)
    if abs(ic) >= 1.0:
        tstat = np.inf * np.sign(ic)
    else:
        tstat = ic * np.sqrt(n - 2) / np.sqrt(1 - ic**2)
    return ic, tstat, pval, int(n)


def verdict(ic_is, tstat_is, ic_oos, tstat_oos):
    """Apply kill criteria. Returns (verdict_str, reason)."""
    reasons = []

    # Check IS first
    if np.isnan(ic_is) or np.isnan(ic_oos):
        return "KILL", "Insufficient data"

    # |IC| < 0.02 in IS
    if abs(ic_is) < 0.02:
        reasons.append(f"|IC_IS|={abs(ic_is):.4f} < 0.02")

    # |t-stat| < 2.0 in IS
    if abs(tstat_is) < 2.0:
        reasons.append(f"|t_IS|={abs(tstat_is):.2f} < 2.0")

    # OOS checks
    if abs(ic_oos) < 0.02:
        reasons.append(f"|IC_OOS|={abs(ic_oos):.4f} < 0.02")

    if abs(tstat_oos) < 2.0:
        reasons.append(f"|t_OOS|={abs(tstat_oos):.2f} < 2.0")

    # Sign flip IS->OOS
    if np.sign(ic_is) != np.sign(ic_oos) and abs(ic_oos) > 0.005:
        reasons.append(f"Sign flip IS({ic_is:+.4f}) -> OOS({ic_oos:+.4f})")

    if reasons:
        return "KILL", "; ".join(reasons)
    return "PASS", f"IC_IS={ic_is:+.4f}, IC_OOS={ic_oos:+.4f}"


# ============================================================
# 2. Load BTC price data and compute daily returns
# ============================================================

print("Loading BTC hourly price data...")
btc_1h = pd.read_parquet("/workspace/crypto_backtest/data/spot/1h_cache/BTC_1h.parquet")
btc_daily = btc_1h["close"].resample("1D").last().dropna()
btc_daily.name = "close"
btc_daily = btc_daily.to_frame()

print(f"BTC daily: {btc_daily.index.min()} to {btc_daily.index.max()}, {len(btc_daily)} days")

# Compute forward returns
horizons = [1, 3, 7, 14]
for h in horizons:
    btc_daily[f"fwd_ret_{h}d"] = btc_daily["close"].shift(-h) / btc_daily["close"] - 1

# Also compute trailing return for regime classification
btc_daily["ret_30d"] = btc_daily["close"] / btc_daily["close"].shift(30) - 1
btc_daily["regime"] = np.where(btc_daily["ret_30d"] > 0, "uptrend", "downtrend")

# ============================================================
# 3. Load and parse Blockchain Info data
# ============================================================

print("\nLoading Blockchain Info data...")
with open("/workspace/crypto_backtest/data/alternative/blockchain_info_btc_1year.json") as f:
    bc_raw = json.load(f)

bc_metrics = {}
bc_descriptions = {}
for key, meta in bc_raw.items():
    name = meta.get("name", key)
    vals = meta.get("values", [])
    if not vals:
        # Some metrics have different structure (e.g., market-cap with timestamps)
        continue

    ts = [datetime.utcfromtimestamp(v["x"]) for v in vals]
    ys = [v["y"] for v in vals]
    s = pd.Series(ys, index=pd.DatetimeIndex(ts), name=key)
    # Resample to daily (take last value per day) in case of intra-day entries
    s = s.resample("1D").last().dropna()
    bc_metrics[key] = s
    bc_descriptions[key] = f"{name} ({meta.get('unit', '?')})"
    print(f"  {key}: {len(s)} daily obs, {s.index.min().date()} to {s.index.max().date()}")

# ============================================================
# 4. Load and parse Mempool Space data
# ============================================================

print("\nLoading Mempool Space data...")
with open("/workspace/crypto_backtest/data/alternative/mempool_space_btc_data.json") as f:
    mp_raw = json.load(f)

mp_metrics = {}
mp_descriptions = {}

# hashrate_1y.hashrates -> daily hashrate
hashrates = mp_raw.get("hashrate_1y", {}).get("hashrates", [])
if hashrates:
    ts = [datetime.utcfromtimestamp(h["timestamp"]) for h in hashrates]
    vals = [h["avgHashrate"] for h in hashrates]
    s = pd.Series(vals, index=pd.DatetimeIndex(ts), name="mp_hashrate")
    s = s.resample("1D").last().dropna()
    mp_metrics["mp_hashrate"] = s
    mp_descriptions["mp_hashrate"] = "Mempool Space Avg Hashrate (H/s)"
    print(f"  mp_hashrate: {len(s)} daily obs, {s.index.min().date()} to {s.index.max().date()}")

# hashrate_1y.difficulty -> difficulty adjustments (sparse, ~2 week intervals)
difficulties = mp_raw.get("hashrate_1y", {}).get("difficulty", [])
if difficulties:
    ts = [datetime.utcfromtimestamp(d["time"]) for d in difficulties]
    diffs = [d["difficulty"] for d in difficulties]
    adjustments = [d.get("adjustment", np.nan) for d in difficulties]

    s_diff = pd.Series(diffs, index=pd.DatetimeIndex(ts), name="mp_difficulty")
    s_diff = s_diff.resample("1D").last().ffill()
    mp_metrics["mp_difficulty"] = s_diff
    mp_descriptions["mp_difficulty"] = "Mining Difficulty (forward-filled)"
    print(f"  mp_difficulty: {len(s_diff)} daily obs")

    s_adj = pd.Series(adjustments, index=pd.DatetimeIndex(ts), name="mp_diff_adjustment")
    s_adj = s_adj.resample("1D").last().ffill()
    mp_metrics["mp_diff_adjustment"] = s_adj
    mp_descriptions["mp_diff_adjustment"] = "Difficulty Adjustment Multiplier (forward-filled)"
    print(f"  mp_diff_adjustment: {len(s_adj)} daily obs")

# mempool_fees -> block-level data (very few obs, mostly recent snapshot)
mempool_fees = mp_raw.get("mempool_fees", [])
if mempool_fees:
    print(f"  mempool_fees: {len(mempool_fees)} block entries (snapshot, not time-series)")
    # This is a snapshot of recent blocks, not a time series.
    # We'll extract what we can but it's likely too few observations.
    # Let's compute cross-sectional stats from the snapshot
    median_fees = [b.get("medianFee", np.nan) for b in mempool_fees]
    n_txs = [b.get("nTx", np.nan) for b in mempool_fees]
    block_vsizes = [b.get("blockVSize", np.nan) for b in mempool_fees]
    print(f"    Snapshot median fees: {median_fees}")
    print(f"    Snapshot nTx: {n_txs}")
    print(f"    NOTE: Only {len(mempool_fees)} blocks in snapshot -- too few for IC analysis")

# ============================================================
# 5. Derived on-chain signals (blockchain_info)
# ============================================================

print("\nComputing derived signals from blockchain_info...")

# NVT-like ratio: market_cap / estimated_tx_volume_usd
if "market-cap" in bc_metrics and "estimated-transaction-volume-usd" in bc_metrics:
    mc = bc_metrics["market-cap"].reindex(bc_metrics["estimated-transaction-volume-usd"].index, method="ffill")
    nvt = mc / bc_metrics["estimated-transaction-volume-usd"].replace(0, np.nan)
    bc_metrics["nvt_ratio"] = nvt
    bc_descriptions["nvt_ratio"] = "NVT Ratio (MarketCap / Est. TX Volume USD)"
    print(f"  nvt_ratio: {nvt.notna().sum()} obs")

# Active address momentum (7d change)
if "n-unique-addresses" in bc_metrics:
    addr = bc_metrics["n-unique-addresses"]
    bc_metrics["active_addr_7d_chg"] = addr / addr.shift(7) - 1
    bc_descriptions["active_addr_7d_chg"] = "Active Address 7d % Change"
    print(f"  active_addr_7d_chg computed")

# Transaction count momentum
if "n-transactions" in bc_metrics:
    txn = bc_metrics["n-transactions"]
    bc_metrics["tx_count_7d_chg"] = txn / txn.shift(7) - 1
    bc_descriptions["tx_count_7d_chg"] = "TX Count 7d % Change"
    print(f"  tx_count_7d_chg computed")

# Fee per transaction (proxy for demand)
if "estimated-transaction-volume-usd" in bc_metrics and "n-transactions" in bc_metrics:
    vol = bc_metrics["estimated-transaction-volume-usd"]
    txn = bc_metrics["n-transactions"]
    # Align
    common = vol.index.intersection(txn.index)
    fee_per_tx = vol.loc[common] / txn.loc[common].replace(0, np.nan)
    bc_metrics["value_per_tx_usd"] = fee_per_tx
    bc_descriptions["value_per_tx_usd"] = "Avg TX Value in USD"
    print(f"  value_per_tx_usd computed")

# Trade volume z-score (30d rolling)
if "trade-volume" in bc_metrics:
    tv = bc_metrics["trade-volume"]
    tv_mean = tv.rolling(30).mean()
    tv_std = tv.rolling(30).std()
    bc_metrics["trade_vol_zscore"] = (tv - tv_mean) / tv_std.replace(0, np.nan)
    bc_descriptions["trade_vol_zscore"] = "Exchange Trade Volume Z-Score (30d)"
    print(f"  trade_vol_zscore computed")

# Hashrate momentum (mempool data)
if "mp_hashrate" in mp_metrics:
    hr = mp_metrics["mp_hashrate"]
    mp_metrics["hashrate_7d_chg"] = hr / hr.shift(7) - 1
    mp_descriptions["hashrate_7d_chg"] = "Hashrate 7d % Change"
    print(f"  hashrate_7d_chg computed")

    mp_metrics["hashrate_30d_chg"] = hr / hr.shift(30) - 1
    mp_descriptions["hashrate_30d_chg"] = "Hashrate 30d % Change"
    print(f"  hashrate_30d_chg computed")

# ============================================================
# 6. Combine all signals and compute IC
# ============================================================

print("\n" + "="*70)
print("COMPUTING IC FOR ALL METRICS")
print("="*70)

all_metrics = {}
all_descriptions = {}
all_metrics.update(bc_metrics)
all_metrics.update(mp_metrics)
all_descriptions.update(bc_descriptions)
all_descriptions.update(mp_descriptions)

# Determine IS/OOS split
# Data range is ~2025-03-25 to 2026-03-16 => midpoint ~2025-09-19
# Use 2025-09-15 as split
IS_OOS_SPLIT = pd.Timestamp("2025-09-15")
print(f"\nIS/OOS Split: {IS_OOS_SPLIT.date()}")

results = []

for metric_name, signal in all_metrics.items():
    desc = all_descriptions.get(metric_name, metric_name)

    # Align with BTC returns
    aligned = pd.DataFrame({"signal": signal}).join(btc_daily, how="inner")

    if len(aligned) < 60:
        print(f"\n{metric_name}: Only {len(aligned)} aligned obs -- skipping")
        continue

    is_mask = aligned.index < IS_OOS_SPLIT
    oos_mask = aligned.index >= IS_OOS_SPLIT

    print(f"\n--- {metric_name} ({desc}) ---")
    print(f"  Aligned: {len(aligned)} obs | IS: {is_mask.sum()} | OOS: {oos_mask.sum()}")

    for h in horizons:
        ret_col = f"fwd_ret_{h}d"

        # Full sample
        ic_full, tstat_full, pval_full, n_full = compute_ic(
            aligned["signal"], aligned[ret_col]
        )

        # IS
        ic_is, tstat_is, pval_is, n_is = compute_ic(
            aligned.loc[is_mask, "signal"], aligned.loc[is_mask, ret_col]
        )

        # OOS
        ic_oos, tstat_oos, pval_oos, n_oos = compute_ic(
            aligned.loc[oos_mask, "signal"], aligned.loc[oos_mask, ret_col]
        )

        v, reason = verdict(ic_is, tstat_is, ic_oos, tstat_oos)

        results.append({
            "metric": metric_name,
            "description": desc,
            "horizon": f"{h}d",
            "ic_full": ic_full,
            "tstat_full": tstat_full,
            "pval_full": pval_full,
            "n_full": n_full,
            "ic_is": ic_is,
            "tstat_is": tstat_is,
            "n_is": n_is,
            "ic_oos": ic_oos,
            "tstat_oos": tstat_oos,
            "n_oos": n_oos,
            "verdict": v,
            "reason": reason,
        })

        print(f"  {h}d: IC_full={ic_full:+.4f} t={tstat_full:+.2f} | "
              f"IC_IS={ic_is:+.4f} t={tstat_is:+.2f} | "
              f"IC_OOS={ic_oos:+.4f} t={tstat_oos:+.2f} | {v}")

# ============================================================
# 7. Regime analysis for any PASS signals
# ============================================================

print("\n" + "="*70)
print("REGIME ANALYSIS FOR PASS SIGNALS")
print("="*70)

pass_signals = set()
for r in results:
    if r["verdict"] == "PASS":
        pass_signals.add(r["metric"])

regime_results = []

if not pass_signals:
    print("\nNo PASS signals found. Checking for borderline cases (|IC| >= 0.04)...")
    # Find borderline for regime analysis anyway
    for r in results:
        if abs(r.get("ic_full", 0)) >= 0.04:
            pass_signals.add(r["metric"])
    if pass_signals:
        print(f"  Analyzing borderline signals: {pass_signals}")
    else:
        print("  No borderline signals either.")

for metric_name in pass_signals:
    signal = all_metrics[metric_name]
    desc = all_descriptions.get(metric_name, metric_name)

    aligned = pd.DataFrame({"signal": signal}).join(btc_daily, how="inner")

    uptrend = aligned[aligned["regime"] == "uptrend"]
    downtrend = aligned[aligned["regime"] == "downtrend"]

    print(f"\n--- {metric_name} ({desc}) ---")
    print(f"  Uptrend days: {len(uptrend)} | Downtrend days: {len(downtrend)}")

    for h in horizons:
        ret_col = f"fwd_ret_{h}d"

        ic_up, t_up, _, n_up = compute_ic(uptrend["signal"], uptrend[ret_col])
        ic_dn, t_dn, _, n_dn = compute_ic(downtrend["signal"], downtrend[ret_col])

        regime_results.append({
            "metric": metric_name,
            "horizon": f"{h}d",
            "ic_uptrend": ic_up,
            "tstat_uptrend": t_up,
            "n_uptrend": n_up,
            "ic_downtrend": ic_dn,
            "tstat_downtrend": t_dn,
            "n_downtrend": n_dn,
        })

        print(f"  {h}d: Uptrend IC={ic_up:+.4f} t={t_up:+.2f} (n={n_up}) | "
              f"Downtrend IC={ic_dn:+.4f} t={t_dn:+.2f} (n={n_dn})")

# ============================================================
# 8. Generate markdown report
# ============================================================

print("\n\nGenerating report...")

df_results = pd.DataFrame(results)
df_regime = pd.DataFrame(regime_results) if regime_results else pd.DataFrame()

# Build markdown
lines = []
lines.append("# On-Chain + Mempool Signal Discovery Results")
lines.append("")
lines.append("## 1. Data Sources")
lines.append("")
lines.append("### 1.1 Blockchain Info (blockchain_info_btc_1year.json)")
lines.append("")
lines.append("| Metric Key | Description | Obs | Date Range |")
lines.append("|---|---|---|---|")
for key, s in bc_metrics.items():
    desc = bc_descriptions.get(key, key)
    lines.append(f"| `{key}` | {desc} | {len(s)} | {s.index.min().date()} to {s.index.max().date()} |")

lines.append("")
lines.append("**Raw metrics:** estimated-transaction-volume-usd, n-transactions, market-cap, total-bitcoins, n-unique-addresses, n-transactions-per-block, estimated-transaction-volume, output-volume, trade-volume")
lines.append("")
lines.append("**Derived signals:** nvt_ratio, active_addr_7d_chg, tx_count_7d_chg, value_per_tx_usd, trade_vol_zscore")
lines.append("")

lines.append("### 1.2 Mempool Space (mempool_space_btc_data.json)")
lines.append("")
lines.append("| Metric Key | Description | Obs | Date Range |")
lines.append("|---|---|---|---|")
for key, s in mp_metrics.items():
    desc = mp_descriptions.get(key, key)
    lines.append(f"| `{key}` | {desc} | {len(s)} | {s.index.min().date()} to {s.index.max().date()} |")

lines.append("")
lines.append("**Note:** `mempool_fees` contains only a snapshot of the most recent 8 blocks -- not a time series. Too few observations for IC analysis.")
lines.append("")
lines.append("**Derived signals:** hashrate_7d_chg, hashrate_30d_chg")
lines.append("")

lines.append("### 1.3 BTC Price Data")
lines.append("")
lines.append(f"- Source: `data/spot/1h_cache/BTC_1h.parquet`, resampled to daily close")
lines.append(f"- Date range: {btc_daily.index.min().date()} to {btc_daily.index.max().date()}")
lines.append(f"- Forward return horizons: 1d, 3d, 7d, 14d")
lines.append(f"- IS/OOS split: {IS_OOS_SPLIT.date()}")
lines.append("")

lines.append("## 2. IC Results (Full Table)")
lines.append("")
lines.append("| Metric | Horizon | IC_Full | t_Full | IC_IS | t_IS | n_IS | IC_OOS | t_OOS | n_OOS | Verdict | Reason |")
lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
for _, r in df_results.iterrows():
    ic_f = f"{r['ic_full']:+.4f}" if not np.isnan(r['ic_full']) else "NaN"
    t_f = f"{r['tstat_full']:+.2f}" if not np.isnan(r['tstat_full']) else "NaN"
    ic_i = f"{r['ic_is']:+.4f}" if not np.isnan(r['ic_is']) else "NaN"
    t_i = f"{r['tstat_is']:+.2f}" if not np.isnan(r['tstat_is']) else "NaN"
    ic_o = f"{r['ic_oos']:+.4f}" if not np.isnan(r['ic_oos']) else "NaN"
    t_o = f"{r['tstat_oos']:+.2f}" if not np.isnan(r['tstat_oos']) else "NaN"
    lines.append(
        f"| `{r['metric']}` | {r['horizon']} | {ic_f} | {t_f} | "
        f"{ic_i} | {t_i} | {r['n_is']} | {ic_o} | {t_o} | {r['n_oos']} | "
        f"**{r['verdict']}** | {r['reason']} |"
    )

lines.append("")

# Summary by verdict
pass_count = (df_results["verdict"] == "PASS").sum()
kill_count = (df_results["verdict"] == "KILL").sum()
lines.append(f"**Summary:** {pass_count} PASS / {kill_count} KILL out of {len(df_results)} metric-horizon combinations")
lines.append("")

# Best performers table (sorted by |IC_full|)
lines.append("### 2.1 Top 10 by |IC_Full|")
lines.append("")
df_results["abs_ic_full"] = df_results["ic_full"].abs()
top10 = df_results.nlargest(10, "abs_ic_full")
lines.append("| Metric | Horizon | IC_Full | t_Full | IC_IS | IC_OOS | Verdict |")
lines.append("|---|---|---|---|---|---|---|")
for _, r in top10.iterrows():
    lines.append(
        f"| `{r['metric']}` | {r['horizon']} | {r['ic_full']:+.4f} | {r['tstat_full']:+.2f} | "
        f"{r['ic_is']:+.4f} | {r['ic_oos']:+.4f} | **{r['verdict']}** |"
    )
lines.append("")

# Verdict summary per metric (across all horizons)
lines.append("### 2.2 Verdict Summary Per Metric")
lines.append("")
lines.append("| Metric | Description | Horizons PASS | Horizons KILL | Best IC | Best Horizon |")
lines.append("|---|---|---|---|---|---|")
for metric_name in all_metrics.keys():
    sub = df_results[df_results["metric"] == metric_name]
    if len(sub) == 0:
        continue
    n_pass = (sub["verdict"] == "PASS").sum()
    n_kill = (sub["verdict"] == "KILL").sum()
    best_row = sub.loc[sub["abs_ic_full"].idxmax()]
    desc = all_descriptions.get(metric_name, metric_name)
    lines.append(
        f"| `{metric_name}` | {desc} | {n_pass} | {n_kill} | "
        f"{best_row['ic_full']:+.4f} | {best_row['horizon']} |"
    )
lines.append("")

# Regime analysis
lines.append("## 3. Regime Analysis")
lines.append("")
if len(df_regime) > 0:
    signals_analyzed = pass_signals
    lines.append(f"Analyzed signals: {', '.join(f'`{s}`' for s in signals_analyzed)}")
    lines.append("")
    lines.append("Regime defined by trailing 30d BTC return: >0 = uptrend, <=0 = downtrend")
    lines.append("")
    lines.append("| Metric | Horizon | IC_Uptrend | t_Uptrend | n_Up | IC_Downtrend | t_Downtrend | n_Down |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for _, r in df_regime.iterrows():
        ic_u = f"{r['ic_uptrend']:+.4f}" if not np.isnan(r['ic_uptrend']) else "NaN"
        t_u = f"{r['tstat_uptrend']:+.2f}" if not np.isnan(r['tstat_uptrend']) else "NaN"
        ic_d = f"{r['ic_downtrend']:+.4f}" if not np.isnan(r['ic_downtrend']) else "NaN"
        t_d = f"{r['tstat_downtrend']:+.2f}" if not np.isnan(r['tstat_downtrend']) else "NaN"
        lines.append(
            f"| `{r['metric']}` | {r['horizon']} | {ic_u} | {t_u} | {r['n_uptrend']} | "
            f"{ic_d} | {t_d} | {r['n_downtrend']} |"
        )
    lines.append("")
else:
    lines.append("No signals qualified for regime analysis.")
    lines.append("")

# Kill criteria explanation
lines.append("## 4. Kill Criteria Applied")
lines.append("")
lines.append("| Criterion | Threshold | Description |")
lines.append("|---|---|---|")
lines.append("| Low IC | abs(IC) < 0.02 | Signal has negligible rank correlation with forward returns |")
lines.append("| Low t-stat | abs(t) < 2.0 | IC not statistically significant at ~95% confidence |")
lines.append("| Sign flip | IS sign != OOS sign | Signal direction reverses out-of-sample (unreliable) |")
lines.append("")

# Conclusions
lines.append("## 5. Conclusions")
lines.append("")
if pass_count > 0:
    pass_metrics = df_results[df_results["verdict"] == "PASS"]["metric"].unique()
    lines.append(f"**{pass_count} metric-horizon combinations passed kill criteria.**")
    lines.append("")
    lines.append("Passing metrics:")
    for m in pass_metrics:
        sub = df_results[(df_results["metric"] == m) & (df_results["verdict"] == "PASS")]
        horizons_pass = ", ".join(sub["horizon"].tolist())
        best = sub.loc[sub["abs_ic_full"].idxmax()]
        lines.append(f"- `{m}`: PASS at {horizons_pass} (best IC={best['ic_full']:+.4f} at {best['horizon']})")
    lines.append("")
else:
    lines.append("**No metric-horizon combinations passed all kill criteria.**")
    lines.append("")
    lines.append("Key observations:")

    # Find the closest-to-passing
    df_sorted = df_results.copy()
    df_sorted["abs_ic"] = df_sorted["ic_full"].abs()
    best = df_sorted.nlargest(5, "abs_ic")
    lines.append("")
    lines.append("Closest to passing (by |IC|):")
    for _, r in best.iterrows():
        lines.append(f"- `{r['metric']}` @ {r['horizon']}: IC_full={r['ic_full']:+.4f}, "
                     f"IC_IS={r['ic_is']:+.4f}, IC_OOS={r['ic_oos']:+.4f} -- {r['reason']}")
    lines.append("")

lines.append("### Limitations")
lines.append("")
lines.append("1. **Short sample:** On-chain data covers ~1 year (2025-03-25 to 2026-03-16), yielding ~170 IS + ~180 OOS daily observations. This is marginal for robust IC estimation.")
lines.append("2. **Mempool fees:** Only a block-level snapshot (8 blocks) was available, not historical time series. Fee pressure signals could not be tested.")
lines.append("3. **Single asset:** All analysis is BTC-only. Cross-sectional IC (across multiple assets) would be a stronger test.")
lines.append("4. **No transaction costs:** IC measures predictive rank correlation only, not tradeable alpha after costs.")
lines.append("")

report = "\n".join(lines)

with open("/workspace/crypto_backtest/research/onchain_mempool_signal_results.md", "w") as f:
    f.write(report)

print("\nReport written to: /workspace/crypto_backtest/research/onchain_mempool_signal_results.md")
print("Done.")
