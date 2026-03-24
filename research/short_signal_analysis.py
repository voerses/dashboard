#!/usr/bin/env python3
"""
Short-Selling Signal Discovery for Crypto Perps
================================================
Tests 6 short signal hypotheses on BTC perp (1h), then validates top signals on ETH.
"""

import pandas as pd
import numpy as np
import json
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# DATA LOADING
# =============================================================================

def load_perp(ticker='BTC'):
    df = pd.read_parquet(f'/workspace/crypto_backtest/data/perp/1h_cache/{ticker}_1h.parquet')
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    return df

def load_macro(name):
    df = pd.read_parquet(f'/workspace/crypto_backtest/data/alternative/macro/{name}.parquet')
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.set_index('Date').sort_index()
    return df

def load_positioning():
    df = pd.read_parquet('/workspace/crypto_backtest/data/alternative/binance_metrics/daily/BTCUSDT_ls_metrics.parquet')
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date').sort_index()
    return df

def load_dvol():
    with open('/workspace/crypto_backtest/data/alternative/deribit_options/dvol/btc_dvol_daily.json') as f:
        raw = json.load(f)
    # Format: [timestamp_ms, open, high, low, close]
    records = []
    for row in raw:
        records.append({
            'date': pd.to_datetime(row[0], unit='ms'),
            'dvol_open': row[1],
            'dvol_high': row[2],
            'dvol_low': row[3],
            'dvol_close': row[4]
        })
    df = pd.DataFrame(records).set_index('date').sort_index()
    return df

print("Loading data...")
btc = load_perp('BTC')
eth = load_perp('ETH')
dxy = load_macro('usd_index')
us10y = load_macro('us10y_yield')
oil = load_macro('oil_wti')
vix_data = load_macro('vix')
positioning = load_positioning()
dvol = load_dvol()

print(f"BTC: {btc.index.min()} to {btc.index.max()} ({len(btc)} bars)")
print(f"Positioning: {positioning.index.min()} to {positioning.index.max()} ({len(positioning)} days)")
print(f"DVOL: {dvol.index.min()} to {dvol.index.max()} ({len(dvol)} days)")
print()

# =============================================================================
# TRADE SIMULATION ENGINE
# =============================================================================

def simulate_short_trades(df, entry_signals, exit_fn, max_hold_hours=168, 
                          atr_stop_mult=None, atr_target_mult=None, atr_trail_mult=None,
                          label='Signal'):
    """
    Simulate short trades on 1h data.
    
    Parameters:
    - df: 1h OHLCV+funding dataframe
    - entry_signals: boolean Series aligned to df index (True = enter short)
    - exit_fn: function(df, entry_idx, entry_price) -> exit condition boolean Series (optional)
    - max_hold_hours: maximum holding period
    - atr_stop_mult: stop loss in ATR multiples (price goes UP by this much = stop out)
    - atr_target_mult: take profit in ATR multiples (price goes DOWN by this much = take profit)
    - atr_trail_mult: trailing stop in ATR multiples
    """
    # Compute 20-period ATR for stops
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift(1)).abs(),
        (df['low'] - df['close'].shift(1)).abs()
    ], axis=1).max(axis=1)
    atr20 = tr.rolling(20).mean()
    
    trades = []
    in_trade = False
    entry_price = 0
    entry_time = None
    entry_atr = 0
    lowest_since_entry = np.inf  # for trailing stop (short = track lowest)
    
    for i in range(20, len(df)):
        idx = df.index[i]
        
        if in_trade:
            current_price = df['close'].iloc[i]
            hours_held = (idx - entry_time).total_seconds() / 3600
            pnl_pct = (entry_price - current_price) / entry_price  # short PnL
            
            # Track lowest price for trailing stop
            lowest_since_entry = min(lowest_since_entry, current_price)
            
            # Cumulative funding cost/income
            funding_cost = df['funding_1h'].iloc[entry_idx_int:i+1].sum()
            # When short: positive funding = we RECEIVE, negative funding = we PAY
            # Actually: short pays negative funding, receives positive funding
            # funding_1h > 0 means longs pay shorts -> shorts receive
            net_pnl = pnl_pct + funding_cost
            
            exit_reason = None
            
            # Check exits
            # 1. ATR stop loss (price goes UP)
            if atr_stop_mult and current_price > entry_price + atr_stop_mult * entry_atr:
                exit_reason = 'stop'
            
            # 2. ATR target (price goes DOWN)
            if atr_target_mult and current_price < entry_price - atr_target_mult * entry_atr:
                exit_reason = 'target'
            
            # 3. Trailing stop
            if atr_trail_mult and current_price > lowest_since_entry + atr_trail_mult * entry_atr:
                exit_reason = 'trail'
            
            # 4. Max hold
            if hours_held >= max_hold_hours:
                exit_reason = 'max_hold'
            
            # 5. Custom exit condition
            if exit_fn is not None:
                try:
                    if exit_fn(df, i, entry_price):
                        exit_reason = 'signal_exit'
                except:
                    pass
            
            if exit_reason:
                trades.append({
                    'entry_time': entry_time,
                    'exit_time': idx,
                    'entry_price': entry_price,
                    'exit_price': current_price,
                    'hours_held': hours_held,
                    'pnl_pct': pnl_pct,
                    'funding_cost': funding_cost,
                    'net_pnl': net_pnl,
                    'exit_reason': exit_reason
                })
                in_trade = False
        
        elif entry_signals.iloc[i]:
            in_trade = True
            entry_price = df['close'].iloc[i]
            entry_time = idx
            entry_idx_int = i
            entry_atr = atr20.iloc[i]
            lowest_since_entry = entry_price
    
    return pd.DataFrame(trades) if trades else pd.DataFrame()


