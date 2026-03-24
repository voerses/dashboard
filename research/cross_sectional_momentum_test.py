#!/workspace/venv/bin/python
"""
Cross-Sectional Momentum Backtest
===================================

Tests cross-sectional momentum strategies across crypto tokens as a diversifier
to the BTC-only V3 strategy (EMA 20/50 + overlays, Sharpe 0.56).

Strategy Variants:
  1. Simple XSMOM: Rank by N-day return, go long top 5 (equal weight)
  2. Risk-adjusted XSMOM: Rank by Sharpe (return/vol) instead of raw return
  3. Dual momentum: Long only where cross-sectional rank AND absolute momentum
     (price > SMA) agree

Lookback periods tested: 14d, 30d, 60d, 90d
Weekly rebalance (168 bars = 7 days * 24 hours)
10 bps total cost per trade
Universe: tokens with >= 3 years of data, sorted by avg volume

Kill criteria (OOS):
  - Sharpe < 0.3
  - MaxDD > 30%
  - Correlation > 0.7 with BTC buy-and-hold
"""

import pandas as pd
import numpy as np
import json
import warnings
from pathlib import Path
from datetime import datetime
from itertools import product

warnings.filterwarnings('ignore')

BASE_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = BASE_DIR / 'data'
OUTPUT_DIR = BASE_DIR / 'research'

COST_BPS = 10  # round-trip cost in basis points
COST_FRAC = COST_BPS / 10_000
REBALANCE_BARS = 168  # weekly = 7 * 24
WARMUP_BARS = 90 * 24  # 90 days in hourly bars
TOP_N = 5  # go long top N tokens
BOTTOM_N = 5  # avoid bottom N tokens
MIN_DURATION_DAYS = 1095  # 3 years minimum data
IS_FRACTION = 0.70  # 70% in-sample, 30% out-of-sample

LOOKBACK_DAYS = [14, 30, 60, 90]
VARIANTS = ['simple_xsmom', 'risk_adjusted_xsmom', 'dual_momentum']

# Annualization factor for hourly data
HOURS_PER_YEAR = 365.25 * 24
SQRT_HOURS_PER_YEAR = np.sqrt(HOURS_PER_YEAR)


# ── 1. Data Loading ──────────────────────────────────────────────────────────

def load_hourly_close(token):
    """Load spot 1h data for a token, return close prices."""
    path = DATA_DIR / f'spot/1h_cache/{token}_1h.parquet'
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    df.index.name = 'timestamp'
    return df[['close', 'volume']]


def build_universe():
    """Select tokens with >= 3 years of data, exclude BTC (used as benchmark).
    Return list of tokens sorted by average volume descending."""
    path = DATA_DIR / 'spot/1h_cache/'
    files = [f for f in path.iterdir() if f.suffix == '.parquet']

    candidates = []
    for f in files:
        token = f.stem.replace('_1h', '')
        df = pd.read_parquet(f)
        duration_days = (df.index.max() - df.index.min()).days
        avg_vol = df['volume'].mean()
        if duration_days >= MIN_DURATION_DAYS and token != 'BTC':
            candidates.append({
                'token': token,
                'duration_days': duration_days,
                'avg_volume': avg_vol,
                'n_bars': len(df),
                'start': df.index.min(),
                'end': df.index.max()
            })

    # Sort by avg volume descending, take top tokens with decent volume
    candidates.sort(key=lambda x: x['avg_volume'], reverse=True)

    # Filter out extremely low volume tokens (volume in base units varies)
    # Keep all tokens with >= 3y data since we need diversity
    # But exclude PAXG (stablecoin-like, gold-backed) and extremely low volume
    exclude = {'PAXG'}  # gold-backed stablecoin, not crypto momentum
    selected = [c for c in candidates if c['token'] not in exclude]

    print(f"Universe: {len(selected)} tokens with >= {MIN_DURATION_DAYS} days of data")
    for c in selected[:30]:
        print(f"  {c['token']:8s} | {c['duration_days']:5d} days | avg_vol={c['avg_volume']:>16,.0f}")

    return selected


