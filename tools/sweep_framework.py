"""
Sweep Framework — Canonical base for all parameter sweeps.
============================================================

Eliminates ~200 lines of boilerplate from every sweep tool:
  - Parallel backtest execution (ProcessPoolExecutor)
  - Canonical parse_result() for stdout metric extraction
  - Strategy file generation from template
  - Result ranking by any metric
  - JSON output with full metadata

Usage:
    from tools.sweep_framework import SweepFramework

    TEMPLATE = '''...strategy code with {leverage}, {signal_code}...'''
    configs = [
        {"label": "lev2_ema", "leverage": 2, "signal_code": "...", "capital": 200000},
    ]
    results = SweepFramework("my_sweep", TEMPLATE, configs).run(workers=4, target_metric='calmar')
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STRATEGY_DIR = PROJECT_ROOT / "strategies"
RESULTS_DIR = PROJECT_ROOT / "results" / "v4"
VENV_PYTHON = "/workspace/venv/bin/python"


@dataclass
class BacktestResult:
    """Standardized backtest result."""
    label: str = ""
    signal: str = ""
    config: dict = field(default_factory=dict)

    # Core metrics
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    calmar: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    max_dd_pct: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    win_rate: float = 0.0
    avg_hold_hours: float = 0.0

    # Status
    error: str = ""
    runtime_s: float = 0.0


def parse_result(output: str) -> dict:
    """Canonical metric parser — extracts metrics from v4 backtest stdout.

    Single source of truth for all sweep tools. Never duplicate this.
    """
    metrics = {}
    for line in output.split('\n'):
        line_stripped = line.strip()

        if 'Total Return:' in line:
            m = re.search(r'([+-]?\d+[\d,]*\.?\d*)%', line)
            if m:
                metrics['total_return_pct'] = float(m.group(1).replace(',', ''))

        elif 'Annualized:' in line:
            m = re.search(r'([+-]?\d+[\d,]*\.?\d*)%', line)
            if m:
                metrics['annualized_return_pct'] = float(m.group(1).replace(',', ''))

        elif 'Calmar:' in line:
            m = re.search(r'([+-]?\d+[\d,]*\.?\d*)', line.split('Calmar:')[1])
            if m:
                metrics['calmar'] = float(m.group(1).replace(',', ''))

        elif 'Sharpe:' in line and 'Sortino' not in line and 'Deflated' not in line:
            m = re.search(r'([+-]?\d+[\d,]*\.?\d*)', line.split('Sharpe:')[1])
            if m:
                metrics['sharpe'] = float(m.group(1).replace(',', ''))

        elif 'Sortino:' in line:
            m = re.search(r'([+-]?\d+[\d,]*\.?\d*)', line.split('Sortino:')[1])
            if m:
                metrics['sortino'] = float(m.group(1).replace(',', ''))

        elif 'Max Drawdown:' in line and 'Duration' not in line:
            m = re.search(r'([+-]?\d+[\d,]*\.?\d*)%', line)
            if m:
                metrics['max_dd_pct'] = float(m.group(1).replace(',', ''))

        elif 'Total Trades:' in line:
            m = re.search(r'(\d+)', line.split('Total Trades:')[1])
            if m:
                metrics['total_trades'] = int(m.group(1))

        elif 'Win Rate:' in line:
            m = re.search(r'(\d+\.?\d*)%', line)
            if m:
                metrics['win_rate'] = float(m.group(1))

        elif 'Profit Factor:' in line:
            m = re.search(r'(\d+\.?\d*)', line.split('Profit Factor:')[1])
            if m:
                metrics['profit_factor'] = float(m.group(1))

        elif 'Avg Hold:' in line:
            m = re.search(r'(\d+\.?\d*)', line.split('Avg Hold:')[1])
            if m:
                metrics['avg_hold_hours'] = float(m.group(1))

    return metrics


def _run_single_backtest(args: tuple) -> BacktestResult:
    """Worker function for parallel backtest execution.

    Args is a tuple: (label, signal_name, strategy_code, backtest_cmd, config_dict)
    """
    label, signal_name, strategy_code, backtest_cmd, config_dict, strategy_file = args
    result = BacktestResult(label=label, signal=signal_name, config=config_dict)

    try:
        # Write strategy file
        with open(strategy_file, 'w') as f:
            f.write(strategy_code)

        # Run backtest
        t0 = time.perf_counter()
        proc = subprocess.run(
            backtest_cmd,
            capture_output=True, text=True,
            timeout=300,
            cwd=str(PROJECT_ROOT),
        )
        result.runtime_s = time.perf_counter() - t0

        output = proc.stdout + proc.stderr
        metrics = parse_result(output)

        # Map to result
        for k, v in metrics.items():
            if hasattr(result, k):
                setattr(result, k, v)

        if not metrics:
            result.error = f"No metrics parsed. stderr: {proc.stderr[-500:]}"

    except subprocess.TimeoutExpired:
        result.error = "Timeout (300s)"
    except Exception as e:
        result.error = str(e)

    return result


class SweepFramework:
    """Canonical sweep runner for all strategy parameter sweeps.

    Usage:
        fw = SweepFramework("my_sweep", TEMPLATE, configs)
        results = fw.run(workers=4, target_metric='calmar')
    """

    def __init__(
        self,
        name: str,
        template: str,
        configs: list[dict],
        strategy_id: str = "s98",
    ):
        self.name = name
        self.template = template
        self.configs = configs
        self.strategy_id = strategy_id

    def _build_backtest_cmd(self, config: dict) -> list[str]:
        """Build v4/portfolio_backtest.py command from config."""
        capital = config.get('capital', 200000)
        months = config.get('months', 12)
        market = config.get('market', 'perp')
        exchange = config.get('exchange', 'binance')

        return [
            VENV_PYTHON, 'v4/portfolio_backtest.py',
            '--strategy', self.strategy_id,
            '--months', str(months),
            '--capital', str(capital),
            '--market', market,
            '--exchange', exchange,
        ]

    def _get_strategy_file(self, index: int) -> str:
        """Get strategy file path. For parallel execution, use index-based temp files."""
        return str(STRATEGY_DIR / f"{self.strategy_id}_sweep_{index}.py")

    def run(
        self,
        workers: int = 1,
        target_metric: str = 'calmar',
        min_trades: int = 30,
        verbose: bool = True,
    ) -> list[BacktestResult]:
        """Run all configs and return ranked results.

        For workers=1, runs sequentially (can reuse same strategy file).
        For workers>1, uses separate strategy files per worker to avoid conflicts.
        """
        # Build work items
        work_items = []
        for i, config in enumerate(self.configs):
            label = config.get('label', f'config_{i}')
            signal_name = config.get('signal', label)

            # Generate strategy code from template
            code = self.template.format(**config)

            # Build backtest command
            cmd = self._build_backtest_cmd(config)

            # Strategy file — use the canonical file for sequential, indexed for parallel
            if workers <= 1:
                sf = str(STRATEGY_DIR / f"{self.strategy_id}_sr_breakout_swing.py")
            else:
                sf = self._get_strategy_file(i)

            work_items.append((label, signal_name, code, cmd, config, sf))

        # Execute
        results = []
        total = len(work_items)

        if workers <= 1:
            # Sequential execution
            for i, item in enumerate(work_items):
                result = _run_single_backtest(item)
                results.append(result)
                if verbose:
                    self._print_progress(i + 1, total, result)
        else:
            # Parallel execution
            with ProcessPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(_run_single_backtest, item): i
                           for i, item in enumerate(work_items)}
                done_count = 0
                for future in as_completed(futures):
                    done_count += 1
                    result = future.result()
                    results.append(result)
                    if verbose:
                        self._print_progress(done_count, total, result)

            # Cleanup temp strategy files
            for i in range(total):
                sf = self._get_strategy_file(i)
                if os.path.exists(sf):
                    os.remove(sf)

        # Filter and rank
        valid = [r for r in results if not r.error and r.total_trades >= min_trades]
        valid.sort(key=lambda r: getattr(r, target_metric, 0), reverse=True)

        # Print summary
        if verbose:
            self._print_summary(valid, target_metric)

        # Save results
        self._save_results(results, target_metric)

        return valid

    def _print_progress(self, done: int, total: int, result: BacktestResult):
        err = f" ERR: {result.error[:60]}" if result.error else ""
        print(
            f"[{done}/{total}] {result.label:30s} | "
            f"Ann={result.annualized_return_pct:>8.1f}% "
            f"Cal={result.calmar:>6.2f} "
            f"DD={result.max_dd_pct:>6.1f}% "
            f"Trd={result.total_trades:>4d} "
            f"PF={result.profit_factor:>5.2f} "
            f"({result.runtime_s:.1f}s)"
            f"{err}"
        )

    def _print_summary(self, valid: list[BacktestResult], target_metric: str):
        print(f"\n{'=' * 110}")
        print(f"TOP 10 BY {target_metric.upper()} ({len(valid)} valid results)")
        print('=' * 110)
        header = (f"{'Label':30s} {'Signal':15s} {'Annual%':>8} {'Calmar':>7} "
                  f"{'MaxDD%':>7} {'Sharpe':>7} {'Sortino':>8} "
                  f"{'Trades':>6} {'WinR%':>6} {'PF':>5}")
        print(header)
        print('-' * 110)
        for r in valid[:10]:
            print(
                f"{r.label:30s} {r.signal:15s} "
                f"{r.annualized_return_pct:>8.1f} {r.calmar:>7.2f} "
                f"{r.max_dd_pct:>7.1f} {r.sharpe:>7.2f} "
                f"{r.sortino:>8.2f} {r.total_trades:>6d} "
                f"{r.win_rate:>6.1f} {r.profit_factor:>5.2f}"
            )

    def _save_results(self, results: list[BacktestResult], target_metric: str):
        os.makedirs(str(RESULTS_DIR), exist_ok=True)
        outpath = RESULTS_DIR / f"{self.name}.json"
        data = {
            "sweep_name": self.name,
            "target_metric": target_metric,
            "total_configs": len(results),
            "results": [asdict(r) for r in results],
        }
        with open(outpath, 'w') as f:
            json.dump(data, f, indent=2, default=str)
        print(f"\nResults saved to {outpath}")


# ---------------------------------------------------------------------------
# Convenience: run a quick single-strategy backtest and return metrics dict
# ---------------------------------------------------------------------------

def quick_backtest(
    strategy_id: str,
    months: int = 12,
    capital: int = 200000,
    market: str = "perp",
    timeout: int = 300,
) -> dict:
    """Run a single backtest and return parsed metrics dict.

    Useful for programmatic validation steps.
    """
    cmd = [
        VENV_PYTHON, 'v4/portfolio_backtest.py',
        '--strategy', strategy_id,
        '--months', str(months),
        '--capital', str(capital),
        '--market', market,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, cwd=str(PROJECT_ROOT),
        )
        return parse_result(proc.stdout + proc.stderr)
    except Exception as e:
        return {"error": str(e)}
