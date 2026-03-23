#!/workspace/venv/bin/python
"""
INDEPENDENT VALIDATION: Funding Short 72h Strategy
====================================================
Hypothesis: When funding rate is extremely high (retail long-crowding),
shorting the token for 24-72h produces positive returns after fees.

Claimed result: +56.7% return, Sharpe 1.44

VALIDATION PROTOCOL:
  1. Strict temporal holdout: train/calibrate on data BEFORE 2025-07-01
  2. Out-of-sample test: 2025-07-01 to end of data
  3. NO parameter optimization on OOS period
  4. Parameters (thresholds, hold periods) determined ONLY from training data
  5. Realistic fees: 0.04% maker, 0.06% taker, plus funding income
  6. Multiple robustness checks: decile/quintile splits, bootstrap CIs,
     monthly stability, token-level decomposition

Author: Independent Validator (not the original researcher)
Date: 2026-03-23
"""

import os
import warnings
import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings('ignore')

# ============================================================================
# CONFIGURATION — FIXED BEFORE LOOKING AT OOS DATA
# ============================================================================
CACHE_DIR = '/workspace/crypto_backtest/data/perp/1h_cache/'
SPLIT_DATE = pd.Timestamp('2025-07-01')

# Realistic fee structure (Binance VIP0 perps)
TAKER_FEE = 0.0006   # 0.06% — entry
MAKER_FEE = 0.0004   # 0.04% — exit (limit order)
ROUND_TRIP_FEE = TAKER_FEE + MAKER_FEE  # 0.10% total

# Min data requirements
MIN_TRAIN_ROWS = 500   # ~21 days of hourly data
MIN_OOS_ROWS = 100     # ~4 days
MIN_FUNDING_ROWS = 1000

# Hold periods to test (determined before OOS)
HOLD_HOURS = [24, 48, 72]

# Z-score window for funding (standard in literature)
ZSCORE_WINDOW = 72  # 3 days rolling
ZSCORE_MIN_PERIODS = 24

# Funding rate is settled every 8h on most exchanges.
# In the parquet cache, funding_rate is already interpolated to 1h.
# For funding income calc: short position earns funding when rate > 0.
FUNDING_SETTLEMENT_HOURS = 8


def load_all_tokens():
    """Load all tokens with sufficient data and funding rates."""
    files = [f for f in os.listdir(CACHE_DIR) if f.endswith('.parquet')]
    token_data = {}
    skipped = {'no_funding': 0, 'too_short_train': 0, 'too_short_oos': 0,
               'too_few_funding': 0}

    for f in sorted(files):
        token = f.replace('_1h.parquet', '')
        df = pd.read_parquet(os.path.join(CACHE_DIR, f))
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)

        # Check for funding column
        if 'funding_rate' in df.columns:
            fcol = 'funding_rate'
        elif 'funding_1h' in df.columns:
            fcol = 'funding_1h'
        else:
            skipped['no_funding'] += 1
            continue

        # Check data sufficiency
        train_rows = len(df[df.index < SPLIT_DATE])
        oos_rows = len(df[df.index >= SPLIT_DATE])
        fund_valid = df[fcol].notna().sum()

        if train_rows < MIN_TRAIN_ROWS:
            skipped['too_short_train'] += 1
            continue
        if oos_rows < MIN_OOS_ROWS:
            skipped['too_short_oos'] += 1
            continue
        if fund_valid < MIN_FUNDING_ROWS:
            skipped['too_few_funding'] += 1
            continue

        # Standardize funding column, keep only needed columns to save memory
        df['funding'] = df[fcol]
        token_data[token] = df[['close', 'funding']].copy()

    return token_data, skipped


def compute_funding_features(df):
    """Compute funding rate z-score and percentile rank. Training-period only for thresholds."""
    df = df.copy()

    # Rolling z-score of funding rate
    roll_mean = df['funding'].rolling(ZSCORE_WINDOW, min_periods=ZSCORE_MIN_PERIODS).mean()
    roll_std = df['funding'].rolling(ZSCORE_WINDOW, min_periods=ZSCORE_MIN_PERIODS).std()
    roll_std = roll_std.replace(0, np.nan)
    df['funding_zscore'] = (df['funding'] - roll_mean) / roll_std

    # Note: skipping rolling percentile (expensive, not needed for core test)

    return df


