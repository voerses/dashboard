#!/workspace/venv/bin/python
"""
R172 Validated Portfolio — Raw Backtest Harness Test
====================================================

Runs the R172 combined strategy through tools/raw_backtest.py which enforces:
- ADV cap (1% of 30d ADV per position)
- Slippage (sqrt model)
- Fee modeling (7bps per side)
- Funding costs (historical rates)
- Equity cap (can't trade more than you have)
- Liquidation checks
- Walk-forward IS/OOS reporting

Strategy:
  Component 1 (20% weight): R162 cross-sectional momentum rotation
    - L=7d lookback, N=7d rebalance, K=3 top/bottom
    - 80/20 long/short, EMA10/30 filter, vol filter
  Component 2 (80% weight): R160 vol breakout (R167 optimized)
    - BB(20,2), vol_mult=1.5, trail_sma=15, max_pos=5, mom_days=7
    - 20% per position, partial profit at 3 ATR
  Combined: 2.5x static leverage, 15d DD control

Approach: Use from_weights() for the momentum leg, and bar-by-bar for breakout leg,
then combine via the harness.

Actually, the cleanest approach: Run BOTH strategies through the harness separately,
since they trade different tokens at different times. Then compare.

BUT — the harness is designed as a single portfolio tracker. Running both components
through a single harness instance requires combining their signals into one order flow.

Strategy: We'll use the bar-by-bar approach with a unified timeline.
- Every hour, we update both sub-strategies
- Momentum rebalances every 7 days
- Breakout checks every 4h bar
- We size positions proportionally: momentum gets 20% of equity, breakout gets 80%
- Apply 2.5x leverage
- Apply DD control (reduce exposure when underwater)
"""

import os
import sys
import warnings
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass

warnings.filterwarnings("ignore")

# Add project root to path for imports
sys.path.insert(0, "/workspace/crypto_backtest")
from tools.raw_backtest import Backtest, Side

# ══════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════

CAPITAL = 100_000
FEE_BPS = 7
MARKET = "perp"
LEVERAGE_MAX = 2.5
START = "2024-01-01"
END = "2026-03-17"

# Component weights
W_MOMENTUM = 0.20   # 20% to R162
W_BREAKOUT = 0.80   # 80% to R160

# R162 Momentum params
MOM_LOOKBACK = 7     # days
MOM_REBAL_DAYS = 7   # rebalance every 7 days
MOM_K = 3            # top K / bottom K
MOM_LONG_WT = 0.80   # within momentum allocation: 80% long
MOM_SHORT_WT = 0.20  # within momentum allocation: 20% short
MOM_EMA_FAST = 10
MOM_EMA_SLOW = 30
MOM_MIN_VOL = 1_000_000

# R160 Breakout params (R167 optimized)
BB_PERIOD = 20
BB_STD_MULT = 2.0
VOL_MULT = 1.5
ATR_PERIOD = 20
PARTIAL_ATR_MULT = 3.0
PARTIAL_CLOSE_FRAC = 0.5
MAX_HOLD_BARS_4H = 180   # 30d in 4H bars
TRAIL_SMA = 15
MOM_LOOKBACK_4H = 42     # 7 days * 6 bars/day
BREAKOUT_MAX_POS = 5
BREAKOUT_POS_SIZE = 0.20  # of breakout allocation
BREAKOUT_MIN_VOL = 2_000_000
BREAKOUT_MIN_DAYS = 365

# DD control
DD_WINDOW = 15  # days
DD_THRESHOLDS = [(-0.05, 0.75), (-0.10, 0.50), (-0.15, 0.25), (-0.20, 0.0)]


print("=" * 70)
print(" R172 VALIDATED PORTFOLIO — RAW BACKTEST HARNESS")
print("=" * 70)
print()

# ══════════════════════════════════════════════════════════════════════
# APPROACH: Run components separately, then run combined
# ══════════════════════════════════════════════════════════════════════
# Since the two sub-strategies are independent, we run each through
# its own Backtest instance to get per-component reality-checked results,
# then run the combined version.
# ══════════════════════════════════════════════════════════════════════