def compute_metrics(trades_df, label='Signal'):
    """Compute standard metrics from trades DataFrame."""
    if trades_df.empty or len(trades_df) < 5:
        return {
            'label': label, 'n_trades': len(trades_df) if not trades_df.empty else 0,
            'hit_rate': 0, 'avg_pnl': 0, 'avg_win': 0, 'avg_loss': 0,
            'profit_factor': 0, 'total_return': 0, 'sharpe': 0,
            'max_dd': 0, 'avg_hold_hours': 0, 'funding_impact': 0,
            'KILL': True, 'kill_reason': f'Too few trades ({len(trades_df)})'
        }
    
    n = len(trades_df)
    winners = trades_df[trades_df['net_pnl'] > 0]
    losers = trades_df[trades_df['net_pnl'] <= 0]
    
    hit_rate = len(winners) / n
    avg_pnl = trades_df['net_pnl'].mean()
    avg_win = winners['net_pnl'].mean() if len(winners) > 0 else 0
    avg_loss = losers['net_pnl'].mean() if len(losers) > 0 else 0
    
    gross_profit = winners['net_pnl'].sum() if len(winners) > 0 else 0
    gross_loss = abs(losers['net_pnl'].sum()) if len(losers) > 0 else 0.0001
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 99
    
    # Equity curve
    cum_pnl = trades_df['net_pnl'].cumsum()
    total_return = cum_pnl.iloc[-1]
    
    # Max drawdown on cumulative PnL
    running_max = cum_pnl.cummax()
    dd = cum_pnl - running_max
    max_dd = dd.min()
    
    # Sharpe-like: annualized
    avg_hold = trades_df['hours_held'].mean()
    trades_per_year = 8760 / avg_hold if avg_hold > 0 else 0
    if trades_df['net_pnl'].std() > 0:
        sharpe = (avg_pnl / trades_df['net_pnl'].std()) * np.sqrt(trades_per_year)
    else:
        sharpe = 0
    
    funding_impact = trades_df['funding_cost'].mean()
    
    # Kill logic
    kill = False
    kill_reason = ''
    if n < 30:
        kill, kill_reason = True, f'Too few trades ({n})'
    elif hit_rate < 0.40:
        kill, kill_reason = True, f'Hit rate too low ({hit_rate:.1%})'
    elif profit_factor < 1.05:
        kill, kill_reason = True, f'PF too low ({profit_factor:.2f})'
    
    return {
        'label': label, 'n_trades': n, 'hit_rate': hit_rate,
        'avg_pnl': avg_pnl, 'avg_win': avg_win, 'avg_loss': avg_loss,
        'profit_factor': profit_factor, 'total_return': total_return,
        'sharpe': sharpe, 'max_dd': max_dd,
        'avg_hold_hours': avg_hold, 'funding_impact': funding_impact,
        'KILL': kill, 'kill_reason': kill_reason
    }


def is_oos_sign_flip(is_metrics, oos_metrics):
    """Check if the sign of avg_pnl flips between IS and OOS."""
    if is_metrics['avg_pnl'] > 0 and oos_metrics['avg_pnl'] < 0:
        return True
    return False


# =============================================================================
# SIGNAL 1: MACRO RISK-OFF SHORT
# =============================================================================

print("=" * 70)
print("SIGNAL 1: MACRO RISK-OFF SHORT")
print("US10Y rising fast (20d change > 1 std) + DXY strengthening -> short BTC")
print("=" * 70)

# Prepare daily macro signals and map to hourly
us10y_daily = us10y[us10y.index >= '2020-01-01']['Close'].copy()
dxy_daily = dxy[dxy.index >= '2020-01-01']['Close'].copy()

# US10Y: 20-day change and its rolling std
us10y_chg20 = us10y_daily.diff(20)
us10y_chg_std = us10y_chg20.rolling(252).std()
us10y_z = us10y_chg20 / us10y_chg_std

# DXY: 20-day change > 0 (strengthening)
dxy_chg20 = dxy_daily.diff(20)
dxy_strengthening = dxy_chg20 > 0

# Combine: both conditions met
macro_short_daily = (us10y_z > 1.0) & dxy_strengthening
macro_short_daily = macro_short_daily.dropna()

print(f"US10Y z-score > 1: {(us10y_z > 1.0).sum()} days")
print(f"DXY strengthening: {dxy_strengthening.sum()} days")
print(f"Both conditions: {macro_short_daily.sum()} days")

# Map daily signal to hourly: signal fires on daily close, active for next 24h
macro_short_hourly = pd.Series(False, index=btc.index)
for date in macro_short_daily[macro_short_daily].index:
    # Signal active from next day's first bar
    next_day = date + pd.Timedelta(days=1)
    mask = (btc.index >= next_day) & (btc.index < next_day + pd.Timedelta(hours=1))
    macro_short_hourly.loc[mask] = True

print(f"Hourly entry signals: {macro_short_hourly.sum()}")

trades1 = simulate_short_trades(
    btc, macro_short_hourly, 
    exit_fn=None,
    max_hold_hours=168,
    atr_stop_mult=3.0,
    atr_target_mult=4.0,
    label='Macro Risk-Off'
)

# IS/OOS split
if not trades1.empty:
    mid = len(trades1) // 2
    trades1_is = trades1.iloc[:mid]
    trades1_oos = trades1.iloc[mid:]
    m1_is = compute_metrics(trades1_is, 'Macro Risk-Off IS')
    m1_oos = compute_metrics(trades1_oos, 'Macro Risk-Off OOS')
    m1_all = compute_metrics(trades1, 'Macro Risk-Off ALL')
    print(f"\nIS:  n={m1_is['n_trades']}, HR={m1_is['hit_rate']:.1%}, PF={m1_is['profit_factor']:.2f}, Sharpe={m1_is['sharpe']:.2f}, AvgPnL={m1_is['avg_pnl']:.4f}")
    print(f"OOS: n={m1_oos['n_trades']}, HR={m1_oos['hit_rate']:.1%}, PF={m1_oos['profit_factor']:.2f}, Sharpe={m1_oos['sharpe']:.2f}, AvgPnL={m1_oos['avg_pnl']:.4f}")
    print(f"ALL: n={m1_all['n_trades']}, HR={m1_all['hit_rate']:.1%}, PF={m1_all['profit_factor']:.2f}, Sharpe={m1_all['sharpe']:.2f}")
    if m1_all['KILL']:
        print(f"** KILL: {m1_all['kill_reason']}")
    if is_oos_sign_flip(m1_is, m1_oos):
        print("** KILL: IS->OOS sign flip")
else:
    m1_is = m1_oos = m1_all = compute_metrics(pd.DataFrame(), 'Macro Risk-Off')
    print("No trades generated!")

print()

# =============================================================================
# SIGNAL 2: POSITIONING CROWDING SHORT
# =============================================================================

print("=" * 70)
print("SIGNAL 2: POSITIONING CROWDING SHORT")
print("Top trader L/S ratio z-score > 2.0 -> short BTC")
print("=" * 70)

# Use sum_toptrader_ls_ratio (represents aggregate positioning)
ls_ratio = positioning['sum_toptrader_ls_ratio'].dropna()
ls_rolling_mean = ls_ratio.rolling(60).mean()
ls_rolling_std = ls_ratio.rolling(60).std()
ls_zscore = (ls_ratio - ls_rolling_mean) / ls_rolling_std

print(f"L/S ratio range: {ls_ratio.min():.3f} to {ls_ratio.max():.3f}")
print(f"Z-score > 2.0: {(ls_zscore > 2.0).sum()} days")
print(f"Z-score > 1.5: {(ls_zscore > 1.5).sum()} days")

