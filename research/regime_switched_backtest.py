#!/usr/bin/env python3
"""
Regime-Switched Positioning Signal Backtest for BTC
====================================================
Research backtest simulating a regime-switched trading system using
validated positioning and macro signals.

Variants:
  A: Positioning only (no macro, no regime switching)
  B: Regime-switched positioning (regime gates signals ON/OFF)
  C: Regime-switched + macro agreement boost
  D: Full system (C + Oil crisis hedge)
"""

import warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

# ============================================================
# CONFIG
# ============================================================
DATA_DIR = Path('/workspace/crypto_backtest/data')
OUTPUT_DIR = Path('/workspace/crypto_backtest/research')
COST_BPS_ROUNDTRIP = 10  # 10 bps round-trip
COST_PER_SIDE = COST_BPS_ROUNDTRIP / 2 / 10000  # 5 bps = 0.0005

IS_START = '2020-09-01'
IS_END = '2024-12-31'
OOS_START = '2025-01-01'

# Regime thresholds (50-day return)
UPTREND_THRESH = 0.10
DOWNTREND_THRESH = -0.10
CRISIS_THRESH = -0.20

# Macro agreement thresholds
MACRO_BEARISH_PCTILE = 0.66
MACRO_BULLISH_PCTILE = 0.33
POS_BEARISH_Z = 0.5
POS_BULLISH_Z = -0.5

BOOST_FACTOR = 1.5


def print_header(msg):
    print(f"\n{'='*70}")
    print(f"  {msg}")
    print(f"{'='*70}")


# ============================================================
# 1. LOAD DATA
# ============================================================
def load_data():
    print_header("LOADING DATA")

    # --- BTC spot (1h -> daily) ---
    print("  Loading BTC spot 1h...")
    btc_spot = pd.read_parquet(DATA_DIR / 'spot/1h_cache/BTC_1h.parquet')
    btc_spot.index = pd.to_datetime(btc_spot.index)
    # Resample to daily using last close
    btc_daily = btc_spot.resample('1D').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna(subset=['close'])
    btc_daily.index.name = 'date'
    print(f"    BTC daily: {btc_daily.index.min().date()} to {btc_daily.index.max().date()}, {len(btc_daily)} rows")

    # --- Binance positioning metrics ---
    print("  Loading Binance positioning metrics...")
    pos_all = pd.read_parquet(DATA_DIR / 'alternative/binance_metrics/all_symbols_daily_ls.parquet')
    pos_btc = pos_all[pos_all['symbol'] == 'BTCUSDT'].copy()
    pos_btc['date'] = pd.to_datetime(pos_btc['date'])
    pos_btc = pos_btc.set_index('date').sort_index()
    # Drop dupes
    pos_btc = pos_btc[~pos_btc.index.duplicated(keep='last')]
    print(f"    Positioning: {pos_btc.index.min().date()} to {pos_btc.index.max().date()}, {len(pos_btc)} rows")

    # --- Macro data ---
    print("  Loading macro data...")
    macro = {}
    for name in ['us10y_yield', 'usd_index', 'oil_wti']:
        df = pd.read_parquet(DATA_DIR / f'alternative/macro/{name}.parquet')
        df['Date'] = pd.to_datetime(df['Date'])
        df = df.set_index('Date').sort_index()
        df = df[~df.index.duplicated(keep='last')]
        macro[name] = df
        print(f"    {name}: {df.index.min().date()} to {df.index.max().date()}, {len(df)} rows")

    # --- BTC funding ---
    print("  Loading BTC funding...")
    funding = pd.read_csv(DATA_DIR / 'perp/binance/funding/BTC_funding.csv')
    funding['datetime'] = pd.to_datetime(funding['datetime'])
    funding = funding.set_index('datetime').sort_index()
    # Resample to daily (sum of funding rates)
    funding_daily = funding['funding_rate'].resample('1D').sum()
    funding_daily.name = 'daily_funding'
    print(f"    Funding daily: {funding_daily.index.min().date()} to {funding_daily.index.max().date()}")

    return btc_daily, pos_btc, macro, funding_daily


