#!/usr/bin/env python3
"""
R168: Multi-Strategy Portfolio Optimizer for Low Drawdown + High Return
Target: 300%+ last 12M return, MaxDD < 20%, Calmar > 3

Components:
  1. R162 — Cross-sectional momentum rotation (faithfully reproducing best variant)
  2. R160 — Volatility breakout rotation (Variant B: momentum filter)
  3. Mean Reversion (RSI extremes in range markets)
  4. Funding Carry

Uses exact same simulation logic as original R162/R160 scripts.
"""

import os
import sys
import warnings
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
from datetime import datetime

warnings.filterwarnings("ignore")

DATA_DIR = "/workspace/crypto_backtest/data/perp/1h_cache/"
RESULTS_PATH = "/workspace/crypto_backtest/research/R168_low_dd_results.md"
FULL_START = pd.Timestamp("2024-03-17")
FULL_END = pd.Timestamp("2026-03-17")
L12M_START = pd.Timestamp("2025-03-17")
L12M_END = pd.Timestamp("2026-03-17")
COST_BPS = 7  # per side
BORROW_RATE = 0.05  # 5% annual

def log(msg):
    print(msg)
    sys.stdout.flush()

# ── Data Loading ─────────────────────────────────────────────────────
log("Loading data...")

def load_all_symbols():
    symbols = {}
    for f in sorted(os.listdir(DATA_DIR)):
        if not f.endswith("_1h.parquet"):
            continue
        sym = f.replace("_1h.parquet", "")
        df = pd.read_parquet(os.path.join(DATA_DIR, f))
        df = df.sort_index()
        df = df[~df.index.duplicated(keep="first")]
        symbols[sym] = df
    return symbols

all_data = load_all_symbols()
log(f"Loaded {len(all_data)} symbols")

# ── Helper Functions ─────────────────────────────────────────────────

def compute_metrics(daily_returns, period_start=None, period_end=None):
    if period_start:
        daily_returns = daily_returns.loc[period_start:period_end]
    daily_returns = daily_returns.dropna()
    if len(daily_returns) < 10:
        return {"ann_ret": np.nan, "sharpe": np.nan, "maxdd": np.nan,
                "calmar": np.nan, "sortino": np.nan, "total_ret": np.nan}
    cum = (1 + daily_returns).cumprod()
    total_ret = cum.iloc[-1] / cum.iloc[0] - 1
    n_days = len(daily_returns)
    ann_factor = 365 / n_days
    ann_ret = (1 + total_ret) ** ann_factor - 1
    peak = cum.cummax()
    dd = (cum - peak) / peak
    maxdd = dd.min()
    mean_r = daily_returns.mean()
    std_r = daily_returns.std()
    sharpe = (mean_r / std_r) * np.sqrt(365) if std_r > 0 else 0
    down_r = daily_returns[daily_returns < 0]
    down_std = down_r.std() if len(down_r) > 0 else 1e-10
    sortino = (mean_r / down_std) * np.sqrt(365) if down_std > 0 else 0
    calmar = ann_ret / abs(maxdd) if abs(maxdd) > 1e-10 else 0
    return {"ann_ret": ann_ret, "sharpe": sharpe, "maxdd": maxdd,
            "calmar": calmar, "sortino": sortino, "total_ret": total_ret}

def eq_to_daily_returns(eq_series):
    """Convert equity series to daily returns."""
    daily_eq = eq_series.resample("1D").last().dropna()
    daily_eq = daily_eq.ffill()
    return daily_eq.pct_change().dropna()


# ══════════════════════════════════════════════════════════════════════
# COMPONENT 1: Cross-Sectional Momentum Rotation (R162)
# Faithful reproduction of the best variant:
# L7_R7_K3_80/20_EMA10/30_VF (Sharpe 1.39, L12M 289.6%)
# ══════════════════════════════════════════════════════════════════════
log("\n=== Component 1: Cross-Sectional Momentum Rotation (R162) ===")

