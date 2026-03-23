#!/usr/bin/env python3
"""
OI-Enhanced SHORT Strategy — Three Variants
=============================================
Based on R8 finding: OI rising + price falling => further downside
(t-stat: -7.97, avg 24h fwd return: -0.343%, 12,104 OOS occurrences)

Variant 1: Pure OI Divergence Short
  - Entry: OI 24h change > +X% AND price 24h change < -Y%
  - Grid: X in [1,2,5,10], Y in [0.5,1,2,3]
  - Exit: Fixed hold (24h, 48h, 72h) OR OI declining
  - 16 entry combos x 4 exits = 64 configs

Variant 2: Cross-Sectional OI-Filtered Short
  - Rank all 14 tokens by 24h return each hour
  - Select bottom 3 (worst performers)
  - FILTER: Only short if their OI is also rising (24h OI change > 0)
  - Compare with/without OI filter
  - Exit: Fixed 24h, 48h

Variant 3: OI Z-Score Weighted Short
  - Rolling 168h z-score of OI change
  - Entry: z-score > 2.0 AND price return < 0 over last 24h
  - Position size proportional to z-score
  - Exit: z-score drops below 1.0 OR fixed 72h max hold

Capital: $200,000 | Slippage: 10bps/side | Max 5 positions | Leverage: 1x, 2x, 3x
OOS: 2025-07-01 to 2026-03-17
"""

import os
import sys
import warnings
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple

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
OOS_CUTOFF = pd.Timestamp('2025-07-01', tz='UTC')


# ── Data Loading ─────────────────────────────────────────────────────────
def load_token_data(token: str) -> Optional[pd.DataFrame]:
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

    # Load price
    price_df = pd.read_parquet(price_path)
    if price_df.index.tz is None:
        price_df.index = price_df.index.tz_localize('UTC')
    price_df = price_df[['open', 'high', 'low', 'close', 'volume']].sort_index()
    price_df = price_df[~price_df.index.duplicated(keep='first')]

    # Inner join on hourly timestamps
    merged = oi_df.join(price_df, how='inner').dropna()
    if len(merged) < 200:
        return None

    merged['token'] = token
    return merged


def load_all_data() -> pd.DataFrame:
    """Load all tokens, return concatenated DataFrame."""
    frames = []
    for token in TOKENS:
        df = load_token_data(token)
        if df is not None:
            frames.append(df)
            print(f'  [{token}] {len(df):,} rows | '
                  f'{df.index.min().strftime("%Y-%m-%d")} to {df.index.max().strftime("%Y-%m-%d")}')
    if not frames:
        print('ERROR: No data loaded!')
        sys.exit(1)
    all_data = pd.concat(frames)
    print(f'\n  Total: {len(all_data):,} rows across {len(frames)} tokens')
    return all_data


