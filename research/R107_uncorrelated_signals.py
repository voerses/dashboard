#!/workspace/venv/bin/python
"""
R107: Uncorrelated Signal Search for V3 Momentum Portfolio Diversification
===========================================================================

Goal: Find trading signals that are GENUINELY UNCORRELATED with V3 Momentum
(EMA20>EMA50 trend-following on BTC). All trend-following variants are correlated
with V3 (R105 finding: corr=0.688). For portfolio diversification we need signals
that fire INDEPENDENTLY of BTC's trend state.

Signal Candidates (none use EMA crossovers):
  1. Mean-Reversion with Volatility Regime Gate
  2. VRP (Volatility Risk Premium) Direction Signal
  3. Macro Regime Rotation
  4. Funding Rate Carry
  5. Intraday Momentum Breakout (Short Timeframe)

Kill Criteria:
  - Correlation with V3 > 0.4 -> KILL
  - Standalone Sharpe < 0.0 -> KILL
  - Walk-forward <3/6 positive -> KILL
  - 50/50 portfolio Sharpe worse than V3 -> KILL

Data:
  - BTC spot 1h: data/spot/1h_cache/BTC_1h.parquet
  - DVOL: data/alternative/deribit_options/dvol/btc_dvol_daily.json
  - Macro: data/alternative/macro/*.parquet
  - Funding: data/alternative/binance_funding_rates_full.json

Author: Quant Research Agent
Date: 2026-03-24
"""

import os
import json
import warnings
import numpy as np
import pandas as pd
from scipy import stats
from pathlib import Path
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')

# ── Paths ──────────────────────────────────────────────────────────────────────
PROJECT_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = PROJECT_DIR / 'data'
OUTPUT_PY = PROJECT_DIR / 'research' / 'R107_uncorrelated_signals.py'
OUTPUT_MD = PROJECT_DIR / 'research' / 'R107_uncorrelated_signals.md'

COST_BPS = 10  # round-trip cost in basis points (10bps)
PERIOD_START = '2021-01-01'
PERIOD_END = '2026-03-31'

# Walk-forward configuration
WF_TRAIN_MONTHS = 12
WF_TEST_MONTHS = 6
WF_N_WINDOWS = 6


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════════════════════

def load_btc_1h():
    """Load BTC 1h spot data."""
    print("[DATA] Loading BTC 1h spot...")
    df = pd.read_parquet(DATA_DIR / 'spot/1h_cache/BTC_1h.parquet')
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep='first')]
    df = df.loc[PERIOD_START:PERIOD_END]
    print(f"  {df.index.min().date()} to {df.index.max().date()}, {len(df)} bars")
    return df


def load_btc_daily(btc_1h):
    """Resample 1h to daily."""
    daily = btc_1h['close'].resample('1D').last().dropna().to_frame('close')
    daily['open'] = btc_1h['open'].resample('1D').first()
    daily['high'] = btc_1h['high'].resample('1D').max()
    daily['low'] = btc_1h['low'].resample('1D').min()
    daily['volume'] = btc_1h['volume'].resample('1D').sum()
    daily['daily_return'] = daily['close'].pct_change()
    daily['log_return'] = np.log(daily['close'] / daily['close'].shift(1))
    print(f"  Daily: {daily.index.min().date()} to {daily.index.max().date()}, {len(daily)} rows")
    return daily


def load_dvol():
    """Load BTC DVOL (Deribit implied volatility index) daily."""
    print("[DATA] Loading BTC DVOL...")
    dvol_path = DATA_DIR / 'alternative/deribit_options/dvol/btc_dvol_daily.json'
    with open(dvol_path) as f:
        data = json.load(f)
    records = []
    for row in data:
        ts = pd.Timestamp(row[0], unit='ms')
        records.append({'date': ts, 'dvol_close': row[4]})
    dvol = pd.DataFrame(records).set_index('date').sort_index()
    dvol = dvol[~dvol.index.duplicated(keep='last')]
    print(f"  DVOL: {dvol.index.min().date()} to {dvol.index.max().date()}, {len(dvol)} rows")
    return dvol['dvol_close']


def load_macro():
    """Load macro data: DXY, US10Y, VIX, SP500, Gold."""
    print("[DATA] Loading macro data...")
    macro = {}
    for name, fname in [('dxy', 'usd_index'), ('us10y', 'us10y_yield'),
                         ('vix', 'vix'), ('sp500', 'sp500'), ('gold', 'gold')]:
        df = pd.read_parquet(DATA_DIR / f'alternative/macro/{fname}.parquet')
        df['Date'] = pd.to_datetime(df['Date'])
        df = df.set_index('Date').sort_index()
        df = df[~df.index.duplicated(keep='last')]
        macro[name] = df['Close']
        print(f"  {name}: {df.index.min().date()} to {df.index.max().date()}, {len(df)} rows")
    return macro


def load_funding():
    """Load BTC daily funding rate from funding proxy parquet (pre-computed daily)."""
    print("[DATA] Loading BTC funding rates (proxy)...")
    proxy_path = DATA_DIR / 'alternative/funding_ls_proxy/BTC_funding_proxy.parquet'
    df = pd.read_parquet(proxy_path)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep='last')]

    daily_fr = df['funding_rate_daily']
    fr_zscore = df['fr_zscore_30d']  # Pre-computed 30-day z-score
    extreme_long = df['extreme_long']
    extreme_short = df['extreme_short']

    print(f"  Funding proxy: {daily_fr.index.min().date()} to {daily_fr.index.max().date()}, {len(daily_fr)} rows")
    print(f"  Funding range: {daily_fr.min():.6f} to {daily_fr.max():.6f}")
    print(f"  Mean: {daily_fr.mean():.6f}, Median: {daily_fr.median():.6f}")
    print(f"  Days > 0.0001: {(daily_fr > 0.0001).sum()}, < -0.0001: {(daily_fr < -0.0001).sum()}")
    print(f"  Extreme long days: {extreme_long.sum():.0f}, Extreme short days: {extreme_short.sum():.0f}")

    return df  # Return full DataFrame for richer signal construction


