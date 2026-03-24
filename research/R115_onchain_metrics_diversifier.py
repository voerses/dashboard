#!/workspace/venv/bin/python
"""
R115: BTC On-Chain Metrics as Portfolio Diversifier for V3
==========================================================

Objective: Test on-chain BTC metrics as diversifier signals for V3 (EMA
trend-following). On-chain data represents fundamentally different information
(blockchain activity vs price patterns), which should provide genuine decorrelation.

Signal Candidates:
  1. Exchange netflow 5d/10d/20d sum (Coinmetrics)
  2. Transaction volume momentum 7d/14d (Blockchain.com)
  3. Active address growth 7d/14d (Blockchain.com)
  4. Exchange balance change 30d (Santiment -- short history, flagged)
  5. On-chain velocity (tx volume / market cap proxy)
  6. Composite on-chain z-score

Data Sources:
  - Blockchain.com on-chain: 725 rows, 2024-03 to 2026-03
  - Coinmetrics exchange flow: 568 rows, 2024-09 to 2026-03
  - Santiment exchange flow: 266 rows, 2025-06 to 2026-02
  - BTC spot 1h -> daily

Previous Finding #27: BTC exchange netflow 5d sum IC=+0.149 (t=3.44)

Kill Criteria:
  - IC < 0.02 across all horizons -> KILL
  - Less than 3 WF windows -> NOTE as insufficient data (don't kill)
  - WF mean Sharpe < 0.3 -> KILL
  - Correlation with V3 > 0.5 -> KILL

Author: Quant Research Agent
Date: 2026-03-24
"""

import warnings
import numpy as np
import pandas as pd
from scipy import stats
from pathlib import Path
from itertools import product

warnings.filterwarnings('ignore')

# -- Paths -------------------------------------------------------------------
PROJECT_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = PROJECT_DIR / 'data'
OUTPUT_PY = PROJECT_DIR / 'research' / 'R115_onchain_metrics_diversifier.py'
OUTPUT_MD = PROJECT_DIR / 'research' / 'R115_onchain_metrics_diversifier.md'

COST_BPS = 10  # round-trip cost in basis points
ANNUALIZE = np.sqrt(365)  # daily -> annual

report_lines = []


def report(line=""):
    """Append line to report and print it."""
    print(line)
    report_lines.append(line)


# ============================================================================
# DATA LOADING
# ============================================================================

def load_btc_daily():
    """Load BTC 1h spot and resample to daily."""
    report("[DATA] Loading BTC 1h spot and resampling to daily...")
    df = pd.read_parquet(DATA_DIR / 'spot/1h_cache/BTC_1h.parquet')
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep='first')]

    daily = pd.DataFrame()
    daily['close'] = df['close'].resample('1D').last().dropna()
    daily['open'] = df['open'].resample('1D').first()
    daily['high'] = df['high'].resample('1D').max()
    daily['low'] = df['low'].resample('1D').min()
    daily['volume'] = df['volume'].resample('1D').sum()
    daily = daily.dropna()
    daily['ret'] = daily['close'].pct_change()

    # Forward returns for IC calculation
    for h in [1, 3, 7, 14]:
        daily[f'fwd_ret_{h}d'] = daily['close'].pct_change(h).shift(-h)

    report(f"  BTC daily: {daily.index.min().date()} to {daily.index.max().date()}, {len(daily)} days")
    return daily


def load_blockchain_com():
    """Load Blockchain.com on-chain metrics."""
    report("[DATA] Loading Blockchain.com on-chain data...")
    df = pd.read_csv(
        DATA_DIR / 'alternative/exchange_netflow/blockchain_com_btc_onchain.csv',
        parse_dates=['date']
    )
    df = df.set_index('date').sort_index()
    df = df[~df.index.duplicated(keep='first')]
    # Rename for easier use
    df = df.rename(columns={
        'estimated-transaction-volume-usd': 'tx_volume_usd',
        'trade-volume': 'trade_volume',
        'n-unique-addresses': 'unique_addresses',
        'n-transactions': 'n_transactions',
        'output-volume': 'output_volume',
    })
    report(f"  Blockchain.com: {df.index.min().date()} to {df.index.max().date()}, {len(df)} rows")
    return df


def load_coinmetrics():
    """Load Coinmetrics BTC exchange flow data."""
    report("[DATA] Loading Coinmetrics BTC exchange flow...")
    df = pd.read_csv(
        DATA_DIR / 'alternative/exchange_netflow/coinmetrics_btc_exchange_flow.csv'
    )
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date').sort_index()
    df = df[~df.index.duplicated(keep='first')]
    # Keep only the numeric columns we need
    cols = ['FlowInExNtv', 'FlowInExUSD', 'FlowOutExNtv', 'FlowOutExUSD',
            'NetFlowNtv', 'NetFlowUSD']
    df = df[cols].apply(pd.to_numeric, errors='coerce')
    report(f"  Coinmetrics: {df.index.min().date()} to {df.index.max().date()}, {len(df)} rows")
    return df


def load_santiment():
    """Load Santiment BTC exchange flow data."""
    report("[DATA] Loading Santiment BTC exchange flow...")
    df = pd.read_csv(
        DATA_DIR / 'alternative/exchange_netflow/santiment_btc_exchange_flow.csv'
    )
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date').sort_index()
    df = df[~df.index.duplicated(keep='first')]
    cols = ['exchange_balance', 'exchange_inflow', 'exchange_outflow', 'exchange_netflow']
    df = df[cols].apply(pd.to_numeric, errors='coerce')
    report(f"  Santiment: {df.index.min().date()} to {df.index.max().date()}, {len(df)} rows")
    return df


def compute_v3_signal(btc_daily):
    """Compute V3 EMA trend-following signal for correlation comparison."""
    ema20 = btc_daily['close'].ewm(span=20, adjust=False).mean()
    ema50 = btc_daily['close'].ewm(span=50, adjust=False).mean()
    signal = pd.Series(0.0, index=btc_daily.index)
    signal[ema20 > ema50] = 1.0
    signal[ema20 <= ema50] = -1.0
    # Shift by 1 to avoid look-ahead
    signal = signal.shift(1)
    v3_ret = signal * btc_daily['ret']
    return v3_ret


# ============================================================================
# SIGNAL GENERATION
# ============================================================================

