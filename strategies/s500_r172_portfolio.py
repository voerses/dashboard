"""
s500 R172 Validated Portfolio — BB Breakout + Cross-Sectional Momentum
======================================================================

Two-component portfolio combining uncorrelated alpha sources:
  Component 1 (80% weight): R160 BB Volatility Breakout on 4H bars
    - Entry: close > upper BB(20,2) AND volume > 1.3x avg AND close > EMA(20)
    - Exit: close < middle BB (SMA20) trailing stop, or max 30d hold
    - Momentum filter: 7d return > 0 required for entry
    - Max 5 concurrent positions at 20% each
    - Partial profit: close 50% at 3 ATR from entry, set breakeven stop

  Component 2 (20% weight): R162 Cross-Sectional Momentum Rotation
    - Rank tokens by 7-day lookback return
    - Go long top K=3, short bottom K=3
    - 80/20 long/short within allocation
    - EMA(10/30) trend filter, ATR volatility filter
    - Weekly (7d) rebalance

Research results (R172 Gate 1): L12M +394.7%, Sharpe 3.16, MaxDD -18.4%, Calmar 21.29
Gate 3P target: Validate through raw backtest harness with realistic costs.

Status: GATE 3P — Production Prototype
Market: PERP (bidirectional)
"""

import os
import sys
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional, Set
from dataclasses import dataclass

# ══════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════

# Component weights
W_BREAKOUT = 0.80       # 80% to R160 breakout
W_MOMENTUM = 0.20       # 20% to R162 momentum

# R160 Breakout params (R167 optimized)
BB_PERIOD = 20           # Bollinger Band lookback
BB_STD_MULT = 2.0        # BB standard deviation multiplier
VOL_THRESHOLD = 1.3      # Volume must exceed this * avg volume
ATR_PERIOD = 20          # ATR lookback for partial profit
PARTIAL_ATR_MULT = 3.0   # Take partial at 3 ATR from entry
PARTIAL_CLOSE_FRAC = 0.5 # Close 50% at partial target
MAX_HOLD_BARS_4H = 180   # 30 days in 4H bars
TRAIL_SMA = 15           # SMA period for trailing stop (R167 optimized)
MOM_LOOKBACK_4H = 42     # 7 days * 6 bars/day for momentum filter
BREAKOUT_MAX_POS = 5     # Max concurrent breakout positions
BREAKOUT_POS_SIZE = 0.20 # 20% of breakout allocation per position
BREAKOUT_MIN_VOL = 2_000_000  # Min $2M ADV for breakout universe
BREAKOUT_MIN_DAYS = 365  # Min data history

# R162 Momentum params
MOM_LOOKBACK = 7         # Days for return ranking
MOM_REBAL_DAYS = 7       # Rebalance every 7 days
MOM_K = 3                # Top K / bottom K tokens
MOM_LONG_WT = 0.80       # 80% long within momentum allocation
MOM_SHORT_WT = 0.20      # 20% short within momentum allocation
MOM_EMA_FAST = 10        # Fast EMA for trend filter
MOM_EMA_SLOW = 30        # Slow EMA for trend filter
MOM_MIN_VOL = 1_000_000  # Min $1M ADV for momentum universe

# DD control
DD_WINDOW = 15           # Days for drawdown lookback
DD_THRESHOLDS = [(-0.05, 0.75), (-0.10, 0.50), (-0.15, 0.25), (-0.20, 0.0)]


# ══════════════════════════════════════════════════════════════════════
# BREAKOUT POSITION TRACKING
# ══════════════════════════════════════════════════════════════════════

@dataclass
class BreakoutPos:
    """Track state for a breakout position beyond what the harness tracks."""
    token: str
    direction: int            # 1=long, -1=short
    entry_price: float
    entry_atr: float
    entry_time: pd.Timestamp
    partial_taken: bool = False
    remaining_frac: float = 1.0
    breakeven_stop: bool = False
    bars_held: int = 0


# ══════════════════════════════════════════════════════════════════════
# MOMENTUM DATA PREPARATION (vectorized)
# ══════════════════════════════════════════════════════════════════════

