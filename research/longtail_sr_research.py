#!/workspace/venv/bin/python
"""
Long-Tail Altcoin & Support/Resistance Research
=================================================

Research questions:
  1. Universe analysis: categorize ALL tokens by ADV tier, compute return/vol/DD stats
  2. Small-cap momentum: test ATR breakout, volume surge signals on top small/mid caps
  3. Support/Resistance: swing high/low detection, test buy-at-support / sell-at-resistance
  4. Tiered strategy framework: different logic per market cap tier, theoretical max return

Data: spot/1h_cache (95 tokens) + perp/1h_cache (195 tokens)
"""

import pandas as pd
import numpy as np
import warnings
from pathlib import Path
from collections import defaultdict

warnings.filterwarnings('ignore')

BASE_DIR = Path('/workspace/crypto_backtest')
SPOT_DIR = BASE_DIR / 'data' / 'spot' / '1h_cache'
PERP_DIR = BASE_DIR / 'data' / 'perp' / '1h_cache'

HOURS_PER_YEAR = 365.25 * 24
HOURS_PER_DAY = 24
COST_BPS = 10
COST_FRAC = COST_BPS / 10_000

# ADV tier thresholds (using ADV as market cap proxy)
TIER_THRESHOLDS = {
    'large':  100_000_000,  # >$100M ADV
    'mid':     10_000_000,  # $10M-$100M
    'small':    1_000_000,  # $1M-$10M
    'micro':           0,   # <$1M
}


def separator(title):
    width = 80
    print(f"\n{'='*width}")
    print(f"  {title}")
    print(f"{'='*width}\n")


# ============================================================================
# PART 1: UNIVERSE ANALYSIS
# ============================================================================

def load_token_data(path):
    """Load a single token parquet, return DataFrame with close/volume."""
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    df.index.name = 'timestamp'
    return df


def compute_adv(close, volume, lookback_days=30):
    """Compute Average Daily Volume in USD (median over lookback)."""
    dollar_volume = close * volume
    bars_per_day = 24
    lookback_bars = lookback_days * bars_per_day

    if len(dollar_volume) < bars_per_day * 3:
        return 0.0

    recent = dollar_volume.iloc[-lookback_bars:] if len(dollar_volume) > lookback_bars else dollar_volume
    n_complete_days = len(recent) // bars_per_day
    if n_complete_days < 3:
        return 0.0

    trimmed = recent.values[-(n_complete_days * bars_per_day):]
    daily_volumes = trimmed.reshape(n_complete_days, bars_per_day).sum(axis=1)
    return float(np.median(daily_volumes))


def classify_tier(adv):
    """Classify ADV into tier string."""
    if adv >= TIER_THRESHOLDS['large']:
        return 'large'
    elif adv >= TIER_THRESHOLDS['mid']:
        return 'mid'
    elif adv >= TIER_THRESHOLDS['small']:
        return 'small'
    else:
        return 'micro'


def compute_token_stats(df, lookback_hours=365*24):
    """Compute return/vol/drawdown stats over the last ~12 months."""
    if len(df) < 100:
        return None

    # Use last 12 months of data (or all if less)
    cutoff = min(lookback_hours, len(df))
    recent = df.iloc[-cutoff:]
    close = recent['close'].values.astype(np.float64)

    if len(close) < 48 or close[0] <= 0 or np.any(close <= 0):
        return None

    # Returns
    hourly_returns = np.diff(close) / close[:-1]
    total_return = (close[-1] / close[0]) - 1.0

    # Annualized vol
    ann_vol = np.std(hourly_returns) * np.sqrt(HOURS_PER_YEAR)

    # Max drawdown
    cummax = np.maximum.accumulate(close)
    drawdown = (close - cummax) / cummax
    max_dd = float(np.min(drawdown))

    # Annualized return
    hours = len(close)
    years = hours / HOURS_PER_YEAR
    ann_return = (1 + total_return) ** (1 / max(years, 0.01)) - 1 if total_return > -1 else -1.0

    # Sharpe (annualized)
    sharpe = ann_return / ann_vol if ann_vol > 0 else 0.0

    return {
        'total_return': total_return,
        'ann_return': ann_return,
        'ann_vol': ann_vol,
        'max_dd': max_dd,
        'sharpe': sharpe,
        'hours': hours,
    }


def scan_universe(data_dir, market_name):
    """Scan all tokens in a data directory, compute ADV + stats."""
    results = []
    files = sorted(data_dir.glob('*_1h.parquet'))

    for f in files:
        token = f.stem.replace('_1h', '')
        try:
            df = load_token_data(f)
            if len(df) < 48:
                continue

            adv = compute_adv(df['close'], df['volume'])
            tier = classify_tier(adv)
            stats = compute_token_stats(df)

            if stats is None:
                continue

            results.append({
                'token': token,
                'market': market_name,
                'adv': adv,
                'tier': tier,
                'n_days': len(df) / 24,
                **stats,
            })
        except Exception as e:
            continue

    return pd.DataFrame(results)


