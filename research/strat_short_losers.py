#!/workspace/venv/bin/python
"""
Short Losers Strategy -- Cross-Sectional Loser Identification Backtest
=====================================================================

Signal: From Experiment E (cross-sectional ML), the model identifies tokens
likely to be bottom-quintile performers. Key features:
  - vol_24h_rank (dominant -- high vol rank = likely loser)
  - ret_24h_rank < 0.2 (already underperforming)
  - drawdown_rank > 0.8 (deep in drawdown)
  - rsi_rank < 0.3 (oversold relative to peers)

Entry Rules:
  A: ret_24h_rank < 0.15 AND vol_24h_rank > 0.8
  B: ret_24h_rank < 0.2 AND drawdown_rank > 0.7 AND rsi_rank < 0.3
  C: Composite score > 0.7

Exit Configs:
  A: Fixed 24h hold
  B: 2x ATR trailing stop, 48h max
  C: Exit when ret_24h_rank > 0.5 (relative recovery)
  D: 3x ATR stop, exit if funding < -0.0003 (crowded short)

OOS: 2025-07-01 to 2026-03-17
Capital: $200K, slippage 10bps, max 5 positions
Leverage: 1x, 3x, 5x
"""

import os
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# -- Config ---------------------------------------------------------------
PROJECT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
CACHE_DIR = os.path.join(PROJECT_DIR, 'data', 'perp', '1h_cache')

OOS_START = pd.Timestamp('2025-07-01')
OOS_END = pd.Timestamp('2026-03-17')

INITIAL_CAPITAL = 200_000.0
SLIPPAGE_BPS = 10
MAX_POSITIONS = 5
LEVERAGE_LEVELS = [1, 3, 5]
TOP_N = 30
RSI_PERIOD = 14
ATR_PERIOD = 14
LOOKBACK_24H = 24
DRAWDOWN_LOOKBACK = 20


# -- Data Loading (wide format) -------------------------------------------
def load_wide_data():
    """Load data into wide-format DataFrames (timestamp x token) for each field."""
    print('Loading data...')
    all_files = sorted(os.listdir(CACHE_DIR))
    candidates = []

    for f in all_files:
        if not f.endswith('_1h.parquet'):
            continue
        sym = f.replace('_1h.parquet', '')
        path = os.path.join(CACHE_DIR, f)
        df = pd.read_parquet(path)
        if df.index.min() <= OOS_START and df.index.max() >= OOS_END - pd.Timedelta(days=2):
            candidates.append((sym, len(df), path))

    candidates.sort(key=lambda x: -x[1])
    selected = candidates[:TOP_N]
    tokens = [s[0] for s in selected]

    print(f'  Selected {len(tokens)} tokens')

    # Build wide dataframes
    close_dict, high_dict, low_dict, volume_dict, funding_dict = {}, {}, {}, {}, {}

    for sym, nrows, path in selected:
        df = pd.read_parquet(path).sort_index()
        df = df[~df.index.duplicated(keep='first')]
        close_dict[sym] = df['close']
        high_dict[sym] = df['high']
        low_dict[sym] = df['low']
        volume_dict[sym] = df['volume']
        funding_dict[sym] = df['funding_rate']
        print(f'  {sym:10s} {nrows:6d} rows  {df.index.min().date()} to {df.index.max().date()}')

    close_w = pd.DataFrame(close_dict)
    high_w = pd.DataFrame(high_dict)
    low_w = pd.DataFrame(low_dict)
    volume_w = pd.DataFrame(volume_dict)
    funding_w = pd.DataFrame(funding_dict)

    print(f'  Wide shape: {close_w.shape}  ({close_w.index.min().date()} to {close_w.index.max().date()})')
    return tokens, close_w, high_w, low_w, volume_w, funding_w


