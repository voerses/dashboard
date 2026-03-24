#!/workspace/venv/bin/python
"""
R105: V3 Momentum vs Trend+Pullback Correlation Analysis
==========================================================

Goal: Determine if V3 Momentum (s320) and Trend+Pullback are uncorrelated enough
to run as a portfolio. If corr < 0.3, they provide genuine diversification.

Strategy Definitions:
  V3 Momentum: Long when daily EMA(20) > EMA(50), flat otherwise. Binary position.
  Trend+Pullback: Long when daily EMA(20) > EMA(50) AND 4h RSI(14) crosses UP
                   through 40. Exit: 2x ATR(14) trailing stop on 4h bars OR 168h max hold.

Data: BTC spot 1h from /workspace/crypto_backtest/data/spot/1h_cache/BTC_1h.parquet
Period: 2021-01 to 2026-03
"""

import pandas as pd
import numpy as np
from scipy import stats
import warnings
import time

warnings.filterwarnings('ignore')

DATA_PATH = '/workspace/crypto_backtest/data/spot/1h_cache/BTC_1h.parquet'
OUTPUT_MD = '/workspace/crypto_backtest/research/R105_v3_tp_correlation.md'
PERIOD_START = '2021-01-01'
PERIOD_END = '2026-03-31'
COST_BPS = 10  # round-trip cost in basis points


# ============================================================
# DATA LOADING & INDICATOR COMPUTATION
# ============================================================

def load_data():
    """Load 1h BTC data and compute all required indicators."""
    df_1h = pd.read_parquet(DATA_PATH)
    df_1h.index = pd.to_datetime(df_1h.index)
    df_1h = df_1h.sort_index()
    df_1h = df_1h[~df_1h.index.duplicated(keep='first')]
    df_1h = df_1h.loc[PERIOD_START:PERIOD_END]

    # ----- Daily bars & indicators -----
    daily = df_1h['close'].resample('1D').last().dropna()
    daily_df = pd.DataFrame({'close': daily})
    daily_df['ema20'] = daily.ewm(span=20, adjust=False).mean()
    daily_df['ema50'] = daily.ewm(span=50, adjust=False).mean()
    daily_df['uptrend'] = (daily_df['ema20'] > daily_df['ema50']).astype(int)
    daily_df['daily_return'] = daily_df['close'].pct_change()

    # SMA50 for regime classification
    daily_df['sma50'] = daily.rolling(50).mean()
    daily_df['sma200'] = daily.rolling(200).mean()

    # ----- 4h bars -----
    bars_4h = df_1h.resample('4h').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).dropna(subset=['close'])

    # RSI(14) on 4h
    period = 14
    delta = bars_4h['close'].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    bars_4h['rsi'] = 100 - (100 / (1 + rs))

    # ATR(14) on 4h
    bars_4h['tr'] = np.maximum(
        bars_4h['high'] - bars_4h['low'],
        np.maximum(
            abs(bars_4h['high'] - bars_4h['close'].shift(1)),
            abs(bars_4h['low'] - bars_4h['close'].shift(1))
        )
    )
    bars_4h['atr14'] = bars_4h['tr'].rolling(14).mean()

    # Map daily uptrend to 4h bars (no lookahead: shift daily by 1 day)
    daily_shifted = daily_df[['uptrend']].copy()
    daily_shifted.index = daily_shifted.index + pd.Timedelta(days=1)
    bars_4h['uptrend'] = daily_shifted['uptrend'].reindex(bars_4h.index, method='ffill')

    return df_1h, daily_df, bars_4h


# ============================================================
# STRATEGY 1: V3 MOMENTUM (binary EMA crossover)
# ============================================================

def compute_v3_positions(daily_df):
    """
    V3 Momentum: Long when daily EMA(20) > EMA(50), flat otherwise.
    Returns a daily Series of positions (1 or 0).
    Weekly rebalance: position only changes at end of each Monday.
    """
    # Shifted uptrend: use prior day's signal (no lookahead)
    pos = daily_df['uptrend'].shift(1).fillna(0).astype(int)

    # Weekly rebalance: lock position on Monday, hold through the week
    # Resample to weekly (Monday end), then forward-fill
    weekly_signal = pos.resample('W-MON').last()
    pos_weekly = weekly_signal.reindex(pos.index, method='ffill').fillna(0).astype(int)

    return pos_weekly


def compute_v3_daily_returns(daily_df):
    """Compute V3 strategy daily returns."""
    pos = compute_v3_positions(daily_df)
    daily_ret = daily_df['daily_return'].fillna(0)
    strat_ret = pos * daily_ret
    return strat_ret, pos


# ============================================================
# STRATEGY 2: TREND + PULLBACK (4h RSI cross + ATR trailing)
# ============================================================

