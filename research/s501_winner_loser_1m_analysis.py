"""
s501 Winner vs Loser 1m Pattern Analysis
=========================================

Analyzes sub-hourly 1m price action around BB cross entries to find
patterns distinguishing winners (pnl > 0) from losers (pnl <= 0).

Memory-efficient: pre-computes BB levels per token, uses MinuteExitCache
with LRU eviction for 1m data.
"""

import sys
import os
import json
import warnings
import gc

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.chdir('/workspace/crypto_backtest')
warnings.filterwarnings('ignore')

# ── Configuration ──────────────────────────────────────────────────────
TRADES_PATH = 'results/v4/s501_12mo_100k_trades.json'
UNIFIED_TS_PATH = '/tmp/s501_unified_ts.npy'
CACHE_1M_DIR = 'data/perp/1m_cache'
CACHE_1H_DIR = 'data/perp/1h_cache'
N_SAMPLE = 200
SEED = 42


def build_unified_ts_if_needed():
    """Build unified_ts using the actual backtest pipeline."""
    if os.path.exists(UNIFIED_TS_PATH):
        ts = np.load(UNIFIED_TS_PATH, allow_pickle=True)
        if len(ts) > 17000:
            return ts

    print("Building unified_ts from pipeline...")
    from v4.config import PortfolioConfig, StrategySpec
    from v4.universe import get_all_tradeable
    from v4.portfolio_signals import _load_all_contexts, _sr_to_token_signals
    from v4.engine import _load_strategy_fn
    from v4.simulator import build_unified_index

    all_tokens = get_all_tradeable('perp')
    config = PortfolioConfig(capital=100_000, exchange='binance', skip_walk_forward=True)
    spec = StrategySpec(strategy_id='s501', market='perp', strategy_type='portfolio',
                        max_positions=50, entry_resolution=1)
    contexts, cutoff, anchor = _load_all_contexts(all_tokens, spec, config, months=24)
    strategy = _load_strategy_fn('s501')
    results = strategy(contexts)

    all_signals = {}
    for token, sr in results.items():
        ctx = contexts[token]
        ctx_perp = ctx[1] if isinstance(ctx, tuple) else ctx
        sig = _sr_to_token_signals(token, sr, None, ctx_perp, 's501', False, 'perp', cutoff, config)
        if sig is not None:
            all_signals.setdefault('s501', {})[token] = sig

    unified_ts, _ = build_unified_index(all_signals)
    np.save(UNIFIED_TS_PATH, unified_ts)
    del contexts, results, all_signals
    gc.collect()
    return unified_ts


def precompute_bb_for_token(token):
    """Compute prior 4H BB upper/lower arrays for a token.

    Uses pandas resample('4h') to match the engine's 4H bar construction.
    Returns (ts_4h, prior_bb_upper, prior_bb_lower) arrays.
    The ts_4h timestamps are at the START of each 4H window (00, 04, 08, ...).
    """
    path = os.path.join(CACHE_1H_DIR, f'{token}_1h.parquet')
    if not os.path.exists(path):
        return None

    df = pd.read_parquet(path, columns=['close'])

    # Resample to 4H exactly as the engine does (aggregate_to_timeframe)
    df_4h = df.resample('4h').agg({'close': 'last'}).dropna()

    if len(df_4h) < 22:
        del df, df_4h
        return None

    close_4h = df_4h['close'].values
    ts_4h = df_4h.index

    # BB(20,2) on 4H close
    period = 20
    sma = pd.Series(close_4h).rolling(period).mean().values
    std = pd.Series(close_4h).rolling(period).std().values
    bb_upper = sma + 2 * std
    bb_lower = sma - 2 * std

    # Prior BB: shift by 1 on 4H grid (point-in-time: known at start of current window)
    prior_upper = np.empty_like(bb_upper)
    prior_upper[0] = np.nan
    prior_upper[1:] = bb_upper[:-1]
    prior_lower = np.empty_like(bb_lower)
    prior_lower[0] = np.nan
    prior_lower[1:] = bb_lower[:-1]

    del df, df_4h
    gc.collect()

    return ts_4h, prior_upper, prior_lower


