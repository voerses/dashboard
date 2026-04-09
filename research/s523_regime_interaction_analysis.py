#!/usr/bin/env python3
"""
s523 Regime Indicator Interaction Analysis
==========================================
Analyzes how multiple regime indicators interact month-by-month across 2022-2026,
and finds the optimal COMBINED short gate decision rule for the s523 strategy.

Indicators:
1. 1mo_red: BTC monthly return < 0
2. 3mo_red: BTC 3-month avg return < 0
3. Alt breadth: % of alts above 50d SMA
4. Alt vs BTC 30d: median alt 30d return minus BTC 30d return
5. BTC vs SMA200d: price above/below 200-day SMA
6. BTC/ETH ratio 30d change
7. Relative body size: 6mo avg |body| / 12mo median |body|
"""

import json
import os
import sys
import glob
import warnings
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

DATA_DIR = '/workspace/crypto_backtest/data/perp/binance/1h_ohlcv'
TRADE_LOG = '/workspace/crypto_backtest/results/v4/s523h_regime_adaptive_51mo_50k_trades.json'
OUTPUT_JSON = '/workspace/crypto_backtest/research/s523_regime_interaction_results.json'

# Backtest starts 2022-01-05, analysis months 2022-01 to 2026-03
BACKTEST_START = pd.Timestamp('2022-01-05')
MONTHS = pd.date_range('2022-01-01', '2026-03-01', freq='MS')

# Top alts with pre-2022 data (excluding BTC, ETH)
ALT_TOKENS = [
    'BCH', 'XRP', 'LTC', 'TRX', 'ETC', 'LINK', 'XLM', 'ADA', 'XMR', 'DASH',
    'ZEC', 'ATOM', 'BNB', 'ONT', 'NEO', 'QTUM', 'ALGO', 'DOGE', 'KAVA', 'SNX',
    'DOT', 'CRV', 'SOL', 'UNI', 'AVAX', 'ENJ', 'NEAR', 'FIL', 'AAVE', 'AXS',
    'ZEN', 'SKL', 'CHZ', 'SAND', 'ANKR', 'RVN', 'HBAR', 'DENT', 'OGN',
    '1000SHIB', 'DYDX', 'GALA', 'ENS'
]


def load_1h_data(token):
    """Load 1h OHLCV data for a token."""
    path = os.path.join(DATA_DIR, f'{token}_perp_1h.csv')
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, parse_dates=['datetime'])
    df['datetime'] = df['datetime'].dt.tz_localize(None)
    df = df.set_index('datetime').sort_index()
    return df


def compute_daily_close(df_1h):
    """Resample 1h to daily close."""
    return df_1h['close'].resample('D').last().dropna()


def compute_monthly_close(daily):
    """Get month-end close from daily data."""
    return daily.resample('ME').last().dropna()


def compute_sma(series, window):
    """Simple moving average."""
    return series.rolling(window).mean()


# ========== STEP 1: Build Monthly Indicator Table ==========