def run_r162_momentum():
    """
    Exact reproduction of R162 momentum rotation.
    L=7, N=7, K=3, 80/20 long/short, EMA(10h)/EMA(30h) regime, Vol filter.
    """
    L = 7           # lookback days
    N = 7           # rebalance every N days
    K = 3           # top/bottom K tokens
    LONG_WT = 0.80
    SHORT_WT = 0.20
    EMA_FAST = 10   # hours
    EMA_SLOW = 30   # hours
    USE_VOL_FILTER = True
    MIN_VOL = 1_000_000

    # Load and filter tokens
    tokens = {}
    for sym, df in all_data.items():
        if df.index.min() >= FULL_START:
            continue
        pre_sim = df[df.index < FULL_START]
        if len(pre_sim) < 30 * 24:
            continue
        last_30d = pre_sim.tail(30 * 24)
        dvol = (last_30d["volume"] * last_30d["close"]).resample("1D").sum().mean()
        if dvol < MIN_VOL:
            continue
        tokens[sym] = df

    log(f"  R162 universe: {len(tokens)} tokens")

    # Prepare daily data
    daily_closes = {}
    daily_volumes = {}
    ema_signals = {}
    hourly_fundings = {}

    for ticker, df in tokens.items():
        daily_closes[ticker] = df["close"].resample("1D").last().dropna()
        dvol = (df["volume"] * df["close"]).resample("1D").sum()
        daily_volumes[ticker] = dvol

        # EMA regime signal on hourly, then daily last
        ema_fast = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
        ema_slow = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
        ema_diff = (ema_fast - ema_slow).resample("1D").last()
        ema_signals[ticker] = ema_diff

        hourly_fundings[ticker] = df["funding_1h"].fillna(0)

    daily_close = pd.DataFrame(daily_closes)
    daily_volume = pd.DataFrame(daily_volumes)
    ema_signal_df = pd.DataFrame(ema_signals)

    # Trim with buffer
    buffer_start = FULL_START - pd.Timedelta(days=40)
    daily_close = daily_close.loc[buffer_start:FULL_END]
    daily_volume = daily_volume.reindex(daily_close.index)
    ema_signal_df = ema_signal_df.reindex(daily_close.index)

    daily_returns = daily_close.pct_change()

    # Shifted lookback returns (look-ahead free)
    lb_rets_shifted = daily_close.shift(1) / daily_close.shift(1 + L) - 1

    # Shifted EMA signal (yesterday's signal)
    ema_signal_shifted = ema_signal_df.shift(1)

    # Rolling avg dollar volume (shifted)
    rolling_dvol = daily_volume.rolling(30, min_periods=15).mean()
    rolling_dvol_shifted = rolling_dvol.shift(1)

    # ATR/price ratio for volatility filter
    daily_highs = {}
    daily_lows = {}
    for ticker, df in tokens.items():
        daily_highs[ticker] = df["high"].resample("1D").max().dropna()
        daily_lows[ticker] = df["low"].resample("1D").min().dropna()

    df_high = pd.DataFrame(daily_highs).reindex(daily_close.index)
    df_low = pd.DataFrame(daily_lows).reindex(daily_close.index)
    prev_c = daily_close.shift(1)
    tr1 = df_high - df_low
    tr2 = (df_high - prev_c).abs()
    tr3 = (df_low - prev_c).abs()
    true_range = pd.concat([tr1, tr2, tr3]).groupby(level=0).max()
    true_range = true_range.reindex(daily_close.index)
    atr_14 = true_range.rolling(14, min_periods=7).mean()
    atr_ratio_shifted = (atr_14 / daily_close).shift(1)

    # Simulation
    sim_dates = daily_close.loc[FULL_START:FULL_END].index
    rebalance_indices = set(range(0, len(sim_dates), N))

    equity = 1.0
    equity_series = {}
    current_longs = {}
    current_shorts = {}
    prev_longs = set()
    prev_shorts = set()
    pending_longs = None
    pending_shorts = None
    fee = COST_BPS / 10000.0

    for i, date in enumerate(sim_dates):
        # Apply pending rebalance
        if pending_longs is not None:
            new_longs_set = set(pending_longs.keys())
            new_shorts_set = set(pending_shorts.keys())
            long_entries = new_longs_set - prev_longs
            long_exits = prev_longs - new_longs_set
            short_entries = new_shorts_set - prev_shorts
            short_exits = prev_shorts - new_shorts_set

            cost = 0.0
            for t in long_entries:
                cost += pending_longs[t] * fee
            for t in long_exits:
                cost += current_longs.get(t, 0) * fee
            for t in short_entries:
                cost += pending_shorts[t] * fee
            for t in short_exits:
                cost += current_shorts.get(t, 0) * fee

            equity *= (1 - cost)
            current_longs = pending_longs
            current_shorts = pending_shorts
            prev_longs = new_longs_set
            prev_shorts = new_shorts_set
            pending_longs = None
            pending_shorts = None

        # Daily PnL
        daily_pnl = 0.0
        daily_ret = daily_returns.loc[date] if date in daily_returns.index else pd.Series(dtype=float)

        for ticker, weight in current_longs.items():
            if ticker in daily_ret.index and not np.isnan(daily_ret[ticker]):
                daily_pnl += weight * daily_ret[ticker]
        for ticker, weight in current_shorts.items():
            if ticker in daily_ret.index and not np.isnan(daily_ret[ticker]):
                daily_pnl -= weight * daily_ret[ticker]

        # Funding
        f_start = date
        f_end = date + pd.Timedelta(hours=23)
        for ticker, weight in current_longs.items():
            if ticker in hourly_fundings:
                f_slice = hourly_fundings[ticker].loc[f_start:f_end]
                if len(f_slice) > 0:
                    daily_pnl += -f_slice.sum() * weight
        for ticker, weight in current_shorts.items():
            if ticker in hourly_fundings:
                f_slice = hourly_fundings[ticker].loc[f_start:f_end]
                if len(f_slice) > 0:
                    daily_pnl += f_slice.sum() * weight

        equity *= (1 + daily_pnl)
        equity_series[date] = equity

        # Rebalance decision
        if i in rebalance_indices:
            lb = lb_rets_shifted.loc[date].dropna() if date in lb_rets_shifted.index else pd.Series(dtype=float)
            vol = rolling_dvol_shifted.loc[date].dropna() if date in rolling_dvol_shifted.index else pd.Series(dtype=float)
            eligible = vol[vol >= MIN_VOL].index
            lb = lb[lb.index.isin(eligible)]

            # Volatility filter
            if USE_VOL_FILTER and date in atr_ratio_shifted.index:
                atr_at_date = atr_ratio_shifted.loc[date].dropna()
                atr_eligible = atr_at_date[atr_at_date.index.isin(lb.index)]
                if len(atr_eligible) > 0:
                    median_atr = atr_eligible.median()
                    high_vol = atr_eligible[atr_eligible > median_atr].index
                    lb = lb[lb.index.isin(high_vol)]

            if len(lb) >= 2 * K:
                ranked = lb.sort_values(ascending=False)
                top_k = ranked.head(K).index.tolist()
                bottom_k = ranked.tail(K).index.tolist()

                # Regime filter
                ema_at = ema_signal_shifted.loc[date].dropna() if date in ema_signal_shifted.index else pd.Series(dtype=float)
                top_k = [t for t in top_k if t in ema_at.index and ema_at[t] > 0]
                bottom_k = [t for t in bottom_k if t in ema_at.index and ema_at[t] < 0]

                n_l = max(len(top_k), 1)
                n_s = max(len(bottom_k), 1)
                pending_longs = {t: LONG_WT / n_l for t in top_k} if top_k else {}
                pending_shorts = {t: SHORT_WT / n_s for t in bottom_k} if bottom_k else {}

    eq = pd.Series(equity_series)
    return eq_to_daily_returns(eq)

