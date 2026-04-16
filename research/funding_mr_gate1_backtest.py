#!/usr/bin/env python3
"""
Gate 1 Raw Backtest: Funding Rate Mean-Reversion on BTC (using Backtest harness)
================================================================================

Hypothesis (IC-validated):
  BTC IC=-0.04 at 48h horizon, cross-sectional IC=-0.042 (t=-30).

  When funding rate z-score is high positive -> SHORT (expect price to drop +
  receive funding payments from longs).
  When funding rate z-score is high negative -> LONG (expect price to rise +
  earn alpha despite paying funding).

Signal:
  - Compute rolling z-score of funding_rate over 168-bar (7-day) window
  - SHORT when zscore > threshold
  - LONG when zscore < -threshold
  - Hold up to max_hold hours, then exit

Sweep:
  - Thresholds: [1.0, 1.5, 2.0]
  - Max hold: [12, 24, 48] hours

Pass criteria:
  Sharpe > 2.0, Calmar > 3.0, MaxDD > -25%, trades > 30 (L12M), positive L12M return.

Author: Quant Research Agent
Date: 2026-04-03
"""

import sys
sys.path.insert(0, '/workspace/crypto_backtest')

import warnings
import numpy as np
import pandas as pd
from itertools import product
from tools.raw_backtest import Backtest

warnings.filterwarnings('ignore')


def log(msg):
    print(msg)
    sys.stdout.flush()


# ── Parameters to sweep ──────────────────────────────────────────────────────
THRESHOLDS = [1.0, 1.5, 2.0]
MAX_HOLDS = [12, 24, 48]
ZSCORE_WINDOW = 168  # 7 days of hourly bars

START = '2024-01-01'
END = '2026-03-17'
CAPITAL = 100_000
SIZE_USD = 50_000
FEE_BPS = 7


def build_signals(df, threshold, max_hold):
    """
    Build a signal series with max_hold logic baked in.

    Signal values: 1 = LONG, -1 = SHORT, 0 = flat.
    Uses the funding_rate z-score with point-in-time shift (signal at T-1, act at T).
    Includes max_hold exit: position exits after max_hold bars regardless.
    """
    # Forward-fill funding_rate NaNs (reported every 8h, spread to hourly)
    funding = df['funding_rate'].ffill()

    # Rolling z-score over 168-bar (7-day) window
    fr_mean = funding.rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW).mean()
    fr_std = funding.rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW).std()
    zscore = (funding - fr_mean) / fr_std

    # Point-in-time: signal from bar T-1 applied at bar T
    zscore_prev = zscore.shift(1)

    # Build signal with max_hold logic
    n = len(df)
    signal = pd.Series(0, index=df.index, dtype=int)

    in_position = 0  # 0 = flat, 1 = long, -1 = short
    bars_held = 0

    for i in range(1, n):
        z = zscore_prev.iloc[i]

        if in_position != 0:
            bars_held += 1
            # Exit on max_hold
            if bars_held >= max_hold:
                signal.iloc[i] = 0
                in_position = 0
                bars_held = 0
                continue
            # Exit on signal reversal (zscore crosses zero region)
            # Stay in position unless max_hold hit
            signal.iloc[i] = in_position
            continue

        # Not in position — check for entry
        if np.isnan(z):
            signal.iloc[i] = 0
            continue

        if z > threshold:
            # High positive funding -> SHORT (expect price drop + receive funding)
            signal.iloc[i] = -1
            in_position = -1
            bars_held = 0
        elif z < -threshold:
            # High negative funding -> LONG (expect price rise)
            signal.iloc[i] = 1
            in_position = 1
            bars_held = 0
        else:
            signal.iloc[i] = 0

    return signal, zscore


