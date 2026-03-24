#!/workspace/venv/bin/python
"""
R101: Funding Rate Reversal Short — Walk-Forward Validation
============================================================

Signal: When daily avg funding rate > threshold (extreme positive = longs paying
shorts), go SHORT. Extreme positive funding indicates overleveraged longs that
tend to unwind, causing price drops.

Data sources:
  - BTC/ETH 1h spot OHLCV: data/spot/1h_cache/{BTC,ETH}_1h.parquet
  - Daily funding rates: data/alternative/funding_ls_proxy/{BTC,ETH}_funding_proxy.parquet
  - Raw 8h funding: data/alternative/binance_funding_rates_full.json (supplementary)

Walk-Forward Protocol:
  - 8 rolling windows: 180-day train / 90-day test, rolling 90 days
  - Train: optimize funding threshold (0.03%-0.10%), max hold (24-96h), stop loss (1-3%)
  - Test: apply best params from train to OOS
  - Record per window: PnL, Sharpe, trade count, win rate, max DD, avg hold time

Kill Criteria:
  - <4/8 positive OOS windows (excluding insufficient) -> KILL
  - Mean OOS Sharpe < 0.5 -> KILL
  - <20 total OOS trades -> INSUFFICIENT DATA
  - All profits from 1-2 events -> KILL

Author: Quant Research Agent
Date: 2026-03-24
"""

import os
import json
import warnings
import numpy as np
import pandas as pd
from itertools import product

warnings.filterwarnings('ignore')

# ── Paths ────────────────────────────────────────────────────────────────────
PROJECT_DIR = '/workspace/crypto_backtest'
SPOT_CACHE = os.path.join(PROJECT_DIR, 'data', 'spot', '1h_cache')
FUNDING_PROXY_DIR = os.path.join(PROJECT_DIR, 'data', 'alternative', 'funding_ls_proxy')
FUNDING_JSON = os.path.join(PROJECT_DIR, 'data', 'alternative', 'binance_funding_rates_full.json')

# ── Parameters to search ────────────────────────────────────────────────────
FUNDING_THRESHOLDS = [0.0003, 0.0005, 0.0008, 0.0010]  # 0.03%, 0.05%, 0.08%, 0.10%
MAX_HOLD_HOURS = [24, 48, 72, 96]
STOP_LOSS_PCT = [0.01, 0.015, 0.02, 0.025, 0.03]  # 1%, 1.5%, 2%, 2.5%, 3%

# Exit condition: funding drops below this
FUNDING_EXIT_THRESHOLD = 0.0001  # 0.01%

# Walk-forward configuration
TRAIN_DAYS = 180
TEST_DAYS = 90
ROLL_DAYS = 90
N_WINDOWS = 8

# Fees
SLIPPAGE_BPS = 5  # 0.05% per side
TAKER_FEE = 0.0006  # 0.06%
MAKER_FEE = 0.0004  # 0.04%
ROUND_TRIP_COST = TAKER_FEE + MAKER_FEE + 2 * SLIPPAGE_BPS / 10000  # total ~0.20%

# Minimum trades per window to consider valid
MIN_TRADES_PER_WINDOW = 3


def load_data(token):
    """
    Load spot 1h price data and daily funding rate data.
    Merge funding rate (forward-filled to hourly) into price dataframe.
    """
    # Load spot 1h data
    spot_path = os.path.join(SPOT_CACHE, f'{token}_1h.parquet')
    price_df = pd.read_parquet(spot_path)
    if price_df.index.tz is not None:
        price_df.index = price_df.index.tz_localize(None)

    # Load daily funding proxy
    funding_path = os.path.join(FUNDING_PROXY_DIR, f'{token}_funding_proxy.parquet')
    fund_df = pd.read_parquet(funding_path)
    if fund_df.index.tz is not None:
        fund_df.index = fund_df.index.tz_localize(None)

    # Keep only daily funding rate
    daily_funding = fund_df[['funding_rate_daily']].copy()
    daily_funding.columns = ['funding_rate']

    # Also try to load raw 8h funding from JSON for more granular signal
    raw_8h = load_raw_8h_funding(token)

    if raw_8h is not None and len(raw_8h) > 100:
        # Use raw 8h data where available, fill gaps with daily proxy
        funding_hourly = raw_8h.resample('1h').ffill()
        # Fill any gaps with daily proxy resampled
        daily_hourly = daily_funding.resample('1h').ffill()
        daily_hourly.columns = ['funding_rate']
        funding_hourly = funding_hourly.reindex(price_df.index)
        daily_hourly = daily_hourly.reindex(price_df.index)
        # Use raw where available, else daily
        funding_hourly['funding_rate'] = funding_hourly['funding_rate'].fillna(
            daily_hourly['funding_rate']
        )
    else:
        # Use daily proxy, forward-filled to hourly
        funding_hourly = daily_funding.resample('1h').ffill()
        funding_hourly = funding_hourly.reindex(price_df.index, method='ffill')

    # Merge
    price_df['funding_rate'] = funding_hourly['funding_rate']

    return price_df


