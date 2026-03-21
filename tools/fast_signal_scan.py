#!/usr/bin/env python3
"""
Fast Signal Scanner — In-process signal evaluation on hourly perp data.
========================================================================

Tests signal conditions directly as numpy masks against forward returns.
No subprocess overhead, no file I/O — 100x faster than pipeline for screening.

Computes: entries, hit rate (fwd return > 0), mean fwd return, t-stat, profit factor.
Uses BTC + top tokens for quick screening, then pipeline for full validation.

Usage:
    python tools/fast_signal_scan.py              # Scan all signals
    python tools/fast_signal_scan.py --months 6   # Last 6 months only
"""

import sys, os, time, warnings
import numpy as np
import pandas as pd
from pathlib import Path

warnings.filterwarnings("ignore")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from v4.engine import Engine, StrategyContext, CRISIS, DOWNTREND, UPTREND, RANGE, QUIET

# Forward return horizons (hours)
FWD_HORIZONS = [6, 12, 24, 48, 96]


def compute_forward_returns(close: np.ndarray, horizons: list) -> dict:
    """Compute forward returns for each horizon."""
    n = len(close)
    fwd = {}
    for h in horizons:
        ret = np.full(n, np.nan)
        if h < n:
            ret[:n-h] = (close[h:] - close[:n-h]) / close[:n-h]
        fwd[h] = ret
    return fwd


def evaluate_signal(entry_mask: np.ndarray, fwd_returns: dict,
                    direction: np.ndarray = None) -> dict:
    """Evaluate a signal's forward return statistics."""
    if direction is None:
        direction = np.ones(len(entry_mask), dtype=np.int8)

    entries = np.where(entry_mask)[0]
    n_entries = len(entries)

    if n_entries < 5:
        return {'entries': n_entries, 'skip': True}

    results = {'entries': n_entries, 'skip': False}

    for h, ret_arr in fwd_returns.items():
        valid = entries[~np.isnan(ret_arr[entries])]
        if len(valid) < 5:
            continue
        # Apply direction (short = negative return is good)
        rets = ret_arr[valid] * direction[valid]
        mean_ret = np.mean(rets) * 100  # percent
        hit_rate = np.mean(rets > 0) * 100
        std = np.std(rets)
        t_stat = mean_ret / (std * 100 / np.sqrt(len(valid))) if std > 0 else 0
        # Profit factor
        gains = rets[rets > 0].sum()
        losses = abs(rets[rets < 0].sum())
        pf = gains / losses if losses > 0 else (99.0 if gains > 0 else 0)
        results[f'h{h}_mean'] = mean_ret
        results[f'h{h}_hit'] = hit_rate
        results[f'h{h}_tstat'] = t_stat
        results[f'h{h}_pf'] = pf
        results[f'h{h}_n'] = len(valid)

    return results


# ===========================================================================
# Signal Library — Each returns (entry_mask, direction, name)
# ===========================================================================