def print_universe_analysis(spot_df, perp_df):
    """Print comprehensive universe analysis."""
    separator("PART 1: LONG-TAIL UNIVERSE ANALYSIS")

    for name, df in [('SPOT', spot_df), ('PERP', perp_df)]:
        print(f"\n--- {name} MARKET ({len(df)} tokens) ---\n")

        for tier in ['large', 'mid', 'small', 'micro']:
            tdf = df[df['tier'] == tier]
            if len(tdf) == 0:
                print(f"  {tier.upper()} (>{TIER_THRESHOLDS.get(tier, 0)/1e6:.0f}M ADV): 0 tokens")
                continue

            print(f"  {tier.upper()} ({len(tdf)} tokens):")
            print(f"    ADV range:       ${tdf['adv'].min()/1e6:.1f}M - ${tdf['adv'].max()/1e6:.1f}M")
            print(f"    Avg 12mo return: {tdf['total_return'].mean()*100:.1f}%")
            print(f"    Median return:   {tdf['total_return'].median()*100:.1f}%")
            print(f"    Avg ann vol:     {tdf['ann_vol'].mean()*100:.1f}%")
            print(f"    Avg max DD:      {tdf['max_dd'].mean()*100:.1f}%")
            print(f"    Best performer:  {tdf.loc[tdf['total_return'].idxmax(), 'token']} "
                  f"({tdf['total_return'].max()*100:.0f}%)")
            if tdf['total_return'].min() < 0:
                print(f"    Worst performer: {tdf.loc[tdf['total_return'].idxmin(), 'token']} "
                      f"({tdf['total_return'].min()*100:.0f}%)")
            print(f"    Avg Sharpe:      {tdf['sharpe'].mean():.2f}")
            print()

    # Cross-tier alpha comparison
    combined = pd.concat([spot_df, perp_df]).drop_duplicates(subset='token', keep='first')
    print("\n--- CROSS-TIER ALPHA COMPARISON (combined unique tokens) ---\n")
    tier_summary = combined.groupby('tier').agg({
        'total_return': ['mean', 'median', 'std', 'max', 'min', 'count'],
        'ann_vol': 'mean',
        'max_dd': 'mean',
        'sharpe': 'mean',
    }).round(4)
    for tier in ['large', 'mid', 'small', 'micro']:
        tdf = combined[combined['tier'] == tier]
        if len(tdf) == 0:
            continue
        print(f"  {tier.upper():6s}: N={len(tdf):3d}  "
              f"AvgRet={tdf['total_return'].mean()*100:+7.1f}%  "
              f"MedRet={tdf['total_return'].median()*100:+7.1f}%  "
              f"AvgVol={tdf['ann_vol'].mean()*100:5.0f}%  "
              f"AvgDD={tdf['max_dd'].mean()*100:6.1f}%  "
              f"Sharpe={tdf['sharpe'].mean():+.2f}")

    # Position sizing constraints per tier
    print("\n\n--- REALISTIC POSITION SIZING PER TIER (5% ADV constraint) ---\n")
    print(f"  {'Tier':6s}  {'Median ADV':>12s}  {'5% of ADV':>12s}  {'Practical Sizing':>18s}")
    print(f"  {'-'*6}  {'-'*12}  {'-'*12}  {'-'*18}")
    for tier in ['large', 'mid', 'small', 'micro']:
        tdf = combined[combined['tier'] == tier]
        if len(tdf) == 0:
            continue
        med_adv = tdf['adv'].median()
        pct5 = med_adv * 0.05
        sizing_note = ""
        if pct5 > 50_000:
            sizing_note = "Full position OK"
        elif pct5 > 5_000:
            sizing_note = "Moderate, cap needed"
        elif pct5 > 500:
            sizing_note = "Tiny only"
        else:
            sizing_note = "Untradeable at scale"
        print(f"  {tier:6s}  ${med_adv/1e6:10.2f}M  ${pct5/1e3:10.1f}K  {sizing_note:>18s}")

    # Survivorship bias check
    print("\n\n--- SURVIVORSHIP BIAS CHECK ---\n")
    for name, df_check in [('SPOT', spot_df), ('PERP', perp_df)]:
        tdf = df_check.copy()
        near_zero = tdf[tdf['total_return'] < -0.90]
        heavy_loss = tdf[tdf['total_return'] < -0.70]
        print(f"  {name}:")
        print(f"    Tokens losing >90%:  {len(near_zero)} ({len(near_zero)/len(tdf)*100:.0f}%)")
        if len(near_zero) > 0:
            for _, row in near_zero.iterrows():
                print(f"      {row['token']:>10s}: {row['total_return']*100:+.0f}%  "
                      f"(ADV ${row['adv']/1e6:.2f}M)")
        print(f"    Tokens losing >70%:  {len(heavy_loss)} ({len(heavy_loss)/len(tdf)*100:.0f}%)")
        if len(heavy_loss) > 0:
            for _, row in heavy_loss.iterrows():
                print(f"      {row['token']:>10s}: {row['total_return']*100:+.0f}%  "
                      f"(ADV ${row['adv']/1e6:.2f}M)")
        print()

    return combined


# ============================================================================
# PART 2: SMALL-CAP MOMENTUM STRATEGIES
# ============================================================================

def compute_atr(high, low, close, period=14):
    """Compute Average True Range."""
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)

    tr = np.zeros(len(h))
    tr[0] = h[0] - l[0]
    for i in range(1, len(h)):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))

    atr = np.full(len(h), np.nan)
    atr[period-1] = np.mean(tr[:period])
    alpha = 1.0 / period
    for i in range(period, len(h)):
        atr[i] = atr[i-1] * (1 - alpha) + tr[i] * alpha

    return atr


def compute_volume_sma(volume, period=20):
    """Compute simple moving average of volume."""
    v = np.asarray(volume, dtype=np.float64)
    sma = np.full(len(v), np.nan)
    if len(v) < period:
        return sma
    cumsum = np.cumsum(v)
    sma[period-1:] = (cumsum[period-1:] - np.concatenate([[0], cumsum[:-period]])) / period
    return sma


