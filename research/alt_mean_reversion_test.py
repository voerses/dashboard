#!/workspace/venv/bin/python
"""
Alt-Coin Mean-Reversion Strategy Test
======================================

All trend-following strategies fail walk-forward validation on altcoins (too choppy
for EMA/momentum). But altcoin choppiness implies potential mean-reversion opportunities.
If alts overshoot and revert, we can trade that.

Strategy Variants:
  1. Bollinger Band MR: Long below lower BB(20d, 2σ), short above upper BB. Exit at SMA.
  2. RSI MR: Long when RSI(14d) < 30, short when RSI > 70. Exit at RSI 50.
  3. Z-score MR: Long when z-score(30d) < -2, short when > +2. Exit at 0.
  4. VWAP MR (Weekly): Long below 168h VWAP * 0.97, short above VWAP * 1.03. Exit at VWAP.

Each variant tested LONG-ONLY and LONG+SHORT.

Tokens: ETH, SOL, BNB, XRP, DOGE, ADA, LINK, DOT
Benchmark: BTC buy-and-hold

Temporal split: 70% IS / 30% OOS (after 60-day warmup)
Trading costs: 10 bps per trade
Stop loss: 5% from entry
Max hold: 168 bars (1 week)

Kill criteria (OOS):
  - Mean Sharpe < 0.2
  - Win rate < 45%
  - < 50 trades per token
  - Max DD > 25%
"""

import pandas as pd
import numpy as np
import warnings
from pathlib import Path
from datetime import datetime

warnings.filterwarnings('ignore')

BASE_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = BASE_DIR / 'data'
OUTPUT_DIR = BASE_DIR / 'research'

COST_BPS = 10  # one-way cost in basis points
STOP_LOSS_PCT = 0.05
MAX_HOLD_BARS = 168  # 1 week in hours
WARMUP_BARS = 60 * 24  # 60 days in hours (1440 bars)
IS_FRAC = 0.70  # 70% in-sample

TARGET_TOKENS = ['ETH', 'SOL', 'BNB', 'XRP', 'DOGE', 'ADA', 'LINK', 'DOT']

VARIANT_NAMES = {
    'bb_long': 'Bollinger Band MR (Long Only)',
    'bb_ls': 'Bollinger Band MR (Long+Short)',
    'rsi_long': 'RSI MR (Long Only)',
    'rsi_ls': 'RSI MR (Long+Short)',
    'zscore_long': 'Z-Score MR (Long Only)',
    'zscore_ls': 'Z-Score MR (Long+Short)',
    'vwap_long': 'VWAP MR (Long Only)',
    'vwap_ls': 'VWAP MR (Long+Short)',
}


# ── 1. Data Loading ──────────────────────────────────────────────────────────

def load_spot_1h(token):
    """Load spot 1h data for a token."""
    path = DATA_DIR / f'spot/1h_cache/{token}_1h.parquet'
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    df.index.name = 'timestamp'
    return df


# ── 2. Indicator Computation ─────────────────────────────────────────────────

def compute_bollinger(df, period=480, num_std=2.0):
    """Bollinger Bands: 20 days = 480 hourly bars."""
    close = df['close']
    sma = close.rolling(period).mean()
    std = close.rolling(period).std()
    df['bb_sma'] = sma
    df['bb_upper'] = sma + num_std * std
    df['bb_lower'] = sma - num_std * std
    return df


def compute_rsi(df, period=336):
    """RSI: 14 days = 336 hourly bars."""
    delta = df['close'].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    df['rsi'] = 100 - (100 / (1 + rs))
    return df


def compute_zscore(df, period=720):
    """Z-score: 30 days = 720 hourly bars."""
    close = df['close']
    mean = close.rolling(period).mean()
    std = close.rolling(period).std()
    df['zscore'] = (close - mean) / std
    return df


def compute_vwap_weekly(df, period=168):
    """VWAP over rolling 168 hours (1 week)."""
    tp = (df['high'] + df['low'] + df['close']) / 3.0
    vol = df['volume']
    df['vwap'] = (tp * vol).rolling(period).sum() / vol.rolling(period).sum()
    return df


