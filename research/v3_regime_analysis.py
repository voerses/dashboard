#!/workspace/venv/bin/python
"""
V3 Momentum Strategy — Regime Analysis (R68)

Analyzes V3 performance across market regimes to understand WHEN it works and WHEN it fails.

V3 Strategy Definition:
  Base (V1): Long when 20d EMA > 50d EMA, flat otherwise. NO stop losses.
  Positioning overlay: Binance Top Trader L/S + L/S Divergence combined z-score (30d rolling)
    -> sizing multiplier (z>1.5->0.3x, z>0.5->0.5x, neutral->1.0x, z<-0.5->1.3x, z<-1.5->1.5x)
  VRP overlay: (IV - RV) z-score over 60d
    -> sizing multiplier (z>1->1.3x, z>-0.5->1.0x, z>-1.5->0.5x, z<-1.5->0.3x)
  Rebalancing: Weekly (Monday). Cost: 10 bps round-trip. Position range: 0 to 1.5x.

Regime Definition (using BTC close price):
  UPTREND:   50d SMA > 200d SMA AND close > 50d SMA
  DOWNTREND: 50d SMA < 200d SMA AND close < 50d SMA
  RANGE:     50d SMA and 200d SMA within 5% of each other OR close between 50d and 200d SMA
  CRISIS:    50d return < -20% (BTC dropped >20% in last 50 days)

Analysis Tasks:
  1. Regime-specific performance (V3 vs V1)
  2. Regime transition analysis
  3. Position sizing distribution per regime
  4. Drawdown attribution
  5. Calendar analysis (monthly heatmap)
  6. What kills V3?
"""

import pandas as pd
import numpy as np
import json
import warnings
from pathlib import Path
from datetime import datetime

warnings.filterwarnings('ignore')

BASE_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = BASE_DIR / 'data'
OUTPUT_DIR = BASE_DIR / 'research'

IS_START = '2020-09-01'   # Start of positioning data
IS_END = '2024-12-31'
OOS_START = '2025-01-01'
COST_BPS = 10


# ══════════════════════════════════════════════════════════════════════════════
# 1. DATA LOADING
# ══════════════════════════════════════════════════════════════════════════════

def load_btc_daily():
    """Load BTC spot 1h data and resample to daily close."""
    print("[1/4] Loading BTC spot data...")
    btc = pd.read_parquet(DATA_DIR / 'spot/1h_cache/BTC_1h.parquet')
    btc.index = pd.to_datetime(btc.index)
    btc.index.name = 'date'
    daily = btc['close'].resample('D').last().dropna().to_frame('close')
    daily['open'] = btc['open'].resample('D').first()
    daily['high'] = btc['high'].resample('D').max()
    daily['low'] = btc['low'].resample('D').min()
    daily['volume'] = btc['volume'].resample('D').sum()
    print(f"  BTC daily: {daily.index.min().date()} to {daily.index.max().date()}, {len(daily)} rows")
    return daily


def load_positioning():
    """Load Binance positioning data for BTCUSDT."""
    print("[2/4] Loading positioning data...")
    pos = pd.read_parquet(DATA_DIR / 'alternative/binance_metrics/all_symbols_daily_ls.parquet')
    pos = pos[pos['symbol'] == 'BTCUSDT'].copy()
    pos['date'] = pd.to_datetime(pos['date'])
    pos = pos.set_index('date').sort_index()
    pos = pos[['sum_toptrader_ls_ratio', 'count_toptrader_ls_ratio', 'count_ls_ratio']].copy()
    pos = pos[~pos.index.duplicated(keep='last')]
    print(f"  Positioning: {pos.index.min().date()} to {pos.index.max().date()}, {len(pos)} rows")
    return pos


def load_dvol():
    """Load BTC DVOL from Deribit JSON (OHLC daily candles)."""
    print("[3/4] Loading BTC DVOL...")
    dvol_path = DATA_DIR / 'alternative/deribit_options/dvol/btc_dvol_daily.json'
    with open(dvol_path) as f:
        data = json.load(f)
    records = []
    for row in data:
        ts = pd.Timestamp(row[0], unit='ms')
        records.append({'date': ts, 'dvol_close': row[4]})
    dvol = pd.DataFrame(records).set_index('date').sort_index()
    dvol = dvol[~dvol.index.duplicated(keep='last')]
    print(f"  DVOL: {dvol.index.min().date()} to {dvol.index.max().date()}, {len(dvol)} rows")
    return dvol['dvol_close']


# ══════════════════════════════════════════════════════════════════════════════
# 2. SIGNAL CONSTRUCTION
# ══════════════════════════════════════════════════════════════════════════════

def build_v1_signal(btc_daily):
    """V1 base signal: Long when 20d EMA > 50d EMA, flat otherwise. No stop losses."""
    print("[4a/4] Building V1 signal (20d EMA > 50d EMA)...")
    ema20 = btc_daily['close'].ewm(span=20, adjust=False).mean()
    ema50 = btc_daily['close'].ewm(span=50, adjust=False).mean()
    # Simple crossover: long when EMA20 > EMA50
    position = (ema20 > ema50).astype(float)
    print(f"  V1 signal: {position.sum():.0f} days long out of {len(position)} ({100*position.mean():.1f}%)")
    return position, ema20, ema50


def build_positioning_multiplier(btc_daily, positioning):
    """Positioning overlay: combined z-score -> sizing multiplier."""
    print("[4b/4] Building positioning overlay...")
    pos = positioning.reindex(btc_daily.index).ffill()

    def rolling_zscore(s, window=30):
        mu = s.rolling(window, min_periods=15).mean()
        sigma = s.rolling(window, min_periods=15).std()
        return (s - mu) / sigma.replace(0, np.nan)

    z_toptrader = rolling_zscore(pos['sum_toptrader_ls_ratio'])
    divergence = pos['count_toptrader_ls_ratio'] - pos['count_ls_ratio']
    z_divergence = rolling_zscore(divergence)
    combined_z = (z_toptrader + z_divergence) / 2.0

    def z_to_multiplier(z):
        if pd.isna(z):
            return 1.0
        if z > 1.5:
            return 0.3
        elif z > 0.5:
            return 0.5
        elif z > -0.5:
            return 1.0
        elif z > -1.5:
            return 1.3
        else:
            return 1.5

    pos_multiplier = combined_z.apply(z_to_multiplier)
    print(f"  Positioning multiplier distribution:")
    for val in [0.3, 0.5, 1.0, 1.3, 1.5]:
        pct = (pos_multiplier == val).mean() * 100
        print(f"    {val}x: {pct:.1f}%")
    return pos_multiplier, combined_z


def build_vrp_multiplier(btc_daily, dvol_series):
    """VRP overlay: (IV - RV) z-score over 60d -> sizing multiplier."""
    print("[4c/4] Building VRP overlay...")
    log_ret = np.log(btc_daily['close'] / btc_daily['close'].shift(1))
    rv_20d = log_ret.rolling(20, min_periods=15).std() * np.sqrt(365) * 100

    iv = dvol_series.reindex(btc_daily.index).ffill()
    vrp = iv - rv_20d

    vrp_mu = vrp.rolling(60, min_periods=30).mean()
    vrp_sigma = vrp.rolling(60, min_periods=30).std()
    vrp_z = (vrp - vrp_mu) / vrp_sigma.replace(0, np.nan)

    def vrp_z_to_multiplier(z):
        if pd.isna(z):
            return 1.0
        if z > 1.0:
            return 1.3
        elif z > -0.5:
            return 1.0
        elif z > -1.5:
            return 0.5
        else:
            return 0.3

    vrp_multiplier = vrp_z.apply(vrp_z_to_multiplier)
    print(f"  VRP multiplier distribution:")
    for val in [0.3, 0.5, 1.0, 1.3]:
        pct = (vrp_multiplier == val).mean() * 100
        print(f"    {val}x: {pct:.1f}%")
    return vrp_multiplier, vrp_z