def test_momentum_breakout(df, atr_mult=2.0, vol_mult=2.0, hold_bars=72,
                           stop_atr_mult=1.5):
    """
    ATR breakout + volume surge strategy.
    Entry: close > prev_close + atr_mult * ATR AND volume > vol_mult * vol_sma
    Exit: after hold_bars OR stop loss at entry - stop_atr_mult * ATR
    Returns equity curve and trade stats.
    """
    close = df['close'].values.astype(np.float64)
    high = df['high'].values.astype(np.float64)
    low = df['low'].values.astype(np.float64)
    volume = df['volume'].values.astype(np.float64)

    n = len(close)
    if n < 500:
        return None

    atr = compute_atr(high, low, close, period=14)
    vol_sma = compute_volume_sma(volume * close, period=20)  # dollar volume SMA

    # Signals
    equity = np.ones(n)
    position = 0.0
    entry_price = 0.0
    entry_bar = 0
    stop_price = 0.0
    trades = []

    warmup = 100  # skip first 100 bars

    for i in range(warmup, n):
        if np.isnan(atr[i]) or np.isnan(vol_sma[i]) or vol_sma[i] <= 0:
            equity[i] = equity[i-1]
            continue

        if position == 0:
            # Entry signal: price breakout + volume surge
            price_breakout = close[i] > close[i-1] + atr_mult * atr[i]
            dollar_vol = volume[i] * close[i]
            vol_surge = dollar_vol > vol_mult * vol_sma[i]

            if price_breakout and vol_surge:
                position = 1.0
                entry_price = close[i]
                entry_bar = i
                stop_price = entry_price - stop_atr_mult * atr[i]
                equity[i] = equity[i-1]
            else:
                equity[i] = equity[i-1]
        else:
            # In position
            ret = (close[i] - close[i-1]) / close[i-1]
            equity[i] = equity[i-1] * (1 + ret)

            # Exit conditions
            bars_held = i - entry_bar
            hit_stop = close[i] <= stop_price
            hit_time = bars_held >= hold_bars

            if hit_stop or hit_time:
                trade_ret = (close[i] / entry_price) - 1.0 - COST_FRAC * 2
                trades.append({
                    'entry_bar': entry_bar,
                    'exit_bar': i,
                    'bars_held': bars_held,
                    'return': trade_ret,
                    'exit_reason': 'stop' if hit_stop else 'time',
                })
                position = 0.0
                entry_price = 0.0

    if len(trades) == 0:
        return None

    trade_df = pd.DataFrame(trades)
    win_rate = (trade_df['return'] > 0).mean()
    avg_win = trade_df.loc[trade_df['return'] > 0, 'return'].mean() if win_rate > 0 else 0
    avg_loss = trade_df.loc[trade_df['return'] <= 0, 'return'].mean() if win_rate < 1 else 0

    return {
        'n_trades': len(trades),
        'win_rate': win_rate,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'total_return': trade_df['return'].sum(),
        'best_trade': trade_df['return'].max(),
        'worst_trade': trade_df['return'].min(),
        'avg_bars_held': trade_df['bars_held'].mean(),
        'profit_factor': abs(trade_df.loc[trade_df['return'] > 0, 'return'].sum() /
                             trade_df.loc[trade_df['return'] <= 0, 'return'].sum())
                         if trade_df.loc[trade_df['return'] <= 0, 'return'].sum() != 0 else float('inf'),
    }


def detect_10x_precursors(df, threshold=5.0):
    """
    Look for signals that precede major (>threshold x) rallies.
    Returns list of rally events with pre-rally characteristics.
    """
    close = df['close'].values.astype(np.float64)
    volume = df['volume'].values.astype(np.float64)
    n = len(close)

    if n < 500:
        return []

    # Find major rally starts: 30-day rolling min to max ratio
    events = []
    window = 30 * 24  # 30 days

    for i in range(window, n - window):
        # Forward 30-day max
        fwd_max = np.max(close[i:i+window])
        if fwd_max / close[i] >= threshold:
            # This bar precedes a major rally
            # Compute pre-rally characteristics (lookback 7 days)
            lb = 7 * 24
            if i < lb:
                continue

            pre_close = close[i-lb:i]
            pre_vol = volume[i-lb:i] * close[i-lb:i]

            # Characteristics
            pre_return = (close[i] / close[i-lb]) - 1
            pre_vol_ratio = np.mean(pre_vol[-24:]) / np.mean(pre_vol[:-24]) if np.mean(pre_vol[:-24]) > 0 else 1
            pre_vol_std = np.std(np.diff(pre_close) / pre_close[:-1])

            events.append({
                'bar': i,
                'price': close[i],
                'rally_peak': fwd_max,
                'rally_multiple': fwd_max / close[i],
                'pre_7d_return': pre_return,
                'pre_vol_ratio': pre_vol_ratio,  # last day vs prior 6 days
                'pre_volatility': pre_vol_std,
            })

    # Deduplicate: keep only the first event in each cluster (within 7 days)
    if len(events) == 0:
        return []

    deduped = [events[0]]
    for e in events[1:]:
        if e['bar'] - deduped[-1]['bar'] > 7 * 24:
            deduped.append(e)

    return deduped