def get_bb_at_entry(bb_data, entry_ts):
    """Look up prior BB levels for a given entry timestamp."""
    if bb_data is None:
        return None, None
    ts_4h, prior_upper, prior_lower = bb_data

    entry_pd = pd.Timestamp(str(entry_ts))
    # Find the 4H window containing this entry.
    # ts_4h has timestamps at 00, 04, 08, 12, 16, 20 (start of window).
    # searchsorted(side='right') - 1 gives the window that starts <= entry_ts.
    search_idx = ts_4h.searchsorted(entry_pd, side='right') - 1
    if search_idx < 0 or search_idx >= len(prior_upper):
        return None, None

    u = float(prior_upper[search_idx])
    lo = float(prior_lower[search_idx])
    if np.isnan(u) or np.isnan(lo):
        return None, None
    return u, lo


def load_1m_for_hour(token, entry_ts, cache):
    """Load 1m data for a specific hour using a simple file-based approach.

    Returns DataFrame slice for the entry hour, or None.
    """
    # Use the cache dict to avoid reloading
    if token not in cache:
        path = os.path.join(CACHE_1M_DIR, f'{token}_1m.parquet')
        if not os.path.exists(path):
            cache[token] = None
            return None
        # Only load columns we need
        df = pd.read_parquet(path, columns=['open', 'high', 'low', 'close', 'volume'])
        cache[token] = df
        # Evict if cache too large (keep at most 10 tokens)
        if len(cache) > 10:
            oldest = next(k for k in cache if k != token and cache[k] is not None)
            del cache[oldest]
            gc.collect()

    df = cache[token]
    if df is None:
        return None

    entry_pd = pd.Timestamp(str(entry_ts))
    hour_end = entry_pd + pd.Timedelta(hours=1)
    mask = (df.index >= entry_pd) & (df.index < hour_end)
    window = df.loc[mask]
    return window if len(window) >= 10 else None