mom_ret = run_r162_momentum()
m = compute_metrics(mom_ret, FULL_START, FULL_END)
m12 = compute_metrics(mom_ret, L12M_START, L12M_END)
log(f"  R162 Full: AnnRet={m['ann_ret']:.1%}, Sharpe={m['sharpe']:.2f}, MaxDD={m['maxdd']:.1%}")
log(f"  R162 L12M: Ret={m12['total_ret']:.1%}, Sharpe={m12['sharpe']:.2f}, MaxDD={m12['maxdd']:.1%}")


# ══════════════════════════════════════════════════════════════════════
# COMPONENT 2: Volatility Breakout Rotation (R160 Variant B)
# ══════════════════════════════════════════════════════════════════════
log("\n=== Component 2: Volatility Breakout Rotation (R160) ===")

@dataclass
class Position:
    token: str
    entry_time: pd.Timestamp
    entry_bar_idx: int
    entry_price: float
    direction: int
    size_frac: float
    leverage: float
    entry_atr: float
    partial_taken: bool = False
    remaining_frac: float = 1.0
    breakeven_stop: bool = False
    bars_held: int = 0

@dataclass
class Trade:
    token: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: int
    entry_price: float
    exit_price: float
    pnl_pct: float
    bars_held: int
    exit_reason: str