# Map daily signal to hourly entry
pos_short_hourly = pd.Series(False, index=btc.index)
crowding_active = ls_zscore > 2.0
for date in crowding_active[crowding_active].index:
    next_day = date + pd.Timedelta(days=1)
    mask = (btc.index >= next_day) & (btc.index < next_day + pd.Timedelta(hours=1))
    pos_short_hourly.loc[mask] = True

# Exit: z-score drops below 1.0
def pos_exit_fn(df, i, entry_price):
    current_date = df.index[i].normalize()
    if current_date in ls_zscore.index:
        return ls_zscore.loc[current_date] < 1.0
    return False

print(f"Hourly entry signals: {pos_short_hourly.sum()}")

trades2 = simulate_short_trades(
    btc, pos_short_hourly,
    exit_fn=pos_exit_fn,
    max_hold_hours=336,  # 14 days max
    atr_stop_mult=2.0,
    label='Positioning Crowding'
)

if not trades2.empty:
    mid = len(trades2) // 2
    trades2_is = trades2.iloc[:mid]
    trades2_oos = trades2.iloc[mid:]
    m2_is = compute_metrics(trades2_is, 'Positioning Crowding IS')
    m2_oos = compute_metrics(trades2_oos, 'Positioning Crowding OOS')
    m2_all = compute_metrics(trades2, 'Positioning Crowding ALL')
    print(f"\nIS:  n={m2_is['n_trades']}, HR={m2_is['hit_rate']:.1%}, PF={m2_is['profit_factor']:.2f}, Sharpe={m2_is['sharpe']:.2f}, AvgPnL={m2_is['avg_pnl']:.4f}")
    print(f"OOS: n={m2_oos['n_trades']}, HR={m2_oos['hit_rate']:.1%}, PF={m2_oos['profit_factor']:.2f}, Sharpe={m2_oos['sharpe']:.2f}, AvgPnL={m2_oos['avg_pnl']:.4f}")
    print(f"ALL: n={m2_all['n_trades']}, HR={m2_all['hit_rate']:.1%}, PF={m2_all['profit_factor']:.2f}, Sharpe={m2_all['sharpe']:.2f}")
    if m2_all['KILL']:
        print(f"** KILL: {m2_all['kill_reason']}")
    if not trades2_is.empty and not trades2_oos.empty and is_oos_sign_flip(m2_is, m2_oos):
        print("** KILL: IS->OOS sign flip")
else:
    m2_is = m2_oos = m2_all = compute_metrics(pd.DataFrame(), 'Positioning Crowding')
    print("No trades generated!")

print()

# =============================================================================
# SIGNAL 3: VOLATILITY REGIME SHORT
# =============================================================================

print("=" * 70)
print("SIGNAL 3: VOLATILITY REGIME SHORT")
print("DVOL z-score > 2.0 + price below 20d EMA -> short")
print("=" * 70)

dvol_close = dvol['dvol_close'].dropna()
dvol_mean = dvol_close.rolling(60).mean()
dvol_std = dvol_close.rolling(60).std()
dvol_z = (dvol_close - dvol_mean) / dvol_std

print(f"DVOL range: {dvol_close.min():.1f} to {dvol_close.max():.1f}")
print(f"DVOL z-score > 2.0: {(dvol_z > 2.0).sum()} days")
print(f"DVOL z-score > 1.5: {(dvol_z > 1.5).sum()} days")

# BTC daily close for EMA
btc_daily = btc['close'].resample('D').last().dropna()
# Deduplicate daily index (in case of DST or other issues)
btc_daily = btc_daily[~btc_daily.index.duplicated(keep='last')]
btc_ema20d = btc_daily.ewm(span=20).mean()
btc_below_ema = btc_daily < btc_ema20d

# Combine conditions - use merge to handle index alignment
dvol_extreme = dvol_z > 2.0
dvol_extreme = dvol_extreme[~dvol_extreme.index.duplicated(keep='last')]
btc_below_ema_dedup = btc_below_ema[~btc_below_ema.index.duplicated(keep='last')]
combined = pd.DataFrame({'dvol': dvol_extreme, 'below_ema': btc_below_ema_dedup}).dropna()
vol_short_daily = combined['dvol'] & combined['below_ema']
print(f"Both conditions met: {vol_short_daily.sum()} days")

# Map to hourly
vol_short_hourly = pd.Series(False, index=btc.index)
for date in vol_short_daily[vol_short_daily].index:
    next_day = date + pd.Timedelta(days=1)
    mask = (btc.index >= next_day) & (btc.index < next_day + pd.Timedelta(hours=1))
    vol_short_hourly.loc[mask] = True

# Exit: DVOL normalizes (z < 0.5) OR trend flips (price > 20d EMA)
def vol_exit_fn(df, i, entry_price):
    current_date = df.index[i].normalize()
    if current_date in dvol_z.index and current_date in btc_below_ema.index:
        dvol_norm = dvol_z.loc[current_date] < 0.5
        trend_flip = not btc_below_ema.loc[current_date]
        return dvol_norm or trend_flip
    return False

print(f"Hourly entry signals: {vol_short_hourly.sum()}")

trades3 = simulate_short_trades(
    btc, vol_short_hourly,
    exit_fn=vol_exit_fn,
    max_hold_hours=168,
    atr_stop_mult=2.5,
    label='Vol Regime Short'
)

if not trades3.empty:
    mid = len(trades3) // 2
    trades3_is = trades3.iloc[:mid]
    trades3_oos = trades3.iloc[mid:]
    m3_is = compute_metrics(trades3_is, 'Vol Regime IS')
    m3_oos = compute_metrics(trades3_oos, 'Vol Regime OOS')
    m3_all = compute_metrics(trades3, 'Vol Regime ALL')
    print(f"\nIS:  n={m3_is['n_trades']}, HR={m3_is['hit_rate']:.1%}, PF={m3_is['profit_factor']:.2f}, Sharpe={m3_is['sharpe']:.2f}, AvgPnL={m3_is['avg_pnl']:.4f}")
    print(f"OOS: n={m3_oos['n_trades']}, HR={m3_oos['hit_rate']:.1%}, PF={m3_oos['profit_factor']:.2f}, Sharpe={m3_oos['sharpe']:.2f}, AvgPnL={m3_oos['avg_pnl']:.4f}")
    print(f"ALL: n={m3_all['n_trades']}, HR={m3_all['hit_rate']:.1%}, PF={m3_all['profit_factor']:.2f}, Sharpe={m3_all['sharpe']:.2f}")
    if m3_all['KILL']:
        print(f"** KILL: {m3_all['kill_reason']}")
    if not trades3_is.empty and not trades3_oos.empty and is_oos_sign_flip(m3_is, m3_oos):
        print("** KILL: IS->OOS sign flip")