def run_small_cap_momentum(combined_df):
    """Run momentum tests on top small/mid cap tokens."""
    separator("PART 2: SMALL-CAP MOMENTUM STRATEGIES")

    # Select top 20 small/mid caps by recent return
    eligible = combined_df[combined_df['tier'].isin(['small', 'mid'])].copy()
    eligible = eligible.sort_values('total_return', ascending=False)
    top20 = eligible.head(20)

    print(f"Top 20 small/mid cap tokens by 12mo return:\n")
    print(f"  {'Token':>10s}  {'Tier':>6s}  {'ADV':>10s}  {'12mo Ret':>10s}  {'Vol':>8s}  {'MaxDD':>8s}")
    print(f"  {'-'*10}  {'-'*6}  {'-'*10}  {'-'*10}  {'-'*8}  {'-'*8}")
    for _, row in top20.iterrows():
        print(f"  {row['token']:>10s}  {row['tier']:>6s}  "
              f"${row['adv']/1e6:8.2f}M  {row['total_return']*100:+8.1f}%  "
              f"{row['ann_vol']*100:6.0f}%  {row['max_dd']*100:6.1f}%")

    # Test ATR breakout on each
    print(f"\n\n--- ATR BREAKOUT + VOLUME SURGE RESULTS ---\n")
    print(f"  {'Token':>10s}  {'#Trades':>8s}  {'WinRate':>8s}  {'AvgWin':>8s}  {'AvgLoss':>8s}  "
          f"{'TotRet':>8s}  {'BestTrade':>10s}  {'PF':>6s}")
    print(f"  {'-'*10}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*10}  {'-'*6}")

    all_results = []
    for _, row in top20.iterrows():
        token = row['token']
        market = row['market']
        data_dir = SPOT_DIR if market == 'spot' else PERP_DIR
        fpath = data_dir / f'{token}_1h.parquet'
        if not fpath.exists():
            continue

        df = load_token_data(fpath)
        result = test_momentum_breakout(df)

        if result is not None:
            result['token'] = token
            result['tier'] = row['tier']
            all_results.append(result)
            print(f"  {token:>10s}  {result['n_trades']:>8d}  {result['win_rate']:>7.1%}  "
                  f"{result['avg_win']:>+7.1%}  {result['avg_loss']:>+7.1%}  "
                  f"{result['total_return']:>+7.1%}  {result['best_trade']:>+9.1%}  "
                  f"{result['profit_factor']:>5.2f}")
        else:
            print(f"  {token:>10s}  {'N/A':>8s}  (insufficient data or no trades)")

    if all_results:
        res_df = pd.DataFrame(all_results)
        print(f"\n  Summary across {len(res_df)} tested tokens:")
        print(f"    Avg win rate:      {res_df['win_rate'].mean():.1%}")
        print(f"    Avg total return:  {res_df['total_return'].mean():+.1%}")
        print(f"    Tokens profitable: {(res_df['total_return'] > 0).sum()}/{len(res_df)}")
        best = res_df.loc[res_df['total_return'].idxmax()]
        print(f"    Best performer:    {best['token']} ({best['total_return']:+.1%} over {best['n_trades']} trades)")

    # 10x move detection
    print(f"\n\n--- 10X MOVE PRECURSOR ANALYSIS ---\n")
    print("  Scanning for tokens with >5x rallies and their pre-rally signals...\n")

    rally_events_all = []
    for _, row in eligible.iterrows():
        token = row['token']
        market = row['market']
        data_dir = SPOT_DIR if market == 'spot' else PERP_DIR
        fpath = data_dir / f'{token}_1h.parquet'
        if not fpath.exists():
            continue

        df = load_token_data(fpath)
        events = detect_10x_precursors(df, threshold=5.0)

        for e in events:
            e['token'] = token
            rally_events_all.append(e)

    if rally_events_all:
        rally_df = pd.DataFrame(rally_events_all)
        print(f"  Found {len(rally_df)} rally events (>5x in 30 days) across "
              f"{rally_df['token'].nunique()} tokens\n")

        # Aggregate precursor characteristics
        print(f"  Pre-rally characteristics (7 days before):")
        print(f"    Avg 7d return:       {rally_df['pre_7d_return'].mean()*100:+.1f}%  "
              f"(median {rally_df['pre_7d_return'].median()*100:+.1f}%)")
        print(f"    Avg volume ratio:    {rally_df['pre_vol_ratio'].mean():.2f}x  "
              f"(last day vs prior 6 days)")
        print(f"    Avg pre-volatility:  {rally_df['pre_volatility'].mean()*100:.2f}%")
        print(f"\n  Top rally events:")
        top_rallies = rally_df.nlargest(10, 'rally_multiple')
        for _, e in top_rallies.iterrows():
            print(f"    {e['token']:>10s}: {e['rally_multiple']:.1f}x  "
                  f"pre-7d-ret={e['pre_7d_return']*100:+.1f}%  "
                  f"vol-ratio={e['pre_vol_ratio']:.2f}x")
    else:
        print("  No 5x+ rally events found in the data.")

    return all_results


# ============================================================================
# PART 3: SUPPORT/RESISTANCE STRATEGY RESEARCH
# ============================================================================

def detect_swing_points(high, low, close, n_bars=48):
    """
    Detect swing highs and lows over N bars.
    Swing high: highest high in window of 2*n_bars+1 centered on the point
    Swing low: lowest low in window of 2*n_bars+1 centered on the point

    Returns arrays of resistance levels and support levels.
    """
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    n = len(h)

    swing_highs = []  # (bar_index, price)
    swing_lows = []

    for i in range(n_bars, n - n_bars):
        # Window around point
        window_high = h[i - n_bars:i + n_bars + 1]
        window_low = l[i - n_bars:i + n_bars + 1]

        if h[i] == np.max(window_high):
            swing_highs.append((i, h[i]))
        if l[i] == np.min(window_low):
            swing_lows.append((i, l[i]))

    return swing_highs, swing_lows


def cluster_levels(levels, tolerance_pct=0.02):
    """Cluster nearby price levels within tolerance_pct of each other."""
    if not levels:
        return []

    prices = sorted([p for _, p in levels])
    clusters = []
    current_cluster = [prices[0]]

    for p in prices[1:]:
        if (p - current_cluster[0]) / current_cluster[0] <= tolerance_pct:
            current_cluster.append(p)
        else:
            clusters.append(np.mean(current_cluster))
            current_cluster = [p]
    if current_cluster:
        clusters.append(np.mean(current_cluster))

    return clusters


