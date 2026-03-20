# Strategy Tooling Roadmap

Tools to build for faster, more accurate strategy development. Each spec is implementation-ready.

---

## 1. `tools/sweep_framework.py` — Standardized Sweep Base

**Problem:** 11 sweep tools each duplicate parse_result(), strategy template writing, config running, and result ranking. Adding a new sweep means copying ~200 lines of boilerplate.

**Build:**
```python
# sweep_framework.py provides:

class SweepFramework:
    def __init__(self, name: str, template: str, configs: list[dict]):
        """
        name: sweep name for output file
        template: strategy code template with {placeholders}
        configs: list of dicts, each dict has template vars + backtest params
        """

    def run(self, workers=1, target_metric='calmar'):
        """
        For each config:
        1. Write strategy file from template
        2. Run v4/portfolio_backtest.py with config's backtest params
        3. Parse stdout for metrics
        4. Rank by target_metric
        5. Save to results/v4/{name}.json
        """

    @staticmethod
    def parse_result(output: str) -> dict:
        """Single canonical parse_result function."""

# CLI: python tools/sweep_framework.py (not run directly, imported by sweep scripts)
```

**New sweep script becomes ~30 lines:**
```python
from sweep_framework import SweepFramework

TEMPLATE = '''...strategy code with {leverage}, {squeeze_mult}...'''

configs = [
    {"label": "lev5", "leverage": 5, "squeeze_mult": 0.8, "capital": 200000, ...},
    {"label": "lev7", "leverage": 7, "squeeze_mult": 0.8, "capital": 200000, ...},
]

SweepFramework("my_sweep", TEMPLATE, configs).run(workers=4, target_metric='calmar')
```

**CLI flags inherited by all sweeps:** `--workers N`, `--target-metric`, `--max-combos`, `--capital`, `--months`.

---

## 2. `tools/auto_filter_discovery.py` — Factorial Filter Experiment

**Problem:** Discovering that regime filtering was the #1 alpha driver took 5 iterative phases. A factorial experiment would have found it in phase 1.

**Build:**
```python
# Takes a base signal (e.g., MACD zero-cross) and tests all 2^N combinations
# of N available filters to find which filters contribute most to PF/Calmar.

FILTERS = {
    'regime':    'uptrend/downtrend only',
    'squeeze':   'BB width < X% of rolling avg',
    'liquid':    'ADV > $1B',
    'adx':       'ADX > 20',
    'di':        '+DI > -DI for longs, vice versa',
    'ema_align': 'EMA 10>20>50 for longs',
    'volume':    'volume > 1.2x rolling avg',
    'rsi':       'RSI > 50 for longs, < 50 for shorts',
}

# For N=8 filters, 2^8 = 256 combinations.
# Run each, record PF/Calmar/DD/trades.

# Output: marginal contribution table
# Filter      | Marginal PF | Marginal Calmar | Trade Reduction
# regime      | +0.48       | +2.50           | -40%
# squeeze     | +0.35       | +0.80           | -25%
# liquid      | +0.18       | +0.30           | -60%
# ema_align   | +0.05       | +0.10           | -15%
# ...

# Also outputs: interaction effects (filter A + filter B together > sum of parts)
```

**CLI:**
```bash
python tools/auto_filter_discovery.py --signal macd_zero_cross --leverage 7 --workers 4
```

---

## 3. `tools/robust_sweep.py` — Walk-Forward Validation in Sweeps

**Problem:** All sweeps run single 12-month in-sample backtests. No out-of-sample check. Overfitting risk is high when testing 30+ configs.

**Build:**
```python
# Wraps any sweep config with rolling walk-forward:
# - Split 12 months into 4 x 3-month windows
# - For each window: train on other 3 windows, test on held-out window
# - Report both IS (in-sample) and OOS (out-of-sample) metrics
# - Kill configs where OOS Calmar < 50% of IS Calmar

# Input: same config list as any sweep tool
# Output: results with additional fields:
#   is_annual, is_calmar, is_pf  (in-sample)
#   oos_annual, oos_calmar, oos_pf  (out-of-sample)
#   wfe (walk-forward efficiency = oos_calmar / is_calmar)

# CLI:
# python tools/robust_sweep.py --config my_configs.json --folds 4 --workers 4
```

---

## 4. `tools/fee_impact.py` — Leverage-Fee Breakeven Analyzer

**Problem:** High leverage destroys edge through fee drag but this only became obvious after manually testing leverage ladders. Need automatic leverage ceiling detection.