else:
    m3_is = m3_oos = m3_all = compute_metrics(pd.DataFrame(), 'Vol Regime')
    print("No trades generated!")

print()

# =============================================================================
# SIGNAL 4: BREAKDOWN SHORT (PURE TECHNICAL)
# =============================================================================

print("=" * 70)
print("SIGNAL 4: BREAKDOWN SHORT (PURE TECHNICAL)")
print("Price breaks below 20-period low on 4h + volume > 1.5x avg -> short")
print("=" * 70)

# Resample to 4h
btc_4h = btc.resample('4h').agg({
    'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last',
    'volume': 'sum', 'funding_1h': 'sum'
}).dropna()

low20 = btc_4h['low'].rolling(20).min().shift(1)  # previous 20-period low (exclude current)
vol_avg = btc_4h['volume'].rolling(20).mean()

breakdown_signal_4h = (btc_4h['close'] < low20) & (btc_4h['volume'] > 1.5 * vol_avg)
print(f"4h breakdown signals: {breakdown_signal_4h.sum()}")

# Map 4h signal to 1h: entry at the first 1h bar after 4h signal
breakdown_hourly = pd.Series(False, index=btc.index)
for ts in breakdown_signal_4h[breakdown_signal_4h].index:
    next_bar = ts + pd.Timedelta(hours=4)
    mask = (btc.index >= next_bar) & (btc.index < next_bar + pd.Timedelta(hours=1))
    breakdown_hourly.loc[mask] = True

print(f"Hourly entry signals: {breakdown_hourly.sum()}")

trades4 = simulate_short_trades(
    btc, breakdown_hourly,
    exit_fn=None,
    max_hold_hours=168,
    atr_trail_mult=2.0,
    atr_target_mult=4.0,
    atr_stop_mult=2.0,
    label='Breakdown Short'
)

if not trades4.empty:
    mid = len(trades4) // 2
    trades4_is = trades4.iloc[:mid]
    trades4_oos = trades4.iloc[mid:]
    m4_is = compute_metrics(trades4_is, 'Breakdown IS')
    m4_oos = compute_metrics(trades4_oos, 'Breakdown OOS')
    m4_all = compute_metrics(trades4, 'Breakdown ALL')
    print(f"\nIS:  n={m4_is['n_trades']}, HR={m4_is['hit_rate']:.1%}, PF={m4_is['profit_factor']:.2f}, Sharpe={m4_is['sharpe']:.2f}, AvgPnL={m4_is['avg_pnl']:.4f}")
    print(f"OOS: n={m4_oos['n_trades']}, HR={m4_oos['hit_rate']:.1%}, PF={m4_oos['profit_factor']:.2f}, Sharpe={m4_oos['sharpe']:.2f}, AvgPnL={m4_oos['avg_pnl']:.4f}")
    print(f"ALL: n={m4_all['n_trades']}, HR={m4_all['hit_rate']:.1%}, PF={m4_all['profit_factor']:.2f}, Sharpe={m4_all['sharpe']:.2f}")
    if m4_all['KILL']:
        print(f"** KILL: {m4_all['kill_reason']}")
    if not trades4_is.empty and not trades4_oos.empty and is_oos_sign_flip(m4_is, m4_oos):
        print("** KILL: IS->OOS sign flip")
else:
    m4_is = m4_oos = m4_all = compute_metrics(pd.DataFrame(), 'Breakdown')
    print("No trades generated!")

print()

# =============================================================================
# SIGNAL 5: FUNDING RATE REVERSAL SHORT
# =============================================================================

print("=" * 70)
print("SIGNAL 5: FUNDING RATE REVERSAL SHORT")
print("8h funding rate > 0.1% -> short BTC")
print("=" * 70)

# funding_rate is 8h rate, funding_1h is hourly allocation
# When funding_rate > 0.001 (0.1%), extreme positive = crowded longs
funding_8h = btc['funding_rate']
extreme_funding = funding_8h > 0.001

# Only trigger at 8h boundaries (when funding actually resets)
# Funding resets at 00:00, 08:00, 16:00 UTC
funding_hours = btc.index.hour.isin([0, 8, 16])
funding_entry = extreme_funding & funding_hours

print(f"Funding > 0.1%: {extreme_funding.sum()} bars")
print(f"At 8h boundaries: {(extreme_funding & funding_hours).sum()} bars")

# Also test softer threshold
extreme_funding_soft = funding_8h > 0.0005  # 0.05%
print(f"Funding > 0.05%: {(extreme_funding_soft & funding_hours).sum()} bars")

# Exit: funding < 0.02% OR 72h max hold
def funding_exit_fn(df, i, entry_price):
    return df['funding_rate'].iloc[i] < 0.0002  # 0.02%

trades5 = simulate_short_trades(
    btc, funding_entry,
    exit_fn=funding_exit_fn,
    max_hold_hours=72,
    atr_stop_mult=2.0,
    label='Funding Reversal'
)

if not trades5.empty:
    mid = len(trades5) // 2
    trades5_is = trades5.iloc[:mid]
    trades5_oos = trades5.iloc[mid:]
    m5_is = compute_metrics(trades5_is, 'Funding Reversal IS')
    m5_oos = compute_metrics(trades5_oos, 'Funding Reversal OOS')
    m5_all = compute_metrics(trades5, 'Funding Reversal ALL')
    print(f"\nIS:  n={m5_is['n_trades']}, HR={m5_is['hit_rate']:.1%}, PF={m5_is['profit_factor']:.2f}, Sharpe={m5_is['sharpe']:.2f}, AvgPnL={m5_is['avg_pnl']:.4f}")
    print(f"OOS: n={m5_oos['n_trades']}, HR={m5_oos['hit_rate']:.1%}, PF={m5_oos['profit_factor']:.2f}, Sharpe={m5_oos['sharpe']:.2f}, AvgPnL={m5_oos['avg_pnl']:.4f}")
    print(f"ALL: n={m5_all['n_trades']}, HR={m5_all['hit_rate']:.1%}, PF={m5_all['profit_factor']:.2f}, Sharpe={m5_all['sharpe']:.2f}")
    if m5_all['KILL']:
        print(f"** KILL: {m5_all['kill_reason']}")
    if not trades5_is.empty and not trades5_oos.empty and is_oos_sign_flip(m5_is, m5_oos):
        print("** KILL: IS->OOS sign flip")
else:
    m5_is = m5_oos = m5_all = compute_metrics(pd.DataFrame(), 'Funding Reversal')
    print("No trades generated!")