# ══════════════════════════════════════════════════════════════════════════════
# V3 MOMENTUM BASELINE
# ══════════════════════════════════════════════════════════════════════════════

def compute_v3_momentum(daily):
    """
    V3 Momentum: Long when daily EMA(20) > EMA(50), flat otherwise.
    Weekly rebalance (Monday). Returns daily return series.
    """
    print("[V3] Computing V3 Momentum baseline...")
    ema20 = daily['close'].ewm(span=20, adjust=False).mean()
    ema50 = daily['close'].ewm(span=50, adjust=False).mean()
    uptrend = (ema20 > ema50).astype(int)

    # Use prior day's signal (no lookahead)
    pos = uptrend.shift(1).fillna(0).astype(int)

    # Weekly rebalance: lock position on Monday
    weekly_signal = pos.resample('W-MON').last()
    pos_weekly = weekly_signal.reindex(pos.index, method='ffill').fillna(0).astype(int)

    # Compute returns with costs
    daily_ret = daily['daily_return'].fillna(0)
    position_changes = pos_weekly.diff().abs().fillna(0)
    cost = position_changes * (COST_BPS / 10000)
    strat_ret = pos_weekly * daily_ret - cost

    # Compute total stats
    days_long = (pos_weekly == 1).sum()
    total_days = len(pos_weekly)
    ann_ret = strat_ret.mean() * 365
    ann_vol = strat_ret.std() * np.sqrt(365)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    print(f"  V3 Momentum: {days_long}/{total_days} days long ({100*days_long/total_days:.1f}%)")
    print(f"  Ann return: {ann_ret:.2%}, Sharpe: {sharpe:.3f}")

    return strat_ret, pos_weekly


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL 1: MEAN-REVERSION WITH VOLATILITY REGIME GATE
# ══════════════════════════════════════════════════════════════════════════════

def signal_mean_reversion_vol_gate(btc_1h, daily):
    """
    Mean-reversion in LOW volatility regimes.
    - Compute 20-day realized vol
    - When vol < 30th percentile (quiet/range market):
      Buy when 1h RSI < 25, sell when RSI > 75
    - Position sizing: binary (1 or -1 or 0)
    - Exit: RSI mean-reverts to 40-60 band
    """
    print("\n[SIGNAL 1] Mean-Reversion with Volatility Regime Gate")

    # 20-day realized vol (annualized)
    daily_logret = daily['log_return'].fillna(0)
    realized_vol = daily_logret.rolling(20).std() * np.sqrt(365) * 100  # as percentage

    # Rolling 30th percentile (expanding window, min 60 days)
    vol_30pct = realized_vol.expanding(min_periods=60).quantile(0.30)

    # Low vol regime flag
    low_vol = (realized_vol < vol_30pct).astype(int)

    # Map daily low_vol to 1h bars (use prior day, no lookahead)
    low_vol_shifted = low_vol.shift(1)
    low_vol_1h = low_vol_shifted.reindex(btc_1h.index, method='ffill').fillna(0).astype(int)

    # Compute 1h RSI(14)
    period = 14
    delta = btc_1h['close'].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_1h = 100 - (100 / (1 + rs))

    # Generate positions on 1h bars
    n = len(btc_1h)
    position = np.zeros(n)
    in_long = False
    in_short = False

    rsi_vals = rsi_1h.values
    lowvol_vals = low_vol_1h.values

    for i in range(1, n):
        if np.isnan(rsi_vals[i]):
            position[i] = position[i-1] if i > 0 else 0
            continue

        # In a position
        if in_long:
            if rsi_vals[i] > 60:  # Exit long on mean-reversion
                in_long = False
                position[i] = 0
            else:
                position[i] = 1
            continue
        if in_short:
            if rsi_vals[i] < 40:  # Exit short on mean-reversion
                in_short = False
                position[i] = 0
            else:
                position[i] = -1
            continue

        # Entry only in low vol regime
        if lowvol_vals[i] == 1:
            if rsi_vals[i] < 25:
                in_long = True
                position[i] = 1
            elif rsi_vals[i] > 75:
                in_short = True
                position[i] = -1

    pos_1h = pd.Series(position, index=btc_1h.index)

    # Convert to daily returns
    # Use end-of-day position (last bar of day), shift by 1 for next day's return
    daily_pos = pos_1h.resample('1D').last().fillna(0)
    daily_pos = daily_pos.reindex(daily.index, method='ffill').fillna(0)
    daily_pos_shifted = daily_pos.shift(1).fillna(0)

    daily_ret = daily['daily_return'].fillna(0)
    pos_changes = daily_pos_shifted.diff().abs().fillna(0)
    cost = pos_changes * (COST_BPS / 10000)
    strat_ret = daily_pos_shifted * daily_ret - cost

    n_long = (daily_pos_shifted > 0).sum()
    n_short = (daily_pos_shifted < 0).sum()
    n_flat = (daily_pos_shifted == 0).sum()
    print(f"  Days: {n_long} long, {n_short} short, {n_flat} flat")

    return strat_ret, daily_pos_shifted


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL 2: VRP DIRECTION SIGNAL
# ══════════════════════════════════════════════════════════════════════════════