def analyze_trade(trade, unified_ts, window_1m, bb_upper, bb_lower, df_1m_full):
    """Analyze 1m price action around BB cross for a single trade."""
    entry_bar = trade['entry_bar']
    if entry_bar >= len(unified_ts):
        return None

    entry_ts = pd.Timestamp(str(unified_ts[entry_bar]))
    direction = trade['direction']
    entry_price = float(trade['entry_price'])
    pnl = float(trade['pnl'])

    bb_level = bb_upper if direction == 1 else bb_lower
    if bb_level is None or np.isnan(bb_level):
        return None

    closes_1m = window_1m['close'].values.astype(np.float64)
    highs_1m = window_1m['high'].values.astype(np.float64)
    lows_1m = window_1m['low'].values.astype(np.float64)
    volumes_1m = window_1m['volume'].values.astype(np.float64)
    opens_1m = window_1m['open'].values.astype(np.float64)

    # Find first BB cross minute
    if direction == 1:
        cross_mask = closes_1m > bb_level
    else:
        cross_mask = closes_1m < bb_level

    cross_indices = np.where(cross_mask)[0]
    if len(cross_indices) == 0:
        return None

    ci = cross_indices[0]
    cross_price = closes_1m[ci]

    result = {
        'token': trade['token'],
        'direction': direction,
        'pnl': pnl,
        'winner': pnl > 0,
        'exit_reason': trade['exit_reason'],
        'hold_hours': trade['hold_hours'],
        'entry_price': entry_price,
        'bb_level': bb_level,
    }

    # 1. Cross minute (0-59)
    result['cross_minute'] = ci

    # 2. Volume at cross vs hour average
    avg_vol = np.mean(volumes_1m)
    cross_vol = volumes_1m[ci]
    result['vol_ratio_at_cross'] = cross_vol / max(avg_vol, 1e-10)
    vol_window = volumes_1m[max(0, ci - 2):min(len(volumes_1m), ci + 3)]
    result['vol_5m_around_cross'] = np.mean(vol_window) / max(avg_vol, 1e-10)

    # 3. Price move after cross using extended data (beyond current hour)
    cross_time = window_1m.index[ci]
    for offset in [5, 10, 15, 30]:
        target = cross_time + pd.Timedelta(minutes=offset)
        idx = df_1m_full.index.searchsorted(target)
        if idx < len(df_1m_full):
            future_price = float(df_1m_full['close'].iloc[idx])
            if direction == 1:
                move = (future_price - cross_price) / cross_price
            else:
                move = (cross_price - future_price) / cross_price
            result[f'move_{offset}m'] = move * 100  # percent
        else:
            result[f'move_{offset}m'] = np.nan

    # 4. Max retrace back toward BB after cross (within the hour)
    remaining_lows = lows_1m[ci:]
    remaining_highs = highs_1m[ci:]
    remaining_closes = closes_1m[ci:]
    if direction == 1:
        min_after = np.min(remaining_lows)
        retrace = (cross_price - min_after) / max(cross_price - bb_level, 1e-10)
    else:
        max_after = np.max(remaining_highs)
        retrace = (max_after - cross_price) / max(bb_level - cross_price, 1e-10)
    result['retrace_ratio'] = retrace

    # 5. Close relative to BB at cross minute
    if direction == 1:
        bb_dist = (cross_price - bb_level) / bb_level
    else:
        bb_dist = (bb_level - cross_price) / bb_level
    result['bb_dist_pct'] = bb_dist * 100

    # 6. Cross candle momentum
    if direction == 1:
        candle_mom = (closes_1m[ci] - opens_1m[ci]) / max(opens_1m[ci], 1e-10)
    else:
        candle_mom = (opens_1m[ci] - closes_1m[ci]) / max(opens_1m[ci], 1e-10)
    result['cross_candle_momentum'] = candle_mom * 100

    # 7. Pre-cross 5m trend
    if ci >= 5:
        pre_closes = closes_1m[ci - 5:ci]
        if direction == 1:
            pre_trend = (pre_closes[-1] - pre_closes[0]) / max(pre_closes[0], 1e-10)
        else:
            pre_trend = (pre_closes[0] - pre_closes[-1]) / max(pre_closes[0], 1e-10)
        result['pre_cross_5m_trend'] = pre_trend * 100
    else:
        result['pre_cross_5m_trend'] = np.nan

    # 8. Immediate continuation (next 3 candles all in direction)
    if ci + 3 < len(closes_1m):
        next3 = closes_1m[ci + 1:ci + 4]
        if direction == 1:
            continuation = bool(np.all(next3 > cross_price))
            pullback = bool(np.any(next3 < bb_level))
        else:
            continuation = bool(np.all(next3 < cross_price))
            pullback = bool(np.any(next3 > bb_level))
        result['immediate_continuation'] = continuation
        result['immediate_pullback'] = pullback
    else:
        result['immediate_continuation'] = np.nan
        result['immediate_pullback'] = np.nan

    # 9. Re-crosses through BB
    if direction == 1:
        re_crosses = int(np.sum(remaining_closes[1:] < bb_level)) if len(remaining_closes) > 1 else 0
    else:
        re_crosses = int(np.sum(remaining_closes[1:] > bb_level)) if len(remaining_closes) > 1 else 0
    result['re_crosses'] = re_crosses

    # 10. MFE intra-hour
    if direction == 1:
        mfe = (np.max(remaining_highs) - cross_price) / max(cross_price, 1e-10)
    else:
        mfe = (cross_price - np.min(remaining_lows)) / max(cross_price, 1e-10)
    result['mfe_intra_hour'] = mfe * 100

    return result