def test_sr_strategy(df, n_bars_detect=48, tolerance_pct=0.02, stop_pct=0.03,
                     take_profit_pct=0.04, lookback_levels=500):
    """
    Test support/resistance trading:
    - Buy when price touches support (within tolerance) with stop below
    - Sell when price touches resistance (within tolerance) with stop above
    - Uses rolling window of recent S/R levels

    Returns trade statistics.
    """
    close = df['close'].values.astype(np.float64)
    high = df['high'].values.astype(np.float64)
    low = df['low'].values.astype(np.float64)
    n = len(close)

    if n < 1000:
        return None

    # Detect all swing points up front (we'll use only lookback for each bar)
    all_swing_highs, all_swing_lows = detect_swing_points(high, low, close, n_bars_detect)

    trades = []
    position = 0  # 0=flat, 1=long, -1=short
    entry_price = 0.0
    stop_price = 0.0
    tp_price = 0.0
    entry_bar = 0

    warmup = n_bars_detect * 2 + lookback_levels

    # Track S/R touches
    support_touches = 0
    support_bounces = 0
    resistance_touches = 0
    resistance_bounces = 0

    for i in range(warmup, n):
        if position == 0:
            # Get recent support/resistance levels (only from bars before current)
            recent_supports = [p for b, p in all_swing_lows
                             if b < i - n_bars_detect and b > i - lookback_levels]
            recent_resistances = [p for b, p in all_swing_highs
                                if b < i - n_bars_detect and b > i - lookback_levels]

            if not recent_supports and not recent_resistances:
                continue

            # Check if price is near support
            for sup in recent_supports:
                if abs(close[i] - sup) / sup <= tolerance_pct:
                    support_touches += 1
                    # Buy at support
                    position = 1
                    entry_price = close[i]
                    entry_bar = i
                    stop_price = sup * (1 - stop_pct)
                    tp_price = entry_price * (1 + take_profit_pct)
                    break

            if position != 0:
                continue

            # Check if price is near resistance (for short)
            for res in recent_resistances:
                if abs(close[i] - res) / res <= tolerance_pct:
                    resistance_touches += 1
                    # Short at resistance
                    position = -1
                    entry_price = close[i]
                    entry_bar = i
                    stop_price = res * (1 + stop_pct)
                    tp_price = entry_price * (1 - take_profit_pct)
                    break

        else:
            # Check exit
            bars_held = i - entry_bar
            if position == 1:
                hit_stop = close[i] <= stop_price
                hit_tp = close[i] >= tp_price
                if hit_stop or hit_tp or bars_held > 168:  # max 1 week
                    trade_ret = (close[i] / entry_price - 1) - COST_FRAC * 2
                    if trade_ret > 0:
                        support_bounces += 1
                    trades.append({
                        'direction': 'long',
                        'return': trade_ret,
                        'bars_held': bars_held,
                        'exit_reason': 'stop' if hit_stop else ('tp' if hit_tp else 'time'),
                    })
                    position = 0
            elif position == -1:
                hit_stop = close[i] >= stop_price
                hit_tp = close[i] <= tp_price
                if hit_stop or hit_tp or bars_held > 168:
                    trade_ret = (entry_price / close[i] - 1) - COST_FRAC * 2
                    if trade_ret > 0:
                        resistance_bounces += 1
                    trades.append({
                        'direction': 'short',
                        'return': trade_ret,
                        'bars_held': bars_held,
                        'exit_reason': 'stop' if hit_stop else ('tp' if hit_tp else 'time'),
                    })
                    position = 0

    if len(trades) == 0:
        return None

    trade_df = pd.DataFrame(trades)
    long_trades = trade_df[trade_df['direction'] == 'long']
    short_trades = trade_df[trade_df['direction'] == 'short']

    return {
        'n_trades': len(trades),
        'n_long': len(long_trades),
        'n_short': len(short_trades),
        'overall_win_rate': (trade_df['return'] > 0).mean(),
        'long_win_rate': (long_trades['return'] > 0).mean() if len(long_trades) > 0 else 0,
        'short_win_rate': (short_trades['return'] > 0).mean() if len(short_trades) > 0 else 0,
        'avg_return': trade_df['return'].mean(),
        'total_return': trade_df['return'].sum(),
        'support_bounce_rate': support_bounces / max(support_touches, 1),
        'resistance_bounce_rate': resistance_bounces / max(resistance_touches, 1),
        'support_touches': support_touches,
        'resistance_touches': resistance_touches,
        'avg_bars_held': trade_df['bars_held'].mean(),
        'profit_factor': abs(trade_df.loc[trade_df['return'] > 0, 'return'].sum() /
                             trade_df.loc[trade_df['return'] <= 0, 'return'].sum())
                         if trade_df.loc[trade_df['return'] <= 0, 'return'].sum() != 0 else float('inf'),
    }