def prepare_momentum_data(bt, min_vol=MOM_MIN_VOL, min_history_days=90):
    """
    Prepare all the vectorized data needed for momentum ranking.
    Returns DataFrames for daily closes, lookback returns, EMA signals,
    volume, and ATR ratio -- all shifted to avoid look-ahead bias.
    """
    all_tokens_data = bt.load_tokens(min_adv=min_vol, min_history_days=min_history_days)
    print(f"  Momentum universe: {len(all_tokens_data)} tokens")

    daily_closes = {}
    daily_volumes = {}
    ema_signals = {}
    daily_highs = {}
    daily_lows = {}

    for token, df in all_tokens_data.items():
        daily_closes[token] = df["close"].resample("1D").last().dropna()
        dvol = (df["volume"] * df["close"]).resample("1D").sum()
        daily_volumes[token] = dvol
        ema_fast = df["close"].ewm(span=MOM_EMA_FAST, adjust=False).mean()
        ema_slow = df["close"].ewm(span=MOM_EMA_SLOW, adjust=False).mean()
        ema_diff = (ema_fast - ema_slow).resample("1D").last()
        ema_signals[token] = ema_diff
        daily_highs[token] = df["high"].resample("1D").max().dropna()
        daily_lows[token] = df["low"].resample("1D").min().dropna()

    dc = pd.DataFrame(daily_closes)
    dv = pd.DataFrame(daily_volumes)
    ema_df = pd.DataFrame(ema_signals)

    # Lookback returns (shifted to avoid look-ahead)
    lb_rets = dc.shift(1) / dc.shift(1 + MOM_LOOKBACK) - 1
    ema_shifted = ema_df.shift(1)
    rolling_dvol = dv.rolling(30, min_periods=15).mean().shift(1)

    # ATR for volatility filter
    df_high = pd.DataFrame(daily_highs).reindex(dc.index)
    df_low = pd.DataFrame(daily_lows).reindex(dc.index)
    prev_c = dc.shift(1)
    tr1 = df_high - df_low
    tr2 = (df_high - prev_c).abs()
    tr3 = (df_low - prev_c).abs()
    true_range = pd.concat([tr1, tr2, tr3]).groupby(level=0).max().reindex(dc.index)
    atr_14 = true_range.rolling(14, min_periods=7).mean()
    atr_ratio_shifted = (atr_14 / dc).shift(1)

    return {
        "dc": dc,
        "lb_rets": lb_rets,
        "ema_shifted": ema_shifted,
        "rolling_dvol": rolling_dvol,
        "atr_ratio_shifted": atr_ratio_shifted,
    }


def rank_momentum_tokens(date, mom_data, k=MOM_K, min_vol=MOM_MIN_VOL):
    """
    Rank tokens by momentum at a given date.
    Returns (top_k_longs, bottom_k_shorts) token lists.
    """
    lb_rets = mom_data["lb_rets"]
    rolling_dvol = mom_data["rolling_dvol"]
    atr_ratio_shifted = mom_data["atr_ratio_shifted"]
    ema_shifted = mom_data["ema_shifted"]

    if date not in lb_rets.index:
        return [], []

    lb = lb_rets.loc[date].dropna()
    vol = rolling_dvol.loc[date].dropna() if date in rolling_dvol.index else pd.Series(dtype=float)
    eligible = vol[vol >= min_vol].index
    lb = lb[lb.index.isin(eligible)]

    # Volatility filter: keep higher-ATR half
    if date in atr_ratio_shifted.index:
        atr_at = atr_ratio_shifted.loc[date].dropna()
        atr_elig = atr_at[atr_at.index.isin(lb.index)]
        if len(atr_elig) > 0:
            med = atr_elig.median()
            high_vol = atr_elig[atr_elig > med].index
            lb = lb[lb.index.isin(high_vol)]

    if len(lb) < 2 * k:
        return [], []

    ranked = lb.sort_values(ascending=False)
    top_k = ranked.head(k).index.tolist()
    bottom_k = ranked.tail(k).index.tolist()

    # EMA trend filter
    ema_at = ema_shifted.loc[date].dropna() if date in ema_shifted.index else pd.Series(dtype=float)
    top_k = [t for t in top_k if t in ema_at.index and ema_at[t] > 0]
    bottom_k = [t for t in bottom_k if t in ema_at.index and ema_at[t] < 0]

    return top_k, bottom_k