def determine_thresholds_from_train(token_data):
    """
    Use ONLY training data to determine what constitutes 'extremely high' funding.
    Returns z-score threshold and percentile threshold.
    """
    train_zscores = []
    train_funding = []

    for token, df in token_data.items():
        train = df[df.index < SPLIT_DATE]
        valid_z = train['funding_zscore'].dropna()
        valid_f = train['funding'].dropna()
        train_zscores.append(valid_z)
        train_funding.append(valid_f)

    all_z = pd.concat(train_zscores)
    all_f = pd.concat(train_funding)

    print(f"\n  Training period funding z-score distribution:")
    print(f"    N observations: {len(all_z):,}")
    for p in [50, 75, 90, 95, 97.5, 99]:
        print(f"    {p}th percentile: {all_z.quantile(p/100):.3f}")

    print(f"\n  Training period raw funding rate distribution:")
    print(f"    N observations: {len(all_f):,}")
    for p in [50, 75, 90, 95, 97.5, 99]:
        print(f"    {p}th percentile: {all_f.quantile(p/100):.8f}")

    # Use 90th percentile z-score as "top decile" threshold
    # and 80th percentile as "top quintile" — determined from train only
    z_p90 = all_z.quantile(0.90)
    z_p80 = all_z.quantile(0.80)
    z_p95 = all_z.quantile(0.95)

    print(f"\n  Thresholds (from training data ONLY):")
    print(f"    Top quintile (80th pctile): z > {z_p80:.3f}")
    print(f"    Top decile (90th pctile):   z > {z_p90:.3f}")
    print(f"    Top 5% (95th pctile):       z > {z_p95:.3f}")
    print(f"    Fixed z > 2.0 (common):     z > 2.000")

    return {
        'quintile': z_p80,
        'decile': z_p90,
        'top5pct': z_p95,
        'fixed_2': 2.0,
    }


def compute_trade_returns_oos(token_data, z_threshold, hold_hours):
    """
    For each token, find OOS bars where funding_zscore > z_threshold.
    Compute short trade return over hold_hours.
    Include realistic fees and funding income.

    IMPORTANT: Non-overlapping trades only. Once a trade is entered,
    skip signals during the hold period.
    """
    all_trades = []

    for token, df in token_data.items():
        oos = df[df.index >= SPLIT_DATE].copy()
        if len(oos) < hold_hours + 1:
            continue

        close_arr = oos['close'].values
        funding_arr = oos['funding'].values
        zscore_arr = oos['funding_zscore'].values
        idx_arr = oos.index.values
        n = len(oos)

        i = 0
        while i < n - hold_hours:
            z = zscore_arr[i]
            if np.isnan(z) or z <= z_threshold:
                i += 1
                continue

            # ENTRY: short at close price + taker fee
            entry_px = close_arr[i]
            exit_idx = i + hold_hours
            if exit_idx >= n:
                break
            exit_px = close_arr[exit_idx]

            # Short return: (entry - exit) / entry
            gross_return = (entry_px - exit_px) / entry_px

            # Fees: taker entry + maker exit
            net_return = gross_return - ROUND_TRIP_FEE

            # Funding income: short earns funding when rate > 0
            # Funding is settled every 8h. Count settlement periods in hold.
            funding_income = 0.0
            for j in range(i, exit_idx):
                # Each hour, the funding that would be settled
                # funding_rate in parquet is the 8h rate, applied once per 8h
                # We accumulate hourly: rate / 8 per hour (amortized)
                fr = funding_arr[j]
                if not np.isnan(fr):
                    # Short earns positive funding (pays negative funding)
                    funding_income += fr / FUNDING_SETTLEMENT_HOURS

            total_return = net_return + funding_income

            all_trades.append({
                'token': token,
                'entry_time': idx_arr[i],
                'exit_time': idx_arr[exit_idx],
                'entry_price': entry_px,
                'exit_price': exit_px,
                'funding_zscore': z,
                'funding_rate_at_entry': funding_arr[i],
                'gross_return': gross_return,
                'fees': ROUND_TRIP_FEE,
                'funding_income': funding_income,
                'net_return': total_return,
                'hold_hours': hold_hours,
            })

            # Skip to after this trade exits (non-overlapping)
            i = exit_idx
            continue

        # If we didn't enter a trade, advance
        # (handled by i += 1 in the loop)

    if not all_trades:
        return pd.DataFrame()
    return pd.DataFrame(all_trades)


def compute_sharpe(returns, periods_per_year=None):
    """
    Compute annualized Sharpe ratio.
    periods_per_year: how many trade-holding-periods fit in a year.
    If None, uses daily-equivalent: 365 * 24 / avg_hold_hours
    """
    if len(returns) < 2:
        return 0.0
    mean_r = returns.mean()
    std_r = returns.std()
    if std_r == 0 or np.isnan(std_r):
        return 0.0
    if periods_per_year is None:
        # Approximate: treat as independent bets
        periods_per_year = len(returns)  # actual number of trades in period
        # But for annualization, we need calendar time
        return mean_r / std_r * np.sqrt(len(returns))
    return mean_r / std_r * np.sqrt(periods_per_year)