def run_sr_research(combined_df):
    """Run S/R strategy tests."""
    separator("PART 3: SUPPORT/RESISTANCE STRATEGY RESEARCH")

    # Test on BTC first
    print("--- S/R DETECTION & TRADING: BTC ---\n")
    btc_path = SPOT_DIR / 'BTC_1h.parquet'
    btc_df = load_token_data(btc_path)

    # Test multiple parameter sets
    params_list = [
        {'n_bars_detect': 24, 'stop_pct': 0.02, 'take_profit_pct': 0.03, 'label': '24bar/2%stop/3%tp'},
        {'n_bars_detect': 48, 'stop_pct': 0.03, 'take_profit_pct': 0.04, 'label': '48bar/3%stop/4%tp'},
        {'n_bars_detect': 72, 'stop_pct': 0.04, 'take_profit_pct': 0.06, 'label': '72bar/4%stop/6%tp'},
        {'n_bars_detect': 120, 'stop_pct': 0.05, 'take_profit_pct': 0.08, 'label': '120bar/5%stop/8%tp'},
    ]

    print(f"  {'Params':>25s}  {'#Tr':>5s}  {'WinR':>6s}  {'L-WR':>6s}  {'S-WR':>6s}  "
          f"{'AvgRet':>8s}  {'TotRet':>8s}  {'S-Bounce':>8s}  {'R-Bounce':>8s}  {'PF':>6s}")
    print(f"  {'-'*25}  {'-'*5}  {'-'*6}  {'-'*6}  {'-'*6}  "
          f"{'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*6}")

    btc_results = []
    for params in params_list:
        r = test_sr_strategy(btc_df, n_bars_detect=params['n_bars_detect'],
                            stop_pct=params['stop_pct'],
                            take_profit_pct=params['take_profit_pct'])
        if r:
            btc_results.append(r)
            print(f"  {params['label']:>25s}  {r['n_trades']:>5d}  {r['overall_win_rate']:>5.1%}  "
                  f"{r['long_win_rate']:>5.1%}  {r['short_win_rate']:>5.1%}  "
                  f"{r['avg_return']:>+7.2%}  {r['total_return']:>+7.1%}  "
                  f"{r['support_bounce_rate']:>7.1%}  {r['resistance_bounce_rate']:>7.1%}  "
                  f"{r['profit_factor']:>5.2f}")

    # Test on volatile altcoins
    print(f"\n\n--- S/R TRADING: VOLATILE ALTCOINS ---\n")
    # Pick top volatile tokens with sufficient data
    volatile_tokens = combined_df[combined_df['n_days'] > 365].nlargest(15, 'ann_vol')

    print(f"  {'Token':>10s}  {'Vol':>6s}  {'#Tr':>5s}  {'WinR':>6s}  {'L-WR':>6s}  {'S-WR':>6s}  "
          f"{'AvgRet':>8s}  {'TotRet':>8s}  {'S-Bnc':>6s}  {'R-Bnc':>6s}  {'PF':>6s}")
    print(f"  {'-'*10}  {'-'*6}  {'-'*5}  {'-'*6}  {'-'*6}  {'-'*6}  "
          f"{'-'*8}  {'-'*8}  {'-'*6}  {'-'*6}  {'-'*6}")

    alt_sr_results = []
    for _, row in volatile_tokens.iterrows():
        token = row['token']
        market = row['market']
        data_dir = SPOT_DIR if market == 'spot' else PERP_DIR
        fpath = data_dir / f'{token}_1h.parquet'
        if not fpath.exists():
            continue

        tdf = load_token_data(fpath)
        r = test_sr_strategy(tdf, n_bars_detect=48, stop_pct=0.04, take_profit_pct=0.06)

        if r:
            r['token'] = token
            r['ann_vol'] = row['ann_vol']
            alt_sr_results.append(r)
            print(f"  {token:>10s}  {row['ann_vol']*100:>5.0f}%  {r['n_trades']:>5d}  "
                  f"{r['overall_win_rate']:>5.1%}  {r['long_win_rate']:>5.1%}  "
                  f"{r['short_win_rate']:>5.1%}  {r['avg_return']:>+7.2%}  "
                  f"{r['total_return']:>+7.1%}  {r['support_bounce_rate']:>5.1%}  "
                  f"{r['resistance_bounce_rate']:>5.1%}  {r['profit_factor']:>5.2f}")

    # S/R accuracy summary
    if alt_sr_results:
        print(f"\n  S/R Level Accuracy Summary (altcoins):")
        bounce_rates_s = [r['support_bounce_rate'] for r in alt_sr_results]
        bounce_rates_r = [r['resistance_bounce_rate'] for r in alt_sr_results]
        print(f"    Avg support bounce rate:     {np.mean(bounce_rates_s):.1%} "
              f"(range {np.min(bounce_rates_s):.1%}-{np.max(bounce_rates_s):.1%})")
        print(f"    Avg resistance bounce rate:  {np.mean(bounce_rates_r):.1%} "
              f"(range {np.min(bounce_rates_r):.1%}-{np.max(bounce_rates_r):.1%})")
        print(f"    Avg profit factor:           {np.mean([r['profit_factor'] for r in alt_sr_results]):.2f}")
        if btc_results:
            print(f"\n  BTC vs Altcoins comparison:")
            print(f"    BTC avg win rate:   {np.mean([r['overall_win_rate'] for r in btc_results]):.1%}")
            print(f"    Alts avg win rate:  {np.mean([r['overall_win_rate'] for r in alt_sr_results]):.1%}")
            print(f"    BTC avg PF:         {np.mean([r['profit_factor'] for r in btc_results]):.2f}")
            print(f"    Alts avg PF:        {np.mean([r['profit_factor'] for r in alt_sr_results]):.2f}")

    return btc_results, alt_sr_results


# ============================================================================
# PART 4: TIERED STRATEGY FRAMEWORK
# ============================================================================

