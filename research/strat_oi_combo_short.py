#!/workspace/venv/bin/python
"""
OI Combo Short Strategies — Does OI Improve Other Short Signals?
================================================================

Tests whether rising OI as a CONFIRMATION FILTER improves standalone short signals.
Four strategies, each tested with and without OI filter, side-by-side comparison.

Strategy 1: Funding Rate Short + OI Confirmation
  - Base: 8h funding rate z-score > 2.0 (crowded longs)
  - OI filter: OI 24h change > 0% (positions still building)

Strategy 2: Momentum Loser Short + OI Confirmation
  - Base: Token's 24h return in bottom 25% of 14-token cross-section
  - OI filter: That token's OI 24h change is positive

Strategy 3: Price Breakdown + OI Acceleration
  - Base: Price breaks below 48h rolling low
  - OI filter: OI 24h change > OI 48h avg change (acceleration)

Strategy 4: Multi-Signal Stack (Triple Filter)
  - Require ALL THREE: bottom 25% cross-section + OI rising + price < 48h low

Capital: $200K, slippage 10bps/side, max 5 concurrent positions, equal weight.
Leverage: 1x, 2x
OOS: 2025-07-01 to 2026-03-17
"""

import os
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# ── Paths ────────────────────────────────────────────────────────────────
PROJECT_DIR = '/workspace/crypto_backtest'
OI_DIR = os.path.join(PROJECT_DIR, 'data', 'perp', 'bybit_oi')
PRICE_DIR = os.path.join(PROJECT_DIR, 'data', 'perp', '1h_cache')

TOKENS = [
    'BTC', 'ETH', 'SOL', 'XRP', 'DOGE',
    'ADA', 'AVAX', 'LINK', 'DOT',
    'ARB', 'OP', 'SUI', 'APT', 'NEAR',
]

SLIPPAGE_BPS = 10
CAPITAL = 200_000
MAX_POSITIONS = 5

OOS_START = pd.Timestamp('2025-07-01', tz='UTC')
OOS_END = pd.Timestamp('2026-03-17', tz='UTC')


# ── Data Loading ─────────────────────────────────────────────────────────
def load_token_data(token: str):
    """Load and merge OI + price data for a single token."""
    oi_path = os.path.join(OI_DIR, f'{token}_oi_1h.csv')
    price_path = os.path.join(PRICE_DIR, f'{token}_1h.parquet')

    if not os.path.exists(oi_path) or not os.path.exists(price_path):
        return None

    # Load OI
    oi_df = pd.read_csv(oi_path)
    oi_df['datetime'] = pd.to_datetime(oi_df['datetime'], utc=True)
    oi_df = oi_df.set_index('datetime')[['open_interest']].sort_index()
    oi_df = oi_df[~oi_df.index.duplicated(keep='first')]

    # Load price (includes funding_rate and funding_1h)
    price_df = pd.read_parquet(price_path)
    if price_df.index.tz is None:
        price_df.index = price_df.index.tz_localize('UTC')
    price_df = price_df.sort_index()
    price_df = price_df[~price_df.index.duplicated(keep='first')]

    # Inner join on hourly timestamps
    merged = oi_df.join(price_df, how='inner').dropna(subset=['open_interest', 'close'])
    if len(merged) < 200:
        return None

    merged['token'] = token
    return merged


def load_all_data():
    """Load all 14 tokens, return concatenated DataFrame."""
    frames = []
    for token in TOKENS:
        df = load_token_data(token)
        if df is not None:
            frames.append(df)
            print(f'  [{token}] {len(df):,} rows | '
                  f'{df.index.min().strftime("%Y-%m-%d")} to {df.index.max().strftime("%Y-%m-%d")}')
    if not frames:
        raise RuntimeError('No data loaded!')
    all_data = pd.concat(frames)
    print(f'\n  Total: {len(all_data):,} rows across {len(frames)} tokens')
    return all_data