# ──────────────────────────────────────────────────────────────────────
# COMPONENT 1: R162 Cross-Sectional Momentum (via from_weights)
# ──────────────────────────────────────────────────────────────────────

print("\n### COMPONENT 1: R162 Cross-Sectional Momentum ###\n")

bt1 = Backtest(
    capital=CAPITAL,
    fee_bps=FEE_BPS,
    market=MARKET,
    leverage_max=LEVERAGE_MAX,
    start=START,
    end=END,
)

# Load eligible tokens
print("Loading tokens for momentum universe...")
all_tokens_data = bt1.load_tokens(min_adv=MOM_MIN_VOL, min_history_days=90)
print(f"  Momentum universe: {len(all_tokens_data)} tokens")

# Precompute daily data for momentum ranking
daily_closes = {}
daily_volumes = {}
ema_signals = {}

for token, df in all_tokens_data.items():
    daily_closes[token] = df["close"].resample("1D").last().dropna()
    dvol = (df["volume"] * df["close"]).resample("1D").sum()
    daily_volumes[token] = dvol
    ema_fast = df["close"].ewm(span=MOM_EMA_FAST, adjust=False).mean()
    ema_slow = df["close"].ewm(span=MOM_EMA_SLOW, adjust=False).mean()
    ema_diff = (ema_fast - ema_slow).resample("1D").last()
    ema_signals[token] = ema_diff

dc = pd.DataFrame(daily_closes)
dv = pd.DataFrame(daily_volumes)
ema_df = pd.DataFrame(ema_signals)

# Lookback returns (shifted to avoid look-ahead)
lb_rets = dc.shift(1) / dc.shift(1 + MOM_LOOKBACK) - 1
ema_shifted = ema_df.shift(1)
rolling_dvol = dv.rolling(30, min_periods=15).mean().shift(1)

# ATR for vol filter
daily_highs = {}
daily_lows = {}
for token, df in all_tokens_data.items():
    daily_highs[token] = df["high"].resample("1D").max().dropna()
    daily_lows[token] = df["low"].resample("1D").min().dropna()

df_high = pd.DataFrame(daily_highs).reindex(dc.index)
df_low = pd.DataFrame(daily_lows).reindex(dc.index)
prev_c = dc.shift(1)
tr1 = df_high - df_low
tr2 = (df_high - prev_c).abs()
tr3 = (df_low - prev_c).abs()
true_range = pd.concat([tr1, tr2, tr3]).groupby(level=0).max().reindex(dc.index)
atr_14 = true_range.rolling(14, min_periods=7).mean()
atr_ratio_shifted = (atr_14 / dc).shift(1)

# Build daily weight matrix for from_weights
start_ts = pd.Timestamp(START)
end_ts = pd.Timestamp(END)
sim_dates = dc.loc[start_ts:end_ts].index
rebal_indices = set(range(0, len(sim_dates), MOM_REBAL_DAYS))

weight_records = []
current_longs = {}
current_shorts = {}

for i, date in enumerate(sim_dates):
    if i in rebal_indices:
        # Rank tokens
        lb = lb_rets.loc[date].dropna() if date in lb_rets.index else pd.Series(dtype=float)
        vol = rolling_dvol.loc[date].dropna() if date in rolling_dvol.index else pd.Series(dtype=float)
        eligible = vol[vol >= MOM_MIN_VOL].index
        lb = lb[lb.index.isin(eligible)]

        # Vol filter: keep high-ATR half
        if date in atr_ratio_shifted.index:
            atr_at = atr_ratio_shifted.loc[date].dropna()
            atr_elig = atr_at[atr_at.index.isin(lb.index)]
            if len(atr_elig) > 0:
                med = atr_elig.median()
                high_vol = atr_elig[atr_elig > med].index
                lb = lb[lb.index.isin(high_vol)]

        current_longs = {}
        current_shorts = {}

        if len(lb) >= 2 * MOM_K:
            ranked = lb.sort_values(ascending=False)
            top_k = ranked.head(MOM_K).index.tolist()
            bottom_k = ranked.tail(MOM_K).index.tolist()

            # EMA filter
            ema_at = ema_shifted.loc[date].dropna() if date in ema_shifted.index else pd.Series(dtype=float)
            top_k = [t for t in top_k if t in ema_at.index and ema_at[t] > 0]
            bottom_k = [t for t in bottom_k if t in ema_at.index and ema_at[t] < 0]

            n_l = max(len(top_k), 1)
            n_s = max(len(bottom_k), 1)
            for t in top_k:
                current_longs[t] = MOM_LONG_WT / n_l
            for t in bottom_k:
                current_shorts[t] = MOM_SHORT_WT / n_s

    # Record weights for this day
    row = {"date": date}
    for t, w in current_longs.items():
        row[t] = w  # positive = long
    for t, w in current_shorts.items():
        row[t] = -w  # negative = short
    weight_records.append(row)