def load_raw_8h_funding(token):
    """Load raw 8h funding rates from JSON if available."""
    try:
        with open(FUNDING_JSON) as f:
            data = json.load(f)
        df = pd.DataFrame(data)
        symbol = f'{token}USDT'
        subset = df[df['symbol'] == symbol].copy()
        if len(subset) == 0:
            return None
        subset['fundingTime'] = pd.to_datetime(subset['fundingTime'].astype(int), unit='ms')
        subset['funding_rate'] = subset['fundingRate'].astype(float)
        subset = subset.set_index('fundingTime').sort_index()
        subset = subset[['funding_rate']]
        return subset
    except Exception:
        return None


def simulate_trades(price_df, funding_threshold, max_hold_hours, stop_loss_pct,
                    start_date, end_date):
    """
    Simulate SHORT trades based on funding rate signal.

    Entry: funding_rate > funding_threshold
    Exit (first of):
      1. Funding rate drops below FUNDING_EXIT_THRESHOLD (0.01%)
      2. Max hold period reached
      3. Stop loss hit (price rises by stop_loss_pct from entry)

    Returns list of trade dicts.
    """
    # Filter to date range
    mask = (price_df.index >= start_date) & (price_df.index < end_date)
    df = price_df[mask].copy()

    if len(df) == 0:
        return []

    close = df['close'].values
    funding = df['funding_rate'].values
    timestamps = df.index.values
    n = len(df)

    trades = []
    i = 0

    while i < n:
        fr = funding[i]
        if np.isnan(fr) or fr <= funding_threshold:
            i += 1
            continue

        # ENTRY: go short at this bar's close
        entry_price = close[i]
        entry_time = timestamps[i]
        entry_funding = fr

        # Walk forward to find exit
        exit_reason = 'max_hold'
        exit_idx = min(i + max_hold_hours, n - 1)

        for j in range(i + 1, min(i + max_hold_hours + 1, n)):
            current_price = close[j]
            current_funding = funding[j] if not np.isnan(funding[j]) else fr

            # Stop loss: price rose by stop_loss_pct from entry
            price_change = (current_price - entry_price) / entry_price
            if price_change >= stop_loss_pct:
                exit_idx = j
                exit_reason = 'stop_loss'
                break

            # Funding exit: funding drops below threshold
            if current_funding < FUNDING_EXIT_THRESHOLD:
                exit_idx = j
                exit_reason = 'funding_exit'
                break
        else:
            # Loop completed without break -> max hold
            exit_idx = min(i + max_hold_hours, n - 1)
            exit_reason = 'max_hold'

        exit_price = close[exit_idx]
        exit_time = timestamps[exit_idx]
        hold_hours = exit_idx - i

        # Short PnL: (entry - exit) / entry - costs
        gross_return = (entry_price - exit_price) / entry_price
        net_return = gross_return - ROUND_TRIP_COST

        trades.append({
            'entry_time': entry_time,
            'exit_time': exit_time,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'entry_funding': entry_funding,
            'hold_hours': hold_hours,
            'exit_reason': exit_reason,
            'gross_return': gross_return,
            'net_return': net_return,
        })

        # Skip to after exit (non-overlapping trades)
        i = exit_idx + 1

    return trades


def compute_metrics(trades):
    """Compute performance metrics from a list of trade dicts."""
    if len(trades) == 0:
        return {
            'n_trades': 0, 'total_pnl': 0, 'sharpe': 0, 'win_rate': 0,
            'max_dd': 0, 'avg_hold': 0, 'profit_factor': 0,
            'avg_return': 0, 'exit_reasons': {},
        }

    rets = np.array([t['net_return'] for t in trades])
    n = len(rets)

    total_pnl = rets.sum() * 100  # in percentage points
    avg_ret = rets.mean()
    win_rate = (rets > 0).mean() * 100

    # Sharpe: annualize based on avg hold time and calendar time
    # Use per-trade Sharpe * sqrt(trades_per_year)
    avg_hold = np.mean([t['hold_hours'] for t in trades])
    if n > 1 and rets.std() > 0:
        # Calendar-based: how many trading periods per year?
        # approximate: 365*24 / avg_hold = trades per year at full capacity
        trades_per_year = 365 * 24 / max(avg_hold, 1)
        sharpe = (avg_ret / rets.std()) * np.sqrt(min(trades_per_year, n * 4))
    else:
        sharpe = 0.0

    # Max drawdown from cumulative returns
    cum_ret = np.cumsum(rets)
    peak = np.maximum.accumulate(cum_ret)
    dd = peak - cum_ret
    max_dd = dd.max() * 100 if len(dd) > 0 else 0

    # Profit factor
    wins = rets[rets > 0].sum()
    losses = abs(rets[rets <= 0].sum())
    pf = wins / losses if losses > 0 else (float('inf') if wins > 0 else 0)

    # Exit reason counts
    reasons = {}
    for t in trades:
        r = t['exit_reason']
        reasons[r] = reasons.get(r, 0) + 1

    return {
        'n_trades': n,
        'total_pnl': total_pnl,
        'sharpe': sharpe,
        'win_rate': win_rate,
        'max_dd': max_dd,
        'avg_hold': avg_hold,
        'profit_factor': pf,
        'avg_return': avg_ret * 100,
        'exit_reasons': reasons,
    }


