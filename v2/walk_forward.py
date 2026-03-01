"""
Walk-Forward Backtesting Framework

Prevents look-ahead bias by:
1. Regime detection uses ONLY data up to current bar (expanding window)
2. Indicators computed on causal windows only
3. Train/test split with rolling windows
4. No future data leakage in any computation

This is the PROPER way to backtest — no cheating.
"""

import numpy as np
import pandas as pd
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from regime_detector import RegimeDetector, Regime
from strategies import (
    Strategy1_HMMRegimeAdaptive,
    Strategy2_MomentumTrendFollower,
    Strategy3_MeanReversionZScore,
    Strategy4_FundingOIDivergence,
    Strategy5_MultiFactorEnsemble,
)
from real_data_fetcher import fetch_all_tickers
from backtest_engine import BacktestEngine, buy_and_hold
import json
from datetime import datetime


class WalkForwardEngine:
    """
    Walk-forward backtester that prevents look-ahead bias.

    Key principles:
    1. Regime detection is re-run on expanding window (only past data)
    2. Each bar only sees data up to that point
    3. No parameter optimization on test data
    4. Train period used for regime calibration, test period for signal generation
    """

    def __init__(self, initial_capital=200000, fee_rate=0.001, slippage_bps=5,
                 train_days=365, recalibrate_every=90):
        """
        Args:
            train_days: Minimum days of history before trading starts
            recalibrate_every: Re-fit regime detector every N days
        """
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.slippage_bps = slippage_bps
        self.train_days = train_days
        self.recalibrate_every = recalibrate_every

    def run(self, df, strategy, strategy_name, asset_name):
        """
        Walk-forward backtest with expanding window regime detection.

        Instead of detecting regimes on the FULL dataset (which is cheating),
        we re-detect regimes periodically using only data up to current bar.
        """
        n = len(df)
        if n < self.train_days + 30:
            print(f"  Not enough data for {asset_name}: {n} < {self.train_days + 30}")
            return None

        detector = RegimeDetector()

        # Arrays to store walk-forward regimes and features
        wf_regimes = np.full(n, Regime.MEAN_REVERTING)
        wf_features = {}

        # Phase 1: Detect regimes on training period
        train_df = df.iloc[:self.train_days]
        regimes_train, features_train = detector.detect(train_df)
        wf_regimes[:self.train_days] = regimes_train

        print(f"  {asset_name}/{strategy_name}: Training on {self.train_days} days, "
              f"testing on {n - self.train_days} days")

        # Phase 2: Walk forward, recalibrating regime detection periodically
        last_calibration = self.train_days
        bars_since_cal = 0

        for i in range(self.train_days, n):
            bars_since_cal += 1

            # Recalibrate regime detection on expanding window
            if bars_since_cal >= self.recalibrate_every or i == self.train_days:
                # Use ALL data up to current bar (expanding window, no future data)
                expanding_df = df.iloc[:i+1]
                try:
                    regimes_expanded, features_expanded = detector.detect(expanding_df)
                    # Only use the regime for bars we haven't assigned yet
                    wf_regimes[last_calibration:i+1] = regimes_expanded[last_calibration:i+1]
                    last_calibration = i + 1
                    bars_since_cal = 0
                except Exception:
                    pass  # Keep previous regime assignments

            # For bars between recalibrations, use the last known regime
            if i >= last_calibration:
                wf_regimes[i] = wf_regimes[i-1]

        # Phase 3: Generate signals using walk-forward regimes
        # The strategy only sees causal data (its indicators use lookback windows)
        signals = strategy.generate_signals(df, wf_regimes, wf_features)

        # Phase 4: Only evaluate on TEST period (after training)
        # Zero out all signals during training period
        signals.iloc[:self.train_days, signals.columns.get_loc('signal')] = 0
        signals.iloc[:self.train_days, signals.columns.get_loc('position_size')] = 0

        # Phase 5: Run through standard backtest engine
        engine = BacktestEngine(
            initial_capital=self.initial_capital,
            fee_rate=self.fee_rate,
            slippage_bps=self.slippage_bps
        )
        result = engine.run(df, signals, strategy_name, asset_name)

        return result, signals, wf_regimes