def compute_sharpe_calendar(trades_df, initial_capital=100_000):
    """
    Compute Sharpe ratio from daily returns of the equity curve.
    This is the standard method — avoids inflating Sharpe with trade count.
    """
    if len(trades_df) == 0:
        return 0.0, pd.Series(dtype=float)

    # Build daily equity curve
    trades = trades_df.sort_values('entry_time').copy()
    trades['entry_time'] = pd.to_datetime(trades['entry_time'])
    trades['exit_time'] = pd.to_datetime(trades['exit_time'])

    # Get date range
    min_date = trades['entry_time'].min().normalize()
    max_date = trades['exit_time'].max().normalize()
    dates = pd.date_range(min_date, max_date, freq='D')

    equity = initial_capital
    daily_equity = []

    for date in dates:
        # Settle any trades that exited on this day
        exited = trades[
            (trades['exit_time'].dt.normalize() == date) &
            (trades.get('_settled', pd.Series(False, index=trades.index)) == False)
        ]
        for _, trade in exited.iterrows():
            # Position size: equal weight, 1/5 of equity at entry
            # But for simplicity and conservatism, use fixed fractional
            pos_size = initial_capital / 5  # fixed $20k per trade
            pnl = pos_size * trade['net_return']
            equity += pnl
            trades.loc[trade.name, '_settled'] = True

        daily_equity.append({'date': date, 'equity': equity})

    eq_df = pd.DataFrame(daily_equity).set_index('date')
    eq_df['daily_ret'] = eq_df['equity'].pct_change()

    daily_rets = eq_df['daily_ret'].dropna()
    if len(daily_rets) < 2 or daily_rets.std() == 0:
        return 0.0, eq_df

    sharpe = daily_rets.mean() / daily_rets.std() * np.sqrt(365)
    return sharpe, eq_df


def compute_equity_curve(trades_df, initial_capital=100_000, n_slots=5):
    """
    Build a proper equity curve with position limits and sequential allocation.
    Returns daily equity, Sharpe, max drawdown, total return.

    Position sizing: equal allocation to n_slots positions.
    Non-overlapping per token already enforced in trade generation.
    """
    if len(trades_df) == 0:
        return {
            'sharpe': 0.0, 'total_return': 0.0, 'max_drawdown': 0.0,
            'equity_curve': pd.DataFrame()
        }

    trades = trades_df.sort_values('entry_time').copy()
    trades['entry_time'] = pd.to_datetime(trades['entry_time'])
    trades['exit_time'] = pd.to_datetime(trades['exit_time'])

    # Simulate with position limits
    equity = initial_capital
    peak = initial_capital
    max_dd = 0.0

    # Track active positions: list of (exit_time, token, net_return, alloc_amount)
    active = []
    settled_trades = []
    equity_snapshots = []

    for _, row in trades.iterrows():
        entry_t = row['entry_time']

        # Close positions that have expired
        still_active = []
        for pos in active:
            if entry_t >= pos[0]:
                pnl = pos[3] * pos[2]
                equity += pnl
                settled_trades.append({**row.to_dict(), 'pnl_usd': pnl, 'alloc': pos[3]})
            else:
                still_active.append(pos)
        active = still_active

        # Update drawdown
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

        equity_snapshots.append({'time': entry_t, 'equity': equity})

        # Try to enter
        if len(active) < n_slots and equity > 0:
            # Check not already short this token
            active_tokens = [p[1] for p in active]
            if row['token'] not in active_tokens:
                alloc = equity / n_slots
                active.append((row['exit_time'], row['token'], row['net_return'], alloc))

    # Close remaining
    for pos in active:
        pnl = pos[3] * pos[2]
        equity += pnl

    if equity > peak:
        peak = equity
    dd = (peak - equity) / peak if peak > 0 else 0
    if dd > max_dd:
        max_dd = dd

    # Build daily equity curve for Sharpe
    if equity_snapshots:
        eq_df = pd.DataFrame(equity_snapshots).set_index('time')
        eq_df = eq_df.resample('D').last().ffill()
        eq_df['daily_ret'] = eq_df['equity'].pct_change()
        daily_rets = eq_df['daily_ret'].dropna()
        if len(daily_rets) > 1 and daily_rets.std() > 0:
            sharpe = daily_rets.mean() / daily_rets.std() * np.sqrt(365)
        else:
            sharpe = 0.0
    else:
        sharpe = 0.0
        eq_df = pd.DataFrame()

    total_return = (equity / initial_capital - 1) * 100

    return {
        'sharpe': sharpe,
        'total_return': total_return,
        'max_drawdown': max_dd * 100,
        'final_equity': equity,
        'equity_curve': eq_df,
    }


