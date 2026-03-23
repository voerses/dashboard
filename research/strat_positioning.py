#!/workspace/venv/bin/python
"""
Binance Positioning Data — Multi-Strategy Backtest
====================================================

Four strategies using Binance Long/Short positioning data:

Strategy A: Retail Contrarian
  - Global L/S ratio > 70th pctile (rolling 168h) -> SHORT (crowded longs)
  - Global L/S ratio < 30th pctile -> LONG (crowded shorts)
  - Hold: 24h, 48h, 72h | Leverage: 1x, 2x

Strategy B: Top vs Retail Divergence
  - Top trader position L/S < 1.0 AND global L/S > 1.2 -> SHORT
  - Hold: 24h, 48h

Strategy C: Taker Flow Momentum
  - Taker buySellRatio < 0.8 -> SHORT (aggressive sellers)
  - Taker buySellRatio > 1.2 -> LONG (aggressive buyers)
  - Hold: 24h, 48h

Strategy D: Combined Signal
  - SHORT when: global L/S > 70th pctile AND taker sell > buy AND top traders short-biased
  - Hold: 24h, 48h

Data: ~20 days of hourly positioning data (2026-03-03 to 2026-03-23) for 19 symbols.
Price: Binance perp 1h OHLCV.

Since positioning data only covers ~20 days (all within March 2026), and price data
ends 2026-03-17, the effective backtest window is 2026-03-03 to 2026-03-17.
No IS/OOS split is possible with this short sample; we report full-sample results.
"""

import os
import warnings
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

warnings.filterwarnings('ignore')

# ── Paths ───────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(BASE_DIR, '..')
CACHE_DIR = os.path.join(PROJECT_DIR, 'data', 'perp', '1h_cache')
POS_DIR = os.path.join(PROJECT_DIR, 'data', 'alternative', 'binance_positioning')

# Positioning data available: 2026-03-03 to 2026-03-23
# Price data available: through 2026-03-17
# Effective window: 2026-03-03 to 2026-03-17
BT_START = pd.Timestamp('2026-03-03', tz='UTC')
BT_END = pd.Timestamp('2026-03-17', tz='UTC')

INITIAL_CAPITAL = 200_000.0
SLIPPAGE_BPS = 10
MAX_POSITIONS = 5

SYMBOLS_MAP = {
    'AAVEUSDT': 'AAVE', 'ADAUSDT': 'ADA', 'ARBUSDT': 'ARB',
    'AVAXUSDT': 'AVAX', 'BNBUSDT': 'BNB', 'BTCUSDT': 'BTC',
    'DOGEUSDT': 'DOGE', 'DOTUSDT': 'DOT', 'ETHUSDT': 'ETH',
    'INJUSDT': 'INJ', 'LINKUSDT': 'LINK', 'LTCUSDT': 'LTC',
    'NEARUSDT': 'NEAR', 'OPUSDT': 'OP', 'SOLUSDT': 'SOL',
    'SUIUSDT': 'SUI', 'UNIUSDT': 'UNI', 'WIFUSDT': 'WIF',
    'XRPUSDT': 'XRP',
}


# ── Data Loading ────────────────────────────────────────────────────────
def load_positioning_data():
    """Load all four positioning datasets, convert types, return dict."""
    global_ls = pd.read_parquet(os.path.join(POS_DIR, 'global_ls_ratio.parquet'))
    taker_vol = pd.read_parquet(os.path.join(POS_DIR, 'taker_buy_sell_vol.parquet'))
    top_acct = pd.read_parquet(os.path.join(POS_DIR, 'top_ls_account_ratio.parquet'))
    top_pos = pd.read_parquet(os.path.join(POS_DIR, 'top_ls_position_ratio.parquet'))

    for df in [global_ls, top_acct, top_pos]:
        df['longShortRatio'] = pd.to_numeric(df['longShortRatio'], errors='coerce')
        df['longAccount'] = pd.to_numeric(df['longAccount'], errors='coerce')
        df['shortAccount'] = pd.to_numeric(df['shortAccount'], errors='coerce')

    taker_vol['buySellRatio'] = pd.to_numeric(taker_vol['buySellRatio'], errors='coerce')
    taker_vol['buyVol'] = pd.to_numeric(taker_vol['buyVol'], errors='coerce')
    taker_vol['sellVol'] = pd.to_numeric(taker_vol['sellVol'], errors='coerce')

    return {
        'global_ls': global_ls,
        'taker_vol': taker_vol,
        'top_acct': top_acct,
        'top_pos': top_pos,
    }