weights_df = pd.DataFrame(weight_records).set_index("date").fillna(0.0)
# Scale by momentum allocation weight
weights_df = weights_df * W_MOMENTUM

print(f"  Weight matrix: {weights_df.shape[0]} days x {weights_df.shape[1]} tokens")
print(f"  Non-zero weight days: {(weights_df.abs().sum(axis=1) > 0).sum()}")

# Run through harness
print("  Running momentum through harness...")
bt1.from_weights(weights_df, rebalance_freq="1W", leverage=LEVERAGE_MAX)
r1 = bt1.report("R162 Momentum (20% allocation, 2.5x leverage)")


# ──────────────────────────────────────────────────────────────────────
# COMPONENT 2: R160 Volatility Breakout (bar-by-bar)
# ──────────────────────────────────────────────────────────────────────

print("\n\n### COMPONENT 2: R160 Volatility Breakout (R167 Optimized) ###\n")

bt2 = Backtest(
    capital=CAPITAL,
    fee_bps=FEE_BPS,
    market=MARKET,
    leverage_max=LEVERAGE_MAX,
    start=START,
    end=END,
)

# Load tokens that meet breakout criteria
print("Loading tokens for breakout universe...")
breakout_tokens = {}
for token in bt2.data.available_tokens():
    try:
        df = bt2.data.load(token)
    except Exception:
        continue
    duration = (df.index.max() - df.index.min()).days
    if duration < BREAKOUT_MIN_DAYS:
        continue

    # Resample to 4H
    df_4h = df.resample("4h").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna(subset=["close"])

    if "funding_1h" in df.columns:
        df_4h["funding_4h"] = df["funding_1h"].resample("4h").sum()
    else:
        df_4h["funding_4h"] = 0.0

    if len(df_4h) < BB_PERIOD + 50:
        continue

    # Compute indicators
    df_4h["sma_trail"] = df_4h["close"].rolling(TRAIL_SMA).mean()
    df_4h["sma_bb"] = df_4h["close"].rolling(BB_PERIOD).mean()
    bb_std = df_4h["close"].rolling(BB_PERIOD).std()
    df_4h["bb_upper"] = df_4h["sma_bb"] + BB_STD_MULT * bb_std
    df_4h["bb_lower"] = df_4h["sma_bb"] - BB_STD_MULT * bb_std
    df_4h["avg_volume"] = df_4h["volume"].rolling(BB_PERIOD).mean()
    df_4h["dollar_volume"] = df_4h["volume"] * df_4h["close"]

    tr_hl = df_4h["high"] - df_4h["low"]
    tr_hc = (df_4h["high"] - df_4h["close"].shift(1)).abs()
    tr_lc = (df_4h["low"] - df_4h["close"].shift(1)).abs()
    df_4h["atr"] = pd.concat([tr_hl, tr_hc, tr_lc], axis=1).max(axis=1).rolling(ATR_PERIOD).mean()

    vol_confirmed = df_4h["volume"] > VOL_MULT * df_4h["avg_volume"]
    df_4h["breakout_long"] = (df_4h["close"] > df_4h["bb_upper"]) & vol_confirmed
    df_4h["breakout_short"] = (df_4h["close"] < df_4h["bb_lower"]) & vol_confirmed
    df_4h["long_strength"] = ((df_4h["close"] - df_4h["bb_upper"]) / df_4h["close"]).clip(lower=0)
    df_4h["short_strength"] = ((df_4h["bb_lower"] - df_4h["close"]) / df_4h["close"]).clip(lower=0)
    df_4h["return_mom"] = df_4h["close"].pct_change(MOM_LOOKBACK_4H)
    df_4h["avg_daily_dollar_vol"] = df_4h["dollar_volume"].rolling(180).mean() * 6

    breakout_tokens[token] = df_4h