# Also test softer threshold
print("\n--- Softer threshold (0.05%) ---")
funding_entry_soft = extreme_funding_soft & funding_hours
trades5b = simulate_short_trades(
    btc, funding_entry_soft,
    exit_fn=funding_exit_fn,
    max_hold_hours=72,
    atr_stop_mult=2.0,
    label='Funding Reversal Soft'
)

if not trades5b.empty:
    mid = len(trades5b) // 2
    m5b_is = compute_metrics(trades5b.iloc[:mid], 'Funding Soft IS')
    m5b_oos = compute_metrics(trades5b.iloc[mid:], 'Funding Soft OOS')
    m5b_all = compute_metrics(trades5b, 'Funding Soft ALL')
    print(f"IS:  n={m5b_is['n_trades']}, HR={m5b_is['hit_rate']:.1%}, PF={m5b_is['profit_factor']:.2f}, Sharpe={m5b_is['sharpe']:.2f}, AvgPnL={m5b_is['avg_pnl']:.4f}")
    print(f"OOS: n={m5b_oos['n_trades']}, HR={m5b_oos['hit_rate']:.1%}, PF={m5b_oos['profit_factor']:.2f}, Sharpe={m5b_oos['sharpe']:.2f}, AvgPnL={m5b_oos['avg_pnl']:.4f}")
    print(f"ALL: n={m5b_all['n_trades']}, HR={m5b_all['hit_rate']:.1%}, PF={m5b_all['profit_factor']:.2f}, Sharpe={m5b_all['sharpe']:.2f}")
else:
    m5b_is = m5b_oos = m5b_all = compute_metrics(pd.DataFrame(), 'Funding Soft')

print()

# =============================================================================
# SIGNAL 6: OIL SPIKE SHORT
# =============================================================================

print("=" * 70)
print("SIGNAL 6: OIL SPIKE SHORT")
print("Oil 20d momentum > 2 std AND BTC below 50d EMA -> short")
print("=" * 70)

oil_daily = oil[oil.index >= '2020-01-01']['Close'].dropna()
oil_mom20 = oil_daily.pct_change(20)
oil_mom_std = oil_mom20.rolling(252).std()
oil_z = oil_mom20 / oil_mom_std

btc_ema50d = btc_daily.ewm(span=50).mean()
btc_below_ema50 = btc_daily < btc_ema50d

# Combine - handle potential index duplicates
oil_z_dedup = oil_z[~oil_z.index.duplicated(keep='last')]
btc_below_ema50_dedup = btc_below_ema50[~btc_below_ema50.index.duplicated(keep='last')]
combined_oil = pd.DataFrame({'oil_z': oil_z_dedup, 'below_ema50': btc_below_ema50_dedup}).dropna()
oil_short_daily = (combined_oil['oil_z'] > 2.0) & combined_oil['below_ema50']

print(f"Oil z-score > 2.0: {(oil_z > 2.0).sum()} days")
print(f"BTC below 50d EMA: {btc_below_ema50.sum()} days")
print(f"Both conditions: {oil_short_daily.sum()} days")

# Map to hourly
oil_short_hourly = pd.Series(False, index=btc.index)
for date in oil_short_daily[oil_short_daily].index:
    next_day = date + pd.Timedelta(days=1)
    mask = (btc.index >= next_day) & (btc.index < next_day + pd.Timedelta(hours=1))
    oil_short_hourly.loc[mask] = True

print(f"Hourly entry signals: {oil_short_hourly.sum()}")

trades6 = simulate_short_trades(
    btc, oil_short_hourly,
    exit_fn=None,
    max_hold_hours=168,
    atr_stop_mult=3.0,
    atr_target_mult=4.0,
    label='Oil Spike Short'
)

if not trades6.empty:
    mid = len(trades6) // 2
    trades6_is = trades6.iloc[:mid]
    trades6_oos = trades6.iloc[mid:]
    m6_is = compute_metrics(trades6_is, 'Oil Spike IS')
    m6_oos = compute_metrics(trades6_oos, 'Oil Spike OOS')
    m6_all = compute_metrics(trades6, 'Oil Spike ALL')
    print(f"\nIS:  n={m6_is['n_trades']}, HR={m6_is['hit_rate']:.1%}, PF={m6_is['profit_factor']:.2f}, Sharpe={m6_is['sharpe']:.2f}, AvgPnL={m6_is['avg_pnl']:.4f}")
    print(f"OOS: n={m6_oos['n_trades']}, HR={m6_oos['hit_rate']:.1%}, PF={m6_oos['profit_factor']:.2f}, Sharpe={m6_oos['sharpe']:.2f}, AvgPnL={m6_oos['avg_pnl']:.4f}")
    print(f"ALL: n={m6_all['n_trades']}, HR={m6_all['hit_rate']:.1%}, PF={m6_all['profit_factor']:.2f}, Sharpe={m6_all['sharpe']:.2f}")
    if m6_all['KILL']:
        print(f"** KILL: {m6_all['kill_reason']}")
    if not trades6_is.empty and not trades6_oos.empty and is_oos_sign_flip(m6_is, m6_oos):
        print("** KILL: IS->OOS sign flip")
else:
    m6_is = m6_oos = m6_all = compute_metrics(pd.DataFrame(), 'Oil Spike')
    print("No trades generated!")

print()

# =============================================================================
# STORE ALL RESULTS FOR LATER USE
# =============================================================================

all_results = {
    '1_macro': {'is': m1_is, 'oos': m1_oos, 'all': m1_all, 'trades': trades1},
    '2_positioning': {'is': m2_is, 'oos': m2_oos, 'all': m2_all, 'trades': trades2},
    '3_vol_regime': {'is': m3_is, 'oos': m3_oos, 'all': m3_all, 'trades': trades3},
    '4_breakdown': {'is': m4_is, 'oos': m4_oos, 'all': m4_all, 'trades': trades4},
    '5_funding': {'is': m5_is, 'oos': m5_oos, 'all': m5_all, 'trades': trades5},
    '6_oil': {'is': m6_is, 'oos': m6_oos, 'all': m6_all, 'trades': trades6},
}

# =============================================================================
# SUMMARY TABLE
# =============================================================================

print("\n" + "=" * 100)
print("SUMMARY TABLE — ALL SHORT SIGNALS (BTC)")
print("=" * 100)
print(f"{'Signal':<28} {'N':>5} {'HR':>7} {'AvgPnL':>9} {'PF':>7} {'Sharpe':>8} {'MaxDD':>9} {'FundImp':>8} {'Verdict':>12}")
print("-" * 100)