def run_single_backtest(threshold, max_hold):
    """Run a single backtest for one threshold/max_hold combo."""
    bt = Backtest(
        capital=CAPITAL,
        fee_bps=FEE_BPS,
        market='perp',
        leverage_max=1.0,
        start=START,
        end=END,
    )

    # Load data
    df = bt.load('BTC')

    # Build signals
    signals, zscore = build_signals(df, threshold, max_hold)

    n_entries = ((signals != 0) & (signals.shift(1).fillna(0) != signals)).sum()
    n_bars_active = (signals != 0).sum()
    pct_active = n_bars_active / len(df) * 100

    log(f"\n{'='*70}")
    log(f"Threshold={threshold}, Max Hold={max_hold}h")
    log(f"Signal: {n_entries} entries, {n_bars_active} bars active ({pct_active:.1f}%)")

    if n_entries < 5:
        log("SKIP: Too few entries")
        return None

    # Run backtest
    bt.from_signals(token='BTC', signals=signals, size_usd=SIZE_USD, leverage=1.0)

    # Get report
    result = bt.report(f'Funding MR (thr={threshold}, hold={max_hold}h)')

    # Extract key metrics for summary
    summary = {
        'threshold': threshold,
        'max_hold': max_hold,
        'verdict': result['verdict'],
        'n_entries': n_entries,
        'n_trades': result['trades'],
        'pct_active': pct_active,
    }

    # Extract window metrics
    for wname in ['L12M', 'L6M', 'L3M']:
        w = result['windows'].get(wname, {})
        summary[f'{wname}_sharpe'] = w.get('sharpe', 0)
        summary[f'{wname}_calmar'] = w.get('calmar', 0)
        summary[f'{wname}_maxdd'] = w.get('maxdd', 0)
        summary[f'{wname}_ret'] = w.get('ann_ret', 0)
        summary[f'{wname}_trades'] = w.get('trades', 0)
        summary[f'{wname}_pf'] = w.get('profit_factor', 0)
        summary[f'{wname}_winrate'] = w.get('win_rate', 0)

    # Full period
    summary['full_sharpe'] = result['full'].get('sharpe', 0)
    summary['full_calmar'] = result['full'].get('calmar', 0)
    summary['full_maxdd'] = result['full'].get('maxdd', 0)
    summary['full_ret'] = result['full'].get('total_ret', 0)

    # Cost breakdown from trades
    if bt.trades:
        summary['total_fees'] = sum(t.fees for t in bt.trades)
        summary['total_funding'] = sum(t.funding for t in bt.trades)
        summary['total_slippage'] = sum(t.slippage for t in bt.trades)
        summary['gross_pnl'] = sum(t.gross_pnl for t in bt.trades)
        summary['net_pnl'] = sum(t.net_pnl for t in bt.trades)
    else:
        summary['total_fees'] = 0
        summary['total_funding'] = 0
        summary['total_slippage'] = 0
        summary['gross_pnl'] = 0
        summary['net_pnl'] = 0

    summary['kill_reasons'] = result.get('kill_reasons', [])

    return summary


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    log("=" * 70)
    log("FUNDING RATE MEAN-REVERSION — GATE 1 BACKTEST")
    log(f"Period: {START} to {END}")
    log(f"Capital: ${CAPITAL:,} | Size: ${SIZE_USD:,} | Fee: {FEE_BPS}bps")
    log(f"Z-score window: {ZSCORE_WINDOW}h (7 days)")
    log(f"Thresholds: {THRESHOLDS}")
    log(f"Max holds: {MAX_HOLDS}")
    log("=" * 70)

    # ── Data exploration ──
    bt_explore = Backtest(capital=CAPITAL, fee_bps=FEE_BPS, market='perp',
                          start=START, end=END)
    df_explore = bt_explore.load('BTC')
    funding = df_explore['funding_rate'].ffill()
    fr_mean = funding.rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW).mean()
    fr_std = funding.rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW).std()
    zscore = (funding - fr_mean) / fr_std
    log(f"\nFunding rate stats:")
    log(f"  Mean: {funding.mean():.8f}")
    log(f"  Std:  {funding.std():.8f}")
    log(f"  Z-score range: [{zscore.min():.2f}, {zscore.max():.2f}]")
    for thr in THRESHOLDS:
        n_above = (zscore > thr).sum()
        n_below = (zscore < -thr).sum()
        log(f"  |z| > {thr}: {n_above} bars above, {n_below} bars below "
            f"({(n_above + n_below)/len(zscore)*100:.1f}%)")

    # ── Run all combos ──
    results = []
    for threshold, max_hold in product(THRESHOLDS, MAX_HOLDS):
        summary = run_single_backtest(threshold, max_hold)
        if summary is not None:
            results.append(summary)

    # ══════════════════════════════════════════════════════════════════════════
    # SUMMARY TABLE
    # ══════════════════════════════════════════════════════════════════════════
    log("\n" + "=" * 120)
    log("SUMMARY TABLE — ALL COMBINATIONS")
    log("=" * 120)

    header = (f"{'Thr':>4s} {'Hold':>5s} │ {'Verdict':>7s} │ "
              f"{'Trades':>6s} │ {'L12M Sh':>8s} {'L12M Cal':>9s} {'L12M DD':>8s} "
              f"{'L12M Ret':>9s} {'L12M PF':>8s} │ "
              f"{'Full Sh':>8s} {'Full DD':>8s} {'Full Ret':>9s} │ "
              f"{'Fees':>8s} {'Funding':>9s} {'Slip':>8s} {'Net PnL':>10s}")
    log(header)
    log("-" * 120)

    for r in results:
        line = (
            f"{r['threshold']:>4.1f} {r['max_hold']:>5d} │ "
            f"{r['verdict']:>7s} │ "
            f"{r['L12M_trades']:>6d} │ "
            f"{r['L12M_sharpe']:>8.2f} {r['L12M_calmar']:>9.2f} "
            f"{r['L12M_maxdd']:>+7.1%} {r['L12M_ret']:>+8.1%} "
            f"{r['L12M_pf']:>8.2f} │ "
            f"{r['full_sharpe']:>8.2f} {r['full_maxdd']:>+7.1%} "
            f"{r['full_ret']:>+8.1%} │ "
            f"${r['total_fees']:>7,.0f} ${r['total_funding']:>+8,.0f} "
            f"${r['total_slippage']:>7,.0f} ${r['net_pnl']:>+9,.0f}"
        )
        log(line)

    # ── Best combo by L12M Sharpe ──
    log("\n" + "=" * 70)
    if results:
        best = max(results, key=lambda x: x['L12M_sharpe'])
        log(f"BEST BY L12M SHARPE: threshold={best['threshold']}, "
            f"max_hold={best['max_hold']}h")
        log(f"  L12M Sharpe:  {best['L12M_sharpe']:.2f}")
        log(f"  L12M Calmar:  {best['L12M_calmar']:.2f}")
        log(f"  L12M MaxDD:   {best['L12M_maxdd']:.1%}")
        log(f"  L12M Return:  {best['L12M_ret']:.1%}")
        log(f"  L12M Trades:  {best['L12M_trades']}")
        log(f"  L12M PF:      {best['L12M_pf']:.2f}")
        log(f"  Full Sharpe:  {best['full_sharpe']:.2f}")
        log(f"  Full Return:  {best['full_ret']:.1%}")
        log(f"  Verdict:      {best['verdict']}")
        if best['kill_reasons']:
            log(f"  Kill reasons:")
            for kr in best['kill_reasons']:
                log(f"    - {kr}")

        # ── Gate 1 pass/fail check ──
        log(f"\n{'='*70}")
        log("GATE 1 PASS/FAIL CHECK")
        log(f"{'='*70}")

        any_pass = False
        for r in results:
            passes_sharpe = r['L12M_sharpe'] > 2.0
            passes_calmar = r['L12M_calmar'] > 3.0
            passes_maxdd = r['L12M_maxdd'] > -0.25
            passes_trades = r['L12M_trades'] > 30
            passes_ret = r['L12M_ret'] > 0

            all_pass = all([passes_sharpe, passes_calmar, passes_maxdd,
                           passes_trades, passes_ret])
            if all_pass:
                any_pass = True
                log(f"  PASS: thr={r['threshold']}, hold={r['max_hold']}h "
                    f"(Sharpe={r['L12M_sharpe']:.2f}, Calmar={r['L12M_calmar']:.2f}, "
                    f"DD={r['L12M_maxdd']:.1%}, trades={r['L12M_trades']}, "
                    f"ret={r['L12M_ret']:.1%})")

        if not any_pass:
            log("  NO COMBINATION PASSES ALL GATE 1 CRITERIA")
            log("  Criteria: Sharpe>2.0, Calmar>3.0, MaxDD>-25%, trades>30, L12M ret>0")

            # Show which criteria each combo fails
            log("\n  Detailed failure analysis:")
            for r in results:
                fails = []
                if r['L12M_sharpe'] <= 2.0:
                    fails.append(f"Sharpe={r['L12M_sharpe']:.2f}<=2.0")
                if r['L12M_calmar'] <= 3.0:
                    fails.append(f"Calmar={r['L12M_calmar']:.2f}<=3.0")
                if r['L12M_maxdd'] <= -0.25:
                    fails.append(f"DD={r['L12M_maxdd']:.1%}<=-25%")
                if r['L12M_trades'] <= 30:
                    fails.append(f"trades={r['L12M_trades']}<=30")
                if r['L12M_ret'] <= 0:
                    fails.append(f"ret={r['L12M_ret']:.1%}<=0")
                log(f"    thr={r['threshold']}, hold={r['max_hold']}h: "
                    f"FAILS [{', '.join(fails)}]")
    else:
        log("NO RESULTS — all combinations had too few entries")

    log("\n" + "=" * 70)
    log("GATE 1 BACKTEST COMPLETE")
    log("=" * 70)