# ── 3. Signal Generation ─────────────────────────────────────────────────────

def generate_bb_signals(df, long_only=True):
    """Bollinger Band mean reversion signals.
    Long: close < lower BB, exit: close > SMA
    Short: close > upper BB, exit: close < SMA
    """
    close = df['close'].values
    lower = df['bb_lower'].values
    upper = df['bb_upper'].values
    sma = df['bb_sma'].values
    n = len(df)

    signals = np.zeros(n, dtype=int)  # 0=flat, 1=long, -1=short

    for i in range(1, n):
        if close[i] < lower[i] and not np.isnan(lower[i]):
            signals[i] = 1  # long entry signal
        elif not long_only and close[i] > upper[i] and not np.isnan(upper[i]):
            signals[i] = -1  # short entry signal

    exit_long = close > sma
    exit_short = close < sma

    return signals, exit_long, exit_short


def generate_rsi_signals(df, long_only=True):
    """RSI mean reversion signals.
    Long: RSI < 30, exit: RSI > 50
    Short: RSI > 70, exit: RSI < 50
    """
    rsi = df['rsi'].values
    n = len(df)

    signals = np.zeros(n, dtype=int)

    for i in range(n):
        if not np.isnan(rsi[i]):
            if rsi[i] < 30:
                signals[i] = 1
            elif not long_only and rsi[i] > 70:
                signals[i] = -1

    exit_long = rsi > 50
    exit_short = rsi < 50

    return signals, exit_long, exit_short


def generate_zscore_signals(df, long_only=True):
    """Z-score mean reversion signals.
    Long: z < -2, exit: z > 0
    Short: z > +2, exit: z < 0
    """
    z = df['zscore'].values
    n = len(df)

    signals = np.zeros(n, dtype=int)

    for i in range(n):
        if not np.isnan(z[i]):
            if z[i] < -2.0:
                signals[i] = 1
            elif not long_only and z[i] > 2.0:
                signals[i] = -1

    exit_long = z > 0
    exit_short = z < 0

    return signals, exit_long, exit_short


def generate_vwap_signals(df, long_only=True):
    """VWAP mean reversion signals.
    Long: close < VWAP * 0.97, exit: close > VWAP
    Short: close > VWAP * 1.03, exit: close < VWAP
    """
    close = df['close'].values
    vwap = df['vwap'].values
    n = len(df)

    signals = np.zeros(n, dtype=int)

    for i in range(n):
        if not np.isnan(vwap[i]):
            if close[i] < vwap[i] * 0.97:
                signals[i] = 1
            elif not long_only and close[i] > vwap[i] * 1.03:
                signals[i] = -1

    exit_long = close > vwap
    exit_short = close < vwap

    return signals, exit_long, exit_short


# ── 4. Trade Simulation ──────────────────────────────────────────────────────

