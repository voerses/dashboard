#!/usr/bin/env python3
"""Concentration and position sizing push on best regime-state configs."""
import subprocess, json, re, os, sys, time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STRATEGY_FILE = PROJECT_ROOT / "strategies" / "s98_sr_breakout_swing.py"

TEMPLATE = '''"""{label}"""
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
    ema10 = ctx.ind_1h['ema_10']
    ema20 = ctx.ind_1h['ema_20']
    ema50 = ctx.ind_1h['ema_50']
    bb_width = ctx.ind_1h['bb_width']
    regime = ctx.regime_1h

    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > {min_adv}

    bb_avg = rolling_mean(bb_width, {bb_lookback})
    squeeze = bb_width < bb_avg * {squeeze_mult}

    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
    adx_ok = adx > 20
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    regime_prev = np.roll(regime, 1); regime_prev[0] = regime[0]
    regime_change_up = (regime == UPTREND) & (regime_prev != UPTREND)
    regime_change_down = (regime == DOWNTREND) & (regime_prev != DOWNTREND)

    ema_bull = (ema10 > ema20) & (ema20 > ema50)
    ema_bear = (ema10 < ema20) & (ema20 < ema50)

    macd_bull = macd > 0
    macd_bear = macd < 0
    macd_prev = np.roll(macd, 1); macd_prev[0] = np.nan
    macd_cross_bull = (macd > 0) & (macd_prev <= 0)
    macd_cross_bear = (macd < 0) & (macd_prev >= 0)

    entry_long = ((regime_change_up & macd_bull & ema_bull) | (macd_cross_bull & uptrend)) & adx_ok & long_di & liquid & squeeze
    entry_short = ((regime_change_down & macd_bear & ema_bear) | (macd_cross_bear & downtrend)) & adx_ok & short_di & liquid & squeeze

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long, 1, np.where(entry_short, -1, 0)).astype(np.int8)
    entry_mask[:WARMUP] = False
    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask, direction=direction,
        market_type=MarketType.PERP, leverage={leverage},
        stop_mult=99.0, trail_mult=2.5, target_mult=999.0,
        no_stop_bars=72, min_hold=24, max_hold=720,
        edge=0.40, exit_regimes={{CRISIS}}, breakeven_atr=0.0,
        exchange='binance', name='s98_sr_breakout_swing',
    )
'''


def parse_result(output):
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


# (label, bb_lookback, squeeze_mult, leverage, min_adv, capital, concentration, max_pos, adv_cap)
configs = [
    # ── BASELINES with default sizing ──
    ("bb240_lev7_base", 240, 0.8, 7, 1e9, 200000, 0.10, 15, 0.05),
    ("t0.8_lev7_base", 120, 0.8, 7, 1e9, 200000, 0.10, 15, 0.05),

    # ── A. Higher concentration (each position gets more capital) ──
    ("bb240_lev7_c20", 240, 0.8, 7, 1e9, 200000, 0.20, 15, 0.05),
    ("bb240_lev7_c30", 240, 0.8, 7, 1e9, 200000, 0.30, 15, 0.05),
    ("bb240_lev7_c50", 240, 0.8, 7, 1e9, 200000, 0.50, 15, 0.05),
    ("bb240_lev7_c100", 240, 0.8, 7, 1e9, 200000, 1.00, 15, 0.05),
    ("t0.8_lev7_c20", 120, 0.8, 7, 1e9, 200000, 0.20, 15, 0.05),
    ("t0.8_lev7_c30", 120, 0.8, 7, 1e9, 200000, 0.30, 15, 0.05),
    ("t0.8_lev7_c50", 120, 0.8, 7, 1e9, 200000, 0.50, 15, 0.05),

    # ── B. Higher ADV cap (allow bigger positions relative to volume) ──
    ("bb240_lev7_adv10", 240, 0.8, 7, 1e9, 200000, 0.10, 15, 0.10),
    ("bb240_lev7_adv20", 240, 0.8, 7, 1e9, 200000, 0.10, 15, 0.20),
    ("bb240_lev7_c30_adv10", 240, 0.8, 7, 1e9, 200000, 0.30, 15, 0.10),
    ("bb240_lev7_c50_adv10", 240, 0.8, 7, 1e9, 200000, 0.50, 15, 0.10),

    # ── C. Fewer max positions (concentrate into fewer trades) ──
    ("bb240_lev7_mp5", 240, 0.8, 7, 1e9, 200000, 0.20, 5, 0.05),
    ("bb240_lev7_mp3", 240, 0.8, 7, 1e9, 200000, 0.33, 3, 0.05),

    # ── D. Higher leverage + concentration ──
    ("bb240_lev10_c30", 240, 0.8, 10, 1e9, 200000, 0.30, 15, 0.05),
    ("bb240_lev10_c50", 240, 0.8, 10, 1e9, 200000, 0.50, 15, 0.05),
    ("bb240_lev12_c30", 240, 0.8, 12, 1e9, 200000, 0.30, 15, 0.05),
    ("bb240_lev12_c50", 240, 0.8, 12, 1e9, 200000, 0.50, 15, 0.05),

    # ── E. t0.8 + leverage + concentration ──
    ("t0.8_lev10_c30", 120, 0.8, 10, 1e9, 200000, 0.30, 15, 0.05),
    ("t0.8_lev10_c50", 120, 0.8, 10, 1e9, 200000, 0.50, 15, 0.05),
    ("t0.8_lev12_c30", 120, 0.8, 12, 1e9, 200000, 0.30, 15, 0.05),

    # ── F. $50k capital + high leverage + high concentration ──
    ("bb240_lev10_c50_50k", 240, 0.8, 10, 1e9, 50000, 0.50, 15, 0.10),
    ("bb240_lev15_c50_50k", 240, 0.8, 15, 1e9, 50000, 0.50, 15, 0.10),
    ("bb240_lev20_c50_50k", 240, 0.8, 20, 1e9, 50000, 0.50, 15, 0.10),
    ("t0.8_lev10_c50_50k", 120, 0.8, 10, 1e9, 50000, 0.50, 15, 0.10),
    ("t0.8_lev15_c50_50k", 120, 0.8, 15, 1e9, 50000, 0.50, 15, 0.10),

    # ── G. PAPER TRADING CONFIG (concentration=1.0) on best strategies ──
    ("bb240_lev7_paper", 240, 0.8, 7, 1e9, 200000, 1.00, 15, 0.10),
    ("bb240_lev10_paper", 240, 0.8, 10, 1e9, 200000, 1.00, 15, 0.10),
    ("t0.8_lev7_paper", 120, 0.8, 7, 1e9, 200000, 1.00, 15, 0.10),
    ("t0.8_lev10_paper", 120, 0.8, 10, 1e9, 200000, 1.00, 15, 0.10),
]