def load_universe_data(universe_tokens):
    """Load all token data into a dict of DataFrames."""
    data = {}
    for token_info in universe_tokens:
        token = token_info['token']
        df = load_hourly_close(token)
        if df is not None:
            data[token] = df
    return data


def build_close_panel(data_dict):
    """Build a panel of close prices aligned on common timestamps."""
    closes = {}
    for token, df in data_dict.items():
        closes[token] = df['close']
    panel = pd.DataFrame(closes)
    panel.index = pd.to_datetime(panel.index)
    panel = panel.sort_index()
    return panel


# ── 2. Signal Computation ───────────────────────────────────────────────────

def compute_returns(close_panel, lookback_hours):
    """Compute lookback-period returns for each token."""
    return close_panel.pct_change(lookback_hours)


def compute_volatility(close_panel, lookback_hours):
    """Compute rolling volatility (std of hourly returns) over lookback period."""
    hourly_ret = close_panel.pct_change(1)
    return hourly_ret.rolling(lookback_hours, min_periods=lookback_hours // 2).std()


def compute_sma(close_panel, lookback_hours):
    """Compute simple moving average over lookback period."""
    return close_panel.rolling(lookback_hours, min_periods=lookback_hours // 2).mean()


def rank_tokens_simple(returns_row):
    """Rank tokens by raw return. Higher return = higher rank."""
    valid = returns_row.dropna()
    if len(valid) < TOP_N + BOTTOM_N:
        return None
    ranked = valid.rank(ascending=True)  # highest return gets highest rank
    return ranked


def rank_tokens_risk_adjusted(returns_row, vol_row):
    """Rank tokens by return / volatility (Sharpe-like). Higher = better."""
    valid_mask = returns_row.notna() & vol_row.notna() & (vol_row > 0)
    if valid_mask.sum() < TOP_N + BOTTOM_N:
        return None
    sharpe_like = returns_row[valid_mask] / vol_row[valid_mask]
    ranked = sharpe_like.rank(ascending=True)
    return ranked


def get_long_portfolio(ranks, above_sma_mask=None, variant='simple_xsmom'):
    """Given ranks for a single timestamp, return equal-weight long portfolio.

    For dual_momentum, only include tokens that are also above their SMA.
    """
    if ranks is None:
        return {}

    n_tokens = len(ranks)

    if variant == 'dual_momentum' and above_sma_mask is not None:
        # Filter to only tokens above SMA
        eligible = ranks[above_sma_mask]
        if len(eligible) < 1:
            return {}
        # Take top N from eligible
        top = eligible.nlargest(min(TOP_N, len(eligible)))
    else:
        # Take top N by rank
        top = ranks.nlargest(TOP_N)

    if len(top) == 0:
        return {}

    weight = 1.0 / len(top)
    return {token: weight for token in top.index}


# ── 3. Backtest Engine ──────────────────────────────────────────────────────

def run_backtest(close_panel, variant, lookback_days):
    """Run a single variant/lookback backtest. Returns equity curve and trade log."""
    lookback_hours = lookback_days * 24

    # Precompute signals
    returns = compute_returns(close_panel, lookback_hours)
    vol = compute_volatility(close_panel, lookback_hours)
    sma = compute_sma(close_panel, lookback_hours)

    # Determine common valid range
    first_valid = max(
        returns.first_valid_index(),
        vol.first_valid_index(),
        sma.first_valid_index()
    )

    # Start after warmup
    timestamps = close_panel.index
    # Find the index position for first_valid (use searchsorted for newer pandas)
    first_valid_pos = timestamps.searchsorted(first_valid)
    warmup_end_idx = max(WARMUP_BARS, first_valid_pos + 1)

    if warmup_end_idx >= len(timestamps) - REBALANCE_BARS:
        return None, None, None

    # Initialize
    equity = 1.0
    equity_curve = []
    current_portfolio = {}  # token -> weight
    trades = []
    turnover_list = []

    # Walk through timestamps
    bars_since_rebal = REBALANCE_BARS  # force first rebalance

    for i in range(warmup_end_idx, len(timestamps)):
        ts = timestamps[i]

        # Compute portfolio return for this bar
        bar_return = 0.0
        for token, weight in current_portfolio.items():
            if token in close_panel.columns:
                curr = close_panel.loc[ts, token]
                prev_ts = timestamps[i - 1]
                prev = close_panel.loc[prev_ts, token]
                if pd.notna(curr) and pd.notna(prev) and prev > 0:
                    bar_return += weight * (curr / prev - 1)

        equity *= (1 + bar_return)
        equity_curve.append({'timestamp': ts, 'equity': equity})

        bars_since_rebal += 1

        # Rebalance check
        if bars_since_rebal >= REBALANCE_BARS:
            bars_since_rebal = 0

            # Compute ranks
            ret_row = returns.loc[ts] if ts in returns.index else None
            vol_row = vol.loc[ts] if ts in vol.index else None
            sma_row = sma.loc[ts] if ts in sma.index else None
            close_row = close_panel.loc[ts] if ts in close_panel.index else None

            if ret_row is None:
                continue

            if variant == 'simple_xsmom':
                ranks = rank_tokens_simple(ret_row)
                new_portfolio = get_long_portfolio(ranks, variant=variant)

            elif variant == 'risk_adjusted_xsmom':
                ranks = rank_tokens_risk_adjusted(ret_row, vol_row)
                new_portfolio = get_long_portfolio(ranks, variant=variant)

            elif variant == 'dual_momentum':
                ranks = rank_tokens_simple(ret_row)
                if ranks is not None and close_row is not None and sma_row is not None:
                    above_sma = close_row > sma_row
                    # Only keep tokens that appear in ranks index
                    above_sma = above_sma.reindex(ranks.index).fillna(False)
                    new_portfolio = get_long_portfolio(
                        ranks, above_sma_mask=above_sma, variant=variant
                    )
                else:
                    new_portfolio = {}

            # Compute turnover
            all_tokens = set(list(current_portfolio.keys()) + list(new_portfolio.keys()))
            turnover = 0.0
            for token in all_tokens:
                old_w = current_portfolio.get(token, 0.0)
                new_w = new_portfolio.get(token, 0.0)
                turnover += abs(new_w - old_w)
            turnover_list.append(turnover)

            # Apply transaction costs
            cost = turnover * COST_FRAC
            equity *= (1 - cost)
            equity_curve[-1]['equity'] = equity

            # Record trades
            for token in all_tokens:
                old_w = current_portfolio.get(token, 0.0)
                new_w = new_portfolio.get(token, 0.0)
                if abs(new_w - old_w) > 1e-8:
                    trades.append({
                        'timestamp': ts,
                        'token': token,
                        'old_weight': old_w,
                        'new_weight': new_w
                    })

            current_portfolio = new_portfolio

    eq_df = pd.DataFrame(equity_curve).set_index('timestamp')
    trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()
    avg_turnover = np.mean(turnover_list) if turnover_list else 0.0

    return eq_df, trades_df, avg_turnover


# ── 4. Metrics ──────────────────────────────────────────────────────────────

def compute_metrics(eq_df, btc_eq_df=None):
    """Compute performance metrics from equity curve."""
    if eq_df is None or len(eq_df) < 100:
        return None

    eq = eq_df['equity']

    # Returns
    total_return = eq.iloc[-1] / eq.iloc[0] - 1

    # Hourly returns
    hourly_ret = eq.pct_change().dropna()
    if len(hourly_ret) < 100:
        return None

    # Sharpe (annualized)
    mean_ret = hourly_ret.mean()
    std_ret = hourly_ret.std()
    sharpe = (mean_ret / std_ret * SQRT_HOURS_PER_YEAR) if std_ret > 0 else 0.0

    # Max drawdown
    cummax = eq.cummax()
    drawdown = (eq - cummax) / cummax
    max_dd = drawdown.min()

    # Profit factor
    pos_ret = hourly_ret[hourly_ret > 0].sum()
    neg_ret = abs(hourly_ret[hourly_ret < 0].sum())
    profit_factor = pos_ret / neg_ret if neg_ret > 0 else float('inf')

    # Duration
    n_hours = len(eq)
    n_days = n_hours / 24

    # Correlation with BTC buy-and-hold
    btc_corr = np.nan
    if btc_eq_df is not None:
        # Align indices
        common = eq_df.index.intersection(btc_eq_df.index)
        if len(common) > 100:
            strat_ret = eq.reindex(common).pct_change().dropna()
            btc_ret = btc_eq_df['equity'].reindex(common).pct_change().dropna()
            common_ret = strat_ret.index.intersection(btc_ret.index)
            if len(common_ret) > 100:
                btc_corr = strat_ret.loc[common_ret].corr(btc_ret.loc[common_ret])

    return {
        'total_return': total_return,
        'total_return_pct': total_return * 100,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'max_dd_pct': max_dd * 100,
        'profit_factor': profit_factor,
        'n_days': n_days,
        'btc_corr': btc_corr
    }


def build_btc_buy_hold(btc_close, start_ts, end_ts):
    """Build BTC buy-and-hold equity curve for correlation comparison."""
    btc = btc_close.loc[start_ts:end_ts].dropna()
    if len(btc) == 0:
        return None
    eq = btc / btc.iloc[0]
    return pd.DataFrame({'equity': eq}, index=btc.index)


# ── 5. Main ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 80)
    print("CROSS-SECTIONAL MOMENTUM BACKTEST")
    print("=" * 80)
    print()

    # 1. Build universe
    universe = build_universe()
    token_names = [t['token'] for t in universe]
    print(f"\nTotal universe size: {len(token_names)} tokens")
    print()

    # 2. Load data
    print("Loading data...")
    data = load_universe_data(universe)
    close_panel = build_close_panel(data)
    print(f"Close panel shape: {close_panel.shape}")
    print(f"Date range: {close_panel.index.min()} to {close_panel.index.max()}")

    # Load BTC for benchmark
    btc_data = load_hourly_close('BTC')
    btc_close = btc_data['close'] if btc_data is not None else None

    # 3. Determine IS/OOS split
    total_bars = len(close_panel)
    is_end_idx = int(total_bars * IS_FRACTION)
    is_end_ts = close_panel.index[is_end_idx]
    oos_start_ts = close_panel.index[is_end_idx + 1]
    print(f"\nIS period: {close_panel.index.min()} to {is_end_ts}")
    print(f"OOS period: {oos_start_ts} to {close_panel.index.max()}")
    print()

    # We need to find the common start date where enough tokens have data
    # Count how many tokens have valid data at each point
    valid_counts = close_panel.notna().sum(axis=1)
    min_tokens_needed = TOP_N + BOTTOM_N  # need at least 10 tokens
    has_enough = valid_counts >= min_tokens_needed
    if not has_enough.any():
        print("ERROR: Never have enough tokens with valid data simultaneously!")
        return

    first_valid_date = has_enough.idxmax()
    print(f"First date with >= {min_tokens_needed} valid tokens: {first_valid_date}")
    print(f"Tokens available at that date: {valid_counts.loc[first_valid_date]}")

    # 4. Run all variants
    results = {}

    for variant, lookback in product(VARIANTS, LOOKBACK_DAYS):
        key = f"{variant}_lb{lookback}d"
        print(f"\nRunning: {key}...")

        eq_df, trades_df, avg_turnover = run_backtest(
            close_panel, variant, lookback
        )

        if eq_df is None or len(eq_df) == 0:
            print(f"  SKIPPED: insufficient data")
            results[key] = {'status': 'skipped'}
            continue

        # Split IS/OOS
        is_eq = eq_df.loc[:is_end_ts]
        oos_eq = eq_df.loc[oos_start_ts:]

        # Normalize OOS equity to start at 1
        if len(oos_eq) > 0:
            oos_eq = oos_eq.copy()
            oos_eq['equity'] = oos_eq['equity'] / oos_eq['equity'].iloc[0]

        # BTC buy-and-hold for OOS period
        btc_oos = None
        if btc_close is not None and len(oos_eq) > 0:
            btc_oos = build_btc_buy_hold(btc_close, oos_eq.index.min(), oos_eq.index.max())

        # Compute IS metrics
        is_eq_norm = is_eq.copy()
        if len(is_eq_norm) > 0:
            is_eq_norm['equity'] = is_eq_norm['equity'] / is_eq_norm['equity'].iloc[0]

        is_btc = None
        if btc_close is not None and len(is_eq) > 0:
            is_btc = build_btc_buy_hold(btc_close, is_eq.index.min(), is_eq.index.max())

        is_metrics = compute_metrics(is_eq_norm, is_btc)
        oos_metrics = compute_metrics(oos_eq, btc_oos)

        n_trades = len(trades_df) if trades_df is not None and len(trades_df) > 0 else 0

        # Count OOS trades
        oos_trades = 0
        if trades_df is not None and len(trades_df) > 0 and 'timestamp' in trades_df.columns:
            oos_trades = len(trades_df[trades_df['timestamp'] >= oos_start_ts])

        results[key] = {
            'status': 'ok',
            'variant': variant,
            'lookback': lookback,
            'is_metrics': is_metrics,
            'oos_metrics': oos_metrics,
            'total_trades': n_trades,
            'oos_trades': oos_trades,
            'avg_turnover': avg_turnover
        }

        if oos_metrics:
            print(f"  IS:  Sharpe={is_metrics['sharpe']:.3f}  Return={is_metrics['total_return_pct']:.1f}%  MaxDD={is_metrics['max_dd_pct']:.1f}%")
            print(f"  OOS: Sharpe={oos_metrics['sharpe']:.3f}  Return={oos_metrics['total_return_pct']:.1f}%  MaxDD={oos_metrics['max_dd_pct']:.1f}%  BTC_corr={oos_metrics['btc_corr']:.3f}")
        else:
            print(f"  OOS: insufficient data for metrics")

    # 5. Summary
    print("\n" + "=" * 80)
    print("RESULTS SUMMARY — OUT-OF-SAMPLE")
    print("=" * 80)

    # Build summary table
    summary_rows = []
    for key, r in results.items():
        if r['status'] != 'ok' or r['oos_metrics'] is None:
            continue
        m = r['oos_metrics']
        summary_rows.append({
            'variant': key,
            'sharpe': m['sharpe'],
            'return_pct': m['total_return_pct'],
            'max_dd_pct': m['max_dd_pct'],
            'profit_factor': m['profit_factor'],
            'btc_corr': m['btc_corr'],
            'avg_turnover': r['avg_turnover'],
            'oos_trades': r['oos_trades'],
        })

    if not summary_rows:
        print("No valid results!")
        return

    summary = pd.DataFrame(summary_rows).sort_values('sharpe', ascending=False)

    print(f"\n{'Variant':<35s} {'Sharpe':>8s} {'Return%':>9s} {'MaxDD%':>8s} {'PF':>6s} {'BTC_r':>7s} {'Turnover':>10s} {'Trades':>8s} {'Verdict':>10s}")
    print("-" * 110)

    for _, row in summary.iterrows():
        # Apply kill criteria
        killed = False
        reasons = []
        if row['sharpe'] < 0.3:
            killed = True
            reasons.append('Sharpe')
        if row['max_dd_pct'] < -30:
            killed = True
            reasons.append('MaxDD')
        if abs(row['btc_corr']) > 0.7:
            killed = True
            reasons.append('BTC_corr')

        verdict = "KILL" if killed else "PASS"
        if killed:
            verdict += f" ({','.join(reasons)})"

        print(f"{row['variant']:<35s} {row['sharpe']:>8.3f} {row['return_pct']:>8.1f}% {row['max_dd_pct']:>7.1f}% {row['profit_factor']:>6.2f} {row['btc_corr']:>7.3f} {row['avg_turnover']:>9.1f}% {row['oos_trades']:>8.0f} {verdict:>10s}")

    # 6. Best variant
    print("\n" + "=" * 80)
    passing = summary[(summary['sharpe'] >= 0.3) & (summary['max_dd_pct'] >= -30) & (summary['btc_corr'].abs() <= 0.7)]
    if len(passing) > 0:
        best = passing.iloc[0]
        print(f"BEST PASSING VARIANT: {best['variant']}")
        print(f"  Sharpe:       {best['sharpe']:.3f}")
        print(f"  Return:       {best['return_pct']:.1f}%")
        print(f"  Max DD:       {best['max_dd_pct']:.1f}%")
        print(f"  BTC corr:     {best['btc_corr']:.3f}")
        print(f"  Avg turnover: {best['avg_turnover']:.1f}%")
    else:
        print("NO VARIANT PASSES ALL CRITERIA")
        print("All variants killed. Cross-sectional momentum does not meet the bar.")

    # 7. Write machine-readable results
    output = {
        'strategy': 'cross_sectional_momentum',
        'run_date': datetime.now().isoformat(),
        'universe_size': len(token_names),
        'tokens': token_names,
        'is_period': f"{close_panel.index.min()} to {is_end_ts}",
        'oos_period': f"{oos_start_ts} to {close_panel.index.max()}",
        'kill_criteria': {
            'min_sharpe': 0.3,
            'max_dd': -30,
            'max_btc_corr': 0.7
        },
        'results': {}
    }

    for key, r in results.items():
        if r['status'] != 'ok':
            output['results'][key] = {'status': 'skipped'}
            continue
        entry = {
            'variant': r['variant'],
            'lookback_days': r['lookback'],
            'total_trades': r['total_trades'],
            'oos_trades': r['oos_trades'],
            'avg_turnover_pct': r['avg_turnover'] * 100 if isinstance(r['avg_turnover'], float) else r['avg_turnover'],
        }
        if r['is_metrics']:
            entry['is_metrics'] = {k: float(v) if isinstance(v, (float, np.floating)) else v
                                   for k, v in r['is_metrics'].items()}
        if r['oos_metrics']:
            entry['oos_metrics'] = {k: float(v) if isinstance(v, (float, np.floating)) else v
                                    for k, v in r['oos_metrics'].items()}
            # Verdict
            m = r['oos_metrics']
            killed = m['sharpe'] < 0.3 or m['max_dd'] < -0.30 or abs(m['btc_corr']) > 0.7
            entry['verdict'] = 'KILL' if killed else 'PASS'
        output['results'][key] = entry

    # Identify best
    if len(passing) > 0:
        output['best_variant'] = passing.iloc[0]['variant']
        output['overall_verdict'] = 'PASS'
    else:
        output['best_variant'] = None
        output['overall_verdict'] = 'KILL'

    results_json_path = OUTPUT_DIR / 'cross_sectional_momentum_results.json'
    with open(results_json_path, 'w') as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nJSON results written to: {results_json_path}")

    # 8. Write markdown results
    write_markdown_results(output, summary, results, close_panel, is_end_ts, oos_start_ts, token_names, universe)


def write_markdown_results(output, summary, results, close_panel, is_end_ts, oos_start_ts, token_names, universe):
    """Write the markdown results file."""
    md_path = OUTPUT_DIR / 'cross_sectional_momentum_results.md'

    lines = []
    lines.append("# Cross-Sectional Momentum Strategy -- Results")
    lines.append("")
    lines.append(f"**Run date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Overall verdict:** {output['overall_verdict']}")
    lines.append("")

    lines.append("## Strategy Design")
    lines.append("")
    lines.append("Cross-sectional momentum ranks crypto tokens by recent performance and")
    lines.append("goes long the top performers. This is a fundamentally different approach")
    lines.append("from time-series momentum (V3) and may provide diversification.")
    lines.append("")
    lines.append("**Variants tested:**")
    lines.append("1. **Simple XSMOM** - Rank by N-day raw return, long top 5")
    lines.append("2. **Risk-adjusted XSMOM** - Rank by return/volatility (Sharpe-like)")
    lines.append("3. **Dual momentum** - Cross-sectional rank AND absolute momentum (price > SMA)")
    lines.append("")
    lines.append("**Lookback periods:** 14d, 30d, 60d, 90d")
    lines.append(f"**Rebalance:** Weekly (168 hourly bars)")
    lines.append(f"**Cost:** {COST_BPS} bps round-trip per trade")
    lines.append(f"**Universe:** {len(token_names)} tokens with >= 3 years data (ex-BTC)")
    lines.append("")

    lines.append("## Universe")
    lines.append("")
    lines.append("| Token | Start Date | Duration (days) | Avg Volume |")
    lines.append("|-------|-----------|----------------|-----------|")
    # Build lookup from universe list
    uni_lookup = {u['token']: u for u in universe}
    for t in token_names[:30]:
        u = uni_lookup.get(t)
        if u:
            lines.append(f"| {t} | {u['start'].strftime('%Y-%m-%d')} | {u['duration_days']} | {u['avg_volume']:,.0f} |")
        else:
            lines.append(f"| {t} | - | - | - |")
    lines.append("")

    lines.append("## Temporal Split")
    lines.append("")
    lines.append(f"- **In-sample:** {close_panel.index.min().strftime('%Y-%m-%d')} to {is_end_ts.strftime('%Y-%m-%d')} (70%)")
    lines.append(f"- **Out-of-sample:** {oos_start_ts.strftime('%Y-%m-%d')} to {close_panel.index.max().strftime('%Y-%m-%d')} (30%)")
    lines.append("")

    lines.append("## Kill Criteria")
    lines.append("")
    lines.append("| Metric | Threshold | Rationale |")
    lines.append("|--------|-----------|-----------|")
    lines.append("| Sharpe ratio | < 0.3 | Not worth the complexity |")
    lines.append("| Max drawdown | > 30% | Unacceptable risk |")
    lines.append("| BTC correlation | > 0.7 | No diversification benefit |")
    lines.append("")

    lines.append("## OOS Results")
    lines.append("")
    lines.append("| Variant | Sharpe | Return | MaxDD | Profit Factor | BTC Corr | Avg Turnover | Trades | Verdict |")
    lines.append("|---------|--------|--------|-------|--------------|----------|-------------|--------|---------|")

    if summary is not None and len(summary) > 0:
        for _, row in summary.iterrows():
            killed = False
            reasons = []
            if row['sharpe'] < 0.3:
                killed = True
                reasons.append('Sharpe')
            if row['max_dd_pct'] < -30:
                killed = True
                reasons.append('MaxDD')
            if abs(row['btc_corr']) > 0.7:
                killed = True
                reasons.append('BTC_corr')
            verdict = "KILL" if killed else "PASS"
            if killed and reasons:
                verdict += f" ({', '.join(reasons)})"

            lines.append(
                f"| {row['variant']} | {row['sharpe']:.3f} | {row['return_pct']:.1f}% | "
                f"{row['max_dd_pct']:.1f}% | {row['profit_factor']:.2f} | "
                f"{row['btc_corr']:.3f} | {row['avg_turnover']:.3f} | "
                f"{row['oos_trades']:.0f} | **{verdict}** |"
            )
    lines.append("")

    # IS results for comparison
    lines.append("## IS Results (for comparison)")
    lines.append("")
    lines.append("| Variant | Sharpe | Return | MaxDD | BTC Corr |")
    lines.append("|---------|--------|--------|-------|----------|")

    for key, r in results.items():
        if r['status'] != 'ok' or r['is_metrics'] is None:
            continue
        m = r['is_metrics']
        lines.append(
            f"| {key} | {m['sharpe']:.3f} | {m['total_return_pct']:.1f}% | "
            f"{m['max_dd_pct']:.1f}% | {m['btc_corr']:.3f} |"
        )
    lines.append("")

    # Best variant
    lines.append("## Best Variant")
    lines.append("")
    if output['best_variant']:
        lines.append(f"**{output['best_variant']}** passes all kill criteria.")
        lines.append("")
        # Get its metrics
        best_key = output['best_variant']
        if best_key in output['results'] and 'oos_metrics' in output['results'][best_key]:
            bm = output['results'][best_key]['oos_metrics']
            lines.append(f"- Sharpe ratio: {bm['sharpe']:.3f}")
            lines.append(f"- Total return: {bm['total_return_pct']:.1f}%")
            lines.append(f"- Max drawdown: {bm['max_dd_pct']:.1f}%")
            lines.append(f"- BTC correlation: {bm['btc_corr']:.3f}")
            lines.append(f"- Profit factor: {bm['profit_factor']:.2f}")
    else:
        lines.append("**No variant passes all kill criteria.** Cross-sectional momentum is KILLED.")
        lines.append("")
        lines.append("Possible reasons:")
        lines.append("- Crypto tokens are too correlated (beta-driven), reducing cross-sectional signal quality")
        lines.append("- Weekly rebalance too slow for rapidly rotating leadership")
        lines.append("- Transaction costs eat into thin momentum spreads")
        lines.append("- The momentum effect may not exist strongly enough in crypto cross-sections")
    lines.append("")

    lines.append("## Correlation Analysis")
    lines.append("")
    lines.append("All XSMOM variants are fundamentally long-only crypto portfolios.")
    lines.append("Even with momentum-based selection, the dominant factor is likely")
    lines.append("crypto beta (market direction). This limits diversification potential")
    lines.append("relative to the BTC-only V3 strategy.")
    lines.append("")
    if summary is not None and len(summary) > 0:
        avg_corr = summary['btc_corr'].mean()
        lines.append(f"Average BTC correlation across variants: {avg_corr:.3f}")
        if avg_corr > 0.5:
            lines.append("")
            lines.append("High correlation with BTC confirms that cross-sectional token selection")
            lines.append("does not meaningfully reduce crypto beta exposure.")
    lines.append("")

    lines.append("## Conclusion")
    lines.append("")
    if output['overall_verdict'] == 'PASS':
        lines.append("Cross-sectional momentum shows promise as a diversifier.")
        lines.append("The best variant should be tested with tighter cost assumptions")
        lines.append("and different rebalance frequencies before live deployment.")
    else:
        lines.append("Cross-sectional momentum does not meet the required bar for")
        lines.append("deployment alongside V3. The strategy is KILLED.")
        lines.append("")
        lines.append("**Recommended next steps:**")
        lines.append("- Investigate mean-reversion (contrarian) strategies instead")
        lines.append("- Consider non-directional strategies (funding rate arb, basis trade)")
        lines.append("- Explore cross-asset strategies (crypto vs. macro) for true decorrelation")
    lines.append("")

    with open(md_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f"Markdown results written to: {md_path}")


if __name__ == '__main__':
    main()