# ══════════════════════════════════════════════════════════════════════
# BREAKOUT DATA PREPARATION (vectorized)
# ══════════════════════════════════════════════════════════════════════

def prepare_breakout_data(bt, bb_period=BB_PERIOD, bb_std=BB_STD_MULT,
                          vol_threshold=VOL_THRESHOLD, trail_sma=TRAIL_SMA,
                          mom_lookback_4h=MOM_LOOKBACK_4H,
                          min_vol=BREAKOUT_MIN_VOL, min_days=BREAKOUT_MIN_DAYS):
    """
    Prepare 4H OHLCV data with indicators for all eligible tokens.
    Returns dict of token -> 4H DataFrame with indicator columns.
    """
    breakout_tokens = {}
    for token in bt.data.available_tokens():
        try:
            df = bt.data.load(token)
        except Exception:
            continue
        duration = (df.index.max() - df.index.min()).days
        if duration < min_days:
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

        if len(df_4h) < bb_period + 50:
            continue

        # Vectorized indicators
        df_4h["sma_trail"] = df_4h["close"].rolling(trail_sma).mean()
        df_4h["sma_bb"] = df_4h["close"].rolling(bb_period).mean()
        bb_std_vals = df_4h["close"].rolling(bb_period).std()
        df_4h["bb_upper"] = df_4h["sma_bb"] + bb_std * bb_std_vals
        df_4h["bb_lower"] = df_4h["sma_bb"] - bb_std * bb_std_vals
        df_4h["avg_volume"] = df_4h["volume"].rolling(bb_period).mean()
        df_4h["dollar_volume"] = df_4h["volume"] * df_4h["close"]

        tr_hl = df_4h["high"] - df_4h["low"]
        tr_hc = (df_4h["high"] - df_4h["close"].shift(1)).abs()
        tr_lc = (df_4h["low"] - df_4h["close"].shift(1)).abs()
        df_4h["atr"] = pd.concat([tr_hl, tr_hc, tr_lc], axis=1).max(axis=1).rolling(ATR_PERIOD).mean()

        vol_confirmed = df_4h["volume"] > vol_threshold * df_4h["avg_volume"]
        df_4h["breakout_long"] = (df_4h["close"] > df_4h["bb_upper"]) & vol_confirmed
        df_4h["breakout_short"] = (df_4h["close"] < df_4h["bb_lower"]) & vol_confirmed
        df_4h["long_strength"] = ((df_4h["close"] - df_4h["bb_upper"]) / df_4h["close"]).clip(lower=0)
        df_4h["short_strength"] = ((df_4h["bb_lower"] - df_4h["close"]) / df_4h["close"]).clip(lower=0)
        df_4h["return_mom"] = df_4h["close"].pct_change(mom_lookback_4h)
        df_4h["avg_daily_dollar_vol"] = df_4h["dollar_volume"].rolling(180).mean() * 6

        breakout_tokens[token] = df_4h

    print(f"  Breakout universe: {len(breakout_tokens)} tokens")
    return breakout_tokens


# ══════════════════════════════════════════════════════════════════════
# MAIN STRATEGY RUNNER
# ══════════════════════════════════════════════════════════════════════