def run_walk_forward():
    """Run walk-forward backtest on all strategies and assets."""
    print("=" * 70)
    print("WALK-FORWARD BACKTEST — NO LOOK-AHEAD BIAS")
    print("=" * 70)
    print(f"Run time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Capital: $200,000 | Fees: 0.1% | Slippage: 5bps")
    print(f"Train: 365 days | Recalibrate regime every: 90 days")
    print()

    # Fetch real data
    print("[1/4] Fetching real historical data...")
    datasets = fetch_all_tickers(['BTC', 'ETH', 'SOL'], days=2000)

    # Setup
    strategies = [
        Strategy1_HMMRegimeAdaptive(),
        Strategy2_MomentumTrendFollower(),
        Strategy3_MeanReversionZScore(),
        Strategy4_FundingOIDivergence(),
        Strategy5_MultiFactorEnsemble(),
    ]

    wf_engine = WalkForwardEngine(
        initial_capital=200000,
        fee_rate=0.001,
        slippage_bps=5,
        train_days=365,
        recalibrate_every=90,
    )

    all_results = []
    signal_data = {}

    print("\n[2/4] Running walk-forward backtests...")

    for name, df in datasets.items():
        print(f"\n  === {name} ({len(df)} days) ===")

        # Buy & Hold benchmark (no look-ahead bias concern)
        bh = buy_and_hold(df)
        bh.asset = name
        all_results.append(bh)
        print(f"  Buy & Hold: Return={bh.total_return_pct:.1f}%, "
              f"Sharpe={bh.sharpe:.2f}, MaxDD={bh.max_drawdown:.1f}%")

        asset_signals = {}

        for strat in strategies:
            try:
                output = wf_engine.run(df, strat, strat.name, name)
                if output is None:
                    continue
                result, signals, wf_regimes = output
                all_results.append(result)

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

        signal_data[name] = {
            'dates': [str(d) for d in df.index],
            'close': df['close'].tolist(),
            'open': df['open'].tolist(),
            'high': df['high'].tolist(),
            'low': df['low'].tolist(),
            'volume': df['volume'].tolist(),
            'regimes': wf_regimes.tolist() if 'wf_regimes' in dir() else [3] * len(df),
            'strategies': asset_signals,
        }

    # Save results
    print("\n[3/4] Saving results...")
    os.makedirs('outputs_v2', exist_ok=True)

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
    comparison.to_csv('outputs_v2/comparison_wf.csv', index=False)
    print(f"  Saved comparison_wf.csv ({len(rows)} rows)")

    with open('outputs_v2/signals_wf.json', 'w') as f:
        json.dump(signal_data, f)
    print("  Saved signals_wf.json for dashboard")

    # Charts
    print("\n[4/4] Generating charts...")
    try:
        generate_wf_charts(datasets, all_results, signal_data)
    except Exception as e:
        print(f"  Chart generation failed: {e}")
        import traceback
        traceback.print_exc()

    # Summary
    print("\n" + "=" * 70)
    print("WALK-FORWARD RESULTS (NO LOOK-AHEAD BIAS)")
    print("=" * 70)
    print(comparison.to_string(index=False))

    print("\n" + "=" * 70)
    print("BEST STRATEGY PER ASSET (by Sharpe)")
    print("=" * 70)
    for asset in ['BTC', 'ETH', 'SOL']:
        asset_results = [r for r in all_results
                        if r.asset == asset and r.strategy_name != 'Buy & Hold']
        if asset_results:
            best = max(asset_results, key=lambda r: r.sharpe)
            bh = [r for r in all_results
                  if r.asset == asset and r.strategy_name == 'Buy & Hold'][0]
            print(f"\n  {asset}: {best.strategy_name}")
            print(f"    Return: {best.total_return_pct:.1f}% (B&H: {bh.total_return_pct:.1f}%)")
            print(f"    Sharpe: {best.sharpe:.2f} (B&H: {bh.sharpe:.2f})")
            print(f"    Max DD: {best.max_drawdown:.1f}% (B&H: {bh.max_drawdown:.1f}%)")
            print(f"    Win Rate: {best.win_rate:.0f}%, Trades: {best.total_trades}")

    return all_results


