"""
V3 Leveraged Futures Test — BTC Perpetual Futures with 1x, 2x, 3x Leverage
============================================================================

Tests whether the validated V3 momentum strategy (20/50 EMA + Positioning + VRP)
can be profitably run on perpetual futures with moderate leverage.

Key additions over spot V3:
  - Funding rate costs (actual Binance rates, 8h settlement)
  - Leverage-amplified returns and drawdowns
  - Liquidation risk tracking
  - Trading cost scaling with leverage

Baseline (spot V3 OOS): Sharpe 0.56, Return +17.52%, MaxDD -20.2%

Kill criteria:
  - MaxDD > 40%
  - Multiple liquidation events
  - Sharpe < 0.3
  - Funding drag > 50% of gross return
"""

import json
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

# =============================================================================
# Configuration
# =============================================================================

# EMA crossover parameters (daily bars)
FAST_EMA = 20
SLOW_EMA = 50

# Positioning overlay parameters
POS_Z_WINDOW = 30
POS_HIGH_THRESH = 1.5
POS_MID_THRESH = 0.5

# VRP overlay parameters
VRP_Z_WINDOW = 60
VRP_HIGH_THRESH = 1.0
VRP_MID_LOW = -0.5
VRP_EXTREME_LOW = -1.5

# Trade management
REBALANCE_BARS = 168    # Weekly rebalance (7 * 24h)
WARMUP_DAILY = 90       # Days of burn-in
WARMUP_BARS = WARMUP_DAILY * 24

# Position limits
MIN_POSITION = 0.0
MAX_POSITION = 1.5

# Leverage levels to test
LEVERAGE_LEVELS = [1.0, 2.0, 3.0]

# Cost parameters
TRADING_COST_BPS = 10   # 10 bps per trade (taker fee on perps)
DEFAULT_FUNDING_RATE_8H = 0.0001  # 0.01% per 8h fallback

# IS/OOS split
IS_FRACTION = 0.70
OOS_FRACTION = 0.30

# Data paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PERP_PATH = PROJECT_ROOT / 'data' / 'perp' / '1h_cache' / 'BTC_1h.parquet'
SPOT_PATH = PROJECT_ROOT / 'data' / 'spot' / '1h_cache' / 'BTC_1h.parquet'
POS_PATH = PROJECT_ROOT / 'data' / 'alternative' / 'binance_metrics' / 'all_symbols_daily_ls.parquet'
DVOL_PATH = PROJECT_ROOT / 'data' / 'alternative' / 'deribit_options' / 'dvol' / 'btc_dvol_daily.json'
FUNDING_CSV_PATH = PROJECT_ROOT / 'data' / 'perp' / 'binance' / 'funding' / 'BTC_funding.csv'


# =============================================================================
# Helper Functions
# =============================================================================

def rolling_zscore(arr, window):
    """Rolling z-score: (x - rolling_mean) / rolling_std."""
    s = pd.Series(arr)
    mean = s.rolling(window, min_periods=window).mean()
    std = s.rolling(window, min_periods=window).std()
    z = ((s - mean) / std.replace(0, np.nan)).values
    return z


def rolling_std(arr, window):
    """Rolling standard deviation."""
    s = pd.Series(arr)
    return s.rolling(window, min_periods=window).std().values


# =============================================================================
# Data Loading
# =============================================================================

def load_perp_data():
    """Load BTC perpetual futures 1H OHLCV data."""
    df = pd.read_parquet(PERP_PATH)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    return df


def load_spot_data():
    """Load BTC spot 1H OHLCV data."""
    df = pd.read_parquet(SPOT_PATH)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    return df


def load_funding_rates():
    """Load Binance BTC funding rate history (8h intervals).
    Returns Series indexed by datetime with funding rate values."""
    funding = pd.read_csv(FUNDING_CSV_PATH)
    funding['datetime'] = pd.to_datetime(funding['datetime'])
    funding = funding.set_index('datetime').sort_index()
    funding.index = funding.index.tz_localize(None)
    return funding['funding_rate']


def load_positioning():
    """Load Binance positioning data for BTCUSDT."""
    if not POS_PATH.exists():
        return pd.DataFrame()

    pos = pd.read_parquet(POS_PATH)
    pos = pos[pos['symbol'] == 'BTCUSDT'].copy()
    pos['date'] = pd.to_datetime(pos['date'])
    pos = pos.set_index('date').sort_index()
    pos = pos[['sum_toptrader_ls_ratio', 'count_toptrader_ls_ratio', 'count_ls_ratio']].copy()
    pos = pos[~pos.index.duplicated(keep='last')]
    return pos


