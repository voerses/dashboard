"""
Parity Validation: Engine (vectorized) vs Freqtrade (event-driven)
===================================================================

Compares trades from our Numba engine against Freqtrade's backtester
on the SAME data slice to detect vectorized→event-driven divergence.

The "Parity Trap": Our engine processes all bars at once (vectorized),
while Freqtrade processes bar-by-bar (event-driven). This can cause:
- Entry timing differences (bar alignment)
- Stop-loss trigger differences (high/low vs close)
- Position sizing rounding

This script quantifies the divergence and flags if it exceeds tolerance.

Usage:
    python parity_check.py                  # Run on all validated tokens
    python parity_check.py --token BTC      # Single token
    python parity_check.py --tolerance 0.15 # 15% PnL divergence tolerance
"""

import sys
import os
import json
import argparse
import numpy as np
from datetime import datetime
from typing import Dict, List, Tuple

# Lazy imports for heavy dependencies — allows module import in test environments
# without requiring the full engine stack
_engine_imported = False
Engine = None
CPCV_ROBUST_TOKENS = None


def _ensure_engine():
    global _engine_imported, Engine, CPCV_ROBUST_TOKENS
    if not _engine_imported:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
        from v2.engine import Engine as _Engine, CPCV_ROBUST_TOKENS as _Tokens
        Engine = _Engine
        CPCV_ROBUST_TOKENS = _Tokens
        _engine_imported = True


def load_engine_trades(strategy_fn, token: str, engine: Engine) -> List[Dict]:
    """Get trades from our vectorized engine."""
    result = engine.backtest_token(strategy_fn, token)
    if result is None:
        return []
    return result.get('trades', [])


def load_freqtrade_trades(token: str, ft_results_dir: str = 'user_data/backtest_results') -> List[Dict]:
    """
    Load Freqtrade backtest trades from its JSON output.

    Run Freqtrade backtester first:
        freqtrade backtesting --strategy CpcvSwingStrategy --timerange 20240101-
    """
    # Find most recent backtest result
    if not os.path.exists(ft_results_dir):
        return []

    result_files = sorted([f for f in os.listdir(ft_results_dir) if f.endswith('.json')])
    if not result_files:
        return []

    with open(os.path.join(ft_results_dir, result_files[-1]), 'r') as f:
        data = json.load(f)

    pair = f'{token}/USDT'
    trades = []
    for strat_data in data.get('strategy', {}).values():
        for t in strat_data.get('trades', []):
            if t.get('pair') == pair:
                trades.append({
                    'pnl': t.get('profit_abs', 0),
                    'return_pct': t.get('profit_ratio', 0) * 100,
                    'hold_hours': (pd.Timestamp(t['close_date']) - pd.Timestamp(t['open_date'])).total_seconds() / 3600,
                    'exit_reason': t.get('exit_reason', 'unknown'),
                    'open_date': t.get('open_date', ''),
                    'close_date': t.get('close_date', ''),
                })

    return trades


def compare_trades(engine_trades: List[Dict], ft_trades: List[Dict],
                   token: str, tolerance_pct: float = 0.10) -> Dict:
    """
    Compare engine vs Freqtrade trades and compute divergence metrics.

    Returns a report dict with pass/fail and divergence details.
    """
    report = {
        'token': token,
        'engine_trades': len(engine_trades),
        'ft_trades': len(ft_trades),
        'trade_count_match': False,
        'pnl_divergence_pct': 0.0,
        'avg_hold_divergence_hrs': 0.0,
        'pass': False,
        'issues': [],
    }

    if not engine_trades and not ft_trades:
        report['pass'] = True
        report['issues'].append('No trades in either system')
        return report

    # Trade count divergence
    if len(engine_trades) > 0:
        count_div = abs(len(engine_trades) - len(ft_trades)) / len(engine_trades)
    else:
        count_div = float('inf') if len(ft_trades) > 0 else 0

    report['trade_count_divergence_pct'] = round(count_div * 100, 1)
    report['trade_count_match'] = count_div < tolerance_pct

    if count_div > tolerance_pct:
        report['issues'].append(
            f"Trade count divergence: {len(engine_trades)} vs {len(ft_trades)} "
            f"({count_div*100:.1f}% > {tolerance_pct*100:.0f}% tolerance)")

    # PnL comparison
    engine_pnl = sum(t['pnl'] for t in engine_trades)
    ft_pnl = sum(t['pnl'] for t in ft_trades)

    if abs(engine_pnl) > 0:
        pnl_div = abs(engine_pnl - ft_pnl) / abs(engine_pnl)
    else:
        pnl_div = abs(ft_pnl) if ft_pnl != 0 else 0

    report['engine_pnl'] = round(engine_pnl, 2)
    report['ft_pnl'] = round(ft_pnl, 2)
    report['pnl_divergence_pct'] = round(pnl_div * 100, 1)

    if pnl_div > tolerance_pct:
        report['issues'].append(
            f"PnL divergence: ${engine_pnl:+,.0f} vs ${ft_pnl:+,.0f} "
            f"({pnl_div*100:.1f}% > {tolerance_pct*100:.0f}% tolerance)")

    # Hold time comparison
    engine_holds = [t['hold_hours'] for t in engine_trades]
    ft_holds = [t['hold_hours'] for t in ft_trades]

    if engine_holds and ft_holds:
        avg_engine = np.mean(engine_holds)
        avg_ft = np.mean(ft_holds)
        hold_div = abs(avg_engine - avg_ft)
        report['avg_hold_divergence_hrs'] = round(hold_div, 1)
        report['engine_avg_hold'] = round(avg_engine, 1)
        report['ft_avg_hold'] = round(avg_ft, 1)

        if hold_div > 6:  # > 6 hour average divergence is concerning
            report['issues'].append(
                f"Hold time divergence: {avg_engine:.1f}h vs {avg_ft:.1f}h "
                f"(diff={hold_div:.1f}h)")

    # Overall pass/fail
    report['pass'] = (
        report['trade_count_match'] and
        pnl_div <= tolerance_pct and
        len(report['issues']) == 0
    )

    return report


