#!/workspace/venv/bin/python
"""
Pairs Trading / Statistical Arbitrage Backtest
================================================

Market-neutral strategies that profit from relative value, not direction.

Strategy Variants:
  A. Spot-Spot Pairs (Cointegration): ETH/BTC, SOL/ETH, BNB/BTC, DOGE/XRP
     - Rolling OLS beta (60d), z-score spread (30d), enter at |z|>2, exit at z~0
  B. Spot-Perp Basis Trade (Cash & Carry): BTC and ETH
     - Basis = (perp - spot) / spot, enter when |basis| > threshold
     - Include funding rate income/cost from perp data
  C. Cross-Token Momentum Spread (Long-Short)
     - Weekly: long top 3 by 14d return, short bottom 3 (dollar-neutral)

Backtest Details:
  - Dollar-neutral sizing per leg
  - 10 bps per leg per trade (20 bps round-trip per leg)
  - IS/OOS split: 70%/30%, warmup: 90 days
  - Kill: Sharpe<0.3, MaxDD>20%, BTC corr>0.3, <30 trades, win rate<45%
"""

import pandas as pd
import numpy as np
import json
import warnings
from pathlib import Path
from datetime import datetime

warnings.filterwarnings('ignore')

BASE_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = BASE_DIR / 'data'
OUTPUT_DIR = BASE_DIR / 'research'

COST_BPS = 10  # per leg per trade
COST_FRAC = COST_BPS / 10_000
WARMUP_BARS = 90 * 24  # 90 days in hourly bars
IS_FRACTION = 0.70
HOURS_PER_YEAR = 365.25 * 24
SQRT_HOURS_PER_YEAR = np.sqrt(HOURS_PER_YEAR)


# ── Data Loading ─────────────────────────────────────────────────────────────

def load_spot(token):
    """Load spot 1h data for a token."""
    path = DATA_DIR / f'spot/1h_cache/{token}_1h.parquet'
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    df.index.name = 'timestamp'
    return df


def load_perp(token):
    """Load perp 1h data for a token."""
    path = DATA_DIR / f'perp/1h_cache/{token}_1h.parquet'
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    df.index.name = 'timestamp'
    return df


def load_btc_returns():
    """Load BTC spot hourly returns for correlation calculation."""
    btc = load_spot('BTC')
    if btc is None:
        return None
    return btc['close'].pct_change()


# ── Metrics ──────────────────────────────────────────────────────────────────

def compute_metrics(equity_curve, btc_returns, trades_list, label):
    """Compute all required metrics for a strategy."""
    # Align equity curve and BTC returns
    returns = equity_curve.pct_change().dropna()
    returns = returns.replace([np.inf, -np.inf], 0.0)

    if len(returns) < 100:
        return None

    # Annualized Sharpe
    mean_ret = returns.mean()
    std_ret = returns.std()
    sharpe = (mean_ret / std_ret * SQRT_HOURS_PER_YEAR) if std_ret > 0 else 0.0

    # Total return
    total_return = (equity_curve.iloc[-1] / equity_curve.iloc[0] - 1) * 100

    # Max drawdown
    cummax = equity_curve.cummax()
    drawdown = (equity_curve - cummax) / cummax
    max_dd = drawdown.min() * 100

    # Trade statistics
    n_trades = len(trades_list)
    if n_trades > 0:
        pnls = [t['pnl'] for t in trades_list]
        win_rate = sum(1 for p in pnls if p > 0) / n_trades * 100
        avg_pnl = np.mean(pnls) * 100  # in percent
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    else:
        win_rate = 0
        avg_pnl = 0
        profit_factor = 0

    # BTC correlation
    aligned = pd.DataFrame({
        'strat': returns,
        'btc': btc_returns
    }).dropna()
    btc_corr = aligned['strat'].corr(aligned['btc']) if len(aligned) > 50 else np.nan

    return {
        'strategy': label,
        'sharpe': round(sharpe, 3),
        'total_return_pct': round(total_return, 2),
        'max_dd_pct': round(max_dd, 2),
        'n_trades': n_trades,
        'win_rate_pct': round(win_rate, 1),
        'avg_trade_pnl_pct': round(avg_pnl, 4),
        'profit_factor': round(profit_factor, 3),
        'btc_correlation': round(btc_corr, 3) if not np.isnan(btc_corr) else None,
    }


