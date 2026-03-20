#!/usr/bin/env python3
"""Final leverage push on regime-state configs — target 300%+."""
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
        stop_mult={stop}, trail_mult={trail}, target_mult=999.0,
        no_stop_bars={grace}, min_hold={min_hold}, max_hold={max_hold},
        edge=0.40, exit_regimes={{CRISIS}}, breakeven_atr={breakeven},
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


# (label, min_adv, lev, bb_lookback, squeeze_mult, stop, trail, grace, min_hold, max_hold, breakeven, capital)
configs = [
    # ── BASELINES ──
    ("rstate_bb240_lev7", 1e9, 7, 240, 0.8, 99.0, 2.5, 72, 24, 720, 0.0, 200000),
    ("rstate_t0.6_lev7", 1e9, 7, 120, 0.6, 99.0, 2.5, 72, 24, 720, 0.0, 200000),
    ("rstate_t0.8_lev7", 1e9, 7, 120, 0.8, 99.0, 2.5, 72, 24, 720, 0.0, 200000),

    # ── A. LEVERAGE LADDER on bb240 (best Calmar=4.50) ──
    ("bb240_lev10", 1e9, 10, 240, 0.8, 99.0, 2.5, 72, 24, 720, 0.0, 200000),
    ("bb240_lev12", 1e9, 12, 240, 0.8, 99.0, 2.5, 72, 24, 720, 0.0, 200000),
    ("bb240_lev15", 1e9, 15, 240, 0.8, 99.0, 2.5, 72, 24, 720, 0.0, 200000),
    ("bb240_lev20", 1e9, 20, 240, 0.8, 99.0, 2.5, 72, 24, 720, 0.0, 200000),

    # ── B. LEVERAGE LADDER on t0.8 (best trades, Cal=4.11) ──
    ("t0.8_lev10", 1e9, 10, 120, 0.8, 99.0, 2.5, 72, 24, 720, 0.0, 200000),
    ("t0.8_lev12", 1e9, 12, 120, 0.8, 99.0, 2.5, 72, 24, 720, 0.0, 200000),
    ("t0.8_lev15", 1e9, 15, 120, 0.8, 99.0, 2.5, 72, 24, 720, 0.0, 200000),
    ("t0.8_lev20", 1e9, 20, 120, 0.8, 99.0, 2.5, 72, 24, 720, 0.0, 200000),

    # ── C. LEVERAGE on t0.6 (lowest DD=-16.7%) ──
    ("t0.6_lev10", 1e9, 10, 120, 0.6, 99.0, 2.5, 72, 24, 720, 0.0, 200000),
    ("t0.6_lev12", 1e9, 12, 120, 0.6, 99.0, 2.5, 72, 24, 720, 0.0, 200000),
    ("t0.6_lev15", 1e9, 15, 120, 0.6, 99.0, 2.5, 72, 24, 720, 0.0, 200000),
    ("t0.6_lev20", 1e9, 20, 120, 0.6, 99.0, 2.5, 72, 24, 720, 0.0, 200000),

    # ── D. $50K CAPITAL (less impact → higher PF at high lev) ──
    ("bb240_lev10_50k", 1e9, 10, 240, 0.8, 99.0, 2.5, 72, 24, 720, 0.0, 50000),
    ("bb240_lev15_50k", 1e9, 15, 240, 0.8, 99.0, 2.5, 72, 24, 720, 0.0, 50000),
    ("bb240_lev20_50k", 1e9, 20, 240, 0.8, 99.0, 2.5, 72, 24, 720, 0.0, 50000),
    ("t0.8_lev10_50k", 1e9, 10, 120, 0.8, 99.0, 2.5, 72, 24, 720, 0.0, 50000),
    ("t0.8_lev15_50k", 1e9, 15, 120, 0.8, 99.0, 2.5, 72, 24, 720, 0.0, 50000),
    ("t0.8_lev20_50k", 1e9, 20, 120, 0.8, 99.0, 2.5, 72, 24, 720, 0.0, 50000),

    # ── E. $20K CAPITAL ──
    ("bb240_lev15_20k", 1e9, 15, 240, 0.8, 99.0, 2.5, 72, 24, 720, 0.0, 20000),
    ("bb240_lev20_20k", 1e9, 20, 240, 0.8, 99.0, 2.5, 72, 24, 720, 0.0, 20000),
    ("bb240_lev25_20k", 1e9, 25, 240, 0.8, 99.0, 2.5, 72, 24, 720, 0.0, 20000),
    ("t0.8_lev15_20k", 1e9, 15, 120, 0.8, 99.0, 2.5, 72, 24, 720, 0.0, 20000),
    ("t0.8_lev20_20k", 1e9, 20, 120, 0.8, 99.0, 2.5, 72, 24, 720, 0.0, 20000),

    # ── F. STOP LOSS + HIGH LEVERAGE (cap downside per trade) ──
    ("bb240_lev15_stop2", 1e9, 15, 240, 0.8, 2.0, 2.5, 72, 24, 720, 0.0, 200000),
    ("bb240_lev20_stop2", 1e9, 20, 240, 0.8, 2.0, 2.5, 72, 24, 720, 0.0, 200000),
    ("t0.8_lev15_stop2", 1e9, 15, 120, 0.8, 2.0, 2.5, 72, 24, 720, 0.0, 200000),
    ("t0.8_lev20_stop2", 1e9, 20, 120, 0.8, 2.0, 2.5, 72, 24, 720, 0.0, 200000),

    # ── G. STOP + BREAKEVEN + HIGH LEV ──
    ("bb240_lev15_stop2_be1", 1e9, 15, 240, 0.8, 2.0, 2.5, 48, 12, 504, 1.0, 200000),
    ("bb240_lev20_stop2_be1", 1e9, 20, 240, 0.8, 2.0, 2.5, 48, 12, 504, 1.0, 200000),
    ("t0.8_lev15_stop2_be1", 1e9, 15, 120, 0.8, 2.0, 2.5, 48, 12, 504, 1.0, 200000),

    # ── H. TIGHTER TRAIL + HIGH LEV ──
    ("bb240_lev15_trail1.5", 1e9, 15, 240, 0.8, 99.0, 1.5, 72, 24, 720, 0.0, 200000),
    ("t0.8_lev15_trail1.5", 1e9, 15, 120, 0.8, 99.0, 1.5, 72, 24, 720, 0.0, 200000),

    # ── I. LOWER ADV THRESHOLD ($500M) for more trades ──
    ("500M_bb240_lev7", 5e8, 7, 240, 0.8, 99.0, 2.5, 72, 24, 720, 0.0, 200000),
    ("500M_bb240_lev10", 5e8, 10, 240, 0.8, 99.0, 2.5, 72, 24, 720, 0.0, 200000),
    ("500M_t0.8_lev7", 5e8, 7, 120, 0.8, 99.0, 2.5, 72, 24, 720, 0.0, 200000),
]