def bootstrap_ci(returns, n_bootstrap=5000, ci=0.95):
    """Bootstrap confidence interval for mean return."""
    if len(returns) < 5:
        return np.nan, np.nan, np.nan
    rng = np.random.RandomState(42)
    boot_means = []
    for _ in range(n_bootstrap):
        sample = rng.choice(returns, size=len(returns), replace=True)
        boot_means.append(sample.mean())
    boot_means = np.array(boot_means)
    alpha = (1 - ci) / 2
    lo = np.percentile(boot_means, alpha * 100)
    hi = np.percentile(boot_means, (1 - alpha) * 100)
    return returns.mean(), lo, hi


def report_trades(trades_df, label, initial_capital=100_000):
    """Comprehensive report of trade results."""
    if len(trades_df) == 0:
        print(f"\n  {label}: NO TRADES")
        return {}

    df = trades_df.copy()
    n = len(df)

    # Basic stats
    gross_mean = df['gross_return'].mean() * 100
    net_mean = df['net_return'].mean() * 100
    funding_mean = df['funding_income'].mean() * 100
    fee_mean = df['fees'].mean() * 100

    win_mask = df['net_return'] > 0
    n_wins = win_mask.sum()
    n_losses = (~win_mask).sum()
    win_rate = n_wins / n * 100

    avg_win = df.loc[win_mask, 'net_return'].mean() * 100 if n_wins > 0 else 0
    avg_loss = df.loc[~win_mask, 'net_return'].mean() * 100 if n_losses > 0 else 0

    # Profit factor
    gross_wins = df.loc[win_mask, 'net_return'].sum()
    gross_losses = abs(df.loc[~win_mask, 'net_return'].sum())
    pf = gross_wins / gross_losses if gross_losses > 0 else float('inf')

    # Portfolio-level metrics
    port = compute_equity_curve(df, initial_capital)

    # Bootstrap CI on net returns
    mean_ret, ci_lo, ci_hi = bootstrap_ci(df['net_return'].values)

    # t-test: is mean net return significantly different from 0?
    t_stat, p_val = stats.ttest_1samp(df['net_return'].dropna(), 0)

    # Date range
    df['entry_time'] = pd.to_datetime(df['entry_time'])
    date_range_days = (df['entry_time'].max() - df['entry_time'].min()).days

    print(f"\n  {'='*72}")
    print(f"  {label}")
    print(f"  {'='*72}")
    print(f"  OOS Period: {df['entry_time'].min().date()} to {df['entry_time'].max().date()} ({date_range_days} days)")
    print(f"  Trades: {n} ({n/max(date_range_days,1):.1f}/day)")
    print(f"  Unique tokens: {df['token'].nunique()}")
    print(f"")
    print(f"  --- Per-Trade Returns ---")
    print(f"  Gross return (avg):    {gross_mean:+.4f}%")
    print(f"  Fees (avg):            {fee_mean:.4f}%")
    print(f"  Funding income (avg):  {funding_mean:+.4f}%")
    print(f"  Net return (avg):      {net_mean:+.4f}%")
    print(f"  Net return 95% CI:     [{ci_lo*100:+.4f}%, {ci_hi*100:+.4f}%]")
    print(f"  t-statistic:           {t_stat:.3f}")
    print(f"  p-value:               {p_val:.6f}")
    print(f"  Significant (p<0.05):  {'YES' if p_val < 0.05 else 'NO'}")
    print(f"")
    print(f"  --- Win/Loss ---")
    print(f"  Win rate:              {win_rate:.1f}%")
    print(f"  Avg win:               {avg_win:+.4f}%")
    print(f"  Avg loss:              {avg_loss:+.4f}%")
    print(f"  Profit factor:         {pf:.2f}")
    print(f"")
    print(f"  --- Portfolio Simulation ($100K, 5 slots, 1x leverage) ---")
    print(f"  Total return:          {port['total_return']:+.2f}%")
    print(f"  Final equity:          ${port['final_equity']:,.0f}")
    print(f"  Max drawdown:          {port['max_drawdown']:.2f}%")
    print(f"  Sharpe ratio:          {port['sharpe']:.2f}")

    # Monthly breakdown
    df['month'] = df['entry_time'].dt.to_period('M')
    print(f"\n  --- Monthly Breakdown ---")
    print(f"  {'Month':>10s}  {'Trades':>6s}  {'WinR':>6s}  {'AvgNet':>10s}  {'Funding':>10s}")
    months = sorted(df['month'].unique())
    monthly_returns = []
    for m in months:
        mdf = df[df['month'] == m]
        mn = len(mdf)
        mwr = (mdf['net_return'] > 0).mean() * 100
        mnet = mdf['net_return'].mean() * 100
        mfund = mdf['funding_income'].mean() * 100
        print(f"  {str(m):>10s}  {mn:6d}  {mwr:5.1f}%  {mnet:>+9.4f}%  {mfund:>+9.4f}%")
        monthly_returns.append(mnet)

    # Monthly consistency
    n_pos_months = sum(1 for r in monthly_returns if r > 0)
    print(f"\n  Positive months: {n_pos_months}/{len(monthly_returns)} ({n_pos_months/max(len(monthly_returns),1)*100:.0f}%)")

    # Top/bottom tokens
    token_stats = df.groupby('token').agg(
        trades=('net_return', 'count'),
        avg_net=('net_return', 'mean'),
        total_net=('net_return', 'sum'),
        win_rate=('net_return', lambda x: (x > 0).mean() * 100),
    ).sort_values('total_net', ascending=False)

    print(f"\n  --- Top 5 Tokens by Total Net Return ---")
    print(f"  {'Token':>10s}  {'Trades':>6s}  {'AvgNet':>10s}  {'TotalNet':>10s}  {'WinR':>6s}")
    for token, row in token_stats.head(5).iterrows():
        print(f"  {token:>10s}  {row['trades']:6.0f}  {row['avg_net']*100:>+9.4f}%  {row['total_net']*100:>+9.4f}%  {row['win_rate']:5.1f}%")

    print(f"\n  --- Bottom 3 Tokens ---")
    for token, row in token_stats.tail(3).iterrows():
        print(f"  {token:>10s}  {row['trades']:6.0f}  {row['avg_net']*100:>+9.4f}%  {row['total_net']*100:>+9.4f}%  {row['win_rate']:5.1f}%")

    return {
        'label': label,
        'n_trades': n,
        'n_tokens': df['token'].nunique(),
        'gross_return_avg': gross_mean,
        'net_return_avg': net_mean,
        'funding_income_avg': funding_mean,
        'ci_lo': ci_lo * 100 if not np.isnan(ci_lo) else np.nan,
        'ci_hi': ci_hi * 100 if not np.isnan(ci_hi) else np.nan,
        't_stat': t_stat,
        'p_val': p_val,
        'win_rate': win_rate,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'profit_factor': pf,
        'portfolio_return': port['total_return'],
        'max_drawdown': port['max_drawdown'],
        'sharpe': port['sharpe'],
        'n_pos_months': n_pos_months,
        'n_months': len(monthly_returns),
    }


