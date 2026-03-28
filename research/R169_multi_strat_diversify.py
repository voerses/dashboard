#!/workspace/venv/bin/python
"""
R169 -- Multi-Strategy Diversified Portfolio (7 Strategies)
============================================================

Key insight: 2 near-zero-correlation strategies at 1.5x achieved 346% return
but -49% MaxDD. Adding MORE uncorrelated strategies should reduce portfolio
MaxDD faster than it reduces returns.

All strategies are ~market-neutral (long/short balanced) to work in any regime.

Strategies:
  S1: Cross-Sectional Momentum (weekly rebalance, top/bottom 3, regime filter)
  S2: Volatility Breakout (4H BB + volume + momentum filter, top 5 signals)
  S3: Short-Term Mean Reversion (RSI extremes in range markets)
  S4: Funding Rate Carry (long negative-funding, short positive-funding)
  S5: Volume-Weighted Breakout (24h high/low breaks with volume confirmation)
  S6: Trend Strength Rotation (ADX ranking, 3-day rebalance)
  S7: Low Volatility Carry (long low-vol, short high-vol tokens)

Date range: 2024-03-17 to 2026-03-17
Costs: 7 bps per side + actual funding + 5% annual borrow on leveraged capital
"""

import sys
import os
import warnings
import time
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict
from scipy.optimize import minimize

warnings.filterwarnings('ignore')

def log(msg):
    print(msg)
    sys.stdout.flush()

# ============================================================
# CONSTANTS
# ============================================================

DATA_DIR = Path('/workspace/crypto_backtest/data/perp/1h_cache')
OUTPUT_PATH = Path('/workspace/crypto_backtest/research/R169_multi_strat_results.md')

SIM_START = pd.Timestamp('2024-03-17')
SIM_END = pd.Timestamp('2026-03-17')
LAST_12MO_START = pd.Timestamp('2025-03-17')

DAYS_PER_YEAR = 365
HOURS_PER_YEAR = 8760

FEE_BPS = 7.0
FEE_FRAC = FEE_BPS / 10000.0
ANNUAL_BORROW_RATE = 0.05
DAILY_BORROW_RATE = ANNUAL_BORROW_RATE / 365.0

MIN_AVG_DAILY_VOLUME_USD = 1_000_000
DATA_CUTOFF = pd.Timestamp('2024-03-17')

# ============================================================
# DATA LOADING
# ============================================================

def load_all_tokens() -> Dict[str, pd.DataFrame]:
    """Load all tokens with data before cutoff and >$1M daily volume."""
    files = sorted(os.listdir(DATA_DIR))
    tokens = {}
    for f in files:
        if not f.endswith('_1h.parquet'):
            continue
        ticker = f.replace('_1h.parquet', '')
        path = DATA_DIR / f
        df = pd.read_parquet(path)
        if df.index.min() >= DATA_CUTOFF:
            continue
        pre_sim = df[df.index < SIM_START]
        if len(pre_sim) < 30 * 24:
            continue
        last_30d = pre_sim.tail(30 * 24)
        daily_dvol = (last_30d['volume'] * last_30d['close']).resample('1D').sum()
        avg_dvol = daily_dvol.mean()
        if avg_dvol < MIN_AVG_DAILY_VOLUME_USD:
            continue
        tokens[ticker] = df
    return tokens


def prepare_daily_data(tokens: Dict[str, pd.DataFrame]):
    """Build aligned daily DataFrames."""
    hourly_close = {}
    hourly_high = {}
    hourly_low = {}
    hourly_volume = {}
    hourly_funding = {}

    lookback_start = SIM_START - pd.Timedelta(days=90)

    for ticker, df in tokens.items():
        mask = (df.index >= lookback_start) & (df.index <= SIM_END)
        d = df.loc[mask].copy()
        hourly_close[ticker] = d['close']
        hourly_high[ticker] = d['high']
        hourly_low[ticker] = d['low']
        hourly_volume[ticker] = d['volume']
        hourly_funding[ticker] = d['funding_1h'].fillna(0)

    close_h = pd.DataFrame(hourly_close)
    high_h = pd.DataFrame(hourly_high)
    low_h = pd.DataFrame(hourly_low)
    volume_h = pd.DataFrame(hourly_volume)
    funding_h = pd.DataFrame(hourly_funding)

    # Daily aggregation
    daily_close = close_h.resample('1D').last().dropna(how='all')
    daily_high = high_h.resample('1D').max()
    daily_low = low_h.resample('1D').min()
    daily_volume = volume_h.resample('1D').sum()
    daily_dvol = (volume_h * close_h).resample('1D').sum()
    daily_funding = funding_h.resample('1D').sum()

    # 4H aggregation
    close_4h = close_h.resample('4h').last().dropna(how='all')
    high_4h = high_h.resample('4h').max()
    low_4h = low_h.resample('4h').min()
    volume_4h = volume_h.resample('4h').sum()

    return (daily_close, daily_high, daily_low, daily_volume, daily_dvol, daily_funding,
            close_4h, high_4h, low_4h, volume_4h, close_h, volume_h)


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI with Wilder smoothing (ewm alpha=1/period)."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1.0/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range."""
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0/period, min_periods=period, adjust=False).mean()


def compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """ADX indicator."""
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    plus_dm = (high - prev_high).clip(lower=0)
    minus_dm = (prev_low - low).clip(lower=0)

    mask_plus = plus_dm > minus_dm
    mask_minus = minus_dm > plus_dm
    plus_dm = plus_dm.where(mask_plus, 0)
    minus_dm = minus_dm.where(mask_minus, 0)

    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0/period, min_periods=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1.0/period, min_periods=period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1.0/period, min_periods=period, adjust=False).mean() / atr)

    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx = dx.ewm(alpha=1.0/period, min_periods=period, adjust=False).mean()
    return adx