def check_kill_criteria(metrics):
    """Check if strategy passes kill criteria."""
    kills = []
    if metrics['sharpe'] < 0.3:
        kills.append(f"Sharpe {metrics['sharpe']} < 0.3")
    if metrics['max_dd_pct'] < -20:
        kills.append(f"MaxDD {metrics['max_dd_pct']}% > 20%")
    if metrics['btc_correlation'] is not None and abs(metrics['btc_correlation']) > 0.3:
        kills.append(f"BTC corr {metrics['btc_correlation']} > 0.3")
    if metrics['n_trades'] < 30:
        kills.append(f"Trades {metrics['n_trades']} < 30")
    if metrics['win_rate_pct'] < 45:
        kills.append(f"Win rate {metrics['win_rate_pct']}% < 45%")
    return kills


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY A: Spot-Spot Pairs (Cointegration-based)
# ══════════════════════════════════════════════════════════════════════════════

def run_spot_spot_pairs(pair_name, token_a, token_b, btc_returns):
    """
    Cointegration-based pairs trading on spot data.
    spread = log(A) - beta * log(B), beta from 60d rolling OLS
    z-score with 30d rolling, entry at |z|>2, exit at z~0, stop at |z|>4
    """
    print(f"\n  [A] Spot-Spot Pair: {pair_name} ({token_a}/{token_b})")

    spot_a = load_spot(token_a)
    spot_b = load_spot(token_b)
    if spot_a is None or spot_b is None:
        print(f"    SKIP: missing data")
        return None, None

    # Align on common timestamps
    common_idx = spot_a.index.intersection(spot_b.index)
    if len(common_idx) < WARMUP_BARS + 1000:
        print(f"    SKIP: insufficient overlap ({len(common_idx)} bars)")
        return None, None

    price_a = spot_a.loc[common_idx, 'close']
    price_b = spot_b.loc[common_idx, 'close']
    log_a = np.log(price_a)
    log_b = np.log(price_b)

    # Rolling OLS beta (60 days = 1440 hourly bars)
    beta_window = 60 * 24
    zscore_window = 30 * 24

    betas = pd.Series(index=common_idx, dtype=float)
    spread = pd.Series(index=common_idx, dtype=float)

    # Vectorized rolling beta using cov/var
    rolling_cov = log_a.rolling(beta_window).cov(log_b)
    rolling_var_b = log_b.rolling(beta_window).var()
    betas = rolling_cov / rolling_var_b
    spread = log_a - betas * log_b

    # Z-score the spread
    spread_mean = spread.rolling(zscore_window).mean()
    spread_std = spread.rolling(zscore_window).std()
    zscore = (spread - spread_mean) / spread_std

    # IS/OOS split
    valid_start = max(beta_window, zscore_window, WARMUP_BARS)
    valid_data = zscore.iloc[valid_start:]
    n_valid = len(valid_data)
    is_end = int(n_valid * IS_FRACTION)
    oos_start_idx = valid_data.index[is_end]

    # Run backtest on OOS portion
    oos_zscore = zscore.loc[oos_start_idx:]
    oos_price_a = price_a.loc[oos_start_idx:]
    oos_price_b = price_b.loc[oos_start_idx:]
    oos_betas = betas.loc[oos_start_idx:]

    print(f"    Data: {len(common_idx)} bars, OOS: {len(oos_zscore)} bars")
    print(f"    OOS period: {oos_zscore.index[0].date()} to {oos_zscore.index[-1].date()}")

    # Trading simulation
    equity = 1.0
    equity_series = []
    trades = []
    position = 0  # 1 = long spread, -1 = short spread, 0 = flat
    entry_equity = None
    entry_bar = 0
    max_hold = 168  # 1 week

    for i in range(len(oos_zscore)):
        z = oos_zscore.iloc[i]
        ts = oos_zscore.index[i]

        if np.isnan(z):
            equity_series.append(equity)
            continue

        # PnL from open positions
        if position != 0 and i > 0:
            ret_a = oos_price_a.iloc[i] / oos_price_a.iloc[i-1] - 1
            ret_b = oos_price_b.iloc[i] / oos_price_b.iloc[i-1] - 1
            # Long spread: long A, short B (each 50% of capital)
            # Short spread: short A, long B
            if position == 1:
                pnl = 0.5 * ret_a - 0.5 * ret_b
            else:
                pnl = -0.5 * ret_a + 0.5 * ret_b
            equity *= (1 + pnl)

        bars_held = i - entry_bar if position != 0 else 0

        # Exit conditions
        if position != 0:
            should_exit = False
            exit_reason = ''

            if position == 1 and z >= 0:
                should_exit = True
                exit_reason = 'mean_reversion'
            elif position == -1 and z <= 0:
                should_exit = True
                exit_reason = 'mean_reversion'
            elif abs(z) > 4:
                should_exit = True
                exit_reason = 'stop_breakdown'
            elif bars_held >= max_hold:
                should_exit = True
                exit_reason = 'max_hold'

            if should_exit:
                # Exit costs: 2 legs * cost per leg
                equity *= (1 - 2 * COST_FRAC)
                trade_pnl = equity / entry_equity - 1
                trades.append({
                    'pnl': trade_pnl,
                    'bars_held': bars_held,
                    'reason': exit_reason,
                })
                position = 0

        # Entry conditions (only if flat)
        if position == 0:
            if z < -2:
                position = 1  # long spread
                equity *= (1 - 2 * COST_FRAC)  # entry costs
                entry_equity = equity
                entry_bar = i
            elif z > 2:
                position = -1  # short spread
                equity *= (1 - 2 * COST_FRAC)
                entry_equity = equity
                entry_bar = i

        equity_series.append(equity)

    # Close any open position at the end
    if position != 0:
        equity *= (1 - 2 * COST_FRAC)
        trade_pnl = equity / entry_equity - 1
        trades.append({
            'pnl': trade_pnl,
            'bars_held': len(oos_zscore) - entry_bar,
            'reason': 'end_of_data',
        })

    equity_curve = pd.Series(equity_series, index=oos_zscore.index[:len(equity_series)])
    metrics = compute_metrics(equity_curve, btc_returns, trades, f"A: {pair_name}")

    if metrics:
        kills = check_kill_criteria(metrics)
        print(f"    Sharpe={metrics['sharpe']}, Return={metrics['total_return_pct']}%, "
              f"MaxDD={metrics['max_dd_pct']}%, Trades={metrics['n_trades']}, "
              f"WR={metrics['win_rate_pct']}%, BTC_corr={metrics['btc_correlation']}")
        if kills:
            print(f"    KILLED: {'; '.join(kills)}")
        else:
            print(f"    PASS all kill criteria")

    return metrics, kills if metrics else None


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY B: Spot-Perp Basis Trade (Cash & Carry)
# ══════════════════════════════════════════════════════════════════════════════