def simulate_tier_strategy(combined_df, data_dirs):
    """
    Simulate different strategy logic per tier and compute theoretical max returns.

    Large cap: trend-following (EMA crossover)
    Mid cap:   momentum breakout with tighter stops
    Small cap: early breakout detection with position limits
    """
    separator("PART 4: TIERED STRATEGY FRAMEWORK")

    tier_configs = {
        'large': {
            'strategy': 'trend_follow',
            'ema_fast': 20,
            'ema_slow': 50,
            'max_position_pct': 0.20,  # 20% of portfolio per token
            'stop_atr_mult': 2.0,
        },
        'mid': {
            'strategy': 'momentum_breakout',
            'atr_mult': 1.5,
            'vol_mult': 1.5,
            'max_position_pct': 0.08,  # 8% per token
            'stop_atr_mult': 1.5,
            'hold_bars': 72,
        },
        'small': {
            'strategy': 'early_breakout',
            'atr_mult': 2.0,
            'vol_mult': 2.5,
            'max_position_pct': 0.03,  # 3% per token
            'stop_atr_mult': 1.0,
            'hold_bars': 48,
        },
    }

    # Simulate each tier
    tier_returns = {}

    for tier_name, config in tier_configs.items():
        tier_tokens = combined_df[combined_df['tier'] == tier_name]
        if len(tier_tokens) == 0:
            tier_returns[tier_name] = {'tokens': 0, 'avg_return': 0, 'best_return': 0}
            continue

        token_results = []
        for _, row in tier_tokens.iterrows():
            token = row['token']
            market = row['market']
            data_dir = SPOT_DIR if market == 'spot' else PERP_DIR
            fpath = data_dir / f'{token}_1h.parquet'
            if not fpath.exists():
                continue

            df = load_token_data(fpath)
            if len(df) < 500:
                continue

            if config['strategy'] == 'trend_follow':
                result = simulate_trend_follow(df, config)
            elif config['strategy'] in ('momentum_breakout', 'early_breakout'):
                result = test_momentum_breakout(
                    df,
                    atr_mult=config['atr_mult'],
                    vol_mult=config['vol_mult'],
                    hold_bars=config.get('hold_bars', 72),
                    stop_atr_mult=config['stop_atr_mult'],
                )
            else:
                result = None

            if result is not None:
                result['token'] = token
                result['max_pos_pct'] = config['max_position_pct']
                token_results.append(result)

        if token_results:
            res_df = pd.DataFrame(token_results)
            tier_returns[tier_name] = {
                'tokens': len(res_df),
                'avg_return': res_df['total_return'].mean(),
                'median_return': res_df['total_return'].median(),
                'best_return': res_df['total_return'].max(),
                'worst_return': res_df['total_return'].min(),
                'avg_win_rate': res_df['win_rate'].mean() if 'win_rate' in res_df.columns else 0,
                'profitable_pct': (res_df['total_return'] > 0).mean(),
                'results': res_df,
            }
        else:
            tier_returns[tier_name] = {'tokens': 0, 'avg_return': 0}

    # Print results
    print("--- STRATEGY RESULTS BY TIER ---\n")
    print(f"  {'Tier':>8s}  {'Strategy':>20s}  {'#Tokens':>8s}  {'AvgRet':>8s}  {'MedRet':>8s}  "
          f"{'BestRet':>8s}  {'WorstRet':>9s}  {'%Profitable':>12s}")
    print(f"  {'-'*8}  {'-'*20}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*9}  {'-'*12}")

    for tier_name, config in tier_configs.items():
        tr = tier_returns[tier_name]
        if tr['tokens'] == 0:
            print(f"  {tier_name:>8s}  {config['strategy']:>20s}  {'0':>8s}  {'N/A':>8s}")
            continue
        print(f"  {tier_name:>8s}  {config['strategy']:>20s}  {tr['tokens']:>8d}  "
              f"{tr['avg_return']:>+7.1%}  {tr.get('median_return', 0):>+7.1%}  "
              f"{tr['best_return']:>+7.1%}  {tr.get('worst_return', 0):>+8.1%}  "
              f"{tr.get('profitable_pct', 0):>11.1%}")

    # Theoretical maximum: optimal allocation across tiers
    print(f"\n\n--- THEORETICAL OPTIMAL ALLOCATION ---\n")

    # Equal weight across tiers, then equal weight within tier
    allocations = [
        {'name': 'All Large Cap (BTC-only baseline)', 'large': 1.0, 'mid': 0.0, 'small': 0.0},
        {'name': 'Equal Weight Tiers', 'large': 0.33, 'mid': 0.34, 'small': 0.33},
        {'name': 'Barbell (70% large, 30% small)', 'large': 0.70, 'mid': 0.0, 'small': 0.30},
        {'name': 'Growth Tilt (30/30/40)', 'large': 0.30, 'mid': 0.30, 'small': 0.40},
        {'name': 'Conservative Tilt (60/30/10)', 'large': 0.60, 'mid': 0.30, 'small': 0.10},
    ]

    print(f"  {'Allocation':>40s}  {'Blended Return':>15s}")
    print(f"  {'-'*40}  {'-'*15}")

    for alloc in allocations:
        blended = 0
        for tier_name in ['large', 'mid', 'small']:
            tr = tier_returns.get(tier_name, {})
            avg_ret = tr.get('avg_return', 0) if tr.get('tokens', 0) > 0 else 0
            blended += alloc[tier_name] * avg_ret
        print(f"  {alloc['name']:>40s}  {blended:>+14.1%}")

    # Print top performers per tier
    print(f"\n\n--- TOP 5 PERFORMERS PER TIER ---\n")
    for tier_name in ['large', 'mid', 'small']:
        tr = tier_returns.get(tier_name, {})
        if tr.get('tokens', 0) == 0:
            print(f"  {tier_name.upper()}: No results")
            continue
        res_df = tr['results']
        top5 = res_df.nlargest(5, 'total_return')
        print(f"  {tier_name.upper()}:")
        for _, row in top5.iterrows():
            print(f"    {row['token']:>10s}: {row['total_return']:+.1%} "
                  f"({row['n_trades']} trades, {row['win_rate']:.0%} win rate)")
        print()

    return tier_returns