def daily_returns_from_weights(daily_weights: pd.DataFrame, daily_close: pd.DataFrame,
                               daily_funding: pd.DataFrame = None) -> pd.Series:
    """
    Compute daily strategy returns from daily position weights.
    Position entered at close of day t, held to close of day t+1.
    """
    common_cols = daily_weights.columns.intersection(daily_close.columns)
    common_idx = daily_weights.index.intersection(daily_close.index)

    weights = daily_weights.loc[common_idx, common_cols].fillna(0)
    closes = daily_close.loc[common_idx, common_cols]

    # Forward returns: return from close_t to close_{t+1}
    fwd_ret = closes.pct_change().shift(-1)

    # PnL: weight_t * return_{t to t+1}
    pnl = (weights * fwd_ret).sum(axis=1)

    # Funding costs
    if daily_funding is not None:
        fund = daily_funding.loc[common_idx, common_cols].fillna(0)
        pnl -= (weights * fund).sum(axis=1)

    # Trading costs on turnover
    turnover = weights.diff().abs().sum(axis=1)
    pnl -= turnover * FEE_FRAC

    # Drop last row (no forward return) and filter to sim period
    pnl = pnl.iloc[:-1]
    pnl = pnl.loc[SIM_START:SIM_END]
    return pnl


def metrics_from_daily(daily_ret: pd.Series, label: str = "") -> dict:
    """Compute key metrics from daily return series."""
    if len(daily_ret) < 30:
        return {'label': label, 'ann_ret': 0, 'sharpe': 0, 'max_dd': 0,
                'calmar': 0, 'sortino': 0, 'total_ret': 0}

    total_ret = (1 + daily_ret).prod() - 1
    n_days = len(daily_ret)
    ann_factor = 365.0 / n_days
    ann_ret = (1 + total_ret) ** ann_factor - 1

    daily_std = daily_ret.std()
    sharpe = (daily_ret.mean() / daily_std * np.sqrt(365)) if daily_std > 0 else 0

    cum = (1 + daily_ret).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    max_dd = dd.min()

    calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0

    downside = daily_ret[daily_ret < 0].std()
    sortino = (daily_ret.mean() / downside * np.sqrt(365)) if (downside is not None and downside > 0) else 0

    return {
        'label': label, 'total_ret': total_ret, 'ann_ret': ann_ret,
        'sharpe': sharpe, 'max_dd': max_dd, 'calmar': calmar, 'sortino': sortino,
    }


# ============================================================
# S1: CROSS-SECTIONAL MOMENTUM
# ============================================================

def run_s1_momentum(daily_close, daily_dvol, daily_funding, daily_high, daily_low):
    """Cross-sectional momentum: weekly rebalance, top/bottom 3, regime + vol filter."""
    log("  S1: Cross-Sectional Momentum...")
    t0 = time.time()

    # EMA regime filter on daily
    ema10 = daily_close.ewm(span=10, adjust=False).mean()
    ema30 = daily_close.ewm(span=30, adjust=False).mean()

    # ATR vol filter (14-day)
    atr_dict = {}
    for ticker in daily_close.columns:
        if ticker in daily_high.columns and ticker in daily_low.columns:
            atr_dict[ticker] = compute_atr(daily_high[ticker], daily_low[ticker], daily_close[ticker], 14)
    atr_df = pd.DataFrame(atr_dict).reindex(daily_close.index)
    atr_ratio = atr_df / daily_close

    # 7-day trailing return
    ret_7d = daily_close.pct_change(7)

    sim_days = daily_close.loc[SIM_START:SIM_END].index
    daily_weights = pd.DataFrame(0.0, index=sim_days, columns=daily_close.columns)

    weekly_dates = pd.date_range(SIM_START, SIM_END, freq='W-MON')

    for wdate in weekly_dates:
        idx = sim_days.searchsorted(wdate)
        if idx >= len(sim_days):
            continue
        day = sim_days[idx]

        # Universe filter
        recent_dvol = daily_dvol.loc[:day].tail(7).mean()
        valid = recent_dvol[recent_dvol > MIN_AVG_DAILY_VOLUME_USD].index
        valid = [t for t in valid if t in ret_7d.columns and t in atr_ratio.columns]
        if len(valid) < 6:
            continue

        rets = ret_7d.loc[day, valid].dropna()
        if len(rets) < 6:
            continue

        # Vol filter: ATR/close > median
        atr_vals = atr_ratio.loc[day, valid].dropna()
        if len(atr_vals) > 0:
            median_atr = atr_vals.median()
            high_vol_tickers = atr_vals[atr_vals > median_atr].index
            rets = rets[rets.index.isin(high_vol_tickers)]
        if len(rets) < 6:
            continue

        ranked = rets.sort_values(ascending=False)
        long_candidates = ranked.head(3).index.tolist()
        short_candidates = ranked.tail(3).index.tolist()

        # Regime filter
        bull = ema10.loc[day] > ema30.loc[day]
        bear = ema10.loc[day] < ema30.loc[day]
        longs = [t for t in long_candidates if t in bull.index and bull[t]]
        shorts = [t for t in short_candidates if t in bear.index and bear[t]]

        if len(longs) == 0 and len(shorts) == 0:
            continue

        # Next rebalance
        next_idx = sim_days.searchsorted(wdate + pd.Timedelta(days=7))
        end_day = sim_days[min(next_idx - 1, len(sim_days) - 1)] if next_idx <= len(sim_days) else sim_days[-1]

        n_long = max(len(longs), 1)
        n_short = max(len(shorts), 1)
        for t in longs:
            daily_weights.loc[day:end_day, t] = 0.80 / n_long
        for t in shorts:
            daily_weights.loc[day:end_day, t] = -0.20 / n_short

    ret = daily_returns_from_weights(daily_weights, daily_close, daily_funding)
    log(f"    S1 done in {time.time()-t0:.1f}s, {len(ret)} days")
    return ret


# ============================================================
# S2: VOLATILITY BREAKOUT (4H)
# ============================================================

