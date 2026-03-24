"""
Trend + Pullback Walk-Forward Validation
Signal from R98: Daily EMA20/50 trend + 4h RSI(14) pullback entry
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional
import json
import warnings
warnings.filterwarnings('ignore')

# ============================================================
# DATA LOADING
# ============================================================

DATA_PATHS = {
    'BTC_spot': '/workspace/crypto_backtest/data/spot/1h_cache/BTC_1h.parquet',
    'ETH_spot': '/workspace/crypto_backtest/data/spot/1h_cache/ETH_1h.parquet',
    'BTC_perp': '/workspace/crypto_backtest/data/perp/1h_cache/BTC_1h.parquet',
    'ETH_perp': '/workspace/crypto_backtest/data/perp/1h_cache/ETH_1h.parquet',
}

def load_data(key: str) -> pd.DataFrame:
    df = pd.read_parquet(DATA_PATHS[key])
    if df.index.name != 'datetime':
        df.index.name = 'datetime'
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    # Deduplicate index
    df = df[~df.index.duplicated(keep='first')]
    return df


# ============================================================
# INDICATOR COMPUTATION
# ============================================================

def compute_daily_emas(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Compute daily 20d and 50d EMA from 1h data."""
    daily = df_1h['close'].resample('1D').last().dropna()
    ema20 = daily.ewm(span=20, adjust=False).mean()
    ema50 = daily.ewm(span=50, adjust=False).mean()
    result = pd.DataFrame({'ema20': ema20, 'ema50': ema50, 'daily_close': daily})
    result['uptrend'] = result['ema20'] > result['ema50']
    # Compute 50d EMA slope for regime classification (normalized by price)
    result['ema50_slope'] = result['ema50'].pct_change(5)  # 5-day slope
    return result


def compute_4h_rsi(df_1h: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute RSI on 4h bars from 1h data."""
    close_4h = df_1h['close'].resample('4h').last().dropna()
    delta = close_4h.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_4h_atr(df_1h: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute ATR on 4h bars from 1h data."""
    ohlc_4h = df_1h.resample('4h').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'
    }).dropna()
    high = ohlc_4h['high']
    low = ohlc_4h['low']
    prev_close = ohlc_4h['close'].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()
    return atr


def compute_4h_close(df_1h: pd.DataFrame) -> pd.Series:
    """Get 4h close prices."""
    return df_1h['close'].resample('4h').last().dropna()


# ============================================================
# SIGNAL GENERATION
# ============================================================

@dataclass
class Trade:
    entry_time: pd.Timestamp
    entry_price: float
    direction: str  # 'long' or 'short'
    exit_time: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    pnl_pct: float = 0.0
    bars_held: int = 0