def build_indicator_table():
    """Build the master monthly indicator table."""
    print("=" * 80)
    print("STEP 1: Building Monthly Indicator Table")
    print("=" * 80)

    # Load BTC data
    btc_1h = load_1h_data('BTC')
    btc_daily = compute_daily_close(btc_1h)
    btc_monthly = compute_monthly_close(btc_daily)

    # Load ETH data
    eth_1h = load_1h_data('ETH')
    eth_daily = compute_daily_close(eth_1h)

    # BTC monthly returns
    btc_monthly_ret = btc_monthly.pct_change()

    # Load all alt daily closes
    print(f"Loading {len(ALT_TOKENS)} alts...")
    alt_daily = {}
    for token in ALT_TOKENS:
        df = load_1h_data(token)
        if df is not None:
            alt_daily[token] = compute_daily_close(df)
    print(f"  Loaded {len(alt_daily)} alts successfully")

    # Build indicators per month
    rows = []
    for month_start in MONTHS:
        month_end = (month_start + pd.offsets.MonthEnd(0))
        ym = month_start.strftime('%Y-%m')

        # --- BTC monthly return ---
        if month_start in btc_monthly_ret.index:
            btc_ret = btc_monthly_ret.loc[month_start]
        else:
            # Use closest available
            prev_month = month_start - pd.offsets.MonthBegin(1)
            mask = (btc_daily.index >= prev_month) & (btc_daily.index <= month_end)
            sub = btc_daily[mask]
            if len(sub) > 1:
                btc_ret = (sub.iloc[-1] / sub.iloc[0]) - 1
            else:
                btc_ret = np.nan
        # Actually compute from daily: first and last day of month
        mask = (btc_daily.index.year == month_start.year) & (btc_daily.index.month == month_start.month)
        sub = btc_daily[mask]
        if len(sub) > 1:
            btc_ret = (sub.iloc[-1] / sub.iloc[0]) - 1
        else:
            btc_ret = np.nan

        # --- 1mo_red ---
        one_mo_red = btc_ret < 0 if not np.isnan(btc_ret) else False

        # --- 3mo_red: average of last 3 months returns ---
        idx = list(MONTHS).index(month_start)
        if idx >= 2:
            # Compute returns for this and prev 2 months
            rets_3m = []
            for i in range(3):
                ms = MONTHS[idx - i]
                m = (btc_daily.index.year == ms.year) & (btc_daily.index.month == ms.month)
                s = btc_daily[m]
                if len(s) > 1:
                    rets_3m.append((s.iloc[-1] / s.iloc[0]) - 1)
            three_mo_red = np.mean(rets_3m) < 0 if rets_3m else False
        else:
            three_mo_red = one_mo_red

        # --- Alt breadth: % of alts above 50d SMA at month end ---
        above_50d = 0
        total_alts = 0
        for token, daily in alt_daily.items():
            # Get data up to month end
            sub = daily[daily.index <= month_end]
            if len(sub) < 50:
                continue
            sma50 = sub.iloc[-50:].mean()
            last_close = sub.iloc[-1]
            total_alts += 1
            if last_close > sma50:
                above_50d += 1
        alt_breadth = (above_50d / total_alts * 100) if total_alts > 0 else np.nan

        # --- Alt vs BTC 30d return ---
        # Median alt 30d return minus BTC 30d return, measured at month end
        btc_sub = btc_daily[btc_daily.index <= month_end]
        if len(btc_sub) >= 30:
            btc_30d_ret = (btc_sub.iloc[-1] / btc_sub.iloc[-30]) - 1
        else:
            btc_30d_ret = np.nan

        alt_30d_rets = []
        for token, daily in alt_daily.items():
            sub = daily[daily.index <= month_end]
            if len(sub) >= 30:
                ret = (sub.iloc[-1] / sub.iloc[-30]) - 1
                alt_30d_rets.append(ret)
        if alt_30d_rets and not np.isnan(btc_30d_ret):
            alt_vs_btc_30d = np.median(alt_30d_rets) - btc_30d_ret
        else:
            alt_vs_btc_30d = np.nan

        # --- BTC vs SMA200d ---
        btc_sub = btc_daily[btc_daily.index <= month_end]
        if len(btc_sub) >= 200:
            sma200 = btc_sub.iloc[-200:].mean()
            btc_above_sma200 = btc_sub.iloc[-1] > sma200
            btc_pct_from_sma200 = (btc_sub.iloc[-1] / sma200 - 1) * 100
        else:
            btc_above_sma200 = np.nan
            btc_pct_from_sma200 = np.nan

        # --- BTC/ETH ratio 30d change ---
        eth_sub = eth_daily[eth_daily.index <= month_end]
        btc_sub2 = btc_daily[btc_daily.index <= month_end]
        if len(eth_sub) >= 30 and len(btc_sub2) >= 30:
            # Align dates
            ratio_now = btc_sub2.iloc[-1] / eth_sub.iloc[-1]
            # Find ~30 days ago
            target_date = month_end - timedelta(days=30)
            btc_30d_ago = btc_daily[btc_daily.index <= target_date]
            eth_30d_ago = eth_daily[eth_daily.index <= target_date]
            if len(btc_30d_ago) > 0 and len(eth_30d_ago) > 0:
                ratio_30d_ago = btc_30d_ago.iloc[-1] / eth_30d_ago.iloc[-1]
                btc_eth_ratio_chg = (ratio_now / ratio_30d_ago - 1) * 100
            else:
                btc_eth_ratio_chg = np.nan
        else:
            btc_eth_ratio_chg = np.nan

        # --- Relative body size ---
        # 6mo avg |body| / 12mo median |body| using monthly candles
        btc_monthly_sub = btc_1h['close'].resample('ME').last()
        btc_monthly_open = btc_1h['open'].resample('ME').first()
        bodies = (btc_monthly_sub - btc_monthly_open).abs()
        bodies = bodies[bodies.index <= month_end]
        if len(bodies) >= 12:
            avg_6m = bodies.iloc[-6:].mean()
            median_12m = bodies.iloc[-12:].median()
            rel_body = avg_6m / median_12m if median_12m > 0 else np.nan
        else:
            rel_body = np.nan

        # --- Regime label ---
        regime = classify_regime(btc_ret, alt_breadth, alt_vs_btc_30d,
                                  btc_above_sma200, ym)

        row = {
            'month': ym,
            'btc_ret_pct': round(btc_ret * 100, 2) if not np.isnan(btc_ret) else np.nan,
            '1mo_red': bool(one_mo_red),
            '3mo_red': bool(three_mo_red),
            'alt_breadth': round(alt_breadth, 1) if not np.isnan(alt_breadth) else np.nan,
            'alt_vs_btc_30d_pct': round(alt_vs_btc_30d * 100, 2) if not np.isnan(alt_vs_btc_30d) else np.nan,
            'btc_above_sma200': bool(btc_above_sma200) if not isinstance(btc_above_sma200, float) else None,
            'btc_pct_from_sma200': round(btc_pct_from_sma200, 1) if not np.isnan(btc_pct_from_sma200) else np.nan,
            'btc_eth_ratio_chg_pct': round(btc_eth_ratio_chg, 2) if not np.isnan(btc_eth_ratio_chg) else np.nan,
            'rel_body_size': round(rel_body, 3) if not np.isnan(rel_body) else np.nan,
            'regime': regime,
        }
        rows.append(row)

    df = pd.DataFrame(rows)
    return df