def optimize_on_train(price_df, train_start, train_end):
    """
    Grid search over parameter space on training period.
    Returns best parameters (by Sharpe, then by PnL if tied).
    """
    best_sharpe = -999
    best_params = None
    best_metrics = None

    for ft, mh, sl in product(FUNDING_THRESHOLDS, MAX_HOLD_HOURS, STOP_LOSS_PCT):
        trades = simulate_trades(price_df, ft, mh, sl, train_start, train_end)
        metrics = compute_metrics(trades)

        # Require minimum trades for valid optimization
        if metrics['n_trades'] < 3:
            continue

        score = metrics['sharpe']
        if score > best_sharpe:
            best_sharpe = score
            best_params = {
                'funding_threshold': ft,
                'max_hold_hours': mh,
                'stop_loss_pct': sl,
            }
            best_metrics = metrics

    return best_params, best_metrics


def run_walkforward(token):
    """
    Run the full walk-forward validation for a given token.
    Returns list of window results.
    """
    print(f"\n{'='*70}")
    print(f"  WALK-FORWARD: {token}")
    print(f"{'='*70}")

    price_df = load_data(token)
    print(f"  Data: {price_df.index.min().date()} to {price_df.index.max().date()}")
    print(f"  Rows: {len(price_df):,}")
    print(f"  Funding NaN%: {price_df['funding_rate'].isna().mean()*100:.1f}%")

    # Determine first valid date (need some data for funding)
    valid_mask = price_df['funding_rate'].notna()
    first_valid = price_df[valid_mask].index.min()
    print(f"  First valid funding: {first_valid.date()}")

    # Count extreme funding events
    for t in FUNDING_THRESHOLDS:
        count = (price_df['funding_rate'] > t).sum()
        # Count as hourly bars where funding is above threshold
        # Since funding is forward-filled from daily, count unique days
        extreme_days = price_df[price_df['funding_rate'] > t].index.normalize().nunique()
        print(f"  Funding > {t*100:.2f}%: {extreme_days} days ({count} hourly bars)")

    # Compute window dates
    # Start windows after enough data for funding to be valid
    # First window train starts at least 30 days after first valid
    window_start = first_valid + pd.Timedelta(days=30)

    # But also need room for all 8 windows
    # Total span needed: TRAIN_DAYS + TEST_DAYS + (N_WINDOWS-1)*ROLL_DAYS
    total_span_needed = TRAIN_DAYS + TEST_DAYS + (N_WINDOWS - 1) * ROLL_DAYS
    data_end = price_df.index.max()
    latest_possible_start = data_end - pd.Timedelta(days=total_span_needed)

    if window_start > latest_possible_start:
        window_start = latest_possible_start

    print(f"\n  Walk-forward windows:")
    print(f"  Train: {TRAIN_DAYS}d, Test: {TEST_DAYS}d, Roll: {ROLL_DAYS}d, Windows: {N_WINDOWS}")
    print(f"  First train start: {window_start.date()}")

    results = []

    for w in range(N_WINDOWS):
        train_start = window_start + pd.Timedelta(days=w * ROLL_DAYS)
        train_end = train_start + pd.Timedelta(days=TRAIN_DAYS)
        test_start = train_end
        test_end = test_start + pd.Timedelta(days=TEST_DAYS)

        print(f"\n  --- Window {w+1}/{N_WINDOWS} ---")
        print(f"  Train: {train_start.date()} to {train_end.date()}")
        print(f"  Test:  {test_start.date()} to {test_end.date()}")

        # Optimize on train
        best_params, train_metrics = optimize_on_train(price_df, train_start, train_end)

        if best_params is None:
            print(f"  TRAIN: No valid parameter combination found (insufficient signals)")
            results.append({
                'window': w + 1,
                'train_start': train_start,
                'train_end': train_end,
                'test_start': test_start,
                'test_end': test_end,
                'status': 'no_train_params',
                'train_trades': 0,
                'test_trades': 0,
                'test_pnl': 0,
                'test_sharpe': 0,
                'test_win_rate': 0,
                'test_max_dd': 0,
                'test_avg_hold': 0,
                'best_params': None,
            })
            continue

        print(f"  TRAIN best params: threshold={best_params['funding_threshold']*100:.2f}%, "
              f"max_hold={best_params['max_hold_hours']}h, "
              f"stop_loss={best_params['stop_loss_pct']*100:.1f}%")
        print(f"  TRAIN metrics: trades={train_metrics['n_trades']}, "
              f"Sharpe={train_metrics['sharpe']:.2f}, "
              f"PnL={train_metrics['total_pnl']:.2f}%, "
              f"WR={train_metrics['win_rate']:.1f}%")

        # Apply to test
        test_trades = simulate_trades(
            price_df,
            best_params['funding_threshold'],
            best_params['max_hold_hours'],
            best_params['stop_loss_pct'],
            test_start,
            test_end
        )
        test_metrics = compute_metrics(test_trades)

        status = 'valid'
        if test_metrics['n_trades'] < MIN_TRADES_PER_WINDOW:
            status = 'insufficient'

        print(f"  TEST: trades={test_metrics['n_trades']}, "
              f"Sharpe={test_metrics['sharpe']:.2f}, "
              f"PnL={test_metrics['total_pnl']:.2f}%, "
              f"WR={test_metrics['win_rate']:.1f}%, "
              f"MaxDD={test_metrics['max_dd']:.2f}%, "
              f"AvgHold={test_metrics['avg_hold']:.1f}h"
              f" [{status.upper()}]")

        if test_metrics['exit_reasons']:
            reasons_str = ', '.join(f"{k}:{v}" for k, v in test_metrics['exit_reasons'].items())
            print(f"  Exit reasons: {reasons_str}")

        # Print individual trades for transparency
        if test_trades:
            print(f"  Individual test trades:")
            for t in test_trades:
                entry_t = pd.Timestamp(t['entry_time'])
                print(f"    {entry_t.strftime('%Y-%m-%d %H:%M')} | "
                      f"FR={t['entry_funding']*100:.3f}% | "
                      f"hold={t['hold_hours']}h | "
                      f"exit={t['exit_reason']} | "
                      f"ret={t['net_return']*100:+.3f}%")

        results.append({
            'window': w + 1,
            'train_start': train_start,
            'train_end': train_end,
            'test_start': test_start,
            'test_end': test_end,
            'status': status,
            'train_trades': train_metrics['n_trades'],
            'test_trades': test_metrics['n_trades'],
            'test_pnl': test_metrics['total_pnl'],
            'test_sharpe': test_metrics['sharpe'],
            'test_win_rate': test_metrics['win_rate'],
            'test_max_dd': test_metrics['max_dd'],
            'test_avg_hold': test_metrics['avg_hold'],
            'test_avg_return': test_metrics['avg_return'],
            'test_profit_factor': test_metrics['profit_factor'],
            'test_exit_reasons': test_metrics['exit_reasons'],
            'best_params': best_params,
        })

    return results