print(f"  Breakout universe: {len(breakout_tokens)} tokens")

# Build unified 4H timeline
all_4h_times = set()
for df in breakout_tokens.values():
    ts_range = df.loc[START:END].index
    all_4h_times.update(ts_range.tolist())
all_4h_times = sorted(all_4h_times)

print(f"  4H bars in range: {len(all_4h_times)}")

# Track breakout positions separately (the harness tracks the real positions)
@dataclass
class BreakoutPos:
    token: str
    direction: int  # 1=long, -1=short
    entry_price: float
    entry_atr: float
    entry_time: pd.Timestamp
    partial_taken: bool = False
    remaining_frac: float = 1.0
    breakeven_stop: bool = False
    bars_held: int = 0

breakout_positions: List[BreakoutPos] = []
breakout_pending: List[Tuple] = []  # (token, direction, strength)

warmup_bars = max(BB_PERIOD, TRAIL_SMA) + 10
warmup_done = False

# Size per position in USD — 80% of capital * per-position fraction * leverage
pos_size_usd = CAPITAL * W_BREAKOUT * BREAKOUT_POS_SIZE * LEVERAGE_MAX

print(f"  Target position size: ${pos_size_usd:,.0f}")
print("  Running breakout through harness (bar-by-bar)...")

# We iterate over the 4H bars manually, using bt2.set_time() + bt2.order()/bt2.close()
for ti, current_time in enumerate(all_4h_times):
    bt2.set_time(current_time)
    bt2._update_positions(current_time)

    if ti < warmup_bars:
        continue

    # Process pending entries
    if breakout_pending:
        slots = BREAKOUT_MAX_POS - len(breakout_positions)
        if slots > 0:
            breakout_pending.sort(key=lambda x: x[2], reverse=True)
            to_enter = breakout_pending[:min(5, slots)]
            for token, direction, strength in to_enter:
                if token not in breakout_tokens:
                    continue
                df_4h = breakout_tokens[token]
                if current_time not in df_4h.index:
                    continue
                bar = df_4h.loc[current_time]
                if pd.isna(bar["open"]) or pd.isna(bar["atr"]) or bar["atr"] <= 0:
                    continue

                # Check not already in this token/direction
                already = any(p.token == token and p.direction == direction
                             for p in breakout_positions)
                if already:
                    continue

                side = "long" if direction == 1 else "short"
                # Use dynamic sizing based on current equity
                dynamic_size = bt2.get_equity() * W_BREAKOUT * BREAKOUT_POS_SIZE * LEVERAGE_MAX
                dynamic_size = max(dynamic_size, 100)  # floor

                pos = bt2.order(token, side=side, size_usd=dynamic_size,
                               leverage=LEVERAGE_MAX, reason="breakout_entry")
                if pos is not None:
                    breakout_positions.append(BreakoutPos(
                        token=token, direction=direction,
                        entry_price=pos.entry_price,
                        entry_atr=bar["atr"],
                        entry_time=current_time,
                    ))
        breakout_pending = []

    # Update breakout positions — check exits
    closed_indices = []
    for pidx, bpos in enumerate(breakout_positions):
        token = bpos.token
        if token not in breakout_tokens:
            bpos.bars_held += 1
            continue
        df_4h = breakout_tokens[token]
        if current_time not in df_4h.index:
            bpos.bars_held += 1
            continue

        bar = df_4h.loc[current_time]
        close_price = bar["close"]
        sma_val = bar["sma_trail"]

        if pd.isna(close_price) or pd.isna(sma_val):
            bpos.bars_held += 1
            continue
        bpos.bars_held += 1

        # Check partial profit (3 ATR from entry)
        if not bpos.partial_taken and bpos.entry_atr > 0:
            atr_dist = (close_price - bpos.entry_price) / bpos.entry_atr * bpos.direction
            if atr_dist >= PARTIAL_ATR_MULT:
                # Close half the position via the harness
                # We close the whole thing and re-open at half size
                bt2.close(token, reason="partial_profit")

                # Re-open at half size
                side = "long" if bpos.direction == 1 else "short"
                remaining_size = bt2.get_equity() * W_BREAKOUT * BREAKOUT_POS_SIZE * LEVERAGE_MAX * 0.5
                if remaining_size > 10:
                    new_pos = bt2.order(token, side=side, size_usd=remaining_size,
                                        leverage=LEVERAGE_MAX, reason="partial_reentry")

                bpos.partial_taken = True
                bpos.remaining_frac = 0.5
                bpos.breakeven_stop = True
                continue

        # Exit checks
        exit_reason = None

        # Trail stop (SMA cross)
        if bpos.direction == 1 and close_price < sma_val:
            if bpos.breakeven_stop and close_price >= bpos.entry_price:
                exit_reason = "trail_stop"
            elif bpos.breakeven_stop and close_price < bpos.entry_price:
                exit_reason = "breakeven_stop"
            else:
                exit_reason = "trail_stop"
        elif bpos.direction == -1 and close_price > sma_val:
            if bpos.breakeven_stop and close_price <= bpos.entry_price:
                exit_reason = "trail_stop"
            elif bpos.breakeven_stop and close_price > bpos.entry_price:
                exit_reason = "breakeven_stop"
            else:
                exit_reason = "trail_stop"

        # Max hold
        if bpos.bars_held >= MAX_HOLD_BARS_4H:
            exit_reason = "max_hold"

        if exit_reason:
            bt2.close(token, reason=exit_reason)
            closed_indices.append(pidx)

    # Remove closed breakout tracking entries
    for pidx in sorted(closed_indices, reverse=True):
        breakout_positions.pop(pidx)

    # Scan for new breakouts
    for token, df_4h in breakout_tokens.items():
        if current_time not in df_4h.index:
            continue
        bar = df_4h.loc[current_time]

        if pd.isna(bar.get("avg_daily_dollar_vol", np.nan)):
            continue
        if bar["avg_daily_dollar_vol"] < BREAKOUT_MIN_VOL:
            continue

        if bar["breakout_long"]:
            ret_m = bar["return_mom"]
            if not pd.isna(ret_m) and ret_m > 0:
                strength = bar["long_strength"]
                if not pd.isna(strength) and strength > 0:
                    breakout_pending.append((token, 1, strength))

        if bar["breakout_short"]:
            ret_m = bar["return_mom"]
            if not pd.isna(ret_m) and ret_m < 0:
                strength = bar["short_strength"]
                if not pd.isna(strength) and strength > 0:
                    breakout_pending.append((token, -1, strength))