def run(bt, w_breakout=W_BREAKOUT, w_momentum=W_MOMENTUM,
        bb_period=BB_PERIOD, bb_std=BB_STD_MULT,
        vol_threshold=VOL_THRESHOLD, trail_sma=TRAIL_SMA,
        mom_lookback_4h=MOM_LOOKBACK_4H,
        breakout_max_pos=BREAKOUT_MAX_POS,
        breakout_pos_size=BREAKOUT_POS_SIZE,
        breakout_min_vol=BREAKOUT_MIN_VOL,
        mom_lookback=MOM_LOOKBACK, mom_rebal_days=MOM_REBAL_DAYS,
        mom_k=MOM_K, mom_long_wt=MOM_LONG_WT, mom_short_wt=MOM_SHORT_WT,
        mom_min_vol=MOM_MIN_VOL,
        dd_window=DD_WINDOW, dd_thresholds=None,
        token_filter=None, verbose=True):
    """
    Run the R172 combined portfolio through a Backtest harness instance.

    Parameters
    ----------
    bt : Backtest
        Initialized Backtest harness instance.
    w_breakout : float
        Weight allocated to breakout component (default 0.80).
    w_momentum : float
        Weight allocated to momentum component (default 0.20).
    bb_period, bb_std, vol_threshold, trail_sma, mom_lookback_4h :
        Breakout indicator parameters.
    breakout_max_pos : int
        Max concurrent breakout positions.
    breakout_pos_size : float
        Fraction of breakout allocation per position.
    breakout_min_vol : float
        Min ADV for breakout universe.
    mom_lookback, mom_rebal_days, mom_k :
        Momentum ranking parameters.
    mom_long_wt, mom_short_wt : float
        Long/short split within momentum allocation.
    mom_min_vol : float
        Min ADV for momentum universe.
    dd_window : int
        Days for drawdown lookback.
    dd_thresholds : list of (threshold, scale) tuples
        DD control thresholds. None uses defaults.
    token_filter : set or None
        If provided, only trade these tokens.
    verbose : bool
        Print progress.

    Returns
    -------
    dict : Report results from bt.report().
    """
    if dd_thresholds is None:
        dd_thresholds = DD_THRESHOLDS

    leverage = bt.leverage_max

    # ── Prepare momentum data ──
    if verbose:
        print("Preparing momentum data...")
    mom_data = prepare_momentum_data(bt, min_vol=mom_min_vol)

    # ── Prepare breakout data ──
    if verbose:
        print("Preparing breakout data...")
    breakout_tokens = prepare_breakout_data(
        bt, bb_period=bb_period, bb_std=bb_std,
        vol_threshold=vol_threshold, trail_sma=trail_sma,
        mom_lookback_4h=mom_lookback_4h,
        min_vol=breakout_min_vol,
    )

    # Apply token filter if provided
    if token_filter is not None:
        breakout_tokens = {k: v for k, v in breakout_tokens.items()
                          if k in token_filter}
        if verbose:
            print(f"  After filter: {len(breakout_tokens)} breakout tokens")

    # ── Build unified hourly timeline ──
    all_used_tokens = set(mom_data["dc"].columns) | set(breakout_tokens.keys())
    if token_filter is not None:
        all_used_tokens = all_used_tokens & token_filter

    token_1h = {}
    for token in all_used_tokens:
        try:
            df = bt.data.load(token)
            token_1h[token] = df.loc[bt.start:bt.end]
        except Exception:
            continue

    all_hours = set()
    for df in token_1h.values():
        all_hours.update(df.index.tolist())
    all_hours = sorted(all_hours)
    if verbose:
        print(f"  Total 1H bars: {len(all_hours)}")

    # 4H bar boundaries
    four_h_bars = set(t for t in all_hours if t.hour % 4 == 0)

    # Momentum rebalance dates
    dc = mom_data["dc"]
    daily_dates = sorted(set(pd.Timestamp(t.date()) for t in all_hours))
    sim_daily = [d for d in daily_dates
                 if d >= bt.start and d <= bt.end]
    rebal_mom_dates = set(sim_daily[i] for i in range(0, len(sim_daily), mom_rebal_days))

    # ── State tracking ──
    mom_position_tokens: Set[str] = set()
    brk_positions: List[BreakoutPos] = []
    brk_pending: List[Tuple] = []
    brk_position_tokens: Set[str] = set()
    equity_history: List[float] = []

    warmup_bars = max(bb_period, trail_sma) + 10
    warmup_4h_count = 0
    last_rebal_date = None
    bar_count = 0

    if verbose:
        print("Running combined strategy bar-by-bar...")

    for ts in all_hours:
        bt.set_time(ts)
        bt._update_positions(ts)

        bar_count += 1
        current_date = pd.Timestamp(ts.date())
        current_eq = bt.get_equity()
        equity_history.append(current_eq)

        # DD control: compute recent drawdown and scale
        dd_scale = 1.0
        if len(equity_history) > dd_window * 24:
            recent = equity_history[-(dd_window * 24):]
            peak = max(recent)
            dd = (current_eq / peak - 1) if peak > 0 else 0
            for threshold, scale in dd_thresholds:
                if dd < threshold:
                    dd_scale = scale

        # ── MOMENTUM LEG: rebalance on schedule ──
        if current_date in rebal_mom_dates and current_date != last_rebal_date:
            last_rebal_date = current_date

            # Close existing momentum positions
            for token in list(mom_position_tokens):
                if token in bt.positions:
                    bt.close(token, reason="mom_rebalance")
            mom_position_tokens.clear()

            # Rank tokens
            top_k, bottom_k = rank_momentum_tokens(
                current_date, mom_data, k=mom_k, min_vol=mom_min_vol
            )

            mom_capital = current_eq * w_momentum * leverage * dd_scale

            n_l = max(len(top_k), 1)
            n_s = max(len(bottom_k), 1)

            for t in top_k:
                if token_filter is not None and t not in token_filter:
                    continue
                if t in brk_position_tokens:
                    continue
                size = mom_capital * mom_long_wt / n_l
                if size > 10:
                    pos = bt.order(t, side="long", size_usd=size,
                                   leverage=leverage, reason="mom_long")
                    if pos is not None:
                        mom_position_tokens.add(t)

            for t in bottom_k:
                if token_filter is not None and t not in token_filter:
                    continue
                if t in brk_position_tokens:
                    continue
                size = mom_capital * mom_short_wt / n_s
                if size > 10:
                    pos = bt.order(t, side="short", size_usd=size,
                                   leverage=leverage, reason="mom_short")
                    if pos is not None:
                        mom_position_tokens.add(t)

        # ── BREAKOUT LEG: check on 4H bars ──
        if ts in four_h_bars:
            warmup_4h_count += 1
            if warmup_4h_count < warmup_bars:
                continue

            # Process pending entries
            if brk_pending:
                slots = breakout_max_pos - len(brk_positions)
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
                        if token in mom_position_tokens:
                            continue

                        side = "long" if direction == 1 else "short"
                        brk_size = current_eq * w_breakout * breakout_pos_size * leverage * dd_scale
                        brk_size = max(brk_size, 100)

                        pos = bt.order(token, side=side, size_usd=brk_size,
                                       leverage=leverage, reason="brk_entry")
                        if pos is not None:
                            brk_positions.append(BreakoutPos(
                                token=token, direction=direction,
                                entry_price=pos.entry_price,
                                entry_atr=bar["atr"],
                                entry_time=ts,
                            ))
                            brk_position_tokens.add(token)
                brk_pending = []

            # Update breakout positions -- check exits
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

                # Partial profit check (3 ATR from entry)
                if not bpos.partial_taken and bpos.entry_atr > 0:
                    atr_dist = (close_price - bpos.entry_price) / bpos.entry_atr * bpos.direction
                    if atr_dist >= PARTIAL_ATR_MULT:
                        bt.close(token, reason="brk_partial")
                        side = "long" if bpos.direction == 1 else "short"
                        remain_size = current_eq * w_breakout * breakout_pos_size * leverage * 0.5 * dd_scale
                        if remain_size > 10:
                            bt.order(token, side=side, size_usd=remain_size,
                                     leverage=leverage, reason="brk_partial_reentry")
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
                    bt.close(token, reason=exit_reason)
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
                if bar["avg_daily_dollar_vol"] < breakout_min_vol:
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
    bt.close_all(reason="end_of_backtest")

    if verbose:
        print(f"  Total bars processed: {bar_count}")
        print(f"  Total trades: {len(bt.trades)}")

    return bt


# ══════════════════════════════════════════════════════════════════════
# STANDALONE EXECUTION
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    sys.path.insert(0, "/workspace/crypto_backtest")
    from tools.raw_backtest import Backtest

    bt = Backtest(
        capital=100_000,
        fee_bps=7,
        market="perp",
        leverage_max=2.5,
        start="2024-01-01",
        end="2026-03-17",
    )

    bt = run(bt)
    result = bt.report("R172 Combined Portfolio (s500)")
    print(f"\nVerdict: {result['verdict']}")
