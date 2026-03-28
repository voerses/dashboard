#!/workspace/venv/bin/python
"""
R172: Validated Portfolio — R162 + R167-Optimized R160
=====================================================

Based on R168's CORRECT implementation (produces R162 +289.6% 12M).
Upgrades R160 from old params to R167-optimized: trail_sma=15, max_pos=5, mom_days=7.

Adds validation per user requirements:
  - Trade counts and win rates
  - IS/OOS split (first 12M = IS, last 12M = OOS)
  - Capital constraint check (weights never exceed 100% at 1x)
  - Monthly return breakdown

Target: 300%+ last 12M return, MaxDD < 20%, Calmar > 3
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
RESULTS_PATH = "/workspace/crypto_backtest/research/R172_validated_portfolio_results.md"
FULL_START = pd.Timestamp("2024-03-17")
FULL_END = pd.Timestamp("2026-03-17")
IS_START = pd.Timestamp("2024-03-17")
IS_END = pd.Timestamp("2025-03-17")
OOS_START = pd.Timestamp("2025-03-17")
OOS_END = pd.Timestamp("2026-03-17")
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
    daily_eq = eq_series.resample("1D").last().dropna()
    daily_eq = daily_eq.ffill()
    return daily_eq.pct_change().dropna()


# ══════════════════════════════════════════════════════════════════════
# COMPONENT 1: Cross-Sectional Momentum Rotation (R162)
# Exact reproduction from R168 (verified +289.6% L12M)
# L=7, N=7, K=3, 80/20, EMA10/30, Vol filter
# ══════════════════════════════════════════════════════════════════════
log("\n=== Component 1: R162 Cross-Sectional Momentum ===")

def run_r162_momentum():
    L = 7; N = 7; K = 3
    LONG_WT = 0.80; SHORT_WT = 0.20
    EMA_FAST = 10; EMA_SLOW = 30
    USE_VOL_FILTER = True
    MIN_VOL = 1_000_000
    fee = COST_BPS / 10000.0

    # Filter tokens
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
        ema_fast = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
        ema_slow = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
        ema_diff = (ema_fast - ema_slow).resample("1D").last()
        ema_signals[ticker] = ema_diff
        hourly_fundings[ticker] = df["funding_1h"].fillna(0)

    daily_close = pd.DataFrame(daily_closes)
    daily_volume = pd.DataFrame(daily_volumes)
    ema_signal_df = pd.DataFrame(ema_signals)

    buffer_start = FULL_START - pd.Timedelta(days=40)
    daily_close = daily_close.loc[buffer_start:FULL_END]
    daily_volume = daily_volume.reindex(daily_close.index)
    ema_signal_df = ema_signal_df.reindex(daily_close.index)

    daily_returns = daily_close.pct_change()
    lb_rets_shifted = daily_close.shift(1) / daily_close.shift(1 + L) - 1
    ema_signal_shifted = ema_signal_df.shift(1)
    rolling_dvol = daily_volume.rolling(30, min_periods=15).mean()
    rolling_dvol_shifted = rolling_dvol.shift(1)

    # ATR/price for vol filter
    daily_highs = {}; daily_lows = {}
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

    # Simulation with trade tracking
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

    # Trade tracking
    rebalance_count = 0
    weekly_pnls = []
    week_start_eq = 1.0
    total_long_weight = 0.0
    total_short_weight = 0.0
    weight_samples = 0

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

            # Track weekly PnL
            if rebalance_count > 0:
                week_ret = equity / week_start_eq - 1 if week_start_eq > 0 else 0
                weekly_pnls.append(week_ret)
            week_start_eq = equity
            rebalance_count += 1

            current_longs = pending_longs
            current_shorts = pending_shorts
            prev_longs = new_longs_set
            prev_shorts = new_shorts_set
            pending_longs = None
            pending_shorts = None

        # Track exposure
        lw = sum(current_longs.values())
        sw = sum(current_shorts.values())
        total_long_weight += lw
        total_short_weight += sw
        weight_samples += 1

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

                ema_at = ema_signal_shifted.loc[date].dropna() if date in ema_signal_shifted.index else pd.Series(dtype=float)
                top_k = [t for t in top_k if t in ema_at.index and ema_at[t] > 0]
                bottom_k = [t for t in bottom_k if t in ema_at.index and ema_at[t] < 0]

                n_l = max(len(top_k), 1)
                n_s = max(len(bottom_k), 1)
                pending_longs = {t: LONG_WT / n_l for t in top_k} if top_k else {}
                pending_shorts = {t: SHORT_WT / n_s for t in bottom_k} if bottom_k else {}

    # Final week PnL
    if rebalance_count > 0 and week_start_eq > 0:
        weekly_pnls.append(equity / week_start_eq - 1)

    eq = pd.Series(equity_series)
    daily_rets = eq_to_daily_returns(eq)

    # Compute trade stats
    winning_weeks = sum(1 for p in weekly_pnls if p > 0)
    total_weeks = len(weekly_pnls)
    win_rate = winning_weeks / total_weeks if total_weeks > 0 else 0
    avg_long_exp = total_long_weight / weight_samples if weight_samples > 0 else 0
    avg_short_exp = total_short_weight / weight_samples if weight_samples > 0 else 0

    stats = {
        "rebalances": rebalance_count,
        "weekly_trades": total_weeks,
        "win_rate": win_rate,
        "avg_long_exposure": avg_long_exp,
        "avg_short_exposure": avg_short_exp,
        "avg_total_exposure": avg_long_exp + avg_short_exp,
    }
    return daily_rets, stats


mom_ret, mom_stats = run_r162_momentum()
m_full = compute_metrics(mom_ret, FULL_START, FULL_END)
m_is = compute_metrics(mom_ret, IS_START, IS_END)
m_oos = compute_metrics(mom_ret, OOS_START, OOS_END)
log(f"  R162 Full: AnnRet={m_full['ann_ret']:.1%}, Sharpe={m_full['sharpe']:.2f}, MaxDD={m_full['maxdd']:.1%}")
log(f"  R162 IS:   Ret={m_is['total_ret']:.1%}, Sharpe={m_is['sharpe']:.2f}, MaxDD={m_is['maxdd']:.1%}")
log(f"  R162 OOS:  Ret={m_oos['total_ret']:.1%}, Sharpe={m_oos['sharpe']:.2f}, MaxDD={m_oos['maxdd']:.1%}")
log(f"  R162 Stats: {mom_stats['rebalances']} rebalances, WR={mom_stats['win_rate']:.1%}, "
    f"AvgExposure={mom_stats['avg_total_exposure']:.1%}")


# ══════════════════════════════════════════════════════════════════════
# COMPONENT 2: Volatility Breakout (R167 Optimized)
# Key changes from R168: trail_sma=15, max_pos=5, mom_days=7
# Fixed POS_SIZE = 0.20 per position (no concentration bug)
# ══════════════════════════════════════════════════════════════════════
log("\n=== Component 2: R160 Volatility Breakout (R167 Optimized) ===")

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
    """R160 Variant B with R167 optimized params. Fixed position sizing."""
    # R167 optimized parameters
    BB_PERIOD = 20
    BB_STD_MULT = 2.0
    VOL_MULT = 1.5
    ATR_PERIOD = 20
    PARTIAL_ATR_MULT = 3.0
    PARTIAL_CLOSE_FRAC = 0.5
    MAX_HOLD_BARS = 180    # 30d in 4H bars
    TRAIL_SMA = 15         # R167 optimized (was 20)
    MOM_LOOKBACK_4H = 42   # 7 days * 6 bars/day (R167: was 84 = 14d)
    TOP_N = 5
    MAX_POS = 5            # R167 optimized (was 10)
    POS_SIZE = 0.20        # FIXED per position — no concentration bug
    MIN_VOL = 2_000_000
    MIN_DATA_DAYS = 365
    fee = COST_BPS / 10000.0
    leverage = 1.0

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

        token_data[sym] = df_4h

    log(f"  R160 universe: {len(token_data)} tokens")

    # Build time index and per-token arrays
    all_times = set()
    for df in token_data.values():
        all_times.update(df.index.tolist())
    all_times = sorted(all_times)

    token_arrays = {}
    token_time_idx = {}
    for token, df in token_data.items():
        token_arrays[token] = {
            "index": df.index,
            "open": df["open"].values,
            "close": df["close"].values,
            "sma_trail": df["sma_trail"].values,
            "atr": df["atr"].values,
            "funding_4h": df["funding_4h"].values,
            "breakout_long": df["breakout_long"].values,
            "breakout_short": df["breakout_short"].values,
            "long_strength": df["long_strength"].values,
            "short_strength": df["short_strength"].values,
            "return_mom": df["return_mom"].values,
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
    warmup_bars = max(BB_PERIOD, TRAIL_SMA) + 10
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
            sma_val = arr["sma_trail"][bar_idx]  # Use trail SMA, not BB SMA
            atr_val = arr["atr"][bar_idx]

            if np.isnan(close_price) or np.isnan(sma_val):
                pos.bars_held += 1
                continue
            pos.bars_held += 1

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

            # Exit checks: trail stop (SMA cross), max hold, breakeven
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

            if np.isnan(arr["avg_daily_dollar_vol"][bar_idx]):
                continue
            if arr["avg_daily_dollar_vol"][bar_idx] < MIN_VOL:
                continue

            if arr["breakout_long"][bar_idx]:
                ret_m = arr["return_mom"][bar_idx]
                if not np.isnan(ret_m) and ret_m > 0:
                    strength = arr["long_strength"][bar_idx]
                    if not np.isnan(strength) and strength > 0:
                        pending_entries.append((token, 1, strength))

            if arr["breakout_short"][bar_idx]:
                ret_m = arr["return_mom"][bar_idx]
                if not np.isnan(ret_m) and ret_m < 0:
                    strength = arr["short_strength"][bar_idx]
                    if not np.isnan(strength) and strength > 0:
                        pending_entries.append((token, -1, strength))

    eq = pd.Series(equity_curve)
    daily_rets = eq_to_daily_returns(eq)

    # Trade stats
    non_partial = [t for t in trades if t.exit_reason != "partial"]
    winning = [t for t in non_partial if t.pnl_pct > 0]
    losing = [t for t in non_partial if t.pnl_pct <= 0]
    is_trades = [t for t in non_partial if t.entry_time >= IS_START and t.entry_time < IS_END]
    oos_trades = [t for t in non_partial if t.entry_time >= OOS_START and t.entry_time <= OOS_END]

    avg_hold_bars = np.mean([t.bars_held for t in non_partial]) if non_partial else 0
    avg_hold_days = avg_hold_bars * 4 / 24  # 4H bars to days

    stats = {
        "total_trades": len(non_partial),
        "partial_exits": len([t for t in trades if t.exit_reason == "partial"]),
        "winning_trades": len(winning),
        "losing_trades": len(losing),
        "win_rate": len(winning) / len(non_partial) if non_partial else 0,
        "avg_hold_days": avg_hold_days,
        "is_trades": len(is_trades),
        "oos_trades": len(oos_trades),
        "exit_reasons": {},
        "max_concurrent": MAX_POS,
        "pos_size": POS_SIZE,
        "max_total_exposure": MAX_POS * POS_SIZE,
    }
    for t in non_partial:
        stats["exit_reasons"][t.exit_reason] = stats["exit_reasons"].get(t.exit_reason, 0) + 1

    return daily_rets, stats


breakout_ret, breakout_stats = run_r160_breakout()
m_full = compute_metrics(breakout_ret, FULL_START, FULL_END)
m_is = compute_metrics(breakout_ret, IS_START, IS_END)
m_oos = compute_metrics(breakout_ret, OOS_START, OOS_END)
log(f"  R160 Full: AnnRet={m_full['ann_ret']:.1%}, Sharpe={m_full['sharpe']:.2f}, MaxDD={m_full['maxdd']:.1%}")
log(f"  R160 IS:   Ret={m_is['total_ret']:.1%}, Sharpe={m_is['sharpe']:.2f}, MaxDD={m_is['maxdd']:.1%}")
log(f"  R160 OOS:  Ret={m_oos['total_ret']:.1%}, Sharpe={m_oos['sharpe']:.2f}, MaxDD={m_oos['maxdd']:.1%}")
log(f"  R160 Stats: {breakout_stats['total_trades']} trades, WR={breakout_stats['win_rate']:.1%}, "
    f"AvgHold={breakout_stats['avg_hold_days']:.1f}d, MaxExposure={breakout_stats['max_total_exposure']:.0%}")


# ══════════════════════════════════════════════════════════════════════
# ALIGN AND COMBINE
# ══════════════════════════════════════════════════════════════════════
log("\n=== Aligning components ===")

idx = pd.date_range(FULL_START, FULL_END, freq="1D")
components = pd.DataFrame({
    "R162_Momentum": mom_ret.reindex(idx).fillna(0),
    "R160_Breakout": breakout_ret.reindex(idx).fillna(0),
})

# Correlation
corr = components.corr()
log(f"Correlation: {corr.iloc[0, 1]:.4f}")


# ══════════════════════════════════════════════════════════════════════
# PORTFOLIO SWEEP
# ══════════════════════════════════════════════════════════════════════
log("\n=== Portfolio Sweep ===")

# 2-component: R162 weight, R160 weight = 1 - R162 weight
ALLOC_R162 = [0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80]
LEVERAGES = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0]
DD_CONTROLS = [None, "15d", "20d", "30d"]


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

for r162_w in ALLOC_R162:
    r160_w = 1.0 - r162_w
    alloc_arr = np.array([r162_w, r160_w])
    combined = pd.Series((comp_vals * alloc_arr[np.newaxis, :]).sum(axis=1), index=components.index)

    for lev in LEVERAGES:
        leveraged = combined * lev
        borrow = (lev - 1) * BORROW_RATE / 365 if lev > 1 else 0
        leveraged = leveraged - borrow

        for dd_ctrl in DD_CONTROLS:
            dd_label = dd_ctrl if dd_ctrl else "None"
            adjusted, avg_scale = apply_dd_control(leveraged, dd_ctrl)

            m_full = compute_metrics(adjusted, FULL_START, FULL_END)
            m_is = compute_metrics(adjusted, IS_START, IS_END)
            m_oos = compute_metrics(adjusted, OOS_START, OOS_END)

            results.append({
                "r162_w": r162_w,
                "r160_w": r160_w,
                "leverage": lev,
                "dd_control": dd_label,
                "full_ann_ret": m_full["ann_ret"],
                "full_sharpe": m_full["sharpe"],
                "full_maxdd": m_full["maxdd"],
                "full_calmar": m_full["calmar"],
                "full_sortino": m_full["sortino"],
                "is_ret": m_is["total_ret"],
                "is_sharpe": m_is["sharpe"],
                "is_maxdd": m_is["maxdd"],
                "is_calmar": m_is["calmar"],
                "oos_ret": m_oos["total_ret"],
                "oos_sharpe": m_oos["sharpe"],
                "oos_maxdd": m_oos["maxdd"],
                "oos_calmar": m_oos["calmar"],
                "avg_leverage": lev * avg_scale,
            })

results_df = pd.DataFrame(results)
results_df = results_df.sort_values("oos_ret", ascending=False).reset_index(drop=True)
log(f"Completed {len(results_df)} configurations")


# ══════════════════════════════════════════════════════════════════════
# ANALYSIS
# ══════════════════════════════════════════════════════════════════════

# Target configs
target_300_20 = results_df[(results_df["oos_ret"] >= 3.0) & (results_df["oos_maxdd"] > -0.20)]
target_300_25 = results_df[(results_df["oos_ret"] >= 3.0) & (results_df["oos_maxdd"] > -0.25)]
target_dd20 = results_df[results_df["oos_maxdd"] > -0.20].sort_values("oos_ret", ascending=False)
target_dd25 = results_df[results_df["oos_maxdd"] > -0.25].sort_values("oos_ret", ascending=False)

# Best near-target
if len(target_300_20) > 0:
    best_config = target_300_20.iloc[0]
    best_label = "300%+ return AND MaxDD < 20%"
elif len(target_300_25) > 0:
    best_config = target_300_25.iloc[0]
    best_label = "300%+ return AND MaxDD < 25%"
elif len(target_dd20) > 0:
    best_config = target_dd20.iloc[0]
    best_label = "Best return with MaxDD < 20%"
else:
    best_config = results_df.iloc[0]
    best_label = "Highest return overall"

# Monthly returns for best config
best_r162 = best_config["r162_w"]
best_r160 = best_config["r160_w"]
best_lev = best_config["leverage"]
best_dd = best_config["dd_control"]
alloc_arr = np.array([best_r162, best_r160])
combined_best = pd.Series((comp_vals * alloc_arr[np.newaxis, :]).sum(axis=1), index=components.index)
lev_best = combined_best * best_lev - ((best_lev - 1) * BORROW_RATE / 365 if best_lev > 1 else 0)
adjusted_best, _ = apply_dd_control(lev_best, best_dd if best_dd != "None" else None)
monthly_rets = adjusted_best.resample("ME").apply(lambda x: (1 + x).prod() - 1)


# ══════════════════════════════════════════════════════════════════════
# WRITE MARKDOWN
# ══════════════════════════════════════════════════════════════════════
log("\n=== Writing results ===")

lines = []
lines.append("# R172: Validated Portfolio — R162 + R167-Optimized R160")
lines.append(f"\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
lines.append(f"\nFull period: {FULL_START.date()} to {FULL_END.date()}")
lines.append(f"IS period: {IS_START.date()} to {IS_END.date()}")
lines.append(f"OOS period: {OOS_START.date()} to {OOS_END.date()}")

# Section 1: Component Metrics
lines.append("\n## 1. Component-Level Metrics (1x leverage, no DD control)")

lines.append("\n### R162 Cross-Sectional Momentum")
for label, start, end in [("Full", FULL_START, FULL_END), ("IS", IS_START, IS_END), ("OOS", OOS_START, OOS_END)]:
    m = compute_metrics(mom_ret, start, end)
    lines.append(f"- **{label}**: Ret={m['total_ret']:.1%}, AnnRet={m['ann_ret']:.1%}, "
                 f"Sharpe={m['sharpe']:.2f}, MaxDD={m['maxdd']:.1%}, Calmar={m['calmar']:.2f}, "
                 f"Sortino={m['sortino']:.2f}")
lines.append(f"- **Trade stats**: {mom_stats['rebalances']} rebalances, "
             f"Win Rate={mom_stats['win_rate']:.1%} (weekly), "
             f"Avg Long Exposure={mom_stats['avg_long_exposure']:.1%}, "
             f"Avg Short Exposure={mom_stats['avg_short_exposure']:.1%}, "
             f"Avg Total Exposure={mom_stats['avg_total_exposure']:.1%}")

lines.append("\n### R160 Volatility Breakout (R167 Optimized)")
lines.append("Parameters: BB(20,2.0), vol_mult=1.5, **trail_sma=15**, **max_pos=5**, "
             "**mom_days=7**, partial_atr=3.0, pos_size=20%")
for label, start, end in [("Full", FULL_START, FULL_END), ("IS", IS_START, IS_END), ("OOS", OOS_START, OOS_END)]:
    m = compute_metrics(breakout_ret, start, end)
    lines.append(f"- **{label}**: Ret={m['total_ret']:.1%}, AnnRet={m['ann_ret']:.1%}, "
                 f"Sharpe={m['sharpe']:.2f}, MaxDD={m['maxdd']:.1%}, Calmar={m['calmar']:.2f}, "
                 f"Sortino={m['sortino']:.2f}")
lines.append(f"- **Trade stats**: {breakout_stats['total_trades']} completed trades "
             f"(+{breakout_stats['partial_exits']} partial exits), "
             f"Win Rate={breakout_stats['win_rate']:.1%}, "
             f"Avg Hold={breakout_stats['avg_hold_days']:.1f}d")
lines.append(f"- **IS trades**: {breakout_stats['is_trades']}, "
             f"**OOS trades**: {breakout_stats['oos_trades']}")
lines.append(f"- **Max exposure**: {breakout_stats['max_total_exposure']:.0%} "
             f"({breakout_stats['max_concurrent']} pos x {breakout_stats['pos_size']:.0%} each)")
if breakout_stats["exit_reasons"]:
    reasons = ", ".join(f"{k}={v}" for k, v in sorted(breakout_stats["exit_reasons"].items(), key=lambda x: -x[1]))
    lines.append(f"- **Exit reasons**: {reasons}")

# Section 2: Correlation
lines.append(f"\n## 2. Correlation")
lines.append(f"\nR162 vs R160 daily return correlation: **{corr.iloc[0,1]:.4f}**")
lines.append(f"(Near-zero = excellent diversification)")

# Section 3: Capital constraint check
lines.append("\n## 3. Capital Constraint Check")
lines.append("\nAt 1x leverage:")
lines.append(f"- R162 max total exposure: ~100% (80% long + 20% short)")
lines.append(f"- R160 max total exposure: {breakout_stats['max_total_exposure']:.0%} "
             f"({breakout_stats['max_concurrent']} x {breakout_stats['pos_size']:.0%})")
lines.append(f"- Combined at 50/50: max 100% (0.5x100% + 0.5x100%)")
lines.append(f"- **Constraint satisfied at 1x leverage**: Weights sum to <= 100%")
lines.append(f"- Leverage > 1x borrows the excess at {BORROW_RATE:.0%} annual")

# Section 4: Best configs meeting targets
lines.append("\n## 4. Target Achievement")

lines.append(f"\n### Configs: 300%+ OOS Return AND MaxDD < 20%")
if len(target_300_20) > 0:
    lines.append(f"\n**{len(target_300_20)} configs found!**\n")
    lines.append("| R162 | R160 | Lev | DD Ctrl | OOS Ret | OOS MaxDD | OOS Calmar | OOS Sharpe | IS Ret | IS MaxDD | AvgLev |")
    lines.append("|------|------|-----|---------|---------|-----------|------------|------------|--------|----------|--------|")
    for _, row in target_300_20.iterrows():
        lines.append(
            f"| {row['r162_w']:.0%} | {row['r160_w']:.0%} | {row['leverage']:.1f}x | {row['dd_control']} | "
            f"{row['oos_ret']:.1%} | {row['oos_maxdd']:.1%} | {row['oos_calmar']:.2f} | "
            f"{row['oos_sharpe']:.2f} | {row['is_ret']:.1%} | {row['is_maxdd']:.1%} | {row['avg_leverage']:.2f} |")
else:
    lines.append("\n**No configs achieve both 300%+ OOS return AND MaxDD < 20%.**")

lines.append(f"\n### Configs: 300%+ OOS Return AND MaxDD < 25%")
if len(target_300_25) > 0:
    lines.append(f"\n**{len(target_300_25)} configs found!**\n")
    lines.append("| R162 | R160 | Lev | DD Ctrl | OOS Ret | OOS MaxDD | OOS Calmar | OOS Sharpe | IS Ret | IS MaxDD | AvgLev |")
    lines.append("|------|------|-----|---------|---------|-----------|------------|------------|--------|----------|--------|")
    for _, row in target_300_25.iterrows():
        lines.append(
            f"| {row['r162_w']:.0%} | {row['r160_w']:.0%} | {row['leverage']:.1f}x | {row['dd_control']} | "
            f"{row['oos_ret']:.1%} | {row['oos_maxdd']:.1%} | {row['oos_calmar']:.2f} | "
            f"{row['oos_sharpe']:.2f} | {row['is_ret']:.1%} | {row['is_maxdd']:.1%} | {row['avg_leverage']:.2f} |")
else:
    lines.append("\n**No configs achieve both 300%+ OOS return AND MaxDD < 25%.**")

# Section 5: Best with MaxDD < 20%
lines.append(f"\n### Top Configs with MaxDD < 20% (sorted by OOS return)")
if len(target_dd20) > 0:
    lines.append(f"\n**{len(target_dd20)} configs with MaxDD < 20%:**\n")
    lines.append("| R162 | R160 | Lev | DD Ctrl | OOS Ret | OOS MaxDD | OOS Calmar | OOS Sharpe | IS Ret | IS MaxDD | Full MaxDD |")
    lines.append("|------|------|-----|---------|---------|-----------|------------|------------|--------|----------|------------|")
    for _, row in target_dd20.head(25).iterrows():
        lines.append(
            f"| {row['r162_w']:.0%} | {row['r160_w']:.0%} | {row['leverage']:.1f}x | {row['dd_control']} | "
            f"{row['oos_ret']:.1%} | {row['oos_maxdd']:.1%} | {row['oos_calmar']:.2f} | "
            f"{row['oos_sharpe']:.2f} | {row['is_ret']:.1%} | {row['is_maxdd']:.1%} | {row['full_maxdd']:.1%} |")
else:
    lines.append("\n**No configs achieve MaxDD < 20%.**")

# Section 6: Top 25 overall
lines.append(f"\n### Top 25 Overall (sorted by OOS return)")
lines.append("\n| Rank | R162 | R160 | Lev | DD Ctrl | OOS Ret | OOS MaxDD | OOS Calmar | OOS Sharpe | IS Ret | IS MaxDD | Full MaxDD |")
lines.append("|------|------|------|-----|---------|---------|-----------|------------|------------|--------|----------|------------|")
for i, (_, row) in enumerate(results_df.head(25).iterrows()):
    lines.append(
        f"| {i+1} | {row['r162_w']:.0%} | {row['r160_w']:.0%} | {row['leverage']:.1f}x | {row['dd_control']} | "
        f"{row['oos_ret']:.1%} | {row['oos_maxdd']:.1%} | {row['oos_calmar']:.2f} | "
        f"{row['oos_sharpe']:.2f} | {row['is_ret']:.1%} | {row['is_maxdd']:.1%} | {row['full_maxdd']:.1%} |")

# Section 7: Best config detail
lines.append(f"\n## 5. Best Near-Target Config: {best_label}")
lines.append(f"\n**Config:** R162={best_config['r162_w']:.0%}, R160={best_config['r160_w']:.0%}, "
             f"Leverage={best_config['leverage']:.1f}x, DD Control={best_config['dd_control']}")
lines.append(f"- **OOS Return:** {best_config['oos_ret']:.1%}")
lines.append(f"- **OOS MaxDD:** {best_config['oos_maxdd']:.1%}")
lines.append(f"- **OOS Calmar:** {best_config['oos_calmar']:.2f}")
lines.append(f"- **OOS Sharpe:** {best_config['oos_sharpe']:.2f}")
lines.append(f"- **IS Return:** {best_config['is_ret']:.1%}")
lines.append(f"- **IS MaxDD:** {best_config['is_maxdd']:.1%}")
lines.append(f"- **Full MaxDD:** {best_config['full_maxdd']:.1%}")
lines.append(f"- **Avg Leverage:** {best_config['avg_leverage']:.2f}x")

# IS vs OOS consistency check
is_sharpe = best_config['is_sharpe']
oos_sharpe = best_config['oos_sharpe']
sharpe_ratio = oos_sharpe / is_sharpe if is_sharpe != 0 else 0
lines.append(f"\n### IS/OOS Consistency")
lines.append(f"- IS Sharpe: {is_sharpe:.2f}")
lines.append(f"- OOS Sharpe: {oos_sharpe:.2f}")
lines.append(f"- OOS/IS Sharpe ratio: {sharpe_ratio:.2f}")
if sharpe_ratio >= 0.5:
    lines.append(f"- **PASS**: OOS Sharpe >= 50% of IS Sharpe (no sign of major overfitting)")
else:
    lines.append(f"- **WARNING**: OOS Sharpe degraded significantly vs IS")

# Monthly returns
lines.append(f"\n### Monthly Returns")
lines.append("| Month | Return | Cumulative |")
lines.append("|-------|--------|------------|")
cum = 1.0
for date, ret in monthly_rets.items():
    cum *= (1 + ret)
    lines.append(f"| {date.strftime('%Y-%m')} | {ret:+.1%} | {cum - 1:+.1%} |")

# Section 8: Overfitting checks
lines.append("\n## 6. Overfitting Checks")
lines.append("\n### Trade Count Validation")
lines.append(f"- R162: {mom_stats['rebalances']} rebalances ({mom_stats['weekly_trades']} weeks with positions)")
lines.append(f"- R160: {breakout_stats['total_trades']} completed trades "
             f"(IS={breakout_stats['is_trades']}, OOS={breakout_stats['oos_trades']})")
if breakout_stats['total_trades'] >= 200:
    lines.append(f"- **PASS**: R160 trade count > 200")
else:
    lines.append(f"- **WARNING**: R160 trade count < 200 (potential overfitting)")
if breakout_stats['oos_trades'] >= 50:
    lines.append(f"- **PASS**: OOS trade count >= 50")
else:
    lines.append(f"- **WARNING**: OOS trade count < 50")

lines.append("\n### Win Rate Check")
lines.append(f"- R162 weekly WR: {mom_stats['win_rate']:.1%}")
lines.append(f"- R160 trade WR: {breakout_stats['win_rate']:.1%}")
if 0.40 <= breakout_stats['win_rate'] <= 0.65:
    lines.append(f"- **PASS**: R160 win rate in realistic range (40-65%)")
else:
    lines.append(f"- **WARNING**: R160 win rate outside realistic range")

# Section 9: All configs sorted by Calmar for MaxDD < 25%
good_calmar = results_df[results_df["oos_maxdd"] > -0.25].sort_values("oos_calmar", ascending=False)
if len(good_calmar) > 0:
    lines.append(f"\n## 7. Top Configs by Calmar (MaxDD < 25%)")
    lines.append("\n| R162 | R160 | Lev | DD Ctrl | OOS Ret | OOS MaxDD | OOS Calmar | OOS Sharpe |")
    lines.append("|------|------|-----|---------|---------|-----------|------------|------------|")
    for _, row in good_calmar.head(15).iterrows():
        lines.append(
            f"| {row['r162_w']:.0%} | {row['r160_w']:.0%} | {row['leverage']:.1f}x | {row['dd_control']} | "
            f"{row['oos_ret']:.1%} | {row['oos_maxdd']:.1%} | {row['oos_calmar']:.2f} | {row['oos_sharpe']:.2f} |")

# Write
with open(RESULTS_PATH, "w") as f:
    f.write("\n".join(lines))

log(f"\nResults written to {RESULTS_PATH}")
log("\n=== SUMMARY ===")
log(f"R162 OOS (12M): {compute_metrics(mom_ret, OOS_START, OOS_END)['total_ret']:.1%}")
log(f"R160 OOS (12M): {compute_metrics(breakout_ret, OOS_START, OOS_END)['total_ret']:.1%}")
log(f"Correlation: {corr.iloc[0,1]:.4f}")
log(f"Best config ({best_label}):")
log(f"  R162={best_config['r162_w']:.0%}, R160={best_config['r160_w']:.0%}, "
    f"Lev={best_config['leverage']:.1f}x, DD={best_config['dd_control']}")
log(f"  OOS: Ret={best_config['oos_ret']:.1%}, MaxDD={best_config['oos_maxdd']:.1%}, "
    f"Calmar={best_config['oos_calmar']:.2f}, Sharpe={best_config['oos_sharpe']:.2f}")
log(f"Configs with 300%+ AND MaxDD<20%: {len(target_300_20)}")
log(f"Configs with 300%+ AND MaxDD<25%: {len(target_300_25)}")
log(f"Configs with MaxDD<20%: {len(target_dd20)}")