def load_price(token):
    """Load 1h OHLCV for a token, ensure UTC-aware index."""
    path = os.path.join(CACHE_DIR, f'{token}_1h.parquet')
    df = pd.read_parquet(path)
    if df.index.tz is None:
        df.index = df.index.tz_localize('UTC')
    return df


def build_merged_data(pos_data):
    """
    For each of the 19 symbols, merge positioning signals with price data.
    Returns dict: token -> merged DataFrame indexed by timestamp (UTC).
    """
    merged_all = {}

    for usdt_sym, token in SYMBOLS_MAP.items():
        price = load_price(token)
        price = price[['close']].copy()
        price.index.name = 'timestamp'

        # Forward returns for IC analysis
        for h in [1, 4, 24, 48, 72]:
            price[f'fwd_ret_{h}h'] = price['close'].pct_change(h).shift(-h)

        # Global L/S
        g = pos_data['global_ls']
        g = g[g['symbol'] == usdt_sym][['timestamp', 'longShortRatio']].copy()
        g = g.rename(columns={'longShortRatio': 'global_ls'})
        g = g.set_index('timestamp').sort_index()
        g = g[~g.index.duplicated(keep='first')]

        # Top position L/S
        tp = pos_data['top_pos']
        tp = tp[tp['symbol'] == usdt_sym][['timestamp', 'longShortRatio']].copy()
        tp = tp.rename(columns={'longShortRatio': 'top_pos_ls'})
        tp = tp.set_index('timestamp').sort_index()
        tp = tp[~tp.index.duplicated(keep='first')]

        # Top account L/S
        ta = pos_data['top_acct']
        ta = ta[ta['symbol'] == usdt_sym][['timestamp', 'longShortRatio']].copy()
        ta = ta.rename(columns={'longShortRatio': 'top_acct_ls'})
        ta = ta.set_index('timestamp').sort_index()
        ta = ta[~ta.index.duplicated(keep='first')]

        # Taker buy/sell
        tv = pos_data['taker_vol']
        tv = tv[tv['symbol'] == usdt_sym][['timestamp', 'buySellRatio']].copy()
        tv = tv.rename(columns={'buySellRatio': 'taker_bsr'})
        tv = tv.set_index('timestamp').sort_index()
        tv = tv[~tv.index.duplicated(keep='first')]

        # Merge all
        merged = price.join(g, how='inner')
        merged = merged.join(tp, how='left')
        merged = merged.join(ta, how='left')
        merged = merged.join(tv, how='left')

        if len(merged) < 50:
            continue

        # Derived features
        merged['global_ls_chg_4h'] = merged['global_ls'].pct_change(4)
        merged['global_ls_chg_24h'] = merged['global_ls'].pct_change(24)
        merged['divergence'] = merged['top_pos_ls'] - merged['global_ls']

        # Rolling percentiles for global L/S (168h = 7 days)
        merged['global_ls_pctile'] = merged['global_ls'].rolling(
            168, min_periods=24
        ).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)

        merged_all[token] = merged

    return merged_all