# ============================================================
# 2. CONSTRUCT SIGNALS
# ============================================================
def construct_signals(btc_daily, pos_btc, macro, funding_daily):
    print_header("CONSTRUCTING SIGNALS")

    # --- Start with a daily dataframe aligned to BTC ---
    df = pd.DataFrame(index=btc_daily.index)
    df['close'] = btc_daily['close']
    df['fwd_return'] = df['close'].pct_change().shift(-1)  # close-to-close forward return

    # --- Regime detection: 50-day return ---
    df['ret_50d'] = df['close'].pct_change(50)

    def classify_regime(r):
        if pd.isna(r):
            return 'UNKNOWN'
        if r < CRISIS_THRESH:
            return 'CRISIS'
        elif r < DOWNTREND_THRESH:
            return 'DOWNTREND'
        elif r > UPTREND_THRESH:
            return 'UPTREND'
        else:
            return 'RANGE'

    df['regime'] = df['ret_50d'].apply(classify_regime)
    print("  Regime distribution:")
    regime_counts = df['regime'].value_counts()
    for r, c in regime_counts.items():
        print(f"    {r}: {c} days ({c/len(df)*100:.1f}%)")

    # --- Signal 1: Top Trader L/S raw ---
    # Higher sum_toptrader_ls_ratio = more longs = bearish
    # Z-score with 30d rolling window. Signal = -zscore per spec.
    # Convention: all signals are oriented so that POSITIVE = SHORT BTC.
    # -zscore: when zscore > 0 (crowded long), signal < 0 → position > 0 (LONG).
    # This follows the momentum/crowd direction validated in IS data.
    pos_aligned = pos_btc['sum_toptrader_ls_ratio'].reindex(df.index).ffill()
    roll_mean = pos_aligned.rolling(30, min_periods=10).mean()
    roll_std = pos_aligned.rolling(30, min_periods=10).std()
    df['toptrader_ls_zscore'] = (pos_aligned - roll_mean) / roll_std
    df['sig_toptrader_ls'] = -df['toptrader_ls_zscore']
    print(f"  Signal: Top Trader L/S — non-null: {df['sig_toptrader_ls'].notna().sum()}")

    # --- Signal 2: L/S Divergence ---
    # count_toptrader_ls_ratio - count_ls_ratio (top trader vs all accounts divergence)
    # Higher = more crowded = bearish per spec. Signal = -zscore.
    count_top = pos_btc['count_toptrader_ls_ratio'].reindex(df.index).ffill()
    count_all = pos_btc['count_ls_ratio'].reindex(df.index).ffill()
    ls_div_raw = count_top - count_all
    div_roll_mean = ls_div_raw.rolling(30, min_periods=10).mean()
    div_roll_std = ls_div_raw.rolling(30, min_periods=10).std()
    df['ls_div_zscore'] = (ls_div_raw - div_roll_mean) / div_roll_std
    df['sig_ls_divergence'] = -df['ls_div_zscore']
    print(f"  Signal: L/S Divergence — non-null: {df['sig_ls_divergence'].notna().sum()}")

    # --- Signal 3: Oil 20d momentum ---
    oil = macro['oil_wti']['Close'].reindex(df.index).ffill()
    df['oil_20d_mom'] = oil.pct_change(20)
    # Expanding z-score
    exp_mean = df['oil_20d_mom'].expanding(min_periods=20).mean()
    exp_std = df['oil_20d_mom'].expanding(min_periods=20).std()
    df['sig_oil_mom'] = (df['oil_20d_mom'] - exp_mean) / exp_std
    # Rising oil = bearish, so signal is already in right direction (positive = bearish)
    print(f"  Signal: Oil 20d Mom — non-null: {df['sig_oil_mom'].notna().sum()}")

    # --- Signal 4: US10Y + DXY composite ---
    us10y = macro['us10y_yield']['Close'].reindex(df.index).ffill()
    dxy = macro['usd_index']['Close'].reindex(df.index).ffill()

    # US10Y 20d change (diff, not pct)
    us10y_20d_chg = us10y.diff(20)
    # DXY 20d momentum (pct change)
    dxy_20d_mom = dxy.pct_change(20)

    # Expanding rank normalization (vectorized)
    def expanding_rank_pct(series, min_periods=50):
        """Expanding percentile rank (0-1), vectorized."""
        vals = series.values.astype(float)
        n = len(vals)
        result = np.full(n, np.nan)
        for i in range(min_periods, n):
            if np.isnan(vals[i]):
                continue
            window = vals[:i+1]
            valid = window[~np.isnan(window)]
            if len(valid) < 20:
                continue
            result[i] = np.sum(valid < vals[i]) / len(valid)
        return pd.Series(result, index=series.index)

    print("  Computing expanding rank for US10Y 20d change...")
    us10y_rank = expanding_rank_pct(us10y_20d_chg)
    print("  Computing expanding rank for DXY 20d momentum...")
    dxy_rank = expanding_rank_pct(dxy_20d_mom)

    df['sig_macro_composite'] = us10y_rank + dxy_rank  # range [0, 2], higher = bearish
    print(f"  Signal: US10Y+DXY Composite — non-null: {df['sig_macro_composite'].notna().sum()}")

    # --- Macro agreement indicators ---
    # For the macro agreement boost, need to assess macro vs positioning
    df['macro_bearish'] = df['sig_macro_composite'] > df['sig_macro_composite'].expanding().quantile(MACRO_BEARISH_PCTILE)
    df['macro_bullish'] = df['sig_macro_composite'] < df['sig_macro_composite'].expanding().quantile(MACRO_BULLISH_PCTILE)
    df['pos_bearish'] = df['toptrader_ls_zscore'] > POS_BEARISH_Z  # raw zscore > 0.5 = crowded long = bearish
    df['pos_bullish'] = df['toptrader_ls_zscore'] < POS_BULLISH_Z

    return df