def load_dvol():
    """Load BTC DVOL from Deribit JSON."""
    if not DVOL_PATH.exists():
        return pd.Series(dtype=float)

    with open(DVOL_PATH) as f:
        data = json.load(f)

    records = []
    for row in data:
        ts = pd.Timestamp(row[0], unit='ms')
        records.append({'date': ts, 'dvol_close': row[4]})

    dvol = pd.DataFrame(records).set_index('date').sort_index()
    dvol = dvol[~dvol.index.duplicated(keep='last')]
    return dvol['dvol_close']


# =============================================================================
# Signal Construction (replicated from V3 prototype)
# =============================================================================

def build_ema_base_signal(daily_close):
    """Base trend signal: long when fast EMA > slow EMA."""
    fast_ema = pd.Series(daily_close).ewm(span=FAST_EMA, adjust=False).mean().values
    slow_ema = pd.Series(daily_close).ewm(span=SLOW_EMA, adjust=False).mean().values
    base = np.where(fast_ema > slow_ema, 1.0, 0.0)
    base[:SLOW_EMA] = 0.0
    return base


def build_positioning_multiplier(daily_idx, positioning):
    """Positioning overlay: combined z-score -> sizing multiplier."""
    n = len(daily_idx)
    if positioning.empty:
        return np.ones(n, dtype=np.float64)

    pos = positioning.reindex(daily_idx).ffill()
    toptrader_ls = pos['sum_toptrader_ls_ratio'].values.astype(np.float64)
    count_toptrader = pos['count_toptrader_ls_ratio'].values.astype(np.float64)
    count_ls = pos['count_ls_ratio'].values.astype(np.float64)
    divergence = count_toptrader - count_ls

    z_toptrader = rolling_zscore(toptrader_ls, POS_Z_WINDOW)
    z_divergence = rolling_zscore(divergence, POS_Z_WINDOW)
    combined_z = (z_toptrader + z_divergence) / 2.0

    multiplier = np.where(
        combined_z > POS_HIGH_THRESH, 0.3,
        np.where(combined_z > POS_MID_THRESH, 0.5,
                 np.where(combined_z > -POS_MID_THRESH, 1.0,
                          np.where(combined_z > -POS_HIGH_THRESH, 1.3,
                                   1.5))))
    multiplier = np.where(np.isnan(combined_z), 1.0, multiplier)
    return multiplier


def build_vrp_multiplier(daily_close, daily_idx, dvol):
    """VRP overlay: (IV - RV) z-score -> sizing multiplier."""
    n = len(daily_close)
    log_ret = np.zeros(n, dtype=np.float64)
    log_ret[1:] = np.log(daily_close[1:] / np.maximum(daily_close[:-1], 1e-10))
    rv_20d = rolling_std(log_ret, 20) * np.sqrt(365) * 100

    if dvol.empty or len(dvol) < 30:
        rv_90d = rolling_std(log_ret, 90) * np.sqrt(365) * 100
        iv = rv_90d * 1.2
    else:
        iv_series = dvol.reindex(daily_idx).ffill()
        iv = iv_series.values.astype(np.float64)

    vrp = iv - rv_20d
    vrp_z = rolling_zscore(vrp, VRP_Z_WINDOW)

    multiplier = np.where(
        vrp_z > VRP_HIGH_THRESH, 1.3,
        np.where(vrp_z > VRP_MID_LOW, 1.0,
                 np.where(vrp_z > VRP_EXTREME_LOW, 0.5,
                          0.3)))
    multiplier = np.where(np.isnan(vrp_z), 1.0, multiplier)
    return multiplier


# =============================================================================
# Build Hourly Signal from Daily Overlays
# =============================================================================

def build_hourly_signal(hourly_df):
    """Construct the full V3 signal on hourly bars, returning position sizing array."""
    # Resample to daily
    daily = hourly_df['close'].resample('1D').last().dropna()
    daily_close = daily.values
    daily_idx = daily.index
    n_daily = len(daily_close)
    n_hourly = len(hourly_df)

    if n_daily < WARMUP_DAILY:
        return np.zeros(n_hourly, dtype=np.float64)

    # Load overlay data
    positioning = load_positioning()
    dvol = load_dvol()

    # Build daily signals
    base_signal = build_ema_base_signal(daily_close)
    pos_multiplier = build_positioning_multiplier(daily_idx, positioning)
    vrp_multiplier = build_vrp_multiplier(daily_close, daily_idx, dvol)

    # Compose final daily position
    final_daily = np.clip(base_signal * pos_multiplier * vrp_multiplier,
                          MIN_POSITION, MAX_POSITION)

    # Align daily signal to hourly bars via forward-fill
    daily_signal = pd.Series(final_daily, index=daily_idx)
    hourly_signal = daily_signal.reindex(hourly_df.index, method='ffill').values

    # Apply weekly rebalance gates: only update signal at rebalance points
    rebalanced = np.zeros(n_hourly, dtype=np.float64)
    current_pos = 0.0
    for i in range(n_hourly):
        if i < WARMUP_BARS:
            rebalanced[i] = 0.0
            continue
        if (i - WARMUP_BARS) % REBALANCE_BARS == 0:
            # Rebalance point: adopt the current signal
            current_pos = hourly_signal[i] if not np.isnan(hourly_signal[i]) else 0.0
        rebalanced[i] = current_pos

    return rebalanced