# -- Feature computation (wide format, vectorized) ------------------------
def compute_rsi_wide(close_w, period=14):
    """RSI computed per-column vectorized."""
    delta = close_w.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0/period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def compute_atr_wide(high_w, low_w, close_w, period=14):
    """ATR computed per-column vectorized."""
    prev_close = close_w.shift(1)
    tr1 = high_w - low_w
    tr2 = (high_w - prev_close).abs()
    tr3 = (low_w - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], keys=['a', 'b', 'c']).groupby(level=1).max()
    # Above approach is wrong for wide; do element-wise max
    tr = tr1.copy()
    tr = tr.where(tr > tr2, tr2)
    tr = tr.where(tr > tr3, tr3)
    return tr.rolling(period, min_periods=period).mean()


def compute_all_features(close_w, high_w, low_w, volume_w, funding_w):
    """Compute all features as wide DataFrames + cross-sectional ranks."""
    print('\nComputing features (vectorized)...')

    # 24h return
    ret_24h = close_w.pct_change(LOOKBACK_24H)

    # 24h volatility
    hourly_ret = close_w.pct_change(1)
    vol_24h = hourly_ret.rolling(LOOKBACK_24H, min_periods=12).std()

    # RSI
    rsi = compute_rsi_wide(close_w, RSI_PERIOD)

    # Drawdown from 20h high
    rolling_high = high_w.rolling(DRAWDOWN_LOOKBACK, min_periods=10).max()
    drawdown = (rolling_high - close_w) / rolling_high

    # ATR
    atr = compute_atr_wide(high_w, low_w, close_w, ATR_PERIOD)

    print('  Computing cross-sectional ranks...')

    # Cross-sectional ranks: rank across columns for each row
    ret_24h_rank = ret_24h.rank(axis=1, pct=True, method='average')
    vol_24h_rank = vol_24h.rank(axis=1, pct=True, method='average')
    rsi_rank = rsi.rank(axis=1, pct=True, method='average')
    drawdown_rank = drawdown.rank(axis=1, pct=True, method='average')

    # Composite score
    composite = (
        0.4 * vol_24h_rank +
        0.3 * (1 - ret_24h_rank) +
        0.2 * drawdown_rank +
        0.1 * (1 - rsi_rank)
    )

    # Signals (wide boolean DataFrames)
    signal_A = (ret_24h_rank < 0.15) & (vol_24h_rank > 0.8)
    signal_B = (ret_24h_rank < 0.2) & (drawdown_rank > 0.7) & (rsi_rank < 0.3)
    signal_C = composite > 0.7
    signal_any = signal_A | signal_B | signal_C

    # Count valid tokens per timestamp (for filtering sparse timestamps)
    n_valid = close_w.notna().sum(axis=1)
    sparse_mask = n_valid < 5
    for sig in [signal_A, signal_B, signal_C, signal_any]:
        sig.loc[sparse_mask] = False

    # OOS counts
    oos_mask = close_w.index >= OOS_START
    for name, sig in [('A', signal_A), ('B', signal_B), ('C', signal_C), ('any', signal_any)]:
        n = sig.loc[oos_mask].sum().sum()
        print(f'  signal_{name}: {int(n)} signals OOS')

    features = {
        'close': close_w,
        'high': high_w,
        'low': low_w,
        'funding': funding_w,
        'ret_24h_rank': ret_24h_rank,
        'vol_24h_rank': vol_24h_rank,
        'rsi_rank': rsi_rank,
        'drawdown_rank': drawdown_rank,
        'composite': composite,
        'atr': atr,
        'signal_A': signal_A,
        'signal_B': signal_B,
        'signal_C': signal_C,
        'signal_any': signal_any,
    }
    return features