results = []
for i, (label, bb_lb, sq_mult, lev, min_adv, cap, conc, mp, adv_cap) in enumerate(configs):
    code = TEMPLATE.format(
        label=label, min_adv=int(min_adv), leverage=lev,
        bb_lookback=bb_lb, squeeze_mult=sq_mult,
    )

    with open(STRATEGY_FILE, 'w') as f:
        f.write(code)

    cmd = [sys.executable, str(PROJECT_ROOT / 'v4' / 'portfolio_backtest.py'),
           '--strategy', 's98', '--months', '12',
           '--capital', str(int(cap)), '--market', 'perp',
           '--concentration', str(conc), '--max-positions', str(mp),
           '--adv-cap', str(adv_cap)]
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
    print(f"[{i+1}/{len(configs)}] {label:30s} | Ann={ann:>8} Cal={cal:>6} DD={dd:>7} PF={pf:>5} WR={wr:>5} Trd={trd:>5}")

# Rank
valid = [r for r in results if 'annual' in r and r.get('trades', 0) > 5]
valid.sort(key=lambda x: x.get('annual', float('-inf')), reverse=True)

print(f"\n{'='*130}")
print("RANKED BY ANNUAL RETURN:")
print(f"{'='*130}")
print(f"{'Label':30s} | {'Ann%':>7} {'Cal':>6} {'DD%':>7} {'Sha':>6} {'Sor':>6} {'Trd':>5} {'WR%':>5} {'PF':>5} {'Pay':>5} {'Hld':>4} {'Fees':>8}")
print("-" * 130)
for r in valid:
    print(f"{r['label']:30s} | {r.get('annual','?'):>7} {r.get('calmar','?'):>6} {r.get('max_dd','?'):>7} "
          f"{r.get('sharpe','?'):>6} {r.get('sortino','?'):>6} {r.get('trades','?'):>5} "
          f"{r.get('win_rate','?'):>5} {r.get('pf','?'):>5} {r.get('payoff','?'):>5} "
          f"{r.get('avg_hold','?'):>4} {r.get('fees','?'):>8}")

# Ann > 200%
high = [r for r in valid if r.get('annual', 0) > 200]
if high:
    print(f"\n*** CONFIGS WITH ANN > 200%: ***")
    for r in high:
        print(f"  {r['label']}: Ann={r['annual']}% Cal={r['calmar']} DD={r['max_dd']}% PF={r['pf']}")

outfile = PROJECT_ROOT / 'results' / 'v4' / 'concentration_push.json'
with open(outfile, 'w') as f:
    json.dump(results, f, indent=2)
print(f"\nSaved to {outfile}")