def run_r160_breakout():
    """
    Reproduction of R160 Variant B (momentum filter) at 1x leverage.
    Per-trade simulation with proper entry/exit logic.
    """
    BB_PERIOD = 20
    BB_STD_MULT = 2.0
    VOL_MULT = 1.5
    ATR_PERIOD = 20
    PARTIAL_ATR_MULT = 3.0
    PARTIAL_CLOSE_FRAC = 0.5
    MAX_HOLD_BARS = 180
    MOM_LOOKBACK_4H = 84  # 14 days in 4H bars
    TOP_N = 5
    MAX_POS = 10
    POS_SIZE = 0.20
    MIN_VOL = 2_000_000
    MIN_DATA_DAYS = 365
    fee = COST_BPS / 10000.0
    leverage = 1.0

    # Load and prepare 4H data for each token
    token_data = {}
    for sym, df in all_data.items():
        duration = (df.index.max() - df.index.min()).days
        if duration < MIN_DATA_DAYS:
            continue

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

        # Indicators
        df_4h["sma20"] = df_4h["close"].rolling(BB_PERIOD).mean()
        bb_std = df_4h["close"].rolling(BB_PERIOD).std()
        df_4h["bb_upper"] = df_4h["sma20"] + BB_STD_MULT * bb_std
        df_4h["bb_lower"] = df_4h["sma20"] - BB_STD_MULT * bb_std
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
        df_4h["return_14d"] = df_4h["close"].pct_change(MOM_LOOKBACK_4H)
        df_4h["avg_daily_dollar_vol"] = df_4h["dollar_volume"].rolling(180).mean() * 6

        token_data[sym] = df_4h

    log(f"  R160 universe: {len(token_data)} tokens")

    # Build aligned time index
    all_times = set()
    for df in token_data.values():
        all_times.update(df.index.tolist())
    all_times = sorted(all_times)

    # Build per-token arrays
    token_arrays = {}
    token_time_idx = {}
    for token, df in token_data.items():
        token_arrays[token] = {
            "index": df.index,
            "open": df["open"].values,
            "close": df["close"].values,
            "sma20": df["sma20"].values,
            "atr": df["atr"].values,
            "funding_4h": df["funding_4h"].values,
            "breakout_long": df["breakout_long"].values,
            "breakout_short": df["breakout_short"].values,
            "long_strength": df["long_strength"].values,
            "short_strength": df["short_strength"].values,
            "return_14d": df["return_14d"].values,
            "avg_daily_dollar_vol": df["avg_daily_dollar_vol"].values,
        }
        idx_map = {}
        for i, t in enumerate(df.index):
            idx_map[t] = i
        token_time_idx[token] = idx_map

    equity = 1.0
    equity_curve = {}
    positions: List[Position] = []
    trades: List[Trade] = []
    pending_entries = []
    warmup_bars = BB_PERIOD + 10
    warmup_time = all_times[min(warmup_bars, len(all_times) - 1)]

    for ti, current_time in enumerate(all_times):
        if current_time < warmup_time:
            equity_curve[current_time] = equity
            continue
        if equity <= 0.01:
            equity_curve[current_time] = max(equity, 0.0)
            continue

        # Process pending entries
        if pending_entries:
            slots = MAX_POS - len(positions)
            if slots > 0:
                pending_entries.sort(key=lambda x: x[2], reverse=True)
                to_enter = pending_entries[:min(TOP_N, slots)]
                for token, direction, strength in to_enter:
                    if token not in token_time_idx or current_time not in token_time_idx[token]:
                        continue
                    bar_idx = token_time_idx[token][current_time]
                    arr = token_arrays[token]
                    if np.isnan(arr["open"][bar_idx]) or np.isnan(arr["atr"][bar_idx]):
                        continue
                    if arr["atr"][bar_idx] <= 0:
                        continue
                    already_in = any(p.token == token and p.direction == direction for p in positions)
                    if already_in:
                        continue
                    equity -= fee * leverage * POS_SIZE
                    positions.append(Position(
                        token=token, entry_time=current_time, entry_bar_idx=bar_idx,
                        entry_price=arr["open"][bar_idx], direction=direction,
                        size_frac=POS_SIZE, leverage=leverage,
                        entry_atr=arr["atr"][bar_idx],
                    ))
            pending_entries = []

        # Update positions
        closed = []
        for pidx, pos in enumerate(positions):
            token = pos.token
            if token not in token_time_idx or current_time not in token_time_idx[token]:
                pos.bars_held += 1
                continue
            bar_idx = token_time_idx[token][current_time]
            arr = token_arrays[token]
            close_price = arr["close"][bar_idx]
            sma_val = arr["sma20"][bar_idx]
            atr_val = arr["atr"][bar_idx]

            if np.isnan(close_price) or np.isnan(sma_val):
                pos.bars_held += 1
                continue
            pos.bars_held += 1

            # PnL
            price_return = (close_price - pos.entry_price) / pos.entry_price * pos.direction
            effective_size = pos.size_frac * pos.remaining_frac * leverage
            pnl_contribution = price_return * effective_size

            # Funding
            funding_val = arr["funding_4h"][bar_idx] if not np.isnan(arr["funding_4h"][bar_idx]) else 0
            if pos.direction == 1:
                pnl_contribution -= funding_val * effective_size
            else:
                pnl_contribution += funding_val * effective_size

            # Check partial profit
            if not pos.partial_taken:
                atr_dist = (close_price - pos.entry_price) / pos.entry_atr * pos.direction
                if atr_dist >= PARTIAL_ATR_MULT:
                    partial_pnl = price_return * pos.size_frac * PARTIAL_CLOSE_FRAC * leverage
                    equity += partial_pnl
                    exit_cost = fee * leverage * pos.size_frac * PARTIAL_CLOSE_FRAC
                    equity -= exit_cost
                    trades.append(Trade(
                        token=token, entry_time=pos.entry_time, exit_time=current_time,
                        direction=pos.direction, entry_price=pos.entry_price,
                        exit_price=close_price, pnl_pct=partial_pnl,
                        bars_held=pos.bars_held, exit_reason="partial",
                    ))
                    pos.partial_taken = True
                    pos.remaining_frac = 1 - PARTIAL_CLOSE_FRAC
                    pos.breakeven_stop = True

            # Check exit: trail stop (SMA cross), max hold, breakeven stop
            exit_reason = None
            if pos.direction == 1 and close_price < sma_val:
                if pos.breakeven_stop:
                    if close_price < pos.entry_price:
                        exit_reason = "breakeven_stop"
                    else:
                        exit_reason = "trail_stop"
                else:
                    exit_reason = "trail_stop"
            elif pos.direction == -1 and close_price > sma_val:
                if pos.breakeven_stop:
                    if close_price > pos.entry_price:
                        exit_reason = "breakeven_stop"
                    else:
                        exit_reason = "trail_stop"
                else:
                    exit_reason = "trail_stop"

            if pos.bars_held >= MAX_HOLD_BARS:
                exit_reason = "max_hold"

            if exit_reason:
                remaining_pnl = price_return * pos.size_frac * pos.remaining_frac * leverage
                equity += remaining_pnl
                exit_cost = fee * leverage * pos.size_frac * pos.remaining_frac
                equity -= exit_cost
                trades.append(Trade(
                    token=token, entry_time=pos.entry_time, exit_time=current_time,
                    direction=pos.direction, entry_price=pos.entry_price,
                    exit_price=close_price, pnl_pct=remaining_pnl,
                    bars_held=pos.bars_held, exit_reason=exit_reason,
                ))
                closed.append(pidx)

        for pidx in sorted(closed, reverse=True):
            positions.pop(pidx)

        equity_curve[current_time] = equity

        # Scan for new breakouts (Variant B: momentum filter)
        for token, arr in token_arrays.items():
            if token not in token_time_idx or current_time not in token_time_idx[token]:
                continue
            bar_idx = token_time_idx[token][current_time]

            # Volume filter
            if np.isnan(arr["avg_daily_dollar_vol"][bar_idx]):
                continue
            if arr["avg_daily_dollar_vol"][bar_idx] < MIN_VOL:
                continue

            # Long breakout
            if arr["breakout_long"][bar_idx]:
                ret14 = arr["return_14d"][bar_idx]
                if not np.isnan(ret14) and ret14 > 0:  # Variant B
                    strength = arr["long_strength"][bar_idx]
                    if not np.isnan(strength) and strength > 0:
                        pending_entries.append((token, 1, strength))

            # Short breakout
            if arr["breakout_short"][bar_idx]:
                ret14 = arr["return_14d"][bar_idx]
                if not np.isnan(ret14) and ret14 < 0:  # Variant B
                    strength = arr["short_strength"][bar_idx]
                    if not np.isnan(strength) and strength > 0:
                        pending_entries.append((token, -1, strength))

    eq = pd.Series(equity_curve)
    return eq_to_daily_returns(eq)

breakout_ret = run_r160_breakout()
m = compute_metrics(breakout_ret, FULL_START, FULL_END)
m12 = compute_metrics(breakout_ret, L12M_START, L12M_END)
log(f"  R160 Full: AnnRet={m['ann_ret']:.1%}, Sharpe={m['sharpe']:.2f}, MaxDD={m['maxdd']:.1%}")
log(f"  R160 L12M: Ret={m12['total_ret']:.1%}, Sharpe={m12['sharpe']:.2f}, MaxDD={m12['maxdd']:.1%}")


# ══════════════════════════════════════════════════════════════════════
# COMPONENT 3: Mean Reversion (RSI extremes in range markets)
# ══════════════════════════════════════════════════════════════════════
log("\n=== Component 3: Mean Reversion ===")