# -- Backtest Engine -------------------------------------------------------
class Position:
    __slots__ = ['token', 'entry_time', 'entry_price', 'size_usd',
                 'leverage', 'trailing_stop', 'bars_held',
                 'entry_atr', 'funding_paid', 'exit_config']

    def __init__(self, token, entry_time, entry_price, size_usd, leverage,
                 entry_atr, exit_config):
        self.token = token
        self.entry_time = entry_time
        self.entry_price = entry_price
        self.size_usd = size_usd
        self.leverage = leverage
        self.trailing_stop = None
        self.bars_held = 0
        self.entry_atr = entry_atr
        self.funding_paid = 0.0
        self.exit_config = exit_config

    def notional(self):
        return self.size_usd * self.leverage


def run_backtest(features, entry_rule, exit_config, leverage):
    """Run a single backtest using wide-format feature data."""
    signal_key = f'signal_{entry_rule}'
    signal_w = features[signal_key]
    close_w = features['close']
    high_w = features['high']
    funding_w = features['funding']
    atr_w = features['atr']
    composite_w = features['composite']
    ret_rank_w = features['ret_24h_rank']

    # OOS timestamps only
    oos_idx = close_w.index[close_w.index >= OOS_START]
    tokens = close_w.columns.tolist()

    capital = INITIAL_CAPITAL
    positions = []
    equity_curve = []
    trades = []
    peak_equity = INITIAL_CAPITAL
    max_dd = 0.0
    slippage_rate = SLIPPAGE_BPS / 10000.0

    for ts in oos_idx:
        # -- Check exits --
        closed = []
        for pos in positions:
            tok = pos.token
            try:
                cur_close = close_w.at[ts, tok]
                cur_high = high_w.at[ts, tok]
                cur_funding = funding_w.at[ts, tok]
            except (KeyError, ValueError):
                continue

            if np.isnan(cur_close):
                continue

            pos.bars_held += 1

            # Funding: short pays when funding negative, receives when positive
            f_cost = -cur_funding * pos.notional() if not np.isnan(cur_funding) else 0.0
            pos.funding_paid += f_cost
            capital += f_cost

            should_exit = False
            exit_reason = ''
            exit_price_raw = cur_close

            if exit_config == 'A':
                if pos.bars_held >= 24:
                    should_exit = True
                    exit_reason = '24h_hold'

            elif exit_config == 'B':
                a = pos.entry_atr
                if pos.trailing_stop is None:
                    pos.trailing_stop = pos.entry_price + 2 * a
                else:
                    new_stop = cur_close + 2 * a
                    if new_stop < pos.trailing_stop:
                        pos.trailing_stop = new_stop
                if cur_high >= pos.trailing_stop:
                    should_exit = True
                    exit_reason = 'atr_trail_stop'
                    exit_price_raw = pos.trailing_stop
                elif pos.bars_held >= 48:
                    should_exit = True
                    exit_reason = '48h_max'

            elif exit_config == 'C':
                if pos.bars_held >= 2:
                    try:
                        rr = ret_rank_w.at[ts, tok]
                    except (KeyError, ValueError):
                        rr = 0.5
                    if not np.isnan(rr) and rr > 0.5:
                        should_exit = True
                        exit_reason = 'rank_recovery'
                if pos.bars_held >= 72:
                    should_exit = True
                    exit_reason = '72h_max'

            elif exit_config == 'D':
                a = pos.entry_atr
                stop_px = pos.entry_price + 3 * a
                if cur_high >= stop_px:
                    should_exit = True
                    exit_reason = 'atr_stop'
                    exit_price_raw = stop_px
                elif not np.isnan(cur_funding) and cur_funding < -0.0003:
                    should_exit = True
                    exit_reason = 'crowded_short'
                elif pos.bars_held >= 48:
                    should_exit = True
                    exit_reason = '48h_max'

            if should_exit:
                exit_px = exit_price_raw * (1 + slippage_rate)  # buy back (adverse)
                pnl = pos.size_usd * leverage * (pos.entry_price - exit_px) / pos.entry_price
                capital += pnl + pos.size_usd
                trades.append({
                    'token': tok,
                    'entry_time': pos.entry_time,
                    'exit_time': ts,
                    'entry_price': pos.entry_price,
                    'exit_price': exit_px,
                    'bars_held': pos.bars_held,
                    'pnl': pnl,
                    'funding': pos.funding_paid,
                    'exit_reason': exit_reason,
                    'return_pct': pnl / pos.size_usd * 100,
                })
                closed.append(pos)

        for p in closed:
            positions.remove(p)

        # -- Check entries --
        if len(positions) < MAX_POSITIONS:
            held = {p.token for p in positions}
            # Get signal row for this timestamp
            try:
                sig_row = signal_w.loc[ts]
            except KeyError:
                sig_row = pd.Series(False, index=tokens)

            cands = []
            for tok in tokens:
                if tok in held:
                    continue
                try:
                    s = sig_row[tok]
                except (KeyError, ValueError):
                    continue
                if s is True or s == True:
                    try:
                        px = close_w.at[ts, tok]
                        a = atr_w.at[ts, tok]
                        comp = composite_w.at[ts, tok]
                    except (KeyError, ValueError):
                        continue
                    if np.isnan(px):
                        continue
                    if np.isnan(a):
                        a = px * 0.02
                    comp_val = comp if not np.isnan(comp) else 0.0
                    cands.append((tok, px, a, comp_val))

            # Sort by composite (strongest short signal first)
            cands.sort(key=lambda x: -x[3])
            slots = MAX_POSITIONS - len(positions)

            for tok, px, a, _ in cands[:slots]:
                size_usd = min(capital * 0.2, capital)
                if size_usd < 100:
                    continue
                entry_px = px * (1 - slippage_rate)
                capital -= size_usd

                pos = Position(
                    token=tok, entry_time=ts, entry_price=entry_px,
                    size_usd=size_usd, leverage=leverage,
                    entry_atr=a, exit_config=exit_config,
                )
                positions.append(pos)

        # -- Equity snapshot --
        unrealized = 0.0
        locked = 0.0
        for p in positions:
            locked += p.size_usd
            try:
                cpx = close_w.at[ts, p.token]
            except (KeyError, ValueError):
                continue
            if not np.isnan(cpx):
                ret = (p.entry_price - cpx) / p.entry_price
                unrealized += p.size_usd * ret * p.leverage

        total_eq = capital + locked + unrealized
        if total_eq > peak_equity:
            peak_equity = total_eq
        dd = (peak_equity - total_eq) / peak_equity if peak_equity > 0 else 0
        if dd > max_dd:
            max_dd = dd

        equity_curve.append((ts, total_eq, len(positions), dd))

        if total_eq < INITIAL_CAPITAL * 0.05:
            print(f'    LIQUIDATED at {ts}')
            break

    # Close remaining
    if positions and len(oos_idx) > 0:
        last_ts = oos_idx[-1]
        for pos in positions:
            try:
                cpx = close_w.at[last_ts, pos.token]
            except (KeyError, ValueError):
                continue
            if np.isnan(cpx):
                continue
            exit_px = cpx * (1 + slippage_rate)
            pnl = pos.size_usd * leverage * (pos.entry_price - exit_px) / pos.entry_price
            capital += pnl + pos.size_usd
            trades.append({
                'token': pos.token, 'entry_time': pos.entry_time,
                'exit_time': last_ts, 'entry_price': pos.entry_price,
                'exit_price': exit_px, 'bars_held': pos.bars_held,
                'pnl': pnl, 'funding': pos.funding_paid,
                'exit_reason': 'end_of_period',
                'return_pct': pnl / pos.size_usd * 100,
            })

    # Build results
    eq_df = pd.DataFrame(equity_curve, columns=['timestamp', 'equity', 'n_pos', 'drawdown'])
    trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()

    if len(eq_df) == 0:
        return {'error': 'No equity data'}

    final_eq = eq_df['equity'].iloc[-1]
    total_ret = (final_eq - INITIAL_CAPITAL) / INITIAL_CAPITAL
    n_days = (eq_df['timestamp'].iloc[-1] - eq_df['timestamp'].iloc[0]).days
    ann_ret = (1 + total_ret) ** (365.0 / max(n_days, 1)) - 1 if n_days > 0 else 0

    if len(eq_df) > 1:
        rets = eq_df['equity'].pct_change().dropna()
        sharpe = (rets.mean() / rets.std()) * np.sqrt(8760) if rets.std() > 0 else 0
    else:
        sharpe = 0

    result = {
        'entry_rule': entry_rule, 'exit_config': exit_config, 'leverage': leverage,
        'final_equity': final_eq, 'total_return_pct': total_ret * 100,
        'annual_return_pct': ann_ret * 100, 'max_drawdown_pct': max_dd * 100,
        'sharpe': sharpe, 'n_trades': len(trades_df), 'n_days': n_days,
        'equity_curve': eq_df, 'trades': trades_df,
    }

    if len(trades_df) > 0:
        result['avg_pnl'] = trades_df['pnl'].mean()
        result['win_rate'] = (trades_df['pnl'] > 0).mean() * 100
        result['avg_hold_hours'] = trades_df['bars_held'].mean()
        result['total_funding'] = trades_df['funding'].sum()
        result['avg_return_pct'] = trades_df['return_pct'].mean()
        losers = trades_df.loc[trades_df['pnl'] < 0, 'pnl'].sum()
        winners = trades_df.loc[trades_df['pnl'] > 0, 'pnl'].sum()
        result['profit_factor'] = winners / abs(losers) if losers != 0 else float('inf')
    else:
        for k in ['avg_pnl', 'win_rate', 'avg_hold_hours', 'total_funding',
                   'avg_return_pct', 'profit_factor']:
            result[k] = 0

    return result