def generate_signals(btc_daily, blockchain, coinmetrics, santiment):
    """Generate all on-chain signal candidates and return as DataFrame."""
    report("\n" + "=" * 80)
    report("SIGNAL GENERATION")
    report("=" * 80)

    signals = pd.DataFrame(index=btc_daily.index)

    # ---- Signal 1: Exchange netflow rolling sums (Coinmetrics) ----
    # Negative netflow = outflow from exchanges = accumulation = bullish
    # So we INVERT: signal = -netflow (outflow -> positive signal)
    report("\n[SIG1] Exchange Netflow Rolling Sums (Coinmetrics)")
    for window in [5, 10, 20]:
        nf_sum = coinmetrics['NetFlowNtv'].rolling(window).sum()
        # Shift by 1 day to avoid look-ahead (use yesterday's on-chain data)
        sig = (-nf_sum).shift(1)
        col = f'netflow_{window}d'
        signals[col] = sig.reindex(btc_daily.index)
        n_valid = signals[col].notna().sum()
        report(f"  netflow_{window}d: {n_valid} valid observations")

    # Also USD-denominated
    for window in [5, 10, 20]:
        nf_sum = coinmetrics['NetFlowUSD'].rolling(window).sum()
        sig = (-nf_sum).shift(1)
        col = f'netflow_usd_{window}d'
        signals[col] = sig.reindex(btc_daily.index)

    # ---- Signal 2: Transaction Volume Momentum (Blockchain.com) ----
    report("\n[SIG2] Transaction Volume Momentum (Blockchain.com)")
    for window in [7, 14]:
        tx_mom = blockchain['tx_volume_usd'].pct_change(window)
        sig = tx_mom.shift(1)
        col = f'txvol_mom_{window}d'
        signals[col] = sig.reindex(btc_daily.index)
        n_valid = signals[col].notna().sum()
        report(f"  txvol_mom_{window}d: {n_valid} valid observations")

    # ---- Signal 3: Active Address Growth (Blockchain.com) ----
    report("\n[SIG3] Active Address Growth (Blockchain.com)")
    for window in [7, 14]:
        addr_growth = blockchain['unique_addresses'].pct_change(window)
        sig = addr_growth.shift(1)
        col = f'addr_growth_{window}d'
        signals[col] = sig.reindex(btc_daily.index)
        n_valid = signals[col].notna().sum()
        report(f"  addr_growth_{window}d: {n_valid} valid observations")

    # ---- Signal 4: Exchange Balance Change (Santiment -- short history) ----
    report("\n[SIG4] Exchange Balance Change (Santiment -- SHORT HISTORY)")
    for window in [14, 30]:
        bal_change = santiment['exchange_balance'].diff(window)
        # Negative balance change = coins leaving exchanges = bullish
        sig = (-bal_change).shift(1)
        col = f'exbal_change_{window}d'
        signals[col] = sig.reindex(btc_daily.index)
        n_valid = signals[col].notna().sum()
        report(f"  exbal_change_{window}d: {n_valid} valid observations (WARNING: ~{n_valid} days only)")

    # ---- Signal 5: On-Chain Velocity (Blockchain.com) ----
    # velocity = tx_volume / (close * ~19.7M BTC supply approx)
    report("\n[SIG5] On-Chain Velocity")
    btc_supply_approx = 19.7e6  # approximate BTC supply
    market_cap_proxy = btc_daily['close'] * btc_supply_approx
    velocity = blockchain['tx_volume_usd'].reindex(btc_daily.index) / market_cap_proxy
    for window in [7, 14]:
        vel_mom = velocity.pct_change(window)
        sig = vel_mom.shift(1)
        col = f'velocity_mom_{window}d'
        signals[col] = sig.reindex(btc_daily.index)
        n_valid = signals[col].notna().sum()
        report(f"  velocity_mom_{window}d: {n_valid} valid observations")

    # ---- Signal 6: Composite On-Chain Z-Score ----
    report("\n[SIG6] Composite On-Chain Z-Score")
    # Z-score of: netflow (inverted), address growth, tx volume momentum
    # Uses 60-day rolling stats for z-scoring
    lookback = 60
    components = {}

    # Netflow component (from Coinmetrics)
    nf_10d = (-coinmetrics['NetFlowNtv'].rolling(10).sum()).shift(1)
    nf_10d = nf_10d.reindex(btc_daily.index)
    nf_z = (nf_10d - nf_10d.rolling(lookback).mean()) / nf_10d.rolling(lookback).std()
    components['netflow_z'] = nf_z

    # Address growth component
    ag_7d = blockchain['unique_addresses'].pct_change(7).shift(1)
    ag_7d = ag_7d.reindex(btc_daily.index)
    ag_z = (ag_7d - ag_7d.rolling(lookback).mean()) / ag_7d.rolling(lookback).std()
    components['addr_z'] = ag_z

    # Tx volume momentum component
    tv_7d = blockchain['tx_volume_usd'].pct_change(7).shift(1)
    tv_7d = tv_7d.reindex(btc_daily.index)
    tv_z = (tv_7d - tv_7d.rolling(lookback).mean()) / tv_7d.rolling(lookback).std()
    components['txvol_z'] = tv_z

    # Equal-weight composite
    composite = pd.DataFrame(components)
    signals['composite_z'] = composite.mean(axis=1)
    n_valid = signals['composite_z'].notna().sum()
    report(f"  composite_z: {n_valid} valid observations")

    # Also a variant weighting netflow heavier (2:1:1)
    signals['composite_z_nf_heavy'] = (
        2 * composite['netflow_z'] + composite['addr_z'] + composite['txvol_z']
    ) / 4
    n_valid = signals['composite_z_nf_heavy'].notna().sum()
    report(f"  composite_z_nf_heavy: {n_valid} valid observations")

    report(f"\nTotal signal columns: {len(signals.columns)}")
    report(f"Signal columns: {signals.columns.tolist()}")
    return signals


# ============================================================================
# IC ANALYSIS
# ============================================================================

def compute_ic(signal_series, forward_returns, method='spearman'):
    """Compute information coefficient (rank correlation) between signal and forward returns."""
    common = signal_series.dropna().index.intersection(forward_returns.dropna().index)
    if len(common) < 30:
        return np.nan, np.nan, len(common)
    sig = signal_series.loc[common]
    fwd = forward_returns.loc[common]
    if method == 'spearman':
        ic, pval = stats.spearmanr(sig, fwd)
    else:
        ic, pval = stats.pearsonr(sig, fwd)
    return ic, pval, len(common)


def ic_scan(signals, btc_daily):
    """Scan all signals across multiple horizons."""
    report("\n" + "=" * 80)
    report("IC SCAN (Spearman Rank Correlation)")
    report("=" * 80)

    horizons = [1, 3, 7, 14]
    results = []

    for sig_name in signals.columns:
        row = {'signal': sig_name}
        for h in horizons:
            fwd_col = f'fwd_ret_{h}d'
            ic, pval, n_obs = compute_ic(signals[sig_name], btc_daily[fwd_col])
            t_stat = ic * np.sqrt((n_obs - 2) / (1 - ic**2)) if abs(ic) < 1 and n_obs > 2 else 0
            row[f'ic_{h}d'] = ic
            row[f'pval_{h}d'] = pval
            row[f'tstat_{h}d'] = t_stat
            row[f'n_{h}d'] = n_obs
        results.append(row)

    ic_df = pd.DataFrame(results)

    # Display table
    report(f"\n{'Signal':<25} | {'IC 1d':>8} {'t':>6} | {'IC 3d':>8} {'t':>6} | {'IC 7d':>8} {'t':>6} | {'IC 14d':>8} {'t':>6} | N")
    report("-" * 110)
    for _, r in ic_df.iterrows():
        line = f"{r['signal']:<25}"
        for h in horizons:
            ic_val = r[f'ic_{h}d']
            t_val = r[f'tstat_{h}d']
            ic_str = f"{ic_val:+.4f}" if not np.isnan(ic_val) else "    NaN"
            t_str = f"{t_val:+.2f}" if not np.isnan(t_val) else "  NaN"
            line += f" | {ic_str} {t_str}"
        line += f" | {int(r[f'n_1d'])}"
        report(line)

    # Find best signals (max absolute IC at any horizon)
    report("\n--- Top Signals by Max |IC| ---")
    for _, r in ic_df.iterrows():
        max_ic = max(abs(r[f'ic_{h}d']) for h in horizons if not np.isnan(r[f'ic_{h}d']))
        best_h = max(horizons, key=lambda h: abs(r[f'ic_{h}d']) if not np.isnan(r[f'ic_{h}d']) else 0)
        best_t = r[f'tstat_{best_h}d']
        ic_df.loc[ic_df['signal'] == r['signal'], 'max_abs_ic'] = max_ic
        ic_df.loc[ic_df['signal'] == r['signal'], 'best_horizon'] = best_h
        ic_df.loc[ic_df['signal'] == r['signal'], 'best_tstat'] = best_t

    ic_df = ic_df.sort_values('max_abs_ic', ascending=False)
    report(f"\n{'Rank':<5} {'Signal':<25} {'Max|IC|':>8} {'Best H':>7} {'t-stat':>7}")
    report("-" * 60)
    for i, (_, r) in enumerate(ic_df.head(10).iterrows()):
        report(f"{i+1:<5} {r['signal']:<25} {r['max_abs_ic']:.4f} {int(r['best_horizon']):>5}d {r['best_tstat']:+.2f}")

    # Kill check: IC < 0.02 across ALL horizons
    report("\n--- Kill Check: IC < 0.02 at ALL horizons ---")
    killed_signals = []
    alive_signals = []
    for _, r in ic_df.iterrows():
        max_ic = r['max_abs_ic']
        if max_ic < 0.02:
            killed_signals.append(r['signal'])
            report(f"  KILLED: {r['signal']} (max |IC| = {max_ic:.4f})")
        else:
            alive_signals.append(r['signal'])
            report(f"  ALIVE:  {r['signal']} (max |IC| = {max_ic:.4f})")

    return ic_df, alive_signals, killed_signals


