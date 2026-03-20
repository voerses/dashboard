"""
Signal sweep: test multiple signal types with standardized trade management.
Writes s98 strategy file with each signal, runs V4 backtest, collects results.
"""
import subprocess, json, re, os, sys, time

STRATEGY_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "strategies")
STRATEGY_FILE = os.path.join(STRATEGY_DIR, "s98_sr_breakout_swing.py")

TEMPLATE = '''"""s98 sweep variant: {name}"""
import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_max, rolling_min, rolling_mean)

WARMUP = {warmup}
LEVERAGE = {leverage}

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
        entry_mask=entry_mask,
        direction=direction,
        market_type=MarketType.PERP,
        leverage=LEVERAGE,
        stop_mult={stop},
        trail_mult={trail},
        target_mult=999.0,
        no_stop_bars={grace},
        min_hold={min_hold},
        max_hold={max_hold},
        edge=0.40,
        exit_regimes={{CRISIS}},
        breakeven_atr={breakeven},
        exchange='binance',
        name='s98_sr_breakout_swing',
    )
'''

SIGNALS = {
    # Signal 1: EMA crossover (always in market)
    "ema_cross": '''
    ema10 = ctx.ind_1h['ema_10']
    ema20 = ctx.ind_1h['ema_20']
    ema10_prev = np.roll(ema10, 1); ema10_prev[0] = np.nan
    ema20_prev = np.roll(ema20, 1); ema20_prev[0] = np.nan
    entry_long = (ema10 > ema20) & (ema10_prev <= ema20_prev)
    entry_short = (ema10 < ema20) & (ema10_prev >= ema20_prev)
''',
    # Signal 2: EMA state (not crossover) - enter whenever aligned
    "ema_state": '''
    ema10 = ctx.ind_1h['ema_10']
    ema20 = ctx.ind_1h['ema_20']
    ema50 = ctx.ind_1h['ema_50']
    adx = ctx.ind_1h['adx']
    entry_long = (ema10 > ema20) & (ema20 > ema50) & (adx > 20) & (regime != CRISIS)
    entry_short = (ema10 < ema20) & (ema20 < ema50) & (adx > 20) & (regime != CRISIS)
''',
    # Signal 3: RSI extremes (mean reversion)
    "rsi_extreme": '''
    rsi = ctx.ind_1h['rsi']
    ema50 = ctx.ind_1h['ema_50']
    # Buy oversold in uptrend, sell overbought in downtrend
    entry_long = (rsi < 30) & (close > ema50) & (regime != CRISIS)
    entry_short = (rsi > 70) & (close < ema50) & (regime != CRISIS)
''',
    # Signal 4: Donchian breakout (precomputed)
    "donchian": '''
    dh = ctx.ind_1h['donch_high']
    dl = ctx.ind_1h['donch_low']
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    dh_prev = np.roll(dh, 1); dh_prev[0] = np.nan
    dl_prev = np.roll(dl, 1); dl_prev[0] = np.nan
    close_prev = np.roll(close, 1); close_prev[0] = np.nan
    entry_long = (close > dh_prev) & (close_prev <= dh_prev) & (adx > 20) & (plus_di > minus_di) & (regime != CRISIS)
    entry_short = (close < dl_prev) & (close_prev >= dl_prev) & (adx > 20) & (minus_di > plus_di) & (regime != CRISIS)
''',
    # Signal 5: BB breakout
    "bb_breakout": '''
    bb_upper = ctx.ind_1h['bb_upper']
    bb_lower = ctx.ind_1h['bb_lower']
    adx = ctx.ind_1h['adx']
    vol_ratio = ctx.ind_1h['vol_ratio']
    bb_prev_u = np.roll(bb_upper, 1); bb_prev_u[0] = np.nan
    bb_prev_l = np.roll(bb_lower, 1); bb_prev_l[0] = np.nan
    close_prev = np.roll(close, 1); close_prev[0] = np.nan
    entry_long = (close > bb_prev_u) & (close_prev <= bb_prev_u) & (adx > 20) & (vol_ratio > 1.0) & (regime != CRISIS)
    entry_short = (close < bb_prev_l) & (close_prev >= bb_prev_l) & (adx > 20) & (vol_ratio > 1.0) & (regime != CRISIS)
''',
    # Signal 6: MACD histogram acceleration (fixed)
    "macd_accel": '''
    macd = ctx.ind_1h['macd']
    macd_sig = ctx.ind_1h['macd_signal']
    hist = ctx.ind_1h['macd_hist']
    adx = ctx.ind_1h['adx']
    ema20 = ctx.ind_1h['ema_20']
    hp = np.roll(hist, 1); hp[0] = np.nan
    hp2 = np.roll(hist, 2); hp2[:2] = np.nan
    entry_long = (macd > macd_sig) & (hist > hp) & (hp > hp2) & (hist > 0) & (adx > 20) & (close > ema20) & (regime != CRISIS)
    entry_short = (macd < macd_sig) & (hist < hp) & (hp < hp2) & (hist < 0) & (adx > 20) & (close < ema20) & (regime != CRISIS)
''',
    # Signal 7: Taker ratio extremes
    "taker": '''
    taker = ctx.ind_1h['taker']
    adx = ctx.ind_1h['adx']
    ema20 = ctx.ind_1h['ema_20']
    taker_avg = rolling_mean(taker, 24)
    entry_long = (taker > taker_avg * 1.1) & (close > ema20) & (adx > 20) & (regime != CRISIS)
    entry_short = (taker < taker_avg * 0.9) & (close < ema20) & (adx > 20) & (regime != CRISIS)
''',
    # Signal 8: ADX trend strength + DI
    "adx_di": '''
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    vol_ratio = ctx.ind_1h['vol_ratio']
    ema20 = ctx.ind_1h['ema_20']
    adx_prev = np.roll(adx, 1); adx_prev[0] = np.nan
    # Strong trend with rising ADX
    entry_long = (adx > 25) & (adx > adx_prev) & (plus_di > minus_di) & (close > ema20) & (vol_ratio > 1.0) & (regime != CRISIS)
    entry_short = (adx > 25) & (adx > adx_prev) & (minus_di > plus_di) & (close < ema20) & (vol_ratio > 1.0) & (regime != CRISIS)
''',
}

