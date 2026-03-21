"""
Rapid Strategy Iterator
========================
Generates strategy variants, backtests each on 12mo, reports results.
Designed for fast iteration: 1 minute per strategy.
Uses subprocess to run portfolio_backtest.py.
"""

import subprocess
import json
import os
import sys
import time
from pathlib import Path

STRATEGIES_DIR = Path('/workspace/crypto_backtest/strategies')
RESULTS_DIR = Path('/workspace/crypto_backtest/results/v4')
PYTHON = '/workspace/venv/bin/python'
BACKTEST_CMD = 'v4/portfolio_backtest.py'


STRATEGY_TEMPLATE = '''"""
Strategy {name}: {description}
"""
import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_max, rolling_min, rolling_mean)

WARMUP = 200

def strategy(ctx: StrategyContext) -> StrategyResult:
    close = ctx.ind_1h['close']
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']
    n = len(close)
    regime = ctx.regime_1h
    rsi = ctx.ind_1h['rsi']
    macd = ctx.ind_1h['macd']
    macd_sig = ctx.ind_1h['macd_signal']
    atr = ctx.ind_1h['atr']
    adx = ctx.ind_1h['adx']
    ema_20 = ctx.ind_1h['ema_20']
    ema_50 = ctx.ind_1h['ema_50']
    bb_pct = ctx.ind_1h['bb_pct']
    vol_ratio = ctx.ind_1h['vol_ratio']
    vol_20 = ctx.ind_1h['vol_20']

    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50
    macd_prev = np.roll(macd, 1); macd_prev[0] = 0
    macd_sig_prev = np.roll(macd_sig, 1); macd_sig_prev[0] = 0

    funding = ctx.funding_1h
    if funding is None:
        funding = np.zeros(n)

{entry_logic}

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long & ~entry_short, 1,
                np.where(entry_short & ~entry_long, -1, 1)).astype(np.int8)
    entry_mask[:WARMUP] = False
    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,
        market_type=MarketType.PERP,
        leverage={leverage},
        stop_mult={stop_mult},
        trail_mult={trail_mult},
        target_mult={target_mult},
        no_stop_bars={no_stop_bars},
        min_hold={min_hold},
        max_hold={max_hold},
        edge={edge},
        exit_regimes={{{exit_regimes}}},
        breakeven_atr={breakeven_atr},
        max_trade_pct={max_trade_pct},
        exchange='binance',
        name='{name}',
    )
'''


def make_strategy(sid, description, entry_logic, **kwargs):
    """Write a strategy file and return the name."""
    defaults = {
        'leverage': 1.0,
        'stop_mult': 3.0,
        'trail_mult': 3.0,
        'target_mult': 999,
        'no_stop_bars': 24,
        'min_hold': 18,
        'max_hold': 720,
        'edge': 0.40,
        'exit_regimes': 'CRISIS',
        'breakeven_atr': 0.8,
        'max_trade_pct': 0.12,
    }
    defaults.update(kwargs)

    name = f's{sid}_auto'
    code = STRATEGY_TEMPLATE.format(
        name=name,
        description=description,
        entry_logic=entry_logic,
        **defaults,
    )

    path = STRATEGIES_DIR / f'{name}.py'
    with open(path, 'w') as f:
        f.write(code)
    return name, str(path)


def backtest(strategy_name, months=12, capital=200000):
    """Run backtest and return metrics dict."""
    sid = strategy_name.split('_')[0]  # e.g., 's204'
    cmd = [
        PYTHON, BACKTEST_CMD,
        '--strategy', sid,
        '--months', str(months),
        '--capital', str(capital),
        '--market', 'perp',
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                                cwd='/workspace/crypto_backtest')
        # Read metrics
        metrics_file = RESULTS_DIR / f'{sid}_12mo_200k_metrics.json'
        if metrics_file.exists():
            with open(metrics_file) as f:
                metrics = json.load(f)
            return metrics
    except subprocess.TimeoutExpired:
        return {'error': 'timeout'}
    except Exception as e:
        return {'error': str(e)}
    return {'error': 'no metrics file'}


