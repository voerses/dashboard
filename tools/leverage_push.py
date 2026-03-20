#!/usr/bin/env python3
"""Leverage push on best configs — target 300%+ annual."""
import subprocess, json, re, os, sys, time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STRATEGY_FILE = PROJECT_ROOT / "strategies" / "s98_sr_breakout_swing.py"

TEMPLATE = '''"""{label}"""
import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_mean, rolling_max, rolling_min)

WARMUP = 200

def strategy(ctx: StrategyContext) -> StrategyResult:
    close = ctx.ind_1h['close']
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']
    volume = ctx.ind_1h['volume']
    n = len(close)
    macd = ctx.ind_1h['macd']
    macd_signal = ctx.ind_1h['macd_signal']
    macd_hist = ctx.ind_1h['macd_hist']
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    rsi = ctx.ind_1h['rsi']
    bb_width = ctx.ind_1h['bb_width']
    ema10 = ctx.ind_1h['ema_10']
    ema20 = ctx.ind_1h['ema_20']
    ema50 = ctx.ind_1h['ema_50']
    vol_ratio = ctx.ind_1h['vol_ratio']
    atr = ctx.ind_1h['atr']
    regime = ctx.regime_1h

    macd_prev = np.roll(macd, 1); macd_prev[0] = np.nan
    cross_bull = (macd > 0) & (macd_prev <= 0)
    cross_bear = (macd < 0) & (macd_prev >= 0)

    # Liquidity filter
    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > {min_adv}

    # Trend confirmation
    adx_ok = adx > {adx_thresh}
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    # Regime filter
    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND

    # BB squeeze filter
    bb_avg = rolling_mean(bb_width, {bb_lookback})
    squeeze = bb_width < bb_avg * {squeeze_mult}

{extra_filters}

    # Entry signals
    entry_long = cross_bull & adx_ok & long_di & liquid & uptrend & squeeze{long_extra}
    entry_short = cross_bear & adx_ok & short_di & liquid & downtrend & squeeze{short_extra}

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


# Fields: (label, min_adv, adx, lev, stop, trail, target, grace, min_h, max_h, be, bb_lookback, squeeze_mult, extra_filters, long_extra, short_extra)
configs = [
    # ── A. LEVERAGE LADDER on sq_tight0.6 (Cal=3.19, DD=-19.8%, PF=2.27) ──
    ("t0.6_lev3", 1_000_000_000, 20, 3.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0, 120, 0.6, "", "", ""),
    ("t0.6_lev5", 1_000_000_000, 20, 5.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0, 120, 0.6, "", "", ""),
    ("t0.6_lev7", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0, 120, 0.6, "", "", ""),
    ("t0.6_lev10", 1_000_000_000, 20, 10.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0, 120, 0.6, "", "", ""),
    ("t0.6_lev12", 1_000_000_000, 20, 12.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0, 120, 0.6, "", "", ""),
    ("t0.6_lev15", 1_000_000_000, 20, 15.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0, 120, 0.6, "", "", ""),
    ("t0.6_lev20", 1_000_000_000, 20, 20.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0, 120, 0.6, "", "", ""),

    # ── B. LEVERAGE LADDER on sq_bb240 (Cal=3.21, DD=-27.8%, PF=2.02) ──
    ("bb240_lev5", 1_000_000_000, 20, 5.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0, 240, 0.8, "", "", ""),
    ("bb240_lev7", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0, 240, 0.8, "", "", ""),
    ("bb240_lev10", 1_000_000_000, 20, 10.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0, 240, 0.8, "", "", ""),
    ("bb240_lev12", 1_000_000_000, 20, 12.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0, 240, 0.8, "", "", ""),
    ("bb240_lev15", 1_000_000_000, 20, 15.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0, 240, 0.8, "", "", ""),

    # ── C. COMBINED: tight squeeze + 240h lookback ──
    ("t0.6_bb240_lev5", 1_000_000_000, 20, 5.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0, 240, 0.6, "", "", ""),
    ("t0.6_bb240_lev7", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0, 240, 0.6, "", "", ""),
    ("t0.6_bb240_lev10", 1_000_000_000, 20, 10.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0, 240, 0.6, "", "", ""),
    ("t0.6_bb240_lev15", 1_000_000_000, 20, 15.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0, 240, 0.6, "", "", ""),

    # ── D. t0.7 + bb240 (more trades, still good PF) ──
    ("t0.7_bb240_lev7", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0, 240, 0.7, "", "", ""),
    ("t0.7_bb240_lev10", 1_000_000_000, 20, 10.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0, 240, 0.7, "", "", ""),
    ("t0.7_bb240_lev15", 1_000_000_000, 20, 15.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0, 240, 0.7, "", "", ""),

    # ── E. t0.5 ULTRA-TIGHT (even fewer, higher quality?) ──
    ("t0.5_lev7", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0, 120, 0.5, "", "", ""),
    ("t0.5_lev10", 1_000_000_000, 20, 10.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0, 120, 0.5, "", "", ""),
    ("t0.5_lev15", 1_000_000_000, 20, 15.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0, 120, 0.5, "", "", ""),
    ("t0.5_bb240_lev10", 1_000_000_000, 20, 10.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0, 240, 0.5, "", "", ""),

    # ── F. STOP + BREAKEVEN on high leverage ──
    ("t0.6_lev10_stop2_be1.5", 1_000_000_000, 20, 10.0, 2.0, 2.5, 999.0, 72, 24, 720, 1.5, 120, 0.6, "", "", ""),
    ("t0.6_lev12_stop2_be1.5", 1_000_000_000, 20, 12.0, 2.0, 2.5, 999.0, 72, 24, 720, 1.5, 120, 0.6, "", "", ""),
    ("t0.6_lev15_stop2_be1", 1_000_000_000, 20, 15.0, 2.0, 2.5, 999.0, 48, 12, 504, 1.0, 120, 0.6, "", "", ""),

    # ── G. CAPITAL SCALING (smaller capital = less impact) ──
    # These use the same strategy but --capital flag is different, handled below
]

results = []
for i, cfg in enumerate(configs):
    label, min_adv, adx, lev, stop, trail, target, grace, min_h, max_h, be, bb_lb, sq_mult, extra_filters, long_extra, short_extra = cfg

    code = TEMPLATE.format(
        label=label, min_adv=min_adv, adx_thresh=adx, leverage=lev,
        stop=stop, trail=trail, target=target, grace=grace,
        min_hold=min_h, max_hold=max_h, breakeven=be,
        bb_lookback=bb_lb, squeeze_mult=sq_mult,
        extra_filters=extra_filters, long_extra=long_extra, short_extra=short_extra,
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
    pay = metrics.get('payoff', 'ERR')
    print(f"[{i+1}/{len(configs)}] {label:30s} | Ann={ann:>8} Cal={cal:>6} DD={dd:>7} PF={pf:>5} WR={wr:>5} Pay={pay:>5} Trd={trd:>5}")

# Also test with smaller capital ($50k) on best configs
print("\n--- CAPITAL SCALING TESTS ($50k) ---")
for small_label, small_lev in [("t0.6_lev10_50k", 10.0), ("t0.6_lev15_50k", 15.0), ("t0.6_lev20_50k", 20.0), ("bb240_lev10_50k", 10.0)]:
    bb_lb = 240 if "bb240" in small_label else 120
    sq_mult = 0.6

    code = TEMPLATE.format(
        label=small_label, min_adv=1_000_000_000, adx_thresh=20, leverage=small_lev,
        stop=99.0, trail=2.5, target=999.0, grace=72,
        min_hold=24, max_hold=720, breakeven=0.0,
        bb_lookback=bb_lb, squeeze_mult=sq_mult,
        extra_filters="", long_extra="", short_extra="",
    )

    with open(STRATEGY_FILE, 'w') as f:
        f.write(code)

    cmd = [sys.executable, str(PROJECT_ROOT / 'v4' / 'portfolio_backtest.py'),
           '--strategy', 's98', '--months', '12',
           '--capital', '50000', '--market', 'perp']
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=str(PROJECT_ROOT))
        metrics = parse_result(proc.stdout + proc.stderr)
    except Exception as e:
        metrics = {'error': str(e)}

    metrics['label'] = small_label
    results.append(metrics)

    ann = metrics.get('annual', 'ERR')
    cal = metrics.get('calmar', 'ERR')
    dd = metrics.get('max_dd', 'ERR')
    trd = metrics.get('trades', 0)
    pf = metrics.get('pf', 'ERR')
    wr = metrics.get('win_rate', 'ERR')
    pay = metrics.get('payoff', 'ERR')
    print(f"  {small_label:30s} | Ann={ann:>8} Cal={cal:>6} DD={dd:>7} PF={pf:>5} WR={wr:>5} Pay={pay:>5} Trd={trd:>5}")

# Rank all
valid = [r for r in results if 'annual' in r and r.get('trades', 0) > 5]
valid.sort(key=lambda x: x.get('calmar', float('-inf')), reverse=True)

print(f"\n{'='*130}")
print(f"ALL CONFIGS RANKED BY CALMAR:")
print(f"{'='*130}")
print(f"{'Label':30s} | {'Ann%':>7} {'Cal':>6} {'DD%':>7} {'Sha':>6} {'Sor':>6} {'Trd':>5} {'WR%':>5} {'PF':>5} {'Pay':>5} {'Hld':>4} {'Fees':>8}")
print("-" * 130)
for r in valid:
    print(f"{r['label']:30s} | {r.get('annual','?'):>7} {r.get('calmar','?'):>6} {r.get('max_dd','?'):>7} "
          f"{r.get('sharpe','?'):>6} {r.get('sortino','?'):>6} {r.get('trades','?'):>5} "
          f"{r.get('win_rate','?'):>5} {r.get('pf','?'):>5} {r.get('payoff','?'):>5} "
          f"{r.get('avg_hold','?'):>4} {r.get('fees','?'):>8}")

# Target analysis
print(f"\n{'='*130}")
print("TARGET ANALYSIS: Ann>300%, DD<20%")
print(f"{'='*130}")
targets = [r for r in valid if r.get('annual', 0) > 300 and r.get('max_dd', -100) > -20]
if targets:
    for r in targets:
        print(f"  *** {r['label']}: Ann={r['annual']}% Cal={r['calmar']} DD={r['max_dd']}% PF={r['pf']}")
else:
    # Show best tradeoff
    print("No config hits both targets. Best configs:")
    best_ann = max(valid, key=lambda x: x.get('annual', float('-inf')))
    best_dd = min([r for r in valid if r.get('annual', 0) > 0], key=lambda x: abs(x.get('max_dd', -100)), default=None)
    best_cal = max(valid, key=lambda x: x.get('calmar', float('-inf')))
    print(f"  Highest Ann: {best_ann['label']} = {best_ann.get('annual')}% (DD={best_ann.get('max_dd')}%)")
    if best_dd:
        print(f"  Lowest DD:   {best_dd['label']} = DD={best_dd.get('max_dd')}% (Ann={best_dd.get('annual')}%)")
    print(f"  Best Calmar: {best_cal['label']} = Cal={best_cal.get('calmar')} (Ann={best_cal.get('annual')}%, DD={best_cal.get('max_dd')}%)")

outfile = PROJECT_ROOT / 'results' / 'v4' / 'leverage_push.json'
with open(outfile, 'w') as f:
    json.dump(results, f, indent=2)
print(f"\nSaved to {outfile}")
