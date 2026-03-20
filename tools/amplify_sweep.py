#!/usr/bin/env python3
"""Amplify sweep: push PF>1.0 configs toward 300%+ annual return."""
import subprocess, json, re, os, sys, time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STRATEGY_FILE = PROJECT_ROOT / "strategies" / "s98_sr_breakout_swing.py"

# Template with more advanced features: signal combos, asymmetric sizing
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
    ema10 = ctx.ind_1h['ema_10']
    ema20 = ctx.ind_1h['ema_20']
    ema50 = ctx.ind_1h['ema_50']
    vol_ratio = ctx.ind_1h['vol_ratio']
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
    regime_ok = regime != CRISIS
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    # Additional filters
{extra_filters}

    # Entry signals
    entry_long = cross_bull & adx_ok & long_di & liquid & regime_ok{long_extra}
    entry_short = cross_bear & adx_ok & short_di & liquid & regime_ok{short_extra}

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


# ── Configuration sets ──────────────────────────────────────────
# Base: combo_1B was best (Ann=+2.1%, PF=1.04, DD=-20.4%)
# Parameters: min_adv=1B, adx=20, lev=2, stop=99, trail=2.5, target=999, grace=72, min_h=24, max_h=720, be=0

configs = [
    # ── A. LEVERAGE AMPLIFICATION on combo_1B base ──
    # combo_1B base with higher leverage
    ("combo_1B_lev5", 1_000_000_000, 20, 5.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "", "", ""),
    ("combo_1B_lev7", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "", "", ""),
    ("combo_1B_lev10", 1_000_000_000, 20, 10.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "", "", ""),

    # ── B. LEVERAGE AMPLIFICATION on ultra_500M base ──
    # ultra_500M was also PF=1.06 with more trades
    ("ultra_500M_lev5", 500_000_000, 20, 5.0, 1.5, 1.5, 6.0, 48, 24, 504, 0.0,
     "", "", ""),
    ("ultra_500M_lev7", 500_000_000, 20, 7.0, 1.5, 1.5, 6.0, 48, 24, 504, 0.0,
     "", "", ""),
    ("ultra_500M_lev10", 500_000_000, 20, 10.0, 1.5, 1.5, 6.0, 48, 24, 504, 0.0,
     "", "", ""),

    # ── C. SIGNAL QUALITY FILTERS (reduce trades, increase PF) ──
    # Add RSI momentum confirmation
    ("rsi_bull_1B", 1_000_000_000, 20, 5.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "    rsi_bull = rsi > 50\n    rsi_bear = rsi < 50",
     " & rsi_bull", " & rsi_bear"),
    # Add volume confirmation
    ("vol_confirm_1B", 1_000_000_000, 20, 5.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "    vol_ok = vol_ratio > 1.2",
     " & vol_ok", " & vol_ok"),
    # EMA trend alignment
    ("ema_align_1B", 1_000_000_000, 20, 5.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "    ema_bull = ema10 > ema20\n    ema_bear = ema10 < ema20",
     " & ema_bull", " & ema_bear"),
    # MACD histogram positive (confirming momentum)
    ("hist_confirm_1B", 1_000_000_000, 20, 5.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "    hist_bull = macd_hist > 0\n    hist_bear = macd_hist < 0",
     " & hist_bull", " & hist_bear"),
    # Combined: RSI + EMA
    ("rsi_ema_1B", 1_000_000_000, 20, 5.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "    rsi_bull = rsi > 50\n    rsi_bear = rsi < 50\n    ema_bull = ema10 > ema20\n    ema_bear = ema10 < ema20",
     " & rsi_bull & ema_bull", " & rsi_bear & ema_bear"),

    # ── D. HIGHER ADX FILTER (stronger trends only) ──
    ("adx25_1B_lev5", 1_000_000_000, 25, 5.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "", "", ""),
    ("adx30_1B_lev5", 1_000_000_000, 30, 5.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "", "", ""),

    # ── E. TRADE MANAGEMENT VARIATIONS on high leverage ──
    # Tighter trail with high leverage
    ("tight_trail_1B_lev7", 1_000_000_000, 20, 7.0, 99.0, 1.5, 999.0, 72, 24, 720, 0.0,
     "", "", ""),
    # Shorter hold
    ("short_hold_1B_lev7", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 48, 12, 336, 0.0,
     "", "", ""),
    # With stop
    ("stop_1B_lev7", 1_000_000_000, 20, 7.0, 3.0, 2.5, 8.0, 72, 24, 720, 0.0,
     "", "", ""),

    # ── F. $500M ULTRA-LIQUID + QUALITY FILTERS + HIGH LEV ──
    ("ultra_rsi_500M_lev7", 500_000_000, 20, 7.0, 1.5, 1.5, 6.0, 48, 24, 504, 0.0,
     "    rsi_bull = rsi > 50\n    rsi_bear = rsi < 50",
     " & rsi_bull", " & rsi_bear"),
    ("ultra_ema_500M_lev7", 500_000_000, 20, 7.0, 1.5, 1.5, 6.0, 48, 24, 504, 0.0,
     "    ema_bull = ema10 > ema20\n    ema_bear = ema10 < ema20",
     " & ema_bull", " & ema_bear"),
    ("ultra_vol_500M_lev7", 500_000_000, 20, 7.0, 1.5, 1.5, 6.0, 48, 24, 504, 0.0,
     "    vol_ok = vol_ratio > 1.2",
     " & vol_ok", " & vol_ok"),

    # ── G. BB SQUEEZE + MACD (convergence of signals) ──
    ("bb_squeeze_macd_1B_lev5", 1_000_000_000, 20, 5.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "    bb_avg = rolling_mean(bb_width, 120)\n    squeeze = bb_width < bb_avg * 0.8",
     " & squeeze", " & squeeze"),
    ("bb_squeeze_macd_1B_lev10", 1_000_000_000, 20, 10.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "    bb_avg = rolling_mean(bb_width, 120)\n    squeeze = bb_width < bb_avg * 0.8",
     " & squeeze", " & squeeze"),

    # ── H. REGIME-FILTERED (only trade in uptrend/downtrend) ──
    ("regime_trend_1B_lev7", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "    uptrend = regime == UPTREND\n    downtrend = regime == DOWNTREND",
     " & uptrend", " & downtrend"),

    # ── I. LONG-ONLY (if market has upward bias) ──
    ("long_only_1B_lev7", 1_000_000_000, 20, 7.0, 99.0, 2.5, 999.0, 72, 24, 720, 0.0,
     "    # Long only - suppress shorts", "", ""),
    # For long-only, we need a different template approach - use direction override
]