def run_basis_trade(token, threshold_bps, btc_returns):
    """
    Cash & carry: exploit spot-perp basis divergence.
    basis = (perp - spot) / spot
    Long basis: long spot + short perp when basis > threshold
    Short basis: short spot + long perp when basis < -threshold
    Include funding rate income/cost from perp data.
    """
    threshold = threshold_bps / 10_000
    label = f"B: {token} basis ({threshold_bps}bps)"
    print(f"\n  [B] Basis Trade: {token} threshold={threshold_bps}bps")

    spot = load_spot(token)
    perp = load_perp(token)
    if spot is None or perp is None:
        print(f"    SKIP: missing data")
        return None, None

    # Align
    common_idx = spot.index.intersection(perp.index)
    if len(common_idx) < WARMUP_BARS + 1000:
        print(f"    SKIP: insufficient overlap ({len(common_idx)} bars)")
        return None, None

    spot_close = spot.loc[common_idx, 'close']
    perp_close = perp.loc[common_idx, 'close']

    # Get funding rate from perp data
    # funding_1h and funding_rate are both the 8-hour rate broadcasted to each hour
    # Divide by 8 to get hourly accrual rate
    if 'funding_1h' in perp.columns:
        funding = (perp.loc[common_idx, 'funding_1h'] / 8).fillna(0)
    elif 'funding_rate' in perp.columns:
        funding = (perp.loc[common_idx, 'funding_rate'] / 8).fillna(0)
    else:
        funding = pd.Series(0, index=common_idx)

    basis = (perp_close - spot_close) / spot_close

    # IS/OOS split
    valid_start = WARMUP_BARS
    valid_data = basis.iloc[valid_start:]
    n_valid = len(valid_data)
    is_end = int(n_valid * IS_FRACTION)
    oos_start_idx = valid_data.index[is_end]

    oos_basis = basis.loc[oos_start_idx:]
    oos_spot = spot_close.loc[oos_start_idx:]
    oos_perp = perp_close.loc[oos_start_idx:]
    oos_funding = funding.loc[oos_start_idx:]

    print(f"    Data: {len(common_idx)} bars, OOS: {len(oos_basis)} bars")
    print(f"    OOS period: {oos_basis.index[0].date()} to {oos_basis.index[-1].date()}")
    print(f"    Basis stats (OOS): mean={oos_basis.mean()*10000:.1f}bps, "
          f"std={oos_basis.std()*10000:.1f}bps, "
          f"median={oos_basis.median()*10000:.1f}bps")

    # Trading simulation
    equity = 1.0
    equity_series = []
    trades = []
    position = 0  # 1 = long basis (long spot, short perp), -1 = short basis
    entry_equity = None
    entry_bar = 0
    max_hold = 2016  # 12 weeks

    for i in range(len(oos_basis)):
        b = oos_basis.iloc[i]
        f_rate = oos_funding.iloc[i]

        if np.isnan(b):
            equity_series.append(equity)
            continue

        # PnL from open positions
        if position != 0 and i > 0:
            spot_ret = oos_spot.iloc[i] / oos_spot.iloc[i-1] - 1
            perp_ret = oos_perp.iloc[i] / oos_perp.iloc[i-1] - 1

            if position == 1:
                # Long spot + short perp
                # Spot leg: +spot_ret, Perp leg: -perp_ret
                # Funding: short perp RECEIVES funding when positive, pays when negative
                pnl = 0.5 * spot_ret - 0.5 * perp_ret + 0.5 * f_rate
            else:
                # Short spot + long perp
                pnl = -0.5 * spot_ret + 0.5 * perp_ret - 0.5 * f_rate

            equity *= (1 + pnl)

        bars_held = i - entry_bar if position != 0 else 0

        # Exit conditions
        if position != 0:
            should_exit = False
            exit_reason = ''

            # Exit when basis reverts near zero
            if position == 1 and b <= threshold * 0.1:
                should_exit = True
                exit_reason = 'basis_revert'
            elif position == -1 and b >= -threshold * 0.1:
                should_exit = True
                exit_reason = 'basis_revert'
            elif bars_held >= max_hold:
                should_exit = True
                exit_reason = 'max_hold'

            if should_exit:
                equity *= (1 - 2 * COST_FRAC)
                trade_pnl = equity / entry_equity - 1
                trades.append({
                    'pnl': trade_pnl,
                    'bars_held': bars_held,
                    'reason': exit_reason,
                })
                position = 0

        # Entry conditions
        if position == 0:
            if b > threshold:
                position = 1  # long basis (long spot, short perp)
                equity *= (1 - 2 * COST_FRAC)
                entry_equity = equity
                entry_bar = i
            elif b < -threshold:
                position = -1  # short basis
                equity *= (1 - 2 * COST_FRAC)
                entry_equity = equity
                entry_bar = i

        equity_series.append(equity)

    # Close any open position
    if position != 0:
        equity *= (1 - 2 * COST_FRAC)
        trade_pnl = equity / entry_equity - 1
        trades.append({
            'pnl': trade_pnl,
            'bars_held': len(oos_basis) - entry_bar,
            'reason': 'end_of_data',
        })

    equity_curve = pd.Series(equity_series, index=oos_basis.index[:len(equity_series)])
    metrics = compute_metrics(equity_curve, btc_returns, trades, label)

    if metrics:
        kills = check_kill_criteria(metrics)
        print(f"    Sharpe={metrics['sharpe']}, Return={metrics['total_return_pct']}%, "
              f"MaxDD={metrics['max_dd_pct']}%, Trades={metrics['n_trades']}, "
              f"WR={metrics['win_rate_pct']}%, BTC_corr={metrics['btc_correlation']}")
        if kills:
            print(f"    KILLED: {'; '.join(kills)}")
        else:
            print(f"    PASS all kill criteria")

    return metrics, kills if metrics else None


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY C: Cross-Token Momentum Spread (Long-Short)
# ══════════════════════════════════════════════════════════════════════════════