def run_s2_vol_breakout(close_4h, high_4h, low_4h, volume_4h, daily_close, daily_funding):
    """
    Volatility breakout on 4H bars. Max 5 positions, equal weight.
    Long: close > BB upper AND volume high AND 14d return > 0
    Short: close < BB lower AND volume high AND 14d return < 0
    Exit: close crosses SMA(20) on 4H
    """
    log("  S2: Volatility Breakout...")
    t0 = time.time()

    bb_mid = close_4h.rolling(20).mean()
    bb_std = close_4h.rolling(20).std()
    bb_upper = bb_mid + 2.0 * bb_std
    bb_lower = bb_mid - 2.0 * bb_std

    vol_avg = volume_4h.rolling(20).mean()
    vol_high = volume_4h > 1.5 * vol_avg
    ret_14d = close_4h.pct_change(84)  # 14*6
    sma20 = close_4h.rolling(20).mean()

    sim_4h = close_4h.loc[SIM_START:SIM_END]

    long_entry = ((close_4h > bb_upper) & vol_high & (ret_14d > 0)).loc[SIM_START:SIM_END].fillna(False)
    short_entry = ((close_4h < bb_lower) & vol_high & (ret_14d < 0)).loc[SIM_START:SIM_END].fillna(False)
    long_exit = (close_4h < sma20).loc[SIM_START:SIM_END].fillna(True)
    short_exit = (close_4h > sma20).loc[SIM_START:SIM_END].fillna(True)

    # Build positions per token with state machine (vectorized per token)
    positions_4h = pd.DataFrame(0.0, index=sim_4h.index, columns=sim_4h.columns)
    bb_dist = ((close_4h - bb_mid) / bb_std.replace(0, np.nan)).loc[SIM_START:SIM_END].abs().fillna(0)

    for ticker in sim_4h.columns:
        le = long_entry[ticker].values
        se = short_entry[ticker].values
        lx = long_exit[ticker].values
        sx = short_exit[ticker].values

        pos = np.zeros(len(sim_4h))
        for i in range(1, len(sim_4h)):
            prev = pos[i-1]
            if prev == 0:
                if le[i]: pos[i] = 1
                elif se[i]: pos[i] = -1
            elif prev > 0:
                pos[i] = 0 if lx[i] else 1
            else:
                pos[i] = 0 if sx[i] else -1
        positions_4h[ticker] = pos

    # Limit to top 5 active positions by BB distance
    for i in range(len(sim_4h)):
        active_mask = positions_4h.iloc[i].abs() > 0
        n_active = active_mask.sum()
        if n_active > 5:
            active_tickers = positions_4h.columns[active_mask]
            dists = bb_dist.iloc[i][active_tickers]
            keep = dists.nlargest(5).index
            for t in active_tickers:
                if t not in keep:
                    positions_4h.iat[i, positions_4h.columns.get_loc(t)] = 0

    # Equal weight to ~1.0 gross
    n_active = positions_4h.abs().sum(axis=1).replace(0, 1)
    positions_4h = positions_4h.div(n_active, axis=0)

    # Resample to daily
    daily_weights = positions_4h.resample('1D').last().dropna(how='all')

    ret = daily_returns_from_weights(daily_weights, daily_close, daily_funding)
    log(f"    S2 done in {time.time()-t0:.1f}s, {len(ret)} days")
    return ret


# ============================================================
# S3: SHORT-TERM MEAN REVERSION
# ============================================================

def run_s3_mean_reversion(daily_close, daily_high, daily_low, daily_funding):
    """RSI mean reversion in range-bound markets. Top 5 extreme RSI, long/short."""
    log("  S3: Short-Term Mean Reversion...")
    t0 = time.time()

    # RSI(14) on daily
    rsi_dict = {}
    for ticker in daily_close.columns:
        rsi_dict[ticker] = compute_rsi(daily_close[ticker], 14)
    rsi_df = pd.DataFrame(rsi_dict)

    # ATR(14) on daily
    atr_dict = {}
    for ticker in daily_close.columns:
        if ticker in daily_high.columns and ticker in daily_low.columns:
            atr_dict[ticker] = compute_atr(daily_high[ticker], daily_low[ticker], daily_close[ticker], 14)
    atr_df = pd.DataFrame(atr_dict).reindex(daily_close.index)

    # SMA(20) on daily
    sma_20d = daily_close.rolling(20, min_periods=10).mean()

    # Range market filter
    deviation = (daily_close - sma_20d).abs()
    in_range = deviation < 1.5 * atr_df

    sim_days = daily_close.loc[SIM_START:SIM_END].index
    daily_weights = pd.DataFrame(0.0, index=sim_days, columns=daily_close.columns)

    active_pos = {}  # ticker -> direction

    for i, day in enumerate(sim_days):
        if day not in rsi_df.index:
            continue

        # Check exits
        to_remove = []
        for ticker, direction in active_pos.items():
            if ticker not in rsi_df.columns:
                to_remove.append(ticker)
                continue
            rsi_val = rsi_df.loc[day, ticker]
            if pd.isna(rsi_val):
                to_remove.append(ticker)
                continue
            if 40 <= rsi_val <= 60:  # RSI returned to neutral
                to_remove.append(ticker)
        for t in to_remove:
            del active_pos[t]

        # Check new entries
        rsi_row = rsi_df.loc[day].dropna()
        range_row = in_range.loc[day] if day in in_range.index else pd.Series(dtype=bool)

        for ticker in rsi_row.index:
            if ticker in active_pos:
                continue
            if ticker not in range_row.index or not range_row[ticker]:
                continue
            if rsi_row[ticker] < 25:
                active_pos[ticker] = 1  # Long oversold
            elif rsi_row[ticker] > 75:
                active_pos[ticker] = -1  # Short overbought

        # Limit to top 5 by RSI extremeness
        if len(active_pos) > 5:
            rsi_dist = {t: abs(rsi_row.get(t, 50) - 50) for t in active_pos}
            sorted_pos = sorted(rsi_dist.items(), key=lambda x: x[1], reverse=True)
            keep = set(t for t, _ in sorted_pos[:5])
            active_pos = {t: d for t, d in active_pos.items() if t in keep}

        # Equal weight
        n = max(len(active_pos), 1)
        for ticker, direction in active_pos.items():
            if ticker in daily_weights.columns:
                daily_weights.loc[day, ticker] = direction / n

    ret = daily_returns_from_weights(daily_weights, daily_close, daily_funding)
    log(f"    S3 done in {time.time()-t0:.1f}s, {len(ret)} days")
    return ret