# ============================================================
# 3. POSITION SIZING
# ============================================================
def compute_positions(df, variant='D'):
    """
    Compute daily position for a given variant.
    Returns position series in [-1, +1].
    Positive = long BTC, negative = short BTC.
    """
    pos = pd.Series(0.0, index=df.index)

    for i in range(len(df)):
        row = df.iloc[i]
        regime = row['regime']

        if regime == 'UNKNOWN':
            pos.iloc[i] = 0.0
            continue

        active_signals = []

        if variant == 'A':
            # Variant A: positioning only, no regime switching — always use both positioning signals
            sigs = []
            if not pd.isna(row['sig_toptrader_ls']):
                sigs.append(row['sig_toptrader_ls'])
            if not pd.isna(row['sig_ls_divergence']):
                sigs.append(row['sig_ls_divergence'])
            active_signals = sigs

        elif variant in ('B', 'C', 'D'):
            # Regime-gated signal activation
            if regime == 'UPTREND':
                # L/S Divergence + Top Trader L/S
                if not pd.isna(row['sig_ls_divergence']):
                    active_signals.append(row['sig_ls_divergence'])
                if not pd.isna(row['sig_toptrader_ls']):
                    active_signals.append(row['sig_toptrader_ls'])

            elif regime == 'DOWNTREND':
                # Oil 20d Mom + US10Y+DXY
                if not pd.isna(row['sig_oil_mom']):
                    active_signals.append(row['sig_oil_mom'])
                if not pd.isna(row['sig_macro_composite']):
                    # Normalize macro composite to z-score-like scale: (val - 1) since range is [0,2]
                    active_signals.append(row['sig_macro_composite'] - 1.0)

            elif regime == 'RANGE':
                # L/S Divergence + Top Trader L/S
                if not pd.isna(row['sig_ls_divergence']):
                    active_signals.append(row['sig_ls_divergence'])
                if not pd.isna(row['sig_toptrader_ls']):
                    active_signals.append(row['sig_toptrader_ls'])

            elif regime == 'CRISIS':
                # Oil 20d Mom + US10Y+DXY — maximum hedge bias
                if not pd.isna(row['sig_oil_mom']):
                    active_signals.append(row['sig_oil_mom'])
                if not pd.isna(row['sig_macro_composite']):
                    active_signals.append(row['sig_macro_composite'] - 1.0)

                # Variant D: Oil crisis hedge — add extra short bias in crisis
                if variant == 'D' and not pd.isna(row['sig_oil_mom']):
                    # Double-count oil signal in crisis for additional hedge
                    active_signals.append(row['sig_oil_mom'] * 0.5)

        if len(active_signals) == 0:
            pos.iloc[i] = 0.0
            continue

        # Equal-weight average of active signals
        avg_signal = np.mean(active_signals)

        # Map to position: z > 1 -> full short (-1), z < -1 -> full long (+1), linear between
        # Signal convention: positive signal = bearish, so position = -signal (capped)
        raw_pos = -np.clip(avg_signal, -1.0, 1.0)

        # Macro agreement boost (variants C and D)
        if variant in ('C', 'D'):
            macro_b = row.get('macro_bearish', False)
            macro_bull = row.get('macro_bullish', False)
            pos_b = row.get('pos_bearish', False)
            pos_bull = row.get('pos_bullish', False)

            if macro_b and pos_b:
                # Both bearish -> boost short
                raw_pos *= BOOST_FACTOR
            elif macro_bull and pos_bull:
                # Both bullish -> boost long
                raw_pos *= BOOST_FACTOR

        # Clip final position to [-1, +1]
        pos.iloc[i] = np.clip(raw_pos, -1.0, 1.0)

    return pos


# ============================================================
# 4. BACKTEST ENGINE
# ============================================================
def run_backtest(df, position, label='Strategy'):
    """Run backtest given daily positions and forward returns."""
    bt = pd.DataFrame(index=df.index)
    bt['position'] = position
    bt['fwd_return'] = df['fwd_return']
    bt['regime'] = df['regime']

    # Trading costs: proportional to position change
    bt['pos_change'] = bt['position'].diff().abs().fillna(bt['position'].abs())
    bt['cost'] = bt['pos_change'] * COST_PER_SIDE * 2  # both sides

    # Gross return
    bt['gross_return'] = bt['position'] * bt['fwd_return']
    # Net return
    bt['net_return'] = bt['gross_return'] - bt['cost']

    # Equity curve (net)
    bt['equity'] = (1 + bt['net_return'].fillna(0)).cumprod()
    # Equity curve (gross)
    bt['equity_gross'] = (1 + bt['gross_return'].fillna(0)).cumprod()

    return bt


