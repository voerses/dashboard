#!/usr/bin/env python3
"""
Main backtest runner — executes causal strategies vs originals,
generates comparison report with charts.
"""
import sys
import os
import time
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

from data_generator import generate_all_data
from backtest_engine import BacktestEngine, buy_and_hold
from base_strategies import RSIMACDMomentum, AdaptiveRegime, SignalWeighted
from causal_strategy import get_all_causal_strategies
from causal_engine import compute_causal_scores, TopologicalRegimeDetector
from indicators import compute_all_indicators

OUTPUT_DIR = 'outputs'
os.makedirs(OUTPUT_DIR, exist_ok=True)

def main():
    print("=" * 70)
    print("CRYPTO BACKTESTING — CAUSAL ANALYSIS FRAMEWORK")
    print("Transfer Entropy + Mutual Information + Persistent Homology + CCM")
    print("=" * 70)

    # 1. Generate data
    print("\n[1/6] Generating historical data...")
    datasets = generate_all_data(OUTPUT_DIR)

    # 2. Run causal analysis
    print("\n[2/6] Running causal indicator analysis...")
    causal_results = {}
    for asset, df in datasets.items():
        print(f"\n  Analyzing {asset}...")
        indicators = compute_all_indicators(df)
        scores = compute_causal_scores(indicators, df['close'], horizon=5, window=200)
        causal_results[asset] = scores
        scores.to_csv(f'{OUTPUT_DIR}/{asset}_causal_scores.csv', index=False)
        print(f"    Top 5 causal indicators:")
        for _, row in scores.head(5).iterrows():
            print(f"      {row['indicator']:25s} TE={row['transfer_entropy']:.4f}  MI={row['mutual_info']:.4f}  CCM={row['ccm_convergence']:.4f}  Score={row['composite_score']:.3f}")

    # 3. Run backtests
    print("\n[3/6] Running backtests...")
    engine = BacktestEngine(initial_capital=200000, fee_rate=0.001, slippage_bps=5)

    # Strategies
    base_strategies = [RSIMACDMomentum(), AdaptiveRegime(), SignalWeighted()]
    causal_strategies = get_all_causal_strategies()
    all_strategies = base_strategies + causal_strategies

    all_results = []

    for asset, df in datasets.items():
        print(f"\n  --- {asset} ---")

        # Buy & Hold benchmark
        bh = buy_and_hold(df)
        bh.asset = asset
        all_results.append(bh)
        print(f"    Buy & Hold: {bh.total_return_pct:.1f}%  Sharpe={bh.sharpe:.2f}  MaxDD={bh.max_drawdown:.1f}%")

        for strat in all_strategies:
            t0 = time.time()
            print(f"    Running {strat.name}...", end=' ', flush=True)
            try:
                signals = strat.generate_signals(df)
                result = engine.run(df, signals, strat.name, asset)
                all_results.append(result)
                elapsed = time.time() - t0
                print(f"{result.total_return_pct:.1f}%  Sharpe={result.sharpe:.2f}  "
                      f"WinRate={result.win_rate:.0f}%  MaxDD={result.max_drawdown:.1f}%  ({elapsed:.1f}s)")
            except Exception as e:
                print(f"FAILED: {e}")

    # 4. Build comparison table
    print("\n[4/6] Building comparison report...")
    rows = []
    for r in all_results:
        rows.append({
            'Asset': r.asset, 'Strategy': r.strategy_name,
            'Final Equity': f'${r.final_equity:,.0f}',
            'Return %': f'{r.total_return_pct:.1f}%',
            'CAGR %': f'{r.cagr:.1f}%',
            'Sharpe': f'{r.sharpe:.2f}',
            'Sortino': f'{r.sortino:.2f}',
            'Calmar': f'{r.calmar:.2f}',
            'Max DD %': f'{r.max_drawdown:.1f}%',
            'Trades': r.total_trades,
            'Win Rate %': f'{r.win_rate:.1f}%',
            'Profit Factor': f'{r.profit_factor:.2f}',
            'Fees': f'${r.total_fees:,.0f}'
        })
    comparison = pd.DataFrame(rows)
    comparison.to_csv(f'{OUTPUT_DIR}/full_comparison.csv', index=False)
    print(comparison.to_string(index=False))

    # 5. Generate charts
    print("\n[5/6] Generating charts...")
    _plot_equity_curves(all_results, datasets)
    _plot_causal_analysis(causal_results)
    _plot_regime_detection(datasets)
    _plot_win_rate_comparison(all_results)
    _plot_risk_return_scatter(all_results)

    # 6. Summary
    print("\n[6/6] Summary")
    print("=" * 70)
    _print_summary(all_results, causal_results)

    print(f"\nAll outputs saved to {OUTPUT_DIR}/")
    print("=" * 70)