def check_in_sample_edge(token_data, thresholds):
    """
    Verify the edge exists in training data (sanity check).
    Uses vectorized numpy for speed. Overlapping trades allowed here
    since we're just measuring the conditional forward return.
    """
    print("\n" + "=" * 72)
    print("IN-SAMPLE EDGE CHECK (Training Period, before 2025-07-01)")
    print("=" * 72)

    for thresh_name, z_thresh in thresholds.items():
        train_rets = {h: [] for h in HOLD_HOURS}

        for token, df in token_data.items():
            train = df[df.index < SPLIT_DATE].copy()
            if len(train) < max(HOLD_HOURS) + 1:
                continue

            close_arr = train['close'].values
            zscore_arr = train['funding_zscore'].values

            # Vectorized: find signal bars
            signal_mask = (~np.isnan(zscore_arr)) & (zscore_arr > z_thresh)
            signal_idx = np.where(signal_mask)[0]

            for h in HOLD_HOURS:
                # Only keep entries where exit is within bounds
                valid = signal_idx[signal_idx + h < len(close_arr)]
                if len(valid) == 0:
                    continue
                entry_px = close_arr[valid]
                exit_px = close_arr[valid + h]
                short_rets = (entry_px - exit_px) / entry_px - ROUND_TRIP_FEE
                train_rets[h].append(short_rets)

        print(f"\n  Threshold: {thresh_name} (z > {z_thresh:.3f})")
        for h in HOLD_HOURS:
            if not train_rets[h]:
                print(f"    {h}h: N=0 (insufficient)")
                continue
            rets = np.concatenate(train_rets[h])
            if len(rets) < 10:
                print(f"    {h}h: N={len(rets)} (insufficient)")
                continue
            mean_r = rets.mean() * 100
            hit = (rets > 0).mean() * 100
            t, p = stats.ttest_1samp(rets, 0)
            print(f"    {h}h: N={len(rets):,}  avg={mean_r:+.4f}%  hit={hit:.1f}%  t={t:.2f}  p={p:.4f}")