def simulate_trades(df, entry_signals, exit_long, exit_short, cost_bps=COST_BPS,
                    stop_loss=STOP_LOSS_PCT, max_hold=MAX_HOLD_BARS):
    """
    Simulate trades based on entry signals and exit conditions.

    entry_signals: array of 0, 1 (long), -1 (short) at each bar
    exit_long: boolean array - when True, exit long positions
    exit_short: boolean array - when True, exit short positions

    Returns list of trade dicts with entry/exit info.
    """
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    timestamps = df.index
    n = len(close)
    cost_frac = cost_bps / 10000.0

    trades = []
    in_trade = False
    direction = 0
    entry_price = 0.0
    entry_bar = 0

    for i in range(n):
        if in_trade:
            hold_bars = i - entry_bar
            pnl_pct = 0.0

            # Check stop loss
            if direction == 1:
                # Long: check if low hit stop
                worst = low[i]
                worst_pnl = (worst - entry_price) / entry_price
                if worst_pnl <= -stop_loss:
                    # Stopped out
                    exit_price = entry_price * (1 - stop_loss)
                    pnl_pct = -stop_loss - 2 * cost_frac
                    trades.append({
                        'entry_time': timestamps[entry_bar],
                        'exit_time': timestamps[i],
                        'direction': direction,
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'pnl_pct': pnl_pct,
                        'hold_bars': hold_bars,
                        'exit_reason': 'stop',
                    })
                    in_trade = False
                    continue

                # Check exit signal
                if exit_long[i] or hold_bars >= max_hold:
                    exit_price = close[i]
                    pnl_pct = (exit_price - entry_price) / entry_price - 2 * cost_frac
                    reason = 'signal' if exit_long[i] else 'timeout'
                    trades.append({
                        'entry_time': timestamps[entry_bar],
                        'exit_time': timestamps[i],
                        'direction': direction,
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'pnl_pct': pnl_pct,
                        'hold_bars': hold_bars,
                        'exit_reason': reason,
                    })
                    in_trade = False

            elif direction == -1:
                # Short: check if high hit stop
                worst = high[i]
                worst_pnl = (entry_price - worst) / entry_price
                if worst_pnl <= -stop_loss:
                    exit_price = entry_price * (1 + stop_loss)
                    pnl_pct = -stop_loss - 2 * cost_frac
                    trades.append({
                        'entry_time': timestamps[entry_bar],
                        'exit_time': timestamps[i],
                        'direction': direction,
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'pnl_pct': pnl_pct,
                        'hold_bars': hold_bars,
                        'exit_reason': 'stop',
                    })
                    in_trade = False
                    continue

                # Check exit signal
                if exit_short[i] or hold_bars >= max_hold:
                    exit_price = close[i]
                    pnl_pct = (entry_price - exit_price) / entry_price - 2 * cost_frac
                    reason = 'signal' if exit_short[i] else 'timeout'
                    trades.append({
                        'entry_time': timestamps[entry_bar],
                        'exit_time': timestamps[i],
                        'direction': direction,
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'pnl_pct': pnl_pct,
                        'hold_bars': hold_bars,
                        'exit_reason': reason,
                    })
                    in_trade = False

        # Check for new entry (only if not in a trade)
        if not in_trade and entry_signals[i] != 0:
            in_trade = True
            direction = entry_signals[i]
            entry_price = close[i]
            entry_bar = i

    return trades


# ── 5. Metrics Computation ───────────────────────────────────────────────────

def compute_metrics(trades, df):
    """Compute strategy metrics from trade list."""
    if len(trades) == 0:
        return {
            'total_return': 0.0,
            'sharpe': 0.0,
            'max_dd': 0.0,
            'win_rate': 0.0,
            'avg_trade': 0.0,
            'num_trades': 0,
            'avg_hold_bars': 0,
            'stop_rate': 0.0,
        }

    pnls = np.array([t['pnl_pct'] for t in trades])
    num_trades = len(pnls)
    winners = np.sum(pnls > 0)
    win_rate = winners / num_trades if num_trades > 0 else 0

    # Compute equity curve for Sharpe and drawdown
    equity = np.cumprod(1 + pnls)
    total_return = equity[-1] - 1

    # Approximate Sharpe: annualize based on average holding period
    avg_hold = np.mean([t['hold_bars'] for t in trades])
    trades_per_year = 8760 / avg_hold if avg_hold > 0 else 0  # 8760 hours/year
    mean_ret = np.mean(pnls)
    std_ret = np.std(pnls)
    sharpe = (mean_ret / std_ret) * np.sqrt(trades_per_year) if std_ret > 0 else 0

    # Max drawdown from equity curve
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    max_dd = np.min(dd)

    # Stop rate
    stops = sum(1 for t in trades if t['exit_reason'] == 'stop')
    stop_rate = stops / num_trades

    return {
        'total_return': total_return,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'win_rate': win_rate,
        'avg_trade': mean_ret,
        'num_trades': num_trades,
        'avg_hold_bars': avg_hold,
        'stop_rate': stop_rate,
    }


