#!/workspace/venv/bin/python
"""
Range Regime Positioning Strategy — Standalone Backtest
========================================================

Tests whether POSITIONING-based signals can generate POSITIVE returns during
RANGE market regimes (regime=3 in V4 detection), where V3 trend-following
loses money (Sharpe -1.01, -21.4% annualized).

Key insight: Finding #42 said standalone positioning FAILS, but that was
with DAILY rebalancing causing 11%/yr cost drag. This test uses WEEKLY
rebalancing per findings #44, #47.

Strategies:
  A: Positioning Contrarian (30d z-score of top trader L/S, weekly rebal)
  B: L/S Divergence (top trader vs retail, 30d z-score, weekly rebal)
  C: Combined (average of A and B z-scores)
  D: Contrarian + VRP filter (only trade when VRP z < -0.5 turbulent)

Data:
  - BTC perp 1h: data/perp/1h_cache/BTC_1h.parquet (2020-2026)
  - Daily positioning: data/alternative/binance_metrics/all_symbols_daily_ls.parquet
    - sum_toptrader_ls_ratio: top trader POSITION L/S ratio (2020-09 to 2026-03)
    - count_ls_ratio: global/retail ACCOUNT L/S ratio (2020-09 to 2026-03)
  - Bybit daily L/S: data/alternative/ls_ratio_extended/bybit_btcusdt_ls_ratio.parquet
    (2020-08 to 2026-03, retail account ratio)
  - DVOL: data/alternative/deribit_options/dvol/btc_dvol_daily.json (2021-03 to 2026-03)

Regime detection: V4 daily regime (ADX-based, expanding percentiles, causal)
  0=CRISIS, 1=QUIET, 2=UPTREND, 3=RANGE, 4=DOWNTREND

Capital: $200K, position size 50% of equity
Fees: 4bps per side, Slippage: 5bps per side (9bps total one-way, 18bps round-trip)
Rebalance: weekly (Monday close)
"""

import os
import json
import warnings
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from scipy import stats

warnings.filterwarnings('ignore')

# ==============================================================================
# Configuration
# ==============================================================================
BASE_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = BASE_DIR / 'data'
OUTPUT_DIR = BASE_DIR / 'research'

INITIAL_CAPITAL = 200_000.0
POSITION_SIZE_PCT = 0.50  # 50% of equity per position
FEE_BPS = 4               # per side
SLIPPAGE_BPS = 5           # per side
TOTAL_ONE_WAY_BPS = FEE_BPS + SLIPPAGE_BPS  # 9 bps

# Z-score thresholds for signal generation
Z_ENTRY_STRONG = 1.5    # enter when |z| > 1.5
Z_EXIT = 0.5            # exit when |z| < 0.5
Z_LOOKBACK = 30         # 30-day rolling window for z-score

# Regime detection parameters (matching V4 engine.py)
ADX_THRESHOLD = 25
CRISIS_MULT = 2.0
QUIET_MULT = 0.7
MIN_REGIME_PERIODS = 60

# IS/OOS split
IS_END = pd.Timestamp('2024-12-31')
OOS_START = pd.Timestamp('2025-01-01')


# ==============================================================================
# Indicator helpers (matching V4 engine.py)
# ==============================================================================

def _ema(arr, span):
    """Exponential moving average."""
    return pd.Series(arr).ewm(span=span, adjust=False).mean().values

def _adx(high, low, close, period=14):
    """Average Directional Index."""
    n = len(close)
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]),
                               np.abs(low[1:] - close[:-1])))
    tr = np.concatenate([[np.nan], tr])

    plus_dm = np.where((high[1:] - high[:-1]) > (low[:-1] - low[1:]),
                       np.maximum(high[1:] - high[:-1], 0), 0)
    plus_dm = np.concatenate([[np.nan], plus_dm])

    minus_dm = np.where((low[:-1] - low[1:]) > (high[1:] - high[:-1]),
                        np.maximum(low[:-1] - low[1:], 0), 0)
    minus_dm = np.concatenate([[np.nan], minus_dm])

    tr_smooth = pd.Series(tr).ewm(span=period, adjust=False).mean().values
    plus_dm_smooth = pd.Series(plus_dm).ewm(span=period, adjust=False).mean().values
    minus_dm_smooth = pd.Series(minus_dm).ewm(span=period, adjust=False).mean().values

    plus_di = 100 * plus_dm_smooth / np.where(tr_smooth > 0, tr_smooth, 1)
    minus_di = 100 * minus_dm_smooth / np.where(tr_smooth > 0, tr_smooth, 1)

    di_sum = plus_di + minus_di
    dx = 100 * np.abs(plus_di - minus_di) / np.where(di_sum > 0, di_sum, 1)
    adx = pd.Series(dx).ewm(span=period, adjust=False).mean().values
    return adx


def detect_daily_regime(daily_df):
    """V4 causal regime detection on daily OHLCV. Returns regime array.
    0=CRISIS, 1=QUIET, 2=UPTREND, 3=RANGE, 4=DOWNTREND"""
    close = daily_df['close'].values
    high = daily_df['high'].values
    low = daily_df['low'].values
    n = len(close)

    adx = _adx(high, low, close, 14)
    ema_20 = _ema(close, 20)
    ema_50 = _ema(close, 50)

    # 20-day realized volatility (annualized)
    log_ret = np.log(close[1:] / close[:-1])
    log_ret = np.concatenate([[np.nan], log_ret])
    vol_20 = pd.Series(log_ret).rolling(20, min_periods=10).std().values * np.sqrt(365)

    # Expanding (causal) percentiles
    vol_series = pd.Series(vol_20)
    vol_p75 = vol_series.expanding(min_periods=MIN_REGIME_PERIODS).quantile(0.75).values
    vol_p25 = vol_series.expanding(min_periods=MIN_REGIME_PERIODS).quantile(0.25).values

    regimes = np.full(n, 3, dtype=np.int8)  # default: RANGE

    valid = ~np.isnan(adx) & ~np.isnan(vol_20) & ~np.isnan(vol_p75)
    crisis = valid & (vol_20 > vol_p75 * CRISIS_MULT)
    quiet = valid & ~crisis & (vol_20 < vol_p25 * QUIET_MULT)
    strong = valid & ~crisis & ~quiet & (adx > ADX_THRESHOLD)
    uptrend = strong & (ema_20 > ema_50)
    downtrend = strong & ~uptrend

    regimes[crisis] = 0
    regimes[quiet] = 1
    regimes[uptrend] = 2
    regimes[downtrend] = 4
    regimes[:20] = 3

    return regimes


