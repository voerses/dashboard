#!/workspace/venv/bin/python
"""
R108: Intraday Momentum Breakout — Deep Walk-Forward Validation
=================================================================

Signal (from R107):
  - Entry: 1h return > threshold AND volume > multiplier x 20-bar avg
  - Exit: trailing stop OR max hold reached
  - Market: BTC spot, long-only (buying momentum bursts)
  - R107 result: Standalone Sharpe 0.594, corr 0.007 with V3, 5/6 WF positive

Walk-Forward Protocol (STRICT):
  - 10 rolling windows: 180-day train / 90-day test, rolling 90 days
  - Grid search over 320 parameter combinations per window
  - Optimize on: Sharpe ratio (train set)
  - Test: apply best train params to OOS window
  - Fees: 10bps round-trip

Additional Tests:
  - Parameter sensitivity (+-20% on best params)
  - Regime analysis (UPTREND, DOWNTREND, RANGE via 50-day SMA)
  - Short side test
  - Cost sensitivity (5/10/15/20 bps)
  - Trade clustering analysis

Kill Criteria:
  - <5/10 positive OOS windows -> KILL
  - Mean OOS Sharpe < 0.3 -> KILL
  - <50 total OOS trades -> KILL
  - >50% profit from top 5 trades -> KILL (concentrated)
  - Edge disappears at 15bps fees -> KILL (not robust)
  - Parameter sensitivity >30% degradation -> CONDITIONAL (fragile)
  - Works in only 1 regime -> CONDITIONAL

Author: Quant Research Agent
Date: 2026-03-24
"""

import warnings
import numpy as np
import pandas as pd
from itertools import product
from pathlib import Path
from datetime import timedelta

warnings.filterwarnings('ignore')

# ── Paths ──────────────────────────────────────────────────────────────────────
PROJECT_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = PROJECT_DIR / 'data'
OUTPUT_PY = PROJECT_DIR / 'research' / 'R108_intraday_momentum_deep_wf.py'
OUTPUT_MD = PROJECT_DIR / 'research' / 'R108_intraday_momentum_deep_wf.md'

# ── Walk-Forward Config ────────────────────────────────────────────────────────
TRAIN_DAYS = 180
TEST_DAYS = 90
ROLL_DAYS = 90
N_WINDOWS = 10

# ── Parameter Grid (320 combos) ────────────────────────────────────────────────
RETURN_THRESHOLDS = [0.015, 0.02, 0.025, 0.03]      # 1.5%, 2%, 2.5%, 3%
VOLUME_MULTIPLIERS = [1.5, 2.0, 2.5, 3.0]            # Volume > Nx 20-bar avg
MAX_HOLD_HOURS = [4, 6, 8, 12, 16]                    # Max hold time
TRAIL_STOPS = [0.01, 0.015, 0.02, 0.025]              # Trailing stop %

DEFAULT_COST_BPS = 10  # Round-trip cost in basis points


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
    print(f"  {df.index.min().date()} to {df.index.max().date()}, {len(df)} bars")
    return df


# ══════════════════════════════════════════════════════════════════════════════
# CORE BACKTEST ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def backtest_momentum(btc_1h_slice, ret_thresh, vol_mult, max_hold, trail_stop,
                      cost_bps=DEFAULT_COST_BPS, long_only=True):
    """
    Run intraday momentum breakout backtest on a 1h OHLCV slice.

    Returns: dict with trade-level details and summary stats.
    """
    ret_1h = btc_1h_slice['close'].pct_change()
    avg_vol = btc_1h_slice['volume'].rolling(20).mean()

    n = len(btc_1h_slice)
    close_vals = btc_1h_slice['close'].values
    high_vals = btc_1h_slice['high'].values
    low_vals = btc_1h_slice['low'].values
    ret_vals = ret_1h.values
    vol_vals = btc_1h_slice['volume'].values
    avg_vol_vals = avg_vol.values

    trades = []
    pnl_1h = np.zeros(n)

    in_trade = False
    trade_dir = 0
    entry_price = 0.0
    peak_price = 0.0
    hold_count = 0
    entry_idx = 0

    for i in range(21, n):  # Start after 20-bar lookback
        if np.isnan(ret_vals[i]) or np.isnan(avg_vol_vals[i]):
            continue

        if in_trade:
            hold_count += 1

            # Compute bar PnL
            if trade_dir == 1:
                bar_ret = (close_vals[i] - close_vals[i - 1]) / close_vals[i - 1]
            else:
                bar_ret = -(close_vals[i] - close_vals[i - 1]) / close_vals[i - 1]

            pnl_1h[i] = bar_ret

            # Check exit: max hold
            exit_reason = None
            if hold_count >= max_hold:
                exit_reason = 'max_hold'

            # Trailing stop check
            if exit_reason is None:
                if trade_dir == 1:
                    if close_vals[i] > peak_price:
                        peak_price = close_vals[i]
                    drawdown = (peak_price - close_vals[i]) / peak_price
                    if drawdown > trail_stop:
                        exit_reason = 'trail_stop'
                elif trade_dir == -1:
                    if close_vals[i] < peak_price:
                        peak_price = close_vals[i]
                    drawup = (close_vals[i] - peak_price) / peak_price
                    if drawup > trail_stop:
                        exit_reason = 'trail_stop'

            if exit_reason is not None:
                # Record trade
                if trade_dir == 1:
                    trade_ret = (close_vals[i] - entry_price) / entry_price
                else:
                    trade_ret = -(close_vals[i] - entry_price) / entry_price

                # Subtract round-trip cost
                trade_ret -= cost_bps / 10000

                trades.append({
                    'entry_idx': entry_idx,
                    'exit_idx': i,
                    'entry_time': btc_1h_slice.index[entry_idx],
                    'exit_time': btc_1h_slice.index[i],
                    'direction': trade_dir,
                    'entry_price': entry_price,
                    'exit_price': close_vals[i],
                    'gross_ret': trade_ret + cost_bps / 10000,
                    'net_ret': trade_ret,
                    'hold_bars': hold_count,
                    'exit_reason': exit_reason
                })
                in_trade = False
                trade_dir = 0
        else:
            # Check entry condition
            if abs(ret_vals[i]) > ret_thresh and vol_vals[i] > vol_mult * avg_vol_vals[i]:
                if long_only:
                    if ret_vals[i] > ret_thresh:
                        trade_dir = 1
                        in_trade = True
                        entry_price = close_vals[i]
                        peak_price = close_vals[i]
                        hold_count = 0
                        entry_idx = i
                else:
                    # Both directions
                    trade_dir = 1 if ret_vals[i] > 0 else -1
                    in_trade = True
                    entry_price = close_vals[i]
                    peak_price = close_vals[i]
                    hold_count = 0
                    entry_idx = i

    # Compute summary
    if len(trades) == 0:
        return {
            'trades': [],
            'n_trades': 0,
            'total_ret': 0,
            'sharpe': 0,
            'win_rate': 0,
            'avg_ret': 0,
            'max_dd': 0,
            'avg_hold': 0,
        }

    trade_rets = np.array([t['net_ret'] for t in trades])
    cum_rets = np.cumsum(trade_rets)
    running_max = np.maximum.accumulate(cum_rets)
    drawdowns = cum_rets - running_max
    max_dd = drawdowns.min() if len(drawdowns) > 0 else 0

    # Annualize: compute based on calendar days in the slice
    calendar_days = (btc_1h_slice.index[-1] - btc_1h_slice.index[0]).days
    if calendar_days < 1:
        calendar_days = 1
    trades_per_year = len(trades) / calendar_days * 365
    avg_ret = trade_rets.mean()
    std_ret = trade_rets.std()

    # Sharpe: annualized = mean * sqrt(N) / std, where N = trades per year
    if std_ret > 0 and trades_per_year > 0:
        sharpe = avg_ret / std_ret * np.sqrt(trades_per_year)
    else:
        sharpe = 0

    return {
        'trades': trades,
        'n_trades': len(trades),
        'total_ret': cum_rets[-1] if len(cum_rets) > 0 else 0,
        'sharpe': sharpe,
        'win_rate': (trade_rets > 0).mean(),
        'avg_ret': avg_ret,
        'std_ret': std_ret,
        'max_dd': max_dd,
        'avg_hold': np.mean([t['hold_bars'] for t in trades]),
        'trades_per_year': trades_per_year,
    }