def classify_regime(btc_ret, alt_breadth, alt_vs_btc, btc_above_sma200, ym):
    """Classify the regime for a given month."""
    if pd.isna(btc_ret) or pd.isna(alt_breadth):
        return 'MIXED'

    btc_ret_pct = btc_ret * 100

    # ALT_SEASON: alts significantly outperforming, breadth high
    if alt_breadth > 60 and not pd.isna(alt_vs_btc) and alt_vs_btc * 100 > 3:
        return 'ALT_SEASON'

    # BULL: BTC up, alts broadly up
    if btc_ret_pct > 3 and alt_breadth > 45 and btc_above_sma200:
        return 'BULL'

    # ALT_BLEED: BTC flat/up but alts down
    if alt_breadth < 35 and not pd.isna(alt_vs_btc) and alt_vs_btc * 100 < -3:
        return 'ALT_BLEED'

    # BEAR: everything down
    if btc_ret_pct < -5 and alt_breadth < 40:
        return 'BEAR'

    # Additional BEAR: below SMA200 and negative
    if btc_ret_pct < 0 and not btc_above_sma200:
        return 'BEAR'

    # BTC strong but alts weak
    if btc_ret_pct > 0 and alt_breadth < 35:
        return 'ALT_BLEED'

    return 'MIXED'


# ========== STEP 2: Load s523 Monthly PnL ==========