def signal_vrp_direction(daily, dvol):
    """
    VRP (Volatility Risk Premium) as a directional signal.
    - VRP = DVOL (implied) / Realized_vol_20d
    - When VRP z-score > 1 std: fear premium high -> go long (buying opportunity)
    - When VRP z-score < -1 std: realized > implied -> go short (sell signal)
    - Neutral zone: flat
    """
    print("\n[SIGNAL 2] VRP Direction Signal")

    # 20-day realized vol (annualized, percentage)
    daily_logret = daily['log_return'].fillna(0)
    realized_vol = daily_logret.rolling(20).std() * np.sqrt(365) * 100

    # Align DVOL with daily
    dvol_aligned = dvol.reindex(daily.index, method='ffill')

    # VRP ratio (keep NaN where data is missing)
    vrp = dvol_aligned / realized_vol.replace(0, np.nan)

    # Z-score with 60-day rolling window
    vrp_mean = vrp.rolling(60, min_periods=30).mean()
    vrp_std = vrp.rolling(60, min_periods=30).std()
    vrp_z = (vrp - vrp_mean) / vrp_std.replace(0, np.nan)

    # Position: z > 1 -> long, z < -1 -> short, else flat
    # Reindex vrp_z to daily.index (it already shares the same index via dvol_aligned)
    vrp_z_aligned = vrp_z.reindex(daily.index)
    position = pd.Series(0.0, index=daily.index)
    position.loc[vrp_z_aligned > 1.0] = 1.0
    position.loc[vrp_z_aligned < -1.0] = -1.0

    # Use prior day's signal (no lookahead)
    pos_shifted = position.shift(1).fillna(0)

    # Compute returns
    daily_ret = daily['daily_return'].fillna(0)
    pos_changes = pos_shifted.diff().abs().fillna(0)
    cost = pos_changes * (COST_BPS / 10000)
    strat_ret = pos_shifted * daily_ret - cost

    # Only count days where we have DVOL data
    valid_mask = dvol_aligned.notna() & realized_vol.notna()
    strat_ret = strat_ret[valid_mask]

    n_long = (pos_shifted > 0).sum()
    n_short = (pos_shifted < 0).sum()
    n_flat = (pos_shifted == 0).sum()
    print(f"  Days: {n_long} long, {n_short} short, {n_flat} flat")
    print(f"  VRP z-score range: {vrp_z_aligned.min():.2f} to {vrp_z_aligned.max():.2f}")
    print(f"  Valid days: {valid_mask.sum()}")

    return strat_ret, pos_shifted


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL 3: MACRO REGIME ROTATION
# ══════════════════════════════════════════════════════════════════════════════

def signal_macro_regime(daily, macro):
    """
    Macro regime rotation:
    - Risk-off: US10Y 20d change > +0.3 std AND DXY rising -> flat/short
    - Risk-on: US10Y falling AND DXY falling -> long
    - Neutral: otherwise -> flat
    """
    print("\n[SIGNAL 3] Macro Regime Rotation")

    dxy = macro['dxy'].reindex(daily.index, method='ffill')
    us10y = macro['us10y'].reindex(daily.index, method='ffill')

    # 20-day changes
    dxy_chg_20d = dxy.pct_change(20)
    us10y_chg_20d = us10y.diff(20)  # Yield is in absolute terms, use diff not pct_change

    # Z-score the US10Y change
    us10y_chg_mean = us10y_chg_20d.rolling(60, min_periods=30).mean()
    us10y_chg_std = us10y_chg_20d.rolling(60, min_periods=30).std()
    us10y_z = (us10y_chg_20d - us10y_chg_mean) / us10y_chg_std.replace(0, np.nan)

    # DXY direction: rising = positive 20d change
    dxy_rising = (dxy_chg_20d > 0).astype(int)
    dxy_falling = (dxy_chg_20d < 0).astype(int)

    # US10Y direction: rising = positive 20d change (positive z)
    us10y_rising = (us10y_z > 0.3).astype(int)
    us10y_falling = (us10y_z < -0.3).astype(int)

    # Signals
    risk_off = (us10y_rising & dxy_rising).astype(int)
    risk_on = (us10y_falling & dxy_falling).astype(int)

    position = pd.Series(0.0, index=daily.index)
    position[risk_on == 1] = 1.0
    position[risk_off == 1] = -1.0

    # Use prior day's signal
    pos_shifted = position.shift(1).fillna(0)

    # Compute returns
    daily_ret = daily['daily_return'].fillna(0)
    pos_changes = pos_shifted.diff().abs().fillna(0)
    cost = pos_changes * (COST_BPS / 10000)
    strat_ret = pos_shifted * daily_ret - cost

    n_long = (pos_shifted > 0).sum()
    n_short = (pos_shifted < 0).sum()
    n_flat = (pos_shifted == 0).sum()
    print(f"  Days: {n_long} long, {n_short} short, {n_flat} flat")

    return strat_ret, pos_shifted


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL 4: FUNDING RATE CARRY
# ══════════════════════════════════════════════════════════════════════════════

def signal_funding_carry(daily, funding_df):
    """
    Funding rate carry / contrarian signal:
    - funding_df is the full BTC funding proxy DataFrame with z-scores
    - When funding z-score (30d) > 1.5: extreme positive funding = overleveraged longs
      -> contrarian SHORT (expect liquidation cascade)
    - When funding z-score < -1.5: extreme negative = overleveraged shorts
      -> contrarian LONG (expect short squeeze)
    - Between: flat
    - This is a CONTRARIAN signal based on funding extremes, not carry
    """
    print("\n[SIGNAL 4] Funding Rate Contrarian")

    # Align funding z-score to daily
    fr_daily = funding_df['funding_rate_daily'].reindex(daily.index, method='ffill')
    fr_z = funding_df['fr_zscore_30d'].reindex(daily.index, method='ffill')
    extreme_long = funding_df['extreme_long'].reindex(daily.index, method='ffill').fillna(0)
    extreme_short = funding_df['extreme_short'].reindex(daily.index, method='ffill').fillna(0)

    print(f"  Aligned funding: {fr_daily.notna().sum()} days with data")
    print(f"  Funding z-score stats: mean={fr_z.mean():.3f}, std={fr_z.std():.3f}")
    print(f"  Extreme long days: {(extreme_long > 0).sum()}, Extreme short days: {(extreme_short > 0).sum()}")

    # Contrarian positioning based on z-score
    position = pd.Series(0.0, index=daily.index)
    # Extreme positive funding -> short (contrarian: longs overleveraged)
    position.loc[fr_z > 1.5] = -1.0
    # Extreme negative funding -> long (contrarian: shorts overleveraged)
    position.loc[fr_z < -1.5] = 1.0

    # Also use the pre-computed extreme flags as confirmation
    # Override: if extreme_long flag is set, definitely short
    position.loc[extreme_long > 0] = -1.0
    # If extreme_short flag is set, definitely long
    position.loc[extreme_short > 0] = 1.0

    # Use prior day's signal
    pos_shifted = position.shift(1).fillna(0)

    # Returns: price return only (no carry component for contrarian)
    daily_ret = daily['daily_return'].fillna(0)
    pos_changes = pos_shifted.diff().abs().fillna(0)
    cost = pos_changes * (COST_BPS / 10000)
    strat_ret = pos_shifted * daily_ret - cost

    n_long = (pos_shifted > 0).sum()
    n_short = (pos_shifted < 0).sum()
    n_flat = (pos_shifted == 0).sum()
    print(f"  Days: {n_long} long, {n_short} short, {n_flat} flat")

    return strat_ret, pos_shifted


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL 5: INTRADAY MOMENTUM BREAKOUT
# ══════════════════════════════════════════════════════════════════════════════

