"""
Compare Instances — Cross-instance metrics, benchmarks, and degradation reporting.

Computes per-strategy metrics (Sortino, Calmar, profit factor, win rate),
dual-benchmark alpha, head-to-head comparison, regime coverage, uptime,
and backtest-to-paper degradation ratios.
"""

import csv
import json
import math
import os
from typing import List, Optional


class CompareInstances:
    """Cross-instance comparison and reporting.

    Provides per-strategy metrics, dual-benchmark alpha, head-to-head
    comparison, regime coverage, uptime reporting, and degradation analysis.
    """

    MIN_REGIME_COUNT = 2
    UPTIME_THRESHOLD = 0.95
    MAX_GAP_SECONDS = 300  # 5 minutes

    # ── Per-strategy metrics ───────────────────────────────────────────

    def compute_sortino(self, trades_path: str, target: float = 0.0) -> float:
        """Compute Sortino ratio from trades.jsonl.

        Sortino = (mean_return - target) / downside_deviation
        Only negative returns contribute to downside std.
        """
        trades = self._load_trades(trades_path)
        returns = [t.get("return_pct", t.get("pnl", 0) / 1000.0) for t in trades]

        if not returns:
            return 0.0

        mean_ret = sum(returns) / len(returns)
        downside = [r for r in returns if r < target]

        if not downside:
            return float("inf") if mean_ret > target else 0.0

        downside_sq = sum((r - target) ** 2 for r in downside) / len(downside)
        downside_dev = math.sqrt(downside_sq)

        if downside_dev == 0:
            return 0.0

        return (mean_ret - target) / downside_dev

    def compute_calmar(self, equity_path: str) -> float:
        """Compute Calmar ratio from equity.csv.

        Calmar = annualized_return / max_drawdown
        """
        rows = self._load_equity(equity_path)
        if len(rows) < 2:
            return 0.0

        equities = [float(r["equity"]) for r in rows]
        start_eq = equities[0]
        end_eq = equities[-1]

        total_return = (end_eq - start_eq) / start_eq if start_eq > 0 else 0.0

        # Annualize (assume daily data)
        n_days = len(equities)
        annual_factor = 365.0 / max(n_days, 1)
        annualized_return = total_return * annual_factor

        # Max drawdown
        peak = equities[0]
        max_dd = 0.0
        for eq in equities:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)

        if max_dd == 0:
            return float("inf") if annualized_return > 0 else 0.0

        return annualized_return / max_dd

    def compute_profit_factor(self, trades_path: str) -> float:
        """Compute profit factor = gross_profit / gross_loss."""
        trades = self._load_trades(trades_path)

        gross_profit = sum(t["pnl"] for t in trades if t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))

        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0

        return gross_profit / gross_loss

    def compute_win_rate(self, trades_path: str) -> float:
        """Compute win rate = winning_trades / total_trades."""
        trades = self._load_trades(trades_path)
        if not trades:
            return 0.0

        winners = sum(1 for t in trades if t["pnl"] > 0)
        return winners / len(trades)

    # ── Dual-benchmark alpha ───────────────────────────────────────────

    def alpha_vs_benchmark(self, strategy_return: float,
                           benchmark_return: float) -> float:
        """Compute alpha = strategy_return - benchmark_return."""
        return strategy_return - benchmark_return

    # ── Head-to-head comparison ────────────────────────────────────────

    def head_to_head(self, instance_a: dict, instance_b: dict) -> dict:
        """Compare two instances head-to-head.

        Args:
            instance_a: Dict with instance_id, sharpe, sortino, total_return
            instance_b: Dict with instance_id, sharpe, sortino, total_return

        Returns:
            Dict with winner, metrics_compared, per-metric breakdown
        """
        metrics = ["sharpe", "sortino", "total_return"]
        a_wins = 0
        b_wins = 0
        comparison = {}

        for metric in metrics:
            a_val = instance_a.get(metric, 0)
            b_val = instance_b.get(metric, 0)
            if a_val > b_val:
                a_wins += 1
                comparison[metric] = instance_a["instance_id"]
            elif b_val > a_val:
                b_wins += 1
                comparison[metric] = instance_b["instance_id"]
            else:
                comparison[metric] = "tie"

        winner = (instance_a["instance_id"] if a_wins > b_wins
                  else instance_b["instance_id"] if b_wins > a_wins
                  else "tie")

        return {
            "winner": winner,
            "metrics_compared": comparison,
            "a_wins": a_wins,
            "b_wins": b_wins,
        }

    # ── Regime coverage ────────────────────────────────────────────────

    def regime_coverage_report(self, observed_regimes: list) -> dict:
        """Check if enough distinct regimes were observed.

        Args:
            observed_regimes: List of regime labels observed

        Returns:
            Dict with sufficient (bool) and observed_regimes (list)
        """
        unique = list(set(observed_regimes))
        return {
            "sufficient": len(unique) >= self.MIN_REGIME_COUNT,
            "observed_regimes": unique,
            "regime_count": len(unique),
        }

    # ── Uptime report ──────────────────────────────────────────────────

    def uptime_report(self, total_expected_seconds: int,
                      total_downtime_seconds: int,
                      max_gap_seconds: int = 0) -> dict:
        """Compute uptime statistics.

        Args:
            total_expected_seconds: Total expected uptime in seconds
            total_downtime_seconds: Total downtime in seconds
            max_gap_seconds: Longest single gap in seconds

        Returns:
            Dict with uptime_pct, passed, prolonged_gap_flagged
        """
        uptime_pct = 1.0 - (total_downtime_seconds / total_expected_seconds
                             if total_expected_seconds > 0 else 0)
        return {
            "uptime_pct": uptime_pct,
            "passed": uptime_pct >= self.UPTIME_THRESHOLD,
            "total_downtime_seconds": total_downtime_seconds,
            "prolonged_gap_flagged": max_gap_seconds > self.MAX_GAP_SECONDS,
        }

    # ── Degradation report ─────────────────────────────────────────────

    def load_backtest_baseline(self, sweep_dir: str,
                               strategy: str) -> dict:
        """Load backtest baseline metrics from sweep summary files.

        Args:
            sweep_dir: Directory containing sweep_summary_*.json files
            strategy: Strategy name

        Returns:
            Dict of baseline metrics (or per-token metrics)
        """
        import glob
        pattern = os.path.join(sweep_dir, f"sweep_summary_{strategy}.json")
        files = sorted(glob.glob(pattern))
        if not files:
            # Try generic pattern
            pattern = os.path.join(sweep_dir, "sweep_summary_*.json")
            files = sorted(glob.glob(pattern))

        if not files:
            return {}

        with open(files[-1]) as f:
            data = json.load(f)

        # Return strategy-specific data if available
        if strategy in data:
            return data[strategy]
        return data

    def compute_degradation(self, backtest_metrics: dict,
                            paper_metrics: dict) -> dict:
        """Compute degradation ratios: paper_metric / backtest_metric.

        Args:
            backtest_metrics: Dict of {metric: value} from backtest
            paper_metrics: Dict of {metric: value} from paper trading

        Returns:
            Dict of {metric: degradation_ratio}
        """
        result = {}
        for metric in backtest_metrics:
            bt_val = backtest_metrics[metric]
            paper_val = paper_metrics.get(metric, 0)
            if bt_val != 0:
                result[metric] = paper_val / bt_val
            else:
                result[metric] = 0.0
        return result

    def degradation_report(self, backtest_metrics: dict,
                           paper_metrics: dict) -> dict:
        """Produce a degradation report with per-metric ratios.

        Args:
            backtest_metrics: Dict of {metric: value} from backtest
            paper_metrics: Dict of {metric: value} from paper trading

        Returns:
            Dict with degradation_ratios and summary
        """
        ratios = self.compute_degradation(backtest_metrics, paper_metrics)
        return {
            "degradation_ratios": ratios,
            "backtest_metrics": backtest_metrics,
            "paper_metrics": paper_metrics,
        }

    # ── Internal helpers ───────────────────────────────────────────────

    def _load_trades(self, trades_path: str) -> list:
        """Load trades from a JSONL file."""
        trades = []
        with open(trades_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    trades.append(json.loads(line))
        return trades

    def _load_equity(self, equity_path: str) -> list:
        """Load equity data from a CSV file."""
        rows = []
        with open(equity_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
        return rows
