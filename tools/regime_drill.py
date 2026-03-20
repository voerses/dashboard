#!/usr/bin/env python3
"""Deep drill on regime-filtered MACD strategy — target: 300%+ Ann, DD<20%, Calmar>3."""
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

    # Regime filter — key insight
    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
{extra_regime}
{extra_filters}

    # Entry signals — only trade in trend regimes
    entry_long = cross_bull & adx_ok & long_di & liquid & uptrend{long_extra}
    entry_short = cross_bear & adx_ok & short_di & liquid & downtrend{short_extra}

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


# All configs: (label, min_adv, adx, lev, stop, trail, target, grace, min_h, max_h, be, extra_regime, extra_filters, long_extra, short_extra)
configs = [
    # ── BASELINE: regime_trend_1B_lev7 (Ann=104%, Cal=2.02, DD=-51.5%) ──
    ("baseline_lev7", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "", "", "", ""),

    # ── A. DD REDUCTION: Lower leverage (keep PF high, reduce DD) ──
    ("lev3", 1_000_000_000, 20, 3.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "", "", "", ""),
    ("lev4", 1_000_000_000, 20, 4.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "", "", "", ""),
    ("lev5", 1_000_000_000, 20, 5.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "", "", "", ""),

    # ── B. DD REDUCTION: Add stop loss ──
    ("lev7_stop2", 1_000_000_000, 20, 7.0, 2.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "", "", "", ""),
    ("lev7_stop3", 1_000_000_000, 20, 7.0, 3.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "", "", "", ""),
    ("lev5_stop2", 1_000_000_000, 20, 5.0, 2.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "", "", "", ""),
    ("lev5_stop3", 1_000_000_000, 20, 5.0, 3.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "", "", "", ""),

    # ── C. DD REDUCTION: Tighter trail ──
    ("lev7_trail1.5", 1_000_000_000, 20, 7.0, 99.0, 1.5, 999.0, 72, 24, 720, 0.0,
     "", "", "", ""),
    ("lev5_trail1.5", 1_000_000_000, 20, 5.0, 99.0, 1.5, 999.0, 72, 24, 720, 0.0,
     "", "", "", ""),
    ("lev7_trail2.0", 1_000_000_000, 20, 7.0, 99.0, 2.0, 999.0, 72, 24, 720, 0.0,
     "", "", "", ""),

    # ── D. DD REDUCTION: Breakeven + stop ──
    ("lev7_be1.5_stop3", 1_000_000_000, 20, 7.0, 3.0, 2.5, 999.0, 72, 24, 720, 1.5,
     "", "", "", ""),
    ("lev5_be1.5_stop3", 1_000_000_000, 20, 5.0, 3.0, 2.5, 999.0, 72, 24, 720, 1.5,
     "", "", "", ""),
    ("lev7_be2_stop3", 1_000_000_000, 20, 7.0, 3.0, 2.5, 999.0, 72, 24, 720, 2.0,
     "", "", "", ""),

    # ── E. DD REDUCTION: Shorter max hold ──
    ("lev7_max336", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 336, 0.0,
     "", "", "", ""),
    ("lev7_max168", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 168, 0.0,
     "", "", "", ""),

    # ── F. HIGHER QUALITY ENTRIES ──
    # Strong ADX only
    ("lev7_adx25", 1_000_000_000, 25, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "", "", "", ""),
    ("lev7_adx30", 1_000_000_000, 30, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "", "", "", ""),
    # BB squeeze convergence
    ("lev7_squeeze", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "", "    bb_avg = rolling_mean(bb_width, 120)\n    squeeze = bb_width < bb_avg * 0.8",
     " & squeeze", " & squeeze"),
    # Volume spike confirmation
    ("lev7_vol", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "", "    vol_ok = vol_ratio > 1.2",
     " & vol_ok", " & vol_ok"),
    # EMA alignment
    ("lev7_ema", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "", "    ema_bull = ema10 > ema20\n    ema_bear = ema10 < ema20",
     " & ema_bull", " & ema_bear"),

    # ── G. ALSO TRADE IN RANGE REGIME (more trades) ──
    ("lev7_range", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "    range_ok = regime == RANGE", "",
     "", ""),
    # Need to modify entry for range — add RANGE to both
    # Actually let's modify uptrend/downtrend to include RANGE
    ("lev7_trend_range", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "    uptrend = (regime == UPTREND) | (regime == RANGE)\n    downtrend = (regime == DOWNTREND) | (regime == RANGE)",
     "", "", ""),

    # ── H. $500M ADV instead of $1B (more trades) ──
    ("lev7_500M", 500_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "", "", "", ""),
    ("lev5_500M", 500_000_000, 20, 5.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "", "", "", ""),

    # ── I. COMBINED BEST: stop + trail + leverage ──
    ("lev7_stop3_trail2", 1_000_000_000, 20, 7.0, 3.0, 2.0, 999.0, 72, 24, 720, 0.0,
     "", "", "", ""),
    ("lev5_stop2_trail2_be1.5", 1_000_000_000, 20, 5.0, 2.0, 2.0, 999.0, 72, 24, 720, 1.5,
     "", "", "", ""),
    ("lev7_stop3_trail2_be2", 1_000_000_000, 20, 7.0, 3.0, 2.0, 999.0, 72, 24, 720, 2.0,
     "", "", "", ""),

    # ── J. TIGHT RISK MANAGEMENT ──
    ("lev7_stop1.5_trail1.5_be1", 1_000_000_000, 20, 7.0, 1.5, 1.5, 999.0, 48, 24, 504, 1.0,
     "", "", "", ""),
    ("lev5_stop1.5_trail1.5_be1", 1_000_000_000, 20, 5.0, 1.5, 1.5, 999.0, 48, 24, 504, 1.0,
     "", "", "", ""),

    # ── K. TARGET PROFIT ──
    ("lev7_target4", 1_000_000_000, 20, 7.0, 3.0, 2.5, 4.0, 72, 24, 720, 0.0,
     "", "", "", ""),
    ("lev7_target8", 1_000_000_000, 20, 7.0, 3.0, 2.5, 8.0, 72, 24, 720, 0.0,
     "", "", "", ""),
    ("lev7_target6", 1_000_000_000, 20, 7.0, 99.0, 2.5, 6.0, 72, 24, 720, 0.0,
     "", "", "", ""),
]

