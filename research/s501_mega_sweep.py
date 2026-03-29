"""Mega optimization sweep for s501 — all dimensions in one script.

Precomputes signals ONCE, then sweeps: hold period, leverage, direction,
conviction, edge, cap_mult, and combined configs.
"""
import sys, os, copy, gc
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.chdir(os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
from v4.config import PortfolioConfig, StrategySpec
from v4.portfolio_signals import precompute_portfolio_signals
from v4.simulator import simulate_portfolio
from v4.universe import get_all_tradeable


def clone_signals(signals, **overrides):
    """Shallow-copy signals dict, applying per-token overrides."""
    out = {}
    for token, sig in signals.items():
        # Shallow copy the signal object
        s = copy.copy(sig)
        for k, v in overrides.items():
            if callable(v):
                setattr(s, k, v(s))
            else:
                setattr(s, k, v)
        out[token] = s
    return out


def filter_signals(signals, direction_filter=None, min_conviction=0.0):
    """Clone signals with direction and conviction filters applied."""
    out = {}
    for token, sig in signals.items():
        s = copy.copy(sig)
        mask = s.entry_mask.copy()
        for i in range(s.n_bars):
            if not mask[i]:
                continue
            if direction_filter is not None and s.direction[i] != direction_filter:
                mask[i] = False
            if min_conviction > 0 and s.conviction_score is not None and s.conviction_score[i] < min_conviction:
                mask[i] = False
        s.entry_mask = mask
        out[token] = s
    return out


def run_sim(signals, max_pos=15, conviction_mode='ranked', capital=100_000):
    """Run simulation on pre-built signals."""
    spec = StrategySpec(strategy_id='s501', market='perp', strategy_type='portfolio',
                        max_positions=max_pos, entry_resolution=1)
    config = PortfolioConfig(capital=capital, exchange='binance', skip_walk_forward=True,
                             conviction_mode=conviction_mode)
    state = simulate_portfolio({'s501': signals}, {'s501': spec}, config)
    return state


def report(label, state, capital=100_000):
    trades = state.position_manager.closed_trades
    if not trades:
        print(f"  {label:>45s}  -- no trades --")
        return
    pnls = np.array([t.pnl for t in trades])
    margins = np.array([t.margin_usd for t in trades])

    raw_rets = []
    for t in trades:
        if t.direction == 1:
            r = (t.exit_price - t.entry_price) / t.entry_price
        else:
            r = (t.entry_price - t.exit_price) / t.entry_price
        raw_rets.append(r)
    raw_rets = np.array(raw_rets)

    eq = state.portfolio_equity
    ret = (eq / capital - 1) * 100

    eq_curve = np.array([e[1] for e in state.equity_snapshots])
    peak = np.maximum.accumulate(eq_curve)
    dd = (eq_curve - peak) / peak
    max_dd = dd.min() * 100

    wr = (pnls > 0).mean() * 100
    hourly_rets = np.diff(eq_curve) / eq_curve[:-1]
    sharpe = hourly_rets.mean() / max(hourly_rets.std(), 1e-10) * np.sqrt(8760)
    calmar = (ret / 100) / max(abs(max_dd / 100), 0.001)

    wins = pnls[pnls > 0].sum()
    losses = abs(pnls[pnls < 0].sum())
    pf = wins / max(losses, 1)

    print(f"  {label:>45s}  {ret:>+8.1f}%  {max_dd:>6.1f}%  {sharpe:>7.2f}  "
          f"{calmar:>7.2f}  {wr:>4.1f}%  {pf:>4.2f}  {len(trades):>5d}  "
          f"{raw_rets.mean()*100:>+7.3f}%  ${margins.mean():>6.0f}")


def header():
    h = (f"{'Config':>45s}  {'Return':>8s}  {'MaxDD':>7s}  {'Sharpe':>7s}  "
         f"{'Calmar':>7s}  {'WR':>5s}  {'PF':>5s}  {'N':>5s}  "
         f"{'RawRet':>8s}  {'AvgMgn':>7s}")
    print(h)
    print("-" * 140)


def main():
    tokens = get_all_tradeable()
    print(f"Universe: {len(tokens)} tokens")

    # Precompute signals ONCE
    spec = StrategySpec(strategy_id='s501', market='perp', strategy_type='portfolio',
                        max_positions=15, entry_resolution=1)
    config = PortfolioConfig(capital=100_000, exchange='binance', skip_walk_forward=True)
    base_signals = precompute_portfolio_signals(spec, tokens, config, months=12)
    print(f"Signals ready: {len(base_signals)} tokens\n")

    # ========================================
    # 1. HOLD PERIOD SWEEP
    # ========================================
    print("=" * 140)
    print("  SWEEP 1: HOLD PERIOD (max_pos=15, ranked, lev=1.0)")
    print("=" * 140)
    header()
    for hold in [2, 3, 4, 5, 6, 8, 10, 12, 16, 24]:
        sigs = clone_signals(base_signals, max_hold=hold)
        state = run_sim(sigs, max_pos=15)
        report(f"hold={hold}", state)

    # ========================================
    # 2. LEVERAGE SWEEP (at best hold)
    # ========================================
    print(f"\n{'=' * 140}")
    print("  SWEEP 2: LEVERAGE (hold=4, max_pos=15, ranked)")
    print("=" * 140)
    header()
    for lev in [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]:
        sigs = clone_signals(base_signals, max_hold=4,
                             leverage=lambda s, l=lev: np.full(s.n_bars, l, dtype=np.float32))
        state = run_sim(sigs, max_pos=15)
        report(f"lev={lev:.2f}", state)

    # ========================================
    # 3. DIRECTION FILTER
    # ========================================
    print(f"\n{'=' * 140}")
    print("  SWEEP 3: DIRECTION FILTER (hold=4, max_pos=15, ranked)")
    print("=" * 140)
    header()
    for label, d_filter in [("Both L+S", None), ("Long only", 1), ("Short only", -1)]:
        sigs = filter_signals(base_signals, direction_filter=d_filter)
        sigs = clone_signals(sigs, max_hold=4)
        state = run_sim(sigs, max_pos=15)
        report(label, state)

    # ========================================
    # 4. CONVICTION THRESHOLD
    # ========================================
    print(f"\n{'=' * 140}")
    print("  SWEEP 4: CONVICTION THRESHOLD (hold=4, max_pos=15, ranked)")
    print("=" * 140)
    header()
    for mc in [0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]:
        sigs = filter_signals(base_signals, min_conviction=mc)
        sigs = clone_signals(sigs, max_hold=4)
        state = run_sim(sigs, max_pos=15)
        report(f"min_conviction={mc:.1f}", state)

    # ========================================
    # 5. EDGE (SIZING) SWEEP
    # ========================================
    print(f"\n{'=' * 140}")
    print("  SWEEP 5: EDGE / SIZING (hold=4, max_pos=15, ranked)")
    print("=" * 140)
    header()
    for edge in [0.20, 0.30, 0.35, 0.50, 0.70, 1.00, 1.50]:
        sigs = clone_signals(base_signals, max_hold=4, edge=edge)
        state = run_sim(sigs, max_pos=15)
        report(f"edge={edge:.2f}", state)

    # ========================================
    # 6. CAP MULTIPLIER SWEEP
    # ========================================
    print(f"\n{'=' * 140}")
    print("  SWEEP 6: CAP MULTIPLIER (hold=4, edge=0.50, max_pos=15, ranked)")
    print("=" * 140)
    header()
    for cm in [1.0, 2.0, 3.0, 4.0, 5.0, 8.0, 10.0]:
        sigs = clone_signals(base_signals, max_hold=4, edge=0.50,
                             cap_multiplier=lambda s, c=cm: np.full(s.n_bars, c, dtype=np.float32))
        state = run_sim(sigs, max_pos=15)
        report(f"cap_mult={cm:.1f}", state)

    # ========================================
    # 7. MAX POSITIONS (at best hold + sizing)
    # ========================================
    print(f"\n{'=' * 140}")
    print("  SWEEP 7: MAX POSITIONS (hold=4, edge=0.50, cap_mult=5.0, ranked)")
    print("=" * 140)
    header()
    for mp in [3, 5, 8, 10, 15, 20, 30, 50]:
        sigs = clone_signals(base_signals, max_hold=4, edge=0.50,
                             cap_multiplier=lambda s: np.full(s.n_bars, 5.0, dtype=np.float32))
        state = run_sim(sigs, max_pos=mp)
        report(f"max_pos={mp}", state)

    # ========================================
    # 8. COMBINED BEST CONFIGS
    # ========================================
    print(f"\n{'=' * 140}")
    print("  SWEEP 8: COMBINED BEST CONFIGS")
    print("=" * 140)
    header()

    configs = [
        ("baseline (hold=8,edge=0.35,pos=5)", dict(max_hold=8, edge=0.35), 5),
        ("hold=4 edge=0.5 pos=15", dict(max_hold=4, edge=0.50), 15),
        ("hold=4 edge=0.5 pos=20", dict(max_hold=4, edge=0.50), 20),
        ("hold=4 edge=0.5 pos=20 lev=1.5", dict(max_hold=4, edge=0.50,
            leverage=lambda s: np.full(s.n_bars, 1.5, dtype=np.float32)), 20),
        ("hold=4 edge=1.0 cap=5 pos=20", dict(max_hold=4, edge=1.0,
            cap_multiplier=lambda s: np.full(s.n_bars, 5.0, dtype=np.float32)), 20),
        ("hold=4 edge=1.0 cap=5 pos=30", dict(max_hold=4, edge=1.0,
            cap_multiplier=lambda s: np.full(s.n_bars, 5.0, dtype=np.float32)), 30),
        ("hold=4 edge=1.0 cap=8 pos=20 lev=1.5", dict(max_hold=4, edge=1.0,
            cap_multiplier=lambda s: np.full(s.n_bars, 8.0, dtype=np.float32),
            leverage=lambda s: np.full(s.n_bars, 1.5, dtype=np.float32)), 20),
        ("hold=3 edge=0.5 pos=20", dict(max_hold=3, edge=0.50), 20),
        ("hold=3 edge=1.0 cap=5 pos=30", dict(max_hold=3, edge=1.0,
            cap_multiplier=lambda s: np.full(s.n_bars, 5.0, dtype=np.float32)), 30),
        ("hold=6 edge=0.5 pos=20", dict(max_hold=6, edge=0.50), 20),
    ]

    for label, overrides, mp in configs:
        sigs = clone_signals(base_signals, **overrides)
        state = run_sim(sigs, max_pos=mp)
        report(label, state)

    print(f"\nDone.")


if __name__ == '__main__':
    main()
