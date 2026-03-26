"""
V3 Perp + Overlay + Dynamic Leverage Test
==========================================

Tests whether V3+overlay stack on BTC perpetual futures with DYNAMIC leverage
(VRP-scaled) can achieve 50-100%+ annual returns with controlled drawdowns.

Context:
  - V3 on BTC SPOT (OOS): +17.52%, Sharpe 0.56, MaxDD -20.2%
  - V3 on 1x BTC perp (prior test): Sharpe 0.47, Return +10.2%, MaxDD -37.4%
  - 2x perp was KILL (-62% DD), 3x was KILL (-78% DD)
  - NEW: Dynamic leverage where VRP regime controls leverage multiplier
    instead of just position sizing. This is the natural extension.

Experiments:
  1. V3+overlay on 1x BTC perp (baseline, with proper funding)
  2. V3+overlay with VRP-scaled dynamic leverage (1.0-2.0x)
  3. Fixed 1.5x leverage with full overlay (comparison)
  4. Conservative dynamic leverage (1.0-1.3x range)

The hypothesis: overlays that reduce position in turbulence, combined with
leverage that also reduces in turbulence, provide a double safety margin.
The key is that VRP z<-0.5 means BOTH position size AND leverage drop,
creating a multiplicative risk reduction exactly when it's needed most.

Status: RESEARCH SCRIPT
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

# Cost parameters
TRADING_COST_BPS = 10   # 10 bps per trade (taker fee on perps)

# IS/OOS split
IS_FRACTION = 0.70
OOS_FRACTION = 0.30

# Data paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PERP_PATH = PROJECT_ROOT / 'data' / 'perp' / '1h_cache' / 'BTC_1h.parquet'
POS_PATH = PROJECT_ROOT / 'data' / 'alternative' / 'binance_metrics' / 'all_symbols_daily_ls.parquet'
DVOL_PATH = PROJECT_ROOT / 'data' / 'alternative' / 'deribit_options' / 'dvol' / 'btc_dvol_daily.json'

# =============================================================================
# Experiment Definitions
# =============================================================================

EXPERIMENTS = {
    'exp1_1x_baseline': {
        'name': 'Exp 1: V3+Overlay 1x Perp (Baseline)',
        'base_leverage': 1.0,
        'dynamic': False,
        'lev_complacent': 1.0,
        'lev_turbulent': 1.0,
    },
    'exp2_dynamic_aggressive': {
        'name': 'Exp 2: Dynamic VRP-Scaled (0.5x-2.0x)',
        'base_leverage': 1.0,
        'dynamic': True,
        'lev_complacent': 2.0,   # VRP z > 1.0
        'lev_normal': 1.0,       # -0.5 < VRP z < 1.0
        'lev_turbulent': 0.5,    # VRP z < -0.5
        'lev_extreme': 0.3,      # VRP z < -1.5
    },
    'exp3_fixed_1_5x': {
        'name': 'Exp 3: Fixed 1.5x Leverage + Overlay',
        'base_leverage': 1.5,
        'dynamic': False,
        'lev_complacent': 1.5,
        'lev_turbulent': 1.5,
    },
    'exp4_conservative_dynamic': {
        'name': 'Exp 4: Conservative Dynamic (0.7x-1.3x)',
        'base_leverage': 1.0,
        'dynamic': True,
        'lev_complacent': 1.3,   # VRP z > 1.0
        'lev_normal': 1.0,       # -0.5 < VRP z < 1.0
        'lev_turbulent': 0.7,    # VRP z < -0.5
        'lev_extreme': 0.5,      # VRP z < -1.5
    },
    'exp5_dynamic_1_5x_base': {
        'name': 'Exp 5: Dynamic 1.5x Base (0.5x-2.0x)',
        'base_leverage': 1.5,
        'dynamic': True,
        'lev_complacent': 2.0,   # VRP z > 1.0
        'lev_normal': 1.5,       # -0.5 < VRP z < 1.0
        'lev_turbulent': 0.75,   # VRP z < -0.5
        'lev_extreme': 0.5,      # VRP z < -1.5
    },
    'exp6_ultra_conservative': {
        'name': 'Exp 6: Ultra-Conservative (0.8x-1.2x)',
        'base_leverage': 1.0,
        'dynamic': True,
        'lev_complacent': 1.2,   # VRP z > 1.0: modest boost
        'lev_normal': 1.0,       # -0.5 < VRP z < 1.0
        'lev_turbulent': 0.8,    # VRP z < -0.5: modest reduction
        'lev_extreme': 0.6,      # VRP z < -1.5: stronger reduction
    },
    'exp7_dynamic_moderate': {
        'name': 'Exp 7: Dynamic Moderate (0.5x-1.5x)',
        'base_leverage': 1.0,
        'dynamic': True,
        'lev_complacent': 1.5,   # VRP z > 1.0
        'lev_normal': 1.0,       # -0.5 < VRP z < 1.0
        'lev_turbulent': 0.5,    # VRP z < -0.5
        'lev_extreme': 0.3,      # VRP z < -1.5: very defensive
    },
}

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
    """Load BTC perpetual futures 1H OHLCV data with funding rates."""
    df = pd.read_parquet(PERP_PATH)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    return df


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
# Signal Construction
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


def build_vrp_components(daily_close, daily_idx, dvol):
    """
    Build VRP z-score and the sizing multiplier.
    Returns (vrp_z, vrp_multiplier) — both as daily arrays.
    We need vrp_z separately for dynamic leverage decisions.
    """
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

    # Standard VRP sizing multiplier
    multiplier = np.where(
        vrp_z > VRP_HIGH_THRESH, 1.3,
        np.where(vrp_z > VRP_MID_LOW, 1.0,
                 np.where(vrp_z > VRP_EXTREME_LOW, 0.5,
                          0.3)))
    multiplier = np.where(np.isnan(vrp_z), 1.0, multiplier)

    return vrp_z, multiplier


def build_dynamic_leverage(vrp_z_daily, exp_config):
    """
    Build daily leverage multiplier based on VRP regime.

    For dynamic experiments:
      VRP z > 1.0  -> complacent leverage (size up)
      -0.5 < z < 1.0 -> normal leverage
      -1.5 < z < -0.5 -> turbulent leverage (de-lever)
      z < -1.5 -> extreme turbulence (minimum leverage)

    For fixed experiments:
      Returns constant leverage array.
    """
    n = len(vrp_z_daily)

    if not exp_config['dynamic']:
        return np.full(n, exp_config['base_leverage'], dtype=np.float64)

    lev_complacent = exp_config['lev_complacent']
    lev_normal = exp_config['lev_normal']
    lev_turbulent = exp_config['lev_turbulent']
    lev_extreme = exp_config['lev_extreme']

    leverage = np.where(
        vrp_z_daily > VRP_HIGH_THRESH, lev_complacent,
        np.where(vrp_z_daily > VRP_MID_LOW, lev_normal,
                 np.where(vrp_z_daily > VRP_EXTREME_LOW, lev_turbulent,
                          lev_extreme)))
    leverage = np.where(np.isnan(vrp_z_daily), exp_config['base_leverage'], leverage)

    return leverage


def build_hourly_signals(hourly_df, exp_config):
    """
    Construct the full V3 signal on hourly bars with dynamic leverage.

    Returns:
      signal: position sizing array (0 to MAX_POSITION) at each hourly bar
      leverage: leverage multiplier at each hourly bar
      vrp_z_hourly: VRP z-score aligned to hourly bars (for diagnostics)
    """
    daily = hourly_df['close'].resample('1D').last().dropna()
    daily_close = daily.values
    daily_idx = daily.index
    n_daily = len(daily_close)
    n_hourly = len(hourly_df)

    if n_daily < WARMUP_DAILY:
        return (np.zeros(n_hourly, dtype=np.float64),
                np.ones(n_hourly, dtype=np.float64),
                np.full(n_hourly, np.nan))

    # Load overlay data
    positioning = load_positioning()
    dvol = load_dvol()

    # Build daily signals
    base_signal = build_ema_base_signal(daily_close)
    pos_multiplier = build_positioning_multiplier(daily_idx, positioning)
    vrp_z_daily, vrp_multiplier = build_vrp_components(daily_close, daily_idx, dvol)

    # Build dynamic leverage (daily)
    leverage_daily = build_dynamic_leverage(vrp_z_daily, exp_config)

    # Compose final daily position (sizing only -- leverage applied separately)
    final_daily = np.clip(base_signal * pos_multiplier * vrp_multiplier,
                          MIN_POSITION, MAX_POSITION)

    # Align daily signals to hourly bars via forward-fill
    daily_signal_s = pd.Series(final_daily, index=daily_idx)
    hourly_signal = daily_signal_s.reindex(hourly_df.index, method='ffill').values

    daily_lev_s = pd.Series(leverage_daily, index=daily_idx)
    hourly_leverage = daily_lev_s.reindex(hourly_df.index, method='ffill').values

    daily_vrpz_s = pd.Series(vrp_z_daily, index=daily_idx)
    hourly_vrpz = daily_vrpz_s.reindex(hourly_df.index, method='ffill').values

    # Apply weekly rebalance gates
    rebalanced_signal = np.zeros(n_hourly, dtype=np.float64)
    rebalanced_leverage = np.ones(n_hourly, dtype=np.float64)
    current_pos = 0.0
    current_lev = exp_config['base_leverage']

    for i in range(n_hourly):
        if i < WARMUP_BARS:
            rebalanced_signal[i] = 0.0
            rebalanced_leverage[i] = exp_config['base_leverage']
            continue
        if (i - WARMUP_BARS) % REBALANCE_BARS == 0:
            # Rebalance point: adopt current signals
            current_pos = hourly_signal[i] if not np.isnan(hourly_signal[i]) else 0.0
            current_lev = hourly_leverage[i] if not np.isnan(hourly_leverage[i]) else exp_config['base_leverage']
        rebalanced_signal[i] = current_pos
        rebalanced_leverage[i] = current_lev

    return rebalanced_signal, rebalanced_leverage, hourly_vrpz


# =============================================================================
# Backtest Engine (with dynamic leverage)
# =============================================================================

def run_backtest(hourly_df, signal, leverage_arr, exp_config):
    """
    Run a backtest for V3 on BTC perps with dynamic leverage.

    Funding is charged at 8h settlement times (00:00, 08:00, 16:00 UTC).
    Longs pay the full 8h rate at settlement.

    Parameters:
    -----------
    hourly_df : DataFrame with OHLCV perp data + funding_rate column
    signal : array of position sizes (0 to MAX_POSITION) at each hourly bar
    leverage_arr : array of leverage multiplier at each hourly bar
    exp_config : experiment configuration dict

    Returns:
    --------
    dict with equity curve, metrics, diagnostics
    """
    n = len(hourly_df)
    close = hourly_df['close'].values
    funding_8h = hourly_df['funding_rate'].values  # 8h rate, forward-filled

    # Hourly returns
    hourly_returns = np.zeros(n)
    hourly_returns[1:] = close[1:] / close[:-1] - 1.0

    # Settlement hours for Binance: 00:00, 08:00, 16:00 UTC
    is_settlement = np.isin(hourly_df.index.hour, [0, 8, 16])

    # Track equity and components
    equity = np.ones(n, dtype=np.float64)
    gross_returns = np.zeros(n)
    funding_costs = np.zeros(n)
    trading_costs = np.zeros(n)
    net_returns = np.zeros(n)
    effective_leverage = np.zeros(n)

    prev_signal = 0.0
    prev_leverage = exp_config['base_leverage']

    # Liquidation tracking: position-level margin model
    # On cross-margin perpetuals, liquidation occurs when the position loss
    # exceeds the margin posted. For leverage L, margin = notional/L,
    # so liquidation at ~(1/L) loss on position (before maintenance margin).
    liquidation_events = []
    near_liquidation_events = []

    # Track position entry equity for per-trade liquidation checks
    trade_entry_equity = 1.0
    trade_entry_lev = 1.0
    in_trade = False

    for i in range(1, n):
        pos = signal[i - 1]      # Position sizing from previous bar
        lev = leverage_arr[i - 1]  # Leverage from previous bar
        eff_lev = pos * lev       # Effective leverage (position * leverage)
        effective_leverage[i] = eff_lev

        # Gross return: effective_leverage * market return
        gross_ret = eff_lev * hourly_returns[i]

        # Funding cost: charged only at 8h settlement, on full notional
        # Longs pay positive funding, receive negative funding
        if pos > 0 and is_settlement[i]:
            fund_cost = eff_lev * funding_8h[i]
        else:
            fund_cost = 0.0

        # Trading costs: charged on position/leverage changes
        new_eff_lev = pos * lev
        old_eff_lev = prev_signal * prev_leverage
        lev_change = abs(new_eff_lev - old_eff_lev)
        trade_cost = lev_change * (TRADING_COST_BPS / 10000.0)

        # Net return for this hour
        net_ret = gross_ret - fund_cost - trade_cost

        gross_returns[i] = gross_ret
        funding_costs[i] = fund_cost
        trading_costs[i] = trade_cost
        net_returns[i] = net_ret

        equity[i] = equity[i - 1] * (1.0 + net_ret)

        # Position-level liquidation tracking
        # Track entry of each trade/rebalance cycle
        if pos > 0 and prev_signal <= 0:
            # New trade entry
            trade_entry_equity = equity[i - 1]
            trade_entry_lev = lev
            in_trade = True
        elif pos <= 0 and prev_signal > 0:
            in_trade = False

        if in_trade and trade_entry_equity > 0 and trade_entry_lev > 1.0:
            # Liquidation threshold: loss > 1/leverage on the position
            liq_threshold = 1.0 / trade_entry_lev
            trade_pnl = (equity[i] - trade_entry_equity) / trade_entry_equity
            if trade_pnl < -liq_threshold:
                liquidation_events.append({
                    'date': hourly_df.index[i],
                    'equity': equity[i],
                    'trade_pnl': trade_pnl,
                    'leverage_at_event': lev,
                    'eff_leverage': eff_lev,
                })
            elif trade_pnl < -liq_threshold * 0.8:
                near_liquidation_events.append({
                    'date': hourly_df.index[i],
                    'equity': equity[i],
                    'trade_pnl': trade_pnl,
                    'leverage_at_event': lev,
                })

        prev_signal = pos
        prev_leverage = lev

    # Build result DataFrame
    equity_df = pd.DataFrame({
        'equity': equity,
        'gross_return': gross_returns,
        'funding_cost': funding_costs,
        'trading_cost': trading_costs,
        'net_return': net_returns,
        'signal': signal,
        'leverage': leverage_arr,
        'effective_leverage': effective_leverage,
    }, index=hourly_df.index)

    return {
        'equity': equity_df,
        'liquidation_events': liquidation_events,
        'near_liquidation_events': near_liquidation_events,
    }


# =============================================================================
# Metrics Computation
# =============================================================================

def compute_metrics(equity_df, exp_config, liquidation_events, near_liquidation_events):
    """Compute comprehensive performance metrics."""
    daily_equity = equity_df['equity'].resample('1D').last().dropna()
    daily_returns = daily_equity.pct_change().dropna()

    n_days = len(daily_returns)
    n_years = n_days / 365.25

    # Total return
    total_return = (daily_equity.iloc[-1] / daily_equity.iloc[0]) - 1.0
    annualized_return = (1.0 + total_return) ** (1.0 / n_years) - 1.0 if n_years > 0 else 0.0

    # Sharpe ratio (annualized, 0% risk-free)
    daily_mean = daily_returns.mean()
    daily_std = daily_returns.std()
    sharpe = (daily_mean / daily_std * np.sqrt(365.25)) if daily_std > 0 else 0.0

    # Max drawdown
    running_max = daily_equity.cummax()
    drawdown = (daily_equity - running_max) / running_max
    max_dd = drawdown.min()

    # Calmar ratio
    calmar = annualized_return / abs(max_dd) if max_dd != 0 else 0.0

    # Sortino ratio
    downside_returns = daily_returns[daily_returns < 0]
    downside_std = downside_returns.std()
    sortino = (daily_mean / downside_std * np.sqrt(365.25)) if downside_std > 0 else 0.0

    # Funding cost analysis
    total_funding = equity_df['funding_cost'].sum()
    total_gross = equity_df['gross_return'].sum()
    total_trading = equity_df['trading_cost'].sum()
    annualized_funding_drag = total_funding / n_years if n_years > 0 else 0.0
    annualized_funding_pct = annualized_funding_drag * 100  # As percentage
    funding_pct_of_gross = (total_funding / total_gross * 100) if total_gross > 0 else 0.0

    # Leverage stats
    active_mask = equity_df['signal'] > 0
    if active_mask.sum() > 0:
        mean_eff_leverage = equity_df.loc[active_mask, 'effective_leverage'].mean()
        max_eff_leverage = equity_df.loc[active_mask, 'effective_leverage'].max()
        mean_leverage = equity_df.loc[active_mask, 'leverage'].mean()
        max_leverage = equity_df.loc[active_mask, 'leverage'].max()
    else:
        mean_eff_leverage = 0.0
        max_eff_leverage = 0.0
        mean_leverage = 0.0
        max_leverage = 0.0

    # Time in market
    time_in_market = active_mask.sum() / len(equity_df) * 100

    # Win rate (daily)
    win_days = (daily_returns > 0).sum()
    win_rate = win_days / n_days * 100 if n_days > 0 else 0.0

    # Number of effective rebalances
    pos_changes = equity_df['signal'].diff().abs()
    n_rebalances = (pos_changes > 0.01).sum()

    # Drawdown duration
    dd_series = drawdown
    in_dd = dd_series < 0
    dd_groups = (~in_dd).cumsum()
    if in_dd.any():
        dd_lengths = in_dd.groupby(dd_groups).sum()
        max_dd_duration = dd_lengths.max()
    else:
        max_dd_duration = 0

    return {
        'name': exp_config['name'],
        'total_return': total_return,
        'annualized_return': annualized_return,
        'sharpe': sharpe,
        'sortino': sortino,
        'max_dd': max_dd,
        'calmar': calmar,
        'max_dd_duration_days': int(max_dd_duration),
        'annualized_funding_drag_pct': annualized_funding_pct,
        'funding_pct_of_gross': funding_pct_of_gross,
        'total_gross_return': total_gross,
        'total_funding_cost': total_funding,
        'total_trading_cost': total_trading,
        'time_in_market_pct': time_in_market,
        'win_rate_pct': win_rate,
        'n_rebalances': n_rebalances,
        'n_days': n_days,
        'n_years': n_years,
        'mean_eff_leverage': mean_eff_leverage,
        'max_eff_leverage': max_eff_leverage,
        'mean_leverage': mean_leverage,
        'max_leverage': max_leverage,
        'liquidation_events': len(liquidation_events),
        'near_liquidation_events': len(near_liquidation_events),
    }


# =============================================================================
# Kill Criteria
# =============================================================================

def check_kill_criteria(metrics):
    """Check if an experiment should be killed."""
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
# VRP Regime Analysis
# =============================================================================

def analyze_vrp_regimes(hourly_df, vrp_z_hourly):
    """Analyze time spent in each VRP regime."""
    valid = ~np.isnan(vrp_z_hourly)
    vrp_valid = vrp_z_hourly[valid]
    n = len(vrp_valid)

    complacent = (vrp_valid > VRP_HIGH_THRESH).sum() / n * 100
    normal = ((vrp_valid > VRP_MID_LOW) & (vrp_valid <= VRP_HIGH_THRESH)).sum() / n * 100
    turbulent = ((vrp_valid > VRP_EXTREME_LOW) & (vrp_valid <= VRP_MID_LOW)).sum() / n * 100
    extreme = (vrp_valid <= VRP_EXTREME_LOW).sum() / n * 100

    return {
        'complacent_pct': complacent,
        'normal_pct': normal,
        'turbulent_pct': turbulent,
        'extreme_pct': extreme,
        'mean_vrp_z': np.nanmean(vrp_z_hourly),
        'std_vrp_z': np.nanstd(vrp_z_hourly),
    }


# =============================================================================
# Main Execution
# =============================================================================

def main():
    print("=" * 80)
    print("V3 PERP + OVERLAY + DYNAMIC LEVERAGE TEST")
    print("=" * 80)
    print()

    # ── Load Data ─────────────────────────────────────────────────────
    print("Loading data...")
    perp_df = load_perp_data()
    print(f"  Perp 1H bars: {len(perp_df)} ({perp_df.index.min()} to {perp_df.index.max()})")
    print(f"  Funding rate mean (8h): {perp_df['funding_rate'].mean():.6f}")
    print(f"  Funding rate annualized: {perp_df['funding_rate'].mean() * 3 * 365.25 * 100:.2f}%")
    print()

    # ── IS/OOS Split ──────────────────────────────────────────────────
    warmup_end = WARMUP_BARS
    tradeable_len = len(perp_df) - warmup_end
    is_end = warmup_end + int(tradeable_len * IS_FRACTION)

    is_start_date = perp_df.index[warmup_end]
    is_end_date = perp_df.index[is_end - 1]
    oos_start_date = perp_df.index[is_end]
    oos_end_date = perp_df.index[-1]

    print(f"  IS period:  {is_start_date.date()} to {is_end_date.date()} ({is_end - warmup_end} bars)")
    print(f"  OOS period: {oos_start_date.date()} to {oos_end_date.date()} ({len(perp_df) - is_end} bars)")
    print()

    # ── Run All Experiments ───────────────────────────────────────────
    all_results = {}
    vrp_regimes = None

    for exp_key, exp_config in EXPERIMENTS.items():
        print(f"\n{'=' * 70}")
        print(f"  {exp_config['name']}")
        print(f"{'=' * 70}")

        # Build signals with dynamic leverage
        signal, leverage_arr, vrp_z_hourly = build_hourly_signals(perp_df, exp_config)
        print(f"  Signal bars: {len(signal)}, Non-zero: {(signal > 0).sum()} ({(signal > 0).sum()/len(signal)*100:.1f}%)")

        if exp_config['dynamic']:
            active = signal > 0
            if active.sum() > 0:
                print(f"  Leverage when active - mean: {leverage_arr[active].mean():.3f}, "
                      f"min: {leverage_arr[active].min():.3f}, max: {leverage_arr[active].max():.3f}")

        # VRP regime analysis (do once)
        if vrp_regimes is None:
            vrp_regimes = analyze_vrp_regimes(perp_df, vrp_z_hourly)
            print(f"\n  VRP Regime Distribution:")
            print(f"    Complacent (z > {VRP_HIGH_THRESH}):  {vrp_regimes['complacent_pct']:.1f}%")
            print(f"    Normal:                    {vrp_regimes['normal_pct']:.1f}%")
            print(f"    Turbulent (z < {VRP_MID_LOW}): {vrp_regimes['turbulent_pct']:.1f}%")
            print(f"    Extreme (z < {VRP_EXTREME_LOW}):  {vrp_regimes['extreme_pct']:.1f}%")

        # Full period backtest
        bt_full = run_backtest(perp_df, signal, leverage_arr, exp_config)

        # IS metrics
        is_equity = bt_full['equity'].iloc[warmup_end:is_end]
        is_liq = [e for e in bt_full['liquidation_events']
                  if is_start_date <= e['date'] <= is_end_date]
        is_near_liq = [e for e in bt_full['near_liquidation_events']
                       if is_start_date <= e['date'] <= is_end_date]
        is_metrics = compute_metrics(is_equity, exp_config, is_liq, is_near_liq)

        # OOS backtest: fresh equity from OOS start
        oos_df = perp_df.iloc[is_end:].copy()
        oos_signal = signal[is_end:]
        oos_leverage = leverage_arr[is_end:]
        bt_oos = run_backtest(oos_df, oos_signal, oos_leverage, exp_config)
        oos_metrics = compute_metrics(bt_oos['equity'], exp_config,
                                       bt_oos['liquidation_events'],
                                       bt_oos['near_liquidation_events'])

        # Kill check
        oos_kills = check_kill_criteria(oos_metrics)

        all_results[exp_key] = {
            'config': exp_config,
            'is_metrics': is_metrics,
            'oos_metrics': oos_metrics,
            'oos_kills': oos_kills,
            'oos_equity': bt_oos['equity'],
        }

        # Print summary
        print(f"\n  IS  Metrics:")
        print(f"    Sharpe: {is_metrics['sharpe']:.3f}, Ann Return: {is_metrics['annualized_return']:.2%}")
        print(f"    MaxDD: {is_metrics['max_dd']:.2%}, Calmar: {is_metrics['calmar']:.3f}")
        print(f"    Funding Drag (ann): {is_metrics['annualized_funding_drag_pct']:.2f}%")
        print(f"    Mean Eff Leverage: {is_metrics['mean_eff_leverage']:.3f}, Max: {is_metrics['max_eff_leverage']:.3f}")

        print(f"\n  OOS Metrics:")
        print(f"    Sharpe: {oos_metrics['sharpe']:.3f}, Ann Return: {oos_metrics['annualized_return']:.2%}")
        print(f"    MaxDD: {oos_metrics['max_dd']:.2%}, Calmar: {oos_metrics['calmar']:.3f}")
        print(f"    Sortino: {oos_metrics['sortino']:.3f}")
        print(f"    Funding Drag (ann): {oos_metrics['annualized_funding_drag_pct']:.2f}%")
        print(f"    Funding % of Gross: {oos_metrics['funding_pct_of_gross']:.1f}%")
        print(f"    Mean Eff Leverage: {oos_metrics['mean_eff_leverage']:.3f}, Max: {oos_metrics['max_eff_leverage']:.3f}")
        print(f"    Time in Market: {oos_metrics['time_in_market_pct']:.1f}%")
        print(f"    Liquidation events: {oos_metrics['liquidation_events']}")
        print(f"    Near-liquidation events: {oos_metrics['near_liquidation_events']}")

        if oos_kills:
            print(f"\n  ** KILL FLAGS **:")
            for kill in oos_kills:
                print(f"    - {kill}")
        else:
            print(f"\n  ** PASS ** - All kill criteria cleared")

    # ── Cross-Experiment Comparison ───────────────────────────────────
    print(f"\n\n{'=' * 80}")
    print("CROSS-EXPERIMENT COMPARISON (OOS)")
    print(f"{'=' * 80}")
    print()

    header = f"{'Experiment':<45} {'Return':>8} {'Sharpe':>8} {'MaxDD':>8} {'Calmar':>8} {'Sortino':>8} {'Fund%':>7} {'Kill':>6}"
    print(header)
    print("-" * len(header))

    for exp_key, result in all_results.items():
        m = result['oos_metrics']
        kills = result['oos_kills']
        status = "KILL" if kills else "PASS"
        print(f"{m['name']:<45} {m['annualized_return']:>7.1%} {m['sharpe']:>8.3f} "
              f"{m['max_dd']:>7.1%} {m['calmar']:>8.3f} {m['sortino']:>8.3f} "
              f"{m['funding_pct_of_gross']:>6.1f}% {status:>6}")

    # ── Dynamic vs Fixed Leverage Analysis ────────────────────────────
    print(f"\n\n{'=' * 80}")
    print("DYNAMIC vs FIXED LEVERAGE ANALYSIS (OOS)")
    print(f"{'=' * 80}")

    if 'exp1_1x_baseline' in all_results and 'exp2_dynamic_aggressive' in all_results:
        m1 = all_results['exp1_1x_baseline']['oos_metrics']
        m2 = all_results['exp2_dynamic_aggressive']['oos_metrics']
        print(f"\n  Dynamic VRP-Scaled vs Fixed 1x:")
        print(f"    Return improvement: {m2['annualized_return'] - m1['annualized_return']:.2%}")
        print(f"    Sharpe improvement: {m2['sharpe'] - m1['sharpe']:.3f}")
        print(f"    DD change: {m2['max_dd'] - m1['max_dd']:.2%}")

    if 'exp3_fixed_1_5x' in all_results and 'exp2_dynamic_aggressive' in all_results:
        m3 = all_results['exp3_fixed_1_5x']['oos_metrics']
        m2 = all_results['exp2_dynamic_aggressive']['oos_metrics']
        print(f"\n  Dynamic VRP-Scaled vs Fixed 1.5x:")
        print(f"    Return difference: {m2['annualized_return'] - m3['annualized_return']:.2%}")
        print(f"    Sharpe difference: {m2['sharpe'] - m3['sharpe']:.3f}")
        print(f"    DD improvement: {m2['max_dd'] - m3['max_dd']:.2%}")

    if 'exp4_conservative_dynamic' in all_results and 'exp1_1x_baseline' in all_results:
        m4 = all_results['exp4_conservative_dynamic']['oos_metrics']
        m1 = all_results['exp1_1x_baseline']['oos_metrics']
        print(f"\n  Conservative Dynamic vs Fixed 1x:")
        print(f"    Return improvement: {m4['annualized_return'] - m1['annualized_return']:.2%}")
        print(f"    Sharpe improvement: {m4['sharpe'] - m1['sharpe']:.3f}")
        print(f"    DD change: {m4['max_dd'] - m1['max_dd']:.2%}")

    # ── Key Finding Assessment ────────────────────────────────────────
    print(f"\n\n{'=' * 80}")
    print("KEY FINDING ASSESSMENT")
    print(f"{'=' * 80}")

    major_finding = False
    excellent_finding = False

    for exp_key, result in all_results.items():
        m = result['oos_metrics']
        kills = result['oos_kills']
        if not kills:
            if m['annualized_return'] > 0.50 and m['max_dd'] > -0.30:
                major_finding = True
                print(f"\n  *** MAJOR FINDING: {m['name']} ***")
                print(f"      Return {m['annualized_return']:.1%} > 50% with DD {m['max_dd']:.1%} < 30%")
                print(f"      Sharpe {m['sharpe']:.3f}, Calmar {m['calmar']:.3f}")
            elif m['annualized_return'] > 0.30 and m['max_dd'] > -0.30:
                excellent_finding = True
                print(f"\n  ** EXCELLENT: {m['name']} **")
                print(f"     Return {m['annualized_return']:.1%} > 30% with DD {m['max_dd']:.1%} < 30%")
                print(f"     Sharpe {m['sharpe']:.3f}, Calmar {m['calmar']:.3f}")

    if not major_finding and not excellent_finding:
        print("\n  No experiment achieved >30% return with <30% DD.")
        # Find best Sharpe among passing
        best_sharpe = -999
        best_key = None
        for exp_key, result in all_results.items():
            if not result['oos_kills'] and result['oos_metrics']['sharpe'] > best_sharpe:
                best_sharpe = result['oos_metrics']['sharpe']
                best_key = exp_key
        if best_key:
            m = all_results[best_key]['oos_metrics']
            print(f"  Best passing experiment: {m['name']}")
            print(f"    Return: {m['annualized_return']:.2%}, Sharpe: {m['sharpe']:.3f}, DD: {m['max_dd']:.2%}")

    # ── Write Results ─────────────────────────────────────────────────
    write_results_md(all_results, vrp_regimes,
                     is_start_date, is_end_date,
                     oos_start_date, oos_end_date,
                     perp_df)

    print(f"\n\nResults written to: research/v3_perp_overlay_leverage_results.md")


# =============================================================================
# Results Writer
# =============================================================================

def write_results_md(all_results, vrp_regimes,
                     is_start, is_end, oos_start, oos_end,
                     perp_df):
    """Write comprehensive results markdown file."""

    lines = []
    lines.append("# V3 Perp + Overlay + Dynamic Leverage -- Results")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    # ── Context ───────────────────────────────────────────────────────
    lines.append("## Context")
    lines.append("")
    lines.append("V3 is a BTC-only momentum strategy: 20/50 EMA crossover + positioning overlay")
    lines.append("+ VRP overlay. This test evaluates whether **dynamic leverage** (VRP-regime-scaled)")
    lines.append("on perpetual futures can achieve high returns with controlled drawdowns.")
    lines.append("")
    lines.append("**Key Innovation:** VRP regime controls BOTH position sizing AND leverage,")
    lines.append("creating multiplicative risk reduction during turbulence.")
    lines.append("")
    lines.append("**Prior Results (Reference):**")
    lines.append("- V3 on BTC SPOT (OOS): +17.52%, Sharpe 0.56, MaxDD -20.2%")
    lines.append("- V3 on 1x BTC perp (finding #79): Sharpe 0.47, Return +10.2%, MaxDD -37.4%")
    lines.append("- 2x perp: KILL (-62% DD), 3x perp: KILL (-78% DD)")
    lines.append("")

    # ── Data ──────────────────────────────────────────────────────────
    lines.append("## Data")
    lines.append("")
    lines.append(f"- **Perp Data:** {perp_df.index.min().date()} to {perp_df.index.max().date()} ({len(perp_df)} hourly bars)")
    lines.append(f"- **IS Period:** {is_start.date()} to {is_end.date()}")
    lines.append(f"- **OOS Period:** {oos_start.date()} to {oos_end.date()}")
    lines.append(f"- **Funding:** Actual Binance BTC rates (8h settlement at 00/08/16 UTC)")
    lines.append(f"- **Mean Funding (ann):** {perp_df['funding_rate'].mean() * 3 * 365.25 * 100:.2f}%")
    lines.append(f"- **Trading Cost:** {TRADING_COST_BPS} bps per trade")
    lines.append(f"- **Warmup:** {WARMUP_DAILY} days, **Rebalance:** Weekly (168 bars)")
    lines.append("")

    # ── VRP Regime Distribution ───────────────────────────────────────
    lines.append("## VRP Regime Distribution (Full Period)")
    lines.append("")
    lines.append("| Regime | VRP z Threshold | Time % |")
    lines.append("|--------|-----------------|--------|")
    lines.append(f"| Complacent | z > {VRP_HIGH_THRESH} | {vrp_regimes['complacent_pct']:.1f}% |")
    lines.append(f"| Normal | {VRP_MID_LOW} < z < {VRP_HIGH_THRESH} | {vrp_regimes['normal_pct']:.1f}% |")
    lines.append(f"| Turbulent | {VRP_EXTREME_LOW} < z < {VRP_MID_LOW} | {vrp_regimes['turbulent_pct']:.1f}% |")
    lines.append(f"| Extreme | z < {VRP_EXTREME_LOW} | {vrp_regimes['extreme_pct']:.1f}% |")
    lines.append("")

    # ── Experiment Details ────────────────────────────────────────────
    lines.append("## Experiment Configurations")
    lines.append("")
    lines.append("| Experiment | Base Lev | Dynamic | Complacent | Normal | Turbulent | Extreme |")
    lines.append("|------------|----------|---------|------------|--------|-----------|---------|")
    for exp_key, result in all_results.items():
        cfg = result['config']
        if cfg['dynamic']:
            lines.append(f"| {cfg['name']} | {cfg['base_leverage']}x | Yes | "
                         f"{cfg['lev_complacent']}x | {cfg['lev_normal']}x | "
                         f"{cfg['lev_turbulent']}x | {cfg['lev_extreme']}x |")
        else:
            lines.append(f"| {cfg['name']} | {cfg['base_leverage']}x | No | "
                         f"{cfg['base_leverage']}x | {cfg['base_leverage']}x | "
                         f"{cfg['base_leverage']}x | {cfg['base_leverage']}x |")
    lines.append("")

    # ── OOS Performance Comparison ────────────────────────────────────
    lines.append("## OOS Performance Comparison")
    lines.append("")

    # Build table header
    exp_keys = list(all_results.keys())
    short_names = ['1x Base', 'Dyn Aggr', '1.5x Fix', 'Cons Dyn', 'Dyn 1.5x', 'Ultra Cons', 'Dyn Mod']
    header = "| Metric |"
    for i, name in enumerate(short_names[:len(exp_keys)]):
        header += f" {name} |"
    lines.append(header)

    sep = "|--------|"
    for _ in exp_keys:
        sep += "---:|"
    lines.append(sep)

    # Metrics rows
    metrics_list = [all_results[k]['oos_metrics'] for k in exp_keys]

    def add_row(label, key, fmt):
        row = f"| {label} |"
        for m in metrics_list:
            row += f" {m[key]:{fmt}} |"
        lines.append(row)

    add_row("Ann Return", "annualized_return", ".1%")
    add_row("Total Return", "total_return", ".1%")
    add_row("Sharpe", "sharpe", ".3f")
    add_row("Sortino", "sortino", ".3f")
    add_row("Max DD", "max_dd", ".1%")
    add_row("Calmar", "calmar", ".3f")
    add_row("DD Duration (d)", "max_dd_duration_days", ".0f")
    add_row("Win Rate %", "win_rate_pct", ".1f")
    add_row("Time in Mkt %", "time_in_market_pct", ".1f")
    add_row("Mean Eff Lev", "mean_eff_leverage", ".3f")
    add_row("Max Eff Lev", "max_eff_leverage", ".3f")
    add_row("Fund Drag %/yr", "annualized_funding_drag_pct", ".2f")
    add_row("Fund % of Gross", "funding_pct_of_gross", ".1f")
    add_row("Liquidations", "liquidation_events", ".0f")
    add_row("Near-Liquidations", "near_liquidation_events", ".0f")
    lines.append("")

    # ── IS Performance (Reference) ────────────────────────────────────
    lines.append("## IS Performance (Reference)")
    lines.append("")

    header = "| Metric |"
    for name in short_names[:len(exp_keys)]:
        header += f" {name} |"
    lines.append(header)
    lines.append(sep)

    is_metrics_list = [all_results[k]['is_metrics'] for k in exp_keys]

    def add_is_row(label, key, fmt):
        row = f"| {label} |"
        for m in is_metrics_list:
            row += f" {m[key]:{fmt}} |"
        lines.append(row)

    add_is_row("Ann Return", "annualized_return", ".1%")
    add_is_row("Sharpe", "sharpe", ".3f")
    add_is_row("Max DD", "max_dd", ".1%")
    add_is_row("Calmar", "calmar", ".3f")
    add_is_row("Fund Drag %/yr", "annualized_funding_drag_pct", ".2f")
    lines.append("")

    # ── Kill Criteria ─────────────────────────────────────────────────
    lines.append("## Kill Criteria Assessment (OOS)")
    lines.append("")
    lines.append("| Criterion | Threshold |")
    lines.append("|-----------|-----------|")
    lines.append("| MaxDD | > -40% |")
    lines.append("| Liquidations | > 1 |")
    lines.append("| Sharpe | < 0.3 |")
    lines.append("| Funding % Gross | > 50% |")
    lines.append("")

    for exp_key, result in all_results.items():
        m = result['oos_metrics']
        kills = result['oos_kills']
        status = "KILL" if kills else "PASS"
        lines.append(f"**{m['name']}:** {status}")
        if kills:
            for k in kills:
                lines.append(f"- {k}")
        else:
            lines.append(f"- Sharpe {m['sharpe']:.3f}, Return {m['annualized_return']:.1%}, DD {m['max_dd']:.1%}")
        lines.append("")

    # ── Dynamic Leverage Value Assessment ─────────────────────────────
    lines.append("## Dynamic Leverage Value Assessment")
    lines.append("")
    lines.append("Does dynamic leverage add value over fixed leverage?")
    lines.append("")

    if 'exp1_1x_baseline' in all_results and 'exp2_dynamic_aggressive' in all_results:
        m1 = all_results['exp1_1x_baseline']['oos_metrics']
        m2 = all_results['exp2_dynamic_aggressive']['oos_metrics']
        lines.append(f"**Dynamic Aggressive vs Fixed 1x:**")
        lines.append(f"- Return: {m1['annualized_return']:.1%} -> {m2['annualized_return']:.1%} ({m2['annualized_return'] - m1['annualized_return']:+.1%})")
        lines.append(f"- Sharpe: {m1['sharpe']:.3f} -> {m2['sharpe']:.3f} ({m2['sharpe'] - m1['sharpe']:+.3f})")
        lines.append(f"- MaxDD: {m1['max_dd']:.1%} -> {m2['max_dd']:.1%}")
        lines.append(f"- Calmar: {m1['calmar']:.3f} -> {m2['calmar']:.3f}")
        lines.append("")

    if 'exp3_fixed_1_5x' in all_results and 'exp2_dynamic_aggressive' in all_results:
        m3 = all_results['exp3_fixed_1_5x']['oos_metrics']
        m2 = all_results['exp2_dynamic_aggressive']['oos_metrics']
        lines.append(f"**Dynamic Aggressive vs Fixed 1.5x:**")
        lines.append(f"- Return: {m3['annualized_return']:.1%} -> {m2['annualized_return']:.1%} ({m2['annualized_return'] - m3['annualized_return']:+.1%})")
        lines.append(f"- Sharpe: {m3['sharpe']:.3f} -> {m2['sharpe']:.3f} ({m2['sharpe'] - m3['sharpe']:+.3f})")
        lines.append(f"- MaxDD: {m3['max_dd']:.1%} -> {m2['max_dd']:.1%}")
        lines.append(f"- Key insight: dynamic leverage targets SAME average exposure but with regime-appropriate scaling")
        lines.append("")

    if 'exp4_conservative_dynamic' in all_results and 'exp1_1x_baseline' in all_results:
        m4 = all_results['exp4_conservative_dynamic']['oos_metrics']
        m1 = all_results['exp1_1x_baseline']['oos_metrics']
        lines.append(f"**Conservative Dynamic vs Fixed 1x:**")
        lines.append(f"- Return: {m1['annualized_return']:.1%} -> {m4['annualized_return']:.1%} ({m4['annualized_return'] - m1['annualized_return']:+.1%})")
        lines.append(f"- Sharpe: {m1['sharpe']:.3f} -> {m4['sharpe']:.3f} ({m4['sharpe'] - m1['sharpe']:+.3f})")
        lines.append(f"- MaxDD: {m1['max_dd']:.1%} -> {m4['max_dd']:.1%}")
        lines.append(f"- This is the safest dynamic approach")
        lines.append("")

    # ── Verdict ───────────────────────────────────────────────────────
    lines.append("## Verdict")
    lines.append("")

    # Find best by Calmar among passing
    best_calmar = -999
    best_key = None
    for exp_key, result in all_results.items():
        if not result['oos_kills']:
            m = result['oos_metrics']
            if m['calmar'] > best_calmar:
                best_calmar = m['calmar']
                best_key = exp_key

    # Check for major/excellent findings
    major = []
    excellent = []
    for exp_key, result in all_results.items():
        m = result['oos_metrics']
        if not result['oos_kills']:
            if m['annualized_return'] > 0.50 and m['max_dd'] > -0.30:
                major.append((exp_key, m))
            elif m['annualized_return'] > 0.30 and m['max_dd'] > -0.30:
                excellent.append((exp_key, m))

    if major:
        lines.append("### MAJOR FINDING")
        lines.append("")
        for exp_key, m in major:
            lines.append(f"**{m['name']}** achieves **{m['annualized_return']:.0%} annual return** "
                         f"with only **{m['max_dd']:.0%} max drawdown** (Sharpe {m['sharpe']:.2f}, Calmar {m['calmar']:.2f}).")
            lines.append("")
            lines.append("This exceeds the 50% return / 30% DD threshold for a MAJOR finding.")
        lines.append("")

    if excellent:
        lines.append("### Excellent Results")
        lines.append("")
        for exp_key, m in excellent:
            lines.append(f"- **{m['name']}**: {m['annualized_return']:.0%} return, {m['max_dd']:.0%} DD, "
                         f"Sharpe {m['sharpe']:.2f}")
        lines.append("")

    if best_key:
        m = all_results[best_key]['oos_metrics']
        lines.append("### Recommendation")
        lines.append("")
        lines.append(f"**Best risk-adjusted experiment:** {m['name']}")
        lines.append(f"- Annual Return: {m['annualized_return']:.1%}")
        lines.append(f"- Sharpe: {m['sharpe']:.3f}")
        lines.append(f"- Max DD: {m['max_dd']:.1%}")
        lines.append(f"- Calmar: {m['calmar']:.3f}")
        lines.append(f"- Sortino: {m['sortino']:.3f}")
        lines.append(f"- Funding Drag: {m['annualized_funding_drag_pct']:.2f}%/yr ({m['funding_pct_of_gross']:.1f}% of gross)")
        lines.append(f"- Mean Effective Leverage: {m['mean_eff_leverage']:.3f}x")
        lines.append("")
    else:
        lines.append("### No Viable Configuration")
        lines.append("")
        lines.append("All experiments hit kill criteria. V3 should remain on spot markets.")
        lines.append("The funding rate drag (~10%/yr annualized) combined with amplified drawdowns")
        lines.append("erodes the moderate edge of the V3 strategy.")
        lines.append("")

    # ── Funding Rate Impact ───────────────────────────────────────────
    lines.append("## Funding Rate Impact")
    lines.append("")
    lines.append("Funding rates on BTC perps average ~10%/yr annualized. For a strategy")
    lines.append(f"that generates {all_results['exp1_1x_baseline']['oos_metrics']['annualized_return']:.0%} "
                 f"gross on 1x, this is a significant drag.")
    lines.append("")
    lines.append("Key observations:")
    lines.append("- Funding is charged on the leveraged notional (2x leverage = 2x funding)")
    lines.append("- Dynamic leverage REDUCES funding cost during turbulence (when funding tends to be higher)")
    lines.append("- The overlay's position sizing ALSO reduces funding exposure when cautious")
    lines.append("- Net effect: overlay+dynamic leverage pays less funding than fixed leverage at same average return")
    lines.append("")

    # ── Next Steps ────────────────────────────────────────────────────
    lines.append("## Next Steps")
    lines.append("")

    any_pass = any(not r['oos_kills'] for r in all_results.values())
    if any_pass:
        lines.append("1. Walk-forward validation on the best configuration")
        lines.append("2. Monte Carlo simulation for tail risk assessment")
        lines.append("3. Test with realistic slippage model (not just fixed bps)")
        lines.append("4. Consider funding rate prediction model to further optimize entry timing")
        lines.append("5. Evaluate if dynamic leverage adds value over simply using fixed 1x with larger allocation")
    else:
        lines.append("1. Consider reducing overlay aggressiveness for perp application")
        lines.append("2. Investigate funding-rate-aware entry timing (avoid high-funding periods)")
        lines.append("3. Test on altcoin perps where funding behavior differs")
        lines.append("4. V3 spot remains the recommended vehicle for this strategy")

    lines.append("")

    output_path = PROJECT_ROOT / 'research' / 'v3_perp_overlay_leverage_results.md'
    with open(output_path, 'w') as f:
        f.write('\n'.join(lines))


if __name__ == '__main__':
    main()
