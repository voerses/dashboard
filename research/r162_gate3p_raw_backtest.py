#!/workspace/venv/bin/python
"""
R162 Triple-Filter Momentum — Gate 3P Raw Backtest
====================================================

Runs the R162 cross-sectional momentum rotation strategy through
tools/raw_backtest.py which enforces:
  - ADV cap (1% of 30d ADV per position)
  - Slippage (sqrt model)
  - Fee modeling (7bps per side)
  - Funding costs (historical rates)
  - Equity cap (can't trade more than you have)
  - Liquidation checks
  - Walk-forward IS/OOS reporting

Strategy (from R162 research, best variant):
  - L=7d return lookback for ranking
  - N=7d weekly rebalance
  - K=3 concentrated top/bottom
  - 90/10 long-biased allocation
  - EMA(10h/30h) regime filter (long if bullish, short if bearish)
  - ATR(14d)/price volatility filter (above-median only)
  - All signals shifted by 1 day to avoid look-ahead
  - Leverage: 1.0x (no leverage for clean Gate 3P)
  - Market: PERP (for short capability)
"""

import sys
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

sys.path.insert(0, "/workspace/crypto_backtest")
from tools.raw_backtest import Backtest

# ══════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════

CAPITAL = 100_000
FEE_BPS = 7
MARKET = "perp"
LEVERAGE = 1.0       # No leverage for clean Gate 3P evaluation
START = "2024-01-01"
END = "2026-03-17"

# R162 Strategy Parameters (best variant from research)
LOOKBACK_DAYS = 7     # Return lookback for cross-sectional ranking
REBALANCE_DAYS = 7    # Weekly rebalance
K = 3                 # Top K / bottom K tokens
LONG_WT = 0.90        # 90% to longs
SHORT_WT = 0.10       # 10% to shorts
EMA_FAST = 10         # Hours (EMA span for regime filter)
EMA_SLOW = 30         # Hours
MIN_VOL_USD = 1_000_000  # Minimum 30d avg daily dollar volume

print("=" * 70)
print(" R162 TRIPLE-FILTER MOMENTUM — GATE 3P RAW BACKTEST")
print("=" * 70)
print()
print(f"  Config: L={LOOKBACK_DAYS}, N={REBALANCE_DAYS}, K={K}, "
      f"Alloc={int(LONG_WT*100)}/{int(SHORT_WT*100)}")
print(f"  Filters: EMA({EMA_FAST}h/{EMA_SLOW}h) regime + "
      f"ATR(14d)/price vol filter")
print(f"  Capital: ${CAPITAL:,} | Fee: {FEE_BPS}bps | "
      f"Leverage: {LEVERAGE}x | Market: {MARKET}")
print(f"  Period: {START} to {END}")
print()

# ══════════════════════════════════════════════════════════════════════
# INITIALIZE BACKTEST HARNESS
# ══════════════════════════════════════════════════════════════════════

bt = Backtest(
    capital=CAPITAL,
    fee_bps=FEE_BPS,
    market=MARKET,
    leverage_max=LEVERAGE,
    start=START,
    end=END,
)

# ══════════════════════════════════════════════════════════════════════
# LOAD AND PREPARE DATA
# ══════════════════════════════════════════════════════════════════════

print("[1/3] Loading tokens...")
all_tokens_data = bt.load_tokens(min_adv=MIN_VOL_USD, min_history_days=90)
print(f"  Universe: {len(all_tokens_data)} tokens meeting volume/history filters")
print(f"  Tokens: {', '.join(sorted(all_tokens_data.keys()))}")

# Build daily close, volume, EMA signals from 1H data
print("\n[2/3] Building daily data panels...")

daily_closes = {}
daily_volumes = {}
ema_signals = {}
daily_highs = {}
daily_lows = {}