def load_trade_pnl_by_month():
    """Load trades, assign to months, compute monthly PnL by direction."""
    print("\n" + "=" * 80)
    print("STEP 2: Loading s523 Trade PnL by Month")
    print("=" * 80)

    with open(TRADE_LOG) as f:
        trades = json.load(f)

    print(f"Total trades: {len(trades)}")

    # Convert entry_bar to approximate datetime
    # Backtest starts 2022-01-05, each bar = 1 hour
    bar_zero_time = pd.Timestamp('2022-01-05')

    monthly_pnl = defaultdict(lambda: {
        'long_pnl': 0.0, 'short_pnl': 0.0, 'total_pnl': 0.0,
        'n_longs': 0, 'n_shorts': 0
    })

    trade_records = []
    for t in trades:
        entry_bar = t['entry_bar']
        exit_bar = t['exit_bar']
        pnl = float(t['pnl'])
        direction = t['direction']  # 1 = long, -1 = short

        # Use exit bar for monthly attribution (when PnL is realized)
        exit_time = bar_zero_time + pd.Timedelta(hours=exit_bar)
        ym = exit_time.strftime('%Y-%m')

        # Also store entry time for filtering
        entry_time = bar_zero_time + pd.Timedelta(hours=entry_bar)
        entry_ym = entry_time.strftime('%Y-%m')

        m = monthly_pnl[ym]
        if direction == 1:
            m['long_pnl'] += pnl
            m['n_longs'] += 1
        else:
            m['short_pnl'] += pnl
            m['n_shorts'] += 1
        m['total_pnl'] += pnl

        trade_records.append({
            'token': t['token'],
            'direction': direction,
            'pnl': pnl,
            'entry_bar': entry_bar,
            'exit_bar': exit_bar,
            'entry_time': entry_time,
            'exit_time': exit_time,
            'entry_month': entry_ym,
            'exit_month': ym,
            'hold_hours': t['hold_hours'],
        })

    trades_df = pd.DataFrame(trade_records)

    # Print monthly summary
    print(f"\n{'Month':<10} {'Longs':>6} {'Long PnL':>10} {'Shorts':>7} {'Short PnL':>10} {'Total':>10} {'Winner':>8}")
    print("-" * 70)
    for ym in sorted(monthly_pnl.keys()):
        m = monthly_pnl[ym]
        winner = 'LONG' if m['long_pnl'] > m['short_pnl'] else 'SHORT'
        if m['long_pnl'] > 0 and m['short_pnl'] > 0:
            winner = 'BOTH+'
        elif m['long_pnl'] < 0 and m['short_pnl'] < 0:
            winner = 'BOTH-'
        print(f"{ym:<10} {m['n_longs']:>6} {m['long_pnl']:>10.0f} {m['n_shorts']:>7} {m['short_pnl']:>10.0f} {m['total_pnl']:>10.0f} {winner:>8}")

    return monthly_pnl, trades_df


# ========== STEP 3: Per-Indicator Accuracy ==========

def indicator_accuracy(indicators_df, monthly_pnl):
    """For each indicator, compute how well it predicts short PnL direction."""
    print("\n" + "=" * 80)
    print("STEP 3: Per-Indicator Recommendation Accuracy")
    print("=" * 80)

    results = {}

    # Merge indicators with PnL
    for _, row in indicators_df.iterrows():
        ym = row['month']
        if ym not in monthly_pnl:
            continue
        m = monthly_pnl[ym]
        indicators_df.loc[indicators_df['month'] == ym, 'short_pnl'] = m['short_pnl']
        indicators_df.loc[indicators_df['month'] == ym, 'long_pnl'] = m['long_pnl']
        indicators_df.loc[indicators_df['month'] == ym, 'n_shorts'] = m['n_shorts']

    merged = indicators_df.copy()
    merged['short_pnl'] = merged['short_pnl'].fillna(0)
    merged['long_pnl'] = merged['long_pnl'].fillna(0)

    # For each indicator, define when it says "shorts OK" vs "shorts blocked"
    indicator_rules = {
        '1mo_red': lambda r: r['1mo_red'],
        '3mo_red': lambda r: r['3mo_red'],
        'alt_breadth_lt40': lambda r: r['alt_breadth'] < 40 if not pd.isna(r['alt_breadth']) else False,
        'alt_breadth_lt30': lambda r: r['alt_breadth'] < 30 if not pd.isna(r['alt_breadth']) else False,
        'alt_vs_btc_lt_neg5': lambda r: r['alt_vs_btc_30d_pct'] < -5 if not pd.isna(r['alt_vs_btc_30d_pct']) else False,
        'alt_vs_btc_lt_neg3': lambda r: r['alt_vs_btc_30d_pct'] < -3 if not pd.isna(r['alt_vs_btc_30d_pct']) else False,
        'btc_below_sma200': lambda r: not r['btc_above_sma200'] if r['btc_above_sma200'] is not None else False,
        'btc_eth_ratio_rising': lambda r: r['btc_eth_ratio_chg_pct'] > 2 if not pd.isna(r['btc_eth_ratio_chg_pct']) else False,
    }

    print(f"\n{'Indicator':<25} {'Shorts OK':<12} {'OK PnL':>10} {'Avg/mo':>8} "
          f"{'Blocked':>8} {'Blk PnL':>10} {'Avg/mo':>8} {'Accuracy':>8}")
    print("-" * 95)

    for name, rule in indicator_rules.items():
        ok_months = 0
        ok_pnl = 0
        blocked_months = 0
        blocked_pnl = 0

        for _, row in merged.iterrows():
            if rule(row):
                ok_months += 1
                ok_pnl += row['short_pnl']
            else:
                blocked_months += 1
                blocked_pnl += row['short_pnl']

        ok_avg = ok_pnl / ok_months if ok_months > 0 else 0
        blk_avg = blocked_pnl / blocked_months if blocked_months > 0 else 0

        # Accuracy: shorts OK should have positive PnL, blocked should have negative
        # "Correct" = OK months with positive short PnL + blocked months with negative short PnL
        correct = 0
        total = 0
        for _, row in merged.iterrows():
            if row['short_pnl'] == 0 and row.get('n_shorts', 0) == 0:
                continue
            total += 1
            if rule(row) and row['short_pnl'] > 0:
                correct += 1
            elif not rule(row) and row['short_pnl'] <= 0:
                correct += 1

        accuracy = correct / total * 100 if total > 0 else 0

        print(f"{name:<25} {ok_months:>8} mo  {ok_pnl:>10.0f} {ok_avg:>8.0f} "
              f"{blocked_months:>8} {blocked_pnl:>10.0f} {blk_avg:>8.0f} {accuracy:>7.1f}%")

        results[name] = {
            'ok_months': ok_months, 'ok_pnl': round(ok_pnl),
            'blocked_months': blocked_months, 'blocked_pnl': round(blocked_pnl),
            'accuracy': round(accuracy, 1)
        }

    return results