def compute_btc_correlation(trades, btc_df):
    """Compute correlation between strategy returns and BTC buy-and-hold."""
    if len(trades) < 10 or btc_df is None:
        return np.nan

    # Build monthly returns for strategy
    trade_df = pd.DataFrame(trades)
    trade_df['exit_time'] = pd.to_datetime(trade_df['exit_time'])
    trade_df = trade_df.set_index('exit_time')
    monthly_strat = trade_df['pnl_pct'].resample('ME').sum()

    # BTC monthly returns
    btc_monthly = btc_df['close'].resample('ME').last().pct_change().dropna()

    # Align
    common = monthly_strat.index.intersection(btc_monthly.index)
    if len(common) < 6:
        return np.nan

    return monthly_strat.loc[common].corr(btc_monthly.loc[common])


# ── 6. Main Backtest Runner ──────────────────────────────────────────────────

def run_backtest_variant(df, variant_key, warmup_bars=WARMUP_BARS):
    """Run a single variant on prepared 1H data. Returns IS and OOS trade lists."""

    # Apply warmup
    df_warm = df.iloc[warmup_bars:].copy()
    n = len(df_warm)
    is_end = int(n * IS_FRAC)

    # Compute all indicators on full data (including warmup for rolling windows)
    df_full = df.copy()
    df_full = compute_bollinger(df_full)
    df_full = compute_rsi(df_full)
    df_full = compute_zscore(df_full)
    df_full = compute_vwap_weekly(df_full)

    # Slice to post-warmup
    df_work = df_full.iloc[warmup_bars:].copy()

    # Determine variant
    variant_type = variant_key.split('_')[0]  # bb, rsi, zscore, vwap
    long_only = variant_key.endswith('_long')

    if variant_type == 'bb':
        signals, exit_l, exit_s = generate_bb_signals(df_work, long_only=long_only)
    elif variant_type == 'rsi':
        signals, exit_l, exit_s = generate_rsi_signals(df_work, long_only=long_only)
    elif variant_type == 'zscore':
        signals, exit_l, exit_s = generate_zscore_signals(df_work, long_only=long_only)
    elif variant_type == 'vwap':
        signals, exit_l, exit_s = generate_vwap_signals(df_work, long_only=long_only)
    else:
        raise ValueError(f"Unknown variant: {variant_key}")

    # Split IS/OOS
    df_is = df_work.iloc[:is_end]
    df_oos = df_work.iloc[is_end:]
    signals_is = signals[:is_end]
    signals_oos = signals[is_end:]
    exit_l_is = exit_l[:is_end]
    exit_l_oos = exit_l[is_end:]
    exit_s_is = exit_s[:is_end]
    exit_s_oos = exit_s[is_end:]

    trades_is = simulate_trades(df_is, signals_is, exit_l_is, exit_s_is)
    trades_oos = simulate_trades(df_oos, signals_oos, exit_l_oos, exit_s_oos)

    metrics_is = compute_metrics(trades_is, df_is)
    metrics_oos = compute_metrics(trades_oos, df_oos)

    return {
        'is': metrics_is,
        'oos': metrics_oos,
        'trades_is': trades_is,
        'trades_oos': trades_oos,
        'is_period': f"{df_is.index[0].date()} to {df_is.index[-1].date()}" if len(df_is) > 0 else "N/A",
        'oos_period': f"{df_oos.index[0].date()} to {df_oos.index[-1].date()}" if len(df_oos) > 0 else "N/A",
    }


# ── 7. Kill Criteria Evaluation ──────────────────────────────────────────────