def simulate_trend_follow(df, config):
    """
    Simple EMA crossover trend following.
    Long when fast EMA > slow EMA, flat otherwise.
    """
    close = df['close'].values.astype(np.float64)
    high = df['high'].values.astype(np.float64)
    low = df['low'].values.astype(np.float64)
    n = len(close)

    if n < 200:
        return None

    # Compute EMAs
    fast = config['ema_fast']
    slow = config['ema_slow']

    ema_fast = np.full(n, np.nan)
    ema_slow = np.full(n, np.nan)
    alpha_f = 2.0 / (fast + 1)
    alpha_s = 2.0 / (slow + 1)

    ema_fast[fast-1] = np.mean(close[:fast])
    ema_slow[slow-1] = np.mean(close[:slow])

    for i in range(fast, n):
        ema_fast[i] = alpha_f * close[i] + (1 - alpha_f) * ema_fast[i-1]
    for i in range(slow, n):
        ema_slow[i] = alpha_s * close[i] + (1 - alpha_s) * ema_slow[i-1]

    # ATR for stops
    atr = compute_atr(high, low, close, period=14)

    trades = []
    position = 0
    entry_price = 0.0
    stop_price = 0.0
    entry_bar = 0
    warmup = slow + 50

    for i in range(warmup, n):
        if np.isnan(ema_fast[i]) or np.isnan(ema_slow[i]) or np.isnan(atr[i]):
            continue

        if position == 0:
            # Entry: fast > slow (trend up)
            if ema_fast[i] > ema_slow[i] and ema_fast[i-1] <= ema_slow[i-1]:
                position = 1
                entry_price = close[i]
                entry_bar = i
                stop_price = entry_price - config['stop_atr_mult'] * atr[i]
        else:
            # Exit: fast < slow or stop
            if ema_fast[i] < ema_slow[i] or close[i] <= stop_price:
                trade_ret = (close[i] / entry_price - 1) - COST_FRAC * 2
                trades.append({
                    'entry_bar': entry_bar,
                    'exit_bar': i,
                    'bars_held': i - entry_bar,
                    'return': trade_ret,
                    'exit_reason': 'signal' if ema_fast[i] < ema_slow[i] else 'stop',
                })
                position = 0

    if len(trades) == 0:
        return None

    trade_df = pd.DataFrame(trades)
    return {
        'n_trades': len(trades),
        'win_rate': (trade_df['return'] > 0).mean(),
        'avg_win': trade_df.loc[trade_df['return'] > 0, 'return'].mean() if (trade_df['return'] > 0).any() else 0,
        'avg_loss': trade_df.loc[trade_df['return'] <= 0, 'return'].mean() if (trade_df['return'] <= 0).any() else 0,
        'total_return': trade_df['return'].sum(),
        'best_trade': trade_df['return'].max(),
        'worst_trade': trade_df['return'].min(),
        'avg_bars_held': trade_df['bars_held'].mean(),
        'profit_factor': abs(trade_df.loc[trade_df['return'] > 0, 'return'].sum() /
                             trade_df.loc[trade_df['return'] <= 0, 'return'].sum())
                         if trade_df.loc[trade_df['return'] <= 0, 'return'].sum() != 0 else float('inf'),
    }


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 80)
    print("  LONG-TAIL ALTCOIN & SUPPORT/RESISTANCE RESEARCH")
    print("  Scanning all available spot + perp tokens")
    print("=" * 80)

    # --- Part 1: Universe scan ---
    print("\nScanning spot universe...")
    spot_df = scan_universe(SPOT_DIR, 'spot')
    print(f"  Loaded {len(spot_df)} spot tokens")

    print("Scanning perp universe...")
    perp_df = scan_universe(PERP_DIR, 'perp')
    print(f"  Loaded {len(perp_df)} perp tokens")

    combined_df = print_universe_analysis(spot_df, perp_df)

    # --- Part 2: Small-cap momentum ---
    momentum_results = run_small_cap_momentum(combined_df)

    # --- Part 3: S/R research ---
    btc_sr, alt_sr = run_sr_research(combined_df)

    # --- Part 4: Tiered strategy ---
    tier_returns = simulate_tier_strategy(combined_df, {'spot': SPOT_DIR, 'perp': PERP_DIR})

    # --- Final Summary ---
    separator("FINAL SUMMARY & RECOMMENDATIONS")

    print("1. UNIVERSE COMPOSITION:")
    for tier in ['large', 'mid', 'small', 'micro']:
        tdf = combined_df[combined_df['tier'] == tier]
        print(f"   {tier:6s}: {len(tdf):3d} tokens  "
              f"avg_ret={tdf['total_return'].mean()*100:+.1f}%  "
              f"avg_vol={tdf['ann_vol'].mean()*100:.0f}%")

    print("\n2. KEY FINDINGS:")
    large_ret = combined_df[combined_df['tier'] == 'large']['total_return'].mean()
    mid_ret = combined_df[combined_df['tier'] == 'mid']['total_return'].mean()
    small_ret = combined_df[combined_df['tier'] == 'small']['total_return'].mean()

    if mid_ret > large_ret:
        print(f"   - Mid-cap outperforms large-cap by {(mid_ret - large_ret)*100:.1f}pp (raw return)")
    else:
        print(f"   - Large-cap outperforms mid-cap by {(large_ret - mid_ret)*100:.1f}pp (raw return)")

    if small_ret > large_ret:
        print(f"   - Small-cap outperforms large-cap by {(small_ret - large_ret)*100:.1f}pp BUT:")
        print(f"     * Position sizing is severely constrained (5% ADV rule)")
        print(f"     * Higher max drawdown and survivorship bias")
    else:
        print(f"   - Small-cap does NOT outperform large-cap on average")
        print(f"     * Dispersion is higher => some extreme winners exist")

    print(f"\n3. S/R STRATEGY VIABILITY:")
    if btc_sr:
        best_btc = max(btc_sr, key=lambda x: x['profit_factor'])
        print(f"   - BTC S/R: best PF = {best_btc['profit_factor']:.2f}, "
              f"win rate = {best_btc['overall_win_rate']:.1%}")
    if alt_sr:
        avg_pf = np.mean([r['profit_factor'] for r in alt_sr])
        print(f"   - Altcoin S/R: avg PF = {avg_pf:.2f}")
        print(f"   - Support bounce rate: {np.mean([r['support_bounce_rate'] for r in alt_sr]):.1%}")
        print(f"   - Resistance bounce rate: {np.mean([r['resistance_bounce_rate'] for r in alt_sr]):.1%}")

    print(f"\n4. CONCRETE PROPOSAL — TIERED STRATEGY:")
    print(f"   Large cap (>$100M ADV):  EMA trend-following, 20% max position per token")
    print(f"   Mid cap ($10M-$100M):    Momentum breakout, 8% max position, 1.5 ATR stop")
    print(f"   Small cap ($1M-$10M):    Early breakout + vol surge, 3% max, tight stops")
    print(f"   Micro (<$1M):            Skip — untradeable at any meaningful size")
    print(f"\n   Recommended allocation: Conservative 60/30/10 tilt")
    print(f"   (largest allocation to large-caps for stability, small positions in alts for upside)")

    print(f"\n5. NEXT STEPS:")
    print(f"   a) Implement tiered universe in v4/universe.py with dynamic tier thresholds")
    print(f"   b) Build momentum breakout strategy in strategies/ for mid-cap tier")
    print(f"   c) Add S/R overlay to existing trend strategies (IF bounce rates > 55%)")
    print(f"   d) Backtest combined tiered portfolio with realistic position limits")
    print(f"   e) Run walk-forward validation on top 3 parameter sets per tier")

    print("\n" + "=" * 80)
    print("  END OF RESEARCH")
    print("=" * 80)


if __name__ == '__main__':
    main()