**Build:**
```python
# Takes a strategy's trade log (or runs the strategy at leverage=1)
# and models fee impact at each leverage level.

# Input: strategy_id or trade log JSON
# Output:
#   Leverage | Gross PnL | Fees    | Net PnL | Fee% of Gross | Net PF
#   1x       | $50,000   | $7,000  | $43,000 | 14%           | 1.86
#   3x       | $150,000  | $21,000 | $129,000| 14%           | 1.86
#   5x       | $250,000  | $35,000 | $215,000| 14%           | 1.86
#   7x       | $350,000  | $50,000 | $300,000| 14%           | 2.11  ← optimal
#   10x      | $500,000  | $118,000| $382,000| 24%           | 1.85
#   15x      | $750,000  | $250,000| $500,000| 33%           | 1.50
#   20x      | $1,000,000| $400,000| $600,000| 40%           | 1.30
#
#   Optimal leverage: 7x (max net Calmar)
#   Fee breakeven leverage: 25x (fees consume 100% of gross PnL)
#   Impact breakeven: 12x (market impact > 50% of spread cost)

# CLI:
# python tools/fee_impact.py --strategy s98 --leverage-range 1,25
```

---

## 5. `tools/regime_decompose.py` — Per-Regime Performance Breakdown

**Problem:** A strategy showing +113% annual could be +300% in uptrends and -50% in downtrends. We don't decompose returns by regime automatically.

**Build:**
```python
# Takes a strategy's equity curve + regime series and splits performance by regime.

# Input: strategy_id (runs backtest and captures per-bar regime + equity)
# Output:
#   Regime    | % Time | Return | Calmar | DD    | PF   | Trades | Win%
#   UPTREND   | 35%    | +180%  | 8.2    | -22%  | 2.8  | 52     | 62%
#   DOWNTREND | 25%    | +45%   | 1.8    | -25%  | 1.6  | 41     | 54%
#   RANGE     | 20%    | -8%    | -0.4   | -18%  | 0.9  | 28     | 46%
#   QUIET     | 15%    | +2%    | 0.3    | -7%   | 1.1  | 8      | 50%
#   CRISIS    | 5%     | 0%     | n/a    | 0%    | n/a  | 0      | n/a
#
# Also: regime transition analysis — performance in first 24h after regime change
# Also: worst regime stretch — longest consecutive loss period by regime

# CLI:
# python tools/regime_decompose.py --strategy s98 --months 12
```

---

## 6. `tools/config_correlation.py` — Trade Overlap Between Configs

**Problem:** When testing 30+ configs in a sweep, many produce nearly identical trades. Can't distinguish truly different strategies from parameter variations of the same trades.

**Build:**
```python
# Takes a sweep result file and re-runs top N configs to capture trade logs.
# Computes pairwise Jaccard similarity of (entry_date, token) pairs.

# Input: sweep result JSON (e.g., results/v4/concentration_push.json)
# Output:
#   Config A         | Config B         | Overlap | Unique A | Unique B
#   bb240_lev7_c20   | bb240_lev7_c30   | 95%     | 3        | 3
#   bb240_lev7_c20   | t0.8_lev7_c20    | 72%     | 18       | 20
#   bb240_lev7_c20   | bb240_lev10_c30  | 88%     | 8        | 9
#
# Clusters: {bb240_lev7_c20, c30, c50, c100} are one cluster (>90% overlap)
#           {t0.8_lev7_c20, c30, c50} are a second cluster
#
# Recommendation: only one config from each cluster should advance

# CLI:
# python tools/config_correlation.py --sweep results/v4/concentration_push.json --top 10
```

---

## 7. `tools/portfolio_optimizer.py` — Optimal Strategy Weights

**Problem:** Portfolio weights in multi_v4_paper.json are set manually (all 1.0). No optimization of allocations based on correlation and return profiles.

**Build:**
```python
# Takes N strategy IDs, runs each individually to get equity curves,
# then optimizes portfolio weights.

# Methods:
# 1. Equal weight (baseline)
# 2. Inverse-volatility (risk parity)
# 3. Max Sharpe (mean-variance)
# 4. Min drawdown (minimize worst-case DD)
# 5. Max Calmar (custom: maximize return / max DD)

# Input: list of strategy IDs
# Output:
#   Method        | s62   | s65   | s72   | s98   | Port Calmar | Port DD
#   Equal         | 0.25  | 0.25  | 0.25  | 0.25  | 1.2         | -35%
#   Risk parity   | 0.10  | 0.15  | 0.15  | 0.60  | 2.8         | -22%
#   Max Sharpe    | 0.00  | 0.05  | 0.05  | 0.90  | 4.1         | -24%
#   Min DD        | 0.20  | 0.20  | 0.20  | 0.40  | 1.8         | -18%
#
# Also: correlation matrix, marginal Sharpe contribution of each strategy

# CLI:
# python tools/portfolio_optimizer.py --strategies s62,s65,s72,s98 --months 12
```

---

## 8. `tools/decay_monitor.py` — Live Strategy Decay Detection