def evaluate_kill_criteria(oos_metrics_by_token):
    """Evaluate kill criteria for a variant across tokens."""
    sharpes = [m['sharpe'] for m in oos_metrics_by_token.values()]
    win_rates = [m['win_rate'] for m in oos_metrics_by_token.values()]
    max_dds = [m['max_dd'] for m in oos_metrics_by_token.values()]
    num_trades_list = [m['num_trades'] for m in oos_metrics_by_token.values()]

    mean_sharpe = np.mean(sharpes)
    mean_win_rate = np.mean(win_rates)
    mean_max_dd = np.mean(max_dds)
    tokens_positive_sharpe = sum(1 for s in sharpes if s > 0)
    min_trades = min(num_trades_list) if num_trades_list else 0

    kills = []
    if mean_sharpe < 0.2:
        kills.append(f"Mean Sharpe {mean_sharpe:.3f} < 0.2")
    if mean_win_rate < 0.45:
        kills.append(f"Mean Win Rate {mean_win_rate:.1%} < 45%")
    if min_trades < 50:
        kills.append(f"Min trades {min_trades} < 50")
    if mean_max_dd < -0.25:
        kills.append(f"Mean MaxDD {mean_max_dd:.1%} > 25%")

    verdict = "KILL" if kills else "PASS"

    return {
        'mean_sharpe': mean_sharpe,
        'mean_win_rate': mean_win_rate,
        'mean_max_dd': mean_max_dd,
        'tokens_positive_sharpe': tokens_positive_sharpe,
        'total_tokens': len(sharpes),
        'min_trades': min_trades,
        'kills': kills,
        'verdict': verdict,
    }


# ── 8. Main Execution ────────────────────────────────────────────────────────

def main():
    print("=" * 80)
    print("ALT-COIN MEAN-REVERSION STRATEGY TEST")
    print("=" * 80)

    # Load BTC for correlation benchmark
    btc_df = load_spot_1h('BTC')

    # Store all results
    all_results = {}  # {variant: {token: {is: metrics, oos: metrics}}}

    for variant_key in VARIANT_NAMES:
        print(f"\n{'─' * 60}")
        print(f"Variant: {VARIANT_NAMES[variant_key]}")
        print(f"{'─' * 60}")
        all_results[variant_key] = {}

        for token in TARGET_TOKENS:
            df = load_spot_1h(token)
            if df is None:
                print(f"  {token}: No data, skipping")
                continue

            result = run_backtest_variant(df, variant_key)
            all_results[variant_key][token] = result

            oos = result['oos']
            print(f"  {token}: OOS Sharpe={oos['sharpe']:+.3f}  "
                  f"WinRate={oos['win_rate']:.1%}  "
                  f"MaxDD={oos['max_dd']:.1%}  "
                  f"Trades={oos['num_trades']:>4d}  "
                  f"AvgTrade={oos['avg_trade']:+.4f}  "
                  f"TotRet={oos['total_return']:+.2%}  "
                  f"StopRate={oos['stop_rate']:.1%}")

    # ── Aggregate OOS Analysis ─────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("AGGREGATE OOS ANALYSIS")
    print("=" * 80)

    variant_summaries = {}
    for variant_key in VARIANT_NAMES:
        oos_metrics = {token: all_results[variant_key][token]['oos']
                       for token in all_results[variant_key]}
        summary = evaluate_kill_criteria(oos_metrics)

        # BTC correlation
        btc_corrs = []
        for token in all_results[variant_key]:
            corr = compute_btc_correlation(
                all_results[variant_key][token]['trades_oos'], btc_df)
            if not np.isnan(corr):
                btc_corrs.append(corr)
        summary['mean_btc_corr'] = np.mean(btc_corrs) if btc_corrs else np.nan

        variant_summaries[variant_key] = summary

        verdict_str = f"**{summary['verdict']}**"
        print(f"\n{VARIANT_NAMES[variant_key]}:")
        print(f"  Mean Sharpe: {summary['mean_sharpe']:+.3f}  |  "
              f"Positive Sharpe Tokens: {summary['tokens_positive_sharpe']}/{summary['total_tokens']}  |  "
              f"Mean MaxDD: {summary['mean_max_dd']:.1%}")
        print(f"  Mean Win Rate: {summary['mean_win_rate']:.1%}  |  "
              f"Min Trades: {summary['min_trades']}  |  "
              f"BTC Corr: {summary['mean_btc_corr']:+.3f}")
        print(f"  Verdict: {summary['verdict']}")
        if summary['kills']:
            for k in summary['kills']:
                print(f"    KILL reason: {k}")

    # ── Find Best Variant (exclude degenerate variants with 0 trades) ─────
    viable = {v: s for v, s in variant_summaries.items() if s['min_trades'] > 0}
    if viable:
        best_variant = max(viable, key=lambda v: viable[v]['mean_sharpe'])
    else:
        best_variant = max(variant_summaries, key=lambda v: variant_summaries[v]['mean_sharpe'])
    print(f"\nBest Variant: {VARIANT_NAMES[best_variant]} "
          f"(Mean OOS Sharpe: {variant_summaries[best_variant]['mean_sharpe']:+.3f})")

    # ── Write Results Markdown ─────────────────────────────────────────────
    write_results_md(all_results, variant_summaries, best_variant)
    print(f"\nResults written to {OUTPUT_DIR / 'alt_mean_reversion_results.md'}")