for key, res in all_results.items():
    m = res['all']
    verdict = 'KILL' if m['KILL'] else 'LIVE'
    # Check IS/OOS sign flip
    if not m['KILL'] and res['is']['avg_pnl'] > 0 and res['oos']['avg_pnl'] < 0:
        verdict = 'KILL(flip)'
    
    print(f"  {m['label']:<26} {m['n_trades']:>5} {m['hit_rate']:>6.1%} {m['avg_pnl']:>8.4f} {m['profit_factor']:>6.2f} {m['sharpe']:>7.2f} {m['max_dd']:>8.4f} {m['funding_impact']:>7.5f} {verdict:>12}")
    # IS
    mi = res['is']
    print(f"    {'IS':<24} {mi['n_trades']:>5} {mi['hit_rate']:>6.1%} {mi['avg_pnl']:>8.4f} {mi['profit_factor']:>6.2f} {mi['sharpe']:>7.2f}")
    # OOS
    mo = res['oos']
    print(f"    {'OOS':<24} {mo['n_trades']:>5} {mo['hit_rate']:>6.1%} {mo['avg_pnl']:>8.4f} {mo['profit_factor']:>6.2f} {mo['sharpe']:>7.2f}")

# =============================================================================
# IDENTIFY SURVIVORS
# =============================================================================

print("\n" + "=" * 70)
print("SURVIVORS (not killed)")
print("=" * 70)

survivors = []
for key, res in all_results.items():
    m = res['all']
    if not m['KILL']:
        # Check sign flip
        if res['is']['avg_pnl'] > 0 and res['oos']['avg_pnl'] < 0:
            print(f"  {m['label']}: KILLED by IS->OOS sign flip")
        else:
            survivors.append((key, res))
            print(f"  {m['label']}: ALIVE — Sharpe={m['sharpe']:.2f}, PF={m['profit_factor']:.2f}")

if not survivors:
    print("  No survivors with strict criteria. Checking relaxed thresholds...")
    # Relax: any signal with positive OOS avg_pnl and >20 trades
    for key, res in all_results.items():
        m = res['all']
        mo = res['oos']
        if m['n_trades'] >= 20 and mo['avg_pnl'] > 0:
            survivors.append((key, res))
            print(f"  {m['label']}: MARGINAL — OOS avg={mo['avg_pnl']:.4f}, n={m['n_trades']}")

# =============================================================================
# ETH VALIDATION ON TOP SIGNALS
# =============================================================================

print("\n" + "=" * 70)
print("ETH VALIDATION ON TOP SIGNALS")
print("=" * 70)

# Test all signals on ETH regardless (even killed ones - for completeness)
# Signal 4 (Breakdown) and Signal 5 (Funding) use only price/funding data, easy to port

# Signal 4 on ETH
eth_4h = eth.resample('4h').agg({
    'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last',
    'volume': 'sum', 'funding_1h': 'sum'
}).dropna()

low20_eth = eth_4h['low'].rolling(20).min().shift(1)
vol_avg_eth = eth_4h['volume'].rolling(20).mean()
breakdown_eth_4h = (eth_4h['close'] < low20_eth) & (eth_4h['volume'] > 1.5 * vol_avg_eth)

breakdown_eth_hourly = pd.Series(False, index=eth.index)
for ts in breakdown_eth_4h[breakdown_eth_4h].index:
    next_bar = ts + pd.Timedelta(hours=4)
    mask = (eth.index >= next_bar) & (eth.index < next_bar + pd.Timedelta(hours=1))
    breakdown_eth_hourly.loc[mask] = True

trades4_eth = simulate_short_trades(
    eth, breakdown_eth_hourly, exit_fn=None,
    max_hold_hours=168, atr_trail_mult=2.0, atr_target_mult=4.0, atr_stop_mult=2.0,
    label='Breakdown ETH'
)
m4_eth = compute_metrics(trades4_eth, 'Breakdown ETH')
print(f"Signal 4 (Breakdown) on ETH: n={m4_eth['n_trades']}, HR={m4_eth['hit_rate']:.1%}, PF={m4_eth['profit_factor']:.2f}, Sharpe={m4_eth['sharpe']:.2f}, AvgPnL={m4_eth['avg_pnl']:.4f}")

# Signal 5 on ETH
funding_8h_eth = eth['funding_rate']
extreme_funding_eth = funding_8h_eth > 0.001
funding_hours_eth = eth.index.hour.isin([0, 8, 16])
funding_entry_eth = extreme_funding_eth & funding_hours_eth

def funding_exit_fn_eth(df, i, entry_price):
    return df['funding_rate'].iloc[i] < 0.0002

trades5_eth = simulate_short_trades(
    eth, funding_entry_eth, exit_fn=funding_exit_fn_eth,
    max_hold_hours=72, atr_stop_mult=2.0,
    label='Funding Reversal ETH'
)
m5_eth = compute_metrics(trades5_eth, 'Funding Reversal ETH')
print(f"Signal 5 (Funding) on ETH: n={m5_eth['n_trades']}, HR={m5_eth['hit_rate']:.1%}, PF={m5_eth['profit_factor']:.2f}, Sharpe={m5_eth['sharpe']:.2f}, AvgPnL={m5_eth['avg_pnl']:.4f}")

# Signal 5 soft on ETH
extreme_funding_soft_eth = funding_8h_eth > 0.0005
funding_entry_soft_eth = extreme_funding_soft_eth & funding_hours_eth
trades5b_eth = simulate_short_trades(
    eth, funding_entry_soft_eth, exit_fn=funding_exit_fn_eth,
    max_hold_hours=72, atr_stop_mult=2.0,
    label='Funding Soft ETH'
)
m5b_eth = compute_metrics(trades5b_eth, 'Funding Soft ETH')
print(f"Signal 5 (Funding Soft) on ETH: n={m5b_eth['n_trades']}, HR={m5b_eth['hit_rate']:.1%}, PF={m5b_eth['profit_factor']:.2f}, Sharpe={m5b_eth['sharpe']:.2f}, AvgPnL={m5b_eth['avg_pnl']:.4f}")

# Signal 1 (Macro) on ETH — same entry signal, just applied to ETH
macro_short_hourly_eth = pd.Series(False, index=eth.index)
for date in macro_short_daily[macro_short_daily].index:
    next_day = date + pd.Timedelta(days=1)
    mask = (eth.index >= next_day) & (eth.index < next_day + pd.Timedelta(hours=1))
    macro_short_hourly_eth.loc[mask] = True

trades1_eth = simulate_short_trades(
    eth, macro_short_hourly_eth, exit_fn=None,
    max_hold_hours=168, atr_stop_mult=3.0, atr_target_mult=4.0,
    label='Macro Risk-Off ETH'
)
m1_eth = compute_metrics(trades1_eth, 'Macro Risk-Off ETH')
print(f"Signal 1 (Macro) on ETH: n={m1_eth['n_trades']}, HR={m1_eth['hit_rate']:.1%}, PF={m1_eth['profit_factor']:.2f}, Sharpe={m1_eth['sharpe']:.2f}, AvgPnL={m1_eth['avg_pnl']:.4f}")