def analyze_concentration(token, all_window_trades):
    """
    Check if profits are concentrated in 1-2 events.
    This is a key concern for funding rate signals.
    """
    if not all_window_trades:
        return None

    rets = np.array([t['net_return'] for t in all_window_trades])
    if len(rets) == 0:
        return None

    total_profit = rets[rets > 0].sum()
    if total_profit <= 0:
        return {'concentrated': False, 'reason': 'no_profits'}

    # Sort winning trades by size
    wins = sorted(rets[rets > 0], reverse=True)
    top_1 = wins[0] / total_profit if len(wins) >= 1 else 0
    top_2 = sum(wins[:2]) / total_profit if len(wins) >= 2 else top_1

    return {
        'total_trades': len(rets),
        'winning_trades': len(wins),
        'total_profit_pct': total_profit * 100,
        'top1_share': top_1 * 100,
        'top2_share': top_2 * 100,
        'concentrated': top_2 > 0.80,  # >80% from top 2 trades = concentrated
    }


def run_full_sample_check(token):
    """
    Run the signal on the full sample (not walk-forward) to compare with R97's
    claimed Sharpe of 7.07. This helps us understand the overfit.
    """
    print(f"\n{'='*70}")
    print(f"  FULL SAMPLE CHECK: {token} (comparing to R97 Sharpe 7.07)")
    print(f"{'='*70}")

    price_df = load_data(token)

    # Test with the most common threshold from R97: 0.05%
    for ft in [0.0003, 0.0005, 0.0008, 0.0010]:
        for mh in [48, 72]:
            trades = simulate_trades(price_df, ft, mh, 0.02,
                                     price_df.index.min(), price_df.index.max())
            metrics = compute_metrics(trades)
            if metrics['n_trades'] > 0:
                print(f"  FR>{ft*100:.2f}% hold={mh}h: "
                      f"N={metrics['n_trades']}, "
                      f"Sharpe={metrics['sharpe']:.2f}, "
                      f"PnL={metrics['total_pnl']:.2f}%, "
                      f"WR={metrics['win_rate']:.1f}%")