# ── Feature Engineering ──────────────────────────────────────────────────
def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute features needed for all four strategies, per token."""
    results = []
    for token, gdf in df.groupby('token'):
        g = gdf.sort_index().copy()

        # ── OI features ──
        g['oi_chg_24h'] = g['open_interest'].pct_change(24)
        g['oi_chg_1h'] = g['open_interest'].pct_change(1)
        # OI 48h average hourly change (rolling mean of 1h pct changes over 48h)
        g['oi_avg_chg_48h'] = g['oi_chg_1h'].rolling(48, min_periods=24).mean()
        # OI acceleration: current 24h change rate vs 48h average rate
        # OI 24h change as hourly rate
        g['oi_chg_24h_rate'] = g['oi_chg_24h'] / 24.0
        g['oi_accel'] = g['oi_chg_24h_rate'] - g['oi_avg_chg_48h']

        # ── Price features ──
        g['ret_1h'] = g['close'].pct_change(1)
        g['ret_24h'] = g['close'].pct_change(24)
        g['ret_48h'] = g['close'].pct_change(48)
        g['low_48h'] = g['low'].rolling(48, min_periods=24).min()
        g['price_below_48h_low'] = g['close'] < g['low_48h'].shift(1)  # shift to avoid look-ahead

        # ── Funding rate features ──
        funding_col = 'funding_1h' if 'funding_1h' in g.columns else 'funding_rate'
        # 8h cumulative funding rate
        g['funding_8h_cum'] = g[funding_col].rolling(8, min_periods=4).sum()
        # Z-score of 8h funding over 7-day rolling window (168h)
        fm = g['funding_8h_cum'].rolling(168, min_periods=48).mean()
        fs = g['funding_8h_cum'].rolling(168, min_periods=48).std().replace(0, np.nan)
        g['funding_zscore_8h'] = (g['funding_8h_cum'] - fm) / fs

        # ── ATR for position sizing / context ──
        tr = pd.concat([
            g['high'] - g['low'],
            (g['high'] - g['close'].shift(1)).abs(),
            (g['low'] - g['close'].shift(1)).abs()
        ], axis=1).max(axis=1)
        g['atr_14'] = tr.rolling(14).mean()

        results.append(g)

    return pd.concat(results)


def compute_cross_sectional_rank(df: pd.DataFrame) -> pd.DataFrame:
    """Compute cross-sectional percentile rank of 24h returns at each timestamp."""
    df = df.copy()

    # For each timestamp, rank each token's 24h return across all tokens
    # Use groupby on the index (timestamp) to compute cross-sectional rank
    df['ret_24h_rank'] = df.groupby(level=0)['ret_24h'].rank(pct=True)

    # Bottom 25% flag
    df['is_bottom_25pct'] = df['ret_24h_rank'] <= 0.25

    return df


# ── Signal Generation ────────────────────────────────────────────────────
def generate_strategy_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Generate all strategy signals (base and OI-filtered versions)."""
    df = df.copy()

    # ── Strategy 1: Funding Rate Short ──
    # Base: 8h funding z-score > 2.0
    df['sig1_base'] = df['funding_zscore_8h'] > 2.0
    # With OI filter: also require OI 24h change > 0%
    df['sig1_oi'] = df['sig1_base'] & (df['oi_chg_24h'] > 0.0)

    # ── Strategy 2: Momentum Loser Short ──
    # Base: token in bottom 25% of cross-sectional 24h returns
    df['sig2_base'] = df['is_bottom_25pct'] == True
    # With OI filter: also require positive OI change
    df['sig2_oi'] = df['sig2_base'] & (df['oi_chg_24h'] > 0.0)

    # ── Strategy 3: Price Breakdown Short ──
    # Base: price below 48h rolling low
    df['sig3_base'] = df['price_below_48h_low'] == True
    # With OI filter: OI acceleration (24h rate > 48h avg rate)
    df['sig3_oi'] = df['sig3_base'] & (df['oi_accel'] > 0)

    # ── Strategy 4: Multi-Signal Stack (Triple Filter) ──
    # Require ALL: bottom 25% return + OI rising + price below 48h low
    df['sig4_triple'] = (
        (df['is_bottom_25pct'] == True) &
        (df['oi_chg_24h'] > 0.0) &
        (df['price_below_48h_low'] == True)
    )

    return df


