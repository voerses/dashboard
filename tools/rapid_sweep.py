#!/usr/bin/env python3
"""
Rapid Parameter Sweep — Systematic strategy parameter optimization.

Tests many parameter combinations rapidly using the V4 portfolio backtester.
Produces a ranked table of results sorted by target metric (Calmar, Sharpe, etc).

Usage:
  python tools/rapid_sweep.py --signal <signal_type> --months 12

Signal types define the entry logic; sweep varies trade management params.
"""
import subprocess, json, re, os, sys, time, itertools
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STRATEGY_FILE = PROJECT_ROOT / "strategies" / "s98_sr_breakout_swing.py"

# ── Strategy template with all parameterizable fields ──────────────
TEMPLATE = '''"""s98 sweep: {label}"""
import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_max, rolling_min, rolling_mean)

WARMUP = 200

def strategy(ctx: StrategyContext) -> StrategyResult:
    close = ctx.ind_1h['close']
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']
    n = len(close)
    regime = ctx.regime_1h

{signal_code}

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long, 1, np.where(entry_short, -1, 0)).astype(np.int8)
    entry_mask[:WARMUP] = False
    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask, direction=direction,
        market_type=MarketType.PERP, leverage={leverage},
        stop_mult={stop}, trail_mult={trail}, target_mult={target},
        no_stop_bars={grace}, min_hold={min_hold}, max_hold={max_hold},
        edge=0.40, exit_regimes={{CRISIS}}, breakeven_atr={breakeven},
        exchange='binance', name='s98_sr_breakout_swing',
    )
'''

# ── Signal definitions ─────────────────────────────────────────────
SIGNALS = {
    "squeeze_bb": '''
    bb_upper = ctx.ind_1h['bb_upper']
    bb_lower = ctx.ind_1h['bb_lower']
    bb_width = ctx.ind_1h['bb_width']
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    bb_avg = rolling_mean(bb_width, 120)
    squeeze = bb_width < bb_avg
    close_prev = np.roll(close, 1); close_prev[0] = np.nan
    bb_up_prev = np.roll(bb_upper, 1); bb_up_prev[0] = np.nan
    bb_lo_prev = np.roll(bb_lower, 1); bb_lo_prev[0] = np.nan
    adx_prev = np.roll(adx, 1); adx_prev[0] = np.nan
    break_long = (close > bb_up_prev) & (close_prev <= bb_up_prev)
    break_short = (close < bb_lo_prev) & (close_prev >= bb_lo_prev)
    entry_long = break_long & squeeze & (adx > 20) & (adx > adx_prev) & (plus_di > minus_di) & (regime != CRISIS)
    entry_short = break_short & squeeze & (adx > 20) & (adx > adx_prev) & (minus_di > plus_di) & (regime != CRISIS)
''',
    "donchian_squeeze": '''
    dh = ctx.ind_1h['donch_high']
    dl = ctx.ind_1h['donch_low']
    bb_width = ctx.ind_1h['bb_width']
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    bb_avg = rolling_mean(bb_width, 120)
    squeeze = bb_width < bb_avg
    close_prev = np.roll(close, 1); close_prev[0] = np.nan
    dh_prev = np.roll(dh, 1); dh_prev[0] = np.nan
    dl_prev = np.roll(dl, 1); dl_prev[0] = np.nan
    entry_long = (close > dh_prev) & (close_prev <= dh_prev) & squeeze & (adx > 20) & (plus_di > minus_di) & (regime != CRISIS)
    entry_short = (close < dl_prev) & (close_prev >= dl_prev) & squeeze & (adx > 20) & (minus_di > plus_di) & (regime != CRISIS)
''',
    "ema_cross_vol": '''
    ema10 = ctx.ind_1h['ema_10']
    ema20 = ctx.ind_1h['ema_20']
    vol_ratio = ctx.ind_1h['vol_ratio']
    adx = ctx.ind_1h['adx']
    ema10_prev = np.roll(ema10, 1); ema10_prev[0] = np.nan
    ema20_prev = np.roll(ema20, 1); ema20_prev[0] = np.nan
    entry_long = (ema10 > ema20) & (ema10_prev <= ema20_prev) & (vol_ratio > 1.2) & (adx > 20) & (regime != CRISIS)
    entry_short = (ema10 < ema20) & (ema10_prev >= ema20_prev) & (vol_ratio > 1.2) & (adx > 20) & (regime != CRISIS)
''',
    "macd_zero_cross": '''
    macd = ctx.ind_1h['macd']
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    macd_prev = np.roll(macd, 1); macd_prev[0] = np.nan
    entry_long = (macd > 0) & (macd_prev <= 0) & (adx > 20) & (plus_di > minus_di) & (regime != CRISIS)
    entry_short = (macd < 0) & (macd_prev >= 0) & (adx > 20) & (minus_di > plus_di) & (regime != CRISIS)
''',
    "rsi_bounce": '''
    rsi = ctx.ind_1h['rsi']
    ema50 = ctx.ind_1h['ema_50']
    adx = ctx.ind_1h['adx']
    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = np.nan
    # RSI crosses above 30 (bounce from oversold in uptrend)
    entry_long = (rsi > 35) & (rsi_prev <= 35) & (close > ema50) & (adx > 15) & (regime != CRISIS)
    # RSI crosses below 70 (drop from overbought in downtrend)
    entry_short = (rsi < 65) & (rsi_prev >= 65) & (close < ema50) & (adx > 15) & (regime != CRISIS)
''',
    "weekly_momentum": '''
    # Use 168h return as signal (weekly momentum = only significant AC pattern)
    ret_168 = np.full(n, np.nan)
    ret_168[168:] = close[168:] / close[:n-168] - 1
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    # Enter when weekly return is positive AND DI confirms
    entry_long = (ret_168 > 0.03) & (adx > 20) & (plus_di > minus_di) & (regime != CRISIS)
    entry_short = (ret_168 < -0.03) & (adx > 20) & (minus_di > plus_di) & (regime != CRISIS)
''',
    "contrarian_daily": '''
    # 24-72h reversal pattern (significant negative autocorrelation)
    ret_48 = np.full(n, np.nan)
    ret_48[48:] = close[48:] / close[:n-48] - 1
    ema20 = ctx.ind_1h['ema_20']
    adx = ctx.ind_1h['adx']
    # Contrarian: buy after 48h drop (>3%), sell after 48h rally (>3%)
    entry_long = (ret_48 < -0.03) & (close > ema20 * 0.97) & (adx < 30) & (regime != CRISIS)
    entry_short = (ret_48 > 0.03) & (close < ema20 * 1.03) & (adx < 30) & (regime != CRISIS)
''',
}