**Problem:** Strategies are validated once and paper traded indefinitely. No automated alert when a strategy's edge is fading.

**Build:**
```python
# Reads paper trading state files and computes rolling metrics.

# Input: state directory (e.g., state/v4_paper_s98/)
# Output (per strategy):
#   Window   | Return  | Calmar | PF   | Trades | Status
#   30d      | +12%    | 3.2    | 2.1  | 11     | OK
#   60d      | +18%    | 2.8    | 1.9  | 24     | OK
#   90d      | +15%    | 1.5    | 1.4  | 35     | WATCH (Calmar declining)
#
# Alert thresholds:
#   WATCH:  rolling 30d Calmar < 1.0 OR rolling PF < 1.2
#   WARN:   rolling 60d Calmar < 0.5 OR rolling PF < 1.0
#   KILL:   rolling 90d Calmar < 0 OR rolling PF < 0.8
#
# Comparison to backtest:
#   Metric   | Backtest | Live   | Ratio  | Status
#   Calmar   | 4.75     | 2.8    | 59%    | OK (>50%)
#   PF       | 2.11     | 1.9    | 90%    | OK
#   WinRate  | 57.4%    | 52%    | 91%    | OK
#   AvgHold  | 74h      | 68h    | 92%    | OK

# CLI:
# python tools/decay_monitor.py --config configs/multi_v4_paper.json
# python tools/decay_monitor.py --state-dir state/v4_paper_s98/
```

---

## 9. `tools/timeframe_sweep.py` — Multi-Timeframe Signal Testing

**Problem:** All signals use 1h candles. Some patterns might be stronger on 4h or daily timeframes. No way to test without rewriting the strategy.

**Build:**
```python
# Derives higher-timeframe candles from 1h data and runs the same signal logic.

# Supported timeframes: 1h (base), 2h, 4h, 8h, 1D
# For each timeframe:
#   1. Resample 1h OHLCV to target timeframe
#   2. Recompute all indicators (EMA, MACD, BB, ADX, etc.)
#   3. Run strategy with resampled indicators
#   4. Report metrics

# Output:
#   Timeframe | Annual | Calmar | DD    | PF   | Trades | AvgHold
#   1h        | +113%  | 4.75   | -24%  | 2.11 | 129    | 74h
#   2h        | +95%   | 3.80   | -25%  | 1.95 | 98     | 96h
#   4h        | +78%   | 4.10   | -19%  | 2.30 | 62     | 144h
#   8h        | +45%   | 2.50   | -18%  | 1.80 | 38     | 240h
#   1D        | +22%   | 1.20   | -18%  | 1.50 | 15     | 480h

# CLI:
# python tools/timeframe_sweep.py --strategy s98 --timeframes 1h,2h,4h,8h,1D
```

---

## 10. `tools/signal_lab_v2.py` — Integrated Signal Workbench

**Problem:** signal_research.py does foundational analysis but doesn't connect to the sweep pipeline. After research, you manually create sweep configs. Need a tool that goes from signal idea to ranked configs in one step.

**Build:**
```python
# End-to-end signal workbench:
# 1. Define signal (code snippet or template name)
# 2. Auto-compute IC, hit rate, optimal hold, cost breakeven
# 3. Auto-generate filter combinations (calls auto_filter_discovery logic)
# 4. Run top 20 filter combos through robust_sweep (walk-forward)
# 5. Run fee_impact on top 5 survivors
# 6. Run regime_decompose on top 3
# 7. Output: final ranked configs with IS, OOS, regime, and fee analysis

# CLI:
# python tools/signal_lab_v2.py --signal macd_zero_cross --budget 100
#   (budget = max number of backtests to run)

# Output: results/v4/signal_lab_{signal_name}.json
# Contains: ranked configs with full IS/OOS/regime/fee breakdown
# Estimated runtime: ~10 minutes for 100 backtests at 6s each
```

---

## Priority Order

| Priority | Tool | Reason |
|----------|------|--------|
| 1 | sweep_framework.py | Eliminates boilerplate for all future tools |
| 2 | auto_filter_discovery.py | Would have saved 4 phases of manual iteration |
| 3 | robust_sweep.py | Overfitting protection is non-negotiable |
| 4 | fee_impact.py | Small, self-contained, immediate value |
| 5 | regime_decompose.py | Small, answers critical "where does alpha come from" |
| 6 | decay_monitor.py | Needed once paper trading starts |
| 7 | config_correlation.py | Prevents false confidence from correlated configs |
| 8 | portfolio_optimizer.py | Needed when we have 3+ profitable strategies |
| 9 | timeframe_sweep.py | Requires engine changes for multi-TF indicators |
| 10 | signal_lab_v2.py | Integrates tools 1-5, build last |