def signal_intraday_momentum(btc_1h, daily):
    """
    Intraday momentum breakout on 1h bars:
    - Entry: 1h return > 2% AND volume > 2x 20-bar average
    - Exit: after 8 bars (8 hours) or trailing stop of 1.5%
    - Direction: momentum direction (long for positive, short for negative)
    """
    print("\n[SIGNAL 5] Intraday Momentum Breakout")

    # 1h returns
    ret_1h = btc_1h['close'].pct_change()

    # 20-bar average volume
    avg_vol = btc_1h['volume'].rolling(20).mean()

    # Breakout detection
    n = len(btc_1h)
    position = np.zeros(n)
    entry_price = 0.0
    hold_count = 0
    max_hold = 8  # 8 hours
    trailing_stop = 0.015  # 1.5%
    in_trade = False
    trade_dir = 0
    peak_price = 0.0

    close_vals = btc_1h['close'].values
    ret_vals = ret_1h.values
    vol_vals = btc_1h['volume'].values
    avg_vol_vals = avg_vol.values

    for i in range(1, n):
        if np.isnan(ret_vals[i]) or np.isnan(avg_vol_vals[i]):
            continue

        if in_trade:
            hold_count += 1
            # Check exit conditions
            if hold_count >= max_hold:
                in_trade = False
                position[i] = 0
                continue

            # Trailing stop
            if trade_dir == 1:
                if close_vals[i] > peak_price:
                    peak_price = close_vals[i]
                if (peak_price - close_vals[i]) / peak_price > trailing_stop:
                    in_trade = False
                    position[i] = 0
                    continue
            elif trade_dir == -1:
                if close_vals[i] < peak_price:
                    peak_price = close_vals[i]
                if (close_vals[i] - peak_price) / peak_price > trailing_stop:
                    in_trade = False
                    position[i] = 0
                    continue

            position[i] = trade_dir
        else:
            # Check entry: large move + high volume
            if abs(ret_vals[i]) > 0.02 and vol_vals[i] > 2 * avg_vol_vals[i]:
                trade_dir = 1 if ret_vals[i] > 0 else -1
                in_trade = True
                entry_price = close_vals[i]
                peak_price = close_vals[i]
                hold_count = 0
                position[i] = trade_dir

    pos_1h = pd.Series(position, index=btc_1h.index)

    # Convert to daily: net position is average of 1h positions in the day
    daily_pos = pos_1h.resample('1D').mean().fillna(0)
    daily_pos = daily_pos.reindex(daily.index, fill_value=0).fillna(0)

    # Clip to [-1, 1]
    daily_pos = daily_pos.clip(-1, 1)

    # For daily returns, we use the actual 1h simulation
    # Compute 1h returns
    ret_1h_clean = btc_1h['close'].pct_change().fillna(0)
    # Strategy 1h returns
    strat_ret_1h = pos_1h.shift(1).fillna(0) * ret_1h_clean

    # Position changes for cost
    pos_changes_1h = pos_1h.diff().abs().fillna(0)
    cost_1h = pos_changes_1h * (COST_BPS / 10000 / 2)  # Half cost per side, but we count each change

    strat_ret_1h = strat_ret_1h - cost_1h

    # Aggregate to daily
    strat_ret_daily = strat_ret_1h.resample('1D').sum()
    strat_ret_daily = strat_ret_daily.reindex(daily.index, fill_value=0).fillna(0)

    n_entries = (pos_1h.diff().abs() > 0).sum()
    print(f"  Total 1h position changes: {n_entries}")

    return strat_ret_daily, daily_pos


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def compute_metrics(returns, name="Strategy"):
    """Compute standard performance metrics from a daily return series."""
    if len(returns) == 0 or returns.std() == 0:
        return {
            'name': name, 'ann_return': 0, 'ann_vol': 0, 'sharpe': 0,
            'max_dd': 0, 'calmar': 0, 'total_return': 0, 'n_days': 0
        }

    ann_ret = returns.mean() * 365
    ann_vol = returns.std() * np.sqrt(365)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

    cum = (1 + returns).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    max_dd = dd.min()

    calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0
    total_ret = cum.iloc[-1] - 1 if len(cum) > 0 else 0

    return {
        'name': name,
        'ann_return': ann_ret,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'calmar': calmar,
        'total_return': total_ret,
        'n_days': len(returns)
    }


def correlation_analysis(v3_ret, signal_ret, name="Signal"):
    """Compute correlation between V3 and a candidate signal."""
    aligned = pd.DataFrame({'v3': v3_ret, 'signal': signal_ret}).dropna()

    if len(aligned) < 30:
        return {
            'name': name, 'pearson_r': np.nan, 'pearson_p': np.nan,
            'spearman_r': np.nan, 'spearman_p': np.nan,
            'n_days': len(aligned), 'active_corr': np.nan
        }

    pearson_r, pearson_p = stats.pearsonr(aligned['v3'], aligned['signal'])
    spearman_r, spearman_p = stats.spearmanr(aligned['v3'], aligned['signal'])

    # Active-only correlation (at least one strategy non-zero)
    active = aligned[(aligned['v3'] != 0) | (aligned['signal'] != 0)]
    if len(active) > 30:
        active_corr, _ = stats.pearsonr(active['v3'], active['signal'])
    else:
        active_corr = np.nan

    return {
        'name': name,
        'pearson_r': pearson_r,
        'pearson_p': pearson_p,
        'spearman_r': spearman_r,
        'spearman_p': spearman_p,
        'n_days': len(aligned),
        'active_corr': active_corr
    }