# Signal 3 (Vol Regime) on ETH
vol_short_hourly_eth = pd.Series(False, index=eth.index)
for date in vol_short_daily[vol_short_daily].index:
    next_day = date + pd.Timedelta(days=1)
    mask = (eth.index >= next_day) & (eth.index < next_day + pd.Timedelta(hours=1))
    vol_short_hourly_eth.loc[mask] = True

def vol_exit_fn_eth(df, i, entry_price):
    current_date = df.index[i].normalize()
    if current_date in dvol_z.index and current_date in btc_below_ema.index:
        dvol_norm = dvol_z.loc[current_date] < 0.5
        trend_flip = not btc_below_ema.loc[current_date]
        return dvol_norm or trend_flip
    return False

trades3_eth = simulate_short_trades(
    eth, vol_short_hourly_eth, exit_fn=vol_exit_fn_eth,
    max_hold_hours=168, atr_stop_mult=2.5,
    label='Vol Regime ETH'
)
m3_eth = compute_metrics(trades3_eth, 'Vol Regime ETH')
print(f"Signal 3 (Vol Regime) on ETH: n={m3_eth['n_trades']}, HR={m3_eth['hit_rate']:.1%}, PF={m3_eth['profit_factor']:.2f}, Sharpe={m3_eth['sharpe']:.2f}, AvgPnL={m3_eth['avg_pnl']:.4f}")

# Signal 6 (Oil) on ETH
oil_short_hourly_eth = pd.Series(False, index=eth.index)
for date in oil_short_daily[oil_short_daily].index:
    next_day = date + pd.Timedelta(days=1)
    mask = (eth.index >= next_day) & (eth.index < next_day + pd.Timedelta(hours=1))
    oil_short_hourly_eth.loc[mask] = True

trades6_eth = simulate_short_trades(
    eth, oil_short_hourly_eth, exit_fn=None,
    max_hold_hours=168, atr_stop_mult=3.0, atr_target_mult=4.0,
    label='Oil Spike ETH'
)
m6_eth = compute_metrics(trades6_eth, 'Oil Spike ETH')
print(f"Signal 6 (Oil Spike) on ETH: n={m6_eth['n_trades']}, HR={m6_eth['hit_rate']:.1%}, PF={m6_eth['profit_factor']:.2f}, Sharpe={m6_eth['sharpe']:.2f}, AvgPnL={m6_eth['avg_pnl']:.4f}")


# =============================================================================
# CORRELATION WITH LONG-ONLY STRATEGY
# =============================================================================

print("\n" + "=" * 70)
print("CORRELATION ANALYSIS WITH LONG-ONLY")
print("=" * 70)

# Simple long-only proxy: buy-and-hold daily returns
btc_daily_ret = btc['close'].resample('D').last().dropna()
btc_daily_ret = btc_daily_ret[~btc_daily_ret.index.duplicated(keep='last')].pct_change()

# For each signal, compute daily PnL series and correlate
for key, res in all_results.items():
    tdf = res['trades']
    if tdf.empty or len(tdf) < 10:
        continue
    
    # Create daily PnL series from trades
    daily_pnl = pd.Series(0.0, index=btc_daily_ret.index)
    for _, trade in tdf.iterrows():
        entry_d = trade['entry_time'].normalize()
        exit_d = trade['exit_time'].normalize()
        days = (exit_d - entry_d).days
        if days > 0:
            daily_alloc = trade['net_pnl'] / days
            date_range = pd.date_range(entry_d, exit_d, freq='D')
            for d in date_range:
                if d in daily_pnl.index:
                    daily_pnl.loc[d] += daily_alloc
    
    # Correlation
    common = btc_daily_ret.index.intersection(daily_pnl[daily_pnl != 0].index)
    if len(common) > 20:
        corr = btc_daily_ret.loc[common].corr(daily_pnl.loc[common])
        print(f"  {res['all']['label']}: corr with long-only = {corr:.3f} (n={len(common)} active days)")
    else:
        print(f"  {res['all']['label']}: too few active days for correlation")


# =============================================================================
# PARAMETER SENSITIVITY FOR TOP SIGNALS
# =============================================================================

print("\n" + "=" * 70)
print("PARAMETER SENSITIVITY — TOP SIGNALS")
print("=" * 70)

# Test breakdown with different parameters
print("\n--- Signal 4 (Breakdown) sensitivity ---")
for lookback in [10, 20, 30]:
    for vol_mult in [1.0, 1.5, 2.0]:
        low_n = btc_4h['low'].rolling(lookback).min().shift(1)
        vol_avg_n = btc_4h['volume'].rolling(lookback).mean()
        sig = (btc_4h['close'] < low_n) & (btc_4h['volume'] > vol_mult * vol_avg_n)
        
        sig_hourly = pd.Series(False, index=btc.index)
        for ts in sig[sig].index:
            next_bar = ts + pd.Timedelta(hours=4)
            mask = (btc.index >= next_bar) & (btc.index < next_bar + pd.Timedelta(hours=1))
            sig_hourly.loc[mask] = True
        
        t = simulate_short_trades(btc, sig_hourly, exit_fn=None,
                                  max_hold_hours=168, atr_trail_mult=2.0,
                                  atr_target_mult=4.0, atr_stop_mult=2.0)
        m = compute_metrics(t, f'BD L={lookback} V={vol_mult}')
        if m['n_trades'] > 0:
            print(f"  LB={lookback:>2} VolMult={vol_mult:.1f}: n={m['n_trades']:>4}, HR={m['hit_rate']:.1%}, PF={m['profit_factor']:.2f}, Sharpe={m['sharpe']:.2f}, AvgPnL={m['avg_pnl']:.4f}")

# Test funding with different thresholds
print("\n--- Signal 5 (Funding) sensitivity ---")
for thresh in [0.0003, 0.0005, 0.0008, 0.001, 0.0015]:
    sig = (funding_8h > thresh) & funding_hours
    t = simulate_short_trades(btc, sig, exit_fn=funding_exit_fn,
                              max_hold_hours=72, atr_stop_mult=2.0)
    m = compute_metrics(t, f'Fund {thresh:.4f}')
    if m['n_trades'] > 0:
        # IS/OOS
        mid = len(t) // 2
        mi = compute_metrics(t.iloc[:mid], 'IS')
        mo = compute_metrics(t.iloc[mid:], 'OOS')
        flip = '*FLIP*' if mi['avg_pnl'] > 0 and mo['avg_pnl'] < 0 else ''
        print(f"  Thresh={thresh:.4f}: n={m['n_trades']:>4}, HR={m['hit_rate']:.1%}, PF={m['profit_factor']:.2f}, Sharpe={m['sharpe']:.2f}, IS_PnL={mi['avg_pnl']:.4f}, OOS_PnL={mo['avg_pnl']:.4f} {flip}")


