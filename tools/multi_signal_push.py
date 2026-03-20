#!/usr/bin/env python3
"""Multi-signal strategy: combine multiple high-PF signals with squeeze+regime filter."""
import subprocess, json, re, os, sys, time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STRATEGY_FILE = PROJECT_ROOT / "strategies" / "s98_sr_breakout_swing.py"


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


# ── MULTI-SIGNAL STRATEGIES ──────────────────────────────────────
STRATEGIES = {
    "multi_3sig": '''"""{label}"""
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
    macd_hist = ctx.ind_1h['macd_hist']
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    ema10 = ctx.ind_1h['ema_10']
    ema20 = ctx.ind_1h['ema_20']
    bb_upper = ctx.ind_1h['bb_upper']
    bb_lower = ctx.ind_1h['bb_lower']
    bb_width = ctx.ind_1h['bb_width']
    regime = ctx.regime_1h

    # Liquidity
    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > {min_adv}

    # Squeeze filter
    bb_avg = rolling_mean(bb_width, {bb_lookback})
    squeeze = bb_width < bb_avg * {squeeze_mult}

    # Regime
    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
    adx_ok = adx > 20
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    # Signal 1: MACD zero cross
    macd_prev = np.roll(macd, 1); macd_prev[0] = np.nan
    sig1_long = (macd > 0) & (macd_prev <= 0)
    sig1_short = (macd < 0) & (macd_prev >= 0)

    # Signal 2: EMA cross
    ema10_prev = np.roll(ema10, 1); ema10_prev[0] = np.nan
    ema20_prev = np.roll(ema20, 1); ema20_prev[0] = np.nan
    sig2_long = (ema10 > ema20) & (ema10_prev <= ema20_prev)
    sig2_short = (ema10 < ema20) & (ema10_prev >= ema20_prev)

    # Signal 3: BB breakout
    bb_up_prev = np.roll(bb_upper, 1); bb_up_prev[0] = np.nan
    bb_lo_prev = np.roll(bb_lower, 1); bb_lo_prev[0] = np.nan
    close_prev = np.roll(close, 1); close_prev[0] = np.nan
    sig3_long = (close > bb_up_prev) & (close_prev <= bb_up_prev)
    sig3_short = (close < bb_lo_prev) & (close_prev >= bb_lo_prev)

    # Any signal triggers entry (union)
    any_long = sig1_long | sig2_long | sig3_long
    any_short = sig1_short | sig2_short | sig3_short

    entry_long = any_long & adx_ok & long_di & liquid & uptrend & squeeze
    entry_short = any_short & adx_ok & short_di & liquid & downtrend & squeeze

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
''',

    "multi_2agree": '''"""{label}"""
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
    bb_upper = ctx.ind_1h['bb_upper']
    bb_lower = ctx.ind_1h['bb_lower']
    bb_width = ctx.ind_1h['bb_width']
    regime = ctx.regime_1h

    # Liquidity
    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > {min_adv}

    # Squeeze
    bb_avg = rolling_mean(bb_width, {bb_lookback})
    squeeze = bb_width < bb_avg * {squeeze_mult}

    # Regime + DI
    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
    adx_ok = adx > 20
    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    # Signal 1: MACD zero cross
    macd_prev = np.roll(macd, 1); macd_prev[0] = np.nan
    sig1_long = (macd > 0) & (macd_prev <= 0)
    sig1_short = (macd < 0) & (macd_prev >= 0)

    # Signal 2: EMA cross
    ema10_prev = np.roll(ema10, 1); ema10_prev[0] = np.nan
    ema20_prev = np.roll(ema20, 1); ema20_prev[0] = np.nan
    sig2_long = (ema10 > ema20) & (ema10_prev <= ema20_prev)
    sig2_short = (ema10 < ema20) & (ema10_prev >= ema20_prev)

    # Signal 3: BB breakout
    bb_up_prev = np.roll(bb_upper, 1); bb_up_prev[0] = np.nan
    bb_lo_prev = np.roll(bb_lower, 1); bb_lo_prev[0] = np.nan
    close_prev = np.roll(close, 1); close_prev[0] = np.nan
    sig3_long = (close > bb_up_prev) & (close_prev <= bb_up_prev)
    sig3_short = (close < bb_lo_prev) & (close_prev >= bb_lo_prev)

    # Require 2 of 3 signals to agree
    agree_long = (sig1_long.astype(int) + sig2_long.astype(int) + sig3_long.astype(int)) >= 2
    agree_short = (sig1_short.astype(int) + sig2_short.astype(int) + sig3_short.astype(int)) >= 2

    entry_long = agree_long & adx_ok & long_di & liquid & uptrend & squeeze
    entry_short = agree_short & adx_ok & short_di & liquid & downtrend & squeeze

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
''',

    "regime_state": '''"""{label}"""
import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_mean)

WARMUP = 200

def strategy(ctx: StrategyContext) -> StrategyResult:
    """State-based: always be positioned in trending regime with squeeze."""
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

    # Liquidity
    dollar_vol = close * volume
    rolling_adv = rolling_mean(dollar_vol, 24) * 24
    liquid = rolling_adv > {min_adv}

    # Squeeze
    bb_avg = rolling_mean(bb_width, {bb_lookback})
    squeeze = bb_width < bb_avg * {squeeze_mult}

    # State-based: enter whenever conditions align (not just on crosses)
    uptrend = regime == UPTREND
    downtrend = regime == DOWNTREND
    adx_ok = adx > 20

    # Regime just changed to uptrend/downtrend
    regime_prev = np.roll(regime, 1); regime_prev[0] = regime[0]
    regime_change_up = (regime == UPTREND) & (regime_prev != UPTREND)
    regime_change_down = (regime == DOWNTREND) & (regime_prev != DOWNTREND)

    # EMA alignment
    ema_bull = (ema10 > ema20) & (ema20 > ema50)
    ema_bear = (ema10 < ema20) & (ema20 < ema50)

    long_di = plus_di > minus_di
    short_di = minus_di > plus_di

    # MACD confirmation (above/below zero, not just crossing)
    macd_bull = macd > 0
    macd_bear = macd < 0

    # Enter on regime change or MACD zero-cross within trending regime
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
''',
}