def generate_signals_and_trade(
    df_1h: pd.DataFrame,
    start: str, end: str,
    rsi_long_thresh: float = 40.0,
    rsi_short_thresh: float = 60.0,
    fee_bps: float = 10.0,
    atr_multiplier: float = 2.0,
    max_hold_hours: int = 168,
    instrument: str = 'spot',
    df_perp_1h: Optional[pd.DataFrame] = None,
) -> List[Trade]:
    """
    Generate signals and simulate trades for the given period.

    Long entry: daily uptrend (EMA20>EMA50) + 4h RSI dips below rsi_long_thresh then recovers above
    Short entry: daily downtrend (EMA20<EMA50) + 4h RSI spikes above rsi_short_thresh then drops below
    Exit: 2x ATR(14) trailing stop on 4h bars, max hold 168h (7 days)
    """
    # Need warmup data: 50 days for daily EMA + 14*4h bars for RSI
    # We'll use all data up to end, but only take signals in [start, end]
    df_slice = df_1h.loc[:end].copy()

    # Compute indicators on full available history for warmup
    daily_emas = compute_daily_emas(df_slice)
    rsi_4h = compute_4h_rsi(df_slice, period=14)
    atr_4h = compute_4h_atr(df_slice, period=14)
    close_4h = compute_4h_close(df_slice)

    # Align to 4h bars in the test period
    test_start = pd.Timestamp(start)
    test_end = pd.Timestamp(end)

    rsi_test = rsi_4h.loc[test_start:test_end]

    # Get funding rate for perp cost
    funding_1h = None
    if instrument == 'perp' and df_perp_1h is not None:
        if 'funding_1h' in df_perp_1h.columns:
            funding_1h = df_perp_1h['funding_1h'].loc[test_start:test_end]

    trades = []
    in_trade = False
    current_trade = None
    trailing_stop = None
    entry_bar_count = 0

    # State for RSI crossover detection
    prev_rsi = None

    rsi_timestamps = rsi_test.index.tolist()

    for i, ts in enumerate(rsi_timestamps):
        current_rsi = rsi_test.iloc[i]

        if np.isnan(current_rsi):
            prev_rsi = current_rsi
            continue

        # Get current 4h close and ATR
        if ts not in close_4h.index:
            prev_rsi = current_rsi
            continue
        current_close = close_4h.loc[ts]

        if ts not in atr_4h.index or np.isnan(atr_4h.loc[ts]):
            prev_rsi = current_rsi
            continue
        current_atr = atr_4h.loc[ts]

        # Get daily trend for this bar's date
        bar_date = ts.normalize()
        # Use previous day's close for daily EMA to avoid lookahead
        prev_dates = daily_emas.index[daily_emas.index < bar_date]
        if len(prev_dates) == 0:
            prev_rsi = current_rsi
            continue
        latest_daily = prev_dates[-1]
        is_uptrend = daily_emas.loc[latest_daily, 'uptrend']

        if in_trade:
            entry_bar_count += 1
            hours_held = entry_bar_count * 4

            # Check trailing stop and max hold
            if current_trade.direction == 'long':
                # Update trailing stop upward
                new_stop = current_close - atr_multiplier * current_atr
                if trailing_stop is None or new_stop > trailing_stop:
                    trailing_stop = new_stop

                # Check stop hit (use low of 4h bar if available)
                stopped = current_close <= trailing_stop
                timed_out = hours_held >= max_hold_hours

                if stopped or timed_out:
                    exit_price = trailing_stop if stopped else current_close
                    # Ensure exit price is not negative or zero
                    exit_price = max(exit_price, current_close * 0.5)
                    pnl = (exit_price / current_trade.entry_price - 1)
                    # Apply fees
                    fee = fee_bps / 10000 * 2  # round trip
                    if instrument == 'perp':
                        fee = 7 / 10000 * 2  # 7bps each way
                        # Add funding cost
                        if funding_1h is not None:
                            # Sum hourly funding over hold period
                            hold_start = current_trade.entry_time
                            hold_end = ts
                            funding_slice = funding_1h.loc[hold_start:hold_end]
                            funding_cost = funding_slice.sum()
                            pnl -= abs(funding_cost)  # funding is cost for both long and short
                    pnl -= fee
                    current_trade.exit_time = ts
                    current_trade.exit_price = exit_price
                    current_trade.pnl_pct = pnl
                    current_trade.bars_held = entry_bar_count
                    trades.append(current_trade)
                    in_trade = False
                    current_trade = None
                    trailing_stop = None
                    entry_bar_count = 0

            elif current_trade.direction == 'short':
                # Update trailing stop downward
                new_stop = current_close + atr_multiplier * current_atr
                if trailing_stop is None or new_stop < trailing_stop:
                    trailing_stop = new_stop

                stopped = current_close >= trailing_stop
                timed_out = hours_held >= max_hold_hours

                if stopped or timed_out:
                    exit_price = trailing_stop if stopped else current_close
                    exit_price = min(exit_price, current_close * 1.5)
                    pnl = (current_trade.entry_price / exit_price - 1)
                    fee = fee_bps / 10000 * 2
                    if instrument == 'perp':
                        fee = 7 / 10000 * 2
                        if funding_1h is not None:
                            hold_start = current_trade.entry_time
                            hold_end = ts
                            funding_slice = funding_1h.loc[hold_start:hold_end]
                            funding_cost = funding_slice.sum()
                            pnl -= abs(funding_cost)
                    pnl -= fee
                    current_trade.exit_time = ts
                    current_trade.exit_price = exit_price
                    current_trade.pnl_pct = pnl
                    current_trade.bars_held = entry_bar_count
                    trades.append(current_trade)
                    in_trade = False
                    current_trade = None
                    trailing_stop = None
                    entry_bar_count = 0

        if not in_trade and prev_rsi is not None and not np.isnan(prev_rsi):
            # Long signal: uptrend + RSI was below threshold, now above
            if is_uptrend and prev_rsi < rsi_long_thresh and current_rsi >= rsi_long_thresh:
                current_trade = Trade(
                    entry_time=ts,
                    entry_price=current_close,
                    direction='long'
                )
                trailing_stop = current_close - atr_multiplier * current_atr
                in_trade = True
                entry_bar_count = 0

            # Short signal: downtrend + RSI was above threshold, now below
            elif not is_uptrend and prev_rsi > rsi_short_thresh and current_rsi <= rsi_short_thresh:
                current_trade = Trade(
                    entry_time=ts,
                    entry_price=current_close,
                    direction='short'
                )
                trailing_stop = current_close + atr_multiplier * current_atr
                in_trade = True
                entry_bar_count = 0

        prev_rsi = current_rsi

    # Close any open trade at end of period
    if in_trade and current_trade is not None:
        last_close = close_4h.loc[:test_end].iloc[-1]
        if current_trade.direction == 'long':
            pnl = (last_close / current_trade.entry_price - 1)
        else:
            pnl = (current_trade.entry_price / last_close - 1)
        fee = fee_bps / 10000 * 2
        if instrument == 'perp':
            fee = 7 / 10000 * 2
        pnl -= fee
        current_trade.exit_time = test_end
        current_trade.exit_price = last_close
        current_trade.pnl_pct = pnl
        current_trade.bars_held = entry_bar_count
        trades.append(current_trade)

    return trades