def backtest_short_side(btc_1h_slice, ret_thresh, vol_mult, max_hold, trail_stop,
                        cost_bps=DEFAULT_COST_BPS):
    """
    Short-only backtest: entry on 1h return < -threshold AND volume > mult * avg.
    """
    ret_1h = btc_1h_slice['close'].pct_change()
    avg_vol = btc_1h_slice['volume'].rolling(20).mean()

    n = len(btc_1h_slice)
    close_vals = btc_1h_slice['close'].values
    ret_vals = ret_1h.values
    vol_vals = btc_1h_slice['volume'].values
    avg_vol_vals = avg_vol.values

    trades = []
    in_trade = False
    entry_price = 0.0
    peak_price = 0.0
    hold_count = 0
    entry_idx = 0

    for i in range(21, n):
        if np.isnan(ret_vals[i]) or np.isnan(avg_vol_vals[i]):
            continue

        if in_trade:
            hold_count += 1
            exit_reason = None

            if hold_count >= max_hold:
                exit_reason = 'max_hold'

            if exit_reason is None:
                # Short side: peak_price is lowest price seen
                if close_vals[i] < peak_price:
                    peak_price = close_vals[i]
                drawup = (close_vals[i] - peak_price) / peak_price
                if drawup > trail_stop:
                    exit_reason = 'trail_stop'

            if exit_reason is not None:
                trade_ret = -(close_vals[i] - entry_price) / entry_price
                trade_ret -= cost_bps / 10000
                trades.append({
                    'entry_idx': entry_idx,
                    'exit_idx': i,
                    'entry_time': btc_1h_slice.index[entry_idx],
                    'exit_time': btc_1h_slice.index[i],
                    'direction': -1,
                    'entry_price': entry_price,
                    'exit_price': close_vals[i],
                    'gross_ret': trade_ret + cost_bps / 10000,
                    'net_ret': trade_ret,
                    'hold_bars': hold_count,
                    'exit_reason': exit_reason
                })
                in_trade = False
        else:
            # Short entry: large negative move + high volume
            if ret_vals[i] < -ret_thresh and vol_vals[i] > vol_mult * avg_vol_vals[i]:
                in_trade = True
                entry_price = close_vals[i]
                peak_price = close_vals[i]
                hold_count = 0
                entry_idx = i

    if len(trades) == 0:
        return {
            'trades': [],
            'n_trades': 0,
            'total_ret': 0,
            'sharpe': 0,
            'win_rate': 0,
            'avg_ret': 0,
            'max_dd': 0,
            'avg_hold': 0,
        }

    trade_rets = np.array([t['net_ret'] for t in trades])
    cum_rets = np.cumsum(trade_rets)
    running_max = np.maximum.accumulate(cum_rets)
    drawdowns = cum_rets - running_max
    max_dd = drawdowns.min()

    calendar_days = (btc_1h_slice.index[-1] - btc_1h_slice.index[0]).days
    if calendar_days < 1:
        calendar_days = 1
    trades_per_year = len(trades) / calendar_days * 365
    avg_ret = trade_rets.mean()
    std_ret = trade_rets.std()

    if std_ret > 0 and trades_per_year > 0:
        sharpe = avg_ret / std_ret * np.sqrt(trades_per_year)
    else:
        sharpe = 0

    return {
        'trades': trades,
        'n_trades': len(trades),
        'total_ret': cum_rets[-1],
        'sharpe': sharpe,
        'win_rate': (trade_rets > 0).mean(),
        'avg_ret': avg_ret,
        'std_ret': std_ret if len(trade_rets) > 1 else 0,
        'max_dd': max_dd,
        'avg_hold': np.mean([t['hold_bars'] for t in trades]),
        'trades_per_year': trades_per_year,
    }