# ==============================================================================
# Data Loading
# ==============================================================================

def load_btc_daily():
    """Load BTC perp 1h and resample to daily OHLCV."""
    print("[1/5] Loading BTC perp 1h data...")
    btc = pd.read_parquet(DATA_DIR / 'perp/1h_cache/BTC_1h.parquet')
    btc.index = pd.to_datetime(btc.index)
    btc.index.name = 'date'
    daily = pd.DataFrame({
        'open': btc['open'].resample('D').first(),
        'high': btc['high'].resample('D').max(),
        'low': btc['low'].resample('D').min(),
        'close': btc['close'].resample('D').last(),
        'volume': btc['volume'].resample('D').sum(),
    }).dropna()
    print(f"  BTC daily: {daily.index.min().date()} to {daily.index.max().date()}, "
          f"{len(daily)} bars")
    return daily


def load_positioning():
    """Load Binance daily positioning metrics for BTC."""
    print("[2/5] Loading positioning data (Binance daily metrics)...")
    df = pd.read_parquet(DATA_DIR / 'alternative/binance_metrics/all_symbols_daily_ls.parquet')
    btc = df[df['symbol'] == 'BTCUSDT'].copy()
    btc['date'] = pd.to_datetime(btc['date'])
    btc = btc.set_index('date').sort_index()
    btc = btc[~btc.index.duplicated(keep='last')]

    # Key columns:
    # sum_toptrader_ls_ratio = top trader position-based L/S
    # count_toptrader_ls_ratio = top trader account-based L/S
    # count_ls_ratio = global/retail account L/S
    # taker_buy_sell_ratio = taker buy/sell volume ratio
    pos = btc[['sum_toptrader_ls_ratio', 'count_toptrader_ls_ratio',
               'count_ls_ratio', 'taker_buy_sell_ratio']].copy()
    pos.columns = ['top_trader_pos_ls', 'top_trader_acct_ls',
                   'retail_ls', 'taker_bsr']

    first_valid = pos['top_trader_pos_ls'].first_valid_index()
    last_valid = pos['top_trader_pos_ls'].last_valid_index()
    n_valid = pos['top_trader_pos_ls'].notna().sum()
    print(f"  Top trader L/S: {first_valid.date()} to {last_valid.date()}, "
          f"{n_valid} valid days")
    print(f"  Retail L/S: {pos['retail_ls'].first_valid_index().date()} to "
          f"{pos['retail_ls'].last_valid_index().date()}, "
          f"{pos['retail_ls'].notna().sum()} valid days")
    return pos


def load_bybit_ls():
    """Load Bybit daily L/S ratio for BTC (supplementary retail data)."""
    print("[3/5] Loading Bybit daily L/S ratio...")
    path = DATA_DIR / 'alternative/ls_ratio_extended/bybit_btcusdt_ls_ratio.parquet'
    df = pd.read_parquet(path)
    df['date'] = pd.to_datetime(df['timestamp']).dt.normalize()
    df = df.set_index('date').sort_index()
    df = df[~df.index.duplicated(keep='first')]
    print(f"  Bybit L/S: {df.index.min().date()} to {df.index.max().date()}, "
          f"{len(df)} days")
    return df[['ls_ratio']].rename(columns={'ls_ratio': 'bybit_retail_ls'})


def load_dvol():
    """Load BTC DVOL from Deribit for VRP computation."""
    print("[4/5] Loading DVOL (Deribit implied vol index)...")
    dvol_path = DATA_DIR / 'alternative/deribit_options/dvol/btc_dvol_daily.json'
    with open(dvol_path) as f:
        data = json.load(f)

    records = [{'date': pd.Timestamp(row[0], unit='ms'), 'dvol': row[4]} for row in data]
    dvol = pd.DataFrame(records).set_index('date').sort_index()
    dvol = dvol[~dvol.index.duplicated(keep='last')]
    print(f"  DVOL: {dvol.index.min().date()} to {dvol.index.max().date()}, "
          f"{len(dvol)} days")
    return dvol


def compute_vrp(daily_df, dvol_df):
    """Compute Volatility Risk Premium (VRP) = Implied Vol - Realized Vol."""
    print("[5/5] Computing VRP...")
    log_ret = np.log(daily_df['close'] / daily_df['close'].shift(1))
    rv_20d = log_ret.rolling(20, min_periods=10).std() * np.sqrt(365) * 100  # annualized %
    rv_20d.name = 'rv_20d'

    vrp = pd.DataFrame({'rv_20d': rv_20d})
    vrp = vrp.join(dvol_df['dvol'], how='left')
    vrp['vrp'] = vrp['dvol'] - vrp['rv_20d']
    vrp['vrp_z'] = (vrp['vrp'] - vrp['vrp'].rolling(60, min_periods=30).mean()) / \
                   vrp['vrp'].rolling(60, min_periods=30).std().replace(0, np.nan)

    valid = vrp['vrp_z'].notna().sum()
    print(f"  VRP z-score: {valid} valid days, "
          f"mean={vrp['vrp_z'].mean():.2f}, std={vrp['vrp_z'].std():.2f}")
    return vrp


# ==============================================================================
# Signal Construction
# ==============================================================================