def run_mean_reversion():
    """RSI-based mean reversion in range-bound markets."""
    MIN_VOL = 1_000_000
    TOP_N = 5
    fee = COST_BPS / 10000.0

    # Filter universe
    universe = []
    for sym, df in all_data.items():
        if df.index.min() >= FULL_START:
            continue
        pre_sim = df[df.index < FULL_START]
        if len(pre_sim) < 30 * 24:
            continue
        dvol = (pre_sim.tail(30*24)["volume"] * pre_sim.tail(30*24)["close"]).resample("1D").sum().mean()
        if dvol >= MIN_VOL:
            universe.append(sym)
    log(f"  MR universe: {len(universe)} tokens")

    # Build hourly panels for universe only
    common_idx = pd.date_range(FULL_START, FULL_END, freq="1h")
    cp = pd.DataFrame({s: all_data[s]["close"].reindex(common_idx) for s in universe}).ffill(limit=8)
    hp = pd.DataFrame({s: all_data[s]["high"].reindex(common_idx) for s in universe}).ffill(limit=8)
    lp = pd.DataFrame({s: all_data[s]["low"].reindex(common_idx) for s in universe}).ffill(limit=8)
    fp = pd.DataFrame({s: all_data[s]["funding_1h"].reindex(common_idx) for s in universe}).fillna(0)

    # RSI(14) with Wilder smoothing
    delta = cp.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, 1e-10))

    # ATR(20) on 1H
    prev_c = cp.shift(1)
    tr_df = pd.DataFrame(
        np.maximum(hp.values - lp.values,
                   np.maximum(np.abs(hp.values - prev_c.values),
                              np.abs(lp.values - prev_c.values))),
        index=cp.index, columns=universe
    )
    atr20 = tr_df.rolling(20, min_periods=10).mean()
    sma20 = cp.rolling(20, min_periods=10).mean()

    # Range condition
    in_range = (cp - sma20).abs() < atr20

    # Signals
    long_signal = (rsi < 25) & in_range
    short_signal = (rsi > 75) & in_range
    exit_signal = (rsi >= 40) & (rsi <= 60)

    # Position tracking per symbol
    positions_1h = pd.DataFrame(0.0, index=cp.index, columns=universe)

    for sym in universe:
        r = rsi[sym].values
        ls_v = long_signal[sym].values.astype(float)
        ss_v = short_signal[sym].values.astype(float)
        ex_v = exit_signal[sym].values.astype(float)
        pos = 0.0
        pos_arr = np.zeros(len(r))
        for i in range(1, len(r)):
            if np.isnan(r[i]):
                pos = 0.0
            elif pos != 0 and ex_v[i] > 0:
                pos = 0.0
            elif pos == 0:
                if ls_v[i] > 0:
                    pos = 1.0
                elif ss_v[i] > 0:
                    pos = -1.0
            pos_arr[i] = pos
        positions_1h[sym] = pos_arr

    # Select top N most extreme
    rsi_extreme = (rsi - 50).abs()
    extreme_rank = rsi_extreme.where(positions_1h != 0).rank(axis=1, ascending=False, method="first")
    pos_filtered = positions_1h.where(extreme_rank <= TOP_N, 0)
    total_active = (pos_filtered != 0).sum(axis=1).replace(0, np.nan)
    weights_1h = pos_filtered.div(total_active, axis=0).fillna(0)

    # Daily
    dc = cp.resample("1D").last().ffill()
    weights_daily = weights_1h.resample("1D").last().reindex(dc.index, method="ffill").fillna(0)
    daily_ret = dc.pct_change()

    # Compute returns with funding and costs
    port_ret = (weights_daily.shift(1) * daily_ret).sum(axis=1)

    # Funding
    funding_daily = fp.resample("1D").sum().reindex(dc.index, fill_value=0)
    fund_cost = -(weights_daily.shift(1) * funding_daily.reindex(weights_daily.index, fill_value=0)).sum(axis=1)
    port_ret = port_ret + fund_cost

    # Trading costs
    tc = weights_daily.diff().abs().sum(axis=1) * fee
    port_ret = port_ret - tc

    return port_ret.loc[FULL_START:FULL_END].fillna(0)

mr_ret = run_mean_reversion()
m = compute_metrics(mr_ret, FULL_START, FULL_END)
m12 = compute_metrics(mr_ret, L12M_START, L12M_END)
log(f"  MR Full: AnnRet={m['ann_ret']:.1%}, Sharpe={m['sharpe']:.2f}, MaxDD={m['maxdd']:.1%}")
log(f"  MR L12M: Ret={m12['total_ret']:.1%}, Sharpe={m12['sharpe']:.2f}, MaxDD={m12['maxdd']:.1%}")


# ══════════════════════════════════════════════════════════════════════
# COMPONENT 4: Funding Carry
# ══════════════════════════════════════════════════════════════════════
log("\n=== Component 4: Funding Carry ===")

def run_funding_carry():
    """Long most negative funding, short most positive. Weekly rebalance."""
    K = 3
    MIN_VOL = 1_000_000
    fee = COST_BPS / 10000.0

    # Filter universe
    universe = []
    for sym, df in all_data.items():
        if df.index.min() >= FULL_START:
            continue
        pre_sim = df[df.index < FULL_START]
        if len(pre_sim) < 30 * 24:
            continue
        dvol = (pre_sim.tail(30*24)["volume"] * pre_sim.tail(30*24)["close"]).resample("1D").sum().mean()
        if dvol >= MIN_VOL:
            universe.append(sym)
    log(f"  Carry universe: {len(universe)} tokens")

    # Daily data
    common_idx = pd.date_range(FULL_START, FULL_END, freq="1h")
    cp = pd.DataFrame({s: all_data[s]["close"].reindex(common_idx) for s in universe}).ffill(limit=8)
    fp = pd.DataFrame({s: all_data[s]["funding_1h"].reindex(common_idx) for s in universe}).fillna(0)

    dc = cp.resample("1D").last().ffill()
    daily_ret = dc.pct_change()
    funding_daily_avg = fp.resample("1D").mean().reindex(dc.index, method="ffill")
    funding_daily_sum = fp.resample("1D").sum().reindex(dc.index, fill_value=0)

    rebal_dates = dc.index[::7]
    positions = pd.DataFrame(0.0, index=dc.index, columns=universe)

    for i, d in enumerate(rebal_dates):
        if d < dc.index[7]:
            continue
        end_d = rebal_dates[i + 1] if i + 1 < len(rebal_dates) else dc.index[-1]

        # 7-day average funding
        lookback = d - pd.Timedelta(days=7)
        fund_window = funding_daily_avg.loc[lookback:d]
        if len(fund_window) < 3:
            continue
        avg_fund = fund_window.mean().dropna()
        if len(avg_fund) < 2 * K:
            continue

        ranked = avg_fund.rank()
        long_syms = avg_fund[ranked <= K].index.tolist()
        short_syms = avg_fund[ranked > len(avg_fund) - K].index.tolist()

        mask = (positions.index >= d) & (positions.index < end_d)
        positions.loc[mask] = 0.0
        if long_syms:
            w = 0.5 / len(long_syms)
            for s in long_syms:
                positions.loc[mask, s] = w
        if short_syms:
            w = 0.5 / len(short_syms)
            for s in short_syms:
                positions.loc[mask, s] = -w

    port_ret = (positions.shift(1) * daily_ret).sum(axis=1)
    fund_income = -(positions.shift(1) * funding_daily_sum.reindex(positions.index, fill_value=0)).sum(axis=1)
    port_ret = port_ret + fund_income
    tc = positions.diff().abs().sum(axis=1) * fee
    port_ret = port_ret - tc

    return port_ret.loc[FULL_START:FULL_END].fillna(0)

