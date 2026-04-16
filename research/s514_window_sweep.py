"""
s514 Z-Score Window & Threshold Sweep
======================================

Tests different ZSCORE_WINDOW sizes and threshold values for s514_ls_div_leveraged.
WARMUP is set to 1.2x the ZSCORE_WINDOW for each test.

Usage:
    /workspace/venv/bin/python research/s514_window_sweep.py
"""

import sys
import os

sys.path.insert(0, '/workspace/crypto_backtest')
sys.path.insert(0, '/workspace/crypto_backtest/v4')
os.chdir('/workspace/crypto_backtest')

import pandas as pd
from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics
from v4.engine import _STRATEGY_MODULE_CACHE

CAPITAL = 100_000
end_date = pd.Timestamp('2026-04-01')
DEFAULTS = {
    'THRESHOLD': 2.5, 'EDGE': 0.35, 'TRAIL_MULT': 2.0, 'STOP_MULT': 2.5,
    'TARGET_MULT': 999.0, 'MAX_HOLD': 336, 'NO_STOP_BARS': 24, 'MIN_HOLD': 24,
    'LEVERAGE': 3.0, 'BREAKEVEN_ATR': 0.5, 'DIRECTION': 'both',
    'ZSCORE_WINDOW': 720, 'WARMUP': 864,
}

# Force initial load
spec0 = StrategySpec(
    strategy_id='s514', weight=1.0, max_positions=28, market='perp',
    strategy_type='per_token', max_concurrent_per_token=1, dd_scaling=[],
)
config0 = PortfolioConfig(
    capital=CAPITAL, exchange='binance', skip_walk_forward=True,
    conviction_mode='ranked', max_portfolio_positions=28,
    strategies=[spec0], concentration_limit=0.15,
)
precompute_strategy_signals(spec0, discover_tokens('perp'), config0, 12, end_date=end_date)


def run(label, overrides):
    mod = _STRATEGY_MODULE_CACHE.get('s514')
    for k, v in DEFAULTS.items():
        setattr(mod, k, v)
    for k, v in overrides.items():
        setattr(mod, k, v)
    mod._aligned_cache.clear()
    mod._ls_loaded = False
    mod._ls_cache.clear()

    spec = StrategySpec(
        strategy_id='s514', weight=1.0, max_positions=28, market='perp',
        strategy_type='per_token', max_concurrent_per_token=1, dd_scaling=[],
    )
    config = PortfolioConfig(
        capital=CAPITAL, exchange='binance', skip_walk_forward=True,
        conviction_mode='ranked', max_portfolio_positions=28,
        strategies=[spec], concentration_limit=0.15,
    )
    tokens = discover_tokens('perp')
    signals = precompute_strategy_signals(spec, tokens, config, 12, end_date=end_date)
    state = simulate_portfolio({'s514': signals}, {'s514': spec}, config)
    metrics, _, _ = compute_portfolio_metrics(state, CAPITAL)
    print(f"{label:<50} Ret={metrics.total_return_pct:>+7.1f}%  Sharpe={metrics.sharpe_ratio:.2f}  "
          f"Calmar={metrics.calmar_ratio:.2f}  MaxDD={metrics.max_drawdown_pct:.1f}%  Trades={metrics.total_trades}")
    return metrics


# ======================================================================
#  PART 1: Z-Score Window Sweep (threshold fixed at 2.5)
# ======================================================================
print("=" * 100)
print("PART 1: Z-Score Window Sweep (THRESHOLD=2.5)")
print("=" * 100)

window_configs = [
    ("10d (240h)",  240),
    ("15d (360h)",  360),
    ("20d (480h)",  480),
    ("30d (720h) [baseline]", 720),
    ("45d (1080h)", 1080),
    ("60d (1440h)", 1440),
    ("90d (2160h)", 2160),
]

best_calmar = -999
best_window = 720
window_results = {}

for label_suffix, window in window_configs:
    warmup = int(window * 1.2)
    label = f"Window={label_suffix}, WARMUP={warmup}"
    m = run(label, {'ZSCORE_WINDOW': window, 'WARMUP': warmup})
    window_results[window] = m
    if m.calmar_ratio > best_calmar:
        best_calmar = m.calmar_ratio
        best_window = window

print(f"\n>>> Best window by Calmar: {best_window}h ({best_window // 24}d) with Calmar={best_calmar:.2f}")

# ======================================================================
#  PART 2: Threshold sweep at best window
# ======================================================================
print("\n" + "=" * 100)
print(f"PART 2: Threshold Sweep at best window={best_window}h ({best_window // 24}d)")
print("=" * 100)

best_warmup = int(best_window * 1.2)
thresholds = [2.0, 2.2, 2.5, 2.8]

best_thresh_calmar = -999
best_thresh = 2.5

for thresh in thresholds:
    label = f"Window={best_window}h, Threshold={thresh}"
    m = run(label, {'ZSCORE_WINDOW': best_window, 'WARMUP': best_warmup, 'THRESHOLD': thresh})
    if m.calmar_ratio > best_thresh_calmar:
        best_thresh_calmar = m.calmar_ratio
        best_thresh = thresh

print(f"\n>>> Best threshold at window={best_window}h: THRESHOLD={best_thresh} with Calmar={best_thresh_calmar:.2f}")

# ======================================================================
#  SUMMARY
# ======================================================================
print("\n" + "=" * 100)
print("SUMMARY")
print("=" * 100)
print(f"Best ZSCORE_WINDOW: {best_window}h ({best_window // 24}d)")
print(f"Best THRESHOLD:     {best_thresh}")
print(f"Best Calmar:        {best_thresh_calmar:.2f}")
print(f"WARMUP should be:   {int(best_window * 1.2)}")
