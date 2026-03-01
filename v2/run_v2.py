"""
V2 Backtest Runner

Runs all 5 elite strategies + buy & hold across BTC/ETH/SOL.
Uses REAL historical data from Binance.
Uses WALK-FORWARD backtesting (no look-ahead bias).

Generates:
- Full comparison CSV
- Equity curve charts per asset
- Regime detection visualization
- Win rate and risk-return scatter
- Dashboard-ready signal data (JSON)
"""

import sys
import os

# Add parent directory for backtest engine
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
import pandas as pd
import json
from datetime import datetime

from regime_detector import RegimeDetector, Regime
from strategies import (
    Strategy1_HMMRegimeAdaptive,
    Strategy2_MomentumTrendFollower,
    Strategy3_MeanReversionZScore,
    Strategy4_FundingOIDivergence,
    Strategy5_MultiFactorEnsemble,
)
from real_data_fetcher import fetch_all_tickers
from walk_forward import WalkForwardEngine
from backtest_engine import BacktestEngine, buy_and_hold


def run_all():
    print("=" * 70)
    print("ELITE CRYPTO TRADING STRATEGIES V2 - WALK-FORWARD BACKTEST")
    print("=" * 70)
    print(f"Run time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Capital: $200,000 | Fees: 0.1% | Slippage: 5bps")
    print(f"Real data from Binance | Walk-forward (no look-ahead bias)")
    print()

    # Fetch real data from Binance
    print("[1/5] Fetching real historical data from Binance...")
    datasets = fetch_all_tickers(['BTC', 'ETH', 'SOL'], days=2000)

    # Run walk-forward strategies
    print("\n[2/5] Setting up walk-forward engine...")
    strategies = [
        Strategy1_HMMRegimeAdaptive(),
        Strategy2_MomentumTrendFollower(),
        Strategy3_MeanReversionZScore(),
        Strategy4_FundingOIDivergence(),
        Strategy5_MultiFactorEnsemble(),
    ]

    wf_engine = WalkForwardEngine(
        initial_capital=200000, fee_rate=0.001, slippage_bps=5,
        train_days=365, recalibrate_every=90,
    )

    all_results = []
    signal_data = {}
    regime_data = {}

    print("\n[3/5] Running walk-forward backtests (no look-ahead bias)...")
    for name, df in datasets.items():
        print(f"\n  === {name} ({len(df)} days) ===")

        # Buy & Hold benchmark
        bh = buy_and_hold(df)
        bh.asset = name
        all_results.append(bh)
        print(f"  Buy & Hold: Return={bh.total_return_pct:.1f}%, "
              f"Sharpe={bh.sharpe:.2f}, MaxDD={bh.max_drawdown:.1f}%")

        asset_signals = {}
        last_regimes = None

        for strat in strategies:
            try:
                output = wf_engine.run(df, strat, strat.name, name)
                if output is None:
                    continue
                result, signals, wf_regimes = output
                all_results.append(result)
                last_regimes = wf_regimes

                trade_count = result.total_trades
                if trade_count > 0:
                    print(f"  {strat.name}: Return={result.total_return_pct:.1f}%, "
                          f"Sharpe={result.sharpe:.2f}, MaxDD={result.max_drawdown:.1f}%, "
                          f"WR={result.win_rate:.0f}%, Trades={trade_count}")
                else:
                    print(f"  {strat.name}: No trades generated")

                asset_signals[strat.name] = {
                    'signal': signals['signal'].tolist(),
                    'position_size': signals['position_size'].tolist(),
                }

            except Exception as e:
                print(f"  {strat.name}: ERROR - {e}")
                import traceback
                traceback.print_exc()

        regimes_list = last_regimes.tolist() if last_regimes is not None else [3] * len(df)
        regime_data[name] = (last_regimes, {})

        signal_data[name] = {
            'dates': [str(d) for d in df.index],
            'close': df['close'].tolist(),
            'open': df['open'].tolist(),
            'high': df['high'].tolist(),
            'low': df['low'].tolist(),
            'volume': df['volume'].tolist(),
            'regimes': regimes_list,
            'strategies': asset_signals,
        }

    # Save results
    print("\n[4/5] Saving results...")
    os.makedirs('outputs_v2', exist_ok=True)

    # Comparison CSV
    rows = []
    for r in all_results:
        rows.append({
            'Asset': r.asset,
            'Strategy': r.strategy_name,
            'Return %': round(r.total_return_pct, 1),
            'CAGR %': round(r.cagr, 1),
            'Sharpe': round(r.sharpe, 2),
            'Sortino': round(r.sortino, 2),
            'Calmar': round(r.calmar, 2),
            'Max DD %': round(r.max_drawdown, 1),
            'Max DD Days': r.max_drawdown_duration,
            'Trades': r.total_trades,
            'Win Rate %': round(r.win_rate, 1),
            'Profit Factor': round(r.profit_factor, 2),
            'Total Fees': round(r.total_fees, 0),
            'Final Equity': round(r.final_equity, 0),
        })

    comparison = pd.DataFrame(rows)
    comparison.to_csv('outputs_v2/comparison.csv', index=False)
    print(f"  Saved comparison.csv ({len(rows)} rows)")

    # Signal data for dashboard
    with open('outputs_v2/signals.json', 'w') as f:
        json.dump(signal_data, f)
    print("  Saved signals.json for dashboard")

    # Generate charts
    print("\n[5/5] Generating charts...")
    try:
        generate_charts(datasets, all_results, regime_data)
    except Exception as e:
        print(f"  Chart generation failed: {e}")
        import traceback
        traceback.print_exc()

    # Print summary table
    print("\n" + "=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)
    print(comparison.to_string(index=False))

    # Best strategy per asset
    print("\n" + "=" * 70)
    print("BEST STRATEGY PER ASSET (by Sharpe)")
    print("=" * 70)
    for asset in ['BTC', 'ETH', 'SOL']:
        asset_results = [r for r in all_results if r.asset == asset and r.strategy_name != 'Buy & Hold']
        if asset_results:
            best = max(asset_results, key=lambda r: r.sharpe)
            bh = [r for r in all_results if r.asset == asset and r.strategy_name == 'Buy & Hold'][0]
            print(f"\n  {asset}: {best.strategy_name}")
            print(f"    Return: {best.total_return_pct:.1f}% (B&H: {bh.total_return_pct:.1f}%)")
            print(f"    Sharpe: {best.sharpe:.2f} (B&H: {bh.sharpe:.2f})")
            print(f"    Max DD: {best.max_drawdown:.1f}% (B&H: {bh.max_drawdown:.1f}%)")
            print(f"    Win Rate: {best.win_rate:.0f}%, Trades: {best.total_trades}")

    return all_results


def generate_charts(datasets, all_results, regime_data):
    """Generate matplotlib charts."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.patches import Patch

    # Color scheme
    strategy_colors = {
        'Buy & Hold': '#888888',
        'HMM Regime Adaptive': '#2196F3',
        'Momentum Trend': '#4CAF50',
        'Mean Reversion Z': '#FF9800',
        'Funding+OI Divergence': '#9C27B0',
        'Multi-Factor Ensemble': '#F44336',
    }

    regime_colors = {
        Regime.TRENDING_UP: '#4CAF50',
        Regime.TRENDING_DOWN: '#F44336',
        Regime.MEAN_REVERTING: '#FFC107',
        Regime.HIGH_VOL_CHAOS: '#9C27B0',
        Regime.LOW_VOL_ACCUMULATION: '#2196F3',
    }

    # 1. Equity curves per asset
    for asset_name, df in datasets.items():
        fig, axes = plt.subplots(2, 1, figsize=(16, 10), height_ratios=[3, 1],
                                gridspec_kw={'hspace': 0.3})

        ax1 = axes[0]
        asset_results = [r for r in all_results if r.asset == asset_name]

        for result in asset_results:
            color = strategy_colors.get(result.strategy_name, '#000000')
            lw = 2.5 if result.strategy_name != 'Buy & Hold' else 1.5
            ls = '-' if result.strategy_name != 'Buy & Hold' else '--'
            label = f"{result.strategy_name} ({result.total_return_pct:+.0f}%, S={result.sharpe:.2f})"
            if len(result.equity_curve) > 0:
                ax1.plot(result.equity_curve.index, result.equity_curve.values,
                        color=color, linewidth=lw, linestyle=ls, label=label, alpha=0.9)

        ax1.set_title(f'{asset_name} — Equity Curves ($200K start)', fontsize=14, fontweight='bold')
        ax1.set_ylabel('Equity ($)')
        ax1.legend(loc='upper left', fontsize=8)
        ax1.grid(True, alpha=0.3)
        ax1.axhline(y=200000, color='gray', linestyle=':', alpha=0.5)

        # Regime subplot
        ax2 = axes[1]
        regimes = regime_data[asset_name][0]
        dates = df.index

        for r in Regime:
            mask = (regimes == r)
            if np.any(mask):
                ax2.fill_between(dates, 0, 1, where=mask,
                               color=regime_colors[r], alpha=0.6, label=r.name)

        ax2.set_title('Market Regime Detection', fontsize=10)
        ax2.set_yticks([])
        ax2.legend(loc='upper left', fontsize=7, ncol=5)

        plt.savefig(f'outputs_v2/{asset_name}_equity.png', dpi=150, bbox_inches='tight')
        plt.close()
        print(f"  Saved {asset_name}_equity.png")

    # 2. Risk-Return scatter
    fig, ax = plt.subplots(figsize=(12, 8))
    for result in all_results:
        color = strategy_colors.get(result.strategy_name, '#000000')
        marker = {'BTC': 'o', 'ETH': 's', 'SOL': '^'}.get(result.asset, 'o')
        size = 150 if result.strategy_name != 'Buy & Hold' else 80

        ax.scatter(abs(result.max_drawdown), result.total_return_pct,
                  color=color, marker=marker, s=size, alpha=0.8, edgecolors='black', linewidths=0.5)
        ax.annotate(f"{result.asset}\n{result.strategy_name[:10]}",
                   (abs(result.max_drawdown), result.total_return_pct),
                   fontsize=6, ha='center', va='bottom')

    ax.set_xlabel('Max Drawdown (%)', fontsize=12)
    ax.set_ylabel('Total Return (%)', fontsize=12)
    ax.set_title('Risk-Return Scatter (all strategies × all assets)', fontsize=14, fontweight='bold')
    ax.grid(True, alpha=0.3)

    # Legend
    patches = [Patch(facecolor=c, label=n) for n, c in strategy_colors.items()]
    ax.legend(handles=patches, loc='upper left', fontsize=8)

    plt.savefig('outputs_v2/risk_return.png', dpi=150, bbox_inches='tight')
    plt.close()
    print("  Saved risk_return.png")

    # 3. Win rate comparison
    fig, ax = plt.subplots(figsize=(14, 6))
    strat_names = list(strategy_colors.keys())
    asset_names = ['BTC', 'ETH', 'SOL']
    x = np.arange(len(strat_names))
    width = 0.25

    for j, asset in enumerate(asset_names):
        win_rates = []
        for sname in strat_names:
            r = [r for r in all_results if r.asset == asset and r.strategy_name == sname]
            win_rates.append(r[0].win_rate if r else 0)
        bars = ax.bar(x + j * width, win_rates, width, label=asset, alpha=0.8)

    ax.set_xlabel('Strategy')
    ax.set_ylabel('Win Rate (%)')
    ax.set_title('Win Rate Comparison by Strategy and Asset', fontsize=14, fontweight='bold')
    ax.set_xticks(x + width)
    ax.set_xticklabels(strat_names, rotation=15, ha='right', fontsize=9)
    ax.legend()
    ax.axhline(y=50, color='red', linestyle='--', alpha=0.5, label='50% line')
    ax.grid(True, alpha=0.3, axis='y')

    plt.savefig('outputs_v2/win_rates.png', dpi=150, bbox_inches='tight')
    plt.close()
    print("  Saved win_rates.png")

    # 4. Sharpe ratio comparison
    fig, ax = plt.subplots(figsize=(14, 6))

    for j, asset in enumerate(asset_names):
        sharpes = []
        for sname in strat_names:
            r = [r for r in all_results if r.asset == asset and r.strategy_name == sname]
            sharpes.append(r[0].sharpe if r else 0)
        ax.bar(x + j * width, sharpes, width, label=asset, alpha=0.8)

    ax.set_xlabel('Strategy')
    ax.set_ylabel('Sharpe Ratio')
    ax.set_title('Sharpe Ratio Comparison', fontsize=14, fontweight='bold')
    ax.set_xticks(x + width)
    ax.set_xticklabels(strat_names, rotation=15, ha='right', fontsize=9)
    ax.legend()
    ax.axhline(y=1.0, color='green', linestyle='--', alpha=0.5)
    ax.grid(True, alpha=0.3, axis='y')

    plt.savefig('outputs_v2/sharpe_comparison.png', dpi=150, bbox_inches='tight')
    plt.close()
    print("  Saved sharpe_comparison.png")


if __name__ == '__main__':
    run_all()