# -- Reporting -------------------------------------------------------------
def monthly_breakdown(result):
    trades_df = result.get('trades', pd.DataFrame())
    if len(trades_df) == 0:
        return pd.DataFrame()
    t = trades_df.copy()
    t['month'] = pd.to_datetime(t['exit_time']).dt.to_period('M')
    rows = []
    for month, mdf in t.groupby('month'):
        rows.append({
            'month': str(month), 'n_trades': len(mdf),
            'total_pnl': mdf['pnl'].sum(), 'avg_pnl': mdf['pnl'].mean(),
            'win_rate': (mdf['pnl'] > 0).mean() * 100,
            'total_funding': mdf['funding'].sum(),
            'avg_hold_h': mdf['bars_held'].mean(),
        })
    return pd.DataFrame(rows)


def per_token_breakdown(result):
    trades_df = result.get('trades', pd.DataFrame())
    if len(trades_df) == 0:
        return pd.DataFrame()
    rows = []
    for token, tdf in trades_df.groupby('token'):
        rows.append({
            'token': token, 'n_trades': len(tdf),
            'total_pnl': tdf['pnl'].sum(), 'avg_pnl': tdf['pnl'].mean(),
            'win_rate': (tdf['pnl'] > 0).mean() * 100,
        })
    return pd.DataFrame(rows).sort_values('total_pnl', ascending=False)