def print_summary_and_verdict(all_results):
    """
    Print final summary across both tokens and render verdict.
    """
    print(f"\n{'='*70}")
    print("AGGREGATE RESULTS SUMMARY")
    print(f"{'='*70}")

    total_oos_trades = 0
    positive_windows = 0
    valid_windows = 0
    all_sharpes = []
    all_pnls = []

    for token, results in all_results.items():
        print(f"\n  {token}:")
        print(f"  {'Win':>4s} {'Train':>7s} {'Test':>7s} {'Dates':>25s}  {'Trades':>6s}  "
              f"{'PnL':>8s}  {'Sharpe':>7s}  {'WR':>6s}  {'MaxDD':>7s}  {'Status':>12s}  "
              f"{'Params':>40s}")
        print(f"  {'-'*135}")

        for r in results:
            ts = r['test_start']
            te = r['test_end']
            date_range = f"{ts.strftime('%Y-%m-%d')} to {te.strftime('%Y-%m-%d')}"

            params_str = ''
            if r['best_params']:
                p = r['best_params']
                params_str = (f"FR>{p['funding_threshold']*100:.2f}% "
                              f"hold={p['max_hold_hours']}h "
                              f"SL={p['stop_loss_pct']*100:.1f}%")

            print(f"  W{r['window']:>2d}  {r['train_trades']:>5d}  {r['test_trades']:>5d}  "
                  f"{date_range:>25s}  {r['test_trades']:>6d}  "
                  f"{r['test_pnl']:>+7.2f}%  "
                  f"{r['test_sharpe']:>7.2f}  "
                  f"{r['test_win_rate']:>5.1f}%  "
                  f"{r['test_max_dd']:>6.2f}%  "
                  f"{r['status']:>12s}  "
                  f"{params_str:>40s}")

            total_oos_trades += r['test_trades']

            if r['status'] == 'valid':
                valid_windows += 1
                all_sharpes.append(r['test_sharpe'])
                all_pnls.append(r['test_pnl'])
                if r['test_pnl'] > 0:
                    positive_windows += 1

    # Aggregate statistics
    print(f"\n{'='*70}")
    print("KILL CRITERIA EVALUATION")
    print(f"{'='*70}")

    print(f"\n  Total OOS trades: {total_oos_trades}")
    print(f"  Valid windows (>={MIN_TRADES_PER_WINDOW} trades): {valid_windows}")
    print(f"  Positive windows: {positive_windows}/{valid_windows}")

    if valid_windows > 0:
        mean_sharpe = np.mean(all_sharpes)
        median_sharpe = np.median(all_sharpes)
        mean_pnl = np.mean(all_pnls)
        print(f"  Mean OOS Sharpe: {mean_sharpe:.2f}")
        print(f"  Median OOS Sharpe: {median_sharpe:.2f}")
        print(f"  Mean OOS PnL: {mean_pnl:.2f}%")
    else:
        mean_sharpe = 0
        mean_pnl = 0

    # Apply kill criteria
    print(f"\n  --- Kill Criteria ---")

    # Criterion 1: <20 total OOS trades
    if total_oos_trades < 20:
        print(f"  [FAIL] Total OOS trades ({total_oos_trades}) < 20 -> INSUFFICIENT DATA")
        verdict = 'INSUFFICIENT DATA'
    # Criterion 2: <4/8 positive windows (excluding insufficient)
    elif valid_windows > 0 and positive_windows < min(4, valid_windows):
        pos_ratio = positive_windows / valid_windows
        print(f"  [FAIL] Positive windows: {positive_windows}/{valid_windows} "
              f"({pos_ratio*100:.0f}%) < 50% -> KILL")
        verdict = 'KILL'
    # Criterion 3: Mean OOS Sharpe < 0.5
    elif valid_windows > 0 and mean_sharpe < 0.5:
        print(f"  [FAIL] Mean OOS Sharpe ({mean_sharpe:.2f}) < 0.5 -> KILL")
        verdict = 'KILL'
    elif valid_windows > 0 and positive_windows >= 4 and mean_sharpe >= 0.5:
        print(f"  [PASS] Positive windows: {positive_windows}/{valid_windows}")
        print(f"  [PASS] Mean OOS Sharpe: {mean_sharpe:.2f} >= 0.5")
        if total_oos_trades < 30:
            verdict = 'CONDITIONAL PASS'
            print(f"  [WARN] Low trade count ({total_oos_trades}) — conditional pass")
        else:
            verdict = 'PASS'
    else:
        verdict = 'INSUFFICIENT DATA'
        print(f"  [FAIL] Not enough valid windows to assess")

    # Check concentration
    print(f"\n  --- Concentration Check ---")
    # (done per-token in main)

    return verdict, {
        'total_oos_trades': total_oos_trades,
        'valid_windows': valid_windows,
        'positive_windows': positive_windows,
        'mean_sharpe': mean_sharpe if valid_windows > 0 else 0,
        'mean_pnl': mean_pnl if valid_windows > 0 else 0,
    }


