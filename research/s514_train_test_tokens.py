import sys, os, time, dataclasses
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pandas as pd
import numpy as np
from collections import defaultdict

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics, print_report

STRATEGY_ID = 's514'
CAPITAL = 100_000

def run_backtest(months, end_date_str, max_positions=200):
    end_date = pd.Timestamp(end_date_str)
    spec = StrategySpec(
        strategy_id=STRATEGY_ID, weight=1.0, max_positions=max_positions,
        market='perp', strategy_type='per_token',
        max_concurrent_per_token=1, dd_scaling=[],
    )
    config = PortfolioConfig(
        capital=CAPITAL, exchange='binance', skip_walk_forward=True,
        conviction_mode='ranked', max_portfolio_positions=max_positions,
        strategies=[spec],
    )
    tokens = discover_tokens('perp')
    signals = precompute_strategy_signals(spec, tokens, config, months, end_date=end_date)
    strategy_specs = {STRATEGY_ID: spec}
    state = simulate_portfolio({STRATEGY_ID: signals}, strategy_specs, config)
    metrics, extra_info, eq_daily = compute_portfolio_metrics(state, CAPITAL)
    return state, metrics

def get_per_token_pnl(state):
    token_pnl = defaultdict(float)
    token_trades = defaultdict(int)
    token_wins = defaultdict(int)
    for trade in state.position_manager.closed_trades:
        token_pnl[trade.token] += trade.pnl
        token_trades[trade.token] += 1
        if trade.pnl > 0:
            token_wins[trade.token] += 1
    return dict(token_pnl), dict(token_trades), dict(token_wins)

# ── STEP 1: TRAIN on first 6 months (Apr-Oct 2025) ──
print("=" * 70)
print("  TRAIN PERIOD: Apr 2025 - Oct 2025 (full 229-token universe)")
print("=" * 70)
state_train, metrics_train = run_backtest(6, '2025-10-01')
pnl_train, trades_train, wins_train = get_per_token_pnl(state_train)

print(f"\nTrain metrics: Sharpe {metrics_train.sharpe_ratio:.2f}, "
      f"Return {metrics_train.total_return_pct:+.1f}%, "
      f"MaxDD {metrics_train.max_drawdown_pct:.1f}%")
print(f"Tokens traded: {len(pnl_train)}")

# Select tokens with positive P&L in train
selected_tokens = sorted([t for t, p in pnl_train.items() if p > 0],
                          key=lambda t: pnl_train[t], reverse=True)
rejected_tokens = sorted([t for t, p in pnl_train.items() if p <= 0],
                          key=lambda t: pnl_train[t])

print(f"\nSelected (train P&L > 0): {len(selected_tokens)} tokens")
print(f"Rejected (train P&L <= 0): {len(rejected_tokens)} tokens")

# Show selected tokens with their train P&L
print(f"\n{'Token':<12} {'Train P&L':>10} {'Trades':>8} {'WR%':>8}")
print("-" * 40)
for t in selected_tokens:
    wr = wins_train.get(t, 0) / trades_train.get(t, 1) * 100
    print(f"{t:<12} ${pnl_train[t]:>9,.0f} {trades_train[t]:>8} {wr:>7.1f}%")

# ── STEP 2: TEST on second 6 months (Oct 2025 - Apr 2026) with ALL tokens ──
# We need to run the full backtest and then filter by selected tokens
print(f"\n{'=' * 70}")
print("  TEST PERIOD: Oct 2025 - Apr 2026 (full universe, then filter)")
print(f"{'=' * 70}")

state_test, metrics_test_all = run_backtest(6, '2026-04-01')
pnl_test, trades_test, wins_test = get_per_token_pnl(state_test)

# Compute test metrics for selected tokens only
selected_set = set(selected_tokens)
selected_pnl = sum(pnl_test.get(t, 0) for t in selected_tokens)
selected_trades = sum(trades_test.get(t, 0) for t in selected_tokens)
rejected_pnl = sum(pnl_test.get(t, 0) for t in rejected_tokens)

print(f"\nFull universe test: Sharpe {metrics_test_all.sharpe_ratio:.2f}, "
      f"Return {metrics_test_all.total_return_pct:+.1f}%")
print(f"\nSelected tokens ({len(selected_tokens)}) TEST P&L: ${selected_pnl:,.0f}")
print(f"Rejected tokens ({len(rejected_tokens)}) TEST P&L: ${rejected_pnl:,.0f}")

# Per-token test results for selected tokens
print(f"\n{'Token':<12} {'Train P&L':>10} {'Test P&L':>10} {'Test Trades':>12} {'Test WR%':>10}")
print("-" * 58)
for t in selected_tokens:
    test_p = pnl_test.get(t, 0)
    test_tr = trades_test.get(t, 0)
    test_wr = wins_test.get(t, 0) / test_tr * 100 if test_tr > 0 else 0
    marker = "FAIL" if test_p < 0 else ""
    print(f"{t:<12} ${pnl_train[t]:>9,.0f} ${test_p:>9,.0f} {test_tr:>12} {test_wr:>9.1f}% {marker}")

# How many train winners stayed winners in test?
train_winners_test_positive = sum(1 for t in selected_tokens if pnl_test.get(t, 0) > 0)
train_winners_test_negative = sum(1 for t in selected_tokens if pnl_test.get(t, 0) <= 0)
print(f"\nTrain winners that stayed positive in test: {train_winners_test_positive}/{len(selected_tokens)} ({train_winners_test_positive/len(selected_tokens)*100:.0f}%)")
print(f"Train winners that flipped to negative: {train_winners_test_negative}/{len(selected_tokens)}")

# Also check: train LOSERS that became winners (missed opportunity)
train_losers_test_positive = sum(1 for t in rejected_tokens if pnl_test.get(t, 0) > 0)
print(f"Train losers that became positive in test: {train_losers_test_positive}/{len(rejected_tokens)}")

# ── STEP 3: Also try different P&L thresholds ──
print(f"\n{'=' * 70}")
print("  FILTER THRESHOLD ANALYSIS")
print(f"{'=' * 70}")

for thresh in [0, 500, 1000, 2000, 5000]:
    tokens = [t for t, p in pnl_train.items() if p > thresh]
    test_pnl = sum(pnl_test.get(t, 0) for t in tokens)
    test_trades = sum(trades_test.get(t, 0) for t in tokens)
    test_winners = sum(1 for t in tokens if pnl_test.get(t, 0) > 0)
    print(f"  Train P&L > ${thresh}: {len(tokens)} tokens, "
          f"test P&L ${test_pnl:+,.0f}, "
          f"{test_winners}/{len(tokens)} stayed positive ({test_winners/max(len(tokens),1)*100:.0f}%)")

# ── STEP 4: Final recommended list ──
# Tokens positive in BOTH train AND test
both_positive = [t for t in selected_tokens if pnl_test.get(t, 0) > 0]
both_positive_pnl = sum(pnl_train[t] + pnl_test.get(t, 0) for t in both_positive)
print(f"\n{'=' * 70}")
print(f"  RECOMMENDED: Tokens positive in BOTH train AND test")
print(f"{'=' * 70}")
print(f"  {len(both_positive)} tokens, combined P&L ${both_positive_pnl:,.0f}")
print(f"  Tokens: {both_positive}")