# =============================================================================
# Funding Rate Processing
# =============================================================================

def build_hourly_funding(hourly_index, funding_rates):
    """
    Build hourly funding cost series.
    Funding is settled every 8 hours. We spread the cost across the 8 hours.
    Returns: Series of per-hour funding rate (rate / 8 per hour).
    """
    # Reindex funding to hourly, forward-fill (funding rate applies until next settlement)
    hourly_funding = funding_rates.reindex(hourly_index, method='ffill')

    # Where we have no data, use default rate
    hourly_funding = hourly_funding.fillna(DEFAULT_FUNDING_RATE_8H)

    # Convert from 8h rate to per-hour cost
    # Funding is charged every 8h, so hourly cost = rate / 8
    hourly_funding_cost = hourly_funding / 8.0

    return hourly_funding_cost


# =============================================================================
# Backtest Engine
# =============================================================================

def run_leveraged_backtest(hourly_df, signal, hourly_funding, leverage):
    """
    Run a leveraged backtest for V3 on BTC perps.

    Parameters:
    -----------
    hourly_df : DataFrame with OHLCV perp data
    signal : array of position sizes (0 to 1.5) at each hourly bar
    hourly_funding : Series of per-hour funding rate
    leverage : float, leverage multiplier (1x, 2x, 3x)

    Returns:
    --------
    dict with equity curve, metrics, trade log
    """
    n = len(hourly_df)
    close = hourly_df['close'].values

    # Returns
    hourly_returns = np.zeros(n)
    hourly_returns[1:] = close[1:] / close[:-1] - 1.0

    # Funding costs per hour
    funding_arr = hourly_funding.values

    # Track equity
    equity = np.ones(n, dtype=np.float64)  # Start at 1.0

    # Track components
    gross_returns = np.zeros(n)
    funding_costs = np.zeros(n)
    trading_costs = np.zeros(n)
    net_returns = np.zeros(n)

    # Track position changes for trading costs
    prev_signal = 0.0

    # Track liquidation events
    liquidation_events = []
    near_liquidation_events = []

    # Liquidation threshold: if cumulative unrealized loss exceeds 1/leverage
    # For 2x: -50% equity = liquidation. For 3x: -33% equity = liquidation.
    liquidation_threshold = 1.0 / leverage if leverage > 1.0 else 1.0

    # Track trade-level drawdown from entry
    trade_entry_equity = 1.0
    in_trade = False

    for i in range(1, n):
        pos = signal[i - 1]  # Position from previous bar (held during this bar)

        # Gross return from position * leverage * market return
        gross_ret = pos * leverage * hourly_returns[i]

        # Funding cost: paid on leveraged notional when in position
        # Long pays positive funding, receives negative funding
        fund_cost = pos * leverage * funding_arr[i] if pos > 0 else 0.0

        # Trading costs: charged on position changes
        pos_change = abs(pos - prev_signal)
        trade_cost = pos_change * leverage * (TRADING_COST_BPS / 10000.0)

        # Net return for this hour
        net_ret = gross_ret - fund_cost - trade_cost

        gross_returns[i] = gross_ret
        funding_costs[i] = fund_cost
        trading_costs[i] = trade_cost
        net_returns[i] = net_ret

        equity[i] = equity[i - 1] * (1.0 + net_ret)

        # Track trade-level drawdown for liquidation
        if pos > 0 and prev_signal <= 0:
            # New trade entry
            trade_entry_equity = equity[i - 1]
            in_trade = True
        elif pos <= 0 and prev_signal > 0:
            # Trade exit
            in_trade = False

        if in_trade and trade_entry_equity > 0:
            trade_dd = (equity[i] - trade_entry_equity) / trade_entry_equity
            if trade_dd < -liquidation_threshold:
                liquidation_events.append({
                    'date': hourly_df.index[i],
                    'equity': equity[i],
                    'trade_dd': trade_dd,
                    'leverage': leverage,
                })
            elif trade_dd < -liquidation_threshold * 0.8:
                near_liquidation_events.append({
                    'date': hourly_df.index[i],
                    'equity': equity[i],
                    'trade_dd': trade_dd,
                    'leverage': leverage,
                })

        prev_signal = pos

    # Build equity DataFrame
    equity_df = pd.DataFrame({
        'equity': equity,
        'gross_return': gross_returns,
        'funding_cost': funding_costs,
        'trading_cost': trading_costs,
        'net_return': net_returns,
        'signal': signal,
    }, index=hourly_df.index)

    return {
        'equity': equity_df,
        'liquidation_events': liquidation_events,
        'near_liquidation_events': near_liquidation_events,
    }