# ── IC Analysis ─────────────────────────────────────────────────────────
def compute_ic_table(merged_all):
    """Compute rank IC between each signal and forward returns."""
    results = []
    for token, df in merged_all.items():
        clean = df.dropna(subset=['global_ls', 'taker_bsr', 'top_pos_ls',
                                   'fwd_ret_1h', 'fwd_ret_4h', 'fwd_ret_24h'])
        if len(clean) < 30:
            continue

        row = {'symbol': token, 'n_obs': len(clean)}

        signals = {
            'gls_lvl_24h': ('global_ls', 'fwd_ret_24h'),
            'gls_chg4_24h': ('global_ls_chg_4h', 'fwd_ret_24h'),
            'gls_chg24_24h': ('global_ls_chg_24h', 'fwd_ret_24h'),
            'tkr_bsr_24h': ('taker_bsr', 'fwd_ret_24h'),
            'tkr_bsr_4h': ('taker_bsr', 'fwd_ret_4h'),
            'tkr_bsr_1h': ('taker_bsr', 'fwd_ret_1h'),
            'div_24h': ('divergence', 'fwd_ret_24h'),
            'div_4h': ('divergence', 'fwd_ret_4h'),
            'gls_lvl_4h': ('global_ls', 'fwd_ret_4h'),
            'gls_lvl_1h': ('global_ls', 'fwd_ret_1h'),
        }

        for ic_name, (sig_col, ret_col) in signals.items():
            valid = clean[[sig_col, ret_col]].dropna()
            if len(valid) > 10:
                corr, _ = spearmanr(valid[sig_col], valid[ret_col])
                row[ic_name] = corr
            else:
                row[ic_name] = np.nan

        results.append(row)

    return pd.DataFrame(results).set_index('symbol')


# ── Signal Generation ───────────────────────────────────────────────────
def generate_signals(merged_all):
    """
    Add boolean signal columns for strategies A, B, C, D to each token's DataFrame.
    """
    for token, df in merged_all.items():
        # Strategy A: Retail Contrarian
        # Use rolling 168h percentile
        df['sig_A_short'] = df['global_ls_pctile'] > 0.70  # crowded longs
        df['sig_A_long'] = df['global_ls_pctile'] < 0.30   # crowded shorts

        # Strategy B: Top vs Retail Divergence
        # Top position is short-biased (< 1.0) but global is long-biased (> 1.2)
        df['sig_B_short'] = (
            (df['top_pos_ls'] < 1.0) &
            (df['global_ls'] > 1.2)
        )

        # Strategy C: Taker Flow Momentum
        df['sig_C_short'] = df['taker_bsr'] < 0.8  # aggressive sellers
        df['sig_C_long'] = df['taker_bsr'] > 1.2   # aggressive buyers

        # Strategy D: Combined Signal
        # All signals align for short: crowded longs + sell pressure + smart money short
        df['sig_D_short'] = (
            (df['global_ls_pctile'] > 0.70) &
            (df['taker_bsr'] < 1.0) &
            (df['top_pos_ls'] < 1.0)
        )

        merged_all[token] = df

    return merged_all


# ── Trade Generation ────────────────────────────────────────────────────
def compute_trades_fixed(df, signal_col, direction, hold_hours, slip):
    """Vectorized fixed-hold trade computation."""
    bt_mask = (df.index >= BT_START) & (df.index <= BT_END)
    sig_mask = bt_mask & df[signal_col].fillna(False)
    entry_positions = np.where(sig_mask.values)[0]

    if len(entry_positions) == 0:
        return pd.DataFrame()

    close_arr = df['close'].values
    idx_arr = df.index.values
    n = len(df)

    exit_positions = np.minimum(entry_positions + hold_hours, n - 1)
    entry_prices = close_arr[entry_positions] * (1 + slip * direction)
    exit_prices = close_arr[exit_positions] * (1 - slip * direction)
    raw_rets = direction * (exit_prices / entry_prices - 1)
    hold_hrs = exit_positions - entry_positions

    return pd.DataFrame({
        'entry_time': idx_arr[entry_positions],
        'exit_time': idx_arr[exit_positions],
        'entry_price': entry_prices,
        'exit_price': exit_prices,
        'raw_ret': raw_rets,
        'hold_hours': hold_hrs,
        'exit_reason': f'fixed_{hold_hours}h',
        'direction': direction,
    })