def write_results_md(all_results, variant_summaries, best_variant):
    """Write comprehensive results to markdown file."""
    lines = []
    lines.append("# Alt-Coin Mean-Reversion Strategy Test Results")
    lines.append(f"\nRun: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"\nTokens: {', '.join(TARGET_TOKENS)}")
    lines.append(f"Cost: {COST_BPS} bps per trade | Stop Loss: {STOP_LOSS_PCT:.0%} | "
                 f"Max Hold: {MAX_HOLD_BARS} bars (1 week)")
    lines.append(f"Split: {IS_FRAC:.0%} IS / {1-IS_FRAC:.0%} OOS | Warmup: {WARMUP_BARS} bars ({WARMUP_BARS//24}d)")

    # ── Per-Variant, Per-Token OOS Table ───────────────────────────────────
    lines.append("\n## Per-Token OOS Metrics by Variant\n")

    for variant_key in VARIANT_NAMES:
        lines.append(f"### {VARIANT_NAMES[variant_key]}\n")

        # Period info from first available token
        first_token = list(all_results[variant_key].keys())[0]
        lines.append(f"IS Period: {all_results[variant_key][first_token]['is_period']}  |  "
                     f"OOS Period: {all_results[variant_key][first_token]['oos_period']}\n")

        lines.append("| Token | Sharpe | Win Rate | MaxDD | Trades | Avg Trade | Total Ret | Stop Rate | Verdict |")
        lines.append("|-------|--------|----------|-------|--------|-----------|-----------|-----------|---------|")

        for token in TARGET_TOKENS:
            if token not in all_results[variant_key]:
                continue
            m = all_results[variant_key][token]['oos']
            # Per-token kill
            token_kill = []
            if m['sharpe'] < 0:
                token_kill.append("Sharpe<0")
            if m['win_rate'] < 0.45:
                token_kill.append("WR<45%")
            if m['num_trades'] < 50:
                token_kill.append("Trades<50")
            if m['max_dd'] < -0.25:
                token_kill.append("DD>25%")
            tv = "PASS" if not token_kill else "KILL"

            lines.append(f"| {token} | {m['sharpe']:+.3f} | {m['win_rate']:.1%} | "
                         f"{m['max_dd']:.1%} | {m['num_trades']} | {m['avg_trade']:+.4f} | "
                         f"{m['total_return']:+.2%} | {m['stop_rate']:.1%} | {tv} |")

        lines.append("")

    # ── Variant Summary Table ──────────────────────────────────────────────
    lines.append("## Variant Summary (OOS Aggregates)\n")
    lines.append("| Variant | Mean Sharpe | Pos. Sharpe Tokens | Mean Win Rate | Mean MaxDD | Min Trades | BTC Corr | Verdict |")
    lines.append("|---------|-------------|-------------------|---------------|------------|------------|----------|---------|")

    for variant_key in VARIANT_NAMES:
        s = variant_summaries[variant_key]
        btc_str = f"{s['mean_btc_corr']:+.3f}" if not np.isnan(s['mean_btc_corr']) else "N/A"
        lines.append(f"| {VARIANT_NAMES[variant_key]} | {s['mean_sharpe']:+.3f} | "
                     f"{s['tokens_positive_sharpe']}/{s['total_tokens']} | {s['mean_win_rate']:.1%} | "
                     f"{s['mean_max_dd']:.1%} | {s['min_trades']} | {btc_str} | "
                     f"**{s['verdict']}** |")

    lines.append("")

    # ── Kill Criteria Detail ───────────────────────────────────────────────
    lines.append("## Kill Criteria Detail\n")
    lines.append("| Criterion | Threshold | Purpose |")
    lines.append("|-----------|-----------|---------|")
    lines.append("| Mean Sharpe | >= 0.2 | Minimum risk-adjusted return |")
    lines.append("| Win Rate | >= 45% | Minimum win frequency |")
    lines.append("| Min Trades/Token | >= 50 | Statistical significance |")
    lines.append("| Mean MaxDD | > -25% | Risk management |")
    lines.append("")

    for variant_key in VARIANT_NAMES:
        s = variant_summaries[variant_key]
        if s['kills']:
            lines.append(f"**{VARIANT_NAMES[variant_key]}** -- KILL reasons:")
            for k in s['kills']:
                lines.append(f"  - {k}")
        else:
            lines.append(f"**{VARIANT_NAMES[variant_key]}** -- PASS (all criteria met)")
        lines.append("")

    # ── Best Variant ───────────────────────────────────────────────────────
    lines.append("## Best Variant\n")
    bs = variant_summaries[best_variant]
    lines.append(f"**{VARIANT_NAMES[best_variant]}** with Mean OOS Sharpe {bs['mean_sharpe']:+.3f}")
    lines.append(f"- {bs['tokens_positive_sharpe']}/{bs['total_tokens']} tokens with positive Sharpe")
    lines.append(f"- Mean Win Rate: {bs['mean_win_rate']:.1%}")
    lines.append(f"- Mean MaxDD: {bs['mean_max_dd']:.1%}")
    btc_str = f"{bs['mean_btc_corr']:+.3f}" if not np.isnan(bs['mean_btc_corr']) else "N/A"
    lines.append(f"- BTC Correlation: {btc_str}")
    lines.append(f"- Verdict: **{bs['verdict']}**")
    lines.append("")

    # ── IS vs OOS Comparison ───────────────────────────────────────────────
    lines.append("## IS vs OOS Comparison (Sharpe)\n")
    lines.append("| Variant | IS Mean Sharpe | OOS Mean Sharpe | Degradation |")
    lines.append("|---------|---------------|-----------------|-------------|")

    for variant_key in VARIANT_NAMES:
        is_sharpes = [all_results[variant_key][t]['is']['sharpe']
                      for t in all_results[variant_key]]
        oos_sharpes = [all_results[variant_key][t]['oos']['sharpe']
                       for t in all_results[variant_key]]
        is_mean = np.mean(is_sharpes)
        oos_mean = np.mean(oos_sharpes)
        degrad = oos_mean - is_mean
        lines.append(f"| {VARIANT_NAMES[variant_key]} | {is_mean:+.3f} | {oos_mean:+.3f} | {degrad:+.3f} |")

    lines.append("")

    # ── Final Verdict ──────────────────────────────────────────────────────
    passing = [v for v, s in variant_summaries.items() if s['verdict'] == 'PASS']
    lines.append("## Final Verdict\n")
    if passing:
        lines.append(f"**{len(passing)} variant(s) PASS kill criteria:**")
        for v in passing:
            lines.append(f"- {VARIANT_NAMES[v]} (Sharpe: {variant_summaries[v]['mean_sharpe']:+.3f})")
        lines.append("\nRecommendation: Proceed to parameter optimization and walk-forward validation.")
    else:
        lines.append("**ALL variants KILLED.** Mean-reversion on altcoins does not meet minimum thresholds.")
        lines.append("\nRecommendation: Investigate alternative approaches:")
        lines.append("- Regime-conditioned MR (only trade in ranging regimes)")
        lines.append("- Pairs/spread MR (relative value rather than absolute)")
        lines.append("- Shorter timeframe MR (5m/15m bars)")
        lines.append("- Adaptive parameters (vary lookback with realized vol)")

    output_path = OUTPUT_DIR / 'alt_mean_reversion_results.md'
    output_path.write_text('\n'.join(lines))


if __name__ == '__main__':
    main()