def simulate_trend_pullback(bars_4h):
    """
    Trend+Pullback simulation on 4h bars.
    Entry: uptrend (daily EMA20>EMA50) AND RSI(14) crosses UP through 40.
    Exit: 2x ATR(14) trailing stop OR 168h (42 bars of 4h) max hold.
    Returns a Series on 4h index with position (1=in trade, 0=flat).
    """
    max_hold_bars = 42  # 168h / 4h = 42 bars

    n = len(bars_4h)
    position = np.zeros(n, dtype=int)
    entry_prices = np.zeros(n)
    trade_entries = []
    trade_exits = []

    rsi_vals = bars_4h['rsi'].values
    close_vals = bars_4h['close'].values
    high_vals = bars_4h['high'].values
    atr_vals = bars_4h['atr14'].values
    uptrend_vals = bars_4h['uptrend'].values
    timestamps = bars_4h.index

    in_trade = False
    entry_price = 0.0
    trailing_high = 0.0
    hold_count = 0

    for i in range(1, n):
        if np.isnan(rsi_vals[i]) or np.isnan(close_vals[i]) or np.isnan(atr_vals[i]):
            continue

        if in_trade:
            hold_count += 1
            # Update trailing high
            if high_vals[i] > trailing_high:
                trailing_high = high_vals[i]

            # Check trailing stop: price drops 2*ATR from trailing high
            trail_stop = trailing_high - 2.0 * atr_vals[i]
            if close_vals[i] <= trail_stop or hold_count >= max_hold_bars:
                # Exit
                in_trade = False
                position[i] = 0
                trade_exits.append(i)
                continue

            position[i] = 1

        if not in_trade:
            # Check entry: uptrend AND RSI crosses UP through 40
            if (uptrend_vals[i] == 1 and
                not np.isnan(rsi_vals[i-1]) and
                rsi_vals[i-1] <= 40 and rsi_vals[i] > 40):
                # Entry
                in_trade = True
                entry_price = close_vals[i]
                trailing_high = high_vals[i]
                hold_count = 0
                position[i] = 1
                trade_entries.append(i)

    pos_series = pd.Series(position, index=bars_4h.index, name='tp_position')
    return pos_series, trade_entries, trade_exits


def compute_tp_daily_returns(bars_4h, daily_df):
    """
    Convert 4h T+P position to daily returns.
    Position is 1 if ANY 4h bar in the day has position=1 (conservative: in-trade).
    """
    pos_4h, entries, exits = simulate_trend_pullback(bars_4h)

    # Resample to daily: position=1 if max of 4h positions is 1 (i.e., in trade at any point)
    daily_pos = pos_4h.resample('1D').max().fillna(0).astype(int)

    # Align with daily returns
    daily_pos = daily_pos.reindex(daily_df.index, method='ffill').fillna(0).astype(int)
    # Shift by 1: position decision at end of day D affects return on day D+1
    daily_pos_shifted = daily_pos.shift(1).fillna(0).astype(int)

    daily_ret = daily_df['daily_return'].fillna(0)
    strat_ret = daily_pos_shifted * daily_ret

    return strat_ret, daily_pos_shifted, pos_4h, entries, exits


# ============================================================
# ANALYSIS FUNCTIONS
# ============================================================

def correlation_analysis(v3_ret, tp_ret):
    """Pearson and Spearman correlation of daily strategy returns."""
    # Align
    aligned = pd.DataFrame({'v3': v3_ret, 'tp': tp_ret}).dropna()

    # Only consider days when at least one strategy is active
    active = aligned[(aligned['v3'] != 0) | (aligned['tp'] != 0)]

    pearson_r, pearson_p = stats.pearsonr(aligned['v3'], aligned['tp'])
    spearman_r, spearman_p = stats.spearmanr(aligned['v3'], aligned['tp'])

    # Active-only correlation
    if len(active) > 10:
        pearson_active, _ = stats.pearsonr(active['v3'], active['tp'])
        spearman_active, _ = stats.spearmanr(active['v3'], active['tp'])
    else:
        pearson_active = np.nan
        spearman_active = np.nan

    return {
        'pearson_r': pearson_r, 'pearson_p': pearson_p,
        'spearman_r': spearman_r, 'spearman_p': spearman_p,
        'pearson_active': pearson_active,
        'spearman_active': spearman_active,
        'n_total': len(aligned),
        'n_active': len(active),
    }


def rolling_correlation(v3_ret, tp_ret, window=90):
    """Rolling 90-day Pearson correlation."""
    aligned = pd.DataFrame({'v3': v3_ret, 'tp': tp_ret}).dropna()
    roll_corr = aligned['v3'].rolling(window).corr(aligned['tp'])
    return roll_corr


def regime_classification(daily_df):
    """
    Classify daily bars into regimes.
    UPTREND: EMA20 > EMA50 AND close > SMA200
    DOWNTREND: EMA20 < EMA50 AND close < SMA200
    RANGE: everything else
    """
    regime = pd.Series('RANGE', index=daily_df.index)
    uptrend = (daily_df['ema20'] > daily_df['ema50']) & (daily_df['close'] > daily_df['sma200'])
    downtrend = (daily_df['ema20'] < daily_df['ema50']) & (daily_df['close'] < daily_df['sma200'])
    regime[uptrend] = 'UPTREND'
    regime[downtrend] = 'DOWNTREND'
    return regime