# ============================================================
# S4: FUNDING RATE CARRY
# ============================================================

def run_s4_funding_carry(daily_close, daily_funding):
    """Funding rate carry: long negative-funding, short positive-funding tokens."""
    log("  S4: Funding Rate Carry...")
    t0 = time.time()

    weekly_avg_funding = daily_funding.rolling(7, min_periods=3).mean()

    sim_days = daily_close.loc[SIM_START:SIM_END].index
    daily_weights = pd.DataFrame(0.0, index=sim_days, columns=daily_close.columns)

    weekly_dates = pd.date_range(SIM_START, SIM_END, freq='W-MON')

    for wdate in weekly_dates:
        idx = sim_days.searchsorted(wdate)
        if idx >= len(sim_days):
            continue
        day = sim_days[idx]

        if day not in weekly_avg_funding.index:
            continue
        rates = weekly_avg_funding.loc[day].dropna()
        if len(rates) < 6:
            continue

        sorted_rates = rates.sort_values()
        longs = sorted_rates.head(3).index.tolist()
        shorts = sorted_rates.tail(3).index.tolist()

        next_idx = sim_days.searchsorted(wdate + pd.Timedelta(days=7))
        end_day = sim_days[min(next_idx - 1, len(sim_days) - 1)] if next_idx <= len(sim_days) else sim_days[-1]

        for t in longs:
            if t in daily_weights.columns:
                daily_weights.loc[day:end_day, t] = 0.5 / 3
        for t in shorts:
            if t in daily_weights.columns:
                daily_weights.loc[day:end_day, t] = -0.5 / 3

    ret = daily_returns_from_weights(daily_weights, daily_close, daily_funding)
    log(f"    S4 done in {time.time()-t0:.1f}s, {len(ret)} days")
    return ret


# ============================================================
# S5: VOLUME-WEIGHTED BREAKOUT
# ============================================================

def run_s5_volume_breakout(daily_close, daily_high, daily_low, daily_volume, daily_funding, close_h, volume_h):
    """
    24H high/low breakout with volume confirmation. Max 5 positions.
    Different from S2: uses daily lookback, hourly volume spikes, strict exits.
    """
    log("  S5: Volume-Weighted Breakout...")
    t0 = time.time()

    # Use hourly data to detect 24h high/low breaks with volume spikes
    # But manage positions at daily level
    prev_day_high = daily_high.shift(1)
    prev_day_low = daily_low.shift(1)
    avg_daily_vol = daily_volume.rolling(20).mean().shift(1)

    # 7-day return
    ret_7d = daily_close.pct_change(7)

    # Entry signals at daily level
    long_entry = (daily_close > prev_day_high) & (daily_volume > 2.0 * avg_daily_vol)
    short_entry = (daily_close < prev_day_low) & (daily_volume > 2.0 * avg_daily_vol) & (ret_7d < 0)

    # Exit: price inside previous range for 4+ days
    inside = (daily_close <= prev_day_high) & (daily_close >= prev_day_low)

    sim_days = daily_close.loc[SIM_START:SIM_END].index
    daily_weights = pd.DataFrame(0.0, index=sim_days, columns=daily_close.columns)

    active_pos = {}  # ticker -> (direction, inside_count)

    for i, day in enumerate(sim_days):
        if day not in long_entry.index:
            continue

        # Update inside counts and check exits
        to_remove = []
        for ticker, (direction, count) in active_pos.items():
            if ticker not in inside.columns or day not in inside.index:
                to_remove.append(ticker)
                continue
            is_inside = inside.loc[day, ticker]
            if pd.isna(is_inside):
                is_inside = True
            new_count = count + 1 if is_inside else 0
            if new_count >= 4:
                to_remove.append(ticker)
            else:
                active_pos[ticker] = (direction, new_count)
        for t in to_remove:
            del active_pos[t]

        # New entries
        le = long_entry.loc[day].fillna(False)
        se = short_entry.loc[day].fillna(False)

        for ticker in le[le].index:
            if ticker not in active_pos and len(active_pos) < 5:
                active_pos[ticker] = (1, 0)
        for ticker in se[se].index:
            if ticker not in active_pos and len(active_pos) < 5:
                active_pos[ticker] = (-1, 0)

        # Equal weight
        n = max(len(active_pos), 1)
        for ticker, (direction, _) in active_pos.items():
            if ticker in daily_weights.columns:
                daily_weights.loc[day, ticker] = direction / n

    ret = daily_returns_from_weights(daily_weights, daily_close, daily_funding)
    log(f"    S5 done in {time.time()-t0:.1f}s, {len(ret)} days")
    return ret


# ============================================================
# S6: TREND STRENGTH ROTATION (ADX)
# ============================================================