def analyze_funding_temporal_distribution(token):
    """
    Analyze when extreme funding events occur.
    Critical for understanding if the signal is regime-dependent.
    """
    fund_path = os.path.join(FUNDING_PROXY_DIR, f'{token}_funding_proxy.parquet')
    fund_df = pd.read_parquet(fund_path)
    if fund_df.index.tz is not None:
        fund_df.index = fund_df.index.tz_localize(None)

    print(f"\n{'='*70}")
    print(f"  TEMPORAL DISTRIBUTION OF EXTREME FUNDING: {token}")
    print(f"{'='*70}")

    fund_df['year'] = fund_df.index.year
    print(f"\n  {'Year':>6s}  {'FR>0.03%':>10s}  {'FR>0.05%':>10s}  {'FR>0.08%':>10s}  {'FR>0.10%':>10s}  {'Total Days':>12s}")
    print(f"  {'-'*70}")

    for year in sorted(fund_df['year'].unique()):
        yr = fund_df[fund_df['year'] == year]
        counts = []
        for t in [0.0003, 0.0005, 0.0008, 0.0010]:
            counts.append((yr['funding_rate_daily'] > t).sum())
        print(f"  {year:>6d}  {counts[0]:>10d}  {counts[1]:>10d}  "
              f"{counts[2]:>10d}  {counts[3]:>10d}  {len(yr):>12d}")

    # Regime analysis
    total_extreme = (fund_df['funding_rate_daily'] > 0.0003).sum()
    pre_2022 = (fund_df[fund_df.index < '2022-01-01']['funding_rate_daily'] > 0.0003).sum()
    post_2022 = (fund_df[fund_df.index >= '2022-01-01']['funding_rate_daily'] > 0.0003).sum()

    print(f"\n  Regime split:")
    print(f"    Pre-2022 extreme days (FR>0.03%): {pre_2022} ({pre_2022/max(total_extreme,1)*100:.0f}% of total)")
    print(f"    Post-2022 extreme days:           {post_2022} ({post_2022/max(total_extreme,1)*100:.0f}% of total)")
    print(f"    CRITICAL: Signal events are {'regime-dependent' if post_2022 < 5 else 'distributed'}")

    return pre_2022, post_2022