configs = [
    # (strategy_type, label, min_adv, leverage, bb_lookback, squeeze_mult, capital, extra_args)
    # Multi-3-signal union with squeeze+regime
    ("multi_3sig", "3sig_t0.6_lev7", 1_000_000_000, 7.0, 120, 0.6, 200000, []),
    ("multi_3sig", "3sig_t0.6_lev10", 1_000_000_000, 10.0, 120, 0.6, 200000, []),
    ("multi_3sig", "3sig_t0.8_lev7", 1_000_000_000, 7.0, 120, 0.8, 200000, []),
    ("multi_3sig", "3sig_t0.8_lev10", 1_000_000_000, 10.0, 120, 0.8, 200000, []),
    ("multi_3sig", "3sig_bb240_lev7", 1_000_000_000, 7.0, 240, 0.8, 200000, []),
    ("multi_3sig", "3sig_bb240_lev10", 1_000_000_000, 10.0, 240, 0.8, 200000, []),
    ("multi_3sig", "3sig_t0.8_lev5", 1_000_000_000, 5.0, 120, 0.8, 200000, []),
    ("multi_3sig", "3sig_t0.8_lev12", 1_000_000_000, 12.0, 120, 0.8, 200000, []),

    # 2-of-3 agreement (higher quality)
    ("multi_2agree", "2agree_t0.8_lev7", 1_000_000_000, 7.0, 120, 0.8, 200000, []),
    ("multi_2agree", "2agree_t0.8_lev10", 1_000_000_000, 10.0, 120, 0.8, 200000, []),
    ("multi_2agree", "2agree_bb240_lev7", 1_000_000_000, 7.0, 240, 0.8, 200000, []),

    # Regime-state (enter on regime changes too)
    ("regime_state", "rstate_t0.6_lev7", 1_000_000_000, 7.0, 120, 0.6, 200000, []),
    ("regime_state", "rstate_t0.6_lev10", 1_000_000_000, 10.0, 120, 0.6, 200000, []),
    ("regime_state", "rstate_t0.8_lev7", 1_000_000_000, 7.0, 120, 0.8, 200000, []),
    ("regime_state", "rstate_t0.8_lev10", 1_000_000_000, 10.0, 120, 0.8, 200000, []),
    ("regime_state", "rstate_bb240_lev7", 1_000_000_000, 7.0, 240, 0.8, 200000, []),

    # Higher concentration (use more capital per trade)
    ("multi_3sig", "3sig_t0.8_lev7_c20", 1_000_000_000, 7.0, 120, 0.8, 200000, ['--concentration', '0.20']),
    ("multi_3sig", "3sig_t0.8_lev10_c20", 1_000_000_000, 10.0, 120, 0.8, 200000, ['--concentration', '0.20']),
    ("multi_3sig", "3sig_t0.8_lev7_c30", 1_000_000_000, 7.0, 120, 0.8, 200000, ['--concentration', '0.30']),

    # Small capital ($50k)
    ("multi_3sig", "3sig_t0.8_lev10_50k", 1_000_000_000, 10.0, 120, 0.8, 50000, []),
    ("multi_3sig", "3sig_t0.8_lev15_50k", 1_000_000_000, 15.0, 120, 0.8, 50000, []),
]

results = []
for i, (strat_type, label, min_adv, lev, bb_lb, sq_mult, cap, extra) in enumerate(configs):
    template = STRATEGIES[strat_type]
    code = template.format(
        label=label, min_adv=min_adv, leverage=lev,
        bb_lookback=bb_lb, squeeze_mult=sq_mult,
    )

    with open(STRATEGY_FILE, 'w') as f:
        f.write(code)

    cmd = [sys.executable, str(PROJECT_ROOT / 'v4' / 'portfolio_backtest.py'),
           '--strategy', 's98', '--months', '12',
           '--capital', str(cap), '--market', 'perp'] + extra
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=str(PROJECT_ROOT))
        metrics = parse_result(proc.stdout + proc.stderr)
    except Exception as e:
        metrics = {'error': str(e)}

    metrics['label'] = label
    metrics['type'] = strat_type
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
print(f"ALL CONFIGS RANKED BY CALMAR:")
print(f"{'='*130}")
print(f"{'Label':30s} | {'Ann%':>7} {'Cal':>6} {'DD%':>7} {'Sha':>6} {'Sor':>6} {'Trd':>5} {'WR%':>5} {'PF':>5} {'Pay':>5} {'Hld':>4} {'Fees':>8}")
print("-" * 130)
for r in valid:
    print(f"{r['label']:30s} | {r.get('annual','?'):>7} {r.get('calmar','?'):>6} {r.get('max_dd','?'):>7} "
          f"{r.get('sharpe','?'):>6} {r.get('sortino','?'):>6} {r.get('trades','?'):>5} "
          f"{r.get('win_rate','?'):>5} {r.get('pf','?'):>5} {r.get('payoff','?'):>5} "
          f"{r.get('avg_hold','?'):>4} {r.get('fees','?'):>8}")

outfile = PROJECT_ROOT / 'results' / 'v4' / 'multi_signal_push.json'
with open(outfile, 'w') as f:
    json.dump(results, f, indent=2)
print(f"\nSaved to {outfile}")