def unconditional_baseline(token_data):
    """
    Compute unconditional short return as a baseline.
    If you short a random token at a random time for 24-72h, what happens?
    """
    print("\n" + "=" * 72)
    print("UNCONDITIONAL BASELINE: Random Short Returns (OOS)")
    print("=" * 72)

    for h in HOLD_HOURS:
        all_rets = []
        for token, df in token_data.items():
            oos = df[df.index >= SPLIT_DATE]
            close_arr = oos['close'].values
            n = len(oos)
            # Sample every 24h to avoid massive overlap
            for i in range(0, n - h, 24):
                entry = close_arr[i]
                exit_px = close_arr[i + h]
                short_ret = (entry - exit_px) / entry
                all_rets.append(short_ret)

        rets = np.array(all_rets)
        print(f"  {h}h: N={len(rets):,}  avg={rets.mean()*100:+.4f}%  "
              f"median={np.median(rets)*100:+.4f}%  "
              f"hit={(rets>0).mean()*100:.1f}%")


def run_placebo_test(token_data, z_threshold, hold_hours, n_permutations=200):
    """
    Permutation test: shuffle the funding z-scores across time within each token,
    then compute the same strategy. If the real result falls within the permuted
    distribution, the signal has no edge.

    Optimized: precompute cumulative funding sums and short returns for all
    possible entry points. Then permutation only shuffles which entries are selected.
    """
    print(f"\n  Placebo Test (permutation): {n_permutations} shuffles, "
          f"z>{z_threshold:.2f}, {hold_hours}h hold")

    # Get real result first
    real_trades = compute_trade_returns_oos(token_data, z_threshold, hold_hours)
    if len(real_trades) == 0:
        print("    No real trades — cannot run placebo")
        return

    real_mean = real_trades['net_return'].mean()

    # Precompute per-token arrays for fast permutation
    token_arrays = {}
    for token, df in token_data.items():
        oos = df[df.index >= SPLIT_DATE]
        if len(oos) < hold_hours + 1:
            continue

        close_arr = oos['close'].values
        funding_arr = np.nan_to_num(oos['funding'].values, nan=0.0)
        zscore_arr = oos['funding_zscore'].values
        n = len(oos)

        # Precompute short returns for every possible entry
        max_entry = n - hold_hours
        entry_px = close_arr[:max_entry]
        exit_px = close_arr[hold_hours:hold_hours + max_entry]
        gross_rets = (entry_px - exit_px) / entry_px

        # Precompute cumulative funding for fast range sums
        cum_funding = np.cumsum(funding_arr / FUNDING_SETTLEMENT_HOURS)
        funding_income = cum_funding[hold_hours:hold_hours + max_entry] - \
                        np.concatenate([[0], cum_funding[:max_entry - 1]])
        # Fix off-by-one: funding from i to i+hold_hours-1
        funding_income_arr = np.zeros(max_entry)
        for idx in range(max_entry):
            start = idx
            end = idx + hold_hours
            funding_income_arr[idx] = cum_funding[end - 1] - (cum_funding[start - 1] if start > 0 else 0)

        net_rets = gross_rets - ROUND_TRIP_FEE + funding_income_arr

        token_arrays[token] = {
            'zscore': zscore_arr[:max_entry].copy(),
            'net_rets': net_rets,
            'n': max_entry,
        }

    # Permutation loop — only shuffle zscores, reuse precomputed returns
    rng = np.random.RandomState(42)
    perm_means = []

    for perm in range(n_permutations):
        perm_rets = []
        for token, arrs in token_arrays.items():
            shuffled_z = arrs['zscore'].copy()
            rng.shuffle(shuffled_z)

            n = arrs['n']
            net_rets = arrs['net_rets']

            # Non-overlapping selection
            i = 0
            while i < n:
                z = shuffled_z[i]
                if np.isnan(z) or z <= z_threshold:
                    i += 1
                    continue
                perm_rets.append(net_rets[i])
                i += hold_hours

        if perm_rets:
            perm_means.append(np.mean(perm_rets))

    perm_means = np.array(perm_means)
    p_value = (perm_means >= real_mean).mean()

    print(f"    Real mean net return: {real_mean*100:+.4f}%")
    print(f"    Permuted mean (avg):  {perm_means.mean()*100:+.4f}%")
    print(f"    Permuted mean (std):  {perm_means.std()*100:.4f}%")
    print(f"    Percentile of real:   {(1-p_value)*100:.1f}th")
    print(f"    p-value (one-sided):  {p_value:.4f}")
    print(f"    Significant (p<0.05): {'YES' if p_value < 0.05 else 'NO'}")