def main():
    print("=" * 78)
    print("  s501 Winner vs Loser -- Sub-Hourly 1m Pattern Analysis")
    print("=" * 78)

    # Load data
    print("\nLoading trades...")
    trades = json.load(open(TRADES_PATH))
    print(f"  Total trades: {len(trades)}")

    unified_ts = build_unified_ts_if_needed()
    print(f"  Unified timestamps: {len(unified_ts)} bars")

    winners = [t for t in trades if float(t['pnl']) > 0]
    losers = [t for t in trades if float(t['pnl']) <= 0]
    print(f"  Winners: {len(winners)}, Losers: {len(losers)}")

    rng = np.random.RandomState(SEED)
    sample_w = [winners[i] for i in rng.choice(len(winners), min(N_SAMPLE, len(winners)), replace=False)]
    sample_l = [losers[i] for i in rng.choice(len(losers), min(N_SAMPLE, len(losers)), replace=False)]
    sample = sample_w + sample_l
    print(f"  Sample: {len(sample_w)} winners + {len(sample_l)} losers = {len(sample)}")

    # Group trades by token for efficient processing
    by_token = {}
    for t in sample:
        by_token.setdefault(t['token'], []).append(t)
    print(f"  Tokens to process: {len(by_token)}")

    # Process token by token (memory efficient)
    print("\nAnalyzing 1m patterns (token by token)...")
    all_results = []
    stats = {'processed': 0, 'no_1m': 0, 'no_cross': 0, 'no_bb': 0}

    tokens_sorted = sorted(by_token.keys())
    for ti, token in enumerate(tokens_sorted):
        token_trades = by_token[token]

        # Load 1m data for this token
        path_1m = os.path.join(CACHE_1M_DIR, f'{token}_1m.parquet')
        if not os.path.exists(path_1m):
            stats['no_1m'] += len(token_trades)
            continue

        df_1m = pd.read_parquet(path_1m, columns=['open', 'high', 'low', 'close', 'volume'])

        # Pre-compute BB levels for this token
        bb_data = precompute_bb_for_token(token)
        if bb_data is None:
            stats['no_bb'] += len(token_trades)
            del df_1m
            gc.collect()
            continue

        for trade in token_trades:
            entry_bar = trade['entry_bar']
            if entry_bar >= len(unified_ts):
                continue

            entry_ts = pd.Timestamp(str(unified_ts[entry_bar]))

            # Get BB levels
            bb_upper, bb_lower = get_bb_at_entry(bb_data, entry_ts)
            if bb_upper is None:
                stats['no_bb'] += 1
                continue

            # Get 1m window for the entry hour
            hour_end = entry_ts + pd.Timedelta(hours=1)
            mask = (df_1m.index >= entry_ts) & (df_1m.index < hour_end)
            window = df_1m.loc[mask]
            if len(window) < 10:
                stats['no_cross'] += 1
                continue

            result = analyze_trade(trade, unified_ts, window, bb_upper, bb_lower, df_1m)
            if result is None:
                stats['no_cross'] += 1
                continue

            all_results.append(result)
            stats['processed'] += 1

        # Free memory
        del df_1m, bb_data
        gc.collect()

        if (ti + 1) % 10 == 0:
            print(f"  {ti + 1}/{len(tokens_sorted)} tokens, {stats['processed']} trades...")

    print(f"\n  Processed: {stats['processed']}")
    print(f"  Skipped: no_1m={stats['no_1m']}, no_bb={stats['no_bb']}, no_cross={stats['no_cross']}")

    if stats['processed'] < 20:
        print("\nInsufficient data for analysis.")
        return

    # Build comparison
    df = pd.DataFrame(all_results)
    w = df[df['winner'] == True]
    l = df[df['winner'] == False]

    print(f"\n  Analyzable: {len(w)} winners, {len(l)} losers")

    # ──────────────────────────────────────────────────────────────────
    #  MAIN COMPARISON TABLE
    # ──────────────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("  COMPARISON TABLE: Winners vs Losers")
    print("=" * 78)

    metrics = [
        ('cross_minute',           'Cross minute (0-59)',       'median'),
        ('cross_minute',           'Cross minute (0-59)',       'mean'),
        ('vol_ratio_at_cross',     'Vol ratio at cross',        'median'),
        ('vol_ratio_at_cross',     'Vol ratio at cross',        'mean'),
        ('vol_5m_around_cross',    'Vol 5m window / hr avg',    'median'),
        ('cross_candle_momentum',  'Cross candle mom (bps)',     'median'),
        ('pre_cross_5m_trend',     'Pre-cross 5m trend (bps)',   'median'),
        ('bb_dist_pct',            'BB distance at cross (%)',   'median'),
        ('bb_dist_pct',            'BB distance at cross (%)',   'mean'),
        ('move_5m',                'Move +5m (%)',               'median'),
        ('move_10m',               'Move +10m (%)',              'median'),
        ('move_15m',               'Move +15m (%)',              'median'),
        ('move_30m',               'Move +30m (%)',              'median'),
        ('retrace_ratio',          'Retrace ratio (to BB)',      'median'),
        ('retrace_ratio',          'Retrace ratio (to BB)',      'mean'),
        ('mfe_intra_hour',         'MFE intra-hour (%)',         'median'),
        ('mfe_intra_hour',         'MFE intra-hour (%)',         'mean'),
        ('re_crosses',             'Re-crosses through BB',      'mean'),
        ('immediate_continuation', 'Immediate continuation %',   'mean'),
        ('immediate_pullback',     'Immediate pullback %',       'mean'),
    ]

    print(f"\n  {'Metric':<32s} {'Stat':>6s}  {'Winners':>10s}  {'Losers':>10s}  {'Delta':>10s}  {'Signal':>8s}")
    print("  " + "-" * 85)

    for col, label, stat in metrics:
        if col not in df.columns:
            continue
        w_vals = w[col].dropna()
        l_vals = l[col].dropna()
        if len(w_vals) < 5 or len(l_vals) < 5:
            continue

        if stat == 'median':
            w_stat = w_vals.median()
            l_stat = l_vals.median()
        else:
            w_stat = w_vals.mean()
            l_stat = l_vals.mean()

        delta = w_stat - l_stat
        pooled_std = np.sqrt((w_vals.var() + l_vals.var()) / 2)
        effect = abs(delta) / pooled_std if pooled_std > 1e-10 else 0.0

        if effect > 0.5:
            sig = "STRONG"
        elif effect > 0.3:
            sig = "MEDIUM"
        elif effect > 0.15:
            sig = "weak"
        else:
            sig = "-"

        stat_l = stat[:4]
        print(f"  {label:<32s} {stat_l:>6s}  {w_stat:>10.4f}  {l_stat:>10.4f}  {delta:>+10.4f}  {sig:>8s}")

    # ──────────────────────────────────────────────────────────────────
    #  CROSS TIMING BREAKDOWN
    # ──────────────────────────────────────────────────────────────────
    print("\n\n" + "=" * 78)
    print("  CROSS TIMING BREAKDOWN")
    print("=" * 78)

    for name, lo, hi in [('min 0-5', 0, 5), ('min 6-15', 6, 15),
                          ('min 16-30', 16, 30), ('min 31-59', 31, 59)]:
        bucket = df[(df['cross_minute'] >= lo) & (df['cross_minute'] <= hi)]
        bw = bucket[bucket['winner'] == True]
        bl = bucket[bucket['winner'] == False]
        n_t = len(bucket)
        if n_t == 0:
            continue
        wr = len(bw) / n_t * 100
        avg_w = bw['pnl'].mean() if len(bw) > 0 else 0
        avg_l = bl['pnl'].mean() if len(bl) > 0 else 0
        print(f"  {name}: N={n_t:3d}  WR={wr:.1f}%  avg_pnl_win=${avg_w:+.1f}  avg_pnl_loss=${avg_l:+.1f}")

    # ──────────────────────────────────────────────────────────────────
    #  VOLUME AT CROSS BREAKDOWN
    # ──────────────────────────────────────────────────────────────────
    print("\n\n" + "=" * 78)
    print("  VOLUME AT CROSS BREAKDOWN")
    print("=" * 78)

    vol_col = 'vol_ratio_at_cross'
    for name, lo, hi in [('low vol (<0.5x)', 0, 0.5), ('avg vol (0.5-1.5x)', 0.5, 1.5),
                          ('high vol (1.5-3x)', 1.5, 3), ('spike (>3x)', 3, 100)]:
        bucket = df[(df[vol_col] >= lo) & (df[vol_col] < hi)]
        bw = bucket[bucket['winner'] == True]
        n_t = len(bucket)
        if n_t == 0:
            continue
        wr = len(bw) / n_t * 100
        print(f"  {name:<22s}: N={n_t:3d}  WR={wr:.1f}%")

    # ──────────────────────────────────────────────────────────────────
    #  CONTINUATION vs PULLBACK
    # ──────────────────────────────────────────────────────────────────
    print("\n\n" + "=" * 78)
    print("  CONTINUATION vs PULLBACK PATTERNS")
    print("=" * 78)

    for col_name, col in [('Immediate continuation', 'immediate_continuation'),
                           ('Immediate pullback', 'immediate_pullback')]:
        valid = df[col].dropna()
        if len(valid) == 0:
            continue
        yes_idx = valid[valid == True].index
        no_idx = valid[valid == False].index
        yes_df = df.loc[yes_idx]
        no_df = df.loc[no_idx]
        yes_wr = yes_df['winner'].mean() * 100 if len(yes_df) > 0 else 0
        no_wr = no_df['winner'].mean() * 100 if len(no_df) > 0 else 0
        print(f"  {col_name}:")
        print(f"    YES: N={len(yes_df):3d}  WR={yes_wr:.1f}%")
        print(f"    NO:  N={len(no_df):3d}  WR={no_wr:.1f}%")

    # ──────────────────────────────────────────────────────────────────
    #  DIRECTION BREAKDOWN
    # ──────────────────────────────────────────────────────────────────
    print("\n\n" + "=" * 78)
    print("  DIRECTION BREAKDOWN")
    print("=" * 78)

    for d, d_name in [(1, 'LONG'), (-1, 'SHORT')]:
        sub = df[df['direction'] == d]
        sw = sub[sub['winner'] == True]
        sl = sub[sub['winner'] == False]
        if len(sub) < 10:
            continue
        print(f"\n  {d_name}: N={len(sub)} (W={len(sw)}, L={len(sl)}, WR={len(sw)/len(sub)*100:.1f}%)")

        for col in ['cross_minute', 'vol_ratio_at_cross', 'bb_dist_pct',
                     'move_5m', 'move_15m', 'retrace_ratio', 'mfe_intra_hour']:
            if col not in sub.columns:
                continue
            w_med = sw[col].dropna().median()
            l_med = sl[col].dropna().median()
            if not np.isnan(w_med) and not np.isnan(l_med):
                print(f"    {col:<25s}: W={w_med:>8.4f}  L={l_med:>8.4f}  d={w_med - l_med:>+8.4f}")

    # ──────────────────────────────────────────────────────────────────
    #  EXIT REASON BREAKDOWN
    # ──────────────────────────────────────────────────────────────────
    print("\n\n" + "=" * 78)
    print("  EXIT REASON BREAKDOWN")
    print("=" * 78)

    for reason in sorted(df['exit_reason'].unique()):
        sub = df[df['exit_reason'] == reason]
        sw = sub[sub['winner'] == True]
        wr = len(sw) / len(sub) * 100 if len(sub) > 0 else 0
        print(f"  {reason:<15s}: N={len(sub):3d}  WR={wr:.1f}%  "
              f"cross_min_med={sub['cross_minute'].median():.0f}  "
              f"vol_ratio_med={sub['vol_ratio_at_cross'].median():.2f}")

    # ──────────────────────────────────────────────────────────────────
    #  POTENTIAL LOSER FILTERS
    # ──────────────────────────────────────────────────────────────────
    print("\n\n" + "=" * 78)
    print("  POTENTIAL LOSER FILTERS (Pre-entry)")
    print("=" * 78)

    baseline_wr = df['winner'].mean() * 100

    filters = [
        ('cross_minute <= 5',       df['cross_minute'] <= 5),
        ('cross_minute <= 10',      df['cross_minute'] <= 10),
        ('cross_minute <= 15',      df['cross_minute'] <= 15),
        ('cross_minute > 30',       df['cross_minute'] > 30),
        ('vol_ratio > 1.0',         df['vol_ratio_at_cross'] > 1.0),
        ('vol_ratio > 1.5',         df['vol_ratio_at_cross'] > 1.5),
        ('vol_ratio > 2.0',         df['vol_ratio_at_cross'] > 2.0),
        ('vol_ratio < 0.5',         df['vol_ratio_at_cross'] < 0.5),
        ('bb_dist > 0.2%',          df['bb_dist_pct'] > 0.2),
        ('bb_dist > 0.5%',          df['bb_dist_pct'] > 0.5),
        ('bb_dist > 1.0%',          df['bb_dist_pct'] > 1.0),
        ('bb_dist < 0.1%',          df['bb_dist_pct'] < 0.1),
        ('cross_candle_mom > 0',    df['cross_candle_momentum'] > 0),
        ('cross_candle_mom > 0.05', df['cross_candle_momentum'] > 0.05),
        ('pre_5m_trend > 0',        df['pre_cross_5m_trend'] > 0),
        ('immed. continuation',     df['immediate_continuation'] == True),
        ('no pullback',             df['immediate_pullback'] == False),
        ('re_crosses == 0',         df['re_crosses'] == 0),
        ('re_crosses <= 2',         df['re_crosses'] <= 2),
        ('retrace < 0.5',           df['retrace_ratio'] < 0.5),
        ('retrace < 1.0',           df['retrace_ratio'] < 1.0),
    ]

    print(f"\n  {'Filter':<30s} {'N':>5s}  {'WR%':>6s}  {'Base':>6s}  {'Lift':>6s}  {'Removed':>15s}")
    print("  " + "-" * 75)

    for name, mask in filters:
        valid_mask = mask.fillna(False) if hasattr(mask, 'fillna') else mask
        filtered = df[valid_mask]
        if len(filtered) < 10:
            continue
        wr = filtered['winner'].mean() * 100
        removed = df[~valid_mask]
        lr = len(removed[removed['winner'] == False])
        wr_rem = len(removed[removed['winner'] == True])
        lift = wr - baseline_wr
        print(f"  {name:<30s} {len(filtered):5d}  {wr:6.1f}  {baseline_wr:6.1f}  {lift:+6.1f}  "
              f"{lr:3d}L/{wr_rem:3d}W removed")

    # ──────────────────────────────────────────────────────────────────
    #  COMBINED FILTER CANDIDATES
    # ──────────────────────────────────────────────────────────────────
    print("\n\n" + "=" * 78)
    print("  COMBINED FILTER CANDIDATES")
    print("=" * 78)

    combos = [
        ('early(<10) + vol>1.0',
         (df['cross_minute'] <= 10) & (df['vol_ratio_at_cross'] > 1.0)),
        ('early(<10) + bb_dist>0.2',
         (df['cross_minute'] <= 10) & (df['bb_dist_pct'] > 0.2)),
        ('vol>1.5 + bb_dist>0.3',
         (df['vol_ratio_at_cross'] > 1.5) & (df['bb_dist_pct'] > 0.3)),
        ('early(<15) + vol>1.0 + cont',
         (df['cross_minute'] <= 15) & (df['vol_ratio_at_cross'] > 1.0) &
         (df['immediate_continuation'] == True)),
        ('no re-cross + continuation',
         (df['re_crosses'] == 0) & (df['immediate_continuation'] == True)),
        ('pos candle mom + retrace<1',
         (df['cross_candle_momentum'] > 0) & (df['retrace_ratio'] < 1.0)),
        ('vol>1.0 + retrace<1 + no_pb',
         (df['vol_ratio_at_cross'] > 1.0) & (df['retrace_ratio'] < 1.0) &
         (df['immediate_pullback'] == False)),
        ('early(<15) + bb_dist>0.2 + vol>1.0',
         (df['cross_minute'] <= 15) & (df['bb_dist_pct'] > 0.2) &
         (df['vol_ratio_at_cross'] > 1.0)),
    ]

    print(f"\n  {'Combo Filter':<42s} {'N':>5s}  {'WR%':>6s}  {'Lift':>6s}  {'Removed':>12s}")
    print("  " + "-" * 75)

    for name, mask in combos:
        valid_mask = mask.fillna(False) if hasattr(mask, 'fillna') else mask
        filtered = df[valid_mask]
        if len(filtered) < 5:
            continue
        wr = filtered['winner'].mean() * 100
        removed = df[~valid_mask]
        lr = len(removed[removed['winner'] == False])
        wr_rem = len(removed[removed['winner'] == True])
        lift = wr - baseline_wr
        print(f"  {name:<42s} {len(filtered):5d}  {wr:6.1f}  {lift:+6.1f}  "
              f"{lr:3d}L/{wr_rem:3d}W")

    # ──────────────────────────────────────────────────────────────────
    #  KEY FINDINGS SUMMARY (Effect Size Ranking)
    # ──────────────────────────────────────────────────────────────────
    print("\n\n" + "=" * 78)
    print("  KEY FINDINGS: Top Discriminating Features (Cohen's d)")
    print("=" * 78)

    feature_effects = []
    for col in ['cross_minute', 'vol_ratio_at_cross', 'bb_dist_pct',
                'cross_candle_momentum', 'pre_cross_5m_trend',
                'retrace_ratio', 'mfe_intra_hour', 'move_5m', 'move_10m',
                'move_15m', 'move_30m', 're_crosses', 'vol_5m_around_cross']:
        if col not in df.columns:
            continue
        w_vals = w[col].dropna()
        l_vals = l[col].dropna()
        if len(w_vals) < 5 or len(l_vals) < 5:
            continue
        pooled_std = np.sqrt((w_vals.var() + l_vals.var()) / 2)
        if pooled_std > 1e-10:
            effect = (w_vals.mean() - l_vals.mean()) / pooled_std
        else:
            effect = 0
        feature_effects.append((col, effect, w_vals.mean(), l_vals.mean()))

    feature_effects.sort(key=lambda x: abs(x[1]), reverse=True)

    print(f"\n  {'Feature':<28s} {'d':>8s}  {'Winners':>10s}  {'Losers':>10s}  {'Dir':>5s}")
    print("  " + "-" * 65)
    for col, effect, w_mean, l_mean in feature_effects:
        arrow = "W>L" if effect > 0 else "L>W"
        print(f"  {col:<28s} {effect:>+8.3f}   {w_mean:>10.4f}  {l_mean:>10.4f}  {arrow:>5s}")


if __name__ == '__main__':
    main()
