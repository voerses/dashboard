#!/usr/bin/env python3
"""Final push: squeeze + regime MACD combos targeting 300%+ Ann, DD<20%, Calmar>3."""
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
    bb_upper = ctx.ind_1h['bb_upper']
    bb_lower = ctx.ind_1h['bb_lower']
    bb_width = ctx.ind_1h['bb_width']
    bb_pct = ctx.ind_1h['bb_pct']
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


# (label, min_adv, adx, lev, stop, trail, target, grace, min_h, max_h, be, bb_lookback, squeeze_mult, extra_filters, long_extra, short_extra)
configs = [
    # ── BASELINE: lev7_squeeze (Ann=89.7%, Cal=2.83, DD=-31.7%, PF=1.92) ──
    ("baseline_squeeze", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     120, 0.8, "", "", ""),

    # ── A. LEVERAGE on squeeze ──
    ("sq_lev3", 1_000_000_000, 20, 3.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     120, 0.8, "", "", ""),
    ("sq_lev4", 1_000_000_000, 20, 4.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     120, 0.8, "", "", ""),
    ("sq_lev5", 1_000_000_000, 20, 5.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     120, 0.8, "", "", ""),
    ("sq_lev10", 1_000_000_000, 20, 10.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     120, 0.8, "", "", ""),
    ("sq_lev15", 1_000_000_000, 20, 15.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     120, 0.8, "", "", ""),
    ("sq_lev20", 1_000_000_000, 20, 20.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     120, 0.8, "", "", ""),

    # ── B. SQUEEZE TIGHTNESS ──
    ("sq_tight0.6", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     120, 0.6, "", "", ""),
    ("sq_tight0.7", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     120, 0.7, "", "", ""),
    ("sq_loose0.9", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     120, 0.9, "", "", ""),
    ("sq_loose1.0", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     120, 1.0, "", "", ""),

    # ── C. BB LOOKBACK ──
    ("sq_bb60", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     60, 0.8, "", "", ""),
    ("sq_bb240", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     240, 0.8, "", "", ""),

    # ── D. STOP + BREAKEVEN on squeeze ──
    ("sq_lev7_stop3_be2", 1_000_000_000, 20, 7.0, 3.0, 2.5, 999.0, 72, 24, 720, 2.0,
     120, 0.8, "", "", ""),
    ("sq_lev5_stop2_be1.5", 1_000_000_000, 20, 5.0, 2.0, 2.5, 999.0, 72, 24, 720, 1.5,
     120, 0.8, "", "", ""),
    ("sq_lev7_stop2_be1.5", 1_000_000_000, 20, 7.0, 2.0, 2.5, 999.0, 72, 24, 720, 1.5,
     120, 0.8, "", "", ""),

    # ── E. TIGHTER TRAIL on squeeze ──
    ("sq_lev7_trail1.5", 1_000_000_000, 20, 7.0, 99.0, 1.5, 999.0, 72, 24, 720, 0.0,
     120, 0.8, "", "", ""),
    ("sq_lev7_trail2.0", 1_000_000_000, 20, 7.0, 99.0, 2.0, 999.0, 72, 24, 720, 0.0,
     120, 0.8, "", "", ""),
    ("sq_lev7_trail3.0", 1_000_000_000, 20, 7.0, 99.0, 3.0, 999.0, 72, 24, 720, 0.0,
     120, 0.8, "", "", ""),

    # ── F. $500M with squeeze ──
    ("sq_500M_lev7", 500_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     120, 0.8, "", "", ""),
    ("sq_500M_lev10", 500_000_000, 20, 10.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     120, 0.8, "", "", ""),

    # ── G. SQUEEZE + RSI CONFIRMATION ──
    ("sq_rsi_lev7", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     120, 0.8, "    rsi_bull = rsi > 45\n    rsi_bear = rsi < 55",
     " & rsi_bull", " & rsi_bear"),

    # ── H. SQUEEZE + EMA ──
    ("sq_ema_lev7", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     120, 0.8, "    ema_bull = close > ema50\n    ema_bear = close < ema50",
     " & ema_bull", " & ema_bear"),

    # ── I. HIGH LEV + TIGHT RISK MANAGEMENT (targeting <20% DD) ──
    ("sq_lev10_stop2_be1_trail1.5", 1_000_000_000, 20, 10.0, 2.0, 1.5, 999.0, 48, 12, 504, 1.0,
     120, 0.8, "", "", ""),
    ("sq_lev15_stop1.5_be1_trail1.5", 1_000_000_000, 20, 15.0, 1.5, 1.5, 999.0, 48, 12, 504, 1.0,
     120, 0.8, "", "", ""),
    ("sq_lev20_stop1.5_be1_trail1.5", 1_000_000_000, 20, 20.0, 1.5, 1.5, 999.0, 48, 12, 504, 1.0,
     120, 0.8, "", "", ""),

    # ── J. SQUEEZE TIGHT + HIGH LEV ──
    ("sq_t0.6_lev10", 1_000_000_000, 20, 10.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     120, 0.6, "", "", ""),
    ("sq_t0.7_lev10", 1_000_000_000, 20, 10.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     120, 0.7, "", "", ""),
    ("sq_t0.7_lev15", 1_000_000_000, 20, 15.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     120, 0.7, "", "", ""),

    # ── K. SHORTER GRACE FOR DD CONTROL ──
    ("sq_lev7_grace48", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 48, 24, 720, 0.0,
     120, 0.8, "", "", ""),
    ("sq_lev7_grace24", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 24, 24, 720, 0.0,
     120, 0.8, "", "", ""),
    ("sq_lev10_grace48", 1_000_000_000, 20, 10.0, 99.0, 2.5, 999.0, 48, 24, 720, 0.0,
     120, 0.8, "", "", ""),
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
    print(f"[{i+1}/{len(configs)}] {label:35s} | Ann={ann:>8} Cal={cal:>6} DD={dd:>7} PF={pf:>5} WR={wr:>5} Pay={pay:>5} Trd={trd:>5}")

# Rank by Calmar
valid = [r for r in results if 'annual' in r and r.get('trades', 0) > 5]
valid.sort(key=lambda x: x.get('calmar', float('-inf')), reverse=True)

print(f"\n{'='*130}")
print(f"RANKED BY CALMAR RATIO:")
print(f"{'='*130}")
print(f"{'Label':35s} | {'Ann%':>7} {'Cal':>6} {'DD%':>7} {'Sha':>6} {'Sor':>6} {'Trd':>5} {'WR%':>5} {'PF':>5} {'Pay':>5} {'Hld':>4} {'Fees':>8}")
print("-" * 130)
for r in valid[:20]:
    print(f"{r['label']:35s} | {r.get('annual','?'):>7} {r.get('calmar','?'):>6} {r.get('max_dd','?'):>7} "
          f"{r.get('sharpe','?'):>6} {r.get('sortino','?'):>6} {r.get('trades','?'):>5} "
          f"{r.get('win_rate','?'):>5} {r.get('pf','?'):>5} {r.get('payoff','?'):>5} "
          f"{r.get('avg_hold','?'):>4} {r.get('fees','?'):>8}")

# Show configs with DD < 25%
low_dd = [r for r in valid if r.get('max_dd', -100) > -25]
if low_dd:
    print(f"\n{'='*130}")
    print(f"CONFIGS WITH DD < 25%:")
    print(f"{'='*130}")
    for r in low_dd:
        print(f"{r['label']:35s} | Ann={r.get('annual','?'):>7}% Cal={r.get('calmar','?'):>6} DD={r.get('max_dd','?'):>7}% "
              f"PF={r.get('pf','?'):>5} Sha={r.get('sharpe','?'):>6} Sor={r.get('sortino','?'):>6} "
              f"Trd={r.get('trades','?'):>5}")

# Show configs with Ann > 200%
high_ret = [r for r in valid if r.get('annual', 0) > 200]
if high_ret:
    print(f"\n{'='*130}")
    print(f"CONFIGS WITH ANN > 200%:")
    print(f"{'='*130}")
    for r in high_ret:
        print(f"{r['label']:35s} | Ann={r.get('annual','?'):>7}% Cal={r.get('calmar','?'):>6} DD={r.get('max_dd','?'):>7}% "
              f"PF={r.get('pf','?'):>5}")

outfile = PROJECT_ROOT / 'results' / 'v4' / 'final_push.json'
with open(outfile, 'w') as f:
    json.dump(results, f, indent=2)
print(f"\nSaved to {outfile}")