# =============================================================================
# Metrics Computation
# =============================================================================

def compute_metrics(equity_df, leverage, liquidation_events, near_liquidation_events):
    """Compute comprehensive performance metrics."""
    # Resample to daily for standard metrics
    daily_equity = equity_df['equity'].resample('1D').last().dropna()
    daily_returns = daily_equity.pct_change().dropna()

    n_days = len(daily_returns)
    n_years = n_days / 365.25

    # Total return
    total_return = (daily_equity.iloc[-1] / daily_equity.iloc[0]) - 1.0
    annualized_return = (1.0 + total_return) ** (1.0 / n_years) - 1.0 if n_years > 0 else 0.0

    # Sharpe ratio (annualized, assuming 0% risk-free)
    daily_mean = daily_returns.mean()
    daily_std = daily_returns.std()
    sharpe = (daily_mean / daily_std * np.sqrt(365.25)) if daily_std > 0 else 0.0

    # Max drawdown
    running_max = daily_equity.cummax()
    drawdown = (daily_equity - running_max) / running_max
    max_dd = drawdown.min()

    # Calmar ratio
    calmar = annualized_return / abs(max_dd) if max_dd != 0 else 0.0

    # Funding cost drag (annualized)
    total_funding = equity_df['funding_cost'].sum()
    total_gross = equity_df['gross_return'].sum()
    total_trading = equity_df['trading_cost'].sum()

    # Annualize the cumulative costs
    annualized_funding_drag = total_funding / n_years if n_years > 0 else 0.0
    annualized_trading_drag = total_trading / n_years if n_years > 0 else 0.0

    # Funding as % of gross return
    funding_pct_of_gross = (total_funding / total_gross * 100) if total_gross > 0 else 0.0

    # Time in market
    in_market = (equity_df['signal'] > 0).sum() / len(equity_df) * 100

    # Win rate (daily)
    win_days = (daily_returns > 0).sum()
    total_days = len(daily_returns)
    win_rate = win_days / total_days * 100 if total_days > 0 else 0.0

    # Number of trades (rebalance events where position changes)
    pos_changes = equity_df['signal'].diff().abs()
    n_trades = (pos_changes > 0.01).sum()

    # Sortino ratio
    downside_returns = daily_returns[daily_returns < 0]
    downside_std = downside_returns.std()
    sortino = (daily_mean / downside_std * np.sqrt(365.25)) if downside_std > 0 else 0.0

    return {
        'leverage': leverage,
        'total_return': total_return,
        'annualized_return': annualized_return,
        'sharpe': sharpe,
        'sortino': sortino,
        'max_dd': max_dd,
        'calmar': calmar,
        'annualized_funding_drag': annualized_funding_drag,
        'annualized_trading_drag': annualized_trading_drag,
        'funding_pct_of_gross': funding_pct_of_gross,
        'total_gross_return': total_gross,
        'total_funding_cost': total_funding,
        'total_trading_cost': total_trading,
        'time_in_market_pct': in_market,
        'win_rate_pct': win_rate,
        'n_trades': n_trades,
        'n_days': n_days,
        'n_years': n_years,
        'liquidation_events': len(liquidation_events),
        'near_liquidation_events': len(near_liquidation_events),
    }


# =============================================================================
# Kill Criteria Check
# =============================================================================

def check_kill_criteria(metrics):
    """Check if a leverage level should be killed based on pre-defined criteria."""
    kills = []

    if metrics['max_dd'] < -0.40:
        kills.append(f"MaxDD {metrics['max_dd']:.1%} exceeds -40% threshold")

    if metrics['liquidation_events'] > 1:
        kills.append(f"{metrics['liquidation_events']} liquidation events (threshold: >1)")

    if metrics['sharpe'] < 0.3:
        kills.append(f"Sharpe {metrics['sharpe']:.2f} below 0.3 threshold")

    if metrics['funding_pct_of_gross'] > 50:
        kills.append(f"Funding drag {metrics['funding_pct_of_gross']:.1f}% of gross return (threshold: >50%)")

    return kills


# =============================================================================
# Main Execution
# =============================================================================

