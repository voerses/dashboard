"""
ETF Flows + Hashrate Regime Analysis
=====================================
Compute regime signals from Bitcoin ETF flows, hashrate, and price data.
Analyze predictive power for strategy gating.
"""

import pandas as pd
import numpy as np
from pathlib import Path

DATA = Path("/workspace/crypto_backtest/data")
pd.set_option("display.max_columns", 20)
pd.set_option("display.width", 200)
pd.set_option("display.float_format", lambda x: f"{x:,.2f}")

# ─── Load Data ────────────────────────────────────────────────────────────────

print("=" * 80)
print("LOADING DATA")
print("=" * 80)

# ETF flows
etf = pd.read_parquet(DATA / "alternative/etf_flows/btc_etf_daily.parquet")
etf["date"] = pd.to_datetime(etf["date"])
etf = etf.sort_values("date").set_index("date")
print(f"ETF flows: {etf.index.min().date()} to {etf.index.max().date()}, {len(etf)} rows")

# BTC price (hourly -> daily)
btc_h = pd.read_csv(DATA / "perp/binance/1h_ohlcv/BTC_perp_1h.csv")
btc_h["datetime"] = pd.to_datetime(btc_h["datetime"])
btc_h = btc_h.set_index("datetime").sort_index()
btc_daily = btc_h.resample("1D").agg(
    {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
).dropna()
btc_daily.index = btc_daily.index.tz_localize(None)
print(f"BTC daily: {btc_daily.index.min().date()} to {btc_daily.index.max().date()}, {len(btc_daily)} rows")

# Hashrate
hr = pd.read_parquet(DATA / "alternative/btc_hashrate.parquet")
hr.index = pd.to_datetime(hr.index)
# Hashrate comes ~every 4 days, resample to daily with ffill
hr_daily = hr.resample("1D").mean().ffill()
print(f"Hashrate: {hr_daily.index.min().date()} to {hr_daily.index.max().date()}, {len(hr_daily)} rows")

# TOTAL2
total2 = pd.read_parquet(DATA / "alternative/total2_total3.parquet")
total2.index = pd.to_datetime(total2.index).tz_localize(None)
print(f"TOTAL2: {total2.index.min().date()} to {total2.index.max().date()}, {len(total2)} rows")

# ─── Merge into daily panel ──────────────────────────────────────────────────

# Common date range
start = max(etf.index.min(), btc_daily.index.min(), hr_daily.index.min())
end = min(etf.index.max(), btc_daily.index.max())
print(f"\nCommon range: {start.date()} to {end.date()}")

# Create daily index
idx = pd.date_range(start, end, freq="D")
df = pd.DataFrame(index=idx)
df.index.name = "date"

df["btc_close"] = btc_daily["close"].reindex(idx).ffill()
df["btc_volume"] = btc_daily["volume"].reindex(idx).ffill()
df["etf_flow"] = etf["total_inflow_mm"].reindex(idx).fillna(0)
df["hashrate"] = hr_daily["hashrate"].reindex(idx).ffill()
df["total2_close"] = total2["total2_close"].reindex(idx).ffill()

df = df.dropna(subset=["btc_close"])
print(f"Merged panel: {len(df)} days")

# ─── A) ETF Flow Regime ──────────────────────────────────────────────────────

print("\n" + "=" * 80)
print("A) ETF FLOW REGIME")
print("=" * 80)

df["flow_30d"] = df["etf_flow"].rolling(30).sum()
df["flow_7d"] = df["etf_flow"].rolling(7).sum()

def etf_regime(flow_30d):
    if pd.isna(flow_30d):
        return "NEUTRAL"
    if flow_30d > 500:
        return "INFLOW"
    elif flow_30d < -500:
        return "OUTFLOW"
    return "NEUTRAL"

df["etf_regime"] = df["flow_30d"].apply(etf_regime)

# Cumulative flow from peak
df["cum_flow"] = df["etf_flow"].cumsum()
df["cum_flow_peak"] = df["cum_flow"].cummax()
df["flow_from_peak"] = df["cum_flow"] - df["cum_flow_peak"]

# Flow momentum
df["flow_momentum"] = np.where(
    df["flow_7d"] > df["flow_30d"] / (30 / 7),
    "ACCELERATING", "DECELERATING"
)

# Print regime breakdown
regime_counts = df["etf_regime"].value_counts()
print("\nRegime day counts:")
print(regime_counts)

print("\nRecent ETF regime:")
recent = df[["btc_close", "etf_flow", "flow_30d", "etf_regime", "flow_from_peak", "flow_momentum"]].tail(30)
print(recent.to_string())

# ─── B) Institutional Cost Basis Proxy ───────────────────────────────────────

print("\n" + "=" * 80)
print("B) INSTITUTIONAL COST BASIS (VWAP of ETF Inflow Days)")
print("=" * 80)

# Only inflow days
inflow_mask = df["etf_flow"] > 0
df["inflow_price_x_vol"] = np.where(inflow_mask, df["btc_close"] * df["etf_flow"], 0)
df["inflow_vol"] = np.where(inflow_mask, df["etf_flow"], 0)

# Cumulative VWAP
cum_pxv = df["inflow_price_x_vol"].cumsum()
cum_vol = df["inflow_vol"].cumsum()
df["inst_vwap"] = np.where(cum_vol > 0, cum_pxv / cum_vol, np.nan)

# Institutional P&L
df["inst_pnl_pct"] = (df["btc_close"] / df["inst_vwap"] - 1) * 100
df["inst_status"] = np.where(df["inst_pnl_pct"] > 0, "IN_PROFIT", "UNDERWATER")

print(f"\nCurrent institutional VWAP: ${df['inst_vwap'].iloc[-1]:,.0f}")
print(f"Current BTC price: ${df['btc_close'].iloc[-1]:,.0f}")
print(f"Institutional P&L: {df['inst_pnl_pct'].iloc[-1]:+.1f}%")
print(f"Status: {df['inst_status'].iloc[-1]}")

# Rolling VWAP (last 90 inflow days)
print("\nInstitutional VWAP evolution (monthly):")
monthly_vwap = df[["btc_close", "inst_vwap", "inst_pnl_pct", "inst_status"]].resample("ME").last().dropna()
print(monthly_vwap.to_string())

# ─── C) Hashrate Regime ──────────────────────────────────────────────────────

print("\n" + "=" * 80)
print("C) HASHRATE REGIME")
print("=" * 80)

df["hr_30d"] = df["hashrate"].rolling(30).mean()
df["hr_90d"] = df["hashrate"].rolling(90).mean()
df["hr_vs_90d"] = df["hashrate"] / df["hr_90d"] - 1

# Block reward (post April 2024 halving = 3.125 BTC)
df["block_reward"] = np.where(df.index >= "2024-04-20", 3.125, 6.25)
# Hash price proxy: daily revenue per TH/s
# blocks per day ~144, so daily BTC emission = 144 * block_reward
# hash_price = (btc_price * 144 * block_reward) / hashrate
df["hash_price"] = (df["btc_close"] * 144 * df["block_reward"]) / df["hashrate"]

# Hashrate declining (30d MA declining over 14 days)
df["hr_30d_chg"] = df["hr_30d"].pct_change(14)
df["btc_30d_ret"] = df["btc_close"].pct_change(30)

def hr_regime(row):
    if pd.isna(row["hr_vs_90d"]):
        return "UNKNOWN"
    if row["hr_30d_chg"] < -0.02 and row["btc_30d_ret"] < -0.05:
        return "CAPITULATING"
    elif row["hr_vs_90d"] < -0.05:
        return "STRESSED"
    return "HEALTHY"

df["hr_regime"] = df.apply(hr_regime, axis=1)

hr_regime_counts = df["hr_regime"].value_counts()
print("\nHashrate regime day counts:")
print(hr_regime_counts)

print("\nRecent hashrate metrics:")
recent_hr = df[["hashrate", "hr_30d", "hr_90d", "hr_vs_90d", "hash_price", "hr_regime"]].tail(30)
print(recent_hr.to_string())

# ─── D) Combined Monthly Dashboard ──────────────────────────────────────────

print("\n" + "=" * 80)
print("D) COMBINED MONTHLY DASHBOARD (2025-2026)")
print("=" * 80)

df_recent = df.loc["2025-01-01":]
monthly = df_recent.resample("ME").agg({
    "btc_close": "last",
    "etf_flow": "sum",
    "flow_30d": "last",
    "etf_regime": "last",
    "inst_vwap": "last",
    "inst_pnl_pct": "last",
    "inst_status": "last",
    "hashrate": "last",
    "hr_regime": "last",
    "hash_price": "last",
    "total2_close": "last",
})

monthly["btc_return"] = monthly["btc_close"].pct_change() * 100
monthly["total2_return"] = monthly["total2_close"].pct_change() * 100

dashboard_cols = [
    "btc_close", "btc_return", "etf_flow", "flow_30d", "etf_regime",
    "inst_pnl_pct", "inst_status", "hr_regime", "hash_price",
    "total2_return"
]
print(monthly[dashboard_cols].to_string())

# ─── E) Predictive Analysis ─────────────────────────────────────────────────

print("\n" + "=" * 80)
print("E) PREDICTIVE ANALYSIS")
print("=" * 80)

# E1: Outflow -> Inflow transitions
print("\n--- E1: ETF Outflow → Inflow Transitions ---")
df["regime_prev"] = df["etf_regime"].shift(1)
transitions = df[(df["etf_regime"] == "INFLOW") & (df["regime_prev"] != "INFLOW")].copy()
# Get unique transition events (cluster within 7 days)
if len(transitions) > 0:
    transitions["cluster"] = (transitions.index.to_series().diff() > pd.Timedelta(days=7)).cumsum()
    trans_dates = transitions.groupby("cluster").first()

    print(f"Found {len(trans_dates)} inflow transition events:")
    for _, row in trans_dates.iterrows():
        date = row.name if isinstance(row.name, pd.Timestamp) else df.index[0]
        # Actually get the date from the row

    # Simpler: iterate over transition dates
    trans_idx = transitions.index
    cluster_starts = [trans_idx[0]]
    for d in trans_idx[1:]:
        if (d - cluster_starts[-1]).days > 30:
            cluster_starts.append(d)

    print(f"\nUnique transition starts (>30d apart): {len(cluster_starts)}")
    for d in cluster_starts:
        price_at = df.loc[d, "btc_close"]
        # Forward returns
        fwd_dates = [d + pd.Timedelta(days=n) for n in [7, 14, 30, 60]]
        fwd_prices = [df.loc[:fd, "btc_close"].iloc[-1] if fd <= df.index.max() else np.nan for fd in fwd_dates]
        fwd_rets = [(p / price_at - 1) * 100 if not np.isnan(p) else np.nan for p in fwd_prices]
        print(f"  {d.date()}: BTC=${price_at:,.0f}  "
              f"7d={fwd_rets[0]:+.1f}%  14d={fwd_rets[1]:+.1f}%  "
              f"30d={fwd_rets[2]:+.1f}%  60d={fwd_rets[3]:+.1f}%")

# E2: Institutional underwater periods
print("\n--- E2: Institutional Underwater Periods ---")
df["was_underwater"] = df["inst_status"] == "UNDERWATER"
df["uw_start"] = df["was_underwater"] & ~df["was_underwater"].shift(1, fill_value=False)
uw_starts = df[df["uw_start"]].index

if len(uw_starts) > 0:
    # Cluster within 30 days
    uw_clusters = [uw_starts[0]]
    for d in uw_starts[1:]:
        if (d - uw_clusters[-1]).days > 30:
            uw_clusters.append(d)

    print(f"Underwater periods started: {len(uw_clusters)}")
    for d in uw_clusters:
        price_at = df.loc[d, "btc_close"]
        vwap = df.loc[d, "inst_vwap"]
        pnl = df.loc[d, "inst_pnl_pct"]
        fwd_dates = [d + pd.Timedelta(days=n) for n in [30, 60, 90]]
        fwd_prices = [df.loc[:fd, "btc_close"].iloc[-1] if fd <= df.index.max() else np.nan for fd in fwd_dates]
        fwd_rets = [(p / price_at - 1) * 100 if not np.isnan(p) else np.nan for p in fwd_prices]
        print(f"  {d.date()}: BTC=${price_at:,.0f} VWAP=${vwap:,.0f} ({pnl:+.1f}%)  "
              f"30d={fwd_rets[0]:+.1f}%  60d={fwd_rets[1]:+.1f}%  90d={fwd_rets[2]:+.1f}%")
else:
    print("Institutions never went underwater in this period")

# E3: Miner capitulation
print("\n--- E3: Miner Capitulation Events ---")
cap_days = df[df["hr_regime"] == "CAPITULATING"]
if len(cap_days) > 0:
    # Cluster
    cap_clusters = [cap_days.index[0]]
    for d in cap_days.index[1:]:
        if (d - cap_clusters[-1]).days > 30:
            cap_clusters.append(d)

    print(f"Capitulation events: {len(cap_clusters)}")
    for d in cap_clusters:
        price_at = df.loc[d, "btc_close"]
        fwd_dates = [d + pd.Timedelta(days=n) for n in [30, 60, 90]]
        fwd_prices = [df.loc[:fd, "btc_close"].iloc[-1] if fd <= df.index.max() else np.nan for fd in fwd_dates]
        fwd_rets = [(p / price_at - 1) * 100 if not np.isnan(p) else np.nan for p in fwd_prices]
        print(f"  {d.date()}: BTC=${price_at:,.0f}  "
              f"30d={fwd_rets[0]:+.1f}%  60d={fwd_rets[1]:+.1f}%  90d={fwd_rets[2]:+.1f}%")
else:
    print("No miner capitulation events found in ETF period")

# ─── F) Strategy Implications ────────────────────────────────────────────────

print("\n" + "=" * 80)
print("F) STRATEGY IMPLICATIONS FOR s523r")
print("=" * 80)

# F1: ETF outflows as bear confirmation
print("\n--- F1: ETF Outflows as Bear Confirmation ---")
df["btc_fwd_30d"] = df["btc_close"].shift(-30) / df["btc_close"] - 1

outflow_fwd = df[df["etf_regime"] == "OUTFLOW"]["btc_fwd_30d"].dropna()
inflow_fwd = df[df["etf_regime"] == "INFLOW"]["btc_fwd_30d"].dropna()
neutral_fwd = df[df["etf_regime"] == "NEUTRAL"]["btc_fwd_30d"].dropna()

print(f"  OUTFLOW days: n={len(outflow_fwd)}, mean 30d fwd return = {outflow_fwd.mean()*100:+.2f}%, "
      f"median = {outflow_fwd.median()*100:+.2f}%")
print(f"  NEUTRAL days: n={len(neutral_fwd)}, mean 30d fwd return = {neutral_fwd.mean()*100:+.2f}%, "
      f"median = {neutral_fwd.median()*100:+.2f}%")
print(f"  INFLOW  days: n={len(inflow_fwd)}, mean 30d fwd return = {inflow_fwd.mean()*100:+.2f}%, "
      f"median = {inflow_fwd.median()*100:+.2f}%")

# Statistical test
if len(outflow_fwd) > 5 and len(inflow_fwd) > 5:
    from scipy import stats
    t_stat, p_val = stats.ttest_ind(outflow_fwd, inflow_fwd)
    print(f"  t-test (OUTFLOW vs INFLOW): t={t_stat:.2f}, p={p_val:.4f}")

# F2: Flow reversal as bull transition
print("\n--- F2: Flow Reversal (Outflow → Inflow) as Bull Transition ---")
df["in_outflow"] = df["etf_regime"] == "OUTFLOW"
df["outflow_exit"] = df["in_outflow"].shift(1, fill_value=False) & ~df["in_outflow"]
outflow_exits = df[df["outflow_exit"]].index

if len(outflow_exits) > 0:
    # Cluster
    exit_clusters = [outflow_exits[0]]
    for d in outflow_exits[1:]:
        if (d - exit_clusters[-1]).days > 14:
            exit_clusters.append(d)

    print(f"Outflow exit events (>14d apart): {len(exit_clusters)}")
    for d in exit_clusters:
        price_at = df.loc[d, "btc_close"]
        regime_to = df.loc[d, "etf_regime"]
        fwd_dates = [d + pd.Timedelta(days=n) for n in [7, 14, 30, 60]]
        fwd_prices = [df.loc[:fd, "btc_close"].iloc[-1] if fd <= df.index.max() else np.nan for fd in fwd_dates]
        fwd_rets = [(p / price_at - 1) * 100 if not np.isnan(p) else np.nan for p in fwd_prices]
        print(f"  {d.date()}: BTC=${price_at:,.0f} → {regime_to}  "
              f"7d={fwd_rets[0]:+.1f}%  14d={fwd_rets[1]:+.1f}%  "
              f"30d={fwd_rets[2]:+.1f}%  60d={fwd_rets[3]:+.1f}%")

# F3: March 2026 analysis
print("\n--- F3: March 2026 Inflow Signal Analysis ---")
mar26 = df.loc["2026-03-01":"2026-03-31"]
if len(mar26) > 0:
    print(f"  March 2026 total ETF flow: ${mar26['etf_flow'].sum():,.0f}M")
    print(f"  March 2026 BTC: ${mar26['btc_close'].iloc[0]:,.0f} → ${mar26['btc_close'].iloc[-1]:,.0f} "
          f"({(mar26['btc_close'].iloc[-1]/mar26['btc_close'].iloc[0]-1)*100:+.1f}%)")
    print(f"  End-of-month regime: {mar26['etf_regime'].iloc[-1]}")
    print(f"  End-of-month inst P&L: {mar26['inst_pnl_pct'].iloc[-1]:+.1f}%")
    print(f"  End-of-month HR regime: {mar26['hr_regime'].iloc[-1]}")

    # Did flow flip in March?
    mar_regimes = mar26["etf_regime"].unique()
    print(f"  Regimes during March: {mar_regimes}")

    # Forward from end of March
    eom = mar26.index[-1]
    fwd_dates = [eom + pd.Timedelta(days=n) for n in [7, 14, 30]]
    for n, fd in zip([7, 14, 30], fwd_dates):
        if fd <= df.index.max():
            price_fwd = df.loc[:fd, "btc_close"].iloc[-1]
            ret = (price_fwd / mar26["btc_close"].iloc[-1] - 1) * 100
            print(f"  {n}d forward from March 31: {ret:+.1f}%")

# Summary
print("\n" + "=" * 80)
print("SUMMARY: KEY FINDINGS")
print("=" * 80)

# Signal quality summary
print("\n1. ETF FLOW REGIME as directional signal:")
print(f"   - Outflow periods: {len(outflow_fwd)} days, avg 30d fwd = {outflow_fwd.mean()*100:+.2f}%")
print(f"   - Inflow periods:  {len(inflow_fwd)} days, avg 30d fwd = {inflow_fwd.mean()*100:+.2f}%")
spread = inflow_fwd.mean() - outflow_fwd.mean()
print(f"   - Spread: {spread*100:+.2f}% (positive = inflow predicts better returns)")

print(f"\n2. INSTITUTIONAL VWAP: ${df['inst_vwap'].iloc[-1]:,.0f}")
print(f"   Current status: {df['inst_status'].iloc[-1]} ({df['inst_pnl_pct'].iloc[-1]:+.1f}%)")

print(f"\n3. HASHRATE: {df['hr_regime'].iloc[-1]}")
print(f"   Hash price: ${df['hash_price'].iloc[-1]:.6f}")
print(f"   Hashrate vs 90d SMA: {df['hr_vs_90d'].iloc[-1]*100:+.1f}%")

print("\n4. STRATEGY GATING IMPLICATIONS:")
print("   - ETF OUTFLOW regime could confirm BEAR (keep shorts ungated)")
print("   - ETF INFLOW regime could signal BULL transition (gate shorts)")
if len(outflow_fwd) > 0 and len(inflow_fwd) > 0:
    if outflow_fwd.mean() < 0 and inflow_fwd.mean() > 0:
        print("   ✓ CONFIRMED: Outflow → negative fwd returns, Inflow → positive")
    elif outflow_fwd.mean() < inflow_fwd.mean():
        print("   ~ WEAK: Inflow beats outflow, but not cleanly negative/positive")
    else:
        print("   ✗ NOT CONFIRMED: Regime doesn't cleanly separate returns")

print("\nDone.")