# ============================================================
# METRICS COMPUTATION
# ============================================================

@dataclass
class WindowMetrics:
    window: str
    asset: str
    side: str  # 'long', 'short', 'combined'
    instrument: str
    trade_count: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_pnl_pct: float = 0.0
    total_pnl_pct: float = 0.0
    sharpe: float = 0.0
    max_dd_pct: float = 0.0
    avg_bars_held: float = 0.0


def compute_metrics(trades: List[Trade], window: str, asset: str, side: str, instrument: str) -> WindowMetrics:
    m = WindowMetrics(window=window, asset=asset, side=side, instrument=instrument)

    if not trades:
        return m

    pnls = [t.pnl_pct for t in trades]
    m.trade_count = len(trades)
    m.win_rate = sum(1 for p in pnls if p > 0) / len(pnls)

    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    m.profit_factor = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    m.avg_pnl_pct = np.mean(pnls)
    m.total_pnl_pct = np.sum(pnls)

    # Sharpe: annualize assuming average hold ~ 2 days, so ~180 trades/year equivalent
    # Actually compute from equity curve
    equity = np.cumprod(1 + np.array(pnls))
    returns = np.diff(np.log(equity))
    if len(returns) > 1 and np.std(returns) > 0:
        # Annualize: assume avg trade duration
        avg_hold_hours = np.mean([t.bars_held * 4 for t in trades])
        trades_per_year = 8760 / max(avg_hold_hours, 4)
        m.sharpe = (np.mean(returns) / np.std(returns)) * np.sqrt(trades_per_year)
    elif len(pnls) > 1 and np.std(pnls) > 0:
        avg_hold_hours = np.mean([t.bars_held * 4 for t in trades])
        trades_per_year = 8760 / max(avg_hold_hours, 4)
        m.sharpe = (np.mean(pnls) / np.std(pnls)) * np.sqrt(trades_per_year)

    m.avg_bars_held = np.mean([t.bars_held for t in trades])

    # Max drawdown from equity curve
    equity = np.cumprod(1 + np.array(pnls))
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    m.max_dd_pct = abs(dd.min()) * 100

    return m


# ============================================================
# WALK-FORWARD WINDOWS
# ============================================================

WINDOWS = [
    ('W1',  '2020-01-01', '2020-12-31', '2021-01-01', '2021-06-30'),
    ('W2',  '2020-07-01', '2021-06-30', '2021-07-01', '2021-12-31'),
    ('W3',  '2021-01-01', '2021-12-31', '2022-01-01', '2022-06-30'),
    ('W4',  '2021-07-01', '2022-06-30', '2022-07-01', '2022-12-31'),
    ('W5',  '2022-01-01', '2022-12-31', '2023-01-01', '2023-06-30'),
    ('W6',  '2022-07-01', '2023-06-30', '2023-07-01', '2023-12-31'),
    ('W7',  '2023-01-01', '2023-12-31', '2024-01-01', '2024-06-30'),
    ('W8',  '2023-07-01', '2024-06-30', '2024-07-01', '2024-12-31'),
    ('W9',  '2024-01-01', '2024-12-31', '2025-01-01', '2025-06-30'),
    ('W10', '2024-07-01', '2025-06-30', '2025-07-01', '2025-12-31'),
]


# ============================================================
# MAIN EXECUTION
# ============================================================

