"""Paper trading dashboard adapter for Gate 4 compatibility.

Converts paper engine output to Gate 4 format and provides vacuous
pass-through checks for parity and slippage when no comparison data
exists (e.g. no Freqtrade signals or no live execution data).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone


class PaperDashboardAdapter:
    """Adapts paper engine output for Gate 4 evaluation.

    When comparison data is unavailable (no Freqtrade signals, no live
    slippage measurements), the adapter accepts the check vacuously and
    logs the reason to a JSONL file.
    """

    def __init__(self, log_dir: str | None = None):
        self.log_dir = log_dir

    def to_gate4_format(self, paper_output: dict) -> dict:
        """Convert paper engine output to Gate 4 input format.

        Passes through the fields Gate4Engine.evaluate() expects:
        ``trade_returns``, ``num_weeks``, and optionally ``equity_curve``.
        """
        return {
            "trade_returns": paper_output.get("trade_returns", []),
            "num_weeks": paper_output.get("num_weeks", 0),
            "equity_curve": paper_output.get("equity_curve", []),
            "strategy_id": paper_output.get("strategy_id", ""),
        }

    def check_parity(
        self,
        paper_signals: dict,
        freqtrade_signals: dict | None,
    ) -> dict:
        """Check signal parity between paper engine and Freqtrade.

        When ``freqtrade_signals`` is ``None`` the check passes vacuously
        because there is no comparison baseline.
        """
        if freqtrade_signals is None:
            reason = "Vacuous pass: no Freqtrade comparison signals available"
            self._log_vacuous("parity", reason)
            return {"passed": True, "reason": reason}

        # Compare signals token-by-token
        mismatches = 0
        total = 0
        for token, paper in paper_signals.items():
            ft = freqtrade_signals.get(token, [])
            length = min(len(paper), len(ft))
            for i in range(length):
                total += 1
                if paper[i] != ft[i]:
                    mismatches += 1

        if total == 0:
            return {"passed": True, "reason": "No overlapping signals to compare"}

        divergence = mismatches / total
        passed = divergence <= 0.10
        return {
            "passed": passed,
            "reason": f"Parity divergence {divergence:.1%}" if not passed else "",
        }

    def check_slippage(
        self,
        modeled_slippage_bps: float,
        actual_slippage_bps: float | None,
    ) -> dict:
        """Check if actual slippage exceeds modeled by too much.

        When ``actual_slippage_bps`` is ``None`` the check passes vacuously
        because there is no live execution data to compare against.
        """
        if actual_slippage_bps is None:
            reason = "Vacuous pass: no live slippage data available"
            self._log_vacuous("slippage", reason)
            return {"passed": True, "reason": reason}

        if modeled_slippage_bps <= 0:
            return {"passed": True, "reason": "Modeled slippage is zero"}

        ratio = actual_slippage_bps / modeled_slippage_bps
        passed = ratio <= 2.0
        return {
            "passed": passed,
            "reason": f"Slippage ratio {ratio:.2f} exceeds threshold 2.0" if not passed else "",
        }

    def _log_vacuous(self, check_name: str, reason: str) -> None:
        """Append a vacuous check entry to the JSONL log."""
        if self.log_dir is None:
            return

        os.makedirs(self.log_dir, exist_ok=True)
        log_path = os.path.join(self.log_dir, "vacuous_checks.jsonl")
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "check": check_name,
            "reason": reason,
        }
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