def portfolio_5050(v3_ret, signal_ret):
    """Compute 50/50 equal weight portfolio metrics."""
    aligned = pd.DataFrame({'v3': v3_ret, 'signal': signal_ret}).dropna()
    portfolio_ret = 0.5 * aligned['v3'] + 0.5 * aligned['signal']
    return portfolio_ret, compute_metrics(portfolio_ret, "50/50 Portfolio")


def walk_forward_test(daily, signal_func, signal_args, v3_ret, signal_name):
    """
    Walk-forward validation: 6 windows, 12mo train / 6mo test.
    For each window, compute OOS metrics and correlation.
    Returns list of window results.
    """
    print(f"\n[WALK-FORWARD] {signal_name}")

    start_date = daily.index.min()
    end_date = daily.index.max()

    results = []
    window_start = pd.Timestamp(PERIOD_START)

    for w in range(WF_N_WINDOWS):
        train_end = window_start + pd.DateOffset(months=WF_TRAIN_MONTHS)
        test_start = train_end
        test_end = test_start + pd.DateOffset(months=WF_TEST_MONTHS)

        if test_end > end_date:
            break

        # Get the full strategy returns (already computed, just slice)
        # For walk-forward, we compute on train to set parameters, test on OOS
        # Since our signals are non-parametric (fixed rules), we just evaluate OOS
        signal_ret = signal_args  # Pre-computed return series

        oos_ret = signal_ret.loc[test_start:test_end]
        oos_v3 = v3_ret.loc[test_start:test_end]

        if len(oos_ret) < 30:
            window_start += pd.DateOffset(months=WF_TEST_MONTHS)
            continue

        oos_metrics = compute_metrics(oos_ret, f"W{w+1}")

        # OOS correlation
        aligned = pd.DataFrame({'v3': oos_v3, 'signal': oos_ret}).dropna()
        if len(aligned) > 20:
            oos_corr, _ = stats.pearsonr(aligned['v3'], aligned['signal'])
        else:
            oos_corr = np.nan

        # Portfolio OOS
        port_ret = 0.5 * aligned['v3'] + 0.5 * aligned['signal']
        port_metrics = compute_metrics(port_ret, f"Portfolio W{w+1}")

        results.append({
            'window': w + 1,
            'train': f"{window_start.date()} to {train_end.date()}",
            'test': f"{test_start.date()} to {test_end.date()}",
            'oos_sharpe': oos_metrics['sharpe'],
            'oos_return': oos_metrics['ann_return'],
            'oos_max_dd': oos_metrics['max_dd'],
            'oos_corr': oos_corr,
            'port_sharpe': port_metrics['sharpe'],
            'positive': oos_metrics['ann_return'] > 0
        })

        print(f"  W{w+1}: OOS Sharpe={oos_metrics['sharpe']:.3f}, "
              f"Corr={oos_corr:.3f}, Port Sharpe={port_metrics['sharpe']:.3f}")

        window_start += pd.DateOffset(months=WF_TEST_MONTHS)

    return results


# ══════════════════════════════════════════════════════════════════════════════
# KILL CRITERIA EVALUATION
# ══════════════════════════════════════════════════════════════════════════════

def evaluate_signal(name, corr_result, metrics, wf_results, v3_metrics, port_metrics):
    """Apply kill criteria to a signal. Returns verdict dict."""
    verdict = {
        'name': name,
        'correlation': corr_result['pearson_r'],
        'active_corr': corr_result['active_corr'],
        'sharpe': metrics['sharpe'],
        'ann_return': metrics['ann_return'],
        'max_dd': metrics['max_dd'],
        'killed': False,
        'kill_reason': None,
        'wf_positive': 0,
        'wf_total': 0,
        'port_sharpe': port_metrics['sharpe'] if port_metrics else 0,
        'v3_sharpe': v3_metrics['sharpe'],
        'diversification_value': False
    }

    # Kill 1: Correlation > 0.4
    if abs(corr_result['pearson_r']) > 0.4:
        verdict['killed'] = True
        verdict['kill_reason'] = f"Correlation too high: {corr_result['pearson_r']:.3f} > 0.4"
        return verdict

    # Kill 2: Standalone Sharpe < 0 (or zero -- means no activity)
    if metrics['sharpe'] <= 0:
        verdict['killed'] = True
        verdict['kill_reason'] = f"Non-positive Sharpe: {metrics['sharpe']:.3f}"
        return verdict

    # Kill 3: Walk-forward < 3/6 positive
    if wf_results:
        n_positive = sum(1 for r in wf_results if r['positive'])
        n_total = len(wf_results)
        verdict['wf_positive'] = n_positive
        verdict['wf_total'] = n_total
        if n_positive < 3:
            verdict['killed'] = True
            verdict['kill_reason'] = f"Walk-forward: {n_positive}/{n_total} positive (< 3/6)"
            return verdict

    # Kill 4: Portfolio Sharpe worse than V3
    if port_metrics and port_metrics['sharpe'] < v3_metrics['sharpe']:
        verdict['killed'] = True
        verdict['kill_reason'] = (f"No diversification value: portfolio Sharpe "
                                   f"{port_metrics['sharpe']:.3f} < V3 {v3_metrics['sharpe']:.3f}")
        return verdict

    # Passed all checks
    verdict['diversification_value'] = True
    return verdict


# ══════════════════════════════════════════════════════════════════════════════
# MARKDOWN REPORT GENERATION
# ══════════════════════════════════════════════════════════════════════════════