results = []
for i, cfg in enumerate(configs):
    label, min_adv, adx, lev, stop, trail, target, grace, min_h, max_h, be, extra_regime, extra_filters, long_extra, short_extra = cfg

    code = TEMPLATE.format(
        label=label, min_adv=min_adv, adx_thresh=adx, leverage=lev,
        stop=stop, trail=trail, target=target, grace=grace,
        min_hold=min_h, max_hold=max_h, breakeven=be,
        extra_regime=extra_regime, extra_filters=extra_filters,
        long_extra=long_extra, short_extra=short_extra,
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

# Rank
valid = [r for r in results if 'annual' in r and r.get('trades', 0) > 5]
valid.sort(key=lambda x: x.get('calmar', float('-inf')), reverse=True)

print(f"\n{'='*130}")
print(f"RANKED BY CALMAR RATIO:")
print(f"{'='*130}")
print(f"{'Label':35s} | {'Ann%':>7} {'Cal':>6} {'DD%':>7} {'Sha':>6} {'Sor':>6} {'Trd':>5} {'WR%':>5} {'PF':>5} {'Pay':>5} {'Hld':>4} {'Fees':>8}")
print("-" * 130)
for r in valid:
    print(f"{r['label']:35s} | {r.get('annual','?'):>7} {r.get('calmar','?'):>6} {r.get('max_dd','?'):>7} "
          f"{r.get('sharpe','?'):>6} {r.get('sortino','?'):>6} {r.get('trades','?'):>5} "
          f"{r.get('win_rate','?'):>5} {r.get('pf','?'):>5} {r.get('payoff','?'):>5} "
          f"{r.get('avg_hold','?'):>4} {r.get('fees','?'):>8}")

# Also rank by calmar but only for DD < 30%
low_dd = [r for r in valid if r.get('max_dd', -100) > -30]
if low_dd:
    print(f"\n{'='*130}")
    print(f"LOW-DD CONFIGS (DD > -30%):")
    print(f"{'='*130}")
    for r in low_dd:
        print(f"{r['label']:35s} | {r.get('annual','?'):>7} {r.get('calmar','?'):>6} {r.get('max_dd','?'):>7} "
              f"{r.get('sharpe','?'):>6} {r.get('sortino','?'):>6} {r.get('trades','?'):>5} "
              f"{r.get('win_rate','?'):>5} {r.get('pf','?'):>5} {r.get('payoff','?'):>5} "
              f"{r.get('avg_hold','?'):>4} {r.get('fees','?'):>8}")

outfile = PROJECT_ROOT / 'results' / 'v4' / 'regime_drill.json'
with open(outfile, 'w') as f:
    json.dump(results, f, indent=2)
print(f"\nSaved to {outfile}")