def regime_correlation(v3_ret, tp_ret, regime):
    """Correlation within each regime."""
    aligned = pd.DataFrame({'v3': v3_ret, 'tp': tp_ret, 'regime': regime}).dropna()
    results = {}
    for r in ['UPTREND', 'DOWNTREND', 'RANGE']:
        subset = aligned[aligned['regime'] == r]
        if len(subset) > 30:
            pr, pp = stats.pearsonr(subset['v3'], subset['tp'])
            results[r] = {'pearson_r': pr, 'p_value': pp, 'n_days': len(subset)}
        else:
            results[r] = {'pearson_r': np.nan, 'p_value': np.nan, 'n_days': len(subset)}
    return results


def entry_overlap_analysis(v3_pos, tp_pos, tp_4h_pos, bars_4h, entries):
    """
    Analyze overlap between V3 and T+P positions.
    """
    aligned = pd.DataFrame({'v3': v3_pos, 'tp': tp_pos}).dropna()

    both_in = ((aligned['v3'] == 1) & (aligned['tp'] == 1)).sum()
    v3_only = ((aligned['v3'] == 1) & (aligned['tp'] == 0)).sum()
    tp_only = ((aligned['v3'] == 0) & (aligned['tp'] == 1)).sum()
    both_flat = ((aligned['v3'] == 0) & (aligned['tp'] == 0)).sum()
    total = len(aligned)

    v3_in = (aligned['v3'] == 1).sum()
    tp_in = (aligned['tp'] == 1).sum()

    # When T+P fires entry, is V3 already long?
    # Map 4h entry indices to dates, check V3 position
    tp_entry_dates = bars_4h.index[entries]
    tp_entry_daily = tp_entry_dates.normalize()
    v3_at_tp_entry = v3_pos.reindex(tp_entry_daily, method='ffill')
    v3_long_at_tp_entry = (v3_at_tp_entry == 1).sum()
    total_tp_entries = len(entries)

    # When V3 is flat, does T+P ever fire?
    v3_flat_days = aligned[aligned['v3'] == 0]
    tp_fires_when_v3_flat = (v3_flat_days['tp'] == 1).sum()

    return {
        'both_in': both_in, 'v3_only': v3_only, 'tp_only': tp_only,
        'both_flat': both_flat, 'total': total,
        'v3_in_days': v3_in, 'tp_in_days': tp_in,
        'v3_long_at_tp_entry': v3_long_at_tp_entry,
        'total_tp_entries': total_tp_entries,
        'tp_fires_when_v3_flat': tp_fires_when_v3_flat,
        'v3_flat_days': len(v3_flat_days),
        'overlap_pct': both_in / max(tp_in, 1) * 100,  # % of T+P in-trade days that overlap V3
    }


def compute_strategy_stats(returns, name='Strategy'):
    """Compute Sharpe, total return, max drawdown, Calmar."""
    if len(returns) == 0 or returns.std() == 0:
        return {'name': name, 'ann_return': 0, 'ann_vol': 0, 'sharpe': 0,
                'max_dd': 0, 'calmar': 0, 'total_return': 0}

    ann_return = returns.mean() * 365
    ann_vol = returns.std() * np.sqrt(365)
    sharpe = ann_return / ann_vol if ann_vol > 0 else 0

    equity = (1 + returns).cumprod()
    peak = equity.cummax()
    dd = (equity - peak) / peak
    max_dd = dd.min()
    calmar = ann_return / abs(max_dd) if max_dd != 0 else 0

    total_return = equity.iloc[-1] - 1 if len(equity) > 0 else 0

    return {
        'name': name,
        'ann_return': ann_return,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'calmar': calmar,
        'total_return': total_return,
    }


def portfolio_simulation(v3_ret, tp_ret, daily_df):
    """
    Portfolio combinations:
    1. V3 only
    2. T+P only
    3. Equal-weight: 50% V3 + 50% T+P
    4. Risk-parity: weight inversely to rolling 60-day volatility
    """
    aligned = pd.DataFrame({'v3': v3_ret, 'tp': tp_ret}).dropna()

    # Buy & hold baseline
    bh_ret = daily_df['daily_return'].reindex(aligned.index).fillna(0)

    # Equal weight
    eq_ret = 0.5 * aligned['v3'] + 0.5 * aligned['tp']

    # Risk parity: weight inversely to 60-day rolling vol
    v3_vol = aligned['v3'].rolling(60, min_periods=20).std()
    tp_vol = aligned['tp'].rolling(60, min_periods=20).std()
    # Avoid division by zero
    v3_vol = v3_vol.replace(0, np.nan)
    tp_vol = tp_vol.replace(0, np.nan)
    inv_v3 = 1.0 / v3_vol
    inv_tp = 1.0 / tp_vol
    total_inv = inv_v3 + inv_tp
    w_v3 = (inv_v3 / total_inv).fillna(0.5)
    w_tp = (inv_tp / total_inv).fillna(0.5)
    rp_ret = w_v3 * aligned['v3'] + w_tp * aligned['tp']

    results = {
        'buy_hold': compute_strategy_stats(bh_ret, 'Buy & Hold'),
        'v3_only': compute_strategy_stats(aligned['v3'], 'V3 Momentum'),
        'tp_only': compute_strategy_stats(aligned['tp'], 'Trend+Pullback'),
        'equal_weight': compute_strategy_stats(eq_ret, '50/50 Equal Weight'),
        'risk_parity': compute_strategy_stats(rp_ret, 'Risk Parity'),
    }

    # Avg risk parity weights
    results['rp_avg_w_v3'] = w_v3.mean()
    results['rp_avg_w_tp'] = w_tp.mean()

    return results