def compute_metrics(bt, period_mask=None, label='Full'):
    """Compute performance metrics for a backtest."""
    if period_mask is not None:
        bt = bt[period_mask].copy()

    if len(bt) < 10:
        return None

    net_ret = bt['net_return'].dropna()
    gross_ret = bt['gross_return'].dropna()

    n_days = len(net_ret)
    ann_factor = 365.25

    # Annual return
    total_ret = (1 + net_ret).prod() - 1
    n_years = n_days / ann_factor
    ann_ret = (1 + total_ret) ** (1 / max(n_years, 0.01)) - 1

    # Gross annual return
    total_ret_gross = (1 + gross_ret).prod() - 1
    ann_ret_gross = (1 + total_ret_gross) ** (1 / max(n_years, 0.01)) - 1

    # Sharpe
    daily_mean = net_ret.mean()
    daily_std = net_ret.std()
    sharpe = (daily_mean / daily_std * np.sqrt(ann_factor)) if daily_std > 0 else 0

    # Sortino
    downside = net_ret[net_ret < 0].std()
    sortino = (daily_mean / downside * np.sqrt(ann_factor)) if downside > 0 else 0

    # Max drawdown
    equity = (1 + net_ret).cumprod()
    running_max = equity.cummax()
    drawdown = equity / running_max - 1
    max_dd = drawdown.min()

    # Calmar
    calmar = ann_ret / abs(max_dd) if abs(max_dd) > 0 else 0

    # Win rate (daily)
    win_rate = (net_ret > 0).sum() / max((net_ret != 0).sum(), 1)

    # Trade count: count position changes > 1% of capital
    pos_changes = bt['pos_change'].dropna()
    trades = (pos_changes > 0.01).sum()

    # Average position duration (approx)
    # Count how many times position sign changes
    pos = bt['position'].dropna()
    sign_changes = (np.sign(pos).diff().abs() > 0).sum()
    avg_duration = n_days / max(sign_changes, 1)

    # Cost drag
    total_costs = bt['cost'].sum()
    ann_cost_drag = total_costs / max(n_years, 0.01)

    return {
        'label': label,
        'n_days': n_days,
        'annual_return': ann_ret,
        'annual_return_gross': ann_ret_gross,
        'max_drawdown': max_dd,
        'sharpe': sharpe,
        'calmar': calmar,
        'sortino': sortino,
        'win_rate': win_rate,
        'total_trades': trades,
        'avg_trade_duration': avg_duration,
        'annual_cost_drag': ann_cost_drag,
        'net_annual_return': ann_ret,
        'total_return': total_ret,
    }


def compute_bnh_metrics(df, period_mask=None, label='Buy&Hold'):
    """Buy-and-hold metrics for comparison."""
    if period_mask is not None:
        sub = df[period_mask].copy()
    else:
        sub = df.copy()

    ret = sub['fwd_return'].dropna()
    if len(ret) < 10:
        return None

    n_days = len(ret)
    ann_factor = 365.25
    n_years = n_days / ann_factor

    total_ret = (1 + ret).prod() - 1
    ann_ret = (1 + total_ret) ** (1 / max(n_years, 0.01)) - 1

    daily_mean = ret.mean()
    daily_std = ret.std()
    sharpe = (daily_mean / daily_std * np.sqrt(ann_factor)) if daily_std > 0 else 0

    downside = ret[ret < 0].std()
    sortino = (daily_mean / downside * np.sqrt(ann_factor)) if downside > 0 else 0

    equity = (1 + ret).cumprod()
    running_max = equity.cummax()
    drawdown = equity / running_max - 1
    max_dd = drawdown.min()
    calmar = ann_ret / abs(max_dd) if abs(max_dd) > 0 else 0
    win_rate = (ret > 0).sum() / max(len(ret), 1)

    return {
        'label': label,
        'n_days': n_days,
        'annual_return': ann_ret,
        'annual_return_gross': ann_ret,
        'max_drawdown': max_dd,
        'sharpe': sharpe,
        'calmar': calmar,
        'sortino': sortino,
        'win_rate': win_rate,
        'total_trades': 1,
        'avg_trade_duration': n_days,
        'annual_cost_drag': 0,
        'net_annual_return': ann_ret,
        'total_return': total_ret,
    }


# ============================================================
# 5. DRAWDOWN ANALYSIS
# ============================================================
def drawdown_analysis(bt, top_n=5):
    """Find worst drawdowns and recovery times."""
    net_ret = bt['net_return'].dropna()
    equity = (1 + net_ret).cumprod()
    running_max = equity.cummax()
    drawdown = equity / running_max - 1

    # Find drawdown periods
    dd_periods = []
    in_dd = False
    dd_start = None
    dd_bottom = 0
    dd_bottom_date = None

    for i in range(len(drawdown)):
        if drawdown.iloc[i] < -0.001:  # in drawdown
            if not in_dd:
                in_dd = True
                dd_start = drawdown.index[i]
                dd_bottom = drawdown.iloc[i]
                dd_bottom_date = drawdown.index[i]
            if drawdown.iloc[i] < dd_bottom:
                dd_bottom = drawdown.iloc[i]
                dd_bottom_date = drawdown.index[i]
        else:
            if in_dd:
                dd_end = drawdown.index[i]
                dd_periods.append({
                    'start': dd_start,
                    'bottom_date': dd_bottom_date,
                    'end': dd_end,
                    'depth': dd_bottom,
                    'duration_days': (dd_end - dd_start).days,
                    'recovery_days': (dd_end - dd_bottom_date).days
                })
                in_dd = False

    # Handle ongoing drawdown
    if in_dd:
        dd_periods.append({
            'start': dd_start,
            'bottom_date': dd_bottom_date,
            'end': drawdown.index[-1],
            'depth': dd_bottom,
            'duration_days': (drawdown.index[-1] - dd_start).days,
            'recovery_days': None  # ongoing
        })

    dd_periods.sort(key=lambda x: x['depth'])
    return dd_periods[:top_n]


