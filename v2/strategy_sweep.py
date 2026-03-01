"""
Mass Strategy Sweep — Automated testing of MANY strategy variations.
====================================================================

Tests hundreds of strategy parameter combinations and signal combinations
to identify the highest-return configurations.

Sweeps:
  1. Entry signal variations (RSI thresholds, EMA combos, volume filters, etc.)
  2. Exit parameter variations (stop mult, trail mult, protection window, etc.)
  3. Indicator combinations (which 2-3 indicators together predict best)
  4. Timeframe emphasis (daily-driven vs 4H-driven vs 1H-driven signals)
  5. Token subset optimization (which groups of tokens work best)

Uses the plugin engine (engine.py) for all backtests.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
import time
import json
from datetime import datetime
from itertools import product
from concurrent.futures import ProcessPoolExecutor

from engine import (
    Engine, StrategyContext, StrategyResult,
    CPCV_ROBUST_TOKENS, strategy_dual_momentum,
    CRISIS, QUIET, UPTREND, RANGE, DOWNTREND,
)
from liquid_universe import LIQUID_TOKENS, get_tier


# =============================================================================
# Signal Generator Library — atomic building blocks
# =============================================================================

def signal_rsi_oversold(ctx, threshold=30, timeframe='1h'):
    """RSI below threshold."""
    if timeframe == '1h':
        return ctx.ind_1h['rsi'] < threshold
    elif timeframe == '4h':
        rsi_4h = ctx.align_4h_to_1h(ctx.ind_4h['rsi'])
        return np.nan_to_num(rsi_4h, 50) < threshold
    elif timeframe == 'daily':
        rsi_d = ctx.align_daily_to_1h(ctx.ind_d['rsi'])
        return np.nan_to_num(rsi_d, 50) < threshold

def signal_rsi_overbought(ctx, threshold=70, timeframe='1h'):
    """RSI above threshold (for shorts or exit)."""
    if timeframe == '1h':
        return ctx.ind_1h['rsi'] > threshold
    elif timeframe == '4h':
        rsi_4h = ctx.align_4h_to_1h(ctx.ind_4h['rsi'])
        return np.nan_to_num(rsi_4h, 50) > threshold

def signal_ema_bullish(ctx, fast=20, slow=50, timeframe='daily'):
    """Fast EMA above slow EMA."""
    if timeframe == 'daily':
        ema_f = ctx.align_daily_to_1h(ctx.ind_d[f'ema_{fast}'])
        ema_s = ctx.align_daily_to_1h(ctx.ind_d[f'ema_{slow}'])
    elif timeframe == '4h':
        ema_f = ctx.align_4h_to_1h(ctx.ind_4h[f'ema_{fast}'])
        ema_s = ctx.align_4h_to_1h(ctx.ind_4h[f'ema_{50}'])
    else:
        ema_f = ctx.ind_1h[f'ema_{fast}']
        ema_s = ctx.ind_1h[f'ema_{slow}']
    return np.nan_to_num(ema_f, 0) > np.nan_to_num(ema_s, 0)

def signal_adx_strong(ctx, threshold=20, timeframe='daily'):
    """ADX above threshold (trending market)."""
    if timeframe == 'daily':
        adx = ctx.align_daily_to_1h(ctx.ind_d['adx'])
    elif timeframe == '4h':
        adx = ctx.align_4h_to_1h(ctx.ind_4h['adx'])
    else:
        adx = ctx.ind_1h['adx']
    return np.nan_to_num(adx, 0) > threshold

def signal_volume_spike(ctx, threshold=1.5, timeframe='1h'):
    """Volume above N× average."""
    if timeframe == '1h':
        return ctx.ind_1h['vol_ratio'] > threshold
    elif timeframe == '4h':
        vr = ctx.align_4h_to_1h(ctx.ind_4h['vol_ratio'])
        return np.nan_to_num(vr, 1) > threshold

def signal_taker_bullish(ctx, threshold=0.52):
    """Taker buy ratio above threshold (buyer aggression)."""
    return ctx.ind_1h['taker'] > threshold

def signal_taker_bearish(ctx, threshold=0.48):
    """Taker buy ratio below threshold (seller aggression)."""
    return ctx.ind_1h['taker'] < threshold

def signal_bb_oversold(ctx, threshold=0.15, timeframe='4h'):
    """BB% below threshold (near lower band)."""
    if timeframe == '4h':
        bb = ctx.align_4h_to_1h(ctx.ind_4h['bb_pct'])
    else:
        bb = ctx.ind_1h['bb_pct']
    return np.nan_to_num(bb, 0.5) < threshold

def signal_bb_overbought(ctx, threshold=0.85, timeframe='4h'):
    """BB% above threshold (near upper band)."""
    if timeframe == '4h':
        bb = ctx.align_4h_to_1h(ctx.ind_4h['bb_pct'])
    else:
        bb = ctx.ind_1h['bb_pct']
    return np.nan_to_num(bb, 0.5) > threshold

def signal_macd_bullish(ctx, timeframe='4h'):
    """MACD histogram turning positive."""
    if timeframe == '4h':
        h = ctx.align_4h_to_1h(ctx.ind_4h['macd_hist'])
    else:
        h = ctx.ind_1h['macd_hist']
    h = np.nan_to_num(h, 0)
    return (h > 0) & (h > np.roll(h, 1))

def signal_pullback_to_ema(ctx, ema_key='ema_20', pct_range=(-0.04, 0.01), timeframe='4h'):
    """Price near EMA (pullback zone)."""
    if timeframe == '4h':
        ema = ctx.align_4h_to_1h(ctx.ind_4h[ema_key])
        close = ctx.align_4h_to_1h(ctx.ind_4h['close'])
    else:
        ema = ctx.ind_1h[ema_key]
        close = ctx.ind_1h['close']
    pct = (np.nan_to_num(close, 1) - np.nan_to_num(ema, 1)) / np.maximum(np.nan_to_num(ema, 1), 1e-10)
    return (pct >= pct_range[0]) & (pct <= pct_range[1])

def signal_regime_is(ctx, regimes):
    """Regime matches one of the given values."""
    mask = np.zeros(len(ctx.regime_1h), dtype=bool)
    for r in regimes:
        mask |= (ctx.regime_1h == r)
    return mask

def signal_momentum_positive(ctx, period=12):
    """N-day cumulative return positive."""
    key = f'ret_{period}d'
    if key in ctx.custom:
        return np.nan_to_num(ctx.custom[key], 0) > 0
    # Fallback: compute from daily
    ret_d = ctx.ind_d['ret_1']
    from mtf_strategy_v2 import _rolling_mean
    mom = pd.Series(ret_d).rolling(period).sum().values
    mom_1h = ctx.align_daily_to_1h(mom)
    return np.nan_to_num(mom_1h, 0) > 0

def signal_above_daily_ema(ctx, ema_key='ema_50'):
    """Price above daily EMA."""
    ema_d = ctx.align_daily_to_1h(ctx.ind_d[ema_key])
    close = ctx.ind_1h['close']
    return close > np.nan_to_num(ema_d, 0)

def signal_donchian_breakout(ctx, timeframe='1h'):
    """Price at or above 20-period high (Donchian breakout)."""
    dh = ctx.ind_1h['donch_high']
    return ctx.ind_1h['close'] >= np.nan_to_num(dh, 999999)

def signal_vpin_informed(ctx, low=0.25, high=0.45):
    """VPIN in range indicating informed trading."""
    vpin = ctx.custom.get('enr_vpin')
    if vpin is None:
        return np.ones(len(ctx.ind_1h['close']), dtype=bool)  # no filter
    return (~np.isnan(vpin)) & (vpin >= low) & (vpin <= high)

def signal_vpin_noise(ctx, threshold=0.30):
    """Low VPIN = noise-driven price (good for mean reversion)."""
    vpin = ctx.custom.get('enr_vpin')
    if vpin is None:
        return np.ones(len(ctx.ind_1h['close']), dtype=bool)
    return (~np.isnan(vpin)) & (vpin < threshold)

def signal_high_realized_vol(ctx, mult=1.5):
    """Realized vol above median * mult (elevated but not extreme)."""
    rv = ctx.custom.get('enr_realized_vol')
    if rv is None:
        return np.ones(len(ctx.ind_1h['close']), dtype=bool)
    valid = rv[~np.isnan(rv)]
    if len(valid) < 20:
        return np.ones(len(ctx.ind_1h['close']), dtype=bool)
    med = np.median(valid[valid > 0])
    return (~np.isnan(rv)) & (rv > med * mult) & (rv < med * 4.0)


# =============================================================================
# Strategy Templates — combine signals into full strategies
# =============================================================================

def make_trend_strategy(entry_signals, exit_regimes={CRISIS, DOWNTREND},
                        stop_mult=5.0, trail_mult=4.0, no_stop_bars=12,
                        edge=0.40, min_hold=18, max_hold=720, name='trend'):
    """Factory: create a trend-following strategy from signal list."""
    def strategy(ctx):
        n = len(ctx.ind_1h['close'])
        entry = np.ones(n, dtype=bool)
        for sig_fn in entry_signals:
            entry = entry & sig_fn(ctx)
        entry[:200] = False
        return StrategyResult(
            entry_mask=entry,
            direction=np.ones(n, dtype=np.int8),
            stop_mult=stop_mult, trail_mult=trail_mult, target_mult=999,
            no_stop_bars=no_stop_bars, min_hold=min_hold, max_hold=max_hold,
            edge=edge, exit_regimes=exit_regimes,
            name=name,
        )
    strategy.__name__ = name
    return strategy


def make_mean_reversion_strategy(entry_signals, exit_regimes={CRISIS, DOWNTREND},
                                  stop_mult=2.0, trail_mult=2.0, target_mult=5.0,
                                  edge=0.50, min_hold=18, max_hold=240, name='mean_rev'):
    """Factory: create a mean-reversion strategy from signal list."""
    def strategy(ctx):
        n = len(ctx.ind_1h['close'])
        entry = np.ones(n, dtype=bool)
        for sig_fn in entry_signals:
            entry = entry & sig_fn(ctx)
        entry[:200] = False
        ema20_4h = ctx.align_4h_to_1h(ctx.ind_4h['ema_20'])
        return StrategyResult(
            entry_mask=entry,
            direction=np.ones(n, dtype=np.int8),
            stop_mult=stop_mult, trail_mult=trail_mult, target_mult=target_mult,
            no_stop_bars=0, min_hold=min_hold, max_hold=max_hold,
            edge=edge, exit_regimes=exit_regimes,
            convex_exit=True, mean_target_vals=ema20_4h,
            name=name,
        )
    strategy.__name__ = name
    return strategy


# =============================================================================
# Sweep Configurations
# =============================================================================

def generate_trend_sweep():
    """Generate all trend strategy variations to test."""
    strategies = []

    # --- Signal variations ---
    # Base: our proven dual momentum signals
    base_signals = [
        lambda ctx: signal_regime_is(ctx, [UPTREND]),
        lambda ctx: signal_above_daily_ema(ctx, 'ema_50'),
        lambda ctx: signal_adx_strong(ctx, 20, 'daily'),
        lambda ctx: signal_momentum_positive(ctx, 12),
    ]

    # Variation 1: Different pullback zones
    for pb_low, pb_high in [(-0.04, 0.01), (-0.03, 0.02), (-0.05, 0.005), (-0.06, 0.01)]:
        signals = base_signals + [
            lambda ctx, l=pb_low, h=pb_high: signal_pullback_to_ema(ctx, 'ema_20', (l, h), '4h'),
            lambda ctx: signal_volume_spike(ctx, 1.2, '1h'),
        ]
        strategies.append(make_trend_strategy(signals,
            name=f'trend_pb_{pb_low}_{pb_high}'))

    # Variation 2: Different volume thresholds
    for vol_thresh in [1.0, 1.2, 1.5, 2.0]:
        signals = base_signals + [
            lambda ctx: signal_pullback_to_ema(ctx, 'ema_20', (-0.04, 0.01), '4h'),
            lambda ctx, v=vol_thresh: signal_volume_spike(ctx, v, '1h'),
        ]
        strategies.append(make_trend_strategy(signals,
            name=f'trend_vol_{vol_thresh}'))

    # Variation 3: Different ADX thresholds
    for adx_thresh in [15, 20, 25, 30]:
        signals = base_signals[:1] + [  # regime
            lambda ctx: signal_above_daily_ema(ctx, 'ema_50'),
            lambda ctx, a=adx_thresh: signal_adx_strong(ctx, a, 'daily'),
            lambda ctx: signal_momentum_positive(ctx, 12),
            lambda ctx: signal_pullback_to_ema(ctx, 'ema_20', (-0.04, 0.01), '4h'),
            lambda ctx: signal_volume_spike(ctx, 1.2, '1h'),
        ]
        strategies.append(make_trend_strategy(signals,
            name=f'trend_adx_{adx_thresh}'))

    # Variation 4: With taker buy confirmation
    for taker_thresh in [0.50, 0.52, 0.54, 0.56]:
        signals = base_signals + [
            lambda ctx: signal_pullback_to_ema(ctx, 'ema_20', (-0.04, 0.01), '4h'),
            lambda ctx: signal_volume_spike(ctx, 1.2, '1h'),
            lambda ctx, t=taker_thresh: signal_taker_bullish(ctx, t),
        ]
        strategies.append(make_trend_strategy(signals,
            name=f'trend_taker_{taker_thresh}'))

    # Variation 5: With VPIN filter
    signals_vpin = base_signals + [
        lambda ctx: signal_pullback_to_ema(ctx, 'ema_20', (-0.04, 0.01), '4h'),
        lambda ctx: signal_volume_spike(ctx, 1.2, '1h'),
        lambda ctx: signal_vpin_informed(ctx, 0.25, 0.45),
    ]
    strategies.append(make_trend_strategy(signals_vpin, name='trend_vpin'))

    # Variation 6: With realized vol filter
    signals_rv = base_signals + [
        lambda ctx: signal_pullback_to_ema(ctx, 'ema_20', (-0.04, 0.01), '4h'),
        lambda ctx: signal_volume_spike(ctx, 1.2, '1h'),
        lambda ctx: signal_high_realized_vol(ctx, 1.5),
    ]
    strategies.append(make_trend_strategy(signals_rv, name='trend_rv_filter'))

    # Variation 7: Simplified trend (fewer filters)
    simple_signals = [
        lambda ctx: signal_regime_is(ctx, [UPTREND]),
        lambda ctx: signal_above_daily_ema(ctx, 'ema_50'),
        lambda ctx: signal_volume_spike(ctx, 1.5, '1h'),
    ]
    strategies.append(make_trend_strategy(simple_signals, name='trend_simple'))

    # Variation 8: Donchian breakout trend
    donchian_signals = [
        lambda ctx: signal_regime_is(ctx, [UPTREND]),
        lambda ctx: signal_donchian_breakout(ctx),
        lambda ctx: signal_volume_spike(ctx, 1.5, '1h'),
        lambda ctx: signal_adx_strong(ctx, 20, 'daily'),
    ]
    strategies.append(make_trend_strategy(donchian_signals, name='trend_donchian'))

    # --- Exit parameter variations on best signal set ---
    best_signals = base_signals + [
        lambda ctx: signal_pullback_to_ema(ctx, 'ema_20', (-0.04, 0.01), '4h'),
        lambda ctx: signal_volume_spike(ctx, 1.2, '1h'),
    ]

    for stop_m in [3.0, 4.0, 5.0, 6.0, 7.0]:
        strategies.append(make_trend_strategy(best_signals,
            stop_mult=stop_m, trail_mult=stop_m-1, name=f'trend_stop_{stop_m}'))

    for no_stop in [0, 6, 12, 18, 24]:
        strategies.append(make_trend_strategy(best_signals,
            no_stop_bars=no_stop, name=f'trend_prot_{no_stop}'))

    for edge_val in [0.30, 0.35, 0.40, 0.45, 0.50]:
        strategies.append(make_trend_strategy(best_signals,
            edge=edge_val, name=f'trend_edge_{edge_val}'))

    return strategies


def generate_mr_sweep():
    """Generate mean-reversion strategy variations."""
    strategies = []

    # Variation 1: RSI thresholds
    for rsi_thresh in [25, 30, 35, 40]:
        signals = [
            lambda ctx: signal_regime_is(ctx, [RANGE, QUIET]),
            lambda ctx, r=rsi_thresh: signal_rsi_oversold(ctx, r, '4h'),
            lambda ctx, r=rsi_thresh: signal_rsi_oversold(ctx, r, '1h'),
            lambda ctx: signal_macd_bullish(ctx, '4h'),
            lambda ctx: signal_taker_bullish(ctx, 0.52),
        ]
        strategies.append(make_mean_reversion_strategy(signals,
            name=f'mr_rsi_{rsi_thresh}'))

    # Variation 2: BB threshold variations
    for bb_thresh in [0.10, 0.15, 0.20, 0.25, 0.30]:
        signals = [
            lambda ctx: signal_regime_is(ctx, [RANGE, QUIET]),
            lambda ctx: signal_rsi_oversold(ctx, 35, '4h'),
            lambda ctx, b=bb_thresh: signal_bb_oversold(ctx, b, '4h'),
            lambda ctx: signal_macd_bullish(ctx, '4h'),
        ]
        strategies.append(make_mean_reversion_strategy(signals,
            name=f'mr_bb_{bb_thresh}'))

    # Variation 3: With volume confirmation
    for vol_thresh in [1.0, 1.5, 2.0]:
        signals = [
            lambda ctx: signal_regime_is(ctx, [RANGE, QUIET]),
            lambda ctx: signal_rsi_oversold(ctx, 35, '4h'),
            lambda ctx: signal_bb_oversold(ctx, 0.15, '4h'),
            lambda ctx, v=vol_thresh: signal_volume_spike(ctx, v, '1h'),
        ]
        strategies.append(make_mean_reversion_strategy(signals,
            name=f'mr_vol_{vol_thresh}'))

    # Variation 4: VPIN-filtered (low = noise dislocation)
    signals_vpin = [
        lambda ctx: signal_regime_is(ctx, [RANGE, QUIET]),
        lambda ctx: signal_rsi_oversold(ctx, 35, '4h'),
        lambda ctx: signal_bb_oversold(ctx, 0.15, '4h'),
        lambda ctx: signal_vpin_noise(ctx, 0.30),
    ]
    strategies.append(make_mean_reversion_strategy(signals_vpin, name='mr_vpin_noise'))

    # Variation 5: Regime variations
    for regime_set in [[RANGE], [QUIET], [RANGE, QUIET], [RANGE, QUIET, DOWNTREND]]:
        regime_name = '_'.join(str(r) for r in regime_set)
        signals = [
            lambda ctx, rs=regime_set: signal_regime_is(ctx, rs),
            lambda ctx: signal_rsi_oversold(ctx, 35, '4h'),
            lambda ctx: signal_bb_oversold(ctx, 0.15, '4h'),
            lambda ctx: signal_macd_bullish(ctx, '4h'),
            lambda ctx: signal_taker_bullish(ctx, 0.52),
        ]
        strategies.append(make_mean_reversion_strategy(signals,
            name=f'mr_regime_{regime_name}'))

    # Variation 6: Stop mult variations
    for stop_m in [1.5, 2.0, 2.5, 3.0, 4.0]:
        signals = [
            lambda ctx: signal_regime_is(ctx, [RANGE, QUIET]),
            lambda ctx: signal_rsi_oversold(ctx, 35, '4h'),
            lambda ctx: signal_bb_oversold(ctx, 0.15, '4h'),
            lambda ctx: signal_macd_bullish(ctx, '4h'),
            lambda ctx: signal_taker_bullish(ctx, 0.52),
        ]
        strategies.append(make_mean_reversion_strategy(signals,
            stop_mult=stop_m, trail_mult=stop_m, name=f'mr_stop_{stop_m}'))

    return strategies


def generate_hybrid_sweep():
    """Generate hybrid strategies (not purely trend or MR)."""
    strategies = []

    # Contrarian: buy panic selling
    for rsi_t in [25, 30, 35]:
        for vol_t in [2.0, 2.5, 3.0]:
            signals = [
                lambda ctx, r=rsi_t: signal_rsi_oversold(ctx, r, '1h'),
                lambda ctx, v=vol_t: signal_volume_spike(ctx, v, '1h'),
                lambda ctx: signal_taker_bullish(ctx, 0.48),  # slight buyer recovery
            ]
            strategies.append(make_trend_strategy(signals,
                stop_mult=3.0, trail_mult=2.5, no_stop_bars=6,
                edge=0.45, min_hold=6, max_hold=240,
                exit_regimes={CRISIS},
                name=f'contrarian_rsi{rsi_t}_vol{vol_t}'))

    # Momentum burst: strong move + follow-through
    for ret_thresh in [0.02, 0.03, 0.05]:
        signals = [
            lambda ctx: signal_regime_is(ctx, [UPTREND, RANGE]),
            lambda ctx, r=ret_thresh: ctx.custom.get('ret_24h', np.zeros(len(ctx.ind_1h['close']))) > r,
            lambda ctx: signal_volume_spike(ctx, 2.0, '1h'),
            lambda ctx: signal_adx_strong(ctx, 20, 'daily'),
        ]
        strategies.append(make_trend_strategy(signals,
            stop_mult=4.0, trail_mult=3.0, no_stop_bars=12,
            name=f'mom_burst_{ret_thresh}'))

    return strategies


# =============================================================================
# Sweep Runner
# =============================================================================

def run_sweep(tokens=None, top_n=20, verbose=True):
    """Run the full strategy sweep and rank results."""
    if tokens is None:
        tokens = CPCV_ROBUST_TOKENS  # Default: test on robust tokens first

    engine = Engine()

    # Generate all strategies
    all_strats = []
    all_strats.extend(generate_trend_sweep())
    all_strats.extend(generate_mr_sweep())
    all_strats.extend(generate_hybrid_sweep())

    # Add baseline for comparison
    all_strats.insert(0, strategy_dual_momentum)

    print(f"{'='*100}")
    print(f"STRATEGY SWEEP — {len(all_strats)} strategies × {len(tokens)} tokens")
    print(f"{'='*100}")

    results = []
    t0 = time.time()

    for idx, strat_fn in enumerate(all_strats):
        name = getattr(strat_fn, '__name__', f'strategy_{idx}')
        try:
            token_results = engine.run(strat_fn, tokens=tokens, verbose=False)
            agg = engine._aggregate(token_results)
            agg['name'] = name
            agg['profitable_pct'] = agg['profitable'] / max(agg['n_tokens'], 1) * 100
            results.append(agg)

            if verbose and idx % 10 == 0:
                print(f"  [{idx+1}/{len(all_strats)}] {name:40s} "
                      f"PnL=${agg['total_pnl']:>+10,.0f}  "
                      f"Trades={agg['n_trades']:>5d}  "
                      f"WR={agg['win_rate']:.0f}%  "
                      f"Payoff={agg['payoff_ratio']:.2f}x  "
                      f"Prof={agg['profitable']}/{agg['n_tokens']}")
        except Exception as e:
            if verbose:
                print(f"  [{idx+1}/{len(all_strats)}] {name}: ERROR: {e}")

    elapsed = time.time() - t0

    # Sort by total PnL
    results.sort(key=lambda x: -x['total_pnl'])

    # Print top N
    print(f"\n{'='*100}")
    print(f"TOP {top_n} STRATEGIES (by total PnL) — {elapsed:.1f}s total")
    print(f"{'='*100}")
    print(f"{'Rank':>4s}  {'Strategy':40s} {'PnL':>12s} {'Annual':>10s} "
          f"{'Trades':>7s} {'WR':>5s} {'Payoff':>7s} {'Prof':>6s}")
    print("-" * 100)

    for i, r in enumerate(results[:top_n]):
        annual = r['total_pnl'] / 2.0
        print(f"  {i+1:>2d}  {r['name']:40s} ${r['total_pnl']:>+10,.0f} "
              f"${annual:>+8,.0f}/yr  {r['n_trades']:>5d}  "
              f"{r['win_rate']:>3.0f}%  {r['payoff_ratio']:>5.2f}x  "
              f"{r['profitable']}/{r['n_tokens']}")

    # Print bottom 5 (to learn what fails)
    print(f"\nBOTTOM 5 (worst performing):")
    for r in results[-5:]:
        annual = r['total_pnl'] / 2.0
        print(f"       {r['name']:40s} ${r['total_pnl']:>+10,.0f} "
              f"${annual:>+8,.0f}/yr  {r['n_trades']:>5d}  "
              f"{r['win_rate']:>3.0f}%")

    # Save results
    os.makedirs('results', exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    fname = f'results/sweep_{timestamp}.json'
    with open(fname, 'w') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'n_strategies': len(all_strats),
            'n_tokens': len(tokens),
            'tokens': tokens,
            'elapsed_seconds': elapsed,
            'results': [{k: v for k, v in r.items()} for r in results],
        }, f, indent=2, default=str)
    print(f"\nResults saved to {fname}")

    return results


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--all-tokens', action='store_true', help='Test on all 49 tokens')
    parser.add_argument('--top', type=int, default=20, help='Show top N results')
    args = parser.parse_args()

    tokens = LIQUID_TOKENS if args.all_tokens else CPCV_ROBUST_TOKENS
    run_sweep(tokens=tokens, top_n=args.top)