# =============================================================================
# COMBO SIGNALS
# =============================================================================

print("\n" + "=" * 70)
print("COMBO SIGNALS")
print("=" * 70)

# Combo 1: Breakdown + Funding confirmation
# Breakdown signal when funding is also elevated (>0.03%)
print("\n--- Combo: Breakdown + High Funding ---")
funding_elevated = btc['funding_rate'] > 0.0003
# For each breakdown entry, check if funding is elevated
combo1_hourly = breakdown_hourly & funding_elevated
print(f"Combo entries: {combo1_hourly.sum()}")

trades_c1 = simulate_short_trades(
    btc, combo1_hourly, exit_fn=None,
    max_hold_hours=168, atr_trail_mult=2.0, atr_target_mult=4.0, atr_stop_mult=2.0,
    label='Breakdown+Funding'
)
mc1 = compute_metrics(trades_c1, 'Breakdown+Funding')
if mc1['n_trades'] > 0:
    mid = len(trades_c1) // 2
    mc1_is = compute_metrics(trades_c1.iloc[:mid], 'IS')
    mc1_oos = compute_metrics(trades_c1.iloc[mid:], 'OOS')
    print(f"  ALL: n={mc1['n_trades']}, HR={mc1['hit_rate']:.1%}, PF={mc1['profit_factor']:.2f}, Sharpe={mc1['sharpe']:.2f}")
    print(f"  IS:  n={mc1_is['n_trades']}, HR={mc1_is['hit_rate']:.1%}, PF={mc1_is['profit_factor']:.2f}, AvgPnL={mc1_is['avg_pnl']:.4f}")
    print(f"  OOS: n={mc1_oos['n_trades']}, HR={mc1_oos['hit_rate']:.1%}, PF={mc1_oos['profit_factor']:.2f}, AvgPnL={mc1_oos['avg_pnl']:.4f}")

# Combo 2: Funding + Vol Regime  
# Extreme funding in high-vol regime
print("\n--- Combo: Funding + Below 20d EMA ---")
btc_ema20h = btc['close'].ewm(span=480).mean()  # 20 days * 24 hours
below_ema = btc['close'] < btc_ema20h
combo2_hourly = funding_entry & below_ema
print(f"Combo entries: {combo2_hourly.sum()}")

trades_c2 = simulate_short_trades(
    btc, combo2_hourly, exit_fn=funding_exit_fn,
    max_hold_hours=72, atr_stop_mult=2.0,
    label='Funding+Downtrend'
)
mc2 = compute_metrics(trades_c2, 'Funding+Downtrend')
if mc2['n_trades'] > 0:
    mid = len(trades_c2) // 2
    mc2_is = compute_metrics(trades_c2.iloc[:mid], 'IS')
    mc2_oos = compute_metrics(trades_c2.iloc[mid:], 'OOS')
    print(f"  ALL: n={mc2['n_trades']}, HR={mc2['hit_rate']:.1%}, PF={mc2['profit_factor']:.2f}, Sharpe={mc2['sharpe']:.2f}")
    print(f"  IS:  n={mc2_is['n_trades']}, HR={mc2_is['hit_rate']:.1%}, PF={mc2_is['profit_factor']:.2f}, AvgPnL={mc2_is['avg_pnl']:.4f}")
    print(f"  OOS: n={mc2_oos['n_trades']}, HR={mc2_oos['hit_rate']:.1%}, PF={mc2_oos['profit_factor']:.2f}, AvgPnL={mc2_oos['avg_pnl']:.4f}")

# Combo 3: Macro + Breakdown
print("\n--- Combo: Macro Risk-Off + Breakdown ---")
# Breakdown when macro conditions are also unfavorable
# Use expanded macro: us10y rising OR dxy strengthening (not both required)
us10y_z_hourly = pd.Series(np.nan, index=btc.index)
for date, val in us10y_z.items():
    mask = (btc.index >= date) & (btc.index < date + pd.Timedelta(days=1))
    us10y_z_hourly.loc[mask] = val

macro_unfavorable = us10y_z_hourly > 0.5  # looser threshold
combo3_hourly = breakdown_hourly & macro_unfavorable
print(f"Combo entries: {combo3_hourly.sum()}")

trades_c3 = simulate_short_trades(
    btc, combo3_hourly, exit_fn=None,
    max_hold_hours=168, atr_trail_mult=2.0, atr_target_mult=4.0, atr_stop_mult=2.0,
    label='Macro+Breakdown'
)
mc3 = compute_metrics(trades_c3, 'Macro+Breakdown')
if mc3['n_trades'] > 0:
    mid = len(trades_c3) // 2
    mc3_is = compute_metrics(trades_c3.iloc[:mid], 'IS')
    mc3_oos = compute_metrics(trades_c3.iloc[mid:], 'OOS')
    print(f"  ALL: n={mc3['n_trades']}, HR={mc3['hit_rate']:.1%}, PF={mc3['profit_factor']:.2f}, Sharpe={mc3['sharpe']:.2f}")
    print(f"  IS:  n={mc3_is['n_trades']}, HR={mc3_is['hit_rate']:.1%}, PF={mc3_is['profit_factor']:.2f}, AvgPnL={mc3_is['avg_pnl']:.4f}")
    print(f"  OOS: n={mc3_oos['n_trades']}, HR={mc3_oos['hit_rate']:.1%}, PF={mc3_oos['profit_factor']:.2f}, AvgPnL={mc3_oos['avg_pnl']:.4f}")


# =============================================================================
# TRADE TIMING ANALYSIS
# =============================================================================

print("\n" + "=" * 70)
print("TRADE TIMING ANALYSIS")
print("=" * 70)

for key, res in all_results.items():
    tdf = res['trades']
    if tdf.empty or len(tdf) < 10:
        continue
    m = res['all']
    entry_years = tdf['entry_time'].dt.year
    print(f"\n  {m['label']}:")
    for yr in sorted(entry_years.unique()):
        yr_trades = tdf[entry_years == yr]
        yr_m = compute_metrics(yr_trades, str(yr))
        print(f"    {yr}: n={yr_m['n_trades']:>3}, HR={yr_m['hit_rate']:.1%}, AvgPnL={yr_m['avg_pnl']:.4f}, TotalRet={yr_m['total_return']:.4f}")


print("\n\n=== ANALYSIS COMPLETE ===")