# ══════════════════════════════════════════════════════════════════════════════
# 3. REGIME CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════════

def classify_regimes(btc_daily):
    """
    Classify each day into one of 4 regimes:
      CRISIS:    50d return < -20%
      UPTREND:   50d SMA > 200d SMA AND close > 50d SMA
      DOWNTREND: 50d SMA < 200d SMA AND close < 50d SMA
      RANGE:     Everything else (SMAs within 5% or close between SMAs)
    Note: CRISIS takes priority.
    """
    close = btc_daily['close']
    sma50 = close.rolling(50, min_periods=50).mean()
    sma200 = close.rolling(200, min_periods=200).mean()
    ret_50d = close.pct_change(50)

    regime = pd.Series('RANGE', index=btc_daily.index)

    # UPTREND
    uptrend = (sma50 > sma200) & (close > sma50)
    regime[uptrend] = 'UPTREND'

    # DOWNTREND
    downtrend = (sma50 < sma200) & (close < sma50)
    regime[downtrend] = 'DOWNTREND'

    # RANGE: explicit check -- SMAs within 5% of each other OR close between SMAs
    sma_close = (abs(sma50 - sma200) / sma200) < 0.05
    close_between = ((close > sma50.clip(upper=sma200)) & (close < sma200.clip(lower=sma50))) | \
                    ((close > sma200.clip(upper=sma50)) & (close < sma50.clip(lower=sma200)))
    range_mask = sma_close | close_between
    # Range overrides uptrend/downtrend only where SMAs are close
    regime[range_mask & ~uptrend & ~downtrend] = 'RANGE'

    # CRISIS overrides everything
    crisis = ret_50d < -0.20
    regime[crisis] = 'CRISIS'

    # NaN for warmup period
    regime[sma200.isna()] = np.nan

    return regime, sma50, sma200


# ══════════════════════════════════════════════════════════════════════════════
# 4. BACKTEST ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def run_backtest(btc_daily, final_position, cost_bps=COST_BPS):
    """Run backtest with weekly rebalancing and transaction costs."""
    daily_ret = btc_daily['close'].pct_change()

    rebalance_dates = btc_daily.index.to_series().groupby(
        btc_daily.index.to_period('W')
    ).first()
    rebalance_set = set(rebalance_dates.values)

    held_position = pd.Series(0.0, index=btc_daily.index)
    current_pos = 0.0
    costs = pd.Series(0.0, index=btc_daily.index)

    for dt in btc_daily.index:
        if dt in rebalance_set:
            new_pos = final_position.loc[dt]
            if not pd.isna(new_pos):
                pos_change = abs(new_pos - current_pos)
                costs.loc[dt] = pos_change * cost_bps / 10000.0
                current_pos = new_pos
        held_position.loc[dt] = current_pos

    strat_ret = held_position.shift(1) * daily_ret - costs
    return strat_ret, held_position, costs


def compute_metrics(returns, label=""):
    """Compute performance metrics from daily returns."""
    returns = returns.dropna()
    if len(returns) < 5:
        return {'label': label, 'total_return': np.nan, 'ann_return': np.nan,
                'ann_vol': np.nan, 'sharpe': np.nan, 'max_dd': np.nan,
                'calmar': np.nan, 'n_days': len(returns), 'profit_factor': np.nan}

    total_ret = (1 + returns).prod() - 1
    n_years = len(returns) / 365
    ann_ret = (1 + total_ret) ** (1 / max(n_years, 0.01)) - 1
    ann_vol = returns.std() * np.sqrt(365)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

    cum = (1 + returns).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    max_dd = dd.min()
    calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0

    # Profit factor
    gains = returns[returns > 0].sum()
    losses = abs(returns[returns < 0].sum())
    pf = gains / losses if losses > 0 else np.inf

    return {
        'label': label,
        'total_return': total_ret,
        'ann_return': ann_ret,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'calmar': calmar,
        'n_days': len(returns),
        'profit_factor': pf,
    }


def find_drawdowns(returns, top_n=5):
    """Find top N drawdowns with start/end dates and depth."""
    cum = (1 + returns.dropna()).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak

    drawdowns = []
    in_dd = False
    dd_start = None

    for i in range(len(dd)):
        if dd.iloc[i] < 0 and not in_dd:
            in_dd = True
            dd_start = dd.index[i]
        elif dd.iloc[i] == 0 and in_dd:
            in_dd = False
            dd_end = dd.index[i]
            dd_period = dd.loc[dd_start:dd_end]
            trough_date = dd_period.idxmin()
            depth = dd_period.min()
            drawdowns.append({
                'start': dd_start,
                'trough': trough_date,
                'end': dd_end,
                'depth': depth,
                'duration_days': (dd_end - dd_start).days,
            })

    # Handle ongoing drawdown
    if in_dd:
        dd_period = dd.loc[dd_start:]
        trough_date = dd_period.idxmin()
        depth = dd_period.min()
        drawdowns.append({
            'start': dd_start,
            'trough': trough_date,
            'end': dd.index[-1],
            'depth': depth,
            'duration_days': (dd.index[-1] - dd_start).days,
            'ongoing': True,
        })

    drawdowns.sort(key=lambda x: x['depth'])
    return drawdowns[:top_n]