# ══════════════════════════════════════════════════════════════════════════════
# WALK-FORWARD ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def run_walk_forward(btc_1h, long_only=True, cost_bps=DEFAULT_COST_BPS):
    """
    10-window rolling walk-forward validation with 320-combo grid search.
    Returns: list of window results with best params and OOS metrics.
    """
    print(f"\n{'='*70}")
    print(f"WALK-FORWARD VALIDATION ({'Long-Only' if long_only else 'Both Sides'})")
    print(f"  Cost: {cost_bps} bps | Train: {TRAIN_DAYS}d | Test: {TEST_DAYS}d | Windows: {N_WINDOWS}")
    print(f"  Grid: {len(RETURN_THRESHOLDS)}x{len(VOLUME_MULTIPLIERS)}x{len(MAX_HOLD_HOURS)}x{len(TRAIL_STOPS)} = {len(RETURN_THRESHOLDS)*len(VOLUME_MULTIPLIERS)*len(MAX_HOLD_HOURS)*len(TRAIL_STOPS)} combos")
    print(f"{'='*70}")

    # Determine start date: need enough history for 10 windows
    data_start = btc_1h.index.min()
    data_end = btc_1h.index.max()

    # Work backwards from data_end to find the last window start
    # Window 10 test ends near data_end
    # Window 10 test starts = data_end - TEST_DAYS
    # Window 10 train starts = data_end - TEST_DAYS - TRAIN_DAYS
    # Window 1 train starts = Window 10 train starts - (N_WINDOWS-1)*ROLL_DAYS
    last_train_start = data_end - timedelta(days=TEST_DAYS + TRAIN_DAYS)
    first_train_start = last_train_start - timedelta(days=(N_WINDOWS - 1) * ROLL_DAYS)

    print(f"  First train start: {first_train_start.date()}")
    print(f"  Last test end:     ~{data_end.date()}")

    if first_train_start < data_start:
        # Adjust: start from data start
        first_train_start = data_start + timedelta(days=1)
        print(f"  [WARN] Adjusted first train start to {first_train_start.date()}")

    param_grid = list(product(RETURN_THRESHOLDS, VOLUME_MULTIPLIERS,
                              MAX_HOLD_HOURS, TRAIL_STOPS))

    all_window_results = []
    all_oos_trades = []

    for w in range(N_WINDOWS):
        train_start = first_train_start + timedelta(days=w * ROLL_DAYS)
        train_end = train_start + timedelta(days=TRAIN_DAYS)
        test_start = train_end
        test_end = test_start + timedelta(days=TEST_DAYS)

        train_data = btc_1h.loc[train_start:train_end]
        test_data = btc_1h.loc[test_start:test_end]

        if len(train_data) < 100 or len(test_data) < 50:
            print(f"  W{w+1}: Insufficient data (train={len(train_data)}, test={len(test_data)}), skipping")
            continue

        print(f"\n  W{w+1}: Train {train_start.date()}->{train_end.date()} ({len(train_data)} bars), "
              f"Test {test_start.date()}->{test_end.date()} ({len(test_data)} bars)")

        # Grid search on train set
        best_sharpe = -999
        best_params = None
        best_result = None

        for rt, vm, mh, ts in param_grid:
            result = backtest_momentum(train_data, rt, vm, mh, ts,
                                       cost_bps=cost_bps, long_only=long_only)
            if result['n_trades'] >= 3 and result['sharpe'] > best_sharpe:
                best_sharpe = result['sharpe']
                best_params = (rt, vm, mh, ts)
                best_result = result

        if best_params is None:
            print(f"    No valid params found (all combos had <3 trades)")
            all_window_results.append({
                'window': w + 1,
                'train_start': train_start,
                'train_end': train_end,
                'test_start': test_start,
                'test_end': test_end,
                'best_params': None,
                'train_sharpe': 0,
                'train_trades': 0,
                'oos_sharpe': 0,
                'oos_trades': 0,
                'oos_total_ret': 0,
                'oos_win_rate': 0,
                'oos_max_dd': 0,
                'oos_avg_hold': 0,
                'positive': False,
            })
            continue

        # Apply best params to OOS
        oos_result = backtest_momentum(test_data, best_params[0], best_params[1],
                                       best_params[2], best_params[3],
                                       cost_bps=cost_bps, long_only=long_only)

        is_positive = oos_result['sharpe'] > 0 and oos_result['n_trades'] >= 2

        print(f"    Best train params: ret={best_params[0]:.1%}, vol={best_params[1]:.1f}x, "
              f"hold={best_params[2]}h, trail={best_params[3]:.1%}")
        print(f"    Train: Sharpe={best_sharpe:.3f}, trades={best_result['n_trades']}")
        print(f"    OOS:   Sharpe={oos_result['sharpe']:.3f}, trades={oos_result['n_trades']}, "
              f"ret={oos_result['total_ret']:.2%}, wr={oos_result['win_rate']:.1%} "
              f"{'[+]' if is_positive else '[-]'}")

        all_window_results.append({
            'window': w + 1,
            'train_start': train_start,
            'train_end': train_end,
            'test_start': test_start,
            'test_end': test_end,
            'best_params': best_params,
            'train_sharpe': best_sharpe,
            'train_trades': best_result['n_trades'],
            'oos_sharpe': oos_result['sharpe'],
            'oos_trades': oos_result['n_trades'],
            'oos_total_ret': oos_result['total_ret'],
            'oos_win_rate': oos_result['win_rate'],
            'oos_max_dd': oos_result['max_dd'],
            'oos_avg_hold': oos_result.get('avg_hold', 0),
            'positive': is_positive,
        })
        all_oos_trades.extend(oos_result['trades'])

    return all_window_results, all_oos_trades


# ══════════════════════════════════════════════════════════════════════════════
# PARAMETER SENSITIVITY ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def parameter_sensitivity(btc_1h, base_params, cost_bps=DEFAULT_COST_BPS):
    """
    Test +-20% on each parameter independently.
    Reports whether Sharpe degrades >30%.
    """
    print(f"\n{'='*70}")
    print("PARAMETER SENSITIVITY ANALYSIS")
    print(f"  Base params: ret={base_params[0]:.1%}, vol={base_params[1]:.1f}x, "
          f"hold={base_params[2]}h, trail={base_params[3]:.1%}")
    print(f"{'='*70}")

    base_result = backtest_momentum(btc_1h, *base_params, cost_bps=cost_bps, long_only=True)
    base_sharpe = base_result['sharpe']
    print(f"  Base Sharpe: {base_sharpe:.3f} ({base_result['n_trades']} trades)")

    param_names = ['ret_thresh', 'vol_mult', 'max_hold', 'trail_stop']
    results = []

    for idx, name in enumerate(param_names):
        for direction in [-0.20, +0.20]:
            test_params = list(base_params)
            test_params[idx] = base_params[idx] * (1 + direction)

            # Ensure max_hold is int
            if idx == 2:
                test_params[idx] = max(1, int(round(test_params[idx])))

            result = backtest_momentum(btc_1h, *test_params, cost_bps=cost_bps, long_only=True)
            pct_change = ((result['sharpe'] - base_sharpe) / abs(base_sharpe) * 100
                          if abs(base_sharpe) > 0.001 else 0)

            results.append({
                'param': name,
                'direction': f"{direction:+.0%}",
                'value': test_params[idx],
                'sharpe': result['sharpe'],
                'n_trades': result['n_trades'],
                'pct_change': pct_change,
                'degraded_30': pct_change < -30,
            })

            symbol = "!!" if pct_change < -30 else "ok"
            print(f"  {name} {direction:+.0%} -> {test_params[idx]:.4f}: "
                  f"Sharpe={result['sharpe']:.3f} ({pct_change:+.1f}%) [{symbol}]")

    return results, base_sharpe