carry_ret = run_funding_carry()
m = compute_metrics(carry_ret, FULL_START, FULL_END)
m12 = compute_metrics(carry_ret, L12M_START, L12M_END)
log(f"  Carry Full: AnnRet={m['ann_ret']:.1%}, Sharpe={m['sharpe']:.2f}, MaxDD={m['maxdd']:.1%}")
log(f"  Carry L12M: Ret={m12['total_ret']:.1%}, Sharpe={m12['sharpe']:.2f}, MaxDD={m12['maxdd']:.1%}")


# ══════════════════════════════════════════════════════════════════════
# ALIGN ALL COMPONENT RETURNS
# ══════════════════════════════════════════════════════════════════════
log("\n=== Aligning component returns ===")

# Align to common daily index
idx = pd.date_range(FULL_START, FULL_END, freq="1D")
components = pd.DataFrame({
    "R162_Momentum": mom_ret.reindex(idx).fillna(0),
    "R160_Breakout": breakout_ret.reindex(idx).fillna(0),
    "MeanReversion": mr_ret.reindex(idx).fillna(0),
    "FundingCarry": carry_ret.reindex(idx).fillna(0),
})
log(f"Aligned: {len(components)} days, {components.columns.tolist()}")

# Component-Level Metrics
log("\n=== Component-Level Metrics (1x leverage, no DD control) ===")
comp_metrics = {}
for col in components.columns:
    m_full = compute_metrics(components[col], FULL_START, FULL_END)
    m_12m = compute_metrics(components[col], L12M_START, L12M_END)
    comp_metrics[col] = {"full": m_full, "l12m": m_12m}
    log(f"\n{col}:")
    log(f"  Full: AnnRet={m_full['ann_ret']:.1%}, Sharpe={m_full['sharpe']:.2f}, MaxDD={m_full['maxdd']:.1%}, Calmar={m_full['calmar']:.2f}")
    log(f"  L12M: Ret={m_12m['total_ret']:.1%}, Sharpe={m_12m['sharpe']:.2f}, MaxDD={m_12m['maxdd']:.1%}, Calmar={m_12m['calmar']:.2f}")

# Correlation matrix
log("\n=== Correlation Matrix (daily returns) ===")
corr = components.corr()
log(str(corr.round(3)))

# ══════════════════════════════════════════════════════════════════════
# PORTFOLIO CONSTRUCTION: Test all 105 configurations
# ══════════════════════════════════════════════════════════════════════
log("\n=== Testing 105 portfolio configurations ===")

ALLOCATIONS = [
    [0.50, 0.30, 0.10, 0.10],
    [0.40, 0.30, 0.15, 0.15],
    [0.30, 0.40, 0.15, 0.15],
    [0.30, 0.30, 0.20, 0.20],
    [0.60, 0.20, 0.10, 0.10],
]
LEVERAGES = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
DD_CONTROLS = [None, "20d", "30d"]


def apply_dd_control(daily_rets, window):
    if window is None:
        return daily_rets, 1.0
    equity = (1 + daily_rets).cumprod()
    w = int(window.replace("d", ""))
    peak = equity.rolling(w, min_periods=1).max()
    dd = (equity / peak - 1)
    scale = np.ones(len(dd))
    dd_vals = dd.values
    scale[dd_vals < -0.05] = 0.75
    scale[dd_vals < -0.10] = 0.50
    scale[dd_vals < -0.15] = 0.25
    scale[dd_vals < -0.20] = 0.00
    scale = pd.Series(scale, index=dd.index)
    adjusted = daily_rets * scale.shift(1).fillna(1.0)
    avg_scale = scale.mean()
    return adjusted, avg_scale


results = []
comp_vals = components.values

for alloc in ALLOCATIONS:
    alloc_arr = np.array(alloc)
    combined = pd.Series((comp_vals * alloc_arr[np.newaxis, :]).sum(axis=1), index=components.index)

    for lev in LEVERAGES:
        leveraged = combined * lev
        borrow = (lev - 1) * BORROW_RATE / 365 if lev > 1 else 0
        leveraged = leveraged - borrow

        for dd_ctrl in DD_CONTROLS:
            dd_label = dd_ctrl if dd_ctrl else "None"
            adjusted, avg_scale = apply_dd_control(leveraged, dd_ctrl)
            m_full = compute_metrics(adjusted, FULL_START, FULL_END)
            m_12m = compute_metrics(adjusted, L12M_START, L12M_END)
            results.append({
                "alloc": str(alloc),
                "leverage": lev,
                "dd_control": dd_label,
                "full_ann_ret": m_full["ann_ret"],
                "full_sharpe": m_full["sharpe"],
                "full_maxdd": m_full["maxdd"],
                "full_calmar": m_full["calmar"],
                "full_sortino": m_full["sortino"],
                "l12m_ret": m_12m["total_ret"],
                "l12m_sharpe": m_12m["sharpe"],
                "l12m_maxdd": m_12m["maxdd"],
                "l12m_calmar": m_12m["calmar"],
                "l12m_sortino": m_12m["sortino"],
                "avg_leverage": lev * avg_scale,
            })