# Close any remaining positions
bt2.close_all(reason="end_of_backtest")

r2 = bt2.report("R160 Breakout (80% allocation, 2.5x leverage)")


# ──────────────────────────────────────────────────────────────────────
# COMPONENT 3: COMBINED R172 via from_weights (unified)
# ──────────────────────────────────────────────────────────────────────

print("\n\n### COMBINED: R172 Portfolio (20% Momentum + 80% Breakout) ###\n")
print("Running combined portfolio through single harness instance...")

# For the combined approach, we'll build a unified daily weight matrix
# that merges momentum weights and breakout "weights" (converted from
# position tracking to daily weights).

# Extract breakout equity curve and compute its effective daily weights
# from the actual trades that happened.

# Actually, the most honest approach is to run a SINGLE harness instance
# that executes BOTH strategies simultaneously, sharing the same capital pool.

bt3 = Backtest(
    capital=CAPITAL,
    fee_bps=FEE_BPS,
    market=MARKET,
    leverage_max=LEVERAGE_MAX,
    start=START,
    end=END,
)

# We need a unified hourly timeline across all tokens
print("Building unified timeline...")

# Collect all tokens used by either strategy
mom_tokens = set(weights_df.columns)
brk_tokens = set(breakout_tokens.keys())
all_used_tokens = mom_tokens | brk_tokens