# ========== STEP 4: Combined Gate Sweep ==========

def combined_gate_sweep(indicators_df, trades_df):
    """Test combinations of short gates using post-hoc trade filtering."""
    print("\n" + "=" * 80)
    print("STEP 4: Combined Gate Sweep (Post-Hoc Trade Filtering)")
    print("=" * 80)

    # Build month lookup for indicators
    ind_lookup = {}
    for _, row in indicators_df.iterrows():
        ind_lookup[row['month']] = row

    # Define gate combinations
    # Each gate returns True if shorts are ALLOWED in that month
    gates = {
        'A: no_gate (all shorts)': lambda r: True,
        'B: 1mo_red': lambda r: r['1mo_red'],
        'C: 3mo_red': lambda r: r['3mo_red'],
        'D: breadth_lt40': lambda r: r['alt_breadth'] < 40 if not pd.isna(r['alt_breadth']) else False,
        'E: breadth_lt30': lambda r: r['alt_breadth'] < 30 if not pd.isna(r['alt_breadth']) else False,
        'F: alt_vs_btc_lt_neg5': lambda r: r['alt_vs_btc_30d_pct'] < -5 if not pd.isna(r['alt_vs_btc_30d_pct']) else False,
        'G: 1mo_red OR breadth_lt40': lambda r: r['1mo_red'] or (r['alt_breadth'] < 40 if not pd.isna(r['alt_breadth']) else False),
        'H: 1mo_red OR alt_vs_btc_lt_neg5': lambda r: r['1mo_red'] or (r['alt_vs_btc_30d_pct'] < -5 if not pd.isna(r['alt_vs_btc_30d_pct']) else False),
        'I: breadth_lt40 OR btc_below_sma200': lambda r: (r['alt_breadth'] < 40 if not pd.isna(r['alt_breadth']) else False) or (not r['btc_above_sma200'] if r['btc_above_sma200'] is not None else False),
        'J: (1mo_red OR breadth_lt40) AND NOT breadth_gt70': lambda r: (
            (r['1mo_red'] or (r['alt_breadth'] < 40 if not pd.isna(r['alt_breadth']) else False))
            and not (r['alt_breadth'] > 70 if not pd.isna(r['alt_breadth']) else False)
        ),
        'K: SMART(breadth_lt40 any, 1mo_red if 40-70, block if gt70)': lambda r: (
            (r['alt_breadth'] < 40 if not pd.isna(r['alt_breadth']) else False)
            or (r['1mo_red'] and 40 <= (r['alt_breadth'] if not pd.isna(r['alt_breadth']) else 50) <= 70)
        ),
        'L: alt_vs_btc_lt_neg3 OR btc_below_sma200': lambda r: (
            (r['alt_vs_btc_30d_pct'] < -3 if not pd.isna(r['alt_vs_btc_30d_pct']) else False)
            or (not r['btc_above_sma200'] if r['btc_above_sma200'] is not None else False)
        ),
        'M: 1mo_red OR alt_vs_btc_lt_neg3': lambda r: r['1mo_red'] or (r['alt_vs_btc_30d_pct'] < -3 if not pd.isna(r['alt_vs_btc_30d_pct']) else False),
        'N: breadth_lt40 OR (1mo_red AND alt_vs_btc_lt0)': lambda r: (
            (r['alt_breadth'] < 40 if not pd.isna(r['alt_breadth']) else False)
            or (r['1mo_red'] and (r['alt_vs_btc_30d_pct'] < 0 if not pd.isna(r['alt_vs_btc_30d_pct']) else False))
        ),
    }

    # For each gate, compute PnL per year
    years = [2022, 2023, 2024, 2025, 2026]
    results = {}

    # Pre-compute: which month each trade's entry falls in, and whether shorts allowed
    # Use ENTRY month to decide if short is gated (you decide at entry time)
    trade_months = trades_df['entry_month'].values
    trade_directions = trades_df['direction'].values
    trade_pnls = trades_df['pnl'].values
    trade_entry_times = trades_df['entry_time']

    print(f"\n{'Gate':<55} ", end='')
    for y in years:
        print(f"  {y:>8}", end='')
    print(f"  {'TOTAL':>8}  {'Sharpe':>6}")
    print("-" * 115)

    for gate_name, gate_fn in gates.items():
        # Pre-compute gate decisions per month
        month_gate = {}
        for ym, row in ind_lookup.items():
            month_gate[ym] = gate_fn(row)

        yearly_pnl = defaultdict(float)
        monthly_pnls_all = defaultdict(float)

        for i in range(len(trades_df)):
            pnl = trade_pnls[i]
            direction = trade_directions[i]
            entry_ym = trade_months[i]
            exit_time = trades_df.iloc[i]['exit_time']
            exit_ym = exit_time.strftime('%Y-%m')
            year = exit_time.year

            if direction == -1:  # short trade
                # Check if shorts were allowed when trade was entered
                if not month_gate.get(entry_ym, False):
                    continue  # Gate blocks this short
            # Longs always pass through

            yearly_pnl[year] += pnl
            monthly_pnls_all[exit_ym] += pnl

        total = sum(yearly_pnl.values())

        # Compute monthly Sharpe-like metric
        monthly_vals = [monthly_pnls_all.get(m.strftime('%Y-%m'), 0) for m in MONTHS]
        monthly_arr = np.array(monthly_vals)
        if monthly_arr.std() > 0:
            sharpe = monthly_arr.mean() / monthly_arr.std() * np.sqrt(12)
        else:
            sharpe = 0

        print(f"{gate_name:<55} ", end='')
        for y in years:
            print(f"  {yearly_pnl.get(y, 0):>8.0f}", end='')
        print(f"  {total:>8.0f}  {sharpe:>6.2f}")

        results[gate_name] = {
            'yearly_pnl': {str(y): round(yearly_pnl.get(y, 0)) for y in years},
            'total_pnl': round(total),
            'monthly_sharpe': round(sharpe, 2),
            'monthly_pnls': {m.strftime('%Y-%m'): round(monthly_pnls_all.get(m.strftime('%Y-%m'), 0))
                             for m in MONTHS}
        }

    # Also compute long-only baseline
    long_only_yearly = defaultdict(float)
    for i in range(len(trades_df)):
        if trade_directions[i] == 1:
            exit_time = trades_df.iloc[i]['exit_time']
            long_only_yearly[exit_time.year] += trade_pnls[i]
    print(f"\n{'(Reference: LONG-ONLY)':<55} ", end='')
    for y in years:
        print(f"  {long_only_yearly.get(y, 0):>8.0f}", end='')
    print(f"  {sum(long_only_yearly.values()):>8.0f}")

    return results