def generate_report(all_results, v3_metrics, corr_matrix_df):
    """Generate the markdown report."""
    lines = []
    lines.append("# R107 -- Uncorrelated Signal Search for V3 Portfolio Diversification")
    lines.append("")
    lines.append(f"**Date**: {datetime.now().strftime('%Y-%m-%d')}")
    lines.append(f"**Period**: {PERIOD_START} to {PERIOD_END}")
    lines.append(f"**Asset**: BTC spot")
    lines.append(f"**Cost assumption**: {COST_BPS} bps round-trip")
    lines.append("")
    lines.append("## Objective")
    lines.append("")
    lines.append("Find signals GENUINELY UNCORRELATED with V3 Momentum (EMA20>EMA50 trend-following).")
    lines.append("All trend-following variants are correlated with V3 (R105: corr=0.688).")
    lines.append("For portfolio diversification, we need signals that fire INDEPENDENTLY of BTC's trend state.")
    lines.append("")

    # V3 Baseline
    lines.append("## V3 Momentum Baseline")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Ann. Return | {v3_metrics['ann_return']:.2%} |")
    lines.append(f"| Ann. Volatility | {v3_metrics['ann_vol']:.2%} |")
    lines.append(f"| Sharpe Ratio | **{v3_metrics['sharpe']:.3f}** |")
    lines.append(f"| Max Drawdown | {v3_metrics['max_dd']:.2%} |")
    lines.append(f"| Calmar Ratio | {v3_metrics['calmar']:.3f} |")
    lines.append(f"| Total Return | {v3_metrics['total_return']:.2%} |")
    lines.append("")

    # Correlation Matrix
    lines.append("## Correlation Matrix (Daily Returns)")
    lines.append("")
    if corr_matrix_df is not None:
        lines.append("| | " + " | ".join(corr_matrix_df.columns) + " |")
        lines.append("|" + "|".join(["---"] * (len(corr_matrix_df.columns) + 1)) + "|")
        for idx, row in corr_matrix_df.iterrows():
            vals = " | ".join([f"{v:.3f}" for v in row.values])
            lines.append(f"| {idx} | {vals} |")
        lines.append("")

    # Per-signal results
    lines.append("## Signal Results")
    lines.append("")

    for r in all_results:
        name = r['name']
        corr = r['correlation']
        metrics = r['metrics']
        verdict = r['verdict']
        wf = r.get('walk_forward', [])
        port = r.get('portfolio_metrics', {})

        lines.append(f"### {name}")
        lines.append("")

        # Standalone metrics
        lines.append("**Standalone Metrics:**")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Ann. Return | {metrics['ann_return']:.2%} |")
        lines.append(f"| Ann. Volatility | {metrics['ann_vol']:.2%} |")
        lines.append(f"| Sharpe Ratio | **{metrics['sharpe']:.3f}** |")
        lines.append(f"| Max Drawdown | {metrics['max_dd']:.2%} |")
        lines.append(f"| Total Return | {metrics['total_return']:.2%} |")
        lines.append("")

        # Correlation with V3
        lines.append("**Correlation with V3:**")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Pearson r | **{corr['pearson_r']:.4f}** |")
        lines.append(f"| Pearson p-value | {corr['pearson_p']:.2e} |")
        lines.append(f"| Spearman r | {corr['spearman_r']:.4f} |")
        lines.append(f"| Active-day Pearson r | {corr['active_corr']:.4f}" if not np.isnan(corr['active_corr']) else f"| Active-day Pearson r | N/A")
        lines.append(f"| N days | {corr['n_days']} |")
        lines.append("")

        # Walk-forward (if not killed before this stage)
        if wf:
            lines.append("**Walk-Forward Results:**")
            lines.append("")
            lines.append("| Window | Test Period | OOS Sharpe | OOS Corr | Portfolio Sharpe | Positive? |")
            lines.append("|--------|-------------|-----------|----------|-----------------|-----------|")
            for w in wf:
                pos_str = "YES" if w['positive'] else "NO"
                corr_str = f"{w['oos_corr']:.3f}" if not np.isnan(w['oos_corr']) else "N/A"
                lines.append(f"| W{w['window']} | {w['test']} | {w['oos_sharpe']:.3f} | "
                           f"{corr_str} | {w['port_sharpe']:.3f} | {pos_str} |")
            lines.append("")

        # Portfolio
        if port:
            lines.append("**50/50 Portfolio with V3:**")
            lines.append("")
            lines.append("| Metric | V3 Only | 50/50 Portfolio | Delta |")
            lines.append("|--------|---------|----------------|-------|")
            v3s = v3_metrics['sharpe']
            ps = port.get('sharpe', 0)
            lines.append(f"| Sharpe | {v3s:.3f} | {ps:.3f} | {ps - v3s:+.3f} |")
            lines.append(f"| Ann. Return | {v3_metrics['ann_return']:.2%} | {port.get('ann_return', 0):.2%} | |")
            lines.append(f"| Max DD | {v3_metrics['max_dd']:.2%} | {port.get('max_dd', 0):.2%} | |")
            lines.append("")

        # Verdict
        v = verdict
        status = "**KILLED**" if v['killed'] else "**PASSED**"
        lines.append(f"**Verdict**: {status}")
        if v['killed']:
            lines.append(f"- Kill reason: {v['kill_reason']}")
        else:
            lines.append(f"- Correlation: {v['correlation']:.3f} (< 0.4 threshold)")
            lines.append(f"- Sharpe: {v['sharpe']:.3f} (> 0.0)")
            if v['wf_total'] > 0:
                lines.append(f"- Walk-forward: {v['wf_positive']}/{v['wf_total']} positive")
            lines.append(f"- Portfolio Sharpe: {v['port_sharpe']:.3f} vs V3: {v['v3_sharpe']:.3f}")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Signal | Corr w/ V3 | Standalone Sharpe | Portfolio Sharpe | Verdict |")
    lines.append("|--------|-----------|------------------|-----------------|---------|")
    for r in all_results:
        v = r['verdict']
        status = "KILLED" if v['killed'] else "PASSED"
        port_s = f"{v['port_sharpe']:.3f}" if v['port_sharpe'] != 0 else "N/A"
        lines.append(f"| {v['name']} | {v['correlation']:.3f} | {v['sharpe']:.3f} | {port_s} | {status} |")
    lines.append("")

    # Conclusions
    lines.append("## Conclusions")
    lines.append("")
    passed = [r for r in all_results if not r['verdict']['killed']]
    if passed:
        lines.append(f"**{len(passed)} signal(s) passed all kill criteria:**")
        for r in passed:
            v = r['verdict']
            lines.append(f"- **{v['name']}**: Sharpe={v['sharpe']:.3f}, "
                        f"Corr={v['correlation']:.3f}, Portfolio Sharpe={v['port_sharpe']:.3f}")
        lines.append("")
        lines.append("These signals provide genuine diversification value when combined with V3.")
    else:
        lines.append("**No signals passed all kill criteria.**")
        lines.append("")
        lines.append("All candidates were killed. The search for uncorrelated signals continues.")
        lines.append("Possible next steps:")
        lines.append("- Try cross-asset signals (ETH/BTC ratio, altcoin momentum)")
        lines.append("- Try on-chain data (exchange flows, whale transactions)")
        lines.append("- Try sentiment data (fear/greed, social media)")
        lines.append("- Try options market signals (put/call ratio, skew)")
    lines.append("")

    # Kill reason summary
    killed = [r for r in all_results if r['verdict']['killed']]
    if killed:
        lines.append("### Kill Reasons")
        lines.append("")
        for r in killed:
            lines.append(f"- **{r['verdict']['name']}**: {r['verdict']['kill_reason']}")
        lines.append("")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 80)
    print("R107: UNCORRELATED SIGNAL SEARCH")
    print("=" * 80)

    # Load data
    btc_1h = load_btc_1h()
    daily = load_btc_daily(btc_1h)
    dvol = load_dvol()
    macro = load_macro()
    daily_funding = load_funding()

    # V3 baseline
    v3_ret, v3_pos = compute_v3_momentum(daily)
    v3_metrics = compute_metrics(v3_ret, "V3 Momentum")
    print(f"\nV3 Baseline Sharpe: {v3_metrics['sharpe']:.3f}")

    # ──────────────────────────────────────────────────────────────────────────
    # Run all signals
    # ──────────────────────────────────────────────────────────────────────────
    all_results = []
    all_returns = {'V3 Momentum': v3_ret}

    # Signal 1: Mean-Reversion with Volatility Regime Gate
    try:
        s1_ret, s1_pos = signal_mean_reversion_vol_gate(btc_1h, daily)
        s1_metrics = compute_metrics(s1_ret, "S1: Mean-Rev Vol Gate")
        s1_corr = correlation_analysis(v3_ret, s1_ret, "S1: Mean-Rev Vol Gate")
        all_returns['S1: Mean-Rev'] = s1_ret

        # Check kill criteria in order
        if abs(s1_corr['pearson_r']) <= 0.4 and s1_metrics['sharpe'] >= 0:
            s1_port_ret, s1_port_metrics = portfolio_5050(v3_ret, s1_ret)
            s1_wf = walk_forward_test(daily, None, s1_ret, v3_ret, "S1: Mean-Rev Vol Gate")
        else:
            s1_port_ret, s1_port_metrics = portfolio_5050(v3_ret, s1_ret)
            s1_wf = []

        s1_verdict = evaluate_signal(
            "S1: Mean-Rev Vol Gate", s1_corr, s1_metrics, s1_wf, v3_metrics, s1_port_metrics)

        all_results.append({
            'name': "Signal 1: Mean-Reversion with Volatility Regime Gate",
            'correlation': s1_corr, 'metrics': s1_metrics, 'verdict': s1_verdict,
            'walk_forward': s1_wf, 'portfolio_metrics': s1_port_metrics
        })
        print(f"\n  >> S1 Verdict: {'KILLED' if s1_verdict['killed'] else 'PASSED'}")
        if s1_verdict['killed']:
            print(f"     Reason: {s1_verdict['kill_reason']}")
    except Exception as e:
        print(f"  ERROR in Signal 1: {e}")
        import traceback
        traceback.print_exc()

    # Signal 2: VRP Direction
    try:
        s2_ret, s2_pos = signal_vrp_direction(daily, dvol)
        s2_metrics = compute_metrics(s2_ret, "S2: VRP Direction")
        s2_corr = correlation_analysis(v3_ret, s2_ret, "S2: VRP Direction")
        all_returns['S2: VRP'] = s2_ret

        if abs(s2_corr['pearson_r']) <= 0.4 and s2_metrics['sharpe'] >= 0:
            s2_port_ret, s2_port_metrics = portfolio_5050(v3_ret, s2_ret)
            s2_wf = walk_forward_test(daily, None, s2_ret, v3_ret, "S2: VRP Direction")
        else:
            s2_port_ret, s2_port_metrics = portfolio_5050(v3_ret, s2_ret)
            s2_wf = []

        s2_verdict = evaluate_signal(
            "S2: VRP Direction", s2_corr, s2_metrics, s2_wf, v3_metrics, s2_port_metrics)

        all_results.append({
            'name': "Signal 2: VRP (Volatility Risk Premium) Direction",
            'correlation': s2_corr, 'metrics': s2_metrics, 'verdict': s2_verdict,
            'walk_forward': s2_wf, 'portfolio_metrics': s2_port_metrics
        })
        print(f"\n  >> S2 Verdict: {'KILLED' if s2_verdict['killed'] else 'PASSED'}")
        if s2_verdict['killed']:
            print(f"     Reason: {s2_verdict['kill_reason']}")
    except Exception as e:
        print(f"  ERROR in Signal 2: {e}")
        import traceback
        traceback.print_exc()

    # Signal 3: Macro Regime Rotation
    try:
        s3_ret, s3_pos = signal_macro_regime(daily, macro)
        s3_metrics = compute_metrics(s3_ret, "S3: Macro Regime")
        s3_corr = correlation_analysis(v3_ret, s3_ret, "S3: Macro Regime")
        all_returns['S3: Macro'] = s3_ret

        if abs(s3_corr['pearson_r']) <= 0.4 and s3_metrics['sharpe'] >= 0:
            s3_port_ret, s3_port_metrics = portfolio_5050(v3_ret, s3_ret)
            s3_wf = walk_forward_test(daily, None, s3_ret, v3_ret, "S3: Macro Regime")
        else:
            s3_port_ret, s3_port_metrics = portfolio_5050(v3_ret, s3_ret)
            s3_wf = []

        s3_verdict = evaluate_signal(
            "S3: Macro Regime", s3_corr, s3_metrics, s3_wf, v3_metrics, s3_port_metrics)

        all_results.append({
            'name': "Signal 3: Macro Regime Rotation",
            'correlation': s3_corr, 'metrics': s3_metrics, 'verdict': s3_verdict,
            'walk_forward': s3_wf, 'portfolio_metrics': s3_port_metrics
        })
        print(f"\n  >> S3 Verdict: {'KILLED' if s3_verdict['killed'] else 'PASSED'}")
        if s3_verdict['killed']:
            print(f"     Reason: {s3_verdict['kill_reason']}")
    except Exception as e:
        print(f"  ERROR in Signal 3: {e}")
        import traceback
        traceback.print_exc()

    # Signal 4: Funding Rate Carry
    try:
        s4_ret, s4_pos = signal_funding_carry(daily, daily_funding)
        s4_metrics = compute_metrics(s4_ret, "S4: Funding Carry")
        s4_corr = correlation_analysis(v3_ret, s4_ret, "S4: Funding Carry")
        all_returns['S4: Funding'] = s4_ret

        if abs(s4_corr['pearson_r']) <= 0.4 and s4_metrics['sharpe'] >= 0:
            s4_port_ret, s4_port_metrics = portfolio_5050(v3_ret, s4_ret)
            s4_wf = walk_forward_test(daily, None, s4_ret, v3_ret, "S4: Funding Carry")
        else:
            s4_port_ret, s4_port_metrics = portfolio_5050(v3_ret, s4_ret)
            s4_wf = []

        s4_verdict = evaluate_signal(
            "S4: Funding Carry", s4_corr, s4_metrics, s4_wf, v3_metrics, s4_port_metrics)

        all_results.append({
            'name': "Signal 4: Funding Rate Carry",
            'correlation': s4_corr, 'metrics': s4_metrics, 'verdict': s4_verdict,
            'walk_forward': s4_wf, 'portfolio_metrics': s4_port_metrics
        })
        print(f"\n  >> S4 Verdict: {'KILLED' if s4_verdict['killed'] else 'PASSED'}")
        if s4_verdict['killed']:
            print(f"     Reason: {s4_verdict['kill_reason']}")
    except Exception as e:
        print(f"  ERROR in Signal 4: {e}")
        import traceback
        traceback.print_exc()

    # Signal 5: Intraday Momentum Breakout
    try:
        s5_ret, s5_pos = signal_intraday_momentum(btc_1h, daily)
        s5_metrics = compute_metrics(s5_ret, "S5: Intraday Momentum")
        s5_corr = correlation_analysis(v3_ret, s5_ret, "S5: Intraday Momentum")
        all_returns['S5: Intraday'] = s5_ret

        if abs(s5_corr['pearson_r']) <= 0.4 and s5_metrics['sharpe'] >= 0:
            s5_port_ret, s5_port_metrics = portfolio_5050(v3_ret, s5_ret)
            s5_wf = walk_forward_test(daily, None, s5_ret, v3_ret, "S5: Intraday Momentum")
        else:
            s5_port_ret, s5_port_metrics = portfolio_5050(v3_ret, s5_ret)
            s5_wf = []

        s5_verdict = evaluate_signal(
            "S5: Intraday Momentum", s5_corr, s5_metrics, s5_wf, v3_metrics, s5_port_metrics)

        all_results.append({
            'name': "Signal 5: Intraday Momentum Breakout",
            'correlation': s5_corr, 'metrics': s5_metrics, 'verdict': s5_verdict,
            'walk_forward': s5_wf, 'portfolio_metrics': s5_port_metrics
        })
        print(f"\n  >> S5 Verdict: {'KILLED' if s5_verdict['killed'] else 'PASSED'}")
        if s5_verdict['killed']:
            print(f"     Reason: {s5_verdict['kill_reason']}")
    except Exception as e:
        print(f"  ERROR in Signal 5: {e}")
        import traceback
        traceback.print_exc()

    # ──────────────────────────────────────────────────────────────────────────
    # Correlation matrix
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[CORR MATRIX] Building correlation matrix...")
    ret_df = pd.DataFrame(all_returns)
    ret_df = ret_df.dropna(how='all')
    corr_matrix = ret_df.corr()
    print(corr_matrix.to_string())

    # ──────────────────────────────────────────────────────────────────────────
    # Generate report
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[REPORT] Generating markdown report...")
    report = generate_report(all_results, v3_metrics, corr_matrix)

    with open(OUTPUT_MD, 'w') as f:
        f.write(report)
    print(f"\nReport written to: {OUTPUT_MD}")

    # Final summary
    print("\n" + "=" * 80)
    print("FINAL SUMMARY")
    print("=" * 80)
    for r in all_results:
        v = r['verdict']
        status = "KILLED" if v['killed'] else "PASSED"
        reason = f" -- {v['kill_reason']}" if v['killed'] else ""
        print(f"  {v['name']}: {status}{reason}")
        if not v['killed']:
            print(f"    Corr={v['correlation']:.3f}, Sharpe={v['sharpe']:.3f}, "
                  f"Portfolio Sharpe={v['port_sharpe']:.3f}")


if __name__ == '__main__':
    main()