def run_walkforward():
    print("Loading data...")
    btc_spot = load_data('BTC_spot')
    eth_spot = load_data('ETH_spot')
    btc_perp = load_data('BTC_perp')
    eth_perp = load_data('ETH_perp')

    datasets = {
        'BTC': {'spot': btc_spot, 'perp': btc_perp},
        'ETH': {'spot': eth_spot, 'perp': eth_perp},
    }

    all_metrics = []
    all_trades = {}  # key: (asset, window, instrument)

    for asset in ['BTC', 'ETH']:
        spot_df = datasets[asset]['spot']
        perp_df = datasets[asset]['perp']

        for wi, (wname, train_start, train_end, test_start, test_end) in enumerate(WINDOWS):
            print(f"  {asset} {wname}: test {test_start} to {test_end}")

            # Check data availability
            actual_end = min(pd.Timestamp(test_end), spot_df.index.max())
            if pd.Timestamp(test_start) > spot_df.index.max():
                print(f"    SKIP: no data for test period")
                continue

            # Run on spot
            trades_spot = generate_signals_and_trade(
                spot_df, test_start, str(actual_end),
                rsi_long_thresh=40.0, rsi_short_thresh=60.0,
                fee_bps=10.0, instrument='spot'
            )

            # Run on perp
            perp_end = min(pd.Timestamp(test_end), perp_df.index.max())
            trades_perp = generate_signals_and_trade(
                perp_df, test_start, str(perp_end),
                rsi_long_thresh=40.0, rsi_short_thresh=60.0,
                fee_bps=7.0, instrument='perp', df_perp_1h=perp_df
            )

            for instrument, trades in [('spot', trades_spot), ('perp', trades_perp)]:
                all_trades[(asset, wname, instrument)] = trades

                long_trades = [t for t in trades if t.direction == 'long']
                short_trades = [t for t in trades if t.direction == 'short']

                m_long = compute_metrics(long_trades, wname, asset, 'long', instrument)
                m_short = compute_metrics(short_trades, wname, asset, 'short', instrument)
                m_combined = compute_metrics(trades, wname, asset, 'combined', instrument)

                all_metrics.extend([m_long, m_short, m_combined])

    return all_metrics, all_trades, datasets


def run_parameter_sensitivity(datasets):
    """Test RSI thresholds at 35/45 and 55/65 vs base 40/60."""
    print("\n--- Parameter Sensitivity ---")
    param_sets = {
        'base_40_60': (40.0, 60.0),
        'tight_35_65': (35.0, 65.0),
        'wide_45_55': (45.0, 55.0),
    }

    results = {}
    for pname, (long_thresh, short_thresh) in param_sets.items():
        print(f"  Testing {pname} (long<{long_thresh}, short>{short_thresh})")
        total_pnl = {'BTC': 0.0, 'ETH': 0.0}
        trade_counts = {'BTC': 0, 'ETH': 0}
        sharpes = {'BTC': [], 'ETH': []}

        for asset in ['BTC', 'ETH']:
            spot_df = datasets[asset]['spot']

            for wname, train_start, train_end, test_start, test_end in WINDOWS:
                actual_end = min(pd.Timestamp(test_end), spot_df.index.max())
                if pd.Timestamp(test_start) > spot_df.index.max():
                    continue

                trades = generate_signals_and_trade(
                    spot_df, test_start, str(actual_end),
                    rsi_long_thresh=long_thresh, rsi_short_thresh=short_thresh,
                    fee_bps=10.0, instrument='spot'
                )

                m = compute_metrics(trades, wname, asset, 'combined', 'spot')
                total_pnl[asset] += m.total_pnl_pct
                trade_counts[asset] += m.trade_count
                if m.trade_count > 0:
                    sharpes[asset].append(m.sharpe)

        results[pname] = {
            'total_pnl': total_pnl,
            'trade_counts': trade_counts,
            'mean_sharpe': {a: np.mean(s) if s else 0.0 for a, s in sharpes.items()},
        }

    return results


def regime_analysis(datasets, all_trades):
    """Classify performance by BTC regime: uptrend, downtrend, range."""
    print("\n--- Regime Analysis ---")
    btc_spot = datasets['BTC']['spot']
    daily_emas = compute_daily_emas(btc_spot)

    # Classify regimes based on 50d EMA slope
    # Uptrend: slope > 0.5% over 5 days
    # Downtrend: slope < -0.5%
    # Range: between
    regime = pd.Series(index=daily_emas.index, dtype='object')
    regime[daily_emas['ema50_slope'] > 0.005] = 'uptrend'
    regime[daily_emas['ema50_slope'] < -0.005] = 'downtrend'
    regime = regime.fillna('range')

    results = {}
    for asset in ['BTC', 'ETH']:
        results[asset] = {'uptrend': [], 'downtrend': [], 'range': []}

        for wname, _, _, test_start, test_end in WINDOWS:
            key = (asset, wname, 'spot')
            if key not in all_trades:
                continue
            trades = all_trades[key]

            for t in trades:
                trade_date = t.entry_time.normalize()
                # Find regime for this trade
                regime_dates = regime.index[regime.index <= trade_date]
                if len(regime_dates) == 0:
                    continue
                trade_regime = regime.loc[regime_dates[-1]]
                results[asset][trade_regime].append(t.pnl_pct)

    return results


