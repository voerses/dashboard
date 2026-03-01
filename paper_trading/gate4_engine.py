"""
Gate 4 Decision Engine — Paper trading → production go-live verdicts.

Implements SPRT (Sequential Probability Ratio Test) and kill threshold
evaluation for deciding whether a strategy should go live.
"""

import json
import math
from typing import List, Optional


class Gate4Engine:
    """Evaluates paper trading results against pre-committed thresholds.

    Verdicts: PASS, FAIL, CONTINUE, INSUFFICIENT DATA

    SPRT: H0: Sharpe=0 (no edge), H1: Sharpe=target, alpha=0.05, beta=0.10
    """

    def __init__(self, thresholds: dict):
        self.thresholds = thresholds

    @classmethod
    def from_file(cls, path: str) -> "Gate4Engine":
        """Load thresholds from a JSON file."""
        if not path or not isinstance(path, str):
            raise FileNotFoundError(f"Invalid path: {path}")
        with open(path) as f:
            thresholds = json.load(f)
        return cls(thresholds=thresholds)

    def evaluate(self, trade_returns: List[float], num_weeks: int,
                 current_drawdown: float = 0.0,
                 realized_sharpe: float = None) -> dict:
        """Evaluate paper trading results.

        Args:
            trade_returns: List of per-trade returns (e.g., [0.02, -0.01, ...])
            num_weeks: Number of weeks of continuous operation
            current_drawdown: Current drawdown as fraction (e.g., 0.15 = 15%)
            realized_sharpe: Realized Sharpe ratio from paper period

        Returns:
            dict with 'verdict' and supporting data
        """
        min_trades = self.thresholds.get("min_trades", 50)
        min_weeks = self.thresholds.get("min_weeks", 4)

        # Minimum data checks
        if len(trade_returns) < min_trades or num_weeks < min_weeks:
            return {
                "verdict": "INSUFFICIENT DATA",
                "reason": f"Need >= {min_trades} trades and >= {min_weeks} weeks. "
                          f"Have {len(trade_returns)} trades and {num_weeks} weeks.",
                "trade_count": len(trade_returns),
                "num_weeks": num_weeks,
            }

        # Kill threshold checks
        max_dd_kill = self.thresholds.get("kill_max_drawdown", 0.25)
        if current_drawdown > max_dd_kill:
            return {
                "verdict": "FAIL",
                "reason": f"Max drawdown {current_drawdown:.1%} exceeds "
                          f"kill threshold {max_dd_kill:.1%}",
            }

        min_sharpe_kill = self.thresholds.get("kill_min_sharpe", -0.5)
        if realized_sharpe is not None and realized_sharpe < min_sharpe_kill:
            return {
                "verdict": "FAIL",
                "reason": f"Realized Sharpe {realized_sharpe:.2f} below "
                          f"kill threshold {min_sharpe_kill:.2f}",
            }

        # SPRT (Sequential Probability Ratio Test)
        target_sharpe = self.thresholds.get("target_sharpe", 0.5)
        alpha = self.thresholds.get("alpha", 0.05)
        beta = self.thresholds.get("beta", 0.10)

        sprt_result = self._run_sprt(trade_returns, target_sharpe, alpha, beta)

        if sprt_result == "reject":
            return {
                "verdict": "FAIL",
                "reason": "SPRT rejects H1 (strategy has edge)",
            }
        elif sprt_result == "accept":
            return {
                "verdict": "PASS",
                "reason": "SPRT accepts H1 (strategy has edge)",
            }
        else:
            return {
                "verdict": "CONTINUE",
                "reason": "SPRT inconclusive — need more data",
            }

    def _run_sprt(self, returns: List[float], target_sharpe: float,
                  alpha: float, beta: float) -> str:
        """Run Sequential Probability Ratio Test.

        H0: mean return = 0 (no edge)
        H1: mean return = target (has edge)

        Returns: 'accept', 'reject', or 'continue'
        """
        if not returns:
            return "continue"

        # Compute test statistic boundaries
        A = (1 - beta) / alpha        # Upper boundary (accept H1)
        B = beta / (1 - alpha)        # Lower boundary (reject H1)

        log_A = math.log(A)
        log_B = math.log(B)

        # Estimate volatility from returns
        n = len(returns)
        mean_ret = sum(returns) / n
        var_ret = sum((r - mean_ret) ** 2 for r in returns) / max(n - 1, 1)
        std_ret = math.sqrt(var_ret) if var_ret > 0 else 0.01

        # Target mean under H1 (annualized Sharpe → per-trade mean)
        # Approximate: target_mean = target_sharpe * std_ret / sqrt(252)
        # Simplified: use target_sharpe * std_ret as the per-trade target
        target_mean = target_sharpe * std_ret / math.sqrt(max(n, 1))

        if target_mean == 0 or std_ret == 0:
            return "continue"

        # Cumulative log-likelihood ratio
        log_lr = 0.0
        for r in returns:
            # Log likelihood ratio for normal distribution
            log_lr += (target_mean * r / (std_ret ** 2)) - \
                      (target_mean ** 2 / (2 * std_ret ** 2))

            if log_lr >= log_A:
                return "accept"
            elif log_lr <= log_B:
                return "reject"

        return "continue"

    def check_degradation(self, backtest_sortino: float = None,
                          paper_sortino: float = None,
                          backtest_max_dd: float = None,
                          paper_max_dd: float = None) -> dict:
        """Check backtest-to-paper degradation ratios.

        Returns dict with 'passed' bool and 'reason' string.
        """
        reasons = []

        # Sortino degradation
        if backtest_sortino is not None and paper_sortino is not None:
            min_ratio = self.thresholds.get("sortino_degradation_min", 0.6)
            if backtest_sortino > 0:
                ratio = paper_sortino / backtest_sortino
                if ratio < min_ratio:
                    reasons.append(
                        f"Sortino degradation {ratio:.2f} below "
                        f"threshold {min_ratio}"
                    )

        # Max DD ratio
        if backtest_max_dd is not None and paper_max_dd is not None:
            max_ratio = self.thresholds.get("max_dd_ratio", 1.5)
            if backtest_max_dd > 0:
                ratio = paper_max_dd / backtest_max_dd
                if ratio > max_ratio:
                    reasons.append(
                        f"Max drawdown ratio {ratio:.2f} exceeds "
                        f"threshold {max_ratio}"
                    )

        if reasons:
            return {"passed": False, "reason": "; ".join(reasons)}
        return {"passed": True, "reason": ""}

    def check_slippage(self, modeled_slippage_bps: float,
                       actual_slippage_bps: float) -> dict:
        """Check if actual slippage exceeds modeled by too much."""
        max_ratio = self.thresholds.get("slippage_ratio_max", 2.0)
        if modeled_slippage_bps > 0:
            ratio = actual_slippage_bps / modeled_slippage_bps
            if ratio > max_ratio:
                return {
                    "passed": False,
                    "reason": f"Slippage ratio {ratio:.2f} exceeds "
                              f"threshold {max_ratio}",
                }
        return {"passed": True, "reason": ""}

    def check_fill_rate(self, fill_rate: float) -> dict:
        """Check if fill rate is above minimum threshold."""
        min_rate = self.thresholds.get("fill_rate_min", 0.95)
        if fill_rate < min_rate:
            return {
                "passed": False,
                "reason": f"Fill rate {fill_rate:.1%} below "
                          f"threshold {min_rate:.1%}",
            }
        return {"passed": True, "reason": ""}

    def check_sharpe_shortfall(self, in_sample_sharpe: float,
                               paper_sharpe: float) -> dict:
        """Check if Sharpe shortfall exceeds threshold."""
        max_shortfall = self.thresholds.get("sharpe_shortfall_max", 1.0)
        shortfall = in_sample_sharpe - paper_sharpe
        if shortfall > max_shortfall:
            return {
                "passed": False,
                "reason": f"Sharpe shortfall {shortfall:.2f} exceeds "
                          f"threshold {max_shortfall}",
            }
        return {"passed": True, "reason": ""}

    def check_rolling_sortino(self, consecutive_negative_days: int) -> dict:
        """Check if rolling Sortino has been negative too long."""
        max_days = self.thresholds.get("rolling_sortino_negative_days_max", 30)
        if consecutive_negative_days > max_days:
            return {
                "passed": False,
                "reason": f"Rolling Sortino negative for "
                          f"{consecutive_negative_days} days, "
                          f"threshold is {max_days}",
            }
        return {"passed": True, "reason": ""}

    def check_regime_coverage(self, observed_regimes: list) -> dict:
        """Check if enough distinct regimes were observed."""
        min_count = self.thresholds.get("min_regime_count", 2)
        unique_regimes = len(set(observed_regimes))
        if unique_regimes < min_count:
            return {
                "passed": False,
                "reason": f"Only {unique_regimes} regime(s) observed, "
                          f"need >= {min_count}",
            }
        return {"passed": True, "reason": ""}

    def check_parity_divergence(self, divergence: float) -> dict:
        """Check if parity divergence exceeds threshold."""
        max_div = self.thresholds.get("parity_divergence_max", 0.10)
        if divergence > max_div:
            return {
                "passed": False,
                "reason": f"Parity divergence {divergence:.1%} exceeds "
                          f"threshold {max_div:.1%}",
            }
        return {"passed": True, "reason": ""}

    def go_live_check(self, backtest_sortino: float, paper_sortino: float,
                      backtest_max_dd: float, paper_max_dd: float,
                      modeled_slippage_bps: float, actual_slippage_bps: float,
                      fill_rate: float, in_sample_sharpe: float,
                      paper_sharpe: float, consecutive_negative_days: int,
                      observed_regimes: list, parity_divergence: float) -> dict:
        """Run all go-live checks and produce final verdict.

        ALL checks must pass for PASS verdict.
        """
        checks = [
            self.check_degradation(
                backtest_sortino=backtest_sortino,
                paper_sortino=paper_sortino,
                backtest_max_dd=backtest_max_dd,
                paper_max_dd=paper_max_dd,
            ),
            self.check_slippage(modeled_slippage_bps, actual_slippage_bps),
            self.check_fill_rate(fill_rate),
            self.check_sharpe_shortfall(in_sample_sharpe, paper_sharpe),
            self.check_rolling_sortino(consecutive_negative_days),
            self.check_regime_coverage(observed_regimes),
            self.check_parity_divergence(parity_divergence),
        ]

        failures = [c for c in checks if not c["passed"]]

        if failures:
            reasons = [f["reason"] for f in failures]
            return {
                "verdict": "FAIL",
                "failures": reasons,
                "checks_passed": len(checks) - len(failures),
                "checks_total": len(checks),
            }

        return {
            "verdict": "PASS",
            "checks_passed": len(checks),
            "checks_total": len(checks),
        }