# ── Vectorized Trade Computation ─────────────────────────────────────────
def compute_short_trades(df_token, signal_col, hold_hours, slip):
    """Compute short trades for a single token given a signal column.

    Returns DataFrame of potential trades with raw returns (before leverage).
    Direction is always -1 (short).
    """
    direction = -1
    oos_mask = (df_token.index >= OOS_START) & (df_token.index <= OOS_END)
    sig_mask = oos_mask & df_token[signal_col].fillna(False)
    entry_positions = np.where(sig_mask.values)[0]

    if len(entry_positions) == 0:
        return pd.DataFrame()

    close_arr = df_token['close'].values
    idx_arr = df_token.index.values
    n = len(df_token)

    exit_positions = np.minimum(entry_positions + hold_hours, n - 1)
    entry_prices = close_arr[entry_positions] * (1 + slip * direction)  # short: sell at bid
    exit_prices = close_arr[exit_positions] * (1 - slip * direction)    # cover: buy at ask
    raw_rets = direction * (exit_prices / entry_prices - 1)
    hold_hrs = exit_positions - entry_positions

    return pd.DataFrame({
        'entry_time': idx_arr[entry_positions],
        'exit_time': idx_arr[exit_positions],
        'entry_price': entry_prices,
        'exit_price': exit_prices,
        'raw_ret': raw_rets,
        'hold_hours': hold_hrs,
        'direction': direction,
    })


def build_all_trades(df, signal_col, hold_hours):
    """Build potential trades across all tokens for a given signal."""
    slip = SLIPPAGE_BPS / 10_000
    all_trades = []

    for token in TOKENS:
        tdf = df[df['token'] == token].copy()
        if len(tdf) == 0:
            continue
        trades = compute_short_trades(tdf, signal_col, hold_hours, slip)
        if len(trades) > 0:
            trades['token'] = token
            all_trades.append(trades)

    if not all_trades:
        return pd.DataFrame()
    return pd.concat(all_trades, ignore_index=True)


# ── Portfolio Simulation ─────────────────────────────────────────────────
def simulate_portfolio(all_potential_trades, leverage):
    """Simulate portfolio with position limits, equal weight allocation."""
    if len(all_potential_trades) == 0:
        return [], CAPITAL, 0.0

    tdf = all_potential_trades.sort_values('entry_time').reset_index(drop=True)

    equity = float(CAPITAL)
    peak_equity = float(CAPITAL)
    max_dd = 0.0
    trades = []

    # active: list of (exit_time, token, raw_ret, entry_time, margin, entry_price, exit_price, hold_hours)
    active = []

    for _, row in tdf.iterrows():
        entry_time = row['entry_time']

        # Close expired positions
        still_active = []
        for pos in active:
            if entry_time >= pos[0]:
                margin = pos[4]
                lev_ret = pos[2] * leverage
                pnl_usd = margin * lev_ret
                equity += pnl_usd
                trades.append({
                    'token': pos[1],
                    'direction': -1,
                    'entry_time': pos[3],
                    'exit_time': pos[0],
                    'entry_price': pos[5],
                    'exit_price': pos[6],
                    'pnl_pct': lev_ret * 100,
                    'pnl_usd': pnl_usd,
                    'leverage': leverage,
                    'hold_hours': pos[7],
                })
                if equity > peak_equity:
                    peak_equity = equity
                dd_now = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
                if dd_now > max_dd:
                    max_dd = dd_now
            else:
                still_active.append(pos)
        active = still_active

        if equity <= 0:
            break

        # Try entry
        if len(active) < MAX_POSITIONS:
            token = row['token']
            already_in = any(p[1] == token for p in active)
            if not already_in:
                margin = equity / MAX_POSITIONS
                active.append((
                    row['exit_time'], token, row['raw_ret'],
                    entry_time, margin,
                    row['entry_price'], row['exit_price'],
                    row['hold_hours'],
                ))

    # Close remaining
    for pos in active:
        margin = pos[4]
        lev_ret = pos[2] * leverage
        pnl_usd = margin * lev_ret
        equity += pnl_usd
        trades.append({
            'token': pos[1],
            'direction': -1,
            'entry_time': pos[3],
            'exit_time': pos[0],
            'entry_price': pos[5],
            'exit_price': pos[6],
            'pnl_pct': lev_ret * 100,
            'pnl_usd': pnl_usd,
            'leverage': leverage,
            'hold_hours': pos[7],
        })
    if equity > peak_equity:
        peak_equity = equity
    dd_now = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
    if dd_now > max_dd:
        max_dd = dd_now

    return trades, equity, max_dd