def build_combined_equity(all_trades, assets=['BTC', 'ETH']):
    """Build concatenated equity curve across all test windows (no overlap)."""
    # Use non-overlapping windows: W1, W3, W5, W7, W9 (even spacing)
    # Actually per spec: concatenate all test windows. But windows overlap in training.
    # Test periods: W1:Jan-Jun21, W2:Jul-Dec21, W3:Jan-Jun22, etc.
    # These are actually non-overlapping test periods already!

    equity_data = {}
    for asset in assets:
        all_pnls = []
        for wname, _, _, test_start, test_end in WINDOWS:
            key = (asset, wname, 'spot')
            if key not in all_trades:
                continue
            trades = all_trades[key]
            # Sort trades by entry time
            trades_sorted = sorted(trades, key=lambda t: t.entry_time)
            for t in trades_sorted:
                all_pnls.append((t.entry_time, t.pnl_pct, t.direction))

        # Build equity
        if all_pnls:
            all_pnls.sort(key=lambda x: x[0])
            equity = [1.0]
            times = [all_pnls[0][0]]
            for ts, pnl, direction in all_pnls:
                equity.append(equity[-1] * (1 + pnl))
                times.append(ts)
            equity_data[asset] = {
                'times': times,
                'equity': equity,
                'total_return': equity[-1] / equity[0] - 1,
                'max_dd': abs(min((e - max(equity[:i+1])) / max(equity[:i+1])
                               for i, e in enumerate(equity) if max(equity[:i+1]) > 0)),
            }
            # Compute combined sharpe from trade-level returns
            rets = [p for _, p, _ in all_pnls]
            if len(rets) > 1 and np.std(rets) > 0:
                # Approx annualization
                equity_data[asset]['sharpe'] = (np.mean(rets) / np.std(rets)) * np.sqrt(len(rets) / 5)  # ~5 years
            else:
                equity_data[asset]['sharpe'] = 0.0

    return equity_data


def monthly_trade_frequency(all_trades):
    """Compute average trades per month across all windows."""
    results = {}
    for asset in ['BTC', 'ETH']:
        total_trades = 0
        total_months = 0
        for wname, _, _, test_start, test_end in WINDOWS:
            key = (asset, wname, 'spot')
            if key not in all_trades:
                continue
            trades = all_trades[key]
            total_trades += len(trades)
            months = (pd.Timestamp(test_end) - pd.Timestamp(test_start)).days / 30.44
            total_months += months

        results[asset] = total_trades / max(total_months, 1)
    return results


# ============================================================
# KILL CRITERIA EVALUATION
# ============================================================

def evaluate_kill_criteria(all_metrics, all_trades):
    """Evaluate kill criteria and return verdict."""
    verdicts = {}
    flags = []

    for asset in ['BTC', 'ETH']:
        for instrument in ['spot', 'perp']:
            prefix = f"{asset}_{instrument}"

            # Combined metrics per window
            combined = [m for m in all_metrics
                       if m.asset == asset and m.instrument == instrument and m.side == 'combined' and m.trade_count > 0]
            long_metrics = [m for m in all_metrics
                          if m.asset == asset and m.instrument == instrument and m.side == 'long' and m.trade_count > 0]
            short_metrics = [m for m in all_metrics
                           if m.asset == asset and m.instrument == instrument and m.side == 'short' and m.trade_count > 0]

            # Kill criterion 1: Less than 5/10 windows positive PF
            positive_pf = sum(1 for m in combined if m.profit_factor > 1.0)
            total_windows = len(combined)
            verdicts[f"{prefix}_positive_pf"] = f"{positive_pf}/{total_windows}"
            if positive_pf < 5:
                flags.append(f"KILL: {prefix} only {positive_pf}/{total_windows} windows with PF>1")

            # Kill criterion 2: Any window with MaxDD > 30%
            for m in combined:
                if m.max_dd_pct > 30:
                    flags.append(f"FLAG: {prefix} {m.window} MaxDD={m.max_dd_pct:.1f}%")

            # Kill criterion 3: Mean OOS Sharpe < 0.5
            sharpes = [m.sharpe for m in combined]
            mean_sharpe = np.mean(sharpes) if sharpes else 0.0
            verdicts[f"{prefix}_mean_sharpe"] = f"{mean_sharpe:.2f}"
            if mean_sharpe < 0.5:
                flags.append(f"KILL: {prefix} mean OOS Sharpe={mean_sharpe:.2f} < 0.5")

            # Kill criterion 4: Short side - less than 4/10 windows PF > 1.0
            short_positive_pf = sum(1 for m in short_metrics if m.profit_factor > 1.0)
            short_total = len(short_metrics)
            verdicts[f"{prefix}_short_pf"] = f"{short_positive_pf}/{short_total}"
            if short_positive_pf < 4:
                flags.append(f"KILL SHORT: {prefix} short side only {short_positive_pf}/{short_total} windows with PF>1")

    return verdicts, flags