def print_result(r, verbose=False):
    print(f'\n  Entry={r["entry_rule"]:>3s}  Exit={r["exit_config"]}  Lev={r["leverage"]}x  '
          f'| Ret: {r["total_return_pct"]:>+8.1f}%  Ann: {r["annual_return_pct"]:>+8.1f}%  '
          f'DD: {r["max_drawdown_pct"]:>6.1f}%  Sharpe: {r["sharpe"]:>5.2f}  '
          f'Trades: {r["n_trades"]:>4d}  WR: {r["win_rate"]:>5.1f}%  '
          f'PF: {r["profit_factor"]:>5.2f}  Hold: {r["avg_hold_hours"]:>4.1f}h  '
          f'Fund: ${r["total_funding"]:>+,.0f}')

    if verbose:
        mo = monthly_breakdown(r)
        if len(mo) > 0:
            print(f'\n    {"Month":>10s}  {"#":>4s}  {"PnL":>12s}  {"AvgPnL":>10s}  '
                  f'{"WR":>6s}  {"Funding":>10s}  {"Hold":>6s}')
            for _, row in mo.iterrows():
                print(f'    {row["month"]:>10s}  {row["n_trades"]:>4d}  '
                      f'${row["total_pnl"]:>+11,.0f}  ${row["avg_pnl"]:>+9,.0f}  '
                      f'{row["win_rate"]:>5.1f}%  ${row["total_funding"]:>+9,.0f}  '
                      f'{row["avg_hold_h"]:>5.1f}h')

        tk = per_token_breakdown(r)
        if len(tk) > 0:
            print(f'\n    {"Token":>10s}  {"#":>4s}  {"TotalPnL":>12s}  '
                  f'{"AvgPnL":>10s}  {"WR":>6s}')
            for _, row in tk.iterrows():
                print(f'    {row["token"]:>10s}  {row["n_trades"]:>4d}  '
                      f'${row["total_pnl"]:>+11,.0f}  ${row["avg_pnl"]:>+9,.0f}  '
                      f'{row["win_rate"]:>5.1f}%')


