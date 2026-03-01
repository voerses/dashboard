"""
V2 Full Backtest — All 149 tokens, recency-weighted analysis

Key features:
1. Walk-forward backtesting (no look-ahead bias)
2. Recency-weighted performance metrics (recent 1-2 years weighted 3x)
3. Cycle-aware analysis: pre-ETF vs post-ETF regime shifts
4. Top 149 tokens by Binance volume with 365+ days history
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
import pandas as pd
import json
from datetime import datetime, timedelta

from regime_detector import RegimeDetector, Regime
from strategies import (
    Strategy1_HMMRegimeAdaptive,
    Strategy2_MomentumTrendFollower,
    Strategy3_MeanReversionZScore,
    Strategy4_FundingOIDivergence,
    Strategy5_MultiFactorEnsemble,
)
from real_data_fetcher import fetch_all_tickers, add_synthetic_derivatives
from walk_forward import WalkForwardEngine
from backtest_engine import BacktestEngine, buy_and_hold


def load_all_tokens(cache_dir='real_data', min_days=365):
    """Load all cached tokens with enough history."""
    datasets = {}
    for f in sorted(os.listdir(cache_dir)):
        if not f.endswith('_daily.csv'):
            continue
        ticker = f.replace('_daily.csv', '')
        df = pd.read_csv(os.path.join(cache_dir, f), index_col=0, parse_dates=True)
        if len(df) >= min_days:
            df = add_synthetic_derivatives(df, ticker)
            datasets[ticker] = df
    return datasets


def compute_recency_weighted_sharpe(equity_curve, recent_weight=3.0, recent_days=730):
    """
    Compute Sharpe ratio with heavier weighting on recent performance.

    Recent 2 years get 3x weight. This captures the post-ETF regime shift.
    """
    returns = equity_curve.pct_change().dropna()
    if len(returns) < 30:
        return 0.0

    n = len(returns)
    cutoff = max(0, n - recent_days)

    old_rets = returns.iloc[:cutoff]
    recent_rets = returns.iloc[cutoff:]

    if len(recent_rets) < 30:
        # Not enough recent data, use full period
        if returns.std() > 0:
            return float((returns.mean() / returns.std()) * np.sqrt(365))
        return 0.0

    # Weighted mean and std
    old_weight = 1.0
    new_weight = recent_weight

    if len(old_rets) > 0:
        total_weight = old_weight * len(old_rets) + new_weight * len(recent_rets)
        weighted_mean = (old_weight * old_rets.sum() + new_weight * recent_rets.sum()) / total_weight
        weighted_var = (
            old_weight * ((old_rets - weighted_mean) ** 2).sum() +
            new_weight * ((recent_rets - weighted_mean) ** 2).sum()
        ) / total_weight
    else:
        weighted_mean = recent_rets.mean()
        weighted_var = recent_rets.var()

    weighted_std = np.sqrt(weighted_var) if weighted_var > 0 else 1e-10
    return float((weighted_mean / weighted_std) * np.sqrt(365))


def analyze_cycle_periods(equity_curve, df):
    """
    Break down performance by crypto cycle periods.

    Key periods:
    - Pre-2021 bull: before Jan 2021
    - 2021 bull: Jan 2021 - Nov 2021
    - 2022 bear: Nov 2021 - Nov 2022
    - 2023 recovery: Nov 2022 - Jan 2024
    - 2024 ETF era: Jan 2024 - present (BTC ETF approved Jan 10, 2024)
    """
    periods = {
        'Pre-2021': ('2017-01-01', '2021-01-01'),
        '2021 Bull': ('2021-01-01', '2021-11-15'),
        '2022 Bear': ('2021-11-15', '2022-11-15'),
        '2023 Recovery': ('2022-11-15', '2024-01-10'),
        'Post-ETF 2024+': ('2024-01-10', '2027-01-01'),
    }

    results = {}
    for name, (start, end) in periods.items():
        mask = (equity_curve.index >= start) & (equity_curve.index < end)
        period_eq = equity_curve[mask]
        if len(period_eq) < 20:
            continue

        ret = (period_eq.iloc[-1] / period_eq.iloc[0] - 1) * 100
        rets = period_eq.pct_change().dropna()
        sharpe = float((rets.mean() / rets.std()) * np.sqrt(365)) if rets.std() > 0 else 0
        dd = float(((period_eq / period_eq.cummax()) - 1).min() * 100)
        results[name] = {'return': ret, 'sharpe': sharpe, 'max_dd': dd, 'days': len(period_eq)}

    return results


def run_full():
    print("=" * 80)
    print("FULL WALK-FORWARD BACKTEST — 149 TOKENS, RECENCY-WEIGHTED")
    print("=" * 80)
    print(f"Run time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Capital: $200,000 | Fees: 0.1% | Slippage: 5bps")
    print(f"Walk-forward: 365d train, 90d recalibrate | Recent 2yr weighted 3x")
    print()

    # Load all tokens
    print("[1/5] Loading cached token data...")
    datasets = load_all_tokens(min_days=730)  # Need 2+ years for meaningful walk-forward
    print(f"  Loaded {len(datasets)} tokens with 730+ days")

    # Focus strategies
    strategies = [
        Strategy1_HMMRegimeAdaptive(),
        Strategy2_MomentumTrendFollower(),
        Strategy4_FundingOIDivergence(),
        Strategy5_MultiFactorEnsemble(),
    ]
    # Skip Mean Reversion (consistently loses on crypto)

    wf_engine = WalkForwardEngine(
        initial_capital=200000, fee_rate=0.001, slippage_bps=5,
        train_days=365, recalibrate_every=90,
    )

    all_results = []
    all_cycle_analysis = {}

    print(f"\n[2/5] Running walk-forward backtests on {len(datasets)} tokens...")
    print(f"  Strategies: {[s.name for s in strategies]}")

    for idx, (ticker, df) in enumerate(datasets.items()):
        if (idx + 1) % 20 == 0:
            print(f"\n  --- [{idx+1}/{len(datasets)}] Processing {ticker} ---")

        # B&H benchmark
        bh = buy_and_hold(df)
        bh.asset = ticker

        for strat in strategies:
            try:
                output = wf_engine.run(df, strat, strat.name, ticker)
                if output is None:
                    continue
                result, signals, wf_regimes = output

                # Compute recency-weighted Sharpe
                rw_sharpe = compute_recency_weighted_sharpe(result.equity_curve)

                # Cycle analysis
                cycle = analyze_cycle_periods(result.equity_curve, df)

                all_results.append({
                    'ticker': ticker,
                    'strategy': strat.name,
                    'return_pct': result.total_return_pct,
                    'sharpe': result.sharpe,
                    'rw_sharpe': rw_sharpe,
                    'max_dd': result.max_drawdown,
                    'win_rate': result.win_rate,
                    'trades': result.total_trades,
                    'profit_factor': result.profit_factor,
                    'bh_return': bh.total_return_pct,
                    'bh_sharpe': bh.sharpe,
                    'bh_max_dd': bh.max_drawdown,
                    'days': len(df),
                    'cycle': cycle,
                })

            except Exception as e:
                pass  # Skip errors silently for bulk run

        if (idx + 1) % 20 == 0:
            print(f"  Results so far: {len(all_results)} strategy-token combinations")

    print(f"\n[3/5] Analyzing results...")
    results_df = pd.DataFrame([{k: v for k, v in r.items() if k != 'cycle'} for r in all_results])

    # Best strategy per token (by recency-weighted Sharpe)
    print(f"\n  Total: {len(results_df)} strategy-token results")

    # Strategy comparison
    print(f"\n{'='*80}")
    print("STRATEGY COMPARISON (across all tokens)")
    print(f"{'='*80}")

    for strat_name in [s.name for s in strategies]:
        strat_df = results_df[results_df['strategy'] == strat_name]
        if len(strat_df) == 0:
            continue

        print(f"\n  {strat_name} ({len(strat_df)} tokens):")
        print(f"    Avg Return:     {strat_df['return_pct'].mean():>8.1f}%  (median: {strat_df['return_pct'].median():.1f}%)")
        print(f"    Avg Sharpe:     {strat_df['sharpe'].mean():>8.2f}  (median: {strat_df['sharpe'].median():.2f})")
        print(f"    Avg RW-Sharpe:  {strat_df['rw_sharpe'].mean():>8.2f}  (recent-weighted)")
        print(f"    Avg Max DD:     {strat_df['max_dd'].mean():>8.1f}%")
        print(f"    Avg Win Rate:   {strat_df['win_rate'].mean():>8.1f}%")
        print(f"    Avg Trades:     {strat_df['trades'].mean():>8.0f}")
        print(f"    Profit > 0:     {(strat_df['return_pct'] > 0).sum()}/{len(strat_df)} tokens")
        print(f"    Beats B&H:      {(strat_df['sharpe'] > strat_df['bh_sharpe']).sum()}/{len(strat_df)} (by Sharpe)")

    # Top 20 by recency-weighted Sharpe
    print(f"\n{'='*80}")
    print("TOP 20 TOKEN-STRATEGY COMBOS (by Recency-Weighted Sharpe)")
    print(f"{'='*80}")
    top20 = results_df.nlargest(20, 'rw_sharpe')
    for _, row in top20.iterrows():
        print(f"  {row['ticker']:8s} {row['strategy']:25s}  "
              f"Ret={row['return_pct']:>7.1f}%  Sharpe={row['sharpe']:.2f}  "
              f"RW-Sharpe={row['rw_sharpe']:.2f}  DD={row['max_dd']:.1f}%  "
              f"WR={row['win_rate']:.0f}%  Trades={row['trades']}")

    # Cycle analysis for top strategies
    print(f"\n{'='*80}")
    print("CRYPTO CYCLE ANALYSIS — Pre-ETF vs Post-ETF Performance")
    print(f"{'='*80}")

    for strat_name in [s.name for s in strategies]:
        cycle_data = [r['cycle'] for r in all_results if r['strategy'] == strat_name and r['cycle']]
        if not cycle_data:
            continue

        print(f"\n  {strat_name}:")
        for period in ['2022 Bear', '2023 Recovery', 'Post-ETF 2024+']:
            period_rets = [c[period]['return'] for c in cycle_data if period in c]
            period_sharpes = [c[period]['sharpe'] for c in cycle_data if period in c]
            period_dds = [c[period]['max_dd'] for c in cycle_data if period in c]
            if period_rets:
                print(f"    {period:20s}: Avg Ret={np.mean(period_rets):>7.1f}%  "
                      f"Sharpe={np.mean(period_sharpes):.2f}  "
                      f"DD={np.mean(period_dds):.1f}%  "
                      f"({len(period_rets)} tokens)")

    # Save full results
    print(f"\n[4/5] Saving results...")
    os.makedirs('outputs_v2', exist_ok=True)
    results_df.to_csv('outputs_v2/full_149_results.csv', index=False)
    print(f"  Saved full_149_results.csv ({len(results_df)} rows)")

    # Save top performers for dashboard
    top_tokens = results_df.nlargest(30, 'rw_sharpe')['ticker'].unique().tolist()
    with open('outputs_v2/top_tokens.json', 'w') as f:
        json.dump(top_tokens, f)
    print(f"  Saved top {len(top_tokens)} tokens for dashboard")

    # Charts
    print(f"\n[5/5] Generating charts...")
    try:
        generate_full_charts(results_df, all_results)
    except Exception as e:
        print(f"  Chart generation failed: {e}")
        import traceback
        traceback.print_exc()

    return results_df, all_results


def generate_full_charts(results_df, all_results):
    """Generate summary charts for full token analysis."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    # 1. Strategy comparison boxplot
    fig, axes = plt.subplots(1, 3, figsize=(18, 6))

    strategies = results_df['strategy'].unique()

    # Sharpe distribution
    data = [results_df[results_df['strategy'] == s]['sharpe'].values for s in strategies]
    bp = axes[0].boxplot(data, labels=[s[:15] for s in strategies], patch_artist=True)
    axes[0].set_title('Sharpe Ratio Distribution')
    axes[0].axhline(y=0, color='red', linestyle='--', alpha=0.5)
    axes[0].set_ylabel('Sharpe')
    axes[0].tick_params(axis='x', rotation=15)

    # Return distribution
    data = [results_df[results_df['strategy'] == s]['return_pct'].values for s in strategies]
    axes[1].boxplot(data, labels=[s[:15] for s in strategies], patch_artist=True)
    axes[1].set_title('Return % Distribution')
    axes[1].axhline(y=0, color='red', linestyle='--', alpha=0.5)
    axes[1].set_ylabel('Return %')
    axes[1].tick_params(axis='x', rotation=15)

    # Win rate distribution
    data = [results_df[results_df['strategy'] == s]['win_rate'].values for s in strategies]
    axes[2].boxplot(data, labels=[s[:15] for s in strategies], patch_artist=True)
    axes[2].set_title('Win Rate % Distribution')
    axes[2].axhline(y=50, color='green', linestyle='--', alpha=0.5)
    axes[2].set_ylabel('Win Rate %')
    axes[2].tick_params(axis='x', rotation=15)

    plt.tight_layout()
    plt.savefig('outputs_v2/strategy_comparison_149.png', dpi=150, bbox_inches='tight')
    plt.close()
    print("  Saved strategy_comparison_149.png")

    # 2. Recency-weighted vs full-period Sharpe scatter
    fig, ax = plt.subplots(figsize=(10, 8))
    colors = {'HMM Regime Adaptive': '#2196F3', 'Momentum Trend': '#4CAF50',
              'Funding+OI Divergence': '#9C27B0', 'Multi-Factor Ensemble': '#F44336'}
    for strat in strategies:
        mask = results_df['strategy'] == strat
        ax.scatter(results_df[mask]['sharpe'], results_df[mask]['rw_sharpe'],
                  color=colors.get(strat, '#888'), alpha=0.6, label=strat[:20], s=40)
    ax.plot([-2, 3], [-2, 3], 'k--', alpha=0.3, label='Equal line')
    ax.set_xlabel('Full-Period Sharpe')
    ax.set_ylabel('Recency-Weighted Sharpe (2yr 3x)')
    ax.set_title('Recent vs Historical Performance')
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    plt.savefig('outputs_v2/recency_vs_full_sharpe.png', dpi=150, bbox_inches='tight')
    plt.close()
    print("  Saved recency_vs_full_sharpe.png")


if __name__ == '__main__':
    run_full()