# ══════════════════════════════════════════════════════════════════════════════
# REGIME ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def regime_analysis(btc_1h, oos_trades, cost_bps=DEFAULT_COST_BPS):
    """
    Split OOS trades into regimes: UPTREND, DOWNTREND, RANGE using 50-day SMA.
    """
    print(f"\n{'='*70}")
    print("REGIME ANALYSIS")
    print(f"{'='*70}")

    if len(oos_trades) == 0:
        print("  No OOS trades to analyze")
        return {}

    # Compute daily close and 50-day SMA
    daily_close = btc_1h['close'].resample('1D').last().dropna()
    sma_50 = daily_close.rolling(50).mean()

    # Classify each day
    regime_daily = pd.Series(index=daily_close.index, dtype='object')
    for date in daily_close.index:
        if pd.isna(sma_50.loc[date]) if date in sma_50.index else True:
            regime_daily[date] = 'UNKNOWN'
            continue

        price = daily_close[date]
        sma = sma_50[date]
        pct_from_sma = (price - sma) / sma

        if pct_from_sma > 0.03:
            regime_daily[date] = 'UPTREND'
        elif pct_from_sma < -0.03:
            regime_daily[date] = 'DOWNTREND'
        else:
            regime_daily[date] = 'RANGE'

    # Classify trades by regime at entry
    regime_results = {'UPTREND': [], 'DOWNTREND': [], 'RANGE': []}

    for trade in oos_trades:
        entry_date = trade['entry_time'].normalize()
        # Find nearest regime date
        if entry_date in regime_daily.index:
            regime = regime_daily[entry_date]
        else:
            # Find closest date
            idx = regime_daily.index.get_indexer([entry_date], method='nearest')[0]
            if idx >= 0:
                regime = regime_daily.iloc[idx]
            else:
                regime = 'UNKNOWN'

        if regime in regime_results:
            regime_results[regime].append(trade)

    print(f"\n  Regime distribution:")
    for regime, trades in regime_results.items():
        if len(trades) > 0:
            rets = [t['net_ret'] for t in trades]
            avg = np.mean(rets)
            total = np.sum(rets)
            wr = np.mean([1 if r > 0 else 0 for r in rets])
            print(f"    {regime:12s}: {len(trades):3d} trades, avg={avg:+.2%}, "
                  f"total={total:+.2%}, WR={wr:.0%}")
        else:
            print(f"    {regime:12s}:   0 trades")

    return regime_results


# ══════════════════════════════════════════════════════════════════════════════
# COST SENSITIVITY ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def cost_sensitivity(btc_1h, cost_levels=[5, 10, 15, 20]):
    """
    Run full WF at different cost levels to find breakeven.
    """
    print(f"\n{'='*70}")
    print("COST SENSITIVITY ANALYSIS")
    print(f"{'='*70}")

    results = []
    for cost in cost_levels:
        wf_results, oos_trades = run_walk_forward(btc_1h, long_only=True, cost_bps=cost)

        valid = [w for w in wf_results if w['best_params'] is not None]
        n_positive = sum(1 for w in valid if w['positive'])
        mean_sharpe = np.mean([w['oos_sharpe'] for w in valid]) if valid else 0
        total_trades = sum(w['oos_trades'] for w in valid)
        total_ret = sum(w['oos_total_ret'] for w in valid)

        results.append({
            'cost_bps': cost,
            'n_positive': n_positive,
            'n_valid': len(valid),
            'mean_oos_sharpe': mean_sharpe,
            'total_oos_trades': total_trades,
            'total_oos_return': total_ret,
        })

        print(f"\n  Cost={cost}bps: {n_positive}/{len(valid)} positive, "
              f"mean Sharpe={mean_sharpe:.3f}, trades={total_trades}, ret={total_ret:.2%}")

    return results