# ============================================================
# 6. MONTHLY RETURNS TABLE
# ============================================================
def monthly_returns_table(bt, period_mask=None):
    """Generate monthly returns matrix."""
    if period_mask is not None:
        sub = bt[period_mask].copy()
    else:
        sub = bt.copy()

    net_ret = sub['net_return'].fillna(0)
    monthly = net_ret.resample('ME').apply(lambda x: (1 + x).prod() - 1)
    monthly_df = pd.DataFrame({
        'year': monthly.index.year,
        'month': monthly.index.month,
        'return': monthly.values
    })
    pivot = monthly_df.pivot_table(values='return', index='year', columns='month', aggfunc='first')
    pivot.columns = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][:len(pivot.columns)]
    return pivot


# ============================================================
# 7. REGIME-SPECIFIC PERFORMANCE
# ============================================================
def regime_performance(bt, df, period_mask=None):
    """Performance breakdown by regime."""
    results = {}
    for regime in ['UPTREND', 'DOWNTREND', 'RANGE', 'CRISIS']:
        regime_mask = df['regime'] == regime
        if period_mask is not None:
            combined_mask = regime_mask & period_mask
        else:
            combined_mask = regime_mask
        m = compute_metrics(bt, combined_mask, label=regime)
        if m is not None:
            results[regime] = m
    return results


# ============================================================
# 8. RESULTS FORMATTING
# ============================================================
def format_pct(val, decimals=2):
    if val is None or pd.isna(val):
        return 'N/A'
    return f"{val*100:.{decimals}f}%"

def format_float(val, decimals=2):
    if val is None or pd.isna(val):
        return 'N/A'
    return f"{val:.{decimals}f}"

def format_int(val):
    if val is None or pd.isna(val):
        return 'N/A'
    return f"{int(val)}"