# ── Metrics Computation ──────────────────────────────────────────────────
def compute_metrics(trades_list, final_equity, max_dd):
    """Compute performance metrics from a list of trade dicts."""
    if not trades_list:
        return {
            'total_return_pct': 0.0,
            'annual_return_pct': 0.0,
            'max_dd_pct': 0.0,
            'sharpe': 0.0,
            'win_rate': 0.0,
            'num_trades': 0,
            'profit_factor': 0.0,
            'avg_trade_pnl': 0.0,
            'final_equity': CAPITAL,
        }

    df = pd.DataFrame(trades_list)
    total_ret = (final_equity / CAPITAL - 1) * 100

    oos_days = (OOS_END - OOS_START).days
    annual_factor = 365.25 / oos_days if oos_days > 0 else 1
    annual_ret = total_ret * annual_factor

    n_trades = len(df)
    n_wins = (df['pnl_usd'] > 0).sum()
    win_rate = n_wins / n_trades * 100 if n_trades > 0 else 0

    gross_wins = df.loc[df['pnl_usd'] > 0, 'pnl_usd'].sum()
    gross_losses = abs(df.loc[df['pnl_usd'] <= 0, 'pnl_usd'].sum())
    pf = gross_wins / gross_losses if gross_losses > 0 else float('inf')

    avg_trade_pnl = df['pnl_usd'].mean()

    # Sharpe: annualized from daily PnL
    df['entry_time'] = pd.to_datetime(df['entry_time'])
    df['date'] = df['entry_time'].dt.date
    daily_pnl = df.groupby('date')['pnl_usd'].sum()
    # Fill in non-trading days with 0
    full_range = pd.date_range(OOS_START, OOS_END, freq='D')
    daily_pnl = daily_pnl.reindex(full_range.date, fill_value=0)
    daily_ret = daily_pnl / CAPITAL  # simple return on initial capital
    if len(daily_ret) > 1 and daily_ret.std() > 0:
        sharpe = (daily_ret.mean() / daily_ret.std()) * np.sqrt(365.25)
    else:
        sharpe = 0.0

    return {
        'total_return_pct': total_ret,
        'annual_return_pct': annual_ret,
        'max_dd_pct': max_dd * 100,
        'sharpe': sharpe,
        'win_rate': win_rate,
        'num_trades': n_trades,
        'profit_factor': pf,
        'avg_trade_pnl': avg_trade_pnl,
        'final_equity': final_equity,
    }


# ── Comparison Printer ───────────────────────────────────────────────────
def print_comparison(strategy_name, metrics_without, metrics_with, hold_label, leverage):
    """Print side-by-side comparison of base vs OI-filtered strategy."""

    def fmt_val(key, m):
        if key == 'total_return_pct':
            return f'{m[key]:+.1f}%'
        elif key == 'annual_return_pct':
            return f'{m[key]:+.1f}%'
        elif key == 'max_dd_pct':
            return f'{m[key]:.1f}%'
        elif key == 'sharpe':
            return f'{m[key]:.2f}'
        elif key == 'win_rate':
            return f'{m[key]:.1f}%'
        elif key == 'num_trades':
            return f'{m[key]}'
        elif key == 'profit_factor':
            return f'{m[key]:.2f}'
        elif key == 'avg_trade_pnl':
            return f'${m[key]:+,.0f}'
        return str(m[key])

    def improvement(key, mw, mo):
        vw = mw[key]
        vo = mo[key]
        if key == 'max_dd_pct':
            # Lower DD is better
            if vo == 0:
                return 'N/A'
            diff = vo - vw
            return f'{diff:+.1f}pp' if diff != 0 else '--'
        elif key == 'num_trades':
            return f'{vw - vo:+d}'
        elif key in ('total_return_pct', 'annual_return_pct', 'win_rate'):
            diff = vw - vo
            return f'{diff:+.1f}pp'
        elif key in ('sharpe', 'profit_factor'):
            diff = vw - vo
            return f'{diff:+.2f}'
        elif key == 'avg_trade_pnl':
            diff = vw - vo
            return f'${diff:+,.0f}'
        return ''

    rows = [
        ('Total Return', 'total_return_pct'),
        ('Annual Return', 'annual_return_pct'),
        ('Max Drawdown', 'max_dd_pct'),
        ('Sharpe', 'sharpe'),
        ('Win Rate', 'win_rate'),
        ('Trades', 'num_trades'),
        ('Profit Factor', 'profit_factor'),
        ('Avg Trade PnL', 'avg_trade_pnl'),
    ]

    print(f'\n  {strategy_name} [{hold_label}, {leverage}x]')
    print(f'  {"-"*72}')
    print(f'  {"Metric":<18s}  {"WITHOUT OI Filter":>20s}  {"WITH OI Filter":>20s}  {"Improvement":>12s}')
    print(f'  {"-"*18}  {"-"*20}  {"-"*20}  {"-"*12}')

    for label, key in rows:
        val_no = fmt_val(key, metrics_without)
        val_oi = fmt_val(key, metrics_with)
        imp = improvement(key, metrics_with, metrics_without)
        print(f'  {label:<18s}  {val_no:>20s}  {val_oi:>20s}  {imp:>12s}')