# Load 1H data for all used tokens
token_1h = {}
for token in all_used_tokens:
    try:
        df = bt3.data.load(token)
        token_1h[token] = df.loc[START:END]
    except Exception:
        continue

# Build hourly timeline
all_hours = set()
for df in token_1h.values():
    all_hours.update(df.index.tolist())
all_hours = sorted(all_hours)
print(f"  Total 1H bars: {len(all_hours)}")

# For the combined run, we need to handle both strategies in one pass.
# The momentum leg rebalances weekly (daily weight changes).
# The breakout leg trades on 4H signals.

# Precompute: which 1H bars are at 4H boundaries?
four_h_bars = set()
for t in all_hours:
    if t.hour % 4 == 0:
        four_h_bars.add(t)

# Precompute daily dates for momentum rebalance
daily_dates = sorted(set(pd.Timestamp(t.date()) for t in all_hours))
sim_daily = [d for d in daily_dates if d >= pd.Timestamp(START) and d <= pd.Timestamp(END)]
rebal_mom_dates = set(sim_daily[i] for i in range(0, len(sim_daily), MOM_REBAL_DAYS))

# State tracking
mom_current_longs = {}   # token -> weight (within momentum allocation)
mom_current_shorts = {}
mom_position_tokens = set()  # tokens currently held for momentum

brk_positions: List[BreakoutPos] = []
brk_pending: List[Tuple] = []
brk_position_tokens = set()  # tokens currently held for breakout

# DD control state
equity_history = []

print("Running combined strategy bar-by-bar...")

bar_count = 0
last_rebal_date = None
warmup_4h_count = 0