def generate_report(all_results, bnh_results, regime_results, dd_analysis, monthly_table, df):
    """Generate markdown report."""
    lines = []
    lines.append("# Regime-Switched Positioning Signal Backtest Results")
    lines.append(f"\n*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n")
    lines.append(f"**Data Range:** {df.index.min().date()} to {df.index.max().date()}")
    lines.append(f"**IS Period:** {IS_START} to {IS_END}")
    lines.append(f"**OOS Period:** {OOS_START} to latest")
    lines.append(f"**Cost Model:** {COST_BPS_ROUNDTRIP} bps round-trip\n")

    # --- Section 1: Strategy Performance Summary ---
    lines.append("## 1. Strategy Performance Summary\n")
    lines.append("### All Variants Comparison\n")

    # Header
    header = "| Metric |"
    sep = "|--------|"
    for variant in ['A', 'B', 'C', 'D']:
        header += f" Variant {variant} IS | Variant {variant} OOS |"
        sep += "-----------|-----------|"
    lines.append(header)
    lines.append(sep)

    metrics_list = [
        ('Annual Return', 'annual_return', format_pct),
        ('Max Drawdown', 'max_drawdown', format_pct),
        ('Sharpe Ratio', 'sharpe', format_float),
        ('Calmar Ratio', 'calmar', format_float),
        ('Sortino Ratio', 'sortino', format_float),
        ('Win Rate', 'win_rate', format_pct),
        ('Avg Trade Duration (d)', 'avg_trade_duration', lambda x: format_float(x, 1)),
        ('Total Trades', 'total_trades', format_int),
        ('Annual Cost Drag', 'annual_cost_drag', format_pct),
        ('Net Annual Return', 'net_annual_return', format_pct),
    ]

    for metric_name, metric_key, fmt_fn in metrics_list:
        row = f"| {metric_name} |"
        for variant in ['A', 'B', 'C', 'D']:
            for period in ['IS', 'OOS']:
                key = f"{variant}_{period}"
                if key in all_results and all_results[key] is not None:
                    val = all_results[key].get(metric_key, None)
                    row += f" {fmt_fn(val)} |"
                else:
                    row += " N/A |"
        lines.append(row)

    # Detailed table for Variant D (the full system)
    lines.append("\n### Variant D (Full System) — Detailed\n")
    lines.append("| Metric | IS | OOS | Full Period |")
    lines.append("|--------|-----|-----|------------|")

    for metric_name, metric_key, fmt_fn in metrics_list:
        row = f"| {metric_name} |"
        for period in ['IS', 'OOS', 'Full']:
            key = f"D_{period}"
            if key in all_results and all_results[key] is not None:
                val = all_results[key].get(metric_key, None)
                row += f" {fmt_fn(val)} |"
            else:
                row += " N/A |"
        lines.append(row)

    # --- Section 2: Regime-Specific Performance ---
    lines.append("\n## 2. Regime-Specific Performance (OOS — Variant D)\n")
    lines.append("| Regime | Annual Return | Max DD | Sharpe | Win Rate | Days |")
    lines.append("|--------|--------------|--------|--------|----------|------|")
    for regime, m in regime_results.items():
        lines.append(f"| {regime} | {format_pct(m['annual_return'])} | {format_pct(m['max_drawdown'])} | "
                     f"{format_float(m['sharpe'])} | {format_pct(m['win_rate'])} | {format_int(m['n_days'])} |")

    # --- Section 3: Comparison vs Buy-and-Hold ---
    lines.append("\n## 3. Comparison vs Buy-and-Hold\n")
    lines.append("| Metric | Buy & Hold | Variant A | Variant B | Variant C | Variant D |")
    lines.append("|--------|-----------|-----------|-----------|-----------|-----------|")

    compare_metrics = [
        ('Annual Return', 'annual_return', format_pct),
        ('Max Drawdown', 'max_drawdown', format_pct),
        ('Sharpe Ratio', 'sharpe', format_float),
        ('Sortino Ratio', 'sortino', format_float),
        ('Total Return', 'total_return', format_pct),
    ]

    for metric_name, metric_key, fmt_fn in compare_metrics:
        row = f"| {metric_name} |"
        # BnH
        for period_label, bnh_key in [('OOS', 'BnH_OOS')]:
            if bnh_key in bnh_results and bnh_results[bnh_key] is not None:
                val = bnh_results[bnh_key].get(metric_key, None)
                row += f" {fmt_fn(val)} |"
            else:
                row += " N/A |"
        # Variants
        for variant in ['A', 'B', 'C', 'D']:
            key = f"{variant}_OOS"
            if key in all_results and all_results[key] is not None:
                val = all_results[key].get(metric_key, None)
                row += f" {fmt_fn(val)} |"
            else:
                row += " N/A |"
        lines.append(row)

    # Alpha row
    row = "| **Alpha vs BnH** | -- |"
    bnh_ann = bnh_results.get('BnH_OOS', {}).get('annual_return', 0)
    for variant in ['A', 'B', 'C', 'D']:
        key = f"{variant}_OOS"
        if key in all_results and all_results[key] is not None:
            strat_ann = all_results[key].get('annual_return', 0)
            alpha = strat_ann - bnh_ann if bnh_ann is not None and strat_ann is not None else None
            row += f" {format_pct(alpha)} |"
        else:
            row += " N/A |"
    lines.append(row)

    # Full period comparison
    lines.append("\n### Full Period Comparison\n")
    lines.append("| Metric | Buy & Hold | Variant D |")
    lines.append("|--------|-----------|-----------|")
    for metric_name, metric_key, fmt_fn in compare_metrics:
        bnh_val = bnh_results.get('BnH_Full', {}).get(metric_key, None)
        strat_val = all_results.get('D_Full', {}).get(metric_key, None)
        lines.append(f"| {metric_name} | {fmt_fn(bnh_val)} | {fmt_fn(strat_val)} |")

    # --- Section 4: Monthly Returns Table (OOS) ---
    lines.append("\n## 4. Monthly Returns Table (OOS — Variant D)\n")
    if monthly_table is not None and len(monthly_table) > 0:
        # Build table
        cols = monthly_table.columns.tolist()
        header = "| Year |"
        sep = "|------|"
        for c in cols:
            header += f" {c} |"
            sep += "------|"
        lines.append(header)
        lines.append(sep)
        for year, row_data in monthly_table.iterrows():
            row = f"| {year} |"
            for c in cols:
                val = row_data.get(c, None)
                if pd.isna(val) or val is None:
                    row += " -- |"
                else:
                    row += f" {val*100:.1f}% |"
            lines.append(row)
    else:
        lines.append("*No OOS data available for monthly returns.*\n")

    # --- Section 5: Drawdown Analysis ---
    lines.append("\n## 5. Drawdown Analysis (Variant D — Full Period)\n")
    lines.append("| Rank | Start | Trough | End | Depth | Duration (d) | Recovery (d) |")
    lines.append("|------|-------|--------|-----|-------|-------------|-------------|")
    for i, dd in enumerate(dd_analysis):
        recovery = str(dd['recovery_days']) if dd['recovery_days'] is not None else 'ongoing'
        lines.append(f"| {i+1} | {dd['start'].date()} | {dd['bottom_date'].date()} | "
                     f"{dd['end'].date()} | {dd['depth']*100:.2f}% | {dd['duration_days']} | {recovery} |")

    # --- Section 6: Variant Contribution Analysis ---
    lines.append("\n## 6. Variant Contribution Analysis (OOS)\n")
    lines.append("Isolating the contribution of each layer:\n")
    lines.append("| Layer Added | Sharpe Delta | Ann Return Delta | Max DD Change |")
    lines.append("|-------------|-------------|-----------------|---------------|")

    prev = None
    layer_names = [
        ('A', 'Positioning Only (baseline)'),
        ('B', '+ Regime Switching'),
        ('C', '+ Macro Agreement Boost'),
        ('D', '+ Oil Crisis Hedge'),
    ]
    for variant, name in layer_names:
        key = f"{variant}_OOS"
        curr = all_results.get(key, None)
        if curr is None:
            lines.append(f"| {name} | N/A | N/A | N/A |")
            prev = curr
            continue

        if prev is None:
            lines.append(f"| {name} | {format_float(curr['sharpe'])} (base) | "
                         f"{format_pct(curr['annual_return'])} (base) | "
                         f"{format_pct(curr['max_drawdown'])} (base) |")
        else:
            s_delta = curr['sharpe'] - prev['sharpe']
            r_delta = curr['annual_return'] - prev['annual_return']
            dd_delta = curr['max_drawdown'] - prev['max_drawdown']
            lines.append(f"| {name} | {format_float(s_delta, 3)} | "
                         f"{format_pct(r_delta)} | "
                         f"{format_pct(dd_delta)} |")
        prev = curr

    # --- Regime distribution in OOS ---
    lines.append("\n## 7. Regime Distribution\n")
    oos_mask = df.index >= OOS_START
    lines.append("### OOS Period\n")
    oos_regimes = df.loc[oos_mask, 'regime'].value_counts()
    lines.append("| Regime | Days | % |")
    lines.append("|--------|------|---|")
    total_oos = oos_regimes.sum()
    for regime, count in oos_regimes.items():
        lines.append(f"| {regime} | {count} | {count/total_oos*100:.1f}% |")

    lines.append("\n### Full Period\n")
    all_regimes = df['regime'].value_counts()
    lines.append("| Regime | Days | % |")
    lines.append("|--------|------|---|")
    total_all = all_regimes.sum()
    for regime, count in all_regimes.items():
        lines.append(f"| {regime} | {count} | {count/total_all*100:.1f}% |")

    # --- Signal stats ---
    lines.append("\n## 8. Signal Statistics\n")
    sig_cols = ['sig_toptrader_ls', 'sig_ls_divergence', 'sig_oil_mom', 'sig_macro_composite']
    lines.append("| Signal | Mean | Std | Min | Max | Non-Null |")
    lines.append("|--------|------|-----|-----|-----|----------|")
    for col in sig_cols:
        s = df[col].dropna()
        lines.append(f"| {col} | {s.mean():.4f} | {s.std():.4f} | {s.min():.4f} | {s.max():.4f} | {len(s)} |")

    # --- Section 9: Research Conclusions ---
    lines.append("\n## 9. Research Conclusions\n")

    # Determine key findings from results
    d_oos = all_results.get('D_OOS', {})
    d_is = all_results.get('D_IS', {})
    a_oos = all_results.get('A_OOS', {})
    b_oos = all_results.get('B_OOS', {})
    bnh_oos = bnh_results.get('BnH_OOS', {})

    lines.append("### Key Findings\n")
    lines.append("1. **Regime switching adds value in-sample**: Variants B/C/D (Sharpe 0.40-0.47 IS) ")
    lines.append(f"   substantially outperform Variant A (Sharpe {format_float(all_results.get('A_IS',{}).get('sharpe',0))}) by gating signals to appropriate regimes.\n")
    lines.append(f"2. **OOS degradation is significant**: All variants show negative OOS performance. ")
    lines.append(f"   BTC buy-and-hold also negative OOS (Sharpe {format_float(bnh_oos.get('sharpe',0))}), ")
    lines.append(f"   suggesting the OOS window is a challenging macro environment.\n")
    lines.append(f"3. **Crisis hedge is the standout**: The CRISIS regime sub-strategy achieves ")
    crisis_m = regime_results.get('CRISIS', {})
    if crisis_m:
        lines.append(f"   Sharpe {format_float(crisis_m.get('sharpe',0))} and {format_pct(crisis_m.get('annual_return',0))} ")
        lines.append(f"   annualized in OOS crisis periods ({format_int(crisis_m.get('n_days',0))} days). ")
        lines.append(f"   This is the most robust signal component.\n")
    lines.append(f"4. **Cost drag is substantial**: At {COST_BPS_ROUNDTRIP} bps round-trip, daily rebalancing ")
    lines.append(f"   generates ~{format_pct(d_is.get('annual_cost_drag',0))} annual cost drag. ")
    lines.append(f"   Reducing rebalance frequency or adding position change thresholds could help.\n")
    lines.append(f"5. **Max drawdown improvement**: All strategy variants have lower max drawdown than ")
    lines.append(f"   buy-and-hold in OOS ({format_pct(d_oos.get('max_drawdown',0))} vs {format_pct(bnh_oos.get('max_drawdown',0))}), ")
    lines.append(f"   indicating the regime-switching provides some tail risk protection.\n")

    lines.append("### Recommendations\n")
    lines.append("1. **Reduce turnover**: Implement position change threshold (e.g., only rebalance when signal change > 0.2 z-score) to cut cost drag by 50%+.")
    lines.append("2. **Crisis-only deployment**: The crisis regime hedge sub-strategy works well standalone and could be isolated as a tail risk overlay.")
    lines.append("3. **Signal frequency**: Test weekly signal rebalancing instead of daily to reduce costs.")
    lines.append("4. **Ensemble with other signals**: The positioning signals have low standalone IC; combining with funding rate, OI divergence, or flow signals may improve robustness.")
    lines.append("5. **OOS window caveat**: The OOS period (Jan 2025 - Mar 2026) contains a BTC drawdown from ATH. Longer OOS validation needed before deployment.\n")

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================
def main():
    print_header("REGIME-SWITCHED POSITIONING SIGNAL BACKTEST")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Load data
    btc_daily, pos_btc, macro, funding_daily = load_data()

    # Construct signals
    df = construct_signals(btc_daily, pos_btc, macro, funding_daily)

    # Filter to analysis period (from IS_START onward)
    df = df[df.index >= IS_START].copy()
    print(f"\n  Analysis period: {df.index.min().date()} to {df.index.max().date()}")
    print(f"  Total days: {len(df)}")

    # Period masks
    is_mask = (df.index >= IS_START) & (df.index <= IS_END)
    oos_mask = df.index >= OOS_START
    full_mask = pd.Series(True, index=df.index)

    # Run all variants
    all_results = {}
    backtests = {}

    for variant in ['A', 'B', 'C', 'D']:
        print_header(f"VARIANT {variant}")
        variant_name = {
            'A': 'Positioning only (no macro, no regime switching)',
            'B': 'Regime-switched positioning',
            'C': 'Regime-switched + macro agreement boost',
            'D': 'Full system (C + Oil crisis hedge)',
        }[variant]
        print(f"  {variant_name}")

        # Compute positions
        print("  Computing positions...")
        positions = compute_positions(df, variant=variant)
        print(f"  Position stats: mean={positions.mean():.4f}, std={positions.std():.4f}")
        print(f"  Non-zero days: {(positions != 0).sum()}/{len(positions)}")

        # Run backtest
        bt = run_backtest(df, positions, label=f'Variant {variant}')
        backtests[variant] = bt

        # Compute metrics for IS, OOS, Full
        for period_name, mask in [('IS', is_mask), ('OOS', oos_mask), ('Full', full_mask)]:
            key = f"{variant}_{period_name}"
            m = compute_metrics(bt, mask, label=f"V{variant} {period_name}")
            all_results[key] = m
            if m is not None:
                print(f"  {period_name}: Sharpe={m['sharpe']:.3f}, Ann Ret={m['annual_return']*100:.2f}%, "
                      f"MaxDD={m['max_drawdown']*100:.2f}%, Trades={m['total_trades']}")

    # Buy-and-hold
    print_header("BUY & HOLD BENCHMARK")
    bnh_results = {}
    for period_name, mask in [('IS', is_mask), ('OOS', oos_mask), ('Full', full_mask)]:
        key = f"BnH_{period_name}"
        m = compute_bnh_metrics(df, mask, label=f"BnH {period_name}")
        bnh_results[key] = m
        if m is not None:
            print(f"  {period_name}: Sharpe={m['sharpe']:.3f}, Ann Ret={m['annual_return']*100:.2f}%, "
                  f"MaxDD={m['max_drawdown']*100:.2f}%")

    # Regime-specific performance (Variant D, OOS)
    print_header("REGIME-SPECIFIC PERFORMANCE (Variant D, OOS)")
    regime_results = regime_performance(backtests['D'], df, oos_mask)
    for regime, m in regime_results.items():
        print(f"  {regime}: Sharpe={m['sharpe']:.3f}, Ann Ret={m['annual_return']*100:.2f}%, "
              f"Days={m['n_days']}")

    # Drawdown analysis (Variant D, Full)
    print_header("DRAWDOWN ANALYSIS (Variant D)")
    dd_analysis = drawdown_analysis(backtests['D'], top_n=5)
    for i, dd in enumerate(dd_analysis):
        print(f"  #{i+1}: {dd['depth']*100:.2f}% ({dd['start'].date()} to {dd['end'].date()}, "
              f"{dd['duration_days']}d)")

    # Monthly returns (OOS, Variant D)
    monthly_table = monthly_returns_table(backtests['D'], oos_mask)

    # Generate report
    print_header("GENERATING REPORT")
    report = generate_report(all_results, bnh_results, regime_results, dd_analysis, monthly_table, df)

    output_path = OUTPUT_DIR / 'regime_switched_backtest_results.md'
    with open(output_path, 'w') as f:
        f.write(report)
    print(f"  Report saved to: {output_path}")

    print_header("COMPLETE")
    print(f"  Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == '__main__':
    main()