def print_strategy4_table(metrics, hold_label, leverage):
    """Print results for Strategy 4 (no base comparison)."""
    rows = [
        ('Total Return', 'total_return_pct'),
        ('Annual Return', 'annual_return_pct'),
        ('Max Drawdown', 'max_dd_pct'),
        ('Sharpe', 'sharpe'),
        ('Win Rate', 'win_rate'),
        ('Trades', 'num_trades'),
        ('Profit Factor', 'profit_factor'),
        ('Avg Trade PnL', 'avg_trade_pnl'),
    ]

    print(f'\n  Strategy 4: Multi-Signal Stack [{hold_label}, {leverage}x]')
    print(f'  {"-"*42}')
    print(f'  {"Metric":<18s}  {"Triple Filter":>20s}')
    print(f'  {"-"*18}  {"-"*20}')

    for label, key in rows:
        if key == 'total_return_pct':
            v = f'{metrics[key]:+.1f}%'
        elif key == 'annual_return_pct':
            v = f'{metrics[key]:+.1f}%'
        elif key == 'max_dd_pct':
            v = f'{metrics[key]:.1f}%'
        elif key == 'sharpe':
            v = f'{metrics[key]:.2f}'
        elif key == 'win_rate':
            v = f'{metrics[key]:.1f}%'
        elif key == 'num_trades':
            v = f'{metrics[key]}'
        elif key == 'profit_factor':
            v = f'{metrics[key]:.2f}'
        elif key == 'avg_trade_pnl':
            v = f'${metrics[key]:+,.0f}'
        else:
            v = str(metrics[key])
        print(f'  {label:<18s}  {v:>20s}')


# ── Per-token and monthly breakdown ──────────────────────────────────────
def print_detailed_breakdown(trades_list, label):
    """Print per-token and monthly breakdown for a set of trades."""
    if not trades_list:
        print(f'\n  {label}: NO TRADES')
        return

    df = pd.DataFrame(trades_list)
    df['entry_time'] = pd.to_datetime(df['entry_time'])
    df['month'] = df['entry_time'].dt.to_period('M')

    print(f'\n  {label} — Per-Month Breakdown:')
    print(f'  {"Month":>10s}  {"Trades":>6s}  {"WinR":>6s}  {"PnL":>12s}  {"AvgRet":>8s}')
    for month, mdf in df.groupby('month'):
        mn = len(mdf)
        mwr = (mdf['pnl_usd'] > 0).mean() * 100
        mpnl = mdf['pnl_usd'].sum()
        mavg = mdf['pnl_pct'].mean()
        print(f'  {str(month):>10s}  {mn:6d}  {mwr:5.1f}%  ${mpnl:>+11,.0f}  {mavg:>+7.2f}%')

    print(f'\n  {label} — Per-Token Breakdown:')
    token_pnl = df.groupby('token').agg(
        trades=('pnl_usd', 'count'),
        total_pnl=('pnl_usd', 'sum'),
        avg_ret=('pnl_pct', 'mean'),
        win_rate=('pnl_usd', lambda x: (x > 0).mean() * 100),
    ).sort_values('total_pnl', ascending=False)
    print(f'  {"Token":>8s}  {"Trades":>6s}  {"PnL":>12s}  {"AvgRet":>8s}  {"WinR":>6s}')
    for token, row in token_pnl.iterrows():
        print(f'  {token:>8s}  {row["trades"]:6.0f}  ${row["total_pnl"]:>+11,.0f}  '
              f'{row["avg_ret"]:>+7.2f}%  {row["win_rate"]:5.1f}%')


