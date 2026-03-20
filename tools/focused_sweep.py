#!/usr/bin/env python3
"""Focused sweep on MACD zero-cross with liquidity filter — find PF > 1.0"""
import subprocess, json, re, os, sys, time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STRATEGY_FILE = PROJECT_ROOT / "strategies" / "s98_sr_breakout_swing.py"

TEMPLATE = '''"""s98 MACD zero-cross liquid sweep: {label}"""
import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_mean)

WARMUP = 200

def strategy(ctx: StrategyContext) -> StrategyResult:
    close = ctx.ind_1h['close']
    volume = ctx.ind_1h['volume']
    n = len(close)
    macd = ctx.ind_1h['macd']
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    regime = ctx.regime_1h

    macd_prev = np.roll(macd, 1); macd_prev[0] = np.nan
    cross_bull = (macd > 0) & (macd_prev <= 0)
    cross_bear = (macd < 0) & (macd_prev >= 0)

    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > {min_adv}

    adx_ok = adx > {adx_thresh}
    regime_ok = regime != CRISIS
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    entry_long = cross_bull & adx_ok & long_di & liquid & regime_ok
    entry_short = cross_bear & adx_ok & short_di & liquid & regime_ok

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


configs = [
    # (label, min_adv, adx, lev, stop, trail, target, grace, min_h, max_h, be)
    # Baseline
    ("base_100M", 100_000_000, 20, 2.0, 1.5, 1.5, 6.0, 48, 24, 504, 0.0),
    # Ultra-liquid only
    ("ultra_500M", 500_000_000, 20, 2.0, 1.5, 1.5, 6.0, 48, 24, 504, 0.0),
    ("ultra_1B", 1_000_000_000, 20, 2.0, 1.5, 1.5, 6.0, 48, 24, 504, 0.0),
    # Higher leverage
    ("lev3_100M", 100_000_000, 20, 3.0, 1.5, 1.5, 6.0, 48, 24, 504, 0.0),
    ("lev3_500M", 500_000_000, 20, 3.0, 1.5, 1.5, 6.0, 48, 24, 504, 0.0),
    # No stop (just trail + max_hold)
    ("nostop_100M", 100_000_000, 20, 2.0, 99.0, 1.5, 999.0, 48, 24, 504, 0.0),
    ("nostop_500M", 500_000_000, 20, 2.0, 99.0, 1.5, 999.0, 48, 24, 504, 0.0),
    ("nostop_1B", 1_000_000_000, 20, 2.0, 99.0, 1.5, 999.0, 48, 24, 504, 0.0),
    # No stop + higher leverage
    ("nostop_lev3_500M", 500_000_000, 20, 3.0, 99.0, 1.5, 999.0, 48, 24, 504, 0.0),
    ("nostop_lev3_1B", 1_000_000_000, 20, 3.0, 99.0, 1.5, 999.0, 48, 24, 504, 0.0),
    # Wider trail
    ("wide_trail_500M", 500_000_000, 20, 2.0, 99.0, 2.5, 999.0, 48, 24, 504, 0.0),
    ("wide_trail_1B", 1_000_000_000, 20, 2.0, 99.0, 2.5, 999.0, 48, 24, 504, 0.0),
    # Longer grace
    ("grace72_500M", 500_000_000, 20, 2.0, 99.0, 1.5, 999.0, 72, 24, 504, 0.0),
    ("grace72_1B", 1_000_000_000, 20, 2.0, 99.0, 1.5, 999.0, 72, 24, 504, 0.0),
    # Lower ADX threshold
    ("adx15_500M", 500_000_000, 15, 2.0, 1.5, 1.5, 6.0, 48, 24, 504, 0.0),
    # No DI/ADX filter at all (removed in template - need custom)
    # Max hold longer
    ("max720_500M", 500_000_000, 20, 2.0, 99.0, 1.5, 999.0, 48, 24, 720, 0.0),
    # Breakeven at 2 ATR
    ("be2_500M", 500_000_000, 20, 2.0, 1.5, 1.5, 6.0, 48, 24, 504, 2.0),
    # Combined: ultra liquid + no stop + wider trail + long grace
    ("combo_1B", 1_000_000_000, 20, 2.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0),
    ("combo_lev3_1B", 1_000_000_000, 20, 3.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0),
    # Lev 4
    ("lev4_1B", 1_000_000_000, 20, 4.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0),
]

results = []
for i, (label, min_adv, adx, lev, stop, trail, target, grace, min_h, max_h, be) in enumerate(configs):
    code = TEMPLATE.format(
        label=label, min_adv=min_adv, adx_thresh=adx, leverage=lev,
        stop=stop, trail=trail, target=target, grace=grace,
        min_hold=min_h, max_hold=max_h, breakeven=be,
    )
    with open(STRATEGY_FILE, 'w') as f:
        f.write(code)

    cmd = [sys.executable, str(PROJECT_ROOT / 'v4' / 'portfolio_backtest.py'),
           '--strategy', 's98', '--months', '12',
           '--capital', '200000', '--market', 'perp']
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=str(PROJECT_ROOT))
        metrics = parse_result(proc.stdout + proc.stderr)
    except Exception as e:
        metrics = {'error': str(e)}

    metrics['label'] = label
    results.append(metrics)

    ann = metrics.get('annual', 'ERR')
    cal = metrics.get('calmar', 'ERR')
    dd = metrics.get('max_dd', 'ERR')
    trd = metrics.get('trades', 0)
    pf = metrics.get('pf', 'ERR')
    wr = metrics.get('win_rate', 'ERR')
    print(f"[{i+1}/{len(configs)}] {label:25s} | Ann={ann:>8} Cal={cal:>6} DD={dd:>7} PF={pf:>5} WR={wr:>5} Trd={trd:>5}")

# Sort by annual return
valid = [r for r in results if 'annual' in r and r.get('trades', 0) > 5]
valid.sort(key=lambda x: x['annual'], reverse=True)

print(f"\n{'='*110}")
print(f"RANKED BY ANNUAL RETURN:")
print(f"{'='*110}")
print(f"{'Label':25s} | {'Ann%':>7} {'Cal':>6} {'DD%':>7} {'Sha':>6} {'Sor':>6} {'Trd':>5} {'WR%':>5} {'PF':>5} {'Pay':>5} {'Hld':>4} {'Fees':>8}")
print("-" * 110)
for r in valid:
    print(f"{r['label']:25s} | {r.get('annual','?'):>7} {r.get('calmar','?'):>6} {r.get('max_dd','?'):>7} "
          f"{r.get('sharpe','?'):>6} {r.get('sortino','?'):>6} {r.get('trades','?'):>5} "
          f"{r.get('win_rate','?'):>5} {r.get('pf','?'):>5} {r.get('payoff','?'):>5} "
          f"{r.get('avg_hold','?'):>4} {r.get('fees','?'):>8}")

outfile = PROJECT_ROOT / 'results' / 'v4' / 'focused_sweep_macd.json'
with open(outfile, 'w') as f:
    json.dump(results, f, indent=2)
print(f"\nSaved to {outfile}")