def compute_positioning_signals(pos_df, daily_df):
    """Compute all z-score-based positioning signals."""
    signals = pd.DataFrame(index=daily_df.index)

    # Top trader position L/S ratio z-score (30d)
    top_ls = pos_df['top_trader_pos_ls'].reindex(daily_df.index, method='ffill')
    top_ls_mean = top_ls.rolling(Z_LOOKBACK, min_periods=Z_LOOKBACK // 2).mean()
    top_ls_std = top_ls.rolling(Z_LOOKBACK, min_periods=Z_LOOKBACK // 2).std()
    signals['top_trader_ls_z'] = (top_ls - top_ls_mean) / top_ls_std.replace(0, np.nan)

    # Retail L/S ratio z-score (30d)
    retail_ls = pos_df['retail_ls'].reindex(daily_df.index, method='ffill')
    retail_mean = retail_ls.rolling(Z_LOOKBACK, min_periods=Z_LOOKBACK // 2).mean()
    retail_std = retail_ls.rolling(Z_LOOKBACK, min_periods=Z_LOOKBACK // 2).std()
    signals['retail_ls_z'] = (retail_ls - retail_mean) / retail_std.replace(0, np.nan)

    # L/S Divergence = top trader L/S - retail L/S (then z-score)
    divergence = top_ls - retail_ls
    div_mean = divergence.rolling(Z_LOOKBACK, min_periods=Z_LOOKBACK // 2).mean()
    div_std = divergence.rolling(Z_LOOKBACK, min_periods=Z_LOOKBACK // 2).std()
    signals['divergence_z'] = (divergence - div_mean) / div_std.replace(0, np.nan)

    # Combined z-score (average of top trader and divergence)
    signals['combined_z'] = (signals['top_trader_ls_z'] + signals['divergence_z']) / 2

    # Raw values for diagnostics
    signals['top_trader_ls_raw'] = top_ls
    signals['retail_ls_raw'] = retail_ls
    signals['divergence_raw'] = divergence

    return signals


# ==============================================================================
# Weekly Rebalance Strategy Simulation
# ==============================================================================

def simulate_range_strategy(daily_df, signal_series, regimes, strategy_name,
                            vrp_z=None, vrp_filter=False):
    """
    Simulate a range-only positioning strategy with weekly rebalancing.

    Rules:
    - ONLY active during RANGE regime (regime == 3)
    - When z > Z_ENTRY_STRONG (crowd long): SHORT BTC
    - When z < -Z_ENTRY_STRONG (crowd short): LONG BTC
    - When -Z_EXIT < z < Z_EXIT: FLAT
    - Between Z_EXIT and Z_ENTRY_STRONG: hold existing position
    - Rebalance weekly (Monday)
    - When regime changes away from RANGE: exit immediately
    - VRP filter (Strategy D): only trade when vrp_z < -0.5

    Returns: dict with equity curve, trades, metrics
    """
    # Align all data
    idx = daily_df.index
    close = daily_df['close'].values
    n = len(idx)

    # Determine weekly rebalance days (Mondays)
    is_monday = idx.dayofweek == 0
    # Also: first day, and any day where regime transitions to/from RANGE
    regime_changes = np.concatenate([[True], regimes[1:] != regimes[:-1]])

    equity = INITIAL_CAPITAL
    position = 0  # -1, 0, +1
    position_entry_price = 0.0
    position_size_usd = 0.0

    equity_curve = np.zeros(n)
    positions_arr = np.zeros(n)
    trades = []
    trade_entry_date = None
    trade_entry_price = 0.0

    cost_per_trade_pct = TOTAL_ONE_WAY_BPS / 10000.0

    for i in range(n):
        date = idx[i]
        price = close[i]
        regime = regimes[i]
        sig = signal_series.iloc[i] if not pd.isna(signal_series.iloc[i]) else 0.0

        # VRP filter for Strategy D
        vrp_ok = True
        if vrp_filter and vrp_z is not None:
            vrp_val = vrp_z.iloc[i] if i < len(vrp_z) and not pd.isna(vrp_z.iloc[i]) else 0.0
            vrp_ok = vrp_val < -0.5

        # 1. Mark-to-market existing position
        if position != 0 and i > 0:
            price_change_pct = (price - close[i - 1]) / close[i - 1]
            pnl = position * price_change_pct * position_size_usd
            equity += pnl

        # 2. Check regime exit: if not RANGE, force exit
        if regime != 3 and position != 0:
            cost = position_size_usd * cost_per_trade_pct
            equity -= cost
            trades.append({
                'entry_date': trade_entry_date,
                'exit_date': date,
                'entry_price': trade_entry_price,
                'exit_price': price,
                'direction': position,
                'pnl_pct': position * (price / trade_entry_price - 1) * 100,
                'cost_pct': cost / INITIAL_CAPITAL * 100,
                'exit_reason': 'regime_change',
            })
            position = 0
            position_size_usd = 0.0

        # 3. Weekly rebalance or regime transition
        should_rebalance = (is_monday[i] or regime_changes[i]) and regime == 3

        if should_rebalance:
            # Determine target position
            if sig > Z_ENTRY_STRONG and vrp_ok:
                target = -1  # crowd long -> SHORT
            elif sig < -Z_ENTRY_STRONG and vrp_ok:
                target = 1   # crowd short -> LONG
            elif abs(sig) < Z_EXIT:
                target = 0   # FLAT
            else:
                target = position  # hold (in deadband between Z_EXIT and Z_ENTRY_STRONG)

            # Execute if position changes
            if target != position:
                # Close existing
                if position != 0:
                    cost = position_size_usd * cost_per_trade_pct
                    equity -= cost
                    trades.append({
                        'entry_date': trade_entry_date,
                        'exit_date': date,
                        'entry_price': trade_entry_price,
                        'exit_price': price,
                        'direction': position,
                        'pnl_pct': position * (price / trade_entry_price - 1) * 100,
                        'cost_pct': cost / INITIAL_CAPITAL * 100,
                        'exit_reason': 'signal_change',
                    })

                # Open new
                if target != 0:
                    position_size_usd = equity * POSITION_SIZE_PCT
                    cost = position_size_usd * cost_per_trade_pct
                    equity -= cost
                    trade_entry_date = date
                    trade_entry_price = price

                position = target
                if target == 0:
                    position_size_usd = 0.0

        equity_curve[i] = equity
        positions_arr[i] = position

    # Close any remaining position at end
    if position != 0:
        cost = position_size_usd * cost_per_trade_pct
        equity -= cost
        trades.append({
            'entry_date': trade_entry_date,
            'exit_date': idx[-1],
            'entry_price': trade_entry_price,
            'exit_price': close[-1],
            'direction': position,
            'pnl_pct': position * (close[-1] / trade_entry_price - 1) * 100,
            'cost_pct': cost / INITIAL_CAPITAL * 100,
            'exit_reason': 'end_of_data',
        })
        equity_curve[-1] = equity

    return {
        'name': strategy_name,
        'equity_curve': pd.Series(equity_curve, index=idx),
        'positions': pd.Series(positions_arr, index=idx),
        'trades': pd.DataFrame(trades) if trades else pd.DataFrame(),
        'final_equity': equity,
    }


# ==============================================================================
# Metrics Computation
# ==============================================================================

def compute_metrics(result, regimes_series, is_end, oos_start):
    """Compute comprehensive metrics for a strategy result."""
    ec = result['equity_curve']
    trades_df = result['trades']
    name = result['name']

    # Range-only equity curve (only count days where regime == 3)
    range_mask = regimes_series == 3
    range_ec = ec[range_mask]

    # Daily returns
    daily_ret = ec.pct_change().fillna(0)
    range_ret = daily_ret[range_mask]

    metrics = {'name': name}

    # Full period
    if len(range_ret) > 0 and range_ret.std() > 0:
        # Annualize: range_ret covers ~35% of days, but we annualize the range-only returns
        n_range_days = len(range_ret)
        n_total_days = len(daily_ret)
        range_frac = n_range_days / n_total_days

        # Annualized return during range periods
        range_cum = (1 + range_ret).cumprod()
        total_range_return = range_cum.iloc[-1] / range_cum.iloc[0] - 1 if range_cum.iloc[0] > 0 else 0
        n_years = n_total_days / 365.25
        range_annual_ret = (1 + total_range_return) ** (1 / n_years) - 1 if n_years > 0 else 0

        # Sharpe of range returns (annualized)
        range_sharpe = range_ret.mean() / range_ret.std() * np.sqrt(365) if range_ret.std() > 0 else 0

        # MaxDD during range
        range_ec_only = (1 + range_ret).cumprod()
        range_maxdd = (range_ec_only / range_ec_only.cummax() - 1).min()

        metrics['range_annual_ret'] = range_annual_ret
        metrics['range_sharpe'] = range_sharpe
        metrics['range_maxdd'] = range_maxdd
        metrics['range_days'] = n_range_days
        metrics['range_frac'] = range_frac
    else:
        metrics['range_annual_ret'] = 0
        metrics['range_sharpe'] = 0
        metrics['range_maxdd'] = 0
        metrics['range_days'] = 0
        metrics['range_frac'] = 0

    # Overall equity curve metrics (all days)
    overall_cum = (1 + daily_ret).cumprod()
    overall_total_ret = overall_cum.iloc[-1] - 1
    n_years_total = len(daily_ret) / 365.25
    overall_annual_ret = (1 + overall_total_ret) ** (1 / n_years_total) - 1 if n_years_total > 0 else 0
    overall_sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(365) if daily_ret.std() > 0 else 0
    overall_maxdd = (overall_cum / overall_cum.cummax() - 1).min()

    metrics['overall_annual_ret'] = overall_annual_ret
    metrics['overall_sharpe'] = overall_sharpe
    metrics['overall_maxdd'] = overall_maxdd

    # Trade statistics
    if len(trades_df) > 0:
        metrics['n_trades'] = len(trades_df)
        metrics['win_rate'] = (trades_df['pnl_pct'] > 0).mean()
        metrics['avg_trade_pnl'] = trades_df['pnl_pct'].mean()
        metrics['total_cost_pct'] = trades_df['cost_pct'].sum()

        # Holding period
        if 'entry_date' in trades_df.columns and 'exit_date' in trades_df.columns:
            trades_df_calc = trades_df.copy()
            trades_df_calc['entry_date'] = pd.to_datetime(trades_df_calc['entry_date'])
            trades_df_calc['exit_date'] = pd.to_datetime(trades_df_calc['exit_date'])
            hold_days = (trades_df_calc['exit_date'] - trades_df_calc['entry_date']).dt.days
            metrics['avg_hold_days'] = hold_days.mean()
            metrics['median_hold_days'] = hold_days.median()
        else:
            metrics['avg_hold_days'] = 0
            metrics['median_hold_days'] = 0

        # Cost drag per year
        cost_total = trades_df['cost_pct'].sum()
        metrics['cost_drag_annual'] = cost_total / n_years_total if n_years_total > 0 else 0

        # Regime change exits
        if 'exit_reason' in trades_df.columns:
            metrics['regime_exits'] = (trades_df['exit_reason'] == 'regime_change').sum()
            metrics['signal_exits'] = (trades_df['exit_reason'] == 'signal_change').sum()
    else:
        metrics['n_trades'] = 0
        metrics['win_rate'] = 0
        metrics['avg_trade_pnl'] = 0
        metrics['total_cost_pct'] = 0
        metrics['avg_hold_days'] = 0
        metrics['median_hold_days'] = 0
        metrics['cost_drag_annual'] = 0
        metrics['regime_exits'] = 0
        metrics['signal_exits'] = 0

    # IS/OOS split
    for label, period_mask in [('IS', ec.index <= is_end), ('OOS', ec.index >= oos_start)]:
        sub_ret = daily_ret[period_mask]
        # Intersect period mask with range mask (both on the full daily index)
        combined_mask = period_mask & range_mask
        sub_range_ret = daily_ret[combined_mask]
        if len(sub_range_ret) > 10 and sub_range_ret.std() > 0:
            sub_sharpe = sub_range_ret.mean() / sub_range_ret.std() * np.sqrt(365)
            sub_cum = (1 + sub_range_ret).cumprod()
            sub_maxdd = (sub_cum / sub_cum.cummax() - 1).min()
            sub_n_years = len(sub_ret) / 365.25
            sub_total = sub_cum.iloc[-1] / sub_cum.iloc[0] - 1
            sub_annual = (1 + sub_total) ** (1 / sub_n_years) - 1 if sub_n_years > 0 else 0
        else:
            sub_sharpe = 0
            sub_maxdd = 0
            sub_annual = 0
        metrics[f'{label}_range_sharpe'] = sub_sharpe
        metrics[f'{label}_range_annual_ret'] = sub_annual
        metrics[f'{label}_range_maxdd'] = sub_maxdd

    return metrics


# ==============================================================================
# V3 Benchmark (Buy & Hold During Range)
# ==============================================================================

def simulate_v3_range_benchmark(daily_df, regimes):
    """Simulate V3 behavior during RANGE: always long BTC (trend-following
    gets whipsawed in range, resulting in losses)."""
    idx = daily_df.index
    close = daily_df['close'].values
    n = len(idx)

    equity = INITIAL_CAPITAL
    position = 0
    position_size_usd = 0.0
    equity_curve = np.zeros(n)
    cost_pct = TOTAL_ONE_WAY_BPS / 10000.0
    entry_price = 0.0

    for i in range(n):
        regime = regimes[i]

        # MTM
        if position != 0 and i > 0:
            pct_change = (close[i] - close[i - 1]) / close[i - 1]
            equity += position * pct_change * position_size_usd

        # Enter range: go long (simulating trend-following being whipsawed)
        if regime == 3 and position == 0:
            position = 1
            position_size_usd = equity * POSITION_SIZE_PCT
            equity -= position_size_usd * cost_pct
            entry_price = close[i]

        # Exit range
        elif regime != 3 and position != 0:
            equity -= position_size_usd * cost_pct
            position = 0
            position_size_usd = 0.0

        equity_curve[i] = equity

    return pd.Series(equity_curve, index=idx)


# ==============================================================================
# Main Analysis
# ==============================================================================

def main():
    print("=" * 80)
    print("RANGE REGIME POSITIONING STRATEGY — STANDALONE BACKTEST")
    print("=" * 80)
    print(f"Capital: ${INITIAL_CAPITAL:,.0f} | Position size: {POSITION_SIZE_PCT:.0%} of equity")
    print(f"Fees: {FEE_BPS}bps/side | Slippage: {SLIPPAGE_BPS}bps/side | "
          f"Total one-way: {TOTAL_ONE_WAY_BPS}bps")
    print(f"Z-score lookback: {Z_LOOKBACK}d | Entry: |z|>{Z_ENTRY_STRONG} | "
          f"Exit: |z|<{Z_EXIT}")
    print(f"Rebalance: Weekly (Monday)")
    print(f"IS/OOS split: {IS_END.date()} / {OOS_START.date()}")
    print()

    # ── Load Data ──
    daily_df = load_btc_daily()
    pos_df = load_positioning()
    bybit_df = load_bybit_ls()
    dvol_df = load_dvol()
    vrp_df = compute_vrp(daily_df, dvol_df)

    # ── Compute Regimes ──
    print("\n[6/6] Computing V4 regimes...")
    regimes = detect_daily_regime(daily_df)
    regime_names = {0: 'CRISIS', 1: 'QUIET', 2: 'UPTREND', 3: 'RANGE', 4: 'DOWNTREND'}
    regime_series = pd.Series(regimes, index=daily_df.index)

    print("  Regime distribution:")
    for r_id, r_name in regime_names.items():
        count = (regimes == r_id).sum()
        pct = count / len(regimes) * 100
        print(f"    {r_name} ({r_id}): {count} days ({pct:.1f}%)")

    range_days = (regimes == 3).sum()
    range_pct = range_days / len(regimes) * 100
    print(f"\n  RANGE regime: {range_days} days ({range_pct:.1f}%) -- "
          f"this is where V3 loses money (Sharpe -1.01)")

    # ── Compute Signals ──
    print("\n--- Computing Positioning Signals ---")
    signals = compute_positioning_signals(pos_df, daily_df)

    # Align VRP z-score to daily index
    vrp_z_aligned = vrp_df['vrp_z'].reindex(daily_df.index, method='ffill')

    # ── Signal Diagnostics ──
    print("\n--- Signal Diagnostics (during RANGE regime) ---")
    range_mask = regime_series == 3
    for sig_name in ['top_trader_ls_z', 'divergence_z', 'combined_z']:
        sig = signals[sig_name][range_mask].dropna()
        if len(sig) > 0:
            n_long = (sig < -Z_ENTRY_STRONG).sum()
            n_short = (sig > Z_ENTRY_STRONG).sum()
            n_flat = (sig.abs() < Z_EXIT).sum()
            print(f"  {sig_name}: mean={sig.mean():.3f}, std={sig.std():.3f}, "
                  f"min={sig.min():.3f}, max={sig.max():.3f}")
            print(f"    SHORT signals (z>{Z_ENTRY_STRONG}): {n_short} days "
                  f"({n_short/len(sig)*100:.1f}%)")
            print(f"    LONG signals (z<-{Z_ENTRY_STRONG}): {n_long} days "
                  f"({n_long/len(sig)*100:.1f}%)")
            print(f"    FLAT signals (|z|<{Z_EXIT}): {n_flat} days "
                  f"({n_flat/len(sig)*100:.1f}%)")

    # ── IC Analysis (Range-only) ──
    print("\n--- IC Analysis (RANGE regime only) ---")
    fwd_7d = daily_df['close'].pct_change(7).shift(-7)
    fwd_14d = daily_df['close'].pct_change(14).shift(-14)

    for sig_name in ['top_trader_ls_z', 'divergence_z', 'combined_z']:
        sig = signals[sig_name]
        for fwd_label, fwd_ret in [('7d', fwd_7d), ('14d', fwd_14d)]:
            aligned = pd.concat([sig[range_mask].rename('sig'),
                                 fwd_ret[range_mask].rename('ret')], axis=1).dropna()
            if len(aligned) > 30:
                ic, pval = stats.spearmanr(aligned['sig'], aligned['ret'])
                n_obs = len(aligned)
                t_stat = ic * np.sqrt((n_obs - 2) / (1 - ic**2)) if abs(ic) < 1 else np.inf
                print(f"  {sig_name} -> {fwd_label}: IC={ic:+.4f} "
                      f"(t={t_stat:+.2f}, p={pval:.4f}, n={n_obs})")

    # ── Run Strategies ──
    print("\n" + "=" * 80)
    print("STRATEGY SIMULATION")
    print("=" * 80)

    results = {}

    # Strategy A: Positioning Contrarian (top trader L/S z-score)
    print("\n--- Strategy A: Positioning Contrarian (Top Trader L/S z-score) ---")
    res_a = simulate_range_strategy(
        daily_df, signals['top_trader_ls_z'], regimes,
        'A: Positioning Contrarian')
    results['A'] = res_a

    # Strategy B: L/S Divergence (top trader - retail z-score)
    print("\n--- Strategy B: L/S Divergence (Top Trader - Retail z-score) ---")
    res_b = simulate_range_strategy(
        daily_df, signals['divergence_z'], regimes,
        'B: L/S Divergence')
    results['B'] = res_b

    # Strategy C: Combined (average of A and B z-scores)
    print("\n--- Strategy C: Combined (Average of A and B z-scores) ---")
    res_c = simulate_range_strategy(
        daily_df, signals['combined_z'], regimes,
        'C: Combined')
    results['C'] = res_c

    # Strategy D: Contrarian + VRP filter
    print("\n--- Strategy D: Contrarian + VRP Filter (vrp_z < -0.5) ---")
    res_d = simulate_range_strategy(
        daily_df, signals['top_trader_ls_z'], regimes,
        'D: Contrarian + VRP Filter',
        vrp_z=vrp_z_aligned, vrp_filter=True)
    results['D'] = res_d

    # V3 benchmark (buy-and-hold during range)
    print("\n--- Benchmark: V3 Buy-and-Hold During RANGE ---")
    v3_ec = simulate_v3_range_benchmark(daily_df, regimes)

    # ── Compute Metrics ──
    print("\n" + "=" * 80)
    print("RESULTS")
    print("=" * 80)

    all_metrics = {}
    for key, res in results.items():
        metrics = compute_metrics(res, regime_series, IS_END, OOS_START)
        all_metrics[key] = metrics

    # V3 benchmark metrics
    v3_ret = v3_ec.pct_change().fillna(0)
    v3_range_ret = v3_ret[range_mask]
    v3_range_cum = (1 + v3_range_ret).cumprod()

    v3_metrics = {
        'name': 'V3 BuyHold (Range)',
        'range_sharpe': v3_range_ret.mean() / v3_range_ret.std() * np.sqrt(365) if v3_range_ret.std() > 0 else 0,
        'range_annual_ret': 0,
        'range_maxdd': (v3_range_cum / v3_range_cum.cummax() - 1).min() if len(v3_range_cum) > 0 else 0,
    }
    n_years = len(v3_ret) / 365.25
    if n_years > 0 and len(v3_range_cum) > 0:
        total_ret = v3_range_cum.iloc[-1] / v3_range_cum.iloc[0] - 1
        v3_metrics['range_annual_ret'] = (1 + total_ret) ** (1 / n_years) - 1

    # ── Print Results Table ──
    print(f"\n{'Strategy':<35s} | {'Range Ann Ret':>13s} | {'Range Sharpe':>12s} | "
          f"{'Range MaxDD':>11s} | {'Trades':>6s} | {'Win Rate':>8s} | "
          f"{'Avg Hold':>8s} | {'Cost/yr':>8s}")
    print("-" * 120)

    # V3 benchmark first
    print(f"{'V3 BuyHold (Range)':<35s} | "
          f"{v3_metrics['range_annual_ret']:>12.2%} | "
          f"{v3_metrics['range_sharpe']:>12.3f} | "
          f"{v3_metrics['range_maxdd']:>10.2%} | "
          f"{'---':>6s} | {'---':>8s} | {'---':>8s} | {'---':>8s}")

    for key in ['A', 'B', 'C', 'D']:
        m = all_metrics[key]
        print(f"{m['name']:<35s} | "
              f"{m['range_annual_ret']:>12.2%} | "
              f"{m['range_sharpe']:>12.3f} | "
              f"{m['range_maxdd']:>10.2%} | "
              f"{m['n_trades']:>6d} | "
              f"{m['win_rate']:>7.1%} | "
              f"{m['avg_hold_days']:>7.1f}d | "
              f"{m['cost_drag_annual']:>7.2f}%")

    # ── IS/OOS Breakdown ──
    print(f"\n{'--- IS/OOS Breakdown ---':^80}")
    print(f"\n{'Strategy':<35s} | {'IS Range Sharpe':>15s} | {'OOS Range Sharpe':>16s} | "
          f"{'IS Range Ret':>12s} | {'OOS Range Ret':>13s}")
    print("-" * 100)

    for key in ['A', 'B', 'C', 'D']:
        m = all_metrics[key]
        print(f"{m['name']:<35s} | "
              f"{m['IS_range_sharpe']:>15.3f} | "
              f"{m['OOS_range_sharpe']:>16.3f} | "
              f"{m['IS_range_annual_ret']:>11.2%} | "
              f"{m['OOS_range_annual_ret']:>12.2%}")

    # ── Trade Details ──
    for key in ['A', 'B', 'C', 'D']:
        res = results[key]
        trades_df = res['trades']
        if len(trades_df) == 0:
            print(f"\n  {res['name']}: NO TRADES")
            continue

        print(f"\n  {res['name']} — Trade Summary ({len(trades_df)} trades):")
        winners = trades_df[trades_df['pnl_pct'] > 0]
        losers = trades_df[trades_df['pnl_pct'] <= 0]
        print(f"    Winners: {len(winners)} (avg: {winners['pnl_pct'].mean():+.2f}%)" if len(winners) > 0 else "    Winners: 0")
        print(f"    Losers:  {len(losers)} (avg: {losers['pnl_pct'].mean():+.2f}%)" if len(losers) > 0 else "    Losers: 0")

        if 'direction' in trades_df.columns:
            longs = trades_df[trades_df['direction'] == 1]
            shorts = trades_df[trades_df['direction'] == -1]
            if len(longs) > 0:
                print(f"    Long trades: {len(longs)}, avg pnl: {longs['pnl_pct'].mean():+.2f}%")
            if len(shorts) > 0:
                print(f"    Short trades: {len(shorts)}, avg pnl: {shorts['pnl_pct'].mean():+.2f}%")

        if 'exit_reason' in trades_df.columns:
            print(f"    Exit reasons: {dict(trades_df['exit_reason'].value_counts())}")

        # Show individual trades
        print(f"\n    {'Entry':>12s} | {'Exit':>12s} | {'Dir':>4s} | "
              f"{'EntryP':>10s} | {'ExitP':>10s} | {'PnL%':>7s} | {'Reason':>15s}")
        print(f"    {'-'*80}")
        for _, t in trades_df.iterrows():
            entry_d = pd.Timestamp(t['entry_date']).strftime('%Y-%m-%d') if pd.notna(t.get('entry_date')) else '?'
            exit_d = pd.Timestamp(t['exit_date']).strftime('%Y-%m-%d') if pd.notna(t.get('exit_date')) else '?'
            d = 'LONG' if t.get('direction', 0) == 1 else 'SHORT'
            print(f"    {entry_d:>12s} | {exit_d:>12s} | {d:>4s} | "
                  f"{t.get('entry_price', 0):>10,.0f} | {t.get('exit_price', 0):>10,.0f} | "
                  f"{t.get('pnl_pct', 0):>+6.2f}% | {t.get('exit_reason', '?'):>15s}")

    # ── Year-by-Year Analysis ──
    print(f"\n{'--- Year-by-Year Range Returns ---':^80}")
    for key in ['A', 'B', 'C', 'D']:
        res = results[key]
        ec = res['equity_curve']
        ret = ec.pct_change().fillna(0)
        range_ret = ret[range_mask]

        print(f"\n  {res['name']}:")
        for year in sorted(range_ret.index.year.unique()):
            yr_ret = range_ret[range_ret.index.year == year]
            if len(yr_ret) > 10:
                yr_cum = (1 + yr_ret).cumprod()
                yr_total = yr_cum.iloc[-1] - 1
                yr_sharpe = yr_ret.mean() / yr_ret.std() * np.sqrt(365) if yr_ret.std() > 0 else 0
                print(f"    {year}: ret={yr_total:+.2%}, sharpe={yr_sharpe:.2f}, "
                      f"range_days={len(yr_ret)}")

    # ── Key Question Answer ──
    print("\n" + "=" * 80)
    print("KEY QUESTION: Can positioning generate POSITIVE returns during RANGE?")
    print("=" * 80)

    best_strat = None
    best_sharpe = -999
    for key in ['A', 'B', 'C', 'D']:
        m = all_metrics[key]
        if m['range_sharpe'] > best_sharpe:
            best_sharpe = m['range_sharpe']
            best_strat = key

    best_m = all_metrics[best_strat]
    v3_sharpe = v3_metrics['range_sharpe']

    print(f"\n  V3 during RANGE:        Sharpe = {v3_sharpe:.3f}")
    print(f"  Best positioning strat: {best_m['name']}")
    print(f"    Range Sharpe:  {best_m['range_sharpe']:.3f}")
    print(f"    Range Ann Ret: {best_m['range_annual_ret']:.2%}")
    print(f"    Range MaxDD:   {best_m['range_maxdd']:.2%}")
    print(f"    N Trades:      {best_m['n_trades']}")
    print(f"    Cost/yr:       {best_m['cost_drag_annual']:.2f}%")

    if best_m['range_sharpe'] > 0:
        improvement = best_m['range_annual_ret'] - v3_metrics['range_annual_ret']
        print(f"\n  POSITIVE range returns achieved!")
        print(f"  Improvement over V3 during range: {improvement:+.2%} annualized")
        range_contribution = best_m['range_annual_ret'] * best_m.get('range_frac', 0.35)
        print(f"  Portfolio contribution (~{best_m.get('range_frac', 0.35):.0%} of time): "
              f"{range_contribution:+.2%} to annual return")
    else:
        print(f"\n  Cannot generate positive returns during RANGE.")
        if best_m['range_sharpe'] > v3_sharpe:
            print(f"  But still better than V3: Sharpe {best_m['range_sharpe']:.3f} vs {v3_sharpe:.3f}")
            print(f"  Reduces range losses from V3's {v3_metrics['range_annual_ret']:.2%} to "
                  f"{best_m['range_annual_ret']:.2%}")
        else:
            print(f"  And worse than V3 buy-and-hold during range.")

    # IS vs OOS consistency
    print(f"\n  IS/OOS Consistency:")
    for key in ['A', 'B', 'C', 'D']:
        m = all_metrics[key]
        is_s = m['IS_range_sharpe']
        oos_s = m['OOS_range_sharpe']
        consistent = 'YES' if (is_s > 0 and oos_s > 0) or (is_s < 0 and oos_s < 0) else 'NO'
        decay = oos_s / is_s if abs(is_s) > 0.01 else float('inf')
        print(f"    {m['name']:<35s}: IS={is_s:+.3f}, OOS={oos_s:+.3f}, "
              f"consistent={consistent}, decay={decay:.2f}x")

    # ── Generate Results Markdown ──
    generate_results_md(all_metrics, v3_metrics, results, regime_series, signals,
                        range_mask, daily_df, vrp_df)

    return all_metrics, results


def generate_results_md(all_metrics, v3_metrics, results, regime_series,
                        signals, range_mask, daily_df, vrp_df):
    """Generate markdown results file."""
    lines = []
    lines.append("# Range Regime Positioning Strategy — Results")
    lines.append(f"\nAnalysis date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"BTC perp data: {daily_df.index.min().date()} to {daily_df.index.max().date()}")
    lines.append(f"IS/OOS split: 2024-12-31 / 2025-01-01")
    lines.append("")

    lines.append("## Context")
    lines.append("- V3 trend-following during RANGE: Sharpe -1.01, -21.4% annualized (finding #68)")
    lines.append("- RANGE is ~35% of time -- the biggest drag on V3 returns")
    lines.append("- Finding #42 said standalone positioning FAILS with DAILY rebalancing (11%/yr cost drag)")
    lines.append("- This test uses WEEKLY rebalancing (findings #44, #47)")
    lines.append("- Capital: $200K, position size 50%, fees 4bps + slippage 5bps per side")
    lines.append("")

    # Regime distribution
    regime_names = {0: 'CRISIS', 1: 'QUIET', 2: 'UPTREND', 3: 'RANGE', 4: 'DOWNTREND'}
    lines.append("## Regime Distribution")
    lines.append("")
    lines.append("| Regime | Days | Pct |")
    lines.append("|--------|------|-----|")
    for r_id, r_name in regime_names.items():
        count = (regime_series == r_id).sum()
        pct = count / len(regime_series) * 100
        lines.append(f"| {r_name} ({r_id}) | {count} | {pct:.1f}% |")
    lines.append("")

    # Signal diagnostics
    lines.append("## Signal Diagnostics (Range Regime Only)")
    lines.append("")
    lines.append("| Signal | Mean | Std | SHORT (z>1.5) | LONG (z<-1.5) | FLAT (|z|<0.5) |")
    lines.append("|--------|------|-----|---------------|---------------|----------------|")
    for sig_name in ['top_trader_ls_z', 'divergence_z', 'combined_z']:
        sig = signals[sig_name][range_mask].dropna()
        if len(sig) > 0:
            n_short = (sig > Z_ENTRY_STRONG).sum()
            n_long = (sig < -Z_ENTRY_STRONG).sum()
            n_flat = (sig.abs() < Z_EXIT).sum()
            lines.append(f"| {sig_name} | {sig.mean():.3f} | {sig.std():.3f} | "
                         f"{n_short} ({n_short/len(sig)*100:.1f}%) | "
                         f"{n_long} ({n_long/len(sig)*100:.1f}%) | "
                         f"{n_flat} ({n_flat/len(sig)*100:.1f}%) |")
    lines.append("")

    # Main results table
    lines.append("## Strategy Results (Range Regime Only)")
    lines.append("")
    lines.append("| Strategy | Range Ann Ret | Range Sharpe | Range MaxDD | "
                 "Trades | Win Rate | Avg Hold | Cost/yr |")
    lines.append("|----------|---------------|--------------|-------------|"
                 "--------|----------|----------|---------|")

    lines.append(f"| V3 BuyHold (Range) | {v3_metrics['range_annual_ret']:.2%} | "
                 f"{v3_metrics['range_sharpe']:.3f} | {v3_metrics['range_maxdd']:.2%} | "
                 f"--- | --- | --- | --- |")

    for key in ['A', 'B', 'C', 'D']:
        m = all_metrics[key]
        lines.append(f"| {m['name']} | {m['range_annual_ret']:.2%} | "
                     f"{m['range_sharpe']:.3f} | {m['range_maxdd']:.2%} | "
                     f"{m['n_trades']} | {m['win_rate']:.1%} | "
                     f"{m['avg_hold_days']:.1f}d | {m['cost_drag_annual']:.2f}% |")
    lines.append("")

    # IS/OOS
    lines.append("## IS/OOS Breakdown")
    lines.append("")
    lines.append("| Strategy | IS Range Sharpe | OOS Range Sharpe | IS Range Ret | OOS Range Ret |")
    lines.append("|----------|-----------------|------------------|--------------|---------------|")
    for key in ['A', 'B', 'C', 'D']:
        m = all_metrics[key]
        lines.append(f"| {m['name']} | {m['IS_range_sharpe']:.3f} | "
                     f"{m['OOS_range_sharpe']:.3f} | "
                     f"{m['IS_range_annual_ret']:.2%} | "
                     f"{m['OOS_range_annual_ret']:.2%} |")
    lines.append("")

    # Trade detail tables
    for key in ['A', 'B', 'C', 'D']:
        res = results[key]
        trades_df = res['trades']
        if len(trades_df) == 0:
            lines.append(f"## {res['name']} — No Trades")
            continue
        lines.append(f"## {res['name']} — Trade Log ({len(trades_df)} trades)")
        lines.append("")
        lines.append("| Entry | Exit | Dir | Entry Price | Exit Price | PnL% | Reason |")
        lines.append("|-------|------|-----|-------------|------------|------|--------|")
        for _, t in trades_df.iterrows():
            entry_d = pd.Timestamp(t['entry_date']).strftime('%Y-%m-%d') if pd.notna(t.get('entry_date')) else '?'
            exit_d = pd.Timestamp(t['exit_date']).strftime('%Y-%m-%d') if pd.notna(t.get('exit_date')) else '?'
            d = 'LONG' if t.get('direction', 0) == 1 else 'SHORT'
            lines.append(f"| {entry_d} | {exit_d} | {d} | "
                         f"${t.get('entry_price', 0):,.0f} | ${t.get('exit_price', 0):,.0f} | "
                         f"{t.get('pnl_pct', 0):+.2f}% | {t.get('exit_reason', '?')} |")
        lines.append("")

    # Key question
    lines.append("## Key Question: Positive Returns During RANGE?")
    lines.append("")

    best_strat = max(all_metrics, key=lambda k: all_metrics[k]['range_sharpe'])
    best_m = all_metrics[best_strat]

    if best_m['range_sharpe'] > 0:
        lines.append(f"**YES** -- {best_m['name']} achieves positive range returns.")
        lines.append(f"- Range Sharpe: {best_m['range_sharpe']:.3f}")
        lines.append(f"- Range annualized return: {best_m['range_annual_ret']:.2%}")
        improvement = best_m['range_annual_ret'] - v3_metrics['range_annual_ret']
        lines.append(f"- Improvement over V3 during range: {improvement:+.2%}")
        range_contribution = best_m['range_annual_ret'] * best_m.get('range_frac', 0.35)
        lines.append(f"- Portfolio contribution (~35% of time): {range_contribution:+.2%} to annual return")
    else:
        lines.append(f"**NO** -- Best strategy ({best_m['name']}) has negative range Sharpe: "
                     f"{best_m['range_sharpe']:.3f}")
        if best_m['range_sharpe'] > v3_metrics['range_sharpe']:
            lines.append(f"- But better than V3 BuyHold: {best_m['range_sharpe']:.3f} vs "
                         f"{v3_metrics['range_sharpe']:.3f}")
            lines.append(f"- Reduces range losses from {v3_metrics['range_annual_ret']:.2%} to "
                         f"{best_m['range_annual_ret']:.2%}")
        else:
            lines.append(f"- And worse than V3 BuyHold during range.")
    lines.append("")

    # IS/OOS consistency
    lines.append("## IS/OOS Consistency Check")
    lines.append("")
    any_consistent = False
    for key in ['A', 'B', 'C', 'D']:
        m = all_metrics[key]
        is_s = m['IS_range_sharpe']
        oos_s = m['OOS_range_sharpe']
        consistent = (is_s > 0 and oos_s > 0) or (is_s < 0 and oos_s < 0)
        if consistent and is_s > 0:
            any_consistent = True
        lines.append(f"- {m['name']}: IS={is_s:+.3f}, OOS={oos_s:+.3f}, "
                     f"consistent={'YES' if consistent else 'NO'}")
    lines.append("")

    if not any_consistent:
        lines.append("**WARNING:** No strategy shows consistent positive returns across IS and OOS.")
    lines.append("")

    # Comparison with finding #42 (daily rebalance)
    lines.append("## Comparison with Finding #42 (Daily Rebalance)")
    lines.append("")
    lines.append("Finding #42 concluded standalone positioning FAILS with daily rebalancing "
                 "due to 11%/yr cost drag.")
    lines.append("")
    for key in ['A', 'B', 'C', 'D']:
        m = all_metrics[key]
        lines.append(f"- {m['name']}: weekly cost drag = {m['cost_drag_annual']:.2f}%/yr "
                     f"(vs ~11% daily)")
    lines.append("")

    # Methodology
    lines.append("## Methodology")
    lines.append("- **Regime detection**: V4 causal regime (ADX + expanding vol percentiles)")
    lines.append("- **Positioning data**: Binance daily top trader position L/S, global account L/S")
    lines.append("- **Z-score**: 30-day rolling z-score of positioning metrics")
    lines.append("- **Entry**: z > 1.5 (crowd long) -> SHORT, z < -1.5 (crowd short) -> LONG")
    lines.append("- **Exit**: z returns to within +/-0.5, or regime changes from RANGE")
    lines.append("- **Rebalance**: Weekly (Monday close)")
    lines.append("- **Position size**: 50% of equity, no leverage")
    lines.append("- **Costs**: 4bps fees + 5bps slippage per side")
    lines.append("- **IS/OOS**: 2020-09 to 2024-12 / 2025-01 to latest")
    lines.append("")

    # Write
    md_path = OUTPUT_DIR / 'range_positioning_strategy_results.md'
    with open(md_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f"\nResults saved to {md_path}")


if __name__ == '__main__':
    all_metrics, results = main()