def run_s6_trend_rotation(close_4h, high_4h, low_4h, daily_close, daily_funding):
    """Rank by ADX on 4H bars, long/short top trending tokens. 3-day rebalance."""
    log("  S6: Trend Strength Rotation...")
    t0 = time.time()

    adx_dict = {}
    for ticker in close_4h.columns:
        if ticker in high_4h.columns and ticker in low_4h.columns:
            adx_dict[ticker] = compute_adx(high_4h[ticker], low_4h[ticker], close_4h[ticker], 14)
    adx_df = pd.DataFrame(adx_dict)
    adx_daily = adx_df.resample('1D').last()

    ret_7d = daily_close.pct_change(7)

    sim_days = daily_close.loc[SIM_START:SIM_END].index
    daily_weights = pd.DataFrame(0.0, index=sim_days, columns=daily_close.columns)

    rebalance_dates = pd.date_range(SIM_START, SIM_END, freq='3D')

    for rdate in rebalance_dates:
        idx = sim_days.searchsorted(rdate)
        if idx >= len(sim_days):
            continue
        day = sim_days[idx]

        if day not in adx_daily.index or day not in ret_7d.index:
            continue

        adx_vals = adx_daily.loc[day].dropna()
        ret_vals = ret_7d.loc[day].dropna()
        common = adx_vals.index.intersection(ret_vals.index)
        if len(common) < 10:
            continue

        adx_vals = adx_vals[common]
        ret_vals = ret_vals[common]

        top_trending = adx_vals.sort_values(ascending=False).head(10).index
        pos_ret = ret_vals[top_trending]

        longs = pos_ret[pos_ret > 0].sort_values(ascending=False).head(5).index.tolist()
        shorts = pos_ret[pos_ret < 0].sort_values().head(5).index.tolist()

        next_idx = sim_days.searchsorted(rdate + pd.Timedelta(days=3))
        end_day = sim_days[min(next_idx - 1, len(sim_days) - 1)] if next_idx <= len(sim_days) else sim_days[-1]

        n_long = max(len(longs), 1)
        n_short = max(len(shorts), 1)

        for t in longs:
            if t in daily_weights.columns:
                daily_weights.loc[day:end_day, t] = 0.60 / n_long
        for t in shorts:
            if t in daily_weights.columns:
                daily_weights.loc[day:end_day, t] = -0.40 / n_short

    ret = daily_returns_from_weights(daily_weights, daily_close, daily_funding)
    log(f"    S6 done in {time.time()-t0:.1f}s, {len(ret)} days")
    return ret


# ============================================================
# S7: LOW VOLATILITY CARRY (LONG-SHORT)
# ============================================================

def run_s7_low_vol_carry(daily_close, daily_funding):
    """
    Long-short low volatility: long bottom 5 (least volatile),
    short top 5 (most volatile). Weekly rebalance.
    Market-neutral version for bear-market robustness.
    """
    log("  S7: Low Volatility Carry...")
    t0 = time.time()

    daily_ret_raw = daily_close.pct_change()
    rvol_7d = daily_ret_raw.rolling(7, min_periods=3).std() * np.sqrt(365)

    sim_days = daily_close.loc[SIM_START:SIM_END].index
    daily_weights = pd.DataFrame(0.0, index=sim_days, columns=daily_close.columns)

    weekly_dates = pd.date_range(SIM_START, SIM_END, freq='W-MON')

    for wdate in weekly_dates:
        idx = sim_days.searchsorted(wdate)
        if idx >= len(sim_days):
            continue
        day = sim_days[idx]

        if day not in rvol_7d.index:
            continue
        vols = rvol_7d.loc[day].dropna()
        vols = vols[vols > 0]
        if len(vols) < 10:
            continue

        sorted_vols = vols.sort_values()
        lowest_vol = sorted_vols.head(5).index.tolist()  # Long
        highest_vol = sorted_vols.tail(5).index.tolist()  # Short

        next_idx = sim_days.searchsorted(wdate + pd.Timedelta(days=7))
        end_day = sim_days[min(next_idx - 1, len(sim_days) - 1)] if next_idx <= len(sim_days) else sim_days[-1]

        for t in lowest_vol:
            if t in daily_weights.columns:
                daily_weights.loc[day:end_day, t] = 0.5 / 5
        for t in highest_vol:
            if t in daily_weights.columns:
                daily_weights.loc[day:end_day, t] = -0.5 / 5

    ret = daily_returns_from_weights(daily_weights, daily_close, daily_funding)
    log(f"    S7 done in {time.time()-t0:.1f}s, {len(ret)} days")
    return ret


# ============================================================
# PORTFOLIO CONSTRUCTION & ANALYSIS
# ============================================================

def apply_leverage_and_dd_control(daily_ret: pd.Series, leverage: float,
                                  use_dd_control: bool = False) -> pd.Series:
    """Apply leverage, borrow costs, and optional DD control overlay."""
    levered = daily_ret.copy() * leverage
    if leverage > 1.0:
        levered -= (leverage - 1.0) * DAILY_BORROW_RATE

    if not use_dd_control:
        return levered

    # DD control with 20-day trailing peak (not all-time peak)
    # This prevents permanent lock-out when DD exceeds 20%
    result = np.zeros(len(levered))
    equity_hist = np.ones(len(levered) + 1)
    equity_hist[0] = 1.0

    for i in range(len(levered)):
        # 20-day trailing peak
        lookback = max(0, i - 19)
        peak_20d = equity_hist[lookback:i+1].max()

        dd = (equity_hist[i] - peak_20d) / peak_20d if peak_20d > 0 else 0

        if dd > -0.05:
            scale = 1.0
        elif dd > -0.10:
            scale = 0.75
        elif dd > -0.15:
            scale = 0.50
        elif dd > -0.20:
            scale = 0.25
        else:
            scale = 0.0

        result[i] = levered.iloc[i] * scale
        equity_hist[i+1] = equity_hist[i] * (1 + result[i])

    return pd.Series(result, index=levered.index)


def portfolio_combine(strat_rets: Dict[str, pd.Series], weights: list,
                      leverage: float = 1.0, use_dd_control: bool = False) -> pd.Series:
    """Combine strategy returns with given weights, then apply leverage."""
    strat_names = list(strat_rets.keys())
    aligned = pd.DataFrame({name: strat_rets[name] for name in strat_names}).dropna()
    if len(aligned) == 0:
        return pd.Series(dtype=float)

    port_ret = pd.Series(0.0, index=aligned.index)
    for i, name in enumerate(strat_names):
        if i < len(weights):
            port_ret += aligned[name] * weights[i]

    return apply_leverage_and_dd_control(port_ret, leverage, use_dd_control)