def conditional_analysis(v3_ret, tp_ret, v3_pos, tp_pos, daily_df):
    """
    Conditional analysis:
    1. When V3 is losing (30-day rolling return < 0), what does T+P do?
    2. When T+P fires, what's V3's recent performance?
    3. Chop detection: periods where V3 is whipsawing
    """
    aligned = pd.DataFrame({
        'v3_ret': v3_ret, 'tp_ret': tp_ret,
        'v3_pos': v3_pos, 'tp_pos': tp_pos
    }).dropna()

    # V3 losing periods: 30-day rolling cumulative return < 0
    v3_rolling_30d = aligned['v3_ret'].rolling(30).sum()
    v3_losing = v3_rolling_30d < 0
    v3_winning = v3_rolling_30d >= 0

    # During V3 losing periods
    tp_during_v3_loss = aligned.loc[v3_losing, 'tp_ret']
    v3_during_v3_loss = aligned.loc[v3_losing, 'v3_ret']
    tp_during_v3_win = aligned.loc[v3_winning, 'tp_ret']

    # T+P in-trade during V3 losing periods
    tp_in_during_v3_loss = (aligned.loc[v3_losing, 'tp_pos'] == 1).sum()
    tp_total_during_v3_loss = v3_losing.sum()

    # When V3 is flat, what does T+P return?
    v3_flat = aligned['v3_pos'] == 0
    tp_when_v3_flat = aligned.loc[v3_flat, 'tp_ret']

    # Chop detection: count V3 position flips per 30-day window
    v3_flips = (aligned['v3_pos'].diff().abs()).rolling(30).sum()
    choppy = v3_flips > 4  # more than 4 flips in 30 days
    tp_during_chop = aligned.loc[choppy, 'tp_ret']
    v3_during_chop = aligned.loc[choppy, 'v3_ret']

    return {
        'tp_mean_during_v3_loss': tp_during_v3_loss.mean() if len(tp_during_v3_loss) > 0 else np.nan,
        'v3_mean_during_v3_loss': v3_during_v3_loss.mean() if len(v3_during_v3_loss) > 0 else np.nan,
        'tp_mean_during_v3_win': tp_during_v3_win.mean() if len(tp_during_v3_win) > 0 else np.nan,
        'tp_in_pct_during_v3_loss': tp_in_during_v3_loss / max(tp_total_during_v3_loss, 1) * 100,
        'v3_loss_days': int(v3_losing.sum()),
        'v3_win_days': int(v3_winning.sum()),
        'tp_when_v3_flat_mean': tp_when_v3_flat.mean() if len(tp_when_v3_flat) > 0 else np.nan,
        'v3_flat_days': int(v3_flat.sum()),
        'choppy_days': int(choppy.sum()),
        'tp_mean_during_chop': tp_during_chop.mean() if len(tp_during_chop) > 0 else np.nan,
        'v3_mean_during_chop': v3_during_chop.mean() if len(v3_during_chop) > 0 else np.nan,
        'tp_cumret_during_v3_loss': tp_during_v3_loss.sum(),
        'v3_cumret_during_v3_loss': v3_during_v3_loss.sum(),
    }


# ============================================================
# REPORT GENERATION
# ============================================================