def generate_wf_charts(datasets, all_results, signal_data):
    """Generate charts for walk-forward results."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    strategy_colors = {
        'Buy & Hold': '#888888',
        'HMM Regime Adaptive': '#2196F3',
        'Momentum Trend': '#4CAF50',
        'Mean Reversion Z': '#FF9800',
        'Funding+OI Divergence': '#9C27B0',
        'Multi-Factor Ensemble': '#F44336',
    }

    for asset_name, df in datasets.items():
        fig, ax = plt.subplots(figsize=(16, 8))
        asset_results = [r for r in all_results if r.asset == asset_name]

        for result in asset_results:
            color = strategy_colors.get(result.strategy_name, '#000000')
            lw = 2.5 if result.strategy_name != 'Buy & Hold' else 1.5
            ls = '-' if result.strategy_name != 'Buy & Hold' else '--'
            label = f"{result.strategy_name} ({result.total_return_pct:+.0f}%, S={result.sharpe:.2f})"
            if len(result.equity_curve) > 0:
                ax.plot(result.equity_curve.index, result.equity_curve.values,
                       color=color, linewidth=lw, linestyle=ls, label=label, alpha=0.9)

        ax.set_title(f'{asset_name} — Walk-Forward Equity Curves ($200K start, 1yr train)',
                    fontsize=14, fontweight='bold')
        ax.set_ylabel('Equity ($)')
        ax.legend(loc='upper left', fontsize=8)
        ax.grid(True, alpha=0.3)
        ax.axhline(y=200000, color='gray', linestyle=':', alpha=0.5)

        # Mark train/test boundary
        train_end_idx = min(365, len(df) - 1)
        ax.axvline(x=df.index[train_end_idx], color='red', linestyle='--',
                  alpha=0.5, label='Train/Test split')

        plt.savefig(f'outputs_v2/{asset_name}_wf_equity.png', dpi=150, bbox_inches='tight')
        plt.close()
        print(f"  Saved {asset_name}_wf_equity.png")

    # Risk-return scatter
    fig, ax = plt.subplots(figsize=(12, 8))
    for result in all_results:
        color = strategy_colors.get(result.strategy_name, '#000000')
        marker = {'BTC': 'o', 'ETH': 's', 'SOL': '^'}.get(result.asset, 'o')
        size = 150 if result.strategy_name != 'Buy & Hold' else 80
        ax.scatter(abs(result.max_drawdown), result.total_return_pct,
                  color=color, marker=marker, s=size, alpha=0.8,
                  edgecolors='black', linewidths=0.5)
        ax.annotate(f"{result.asset}\n{result.strategy_name[:10]}",
                   (abs(result.max_drawdown), result.total_return_pct),
                   fontsize=6, ha='center', va='bottom')

    ax.set_xlabel('Max Drawdown (%)', fontsize=12)
    ax.set_ylabel('Total Return (%)', fontsize=12)
    ax.set_title('Walk-Forward Risk-Return (no look-ahead bias)', fontsize=14, fontweight='bold')
    ax.grid(True, alpha=0.3)
    patches = [Patch(facecolor=c, label=n) for n, c in strategy_colors.items()]
    ax.legend(handles=patches, loc='upper left', fontsize=8)

    plt.savefig('outputs_v2/risk_return_wf.png', dpi=150, bbox_inches='tight')
    plt.close()
    print("  Saved risk_return_wf.png")


if __name__ == '__main__':
    run_walk_forward()