# ========== STEP 5: Regime Transitions ==========

def analyze_transitions(indicators_df, monthly_pnl):
    """Analyze regime transition patterns, especially 'top + bleed'."""
    print("\n" + "=" * 80)
    print("STEP 5: Regime Transition Analysis")
    print("=" * 80)

    df = indicators_df.copy()
    df['short_pnl'] = df['month'].map(lambda ym: monthly_pnl.get(ym, {}).get('short_pnl', 0))

    # Look for BTC top + alt bleed pattern
    print("\n--- 'BTC Top + Alt Bleed' Detection ---")
    print("Looking for: BTC was above SMA200 + positive returns -> then alts collapse")
    print(f"\n{'Month':<10} {'BTC Ret%':>9} {'>SMA200':>8} {'Breadth':>8} {'AltVsBTC':>9} {'Regime':<12} {'Pattern':>10}")
    print("-" * 75)

    transitions = []
    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]

        # Detect: previous month was bullish, this month alts bleeding
        pattern = ''
        if (prev.get('btc_above_sma200') and
            prev.get('alt_breadth', 50) > 45 and
            not pd.isna(row.get('alt_breadth')) and row['alt_breadth'] < 35):
            pattern = 'TOP+BLEED'

        if (prev.get('alt_breadth', 0) > 50 and
            not pd.isna(row.get('alt_breadth')) and
            row['alt_breadth'] < prev['alt_breadth'] - 15):
            if not pattern:
                pattern = 'BREADTH_DROP'

        btc_ret_str = f"{row['btc_ret_pct']:>8.1f}" if not pd.isna(row['btc_ret_pct']) else '     N/A'
        sma200_str = 'YES' if row['btc_above_sma200'] else 'NO' if row['btc_above_sma200'] is not None else 'N/A'
        breadth_str = f"{row['alt_breadth']:>7.1f}" if not pd.isna(row['alt_breadth']) else '    N/A'
        avb_str = f"{row['alt_vs_btc_30d_pct']:>8.1f}" if not pd.isna(row['alt_vs_btc_30d_pct']) else '     N/A'

        if pattern or row['regime'] in ('ALT_BLEED', 'BEAR') or row.get('alt_breadth', 50) < 30:
            print(f"{row['month']:<10} {btc_ret_str} {sma200_str:>8} {breadth_str} {avb_str} "
                  f"{row['regime']:<12} {pattern:>10}")

        if pattern:
            transitions.append({
                'month': row['month'],
                'pattern': pattern,
                'prev_breadth': prev.get('alt_breadth'),
                'curr_breadth': row.get('alt_breadth'),
                'btc_ret': row['btc_ret_pct'],
                'regime': row['regime'],
            })

    # Multi-month transition tracking
    print("\n--- Breadth Trajectory (rolling 3-month window) ---")
    print(f"{'Month':<10} {'Breadth':>8} {'3m Chg':>8} {'Trend':>10}")
    print("-" * 40)
    for i in range(3, len(df)):
        row = df.iloc[i]
        prev3 = df.iloc[i-3]
        if pd.isna(row.get('alt_breadth')) or pd.isna(prev3.get('alt_breadth')):
            continue
        chg = row['alt_breadth'] - prev3['alt_breadth']
        trend = 'COLLAPSE' if chg < -25 else 'FALLING' if chg < -10 else 'RISING' if chg > 10 else 'FLAT'
        if abs(chg) > 10:
            print(f"{row['month']:<10} {row['alt_breadth']:>7.1f} {chg:>+7.1f} {trend:>10}")

    return transitions


