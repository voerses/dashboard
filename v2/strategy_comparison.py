"""
Strategy Comparison: Fat-Tail vs VPIN-Enhanced vs Prior Strategies vs Token Selection
=====================================================================================

Compares ALL approaches on the same data to find maximum annual return.
Does NOT modify mtf_strategy_v2.py — imports and wraps it.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
import pandas as pd
import time
from pathlib import Path

from liquid_universe import LIQUID_TOKENS, TIER1, TIER2, TIER3, get_tier
from mtf_strategy_v2 import (
    backtest_token, aggregate_to_timeframe, compute_indicators_fast,
    detect_daily_regime, _align_higher_to_lower, _generate_dual_momentum_signals,
    _generate_vol_breakout_signals, _generate_mean_reversion_signals,
    _simulate_trades, _compute_position_size,
    CRISIS, QUIET, UPTREND, RANGE, DOWNTREND,
)


# =============================================================================
# Load enriched daily data (VPIN, realized vol, taker, etc.)
# =============================================================================

def load_enriched_daily():
    """Load the enriched daily data with VPIN/microstructure signals."""
    path = 'real_data/all_tokens_enriched.parquet'
    if not os.path.exists(path):
        return None
    df = pd.read_parquet(path)
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date')
    return df


def get_daily_vpin(enriched_df, ticker):
    """Extract daily VPIN for a ticker, return as Series with datetime index."""
    if enriched_df is None:
        return None
    mask = enriched_df['ticker'] == ticker
    if mask.sum() == 0:
        return None
    sub = enriched_df.loc[mask, ['vpin', 'realized_vol', 'taker_buy_ratio',
                                  'amihud_1m', 'vwap_deviation', 'intraday_skew']]
    # Filter to our data period (2024+)
    sub = sub[sub.index >= '2024-01-01']
    if len(sub) < 30:
        return None
    return sub


# =============================================================================
# Approach 1: VPIN-Enhanced Dual Momentum
# Uses daily VPIN as additional filter on top of existing signals
# =============================================================================

def backtest_token_vpin_enhanced(ticker, df_1h, enriched_daily, capital=200_000,
                                  fee_rate=0.001, slippage_bps=5):
    """
    Enhanced backtest: uses VPIN + realized vol from enriched daily data
    as additional entry filters on the dual momentum strategy.

    VPIN logic:
    - VPIN < 0.35 = low informed trading → market driven by noise, momentum less reliable
    - VPIN 0.35-0.50 = moderate informed → good for momentum (informed traders driving direction)
    - Skip entries when VPIN = 0.5 (no data / meaningless)
    """
    if df_1h is None or len(df_1h) < 500:
        return None

    daily_signals = get_daily_vpin(enriched_daily, ticker)

    df_4h = aggregate_to_timeframe(df_1h, hours=4)
    df_daily = aggregate_to_timeframe(df_1h, hours=24)

    if len(df_4h) < 100 or len(df_daily) < 30:
        return None

    # Extract arrays
    close_1h = df_1h['close'].values.astype(np.float64)
    high_1h = df_1h['high'].values.astype(np.float64)
    low_1h = df_1h['low'].values.astype(np.float64)
    vol_1h = df_1h['volume'].values.astype(np.float64)
    taker_1h = df_1h['taker_buy_base'].values.astype(np.float64) if 'taker_buy_base' in df_1h.columns else None

    close_4h = df_4h['close'].values.astype(np.float64)
    high_4h = df_4h['high'].values.astype(np.float64)
    low_4h = df_4h['low'].values.astype(np.float64)
    vol_4h = df_4h['volume'].values.astype(np.float64)
    taker_4h = df_4h['taker_buy_base'].values.astype(np.float64) if 'taker_buy_base' in df_4h.columns else None

    close_d = df_daily['close'].values.astype(np.float64)
    high_d = df_daily['high'].values.astype(np.float64)
    low_d = df_daily['low'].values.astype(np.float64)
    vol_d = df_daily['volume'].values.astype(np.float64)

    # Indicators
    ind_1h = compute_indicators_fast(close_1h, high_1h, low_1h, vol_1h, taker_1h)
    ind_4h = compute_indicators_fast(close_4h, high_4h, low_4h, vol_4h, taker_4h)
    ind_d = compute_indicators_fast(close_d, high_d, low_d, vol_d)

    idx_1h = df_1h.index
    idx_4h = df_4h.index
    idx_d = df_daily.index

    regimes_d = detect_daily_regime(ind_d)
    regime_1h = _align_higher_to_lower(idx_d, regimes_d.astype(float), idx_1h).astype(np.int8)
    regime_1h = np.nan_to_num(regime_1h, nan=RANGE).astype(np.int8)

    # --- VPIN filter: map daily VPIN to 1H ---
    vpin_filter = np.ones(len(close_1h), dtype=bool)  # default: allow all
    rv_boost = np.ones(len(close_1h), dtype=np.float64)  # edge multiplier

    if daily_signals is not None and len(daily_signals) > 0:
        vpin_daily = daily_signals['vpin']
        rv_daily = daily_signals['realized_vol']

        # Map to 1H
        vpin_1h = _align_higher_to_lower(vpin_daily.index, vpin_daily.values, idx_1h)
        rv_1h = _align_higher_to_lower(rv_daily.index, rv_daily.values, idx_1h)

        # VPIN filter: only trade when VPIN indicates informed flow (0.25-0.48)
        # Skip when VPIN = 0.5 (no data) or VPIN < 0.20 (too noisy)
        vpin_ok = (~np.isnan(vpin_1h)) & (vpin_1h >= 0.20) & (vpin_1h < 0.48)
        vpin_filter = vpin_ok

        # Realized vol boost: when RV is elevated but not extreme, increase edge
        # (volatility expansion = bigger moves = more opportunity)
        rv_med = np.nanmedian(rv_1h[rv_1h > 0]) if np.any(rv_1h > 0) else 0.5
        high_rv = (~np.isnan(rv_1h)) & (rv_1h > rv_med * 1.2) & (rv_1h < rv_med * 3.0)
        rv_boost[high_rv] = 1.15  # 15% edge boost during elevated vol

    # --- Strategy: VPIN-enhanced dual momentum ---
    entry_dm = _generate_dual_momentum_signals(
        ind_1h, ind_4h, ind_d, regime_1h, idx_1h, idx_4h, idx_d)

    # Apply VPIN filter
    entry_dm = entry_dm & vpin_filter

    direction = np.ones(len(close_1h), dtype=np.int8)
    trades_dm, eq_dm = _simulate_trades(
        close_1h, high_1h, low_1h, ind_1h['atr'], entry_dm, direction,
        stop_mult=5.0, trail_mult=4.0, target_mult=999,
        regime=regime_1h, exit_regimes={CRISIS, DOWNTREND},
        min_hold=18, max_hold=720,
        rsi=ind_1h['rsi'], rsi_exit_level=999,
        fee_rate=fee_rate, slippage_bps=slippage_bps,
        initial_capital=capital / 2, ticker=ticker,
        no_stop_bars=12, edge_override=0.45,  # slightly higher edge with VPIN confirmation
    )

    # --- Strategy: VPIN-enhanced mean reversion ---
    entry_mr = _generate_mean_reversion_signals(
        ind_1h, ind_4h, ind_d, regime_1h, idx_1h, idx_4h, idx_d)

    # For mean reversion, we want LOW VPIN (noise-driven dislocation, not informed selling)
    if daily_signals is not None and len(daily_signals) > 0:
        mr_vpin_ok = (~np.isnan(vpin_1h)) & (vpin_1h < 0.35)  # low informed = dislocation
        entry_mr = entry_mr & mr_vpin_ok

    ema20_4h_1h = _align_higher_to_lower(idx_4h, ind_4h['ema_20'], idx_1h)
    trades_mr, eq_mr = _simulate_trades(
        close_1h, high_1h, low_1h, ind_1h['atr'], entry_mr, direction,
        stop_mult=2.0, trail_mult=2.0, target_mult=5.0,
        regime=regime_1h, exit_regimes={CRISIS, DOWNTREND},
        min_hold=18, max_hold=240,
        rsi=ind_1h['rsi'], rsi_exit_level=999,
        fee_rate=fee_rate, slippage_bps=slippage_bps,
        initial_capital=capital / 2, ticker=ticker,
        convex_exit=True, mean_target_vals=ema20_4h_1h,
        edge_override=0.55,
    )

    all_trades = []
    for t in trades_dm:
        t['strategy'] = 'vpin_dual_mom'
    for t in trades_mr:
        t['strategy'] = 'vpin_mean_rev'
    all_trades = trades_dm + trades_mr

    total_equity = capital + (eq_dm - capital/2) + (eq_mr - capital/2)
    total_return = (total_equity - capital) / capital * 100
    wins = [t for t in all_trades if t['pnl'] > 0]
    losers = [t for t in all_trades if t['pnl'] <= 0]
    avg_win = np.mean([t['pnl'] for t in wins]) if wins else 0
    avg_loss = abs(np.mean([t['pnl'] for t in losers])) if losers else 1

    return {
        'ticker': ticker,
        'tier': get_tier(ticker)[0],
        'total_return': total_return,
        'n_trades': len(all_trades),
        'win_rate': len(wins) / max(len(all_trades), 1) * 100,
        'payoff_ratio': avg_win / max(avg_loss, 1),
        'equity': total_equity,
        'trades': all_trades,
        'strategy_breakdown': {
            'vpin_dual_mom': {'n_trades': len(trades_dm), 'pnl': eq_dm - capital/2},
            'vpin_mean_rev': {'n_trades': len(trades_mr), 'pnl': eq_mr - capital/2},
        },
    }


# =============================================================================
# Approach 2: CPCV-Selected Tokens Only (robust subset)
# =============================================================================

# These 11 tokens had PBO < 40% in CPCV analysis
CPCV_ROBUST_TOKENS = [
    'PENGU', 'SUI', 'OM', 'TRX', 'DOT', 'AVAX',
    'BONK', 'FIL', 'FLOKI', 'DENT', 'ZRO'
]


# =============================================================================
# Approach 3: Prior V2/V3 strategies on daily data (from strategies.py/strategies_v3.py)
# Simplified reimplementation using daily bars (original was daily-only)
# =============================================================================

def backtest_prior_v2_momentum(ticker, df_daily, capital=200_000, fee_rate=0.001, slippage_bps=5):
    """
    V2 Strategy 2: Momentum Trend Follower (daily).
    EMA cross + MACD + ADX > 20, ATR trailing stops, min 5-day hold.
    """
    if df_daily is None or len(df_daily) < 100:
        return None

    close = df_daily['close'].values.astype(np.float64)
    high = df_daily['high'].values.astype(np.float64)
    low = df_daily['low'].values.astype(np.float64)
    vol = df_daily['volume'].values.astype(np.float64)

    ind = compute_indicators_fast(close, high, low, vol)

    n = len(close)
    trades = []
    equity = capital
    position = 0.0
    entry_price = 0.0
    entry_bar = 0
    stop_price = 0.0
    highest = 0.0

    tier, _ = get_tier(ticker)
    if tier == 0:
        return None

    for i in range(60, n):
        # EXIT
        if position != 0:
            bars_held = i - entry_bar
            highest = max(highest, high[i])
            cur_atr = ind['atr'][i] if not np.isnan(ind['atr'][i]) else abs(entry_price) * 0.02

            # Trailing stop (3x ATR)
            trail = highest - 3.0 * cur_atr
            stop_price = max(stop_price, trail)

            exit_signal = False
            exit_price = close[i]

            if low[i] <= stop_price and bars_held >= 5:
                exit_signal = True
                exit_price = stop_price
            elif ind['rsi'][i] > 75 and bars_held >= 5:
                exit_signal = True
            elif bars_held >= 60:
                exit_signal = True

            if exit_signal:
                pnl = position * (exit_price - entry_price)
                fee = abs(position * exit_price) * fee_rate
                net_pnl = pnl - fee
                equity += net_pnl
                trades.append({
                    'pnl': net_pnl,
                    'return_pct': net_pnl / max(abs(position * entry_price), 1) * 100,
                    'hold_hours': bars_held * 24,
                    'exit_reason': 'trail',
                })
                position = 0.0

        # ENTRY (daily)
        if position == 0:
            ema_cross = ind['ema_20'][i] > ind['ema_50'][i]
            macd_pos = ind['macd_hist'][i] > 0
            adx_ok = ind['adx'][i] > 20
            rsi_ok = 30 < ind['rsi'][i] < 70
            vol_ok = ind['vol_ratio'][i] > 0.8

            if ema_cross and macd_pos and adx_ok and rsi_ok and vol_ok:
                v = ind['atr'][i] / max(close[i], 1e-10) if not np.isnan(ind['atr'][i]) else 0.02
                pos_usd = _compute_position_size(tier, 0.35, equity, v)
                if pos_usd < 200:
                    continue

                entry_price = close[i] * (1 + slippage_bps/10000)
                position = pos_usd / max(entry_price, 1e-10)
                entry_bar = i
                highest = high[i]
                fee = abs(position * entry_price) * fee_rate
                equity -= fee
                stop_price = entry_price - 3.0 * ind['atr'][i]

    if position != 0:
        equity += position * (close[-1] - entry_price)

    total_return = (equity - capital) / capital * 100
    wins = [t for t in trades if t['pnl'] > 0]
    losers = [t for t in trades if t['pnl'] <= 0]
    avg_win = np.mean([t['pnl'] for t in wins]) if wins else 0
    avg_loss = abs(np.mean([t['pnl'] for t in losers])) if losers else 1

    return {
        'ticker': ticker,
        'tier': get_tier(ticker)[0],
        'total_return': total_return,
        'n_trades': len(trades),
        'win_rate': len(wins) / max(len(trades), 1) * 100,
        'payoff_ratio': avg_win / max(avg_loss, 1),
        'equity': equity,
        'trades': trades,
    }


def backtest_prior_v3_contrarian(ticker, df_daily, enriched_daily, capital=200_000,
                                  fee_rate=0.001, slippage_bps=5):
    """
    V3 Strategy 8: Liquidity Contrarian (daily).
    Buy on volume spike + price drop + low close (forced selling).
    Uses Amihud illiquidity + VPIN from enriched data.
    """
    if df_daily is None or len(df_daily) < 100:
        return None

    close = df_daily['close'].values.astype(np.float64)
    high = df_daily['high'].values.astype(np.float64)
    low = df_daily['low'].values.astype(np.float64)
    vol = df_daily['volume'].values.astype(np.float64)

    ind = compute_indicators_fast(close, high, low, vol)

    # Get VPIN if available
    daily_signals = get_daily_vpin(enriched_daily, ticker) if enriched_daily is not None else None
    has_vpin = daily_signals is not None and len(daily_signals) > 0

    n = len(close)
    trades = []
    equity = capital
    position = 0.0
    entry_price = 0.0
    entry_bar = 0

    tier, _ = get_tier(ticker)
    if tier == 0:
        return None

    for i in range(60, n):
        # EXIT
        if position != 0:
            bars_held = i - entry_bar
            cur_atr = ind['atr'][i] if not np.isnan(ind['atr'][i]) else abs(entry_price) * 0.02

            exit_signal = False
            exit_price = close[i]

            # Mean reversion target
            sma20 = ind['ema_20'][i]
            if not np.isnan(sma20) and close[i] >= sma20 and bars_held >= 3:
                exit_signal = True
            elif close[i] <= entry_price - 2.0 * cur_atr and bars_held >= 2:
                exit_signal = True
                exit_price = entry_price - 2.0 * cur_atr
            elif bars_held >= 20:
                exit_signal = True

            if exit_signal:
                pnl = position * (exit_price - entry_price)
                fee = abs(position * exit_price) * fee_rate
                net_pnl = pnl - fee
                equity += net_pnl
                trades.append({
                    'pnl': net_pnl,
                    'return_pct': net_pnl / max(abs(position * entry_price), 1) * 100,
                    'hold_hours': bars_held * 24,
                    'exit_reason': 'target' if close[i] >= sma20 else 'stop',
                })
                position = 0.0

        # ENTRY: volume spike + price drop + oversold
        if position == 0:
            ret_5d = (close[i] - close[max(i-5,0)]) / max(close[max(i-5,0)], 1e-10)
            vol_spike = ind['vol_ratio'][i] > 2.0
            price_drop = ret_5d < -0.05
            oversold = ind['rsi'][i] < 35
            close_loc = (close[i] - low[i]) / max(high[i] - low[i], 1e-10)
            weak_close = close_loc < 0.3  # closed near low = forced selling

            if vol_spike and price_drop and oversold and weak_close:
                v = ind['atr'][i] / max(close[i], 1e-10) if not np.isnan(ind['atr'][i]) else 0.02
                pos_usd = _compute_position_size(tier, 0.40, equity, v)
                if pos_usd < 200:
                    continue

                entry_price = close[i] * (1 + slippage_bps/10000)
                position = pos_usd / max(entry_price, 1e-10)
                entry_bar = i
                fee = abs(position * entry_price) * fee_rate
                equity -= fee

    if position != 0:
        equity += position * (close[-1] - entry_price)

    total_return = (equity - capital) / capital * 100
    wins = [t for t in trades if t['pnl'] > 0]
    losers = [t for t in trades if t['pnl'] <= 0]
    avg_win = np.mean([t['pnl'] for t in wins]) if wins else 0
    avg_loss = abs(np.mean([t['pnl'] for t in losers])) if losers else 1

    return {
        'ticker': ticker,
        'tier': get_tier(ticker)[0],
        'total_return': total_return,
        'n_trades': len(trades),
        'win_rate': len(wins) / max(len(trades), 1) * 100,
        'payoff_ratio': avg_win / max(avg_loss, 1),
        'equity': equity,
        'trades': trades,
    }


# =============================================================================
# Master Comparison Runner
# =============================================================================

def run_comparison():
    """Run all strategy approaches and compare."""
    enriched = load_enriched_daily()
    print(f"Enriched data: {'loaded' if enriched is not None else 'not found'}")

    tokens = LIQUID_TOKENS
    capital = 200_000

    configs = {
        'A_DM_MR_baseline': {'desc': 'Dual Momentum + Mean Rev (current best)',
                              'strategies': ('dual_momentum', 'mean_reversion')},
        'B_VPIN_enhanced': {'desc': 'VPIN-Enhanced DM + MR (microstructure filter)'},
        'C_CPCV_selected': {'desc': 'DM+MR on CPCV-robust tokens only (11 tokens)',
                            'strategies': ('dual_momentum', 'mean_reversion'),
                            'tokens': CPCV_ROBUST_TOKENS},
        'D_V2_momentum': {'desc': 'Prior V2 Daily Momentum (EMA cross + MACD)'},
        'E_V3_contrarian': {'desc': 'Prior V3 Liquidity Contrarian (vol spike + oversold)'},
        'F_all_3_strats': {'desc': 'All 3 fat-tail strategies (including vol breakout)',
                           'strategies': ('dual_momentum', 'vol_breakout', 'mean_reversion')},
    }

    all_results = {}

    for config_name, config in configs.items():
        print(f"\n{'='*80}")
        print(f"  {config_name}: {config['desc']}")
        print(f"{'='*80}")
        t0 = time.time()
        results = {}

        token_list = config.get('tokens', tokens)

        for idx, ticker in enumerate(token_list, 1):
            h1_path = f'real_data/1h_cache/{ticker}_1h.parquet'
            if not os.path.exists(h1_path):
                continue

            df_1h = pd.read_parquet(h1_path)
            if len(df_1h) < 500:
                continue

            if config_name == 'B_VPIN_enhanced':
                result = backtest_token_vpin_enhanced(
                    ticker, df_1h, enriched, capital=capital)
            elif config_name == 'D_V2_momentum':
                df_daily = aggregate_to_timeframe(df_1h, hours=24)
                result = backtest_prior_v2_momentum(ticker, df_daily, capital=capital)
            elif config_name == 'E_V3_contrarian':
                df_daily = aggregate_to_timeframe(df_1h, hours=24)
                result = backtest_prior_v3_contrarian(
                    ticker, df_daily, enriched, capital=capital)
            else:
                strats = config.get('strategies', ('dual_momentum', 'mean_reversion'))
                result = backtest_token(ticker, df_1h, capital=capital, strategies=strats)

            if result is not None:
                results[ticker] = result

        elapsed = time.time() - t0

        # Aggregate
        total_pnl = sum(r['equity'] - capital for r in results.values())
        total_trades = sum(r['n_trades'] for r in results.values())
        profitable = sum(1 for r in results.values() if r['equity'] > capital)
        n_tokens = len(results)

        all_t = []
        for r in results.values():
            all_t.extend(r.get('trades', []))
        wins = [t for t in all_t if t['pnl'] > 0]
        losers = [t for t in all_t if t['pnl'] <= 0]
        avg_win = np.mean([t['pnl'] for t in wins]) if wins else 0
        avg_loss = abs(np.mean([t['pnl'] for t in losers])) if losers else 1

        # Annualize: data is ~2 years (Jan 2024 - Jan 2026)
        years = 2.0
        annual_return = total_pnl / years
        annual_pct = (total_pnl / capital) / years * 100

        all_results[config_name] = {
            'desc': config['desc'],
            'total_pnl': total_pnl,
            'annual_pnl': annual_return,
            'annual_pct': annual_pct,
            'n_tokens': n_tokens,
            'n_trades': total_trades,
            'profitable': profitable,
            'win_rate': len(wins) / max(len(all_t), 1) * 100,
            'payoff_ratio': avg_win / max(avg_loss, 1),
            'elapsed': elapsed,
        }

        print(f"  Tokens: {n_tokens}  Trades: {total_trades}  "
              f"Profitable: {profitable}/{n_tokens}")
        print(f"  Total PnL: ${total_pnl:+,.0f}  "
              f"Annual: ${annual_return:+,.0f}/yr ({annual_pct:+.1f}%/yr)")
        print(f"  WR: {len(wins)/max(len(all_t),1)*100:.0f}%  "
              f"Payoff: {avg_win/max(avg_loss,1):.2f}x  "
              f"Time: {elapsed:.1f}s")

        # Top 5
        sorted_r = sorted(results.items(), key=lambda x: x[1]['total_return'], reverse=True)
        top5 = ', '.join(f"{tk}:{r['total_return']:+.1f}%" for tk, r in sorted_r[:5])
        print(f"  Top 5: {top5}")

    # Final comparison table
    print(f"\n\n{'='*100}")
    print("FINAL COMPARISON — RANKED BY ANNUAL PnL")
    print(f"{'='*100}")
    print(f"{'Config':40s} {'Annual PnL':>12s} {'Annual %':>10s} {'Trades':>8s} "
          f"{'WR':>6s} {'Payoff':>8s} {'Tokens':>8s}")
    print("-" * 100)

    for name, r in sorted(all_results.items(), key=lambda x: -x[1]['annual_pnl']):
        print(f"  {r['desc']:38s} ${r['annual_pnl']:>+10,.0f}  "
              f"{r['annual_pct']:>+8.1f}%  {r['n_trades']:>6d}  "
              f"{r['win_rate']:>4.0f}%  {r['payoff_ratio']:>6.2f}x  "
              f"{r['profitable']}/{r['n_tokens']}")

    return all_results


if __name__ == '__main__':
    run_comparison()