def run_cross_momentum_spread(btc_returns):
    """
    Each week: rank tokens by 14d return, long top 3, short bottom 3.
    Dollar-neutral (equal weight each side).
    """
    print(f"\n  [C] Cross-Token Momentum Spread (Long 3 / Short 3)")

    # Build universe: tokens with enough history
    spot_dir = DATA_DIR / 'spot/1h_cache/'
    perp_dir = DATA_DIR / 'perp/1h_cache/'

    # Use tokens that have BOTH spot and perp (can short via perp)
    spot_files = {f.stem.replace('_1h', ''): f for f in spot_dir.iterdir() if f.suffix == '.parquet'}
    perp_files = {f.stem.replace('_1h', ''): f for f in perp_dir.iterdir() if f.suffix == '.parquet'}
    common_tokens = sorted(set(spot_files.keys()) & set(perp_files.keys()))

    # Filter: need at least 2 years of data and exclude stablecoins/memes with weird names
    exclude = {'PAXG', 'WLFI', '币安人生'}
    valid_tokens = []
    close_dict = {}

    for token in common_tokens:
        if token in exclude:
            continue
        df = pd.read_parquet(spot_files[token])
        df.index = pd.to_datetime(df.index)
        duration = (df.index.max() - df.index.min()).days
        if duration < 730:  # 2 years minimum
            continue
        valid_tokens.append(token)
        close_dict[token] = df['close']

    print(f"    Universe: {len(valid_tokens)} tokens with 2+ years of spot+perp data")
    if len(valid_tokens) < 10:
        print(f"    SKIP: need at least 10 tokens, got {len(valid_tokens)}")
        return None, None

    # Build panel of close prices
    panel = pd.DataFrame(close_dict)
    panel = panel.sort_index()
    panel = panel.dropna(how='all')

    # Need at least 8 non-null tokens per row for ranking
    min_tokens_per_row = 8
    panel = panel.loc[panel.notna().sum(axis=1) >= min_tokens_per_row]

    print(f"    Panel: {len(panel)} bars x {panel.shape[1]} tokens")

    # IS/OOS split
    valid_start = WARMUP_BARS
    valid_panel = panel.iloc[valid_start:]
    n_valid = len(valid_panel)
    is_end = int(n_valid * IS_FRACTION)
    oos_start_idx = valid_panel.index[is_end]

    oos_panel = panel.loc[oos_start_idx:]
    print(f"    OOS: {len(oos_panel)} bars, {oos_panel.index[0].date()} to {oos_panel.index[-1].date()}")

    # 14-day lookback = 336 hourly bars
    lookback = 14 * 24
    rebalance = 168  # weekly

    # Compute returns for ranking
    returns_14d = oos_panel.pct_change(lookback)

    # Trading simulation
    equity = 1.0
    equity_series = []
    trades = []
    current_longs = []
    current_shorts = []
    entry_equity = None
    last_rebal = -rebalance  # force first rebalance

    top_n = 3
    bottom_n = 3

    for i in range(len(oos_panel)):
        ts = oos_panel.index[i]

        # Hourly PnL from positions
        if i > 0 and (len(current_longs) > 0 or len(current_shorts) > 0):
            pnl = 0.0
            n_legs = len(current_longs) + len(current_shorts)
            weight = 1.0 / n_legs if n_legs > 0 else 0

            for tok in current_longs:
                if tok in oos_panel.columns:
                    prev = oos_panel[tok].iloc[i-1]
                    curr = oos_panel[tok].iloc[i]
                    if pd.notna(prev) and pd.notna(curr) and prev > 0:
                        pnl += weight * (curr / prev - 1)

            for tok in current_shorts:
                if tok in oos_panel.columns:
                    prev = oos_panel[tok].iloc[i-1]
                    curr = oos_panel[tok].iloc[i]
                    if pd.notna(prev) and pd.notna(curr) and prev > 0:
                        pnl -= weight * (curr / prev - 1)

            equity *= (1 + pnl)

        # Rebalance weekly
        if i - last_rebal >= rebalance:
            if i < lookback:
                equity_series.append(equity)
                continue

            rets = returns_14d.iloc[i]
            valid_rets = rets.dropna()

            if len(valid_rets) < top_n + bottom_n + 2:
                equity_series.append(equity)
                continue

            # Exclude BTC from universe (used as benchmark)
            if 'BTC' in valid_rets.index:
                valid_rets = valid_rets.drop('BTC')

            ranked = valid_rets.sort_values(ascending=False)
            new_longs = ranked.head(top_n).index.tolist()
            new_shorts = ranked.tail(bottom_n).index.tolist()

            # Count turnover for costs
            prev_set = set(current_longs + current_shorts)
            new_set = set(new_longs + new_shorts)
            turnover_legs = len(prev_set.symmetric_difference(new_set))

            if turnover_legs > 0:
                # Cost per leg traded
                equity *= (1 - turnover_legs * COST_FRAC)

            # If we had positions before and now changed, record trade
            if len(current_longs) > 0 or len(current_shorts) > 0:
                if entry_equity is not None:
                    trade_pnl = equity / entry_equity - 1
                    trades.append({
                        'pnl': trade_pnl,
                        'bars_held': rebalance,
                        'reason': 'rebalance',
                        'longs': current_longs,
                        'shorts': current_shorts,
                    })

            current_longs = new_longs
            current_shorts = new_shorts
            entry_equity = equity
            last_rebal = i

        equity_series.append(equity)

    # Record final period
    if entry_equity is not None and entry_equity != equity:
        trade_pnl = equity / entry_equity - 1
        trades.append({
            'pnl': trade_pnl,
            'bars_held': len(oos_panel) - last_rebal,
            'reason': 'end_of_data',
            'longs': current_longs,
            'shorts': current_shorts,
        })

    equity_curve = pd.Series(equity_series, index=oos_panel.index[:len(equity_series)])
    metrics = compute_metrics(equity_curve, btc_returns, trades, "C: XS Momentum L3/S3")

    if metrics:
        kills = check_kill_criteria(metrics)
        print(f"    Sharpe={metrics['sharpe']}, Return={metrics['total_return_pct']}%, "
              f"MaxDD={metrics['max_dd_pct']}%, Trades={metrics['n_trades']}, "
              f"WR={metrics['win_rate_pct']}%, BTC_corr={metrics['btc_correlation']}")
        if kills:
            print(f"    KILLED: {'; '.join(kills)}")
        else:
            print(f"    PASS all kill criteria")

    return metrics, kills if metrics else None


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 80)
    print("PAIRS TRADING / STATISTICAL ARBITRAGE BACKTEST")
    print("=" * 80)

    btc_returns = load_btc_returns()
    all_results = []

    # ── Strategy A: Spot-Spot Pairs ──────────────────────────────────────────
    print("\n" + "─" * 60)
    print("STRATEGY A: Spot-Spot Pairs (Cointegration)")
    print("─" * 60)

    pairs = [
        ('ETH/BTC', 'ETH', 'BTC'),
        ('SOL/ETH', 'SOL', 'ETH'),
        ('BNB/BTC', 'BNB', 'BTC'),
        ('DOGE/XRP', 'DOGE', 'XRP'),
    ]

    for pair_name, token_a, token_b in pairs:
        metrics, kills = run_spot_spot_pairs(pair_name, token_a, token_b, btc_returns)
        if metrics:
            metrics['kills'] = kills
            all_results.append(metrics)

    # ── Strategy B: Spot-Perp Basis Trade ────────────────────────────────────
    print("\n" + "─" * 60)
    print("STRATEGY B: Spot-Perp Basis Trade (Cash & Carry)")
    print("─" * 60)

    basis_configs = [
        ('BTC', 10),   # 0.1%
        ('BTC', 20),   # 0.2%
        ('BTC', 50),   # 0.5%
        ('ETH', 10),
        ('ETH', 20),
        ('ETH', 50),
    ]

    for token, threshold_bps in basis_configs:
        metrics, kills = run_basis_trade(token, threshold_bps, btc_returns)
        if metrics:
            metrics['kills'] = kills
            all_results.append(metrics)

    # ── Strategy C: Cross-Token Momentum Spread ──────────────────────────────
    print("\n" + "─" * 60)
    print("STRATEGY C: Cross-Token Momentum Spread")
    print("─" * 60)

    metrics, kills = run_cross_momentum_spread(btc_returns)
    if metrics:
        metrics['kills'] = kills
        all_results.append(metrics)

    # ── Summary ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    passed = []
    killed = []

    for r in all_results:
        kills = r.pop('kills', [])
        status = "PASS" if not kills else "KILLED"
        print(f"\n  [{status}] {r['strategy']}")
        print(f"    Sharpe={r['sharpe']}, Return={r['total_return_pct']}%, "
              f"MaxDD={r['max_dd_pct']}%, Trades={r['n_trades']}, "
              f"WR={r['win_rate_pct']}%, BTC_corr={r['btc_correlation']}, "
              f"PF={r['profit_factor']}")
        if kills:
            print(f"    Kills: {'; '.join(kills)}")
            killed.append({'metrics': r, 'kills': kills})
        else:
            passed.append(r)

    print(f"\n\nPassed: {len(passed)} / {len(all_results)}")
    print(f"Killed: {len(killed)} / {len(all_results)}")

    # Save results as JSON
    results_json = {
        'timestamp': datetime.now().isoformat(),
        'kill_criteria': {
            'sharpe_min': 0.3,
            'max_dd_max': -20,
            'btc_corr_max': 0.3,
            'min_trades': 30,
            'win_rate_min': 45,
        },
        'passed': passed,
        'killed': killed,
        'all_results': all_results,
    }

    json_path = OUTPUT_DIR / 'pairs_statarb_results.json'
    with open(json_path, 'w') as f:
        json.dump(results_json, f, indent=2, default=str)
    print(f"\nJSON results saved to {json_path}")

    # Generate markdown report
    generate_markdown_report(all_results, passed, killed)

    return results_json