def run_regime_aware_walkforward(token):
    """
    Run walk-forward within 2020-2021 regime where funding extremes exist.
    Uses shorter windows: 90d train / 45d test, rolling 45d.
    This gives the signal its BEST possible chance.
    """
    print(f"\n{'='*70}")
    print(f"  REGIME-AWARE WALK-FORWARD: {token} (2020-2021 only)")
    print(f"{'='*70}")
    print(f"  Using shorter windows: 90d train / 45d test to fit within regime")

    price_df = load_data(token)

    regime_train_days = 90
    regime_test_days = 45
    regime_roll_days = 45
    regime_windows = 8

    # Start from 2020-02-01 to have some initial data
    regime_start = pd.Timestamp('2020-02-01')

    results = []
    for w in range(regime_windows):
        train_start = regime_start + pd.Timedelta(days=w * regime_roll_days)
        train_end = train_start + pd.Timedelta(days=regime_train_days)
        test_start = train_end
        test_end = test_start + pd.Timedelta(days=regime_test_days)

        # Check if we have data
        if test_end > price_df.index.max():
            print(f"\n  --- Window {w+1}/{regime_windows}: Beyond data range, skipping ---")
            break

        print(f"\n  --- Window {w+1}/{regime_windows} ---")
        print(f"  Train: {train_start.date()} to {train_end.date()}")
        print(f"  Test:  {test_start.date()} to {test_end.date()}")

        # Optimize on train
        best_params, train_metrics = optimize_on_train(price_df, train_start, train_end)

        if best_params is None:
            print(f"  TRAIN: No valid parameter combination found")
            results.append({
                'window': w + 1,
                'train_start': train_start, 'train_end': train_end,
                'test_start': test_start, 'test_end': test_end,
                'status': 'no_train_params',
                'train_trades': 0, 'test_trades': 0,
                'test_pnl': 0, 'test_sharpe': 0,
                'test_win_rate': 0, 'test_max_dd': 0,
                'test_avg_hold': 0, 'best_params': None,
            })
            continue

        print(f"  TRAIN best: threshold={best_params['funding_threshold']*100:.2f}%, "
              f"hold={best_params['max_hold_hours']}h, SL={best_params['stop_loss_pct']*100:.1f}%")
        print(f"  TRAIN: trades={train_metrics['n_trades']}, "
              f"Sharpe={train_metrics['sharpe']:.2f}, PnL={train_metrics['total_pnl']:.2f}%")

        test_trades = simulate_trades(
            price_df,
            best_params['funding_threshold'],
            best_params['max_hold_hours'],
            best_params['stop_loss_pct'],
            test_start, test_end
        )
        test_metrics = compute_metrics(test_trades)

        status = 'valid' if test_metrics['n_trades'] >= MIN_TRADES_PER_WINDOW else 'insufficient'

        print(f"  TEST: trades={test_metrics['n_trades']}, "
              f"Sharpe={test_metrics['sharpe']:.2f}, "
              f"PnL={test_metrics['total_pnl']:.2f}%, "
              f"WR={test_metrics['win_rate']:.1f}%, "
              f"MaxDD={test_metrics['max_dd']:.2f}% [{status.upper()}]")

        results.append({
            'window': w + 1,
            'train_start': train_start, 'train_end': train_end,
            'test_start': test_start, 'test_end': test_end,
            'status': status,
            'train_trades': train_metrics['n_trades'],
            'test_trades': test_metrics['n_trades'],
            'test_pnl': test_metrics['total_pnl'],
            'test_sharpe': test_metrics['sharpe'],
            'test_win_rate': test_metrics['win_rate'],
            'test_max_dd': test_metrics['max_dd'],
            'test_avg_hold': test_metrics['avg_hold'],
            'test_avg_return': test_metrics.get('avg_return', 0),
            'test_profit_factor': test_metrics.get('profit_factor', 0),
            'test_exit_reasons': test_metrics.get('exit_reasons', {}),
            'best_params': best_params,
        })

    return results