# ══════════════════════════════════════════════════════════════════════════════
# 5. MAIN ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 78)
    print("R68: V3 MOMENTUM STRATEGY — REGIME ANALYSIS")
    print("=" * 78)
    print()

    # ── Load Data ──
    btc_daily = load_btc_daily()
    positioning = load_positioning()
    dvol = load_dvol()
    print()

    # ── Build Signals ──
    v1_pos, ema20, ema50 = build_v1_signal(btc_daily)
    pos_mult, pos_z = build_positioning_multiplier(btc_daily, positioning)
    vrp_mult, vrp_z = build_vrp_multiplier(btc_daily, dvol)

    # V3 = V1 * positioning * VRP, clipped to [0, 1.5]
    v3_target_pos = (v1_pos * pos_mult * vrp_mult).clip(0, 1.5)

    print()
    print("Running backtests...")

    # V1 backtest
    v1_ret, v1_held, v1_costs = run_backtest(btc_daily, v1_pos)
    # V3 backtest
    v3_ret, v3_held, v3_costs = run_backtest(btc_daily, v3_target_pos)
    # BTC buy-and-hold
    bh_ret = btc_daily['close'].pct_change()

    print("  Done.")
    print()

    # ── Classify Regimes ──
    print("Classifying market regimes...")
    regime, sma50, sma200 = classify_regimes(btc_daily)

    # Define masks
    full_mask = btc_daily.index >= IS_START
    is_mask = (btc_daily.index >= IS_START) & (btc_daily.index <= IS_END)
    oos_mask = btc_daily.index >= OOS_START

    print("  Regime distribution (full period from IS_START):")
    for r in ['UPTREND', 'DOWNTREND', 'RANGE', 'CRISIS']:
        n = ((regime == r) & full_mask).sum()
        pct = 100 * n / full_mask.sum()
        print(f"    {r}: {n} days ({pct:.1f}%)")
    print()

    # ══════════════════════════════════════════════════════════════════════════
    # TASK 1: REGIME-SPECIFIC PERFORMANCE
    # ══════════════════════════════════════════════════════════════════════════
    print("=" * 78)
    print("TASK 1: Regime-Specific Performance")
    print("=" * 78)

    regime_metrics = {}
    for period_name, period_mask in [('IS', is_mask), ('OOS', oos_mask), ('FULL', full_mask)]:
        regime_metrics[period_name] = {}
        for r in ['UPTREND', 'DOWNTREND', 'RANGE', 'CRISIS']:
            r_mask = (regime == r) & period_mask
            v1_r = v1_ret[r_mask]
            v3_r = v3_ret[r_mask]
            bh_r = bh_ret[r_mask]
            regime_metrics[period_name][r] = {
                'v1': compute_metrics(v1_r, f"V1 {r} ({period_name})"),
                'v3': compute_metrics(v3_r, f"V3 {r} ({period_name})"),
                'bh': compute_metrics(bh_r, f"BH {r} ({period_name})"),
                'n_days': r_mask.sum(),
            }
            print(f"  {period_name} {r}: {r_mask.sum()} days, V1 Sharpe={regime_metrics[period_name][r]['v1']['sharpe']:.2f}, V3 Sharpe={regime_metrics[period_name][r]['v3']['sharpe']:.2f}")

    print()

    # ══════════════════════════════════════════════════════════════════════════
    # TASK 2: REGIME TRANSITION ANALYSIS
    # ══════════════════════════════════════════════════════════════════════════
    print("=" * 78)
    print("TASK 2: Regime Transition Analysis")
    print("=" * 78)

    regime_valid = regime.dropna()
    transitions = []
    for i in range(1, len(regime_valid)):
        prev = regime_valid.iloc[i-1]
        curr = regime_valid.iloc[i]
        if prev != curr:
            transitions.append({
                'date': regime_valid.index[i],
                'from': prev,
                'to': curr,
            })

    transition_df = pd.DataFrame(transitions)
    print(f"  Total transitions: {len(transitions)}")

    # Compute 10-day return after each transition for V1 and V3
    transition_returns = []
    for _, tr in transition_df.iterrows():
        dt = tr['date']
        idx = btc_daily.index.get_loc(dt)
        if idx + 10 < len(btc_daily):
            v1_10d = v1_ret.iloc[idx:idx+10].sum()
            v3_10d = v3_ret.iloc[idx:idx+10].sum()
            bh_10d = bh_ret.iloc[idx:idx+10].sum()
            transition_returns.append({
                'date': dt,
                'from': tr['from'],
                'to': tr['to'],
                'v1_10d': v1_10d,
                'v3_10d': v3_10d,
                'bh_10d': bh_10d,
            })

    tr_df = pd.DataFrame(transition_returns)

    # Key transitions
    key_transitions = [
        ('UPTREND', 'DOWNTREND'),
        ('UPTREND', 'CRISIS'),
        ('RANGE', 'CRISIS'),
        ('CRISIS', 'RANGE'),
        ('CRISIS', 'UPTREND'),
        ('DOWNTREND', 'UPTREND'),
    ]

    transition_summary = {}
    for from_r, to_r in key_transitions:
        subset = tr_df[(tr_df['from'] == from_r) & (tr_df['to'] == to_r)]
        if len(subset) > 0:
            avg_v1 = subset['v1_10d'].mean()
            avg_v3 = subset['v3_10d'].mean()
            avg_bh = subset['bh_10d'].mean()
            transition_summary[(from_r, to_r)] = {
                'count': len(subset),
                'avg_v1_10d': avg_v1,
                'avg_v3_10d': avg_v3,
                'avg_bh_10d': avg_bh,
            }
            print(f"  {from_r}->{to_r}: N={len(subset)}, V1 10d={avg_v1:.2%}, V3 10d={avg_v3:.2%}, BH 10d={avg_bh:.2%}")
        else:
            print(f"  {from_r}->{to_r}: No occurrences")

    print()

    # ══════════════════════════════════════════════════════════════════════════
    # TASK 3: POSITION SIZING DISTRIBUTION PER REGIME
    # ══════════════════════════════════════════════════════════════════════════
    print("=" * 78)
    print("TASK 3: Position Sizing Distribution Per Regime")
    print("=" * 78)

    pos_size_by_regime = {}
    for r in ['UPTREND', 'DOWNTREND', 'RANGE', 'CRISIS']:
        r_mask = (regime == r) & full_mask
        v3_held_r = v3_held[r_mask]
        if len(v3_held_r) > 0:
            pos_size_by_regime[r] = {
                'mean': v3_held_r.mean(),
                'median': v3_held_r.median(),
                'std': v3_held_r.std(),
                'pct_flat': (v3_held_r == 0).mean() * 100,
                'pct_reduced': ((v3_held_r > 0) & (v3_held_r < 0.9)).mean() * 100,
                'pct_full': ((v3_held_r >= 0.9) & (v3_held_r <= 1.1)).mean() * 100,
                'pct_levered': (v3_held_r > 1.1).mean() * 100,
            }
            print(f"  {r}: mean={v3_held_r.mean():.3f}, median={v3_held_r.median():.3f}, "
                  f"flat={pos_size_by_regime[r]['pct_flat']:.1f}%, reduced={pos_size_by_regime[r]['pct_reduced']:.1f}%, "
                  f"full={pos_size_by_regime[r]['pct_full']:.1f}%, levered={pos_size_by_regime[r]['pct_levered']:.1f}%")

    print()

    # ══════════════════════════════════════════════════════════════════════════
    # TASK 4: DRAWDOWN ATTRIBUTION
    # ══════════════════════════════════════════════════════════════════════════
    print("=" * 78)
    print("TASK 4: Drawdown Attribution")
    print("=" * 78)

    v3_full_ret = v3_ret[full_mask]
    v1_full_ret = v1_ret[full_mask]

    v3_drawdowns = find_drawdowns(v3_full_ret, top_n=5)
    v1_drawdowns = find_drawdowns(v1_full_ret, top_n=5)

    print("\n  V3 Top 5 Drawdowns:")
    dd_attribution = []
    for i, dd in enumerate(v3_drawdowns):
        # What regime was active during the drawdown?
        dd_regime = regime.loc[dd['start']:dd['trough']]
        regime_counts = dd_regime.value_counts()
        dominant_regime = regime_counts.index[0] if len(regime_counts) > 0 else 'N/A'
        # Average V3 position during drawdown
        avg_pos = v3_held.loc[dd['start']:dd['trough']].mean()
        # V1 drawdown over same period
        v1_dd_period = v1_ret.loc[dd['start']:dd['end']]
        v1_dd_cum = (1 + v1_dd_period.dropna()).cumprod()
        v1_dd_depth = (v1_dd_cum / v1_dd_cum.cummax() - 1).min() if len(v1_dd_cum) > 0 else np.nan
        # BTC drawdown over same period
        bh_dd_period = bh_ret.loc[dd['start']:dd['end']]
        bh_dd_cum = (1 + bh_dd_period.dropna()).cumprod()
        bh_dd_depth = (bh_dd_cum / bh_dd_cum.cummax() - 1).min() if len(bh_dd_cum) > 0 else np.nan

        ongoing = dd.get('ongoing', False)
        dd_info = {
            'rank': i+1,
            'start': dd['start'],
            'trough': dd['trough'],
            'end': dd['end'],
            'depth': dd['depth'],
            'duration': dd['duration_days'],
            'regime': dominant_regime,
            'regime_breakdown': dict(regime_counts),
            'avg_v3_pos': avg_pos,
            'v1_depth': v1_dd_depth,
            'bh_depth': bh_dd_depth,
            'ongoing': ongoing,
        }
        dd_attribution.append(dd_info)
        end_str = f"{dd['end'].date()}" + (" (ongoing)" if ongoing else "")
        print(f"  #{i+1}: {dd['depth']:.1%} | {dd['start'].date()} to {end_str} "
              f"| {dd['duration_days']}d | Regime: {dominant_regime} | Avg pos: {avg_pos:.2f} "
              f"| V1 DD: {v1_dd_depth:.1%} | BH DD: {bh_dd_depth:.1%}")

    print()

    # ══════════════════════════════════════════════════════════════════════════
    # TASK 5: CALENDAR ANALYSIS
    # ══════════════════════════════════════════════════════════════════════════
    print("=" * 78)
    print("TASK 5: Calendar Analysis (Monthly Return Heatmap)")
    print("=" * 78)

    v3_full = v3_ret[full_mask]
    v3_monthly = (1 + v3_full).resample('ME').prod() - 1
    v3_monthly_df = v3_monthly.to_frame('return')
    v3_monthly_df['year'] = v3_monthly_df.index.year
    v3_monthly_df['month'] = v3_monthly_df.index.month

    # Pivot for heatmap
    heatmap = v3_monthly_df.pivot_table(index='year', columns='month', values='return', aggfunc='sum')

    print("\n  V3 Monthly Returns (%):")
    month_names = {1:'Jan', 2:'Feb', 3:'Mar', 4:'Apr', 5:'May', 6:'Jun',
                   7:'Jul', 8:'Aug', 9:'Sep', 10:'Oct', 11:'Nov', 12:'Dec'}
    header = "  Year  | " + " | ".join(f"{month_names[m]:>6s}" for m in range(1,13)) + " | Annual"
    print(header)
    print("  " + "-" * (len(header) - 2))

    for year in sorted(heatmap.index):
        parts = [f"  {year}  "]
        annual = 0
        for m in range(1, 13):
            if m in heatmap.columns and not pd.isna(heatmap.loc[year, m]):
                val = heatmap.loc[year, m]
                annual += val
                parts.append(f"{val*100:6.1f}%")
            else:
                parts.append("     --")
        parts.append(f"{annual*100:6.1f}%")
        print(" | ".join(parts))

    # Average by month
    print()
    monthly_avg = heatmap.mean()
    monthly_std = heatmap.std()
    monthly_win_rate = (heatmap > 0).mean()

    print("  Monthly Averages and Win Rates:")
    for m in range(1, 13):
        if m in monthly_avg.index:
            avg = monthly_avg[m]
            std = monthly_std[m]
            wr = monthly_win_rate[m]
            print(f"    {month_names[m]}: avg={avg*100:+.1f}%, std={std*100:.1f}%, win_rate={wr*100:.0f}%")

    # Quarterly analysis
    v3_quarterly = (1 + v3_full).resample('QE').prod() - 1
    v3_quarterly_df = v3_quarterly.to_frame('return')
    v3_quarterly_df['year'] = v3_quarterly_df.index.year
    v3_quarterly_df['quarter'] = v3_quarterly_df.index.quarter
    qtr_avg = v3_quarterly_df.groupby('quarter')['return'].agg(['mean', 'std', 'count'])
    qtr_avg['win_rate'] = v3_quarterly_df.groupby('quarter')['return'].apply(lambda x: (x > 0).mean())

    print("\n  Quarterly Averages:")
    for q in range(1, 5):
        if q in qtr_avg.index:
            print(f"    Q{q}: avg={qtr_avg.loc[q, 'mean']*100:+.1f}%, std={qtr_avg.loc[q, 'std']*100:.1f}%, "
                  f"win_rate={qtr_avg.loc[q, 'win_rate']*100:.0f}%, N={qtr_avg.loc[q, 'count']:.0f}")

    print()

    # ══════════════════════════════════════════════════════════════════════════
    # TASK 6: WHAT KILLS V3?
    # ══════════════════════════════════════════════════════════════════════════
    print("=" * 78)
    print("TASK 6: What Kills V3?")
    print("=" * 78)

    # Weekly returns for consecutive losing weeks analysis
    v3_weekly = (1 + v3_full).resample('W-MON').prod() - 1

    # Maximum consecutive losing weeks
    is_losing = (v3_weekly < 0).astype(int)
    streak = 0
    max_streak = 0
    max_streak_end = None
    for dt, val in is_losing.items():
        if val == 1:
            streak += 1
            if streak > max_streak:
                max_streak = streak
                max_streak_end = dt
        else:
            streak = 0

    print(f"\n  Maximum consecutive losing weeks: {max_streak}")
    if max_streak_end is not None:
        streak_start = max_streak_end - pd.Timedelta(weeks=max_streak - 1)
        print(f"  Period: ~{streak_start.date()} to {max_streak_end.date()}")

    # Worst 10 weekly returns
    worst_weeks = v3_weekly.nsmallest(10)
    print("\n  Worst 10 Weekly Returns:")
    for dt, ret in worst_weeks.items():
        r = regime.loc[dt] if dt in regime.index else 'N/A'
        pos = v3_held.loc[dt] if dt in v3_held.index else np.nan
        btc_wk = bh_ret.loc[:dt].iloc[-5:].sum() if dt in bh_ret.index else np.nan
        print(f"    {dt.date()}: {ret:.2%} | Regime: {r} | V3 pos: {pos:.2f} | BTC week: {btc_wk:.2%}")

    # Worst 30-day rolling periods
    v3_30d_roll = v3_full.rolling(30).sum()
    worst_30d_idx = v3_30d_roll.nsmallest(5).index
    print("\n  Worst 30-Day Rolling Return Periods:")
    for dt in worst_30d_idx:
        ret_30d = v3_30d_roll.loc[dt]
        start = dt - pd.Timedelta(days=30)
        period_regime = regime.loc[start:dt].value_counts()
        avg_pos = v3_held.loc[start:dt].mean()
        print(f"    Ending {dt.date()}: {ret_30d:.2%} | Regimes: {dict(period_regime)} | Avg pos: {avg_pos:.2f}")

    # Analyze what conditions are present during worst periods
    print("\n  Conditions During V3's Worst Drawdowns:")
    for dd_info in dd_attribution[:3]:
        start = dd_info['start']
        trough = dd_info['trough']
        print(f"\n    DD #{dd_info['rank']}: {dd_info['depth']:.1%} ({start.date()} to {trough.date()})")
        # BTC price action
        btc_start = btc_daily['close'].loc[start]
        btc_trough = btc_daily['close'].loc[trough]
        btc_chg = (btc_trough / btc_start - 1)
        print(f"      BTC: {btc_start:.0f} -> {btc_trough:.0f} ({btc_chg:.1%})")
        # Regime breakdown
        print(f"      Regimes: {dd_info['regime_breakdown']}")
        # Overlay activity
        pos_z_period = pos_z.loc[start:trough].dropna()
        vrp_z_period = vrp_z.loc[start:trough].dropna()
        if len(pos_z_period) > 0:
            print(f"      Positioning z: avg={pos_z_period.mean():.2f}, range=[{pos_z_period.min():.2f}, {pos_z_period.max():.2f}]")
        if len(vrp_z_period) > 0:
            print(f"      VRP z: avg={vrp_z_period.mean():.2f}, range=[{vrp_z_period.min():.2f}, {vrp_z_period.max():.2f}]")
        print(f"      Avg V3 position: {dd_info['avg_v3_pos']:.2f}")
        print(f"      V1 drawdown over same period: {dd_info['v1_depth']:.1%}")
        print(f"      BH drawdown over same period: {dd_info['bh_depth']:.1%}")

    # V3 vulnerability analysis
    print("\n  V3 Vulnerability Analysis:")

    # Whipsaw detection: count regime transitions per 30d window
    regime_changes = (regime != regime.shift(1)).astype(int)
    regime_changes_30d = regime_changes.rolling(30).sum()
    high_whipsaw = regime_changes_30d > 4

    # V3 performance during high whipsaw periods
    whipsaw_mask = high_whipsaw & full_mask
    v3_whipsaw = v3_ret[whipsaw_mask]
    v3_no_whipsaw = v3_ret[~high_whipsaw & full_mask]

    if len(v3_whipsaw.dropna()) > 10:
        ws_sharpe = compute_metrics(v3_whipsaw)['sharpe']
        nws_sharpe = compute_metrics(v3_no_whipsaw)['sharpe']
        print(f"    Whipsaw periods (>4 regime changes/30d): {whipsaw_mask.sum()} days, Sharpe={ws_sharpe:.2f}")
        print(f"    Non-whipsaw periods: {(~high_whipsaw & full_mask).sum()} days, Sharpe={nws_sharpe:.2f}")

    # EMA crossover whipsaw: count 20/50 EMA crosses in 30d window
    ema_cross = ((ema20 > ema50) != (ema20.shift(1) > ema50.shift(1))).astype(int)
    ema_cross_30d = ema_cross.rolling(30).sum()
    ema_whipsaw = ema_cross_30d > 2

    ema_ws_mask = ema_whipsaw & full_mask
    if len(v3_ret[ema_ws_mask].dropna()) > 10:
        ema_ws_sharpe = compute_metrics(v3_ret[ema_ws_mask])['sharpe']
        print(f"    EMA whipsaw periods (>2 crosses/30d): {ema_ws_mask.sum()} days, Sharpe={ema_ws_sharpe:.2f}")

    print()

    # ══════════════════════════════════════════════════════════════════════════
    # OVERALL METRICS
    # ══════════════════════════════════════════════════════════════════════════
    print("=" * 78)
    print("OVERALL PERFORMANCE SUMMARY")
    print("=" * 78)

    for period_name, mask in [('IS', is_mask), ('OOS', oos_mask), ('FULL', full_mask)]:
        v1_m = compute_metrics(v1_ret[mask], f"V1 ({period_name})")
        v3_m = compute_metrics(v3_ret[mask], f"V3 ({period_name})")
        bh_m = compute_metrics(bh_ret[mask], f"BH ({period_name})")
        print(f"\n  {period_name} ({mask.sum()} days):")
        print(f"    BH:  AnnRet={bh_m['ann_return']:.1%}, Sharpe={bh_m['sharpe']:.2f}, MaxDD={bh_m['max_dd']:.1%}, PF={bh_m['profit_factor']:.2f}")
        print(f"    V1:  AnnRet={v1_m['ann_return']:.1%}, Sharpe={v1_m['sharpe']:.2f}, MaxDD={v1_m['max_dd']:.1%}, PF={v1_m['profit_factor']:.2f}")
        print(f"    V3:  AnnRet={v3_m['ann_return']:.1%}, Sharpe={v3_m['sharpe']:.2f}, MaxDD={v3_m['max_dd']:.1%}, PF={v3_m['profit_factor']:.2f}")
        alpha_ret = v3_m['ann_return'] - v1_m['ann_return']
        alpha_sharpe = v3_m['sharpe'] - v1_m['sharpe']
        alpha_dd = v3_m['max_dd'] - v1_m['max_dd']
        print(f"    Overlay alpha (V3-V1): dReturn={alpha_ret:+.1%}, dSharpe={alpha_sharpe:+.3f}, dMaxDD={alpha_dd:+.1%}")

    print()

    # ══════════════════════════════════════════════════════════════════════════
    # GENERATE REPORT
    # ══════════════════════════════════════════════════════════════════════════
    print("Generating report...")

    lines = []
    lines.append("# R68: V3 Momentum Strategy — Regime Analysis Results")
    lines.append("")
    lines.append(f"**Run date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**IS period**: {IS_START} to {IS_END}")
    lines.append(f"**OOS period**: {OOS_START} to latest data ({btc_daily.index.max().date()})")
    lines.append(f"**Rebalancing**: Weekly (Monday)")
    lines.append(f"**Transaction cost**: {COST_BPS} bps round-trip")
    lines.append("")
    lines.append("## Strategy Definition")
    lines.append("")
    lines.append("- **V1 (Base)**: Long when 20d EMA > 50d EMA, flat otherwise. No stop losses.")
    lines.append("- **V3 (Full)**: V1 + Positioning overlay + VRP overlay")
    lines.append("  - Positioning: Binance Top Trader L/S + L/S Divergence combined z-score (30d rolling)")
    lines.append("    - z>1.5 -> 0.3x, z>0.5 -> 0.5x, neutral -> 1.0x, z<-0.5 -> 1.3x, z<-1.5 -> 1.5x")
    lines.append("  - VRP: (IV - RV) z-score over 60d")
    lines.append("    - z>1 -> 1.3x, z>-0.5 -> 1.0x, z>-1.5 -> 0.5x, z<-1.5 -> 0.3x")
    lines.append("  - Position range: [0, 1.5x]. Weekly rebalancing.")
    lines.append("")
    lines.append("## Regime Definition")
    lines.append("")
    lines.append("- **UPTREND**: 50d SMA > 200d SMA AND close > 50d SMA")
    lines.append("- **DOWNTREND**: 50d SMA < 200d SMA AND close < 50d SMA")
    lines.append("- **RANGE**: 50d SMA and 200d SMA within 5% OR close between the two SMAs")
    lines.append("- **CRISIS**: 50d return < -20% (overrides other regimes)")
    lines.append("")

    # ── Section 1: Overall Performance ──
    lines.append("---")
    lines.append("")
    lines.append("## 1. Overall Performance Summary")
    lines.append("")
    lines.append("| Strategy | Period | Ann Return | Sharpe | MaxDD | Calmar | Profit Factor | Days |")
    lines.append("|----------|--------|------------|--------|-------|--------|---------------|------|")

    for period_name, mask in [('IS', is_mask), ('OOS', oos_mask), ('FULL', full_mask)]:
        for strat_name, strat_ret_s in [('BH', bh_ret), ('V1', v1_ret), ('V3', v3_ret)]:
            m = compute_metrics(strat_ret_s[mask])
            pf_str = f"{m['profit_factor']:.2f}" if not np.isinf(m['profit_factor']) else "inf"
            lines.append(f"| {strat_name} | {period_name} | {m['ann_return']:.1%} | {m['sharpe']:.2f} | {m['max_dd']:.1%} | {m['calmar']:.2f} | {pf_str} | {m['n_days']} |")

    lines.append("")

    # Overlay alpha
    lines.append("### Overlay Alpha (V3 - V1)")
    lines.append("")
    lines.append("| Period | dReturn | dSharpe | dMaxDD |")
    lines.append("|--------|---------|---------|--------|")
    for period_name, mask in [('IS', is_mask), ('OOS', oos_mask), ('FULL', full_mask)]:
        v1_m = compute_metrics(v1_ret[mask])
        v3_m = compute_metrics(v3_ret[mask])
        dr = v3_m['ann_return'] - v1_m['ann_return']
        ds = v3_m['sharpe'] - v1_m['sharpe']
        dd = v3_m['max_dd'] - v1_m['max_dd']
        lines.append(f"| {period_name} | {dr:+.1%} | {ds:+.3f} | {dd:+.1%} |")

    lines.append("")

    # ── Section 2: Regime-Specific Performance ──
    lines.append("---")
    lines.append("")
    lines.append("## 2. Regime-Specific Performance")
    lines.append("")

    # Regime day counts
    lines.append("### Regime Distribution")
    lines.append("")
    lines.append("| Regime | IS Days | IS % | OOS Days | OOS % | Total Days | Total % |")
    lines.append("|--------|---------|------|----------|-------|------------|---------|")
    for r in ['UPTREND', 'DOWNTREND', 'RANGE', 'CRISIS']:
        is_n = ((regime == r) & is_mask).sum()
        oos_n = ((regime == r) & oos_mask).sum()
        full_n = ((regime == r) & full_mask).sum()
        is_pct = 100 * is_n / is_mask.sum() if is_mask.sum() > 0 else 0
        oos_pct = 100 * oos_n / oos_mask.sum() if oos_mask.sum() > 0 else 0
        full_pct = 100 * full_n / full_mask.sum() if full_mask.sum() > 0 else 0
        lines.append(f"| {r} | {is_n} | {is_pct:.0f}% | {oos_n} | {oos_pct:.0f}% | {full_n} | {full_pct:.0f}% |")

    lines.append("")

    # Regime performance tables for each period
    for period_name in ['IS', 'OOS', 'FULL']:
        lines.append(f"### {period_name} Regime Performance")
        lines.append("")
        lines.append(f"| Regime | V1 AnnRet | V1 Sharpe | V1 MaxDD | V3 AnnRet | V3 Sharpe | V3 MaxDD | V3 PF | Overlay Alpha (dSharpe) | Days |")
        lines.append(f"|--------|-----------|-----------|----------|-----------|-----------|----------|-------|-------------------------|------|")

        for r in ['UPTREND', 'DOWNTREND', 'RANGE', 'CRISIS']:
            rm = regime_metrics[period_name][r]
            v1_m = rm['v1']
            v3_m = rm['v3']
            n = rm['n_days']

            if pd.isna(v1_m['sharpe']):
                lines.append(f"| {r} | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | {n} |")
            else:
                d_sharpe = v3_m['sharpe'] - v1_m['sharpe']
                pf_str = f"{v3_m['profit_factor']:.2f}" if not np.isinf(v3_m.get('profit_factor', 0)) else "inf"
                lines.append(f"| {r} | {v1_m['ann_return']:.1%} | {v1_m['sharpe']:.2f} | {v1_m['max_dd']:.1%} | "
                             f"{v3_m['ann_return']:.1%} | {v3_m['sharpe']:.2f} | {v3_m['max_dd']:.1%} | {pf_str} | {d_sharpe:+.3f} | {n} |")

        lines.append("")

    # ── Section 3: Regime Transition Analysis ──
    lines.append("---")
    lines.append("")
    lines.append("## 3. Regime Transition Analysis")
    lines.append("")
    lines.append("10-day return after each regime transition:")
    lines.append("")
    lines.append("| Transition | Count | V1 Avg 10d | V3 Avg 10d | BH Avg 10d | V3 Better? |")
    lines.append("|------------|-------|------------|------------|------------|------------|")

    for (from_r, to_r), ts in transition_summary.items():
        v3_better = "YES" if ts['avg_v3_10d'] > ts['avg_v1_10d'] else "NO"
        lines.append(f"| {from_r} -> {to_r} | {ts['count']} | {ts['avg_v1_10d']:.2%} | {ts['avg_v3_10d']:.2%} | {ts['avg_bh_10d']:.2%} | {v3_better} |")

    lines.append("")

    # All transition matrix
    lines.append("### Full Transition Count Matrix")
    lines.append("")
    all_from = tr_df['from'].unique()
    all_to = tr_df['to'].unique()
    all_regimes_list = sorted(set(list(all_from) + list(all_to)))
    header = "| From \\\\ To | " + " | ".join(all_regimes_list) + " |"
    sep = "|" + "---|" * (len(all_regimes_list) + 1)
    lines.append(header)
    lines.append(sep)
    for fr in all_regimes_list:
        parts = [f"| {fr}"]
        for to in all_regimes_list:
            cnt = len(tr_df[(tr_df['from'] == fr) & (tr_df['to'] == to)])
            parts.append(str(cnt))
        lines.append(" | ".join(parts) + " |")

    lines.append("")

    # ── Section 4: Position Sizing Distribution Per Regime ──
    lines.append("---")
    lines.append("")
    lines.append("## 4. Position Sizing Distribution Per Regime")
    lines.append("")
    lines.append("V3 held position statistics by regime (full period):")
    lines.append("")
    lines.append("| Regime | Mean Pos | Median Pos | Std | % Flat | % Reduced (<0.9) | % Full (0.9-1.1) | % Levered (>1.1) |")
    lines.append("|--------|----------|------------|-----|--------|-------------------|-------------------|------------------|")

    for r in ['UPTREND', 'DOWNTREND', 'RANGE', 'CRISIS']:
        if r in pos_size_by_regime:
            ps = pos_size_by_regime[r]
            lines.append(f"| {r} | {ps['mean']:.3f} | {ps['median']:.3f} | {ps['std']:.3f} | "
                         f"{ps['pct_flat']:.1f}% | {ps['pct_reduced']:.1f}% | {ps['pct_full']:.1f}% | {ps['pct_levered']:.1f}% |")

    lines.append("")
    lines.append("**Expected behavior**: Overlays should reduce position in CRISIS/DOWNTREND and maintain/boost in UPTREND.")
    lines.append("")

    # ── Section 5: Drawdown Attribution ──
    lines.append("---")
    lines.append("")
    lines.append("## 5. Drawdown Attribution")
    lines.append("")
    lines.append("### V3 Top 5 Drawdowns")
    lines.append("")
    lines.append("| # | Depth | Start | Trough | End | Duration | Dominant Regime | Avg V3 Pos | V1 DD | BH DD | Overlay Helped? |")
    lines.append("|---|-------|-------|--------|-----|----------|-----------------|------------|-------|-------|-----------------|")

    for dd_info in dd_attribution:
        ongoing = " (ongoing)" if dd_info.get('ongoing', False) else ""
        helped = "YES" if dd_info['depth'] > dd_info['v1_depth'] else "NO"  # V3 depth less negative = helped
        lines.append(f"| {dd_info['rank']} | {dd_info['depth']:.1%} | {dd_info['start'].date()} | {dd_info['trough'].date()} | "
                     f"{dd_info['end'].date()}{ongoing} | {dd_info['duration']}d | {dd_info['regime']} | "
                     f"{dd_info['avg_v3_pos']:.2f} | {dd_info['v1_depth']:.1%} | {dd_info['bh_depth']:.1%} | {helped} |")

    lines.append("")
    lines.append("*Overlay Helped = V3 drawdown shallower than V1 (YES means V3 depth > V1 depth, i.e., less negative)*")
    lines.append("")

    # Detailed drawdown narrative
    lines.append("### Drawdown Narratives")
    lines.append("")
    for dd_info in dd_attribution[:3]:
        start = dd_info['start']
        trough = dd_info['trough']
        lines.append(f"**DD #{dd_info['rank']}: {dd_info['depth']:.1%}** ({start.date()} to {trough.date()})")
        lines.append("")
        btc_start_p = btc_daily['close'].loc[start]
        btc_trough_p = btc_daily['close'].loc[trough]
        btc_chg = (btc_trough_p / btc_start_p - 1)
        lines.append(f"- BTC price: ${btc_start_p:,.0f} -> ${btc_trough_p:,.0f} ({btc_chg:.1%})")
        regime_str = ", ".join(f"{k}: {int(v)}" for k, v in dd_info['regime_breakdown'].items())
        lines.append(f"- Regime breakdown: {regime_str}")
        lines.append(f"- Average V3 position: {dd_info['avg_v3_pos']:.2f}")
        lines.append(f"- V1 drawdown: {dd_info['v1_depth']:.1%}, BH drawdown: {dd_info['bh_depth']:.1%}")

        pos_z_period = pos_z.loc[start:trough].dropna()
        vrp_z_period = vrp_z.loc[start:trough].dropna()
        if len(pos_z_period) > 0:
            lines.append(f"- Positioning z: avg={pos_z_period.mean():.2f}")
        if len(vrp_z_period) > 0:
            lines.append(f"- VRP z: avg={vrp_z_period.mean():.2f}")

        if dd_info['depth'] > dd_info['v1_depth']:
            lines.append(f"- **Overlay HELPED**: V3 drawdown ({dd_info['depth']:.1%}) shallower than V1 ({dd_info['v1_depth']:.1%})")
        else:
            lines.append(f"- **Overlay HURT**: V3 drawdown ({dd_info['depth']:.1%}) deeper than V1 ({dd_info['v1_depth']:.1%})")
        lines.append("")

    # ── Section 6: Calendar Analysis ──
    lines.append("---")
    lines.append("")
    lines.append("## 6. Calendar Analysis")
    lines.append("")
    lines.append("### V3 Monthly Return Heatmap (%)")
    lines.append("")

    months_header = "| Year | " + " | ".join(month_names[m] for m in range(1, 13)) + " | Annual |"
    months_sep = "|------|" + "------|" * 13
    lines.append(months_header)
    lines.append(months_sep)

    for year in sorted(heatmap.index):
        parts = [f"| {year}"]
        annual = 0
        for m in range(1, 13):
            if m in heatmap.columns and not pd.isna(heatmap.loc[year, m]):
                val = heatmap.loc[year, m]
                annual += val
                parts.append(f"{val*100:+.1f}%")
            else:
                parts.append("--")
        parts.append(f"**{annual*100:+.1f}%**")
        lines.append(" | ".join(parts) + " |")

    lines.append("")

    # Monthly averages
    lines.append("### Monthly Averages and Win Rates")
    lines.append("")
    lines.append("| Month | Avg Return | Std Dev | Win Rate |")
    lines.append("|-------|------------|---------|----------|")

    for m in range(1, 13):
        if m in monthly_avg.index:
            lines.append(f"| {month_names[m]} | {monthly_avg[m]*100:+.1f}% | {monthly_std[m]*100:.1f}% | {monthly_win_rate[m]*100:.0f}% |")

    lines.append("")

    # Quarterly
    lines.append("### Quarterly Performance")
    lines.append("")
    lines.append("| Quarter | Avg Return | Std Dev | Win Rate | N |")
    lines.append("|---------|------------|---------|----------|---|")
    for q in range(1, 5):
        if q in qtr_avg.index:
            lines.append(f"| Q{q} | {qtr_avg.loc[q, 'mean']*100:+.1f}% | {qtr_avg.loc[q, 'std']*100:.1f}% | "
                         f"{qtr_avg.loc[q, 'win_rate']*100:.0f}% | {qtr_avg.loc[q, 'count']:.0f} |")

    lines.append("")

    # Seasonality flag
    worst_months = monthly_avg.nsmallest(3)
    best_months = monthly_avg.nlargest(3)
    lines.append("### Seasonality Summary")
    lines.append("")
    lines.append(f"**Best months**: {', '.join(month_names[m] for m in best_months.index)} "
                 f"(avg: {', '.join(f'{v*100:+.1f}%' for v in best_months.values)})")
    lines.append(f"**Worst months**: {', '.join(month_names[m] for m in worst_months.index)} "
                 f"(avg: {', '.join(f'{v*100:+.1f}%' for v in worst_months.values)})")
    lines.append("")

    # ── Section 7: What Kills V3? ──
    lines.append("---")
    lines.append("")
    lines.append("## 7. What Kills V3?")
    lines.append("")

    lines.append("### Maximum Consecutive Losing Weeks")
    lines.append("")
    lines.append(f"- **{max_streak} weeks** ending around {max_streak_end.date() if max_streak_end else 'N/A'}")
    lines.append("")

    lines.append("### Worst 10 Weekly Returns")
    lines.append("")
    lines.append("| Week | Return | Regime | V3 Position | BTC Weekly |")
    lines.append("|------|--------|--------|-------------|------------|")
    for dt, ret in worst_weeks.items():
        r = regime.loc[dt] if dt in regime.index else 'N/A'
        pos = v3_held.loc[dt] if dt in v3_held.index else np.nan
        btc_start_idx = btc_daily.index.get_loc(dt)
        btc_start_idx = max(0, btc_start_idx - 4)
        btc_wk = bh_ret.iloc[btc_start_idx:btc_start_idx+5].sum()
        lines.append(f"| {dt.date()} | {ret:.2%} | {r} | {pos:.2f} | {btc_wk:.2%} |")

    lines.append("")

    lines.append("### Worst 30-Day Rolling Return Periods")
    lines.append("")
    lines.append("| End Date | 30d Return | Dominant Regime | Avg Position |")
    lines.append("|----------|------------|-----------------|--------------|")
    for dt in worst_30d_idx:
        ret_30d = v3_30d_roll.loc[dt]
        start = dt - pd.Timedelta(days=30)
        period_regime = regime.loc[start:dt].value_counts()
        dominant = period_regime.index[0] if len(period_regime) > 0 else 'N/A'
        avg_pos = v3_held.loc[start:dt].mean()
        lines.append(f"| {dt.date()} | {ret_30d:.2%} | {dominant} | {avg_pos:.2f} |")

    lines.append("")

    # Vulnerability analysis
    lines.append("### Vulnerability Analysis")
    lines.append("")

    if len(v3_whipsaw.dropna()) > 10:
        ws_m = compute_metrics(v3_whipsaw)
        nws_m = compute_metrics(v3_no_whipsaw)
        lines.append(f"**Regime whipsaw** (>4 transitions in 30 days):")
        lines.append(f"- Whipsaw periods: {whipsaw_mask.sum()} days, Sharpe={ws_m['sharpe']:.2f}, AnnRet={ws_m['ann_return']:.1%}")
        lines.append(f"- Non-whipsaw periods: Sharpe={nws_m['sharpe']:.2f}, AnnRet={nws_m['ann_return']:.1%}")
        lines.append("")

    if len(v3_ret[ema_ws_mask].dropna()) > 10:
        ema_ws_m = compute_metrics(v3_ret[ema_ws_mask])
        lines.append(f"**EMA whipsaw** (>2 EMA 20/50 crosses in 30 days):")
        lines.append(f"- {ema_ws_mask.sum()} days, Sharpe={ema_ws_m['sharpe']:.2f}, AnnRet={ema_ws_m['ann_return']:.1%}")
        lines.append("")

    # Identify the kill scenarios
    lines.append("### Kill Scenarios (When V3 Loses Money)")
    lines.append("")
    lines.append("Based on the analysis above, V3 is most vulnerable to:")
    lines.append("")

    # Analyze which conditions coincide with worst returns
    kill_conditions = []

    # 1. Check if V3 struggles in RANGE regime
    range_is = regime_metrics.get('IS', {}).get('RANGE', {}).get('v3', {})
    range_oos = regime_metrics.get('OOS', {}).get('RANGE', {}).get('v3', {})
    if not pd.isna(range_is.get('sharpe', np.nan)) and range_is['sharpe'] < 0.3:
        kill_conditions.append(f"1. **RANGE regime**: IS Sharpe={range_is['sharpe']:.2f}. The EMA crossover generates false signals in range-bound markets.")
    if not pd.isna(range_oos.get('sharpe', np.nan)) and range_oos['sharpe'] < 0.3:
        kill_conditions.append(f"1. **RANGE regime (OOS)**: Sharpe={range_oos['sharpe']:.2f}. Choppy markets cause whipsaw losses.")

    # 2. Check crisis behavior
    crisis_is = regime_metrics.get('IS', {}).get('CRISIS', {}).get('v3', {})
    if not pd.isna(crisis_is.get('sharpe', np.nan)):
        if crisis_is['ann_return'] < -0.10:
            kill_conditions.append(f"2. **CRISIS regime**: AnnRet={crisis_is['ann_return']:.1%}. The EMA signal is too slow to exit during crashes.")

    # 3. Transition losses
    for (from_r, to_r), ts in transition_summary.items():
        if ts['avg_v3_10d'] < -0.03:
            kill_conditions.append(f"3. **{from_r}->{to_r} transitions**: Avg 10d return={ts['avg_v3_10d']:.2%}. V3 is slow to react.")

    # 4. Whipsaw vulnerability
    if len(v3_whipsaw.dropna()) > 10:
        ws_ann = compute_metrics(v3_whipsaw)['ann_return']
        if ws_ann < 0:
            kill_conditions.append(f"4. **Whipsaw periods**: AnnRet={ws_ann:.1%}. Frequent regime changes cause losses from rebalancing costs and mis-timing.")

    if not kill_conditions:
        kill_conditions.append("No major kill scenarios identified -- V3 is relatively robust.")

    for kc in kill_conditions:
        lines.append(kc)
    lines.append("")

    # ── Section 8: Actionable Summary ──
    lines.append("---")
    lines.append("")
    lines.append("## 8. Actionable Summary")
    lines.append("")

    # Compute key numbers for summary
    is_v3 = compute_metrics(v3_ret[is_mask])
    oos_v3 = compute_metrics(v3_ret[oos_mask])
    is_v1 = compute_metrics(v1_ret[is_mask])
    oos_v1 = compute_metrics(v1_ret[oos_mask])

    lines.append("### Strategy Health")
    lines.append("")
    lines.append(f"| Metric | V1 IS | V1 OOS | V3 IS | V3 OOS |")
    lines.append(f"|--------|-------|--------|-------|--------|")
    lines.append(f"| Ann Return | {is_v1['ann_return']:.1%} | {oos_v1['ann_return']:.1%} | {is_v3['ann_return']:.1%} | {oos_v3['ann_return']:.1%} |")
    lines.append(f"| Sharpe | {is_v1['sharpe']:.2f} | {oos_v1['sharpe']:.2f} | {is_v3['sharpe']:.2f} | {oos_v3['sharpe']:.2f} |")
    lines.append(f"| MaxDD | {is_v1['max_dd']:.1%} | {oos_v1['max_dd']:.1%} | {is_v3['max_dd']:.1%} | {oos_v3['max_dd']:.1%} |")
    lines.append("")

    # Regime worry list
    lines.append("### Regimes to Worry About")
    lines.append("")

    worry_regimes = []
    for r in ['UPTREND', 'DOWNTREND', 'RANGE', 'CRISIS']:
        full_rm = regime_metrics['FULL'].get(r, {}).get('v3', {})
        if pd.isna(full_rm.get('sharpe', np.nan)):
            continue
        if full_rm['sharpe'] < 0.3:
            worry_regimes.append((r, full_rm['sharpe'], full_rm['ann_return']))

    if worry_regimes:
        for r, sharpe, ann_ret in worry_regimes:
            lines.append(f"- **{r}**: Sharpe={sharpe:.2f}, AnnRet={ann_ret:.1%} -- V3 underperforms here")
    else:
        lines.append("- No regimes with Sharpe < 0.3 (strategy is reasonably balanced)")

    lines.append("")

    # Does the overlay help?
    lines.append("### Does the Overlay Help?")
    lines.append("")

    overlay_helps_regimes = []
    overlay_hurts_regimes = []
    for r in ['UPTREND', 'DOWNTREND', 'RANGE', 'CRISIS']:
        v1_full = regime_metrics['FULL'].get(r, {}).get('v1', {})
        v3_full = regime_metrics['FULL'].get(r, {}).get('v3', {})
        if pd.isna(v1_full.get('sharpe', np.nan)) or pd.isna(v3_full.get('sharpe', np.nan)):
            continue
        d_sharpe = v3_full['sharpe'] - v1_full['sharpe']
        if d_sharpe > 0.05:
            overlay_helps_regimes.append((r, d_sharpe))
        elif d_sharpe < -0.05:
            overlay_hurts_regimes.append((r, d_sharpe))

    if overlay_helps_regimes:
        lines.append("**Overlay HELPS in:**")
        for r, ds in overlay_helps_regimes:
            lines.append(f"- {r}: +{ds:.3f} Sharpe improvement")
    if overlay_hurts_regimes:
        lines.append("")
        lines.append("**Overlay HURTS in:**")
        for r, ds in overlay_hurts_regimes:
            lines.append(f"- {r}: {ds:.3f} Sharpe degradation")
    if not overlay_helps_regimes and not overlay_hurts_regimes:
        lines.append("- Overlay has minimal impact across all regimes (dSharpe within +/-0.05)")

    lines.append("")

    # Kill signals
    lines.append("### Kill Signals (When to Reduce/Stop V3)")
    lines.append("")
    lines.append("Based on this analysis, consider reducing V3 exposure when:")
    lines.append("")

    # Derive from data
    kill_signals = []
    # Range regime detection
    range_sharpe_full = regime_metrics['FULL'].get('RANGE', {}).get('v3', {}).get('sharpe', 0)
    if range_sharpe_full < 0.5:
        kill_signals.append("1. **Regime enters RANGE**: SMAs converging with no clear trend. "
                            f"V3 Sharpe in RANGE = {range_sharpe_full:.2f}. Consider reducing position to 0.5x.")

    # Crisis detection
    crisis_sharpe_full = regime_metrics['FULL'].get('CRISIS', {}).get('v3', {}).get('sharpe', 0)
    kill_signals.append(f"2. **50d return drops below -15%**: Approaching CRISIS territory. "
                        f"V3 Sharpe in CRISIS = {crisis_sharpe_full:.2f}. The EMA signal is a lagging indicator.")

    # Whipsaw detection
    if len(v3_whipsaw.dropna()) > 10:
        ws_sharpe_val = compute_metrics(v3_whipsaw)['sharpe']
        kill_signals.append(f"3. **>4 regime transitions in 30 days**: Whipsaw indicator. "
                            f"V3 Sharpe during whipsaw = {ws_sharpe_val:.2f}.")

    # EMA whipsaw
    if len(v3_ret[ema_ws_mask].dropna()) > 10:
        ema_ws_sharpe_val = compute_metrics(v3_ret[ema_ws_mask])['sharpe']
        kill_signals.append(f"4. **>2 EMA crosses in 30 days**: EMA whipsaw indicator. "
                            f"V3 Sharpe during EMA whipsaw = {ema_ws_sharpe_val:.2f}.")

    kill_signals.append(f"5. **Max consecutive losing weeks hits {max_streak - 2}+**: "
                        f"Historical max is {max_streak} weeks. Exceeding this may indicate regime shift.")

    for ks in kill_signals:
        lines.append(ks)

    lines.append("")

    # Final bottom line
    lines.append("### Bottom Line")
    lines.append("")

    is_degradation = is_v3['sharpe'] - oos_v3['sharpe']
    if is_degradation > 0.3:
        lines.append(f"**WARNING**: Significant IS-to-OOS degradation in Sharpe ({is_v3['sharpe']:.2f} -> {oos_v3['sharpe']:.2f}). "
                     f"This suggests possible overfitting of overlay parameters.")
    elif oos_v3['sharpe'] > oos_v1['sharpe']:
        lines.append(f"V3 outperforms V1 in OOS (Sharpe {oos_v3['sharpe']:.2f} vs {oos_v1['sharpe']:.2f}). "
                     f"The overlay adds value, primarily through {'drawdown reduction' if oos_v3['max_dd'] > oos_v1['max_dd'] else 'return enhancement'}.")
    else:
        lines.append(f"V3 underperforms V1 in OOS (Sharpe {oos_v3['sharpe']:.2f} vs {oos_v1['sharpe']:.2f}). "
                     f"The overlay may be adding noise. Consider simplifying to V1 only or re-tuning overlay thresholds.")

    lines.append("")
    lines.append(f"Main risk: **{worry_regimes[0][0] if worry_regimes else 'N/A'}** regime "
                 f"{'accounts for the bulk of losses' if worry_regimes else 'is not a major concern'}. "
                 f"The EMA crossover's main weakness is response time -- it enters late and exits late, "
                 f"which the positioning/VRP overlays can partially compensate for but not fully eliminate.")
    lines.append("")

    # Write report
    report_path = OUTPUT_DIR / 'v3_regime_analysis_results.md'
    with open(report_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f"\nReport saved to: {report_path}")
    print("Done.")


if __name__ == '__main__':
    main()