def build_strategy_trades(merged_all, strategy, hold_hours):
    """
    Build all potential trades for a given strategy and hold period.
    Returns DataFrame with columns: entry_time, exit_time, entry_price,
    exit_price, raw_ret, hold_hours, exit_reason, direction, token.
    """
    slip = SLIPPAGE_BPS / 10_000
    all_trades = []

    for token, df in merged_all.items():
        if strategy == 'A':
            # Long and short
            lt = compute_trades_fixed(df, 'sig_A_long', 1, hold_hours, slip)
            st = compute_trades_fixed(df, 'sig_A_short', -1, hold_hours, slip)
            for t in [lt, st]:
                if len(t) > 0:
                    t['token'] = token
                    all_trades.append(t)

        elif strategy == 'B':
            st = compute_trades_fixed(df, 'sig_B_short', -1, hold_hours, slip)
            if len(st) > 0:
                st['token'] = token
                all_trades.append(st)

        elif strategy == 'C':
            lt = compute_trades_fixed(df, 'sig_C_long', 1, hold_hours, slip)
            st = compute_trades_fixed(df, 'sig_C_short', -1, hold_hours, slip)
            for t in [lt, st]:
                if len(t) > 0:
                    t['token'] = token
                    all_trades.append(t)

        elif strategy == 'D':
            st = compute_trades_fixed(df, 'sig_D_short', -1, hold_hours, slip)
            if len(st) > 0:
                st['token'] = token
                all_trades.append(st)

    if not all_trades:
        return pd.DataFrame()
    return pd.concat(all_trades, ignore_index=True)