# ========== STEP 6: Recommendation ==========

def recommend_best_rule(gate_results, indicators_df, monthly_pnl):
    """Find and report the best combined rule."""
    print("\n" + "=" * 80)
    print("STEP 6: OPTIMAL COMBINED RULE RECOMMENDATION")
    print("=" * 80)

    # Rank by total PnL, then by consistency (positive in most years)
    ranked = []
    for name, res in gate_results.items():
        yearly = res['yearly_pnl']
        total = res['total_pnl']
        sharpe = res['monthly_sharpe']
        positive_years = sum(1 for v in yearly.values() if float(v) > 0)
        # Score: weight total PnL + consistency + Sharpe
        score = total + positive_years * 5000 + sharpe * 10000
        ranked.append((name, total, sharpe, positive_years, score, res))

    ranked.sort(key=lambda x: x[4], reverse=True)

    print("\nRanked by composite score (PnL + consistency + Sharpe):\n")
    print(f"{'Rank':>4} {'Gate':<55} {'Total PnL':>10} {'Sharpe':>7} {'Pos Yrs':>8} {'Score':>10}")
    print("-" * 100)
    for i, (name, total, sharpe, pos_yrs, score, _) in enumerate(ranked):
        marker = ' <<<' if i == 0 else ''
        print(f"{i+1:>4} {name:<55} {total:>10.0f} {sharpe:>7.2f} {pos_yrs:>8} {score:>10.0f}{marker}")

    # Best rule details
    best_name, best_total, best_sharpe, best_pos, best_score, best_res = ranked[0]

    print(f"\n{'='*60}")
    print(f"BEST RULE: {best_name}")
    print(f"{'='*60}")
    print(f"Total PnL: ${best_total:,.0f}")
    print(f"Monthly Sharpe: {best_sharpe:.2f}")
    print(f"Positive years: {best_pos}/5")
    print(f"\nPer-year breakdown:")
    for y, v in sorted(best_res['yearly_pnl'].items()):
        print(f"  {y}: ${float(v):>+10,.0f}")

    # Show which months shorts were allowed vs blocked
    print(f"\nMonth-by-month short gate decisions:")
    # Re-derive the gate function for the best rule
    ind_lookup = {r['month']: r for _, r in indicators_df.iterrows()}

    # Count months shorts allowed vs blocked for each regime
    regime_gate = defaultdict(lambda: {'allowed': 0, 'blocked': 0, 'allowed_pnl': 0, 'blocked_pnl': 0})
    for ym, row in ind_lookup.items():
        regime = row['regime']
        short_pnl = monthly_pnl.get(ym, {}).get('short_pnl', 0)
        # Check if shorts were in the best monthly PnL (positive = allowed)
        best_monthly = best_res['monthly_pnls'].get(ym, 0)
        # This is total PnL (long+short), need to compare with long-only to see if shorts contributed
        # Actually we need to check the gate directly - but we have total PnL which includes longs
        # Skip for now - the gate sweep results are definitive

    return ranked[0]