# ══════════════════════════════════════════════════════════════════════════════
# TRADE CLUSTERING ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def trade_clustering(oos_trades):
    """
    Analyze whether profits are concentrated in a few trades or distributed.
    """
    print(f"\n{'='*70}")
    print("TRADE CLUSTERING ANALYSIS")
    print(f"{'='*70}")

    if len(oos_trades) == 0:
        print("  No trades to analyze")
        return {}

    rets = sorted([t['net_ret'] for t in oos_trades], reverse=True)
    total_pnl = sum(rets)

    if total_pnl <= 0:
        print(f"  Total PnL is non-positive ({total_pnl:.4f}), clustering is N/A")
        return {
            'total_pnl': total_pnl,
            'top5_pct': 100.0,
            'top10_pct': 100.0,
            'n_trades': len(rets),
            'concentrated': True
        }

    top5_pnl = sum(rets[:5])
    top10_pnl = sum(rets[:10])
    top5_pct = top5_pnl / total_pnl * 100 if total_pnl > 0 else 0
    top10_pct = top10_pnl / total_pnl * 100 if total_pnl > 0 else 0

    # Profit factor
    gross_profit = sum(r for r in rets if r > 0)
    gross_loss = abs(sum(r for r in rets if r < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    print(f"  Total trades: {len(rets)}")
    print(f"  Total PnL: {total_pnl:.4f}")
    print(f"  Top 5 trades PnL: {top5_pnl:.4f} ({top5_pct:.1f}%)")
    print(f"  Top 10 trades PnL: {top10_pnl:.4f} ({top10_pct:.1f}%)")
    print(f"  Profit factor: {profit_factor:.2f}")
    print(f"  Win rate: {np.mean([1 for r in rets if r > 0]) / len(rets):.1%}")

    # Distribution of returns
    pct_rets = np.array(rets) * 100
    print(f"  Return distribution: mean={np.mean(pct_rets):.2f}%, "
          f"median={np.median(pct_rets):.2f}%, "
          f"std={np.std(pct_rets):.2f}%")

    return {
        'total_pnl': total_pnl,
        'top5_pnl': top5_pnl,
        'top5_pct': top5_pct,
        'top10_pnl': top10_pnl,
        'top10_pct': top10_pct,
        'profit_factor': profit_factor,
        'n_trades': len(rets),
        'concentrated': top5_pct > 50,
    }


# ══════════════════════════════════════════════════════════════════════════════
# FULL-PERIOD BEST PARAMS FINDER
# ══════════════════════════════════════════════════════════════════════════════

def find_best_full_period_params(btc_1h, cost_bps=DEFAULT_COST_BPS):
    """
    Grid search over entire dataset to find best params (for sensitivity testing).
    """
    print(f"\n{'='*70}")
    print("FULL-PERIOD PARAMETER OPTIMIZATION")
    print(f"{'='*70}")

    param_grid = list(product(RETURN_THRESHOLDS, VOLUME_MULTIPLIERS,
                              MAX_HOLD_HOURS, TRAIL_STOPS))

    best_sharpe = -999
    best_params = None
    best_result = None

    for rt, vm, mh, ts in param_grid:
        result = backtest_momentum(btc_1h, rt, vm, mh, ts,
                                   cost_bps=cost_bps, long_only=True)
        if result['n_trades'] >= 10 and result['sharpe'] > best_sharpe:
            best_sharpe = result['sharpe']
            best_params = (rt, vm, mh, ts)
            best_result = result

    if best_params:
        print(f"  Best params: ret={best_params[0]:.1%}, vol={best_params[1]:.1f}x, "
              f"hold={best_params[2]}h, trail={best_params[3]:.1%}")
        print(f"  Sharpe={best_sharpe:.3f}, trades={best_result['n_trades']}, "
              f"ret={best_result['total_ret']:.2%}, WR={best_result['win_rate']:.1%}")

    return best_params, best_result


# ══════════════════════════════════════════════════════════════════════════════
# REPORT GENERATION
# ══════════════════════════════════════════════════════════════════════════════

def generate_report(wf_results, oos_trades, sensitivity_results, base_sharpe,
                    regime_results, short_results, short_oos_trades,
                    cost_results, clustering_results,
                    best_full_params, best_full_result):
    """Generate markdown report."""

    valid_windows = [w for w in wf_results if w['best_params'] is not None]
    n_positive = sum(1 for w in valid_windows if w['positive'])
    n_valid = len(valid_windows)
    mean_oos_sharpe = np.mean([w['oos_sharpe'] for w in valid_windows]) if valid_windows else 0
    total_oos_trades = sum(w['oos_trades'] for w in valid_windows)
    total_oos_ret = sum(w['oos_total_ret'] for w in valid_windows)

    # Determine verdict
    kill_reasons = []
    conditional_reasons = []

    # Kill criteria
    if n_positive < 5:
        kill_reasons.append(f"Only {n_positive}/{n_valid} positive OOS windows (need >=5/10)")
    if mean_oos_sharpe < 0.3:
        kill_reasons.append(f"Mean OOS Sharpe {mean_oos_sharpe:.3f} < 0.3")
    if total_oos_trades < 50:
        kill_reasons.append(f"Only {total_oos_trades} total OOS trades (need >=50)")
    if clustering_results.get('concentrated', False) and clustering_results.get('top5_pct', 0) > 50:
        kill_reasons.append(f"Top 5 trades = {clustering_results['top5_pct']:.1f}% of profit (>50%)")

    # Cost sensitivity kill
    cost_15 = next((c for c in cost_results if c['cost_bps'] == 15), None)
    if cost_15 and cost_15['mean_oos_sharpe'] < 0.1:
        kill_reasons.append(f"Edge disappears at 15bps (Sharpe={cost_15['mean_oos_sharpe']:.3f})")

    # Conditional criteria
    any_degraded = any(r['degraded_30'] for r in sensitivity_results)
    if any_degraded:
        conditional_reasons.append("Parameter sensitivity: >30% Sharpe degradation on some params")

    # Regime check
    regime_with_trades = {k: v for k, v in regime_results.items() if len(v) >= 3}
    regime_positive = sum(1 for k, v in regime_with_trades.items()
                          if np.mean([t['net_ret'] for t in v]) > 0)
    if len(regime_with_trades) >= 2 and regime_positive <= 1:
        conditional_reasons.append(f"Works in only {regime_positive}/{len(regime_with_trades)} regimes")

    if kill_reasons:
        verdict = "KILL"
    elif conditional_reasons:
        verdict = "CONDITIONAL PASS"
    else:
        verdict = "PASS"

    # Build report
    lines = []
    lines.append("# R108 -- Intraday Momentum Breakout: Deep Walk-Forward Validation")
    lines.append("")
    lines.append(f"**Date**: 2026-03-24")
    lines.append(f"**Asset**: BTC spot 1h")
    lines.append(f"**Base cost**: 10 bps round-trip")
    lines.append(f"**R107 reference**: Standalone Sharpe 0.594, corr 0.007 with V3, 5/6 WF positive")
    lines.append("")

    # Verdict box
    lines.append(f"## VERDICT: **{verdict}**")
    lines.append("")
    if kill_reasons:
        lines.append("### Kill Reasons")
        for r in kill_reasons:
            lines.append(f"- {r}")
        lines.append("")
    if conditional_reasons:
        lines.append("### Conditional Flags")
        for r in conditional_reasons:
            lines.append(f"- {r}")
        lines.append("")

    # Summary stats
    lines.append("## Walk-Forward Summary")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Windows | {n_valid} (of {N_WINDOWS} configured) |")
    lines.append(f"| Positive OOS windows | {n_positive}/{n_valid} |")
    lines.append(f"| Mean OOS Sharpe | {mean_oos_sharpe:.3f} |")
    lines.append(f"| Total OOS trades | {total_oos_trades} |")
    lines.append(f"| Total OOS return | {total_oos_ret:.2%} |")
    lines.append("")

    # Walk-forward detail table
    lines.append("## Walk-Forward Window Detail")
    lines.append("")
    lines.append("| Window | Train Period | Test Period | Best Params (ret/vol/hold/trail) | Train Sharpe | OOS Sharpe | OOS Trades | OOS Return | OOS WR | Positive? |")
    lines.append("|--------|-------------|-------------|----------------------------------|-------------|-----------|-----------|-----------|--------|-----------|")

    for w in wf_results:
        if w['best_params']:
            bp = w['best_params']
            params_str = f"{bp[0]:.1%}/{bp[1]:.1f}x/{bp[2]}h/{bp[3]:.1%}"
        else:
            params_str = "N/A"

        pos_str = "YES" if w['positive'] else "NO"
        lines.append(
            f"| W{w['window']} "
            f"| {w['train_start'].strftime('%Y-%m-%d')} to {w['train_end'].strftime('%Y-%m-%d')} "
            f"| {w['test_start'].strftime('%Y-%m-%d')} to {w['test_end'].strftime('%Y-%m-%d')} "
            f"| {params_str} "
            f"| {w['train_sharpe']:.3f} "
            f"| {w['oos_sharpe']:.3f} "
            f"| {w['oos_trades']} "
            f"| {w['oos_total_ret']:.2%} "
            f"| {w['oos_win_rate']:.0%} "
            f"| {pos_str} |"
        )

    lines.append("")

    # Best full-period params
    if best_full_params and best_full_result:
        lines.append("## Full-Period Best Parameters")
        lines.append("")
        lines.append(f"| Parameter | Value |")
        lines.append(f"|-----------|-------|")
        lines.append(f"| Return threshold | {best_full_params[0]:.1%} |")
        lines.append(f"| Volume multiplier | {best_full_params[1]:.1f}x |")
        lines.append(f"| Max hold | {best_full_params[2]}h |")
        lines.append(f"| Trailing stop | {best_full_params[3]:.1%} |")
        lines.append(f"| Sharpe (full period) | {best_full_result['sharpe']:.3f} |")
        lines.append(f"| Trades | {best_full_result['n_trades']} |")
        lines.append(f"| Total return | {best_full_result['total_ret']:.2%} |")
        lines.append(f"| Win rate | {best_full_result['win_rate']:.1%} |")
        lines.append(f"| Avg hold (bars) | {best_full_result['avg_hold']:.1f} |")
        lines.append("")

    # Parameter sensitivity table
    lines.append("## Parameter Sensitivity (+/-20%)")
    lines.append("")
    lines.append(f"Base Sharpe: {base_sharpe:.3f}")
    lines.append("")
    lines.append("| Parameter | Direction | Value | Sharpe | Change | Degraded >30%? |")
    lines.append("|-----------|----------|-------|--------|--------|----------------|")
    for r in sensitivity_results:
        flag = "YES" if r['degraded_30'] else "no"
        if isinstance(r['value'], float) and r['value'] < 1:
            val_str = f"{r['value']:.3f}"
        elif isinstance(r['value'], float):
            val_str = f"{r['value']:.2f}"
        else:
            val_str = str(r['value'])
        lines.append(
            f"| {r['param']} | {r['direction']} | {val_str} "
            f"| {r['sharpe']:.3f} | {r['pct_change']:+.1f}% | {flag} |"
        )
    lines.append("")

    any_degraded = any(r['degraded_30'] for r in sensitivity_results)
    if any_degraded:
        lines.append("**WARNING**: Some parameters show >30% Sharpe degradation at +/-20% perturbation. Signal is FRAGILE on those axes.")
    else:
        lines.append("All parameters show <30% Sharpe degradation at +/-20% perturbation. Signal is ROBUST to parameter choice.")
    lines.append("")

    # Regime analysis
    lines.append("## Regime Analysis (50-day SMA)")
    lines.append("")
    lines.append("| Regime | Trades | Avg Return | Total Return | Win Rate | Profitable? |")
    lines.append("|--------|--------|-----------|-------------|---------|-------------|")
    for regime in ['UPTREND', 'DOWNTREND', 'RANGE']:
        trades = regime_results.get(regime, [])
        if len(trades) >= 1:
            rets = [t['net_ret'] for t in trades]
            avg_r = np.mean(rets)
            tot_r = np.sum(rets)
            wr = np.mean([1 if r > 0 else 0 for r in rets])
            profitable = "YES" if avg_r > 0 else "NO"
            lines.append(f"| {regime} | {len(trades)} | {avg_r:.2%} | {tot_r:.2%} | {wr:.0%} | {profitable} |")
        else:
            lines.append(f"| {regime} | 0 | N/A | N/A | N/A | N/A |")
    lines.append("")

    # Key question: does it work during V3's weakness (RANGE)?
    range_trades = regime_results.get('RANGE', [])
    if len(range_trades) >= 3:
        range_avg = np.mean([t['net_ret'] for t in range_trades])
        if range_avg > 0:
            lines.append("**Key finding**: Signal IS profitable in RANGE regime (V3's weakness). This provides diversification value.")
        else:
            lines.append("**Key finding**: Signal is NOT profitable in RANGE regime. Diversification value is limited.")
    else:
        lines.append(f"**Key finding**: Insufficient RANGE trades ({len(range_trades)}) to draw conclusions.")
    lines.append("")

    # Short side test
    lines.append("## Short Side Test")
    lines.append("")
    if short_results:
        short_valid = [w for w in short_results if w['best_params'] is not None]
        short_positive = sum(1 for w in short_valid if w['positive'])
        short_mean_sharpe = np.mean([w['oos_sharpe'] for w in short_valid]) if short_valid else 0
        short_total_trades = sum(w['oos_trades'] for w in short_valid)
        short_total_ret = sum(w['oos_total_ret'] for w in short_valid)

        lines.append(f"| Metric | Long-Only | Short-Only |")
        lines.append(f"|--------|-----------|-----------|")
        lines.append(f"| Positive windows | {n_positive}/{n_valid} | {short_positive}/{len(short_valid)} |")
        lines.append(f"| Mean OOS Sharpe | {mean_oos_sharpe:.3f} | {short_mean_sharpe:.3f} |")
        lines.append(f"| Total OOS trades | {total_oos_trades} | {short_total_trades} |")
        lines.append(f"| Total OOS return | {total_oos_ret:.2%} | {short_total_ret:.2%} |")
        lines.append("")

        if short_mean_sharpe > 0.2 and short_positive >= 3:
            lines.append("Short side shows promise and could ADD value to the long-only signal.")
        else:
            lines.append("Short side does NOT add meaningful value. Keep long-only.")
    else:
        lines.append("Short side test not run or no results.")
    lines.append("")

    # Cost sensitivity
    lines.append("## Cost Sensitivity")
    lines.append("")
    lines.append("| Cost (bps) | Positive Windows | Mean OOS Sharpe | Total OOS Trades | Total OOS Return |")
    lines.append("|-----------|-----------------|----------------|-----------------|-----------------|")
    for c in cost_results:
        lines.append(
            f"| {c['cost_bps']} "
            f"| {c['n_positive']}/{c['n_valid']} "
            f"| {c['mean_oos_sharpe']:.3f} "
            f"| {c['total_oos_trades']} "
            f"| {c['total_oos_return']:.2%} |"
        )
    lines.append("")

    # Find breakeven
    # Check if ALL cost levels are negative
    all_negative = all(c['mean_oos_sharpe'] < 0.1 for c in cost_results)
    if all_negative:
        lines.append(f"**No edge at any cost level**: Signal has negative mean OOS Sharpe even at lowest tested cost ({cost_results[0]['cost_bps']} bps). No tradeable edge exists.")
    else:
        breakeven = None
        for c in cost_results:
            if c['mean_oos_sharpe'] < 0.1:
                breakeven = c['cost_bps']
                break
        if breakeven:
            lines.append(f"**Edge breakeven**: Signal becomes unprofitable around {breakeven} bps round-trip.")
        else:
            lines.append("**Edge is robust**: Signal remains profitable across all tested cost levels.")
    lines.append("")

    # Trade clustering
    lines.append("## Trade Clustering")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total OOS trades | {clustering_results.get('n_trades', 0)} |")
    lines.append(f"| Total PnL | {clustering_results.get('total_pnl', 0):.4f} |")
    lines.append(f"| Top 5 trades % | {clustering_results.get('top5_pct', 0):.1f}% |")
    lines.append(f"| Top 10 trades % | {clustering_results.get('top10_pct', 0):.1f}% |")
    lines.append(f"| Profit factor | {clustering_results.get('profit_factor', 0):.2f} |")
    lines.append(f"| Concentrated (>50% top5)? | {'YES - FRAGILE' if clustering_results.get('concentrated') else 'NO - distributed'} |")
    lines.append("")

    # Kill criteria checklist
    lines.append("## Kill Criteria Checklist")
    lines.append("")
    lines.append("| Criterion | Threshold | Actual | Pass? |")
    lines.append("|-----------|----------|--------|-------|")

    pass_pos = n_positive >= 5
    lines.append(f"| Positive OOS windows | >=5/10 | {n_positive}/{n_valid} | {'PASS' if pass_pos else 'FAIL'} |")

    pass_sharpe = mean_oos_sharpe >= 0.3
    lines.append(f"| Mean OOS Sharpe | >=0.3 | {mean_oos_sharpe:.3f} | {'PASS' if pass_sharpe else 'FAIL'} |")

    pass_trades = total_oos_trades >= 50
    lines.append(f"| Total OOS trades | >=50 | {total_oos_trades} | {'PASS' if pass_trades else 'FAIL'} |")

    not_concentrated = not clustering_results.get('concentrated', True)
    lines.append(f"| Top 5 trades < 50% | <50% | {clustering_results.get('top5_pct', 0):.1f}% | {'PASS' if not_concentrated else 'FAIL'} |")

    pass_cost_15 = True
    if cost_15:
        pass_cost_15 = cost_15['mean_oos_sharpe'] >= 0.1
    cost_15_str = f"{cost_15['mean_oos_sharpe']:.3f}" if cost_15 else "N/A"
    lines.append(f"| Edge at 15bps | Sharpe>=0.1 | {cost_15_str} | {'PASS' if pass_cost_15 else 'FAIL'} |")

    pass_sensitivity = not any_degraded
    lines.append(f"| Param sensitivity <30% | all <30% | {'all ok' if pass_sensitivity else 'some >30%'} | {'PASS' if pass_sensitivity else 'CONDITIONAL'} |")

    pass_regime = regime_positive >= 2 if len(regime_with_trades) >= 2 else True
    lines.append(f"| Multi-regime | >=2 regimes | {regime_positive}/{len(regime_with_trades)} | {'PASS' if pass_regime else 'CONDITIONAL'} |")

    lines.append("")

    # Final verdict with reasoning
    lines.append(f"## Final Verdict: **{verdict}**")
    lines.append("")
    if verdict == "KILL":
        lines.append("The intraday momentum breakout signal **fails** deep walk-forward validation:")
        for r in kill_reasons:
            lines.append(f"- {r}")
        lines.append("")
        lines.append("**Recommendation**: Do NOT allocate capital to this signal. "
                      "The R107 results were likely due to limited WF windows (6 vs 10) "
                      "or overfitting in the initial evaluation.")
    elif verdict == "CONDITIONAL PASS":
        lines.append("The signal **conditionally passes** deep walk-forward validation:")
        lines.append("")
        lines.append("Strengths:")
        if pass_pos:
            lines.append(f"- {n_positive}/{n_valid} positive OOS windows")
        if pass_sharpe:
            lines.append(f"- Mean OOS Sharpe {mean_oos_sharpe:.3f}")
        if pass_trades:
            lines.append(f"- {total_oos_trades} OOS trades (sufficient sample)")
        if not_concentrated:
            lines.append(f"- Profits distributed (top5 = {clustering_results.get('top5_pct', 0):.1f}%)")
        lines.append("")
        lines.append("Concerns:")
        for r in conditional_reasons:
            lines.append(f"- {r}")
        lines.append("")
        lines.append("**Recommendation**: Consider at REDUCED allocation (25-50% of standard). "
                      "Monitor for regime sensitivity. Good diversifier for V3 due to near-zero correlation.")
    else:
        lines.append("The signal **passes** all deep walk-forward validation criteria:")
        lines.append(f"- {n_positive}/{n_valid} positive OOS windows")
        lines.append(f"- Mean OOS Sharpe {mean_oos_sharpe:.3f}")
        lines.append(f"- {total_oos_trades} OOS trades")
        lines.append(f"- Profits distributed (top5 = {clustering_results.get('top5_pct', 0):.1f}%)")
        lines.append(f"- Robust to costs up to tested levels")
        lines.append(f"- Parameters stable to +-20% perturbation")
        lines.append("")
        lines.append("**Recommendation**: Proceed with standard allocation. "
                      "Near-zero correlation with V3 (r=0.007) makes this an excellent diversifier.")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# SHORT SIDE WALK-FORWARD
# ══════════════════════════════════════════════════════════════════════════════

def run_short_walk_forward(btc_1h, cost_bps=DEFAULT_COST_BPS):
    """
    Walk-forward for short-only side of the momentum breakout.
    """
    print(f"\n{'='*70}")
    print("SHORT SIDE WALK-FORWARD")
    print(f"{'='*70}")

    data_end = btc_1h.index.max()
    last_train_start = data_end - timedelta(days=TEST_DAYS + TRAIN_DAYS)
    first_train_start = last_train_start - timedelta(days=(N_WINDOWS - 1) * ROLL_DAYS)

    if first_train_start < btc_1h.index.min():
        first_train_start = btc_1h.index.min() + timedelta(days=1)

    param_grid = list(product(RETURN_THRESHOLDS, VOLUME_MULTIPLIERS,
                              MAX_HOLD_HOURS, TRAIL_STOPS))

    all_window_results = []
    all_oos_trades = []

    for w in range(N_WINDOWS):
        train_start = first_train_start + timedelta(days=w * ROLL_DAYS)
        train_end = train_start + timedelta(days=TRAIN_DAYS)
        test_start = train_end
        test_end = test_start + timedelta(days=TEST_DAYS)

        train_data = btc_1h.loc[train_start:train_end]
        test_data = btc_1h.loc[test_start:test_end]

        if len(train_data) < 100 or len(test_data) < 50:
            continue

        print(f"\n  W{w+1}: Train {train_start.date()}->{train_end.date()}, "
              f"Test {test_start.date()}->{test_end.date()}")

        best_sharpe = -999
        best_params = None

        for rt, vm, mh, ts in param_grid:
            result = backtest_short_side(train_data, rt, vm, mh, ts, cost_bps=cost_bps)
            if result['n_trades'] >= 3 and result['sharpe'] > best_sharpe:
                best_sharpe = result['sharpe']
                best_params = (rt, vm, mh, ts)

        if best_params is None:
            all_window_results.append({
                'window': w + 1,
                'train_start': train_start, 'train_end': train_end,
                'test_start': test_start, 'test_end': test_end,
                'best_params': None,
                'train_sharpe': 0, 'train_trades': 0,
                'oos_sharpe': 0, 'oos_trades': 0,
                'oos_total_ret': 0, 'oos_win_rate': 0,
                'oos_max_dd': 0, 'oos_avg_hold': 0,
                'positive': False,
            })
            continue

        oos_result = backtest_short_side(test_data, *best_params, cost_bps=cost_bps)
        is_positive = oos_result['sharpe'] > 0 and oos_result['n_trades'] >= 2

        print(f"    Best: ret={best_params[0]:.1%}, vol={best_params[1]:.1f}x, "
              f"hold={best_params[2]}h, trail={best_params[3]:.1%}")
        print(f"    OOS: Sharpe={oos_result['sharpe']:.3f}, trades={oos_result['n_trades']}, "
              f"ret={oos_result['total_ret']:.2%} {'[+]' if is_positive else '[-]'}")

        all_window_results.append({
            'window': w + 1,
            'train_start': train_start, 'train_end': train_end,
            'test_start': test_start, 'test_end': test_end,
            'best_params': best_params,
            'train_sharpe': best_sharpe,
            'train_trades': 0,
            'oos_sharpe': oos_result['sharpe'],
            'oos_trades': oos_result['n_trades'],
            'oos_total_ret': oos_result['total_ret'],
            'oos_win_rate': oos_result['win_rate'],
            'oos_max_dd': oos_result['max_dd'],
            'oos_avg_hold': oos_result.get('avg_hold', 0),
            'positive': is_positive,
        })
        all_oos_trades.extend(oos_result['trades'])

    return all_window_results, all_oos_trades


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("R108: INTRADAY MOMENTUM BREAKOUT — DEEP WALK-FORWARD VALIDATION")
    print("=" * 70)

    btc_1h = load_btc_1h()

    # ── 1. Main Walk-Forward (10 windows, long-only, 10bps) ────────────────
    wf_results, oos_trades = run_walk_forward(btc_1h, long_only=True, cost_bps=DEFAULT_COST_BPS)

    # ── 2. Find best full-period params ────────────────────────────────────
    best_full_params, best_full_result = find_best_full_period_params(btc_1h)

    # ── 3. Parameter sensitivity ───────────────────────────────────────────
    if best_full_params:
        sensitivity_results, base_sharpe = parameter_sensitivity(btc_1h, best_full_params)
    else:
        sensitivity_results, base_sharpe = [], 0

    # ── 4. Regime analysis ─────────────────────────────────────────────────
    regime_results = regime_analysis(btc_1h, oos_trades)

    # ── 5. Short side test ─────────────────────────────────────────────────
    short_results, short_oos_trades = run_short_walk_forward(btc_1h)

    # ── 6. Cost sensitivity ────────────────────────────────────────────────
    cost_results = cost_sensitivity(btc_1h, cost_levels=[5, 10, 15, 20])

    # ── 7. Trade clustering ────────────────────────────────────────────────
    clustering_results = trade_clustering(oos_trades)

    # ── 8. Generate report ─────────────────────────────────────────────────
    report = generate_report(
        wf_results, oos_trades,
        sensitivity_results, base_sharpe,
        regime_results,
        short_results, short_oos_trades,
        cost_results, clustering_results,
        best_full_params, best_full_result,
    )

    # Write report
    with open(OUTPUT_MD, 'w') as f:
        f.write(report)
    print(f"\n[DONE] Report written to {OUTPUT_MD}")

    return report


if __name__ == '__main__':
    main()