def run_parity_check(tokens: List[str], strategy_fn, engine: Engine,
                     ft_results_dir: str = 'user_data/backtest_results',
                     tolerance: float = 0.10, verbose: bool = True) -> Dict:
    """Run parity check across all validated tokens."""

    print("=" * 70)
    print("PARITY VALIDATION: Engine vs Freqtrade")
    print("=" * 70)

    all_reports = {}
    passes = 0

    for tk in tokens:
        if verbose:
            print(f"\n  {tk}:", end=' ')

        engine_trades = load_engine_trades(strategy_fn, tk, engine)
        ft_trades = load_freqtrade_trades(tk, ft_results_dir)

        report = compare_trades(engine_trades, ft_trades, tk, tolerance)
        all_reports[tk] = report

        if report['pass']:
            passes += 1
            if verbose:
                print(f"PASS  (trades: {report['engine_trades']} vs {report['ft_trades']}, "
                      f"PnL div: {report['pnl_divergence_pct']:.1f}%)")
        else:
            if verbose:
                print(f"FAIL")
                for issue in report['issues']:
                    print(f"    - {issue}")

    print(f"\n{'=' * 70}")
    print(f"RESULT: {passes}/{len(tokens)} tokens pass parity check "
          f"(tolerance: {tolerance*100:.0f}%)")
    if passes < len(tokens):
        print(f"NOTE: Some divergence is expected due to vectorized vs event-driven execution.")
        print(f"      18-720hr holds make <1hr timing skew negligible.")
        print(f"      Check that PnL sign matches (both profitable or both losing).")
    print(f"{'=' * 70}")

    return all_reports


def main():
    parser = argparse.ArgumentParser(description='Parity check: engine vs Freqtrade')
    parser.add_argument('--token', type=str, help='Single token to check')
    parser.add_argument('--tolerance', type=float, default=0.10,
                        help='Max acceptable divergence (0.10 = 10%%)')
    parser.add_argument('--ft-dir', default='user_data/backtest_results',
                        help='Freqtrade backtest results directory')
    parser.add_argument('--strategy', default='s11', choices=['s11', 's09', 'dm'])
    args = parser.parse_args()

    if args.strategy == 's11':
        from strategies.s11_momentum_burst import strategy as strat_fn
    elif args.strategy == 's09':
        from strategies.s09_optimized_trend import strategy as strat_fn
    else:
        from engine import strategy_dual_momentum as strat_fn

    tokens = [args.token] if args.token else CPCV_ROBUST_TOKENS

    engine = Engine()
    run_parity_check(tokens, strat_fn, engine, args.ft_dir, args.tolerance)


class ParityCheck:
    """Signal parity checker: engine (vectorized) vs Freqtrade (event-driven).

    Computes divergence between two signal lists and flags alert/kill thresholds.
    Alert threshold: >10% divergence
    Kill threshold: >20% divergence
    """

    def __init__(self, alert_threshold: float = 0.10, kill_threshold: float = 0.20):
        self.alert_threshold = alert_threshold
        self.kill_threshold = kill_threshold

    def compute_divergence(self, signals_a, signals_b) -> float:
        """Compute fraction of signals that disagree between two flat lists.

        Args:
            signals_a: List of bool signals from source A
            signals_b: List of bool signals from source B

        Returns:
            Fraction of disagreements (0.0 to 1.0)

        Raises:
            ValueError: If lists are empty or have different lengths
        """
        if len(signals_a) == 0 and len(signals_b) == 0:
            raise ValueError("Empty signal lists")
        if len(signals_a) != len(signals_b):
            raise ValueError(
                f"Signal lists must have same length: "
                f"{len(signals_a)} vs {len(signals_b)}"
            )
        disagreements = sum(1 for a, b in zip(signals_a, signals_b) if a != b)
        return disagreements / len(signals_a)

    def check(self, engine_signals, freqtrade_signals) -> dict:
        """Check divergence between engine and Freqtrade signals.

        Args:
            engine_signals: List of bool signals from engine
            freqtrade_signals: List of bool signals from Freqtrade

        Returns:
            Dict with 'divergence' (float), 'alert' (bool), 'kill' (bool)
        """
        div = self.compute_divergence(engine_signals, freqtrade_signals)
        return {
            "divergence": div,
            "alert": div > self.alert_threshold,
            "kill": div > self.kill_threshold,
        }

    def per_token_breakdown(self, engine_signals, freqtrade_signals) -> dict:
        """Per-token divergence breakdown.

        Args:
            engine_signals: Dict of {token: list of bool}
            freqtrade_signals: Dict of {token: list of bool}

        Returns:
            Dict of {token: {"divergence": float, "disagreement_indices": list}}
        """
        result = {}
        for token in engine_signals:
            eng = engine_signals[token]
            ft = freqtrade_signals.get(token, [])
            disagreement_indices = [
                i for i, (a, b) in enumerate(zip(eng, ft)) if a != b
            ]
            div = len(disagreement_indices) / len(eng) if len(eng) > 0 else 0.0
            result[token] = {
                "divergence": div,
                "disagreement_indices": disagreement_indices,
            }
        return result

    def run_full_check(self, tokens, strategy_fn, engine, ft_results_dir,
                       tolerance=None):
        """Run parity check using the original comparison logic."""
        tol = tolerance or 0.10
        return run_parity_check(tokens, strategy_fn, engine, ft_results_dir, tol)


if __name__ == '__main__':
    main()