def main():
    print("=" * 80)
    print("V3 LEVERAGED FUTURES TEST — BTC Perpetual with 1x, 2x, 3x Leverage")
    print("=" * 80)
    print()

    # ── Load Data ─────────────────────────────────────────────────────
    print("Loading data...")
    perp_df = load_perp_data()
    funding_rates = load_funding_rates()
    print(f"  Perp 1H bars: {len(perp_df)} ({perp_df.index.min()} to {perp_df.index.max()})")
    print(f"  Funding rates: {len(funding_rates)} records")

    # ── Build Signal ──────────────────────────────────────────────────
    print("\nBuilding V3 signal...")
    signal = build_hourly_signal(perp_df)
    print(f"  Signal built: {len(signal)} bars")
    print(f"  Non-zero signal bars: {(signal > 0).sum()} ({(signal > 0).sum()/len(signal)*100:.1f}%)")

    # ── Build Funding Schedule ────────────────────────────────────────
    print("\nBuilding hourly funding schedule...")
    hourly_funding = build_hourly_funding(perp_df.index, funding_rates)
    actual_coverage = hourly_funding.notna().sum() / len(hourly_funding) * 100
    print(f"  Hourly funding bars: {len(hourly_funding)}")
    print(f"  Mean hourly funding rate: {hourly_funding.mean():.8f}")
    print(f"  Annualized mean funding: {hourly_funding.mean() * 24 * 365.25 * 100:.2f}%")

    # ── IS/OOS Split ──────────────────────────────────────────────────
    # Split after warmup period
    warmup_end = WARMUP_BARS
    tradeable_len = len(perp_df) - warmup_end
    is_end = warmup_end + int(tradeable_len * IS_FRACTION)

    is_start_date = perp_df.index[warmup_end]
    is_end_date = perp_df.index[is_end - 1]
    oos_start_date = perp_df.index[is_end]
    oos_end_date = perp_df.index[-1]

    print(f"\n  IS period:  {is_start_date.date()} to {is_end_date.date()} ({is_end - warmup_end} bars)")
    print(f"  OOS period: {oos_start_date.date()} to {oos_end_date.date()} ({len(perp_df) - is_end} bars)")

    # ── Run Backtests ─────────────────────────────────────────────────
    results = {}

    for leverage in LEVERAGE_LEVELS:
        print(f"\n{'─' * 60}")
        print(f"Running backtest: {leverage:.0f}x leverage")
        print(f"{'─' * 60}")

        # Full period backtest
        bt_full = run_leveraged_backtest(perp_df, signal, hourly_funding, leverage)

        # IS period
        is_equity = bt_full['equity'].iloc[warmup_end:is_end]
        is_liq = [e for e in bt_full['liquidation_events']
                  if e['date'] >= is_start_date and e['date'] <= is_end_date]
        is_near_liq = [e for e in bt_full['near_liquidation_events']
                       if e['date'] >= is_start_date and e['date'] <= is_end_date]
        is_metrics = compute_metrics(is_equity, leverage, is_liq, is_near_liq)

        # OOS period: re-run from OOS start with fresh equity
        oos_df = perp_df.iloc[is_end:].copy()
        oos_signal = signal[is_end:]
        oos_funding = hourly_funding.iloc[is_end:]
        bt_oos = run_leveraged_backtest(oos_df, oos_signal, oos_funding, leverage)
        oos_metrics = compute_metrics(bt_oos['equity'], leverage,
                                       bt_oos['liquidation_events'],
                                       bt_oos['near_liquidation_events'])

        # Kill check
        oos_kills = check_kill_criteria(oos_metrics)

        results[leverage] = {
            'is_metrics': is_metrics,
            'oos_metrics': oos_metrics,
            'oos_kills': oos_kills,
            'oos_equity': bt_oos['equity'],
        }

        # Print summary
        print(f"\n  IS  Metrics ({leverage:.0f}x):")
        print(f"    Sharpe: {is_metrics['sharpe']:.3f}")
        print(f"    Ann Return: {is_metrics['annualized_return']:.2%}")
        print(f"    Max DD: {is_metrics['max_dd']:.2%}")
        print(f"    Calmar: {is_metrics['calmar']:.3f}")
        print(f"    Funding Drag (ann): {is_metrics['annualized_funding_drag']:.4f}")
        print(f"    Liquidation events: {is_metrics['liquidation_events']}")

        print(f"\n  OOS Metrics ({leverage:.0f}x):")
        print(f"    Sharpe: {oos_metrics['sharpe']:.3f}")
        print(f"    Ann Return: {oos_metrics['annualized_return']:.2%}")
        print(f"    Max DD: {oos_metrics['max_dd']:.2%}")
        print(f"    Calmar: {oos_metrics['calmar']:.3f}")
        print(f"    Funding Drag (ann): {oos_metrics['annualized_funding_drag']:.4f}")
        print(f"    Funding % of Gross: {oos_metrics['funding_pct_of_gross']:.1f}%")
        print(f"    Time in Market: {oos_metrics['time_in_market_pct']:.1f}%")
        print(f"    Trades: {oos_metrics['n_trades']}")
        print(f"    Liquidation events: {oos_metrics['liquidation_events']}")
        print(f"    Near-liquidation events: {oos_metrics['near_liquidation_events']}")

        if oos_kills:
            print(f"\n  ** KILL FLAGS ({leverage:.0f}x) **:")
            for kill in oos_kills:
                print(f"    - {kill}")

    # ── Leverage Efficiency ───────────────────────────────────────────
    print(f"\n{'=' * 80}")
    print("LEVERAGE EFFICIENCY ANALYSIS (OOS)")
    print(f"{'=' * 80}")

    base_sharpe = results[1.0]['oos_metrics']['sharpe']
    print(f"\n  Base Sharpe (1x): {base_sharpe:.3f}")
    print()
    print(f"  {'Leverage':<10} {'Sharpe':<10} {'Ratio vs 1x':<15} {'Expected':<12} {'Efficiency':<12}")
    print(f"  {'─' * 59}")

    for lev in LEVERAGE_LEVELS:
        oos_m = results[lev]['oos_metrics']
        sharpe_ratio = oos_m['sharpe'] / base_sharpe if base_sharpe != 0 else 0.0
        expected_ratio = lev  # Linear scaling (best case)
        efficiency = sharpe_ratio / expected_ratio if expected_ratio > 0 else 0.0
        print(f"  {lev:<10.0f} {oos_m['sharpe']:<10.3f} {sharpe_ratio:<15.3f} {expected_ratio:<12.1f} {efficiency:<12.1%}")

    # ── Final Verdict ─────────────────────────────────────────────────
    print(f"\n{'=' * 80}")
    print("FINAL VERDICT")
    print(f"{'=' * 80}")

    for lev in LEVERAGE_LEVELS:
        kills = results[lev]['oos_kills']
        oos_m = results[lev]['oos_metrics']
        if kills:
            print(f"\n  {lev:.0f}x: KILL")
            for k in kills:
                print(f"    - {k}")
        else:
            print(f"\n  {lev:.0f}x: PASS")
            print(f"    Sharpe {oos_m['sharpe']:.3f}, Return {oos_m['annualized_return']:.2%}, "
                  f"MaxDD {oos_m['max_dd']:.2%}, Calmar {oos_m['calmar']:.3f}")

    # ── Write Results ─────────────────────────────────────────────────
    write_results_md(results, is_start_date, is_end_date, oos_start_date, oos_end_date,
                     base_sharpe, hourly_funding)

    print(f"\nResults written to: research/v3_leveraged_results.md")