def generate_markdown_report(all_results, passed, killed):
    """Generate detailed markdown results file."""
    lines = [
        "# Pairs Trading / Statistical Arbitrage Results",
        "",
        f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
        "",
        "## Kill Criteria",
        "",
        "| Criterion | Threshold |",
        "|-----------|-----------|",
        "| Sharpe Ratio | >= 0.3 |",
        "| Max Drawdown | <= 20% |",
        "| BTC Correlation | <= 0.3 (absolute) |",
        "| Min Trades | >= 30 |",
        "| Win Rate | >= 45% |",
        "",
        f"## Summary: {len(passed)} PASS / {len(killed)} KILLED out of {len(all_results)} variants",
        "",
    ]

    # Results table
    lines.extend([
        "## All Results",
        "",
        "| Strategy | Sharpe | Return% | MaxDD% | Trades | WR% | Avg PnL% | PF | BTC Corr | Status |",
        "|----------|--------|---------|--------|--------|-----|----------|-----|----------|--------|",
    ])

    for r in all_results:
        # Determine status
        kills = check_kill_criteria(r)
        status = "PASS" if not kills else "KILLED"
        lines.append(
            f"| {r['strategy']} | {r['sharpe']} | {r['total_return_pct']} | "
            f"{r['max_dd_pct']} | {r['n_trades']} | {r['win_rate_pct']} | "
            f"{r['avg_trade_pnl_pct']} | {r['profit_factor']} | {r['btc_correlation']} | {status} |"
        )

    lines.append("")

    # Detailed analysis per strategy type
    lines.extend([
        "## Strategy A: Spot-Spot Pairs (Cointegration)",
        "",
        "Pairs tested: ETH/BTC, SOL/ETH, BNB/BTC, DOGE/XRP",
        "",
        "**Method:**",
        "- spread = log(price_A) - beta * log(price_B), beta from 60d rolling OLS",
        "- Z-score spread with 30d rolling window",
        "- Entry: |z| > 2, Exit: z crosses 0, Stop: |z| > 4",
        "- Max hold: 168 bars (1 week)",
        "",
    ])

    a_results = [r for r in all_results if r['strategy'].startswith('A:')]
    if a_results:
        for r in a_results:
            kills = check_kill_criteria(r)
            status = "PASS" if not kills else "KILLED"
            lines.append(f"**{r['strategy']}** [{status}]")
            if kills:
                lines.append(f"  - Kill reasons: {'; '.join(kills)}")
            lines.append(f"  - Sharpe: {r['sharpe']}, Return: {r['total_return_pct']}%, "
                         f"MaxDD: {r['max_dd_pct']}%, Trades: {r['n_trades']}")
            lines.append("")
    else:
        lines.append("No results generated.")
        lines.append("")

    lines.extend([
        "## Strategy B: Spot-Perp Basis Trade (Cash & Carry)",
        "",
        "Tokens: BTC, ETH. Thresholds: 10bps (0.1%), 20bps (0.2%), 50bps (0.5%)",
        "",
        "**Method:**",
        "- basis = (perp_price - spot_price) / spot_price",
        "- When basis > threshold: long spot + short perp (earn convergence + funding)",
        "- When basis < -threshold: short spot + long perp",
        "- Exit when basis reverts near 0",
        "- Max hold: 2016 bars (12 weeks)",
        "- Includes funding rate income/cost from perp data",
        "",
    ])

    b_results = [r for r in all_results if r['strategy'].startswith('B:')]
    if b_results:
        for r in b_results:
            kills = check_kill_criteria(r)
            status = "PASS" if not kills else "KILLED"
            lines.append(f"**{r['strategy']}** [{status}]")
            if kills:
                lines.append(f"  - Kill reasons: {'; '.join(kills)}")
            lines.append(f"  - Sharpe: {r['sharpe']}, Return: {r['total_return_pct']}%, "
                         f"MaxDD: {r['max_dd_pct']}%, Trades: {r['n_trades']}")
            lines.append("")
    else:
        lines.append("No results generated.")
        lines.append("")

    lines.extend([
        "## Strategy C: Cross-Token Momentum Spread (Long-Short)",
        "",
        "**Method:**",
        "- Weekly: rank all tokens by 14d return",
        "- Long top 3, short bottom 3 (equal dollar weight)",
        "- Excludes BTC (used as correlation benchmark)",
        "- Dollar-neutral: equal weight long and short legs",
        "",
    ])

    c_results = [r for r in all_results if r['strategy'].startswith('C:')]
    if c_results:
        for r in c_results:
            kills = check_kill_criteria(r)
            status = "PASS" if not kills else "KILLED"
            lines.append(f"**{r['strategy']}** [{status}]")
            if kills:
                lines.append(f"  - Kill reasons: {'; '.join(kills)}")
            lines.append(f"  - Sharpe: {r['sharpe']}, Return: {r['total_return_pct']}%, "
                         f"MaxDD: {r['max_dd_pct']}%, Trades: {r['n_trades']}")
            lines.append("")
    else:
        lines.append("No results generated.")
        lines.append("")

    # Conclusions
    lines.extend([
        "## Conclusions",
        "",
    ])

    if passed:
        lines.append("### Strategies passing all kill criteria:")
        for r in passed:
            lines.append(f"- **{r['strategy']}**: Sharpe {r['sharpe']}, "
                         f"Return {r['total_return_pct']}%, MaxDD {r['max_dd_pct']}%, "
                         f"BTC corr {r['btc_correlation']}")
        lines.append("")
    else:
        lines.append("**No strategies passed all kill criteria.**")
        lines.append("")

    # Find the best strategy even among killed ones
    if all_results:
        best = max(all_results, key=lambda x: x['sharpe'])
        lines.append(f"### Best strategy by Sharpe: {best['strategy']} (Sharpe={best['sharpe']})")
        lines.append("")

        # Market neutrality analysis
        lines.append("### Market Neutrality Analysis")
        for r in all_results:
            corr = r['btc_correlation']
            neutral = "YES" if corr is not None and abs(corr) < 0.3 else "NO"
            lines.append(f"- {r['strategy']}: BTC corr = {corr} -> Neutral: {neutral}")
        lines.append("")

    md_path = OUTPUT_DIR / 'pairs_statarb_results.md'
    with open(md_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f"Markdown report saved to {md_path}")


if __name__ == '__main__':
    main()