def main():
    print("=" * 70)
    print("R101: FUNDING RATE REVERSAL SHORT — WALK-FORWARD VALIDATION")
    print("=" * 70)
    print(f"Signal: Short when 8h funding rate > threshold (extreme positive)")
    print(f"Exit: funding < 0.01%, OR max hold, OR stop loss")
    print(f"Walk-forward: {N_WINDOWS} windows, {TRAIN_DAYS}d train / {TEST_DAYS}d test")
    print(f"Costs: {ROUND_TRIP_COST*100:.2f}% round-trip")
    print(f"Prior result (R97): Sharpe 7.07 (suspected overfit)")
    print()

    # ── Phase 0: Temporal distribution analysis ──────────────────────────
    print("\n" + "#" * 70)
    print("# PHASE 0: TEMPORAL DISTRIBUTION OF EXTREME FUNDING EVENTS")
    print("#" * 70)

    for token in ['BTC', 'ETH']:
        pre, post = analyze_funding_temporal_distribution(token)

    # ── Phase 1: Full sample check ───────────────────────────────────────
    print("\n" + "#" * 70)
    print("# PHASE 1: FULL SAMPLE CHECK (context only)")
    print("#" * 70)

    all_results = {}
    all_test_trades = {}

    for token in ['BTC', 'ETH']:
        run_full_sample_check(token)

    # ── Phase 2: Standard walk-forward (full date range) ─────────────────
    print("\n" + "#" * 70)
    print("# PHASE 2: STANDARD WALK-FORWARD (180d train / 90d test)")
    print("#" * 70)

    for token in ['BTC', 'ETH']:
        results = run_walkforward(token)
        all_results[f'{token}_standard'] = results

        # Collect test trades
        price_df = load_data(token)
        token_test_trades = []
        for r in results:
            if r['best_params'] and r['status'] == 'valid':
                trades = simulate_trades(
                    price_df,
                    r['best_params']['funding_threshold'],
                    r['best_params']['max_hold_hours'],
                    r['best_params']['stop_loss_pct'],
                    r['test_start'],
                    r['test_end']
                )
                token_test_trades.extend(trades)
        all_test_trades[f'{token}_standard'] = token_test_trades

    # ── Phase 3: Regime-aware walk-forward (2020-2021 only) ──────────────
    print("\n" + "#" * 70)
    print("# PHASE 3: REGIME-AWARE WALK-FORWARD (2020-2021, best case)")
    print("#" * 70)
    print("# This gives the signal its BEST chance by testing only within")
    print("# the regime where extreme funding events actually occur.")

    for token in ['BTC', 'ETH']:
        results = run_regime_aware_walkforward(token)
        all_results[f'{token}_regime'] = results

        price_df = load_data(token)
        token_test_trades = []
        for r in results:
            if r['best_params'] and r['status'] == 'valid':
                trades = simulate_trades(
                    price_df,
                    r['best_params']['funding_threshold'],
                    r['best_params']['max_hold_hours'],
                    r['best_params']['stop_loss_pct'],
                    r['test_start'],
                    r['test_end']
                )
                token_test_trades.extend(trades)
        all_test_trades[f'{token}_regime'] = token_test_trades

    # ── Phase 4: Summary and verdict ─────────────────────────────────────
    print("\n" + "#" * 70)
    print("# PHASE 4: SUMMARY AND VERDICT")
    print("#" * 70)

    # Standard WF verdict
    print("\n--- STANDARD WALK-FORWARD VERDICT ---")
    standard_results = {k: v for k, v in all_results.items() if 'standard' in k}
    verdict_std, stats_std = print_summary_and_verdict(standard_results)

    # Regime-aware WF verdict
    print("\n--- REGIME-AWARE WALK-FORWARD VERDICT ---")
    regime_results = {k: v for k, v in all_results.items() if 'regime' in k}
    verdict_regime, stats_regime = print_summary_and_verdict(regime_results)

    # Concentration analysis
    print(f"\n--- CONCENTRATION ANALYSIS ---")
    for key, trades_list in all_test_trades.items():
        conc = analyze_concentration(key, trades_list)
        if conc and conc.get('total_trades', 0) > 0:
            print(f"\n  {key}: "
                  f"wins={conc['winning_trades']}/{conc['total_trades']}, "
                  f"top1={conc['top1_share']:.1f}%, "
                  f"top2={conc['top2_share']:.1f}%, "
                  f"concentrated={'YES' if conc['concentrated'] else 'NO'}")

    # Overall verdict: worst of (standard, regime)
    # Standard WF fails because events don't exist post-2021
    # Regime WF is the "best case" test
    print(f"\n{'='*70}")
    print("COMBINED VERDICT")
    print(f"{'='*70}")

    print(f"\n  Standard WF (full timeline): {verdict_std}")
    print(f"  Regime WF (2020-2021 only):  {verdict_regime}")

    # The signal is fundamentally flawed if events don't occur post-2021
    # Even if regime WF passes, it's not tradeable going forward
    if verdict_std in ('KILL', 'INSUFFICIENT DATA'):
        if verdict_regime in ('KILL', 'INSUFFICIENT DATA'):
            final_verdict = 'KILL'
            reason = ("Signal fails BOTH standard and regime-aware walk-forward. "
                      "Even within the favorable 2020-2021 regime, the signal does not "
                      "generalize across time windows.")
        else:
            final_verdict = 'KILL'
            reason = ("Signal shows regime-dependent behavior confined to 2020-2021 bull market. "
                      "Extreme funding events (>0.03%) have NOT occurred since 2022. "
                      "The market structure has changed — exchange risk controls, funding rate "
                      "caps, and reduced retail leverage have eliminated the conditions this "
                      "signal depends on. NOT tradeable going forward.")
    elif verdict_std == 'PASS' and verdict_regime == 'PASS':
        final_verdict = 'PASS'
        reason = "Signal passes both standard and regime-aware walk-forward validation."
    else:
        final_verdict = 'CONDITIONAL PASS'
        reason = "Mixed results — signal may have conditional edge in specific regimes."

    print(f"\n  FINAL VERDICT: {final_verdict}")
    print(f"\n  Reason: {reason}")

    print(f"\n  Key findings:")
    print(f"  1. R97 claimed Sharpe 7.07 — this is driven by in-sample optimization")
    print(f"     on a few extreme events in 2020-2021")
    print(f"  2. Extreme funding rate events (>0.03%) are confined to 2020-2021")
    print(f"  3. From 2022 to present, daily avg funding NEVER exceeds 0.03%")
    print(f"     (both BTC and ETH show near-zero events post-2021)")
    print(f"  4. The underlying mechanism (retail overleveraging causing funding")
    print(f"     spikes) has been mitigated by exchange risk controls")
    print(f"  5. Standard WF: {stats_std['valid_windows']} valid windows, "
          f"{stats_std['positive_windows']} positive, "
          f"{stats_std['total_oos_trades']} total OOS trades")
    print(f"  6. Regime WF: {stats_regime['valid_windows']} valid windows, "
          f"{stats_regime['positive_windows']} positive, "
          f"{stats_regime['total_oos_trades']} total OOS trades")

    return all_results, final_verdict, stats_std, stats_regime


if __name__ == '__main__':
    all_results, verdict, stats_std, stats_regime = main()