def compute_monthly_returns(daily_ret: pd.Series) -> pd.Series:
    return (1 + daily_ret).resample('ME').prod() - 1


def max_sharpe_weights(strat_rets: Dict[str, pd.Series]) -> np.ndarray:
    """Optimize weights for max Sharpe. Allow negative weights (shorting strategies)."""
    strat_names = list(strat_rets.keys())
    aligned = pd.DataFrame({name: strat_rets[name] for name in strat_names}).dropna()
    n = len(strat_names)

    mu = aligned.mean().values * 365
    cov = aligned.cov().values * 365

    def neg_sharpe(w):
        port_ret = w @ mu
        port_vol = np.sqrt(w @ cov @ w)
        return -port_ret / port_vol if port_vol > 1e-10 else 10.0

    constraints = [{'type': 'eq', 'fun': lambda w: np.sum(np.abs(w)) - 1.0}]
    bounds = [(-0.3, 0.5) for _ in range(n)]
    x0 = np.ones(n) / n

    result = minimize(neg_sharpe, x0, method='SLSQP', bounds=bounds,
                      constraints=[{'type': 'eq', 'fun': lambda w: np.sum(w) - 1.0}])
    if result.success:
        return result.x
    return np.ones(n) / n


def risk_parity_weights(strat_rets: Dict[str, pd.Series]) -> np.ndarray:
    """Inverse volatility weights."""
    strat_names = list(strat_rets.keys())
    aligned = pd.DataFrame({name: strat_rets[name] for name in strat_names}).dropna()
    vols = aligned.std()
    inv_vol = 1.0 / vols.replace(0, np.inf)
    return (inv_vol / inv_vol.sum()).values


# ============================================================
# MAIN
# ============================================================