def gen_signals(ctx: StrategyContext) -> list:
    """Generate all signal candidates from a context."""
    signals = []
    n = len(ctx.ind_1h['close'])
    close = ctx.ind_1h['close']
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']
    rsi = ctx.ind_1h['rsi']
    macd = ctx.ind_1h['macd']
    macd_sig = ctx.ind_1h['macd_signal']
    macd_hist = ctx.ind_1h['macd_hist']
    bb_upper = ctx.ind_1h['bb_upper']
    bb_lower = ctx.ind_1h['bb_lower']
    bb_pct = ctx.ind_1h['bb_pct']
    atr = ctx.ind_1h['atr']
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    ema10 = ctx.ind_1h['ema_10']
    ema20 = ctx.ind_1h['ema_20']
    ema50 = ctx.ind_1h['ema_50']
    vol_ratio = ctx.ind_1h['vol_ratio']
    ret_1 = ctx.ind_1h['ret_1']
    regime = ctx.regime_1h

    # Previous bar values
    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50
    macd_prev = np.roll(macd, 1); macd_prev[0] = 0
    macd_sig_prev = np.roll(macd_sig, 1); macd_sig_prev[0] = 0
    close_prev = np.roll(close, 1); close_prev[0] = close[0]
    bb_pct_prev = np.roll(bb_pct, 1); bb_pct_prev[0] = 0.5
    macd_hist_prev = np.roll(macd_hist, 1); macd_hist_prev[0] = 0
    adx_prev = np.roll(adx, 1); adx_prev[0] = 25

    warmup = np.ones(n, dtype=bool)
    warmup[:200] = False
    not_crisis = regime != CRISIS

    ones = np.ones(n, dtype=np.int8)
    neg_ones = -np.ones(n, dtype=np.int8)

    # --- MEAN REVERSION (LONG) ---

    # 1. RSI-MACD classic (s108 original)
    e = ((rsi < 35) & (rsi > rsi_prev)
         & (macd > macd_sig) & (macd_prev <= macd_sig_prev)
         & not_crisis & warmup)
    signals.append((e, ones, "rsi35_macd_cross_long"))

    # 2. RSI-MACD tight (s110 optimized)
    e = ((rsi < 30) & (rsi > rsi_prev)
         & (macd > macd_sig) & (macd_prev <= macd_sig_prev)
         & not_crisis & warmup)
    signals.append((e, ones, "rsi30_macd_cross_long"))

    # 3. RSI-MACD loose
    e = ((rsi < 40) & (rsi > rsi_prev)
         & (macd > macd_sig) & (macd_prev <= macd_sig_prev)
         & not_crisis & warmup)
    signals.append((e, ones, "rsi40_macd_cross_long"))

    # 4. Pure RSI bounce (no MACD req)
    e = ((rsi < 25) & (rsi > rsi_prev) & not_crisis & warmup)
    signals.append((e, ones, "rsi25_bounce_long"))

    e = ((rsi < 30) & (rsi > rsi_prev) & not_crisis & warmup)
    signals.append((e, ones, "rsi30_bounce_long"))

    e = ((rsi < 35) & (rsi > rsi_prev) & not_crisis & warmup)
    signals.append((e, ones, "rsi35_bounce_long"))

    # 5. BB lower band bounce
    e = ((bb_pct < 0) & (bb_pct > bb_pct_prev) & not_crisis & warmup)
    signals.append((e, ones, "bb_lower_bounce_long"))

    e = ((bb_pct < -0.1) & (bb_pct > bb_pct_prev) & not_crisis & warmup)
    signals.append((e, ones, "bb_deep_bounce_long"))

    # 6. BB + RSI double oversold
    e = ((bb_pct < 0) & (rsi < 35) & (rsi > rsi_prev) & not_crisis & warmup)
    signals.append((e, ones, "bb_rsi35_bounce_long"))

    e = ((bb_pct < 0) & (rsi < 30) & (rsi > rsi_prev) & not_crisis & warmup)
    signals.append((e, ones, "bb_rsi30_bounce_long"))

    # 7. ATR mean reversion (price far below EMA)
    dist = (close - ema50) / atr
    e = ((dist < -2.0) & (close > close_prev) & not_crisis & warmup)
    signals.append((e, ones, "atr_mr_2x_long"))

    e = ((dist < -1.5) & (close > close_prev) & not_crisis & warmup)
    signals.append((e, ones, "atr_mr_1.5x_long"))

    e = ((dist < -3.0) & (close > close_prev) & not_crisis & warmup)
    signals.append((e, ones, "atr_mr_3x_long"))

    # 8. MACD histogram reversal
    e = ((macd_hist > 0) & (macd_hist_prev < 0)
         & (rsi < 45) & not_crisis & warmup)
    signals.append((e, ones, "macd_hist_rev_rsi45_long"))

    e = ((macd_hist > 0) & (macd_hist_prev < 0)
         & (rsi < 40) & not_crisis & warmup)
    signals.append((e, ones, "macd_hist_rev_rsi40_long"))

    # 9. Volume climax reversal
    e = ((vol_ratio > 2.0) & (ret_1 < -0.02) & (rsi < 35) & not_crisis & warmup)
    signals.append((e, ones, "vol_climax_rsi35_long"))

    e = ((vol_ratio > 3.0) & (ret_1 < -0.03) & (rsi < 40) & not_crisis & warmup)
    signals.append((e, ones, "vol_climax_deep_long"))

    # 10. RSI double bounce (RSI was < 30, dropped back, rising again)
    rsi_prev2 = np.roll(rsi, 2); rsi_prev2[:2] = 50
    e = ((rsi < 35) & (rsi > rsi_prev) & (rsi_prev < rsi_prev2)
         & not_crisis & warmup)
    signals.append((e, ones, "rsi_double_dip_long"))

    # 11. EMA fan-out reversal (bear trend exhaustion)
    bear_trend = (ema10 < ema20) & (ema20 < ema50)
    ema_gap = (ema50 - ema10) / atr
    e = (bear_trend & (ema_gap > 1.5) & (rsi < 35) & (rsi > rsi_prev)
         & not_crisis & warmup)
    signals.append((e, ones, "ema_fan_rsi35_long"))

    # 12. ADX declining from high (trend exhaustion)
    e = ((adx < adx_prev) & (adx_prev > 30) & (rsi < 40) & (rsi > rsi_prev)
         & not_crisis & warmup)
    signals.append((e, ones, "adx_exhaust_rsi40_long"))

    # --- MEAN REVERSION (SHORT) ---

    # 13. RSI overbought + MACD bearish cross
    e = ((rsi > 70) & (rsi < rsi_prev)
         & (macd < macd_sig) & (macd_prev >= macd_sig_prev)
         & not_crisis & warmup)
    signals.append((e, neg_ones, "rsi70_macd_cross_short"))

    e = ((rsi > 65) & (rsi < rsi_prev)
         & (macd < macd_sig) & (macd_prev >= macd_sig_prev)
         & not_crisis & warmup)
    signals.append((e, neg_ones, "rsi65_macd_cross_short"))

    # 14. BB upper band rejection
    e = ((bb_pct > 1.0) & (bb_pct < bb_pct_prev) & not_crisis & warmup)
    signals.append((e, neg_ones, "bb_upper_reject_short"))

    # 15. RSI overbought bounce
    e = ((rsi > 75) & (rsi < rsi_prev) & not_crisis & warmup)
    signals.append((e, neg_ones, "rsi75_reject_short"))

    e = ((rsi > 70) & (rsi < rsi_prev) & not_crisis & warmup)
    signals.append((e, neg_ones, "rsi70_reject_short"))

    # 16. ATR mean reversion short
    e = ((dist > 2.0) & (close < close_prev) & not_crisis & warmup)
    signals.append((e, neg_ones, "atr_mr_2x_short"))

    # --- TREND FOLLOWING ---

    # 17. EMA alignment + ADX
    e = ((ema10 > ema20) & (ema20 > ema50) & (adx > 25)
         & (regime != CRISIS) & warmup)
    # Reduce to entries (only fire once per alignment start)
    ema_bull = (ema10 > ema20) & (ema20 > ema50) & (adx > 25)
    ema_bull_prev = np.roll(ema_bull.astype(np.int8), 1); ema_bull_prev[0] = 0
    e = (ema_bull & ~ema_bull_prev.astype(bool) & not_crisis & warmup)
    signals.append((e, ones, "ema_align_adx25_long"))

    # 18. EMA bear alignment entry
    ema_bear = (ema10 < ema20) & (ema20 < ema50) & (adx > 25)
    ema_bear_prev = np.roll(ema_bear.astype(np.int8), 1); ema_bear_prev[0] = 0
    e = (ema_bear & ~ema_bear_prev.astype(bool) & not_crisis & warmup)
    signals.append((e, neg_ones, "ema_align_adx25_short"))

    # 19. MACD cross above zero + trend
    e = ((macd > 0) & (macd_prev <= 0) & (ema10 > ema20)
         & not_crisis & warmup)
    signals.append((e, ones, "macd_zero_cross_long"))

    e = ((macd < 0) & (macd_prev >= 0) & (ema10 < ema20)
         & not_crisis & warmup)
    signals.append((e, neg_ones, "macd_zero_cross_short"))

    # --- FUNDING-BASED (perp-specific) ---
    if ctx.funding_1h is not None:
        fr = ctx.funding_1h
        fr_prev = np.roll(fr, 1); fr_prev[0] = 0
        # Rolling 24h funding
        fr_24h = pd.Series(fr).rolling(24, min_periods=1).sum().values

        # 20. Extreme negative funding → long (short squeeze setup)
        e = ((fr_24h < -0.001) & (rsi < 40) & (rsi > rsi_prev)
             & not_crisis & warmup)
        signals.append((e, ones, "funding_neg_rsi40_long"))

        e = ((fr_24h < -0.002) & not_crisis & warmup)
        signals.append((e, ones, "funding_deep_neg_long"))

        # 21. Extreme positive funding → short
        e = ((fr_24h > 0.002) & (rsi > 60) & not_crisis & warmup)
        signals.append((e, neg_ones, "funding_pos_rsi60_short"))

        # 22. Funding flip signals
        e = ((fr > 0) & (fr_prev <= 0) & (rsi < 40) & not_crisis & warmup)
        signals.append((e, ones, "funding_flip_pos_long"))

        e = ((fr < 0) & (fr_prev >= 0) & (rsi > 60) & not_crisis & warmup)
        signals.append((e, neg_ones, "funding_flip_neg_short"))

    # --- REGIME-BASED ---

    # 23. Regime transition signals
    regime_prev = np.roll(regime, 1); regime_prev[0] = RANGE
    e = ((regime == UPTREND) & (regime_prev != UPTREND) & warmup)
    signals.append((e, ones, "regime_to_uptrend_long"))

    e = ((regime == DOWNTREND) & (regime_prev != DOWNTREND) & warmup)
    signals.append((e, neg_ones, "regime_to_downtrend_short"))

    # 24. Range-bound RSI reversal
    e = ((regime == RANGE) & (rsi < 30) & (rsi > rsi_prev) & warmup)
    signals.append((e, ones, "range_rsi30_long"))

    e = ((regime == RANGE) & (rsi > 70) & (rsi < rsi_prev) & warmup)
    signals.append((e, neg_ones, "range_rsi70_short"))

    # --- COMPOSITE SIGNALS ---

    # 25. Triple confirmation long
    e = ((rsi < 35) & (rsi > rsi_prev)
         & (bb_pct < 0.2)
         & (macd_hist > macd_hist_prev)
         & not_crisis & warmup)
    signals.append((e, ones, "triple_confirm_long"))

    # 26. Capitulation buy
    e = ((ret_1 < -0.05) & (vol_ratio > 2.0) & (rsi < 25)
         & not_crisis & warmup)
    signals.append((e, ones, "capitulation_buy_long"))

    # 27. DI crossover
    plus_di_prev = np.roll(plus_di, 1); plus_di_prev[0] = 20
    minus_di_prev = np.roll(minus_di, 1); minus_di_prev[0] = 20
    e = ((plus_di > minus_di) & (plus_di_prev <= minus_di_prev)
         & (adx > 20) & not_crisis & warmup)
    signals.append((e, ones, "di_cross_long"))

    e = ((minus_di > plus_di) & (minus_di_prev <= plus_di_prev)
         & (adx > 20) & not_crisis & warmup)
    signals.append((e, neg_ones, "di_cross_short"))

    # 28. Multi-timeframe RSI bounce
    if hasattr(ctx, 'ind_4h') and 'rsi' in ctx.ind_4h:
        rsi_4h = ctx.align_4h_to_1h(ctx.ind_4h['rsi'])
        e = ((rsi < 30) & (rsi > rsi_prev) & (rsi_4h < 40)
             & not_crisis & warmup)
        signals.append((e, ones, "mtf_rsi_bounce_long"))

    # 29. Donchian channel signals
    donch_high = ctx.ind_1h['donch_high']
    donch_low = ctx.ind_1h['donch_low']
    donch_mid = (donch_high + donch_low) / 2

    e = ((close <= donch_low * 1.001) & (close > close_prev)
         & not_crisis & warmup)
    signals.append((e, ones, "donch_low_bounce_long"))

    e = ((close >= donch_high * 0.999) & (close < close_prev)
         & not_crisis & warmup)
    signals.append((e, neg_ones, "donch_high_reject_short"))

    # 30. Volatility contraction breakout
    bb_width = ctx.ind_1h['bb_width']
    bb_width_prev = np.roll(bb_width, 1); bb_width_prev[0] = bb_width[0]
    bb_w_20 = pd.Series(bb_width).rolling(20, min_periods=1).mean().values
    e = ((bb_width < bb_w_20 * 0.7) & (close > ema20) & (adx > 15)
         & not_crisis & warmup)
    signals.append((e, ones, "vol_squeeze_long"))

    e = ((bb_width < bb_w_20 * 0.7) & (close < ema20) & (adx > 15)
         & not_crisis & warmup)
    signals.append((e, neg_ones, "vol_squeeze_short"))

    return signals