# =============================================================================
# Results Writer
# =============================================================================

def write_results_md(results, is_start, is_end, oos_start, oos_end,
                     base_sharpe, hourly_funding):
    """Write comprehensive results markdown file."""

    lines = []
    lines.append("# V3 Leveraged Futures Test — Results")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("## Context")
    lines.append("")
    lines.append("V3 is a validated BTC-only momentum strategy: 20/50 EMA crossover + positioning")
    lines.append("overlay + VRP overlay. This test evaluates running V3 on perpetual futures with")
    lines.append("1x, 2x, and 3x leverage, including actual Binance funding rate costs.")
    lines.append("")
    lines.append("**Spot V3 Baseline (OOS):** Sharpe 0.56, Return +17.52%, MaxDD -20.2%")
    lines.append("")
    lines.append("## Data")
    lines.append("")
    lines.append(f"- **IS Period:** {is_start.date()} to {is_end.date()}")
    lines.append(f"- **OOS Period:** {oos_start.date()} to {oos_end.date()}")
    lines.append(f"- **Funding Rates:** Actual Binance BTC funding (8h settlement)")
    lines.append(f"- **Mean Funding (ann):** {hourly_funding.mean() * 24 * 365.25 * 100:.2f}%")
    lines.append(f"- **Trading Cost:** {TRADING_COST_BPS} bps per trade")
    lines.append(f"- **Warmup:** {WARMUP_DAILY} days")
    lines.append(f"- **Rebalance:** Weekly (168 bars)")
    lines.append("")

    # ── OOS Metrics Table ─────────────────────────────────────────────
    lines.append("## OOS Performance Metrics")
    lines.append("")
    lines.append("| Metric | 1x | 2x | 3x |")
    lines.append("|--------|---:|---:|---:|")

    m1 = results[1.0]['oos_metrics']
    m2 = results[2.0]['oos_metrics']
    m3 = results[3.0]['oos_metrics']

    def row(label, key, fmt=".2%"):
        v1 = m1[key]
        v2 = m2[key]
        v3 = m3[key]
        return f"| {label} | {v1:{fmt}} | {v2:{fmt}} | {v3:{fmt}} |"

    lines.append(row("Annualized Return", "annualized_return", ".2%"))
    lines.append(row("Total Return", "total_return", ".2%"))
    lines.append(row("Sharpe Ratio", "sharpe", ".3f"))
    lines.append(row("Sortino Ratio", "sortino", ".3f"))
    lines.append(row("Max Drawdown", "max_dd", ".2%"))
    lines.append(row("Calmar Ratio", "calmar", ".3f"))
    lines.append(row("Win Rate", "win_rate_pct", ".1f"))
    lines.append(row("Time in Market", "time_in_market_pct", ".1f"))

    lines.append(f"| Trades | {m1['n_trades']:.0f} | {m2['n_trades']:.0f} | {m3['n_trades']:.0f} |")
    lines.append(f"| OOS Days | {m1['n_days']:.0f} | {m2['n_days']:.0f} | {m3['n_days']:.0f} |")
    lines.append("")

    # ── Cost Analysis ─────────────────────────────────────────────────
    lines.append("## Cost Analysis (OOS)")
    lines.append("")
    lines.append("| Metric | 1x | 2x | 3x |")
    lines.append("|--------|---:|---:|---:|")
    lines.append(row("Funding Drag (ann)", "annualized_funding_drag", ".4f"))
    lines.append(row("Trading Drag (ann)", "annualized_trading_drag", ".4f"))
    lines.append(row("Funding % of Gross", "funding_pct_of_gross", ".1f"))
    lines.append(row("Total Gross Return", "total_gross_return", ".4f"))
    lines.append(row("Total Funding Cost", "total_funding_cost", ".4f"))
    lines.append(row("Total Trading Cost", "total_trading_cost", ".4f"))
    lines.append("")

    # ── Liquidation Risk ──────────────────────────────────────────────
    lines.append("## Liquidation Risk (OOS)")
    lines.append("")
    lines.append("| Metric | 1x | 2x | 3x |")
    lines.append("|--------|---:|---:|---:|")
    lines.append(f"| Liquidation Events | {m1['liquidation_events']} | {m2['liquidation_events']} | {m3['liquidation_events']} |")
    lines.append(f"| Near-Liquidation Events | {m1['near_liquidation_events']} | {m2['near_liquidation_events']} | {m3['near_liquidation_events']} |")

    liq_thresholds = {1.0: "100%", 2.0: "50%", 3.0: "33%"}
    lines.append(f"| Liquidation Threshold | {liq_thresholds[1.0]} | {liq_thresholds[2.0]} | {liq_thresholds[3.0]} |")
    lines.append(f"| Near-Liq Threshold (80%) | 80% | 40% | 27% |")
    lines.append("")

    # ── Leverage Efficiency ───────────────────────────────────────────
    lines.append("## Leverage Efficiency (OOS)")
    lines.append("")
    lines.append("Risk-adjusted leverage efficiency measures how well Sharpe scales with leverage.")
    lines.append("Efficiency = (Sharpe_Lx / Sharpe_1x) / L. Perfect scaling = 100%.")
    lines.append("")
    lines.append("| Leverage | Sharpe | Ratio vs 1x | Expected | Efficiency |")
    lines.append("|----------|--------|-------------|----------|------------|")

    for lev in LEVERAGE_LEVELS:
        oos_m = results[lev]['oos_metrics']
        ratio = oos_m['sharpe'] / base_sharpe if base_sharpe != 0 else 0.0
        expected = lev
        eff = ratio / expected if expected > 0 else 0.0
        lines.append(f"| {lev:.0f}x | {oos_m['sharpe']:.3f} | {ratio:.3f} | {expected:.1f} | {eff:.1%} |")

    lines.append("")

    # ── IS Metrics (for reference) ────────────────────────────────────
    lines.append("## IS Performance Metrics (Reference)")
    lines.append("")
    lines.append("| Metric | 1x | 2x | 3x |")
    lines.append("|--------|---:|---:|---:|")

    m1i = results[1.0]['is_metrics']
    m2i = results[2.0]['is_metrics']
    m3i = results[3.0]['is_metrics']

    def irow(label, key, fmt=".2%"):
        v1 = m1i[key]
        v2 = m2i[key]
        v3 = m3i[key]
        return f"| {label} | {v1:{fmt}} | {v2:{fmt}} | {v3:{fmt}} |"

    lines.append(irow("Annualized Return", "annualized_return", ".2%"))
    lines.append(irow("Sharpe Ratio", "sharpe", ".3f"))
    lines.append(irow("Max Drawdown", "max_dd", ".2%"))
    lines.append(irow("Calmar Ratio", "calmar", ".3f"))
    lines.append(f"| Liquidation Events | {m1i['liquidation_events']} | {m2i['liquidation_events']} | {m3i['liquidation_events']} |")
    lines.append("")

    # ── Kill Criteria ─────────────────────────────────────────────────
    lines.append("## Kill Criteria Assessment (OOS)")
    lines.append("")
    lines.append("| Criterion | Threshold | 1x | 2x | 3x |")
    lines.append("|-----------|-----------|---:|---:|---:|")

    # MaxDD
    dd_status = lambda m: "KILL" if m['max_dd'] < -0.40 else "PASS"
    lines.append(f"| MaxDD > 40% | -40% | {dd_status(m1)} ({m1['max_dd']:.1%}) | {dd_status(m2)} ({m2['max_dd']:.1%}) | {dd_status(m3)} ({m3['max_dd']:.1%}) |")

    # Liquidation
    liq_status = lambda m: "KILL" if m['liquidation_events'] > 1 else "PASS"
    lines.append(f"| Multiple Liquidations | >1 | {liq_status(m1)} ({m1['liquidation_events']}) | {liq_status(m2)} ({m2['liquidation_events']}) | {liq_status(m3)} ({m3['liquidation_events']}) |")

    # Sharpe
    sharpe_status = lambda m: "KILL" if m['sharpe'] < 0.3 else "PASS"
    lines.append(f"| Sharpe < 0.3 | 0.3 | {sharpe_status(m1)} ({m1['sharpe']:.2f}) | {sharpe_status(m2)} ({m2['sharpe']:.2f}) | {sharpe_status(m3)} ({m3['sharpe']:.2f}) |")

    # Funding
    fund_status = lambda m: "KILL" if m['funding_pct_of_gross'] > 50 else "PASS"
    lines.append(f"| Funding > 50% Gross | 50% | {fund_status(m1)} ({m1['funding_pct_of_gross']:.0f}%) | {fund_status(m2)} ({m2['funding_pct_of_gross']:.0f}%) | {fund_status(m3)} ({m3['funding_pct_of_gross']:.0f}%) |")

    lines.append("")

    # ── Verdict ───────────────────────────────────────────────────────
    lines.append("## Verdict")
    lines.append("")

    any_pass = False
    for lev in LEVERAGE_LEVELS:
        kills = results[lev]['oos_kills']
        oos_m = results[lev]['oos_metrics']
        if kills:
            lines.append(f"### {lev:.0f}x Leverage: KILL")
            lines.append("")
            for k in kills:
                lines.append(f"- {k}")
            lines.append("")
        else:
            any_pass = True
            lines.append(f"### {lev:.0f}x Leverage: PASS")
            lines.append("")
            lines.append(f"- Sharpe: {oos_m['sharpe']:.3f}")
            lines.append(f"- Annualized Return: {oos_m['annualized_return']:.2%}")
            lines.append(f"- Max Drawdown: {oos_m['max_dd']:.2%}")
            lines.append(f"- Calmar: {oos_m['calmar']:.3f}")
            lines.append(f"- Funding Drag: {oos_m['funding_pct_of_gross']:.1f}% of gross")
            lines.append("")

    lines.append("### Recommendation")
    lines.append("")

    # Determine best leverage
    best_lev = None
    best_calmar = -999
    for lev in LEVERAGE_LEVELS:
        if not results[lev]['oos_kills']:
            oos_m = results[lev]['oos_metrics']
            if oos_m['calmar'] > best_calmar:
                best_calmar = oos_m['calmar']
                best_lev = lev

    if best_lev is not None:
        oos_m = results[best_lev]['oos_metrics']
        lines.append(f"**{best_lev:.0f}x leverage** is the recommended level based on risk-adjusted returns.")
        lines.append(f"It achieves Sharpe {oos_m['sharpe']:.3f} with MaxDD {oos_m['max_dd']:.2%} and")
        lines.append(f"Calmar {oos_m['calmar']:.3f}. Funding costs consume {oos_m['funding_pct_of_gross']:.1f}%")
        lines.append(f"of gross returns, which is {'acceptable' if oos_m['funding_pct_of_gross'] < 30 else 'notable but within limits'}.")
    else:
        lines.append("**No leverage level passes all kill criteria.** The V3 strategy should remain")
        lines.append("on spot markets. Funding costs and amplified drawdowns erode the edge.")

    lines.append("")

    # Funding rate note
    lines.append("### Funding Rate Notes")
    lines.append("")
    lines.append(f"- Actual Binance BTC funding rates were used (mean: {hourly_funding.mean() * 24 * 365.25 * 100:.2f}% annualized)")
    lines.append("- Funding is charged every 8 hours on the leveraged notional when in a long position")
    lines.append("- The 2020-2026 period includes both high-funding bull markets and negative-funding corrections")
    lines.append("- Funding costs scale linearly with leverage (2x leverage = 2x funding cost)")
    lines.append("")

    output_path = PROJECT_ROOT / 'research' / 'v3_leveraged_results.md'
    with open(output_path, 'w') as f:
        f.write('\n'.join(lines))


if __name__ == '__main__':
    main()