for token, df in all_tokens_data.items():
    daily_closes[token] = df["close"].resample("1D").last().dropna()
    dvol = (df["volume"] * df["close"]).resample("1D").sum()
    daily_volumes[token] = dvol

    # EMA regime filter (computed on hourly data, sampled daily)
    ema_fast = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    ema_slow = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
    ema_diff = (ema_fast - ema_slow).resample("1D").last()
    ema_signals[token] = ema_diff

    # For ATR volatility filter
    daily_highs[token] = df["high"].resample("1D").max().dropna()
    daily_lows[token] = df["low"].resample("1D").min().dropna()

dc = pd.DataFrame(daily_closes)
dv = pd.DataFrame(daily_volumes)
ema_df = pd.DataFrame(ema_signals)

# Lookback returns -- shifted by 1 day to avoid look-ahead
# lb_rets[date] = close[date-1] / close[date-1-LOOKBACK] - 1
lb_rets = dc.shift(1) / dc.shift(1 + LOOKBACK_DAYS) - 1

# EMA signal -- shifted by 1 day
ema_shifted = ema_df.shift(1)

# Rolling 30d average daily dollar volume -- shifted by 1 day
rolling_dvol = dv.rolling(30, min_periods=15).mean().shift(1)

# ATR(14d)/price volatility ratio -- shifted by 1 day
df_high = pd.DataFrame(daily_highs).reindex(dc.index)
df_low = pd.DataFrame(daily_lows).reindex(dc.index)
prev_close = dc.shift(1)

# True Range = max(H-L, |H-Cprev|, |L-Cprev|)
tr1 = df_high - df_low
tr2 = (df_high - prev_close).abs()
tr3 = (df_low - prev_close).abs()
true_range = pd.concat([tr1, tr2, tr3]).groupby(level=0).max().reindex(dc.index)
atr_14 = true_range.rolling(14, min_periods=7).mean()
atr_ratio_shifted = (atr_14 / dc).shift(1)

print(f"  Daily close shape: {dc.shape}")
print(f"  Date range: {dc.index.min().date()} to {dc.index.max().date()}")

# Sanity check -- BTC return over period
start_ts = pd.Timestamp(START)
end_ts = pd.Timestamp(END)
if "BTC" in dc.columns:
    btc_sim = dc["BTC"].loc[start_ts:end_ts].dropna()
    if len(btc_sim) > 0:
        btc_ret = btc_sim.iloc[-1] / btc_sim.iloc[0] - 1
        print(f"  BTC total return: {btc_ret*100:+.1f}%")

# ══════════════════════════════════════════════════════════════════════
# BUILD WEIGHT MATRIX (R162 signal generation)
# ══════════════════════════════════════════════════════════════════════

print("\n[3/3] Building weight matrix (R162 signal generation)...")

sim_dates = dc.loc[start_ts:end_ts].index
rebal_indices = set(range(0, len(sim_dates), REBALANCE_DAYS))

weight_records = []
current_longs = {}
current_shorts = {}

for i, date in enumerate(sim_dates):
    if i in rebal_indices:
        # Get lookback returns for ranking
        lb = (lb_rets.loc[date].dropna()
              if date in lb_rets.index
              else pd.Series(dtype=float))

        # Volume filter: require MIN_VOL_USD daily dollar volume
        vol = (rolling_dvol.loc[date].dropna()
               if date in rolling_dvol.index
               else pd.Series(dtype=float))
        eligible = vol[vol >= MIN_VOL_USD].index
        lb = lb[lb.index.isin(eligible)]

        # ATR volatility filter: keep only above-median ATR/price tokens
        if date in atr_ratio_shifted.index:
            atr_at = atr_ratio_shifted.loc[date].dropna()
            atr_elig = atr_at[atr_at.index.isin(lb.index)]
            if len(atr_elig) > 0:
                med = atr_elig.median()
                high_vol = atr_elig[atr_elig > med].index
                lb = lb[lb.index.isin(high_vol)]

        # Reset weights for this rebalance
        current_longs = {}
        current_shorts = {}

        if len(lb) >= 2 * K:
            ranked = lb.sort_values(ascending=False)
            top_k = ranked.head(K).index.tolist()
            bottom_k = ranked.tail(K).index.tolist()

            # EMA regime filter
            ema_at = (ema_shifted.loc[date].dropna()
                      if date in ema_shifted.index
                      else pd.Series(dtype=float))

            # Long only if EMA fast > EMA slow (bullish)
            top_k = [t for t in top_k
                     if t in ema_at.index and ema_at[t] > 0]
            # Short only if EMA fast < EMA slow (bearish)
            bottom_k = [t for t in bottom_k
                        if t in ema_at.index and ema_at[t] < 0]

            n_l = max(len(top_k), 1)
            n_s = max(len(bottom_k), 1)

            for t in top_k:
                current_longs[t] = LONG_WT / n_l
            for t in bottom_k:
                current_shorts[t] = SHORT_WT / n_s

    # Record weights for this day
    row = {"date": date}
    for t, w in current_longs.items():
        row[t] = w     # positive = long
    for t, w in current_shorts.items():
        row[t] = -w    # negative = short
    weight_records.append(row)