# ── Feature Engineering ──────────────────────────────────────────────────
def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute OI divergence features per token."""
    results = []
    for token, gdf in df.groupby('token'):
        g = gdf.sort_index().copy()

        # OI changes (percentage)
        g['oi_chg_1h'] = g['open_interest'].pct_change(1)
        g['oi_chg_24h'] = g['open_interest'].pct_change(24)

        # Price changes (percentage)
        g['price_chg_24h'] = g['close'].pct_change(24)

        # OI z-score: rolling 168h (7 days) window on 24h OI change
        oi_chg_24h = g['oi_chg_24h']
        roll_mean = oi_chg_24h.rolling(168, min_periods=48).mean()
        roll_std = oi_chg_24h.rolling(168, min_periods=48).std()
        g['oi_zscore'] = (oi_chg_24h - roll_mean) / roll_std.replace(0, np.nan)

        # OI declining flag (for exit): OI 4h change < 0
        g['oi_declining'] = g['open_interest'].pct_change(4) < 0

        results.append(g)

    return pd.concat(results)


# ── Position / Trade Tracking ────────────────────────────────────────────
@dataclass
class Position:
    token: str
    direction: int         # -1 for short
    entry_price: float
    entry_time: pd.Timestamp
    size_usd: float
    leverage: float
    weight: float = 1.0    # for z-score weighted sizing

    def unrealized_pnl(self, current_price: float) -> float:
        raw_ret = (current_price / self.entry_price - 1) * self.direction
        return raw_ret * self.size_usd * self.leverage

    def mark_to_market(self, current_price: float) -> float:
        return self.size_usd + self.unrealized_pnl(current_price)


@dataclass
class Trade:
    token: str
    direction: int
    entry_price: float
    exit_price: float
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    size_usd: float
    leverage: float
    pnl: float
    exit_reason: str


# ── Backtester Core ──────────────────────────────────────────────────────
def close_position(pos: Position, current_price: float, t: pd.Timestamp,
                   exit_reason: str, slip_factor: float) -> Tuple[Trade, float]:
    """Close a position and return (Trade, cash_returned)."""
    # For short: exit by buying back. Slippage means worse (higher) exit price.
    exit_price = current_price * (1 + slip_factor)  # shorts buy back at higher price
    raw_ret = (exit_price / pos.entry_price - 1) * pos.direction
    pnl = raw_ret * pos.size_usd * pos.leverage
    cash_returned = pos.size_usd + pnl
    trade = Trade(
        token=pos.token,
        direction=pos.direction,
        entry_price=pos.entry_price,
        exit_price=exit_price,
        entry_time=pos.entry_time,
        exit_time=t,
        size_usd=pos.size_usd,
        leverage=pos.leverage,
        pnl=pnl,
        exit_reason=exit_reason,
    )
    return trade, cash_returned


def run_backtest_v1(
    df: pd.DataFrame,
    oi_thresh: float,        # OI 24h change threshold (e.g., 0.02 for 2%)
    price_thresh: float,     # price 24h decline threshold (e.g., 0.01 for -1%)
    exit_mode: str,          # 'fixed_24h', 'fixed_48h', 'fixed_72h', 'oi_decline'
    leverage: float = 1.0,
    capital: float = CAPITAL,
    max_positions: int = MAX_POSITIONS,
    slippage_bps: float = SLIPPAGE_BPS,
) -> Dict:
    """Variant 1: Pure OI Divergence Short."""

    # Filter to OOS period
    df_oos = df[df.index >= OOS_CUTOFF].copy()
    if len(df_oos) == 0:
        return {'trades': [], 'equity_curve': [], 'capital': capital}

    # Generate signals: OI rising AND price falling
    df_oos['signal_short'] = (
        (df_oos['oi_chg_24h'] > oi_thresh) &
        (df_oos['price_chg_24h'] < -price_thresh)
    ).astype(int)

    # Build token-indexed data
    token_data = {}
    for token, gdf in df_oos.groupby('token'):
        token_data[token] = gdf

    all_times = df_oos.index.unique().sort_values()
    positions: List[Position] = []
    trades: List[Trade] = []
    equity_curve = []
    cash = capital
    slip_factor = slippage_bps / 10_000

    # Determine hold hours for fixed exits
    hold_hours_map = {'fixed_24h': 24, 'fixed_48h': 48, 'fixed_72h': 72, 'oi_decline': 72}
    max_hold = hold_hours_map.get(exit_mode, 24)

    for t in all_times:
        # ── Check exits ──
        new_positions = []
        for pos in positions:
            if pos.token not in token_data:
                new_positions.append(pos)
                continue
            tdf = token_data[pos.token]
            if t not in tdf.index:
                new_positions.append(pos)
                continue

            row = tdf.loc[t]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]

            current_price = row['close']
            hours_held = (t - pos.entry_time).total_seconds() / 3600
            exit_reason = None

            if exit_mode in ('fixed_24h', 'fixed_48h', 'fixed_72h'):
                if hours_held >= max_hold:
                    exit_reason = exit_mode
            elif exit_mode == 'oi_decline':
                # Exit when OI starts declining (pressure releasing)
                if hours_held >= 4 and row.get('oi_declining', False):
                    exit_reason = 'oi_decline'
                if hours_held >= max_hold:
                    exit_reason = 'max_hold_72h'

            if exit_reason:
                trade, cash_ret = close_position(pos, current_price, t, exit_reason, slip_factor)
                cash += cash_ret
                trades.append(trade)
            else:
                new_positions.append(pos)

        positions = new_positions

        # ── Check entries ──
        if len(positions) < max_positions:
            candidates = []
            for token in TOKENS:
                if token not in token_data:
                    continue
                tdf = token_data[token]
                if t not in tdf.index:
                    continue
                row = tdf.loc[t]
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[0]

                if any(p.token == token for p in positions):
                    continue

                if row.get('signal_short', 0) == 1:
                    candidates.append((token, row['close'], abs(row.get('oi_chg_24h', 0))))

            # Rank by OI change magnitude (strongest divergence first)
            candidates.sort(key=lambda x: x[2], reverse=True)

            for token, price, _ in candidates:
                if len(positions) >= max_positions:
                    break

                size_usd = min(cash * 0.95, capital / max_positions, capital * 0.25)
                if size_usd < 1000 or cash < size_usd:
                    continue

                # For short: slippage means worse (lower) entry price
                entry_price = price * (1 - slip_factor)
                cash -= size_usd
                positions.append(Position(
                    token=token, direction=-1,
                    entry_price=entry_price, entry_time=t,
                    size_usd=size_usd, leverage=leverage,
                ))

        # ── Record equity ──
        total_equity = cash
        for pos in positions:
            if pos.token in token_data and t in token_data[pos.token].index:
                row = token_data[pos.token].loc[t]
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[0]
                total_equity += pos.mark_to_market(row['close'])
            else:
                total_equity += pos.size_usd

        equity_curve.append((t, total_equity))

    # Close remaining positions
    for pos in positions:
        if pos.token in token_data:
            tdf = token_data[pos.token]
            last_price = tdf['close'].iloc[-1]
            trade, cash_ret = close_position(pos, last_price, all_times[-1], 'eod_close', slip_factor)
            trades.append(trade)

    return {'trades': trades, 'equity_curve': equity_curve, 'capital': capital}


def run_backtest_v2(
    df: pd.DataFrame,
    use_oi_filter: bool = True,
    hold_hours: int = 24,
    leverage: float = 1.0,
    capital: float = CAPITAL,
    max_positions: int = MAX_POSITIONS,
    slippage_bps: float = SLIPPAGE_BPS,
    bottom_n: int = 3,
) -> Dict:
    """Variant 2: Cross-Sectional OI-Filtered Short.

    Each hour, rank all 14 tokens by 24h return.
    Select the bottom N (worst performers).
    If use_oi_filter: only short if their OI is also rising.
    """

    df_oos = df[df.index >= OOS_CUTOFF].copy()
    if len(df_oos) == 0:
        return {'trades': [], 'equity_curve': [], 'capital': capital}

    # Build token-indexed data
    token_data = {}
    for token, gdf in df_oos.groupby('token'):
        token_data[token] = gdf

    all_times = df_oos.index.unique().sort_values()
    positions: List[Position] = []
    trades: List[Trade] = []
    equity_curve = []
    cash = capital
    slip_factor = slippage_bps / 10_000

    # We only enter new positions every 24h/48h (at the hold interval)
    # to avoid overlapping positions on the same signal
    last_entry_time = None
    entry_interval = pd.Timedelta(hours=hold_hours)

    for t in all_times:
        # ── Check exits ──
        new_positions = []
        for pos in positions:
            if pos.token not in token_data:
                new_positions.append(pos)
                continue
            tdf = token_data[pos.token]
            if t not in tdf.index:
                new_positions.append(pos)
                continue

            row = tdf.loc[t]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]

            hours_held = (t - pos.entry_time).total_seconds() / 3600
            if hours_held >= hold_hours:
                trade, cash_ret = close_position(pos, row['close'], t, f'fixed_{hold_hours}h', slip_factor)
                cash += cash_ret
                trades.append(trade)
            else:
                new_positions.append(pos)

        positions = new_positions

        # ── Check entries (only if we have capacity and sufficient time gap) ──
        if len(positions) < max_positions:
            if last_entry_time is not None and (t - last_entry_time) < entry_interval:
                pass  # skip, too soon since last entry batch
            else:
                # Rank all tokens by 24h return at this hour
                token_returns = []
                for token in TOKENS:
                    if token not in token_data:
                        continue
                    tdf = token_data[token]
                    if t not in tdf.index:
                        continue
                    row = tdf.loc[t]
                    if isinstance(row, pd.DataFrame):
                        row = row.iloc[0]

                    ret_24h = row.get('price_chg_24h', np.nan)
                    oi_chg_24h = row.get('oi_chg_24h', np.nan)
                    if np.isnan(ret_24h) or np.isnan(oi_chg_24h):
                        continue

                    token_returns.append((token, ret_24h, oi_chg_24h, row['close']))

                if len(token_returns) >= bottom_n:
                    # Sort by return ascending (worst performers first)
                    token_returns.sort(key=lambda x: x[1])
                    bottom = token_returns[:bottom_n]

                    entered_any = False
                    for token, ret, oi_chg, price in bottom:
                        if len(positions) >= max_positions:
                            break
                        if any(p.token == token for p in positions):
                            continue

                        # Apply OI filter if enabled
                        if use_oi_filter and oi_chg <= 0:
                            continue  # OI not rising, skip

                        size_usd = min(cash * 0.95, capital / max_positions, capital * 0.25)
                        if size_usd < 1000 or cash < size_usd:
                            continue

                        entry_price = price * (1 - slip_factor)
                        cash -= size_usd
                        positions.append(Position(
                            token=token, direction=-1,
                            entry_price=entry_price, entry_time=t,
                            size_usd=size_usd, leverage=leverage,
                        ))
                        entered_any = True

                    if entered_any:
                        last_entry_time = t

        # ── Record equity ──
        total_equity = cash
        for pos in positions:
            if pos.token in token_data and t in token_data[pos.token].index:
                row = token_data[pos.token].loc[t]
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[0]
                total_equity += pos.mark_to_market(row['close'])
            else:
                total_equity += pos.size_usd

        equity_curve.append((t, total_equity))

    # Close remaining
    for pos in positions:
        if pos.token in token_data:
            tdf = token_data[pos.token]
            last_price = tdf['close'].iloc[-1]
            trade, cash_ret = close_position(pos, last_price, all_times[-1], 'eod_close', slip_factor)
            trades.append(trade)

    return {'trades': trades, 'equity_curve': equity_curve, 'capital': capital}


def run_backtest_v3(
    df: pd.DataFrame,
    zscore_entry: float = 2.0,
    zscore_exit: float = 1.0,
    max_hold_hours: int = 72,
    leverage: float = 1.0,
    capital: float = CAPITAL,
    max_positions: int = MAX_POSITIONS,
    slippage_bps: float = SLIPPAGE_BPS,
) -> Dict:
    """Variant 3: OI Z-Score Weighted Short.

    Entry: OI z-score > zscore_entry AND price_chg_24h < 0
    Position size proportional to z-score (higher z = bigger short)
    Exit: z-score drops below zscore_exit OR fixed max hold
    """

    df_oos = df[df.index >= OOS_CUTOFF].copy()
    if len(df_oos) == 0:
        return {'trades': [], 'equity_curve': [], 'capital': capital}

    token_data = {}
    for token, gdf in df_oos.groupby('token'):
        token_data[token] = gdf

    all_times = df_oos.index.unique().sort_values()
    positions: List[Position] = []
    trades: List[Trade] = []
    equity_curve = []
    cash = capital
    slip_factor = slippage_bps / 10_000

    for t in all_times:
        # ── Check exits ──
        new_positions = []
        for pos in positions:
            if pos.token not in token_data:
                new_positions.append(pos)
                continue
            tdf = token_data[pos.token]
            if t not in tdf.index:
                new_positions.append(pos)
                continue

            row = tdf.loc[t]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]

            current_price = row['close']
            hours_held = (t - pos.entry_time).total_seconds() / 3600
            current_zscore = row.get('oi_zscore', np.nan)
            exit_reason = None

            # Z-score exit: pressure releasing
            if not np.isnan(current_zscore) and current_zscore < zscore_exit and hours_held >= 4:
                exit_reason = 'zscore_exit'
            # Max hold
            if hours_held >= max_hold_hours:
                exit_reason = 'max_hold'

            if exit_reason:
                trade, cash_ret = close_position(pos, current_price, t, exit_reason, slip_factor)
                cash += cash_ret
                trades.append(trade)
            else:
                new_positions.append(pos)

        positions = new_positions

        # ── Check entries ──
        if len(positions) < max_positions:
            candidates = []
            for token in TOKENS:
                if token not in token_data:
                    continue
                tdf = token_data[token]
                if t not in tdf.index:
                    continue
                row = tdf.loc[t]
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[0]

                if any(p.token == token for p in positions):
                    continue

                zscore = row.get('oi_zscore', np.nan)
                price_chg = row.get('price_chg_24h', np.nan)
                if np.isnan(zscore) or np.isnan(price_chg):
                    continue

                if zscore > zscore_entry and price_chg < 0:
                    candidates.append((token, row['close'], zscore))

            # Rank by z-score (highest = strongest signal)
            candidates.sort(key=lambda x: x[2], reverse=True)

            if candidates:
                # Compute total z-score weight for proportional sizing
                total_zscore = sum(c[2] for c in candidates[:max_positions - len(positions)])

                for token, price, zscore in candidates:
                    if len(positions) >= max_positions:
                        break

                    # Proportional sizing based on z-score
                    weight = zscore / max(total_zscore, 1.0)
                    available_capital = min(cash * 0.95, capital * 0.8)
                    size_usd = available_capital * weight
                    # Clamp to reasonable bounds
                    size_usd = max(min(size_usd, capital * 0.25, cash * 0.95), 0)
                    if size_usd < 1000 or cash < size_usd:
                        continue

                    entry_price = price * (1 - slip_factor)
                    cash -= size_usd
                    positions.append(Position(
                        token=token, direction=-1,
                        entry_price=entry_price, entry_time=t,
                        size_usd=size_usd, leverage=leverage,
                        weight=weight,
                    ))

        # ── Record equity ──
        total_equity = cash
        for pos in positions:
            if pos.token in token_data and t in token_data[pos.token].index:
                row = token_data[pos.token].loc[t]
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[0]
                total_equity += pos.mark_to_market(row['close'])
            else:
                total_equity += pos.size_usd

        equity_curve.append((t, total_equity))

    # Close remaining
    for pos in positions:
        if pos.token in token_data:
            tdf = token_data[pos.token]
            last_price = tdf['close'].iloc[-1]
            trade, cash_ret = close_position(pos, last_price, all_times[-1], 'eod_close', slip_factor)
            trades.append(trade)

    return {'trades': trades, 'equity_curve': equity_curve, 'capital': capital}


# ── Metrics ──────────────────────────────────────────────────────────────
def compute_metrics(result: Dict) -> Dict:
    """Compute performance metrics from backtest result."""
    trades = result['trades']
    eq = result['equity_curve']
    capital = result['capital']

    if not eq:
        return {'total_return_pct': 0, 'max_dd_pct': 0, 'sharpe': 0,
                'num_trades': 0, 'win_rate': 0, 'avg_trade_pnl': 0,
                'profit_factor': 0, 'final_equity': capital}

    eq_df = pd.DataFrame(eq, columns=['time', 'equity']).set_index('time')
    eq_daily = eq_df['equity'].resample('D').last().ffill().dropna()

    if len(eq_daily) < 2:
        return {'total_return_pct': 0, 'max_dd_pct': 0, 'sharpe': 0,
                'num_trades': 0, 'win_rate': 0, 'avg_trade_pnl': 0,
                'profit_factor': 0, 'final_equity': capital}

    final_equity = eq_daily.iloc[-1]
    total_return = (final_equity / capital - 1) * 100

    # Daily returns for Sharpe
    daily_rets = eq_daily.pct_change().dropna()
    n_days = len(eq_daily)

    # Annualized Sharpe
    if len(daily_rets) > 1 and daily_rets.std() > 0:
        sharpe = (daily_rets.mean() / daily_rets.std()) * np.sqrt(365.25)
    else:
        sharpe = 0

    # Max drawdown
    running_max = eq_daily.cummax()
    drawdowns = (eq_daily - running_max) / running_max
    max_dd = drawdowns.min() * 100

    # Trade stats
    num_trades = len(trades)
    if num_trades > 0:
        pnls = [t.pnl for t in trades]
        wins = sum(1 for p in pnls if p > 0)
        win_rate = wins / num_trades * 100
        avg_trade_pnl = np.mean(pnls)
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = sum(abs(p) for p in pnls if p < 0)
        profit_factor = gross_profit / max(gross_loss, 1)
    else:
        win_rate = 0
        avg_trade_pnl = 0
        profit_factor = 0

    return {
        'total_return_pct': total_return,
        'max_dd_pct': max_dd,
        'sharpe': sharpe,
        'num_trades': num_trades,
        'win_rate': win_rate,
        'avg_trade_pnl': avg_trade_pnl,
        'profit_factor': profit_factor,
        'final_equity': final_equity,
        'n_days': n_days,
    }


# ── Flag promising configs ──────────────────────────────────────────────
def flag_config(m: Dict) -> str:
    """Return flags for promising configs."""
    flags = []
    if m['total_return_pct'] > 0:
        flags.append('RET+')
    if m['win_rate'] > 50:
        flags.append('WR>50')
    if m['sharpe'] > 0.5:
        flags.append('SR>0.5')
    if m['max_dd_pct'] > -30:
        flags.append('DD<30')
    return ' '.join(flags)


# ── Main ─────────────────────────────────────────────────────────────────
def main():
    print('=' * 100)
    print('  OI-ENHANCED SHORT STRATEGY — THREE VARIANTS (OOS BACKTEST)')
    print('  Based on R8: OI rising + price falling => further downside (t-stat: -7.97)')
    print('=' * 100)

    # ── Load data ──
    print('\n[1] Loading data...')
    df = load_all_data()
    df = compute_features(df)

    # Confirm OOS data availability
    oos_count = (df.index >= OOS_CUTOFF).sum()
    print(f'\n  OOS rows (>= {OOS_CUTOFF.strftime("%Y-%m-%d")}): {oos_count:,}')
    print(f'  OOS date range: {df[df.index >= OOS_CUTOFF].index.min().strftime("%Y-%m-%d")} '
          f'to {df[df.index >= OOS_CUTOFF].index.max().strftime("%Y-%m-%d")}')

    # Signal preview on OOS data
    df_oos = df[df.index >= OOS_CUTOFF]
    for oi_t in [0.01, 0.02, 0.05, 0.10]:
        for pr_t in [0.005, 0.01, 0.02, 0.03]:
            cnt = ((df_oos['oi_chg_24h'] > oi_t) & (df_oos['price_chg_24h'] < -pr_t)).sum()
            if oi_t == 0.02 and pr_t == 0.01:
                print(f'\n  Example signal count (OI>+{oi_t*100:.0f}%, Price<-{pr_t*100:.1f}%): {cnt:,}')

    # ══════════════════════════════════════════════════════════════════════
    # VARIANT 1: Pure OI Divergence Short
    # ══════════════════════════════════════════════════════════════════════
    print('\n' + '=' * 100)
    print('  VARIANT 1: PURE OI DIVERGENCE SHORT')
    print('  Grid: OI thresh x [1,2,5,10]%, Price thresh x [0.5,1,2,3]%')
    print('  Exits: fixed_24h, fixed_48h, fixed_72h, oi_decline')
    print('  Leverage: 1x, 2x, 3x')
    print('=' * 100)

    oi_thresholds = [0.01, 0.02, 0.05, 0.10]
    price_thresholds = [0.005, 0.01, 0.02, 0.03]
    exit_modes_v1 = ['fixed_24h', 'fixed_48h', 'fixed_72h', 'oi_decline']
    leverages = [1, 2, 3]

    v1_results = []
    total_configs = len(oi_thresholds) * len(price_thresholds) * len(exit_modes_v1) * len(leverages)
    print(f'\n  Running {total_configs} configurations...')

    config_num = 0
    for lev in leverages:
        for oi_t in oi_thresholds:
            for pr_t in price_thresholds:
                for exit_m in exit_modes_v1:
                    config_num += 1
                    label = f'V1_OI{oi_t*100:.0f}_P{pr_t*100:.1f}_{exit_m}_L{lev}x'

                    result = run_backtest_v1(
                        df, oi_thresh=oi_t, price_thresh=pr_t,
                        exit_mode=exit_m, leverage=lev,
                    )
                    m = compute_metrics(result)
                    m['label'] = label
                    m['variant'] = 'V1'
                    m['oi_thresh'] = oi_t
                    m['price_thresh'] = pr_t
                    m['exit_mode'] = exit_m
                    m['leverage'] = lev
                    m['flags'] = flag_config(m)
                    v1_results.append(m)

                    if config_num % 48 == 0:
                        print(f'  ... {config_num}/{total_configs} done')

    print(f'  ... {total_configs}/{total_configs} done')

    v1_df = pd.DataFrame(v1_results).sort_values('sharpe', ascending=False)

    # Print all V1 results
    print(f'\n  {"Config":<45s} {"Return":>8s} {"MaxDD":>8s} {"Sharpe":>7s} '
          f'{"Trades":>7s} {"WinR":>6s} {"AvgPnL":>10s} {"PF":>6s}  Flags')
    print(f'  {"-"*45} {"-"*8} {"-"*8} {"-"*7} {"-"*7} {"-"*6} {"-"*10} {"-"*6}  -----')

    for _, row in v1_df.head(30).iterrows():
        print(f'  {row["label"]:<45s} '
              f'{row["total_return_pct"]:>+7.1f}% '
              f'{row["max_dd_pct"]:>+7.1f}% '
              f'{row["sharpe"]:>7.2f} '
              f'{row["num_trades"]:>7.0f} '
              f'{row["win_rate"]:>5.1f}% '
              f'${row["avg_trade_pnl"]:>+9,.0f} '
              f'{row["profit_factor"]:>6.2f}  '
              f'{row["flags"]}')

    # Flag promising configs
    promising_v1 = v1_df[
        (v1_df['total_return_pct'] > 0) &
        (v1_df['win_rate'] > 50) &
        (v1_df['sharpe'] > 0.5) &
        (v1_df['max_dd_pct'] > -30)
    ]
    print(f'\n  *** PROMISING V1 configs (Ret>0, WR>50%, Sharpe>0.5, DD<30%): {len(promising_v1)} ***')
    for _, row in promising_v1.iterrows():
        print(f'      {row["label"]}: ret={row["total_return_pct"]:+.1f}%, '
              f'sharpe={row["sharpe"]:.2f}, wr={row["win_rate"]:.1f}%, '
              f'dd={row["max_dd_pct"]:.1f}%, pf={row["profit_factor"]:.2f}')

    # ══════════════════════════════════════════════════════════════════════
    # VARIANT 2: Cross-Sectional OI-Filtered Short
    # ══════════════════════════════════════════════════════════════════════
    print('\n' + '=' * 100)
    print('  VARIANT 2: CROSS-SECTIONAL OI-FILTERED SHORT')
    print('  Short bottom 3 by 24h return, with/without OI filter')
    print('  Exits: 24h, 48h | Leverage: 1x, 2x, 3x')
    print('=' * 100)

    v2_results = []
    v2_configs = 0
    for lev in leverages:
        for hold_h in [24, 48]:
            for use_filter in [False, True]:
                v2_configs += 1
                filter_str = 'OI_FILT' if use_filter else 'NO_FILT'
                label = f'V2_{filter_str}_hold{hold_h}h_L{lev}x'

                result = run_backtest_v2(
                    df, use_oi_filter=use_filter,
                    hold_hours=hold_h, leverage=lev,
                )
                m = compute_metrics(result)
                m['label'] = label
                m['variant'] = 'V2'
                m['use_oi_filter'] = use_filter
                m['hold_hours'] = hold_h
                m['leverage'] = lev
                m['flags'] = flag_config(m)
                v2_results.append(m)

    v2_df = pd.DataFrame(v2_results).sort_values('sharpe', ascending=False)

    print(f'\n  {"Config":<45s} {"Return":>8s} {"MaxDD":>8s} {"Sharpe":>7s} '
          f'{"Trades":>7s} {"WinR":>6s} {"AvgPnL":>10s} {"PF":>6s}  Flags')
    print(f'  {"-"*45} {"-"*8} {"-"*8} {"-"*7} {"-"*7} {"-"*6} {"-"*10} {"-"*6}  -----')

    for _, row in v2_df.iterrows():
        print(f'  {row["label"]:<45s} '
              f'{row["total_return_pct"]:>+7.1f}% '
              f'{row["max_dd_pct"]:>+7.1f}% '
              f'{row["sharpe"]:>7.2f} '
              f'{row["num_trades"]:>7.0f} '
              f'{row["win_rate"]:>5.1f}% '
              f'${row["avg_trade_pnl"]:>+9,.0f} '
              f'{row["profit_factor"]:>6.2f}  '
              f'{row["flags"]}')

    # Compare OI filter impact
    print('\n  --- OI Filter Impact Analysis ---')
    for hold_h in [24, 48]:
        for lev in leverages:
            no_f = v2_df[(~v2_df['use_oi_filter']) & (v2_df['hold_hours'] == hold_h) & (v2_df['leverage'] == lev)]
            wi_f = v2_df[(v2_df['use_oi_filter']) & (v2_df['hold_hours'] == hold_h) & (v2_df['leverage'] == lev)]
            if len(no_f) > 0 and len(wi_f) > 0:
                no_f = no_f.iloc[0]
                wi_f = wi_f.iloc[0]
                ret_diff = wi_f['total_return_pct'] - no_f['total_return_pct']
                wr_diff = wi_f['win_rate'] - no_f['win_rate']
                sr_diff = wi_f['sharpe'] - no_f['sharpe']
                print(f'  Hold {hold_h}h, {lev}x: OI filter effect -> '
                      f'Return: {ret_diff:+.1f}pp, WinRate: {wr_diff:+.1f}pp, Sharpe: {sr_diff:+.2f}')

    # Flag promising
    promising_v2 = v2_df[
        (v2_df['total_return_pct'] > 0) &
        (v2_df['win_rate'] > 50) &
        (v2_df['sharpe'] > 0.5) &
        (v2_df['max_dd_pct'] > -30)
    ]
    print(f'\n  *** PROMISING V2 configs: {len(promising_v2)} ***')
    for _, row in promising_v2.iterrows():
        print(f'      {row["label"]}: ret={row["total_return_pct"]:+.1f}%, '
              f'sharpe={row["sharpe"]:.2f}, wr={row["win_rate"]:.1f}%, '
              f'dd={row["max_dd_pct"]:.1f}%, pf={row["profit_factor"]:.2f}')

    # ══════════════════════════════════════════════════════════════════════
    # VARIANT 3: OI Z-Score Weighted Short
    # ══════════════════════════════════════════════════════════════════════
    print('\n' + '=' * 100)
    print('  VARIANT 3: OI Z-SCORE WEIGHTED SHORT')
    print('  Entry: z-score > 2.0 AND price_chg_24h < 0')
    print('  Size proportional to z-score | Exit: z-score < 1.0 OR 72h max hold')
    print('  Leverage: 1x, 2x, 3x')
    print('=' * 100)

    v3_results = []
    zscore_entries = [1.5, 2.0, 2.5, 3.0]
    zscore_exits = [0.5, 1.0, 1.5]
    max_holds = [48, 72]

    v3_total = len(zscore_entries) * len(zscore_exits) * len(max_holds) * len(leverages)
    print(f'\n  Running {v3_total} configurations...')

    v3_num = 0
    for lev in leverages:
        for z_entry in zscore_entries:
            for z_exit in zscore_exits:
                if z_exit >= z_entry:
                    continue  # skip nonsensical configs
                for mh in max_holds:
                    v3_num += 1
                    label = f'V3_Zen{z_entry:.1f}_Zex{z_exit:.1f}_hold{mh}h_L{lev}x'

                    result = run_backtest_v3(
                        df, zscore_entry=z_entry, zscore_exit=z_exit,
                        max_hold_hours=mh, leverage=lev,
                    )
                    m = compute_metrics(result)
                    m['label'] = label
                    m['variant'] = 'V3'
                    m['zscore_entry'] = z_entry
                    m['zscore_exit'] = z_exit
                    m['max_hold'] = mh
                    m['leverage'] = lev
                    m['flags'] = flag_config(m)
                    v3_results.append(m)

    print(f'  ... {v3_num}/{v3_total} done')

    v3_df = pd.DataFrame(v3_results).sort_values('sharpe', ascending=False)

    print(f'\n  {"Config":<50s} {"Return":>8s} {"MaxDD":>8s} {"Sharpe":>7s} '
          f'{"Trades":>7s} {"WinR":>6s} {"AvgPnL":>10s} {"PF":>6s}  Flags')
    print(f'  {"-"*50} {"-"*8} {"-"*8} {"-"*7} {"-"*7} {"-"*6} {"-"*10} {"-"*6}  -----')

    for _, row in v3_df.iterrows():
        print(f'  {row["label"]:<50s} '
              f'{row["total_return_pct"]:>+7.1f}% '
              f'{row["max_dd_pct"]:>+7.1f}% '
              f'{row["sharpe"]:>7.2f} '
              f'{row["num_trades"]:>7.0f} '
              f'{row["win_rate"]:>5.1f}% '
              f'${row["avg_trade_pnl"]:>+9,.0f} '
              f'{row["profit_factor"]:>6.2f}  '
              f'{row["flags"]}')

    # Flag promising
    promising_v3 = v3_df[
        (v3_df['total_return_pct'] > 0) &
        (v3_df['win_rate'] > 50) &
        (v3_df['sharpe'] > 0.5) &
        (v3_df['max_dd_pct'] > -30)
    ]
    print(f'\n  *** PROMISING V3 configs: {len(promising_v3)} ***')
    for _, row in promising_v3.iterrows():
        print(f'      {row["label"]}: ret={row["total_return_pct"]:+.1f}%, '
              f'sharpe={row["sharpe"]:.2f}, wr={row["win_rate"]:.1f}%, '
              f'dd={row["max_dd_pct"]:.1f}%, pf={row["profit_factor"]:.2f}')

    # ══════════════════════════════════════════════════════════════════════
    # COMBINED SUMMARY
    # ══════════════════════════════════════════════════════════════════════
    print('\n' + '=' * 100)
    print('  COMBINED SUMMARY — ALL VARIANTS')
    print('=' * 100)

    all_results = pd.concat([v1_df, v2_df, v3_df], ignore_index=True)
    all_results = all_results.sort_values('sharpe', ascending=False)

    print(f'\n  Total configs tested: {len(all_results)}')
    print(f'\n  TOP 20 BY SHARPE:')
    print(f'  {"#":>3s} {"Config":<50s} {"Return":>8s} {"MaxDD":>8s} {"Sharpe":>7s} '
          f'{"Trades":>7s} {"WinR":>6s} {"AvgPnL":>10s} {"PF":>6s}  Flags')
    print(f'  {"-"*3} {"-"*50} {"-"*8} {"-"*8} {"-"*7} {"-"*7} {"-"*6} {"-"*10} {"-"*6}  -----')

    for rank, (_, row) in enumerate(all_results.head(20).iterrows(), 1):
        print(f'  {rank:>3d} {row["label"]:<50s} '
              f'{row["total_return_pct"]:>+7.1f}% '
              f'{row["max_dd_pct"]:>+7.1f}% '
              f'{row["sharpe"]:>7.2f} '
              f'{row["num_trades"]:>7.0f} '
              f'{row["win_rate"]:>5.1f}% '
              f'${row["avg_trade_pnl"]:>+9,.0f} '
              f'{row["profit_factor"]:>6.2f}  '
              f'{row["flags"]}')

    # Overall promising
    all_promising = all_results[
        (all_results['total_return_pct'] > 0) &
        (all_results['win_rate'] > 50) &
        (all_results['sharpe'] > 0.5) &
        (all_results['max_dd_pct'] > -30)
    ]

    print(f'\n  *** ALL PROMISING CONFIGS (Ret>0, WR>50%, Sharpe>0.5, DD<30%): {len(all_promising)} ***')
    if len(all_promising) > 0:
        for rank, (_, row) in enumerate(all_promising.sort_values('sharpe', ascending=False).iterrows(), 1):
            print(f'  {rank:>3d}. {row["label"]}: '
                  f'ret={row["total_return_pct"]:+.1f}%, sharpe={row["sharpe"]:.2f}, '
                  f'wr={row["win_rate"]:.1f}%, dd={row["max_dd_pct"]:.1f}%, '
                  f'pf={row["profit_factor"]:.2f}, trades={row["num_trades"]:.0f}')
    else:
        print('  None meeting all four criteria. Relaxing criteria...')
        # Relax: just positive return and reasonable DD
        relaxed = all_results[
            (all_results['total_return_pct'] > 0) &
            (all_results['max_dd_pct'] > -30)
        ].sort_values('sharpe', ascending=False)
        print(f'\n  Configs with Ret>0 AND DD<30%: {len(relaxed)}')
        for rank, (_, row) in enumerate(relaxed.head(10).iterrows(), 1):
            print(f'  {rank:>3d}. {row["label"]}: '
                  f'ret={row["total_return_pct"]:+.1f}%, sharpe={row["sharpe"]:.2f}, '
                  f'wr={row["win_rate"]:.1f}%, dd={row["max_dd_pct"]:.1f}%, '
                  f'pf={row["profit_factor"]:.2f}, trades={row["num_trades"]:.0f}')

    # ── Detailed breakdown of top 3 overall ──
    print('\n' + '=' * 100)
    print('  TOP 3 CONFIGS — DETAILED ANALYSIS')
    print('=' * 100)

    # Identify which variant's backtest function to re-run
    for rank in range(min(3, len(all_results))):
        row = all_results.iloc[rank]
        label = row['label']
        variant = row['variant']

        print(f'\n  --- #{rank+1}: {label} ---')
        print(f'  Variant:          {variant}')
        print(f'  Total Return:     {row["total_return_pct"]:>+8.1f}%')
        print(f'  Final Equity:     ${row["final_equity"]:>12,.0f}')
        print(f'  Max Drawdown:     {row["max_dd_pct"]:>+8.1f}%')
        print(f'  Sharpe Ratio:     {row["sharpe"]:>8.2f}')
        print(f'  Total Trades:     {row["num_trades"]:>8.0f}')
        print(f'  Win Rate:         {row["win_rate"]:>8.1f}%')
        print(f'  Avg Trade PnL:    ${row["avg_trade_pnl"]:>+9,.0f}')
        print(f'  Profit Factor:    {row["profit_factor"]:>8.2f}')
        print(f'  Leverage:         {row["leverage"]:>8.0f}x')

        # Re-run to get trade details
        if variant == 'V1':
            result = run_backtest_v1(
                df, oi_thresh=row['oi_thresh'], price_thresh=row['price_thresh'],
                exit_mode=row['exit_mode'], leverage=row['leverage'],
            )
        elif variant == 'V2':
            result = run_backtest_v2(
                df, use_oi_filter=row['use_oi_filter'],
                hold_hours=row['hold_hours'], leverage=row['leverage'],
            )
        else:  # V3
            result = run_backtest_v3(
                df, zscore_entry=row['zscore_entry'], zscore_exit=row['zscore_exit'],
                max_hold_hours=row['max_hold'], leverage=row['leverage'],
            )

        trades = result['trades']
        if trades:
            # Per-token breakdown
            token_pnl = {}
            token_count = {}
            token_wins = {}
            for t in trades:
                token_pnl[t.token] = token_pnl.get(t.token, 0) + t.pnl
                token_count[t.token] = token_count.get(t.token, 0) + 1
                if t.pnl > 0:
                    token_wins[t.token] = token_wins.get(t.token, 0) + 1

            print(f'\n  Per-token breakdown:')
            print(f'  {"Token":>8s}  {"Trades":>7s}  {"Wins":>5s}  {"WinR":>6s}  {"PnL":>12s}  {"AvgPnL":>10s}')
            print(f'  {"-"*8}  {"-"*7}  {"-"*5}  {"-"*6}  {"-"*12}  {"-"*10}')
            for token in sorted(token_pnl.keys(), key=lambda x: token_pnl[x], reverse=True):
                n = token_count[token]
                w = token_wins.get(token, 0)
                wr = w / n * 100 if n > 0 else 0
                pnl = token_pnl[token]
                avg = pnl / n if n > 0 else 0
                print(f'  {token:>8s}  {n:>7d}  {w:>5d}  {wr:>5.1f}%  ${pnl:>+11,.0f}  ${avg:>+9,.0f}')

            # Exit reason breakdown
            exit_reasons = {}
            for t in trades:
                exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1
            print(f'\n  Exit reasons:')
            for reason, count in sorted(exit_reasons.items(), key=lambda x: x[1], reverse=True):
                print(f'    {reason}: {count}')

            # Monthly equity curve
            eq = result['equity_curve']
            if eq:
                eq_df = pd.DataFrame(eq, columns=['time', 'equity']).set_index('time')
                eq_monthly = eq_df['equity'].resample('ME').last().ffill().dropna()
                if len(eq_monthly) > 1:
                    print(f'\n  Monthly equity:')
                    print(f'  {"Month":>10s}  {"Equity":>14s}  {"Return":>8s}')
                    print(f'  {"-"*10}  {"-"*14}  {"-"*8}')
                    prev = CAPITAL
                    for dt, eq_val in eq_monthly.items():
                        ret = (eq_val / prev - 1) * 100
                        print(f'  {dt.strftime("%Y-%m"):>10s}  ${eq_val:>13,.0f}  {ret:>+7.1f}%')
                        prev = eq_val

    # ── Leverage sensitivity ──
    print('\n' + '=' * 100)
    print('  LEVERAGE SENSITIVITY (best V1 entry/exit, varying leverage)')
    print('=' * 100)

    # Find best V1 config at 1x and show all leverage levels
    best_v1_1x = v1_df[v1_df['leverage'] == 1].sort_values('sharpe', ascending=False)
    if len(best_v1_1x) > 0:
        best_oi = best_v1_1x.iloc[0]['oi_thresh']
        best_pr = best_v1_1x.iloc[0]['price_thresh']
        best_exit = best_v1_1x.iloc[0]['exit_mode']
        print(f'  Config: OI>{best_oi*100:.0f}%, Price<-{best_pr*100:.1f}%, Exit={best_exit}')

        lev_filter = v1_df[
            (v1_df['oi_thresh'] == best_oi) &
            (v1_df['price_thresh'] == best_pr) &
            (v1_df['exit_mode'] == best_exit)
        ].sort_values('leverage')

        print(f'  {"Lev":>4s}  {"Return":>8s}  {"MaxDD":>8s}  {"Sharpe":>7s}  '
              f'{"Trades":>7s}  {"WinR":>6s}  {"PF":>6s}')
        print(f'  {"-"*4}  {"-"*8}  {"-"*8}  {"-"*7}  {"-"*7}  {"-"*6}  {"-"*6}')
        for _, row in lev_filter.iterrows():
            print(f'  {row["leverage"]:>3.0f}x  '
                  f'{row["total_return_pct"]:>+7.1f}%  '
                  f'{row["max_dd_pct"]:>+7.1f}%  '
                  f'{row["sharpe"]:>7.2f}  '
                  f'{row["num_trades"]:>7.0f}  '
                  f'{row["win_rate"]:>5.1f}%  '
                  f'{row["profit_factor"]:>6.2f}')

    # ── Summary stats ──
    print('\n' + '=' * 100)
    print('  FINAL SUMMARY')
    print('=' * 100)

    for vname, vdf in [('V1 (OI Divergence)', v1_df),
                        ('V2 (Cross-Sectional)', v2_df),
                        ('V3 (Z-Score Weighted)', v3_df)]:
        best = vdf.iloc[0] if len(vdf) > 0 else None
        if best is not None:
            print(f'\n  {vname}:')
            print(f'    Best Sharpe:    {best["sharpe"]:.2f}  ({best["label"]})')
            print(f'    Return:         {best["total_return_pct"]:+.1f}%')
            print(f'    Max DD:         {best["max_dd_pct"]:.1f}%')
            print(f'    Win Rate:       {best["win_rate"]:.1f}%')
            print(f'    Profit Factor:  {best["profit_factor"]:.2f}')
            print(f'    Trades:         {best["num_trades"]:.0f}')

    # Best overall
    best_overall = all_results.iloc[0]
    print(f'\n  BEST OVERALL: {best_overall["label"]}')
    print(f'    Return:         {best_overall["total_return_pct"]:+.1f}%')
    print(f'    Sharpe:         {best_overall["sharpe"]:.2f}')
    print(f'    Max DD:         {best_overall["max_dd_pct"]:.1f}%')
    print(f'    Win Rate:       {best_overall["win_rate"]:.1f}%')
    print(f'    Profit Factor:  {best_overall["profit_factor"]:.2f}')

    print('\n' + '=' * 100)
    print('  DONE')
    print('=' * 100)


if __name__ == '__main__':
    main()