# ============================================================================
# ROLLING IC ANALYSIS (Stationarity Check)
# ============================================================================

def rolling_ic_analysis(signals, btc_daily, alive_signals):
    """Compute rolling IC to check signal stationarity."""
    report("\n" + "=" * 80)
    report("ROLLING IC ANALYSIS (Stationarity Check)")
    report("=" * 80)

    window = 90  # 90-day rolling IC
    results = {}

    for sig_name in alive_signals:
        # Use 7d horizon as primary
        fwd = btc_daily['fwd_ret_7d']
        sig = signals[sig_name]
        common = sig.dropna().index.intersection(fwd.dropna().index)
        if len(common) < window + 30:
            report(f"  {sig_name}: Insufficient data for rolling IC")
            continue

        sig_aligned = sig.loc[common]
        fwd_aligned = fwd.loc[common]

        # Rolling IC
        rolling_ic = pd.Series(index=common, dtype=float)
        for i in range(window, len(common)):
            idx = common[i - window:i]
            s = sig_aligned.loc[idx]
            f = fwd_aligned.loc[idx]
            if s.std() > 0 and f.std() > 0:
                ic, _ = stats.spearmanr(s, f)
                rolling_ic.iloc[i] = ic

        rolling_ic = rolling_ic.dropna()
        if len(rolling_ic) < 10:
            report(f"  {sig_name}: Too few rolling IC observations")
            continue

        mean_ic = rolling_ic.mean()
        std_ic = rolling_ic.std()
        pct_positive = (rolling_ic > 0).mean()
        # Check last 90 days
        recent_ic = rolling_ic.iloc[-min(90, len(rolling_ic)):]
        recent_mean = recent_ic.mean()

        # Sign stability: did the IC sign flip in the most recent period?
        first_half = rolling_ic.iloc[:len(rolling_ic)//2].mean()
        second_half = rolling_ic.iloc[len(rolling_ic)//2:].mean()
        sign_stable = np.sign(first_half) == np.sign(second_half)

        results[sig_name] = {
            'mean_ic': mean_ic,
            'std_ic': std_ic,
            'pct_positive': pct_positive,
            'recent_mean': recent_mean,
            'sign_stable': sign_stable,
            'first_half_ic': first_half,
            'second_half_ic': second_half,
            'n_obs': len(rolling_ic),
        }

        report(f"\n  {sig_name}:")
        report(f"    Rolling IC (90d): mean={mean_ic:.4f}, std={std_ic:.4f}")
        report(f"    % positive IC windows: {pct_positive:.1%}")
        report(f"    Recent IC (last 90d): {recent_mean:.4f}")
        report(f"    First half: {first_half:.4f}, Second half: {second_half:.4f}")
        report(f"    Sign stable: {sign_stable}")

    return results


# ============================================================================
# WALK-FORWARD ENGINE
# ============================================================================

def signal_to_position(sig_series, threshold_percentile=50, mode='long_only'):
    """
    Convert continuous signal to discrete position.
    Above threshold -> long (+1)
    Below -threshold -> short (-1) or flat (0) depending on mode
    """
    sig = sig_series.dropna()
    if len(sig) < 20:
        return sig * 0

    # Use rolling percentile rank for threshold
    rolling_rank = sig.rolling(60, min_periods=20).apply(
        lambda x: stats.percentileofscore(x, x.iloc[-1]) / 100
    )

    pos = pd.Series(0.0, index=sig.index)
    pos[rolling_rank > (threshold_percentile / 100)] = 1.0
    if mode == 'bidirectional':
        pos[rolling_rank < (1 - threshold_percentile / 100)] = -1.0

    return pos


def backtest_signal(position, btc_daily, cost_bps=COST_BPS):
    """
    Backtest a position series against BTC daily returns.
    Position is already shifted (no look-ahead).
    """
    common = position.index.intersection(btc_daily.index)
    if len(common) < 30:
        return None

    pos = position.loc[common]
    ret = btc_daily.loc[common, 'ret']

    # Trade costs
    trades = pos.diff().abs()
    trade_cost = trades * (cost_bps / 10000)

    strat_ret = pos * ret - trade_cost
    strat_ret_clean = strat_ret.dropna()

    if len(strat_ret_clean) < 20:
        return None

    n_days = len(strat_ret_clean)
    total_ret = (1 + strat_ret_clean).prod() - 1
    ann_ret = (1 + total_ret) ** (365 / n_days) - 1
    ann_vol = strat_ret_clean.std() * ANNUALIZE
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

    # Drawdown
    cum = (1 + strat_ret_clean).cumprod()
    peak = cum.cummax()
    max_dd = (cum / peak - 1).min()

    n_trades = int(trades.sum() / 2)
    signal_changes = int((pos.diff() != 0).sum())
    pct_long = (pos == 1).mean()
    pct_short = (pos == -1).mean()
    pct_flat = (pos == 0).mean()

    return {
        'total_ret': total_ret,
        'ann_ret': ann_ret,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'n_days': n_days,
        'n_trades': n_trades,
        'signal_changes': signal_changes,
        'pct_long': pct_long,
        'pct_short': pct_short,
        'pct_flat': pct_flat,
        'daily_returns': strat_ret_clean,
    }


def run_walk_forward(signals, btc_daily, sig_name, train_days=120, test_days=90,
                     mode='long_only'):
    """
    Walk-forward optimization for a single signal.
    Given short history (725 days max), use 120d train / 90d test.
    """
    report(f"\n  Walk-Forward: {sig_name} (train={train_days}d, test={test_days}d)")

    sig = signals[sig_name].dropna()
    common = sig.index.intersection(btc_daily.index)
    common = common.sort_values()

    if len(common) < train_days + test_days:
        report(f"    ERROR: Only {len(common)} common days, need {train_days + test_days}")
        return None, None

    # Calculate how many windows we can fit
    n_windows = (len(common) - train_days) // test_days
    if n_windows < 1:
        report(f"    ERROR: Cannot fit any windows")
        return None, None

    report(f"    Available: {len(common)} days -> {n_windows} windows")

    if n_windows < 3:
        report(f"    WARNING: Only {n_windows} windows -- insufficient for strong conclusions")

    # Parameter grid: threshold percentiles and modes
    thresholds = [40, 50, 60, 70]
    modes = ['long_only', 'bidirectional']

    # Start from the beginning to maximize windows
    start_idx = len(common) - (train_days + test_days * n_windows)
    if start_idx < 0:
        start_idx = 0

    window_results = []

    for w in range(n_windows):
        train_start_idx = start_idx + w * test_days
        train_end_idx = train_start_idx + train_days
        test_start_idx = train_end_idx
        test_end_idx = test_start_idx + test_days

        if test_end_idx > len(common):
            break

        train_dates = common[train_start_idx:train_end_idx]
        test_dates = common[test_start_idx:test_end_idx]

        train_start = train_dates[0]
        train_end = train_dates[-1]
        test_start = test_dates[0]
        test_end = test_dates[-1]

        # Grid search on train period
        best_sharpe = -999
        best_params = None

        for thr, m in product(thresholds, modes):
            pos = signal_to_position(sig.loc[:train_end], threshold_percentile=thr, mode=m)
            pos_train = pos.loc[train_start:train_end]
            result = backtest_signal(pos_train, btc_daily.loc[train_start:train_end])
            if result and result['sharpe'] > best_sharpe:
                best_sharpe = result['sharpe']
                best_params = {'threshold': thr, 'mode': m}

        if best_params is None:
            report(f"    W{w+1}: No valid params")
            continue

        # Apply best params to OOS
        pos_oos = signal_to_position(
            sig.loc[:test_end],
            threshold_percentile=best_params['threshold'],
            mode=best_params['mode']
        )
        pos_oos = pos_oos.loc[test_start:test_end]
        oos_result = backtest_signal(pos_oos, btc_daily.loc[test_start:test_end])

        if oos_result is None:
            report(f"    W{w+1}: OOS backtest failed")
            continue

        report(f"    W{w+1}: Train {train_start.date()}-{train_end.date()} | "
               f"Test {test_start.date()}-{test_end.date()} | "
               f"Train Sharpe={best_sharpe:.3f} | "
               f"OOS Sharpe={oos_result['sharpe']:.3f} | "
               f"OOS Return={oos_result['total_ret']*100:.1f}% | "
               f"Params={best_params}")

        window_results.append({
            'window': w + 1,
            'train_start': train_start.date(),
            'train_end': train_end.date(),
            'test_start': test_start.date(),
            'test_end': test_end.date(),
            'best_params': best_params,
            'train_sharpe': best_sharpe,
            'oos_sharpe': oos_result['sharpe'],
            'oos_return': oos_result['total_ret'],
            'oos_max_dd': oos_result['max_dd'],
            'oos_n_trades': oos_result['n_trades'],
            'oos_daily_returns': oos_result['daily_returns'],
        })

    if not window_results:
        return None, None

    # Aggregate
    oos_sharpes = [w['oos_sharpe'] for w in window_results]
    oos_returns = [w['oos_return'] for w in window_results]
    positive_windows = sum(1 for s in oos_sharpes if s > 0)

    agg = {
        'n_windows': len(window_results),
        'positive_windows': positive_windows,
        'mean_oos_sharpe': np.mean(oos_sharpes),
        'median_oos_sharpe': np.median(oos_sharpes),
        'std_oos_sharpe': np.std(oos_sharpes),
        'mean_oos_return': np.mean(oos_returns),
        'min_oos_sharpe': np.min(oos_sharpes),
        'max_oos_sharpe': np.max(oos_sharpes),
    }

    report(f"\n    AGGREGATE: {positive_windows}/{len(window_results)} positive | "
           f"Mean Sharpe={agg['mean_oos_sharpe']:.3f} | "
           f"Median Sharpe={agg['median_oos_sharpe']:.3f}")

    return window_results, agg


# ============================================================================
# V3 CORRELATION ANALYSIS
# ============================================================================

def v3_correlation_analysis(signals, btc_daily, alive_signals):
    """Check correlation of on-chain signals with V3 daily returns."""
    report("\n" + "=" * 80)
    report("V3 CORRELATION ANALYSIS")
    report("=" * 80)

    v3_ret = compute_v3_signal(btc_daily)

    results = {}
    for sig_name in alive_signals:
        sig = signals[sig_name]

        # Convert signal to position for return-space correlation
        pos = signal_to_position(sig, threshold_percentile=50, mode='long_only')
        sig_ret = pos * btc_daily['ret']

        common = v3_ret.dropna().index.intersection(sig_ret.dropna().index)
        if len(common) < 30:
            report(f"  {sig_name}: Insufficient overlap with V3")
            continue

        v3 = v3_ret.loc[common]
        sr = sig_ret.loc[common]

        pearson_r, pearson_p = stats.pearsonr(v3, sr)
        spearman_r, spearman_p = stats.spearmanr(v3, sr)

        # Also check signal-level correlation (not return-level)
        sig_vals = sig.loc[common].dropna()
        v3_pos = pd.Series(0.0, index=btc_daily.index)
        ema20 = btc_daily['close'].ewm(span=20, adjust=False).mean()
        ema50 = btc_daily['close'].ewm(span=50, adjust=False).mean()
        v3_pos[ema20 > ema50] = 1.0
        v3_pos[ema20 <= ema50] = -1.0
        v3_pos = v3_pos.shift(1)
        common2 = sig_vals.index.intersection(v3_pos.dropna().index)
        if len(common2) > 30:
            sig_corr, sig_corr_p = stats.spearmanr(sig_vals.loc[common2], v3_pos.loc[common2])
        else:
            sig_corr, sig_corr_p = np.nan, np.nan

        results[sig_name] = {
            'pearson_r': pearson_r,
            'pearson_p': pearson_p,
            'spearman_r': spearman_r,
            'spearman_p': spearman_p,
            'signal_corr': sig_corr,
            'n_days': len(common),
        }

        report(f"\n  {sig_name}:")
        report(f"    Return-space: Pearson r={pearson_r:.4f} (p={pearson_p:.4f}), "
               f"Spearman r={spearman_r:.4f}")
        report(f"    Signal-space: Spearman r={sig_corr:.4f}")
        report(f"    N={len(common)} days")

        if abs(pearson_r) > 0.5:
            report(f"    ** KILL: Correlation with V3 > 0.5 **")

    return results


# ============================================================================
# PORTFOLIO ANALYSIS
# ============================================================================

def portfolio_analysis(signals, btc_daily, sig_name, best_params=None):
    """
    Analyze 50/50 portfolio of V3 + on-chain signal.
    """
    v3_ret = compute_v3_signal(btc_daily)

    # Generate signal returns
    sig = signals[sig_name]
    thr = best_params.get('threshold', 50) if best_params else 50
    mode = best_params.get('mode', 'long_only') if best_params else 'long_only'
    pos = signal_to_position(sig, threshold_percentile=thr, mode=mode)
    sig_ret = pos * btc_daily['ret']

    # Trade costs for signal
    trades = pos.diff().abs()
    sig_ret = sig_ret - trades * (COST_BPS / 10000)

    common = v3_ret.dropna().index.intersection(sig_ret.dropna().index)
    if len(common) < 60:
        return None

    v3 = v3_ret.loc[common]
    sr = sig_ret.loc[common]

    # 50/50 portfolio
    port_ret = 0.5 * v3 + 0.5 * sr

    def metrics(r, name):
        n = len(r)
        total = (1 + r).prod() - 1
        ann_r = (1 + total) ** (365 / n) - 1
        ann_v = r.std() * ANNUALIZE
        sharpe = ann_r / ann_v if ann_v > 0 else 0
        cum = (1 + r).cumprod()
        max_dd = (cum / cum.cummax() - 1).min()
        return {
            'name': name, 'total_ret': total, 'ann_ret': ann_r,
            'ann_vol': ann_v, 'sharpe': sharpe, 'max_dd': max_dd, 'n_days': n
        }

    v3_m = metrics(v3, 'V3 Only')
    sig_m = metrics(sr, f'{sig_name} Only')
    port_m = metrics(port_ret, '50/50 Portfolio')

    return v3_m, sig_m, port_m


# ============================================================================
# DEEP VALIDATION: NETFLOW SIGNAL (Confirming Finding #27)
# ============================================================================

def deep_netflow_validation(signals, btc_daily, coinmetrics):
    """
    Deep validation of exchange netflow signal, confirming finding #27.
    Tests: IC stability, lag sensitivity, multiple windows, direction analysis.
    """
    report("\n" + "=" * 80)
    report("DEEP VALIDATION: EXCHANGE NETFLOW SIGNAL (Finding #27 Confirmation)")
    report("=" * 80)

    # 1. IC at multiple lags (to check if signal works with execution delay)
    report("\n--- Lag Sensitivity Analysis ---")
    report("Testing if the signal survives realistic execution delays.")
    sig_base = coinmetrics['NetFlowNtv'].rolling(5).sum()

    for extra_lag in [0, 1, 2, 3]:
        sig = (-sig_base).shift(1 + extra_lag)  # base shift + extra
        sig = sig.reindex(btc_daily.index)
        for h in [1, 3, 7]:
            fwd = btc_daily[f'fwd_ret_{h}d']
            ic, pval, n = compute_ic(sig, fwd)
            t_stat = ic * np.sqrt((n - 2) / (1 - ic**2)) if abs(ic) < 1 and n > 2 else 0
            report(f"  Lag={extra_lag}d, Horizon={h}d: IC={ic:+.4f}, t={t_stat:+.2f}, n={n}")

    # 2. Subsample analysis (first half vs second half)
    report("\n--- Subsample Stability ---")
    sig = signals['netflow_5d']
    fwd = btc_daily['fwd_ret_7d']
    common = sig.dropna().index.intersection(fwd.dropna().index)
    common = common.sort_values()
    mid = len(common) // 2

    first_half = common[:mid]
    second_half = common[mid:]

    ic1, p1, n1 = compute_ic(sig.loc[first_half], fwd.loc[first_half])
    ic2, p2, n2 = compute_ic(sig.loc[second_half], fwd.loc[second_half])
    report(f"  First half  ({first_half[0].date()} to {first_half[-1].date()}): IC={ic1:+.4f}, n={n1}")
    report(f"  Second half ({second_half[0].date()} to {second_half[-1].date()}): IC={ic2:+.4f}, n={n2}")
    report(f"  Sign consistent: {np.sign(ic1) == np.sign(ic2)}")

    # 3. Directional analysis -- does outflow really predict positive returns?
    report("\n--- Directional Analysis ---")
    sig_vals = signals['netflow_5d'].dropna()
    fwd_vals = btc_daily['fwd_ret_7d'].reindex(sig_vals.index).dropna()
    common_dir = sig_vals.index.intersection(fwd_vals.index)
    sig_dir = sig_vals.loc[common_dir]
    fwd_dir = fwd_vals.loc[common_dir]

    # Top/bottom quintile returns
    q20 = sig_dir.quantile(0.20)
    q80 = sig_dir.quantile(0.80)

    bottom_mask = sig_dir <= q20
    top_mask = sig_dir >= q80
    mid_mask = ~(bottom_mask | top_mask)

    bottom_ret = fwd_dir[bottom_mask].mean()
    mid_ret = fwd_dir[mid_mask].mean()
    top_ret = fwd_dir[top_mask].mean()

    report(f"  Bottom quintile (inflow days): mean 7d fwd return = {bottom_ret*100:.3f}%")
    report(f"  Middle 60%:                    mean 7d fwd return = {mid_ret*100:.3f}%")
    report(f"  Top quintile (outflow days):   mean 7d fwd return = {top_ret*100:.3f}%")
    report(f"  Spread (top - bottom):         {(top_ret - bottom_ret)*100:.3f}%")
    report(f"  Monotonic: {bottom_ret < mid_ret < top_ret}")

    # 4. Bootstrap confidence interval for IC
    report("\n--- Bootstrap IC Confidence Interval (1000 samples) ---")
    n_boot = 1000
    boot_ics = []
    rng = np.random.RandomState(42)
    for _ in range(n_boot):
        idx = rng.choice(len(common_dir), size=len(common_dir), replace=True)
        s = sig_dir.iloc[idx].values
        f = fwd_dir.iloc[idx].values
        ic_boot, _ = stats.spearmanr(s, f)
        boot_ics.append(ic_boot)

    boot_ics = np.array(boot_ics)
    ci_low = np.percentile(boot_ics, 2.5)
    ci_high = np.percentile(boot_ics, 97.5)
    report(f"  Bootstrap IC: mean={boot_ics.mean():.4f}, "
           f"95% CI=[{ci_low:.4f}, {ci_high:.4f}]")
    report(f"  CI excludes zero: {ci_low > 0 or ci_high < 0}")

    return {
        'lag_sensitivity': 'passed',  # will be evaluated in output
        'subsample_ic1': ic1,
        'subsample_ic2': ic2,
        'sign_consistent': np.sign(ic1) == np.sign(ic2),
        'quintile_spread': top_ret - bottom_ret,
        'monotonic': bottom_ret < mid_ret < top_ret,
        'boot_ci_low': ci_low,
        'boot_ci_high': ci_high,
        'boot_excludes_zero': ci_low > 0 or ci_high < 0,
    }


# ============================================================================
# MAIN
# ============================================================================

def main():
    report("=" * 80)
    report("R115: BTC On-Chain Metrics as Portfolio Diversifier for V3")
    report("=" * 80)
    report(f"Date: 2026-03-24")
    report(f"Cost assumption: {COST_BPS} bps round-trip")
    report()

    # ---- Load Data ----
    btc_daily = load_btc_daily()
    blockchain = load_blockchain_com()
    coinmetrics = load_coinmetrics()
    santiment = load_santiment()

    # ---- Generate Signals ----
    signals = generate_signals(btc_daily, blockchain, coinmetrics, santiment)

    # ---- IC Scan ----
    ic_df, alive_signals, killed_signals = ic_scan(signals, btc_daily)

    # ---- Rolling IC (Stationarity) for alive signals ----
    rolling_results = rolling_ic_analysis(signals, btc_daily, alive_signals)

    # ---- V3 Correlation ----
    corr_results = v3_correlation_analysis(signals, btc_daily, alive_signals)

    # Filter out signals killed by correlation
    corr_killed = []
    for sig_name in list(alive_signals):
        if sig_name in corr_results and abs(corr_results[sig_name]['pearson_r']) > 0.5:
            alive_signals.remove(sig_name)
            corr_killed.append(sig_name)
            report(f"\n  KILLED by V3 correlation: {sig_name} (r={corr_results[sig_name]['pearson_r']:.4f})")

    # ---- Walk-Forward for survivors ----
    report("\n" + "=" * 80)
    report("WALK-FORWARD ANALYSIS (Alive Signals)")
    report("=" * 80)

    wf_results = {}
    wf_agg = {}
    wf_killed = []
    wf_passed = []
    wf_flagged = []  # insufficient data

    for sig_name in alive_signals:
        windows, agg = run_walk_forward(signals, btc_daily, sig_name,
                                        train_days=120, test_days=90)
        if windows is None:
            wf_flagged.append(sig_name)
            continue

        wf_results[sig_name] = windows
        wf_agg[sig_name] = agg

        # Kill checks
        if agg['n_windows'] < 3:
            report(f"    NOTE: {sig_name} has only {agg['n_windows']} WF windows -- insufficient data")
            wf_flagged.append(sig_name)
        elif agg['mean_oos_sharpe'] < 0.3:
            report(f"    KILLED: {sig_name} mean OOS Sharpe={agg['mean_oos_sharpe']:.3f} < 0.3")
            wf_killed.append(sig_name)
        else:
            wf_passed.append(sig_name)
            report(f"    PASSED: {sig_name}")

    # ---- Deep Netflow Validation ----
    # Regardless of kill status, do deep validation on netflow_5d per brief
    deep_results = deep_netflow_validation(signals, btc_daily, coinmetrics)

    # ---- Portfolio Analysis for top signals ----
    report("\n" + "=" * 80)
    report("PORTFOLIO ANALYSIS (50/50 with V3)")
    report("=" * 80)

    portfolio_results = {}
    # Do portfolio analysis for all alive signals that had WF results
    portfolio_candidates = wf_passed + wf_flagged
    for sig_name in portfolio_candidates:
        if sig_name not in wf_results:
            continue

        # Use most common best params from WF
        best_params_list = [w['best_params'] for w in wf_results[sig_name]]
        # Use the params from the most recent window
        best_params = best_params_list[-1] if best_params_list else None

        result = portfolio_analysis(signals, btc_daily, sig_name, best_params)
        if result is None:
            continue

        v3_m, sig_m, port_m = result
        portfolio_results[sig_name] = (v3_m, sig_m, port_m)

        report(f"\n  {sig_name}:")
        report(f"    {'Metric':<20} {'V3 Only':>12} {'Signal Only':>12} {'50/50 Port':>12}")
        report(f"    {'-'*60}")
        report(f"    {'Sharpe':<20} {v3_m['sharpe']:>12.3f} {sig_m['sharpe']:>12.3f} {port_m['sharpe']:>12.3f}")
        report(f"    {'Ann. Return':<20} {v3_m['ann_ret']*100:>11.1f}% {sig_m['ann_ret']*100:>11.1f}% {port_m['ann_ret']*100:>11.1f}%")
        report(f"    {'Ann. Volatility':<20} {v3_m['ann_vol']*100:>11.1f}% {sig_m['ann_vol']*100:>11.1f}% {port_m['ann_vol']*100:>11.1f}%")
        report(f"    {'Max Drawdown':<20} {v3_m['max_dd']*100:>11.1f}% {sig_m['max_dd']*100:>11.1f}% {port_m['max_dd']*100:>11.1f}%")

    # ============================================================================
    # FINAL SUMMARY
    # ============================================================================
    report("\n" + "=" * 80)
    report("FINAL SUMMARY")
    report("=" * 80)

    report("\n--- IC Scan Results ---")
    report(f"  Total signals scanned: {len(signals.columns)}")
    report(f"  Killed by IC < 0.02: {len(killed_signals)}")
    report(f"  {killed_signals}")
    report(f"  Alive after IC scan: {len(alive_signals) + len(corr_killed)}")

    report("\n--- V3 Correlation Filter ---")
    report(f"  Killed by corr > 0.5: {len(corr_killed)}")
    if corr_killed:
        for sig in corr_killed:
            report(f"    {sig}: r={corr_results[sig]['pearson_r']:.4f}")

    report("\n--- Walk-Forward Results ---")
    report(f"  Passed (mean Sharpe >= 0.3): {wf_passed}")
    report(f"  Killed (mean Sharpe < 0.3): {wf_killed}")
    report(f"  Flagged (insufficient data): {wf_flagged}")

    report("\n--- Deep Netflow Validation (Finding #27) ---")
    report(f"  Subsample IC consistency: {deep_results['sign_consistent']}")
    report(f"  Quintile spread: {deep_results['quintile_spread']*100:.3f}%")
    report(f"  Monotonic quintiles: {deep_results['monotonic']}")
    report(f"  Bootstrap 95% CI: [{deep_results['boot_ci_low']:.4f}, {deep_results['boot_ci_high']:.4f}]")
    report(f"  CI excludes zero: {deep_results['boot_excludes_zero']}")

    # Final verdict for each signal
    report("\n--- Final Verdicts ---")
    all_signals = signals.columns.tolist()
    for sig_name in all_signals:
        if sig_name in killed_signals:
            verdict = "KILLED (IC < 0.02)"
        elif sig_name in corr_killed:
            verdict = f"KILLED (V3 corr > 0.5)"
        elif sig_name in wf_killed:
            sharpe_val = wf_agg[sig_name]['mean_oos_sharpe'] if sig_name in wf_agg else 'N/A'
            verdict = f"KILLED (WF Sharpe={sharpe_val:.3f} < 0.3)" if isinstance(sharpe_val, float) else f"KILLED (WF Sharpe={sharpe_val})"
        elif sig_name in wf_passed:
            sharpe_val = wf_agg[sig_name]['mean_oos_sharpe']
            n_win = wf_agg[sig_name]['n_windows']
            pos_win = wf_agg[sig_name]['positive_windows']
            verdict = f"PASSED (WF Sharpe={sharpe_val:.3f}, {pos_win}/{n_win} positive)"
        elif sig_name in wf_flagged:
            if sig_name in wf_agg:
                sharpe_val = wf_agg[sig_name]['mean_oos_sharpe']
                n_win = wf_agg[sig_name]['n_windows']
                verdict = f"FLAGGED (only {n_win} WF windows, Sharpe={sharpe_val:.3f})"
            else:
                verdict = "FLAGGED (insufficient data for WF)"
        else:
            verdict = "NOT TESTED"

        report(f"  {sig_name:<25}: {verdict}")

    # ---- Write Markdown Report ----
    write_markdown_report(ic_df, alive_signals, corr_results, wf_results, wf_agg,
                          wf_passed, wf_killed, wf_flagged, killed_signals, corr_killed,
                          deep_results, portfolio_results, rolling_results)

    report(f"\nReport written to: {OUTPUT_MD}")
    report("Done.")


# ============================================================================
# MARKDOWN REPORT WRITER
# ============================================================================

def write_markdown_report(ic_df, alive_signals, corr_results, wf_results, wf_agg,
                          wf_passed, wf_killed, wf_flagged, ic_killed, corr_killed,
                          deep_results, portfolio_results, rolling_results):
    """Write comprehensive markdown report."""
    md = []
    md.append("# R115 -- BTC On-Chain Metrics as Portfolio Diversifier for V3")
    md.append("")
    md.append("**Date**: 2026-03-24")
    md.append("**Period**: 2024-03 to 2026-03 (725 days max, varies by source)")
    md.append("**Asset**: BTC spot")
    md.append("**Cost assumption**: 10 bps round-trip")
    md.append("")
    md.append("## Objective")
    md.append("")
    md.append("Test on-chain BTC metrics as diversifier signals for V3 (EMA trend-following).")
    md.append("On-chain data represents fundamentally different information (blockchain activity")
    md.append("vs price patterns), which should provide genuine decorrelation.")
    md.append("")
    md.append("**Previous Finding #27**: BTC exchange netflow 5d sum IC=+0.149 (t=3.44). This")
    md.append("research performs DEEP validation of that result and extends to additional on-chain metrics.")
    md.append("")

    # Data summary
    md.append("## Data Sources")
    md.append("")
    md.append("| Source | Period | Rows | Key Columns |")
    md.append("|--------|--------|------|-------------|")
    md.append("| Blockchain.com | 2024-03 to 2026-03 | 725 | tx_volume, addresses, transactions |")
    md.append("| Coinmetrics | 2024-09 to 2026-03 | 568 | NetFlowNtv, NetFlowUSD, FlowIn/Out |")
    md.append("| Santiment | 2025-06 to 2026-02 | 266 | exchange_balance, netflow |")
    md.append("| BTC Spot 1h | 2020-01 to 2026-03 | ~54k hourly | OHLCV |")
    md.append("")
    md.append("**CAVEAT**: Short data history (725 days max). All conclusions should be treated as")
    md.append("preliminary. Santiment data (266 days) is flagged as insufficient for standalone conclusions.")
    md.append("")

    # IC scan table
    md.append("## IC Scan (Spearman Rank Correlation)")
    md.append("")
    md.append("| Signal | IC 1d | IC 3d | IC 7d | IC 14d | Max |IC| | N |")
    md.append("|--------|-------|-------|-------|--------|---------|---|")
    for _, r in ic_df.iterrows():
        cols = []
        for h in [1, 3, 7, 14]:
            ic_val = r[f'ic_{h}d']
            t_val = r[f'tstat_{h}d']
            if np.isnan(ic_val):
                cols.append("NaN")
            else:
                bold = "**" if abs(t_val) > 2 else ""
                cols.append(f"{bold}{ic_val:+.4f}{bold}")
        max_ic = r['max_abs_ic'] if 'max_abs_ic' in r else 0
        n = int(r['n_1d'])
        md.append(f"| {r['signal']} | {' | '.join(cols)} | {max_ic:.4f} | {n} |")
    md.append("")

    # Kill summary from IC scan
    md.append("### IC Kill Results")
    md.append("")
    if ic_killed:
        for sig in ic_killed:
            md.append(f"- **KILLED**: {sig} (max |IC| < 0.02)")
    else:
        md.append("- No signals killed by IC threshold")
    md.append("")

    # V3 correlation
    md.append("## V3 Correlation Analysis")
    md.append("")
    md.append("| Signal | Pearson r | p-value | Signal Corr | N | Verdict |")
    md.append("|--------|-----------|---------|-------------|---|---------|")
    for sig_name in list(corr_results.keys()):
        r = corr_results[sig_name]
        killed = "KILLED" if abs(r['pearson_r']) > 0.5 else "OK"
        md.append(f"| {sig_name} | {r['pearson_r']:+.4f} | {r['pearson_p']:.4f} | "
                  f"{r['signal_corr']:+.4f} | {r['n_days']} | {killed} |")
    md.append("")
    if corr_killed:
        for sig in corr_killed:
            md.append(f"- **KILLED**: {sig} (correlation with V3 > 0.5)")
    else:
        md.append("- No signals killed by V3 correlation threshold")
    md.append("")

    # Rolling IC
    md.append("## Rolling IC Stationarity Check (90-day window)")
    md.append("")
    if rolling_results:
        md.append("| Signal | Mean IC | Std IC | % Positive | Recent IC | Sign Stable |")
        md.append("|--------|---------|--------|------------|-----------|-------------|")
        for sig, r in rolling_results.items():
            stable = "Yes" if r['sign_stable'] else "**No**"
            md.append(f"| {sig} | {r['mean_ic']:+.4f} | {r['std_ic']:.4f} | "
                      f"{r['pct_positive']:.0%} | {r['recent_mean']:+.4f} | {stable} |")
        md.append("")
    else:
        md.append("No signals had sufficient data for rolling IC analysis.")
        md.append("")

    # Walk-forward results
    md.append("## Walk-Forward Analysis (120d train / 90d test)")
    md.append("")

    for sig_name in list(wf_results.keys()):
        windows = wf_results[sig_name]
        agg = wf_agg[sig_name]
        md.append(f"### {sig_name}")
        md.append("")
        md.append(f"**{agg['positive_windows']}/{agg['n_windows']} positive windows** | "
                  f"Mean OOS Sharpe: **{agg['mean_oos_sharpe']:.3f}** | "
                  f"Median: {agg['median_oos_sharpe']:.3f}")
        md.append("")
        md.append("| Window | Train Period | Test Period | Train Sharpe | OOS Sharpe | OOS Return | Params |")
        md.append("|--------|-------------|-------------|-------------|-----------|-----------|--------|")
        for w in windows:
            params_str = f"thr={w['best_params']['threshold']}, {w['best_params']['mode']}"
            positive = "+" if w['oos_sharpe'] > 0 else ""
            md.append(f"| W{w['window']} | {w['train_start']} to {w['train_end']} | "
                      f"{w['test_start']} to {w['test_end']} | {w['train_sharpe']:.3f} | "
                      f"{positive}{w['oos_sharpe']:.3f} | {w['oos_return']*100:.1f}% | {params_str} |")
        md.append("")

        # Verdict for this signal
        if sig_name in wf_passed:
            md.append(f"**Verdict**: PASSED")
        elif sig_name in wf_killed:
            md.append(f"**Verdict**: KILLED (mean OOS Sharpe < 0.3)")
        elif sig_name in wf_flagged:
            md.append(f"**Verdict**: FLAGGED (insufficient WF windows)")
        md.append("")

    # Deep netflow validation
    md.append("## Deep Netflow Validation (Finding #27 Confirmation)")
    md.append("")
    md.append("### Lag Sensitivity")
    md.append("Testing whether the netflow signal survives realistic execution delays (1-3 day lags).")
    md.append("See console output for detailed lag-by-horizon IC values.")
    md.append("")

    md.append("### Subsample Stability")
    md.append(f"- First half IC: {deep_results['subsample_ic1']:+.4f}")
    md.append(f"- Second half IC: {deep_results['subsample_ic2']:+.4f}")
    md.append(f"- Sign consistent: **{deep_results['sign_consistent']}**")
    md.append("")

    md.append("### Quintile Analysis")
    md.append(f"- Top quintile (outflow) vs bottom quintile (inflow) 7d return spread: "
              f"**{deep_results['quintile_spread']*100:.3f}%**")
    md.append(f"- Monotonic quintiles: **{deep_results['monotonic']}**")
    md.append("")

    md.append("### Bootstrap Confidence Interval")
    md.append(f"- 95% CI: [{deep_results['boot_ci_low']:.4f}, {deep_results['boot_ci_high']:.4f}]")
    md.append(f"- CI excludes zero: **{deep_results['boot_excludes_zero']}**")
    md.append("")

    # Portfolio analysis
    md.append("## Portfolio Analysis (50/50 with V3)")
    md.append("")
    if portfolio_results:
        for sig_name, (v3_m, sig_m, port_m) in portfolio_results.items():
            md.append(f"### {sig_name}")
            md.append("")
            md.append(f"| Metric | V3 Only | Signal Only | 50/50 Portfolio |")
            md.append(f"|--------|---------|-------------|-----------------|")
            md.append(f"| Sharpe | {v3_m['sharpe']:.3f} | {sig_m['sharpe']:.3f} | **{port_m['sharpe']:.3f}** |")
            md.append(f"| Ann. Return | {v3_m['ann_ret']*100:.1f}% | {sig_m['ann_ret']*100:.1f}% | {port_m['ann_ret']*100:.1f}% |")
            md.append(f"| Ann. Volatility | {v3_m['ann_vol']*100:.1f}% | {sig_m['ann_vol']*100:.1f}% | {port_m['ann_vol']*100:.1f}% |")
            md.append(f"| Max Drawdown | {v3_m['max_dd']*100:.1f}% | {sig_m['max_dd']*100:.1f}% | {port_m['max_dd']*100:.1f}% |")
            md.append("")

            delta = port_m['sharpe'] - v3_m['sharpe']
            if delta > 0:
                md.append(f"Portfolio Sharpe improvement: **+{delta:.3f}** (diversification benefit)")
            else:
                md.append(f"Portfolio Sharpe change: {delta:.3f} (no diversification benefit)")
            md.append("")
    else:
        md.append("No signals reached portfolio analysis stage.")
        md.append("")

    # Final summary table
    md.append("## Final Summary")
    md.append("")
    md.append("| Signal | Max |IC| | V3 Corr | WF Sharpe | WF Win/Total | Verdict |")
    md.append("|--------|---------|---------|-----------|-------------|---------|")

    for _, r in ic_df.iterrows():
        sig = r['signal']
        max_ic = r['max_abs_ic'] if 'max_abs_ic' in r else 0

        # V3 corr
        if sig in corr_results:
            v3_corr = f"{corr_results[sig]['pearson_r']:+.3f}"
        else:
            v3_corr = "N/A"

        # WF
        if sig in wf_agg:
            wf_s = f"{wf_agg[sig]['mean_oos_sharpe']:.3f}"
            wf_w = f"{wf_agg[sig]['positive_windows']}/{wf_agg[sig]['n_windows']}"
        else:
            wf_s = "N/A"
            wf_w = "N/A"

        # Verdict
        if sig in ic_killed:
            verdict = "KILLED (IC)"
        elif sig in corr_killed:
            verdict = "KILLED (V3 corr)"
        elif sig in wf_killed:
            verdict = "KILLED (WF)"
        elif sig in wf_passed:
            verdict = "**PASSED**"
        elif sig in wf_flagged:
            verdict = "FLAGGED"
        else:
            verdict = "N/A"

        md.append(f"| {sig} | {max_ic:.4f} | {v3_corr} | {wf_s} | {wf_w} | {verdict} |")
    md.append("")

    # Kill criteria assessment
    md.append("## Kill Criteria Assessment")
    md.append("")
    md.append("| Criterion | Threshold | Result | Status |")
    md.append("|-----------|-----------|--------|--------|")

    # Best signal IC
    best_ic = ic_df['max_abs_ic'].max() if 'max_abs_ic' in ic_df.columns else 0
    md.append(f"| IC across all horizons | < 0.02 all signals | Best: {best_ic:.4f} | "
              f"{'KILL' if best_ic < 0.02 else 'PASS'} |")

    # WF windows
    max_windows = max((wf_agg[s]['n_windows'] for s in wf_agg), default=0)
    md.append(f"| WF windows available | < 3 | Max: {max_windows} | "
              f"{'FLAG' if max_windows < 3 else 'PASS'} |")

    # WF Sharpe
    best_wf_sharpe = max((wf_agg[s]['mean_oos_sharpe'] for s in wf_agg), default=0)
    md.append(f"| WF mean Sharpe | < 0.3 | Best: {best_wf_sharpe:.3f} | "
              f"{'KILL' if best_wf_sharpe < 0.3 else 'PASS'} |")

    # V3 correlation
    max_corr = max((abs(corr_results[s]['pearson_r']) for s in corr_results), default=0)
    md.append(f"| V3 correlation | > 0.5 | Max: {max_corr:.3f} | "
              f"{'KILL' if max_corr > 0.5 else 'PASS'} |")
    md.append("")

    # Conclusions
    md.append("## Conclusions")
    md.append("")

    if wf_passed:
        md.append(f"**{len(wf_passed)} signal(s) passed all kill criteria:**")
        for sig in wf_passed:
            agg = wf_agg[sig]
            md.append(f"- **{sig}**: WF Sharpe={agg['mean_oos_sharpe']:.3f}, "
                      f"{agg['positive_windows']}/{agg['n_windows']} positive windows")
        md.append("")
    else:
        md.append("**No signals passed all kill criteria.**")
        md.append("")

    if wf_flagged:
        md.append(f"**{len(wf_flagged)} signal(s) flagged for insufficient data:**")
        for sig in wf_flagged:
            if sig in wf_agg:
                agg = wf_agg[sig]
                md.append(f"- **{sig}**: {agg['n_windows']} WF windows, "
                          f"Sharpe={agg['mean_oos_sharpe']:.3f}")
            else:
                md.append(f"- **{sig}**: Could not run WF (too few observations)")
        md.append("")

    # Deep validation conclusion
    md.append("### Finding #27 Deep Validation")
    md.append("")
    if deep_results['boot_excludes_zero'] and deep_results['sign_consistent']:
        md.append("The exchange netflow signal (5d sum) **CONFIRMED** with deep validation:")
    elif deep_results['boot_excludes_zero']:
        md.append("The exchange netflow signal (5d sum) shows MIXED results in deep validation:")
    else:
        md.append("The exchange netflow signal (5d sum) **FAILS** deep validation:")
    md.append(f"- Bootstrap 95% CI excludes zero: {deep_results['boot_excludes_zero']}")
    md.append(f"- Subsample sign consistency: {deep_results['sign_consistent']}")
    md.append(f"- Monotonic quintiles: {deep_results['monotonic']}")
    md.append(f"- Quintile return spread: {deep_results['quintile_spread']*100:.3f}%")
    md.append("")

    md.append("### Caveats")
    md.append("")
    md.append("1. **Short history**: 725 days maximum (Blockchain.com), 568 days (Coinmetrics), ")
    md.append("   266 days (Santiment). All results are preliminary.")
    md.append("2. **Limited WF windows**: At best ~4 windows of 120d/90d. This is below the ")
    md.append("   10-window standard used in other research (R109).")
    md.append("3. **Finding #27 context**: The original IC=+0.149 was for BTC only; ETH showed ")
    md.append("   INVERTED relationship. Do not generalize across assets.")
    md.append("4. **On-chain data quality**: Blockchain.com and Coinmetrics may measure different ")
    md.append("   things (e.g., different exchange coverage). Results are source-dependent.")
    md.append("")

    md.append("### Next Steps")
    md.append("")
    md.append("1. Monitor signals that passed/flagged with additional data as it accumulates")
    md.append("2. Re-run with 12+ months of additional data for stronger WF conclusions")
    md.append("3. Test portfolio weights other than 50/50 (e.g., risk-parity)")
    md.append("4. Investigate whether netflow signal adds value as a FILTER for V3 entries")
    md.append("   (rather than standalone signal)")

    # Write file
    with open(OUTPUT_MD, 'w') as f:
        f.write('\n'.join(md))


if __name__ == '__main__':
    main()