# ── Parameter grid ─────────────────────────────────────────────────
PARAM_GRID = {
    'leverage': [2.0, 3.0],
    'stop': [1.5, 3.0, 99.0],          # tight, medium, no stop
    'trail': [1.5, 2.5],               # flat proven best
    'target': [6.0, 999.0],            # take profit, or no TP
    'grace': [12, 24, 48],             # grace period
    'min_hold': [6, 12, 24],
    'max_hold': [336, 504],            # 14d, 21d
    'breakeven': [0.0, 1.5],           # no breakeven, or at 1.5 ATR
}


def parse_result(output):
    """Extract key metrics from backtest output."""
    metrics = {}
    for line in output.split('\n'):
        for key, pattern in [
            ('return', r'Total Return:.*?([+-]?\d+\.?\d*)%'),
            ('annual', r'Annualized:.*?([+-]?\d+\.?\d*)%'),
            ('calmar', r'Calmar:.*?([+-]?\d+\.?\d*)'),
            ('sharpe', r'Sharpe:.*?([+-]?\d+\.?\d*)'),
            ('sortino', r'Sortino:.*?([+-]?\d+\.?\d*)'),
            ('max_dd', r'Max Drawdown:.*?([+-]?\d+\.?\d*)%'),
            ('trades', r'Total Trades:.*?(\d+)'),
            ('win_rate', r'Win Rate:.*?(\d+\.?\d*)%'),
            ('pf', r'Profit Factor:.*?(\d+\.?\d*)'),
            ('payoff', r'Payoff Ratio:.*?(\d+\.?\d*)'),
            ('fees', r'Total Fees:.*?\$([0-9,]+)'),
            ('avg_hold', r'Avg Hold:.*?(\d+)h'),
        ]:
            if key not in metrics:
                m = re.search(pattern, line)
                if m:
                    val = m.group(1).replace(',', '')
                    metrics[key] = float(val) if '.' in val or key != 'trades' else int(val)
    return metrics