# ========== MAIN ==========

def main():
    # Step 1: Build indicators
    indicators_df = build_indicator_table()

    # Print the full table
    print("\n--- Full Monthly Indicator Table ---")
    pd.set_option('display.max_rows', 60)
    pd.set_option('display.width', 200)
    pd.set_option('display.max_columns', 15)
    print(indicators_df.to_string(index=False))

    # Step 2: Load trade PnL
    monthly_pnl, trades_df = load_trade_pnl_by_month()

    # Step 3: Indicator accuracy
    indicator_results = indicator_accuracy(indicators_df, monthly_pnl)

    # Step 4: Combined gate sweep
    gate_results = combined_gate_sweep(indicators_df, trades_df)

    # Step 5: Transition analysis
    transitions = analyze_transitions(indicators_df, monthly_pnl)

    # Step 6: Recommendation
    best = recommend_best_rule(gate_results, indicators_df, monthly_pnl)

    # Save results
    output = {
        'monthly_indicators': indicators_df.to_dict(orient='records'),
        'indicator_accuracy': indicator_results,
        'gate_sweep': {k: {kk: vv for kk, vv in v.items() if kk != 'monthly_pnls'}
                       for k, v in gate_results.items()},
        'gate_sweep_monthly': {k: v.get('monthly_pnls', {}) for k, v in gate_results.items()},
        'transitions': transitions,
        'best_rule': {
            'name': best[0],
            'total_pnl': best[1],
            'monthly_sharpe': best[2],
            'positive_years': best[3],
        }
    }

    with open(OUTPUT_JSON, 'w') as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to {OUTPUT_JSON}")


if __name__ == '__main__':
    main()