# Parameter combos to test
CONFIGS = [
    # (leverage, stop, trail, grace, min_hold, max_hold, breakeven, warmup)
    (2.0, 3.0, 1.5, 24, 12, 504, 1.5, 200),   # Conservative proven
    (3.0, 3.0, 1.5, 24, 12, 504, 1.5, 200),   # Moderate
    (3.0, 4.5, 1.5, 48, 24, 336, 2.0, 200),   # Wide stop, long grace
    (2.0, 4.5, 1.5, 48, 24, 336, 2.0, 200),   # Conservative + wide stop
]


def parse_result(output):
    """Extract key metrics from backtest output."""
    metrics = {}
    for line in output.split('\n'):
        if 'Total Return:' in line:
            m = re.search(r'([+-]?\d+\.?\d*)%', line)
            if m: metrics['return'] = float(m.group(1))
        elif 'Annualized:' in line:
            m = re.search(r'([+-]?\d+\.?\d*)%', line)
            if m: metrics['annual'] = float(m.group(1))
        elif 'Calmar:' in line:
            m = re.search(r'([+-]?\d+\.?\d*)', line.split('Calmar:')[1])
            if m: metrics['calmar'] = float(m.group(1))
        elif 'Sharpe:' in line and 'Sortino' not in line:
            m = re.search(r'([+-]?\d+\.?\d*)', line.split('Sharpe:')[1])
            if m: metrics['sharpe'] = float(m.group(1))
        elif 'Sortino:' in line:
            m = re.search(r'([+-]?\d+\.?\d*)', line.split('Sortino:')[1])
            if m: metrics['sortino'] = float(m.group(1))
        elif 'Max Drawdown:' in line and 'Duration' not in line:
            m = re.search(r'([+-]?\d+\.?\d*)%', line)
            if m: metrics['max_dd'] = float(m.group(1))
        elif 'Total Trades:' in line:
            m = re.search(r'(\d+)', line.split('Total Trades:')[1])
            if m: metrics['trades'] = int(m.group(1))
        elif 'Win Rate:' in line:
            m = re.search(r'(\d+\.?\d*)%', line)
            if m: metrics['win_rate'] = float(m.group(1))
        elif 'Profit Factor:' in line:
            m = re.search(r'(\d+\.?\d*)', line.split('Profit Factor:')[1])
            if m: metrics['pf'] = float(m.group(1))
    return metrics


def run_sweep():
    results = []
    total = len(SIGNALS) * len(CONFIGS)
    i = 0

    for sig_name, sig_code in SIGNALS.items():
        for ci, (lev, stop, trail, grace, min_h, max_h, be, warmup) in enumerate(CONFIGS):
            i += 1
            config_label = f"L{lev}_S{stop}_T{trail}_G{grace}"

            # Write strategy file
            code = TEMPLATE.format(
                name=f"{sig_name}_{config_label}",
                signal_code=sig_code,
                leverage=lev, stop=stop, trail=trail, grace=grace,
                min_hold=min_h, max_hold=max_h, breakeven=be, warmup=warmup,
            )
            with open(STRATEGY_FILE, 'w') as f:
                f.write(code)

            # Run backtest
            cmd = [
                sys.executable, 'v4/portfolio_backtest.py',
                '--strategy', 's98', '--months', '12',
                '--capital', '200000', '--market', 'perp',
            ]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                output = proc.stdout + proc.stderr
                metrics = parse_result(output)
            except Exception as e:
                metrics = {'error': str(e)}

            metrics['signal'] = sig_name
            metrics['config'] = config_label
            results.append(metrics)

            ann = metrics.get('annual', 'ERR')
            cal = metrics.get('calmar', 'ERR')
            dd = metrics.get('max_dd', 'ERR')
            trd = metrics.get('trades', 0)
            pf = metrics.get('pf', 'ERR')
            print(f"[{i}/{total}] {sig_name:15s} {config_label:25s} | Ann={ann:>8}% Cal={cal:>6} DD={dd:>7}% Trd={trd:>5} PF={pf}")

    # Sort by annual return descending
    valid = [r for r in results if 'annual' in r]
    valid.sort(key=lambda x: x['annual'], reverse=True)

    print("\n" + "=" * 90)
    print("TOP 10 BY ANNUAL RETURN:")
    print("=" * 90)
    print(f"{'Signal':15s} {'Config':25s} {'Annual%':>8} {'Calmar':>7} {'MaxDD%':>7} {'Sharpe':>7} {'Sortino':>8} {'Trades':>6} {'WinR%':>6} {'PF':>5}")
    print("-" * 90)
    for r in valid[:10]:
        print(f"{r['signal']:15s} {r['config']:25s} {r.get('annual','?'):>8} {r.get('calmar','?'):>7} {r.get('max_dd','?'):>7} {r.get('sharpe','?'):>7} {r.get('sortino','?'):>8} {r.get('trades','?'):>6} {r.get('win_rate','?'):>6} {r.get('pf','?'):>5}")

    # Save full results
    with open('results/v4/signal_sweep.json', 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nFull results saved to results/v4/signal_sweep.json")


if __name__ == '__main__':
    run_sweep()