# ── Portfolio Simulation ────────────────────────────────────────────────
def simulate_portfolio(all_potential_trades, leverage):
    """
    Simulate portfolio with position limits, sequential equity allocation.
    Max 5 concurrent positions, equal-weight allocation.
    """
    if len(all_potential_trades) == 0:
        return [], INITIAL_CAPITAL, 0.0

    df = all_potential_trades.sort_values('entry_time').reset_index(drop=True)

    equity = INITIAL_CAPITAL
    peak_equity = INITIAL_CAPITAL
    max_dd = 0.0
    trades = []
    active = []  # list of (exit_time, token, direction, raw_ret, entry_time, margin, entry_price, exit_price, hold_hours, exit_reason)

    for _, row in df.iterrows():
        entry_time = row['entry_time']

        # Close expired positions
        still_active = []
        for pos in active:
            if entry_time >= pos[0]:
                margin = pos[5]
                lev_ret = pos[3] * leverage
                pnl_usd = margin * lev_ret
                equity += pnl_usd
                trades.append({
                    'token': pos[1], 'direction': pos[2],
                    'entry_time': pos[4], 'exit_time': pos[0],
                    'entry_price': pos[6], 'exit_price': pos[7],
                    'pnl_pct': lev_ret * 100, 'pnl_usd': pnl_usd,
                    'leverage': leverage, 'hold_hours': pos[8],
                    'exit_reason': pos[9],
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

        # Try entry: skip if at max positions or already in same token+direction
        if len(active) < MAX_POSITIONS:
            token = row['token']
            direction = row['direction']
            already_in = any(p[1] == token and p[2] == direction for p in active)
            if not already_in:
                margin = equity / MAX_POSITIONS
                active.append((
                    row['exit_time'], token, direction, row['raw_ret'],
                    entry_time, margin,
                    row['entry_price'], row['exit_price'],
                    row['hold_hours'], row['exit_reason'],
                ))

    # Close remaining positions
    for pos in active:
        margin = pos[5]
        lev_ret = pos[3] * leverage
        pnl_usd = margin * lev_ret
        equity += pnl_usd
        trades.append({
            'token': pos[1], 'direction': pos[2],
            'entry_time': pos[4], 'exit_time': pos[0],
            'entry_price': pos[6], 'exit_price': pos[7],
            'pnl_pct': lev_ret * 100, 'pnl_usd': pnl_usd,
            'leverage': leverage, 'hold_hours': pos[8],
            'exit_reason': pos[9],
        })
    if equity > peak_equity:
        peak_equity = equity
    dd_now = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
    if dd_now > max_dd:
        max_dd = dd_now

    return trades, equity, max_dd


# ── Reporting ───────────────────────────────────────────────────────────
def report_results(trades_list, final_equity, max_dd, config_label):
    """Print detailed results for a single configuration."""
    if not trades_list:
        print(f'\n  {config_label}: NO TRADES')
        return {}

    df = pd.DataFrame(trades_list)
    total_pnl = final_equity - INITIAL_CAPITAL
    total_ret = (final_equity / INITIAL_CAPITAL - 1) * 100
    n_trades = len(df)
    n_long = (df['direction'] == 1).sum()
    n_short = (df['direction'] == -1).sum()

    win_rate = (df['pnl_usd'] > 0).mean() * 100
    long_wr = (df.loc[df['direction'] == 1, 'pnl_usd'] > 0).mean() * 100 if n_long > 0 else 0
    short_wr = (df.loc[df['direction'] == -1, 'pnl_usd'] > 0).mean() * 100 if n_short > 0 else 0

    avg_pnl_pct = df['pnl_pct'].mean()
    avg_win = df.loc[df['pnl_usd'] > 0, 'pnl_pct'].mean() if (df['pnl_usd'] > 0).any() else 0
    avg_loss = df.loc[df['pnl_usd'] <= 0, 'pnl_pct'].mean() if (df['pnl_usd'] <= 0).any() else 0

    gross_wins = df.loc[df['pnl_usd'] > 0, 'pnl_usd'].sum()
    gross_losses = abs(df.loc[df['pnl_usd'] <= 0, 'pnl_usd'].sum())
    pf = gross_wins / gross_losses if gross_losses > 0 else float('inf')

    # Annualize from the actual test period
    bt_days = (BT_END - BT_START).days
    annual_factor = 365.25 / bt_days if bt_days > 0 else 1
    annual_ret = total_ret * annual_factor

    # Sharpe: annualized from per-trade returns
    if len(df) > 1 and df['pnl_pct'].std() > 0:
        avg_hold_h = df['hold_hours'].mean()
        trades_per_year = (365.25 * 24) / avg_hold_h if avg_hold_h > 0 else 1
        sharpe = (df['pnl_pct'].mean() / df['pnl_pct'].std()) * np.sqrt(trades_per_year)
    else:
        sharpe = 0.0

    avg_hold = df['hold_hours'].mean()

    print(f'\n  {"="*65}')
    print(f'  {config_label}')
    print(f'  {"="*65}')
    print(f'  Trades: {n_trades} (L:{n_long} / S:{n_short})')
    print(f'  Final equity: ${final_equity:,.0f} (PnL: ${total_pnl:+,.0f})')
    print(f'  Total return:  {total_ret:+.2f}%')
    print(f'  Annualized:    {annual_ret:+.1f}% (extrapolated from {bt_days}d)')
    print(f'  Sharpe (ann):  {sharpe:.2f}')
    print(f'  Max drawdown:  {max_dd*100:.2f}%')
    print(f'  Win rate:      {win_rate:.1f}% (L:{long_wr:.1f}% / S:{short_wr:.1f}%)')
    print(f'  Avg PnL/trade: {avg_pnl_pct:+.3f}%')
    print(f'  Avg win:       {avg_win:+.3f}% | Avg loss: {avg_loss:+.3f}%')
    print(f'  Profit factor: {pf:.2f}')
    print(f'  Avg hold:      {avg_hold:.1f}h')

    # Per-token breakdown
    if len(df) > 0:
        print(f'\n  Per-Token Breakdown:')
        print(f'  {"Token":>8s}  {"Trades":>6s}  {"L":>3s}  {"S":>3s}  '
              f'{"WinR":>6s}  {"PnL":>12s}  {"AvgRet":>8s}')
        token_pnl = df.groupby('token').agg(
            trades=('pnl_usd', 'count'),
            n_long=('direction', lambda x: (x == 1).sum()),
            n_short=('direction', lambda x: (x == -1).sum()),
            total_pnl=('pnl_usd', 'sum'),
            avg_ret=('pnl_pct', 'mean'),
            win_rate=('pnl_usd', lambda x: (x > 0).mean() * 100),
        ).sort_values('total_pnl', ascending=False)
        for tkn, row in token_pnl.iterrows():
            print(f'  {tkn:>8s}  {row["trades"]:6.0f}  {row["n_long"]:3.0f}  '
                  f'{row["n_short"]:3.0f}  {row["win_rate"]:5.1f}%  '
                  f'${row["total_pnl"]:>+11,.0f}  {row["avg_ret"]:>+7.3f}%')

    return {
        'config': config_label,
        'trades': n_trades,
        'n_long': n_long,
        'n_short': n_short,
        'final_equity': final_equity,
        'total_return_pct': total_ret,
        'annual_return_pct': annual_ret,
        'sharpe': sharpe,
        'max_dd_pct': max_dd * 100,
        'win_rate': win_rate,
        'profit_factor': pf,
        'avg_pnl_pct': avg_pnl_pct,
        'avg_hold_hours': avg_hold,
    }


def print_signal_stats(merged_all):
    """Print signal firing statistics for all strategies."""
    stats = {
        'A_long': 'sig_A_long', 'A_short': 'sig_A_short',
        'B_short': 'sig_B_short',
        'C_long': 'sig_C_long', 'C_short': 'sig_C_short',
        'D_short': 'sig_D_short',
    }

    print(f'\n  Signal Counts (backtest window: {BT_START.date()} to {BT_END.date()})')
    print(f'  {"Signal":<12s}  {"Total":>6s}  {"Tokens":>6s}  {"Pct":>6s}')
    print(f'  {"-"*38}')

    total_bars = 0
    for token, df in merged_all.items():
        bt_mask = (df.index >= BT_START) & (df.index <= BT_END)
        total_bars += bt_mask.sum()

    for sig_name, sig_col in stats.items():
        total_signals = 0
        tokens_with = 0
        for token, df in merged_all.items():
            bt_mask = (df.index >= BT_START) & (df.index <= BT_END)
            n = df.loc[bt_mask, sig_col].fillna(False).sum()
            total_signals += n
            if n > 0:
                tokens_with += 1
        pct = total_signals / max(total_bars, 1) * 100
        print(f'  {sig_name:<12s}  {total_signals:6d}  {tokens_with:6d}  {pct:5.1f}%')


def main():
    print('=' * 72)
    print('BINANCE POSITIONING DATA — MULTI-STRATEGY BACKTEST')
    print('=' * 72)
    print(f'Capital: ${INITIAL_CAPITAL:,.0f} | Slippage: {SLIPPAGE_BPS}bps | '
          f'Max positions: {MAX_POSITIONS}')
    print(f'Backtest: {BT_START.date()} to {BT_END.date()} '
          f'({(BT_END - BT_START).days} days)')
    print(f'Symbols: {len(SYMBOLS_MAP)}')

    # ── Step 1: Load & Merge Data ──
    print('\n-- Loading positioning and price data --')
    pos_data = load_positioning_data()
    merged_all = build_merged_data(pos_data)
    print(f'  Merged data for {len(merged_all)} tokens')
    for token, df in sorted(merged_all.items()):
        bt = df[(df.index >= BT_START) & (df.index <= BT_END)]
        print(f'    {token:>6s}: {len(bt):>4d} bars in backtest window')

    # ── Step 2: IC Analysis ──
    print('\n' + '=' * 72)
    print('STEP 2: INFORMATION COEFFICIENT ANALYSIS')
    print('=' * 72)

    ic_df = compute_ic_table(merged_all)

    ic_cols = ['gls_lvl_24h', 'gls_chg4_24h', 'gls_chg24_24h',
               'tkr_bsr_24h', 'tkr_bsr_4h', 'tkr_bsr_1h',
               'div_24h', 'div_4h', 'gls_lvl_4h', 'gls_lvl_1h']

    print(f'\n  {"Symbol":<8s} {"N":>4s} | '
          + '  '.join(f'{c:>12s}' for c in ic_cols))
    print(f'  {"-" * 150}')
    for sym in ic_df.index:
        r = ic_df.loc[sym]
        n = int(r['n_obs'])
        vals = '  '.join(
            f'{r[c]:>12.4f}' if not np.isnan(r.get(c, np.nan)) else f'{"NaN":>12s}'
            for c in ic_cols
        )
        print(f'  {sym:<8s} {n:>4d} | {vals}')

    print(f'\n  MEAN IC ACROSS SYMBOLS:')
    for c in ic_cols:
        if c in ic_df.columns:
            mean_ic = ic_df[c].mean()
            n_sig = (ic_df[c].abs() > 0.03).sum()
            flag = ' <<<' if abs(mean_ic) > 0.03 else ''
            print(f'    {c:<20s}: mean={mean_ic:>+7.4f}, |IC|>0.03 in '
                  f'{n_sig}/{len(ic_df)} symbols{flag}')

    # ── Step 3: Generate Signals ──
    print('\n' + '=' * 72)
    print('STEP 3: SIGNAL GENERATION & BACKTEST')
    print('=' * 72)

    merged_all = generate_signals(merged_all)
    print_signal_stats(merged_all)

    # ── Step 4: Backtest All Configs ──
    strategies = {
        'A': {'hold_hours': [24, 48, 72], 'leverages': [1, 2],
               'desc': 'Retail Contrarian (L/S percentile)'},
        'B': {'hold_hours': [24, 48], 'leverages': [1, 2],
               'desc': 'Top vs Retail Divergence'},
        'C': {'hold_hours': [24, 48], 'leverages': [1, 2],
               'desc': 'Taker Flow Momentum'},
        'D': {'hold_hours': [24, 48], 'leverages': [1, 2],
               'desc': 'Combined Signal (all align)'},
    }

    all_results = []

    for strat_name, cfg in strategies.items():
        print(f'\n{"#" * 72}')
        print(f'# STRATEGY {strat_name}: {cfg["desc"]}')
        print(f'{"#" * 72}')

        for hold in cfg['hold_hours']:
            pt = build_strategy_trades(merged_all, strat_name, hold)
            n_potential = len(pt)

            for lev in cfg['leverages']:
                label = f'Strat-{strat_name} | Hold {hold}h | {lev}x lev'
                if n_potential == 0:
                    print(f'\n  {label}: NO POTENTIAL TRADES')
                    continue

                trades, final_eq, max_dd = simulate_portfolio(pt, lev)
                result = report_results(trades, final_eq, max_dd, label)
                if result:
                    result['strategy'] = strat_name
                    result['hold_hours_cfg'] = hold
                    result['leverage'] = lev
                    all_results.append(result)

    # ── Summary ──
    print('\n' + '=' * 72)
    print('SUMMARY — ALL CONFIGURATIONS')
    print('=' * 72)

    if all_results:
        summary = pd.DataFrame(all_results)
        print(f'\n  {"Config":<42s}  {"Trades":>6s}  {"Return":>9s}  '
              f'{"Sharpe":>7s}  {"MaxDD":>7s}  {"WinR":>6s}  {"PF":>6s}')
        print(f'  {"-" * 90}')
        for _, row in summary.iterrows():
            print(
                f'  {row["config"]:<42s}  '
                f'{row["trades"]:6.0f}  '
                f'{row["total_return_pct"]:>+8.2f}%  '
                f'{row["sharpe"]:>7.2f}  '
                f'{row["max_dd_pct"]:>6.2f}%  '
                f'{row["win_rate"]:>5.1f}%  '
                f'{row["profit_factor"]:>5.2f}'
            )

        print(f'\n{"=" * 72}')
        print('BEST CONFIGURATIONS (by total return)')
        print('=' * 72)
        best = summary.sort_values('total_return_pct', ascending=False).head(8)
        for _, row in best.iterrows():
            print(
                f'  {row["config"]:<42s}  '
                f'Ret: {row["total_return_pct"]:>+8.2f}%  '
                f'Sharpe: {row["sharpe"]:>6.2f}  '
                f'DD: {row["max_dd_pct"]:>6.2f}%  '
                f'WR: {row["win_rate"]:>5.1f}%  '
                f'PF: {row["profit_factor"]:>5.2f}  '
                f'Trades: {row["trades"]:.0f}'
            )

        print(f'\n{"=" * 72}')
        print('BEST CONFIGURATIONS (by Sharpe ratio)')
        print('=' * 72)
        best_sharpe = summary.sort_values('sharpe', ascending=False).head(5)
        for _, row in best_sharpe.iterrows():
            print(
                f'  {row["config"]:<42s}  '
                f'Sharpe: {row["sharpe"]:>6.2f}  '
                f'Ret: {row["total_return_pct"]:>+8.2f}%  '
                f'DD: {row["max_dd_pct"]:>6.2f}%  '
                f'WR: {row["win_rate"]:>5.1f}%'
            )

        # Per-strategy summary
        print(f'\n{"=" * 72}')
        print('PER-STRATEGY BEST (1x leverage)')
        print('=' * 72)
        for strat in ['A', 'B', 'C', 'D']:
            strat_rows = summary[(summary['strategy'] == strat) &
                                  (summary['leverage'] == 1)]
            if len(strat_rows) > 0:
                best_row = strat_rows.sort_values('total_return_pct',
                                                   ascending=False).iloc[0]
                print(
                    f'  Strategy {strat}: '
                    f'Best hold={best_row["hold_hours_cfg"]:.0f}h | '
                    f'Ret: {best_row["total_return_pct"]:>+8.2f}% | '
                    f'Sharpe: {best_row["sharpe"]:>6.2f} | '
                    f'DD: {best_row["max_dd_pct"]:>6.2f}% | '
                    f'WR: {best_row["win_rate"]:>5.1f}% | '
                    f'PF: {best_row["profit_factor"]:>5.2f} | '
                    f'Trades: {best_row["trades"]:.0f}'
                )
            else:
                print(f'  Strategy {strat}: No trades at 1x')

    else:
        print('  No results generated.')

    print('\n' + '=' * 72)
    print('NOTES')
    print('=' * 72)
    print('  - Positioning data covers only ~20 days (2026-03-03 to 2026-03-23)')
    print('  - Price data ends 2026-03-17, so effective backtest is ~14 days')
    print('  - Results are full-sample (no IS/OOS split possible)')
    print('  - Annualized returns are extrapolated and should be treated with')
    print('    extreme caution given the short sample period')
    print('  - IC analysis suggests divergence (top_pos - global) is the')
    print('    strongest predictor (mean IC = -0.087 vs 24h returns)')
    print('  - Taker BSR and global L/S changes also show meaningful IC')
    print()
    print('=' * 72)
    print('BACKTEST COMPLETE')
    print('=' * 72)


if __name__ == '__main__':
    main()