for ts in all_hours:
    bt3.set_time(ts)
    bt3._update_positions(ts)

    bar_count += 1
    current_date = pd.Timestamp(ts.date())
    current_eq = bt3.get_equity()
    equity_history.append(current_eq)

    # DD control: compute recent drawdown and scale
    dd_scale = 1.0
    if len(equity_history) > DD_WINDOW * 24:
        recent = equity_history[-(DD_WINDOW * 24):]
        peak = max(recent)
        dd = (current_eq / peak - 1) if peak > 0 else 0
        for threshold, scale in DD_THRESHOLDS:
            if dd < threshold:
                dd_scale = scale

    # ── MOMENTUM LEG: rebalance on schedule ──
    if current_date in rebal_mom_dates and current_date != last_rebal_date:
        last_rebal_date = current_date

        # Close existing momentum positions
        for token in list(mom_position_tokens):
            if token in bt3.positions:
                bt3.close(token, reason="mom_rebalance")
        mom_position_tokens.clear()

        # Rank tokens
        if current_date in lb_rets.index:
            lb = lb_rets.loc[current_date].dropna()
            vol = rolling_dvol.loc[current_date].dropna() if current_date in rolling_dvol.index else pd.Series(dtype=float)
            eligible = vol[vol >= MOM_MIN_VOL].index
            lb = lb[lb.index.isin(eligible)]

            # Vol filter
            if current_date in atr_ratio_shifted.index:
                atr_at = atr_ratio_shifted.loc[current_date].dropna()
                atr_elig = atr_at[atr_at.index.isin(lb.index)]
                if len(atr_elig) > 0:
                    med = atr_elig.median()
                    high_vol = atr_elig[atr_elig > med].index
                    lb = lb[lb.index.isin(high_vol)]

            if len(lb) >= 2 * MOM_K:
                ranked = lb.sort_values(ascending=False)
                top_k = ranked.head(MOM_K).index.tolist()
                bottom_k = ranked.tail(MOM_K).index.tolist()

                ema_at = ema_shifted.loc[current_date].dropna() if current_date in ema_shifted.index else pd.Series(dtype=float)
                top_k = [t for t in top_k if t in ema_at.index and ema_at[t] > 0]
                bottom_k = [t for t in bottom_k if t in ema_at.index and ema_at[t] < 0]

                n_l = max(len(top_k), 1)
                n_s = max(len(bottom_k), 1)

                # Size: momentum allocation * equity * leverage * dd_scale
                mom_capital = current_eq * W_MOMENTUM * LEVERAGE_MAX * dd_scale

                for t in top_k:
                    # Skip if token is held by breakout
                    if t in brk_position_tokens:
                        continue
                    size = mom_capital * MOM_LONG_WT / n_l
                    if size > 10:
                        pos = bt3.order(t, side="long", size_usd=size,
                                       leverage=LEVERAGE_MAX, reason="mom_long")
                        if pos is not None:
                            mom_position_tokens.add(t)

                for t in bottom_k:
                    if t in brk_position_tokens:
                        continue
                    size = mom_capital * MOM_SHORT_WT / n_s
                    if size > 10:
                        pos = bt3.order(t, side="short", size_usd=size,
                                       leverage=LEVERAGE_MAX, reason="mom_short")
                        if pos is not None:
                            mom_position_tokens.add(t)

    # ── BREAKOUT LEG: check on 4H bars ──
    if ts in four_h_bars:
        warmup_4h_count += 1
        if warmup_4h_count < warmup_bars:
            continue

        # Process pending entries
        if brk_pending:
            slots = BREAKOUT_MAX_POS - len(brk_positions)
            if slots > 0:
                brk_pending.sort(key=lambda x: x[2], reverse=True)
                to_enter = brk_pending[:min(5, slots)]
                for token, direction, strength in to_enter:
                    if token not in breakout_tokens:
                        continue
                    df_4h = breakout_tokens[token]
                    if ts not in df_4h.index:
                        continue
                    bar = df_4h.loc[ts]
                    if pd.isna(bar["open"]) or pd.isna(bar["atr"]) or bar["atr"] <= 0:
                        continue

                    already = any(p.token == token and p.direction == direction
                                 for p in brk_positions)
                    if already:
                        continue

                    # Skip if held by momentum
                    if token in mom_position_tokens:
                        continue

                    side = "long" if direction == 1 else "short"
                    brk_size = current_eq * W_BREAKOUT * BREAKOUT_POS_SIZE * LEVERAGE_MAX * dd_scale
                    brk_size = max(brk_size, 100)

                    pos = bt3.order(token, side=side, size_usd=brk_size,
                                   leverage=LEVERAGE_MAX, reason="brk_entry")
                    if pos is not None:
                        brk_positions.append(BreakoutPos(
                            token=token, direction=direction,
                            entry_price=pos.entry_price,
                            entry_atr=bar["atr"],
                            entry_time=ts,
                        ))
                        brk_position_tokens.add(token)
            brk_pending = []

        # Update breakout positions
        closed_brk = []
        for pidx, bpos in enumerate(brk_positions):
            token = bpos.token
            if token not in breakout_tokens:
                bpos.bars_held += 1
                continue
            df_4h = breakout_tokens[token]
            if ts not in df_4h.index:
                bpos.bars_held += 1
                continue

            bar = df_4h.loc[ts]
            close_price = bar["close"]
            sma_val = bar["sma_trail"]

            if pd.isna(close_price) or pd.isna(sma_val):
                bpos.bars_held += 1
                continue
            bpos.bars_held += 1

            # Partial profit check
            if not bpos.partial_taken and bpos.entry_atr > 0:
                atr_dist = (close_price - bpos.entry_price) / bpos.entry_atr * bpos.direction
                if atr_dist >= PARTIAL_ATR_MULT:
                    bt3.close(token, reason="brk_partial")
                    side = "long" if bpos.direction == 1 else "short"
                    remain_size = current_eq * W_BREAKOUT * BREAKOUT_POS_SIZE * LEVERAGE_MAX * 0.5 * dd_scale
                    if remain_size > 10:
                        bt3.order(token, side=side, size_usd=remain_size,
                                 leverage=LEVERAGE_MAX, reason="brk_partial_reentry")
                    bpos.partial_taken = True
                    bpos.remaining_frac = 0.5
                    bpos.breakeven_stop = True
                    continue

            # Exit checks
            exit_reason = None
            if bpos.direction == 1 and close_price < sma_val:
                if bpos.breakeven_stop and close_price >= bpos.entry_price:
                    exit_reason = "trail_stop"
                elif bpos.breakeven_stop and close_price < bpos.entry_price:
                    exit_reason = "breakeven_stop"
                else:
                    exit_reason = "trail_stop"
            elif bpos.direction == -1 and close_price > sma_val:
                if bpos.breakeven_stop and close_price <= bpos.entry_price:
                    exit_reason = "trail_stop"
                elif bpos.breakeven_stop and close_price > bpos.entry_price:
                    exit_reason = "breakeven_stop"
                else:
                    exit_reason = "trail_stop"

            if bpos.bars_held >= MAX_HOLD_BARS_4H:
                exit_reason = "max_hold"

            if exit_reason:
                bt3.close(token, reason=exit_reason)
                brk_position_tokens.discard(token)
                closed_brk.append(pidx)

        for pidx in sorted(closed_brk, reverse=True):
            brk_positions.pop(pidx)

        # Scan for new breakouts
        for token, df_4h in breakout_tokens.items():
            if ts not in df_4h.index:
                continue
            bar = df_4h.loc[ts]

            if pd.isna(bar.get("avg_daily_dollar_vol", np.nan)):
                continue
            if bar["avg_daily_dollar_vol"] < BREAKOUT_MIN_VOL:
                continue

            if bar["breakout_long"]:
                ret_m = bar["return_mom"]
                if not pd.isna(ret_m) and ret_m > 0:
                    strength = bar["long_strength"]
                    if not pd.isna(strength) and strength > 0:
                        brk_pending.append((token, 1, strength))

            if bar["breakout_short"]:
                ret_m = bar["return_mom"]
                if not pd.isna(ret_m) and ret_m < 0:
                    strength = bar["short_strength"]
                    if not pd.isna(strength) and strength > 0:
                        brk_pending.append((token, -1, strength))