# ============================================================
# REPORT GENERATION
# ============================================================

def generate_report(all_metrics, all_trades, datasets, param_sensitivity, regime_results,
                    equity_data, monthly_freq, verdicts, flags):
    """Generate the markdown report."""

    lines = []
    lines.append("# Trend + Pullback Walk-Forward Validation Results")
    lines.append("")
    lines.append("**Signal**: Daily 20d EMA > 50d EMA (trend) + 4h RSI(14) pullback cross")
    lines.append("**Protocol**: 10 rolling windows, 12m train / 6m test")
    lines.append(f"**Date**: {pd.Timestamp.now().strftime('%Y-%m-%d')}")
    lines.append("")

    # ---- Per-window metrics table ----
    for asset in ['BTC', 'ETH']:
        for instrument in ['spot', 'perp']:
            lines.append(f"## {asset} {instrument.upper()} - Per-Window Metrics")
            lines.append("")

            # Combined
            lines.append("### Combined (Long + Short)")
            lines.append("")
            lines.append("| Window | Trades | Win Rate | PF | Avg PnL | Total PnL | Sharpe | MaxDD |")
            lines.append("|--------|--------|----------|-----|---------|-----------|--------|-------|")

            for wname, _, _, ts, te in WINDOWS:
                m = [x for x in all_metrics
                     if x.window == wname and x.asset == asset and x.instrument == instrument and x.side == 'combined']
                if m:
                    m = m[0]
                    lines.append(f"| {wname} ({ts[:7]} to {te[:7]}) | {m.trade_count} | {m.win_rate:.1%} | {m.profit_factor:.2f} | {m.avg_pnl_pct:.3%} | {m.total_pnl_pct:.2%} | {m.sharpe:.2f} | {m.max_dd_pct:.1f}% |")
                else:
                    lines.append(f"| {wname} ({ts[:7]} to {te[:7]}) | - | - | - | - | - | - | - |")

            lines.append("")

            # Long side
            lines.append("### Long Side Only")
            lines.append("")
            lines.append("| Window | Trades | Win Rate | PF | Avg PnL | Total PnL | Sharpe | MaxDD |")
            lines.append("|--------|--------|----------|-----|---------|-----------|--------|-------|")

            for wname, _, _, ts, te in WINDOWS:
                m = [x for x in all_metrics
                     if x.window == wname and x.asset == asset and x.instrument == instrument and x.side == 'long']
                if m:
                    m = m[0]
                    if m.trade_count > 0:
                        lines.append(f"| {wname} | {m.trade_count} | {m.win_rate:.1%} | {m.profit_factor:.2f} | {m.avg_pnl_pct:.3%} | {m.total_pnl_pct:.2%} | {m.sharpe:.2f} | {m.max_dd_pct:.1f}% |")
                    else:
                        lines.append(f"| {wname} | 0 | - | - | - | - | - | - |")

            lines.append("")

            # Short side
            lines.append("### Short Side Only")
            lines.append("")
            lines.append("| Window | Trades | Win Rate | PF | Avg PnL | Total PnL | Sharpe | MaxDD |")
            lines.append("|--------|--------|----------|-----|---------|-----------|--------|-------|")

            for wname, _, _, ts, te in WINDOWS:
                m = [x for x in all_metrics
                     if x.window == wname and x.asset == asset and x.instrument == instrument and x.side == 'short']
                if m:
                    m = m[0]
                    if m.trade_count > 0:
                        lines.append(f"| {wname} | {m.trade_count} | {m.win_rate:.1%} | {m.profit_factor:.2f} | {m.avg_pnl_pct:.3%} | {m.total_pnl_pct:.2%} | {m.sharpe:.2f} | {m.max_dd_pct:.1f}% |")
                    else:
                        lines.append(f"| {wname} | 0 | - | - | - | - | - | - |")

            lines.append("")

    # ---- Combined Equity Curve Stats ----
    lines.append("## Combined Equity Curve (All Test Windows Concatenated)")
    lines.append("")
    for asset in ['BTC', 'ETH']:
        if asset in equity_data:
            ed = equity_data[asset]
            lines.append(f"### {asset}")
            lines.append(f"- Total Return: {ed['total_return']:.2%}")
            lines.append(f"- Max Drawdown: {ed['max_dd']:.2%}")
            lines.append(f"- Sharpe Ratio: {ed.get('sharpe', 0):.2f}")
            lines.append("")

    # ---- Regime Analysis ----
    lines.append("## Regime Analysis")
    lines.append("")
    lines.append("Regime classified by BTC 50d EMA 5-day slope: uptrend (>0.5%), downtrend (<-0.5%), range.")
    lines.append("")
    lines.append("| Asset | Regime | Trades | Win Rate | Avg PnL | Total PnL |")
    lines.append("|-------|--------|--------|----------|---------|-----------|")

    for asset in ['BTC', 'ETH']:
        if asset in regime_results:
            for regime in ['uptrend', 'downtrend', 'range']:
                pnls = regime_results[asset][regime]
                if pnls:
                    wr = sum(1 for p in pnls if p > 0) / len(pnls)
                    lines.append(f"| {asset} | {regime} | {len(pnls)} | {wr:.1%} | {np.mean(pnls):.3%} | {np.sum(pnls):.2%} |")
                else:
                    lines.append(f"| {asset} | {regime} | 0 | - | - | - |")

    lines.append("")

    # ---- Parameter Sensitivity ----
    lines.append("## Parameter Sensitivity")
    lines.append("")
    lines.append("Testing RSI threshold variants against base (40/60):")
    lines.append("")
    lines.append("| Params | BTC Trades | BTC Total PnL | BTC Sharpe | ETH Trades | ETH Total PnL | ETH Sharpe |")
    lines.append("|--------|-----------|---------------|------------|-----------|---------------|------------|")

    base_pnl = {}
    for pname, data in param_sensitivity.items():
        btc_pnl = data['total_pnl']['BTC']
        eth_pnl = data['total_pnl']['ETH']
        if pname == 'base_40_60':
            base_pnl = {'BTC': btc_pnl, 'ETH': eth_pnl}
        lines.append(f"| {pname} | {data['trade_counts']['BTC']} | {btc_pnl:.2%} | {data['mean_sharpe']['BTC']:.2f} | {data['trade_counts']['ETH']} | {eth_pnl:.2%} | {data['mean_sharpe']['ETH']:.2f} |")

    lines.append("")

    # Degradation check
    if base_pnl:
        lines.append("### Degradation Check (>30% drop = fragile)")
        lines.append("")
        for pname, data in param_sensitivity.items():
            if pname == 'base_40_60':
                continue
            for asset in ['BTC', 'ETH']:
                bp = base_pnl[asset]
                pp = data['total_pnl'][asset]
                if bp != 0:
                    degrade = (bp - pp) / abs(bp) * 100
                    status = "FRAGILE" if degrade > 30 else "OK"
                    lines.append(f"- {pname} {asset}: {degrade:+.1f}% change -- **{status}**")
                else:
                    lines.append(f"- {pname} {asset}: base PnL is 0, cannot compute degradation")
        lines.append("")

    # ---- Monthly Trade Frequency ----
    lines.append("## Monthly Trade Frequency")
    lines.append("")
    for asset in ['BTC', 'ETH']:
        if asset in monthly_freq:
            lines.append(f"- {asset}: {monthly_freq[asset]:.1f} trades/month")
    lines.append("")

    # ---- Kill Criteria Assessment ----
    lines.append("## Kill Criteria Assessment")
    lines.append("")
    lines.append("| Criterion | Threshold | Result | Status |")
    lines.append("|-----------|-----------|--------|--------|")

    for asset in ['BTC', 'ETH']:
        for instrument in ['spot', 'perp']:
            prefix = f"{asset}_{instrument}"
            pf_val = verdicts.get(f"{prefix}_positive_pf", "N/A")
            sharpe_val = verdicts.get(f"{prefix}_mean_sharpe", "N/A")
            short_pf_val = verdicts.get(f"{prefix}_short_pf", "N/A")

            # Parse PF check
            try:
                pf_num = int(pf_val.split('/')[0])
                pf_status = "PASS" if pf_num >= 5 else "KILL"
            except:
                pf_status = "N/A"

            try:
                sharpe_num = float(sharpe_val)
                sharpe_status = "PASS" if sharpe_num >= 0.5 else "KILL"
            except:
                sharpe_status = "N/A"

            try:
                short_num = int(short_pf_val.split('/')[0])
                short_status = "PASS" if short_num >= 4 else "KILL SHORT"
            except:
                short_status = "N/A"

            lines.append(f"| {prefix} PF>1 windows | >=5/10 | {pf_val} | {pf_status} |")
            lines.append(f"| {prefix} mean Sharpe | >=0.5 | {sharpe_val} | {sharpe_status} |")
            lines.append(f"| {prefix} short PF>1 | >=4/10 | {short_pf_val} | {short_status} |")

    lines.append("")

    # MaxDD flags
    dd_flags = [f for f in flags if 'MaxDD' in f]
    if dd_flags:
        lines.append("### MaxDD Flags (>30%)")
        lines.append("")
        for f in dd_flags:
            lines.append(f"- {f}")
        lines.append("")

    # ---- All flags ----
    lines.append("## All Flags and Kill Signals")
    lines.append("")
    if flags:
        for f in flags:
            lines.append(f"- {f}")
    else:
        lines.append("- No flags triggered.")
    lines.append("")

    # ---- Final Verdict ----
    kill_signals = [f for f in flags if f.startswith("KILL:")]
    kill_short_signals = [f for f in flags if f.startswith("KILL SHORT:")]
    flag_signals = [f for f in flags if f.startswith("FLAG:")]

    lines.append("## Final Verdict")
    lines.append("")

    if kill_signals:
        lines.append("### **KILL**")
        lines.append("")
        lines.append("The strategy fails fundamental kill criteria:")
        for k in kill_signals:
            lines.append(f"- {k}")
        if kill_short_signals:
            lines.append("")
            lines.append("Additionally, the short side is weak:")
            for k in kill_short_signals:
                lines.append(f"- {k}")
    elif kill_short_signals and not kill_signals:
        lines.append("### **NEEDS_TUNING**")
        lines.append("")
        lines.append("The combined strategy passes core criteria but the short side is weak:")
        for k in kill_short_signals:
            lines.append(f"- {k}")
        lines.append("")
        lines.append("Recommendation: Consider running long-only or tightening short entry criteria.")
    elif flag_signals:
        lines.append("### **NEEDS_TUNING**")
        lines.append("")
        lines.append("The strategy passes kill criteria but has drawdown flags:")
        for f in flag_signals:
            lines.append(f"- {f}")
    else:
        lines.append("### **DEPLOY**")
        lines.append("")
        lines.append("All kill criteria passed. Strategy is viable for deployment.")

    lines.append("")

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