def report(name, metrics):
    """Print one-line report."""
    if 'error' in metrics:
        print(f'  {name}: ERROR: {metrics["error"]}')
        return

    ann = float(metrics.get('annual_return_pct', 0))
    dd = float(metrics.get('max_dd_pct', 0))
    cal = float(metrics.get('calmar', 0))
    pf = float(metrics.get('profit_factor', 0))
    trades = int(metrics.get('total_trades', 0))
    final = float(metrics.get('final_equity', 200000))
    wr = float(metrics.get('win_rate', 0)) * 100

    print(f'  {name}: Ann={ann:+.1f}% DD={dd:.1f}% Cal={cal:.2f} '
          f'PF={pf:.2f} Trd={trades} WR={wr:.0f}% Final=${final:,.0f}')


# ==================== Strategy Variants ====================

def gen_strategies():
    """Generate all strategy variants to test."""
    variants = []

    # V1: s110 clone (baseline) with 2x leverage
    variants.append(('204', 'RSI-MACD proven signal + 2x leverage', '''
    # Proven s110 signal with leverage
    entry_long = ((rsi < 30) & (rsi > rsi_prev)
                  & (macd > macd_sig) & (macd_prev <= macd_sig_prev)
                  & (regime != CRISIS))
    entry_short = np.zeros(n, dtype=bool)
''', {'leverage': 2.0, 'exit_regimes': 'CRISIS, DOWNTREND'}))

    # V2: Relaxed RSI with multiple confirmation
    variants.append(('205', 'Relaxed RSI + EMA + volume confirmation', '''
    # Relaxed RSI with EMA and volume confirmation
    ema_bull = ema_20 > ema_50
    ema_bear = ema_20 < ema_50
    entry_long = ((rsi < 35) & (rsi > rsi_prev)
                  & ema_bull
                  & (vol_ratio > 1.2)
                  & (regime != CRISIS) & (regime != DOWNTREND))
    entry_short = ((rsi > 65) & (rsi < rsi_prev)
                   & ema_bear
                   & (vol_ratio > 1.2)
                   & (regime != CRISIS) & (regime != UPTREND))
''', {'leverage': 2.0}))

    # V3: Multi-signal union (s129 bidir + BB + EMA pullback)
    variants.append(('206', 'Multi-signal union: MACD + BB + EMA', '''
    # Signal 1: RSI-MACD cross (proven)
    sig1_long = ((rsi < 30) & (rsi > rsi_prev)
                 & (macd > macd_sig) & (macd_prev <= macd_sig_prev))
    sig1_short = ((rsi > 70) & (rsi < rsi_prev)
                  & (macd < macd_sig) & (macd_prev >= macd_sig_prev))

    # Signal 2: BB extreme + RSI
    sig2_long = (bb_pct < 0.05) & (rsi < 25) & (rsi > rsi_prev)
    sig2_short = (bb_pct > 0.95) & (rsi > 75) & (rsi < rsi_prev)

    # Signal 3: EMA pullback in trend
    sig3_long = ((close < ema_20) & (close > ema_50) & (rsi < 40) & (rsi > rsi_prev)
                 & (adx > 25) & (ema_20 > ema_50))
    sig3_short = ((close > ema_20) & (close < ema_50) & (rsi > 60) & (rsi < rsi_prev)
                  & (adx > 25) & (ema_20 < ema_50))

    # Union with regime filter
    entry_long = ((sig1_long | sig2_long | sig3_long) & (regime != CRISIS))
    entry_short = ((sig1_short | sig2_short | sig3_short) & (regime != CRISIS))
''', {'leverage': 2.0}))

    # V4: V3 with 3x leverage
    variants.append(('207', 'Multi-signal union 3x leverage', '''
    sig1_long = ((rsi < 30) & (rsi > rsi_prev)
                 & (macd > macd_sig) & (macd_prev <= macd_sig_prev))
    sig1_short = ((rsi > 70) & (rsi < rsi_prev)
                  & (macd < macd_sig) & (macd_prev >= macd_sig_prev))
    sig2_long = (bb_pct < 0.05) & (rsi < 25) & (rsi > rsi_prev)
    sig2_short = (bb_pct > 0.95) & (rsi > 75) & (rsi < rsi_prev)
    sig3_long = ((close < ema_20) & (close > ema_50) & (rsi < 40) & (rsi > rsi_prev)
                 & (adx > 25) & (ema_20 > ema_50))
    sig3_short = ((close > ema_20) & (close < ema_50) & (rsi > 60) & (rsi < rsi_prev)
                  & (adx > 25) & (ema_20 < ema_50))
    entry_long = ((sig1_long | sig2_long | sig3_long) & (regime != CRISIS))
    entry_short = ((sig1_short | sig2_short | sig3_short) & (regime != CRISIS))
''', {'leverage': 3.0}))

    # V5: Aggressive mean reversion with funding edge
    variants.append(('208', 'Mean reversion + funding dynamics', '''
    fund_ma8 = rolling_mean(funding, 8)
    fund_ma48 = rolling_mean(funding, 48)

    # Long: oversold + negative funding (shorts paying longs)
    entry_long = ((rsi < 30) & (rsi > rsi_prev)
                  & (fund_ma8 < 0)
                  & (regime != CRISIS))

    # Short: overbought + positive funding (longs paying shorts)
    entry_short = ((rsi > 70) & (rsi < rsi_prev)
                   & (fund_ma8 > 0.0001)
                   & (regime != CRISIS))
''', {'leverage': 2.0}))

    # V6: Volatility-regime adaptive with leverage
    variants.append(('209', 'Vol-adaptive: low vol = bigger bets', '''
    # Low volatility → bigger moves coming, take directional bets
    vol_percentile = np.zeros(n)
    for i in range(200, n):
        window = vol_20[100:i+1]
        vol_percentile[i] = (window < vol_20[i]).sum() / len(window)

    low_vol = vol_percentile < 0.25  # Bottom quartile

    # In low vol, use momentum to pick direction
    ret_24h = np.zeros(n)
    ret_24h[24:] = close[24:] / close[:-24] - 1

    entry_long = (low_vol & (ret_24h > 0) & (rsi > 50) & (rsi < 70)
                  & (macd > macd_sig) & (regime != CRISIS))
    entry_short = (low_vol & (ret_24h < 0) & (rsi < 50) & (rsi > 30)
                   & (macd < macd_sig) & (regime != CRISIS))
''', {'leverage': 2.0, 'stop_mult': 2.0, 'trail_mult': 1.5, 'max_hold': 168}))

    # V7: Trend-following with momentum confirmation
    variants.append(('210', 'Trend following + ADX + momentum', '''
    ret_168h = np.zeros(n)
    ret_168h[168:] = close[168:] / close[:-168] - 1

    # Strong trend + momentum alignment
    entry_long = ((adx > 30) & (ema_20 > ema_50)
                  & (ret_168h > 0.05)
                  & (rsi > 50) & (rsi < 70)
                  & (close > ema_20)
                  & (regime == UPTREND))
    entry_short = ((adx > 30) & (ema_20 < ema_50)
                   & (ret_168h < -0.05)
                   & (rsi < 50) & (rsi > 30)
                   & (close < ema_20)
                   & (regime == DOWNTREND))
''', {'leverage': 2.0, 'stop_mult': 2.5, 'trail_mult': 2.0}))

    # V8: s129 bidir with 2x leverage (known best total return)
    variants.append(('211', 's129 bidir clone + 2x leverage', '''
    entry_long = ((rsi < 30) & (rsi > rsi_prev)
                  & (macd > macd_sig) & (macd_prev <= macd_sig_prev)
                  & (regime != CRISIS))
    entry_short = ((rsi > 70) & (rsi < rsi_prev)
                   & (macd < macd_sig) & (macd_prev >= macd_sig_prev)
                   & (regime != CRISIS))
''', {'leverage': 2.0}))

    # V9: High frequency mean reversion (short hold)
    variants.append(('212', 'HF mean revert: 6h hold, BB extreme', '''
    # Very short-term mean reversion
    entry_long = ((bb_pct < 0.02) & (rsi < 20) & (rsi > rsi_prev)
                  & (regime != CRISIS))
    entry_short = ((bb_pct > 0.98) & (rsi > 80) & (rsi < rsi_prev)
                   & (regime != CRISIS))
''', {'leverage': 2.0, 'stop_mult': 2.0, 'trail_mult': 1.5,
      'no_stop_bars': 6, 'min_hold': 3, 'max_hold': 48,
      'target_mult': 3.0, 'breakeven_atr': 0.5}))

    # V10: BTC-correlated momentum (load BTC as market indicator)
    variants.append(('213', 'RSI extreme + vol spike reversal', '''
    # Volume spike = capitulation/euphoria
    vol_spike = vol_ratio > 3.0

    # Combine: extreme RSI + volume spike = high conviction reversal
    entry_long = ((rsi < 25) & (rsi > rsi_prev) & vol_spike
                  & (regime != CRISIS))
    entry_short = ((rsi > 75) & (rsi < rsi_prev) & vol_spike
                   & (regime != CRISIS))
''', {'leverage': 2.0, 'edge': 0.50}))

    # V11: Donchian breakout with trend filter
    variants.append(('214', 'Donchian breakout in trend', '''
    donch_h = ctx.ind_1h['donch_high']
    donch_l = ctx.ind_1h['donch_low']
    prev_close = np.roll(close, 1); prev_close[0] = close[0]

    # Breakout: close > 20-bar high, in uptrend
    entry_long = ((close >= donch_h) & (prev_close < donch_h)
                  & (adx > 25) & (ema_20 > ema_50)
                  & (regime != CRISIS))
    # Breakdown: close < 20-bar low, in downtrend
    entry_short = ((close <= donch_l) & (prev_close > donch_l)
                   & (adx > 25) & (ema_20 < ema_50)
                   & (regime != CRISIS))
''', {'leverage': 2.0, 'stop_mult': 2.5, 'trail_mult': 2.0}))

    # V12: Aggressive multi-signal with 3x, tight stops
    variants.append(('215', 'Aggressive multi-signal 3x tight stops', '''
    sig1_long = (rsi < 30) & (rsi > rsi_prev) & (macd > macd_sig) & (macd_prev <= macd_sig_prev)
    sig1_short = (rsi > 70) & (rsi < rsi_prev) & (macd < macd_sig) & (macd_prev >= macd_sig_prev)
    sig2_long = (bb_pct < 0.05) & (rsi < 25) & (rsi > rsi_prev)
    sig2_short = (bb_pct > 0.95) & (rsi > 75) & (rsi < rsi_prev)
    entry_long = ((sig1_long | sig2_long) & (regime != CRISIS))
    entry_short = ((sig1_short | sig2_short) & (regime != CRISIS))
''', {'leverage': 3.0, 'stop_mult': 2.0, 'trail_mult': 1.5,
      'breakeven_atr': 0.5, 'max_hold': 336}))

    return variants