def _plot_equity_curves(results, datasets):
    """Equity curves per asset: causal vs base strategies."""
    for asset in datasets.keys():
        asset_results = [r for r in results if r.asset == asset]
        if not asset_results:
            continue

        fig, ax = plt.subplots(figsize=(14, 7))

        # Color scheme: base=blue tones, causal=red/orange tones, benchmark=gray
        colors = {
            'Buy & Hold': '#888888',
            'RSI+MACD Momentum': '#4A90D9',
            'Adaptive Regime': '#2D6BB0',
            'Signal Weighted': '#1A4D80',
            'Causal Composite': '#E74C3C',
            'TE Momentum': '#E67E22',
            'Topological Trend': '#F39C12',
            'Causal Ensemble': '#C0392B',
        }
        linewidths = {
            'Buy & Hold': 1.5,
            'Causal Ensemble': 2.5,
        }

        for r in asset_results:
            color = colors.get(r.strategy_name, '#666666')
            lw = linewidths.get(r.strategy_name, 1.5)
            ls = '--' if r.strategy_name == 'Buy & Hold' else '-'
            ax.plot(r.equity_curve.index, r.equity_curve.values,
                    label=f"{r.strategy_name} ({r.total_return_pct:.0f}%)",
                    color=color, linewidth=lw, linestyle=ls, alpha=0.85)

        ax.set_title(f'{asset} — Equity Curves (Causal vs Base Strategies)', fontsize=14, fontweight='bold')
        ax.set_ylabel('Equity ($)')
        ax.set_yscale('log')
        ax.legend(loc='upper left', fontsize=8)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(f'{OUTPUT_DIR}/{asset}_equity_curves.png', dpi=150)
        plt.close(fig)


def _plot_causal_analysis(causal_results):
    """Plot top causal indicators per asset."""
    fig, axes = plt.subplots(1, 3, figsize=(18, 8))

    for idx, (asset, scores) in enumerate(causal_results.items()):
        ax = axes[idx]
        top = scores.head(15)

        # Horizontal bar chart of composite scores
        colors_map = {
            'transfer_entropy': '#E74C3C',
            'mutual_info': '#3498DB',
            'ccm_convergence': '#2ECC71',
            'pearson_r': '#95A5A6'
        }

        y_pos = np.arange(len(top))
        bars = ax.barh(y_pos, top['composite_score'].values, color='#E74C3C', alpha=0.7)

        ax.set_yticks(y_pos)
        ax.set_yticklabels(top['indicator'].values, fontsize=7)
        ax.set_xlabel('Causal Composite Score')
        ax.set_title(f'{asset}\nTop Causal Indicators', fontsize=11, fontweight='bold')
        ax.invert_yaxis()

        # Add TE values as text
        for j, (_, row) in enumerate(top.iterrows()):
            ax.text(row['composite_score'] + 0.01, j,
                    f"TE={row['transfer_entropy']:.3f}  MI={row['mutual_info']:.3f}",
                    va='center', fontsize=6, color='#333')

    fig.suptitle('Causal Indicator Ranking (Transfer Entropy + MI + CCM)', fontsize=14, fontweight='bold', y=1.02)
    fig.tight_layout()
    fig.savefig(f'{OUTPUT_DIR}/causal_indicator_ranking.png', dpi=150, bbox_inches='tight')
    plt.close(fig)


def _plot_regime_detection(datasets):
    """Plot topological regime detection for each asset."""
    fig, axes = plt.subplots(3, 1, figsize=(14, 12), sharex=False)

    regime_colors = {0: '#CCCCCC', 1: '#2ECC71', 2: '#E74C3C', 3: '#3498DB', 4: '#9B59B6'}
    regime_labels = {0: 'Unknown', 1: 'Trend Up', 2: 'Trend Down', 3: 'Mean Revert', 4: 'Chaotic'}

    for idx, (asset, df) in enumerate(datasets.items()):
        ax = axes[idx]
        returns = np.log(df['close'] / df['close'].shift(1)).fillna(0).values

        detector = TopologicalRegimeDetector(window=50, embedding_dim=3)
        regimes, _ = detector.detect_regimes(df['close'].values, returns)

        ax.plot(df.index, df['close'].values, color='black', linewidth=0.8, alpha=0.7)

        # Color background by regime
        for i in range(1, len(regimes)):
            ax.axvspan(df.index[i-1], df.index[i],
                       alpha=0.15, color=regime_colors.get(regimes[i], '#CCCCCC'))

        ax.set_title(f'{asset} — Topological Regime Detection', fontsize=11, fontweight='bold')
        ax.set_ylabel('Price')
        ax.set_yscale('log')
        ax.grid(True, alpha=0.2)

        # Legend
        from matplotlib.patches import Patch
        legend_elements = [Patch(facecolor=c, alpha=0.3, label=regime_labels[k])
                          for k, c in regime_colors.items() if k > 0]
        ax.legend(handles=legend_elements, loc='upper left', fontsize=7)

    fig.tight_layout()
    fig.savefig(f'{OUTPUT_DIR}/regime_detection.png', dpi=150)
    plt.close(fig)