def run_sweep(signal_name, months=12, capital=200000, max_combos=None):
    """Run parameter sweep for a given signal."""
    signal_code = SIGNALS[signal_name]

    # Generate parameter combinations
    keys = list(PARAM_GRID.keys())
    values = [PARAM_GRID[k] for k in keys]
    combos = list(itertools.product(*values))

    if max_combos and len(combos) > max_combos:
        # Sample a subset
        import random
        random.seed(42)
        combos = random.sample(combos, max_combos)

    total = len(combos)
    print(f"\n{'='*90}")
    print(f"RAPID SWEEP: {signal_name} | {total} combos | {months}mo | ${capital:,}")
    print(f"{'='*90}")

    results = []
    for i, combo in enumerate(combos):
        params = dict(zip(keys, combo))
        label = "_".join(f"{k[0]}{v}" for k, v in params.items())

        code = TEMPLATE.format(
            label=f"{signal_name}_{label}",
            signal_code=signal_code,
            **params,
        )

        with open(STRATEGY_FILE, 'w') as f:
            f.write(code)

        cmd = [
            sys.executable, str(PROJECT_ROOT / 'v4' / 'portfolio_backtest.py'),
            '--strategy', 's98', '--months', str(months),
            '--capital', str(capital), '--market', 'perp',
        ]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                                  cwd=str(PROJECT_ROOT))
            metrics = parse_result(proc.stdout + proc.stderr)
        except Exception as e:
            metrics = {'error': str(e)}

        metrics.update(params)
        metrics['signal'] = signal_name
        results.append(metrics)

        ann = metrics.get('annual', 'ERR')
        cal = metrics.get('calmar', 'ERR')
        dd = metrics.get('max_dd', 'ERR')
        trd = metrics.get('trades', 0)
        pf = metrics.get('pf', 'ERR')

        if (i + 1) % 10 == 0 or i == total - 1:
            print(f"  [{i+1}/{total}] Best so far: Ann={max((r.get('annual',float('-inf')) for r in results)):.1f}%")

    # Sort by calmar (or annual if calmar unavailable)
    valid = [r for r in results if 'annual' in r and r.get('trades', 0) > 10]
    valid.sort(key=lambda x: x.get('calmar', float('-inf')), reverse=True)

    print(f"\n{'='*120}")
    print(f"TOP 15 BY CALMAR ({signal_name}):")
    print(f"{'='*120}")
    header = f"{'Lev':>4} {'Stop':>5} {'Trail':>5} {'Tgt':>5} {'Grace':>5} {'MinH':>4} {'MaxH':>4} {'BE':>4} | {'Ann%':>7} {'Cal':>6} {'DD%':>7} {'Sha':>6} {'Sor':>6} {'Trd':>5} {'WR%':>5} {'PF':>5} {'Pay':>5} {'Hld':>4}"
    print(header)
    print("-" * 120)
    for r in valid[:15]:
        print(f"{r.get('leverage','?'):>4} {r.get('stop','?'):>5} {r.get('trail','?'):>5} "
              f"{r.get('target','?'):>5} {r.get('grace','?'):>5} {r.get('min_hold','?'):>4} "
              f"{r.get('max_hold','?'):>4} {r.get('breakeven','?'):>4} | "
              f"{r.get('annual','?'):>7} {r.get('calmar','?'):>6} {r.get('max_dd','?'):>7} "
              f"{r.get('sharpe','?'):>6} {r.get('sortino','?'):>6} {r.get('trades','?'):>5} "
              f"{r.get('win_rate','?'):>5} {r.get('pf','?'):>5} {r.get('payoff','?'):>5} "
              f"{r.get('avg_hold','?'):>4}")

    # Save results
    outfile = PROJECT_ROOT / 'results' / 'v4' / f'sweep_{signal_name}.json'
    with open(outfile, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {outfile}")

    return results


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--signal", type=str, required=True,
                        choices=list(SIGNALS.keys()) + ["all"])
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--capital", type=int, default=200000)
    parser.add_argument("--max-combos", type=int, default=100,
                        help="Max parameter combinations to test")
    args = parser.parse_args()

    if args.signal == "all":
        # Run top 30 combos per signal
        all_results = {}
        for sig_name in SIGNALS:
            results = run_sweep(sig_name, args.months, args.capital, max_combos=30)
            all_results[sig_name] = results

        # Cross-signal comparison
        print(f"\n{'='*90}")
        print("CROSS-SIGNAL COMPARISON (best from each):")
        print(f"{'='*90}")
        for sig_name, results in all_results.items():
            valid = [r for r in results if 'annual' in r and r.get('trades', 0) > 10]
            if valid:
                best = max(valid, key=lambda x: x.get('calmar', float('-inf')))
                print(f"  {sig_name:20s}: Ann={best.get('annual','?'):>7}% Cal={best.get('calmar','?'):>6} "
                      f"DD={best.get('max_dd','?'):>7}% PF={best.get('pf','?'):>5} Trd={best.get('trades','?'):>5}")
    else:
        run_sweep(args.signal, args.months, args.capital, max_combos=args.max_combos)