def main():
    t_start = time.time()
    log("=" * 70)
    log("R169: Multi-Strategy Diversified Portfolio (7 Strategies)")
    log("=" * 70)

    log("\n[1/5] Loading data...")
    tokens = load_all_tokens()
    log(f"  Loaded {len(tokens)} tokens")

    log("\n[2/5] Preparing data...")
    (daily_close, daily_high, daily_low, daily_volume, daily_dvol, daily_funding,
     close_4h, high_4h, low_4h, volume_4h, close_h, volume_h) = prepare_daily_data(tokens)
    log(f"  Daily shape: {daily_close.shape}, 4H shape: {close_4h.shape}")

    log("\n[3/5] Running individual strategies...")
    s1_ret = run_s1_momentum(daily_close, daily_dvol, daily_funding, daily_high, daily_low)
    s2_ret = run_s2_vol_breakout(close_4h, high_4h, low_4h, volume_4h, daily_close, daily_funding)
    s3_ret = run_s3_mean_reversion(daily_close, daily_high, daily_low, daily_funding)
    s4_ret = run_s4_funding_carry(daily_close, daily_funding)
    s5_ret = run_s5_volume_breakout(daily_close, daily_high, daily_low, daily_volume, daily_funding, close_h, volume_h)
    s6_ret = run_s6_trend_rotation(close_4h, high_4h, low_4h, daily_close, daily_funding)
    s7_ret = run_s7_low_vol_carry(daily_close, daily_funding)

    strat_rets = {
        'S1_Momentum': s1_ret,
        'S2_VolBreakout': s2_ret,
        'S3_MeanRevert': s3_ret,
        'S4_FundCarry': s4_ret,
        'S5_VolWeightBO': s5_ret,
        'S6_TrendRot': s6_ret,
        'S7_LowVolCarry': s7_ret,
    }

    log("\n  Sanity check - daily return stats:")
    for name, ret in strat_rets.items():
        m = metrics_from_daily(ret, name)
        log(f"    {name}: mean={ret.mean()*100:.4f}%/day, std={ret.std()*100:.2f}%/day, "
            f"total={m['total_ret']*100:+.1f}%, sharpe={m['sharpe']:.2f}, maxDD={m['max_dd']*100:.1f}%")

    # Metrics
    log("\n[4/5] Computing metrics...")
    strat_names = list(strat_rets.keys())
    n_strats = len(strat_names)

    ind_metrics_full = {}
    ind_metrics_12m = {}
    for name, ret in strat_rets.items():
        ind_metrics_full[name] = metrics_from_daily(ret, name)
        ind_metrics_12m[name] = metrics_from_daily(ret.loc[LAST_12MO_START:], name + " (12M)")

    aligned_all = pd.DataFrame(strat_rets).dropna()
    corr_matrix = aligned_all.corr()

    # Portfolio combos
    log("\n[5/5] Testing portfolio combinations...")

    equal_w = [1.0/n_strats] * n_strats
    mom_heavy_w = [0.30, 0.15, 0.15, 0.10, 0.10, 0.10, 0.10]
    rp_w = risk_parity_weights(strat_rets).tolist()
    ms_w = max_sharpe_weights(strat_rets).tolist()

    weight_schemes = {
        'Equal': equal_w,
        'MomHeavy': mom_heavy_w,
        'RiskParity': rp_w,
        'MaxSharpe': ms_w,
    }

    leverage_levels = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0]
    all_configs = []

    for wname, weights in weight_schemes.items():
        for lev in leverage_levels:
            port_ret = portfolio_combine(strat_rets, weights, lev, False)
            if len(port_ret) > 30:
                m_full = metrics_from_daily(port_ret, f"{wname}_L{lev:.1f}")
                m_12m = metrics_from_daily(port_ret.loc[LAST_12MO_START:], f"{wname}_L{lev:.1f}_12M")
                all_configs.append({
                    'name': f"{wname}_L{lev:.1f}", 'weights': wname, 'leverage': lev,
                    'dd_control': False, 'full': m_full, '12m': m_12m, 'daily_ret': port_ret,
                })

            if lev >= 2.0:
                port_ret_dd = portfolio_combine(strat_rets, weights, lev, True)
                if len(port_ret_dd) > 30:
                    m_full_dd = metrics_from_daily(port_ret_dd, f"{wname}_L{lev:.1f}_DDC")
                    m_12m_dd = metrics_from_daily(port_ret_dd.loc[LAST_12MO_START:], f"{wname}_L{lev:.1f}_DDC_12M")
                    all_configs.append({
                        'name': f"{wname}_L{lev:.1f}_DDC", 'weights': wname, 'leverage': lev,
                        'dd_control': True, 'full': m_full_dd, '12m': m_12m_dd, 'daily_ret': port_ret_dd,
                    })

    log(f"  Tested {len(all_configs)} portfolio configurations")

    # ============================================================
    # BUILD RESULTS MARKDOWN
    # ============================================================
    lines = []
    lines.append("# R169: Multi-Strategy Diversified Portfolio Results")
    lines.append(f"\nGenerated: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"\nPeriod: {SIM_START.date()} to {SIM_END.date()} ({len(aligned_all)} trading days)")
    lines.append(f"Universe: {len(tokens)} tokens with >$1M daily volume and data since {DATA_CUTOFF.date()}")
    lines.append(f"Costs: {FEE_BPS:.0f} bps/side + actual funding + {ANNUAL_BORROW_RATE*100:.0f}% annual borrow on leveraged capital")

    # Section 1
    lines.append("\n## 1. Individual Strategy Metrics (1x Leverage, No Overlay)")
    lines.append("\n### Full Period")
    lines.append("| Strategy | Total Ret | Ann Ret | Sharpe | MaxDD | Calmar | Sortino |")
    lines.append("|----------|----------|---------|--------|-------|--------|---------|")
    for name in strat_names:
        m = ind_metrics_full[name]
        lines.append(f"| {name} | {m['total_ret']*100:+.1f}% | {m['ann_ret']*100:+.1f}% | {m['sharpe']:.2f} | {m['max_dd']*100:.1f}% | {m['calmar']:.2f} | {m['sortino']:.2f} |")

    lines.append("\n### Last 12 Months")
    lines.append("| Strategy | Return | Sharpe | MaxDD | Calmar | Sortino |")
    lines.append("|----------|--------|--------|-------|--------|---------|")
    for name in strat_names:
        m = ind_metrics_12m[name]
        lines.append(f"| {name} | {m['total_ret']*100:+.1f}% | {m['sharpe']:.2f} | {m['max_dd']*100:.1f}% | {m['calmar']:.2f} | {m['sortino']:.2f} |")

    # Section 2
    lines.append("\n## 2. Strategy Correlation Matrix (Daily Returns)")
    header = "| | " + " | ".join(strat_names) + " |"
    sep = "|" + "|".join(["---"] * (n_strats + 1)) + "|"
    lines.append(header)
    lines.append(sep)
    for i, name_i in enumerate(strat_names):
        row = f"| {name_i} |"
        for j, name_j in enumerate(strat_names):
            row += f" {corr_matrix.iloc[i, j]:+.3f} |"
        lines.append(row)

    avg_abs_corr = corr_matrix.abs().values[np.triu_indices(n_strats, k=1)].mean()
    lines.append(f"\nAverage absolute pairwise correlation: {avg_abs_corr:.3f}")

    # Section 3
    lines.append("\n## 3. Weight Allocation Schemes")
    lines.append("| Scheme | " + " | ".join(strat_names) + " |")
    lines.append("|--------|" + "|".join(["------"] * n_strats) + "|")
    for wname, weights in weight_schemes.items():
        row = f"| {wname} |"
        for w in weights:
            row += f" {w:.3f} |"
        lines.append(row)

    # Section 4
    lines.append("\n## 4. All Portfolio Configurations (Sorted by Last 12M Return)")
    all_configs_sorted = sorted(all_configs, key=lambda x: x['12m']['total_ret'], reverse=True)

    lines.append("| Config | Lev | DDC | Full Return | Full Sharpe | Full MaxDD | Full Calmar | 12M Return | 12M Sharpe | 12M MaxDD | 12M Calmar |")
    lines.append("|--------|-----|-----|------------|-------------|-----------|-------------|-----------|-----------|----------|------------|")
    for cfg in all_configs_sorted:
        f = cfg['full']
        m = cfg['12m']
        ddc = "Y" if cfg['dd_control'] else "N"
        lines.append(f"| {cfg['name']} | {cfg['leverage']:.1f} | {ddc} | {f['total_ret']*100:+.1f}% | {f['sharpe']:.2f} | {f['max_dd']*100:.1f}% | {f['calmar']:.2f} | {m['total_ret']*100:+.1f}% | {m['sharpe']:.2f} | {m['max_dd']*100:.1f}% | {m['calmar']:.2f} |")

    # Section 5
    lines.append("\n## 5. Pareto Frontier (Return vs MaxDD)")
    lines.append("\nConfigurations where no other config has both higher return AND lower MaxDD:")

    pareto = []
    for cfg in all_configs:
        ret_val = cfg['full']['total_ret']
        dd_val = abs(cfg['full']['max_dd'])
        dominated = any(
            other['full']['total_ret'] > ret_val and abs(other['full']['max_dd']) < dd_val
            for other in all_configs
        )
        if not dominated:
            pareto.append(cfg)

    pareto_sorted = sorted(pareto, key=lambda x: x['full']['total_ret'], reverse=True)
    lines.append("| Config | Total Return | MaxDD | Sharpe | Calmar |")
    lines.append("|--------|-------------|-------|--------|--------|")
    for cfg in pareto_sorted:
        f = cfg['full']
        lines.append(f"| {cfg['name']} | {f['total_ret']*100:+.1f}% | {f['max_dd']*100:.1f}% | {f['sharpe']:.2f} | {f['calmar']:.2f} |")

    # Section 6
    lines.append("\n## 6. Target Zone: 300%+ Return AND MaxDD < 25%")
    target_cfgs = [c for c in all_configs if c['full']['total_ret'] >= 3.0 and abs(c['full']['max_dd']) < 0.25]
    target_cfgs.sort(key=lambda x: x['full']['sharpe'], reverse=True)

    if target_cfgs:
        lines.append("| Config | Total Return | MaxDD | Sharpe | Calmar | 12M Return | 12M MaxDD |")
        lines.append("|--------|-------------|-------|--------|--------|-----------|----------|")
        for cfg in target_cfgs:
            f = cfg['full']
            m = cfg['12m']
            lines.append(f"| {cfg['name']} | {f['total_ret']*100:+.1f}% | {f['max_dd']*100:.1f}% | {f['sharpe']:.2f} | {f['calmar']:.2f} | {m['total_ret']*100:+.1f}% | {m['max_dd']*100:.1f}% |")
    else:
        lines.append("\nNo configurations meet both criteria. Closest configs:")
        scored = []
        for c in all_configs:
            ret_gap = max(0, 3.0 - c['full']['total_ret'])
            dd_gap = max(0, abs(c['full']['max_dd']) - 0.25)
            scored.append((ret_gap + dd_gap * 10, c))
        scored.sort(key=lambda x: x[0])
        lines.append("| Config | Total Return | MaxDD | Sharpe | Calmar | Gap Score |")
        lines.append("|--------|-------------|-------|--------|--------|-----------|")
        for score, cfg in scored[:10]:
            f = cfg['full']
            lines.append(f"| {cfg['name']} | {f['total_ret']*100:+.1f}% | {f['max_dd']*100:.1f}% | {f['sharpe']:.2f} | {f['calmar']:.2f} | {score:.3f} |")

    # Section 6b
    lines.append("\n## 6b. Best Configs with MaxDD < 20% (Sorted by Return)")
    low_dd_cfgs = [c for c in all_configs if abs(c['full']['max_dd']) < 0.20]
    low_dd_cfgs.sort(key=lambda x: x['full']['total_ret'], reverse=True)

    if low_dd_cfgs:
        lines.append("| Config | Total Return | MaxDD | Sharpe | Calmar | Sortino | 12M Return |")
        lines.append("|--------|-------------|-------|--------|--------|---------|-----------|")
        for cfg in low_dd_cfgs[:15]:
            f = cfg['full']
            m = cfg['12m']
            lines.append(f"| {cfg['name']} | {f['total_ret']*100:+.1f}% | {f['max_dd']*100:.1f}% | {f['sharpe']:.2f} | {f['calmar']:.2f} | {f['sortino']:.2f} | {m['total_ret']*100:+.1f}% |")
    else:
        lines.append("\nNo configurations with MaxDD < 20%.")

    # Section 7
    lines.append("\n## 7. Monthly Returns for Top 3 Configurations")
    top3_by_sharpe = sorted(all_configs, key=lambda x: x['full']['sharpe'], reverse=True)[:3]

    for cfg in top3_by_sharpe:
        monthly = compute_monthly_returns(cfg['daily_ret'])
        f = cfg['full']
        lines.append(f"\n### {cfg['name']} (Sharpe={f['sharpe']:.2f}, Return={f['total_ret']*100:+.1f}%, MaxDD={f['max_dd']*100:.1f}%)")
        lines.append("| Month | Return |")
        lines.append("|-------|--------|")
        for date, ret in monthly.items():
            lines.append(f"| {date.strftime('%Y-%m')} | {ret*100:+.2f}% |")

    # Summary
    lines.append("\n## Summary")
    best_sharpe = max(all_configs, key=lambda x: x['full']['sharpe'])
    best_calmar = max(all_configs, key=lambda x: x['full']['calmar'])
    best_return = max(all_configs, key=lambda x: x['full']['total_ret'])

    lines.append(f"\n- **Best Sharpe (full):** {best_sharpe['name']} -- Sharpe={best_sharpe['full']['sharpe']:.2f}, Return={best_sharpe['full']['total_ret']*100:+.1f}%, MaxDD={best_sharpe['full']['max_dd']*100:.1f}%")
    lines.append(f"- **Best Calmar (full):** {best_calmar['name']} -- Calmar={best_calmar['full']['calmar']:.2f}, Return={best_calmar['full']['total_ret']*100:+.1f}%, MaxDD={best_calmar['full']['max_dd']*100:.1f}%")
    lines.append(f"- **Best Return (full):** {best_return['name']} -- Return={best_return['full']['total_ret']*100:+.1f}%, Sharpe={best_return['full']['sharpe']:.2f}, MaxDD={best_return['full']['max_dd']*100:.1f}%")

    if low_dd_cfgs:
        best_low_dd = low_dd_cfgs[0]
        lines.append(f"- **Best Return with MaxDD<20%:** {best_low_dd['name']} -- Return={best_low_dd['full']['total_ret']*100:+.1f}%, MaxDD={best_low_dd['full']['max_dd']*100:.1f}%")

    if target_cfgs:
        best_target = target_cfgs[0]
        lines.append(f"- **Best in Target Zone (300%+, <25% DD):** {best_target['name']} -- Return={best_target['full']['total_ret']*100:+.1f}%, MaxDD={best_target['full']['max_dd']*100:.1f}%")

    lines.append(f"\n- **Avg abs pairwise correlation:** {avg_abs_corr:.3f}")
    lines.append(f"- **Number of strategies:** {n_strats}")
    lines.append(f"- **Total configs tested:** {len(all_configs)}")

    elapsed = time.time() - t_start
    lines.append(f"\nComputation time: {elapsed:.0f}s")

    output = "\n".join(lines)
    OUTPUT_PATH.write_text(output)
    log(f"\nResults written to {OUTPUT_PATH}")
    print("\n" + "=" * 70)
    print(output)


if __name__ == '__main__':
    main()