def generate_report(corr_res, roll_corr, regime_corr, overlap, portfolio,
                    conditional, v3_stats, tp_stats, daily_df, regime):
    lines = []
    lines.append("# R105 -- V3 Momentum vs Trend+Pullback Correlation Analysis")
    lines.append("")
    lines.append(f"**Date**: {pd.Timestamp.now().strftime('%Y-%m-%d')}")
    lines.append(f"**Period**: {PERIOD_START} to {daily_df.index.max().strftime('%Y-%m-%d')}")
    lines.append(f"**Asset**: BTC spot")
    lines.append("")

    # ---- STRATEGY DEFINITIONS ----
    lines.append("## Strategy Definitions")
    lines.append("")
    lines.append("### V3 Momentum (s320)")
    lines.append("- Signal: Long when daily EMA(20) > EMA(50), flat otherwise")
    lines.append("- Rebalance: Weekly (end of Monday)")
    lines.append("- No overlays for this comparison (binary position only)")
    lines.append("")
    lines.append("### Trend+Pullback")
    lines.append("- Signal: Long when daily EMA(20) > EMA(50) AND 4h RSI(14) crosses UP through 40")
    lines.append("- Exit: 2x ATR(14) trailing stop on 4h bars OR 168h max hold")
    lines.append("- No overlays")
    lines.append("")

    # ---- SECTION 1: CORRELATION ----
    lines.append("## 1. Correlation of Daily Returns")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Pearson r (all days) | **{corr_res['pearson_r']:.4f}** |")
    lines.append(f"| Pearson p-value | {corr_res['pearson_p']:.2e} |")
    lines.append(f"| Spearman r (all days) | **{corr_res['spearman_r']:.4f}** |")
    lines.append(f"| Spearman p-value | {corr_res['spearman_p']:.2e} |")
    lines.append(f"| Pearson r (active days only) | {corr_res['pearson_active']:.4f} |")
    lines.append(f"| Spearman r (active days only) | {corr_res['spearman_active']:.4f} |")
    lines.append(f"| Total days | {corr_res['n_total']} |")
    lines.append(f"| Active days (at least 1 in trade) | {corr_res['n_active']} |")
    lines.append("")

    # Interpretation
    pr = abs(corr_res['pearson_r'])
    if pr < 0.3:
        interp = "LOW correlation -- genuine diversification potential"
    elif pr < 0.5:
        interp = "MODERATE correlation -- some diversification, but limited"
    elif pr < 0.7:
        interp = "HIGH correlation -- strategies are substantially overlapping"
    else:
        interp = "VERY HIGH correlation -- strategies are near-redundant"
    lines.append(f"> **Interpretation**: {interp}")
    lines.append("")

    # ---- ROLLING CORRELATION ----
    lines.append("### Rolling 90-Day Correlation")
    lines.append("")
    roll_valid = roll_corr.dropna()
    if len(roll_valid) > 0:
        lines.append("| Statistic | Value |")
        lines.append("|-----------|-------|")
        lines.append(f"| Mean | {roll_valid.mean():.4f} |")
        lines.append(f"| Median | {roll_valid.median():.4f} |")
        lines.append(f"| Std Dev | {roll_valid.std():.4f} |")
        lines.append(f"| Min | {roll_valid.min():.4f} |")
        lines.append(f"| Max | {roll_valid.max():.4f} |")
        lines.append(f"| % of windows < 0.3 | {(roll_valid < 0.3).mean():.1%} |")
        lines.append(f"| % of windows < 0.0 | {(roll_valid < 0.0).mean():.1%} |")
        lines.append("")

        # Yearly breakdown
        lines.append("#### Yearly Rolling Correlation Summary")
        lines.append("")
        lines.append("| Year | Mean Corr | Min | Max |")
        lines.append("|------|----------|-----|-----|")
        for year in sorted(roll_valid.index.year.unique()):
            yr_data = roll_valid[roll_valid.index.year == year]
            if len(yr_data) > 0:
                lines.append(f"| {year} | {yr_data.mean():.3f} | {yr_data.min():.3f} | {yr_data.max():.3f} |")
        lines.append("")

    # ---- REGIME CORRELATION ----
    lines.append("### Regime-Specific Correlation")
    lines.append("")
    lines.append("| Regime | Pearson r | p-value | N days |")
    lines.append("|--------|----------|---------|--------|")
    for r in ['UPTREND', 'DOWNTREND', 'RANGE']:
        rc = regime_corr[r]
        if not np.isnan(rc['pearson_r']):
            lines.append(f"| {r} | {rc['pearson_r']:.4f} | {rc['p_value']:.2e} | {rc['n_days']} |")
        else:
            lines.append(f"| {r} | insufficient data | - | {rc['n_days']} |")
    lines.append("")

    # Count regime days
    regime_counts = regime.value_counts()
    lines.append("Regime distribution:")
    for r in ['UPTREND', 'DOWNTREND', 'RANGE']:
        if r in regime_counts.index:
            lines.append(f"- {r}: {regime_counts[r]} days ({regime_counts[r]/len(regime):.1%})")
    lines.append("")

    # ---- SECTION 3: ENTRY SIGNAL OVERLAP ----
    lines.append("## 2. Entry Signal Overlap")
    lines.append("")
    lines.append("### Position Overlap (daily)")
    lines.append("")
    lines.append("| State | Days | % |")
    lines.append("|-------|------|---|")
    lines.append(f"| Both in trade | {overlap['both_in']} | {overlap['both_in']/overlap['total']:.1%} |")
    lines.append(f"| V3 only (T+P flat) | {overlap['v3_only']} | {overlap['v3_only']/overlap['total']:.1%} |")
    lines.append(f"| T+P only (V3 flat) | {overlap['tp_only']} | {overlap['tp_only']/overlap['total']:.1%} |")
    lines.append(f"| Both flat | {overlap['both_flat']} | {overlap['both_flat']/overlap['total']:.1%} |")
    lines.append("")
    lines.append(f"- V3 in trade: {overlap['v3_in_days']} days ({overlap['v3_in_days']/overlap['total']:.1%})")
    lines.append(f"- T+P in trade: {overlap['tp_in_days']} days ({overlap['tp_in_days']/overlap['total']:.1%})")
    lines.append(f"- **T+P overlap with V3**: {overlap['overlap_pct']:.1f}% of T+P in-trade days overlap with V3")
    lines.append("")

    lines.append("### Entry-Level Analysis")
    lines.append("")
    lines.append(f"- Total T+P entry signals: **{overlap['total_tp_entries']}**")
    if overlap['total_tp_entries'] > 0:
        lines.append(f"- When T+P fires entry, V3 is already long: **{overlap['v3_long_at_tp_entry']}/{overlap['total_tp_entries']}** ({overlap['v3_long_at_tp_entry']/overlap['total_tp_entries']:.1%})")
    lines.append(f"- When V3 is flat, T+P fires: **{overlap['tp_fires_when_v3_flat']}** days out of {overlap['v3_flat_days']} V3-flat days")
    lines.append("")

    if overlap['total_tp_entries'] > 0 and overlap['v3_long_at_tp_entry'] / overlap['total_tp_entries'] > 0.90:
        lines.append("> **Key Finding**: T+P almost always fires when V3 is already long. This is expected since both require EMA20 > EMA50. T+P is a *subset* of V3 -- it picks specific *entry moments* within the broader V3 uptrend.")
    elif overlap['tp_fires_when_v3_flat'] == 0:
        lines.append("> **Key Finding**: T+P never fires when V3 is flat. Both strategies share the same trend precondition. T+P adds selectivity (RSI pullback timing) but not independence.")
    lines.append("")

    # ---- SECTION 4: PORTFOLIO SIMULATION ----
    lines.append("## 3. Portfolio Simulation")
    lines.append("")
    lines.append("| Portfolio | Ann. Return | Ann. Vol | Sharpe | Max DD | Calmar | Total Return |")
    lines.append("|-----------|-----------|---------|--------|--------|--------|-------------|")
    for key in ['buy_hold', 'v3_only', 'tp_only', 'equal_weight', 'risk_parity']:
        p = portfolio[key]
        lines.append(
            f"| {p['name']} | {p['ann_return']:.2%} | {p['ann_vol']:.2%} | "
            f"**{p['sharpe']:.3f}** | {p['max_dd']:.2%} | {p['calmar']:.2f} | {p['total_return']:.2%} |"
        )
    lines.append("")
    lines.append(f"Risk parity average weights: V3={portfolio['rp_avg_w_v3']:.1%}, T+P={portfolio['rp_avg_w_tp']:.1%}")
    lines.append("")

    # Sharpe improvement
    v3_sharpe = portfolio['v3_only']['sharpe']
    eq_sharpe = portfolio['equal_weight']['sharpe']
    rp_sharpe = portfolio['risk_parity']['sharpe']
    lines.append("### Portfolio Improvement vs V3 Alone")
    lines.append("")
    lines.append("| Metric | V3 Only | Equal Weight | Risk Parity |")
    lines.append("|--------|---------|-------------|-------------|")
    lines.append(f"| Sharpe | {v3_sharpe:.3f} | {eq_sharpe:.3f} ({(eq_sharpe - v3_sharpe)/abs(v3_sharpe)*100 if v3_sharpe != 0 else 0:+.1f}%) | {rp_sharpe:.3f} ({(rp_sharpe - v3_sharpe)/abs(v3_sharpe)*100 if v3_sharpe != 0 else 0:+.1f}%) |")
    lines.append(f"| Max DD | {portfolio['v3_only']['max_dd']:.2%} | {portfolio['equal_weight']['max_dd']:.2%} | {portfolio['risk_parity']['max_dd']:.2%} |")
    lines.append(f"| Calmar | {portfolio['v3_only']['calmar']:.2f} | {portfolio['equal_weight']['calmar']:.2f} | {portfolio['risk_parity']['calmar']:.2f} |")
    lines.append("")

    # ---- SECTION 5: CONDITIONAL ANALYSIS ----
    lines.append("## 4. Conditional Analysis")
    lines.append("")
    lines.append("### When V3 Is Losing (30-day rolling return < 0)")
    lines.append("")
    lines.append(f"- V3 losing periods: {conditional['v3_loss_days']} days")
    lines.append(f"- V3 mean daily return during loss: {conditional['v3_mean_during_v3_loss']:.4%}")
    lines.append(f"- T+P mean daily return during V3 loss: {conditional['tp_mean_during_v3_loss']:.4%}")
    lines.append(f"- T+P cumulative return during V3 loss periods: {conditional['tp_cumret_during_v3_loss']:.2%}")
    lines.append(f"- V3 cumulative return during V3 loss periods: {conditional['v3_cumret_during_v3_loss']:.2%}")
    lines.append(f"- T+P in trade during V3 loss: {conditional['tp_in_pct_during_v3_loss']:.1f}% of days")
    lines.append("")

    lines.append("### When V3 Is Flat (EMA20 < EMA50)")
    lines.append("")
    lines.append(f"- V3 flat days: {conditional['v3_flat_days']}")
    lines.append(f"- T+P mean daily return when V3 flat: {conditional['tp_when_v3_flat_mean']:.6%}")
    lines.append("")

    lines.append("### During Choppy Markets (>4 V3 position flips in 30 days)")
    lines.append("")
    lines.append(f"- Choppy days: {conditional['choppy_days']}")
    if conditional['choppy_days'] > 0:
        lines.append(f"- V3 mean daily return during chop: {conditional['v3_mean_during_chop']:.4%}")
        lines.append(f"- T+P mean daily return during chop: {conditional['tp_mean_during_chop']:.4%}")
    lines.append("")

    # ---- SECTION 6: VERDICT ----
    lines.append("## 5. Verdict")
    lines.append("")

    # Decision logic
    pearson = abs(corr_res['pearson_r'])
    active_corr = abs(corr_res['pearson_active']) if not np.isnan(corr_res['pearson_active']) else pearson
    sharpe_improvement = (eq_sharpe - v3_sharpe) / abs(v3_sharpe) * 100 if v3_sharpe != 0 else 0

    lines.append("### Correlation Assessment")
    lines.append("")
    if pearson < 0.3:
        lines.append(f"- Pearson correlation: **{corr_res['pearson_r']:.4f}** -- PASS (< 0.3 threshold)")
    else:
        lines.append(f"- Pearson correlation: **{corr_res['pearson_r']:.4f}** -- FAIL (>= 0.3 threshold)")
    if active_corr < 0.3:
        lines.append(f"- Active-day correlation: **{corr_res['pearson_active']:.4f}** -- PASS")
    else:
        lines.append(f"- Active-day correlation: **{corr_res['pearson_active']:.4f}** -- FAIL")
    lines.append("")

    lines.append("### Diversification Value")
    lines.append("")
    if sharpe_improvement > 5:
        lines.append(f"- Portfolio Sharpe improvement: **{sharpe_improvement:+.1f}%** -- meaningful improvement")
    elif sharpe_improvement > 0:
        lines.append(f"- Portfolio Sharpe improvement: **{sharpe_improvement:+.1f}%** -- marginal improvement")
    else:
        lines.append(f"- Portfolio Sharpe improvement: **{sharpe_improvement:+.1f}%** -- no improvement (adding T+P hurts)")
    lines.append("")

    tp_subset = overlap['overlap_pct']
    lines.append("### Structural Overlap")
    lines.append("")
    lines.append(f"- T+P in-trade overlap with V3: **{tp_subset:.1f}%**")
    if tp_subset > 90:
        lines.append("- T+P is structurally a **subset** of V3 -- it can only fire within V3 uptrends")
        lines.append("- The two strategies are NOT independent signal generators")
    lines.append(f"- T+P fires when V3 flat: **{overlap['tp_fires_when_v3_flat']}** days")
    lines.append("")

    lines.append("### Final Assessment")
    lines.append("")

    # Composite verdict
    is_low_corr = pearson < 0.3
    is_subset = tp_subset > 90
    has_sharpe_improvement = sharpe_improvement > 5

    if is_low_corr and not is_subset and has_sharpe_improvement:
        verdict = "DIVERSIFY: Add T+P to portfolio"
        explanation = "Low return correlation AND structural independence AND portfolio improvement."
    elif is_low_corr and is_subset:
        verdict = "MISLEADING CORRELATION: T+P appears uncorrelated but is structurally dependent"
        explanation = ("The low daily return correlation is driven by T+P being flat most of the time "
                      "(it only trades during pullbacks within V3 uptrends). When both are active, "
                      "they are exposed to the same trend. The 'diversification' comes from T+P's "
                      "intermittent exposure, not from independent signals.")
    elif not is_low_corr and is_subset:
        verdict = "REDUNDANT: T+P is a subset of V3, no diversification"
        explanation = "Both high correlation and structural overlap. T+P adds nothing to V3."
    elif has_sharpe_improvement:
        verdict = "CONDITIONAL: Marginal improvement despite correlation"
        explanation = "T+P improves portfolio Sharpe despite moderate correlation, likely through timing."
    else:
        verdict = "NO VALUE: T+P does not improve V3 portfolio"
        explanation = "Neither diversification nor performance improvement."

    lines.append(f"**{verdict}**")
    lines.append("")
    lines.append(f"{explanation}")
    lines.append("")

    lines.append("### Summary Statistics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Pearson correlation (daily returns) | {corr_res['pearson_r']:.4f} |")
    lines.append(f"| Active-day correlation | {corr_res['pearson_active']:.4f} |")
    lines.append(f"| T+P entry overlap with V3 | {tp_subset:.1f}% |")
    lines.append(f"| Portfolio Sharpe improvement (EW) | {sharpe_improvement:+.1f}% |")
    lines.append(f"| T+P fires when V3 flat | {overlap['tp_fires_when_v3_flat']} days |")
    lines.append(f"| V3 Sharpe | {v3_sharpe:.3f} |")
    lines.append(f"| T+P Sharpe | {portfolio['tp_only']['sharpe']:.3f} |")
    lines.append(f"| EW Portfolio Sharpe | {eq_sharpe:.3f} |")
    lines.append(f"| RP Portfolio Sharpe | {rp_sharpe:.3f} |")
    lines.append("")

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():
    t0 = time.time()
    print("=" * 70)
    print("R105 -- V3 Momentum vs Trend+Pullback Correlation Analysis")
    print("=" * 70)

    # 1. Load data
    print("\nLoading data...", flush=True)
    df_1h, daily_df, bars_4h = load_data()
    print(f"  1h bars: {len(df_1h)}, range {df_1h.index.min()} to {df_1h.index.max()}")
    print(f"  Daily bars: {len(daily_df)}")
    print(f"  4h bars: {len(bars_4h)}")

    # 2. Compute strategy returns
    print("\nComputing V3 Momentum returns...", flush=True)
    v3_ret, v3_pos = compute_v3_daily_returns(daily_df)
    print(f"  V3 in-trade days: {(v3_pos == 1).sum()} / {len(v3_pos)} ({(v3_pos == 1).mean():.1%})")

    print("\nComputing Trend+Pullback returns...", flush=True)
    tp_ret, tp_pos, tp_4h_pos, tp_entries, tp_exits = compute_tp_daily_returns(bars_4h, daily_df)
    print(f"  T+P in-trade days: {(tp_pos == 1).sum()} / {len(tp_pos)} ({(tp_pos == 1).mean():.1%})")
    print(f"  T+P total entries: {len(tp_entries)}")
    print(f"  T+P total exits: {len(tp_exits)}")

    # 3. Correlation analysis
    print("\nRunning correlation analysis...", flush=True)
    corr_res = correlation_analysis(v3_ret, tp_ret)
    print(f"  Pearson r: {corr_res['pearson_r']:.4f} (p={corr_res['pearson_p']:.2e})")
    print(f"  Spearman r: {corr_res['spearman_r']:.4f} (p={corr_res['spearman_p']:.2e})")
    print(f"  Active-day Pearson r: {corr_res['pearson_active']:.4f}")

    # 4. Rolling correlation
    print("\nComputing rolling 90-day correlation...", flush=True)
    roll_corr = rolling_correlation(v3_ret, tp_ret)

    # 5. Regime analysis
    print("\nRunning regime analysis...", flush=True)
    regime = regime_classification(daily_df)
    regime_corr = regime_correlation(v3_ret, tp_ret, regime)
    for r in ['UPTREND', 'DOWNTREND', 'RANGE']:
        rc = regime_corr[r]
        if not np.isnan(rc['pearson_r']):
            print(f"  {r}: r={rc['pearson_r']:.4f} (n={rc['n_days']})")
        else:
            print(f"  {r}: insufficient data (n={rc['n_days']})")

    # 6. Entry overlap
    print("\nAnalyzing entry signal overlap...", flush=True)
    overlap = entry_overlap_analysis(v3_pos, tp_pos, tp_4h_pos, bars_4h, tp_entries)
    print(f"  Both in: {overlap['both_in']} days ({overlap['both_in']/overlap['total']:.1%})")
    print(f"  V3 only: {overlap['v3_only']} days ({overlap['v3_only']/overlap['total']:.1%})")
    print(f"  T+P only: {overlap['tp_only']} days ({overlap['tp_only']/overlap['total']:.1%})")
    print(f"  T+P overlap with V3: {overlap['overlap_pct']:.1f}%")

    # 7. Portfolio simulation
    print("\nRunning portfolio simulation...", flush=True)
    portfolio = portfolio_simulation(v3_ret, tp_ret, daily_df)
    for key in ['buy_hold', 'v3_only', 'tp_only', 'equal_weight', 'risk_parity']:
        p = portfolio[key]
        print(f"  {p['name']}: Sharpe={p['sharpe']:.3f}, Return={p['total_return']:.2%}, MaxDD={p['max_dd']:.2%}")

    # 8. Conditional analysis
    print("\nRunning conditional analysis...", flush=True)
    conditional = conditional_analysis(v3_ret, tp_ret, v3_pos, tp_pos, daily_df)
    print(f"  V3 losing days: {conditional['v3_loss_days']}")
    print(f"  T+P mean during V3 loss: {conditional['tp_mean_during_v3_loss']:.4%}")
    print(f"  T+P in-trade during V3 loss: {conditional['tp_in_pct_during_v3_loss']:.1f}%")

    # 9. Generate report
    print("\nGenerating report...", flush=True)
    v3_stats = compute_strategy_stats(v3_ret, 'V3 Momentum')
    tp_stats = compute_strategy_stats(tp_ret, 'Trend+Pullback')
    report = generate_report(corr_res, roll_corr, regime_corr, overlap, portfolio,
                             conditional, v3_stats, tp_stats, daily_df, regime)

    with open(OUTPUT_MD, 'w') as f:
        f.write(report)

    elapsed = time.time() - t0
    print(f"\n{'='*70}")
    print(f"Report written to {OUTPUT_MD}")
    print(f"Total elapsed: {elapsed:.1f}s")
    print(f"{'='*70}")


if __name__ == '__main__':
    main()