weights_df = pd.DataFrame(weight_records).set_index("date").fillna(0.0)

print(f"  Weight matrix: {weights_df.shape[0]} days x {weights_df.shape[1]} tokens")
non_zero_days = (weights_df.abs().sum(axis=1) > 0).sum()
print(f"  Non-zero weight days: {non_zero_days}")
if non_zero_days > 0:
    avg_positions = (weights_df != 0).sum(axis=1).loc[weights_df.abs().sum(axis=1) > 0].mean()
    print(f"  Avg positions per active day: {avg_positions:.1f}")
    gross_exp = weights_df.abs().sum(axis=1).loc[weights_df.abs().sum(axis=1) > 0]
    print(f"  Avg gross exposure: {gross_exp.mean():.2%}")
    print(f"  Max gross exposure: {gross_exp.max():.2%}")

# ══════════════════════════════════════════════════════════════════════
# RUN THROUGH RAW BACKTEST HARNESS
# ══════════════════════════════════════════════════════════════════════

print("\nRunning through raw backtest harness...")
bt.from_weights(weights_df, rebalance_freq="1W", leverage=LEVERAGE)

print("\n")
result = bt.report("R162 Triple-Filter Momentum - Gate 3P")

# ══════════════════════════════════════════════════════════════════════
# GATE 3P VERDICT SUMMARY
# ══════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print(" GATE 3P VERDICT SUMMARY")
print("=" * 70)

if result:
    verdict = result.get("verdict", "UNKNOWN")
    kill_reasons = result.get("kill_reasons", [])

    print(f"\n  VERDICT: {verdict}")
    if kill_reasons:
        print("\n  Kill reasons:")
        for kr in kill_reasons:
            print(f"    - {kr}")
    else:
        print("  All windows PASS thresholds")

    print(f"\n  Trades: {result.get('trades', 0)}")
    print(f"  Liquidations: {result.get('liquidations', 0)}")
    print(f"  Violations: {result.get('violations', 0)}")

    # Research comparison
    print("\n  R162 Research Baseline (best variant L7/K3/90-10/EMA10-30/VF):")
    print("    Full period: Sharpe ~1.39, Ann Ret ~+289.6%, MaxDD ~-55%")
    print("    L12M: +289.6%")

    windows = result.get("windows", {})
    if "L12M" in windows:
        l12m = windows["L12M"]
        print(f"\n  Raw Backtest L12M:")
        print(f"    Return:  {l12m.get('total_ret', 0):+.1%}")
        print(f"    Sharpe:  {l12m.get('sharpe', 0):.2f}")
        print(f"    Calmar:  {l12m.get('calmar', 0):.2f}")
        print(f"    MaxDD:   {l12m.get('maxdd', 0):+.1%}")
        print(f"    Trades:  {l12m.get('trades', 0)}")
else:
    print("\n  [No results returned from backtest]")

print("\n" + "=" * 70)
print(" END OF R162 GATE 3P RAW BACKTEST")
print("=" * 70)