def _plot_win_rate_comparison(results):
    """Compare win rates across strategies."""
    fig, ax = plt.subplots(figsize=(12, 7))

    assets = sorted(set(r.asset for r in results))
    strategies = sorted(set(r.strategy_name for r in results if r.strategy_name != 'Buy & Hold'))

    x = np.arange(len(strategies))
    width = 0.25

    colors = {'BTCUSDT': '#F7931A', 'ETHUSDT': '#627EEA', 'SOLUSDT': '#00FFA3'}

    for i, asset in enumerate(assets):
        win_rates = []
        for strat in strategies:
            r = next((r for r in results if r.asset == asset and r.strategy_name == strat), None)
            win_rates.append(r.win_rate if r else 0)
        ax.bar(x + i * width, win_rates, width, label=asset, color=colors.get(asset, '#999'), alpha=0.8)

    ax.axhline(y=60, color='red', linestyle='--', linewidth=1.5, label='60% Target')
    ax.set_ylabel('Win Rate (%)')
    ax.set_title('Win Rate Comparison — Causal vs Base Strategies', fontsize=13, fontweight='bold')
    ax.set_xticks(x + width)
    ax.set_xticklabels(strategies, rotation=45, ha='right', fontsize=8)
    ax.legend()
    ax.grid(True, alpha=0.3, axis='y')
    fig.tight_layout()
    fig.savefig(f'{OUTPUT_DIR}/win_rate_comparison.png', dpi=150)
    plt.close(fig)


def _plot_risk_return_scatter(results):
    """Risk-return scatter with strategy labeling."""
    fig, ax = plt.subplots(figsize=(12, 8))

    markers = {'BTCUSDT': 'o', 'ETHUSDT': 's', 'SOLUSDT': '^'}
    colors = {
        'Buy & Hold': '#888888',
        'RSI+MACD Momentum': '#4A90D9',
        'Adaptive Regime': '#2D6BB0',
        'Signal Weighted': '#1A4D80',
        'Causal Composite': '#E74C3C',
        'TE Momentum': '#E67E22',
        'Topological Trend': '#F39C12',
        'Causal Ensemble': '#C0392B',
    }

    for r in results:
        ax.scatter(abs(r.max_drawdown), r.cagr,
                   marker=markers.get(r.asset, 'o'),
                   color=colors.get(r.strategy_name, '#666'),
                   s=max(50, r.win_rate * 2), alpha=0.7,
                   edgecolors='black', linewidth=0.5)
        ax.annotate(f"{r.strategy_name}\n{r.asset}",
                    (abs(r.max_drawdown), r.cagr),
                    fontsize=5, alpha=0.7)

    ax.set_xlabel('Max Drawdown (%)', fontsize=11)
    ax.set_ylabel('CAGR (%)', fontsize=11)
    ax.set_title('Risk-Return Profile (size = win rate)', fontsize=13, fontweight='bold')
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(f'{OUTPUT_DIR}/risk_return_scatter.png', dpi=150)
    plt.close(fig)


def _print_summary(results, causal_results):
    """Print the key findings."""
    print("\nBEST STRATEGY PER ASSET (by Sharpe):")
    assets = sorted(set(r.asset for r in results))

    for asset in assets:
        asset_results = [r for r in results if r.asset == asset and r.strategy_name != 'Buy & Hold']
        if not asset_results:
            continue
        best = max(asset_results, key=lambda r: r.sharpe)
        bh = next(r for r in results if r.asset == asset and r.strategy_name == 'Buy & Hold')

        print(f"\n  {asset}:")
        print(f"    Best: {best.strategy_name}")
        print(f"      Sharpe={best.sharpe:.2f}  Return={best.total_return_pct:.1f}%  "
              f"MaxDD={best.max_drawdown:.1f}%  WinRate={best.win_rate:.1f}%  Trades={best.total_trades}")
        print(f"    vs Buy&Hold: Sharpe={bh.sharpe:.2f}  Return={bh.total_return_pct:.1f}%  MaxDD={bh.max_drawdown:.1f}%")

    # Causal analysis summary
    print("\n\nCAUSAL ANALYSIS — KEY FINDINGS:")
    for asset, scores in causal_results.items():
        top3 = scores.head(3)
        print(f"\n  {asset} — Top 3 causal indicators:")
        for _, row in top3.iterrows():
            print(f"    {row['indicator']:25s}  TE={row['transfer_entropy']:.4f}  "
                  f"MI={row['mutual_info']:.4f}  CCM_conv={row['ccm_convergence']:.4f}")

    # Win rate analysis
    print("\n\nSTRATEGIES ACHIEVING 60%+ WIN RATE:")
    above_60 = [r for r in results if r.win_rate >= 60 and r.strategy_name != 'Buy & Hold']
    if above_60:
        for r in sorted(above_60, key=lambda r: r.win_rate, reverse=True):
            print(f"  {r.strategy_name} on {r.asset}: {r.win_rate:.1f}% win rate, "
                  f"Sharpe={r.sharpe:.2f}, Return={r.total_return_pct:.1f}%")
    else:
        print("  None achieved 60%+ win rate. Closest:")
        close = sorted([r for r in results if r.strategy_name != 'Buy & Hold'],
                       key=lambda r: r.win_rate, reverse=True)[:5]
        for r in close:
            print(f"  {r.strategy_name} on {r.asset}: {r.win_rate:.1f}% win rate")


if __name__ == '__main__':
    main()