results_df = pd.DataFrame(results)
results_df = results_df.sort_values("l12m_ret", ascending=False).reset_index(drop=True)
log(f"Completed {len(results_df)} configurations")

# ══════════════════════════════════════════════════════════════════════
# ANALYSIS & OUTPUT
# ══════════════════════════════════════════════════════════════════════
log("\n=== Generating results markdown ===")

# Pareto frontier
pareto = []
for idx, row in results_df.iterrows():
    dominated = any(
        (other["l12m_ret"] > row["l12m_ret"]) and (other["l12m_maxdd"] > row["l12m_maxdd"])
        for _, other in results_df.iterrows()
    )
    if not dominated:
        pareto.append(row)
pareto_df = pd.DataFrame(pareto) if pareto else pd.DataFrame()

# Target configs
target_300_25 = results_df[(results_df["l12m_ret"] >= 3.0) & (results_df["l12m_maxdd"] > -0.25)]
target_dd20 = results_df[results_df["l12m_maxdd"] > -0.20].sort_values("l12m_ret", ascending=False)

# Best near-target
if len(target_300_25) > 0:
    best_config = target_300_25.iloc[0]
elif len(target_dd20) > 0:
    best_config = target_dd20.iloc[0]
else:
    best_config = results_df.iloc[0]

# Monthly returns for best config
best_alloc = eval(best_config["alloc"])
best_lev = best_config["leverage"]
best_dd = best_config["dd_control"]
alloc_arr = np.array(best_alloc)
combined = pd.Series((comp_vals * alloc_arr[np.newaxis, :]).sum(axis=1), index=components.index)
leveraged = combined * best_lev - ((best_lev - 1) * BORROW_RATE / 365 if best_lev > 1 else 0)
adjusted, _ = apply_dd_control(leveraged, best_dd if best_dd != "None" else None)
monthly_rets = adjusted.resample("ME").apply(lambda x: (1 + x).prod() - 1)

# ══════════════════════════════════════════════════════════════════════
# WRITE MARKDOWN
# ══════════════════════════════════════════════════════════════════════