def equity_monthly_returns(r):
    eq = r.get('equity_curve', pd.DataFrame())
    if len(eq) == 0:
        return pd.DataFrame()
    eq = eq.copy()
    eq['month'] = pd.to_datetime(eq['timestamp']).dt.to_period('M')
    m = eq.groupby('month')['equity'].agg(['first', 'last'])
    m['return_pct'] = (m['last'] / m['first'] - 1) * 100
    return m[['return_pct']]


# -- Main ------------------------------------------------------------------
def main():
    print('=' * 80)
    print('SHORT LOSERS STRATEGY -- CROSS-SECTIONAL LOSER IDENTIFICATION')
    print('=' * 80)
    print(f'OOS: {OOS_START.date()} to {OOS_END.date()}')
    print(f'Capital: ${INITIAL_CAPITAL:,.0f}  Slip: {SLIPPAGE_BPS}bps  MaxPos: {MAX_POSITIONS}')

    tokens, close_w, high_w, low_w, volume_w, funding_w = load_wide_data()
    features = compute_all_features(close_w, high_w, low_w, volume_w, funding_w)

    entry_rules = ['A', 'B', 'C', 'any']
    exit_configs = ['A', 'B', 'C', 'D']
    all_results = []
    best_result = None
    best_score = -np.inf

    print('\n' + '=' * 80)
    print('BACKTEST RESULTS -- ALL CONFIGURATIONS')
    print('=' * 80)

    total_configs = len(entry_rules) * len(exit_configs) * len(LEVERAGE_LEVELS)
    done = 0

    for entry in entry_rules:
        for exit_cfg in exit_configs:
            for lev in LEVERAGE_LEVELS:
                done += 1
                result = run_backtest(features, entry, exit_cfg, lev)
                if 'error' in result:
                    print(f'  [{done}/{total_configs}] Entry={entry} Exit={exit_cfg} Lev={lev}x: {result["error"]}')
                    continue
                all_results.append(result)
                print(f'  [{done}/{total_configs}]', end='')
                print_result(result)

                score = result['annual_return_pct'] / max(result['max_drawdown_pct'], 1)
                if score > best_score:
                    best_score = score
                    best_result = result

    # -- Summary table --
    print('\n' + '=' * 80)
    print('SUMMARY TABLE -- SORTED BY RETURN/DD RATIO')
    print('=' * 80)

    summary_rows = []
    for r in all_results:
        ret_dd = r['annual_return_pct'] / max(r['max_drawdown_pct'], 1)
        summary_rows.append({
            'Entry': r['entry_rule'], 'Exit': r['exit_config'], 'Lev': r['leverage'],
            'AnnRet%': r['annual_return_pct'], 'MaxDD%': r['max_drawdown_pct'],
            'Ret/DD': ret_dd, 'Sharpe': r['sharpe'], '#Trades': r['n_trades'],
            'WinR%': r['win_rate'], 'PF': r['profit_factor'],
            'AvgHold': r['avg_hold_hours'], 'Fund$': r['total_funding'],
        })

    summary_df = pd.DataFrame(summary_rows).sort_values('Ret/DD', ascending=False)
    pd.set_option('display.max_rows', 100)
    pd.set_option('display.width', 220)
    pd.set_option('display.float_format', lambda x: f'{x:.2f}')
    print(summary_df.to_string(index=False))

    # -- Best config detail --
    if best_result is not None:
        print('\n' + '=' * 80)
        print(f'BEST CONFIG: Entry={best_result["entry_rule"]} '
              f'Exit={best_result["exit_config"]} Lev={best_result["leverage"]}x')
        print('=' * 80)
        for k, fmt in [('final_equity', '${:>12,.0f}'), ('total_return_pct', '{:>+10.1f}%'),
                        ('annual_return_pct', '{:>+10.1f}%'), ('max_drawdown_pct', '{:>10.1f}%'),
                        ('sharpe', '{:>10.2f}'), ('n_trades', '{:>10d}'),
                        ('win_rate', '{:>10.1f}%'), ('profit_factor', '{:>10.2f}'),
                        ('avg_hold_hours', '{:>10.1f}h'), ('total_funding', '${:>+10,.0f}')]:
            label = k.replace('_', ' ').title()
            print(f'  {label:20s} {fmt.format(best_result[k])}')

        print_result(best_result, verbose=True)

        meq = equity_monthly_returns(best_result)
        if len(meq) > 0:
            print(f'\n  Monthly Equity Returns:')
            print(f'    {"Month":>10s}  {"Return":>8s}')
            for month, row in meq.iterrows():
                print(f'    {str(month):>10s}  {row["return_pct"]:>+7.2f}%')

    # -- Top 5 per leverage --
    for lev in LEVERAGE_LEVELS:
        lev_df = summary_df[summary_df['Lev'] == lev].head(5)
        print(f'\n  Top 5 at {lev}x leverage:')
        print(lev_df.to_string(index=False))

    # -- Target check --
    print('\n' + '=' * 80)
    print('TARGET CHECK: 300%+ Annual, <20% DD')
    print('=' * 80)
    hits = summary_df[(summary_df['AnnRet%'] >= 300) & (summary_df['MaxDD%'] <= 20)]
    if len(hits) > 0:
        print(f'  {len(hits)} configs meet target:')
        print(hits.to_string(index=False))
    else:
        print('  No configs meet both targets simultaneously.')
        cr = summary_df[summary_df['AnnRet%'] >= 100].head(5)
        if len(cr) > 0:
            print(f'\n  Closest by return (>100% annual):')
            print(cr.to_string(index=False))
        ld = summary_df[summary_df['MaxDD%'] <= 30].sort_values('AnnRet%', ascending=False).head(5)
        if len(ld) > 0:
            print(f'\n  Closest by drawdown (<30% DD):')
            print(ld.to_string(index=False))

    # -- Exit reason distribution --
    if best_result and len(best_result.get('trades', pd.DataFrame())) > 0:
        print('\n  Exit Reason Distribution (best config):')
        for reason, count in best_result['trades']['exit_reason'].value_counts().items():
            pnl_r = best_result['trades'].loc[best_result['trades']['exit_reason'] == reason, 'pnl']
            print(f'    {reason:20s}: {count:>4d}  '
                  f'avg=${pnl_r.mean():>+,.0f}  total=${pnl_r.sum():>+,.0f}')

    print('\n' + '=' * 80)
    print('DONE')
    print('=' * 80)


if __name__ == '__main__':
    main()