def main():
    print("=" * 72)
    print("INDEPENDENT VALIDATION: Funding Short 72h Strategy")
    print("Claimed: +56.7% return, Sharpe 1.44")
    print("=" * 72)
    print(f"Split date: {SPLIT_DATE.date()}")
    print(f"Fees: {TAKER_FEE*100:.2f}% taker + {MAKER_FEE*100:.2f}% maker = {ROUND_TRIP_FEE*100:.2f}% round-trip")
    print(f"Hold periods: {HOLD_HOURS}")

    # ── Step 1: Load data ──────────────────────────────────────────────
    print("\n" + "-" * 72)
    print("STEP 1: Loading data")
    print("-" * 72)
    token_data, skipped = load_all_tokens()
    print(f"  Loaded: {len(token_data)} tokens")
    print(f"  Skipped: {skipped}")

    # ── Step 2: Compute features ───────────────────────────────────────
    print("\n" + "-" * 72)
    print("STEP 2: Computing funding features")
    print("-" * 72)
    for token in list(token_data.keys()):
        token_data[token] = compute_funding_features(token_data[token])
    print(f"  Features computed for {len(token_data)} tokens")

    # ── Step 3: Determine thresholds from TRAIN only ───────────────────
    print("\n" + "-" * 72)
    print("STEP 3: Determining thresholds from TRAINING data only")
    print("-" * 72)
    thresholds = determine_thresholds_from_train(token_data)

    # ── Step 4: In-sample edge check ──────────────────────────────────
    check_in_sample_edge(token_data, thresholds)

    # ── Step 5: Unconditional baseline ─────────────────────────────────
    unconditional_baseline(token_data)

    # ── Step 6: OUT-OF-SAMPLE RESULTS (the real test) ──────────────────
    print("\n" + "=" * 72)
    print("STEP 6: OUT-OF-SAMPLE RESULTS (2025-07-01 onwards)")
    print("=" * 72)
    print("These are the ONLY results that matter for validation.")

    all_results = []

    for hold_h in HOLD_HOURS:
        for thresh_name, z_thresh in thresholds.items():
            label = f"Short {hold_h}h | z>{z_thresh:.2f} ({thresh_name})"
            trades = compute_trade_returns_oos(token_data, z_thresh, hold_h)
            result = report_trades(trades, label)
            if result:
                result['hold_hours'] = hold_h
                result['threshold'] = thresh_name
                result['z_threshold'] = z_thresh
                all_results.append(result)

    # ── Step 7: CRITICAL — Edge over unconditional short baseline ─────
    print("\n" + "=" * 72)
    print("STEP 7: EDGE OVER UNCONDITIONAL SHORT (regime-adjusted)")
    print("=" * 72)
    print("OOS period (Jul 2025 - Mar 2026) was a bear market (BTC -31%).")
    print("Shorts profit regardless of signal. The TRUE test is whether the")
    print("signal adds edge OVER random shorts in the same period.")

    for hold_h in [24, 48, 72]:
        # Compute unconditional short returns (non-overlapping, every h hours)
        uncond_rets = []
        for token, df in token_data.items():
            oos = df[df.index >= SPLIT_DATE]
            close = oos['close'].values
            funding = np.nan_to_num(oos['funding'].values, nan=0.0)
            n = len(oos)
            for i in range(0, n - hold_h, hold_h):
                ret = (close[i] - close[i + hold_h]) / close[i] - ROUND_TRIP_FEE
                fund_inc = sum(funding[j] / FUNDING_SETTLEMENT_HOURS for j in range(i, i + hold_h))
                ret += fund_inc
                uncond_rets.append(ret)

        unc_arr = np.array(uncond_rets)
        print(f"\n  {hold_h}h unconditional short: avg={unc_arr.mean()*100:+.4f}% "
              f"(N={len(unc_arr):,})")

        for thresh_name in ['decile', 'top5pct', 'fixed_2']:
            z_thresh = thresholds[thresh_name]
            trades = compute_trade_returns_oos(token_data, z_thresh, hold_h)
            if len(trades) == 0:
                continue
            sig_arr = trades['net_return'].values
            edge = sig_arr.mean() - unc_arr.mean()
            t, p = stats.ttest_ind(sig_arr, unc_arr, equal_var=False)
            print(f"    z>{z_thresh:.2f} ({thresh_name}): "
                  f"signal={sig_arr.mean()*100:+.4f}% (N={len(sig_arr):,}) | "
                  f"edge={edge*100:+.4f}% | t={t:.2f} p={p:.4f} "
                  f"{'***' if p < 0.01 else '**' if p < 0.05 else '*' if p < 0.10 else 'ns'}")

    # ── Step 8: Placebo tests on the best config ──────────────────────
    print("\n" + "=" * 72)
    print("STEP 8: PLACEBO / PERMUTATION TESTS")
    print("=" * 72)
    print("If the signal is real, the real result should rank in the top 5%")
    print("of permuted (time-shuffled) results.")

    # Run placebo on the most relevant config only (72h/decile)
    z_thresh = thresholds['decile']
    run_placebo_test(token_data, z_thresh, 72, n_permutations=200)

    # ── Step 9: Summary and verdict ───────────────────────────────────
    print("\n" + "=" * 72)
    print("STEP 9: SUMMARY — ALL CONFIGURATIONS")
    print("=" * 72)

    if all_results:
        print(f"\n  {'Config':<45s}  {'N':>5s}  {'NetAvg':>8s}  {'WinR':>6s}  "
              f"{'Sharpe':>7s}  {'Return':>8s}  {'MaxDD':>7s}  {'p-val':>8s}")
        print("  " + "-" * 105)
        for r in all_results:
            sig = '*' if r['p_val'] < 0.05 else ' '
            print(f"  {r['label']:<45s}  {r['n_trades']:5d}  "
                  f"{r['net_return_avg']:>+7.3f}%  {r['win_rate']:>5.1f}%  "
                  f"{r['sharpe']:>7.2f}  {r['portfolio_return']:>+7.2f}%  "
                  f"{r['max_drawdown']:>6.2f}%  {r['p_val']:>7.4f}{sig}")

    # Final verdict
    print("\n" + "=" * 72)
    print("FINAL VERDICT")
    print("=" * 72)

    # Check if any config approaches claimed Sharpe 1.44 / +56.7%
    best_sharpe = max((r['sharpe'] for r in all_results), default=0)
    best_return = max((r['portfolio_return'] for r in all_results), default=0)
    any_significant = any(r['p_val'] < 0.05 for r in all_results)

    print(f"\n  Claimed: Sharpe 1.44, +56.7% return")
    print(f"  Best OOS Sharpe found:  {best_sharpe:.2f}")
    print(f"  Best OOS return found:  {best_return:+.2f}%")
    print(f"  Any p < 0.05:           {'YES' if any_significant else 'NO'}")

    if best_sharpe >= 1.0 and best_return >= 30:
        print(f"\n  VERDICT: EDGE PARTIALLY CONFIRMED")
        print(f"  The strategy shows positive OOS performance, though")
        print(f"  the claimed Sharpe/return may be optimistic.")
    elif any_significant and best_return > 0:
        print(f"\n  VERDICT: MARGINAL EDGE")
        print(f"  Statistically significant per-trade returns exist,")
        print(f"  but portfolio-level results are weaker than claimed.")
    elif best_return > 0 and best_sharpe > 0:
        print(f"\n  VERDICT: INCONCLUSIVE")
        print(f"  Positive but not statistically significant. Could be noise.")
    else:
        print(f"\n  VERDICT: EDGE NOT CONFIRMED")
        print(f"  The claimed result does not hold up under strict OOS testing.")

    # Additional diagnostics
    print(f"\n  --- Key Diagnostics ---")
    print(f"  1. Funding income contribution: check if most 'alpha' is just")
    print(f"     collecting high funding rates (carry trade) vs directional edge")
    if all_results:
        r72_dec = [r for r in all_results if r['hold_hours'] == 72 and r['threshold'] == 'decile']
        if r72_dec:
            r = r72_dec[0]
            fund_pct = (r['funding_income_avg'] / r['net_return_avg'] * 100
                       if r['net_return_avg'] != 0 else float('inf'))
            print(f"     72h/decile: funding income = {r['funding_income_avg']:+.4f}% of "
                  f"{r['net_return_avg']:+.4f}% net ({fund_pct:.0f}% of alpha)")

    print(f"  2. If alpha is mostly funding income, it's a carry trade, not reversal")
    print(f"  3. Check monthly consistency — edge should not cluster in 1-2 months")
    print(f"  4. Permutation test p-value is the gold standard for signal validity")

    print("\n" + "=" * 72)
    print("VALIDATION COMPLETE")
    print("=" * 72)


if __name__ == '__main__':
    main()