if __name__ == '__main__':
    print("=" * 60)
    print("Trend + Pullback Walk-Forward Validation")
    print("=" * 60)

    # Run main walk-forward
    all_metrics, all_trades, datasets = run_walkforward()

    # Parameter sensitivity
    param_sensitivity = run_parameter_sensitivity(datasets)

    # Regime analysis
    regime_results = regime_analysis(datasets, all_trades)

    # Combined equity curve
    equity_data = build_combined_equity(all_trades)

    # Monthly frequency
    monthly_freq = monthly_trade_frequency(all_trades)

    # Kill criteria
    verdicts, flags = evaluate_kill_criteria(all_metrics, all_trades)

    # Generate report
    report = generate_report(
        all_metrics, all_trades, datasets, param_sensitivity,
        regime_results, equity_data, monthly_freq, verdicts, flags
    )

    output_path = '/workspace/crypto_backtest/research/trend_pullback_walkforward_results.md'
    with open(output_path, 'w') as f:
        f.write(report)

    print(f"\nReport written to {output_path}")
    print("\n--- Quick Summary ---")
    print(f"Flags: {len(flags)}")
    for f in flags:
        print(f"  {f}")

    # Print combined equity stats
    print("\nCombined Equity:")
    for asset in ['BTC', 'ETH']:
        if asset in equity_data:
            ed = equity_data[asset]
            print(f"  {asset}: return={ed['total_return']:.2%}, maxDD={ed['max_dd']:.2%}, sharpe={ed.get('sharpe', 0):.2f}")

    # Print monthly freq
    print("\nMonthly Trade Frequency:")
    for asset, freq in monthly_freq.items():
        print(f"  {asset}: {freq:.1f} trades/month")