def main():
    print('=' * 70)
    print('RAPID STRATEGY ITERATION')
    print('=' * 70)

    variants = gen_strategies()
    print(f'\nTesting {len(variants)} variants...\n')

    results = []
    for sid, desc, logic, params in variants:
        name, path = make_strategy(sid, desc, logic, **params)
        print(f'[{sid}] {desc}')
        t0 = time.time()
        metrics = backtest(name)
        elapsed = time.time() - t0
        report(name, metrics)
        results.append((name, metrics, elapsed))

        # Clean up strategy file
        # os.remove(path)  # Keep for inspection

    # Summary
    print(f'\n{"="*70}')
    print('SUMMARY — Sorted by Annual Return')
    print(f'{"="*70}')

    valid = [(n, m, t) for n, m, t in results if 'error' not in m]
    valid.sort(key=lambda x: float(x[1].get('annual_return_pct', -999)), reverse=True)

    for name, m, t in valid:
        ann = float(m.get('annual_return_pct', 0))
        dd = float(m.get('max_dd_pct', 0))
        cal = float(m.get('calmar', 0))
        pf = float(m.get('profit_factor', 0))
        trades = int(m.get('total_trades', 0))
        final = float(m.get('final_equity', 200000))
        print(f'  {name}: Ann={ann:+.1f}% DD={dd:.1f}% Cal={cal:.2f} '
              f'PF={pf:.2f} Trd={trades} Final=${final:,.0f} ({t:.0f}s)')

    # Save results
    out = {n: m for n, m, _ in results}
    with open(RESULTS_DIR / 'rapid_iterate_results.json', 'w') as f:
        json.dump(out, f, indent=2)


if __name__ == '__main__':
    main()