# Close all remaining
bt3.close_all(reason="end_of_backtest")

print(f"\n  Total bars processed: {bar_count}")
print(f"  Total trades: {len(bt3.trades)}")

r3 = bt3.report("R172 Combined Portfolio (20% Mom + 80% Brk, 2.5x lev, 15d DD ctrl)")


# ──────────────────────────────────────────────────────────────────────
# SUMMARY COMPARISON
# ──────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print(" SUMMARY: R172 Research Claims vs Raw Backtest Reality")
print("=" * 70)
print()
print(f"{'Metric':<25s} {'Research':>12s} {'Raw Backtest':>12s} {'Delta':>10s}")
print("-" * 60)

# Research claims
research_ret = 4.469   # +446.9%
research_sharpe = 2.79
research_maxdd = -0.18  # -18%

if r3 and "windows" in r3:
    l12m = r3["windows"].get("L12M", {})
    raw_ret = l12m.get("total_ret", 0)
    raw_sharpe = l12m.get("sharpe", 0)
    raw_maxdd = l12m.get("maxdd", 0)

    print(f"{'L12M Return':<25s} {research_ret:>+11.1%} {raw_ret:>+11.1%} {raw_ret - research_ret:>+9.1%}")
    print(f"{'Sharpe':<25s} {research_sharpe:>12.2f} {raw_sharpe:>12.2f} {raw_sharpe - research_sharpe:>+9.2f}")
    print(f"{'Max Drawdown':<25s} {research_maxdd:>+11.1%} {raw_maxdd:>+11.1%} {raw_maxdd - research_maxdd:>+9.1%}")
    print()
    print(f"Verdict: {r3.get('verdict', 'UNKNOWN')}")
    if r3.get("kill_reasons"):
        print("Kill reasons:")
        for kr in r3["kill_reasons"]:
            print(f"  - {kr}")
    print()
    print(f"Liquidations: {r3.get('liquidations', 0)}")
    print(f"Violations: {r3.get('violations', 0)}")
else:
    print("  [Combined backtest produced no results]")

print("\n" + "=" * 70)
print(" END OF R172 RAW BACKTEST")
print("=" * 70)