lines = []
lines.append("# R168: Low Drawdown Multi-Strategy Portfolio Optimizer")
lines.append(f"\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
lines.append(f"\nFull period: {FULL_START.date()} to {FULL_END.date()}")
lines.append(f"Last 12M: {L12M_START.date()} to {L12M_END.date()}")

# Section 1
lines.append("\n## 1. Component-Level Metrics (1x leverage, no DD control)")
lines.append("\n### Full Period")
lines.append("| Component | Ann Return | Sharpe | MaxDD | Calmar | Sortino |")
lines.append("|-----------|-----------|--------|-------|--------|---------|")
for col in components.columns:
    m = comp_metrics[col]["full"]
    lines.append(f"| {col} | {m['ann_ret']:.1%} | {m['sharpe']:.2f} | {m['maxdd']:.1%} | {m['calmar']:.2f} | {m['sortino']:.2f} |")

lines.append("\n### Last 12 Months")
lines.append("| Component | Return | Sharpe | MaxDD | Calmar | Sortino |")
lines.append("|-----------|--------|--------|-------|--------|---------|")
for col in components.columns:
    m = comp_metrics[col]["l12m"]
    lines.append(f"| {col} | {m['total_ret']:.1%} | {m['sharpe']:.2f} | {m['maxdd']:.1%} | {m['calmar']:.2f} | {m['sortino']:.2f} |")

# Section 2
lines.append("\n## 2. Correlation Matrix (Daily Returns)")
lines.append(f"\n| | {' | '.join(components.columns)} |")
lines.append(f"|{'|'.join(['---'] * (len(components.columns) + 1))}|")
for i, col in enumerate(components.columns):
    vals = " | ".join([f"{corr.iloc[i, j]:.3f}" for j in range(len(components.columns))])
    lines.append(f"| {col} | {vals} |")

# Section 3
lines.append("\n## 3. All 105 Configurations (sorted by Last 12M Return)")
lines.append("\n| Rank | Allocation | Lev | DD Ctrl | L12M Ret | L12M Sharpe | L12M MaxDD | L12M Calmar | Full AnnRet | Full Sharpe | Full MaxDD | Avg Lev |")
lines.append("|------|-----------|-----|---------|----------|-------------|------------|-------------|-------------|-------------|------------|---------|")
for idx, row in results_df.iterrows():
    lines.append(
        f"| {idx+1} | {row['alloc']} | {row['leverage']:.1f}x | {row['dd_control']} | "
        f"{row['l12m_ret']:.1%} | {row['l12m_sharpe']:.2f} | {row['l12m_maxdd']:.1%} | "
        f"{row['l12m_calmar']:.2f} | {row['full_ann_ret']:.1%} | {row['full_sharpe']:.2f} | "
        f"{row['full_maxdd']:.1%} | {row['avg_leverage']:.2f} |"
    )

# Section 4
lines.append("\n## 4. Pareto Frontier (Return vs MaxDD)")
if len(pareto_df) > 0:
    lines.append("\n| Allocation | Lev | DD Ctrl | L12M Ret | L12M MaxDD | L12M Calmar | L12M Sharpe |")
    lines.append("|-----------|-----|---------|----------|------------|-------------|-------------|")
    for _, row in pareto_df.sort_values("l12m_ret", ascending=False).iterrows():
        lines.append(
            f"| {row['alloc']} | {row['leverage']:.1f}x | {row['dd_control']} | "
            f"{row['l12m_ret']:.1%} | {row['l12m_maxdd']:.1%} | {row['l12m_calmar']:.2f} | {row['l12m_sharpe']:.2f} |"
        )

# Section 5
lines.append("\n## 5. Configs Achieving 300%+ Return AND MaxDD < 25%")
if len(target_300_25) > 0:
    lines.append(f"\n**{len(target_300_25)} configurations found:**\n")
    lines.append("| Allocation | Lev | DD Ctrl | L12M Ret | L12M MaxDD | L12M Calmar | L12M Sharpe | Full MaxDD |")
    lines.append("|-----------|-----|---------|----------|------------|-------------|-------------|------------|")
    for _, row in target_300_25.iterrows():
        lines.append(
            f"| {row['alloc']} | {row['leverage']:.1f}x | {row['dd_control']} | "
            f"{row['l12m_ret']:.1%} | {row['l12m_maxdd']:.1%} | {row['l12m_calmar']:.2f} | "
            f"{row['l12m_sharpe']:.2f} | {row['full_maxdd']:.1%} |"
        )
else:
    lines.append("\n**No configurations achieve both 300%+ return AND MaxDD < 25%.**")
    lines.append("\nClosest configurations (MaxDD < 40%, sorted by return):")
    close_df = results_df[results_df["l12m_maxdd"] > -0.40].head(15)
    if len(close_df) > 0:
        lines.append("\n| Allocation | Lev | DD Ctrl | L12M Ret | L12M MaxDD | L12M Calmar | L12M Sharpe |")
        lines.append("|-----------|-----|---------|----------|------------|-------------|-------------|")
        for _, row in close_df.iterrows():
            lines.append(
                f"| {row['alloc']} | {row['leverage']:.1f}x | {row['dd_control']} | "
                f"{row['l12m_ret']:.1%} | {row['l12m_maxdd']:.1%} | {row['l12m_calmar']:.2f} | {row['l12m_sharpe']:.2f} |"
            )

# Section 6
lines.append("\n## 6. Configs Achieving MaxDD < 20% (sorted by return)")
if len(target_dd20) > 0:
    lines.append(f"\n**{len(target_dd20)} configurations with MaxDD < 20%:**\n")
    lines.append("| Allocation | Lev | DD Ctrl | L12M Ret | L12M MaxDD | L12M Calmar | L12M Sharpe | Full AnnRet |")
    lines.append("|-----------|-----|---------|----------|------------|-------------|-------------|-------------|")
    for _, row in target_dd20.head(30).iterrows():
        lines.append(
            f"| {row['alloc']} | {row['leverage']:.1f}x | {row['dd_control']} | "
            f"{row['l12m_ret']:.1%} | {row['l12m_maxdd']:.1%} | {row['l12m_calmar']:.2f} | "
            f"{row['l12m_sharpe']:.2f} | {row['full_ann_ret']:.1%} |"
        )
    if len(target_dd20) > 30:
        lines.append(f"\n... and {len(target_dd20) - 30} more configurations")
else:
    lines.append("\n**No configurations achieve MaxDD < 20%.**")
    lowest_dd = results_df.nlargest(10, "l12m_maxdd")
    lines.append("\nLowest drawdown configurations:")
    lines.append("\n| Allocation | Lev | DD Ctrl | L12M Ret | L12M MaxDD | L12M Calmar |")
    lines.append("|-----------|-----|---------|----------|------------|-------------|")
    for _, row in lowest_dd.iterrows():
        lines.append(
            f"| {row['alloc']} | {row['leverage']:.1f}x | {row['dd_control']} | "
            f"{row['l12m_ret']:.1%} | {row['l12m_maxdd']:.1%} | {row['l12m_calmar']:.2f} |"
        )

# Section 7
lines.append("\n## 7. Monthly Returns -- Best Near-Target Config")
lines.append(f"\n**Config:** Allocation={best_config['alloc']}, Leverage={best_config['leverage']:.1f}x, DD Control={best_config['dd_control']}")
lines.append(f"**L12M Return:** {best_config['l12m_ret']:.1%}, **MaxDD:** {best_config['l12m_maxdd']:.1%}, **Calmar:** {best_config['l12m_calmar']:.2f}")
lines.append(f"\n| Month | Return | Cumulative |")
lines.append("|-------|--------|------------|")
cum = 1.0
for date, ret in monthly_rets.items():
    cum *= (1 + ret)
    lines.append(f"| {date.strftime('%Y-%m')} | {ret:.1%} | {cum - 1:.1%} |")

# Summary
lines.append("\n## Summary")
lines.append(f"\n- **Total configurations tested:** {len(results_df)}")
lines.append(f"- **Configs with 300%+ L12M AND MaxDD < 25%:** {len(target_300_25)}")
lines.append(f"- **Configs with MaxDD < 20%:** {len(target_dd20)}")
if len(results_df) > 0:
    best_overall = results_df.iloc[0]
    lines.append(f"- **Highest L12M return:** {best_overall['l12m_ret']:.1%} (alloc={best_overall['alloc']}, lev={best_overall['leverage']:.1f}x, dd={best_overall['dd_control']})")
    lowest = results_df.nlargest(1, "l12m_maxdd").iloc[0]
    lines.append(f"- **Lowest MaxDD:** {lowest['l12m_maxdd']:.1%} (alloc={lowest['alloc']}, lev={lowest['leverage']:.1f}x, dd={lowest['dd_control']})")

md_content = "\n".join(lines)
with open(RESULTS_PATH, "w") as f:
    f.write(md_content)

log(f"\nResults saved to {RESULTS_PATH}")
log("\n" + "=" * 80)
log(md_content)