def scan_token(eng, ticker, months=6):
    """Scan all signals on one token."""
    # Check perp 1h cache first (our primary market)
    cache_dir = PROJECT_ROOT / "data" / "perp" / "1h_cache"
    fname = cache_dir / f"{ticker}_1h.parquet"
    if not fname.exists():
        # Try spot cache
        fname = PROJECT_ROOT / "data" / "spot" / "1h_cache" / f"{ticker}_1h.parquet"
        if not fname.exists():
            return []

    df = pd.read_parquet(fname)
    if months > 0:
        cutoff = pd.Timestamp.now(tz='UTC') - pd.Timedelta(days=months * 30)
        # Handle tz-naive indexes
        if df.index.tz is None:
            cutoff = cutoff.tz_localize(None)
        df = df[df.index >= cutoff]

    ctx = eng._build_context(ticker, df, market_override='perp')
    if ctx is None:
        return []

    close = ctx.ind_1h['close']
    fwd_rets = compute_forward_returns(close, FWD_HORIZONS)
    signals = gen_signals(ctx)

    results = []
    for entry_mask, direction, name in signals:
        r = evaluate_signal(entry_mask, fwd_rets, direction)
        r['signal'] = name
        r['token'] = ticker
        if not r.get('skip', True):
            results.append(r)
    return results


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--months', type=int, default=6)
    parser.add_argument('--tokens', type=str, default='BTC,ETH,SOL,BNB,XRP,DOGE,AVAX,LINK,ADA,DOT')
    args = parser.parse_args()

    tokens = args.tokens.split(',')
    eng = Engine(data_dir=str(PROJECT_ROOT / 'data'))

    print(f"Fast Signal Scan — {len(tokens)} tokens, {args.months}mo window")
    print(f"{'='*120}")

    all_results = []
    for tok in tokens:
        t0 = time.perf_counter()
        results = scan_token(eng, tok, args.months)
        elapsed = time.perf_counter() - t0
        all_results.extend(results)
        print(f"  {tok:6s}: {len(results):3d} signals evaluated in {elapsed:.1f}s")

    if not all_results:
        print("\nNo results — check data paths.")
        return

    # Aggregate across tokens
    df = pd.DataFrame(all_results)
    agg = df.groupby('signal').agg({
        'entries': 'sum',
        'h24_mean': 'mean',
        'h24_hit': 'mean',
        'h24_tstat': 'mean',
        'h24_pf': 'mean',
    }).dropna()

    agg = agg.sort_values('h24_pf', ascending=False)

    print(f"\n{'='*120}")
    print(f"  TOP SIGNALS by 24h Forward Profit Factor (avg across {len(tokens)} tokens)")
    print(f"{'='*120}")
    print(f"  {'Signal':<35s} {'Entries':>8s} {'24h Mean%':>10s} {'24h Hit%':>9s} {'24h t-stat':>10s} {'24h PF':>8s}")
    print(f"  {'-'*35} {'-'*8} {'-'*10} {'-'*9} {'-'*10} {'-'*8}")

    for name, row in agg.head(30).iterrows():
        entries = int(row['entries'])
        mean_r = row['h24_mean']
        hit = row['h24_hit']
        tstat = row['h24_tstat']
        pf = row['h24_pf']
        flag = " ***" if pf > 1.3 and entries > 50 and tstat > 1.0 else ""
        print(f"  {name:<35s} {entries:8d} {mean_r:10.3f} {hit:9.1f} {tstat:10.2f} {pf:8.2f}{flag}")

    # Also show by horizon for top signals
    top_sigs = list(agg.head(10).index)
    horizons_to_show = [6, 12, 24, 48, 96]

    print(f"\n{'='*120}")
    print(f"  TOP 10 SIGNALS — Multi-Horizon Detail")
    print(f"{'='*120}")
    for sig in top_sigs:
        sig_data = df[df['signal'] == sig]
        total_entries = sig_data['entries'].sum()
        print(f"\n  {sig} ({total_entries} entries across {len(sig_data)} tokens)")
        for h in horizons_to_show:
            col_mean = f'h{h}_mean'
            col_hit = f'h{h}_hit'
            col_pf = f'h{h}_pf'
            if col_mean in sig_data.columns:
                m = sig_data[col_mean].mean()
                hr = sig_data[col_hit].mean()
                pf = sig_data[col_pf].mean()
                print(f"    {h:3d}h: mean={m:+.3f}% hit={hr:.1f}% PF={pf:.2f}")

    # Save results
    out_dir = PROJECT_ROOT / "results" / "v4"
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_json(out_dir / "fast_signal_scan.json", orient='records', indent=2)
    print(f"\n  Full results: {out_dir / 'fast_signal_scan.json'}")


if __name__ == '__main__':
    main()