results = []
for i, cfg in enumerate(configs):
    if len(cfg) == 14:
        label, min_adv, adx, lev, stop, trail, target, grace, min_h, max_h, be, extra_filters, long_extra, short_extra = cfg
    else:
        continue

    # Special handling for long-only
    if "long_only" in label:
        code = TEMPLATE.format(
            label=label, min_adv=min_adv, adx_thresh=adx, leverage=lev,
            stop=stop, trail=trail, target=target, grace=grace,
            min_hold=min_h, max_hold=max_h, breakeven=be,
            extra_filters="", long_extra="", short_extra="",
        )
        # Override: suppress short entries
        code = code.replace(
            "    entry_short = cross_bear & adx_ok & short_di & liquid & regime_ok",
            "    entry_short = np.zeros(n, dtype=bool)  # long only"
        )
    else:
        code = TEMPLATE.format(
            label=label, min_adv=min_adv, adx_thresh=adx, leverage=lev,
            stop=stop, trail=trail, target=target, grace=grace,
            min_hold=min_h, max_hold=max_h, breakeven=be,
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

# Rank results
valid = [r for r in results if 'annual' in r and r.get('trades', 0) > 5]
valid.sort(key=lambda x: x['annual'], reverse=True)

print(f"\n{'='*120}")
print(f"RANKED BY ANNUAL RETURN:")
print(f"{'='*120}")
print(f"{'Label':30s} | {'Ann%':>7} {'Cal':>6} {'DD%':>7} {'Sha':>6} {'Sor':>6} {'Trd':>5} {'WR%':>5} {'PF':>5} {'Pay':>5} {'Hld':>4} {'Fees':>8}")
print("-" * 120)
for r in valid:
    print(f"{r['label']:30s} | {r.get('annual','?'):>7} {r.get('calmar','?'):>6} {r.get('max_dd','?'):>7} "
          f"{r.get('sharpe','?'):>6} {r.get('sortino','?'):>6} {r.get('trades','?'):>5} "
          f"{r.get('win_rate','?'):>5} {r.get('pf','?'):>5} {r.get('payoff','?'):>5} "
          f"{r.get('avg_hold','?'):>4} {r.get('fees','?'):>8}")

outfile = PROJECT_ROOT / 'results' / 'v4' / 'amplify_sweep.json'
with open(outfile, 'w') as f:
    json.dump(results, f, indent=2)
print(f"\nSaved to {outfile}")