# ── Main ─────────────────────────────────────────────────────────────────
def main():
    print('=' * 78)
    print('  OI COMBO SHORT STRATEGIES — DOES OI IMPROVE SHORT SIGNALS?')
    print('=' * 78)
    print(f'  Capital: ${CAPITAL:,.0f} | Slippage: {SLIPPAGE_BPS}bps/side | '
          f'Max positions: {MAX_POSITIONS}')
    print(f'  OOS: {OOS_START.date()} to {OOS_END.date()}')
    print(f'  Tokens: {", ".join(TOKENS)}')

    # ── Load data ──
    print(f'\n{"="*78}')
    print('  [1] LOADING DATA')
    print('=' * 78)
    df = load_all_data()

    # ── Compute features ──
    print(f'\n{"="*78}')
    print('  [2] COMPUTING FEATURES')
    print('=' * 78)
    df = compute_features(df)
    df = compute_cross_sectional_rank(df)
    df = generate_strategy_signals(df)

    # ── Signal counts ──
    oos = df[(df.index >= OOS_START) & (df.index <= OOS_END)]
    print(f'\n  Signal Counts (OOS period only):')
    print(f'  {"Signal":<30s}  {"Count":>8s}  {"% of bars":>10s}')
    print(f'  {"-"*30}  {"-"*8}  {"-"*10}')
    signal_cols = [
        ('Strat1 Base (Funding)', 'sig1_base'),
        ('Strat1 + OI Filter', 'sig1_oi'),
        ('Strat2 Base (Loser)', 'sig2_base'),
        ('Strat2 + OI Filter', 'sig2_oi'),
        ('Strat3 Base (Breakdown)', 'sig3_base'),
        ('Strat3 + OI Filter', 'sig3_oi'),
        ('Strat4 Triple Stack', 'sig4_triple'),
    ]
    total_bars = len(oos)
    for label, col in signal_cols:
        cnt = oos[col].sum()
        pct = cnt / total_bars * 100
        print(f'  {label:<30s}  {cnt:>8.0f}  {pct:>9.2f}%')

    # ── Run backtests ──
    print(f'\n{"="*78}')
    print('  [3] RUNNING BACKTESTS')
    print('=' * 78)

    # Strategy configs: (name, base_signal, oi_signal, hold_hours_list)
    strategies = [
        ('Strategy 1: Funding Short', 'sig1_base', 'sig1_oi', [24, 48, 72]),
        ('Strategy 2: Momentum Loser', 'sig2_base', 'sig2_oi', [24, 48]),
        ('Strategy 3: Price Breakdown', 'sig3_base', 'sig3_oi', [24, 48, 72]),
    ]
    leverages = [1, 2]

    # Store all results for final summary
    all_results = []
    best_oi_improvement = None
    best_improvement_val = -999

    for strat_name, base_sig, oi_sig, hold_hours_list in strategies:
        print(f'\n{"#"*78}')
        print(f'  {strat_name}')
        print(f'{"#"*78}')

        for hold_h in hold_hours_list:
            hold_label = f'{hold_h}h hold'

            # Build potential trades
            trades_base = build_all_trades(df, base_sig, hold_h)
            trades_oi = build_all_trades(df, oi_sig, hold_h)

            for lev in leverages:
                # Simulate base
                tl_base, eq_base, dd_base = simulate_portfolio(trades_base, lev)
                m_base = compute_metrics(tl_base, eq_base, dd_base)
                m_base['config'] = f'{strat_name} | base | {hold_label} | {lev}x'

                # Simulate with OI filter
                tl_oi, eq_oi, dd_oi = simulate_portfolio(trades_oi, lev)
                m_oi = compute_metrics(tl_oi, eq_oi, dd_oi)
                m_oi['config'] = f'{strat_name} | +OI | {hold_label} | {lev}x'

                # Print comparison
                print_comparison(strat_name, m_base, m_oi, hold_label, lev)

                all_results.append(m_base)
                all_results.append(m_oi)

                # Track best OI improvement by Sharpe
                sharpe_diff = m_oi['sharpe'] - m_base['sharpe']
                if sharpe_diff > best_improvement_val:
                    best_improvement_val = sharpe_diff
                    best_oi_improvement = (strat_name, hold_label, lev, m_base, m_oi, tl_oi)

    # ── Strategy 4: Triple Filter ──
    print(f'\n{"#"*78}')
    print(f'  Strategy 4: Multi-Signal Stack (Triple Filter)')
    print(f'{"#"*78}')

    for hold_h in [24, 48, 72]:
        hold_label = f'{hold_h}h hold'
        trades_s4 = build_all_trades(df, 'sig4_triple', hold_h)

        for lev in leverages:
            tl_s4, eq_s4, dd_s4 = simulate_portfolio(trades_s4, lev)
            m_s4 = compute_metrics(tl_s4, eq_s4, dd_s4)
            m_s4['config'] = f'Strategy 4: Triple | {hold_label} | {lev}x'

            print_strategy4_table(m_s4, hold_label, lev)
            all_results.append(m_s4)

    # ── Detailed breakdown for best OI-filtered config ──
    if best_oi_improvement:
        strat_name, hold_label, lev, m_base, m_oi, tl_oi = best_oi_improvement
        print(f'\n{"="*78}')
        print(f'  DETAILED BREAKDOWN — Best OI Improvement')
        print(f'  {strat_name} | {hold_label} | {lev}x')
        print(f'  (Sharpe improvement: {best_improvement_val:+.2f})')
        print('=' * 78)
        print_detailed_breakdown(tl_oi, f'{strat_name} + OI ({hold_label}, {lev}x)')

    # ── Grand Summary ──
    print(f'\n{"="*78}')
    print('  GRAND SUMMARY — ALL CONFIGURATIONS')
    print('=' * 78)

    summary_df = pd.DataFrame(all_results)
    summary_df = summary_df.sort_values('sharpe', ascending=False)

    print(f'\n  {"Config":<55s}  {"Return":>8s}  {"Annual":>8s}  {"MaxDD":>7s}  '
          f'{"Sharpe":>7s}  {"Trades":>6s}  {"WinR":>6s}  {"PF":>6s}')
    print(f'  {"-"*55}  {"-"*8}  {"-"*8}  {"-"*7}  {"-"*7}  {"-"*6}  {"-"*6}  {"-"*6}')

    for _, row in summary_df.iterrows():
        print(f'  {row["config"]:<55s}  '
              f'{row["total_return_pct"]:>+7.1f}%  '
              f'{row["annual_return_pct"]:>+7.1f}%  '
              f'{row["max_dd_pct"]:>6.1f}%  '
              f'{row["sharpe"]:>7.2f}  '
              f'{row["num_trades"]:>6.0f}  '
              f'{row["win_rate"]:>5.1f}%  '
              f'{row["profit_factor"]:>5.2f}')

    # ── OI Value Add Summary ──
    print(f'\n{"="*78}')
    print('  OI VALUE-ADD SUMMARY: Does OI Improve Short Signals?')
    print('=' * 78)

    # Compare base vs OI for each strategy/hold/leverage combo
    print(f'\n  {"Strategy + Config":<45s}  {"Base Sharpe":>12s}  {"OI Sharpe":>12s}  '
          f'{"Delta":>8s}  {"OI Helps?":>10s}')
    print(f'  {"-"*45}  {"-"*12}  {"-"*12}  {"-"*8}  {"-"*10}')

    # Re-iterate through strategies to pair base vs OI
    oi_helps_count = 0
    oi_hurts_count = 0

    for strat_name, base_sig, oi_sig, hold_hours_list in strategies:
        for hold_h in hold_hours_list:
            hold_label = f'{hold_h}h'
            for lev in leverages:
                base_key = f'{strat_name} | base | {hold_h}h hold | {lev}x'
                oi_key = f'{strat_name} | +OI | {hold_h}h hold | {lev}x'

                m_base = summary_df[summary_df['config'] == base_key]
                m_oi = summary_df[summary_df['config'] == oi_key]

                if len(m_base) == 0 or len(m_oi) == 0:
                    continue

                s_base = m_base.iloc[0]['sharpe']
                s_oi = m_oi.iloc[0]['sharpe']
                delta = s_oi - s_base
                helps = 'YES' if delta > 0.05 else ('NO' if delta < -0.05 else 'NEUTRAL')

                if helps == 'YES':
                    oi_helps_count += 1
                elif helps == 'NO':
                    oi_hurts_count += 1

                short_name = f'{strat_name} | {hold_label} | {lev}x'
                print(f'  {short_name:<45s}  {s_base:>12.2f}  {s_oi:>12.2f}  '
                      f'{delta:>+7.2f}  {helps:>10s}')

    total_comparisons = oi_helps_count + oi_hurts_count + \
        (len(all_results) // 2 - oi_helps_count - oi_hurts_count -
         len([h for h in [24, 48, 72] for _ in leverages]))  # subtract strat4

    print(f'\n  Verdict: OI filter IMPROVED Sharpe in {oi_helps_count} comparisons, '
          f'HURT in {oi_hurts_count}')

    # ── Win rate improvement ──
    print(f'\n  Win Rate Impact:')
    print(f'  {"Strategy + Config":<45s}  {"Base WR":>8s}  {"OI WR":>8s}  {"Delta":>8s}')
    print(f'  {"-"*45}  {"-"*8}  {"-"*8}  {"-"*8}')

    for strat_name, base_sig, oi_sig, hold_hours_list in strategies:
        for hold_h in hold_hours_list:
            hold_label = f'{hold_h}h'
            for lev in leverages:
                base_key = f'{strat_name} | base | {hold_h}h hold | {lev}x'
                oi_key = f'{strat_name} | +OI | {hold_h}h hold | {lev}x'

                m_base = summary_df[summary_df['config'] == base_key]
                m_oi = summary_df[summary_df['config'] == oi_key]

                if len(m_base) == 0 or len(m_oi) == 0:
                    continue

                wr_base = m_base.iloc[0]['win_rate']
                wr_oi = m_oi.iloc[0]['win_rate']
                delta = wr_oi - wr_base

                short_name = f'{strat_name} | {hold_label} | {lev}x'
                print(f'  {short_name:<45s}  {wr_base:>7.1f}%  {wr_oi:>7.1f}%  '
                      f'{delta:>+7.1f}pp')

    # ── Best overall config ──
    print(f'\n{"="*78}')
    print('  TOP 5 CONFIGURATIONS BY SHARPE')
    print('=' * 78)
    top5 = summary_df.head(5)
    for rank, (_, row) in enumerate(top5.iterrows(), 1):
        print(f'\n  #{rank}: {row["config"]}')
        print(f'      Total Return:  {row["total_return_pct"]:+.1f}%')
        print(f'      Annual Return: {row["annual_return_pct"]:+.1f}%')
        print(f'      Max Drawdown:  {row["max_dd_pct"]:.1f}%')
        print(f'      Sharpe:        {row["sharpe"]:.2f}')
        print(f'      Win Rate:      {row["win_rate"]:.1f}%')
        print(f'      Trades:        {row["num_trades"]:.0f}')
        print(f'      Profit Factor: {row["profit_factor"]:.2f}')
        print(f'      Avg Trade PnL: ${row["avg_trade_pnl"]:+,.0f}')

    print(f'\n{"="*78}')
    print('  CONCLUSION')
    print('=' * 78)
    print(f'  OI filter improved Sharpe in {oi_helps_count} of '
          f'{oi_helps_count + oi_hurts_count + (len(all_results) // 2 - oi_helps_count - oi_hurts_count - len([h for h in [24, 48, 72] for _ in leverages]))} '
          f'strategy-hold-leverage combinations (excluding Strat 4 which has no base).')
    print(f'  Check the tables above for specific improvements per strategy.')

    print(f'\n{"="*78}')
    print('  BACKTEST COMPLETE')
    print('=' * 78)


if __name__ == '__main__':
    main()