results = []
for i, (label, min_adv, lev, bb_lb, sq_mult, stop, trail, grace, min_h, max_h, be, cap) in enumerate(configs):
    code = TEMPLATE.format(
        label=label, min_adv=int(min_adv), leverage=lev,
        bb_lookback=bb_lb, squeeze_mult=sq_mult,
        stop=stop, trail=trail, grace=grace,
        min_hold=min_h, max_hold=max_h, breakeven=be,
    )

    with open(STRATEGY_FILE, 'w') as f:
        f.write(code)

    cmd = [sys.executable, str(PROJECT_ROOT / 'v4' / 'portfolio_backtest.py'),
           '--strategy', 's98', '--months', '12',
           '--capital', str(int(cap)), '--market', 'perp']
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
valid.sort(key=lambda x: x.get('calmar', float('-inf')), reverse=True)

print(f"\n{'='*130}")
print("RANKED BY CALMAR:")
print(f"{'='*130}")
print(f"{'Label':30s} | {'Ann%':>7} {'Cal':>6} {'DD%':>7} {'Sha':>6} {'Sor':>6} {'Trd':>5} {'WR%':>5} {'PF':>5} {'Pay':>5} {'Hld':>4} {'Fees':>8}")
print("-" * 130)
for r in valid[:25]:
    print(f"{r['label']:30s} | {r.get('annual','?'):>7} {r.get('calmar','?'):>6} {r.get('max_dd','?'):>7} "
          f"{r.get('sharpe','?'):>6} {r.get('sortino','?'):>6} {r.get('trades','?'):>5} "
          f"{r.get('win_rate','?'):>5} {r.get('pf','?'):>5} {r.get('payoff','?'):>5} "
          f"{r.get('avg_hold','?'):>4} {r.get('fees','?'):>8}")

# DD < 30% filter
low_dd = sorted([r for r in valid if r.get('max_dd', -100) > -30], key=lambda x: x.get('annual', 0), reverse=True)
if low_dd:
    print(f"\n{'='*130}")
    print("LOW-DD (<30%) RANKED BY ANNUAL:")
    print(f"{'='*130}")
    for r in low_dd[:15]:
        print(f"{r['label']:30s} | Ann={r.get('annual','?'):>7}% Cal={r.get('calmar','?'):>6} DD={r.get('max_dd','?'):>7}% "
              f"PF={r.get('pf','?'):>5} WR={r.get('win_rate','?'):>5}% Trd={r.get('trades','?'):>5}")

# Ann > 200%
high_ret = [r for r in valid if r.get('annual', 0) > 200]
if high_ret:
    print(f"\n*** ANN > 200%: ***")
    for r in high_ret:
        print(f"  {r['label']}: Ann={r['annual']}% Cal={r['calmar']} DD={r['max_dd']}%")

outfile = PROJECT_ROOT / 'results' / 'v4' / 'rstate_leverage.json'
with open(outfile, 'w') as f:
    json.dump(results, f, indent=2)
print(f"\nSaved to {outfile}")
