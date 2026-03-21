#!/usr/bin/env python3
"""Parameter sweep for s108 rsi_macd_divergence — find optimal config."""
import subprocess, sys, time, json, itertools
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STRATEGY_DIR = PROJECT_ROOT / "strategies"
VENV_PYTHON = "/workspace/venv/bin/python"

sys.path.insert(0, str(PROJECT_ROOT / "tools"))
from sweep_framework import parse_result

TEMPLATE = '''"""Auto-sweep: s108 variant {label}"""
import numpy as np
from engine import StrategyContext, StrategyResult, CRISIS, DOWNTREND

def strategy(ctx: StrategyContext) -> StrategyResult:
    n = len(ctx.ind_1h['close'])
    close = ctx.ind_1h['close']
    rsi = ctx.ind_1h['rsi']
    macd = ctx.ind_1h['macd']
    macd_sig = ctx.ind_1h['macd_signal']
    regime = ctx.regime_1h
    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50
    macd_prev = np.roll(macd, 1); macd_prev[0] = 0
    macd_sig_prev = np.roll(macd_sig, 1); macd_sig_prev[0] = 0
    entry = ((rsi < {rsi_thresh}) & (rsi > rsi_prev) &
             (macd > macd_sig) & (macd_prev <= macd_sig_prev) &
             (regime != CRISIS))
    entry[:200] = False
    return StrategyResult(
        entry_mask=entry,
        direction=np.ones(n, dtype=np.int8),
        stop_mult={stop},
        trail_mult={trail},
        target_mult=999,
        no_stop_bars={grace},
        min_hold={min_hold},
        max_hold={max_hold},
        edge=0.40,
        exit_regimes={{CRISIS, DOWNTREND}},
        max_trade_pct={max_trade_pct},
        breakeven_atr={breakeven},
        exchange='binance',
        name='s108_sweep_{label}',
    )
'''

def run_one(args):
    idx, params, months = args
    strat_id = f"sS{idx:03d}"
    strat_file = STRATEGY_DIR / f"{strat_id}_sweep.py"
    label = params['label']
    result = {**params, 'error': '', 'runtime_s': 0}
    try:
        code = TEMPLATE.format(**params)
        with open(strat_file, 'w') as f:
            f.write(code)
        cmd = [VENV_PYTHON, 'v4/portfolio_backtest.py',
               '--strategy', strat_id, '--months', str(months),
               '--capital', '200000', '--market', 'perp']
        t0 = time.perf_counter()
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=300, cwd=str(PROJECT_ROOT))
        result['runtime_s'] = time.perf_counter() - t0
        metrics = parse_result(proc.stdout + proc.stderr)
        result.update(metrics)
        if not metrics:
            result['error'] = f"No metrics"
    except Exception as e:
        result['error'] = str(e)
    finally:
        if strat_file.exists():
            strat_file.unlink()
    return result

def main():
    # Parameter grid
    rsi_thresholds = [25, 30, 35, 40, 45]
    stop_mults = [2.0, 3.0, 4.0]
    trail_mults = [1.5, 2.0, 3.0, 4.0]
    grace_vals = [12, 24]
    min_holds = [12, 18]
    max_holds = [504, 720]
    max_trade_pcts = [0.10, 0.15]
    breakevens = [0.3, 0.5]

    # Generate combos — focus on most impactful params first
    combos = []
    for rsi_t in rsi_thresholds:
        for trail in trail_mults:
            for stop in stop_mults:
                label = f"rsi{rsi_t}_s{stop}_t{trail}"
                combos.append({
                    'label': label,
                    'rsi_thresh': rsi_t,
                    'stop': stop,
                    'trail': trail,
                    'grace': 24,
                    'min_hold': 18,
                    'max_hold': 720,
                    'max_trade_pct': 0.12,
                    'breakeven': 0.5,
                })

    # Add extra combos for best RSI values with secondary params
    for rsi_t in [30, 35, 40]:
        for mtp in [0.10, 0.15, 0.20]:
            for be in [0.3, 0.5, 0.8]:
                label = f"rsi{rsi_t}_mtp{mtp}_be{be}"
                combos.append({
                    'label': label,
                    'rsi_thresh': rsi_t,
                    'stop': 3.0,
                    'trail': 3.0,
                    'grace': 24,
                    'min_hold': 18,
                    'max_hold': 720,
                    'max_trade_pct': mtp,
                    'breakeven': be,
                })

    print(f"Sweeping {len(combos)} configurations on 6-month window...")
    months = 6  # Use 6mo where we saw best PF

    work = [(i, c, months) for i, c in enumerate(combos)]
    results = []
    workers = 4

    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(run_one, item): i for i, item in enumerate(work)}
        done = 0
        for future in as_completed(futures):
            done += 1
            r = future.result()
            results.append(r)
            ann = r.get('annualized_return', 0)
            pf = r.get('profit_factor', 0)
            dd = r.get('max_dd_pct', 0)
            trd = r.get('total_trades', 0)
            if done % 10 == 0 or pf > 1.2:
                print(f"  [{done:3d}/{len(combos)}] {r['label']:40s} | Ann={ann:7.1f}% PF={pf:.2f} DD={dd:.1f}% Trd={trd}")

    # Sort by PF descending
    ranked = sorted(results, key=lambda x: x.get('profit_factor', 0), reverse=True)

    print(f"\n{'='*100}")
    print(f"  TOP 20 by Profit Factor (6mo window)")
    print(f"{'='*100}")
    for i, r in enumerate(ranked[:20]):
        ann = r.get('annualized_return', 0)
        pf = r.get('profit_factor', 0)
        dd = r.get('max_dd_pct', 0)
        cal = r.get('calmar', 0)
        trd = r.get('total_trades', 0)
        wr = r.get('win_rate', 0)
        print(f"  {i+1:2d}. {r['label']:40s} | Ann={ann:7.1f}% Cal={cal:6.2f} DD={dd:6.1f}% PF={pf:.2f} Trd={trd:4d} WR={wr:.1f}%")

    # Save full results
    out = PROJECT_ROOT / "results" / "v4" / "sweep_s108.json"
    with open(out, 'w') as f:
        json.dump(ranked, f, indent=2, default=str)
    print(f"\n  Full results: {out}")

if __name__ == '__main__':
    main()
