"""
R163 -- Adaptive Leverage Overlay on Momentum Rotation (R158)

Takes the best R158 momentum rotation strategy (L7_R7_K5_70/30_RF) and applies
adaptive leverage overlays to boost returns while controlling drawdowns.

Base strategy: L7_R7_K5_70/30 with per-token regime filter
  - Lookback: 7 days
  - Rebalance: every 7 days
  - K: 5 tokens per leg
  - Allocation: 70% long, 30% short
  - Per-token regime filter: EMA(20h) > EMA(50h)

Adaptive leverage variants:
  A: Regime-based (BTC EMA20h vs EMA50h)
  B: Drawdown-based (portfolio drawdown tiers)
  C: Momentum-of-momentum (strategy trailing 14d return)
  D: Combined (Regime + Drawdown)
  E: Static leverage comparison (1x, 1.5x, 2x, 2.5x, 3x)

All variants apply leverage DAILY to the base strategy's daily returns,
including borrow cost at 5% annual.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional
import warnings
import time
import os

warnings.filterwarnings('ignore')

# ============================================================
# CONSTANTS
# ============================================================

DATA_DIR = '/workspace/crypto_backtest/data/perp/1h_cache'
OUTPUT_PATH = '/workspace/crypto_backtest/research/R163_adaptive_leverage_results.md'

# Date constraints (same as R158)
DATA_CUTOFF = pd.Timestamp('2024-03-17')
SIM_START = pd.Timestamp('2024-03-17')
SIM_END = pd.Timestamp('2026-03-17')
LAST_12MO_START = pd.Timestamp('2025-03-17')

# Volume filter
MIN_AVG_DAILY_VOLUME_USD = 1_000_000

# Cost
FEE_BPS = 7.0
BORROW_COST_ANNUAL = 0.05  # 5% annual borrow cost for leverage

# EMA regime filter params (hourly)
EMA_FAST_H = 20
EMA_SLOW_H = 50

# Lookback warmup
MAX_LOOKBACK_DAYS = 30

# Time
HOURS_PER_YEAR = 8760
DAYS_PER_YEAR = 365

# Base strategy params (R158 best: L7_R7_K5_70/30_RFY)
BASE_LOOKBACK = 7
BASE_REBALANCE = 7
BASE_K = 5
BASE_LONG_WT = 0.70
BASE_SHORT_WT = 0.30
BASE_USE_REGIME_FILTER = True


# ============================================================
# DATA LOADING (same as R158)
# ============================================================

def load_all_tokens() -> Dict[str, pd.DataFrame]:
    """Load all tokens with data before cutoff and apply volume filter."""
    files = sorted(os.listdir(DATA_DIR))
    tokens = {}

    for f in files:
        if not f.endswith('_1h.parquet'):
            continue
        ticker = f.replace('_1h.parquet', '')
        path = os.path.join(DATA_DIR, f)
        df = pd.read_parquet(path)

        if df.index.min() >= DATA_CUTOFF:
            continue

        pre_sim = df[df.index < SIM_START]
        if len(pre_sim) < 30 * 24:
            continue

        last_30d = pre_sim.tail(30 * 24)
        daily_dvol = (last_30d['volume'] * last_30d['close']).resample('1D').sum()
        avg_dvol = daily_dvol.mean()

        if avg_dvol < MIN_AVG_DAILY_VOLUME_USD:
            continue

        tokens[ticker] = df

    return tokens


def prepare_daily_data(tokens: Dict[str, pd.DataFrame]):
    """Prepare aligned daily data panels."""
    daily_closes = {}
    daily_volumes = {}
    ema_signals = {}
    hourly_fundings = {}

    for ticker, df in tokens.items():
        daily = df['close'].resample('1D').last().dropna()
        daily_closes[ticker] = daily

        dvol = (df['volume'] * df['close']).resample('1D').sum()
        daily_volumes[ticker] = dvol

        ema_fast = df['close'].ewm(span=EMA_FAST_H, adjust=False).mean()
        ema_slow = df['close'].ewm(span=EMA_SLOW_H, adjust=False).mean()
        ema_diff = (ema_fast - ema_slow).resample('1D').last()
        ema_signals[ticker] = ema_diff

        hourly_fundings[ticker] = df['funding_1h'].fillna(0)

    daily_close = pd.DataFrame(daily_closes)
    daily_volume = pd.DataFrame(daily_volumes)
    ema_signal = pd.DataFrame(ema_signals)

    start_with_buffer = SIM_START - pd.Timedelta(days=MAX_LOOKBACK_DAYS + 10)
    daily_close = daily_close.loc[start_with_buffer:SIM_END]
    daily_volume = daily_volume.reindex(daily_close.index)
    ema_signal = ema_signal.reindex(daily_close.index)

    daily_returns = daily_close.pct_change()

    lookback_returns_shifted = {}
    for L in [BASE_LOOKBACK]:
        lb = daily_close.shift(1) / daily_close.shift(1 + L) - 1
        lookback_returns_shifted[L] = lb

    ema_signal_shifted = ema_signal.shift(1)

    rolling_avg_dvol = daily_volume.rolling(30, min_periods=15).mean()
    rolling_avg_dvol_shifted = rolling_avg_dvol.shift(1)

    return (daily_close, daily_returns, lookback_returns_shifted,
            ema_signal_shifted, rolling_avg_dvol_shifted, hourly_fundings)


def prepare_btc_regime(tokens: Dict[str, pd.DataFrame]) -> pd.Series:
    """
    Compute BTC regime signal: EMA(20h) > EMA(50h) → bullish (True).
    Returns a daily Series (shifted by 1 day for look-ahead protection).
    """
    btc_df = tokens['BTC']
    ema_fast = btc_df['close'].ewm(span=EMA_FAST_H, adjust=False).mean()
    ema_slow = btc_df['close'].ewm(span=EMA_SLOW_H, adjust=False).mean()
    btc_bullish = (ema_fast > ema_slow).resample('1D').last()
    # Shift by 1 day to avoid look-ahead
    return btc_bullish.shift(1)


# ============================================================
# BASE STRATEGY SIMULATION (R158 core logic)
# ============================================================

def run_base_strategy(
    daily_close, daily_returns, lookback_returns_shifted,
    ema_signal_shifted, rolling_avg_dvol_shifted, hourly_fundings
) -> pd.Series:
    """
    Run the R158 base strategy (L7_R7_K5_70/30_RFY) and return DAILY returns
    as a Series (date -> daily_return_fraction).

    This is the unleveraged daily return of the strategy.
    """
    L = BASE_LOOKBACK
    N = BASE_REBALANCE
    K = BASE_K
    long_wt = BASE_LONG_WT
    short_wt = BASE_SHORT_WT
    use_regime_filter = BASE_USE_REGIME_FILTER

    sim_dates = daily_close.loc[SIM_START:SIM_END].index
    lb_rets_df = lookback_returns_shifted[L]
    rebalance_indices = set(range(0, len(sim_dates), N))

    # Track state
    current_longs = {}
    current_shorts = {}
    prev_longs = set()
    prev_shorts = set()

    pending_longs = None
    pending_shorts = None

    daily_strategy_returns = {}
    trade_count = 0
    total_cost_impact = 0.0  # cumulative cost as fraction of equity

    for i, date in enumerate(sim_dates):
        # ---- Apply pending rebalance from yesterday ----
        cost_frac = 0.0
        if pending_longs is not None:
            new_longs_set = set(pending_longs.keys())
            new_shorts_set = set(pending_shorts.keys())

            long_entries = new_longs_set - prev_longs
            long_exits = prev_longs - new_longs_set
            short_entries = new_shorts_set - prev_shorts
            short_exits = prev_shorts - new_shorts_set
            n_trades = len(long_entries) + len(long_exits) + len(short_entries) + len(short_exits)
            trade_count += n_trades

            cost = 0.0
            for t in long_entries:
                cost += pending_longs[t] * (FEE_BPS / 10000)
            for t in long_exits:
                cost += current_longs.get(t, 0) * (FEE_BPS / 10000)
            for t in short_entries:
                cost += pending_shorts[t] * (FEE_BPS / 10000)
            for t in short_exits:
                cost += current_shorts.get(t, 0) * (FEE_BPS / 10000)

            cost_frac = cost
            current_longs = pending_longs
            current_shorts = pending_shorts
            prev_longs = new_longs_set
            prev_shorts = new_shorts_set
            pending_longs = None
            pending_shorts = None

        # ---- Daily PnL from current positions ----
        daily_pnl = 0.0
        daily_ret = daily_returns.loc[date] if date in daily_returns.index else pd.Series(dtype=float)

        for ticker, weight in current_longs.items():
            if ticker in daily_ret.index and not np.isnan(daily_ret[ticker]):
                daily_pnl += weight * daily_ret[ticker]

        for ticker, weight in current_shorts.items():
            if ticker in daily_ret.index and not np.isnan(daily_ret[ticker]):
                daily_pnl -= weight * daily_ret[ticker]

        # Funding
        funding_start = date
        funding_end = date + pd.Timedelta(hours=23)

        for ticker, weight in current_longs.items():
            if ticker in hourly_fundings:
                f_slice = hourly_fundings[ticker].loc[funding_start:funding_end]
                if len(f_slice) > 0:
                    daily_pnl += -f_slice.sum() * weight

        for ticker, weight in current_shorts.items():
            if ticker in hourly_fundings:
                f_slice = hourly_fundings[ticker].loc[funding_start:funding_end]
                if len(f_slice) > 0:
                    daily_pnl += f_slice.sum() * weight

        # Net daily return including cost
        net_daily_return = (1 - cost_frac) * (1 + daily_pnl) - 1
        daily_strategy_returns[date] = net_daily_return

        # ---- Decide rebalance ----
        if i in rebalance_indices:
            lb_rets = lb_rets_df.loc[date].dropna() if date in lb_rets_df.index else pd.Series(dtype=float)
            vol_at_date = rolling_avg_dvol_shifted.loc[date].dropna() if date in rolling_avg_dvol_shifted.index else pd.Series(dtype=float)
            eligible = vol_at_date[vol_at_date >= MIN_AVG_DAILY_VOLUME_USD].index
            lb_rets = lb_rets[lb_rets.index.isin(eligible)]

            if len(lb_rets) >= 2 * K:
                ranked = lb_rets.sort_values(ascending=False)
                top_k = ranked.head(K).index.tolist()
                bottom_k = ranked.tail(K).index.tolist()

                if use_regime_filter:
                    ema_at_date = ema_signal_shifted.loc[date].dropna() if date in ema_signal_shifted.index else pd.Series(dtype=float)
                    top_k = [t for t in top_k if t in ema_at_date.index and ema_at_date[t] > 0]
                    bottom_k = [t for t in bottom_k if t in ema_at_date.index and ema_at_date[t] < 0]

                n_longs = len(top_k) if len(top_k) > 0 else 1
                n_shorts = len(bottom_k) if len(bottom_k) > 0 else 1

                pending_longs = {t: long_wt / n_longs for t in top_k} if top_k else {}
                pending_shorts = {t: short_wt / n_shorts for t in bottom_k} if bottom_k else {}

    return pd.Series(daily_strategy_returns)


# ============================================================
# ADAPTIVE LEVERAGE OVERLAYS
# ============================================================

def apply_leverage(base_daily_returns: pd.Series, leverage_series: pd.Series,
                   borrow_cost_annual: float = BORROW_COST_ANNUAL) -> Tuple[pd.Series, pd.Series]:
    """
    Apply daily leverage to base strategy returns.

    leveraged_return[t] = leverage[t] * base_return[t] - (leverage[t] - 1) * borrow_cost / 365

    Returns:
        equity_curve: pd.Series of cumulative equity
        leverage_used: pd.Series of daily leverage values
    """
    daily_borrow = borrow_cost_annual / DAYS_PER_YEAR

    equity = 1.0
    equity_series = {}
    leverage_used = {}

    for date in base_daily_returns.index:
        base_ret = base_daily_returns[date]
        lev = leverage_series.get(date, 1.0) if isinstance(leverage_series, dict) else (
            leverage_series.loc[date] if date in leverage_series.index else 1.0
        )

        # Leveraged return with borrow cost
        lev_ret = lev * base_ret - (lev - 1) * daily_borrow

        # Protect against going below zero (liquidation)
        if equity * (1 + lev_ret) <= 0:
            equity = equity * 0.01  # wipeout to near-zero
        else:
            equity *= (1 + lev_ret)

        equity_series[date] = equity
        leverage_used[date] = lev

    return pd.Series(equity_series), pd.Series(leverage_used)


def variant_a_regime_leverage(base_daily_returns: pd.Series,
                               btc_regime: pd.Series) -> Tuple[pd.Series, pd.Series, str]:
    """Variant A: Regime-based leverage. BTC bullish → 2.5x, bearish → 1.0x."""
    leverage = {}
    for date in base_daily_returns.index:
        if date in btc_regime.index and not pd.isna(btc_regime.loc[date]):
            bullish = btc_regime.loc[date]
            leverage[date] = 2.5 if bullish else 1.0
        else:
            leverage[date] = 1.0

    eq, lev_used = apply_leverage(base_daily_returns, leverage)
    return eq, lev_used, "A: Regime (BTC EMA20>50 → 2.5x, else 1.0x)"


def variant_b_drawdown_leverage(base_daily_returns: pd.Series) -> Tuple[pd.Series, pd.Series, str]:
    """Variant B: Drawdown-based deleveraging."""
    daily_borrow = BORROW_COST_ANNUAL / DAYS_PER_YEAR
    equity = 1.0
    peak = 1.0
    equity_series = {}
    leverage_used = {}

    for date in base_daily_returns.index:
        # Compute current drawdown
        dd = (equity / peak - 1) if peak > 0 else 0

        # Determine leverage based on drawdown
        if dd >= 0:       # at or above peak (dd=0 means at peak)
            lev = 2.5
        elif dd > -0.10:  # 0-10% DD
            lev = 2.0
        elif dd > -0.20:  # 10-20% DD
            lev = 1.5
        elif dd > -0.30:  # 20-30% DD
            lev = 1.0
        else:             # >30% DD
            lev = 0.5

        base_ret = base_daily_returns[date]
        lev_ret = lev * base_ret - (lev - 1) * daily_borrow

        if equity * (1 + lev_ret) <= 0:
            equity = equity * 0.01
        else:
            equity *= (1 + lev_ret)

        if equity > peak:
            peak = equity

        equity_series[date] = equity
        leverage_used[date] = lev

    return pd.Series(equity_series), pd.Series(leverage_used), "B: Drawdown (0%→2.5x, 10%→2.0x, 20%→1.5x, 30%→1.0x, >30%→0.5x)"


def variant_c_mom_of_mom_leverage(base_daily_returns: pd.Series) -> Tuple[pd.Series, pd.Series, str]:
    """Variant C: Momentum of momentum. Trailing 14d strategy return → leverage."""
    # First compute the base equity curve to get trailing returns
    base_equity = (1 + base_daily_returns).cumprod()

    daily_borrow = BORROW_COST_ANNUAL / DAYS_PER_YEAR
    equity = 1.0
    equity_series = {}
    leverage_used = {}

    dates = base_daily_returns.index.tolist()

    for idx, date in enumerate(dates):
        # Compute trailing 14-day return of BASE strategy
        if idx >= 14:
            trailing_ret = base_equity.iloc[idx] / base_equity.iloc[idx - 14] - 1
        else:
            trailing_ret = 0.0

        # Determine leverage
        if trailing_ret > 0.05:
            lev = 3.0
        elif trailing_ret > 0.0:
            lev = 2.0
        elif trailing_ret > -0.05:
            lev = 1.0
        else:
            lev = 0.5

        base_ret = base_daily_returns.iloc[idx]
        lev_ret = lev * base_ret - (lev - 1) * daily_borrow

        if equity * (1 + lev_ret) <= 0:
            equity = equity * 0.01
        else:
            equity *= (1 + lev_ret)

        equity_series[date] = equity
        leverage_used[date] = lev

    return pd.Series(equity_series), pd.Series(leverage_used), "C: MoM (14d ret >5%→3x, 0-5%→2x, -5-0%→1x, <-5%→0.5x)"


def variant_d_combined_leverage(base_daily_returns: pd.Series,
                                 btc_regime: pd.Series) -> Tuple[pd.Series, pd.Series, str]:
    """Variant D: Combined regime + drawdown. Base leverage from regime, scaled by drawdown."""
    daily_borrow = BORROW_COST_ANNUAL / DAYS_PER_YEAR
    equity = 1.0
    peak = 1.0
    equity_series = {}
    leverage_used = {}

    for date in base_daily_returns.index:
        # Regime base leverage
        if date in btc_regime.index and not pd.isna(btc_regime.loc[date]):
            bullish = btc_regime.loc[date]
            regime_lev = 2.5 if bullish else 1.0
        else:
            regime_lev = 1.0

        # Drawdown scale factor
        dd = (equity / peak - 1) if peak > 0 else 0

        if dd > -0.10:
            dd_scale = 1.0
        elif dd > -0.20:
            dd_scale = 0.75
        elif dd > -0.30:
            dd_scale = 0.50
        else:
            dd_scale = 0.25

        lev = regime_lev * dd_scale

        base_ret = base_daily_returns[date]
        lev_ret = lev * base_ret - (lev - 1) * daily_borrow

        if equity * (1 + lev_ret) <= 0:
            equity = equity * 0.01
        else:
            equity *= (1 + lev_ret)

        if equity > peak:
            peak = equity

        equity_series[date] = equity
        leverage_used[date] = lev

    return pd.Series(equity_series), pd.Series(leverage_used), "D: Combined (Regime * DD scale)"


def variant_e_static_leverage(base_daily_returns: pd.Series,
                               static_lev: float) -> Tuple[pd.Series, pd.Series, str]:
    """Variant E: Static leverage."""
    leverage = {date: static_lev for date in base_daily_returns.index}
    eq, lev_used = apply_leverage(base_daily_returns, leverage)
    return eq, lev_used, f"E: Static {static_lev:.1f}x"


# ============================================================
# METRICS
# ============================================================

def compute_metrics(equity: pd.Series) -> dict:
    """Compute standard strategy metrics from an equity curve."""
    if len(equity) < 2 or equity.iloc[-1] <= 0:
        return {
            'annual_return': -1.0, 'max_dd': -1.0, 'sharpe': -99.0,
            'calmar': -99.0, 'last_12m_return': -1.0,
        }

    daily_eq = equity.resample('1D').last().dropna() if hasattr(equity.index, 'freq') else equity
    daily_rets = daily_eq.pct_change().dropna()

    if len(daily_rets) < 30:
        return {
            'annual_return': -1.0, 'max_dd': -1.0, 'sharpe': -99.0,
            'calmar': -99.0, 'last_12m_return': -1.0,
        }

    total_return = equity.iloc[-1] / equity.iloc[0] - 1
    n_days = (equity.index[-1] - equity.index[0]).days
    if n_days < 1:
        n_days = 1
    annual_return = (1 + total_return) ** (DAYS_PER_YEAR / n_days) - 1

    running_max = daily_eq.cummax()
    drawdowns = daily_eq / running_max - 1
    max_dd = drawdowns.min()

    mean_daily = daily_rets.mean()
    std_daily = daily_rets.std()
    sharpe = (mean_daily / std_daily * np.sqrt(DAYS_PER_YEAR)) if std_daily > 0 else 0.0

    calmar = annual_return / abs(max_dd) if max_dd != 0 else 0.0

    last_12m = daily_eq[daily_eq.index >= LAST_12MO_START]
    if len(last_12m) > 1:
        last_12m_return = last_12m.iloc[-1] / last_12m.iloc[0] - 1
    else:
        last_12m_return = 0.0

    return {
        'annual_return': annual_return,
        'max_dd': max_dd,
        'sharpe': sharpe,
        'calmar': calmar,
        'last_12m_return': last_12m_return,
    }


# ============================================================
# REPORTING
# ============================================================

def format_pct(v: float) -> str:
    return f"{v*100:.1f}%"


def format_float(v: float, decimals: int = 2) -> str:
    return f"{v:.{decimals}f}"


def monthly_returns_table(equity: pd.Series) -> str:
    """Generate a monthly returns table as markdown."""
    daily_eq = equity
    monthly_eq = daily_eq.resample('ME').last().dropna()
    monthly_rets = monthly_eq.pct_change().dropna()

    years = sorted(monthly_rets.index.year.unique())
    month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

    lines = []
    header = "| Year | " + " | ".join(month_names) + " | Annual |"
    sep = "|------|" + "|".join(["-------"] * 12) + "|--------|"
    lines.append(header)
    lines.append(sep)

    for year in years:
        row = [f"| {year} "]
        yr_ret = 1.0
        for m in range(1, 13):
            mask = (monthly_rets.index.year == year) & (monthly_rets.index.month == m)
            vals = monthly_rets[mask]
            if len(vals) > 0:
                r = vals.iloc[0]
                yr_ret *= (1 + r)
                row.append(f" {r*100:+.1f}% ")
            else:
                row.append("  --  ")
        row.append(f" {(yr_ret-1)*100:+.1f}% ")
        lines.append("|".join(row) + "|")

    return "\n".join(lines)


def generate_report(results: List[dict], base_metrics: dict, base_equity: pd.Series,
                    n_tokens: int, token_list: List[str]) -> str:
    """Generate the full results markdown report."""
    lines = []
    lines.append("# R163 -- Adaptive Leverage on Momentum Rotation\n")
    lines.append(f"**Generated:** {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Simulation Period:** {SIM_START.date()} to {SIM_END.date()} (~2 years)")
    lines.append(f"**Last 12 Months:** {LAST_12MO_START.date()} to {SIM_END.date()}")
    lines.append(f"**Token Universe:** {n_tokens} tokens")
    lines.append(f"**Base Strategy:** R158 L{BASE_LOOKBACK}_R{BASE_REBALANCE}_K{BASE_K}_{int(BASE_LONG_WT*100)}/{int(BASE_SHORT_WT*100)}_RFY")
    lines.append(f"**Costs:** {FEE_BPS:.0f} bps per side + hourly funding + {BORROW_COST_ANNUAL*100:.0f}% annual borrow cost on leveraged portion")
    lines.append(f"**Look-ahead protection:** All signals shifted by 1 day\n")

    # Base strategy reference
    lines.append("---\n")
    lines.append("## Base Strategy (1x, no leverage overlay)\n")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Annual Return | {format_pct(base_metrics['annual_return'])} |")
    lines.append(f"| Last 12M Return | {format_pct(base_metrics['last_12m_return'])} |")
    lines.append(f"| Sharpe | {format_float(base_metrics['sharpe'])} |")
    lines.append(f"| MaxDD | {format_pct(base_metrics['max_dd'])} |")
    lines.append(f"| Calmar | {format_float(base_metrics['calmar'])} |")
    lines.append(f"| Avg Leverage | 1.00x |")
    lines.append("")

    # ---- ALL VARIANTS COMPARISON TABLE ----
    lines.append("---\n")
    lines.append("## All Variants Comparison\n")
    lines.append("| Variant | Description | Ann Ret | 12M Ret | Sharpe | MaxDD | Calmar | Avg Lev | Target Met? |")
    lines.append("|---------|-------------|---------|---------|--------|-------|--------|---------|-------------|")

    # Sort by 12M return descending
    sorted_results = sorted(results, key=lambda r: r['last_12m_return'], reverse=True)

    for r in sorted_results:
        target = ""
        if r['last_12m_return'] >= 3.0 and r['max_dd'] > -0.70:
            target = "YES"
        elif r['last_12m_return'] >= 3.0:
            target = "RET OK, DD HIGH"
        elif r['max_dd'] > -0.70:
            target = "DD OK, RET LOW"
        else:
            target = "NO"

        lines.append(
            f"| {r['variant']} | {r['description']} "
            f"| {format_pct(r['annual_return'])} | **{format_pct(r['last_12m_return'])}** "
            f"| {format_float(r['sharpe'])} | {format_pct(r['max_dd'])} "
            f"| {format_float(r['calmar'])} | {r['avg_leverage']:.2f}x "
            f"| {target} |"
        )

    # ---- TARGET ANALYSIS ----
    lines.append("\n---\n")
    lines.append("## Target Analysis (300%+ 12M Return, MaxDD < 70%)\n")

    hits = [r for r in results if r['last_12m_return'] >= 3.0 and r['max_dd'] > -0.70]
    if hits:
        lines.append(f"**{len(hits)} variant(s) meet the target:**\n")
        for r in sorted(hits, key=lambda x: x['last_12m_return'], reverse=True):
            lines.append(f"- **{r['variant']}** ({r['description']}): "
                         f"12M Return = {format_pct(r['last_12m_return'])}, "
                         f"MaxDD = {format_pct(r['max_dd'])}, "
                         f"Sharpe = {format_float(r['sharpe'])}, "
                         f"Avg Leverage = {r['avg_leverage']:.2f}x")
    else:
        lines.append("**No variants meet both targets simultaneously.**\n")
        # Find closest
        close_ret = [r for r in results if r['last_12m_return'] >= 3.0]
        close_dd = [r for r in results if r['max_dd'] > -0.70]
        if close_ret:
            lines.append("Variants hitting 300%+ return but exceeding DD limit:")
            for r in close_ret:
                lines.append(f"  - {r['variant']}: 12M={format_pct(r['last_12m_return'])}, MaxDD={format_pct(r['max_dd'])}")
        best_by_ret = sorted(results, key=lambda x: x['last_12m_return'], reverse=True)[0]
        best_dd_ok = sorted([r for r in results if r['max_dd'] > -0.70],
                            key=lambda x: x['last_12m_return'], reverse=True)
        if best_dd_ok:
            lines.append(f"\nBest 12M return with MaxDD < 70%: **{best_dd_ok[0]['variant']}** "
                         f"({format_pct(best_dd_ok[0]['last_12m_return'])}, MaxDD={format_pct(best_dd_ok[0]['max_dd'])})")

    # ---- LEVERAGE ANALYSIS ----
    lines.append("\n---\n")
    lines.append("## Leverage Usage Analysis\n")
    lines.append("| Variant | Avg Lev | Min Lev | Max Lev | % Days >2x | % Days >2.5x |")
    lines.append("|---------|---------|---------|---------|------------|--------------|")

    for r in sorted_results:
        lev = r['leverage_series']
        pct_above_2 = (lev > 2.0).mean() * 100
        pct_above_2_5 = (lev > 2.5).mean() * 100
        lines.append(
            f"| {r['variant']} | {lev.mean():.2f}x | {lev.min():.1f}x | {lev.max():.1f}x "
            f"| {pct_above_2:.1f}% | {pct_above_2_5:.1f}% |"
        )

    # ---- MONTHLY RETURNS for best variant ----
    if sorted_results:
        lines.append("\n---\n")
        best = sorted_results[0]
        lines.append(f"## Monthly Returns: Best by 12M Return ({best['variant']})\n")
        lines.append(f"**{best['description']}**\n")
        lines.append(monthly_returns_table(best['equity_curve']))

        # Also show best target-meeting variant if different
        if hits:
            best_hit = sorted(hits, key=lambda x: x['last_12m_return'], reverse=True)[0]
            if best_hit['variant'] != best['variant']:
                lines.append(f"\n---\n")
                lines.append(f"## Monthly Returns: Best Target-Meeting ({best_hit['variant']})\n")
                lines.append(f"**{best_hit['description']}**\n")
                lines.append(monthly_returns_table(best_hit['equity_curve']))

    # ---- STATIC LEVERAGE ANALYSIS ----
    lines.append("\n---\n")
    lines.append("## Static Leverage Scaling Analysis\n")
    lines.append("| Leverage | Ann Ret | 12M Ret | Sharpe | MaxDD | Calmar |")
    lines.append("|----------|---------|---------|--------|-------|--------|")

    static_results = sorted([r for r in results if r['variant'].startswith('E')],
                            key=lambda r: r['avg_leverage'])
    for r in static_results:
        lines.append(
            f"| {r['avg_leverage']:.1f}x | {format_pct(r['annual_return'])} "
            f"| {format_pct(r['last_12m_return'])} | {format_float(r['sharpe'])} "
            f"| {format_pct(r['max_dd'])} | {format_float(r['calmar'])} |"
        )

    lines.append("\n---\n")
    lines.append("## Key Insight\n")
    lines.append("Adaptive leverage aims to be 'in heavy' during favorable conditions ")
    lines.append("and 'light' during drawdowns. The advantage over static leverage is ")
    lines.append("reducing exposure during the worst periods while maintaining aggressive ")
    lines.append("exposure during the best periods.\n")

    # Compare best adaptive vs equivalent static
    adaptive_variants = [r for r in results if not r['variant'].startswith('E')]
    if adaptive_variants:
        best_adaptive = sorted(adaptive_variants, key=lambda x: x['calmar'], reverse=True)[0]
        avg_lev = best_adaptive['avg_leverage']
        # Find closest static
        closest_static = min(static_results, key=lambda x: abs(x['avg_leverage'] - avg_lev))

        lines.append(f"**Best adaptive (by Calmar):** {best_adaptive['variant']} — "
                     f"Calmar={format_float(best_adaptive['calmar'])}, "
                     f"12M={format_pct(best_adaptive['last_12m_return'])}, "
                     f"MaxDD={format_pct(best_adaptive['max_dd'])}, "
                     f"Avg Lev={best_adaptive['avg_leverage']:.2f}x\n")
        lines.append(f"**Nearest static ({closest_static['avg_leverage']:.1f}x):** "
                     f"Calmar={format_float(closest_static['calmar'])}, "
                     f"12M={format_pct(closest_static['last_12m_return'])}, "
                     f"MaxDD={format_pct(closest_static['max_dd'])}\n")

        if best_adaptive['calmar'] > closest_static['calmar']:
            improvement = (best_adaptive['calmar'] / closest_static['calmar'] - 1) * 100
            lines.append(f"Adaptive leverage improves risk-adjusted returns by {improvement:.0f}% (Calmar ratio) "
                         f"vs equivalent static leverage.")
        else:
            lines.append("Static leverage matches or exceeds adaptive leverage in this backtest period. "
                         "Adaptive leverage may still help in different market conditions.")

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():
    t0 = time.time()

    print("=" * 70)
    print("R163 -- Adaptive Leverage on Momentum Rotation (R158)")
    print("=" * 70)

    # Load data
    print("\n[1/4] Loading token data...")
    tokens = load_all_tokens()
    token_list = sorted(tokens.keys())
    print(f"  Loaded {len(tokens)} tokens passing volume filter")

    # Prepare daily data
    print("\n[2/4] Preparing daily data panels...")
    (daily_close, daily_returns, lookback_returns_shifted,
     ema_signal_shifted, rolling_avg_dvol_shifted, hourly_fundings) = prepare_daily_data(tokens)
    print(f"  Daily close shape: {daily_close.shape}")
    print(f"  Date range: {daily_close.index.min().date()} to {daily_close.index.max().date()}")

    # Prepare BTC regime signal
    print("  Computing BTC regime signal...")
    btc_regime = prepare_btc_regime(tokens)
    btc_bullish_pct = btc_regime.loc[SIM_START:SIM_END].dropna().mean() * 100
    print(f"  BTC bullish {btc_bullish_pct:.1f}% of simulation period")

    # Run base strategy
    print("\n[3/4] Running base strategy (L7_R7_K5_70/30_RFY)...")
    t1 = time.time()
    base_daily_returns = run_base_strategy(
        daily_close, daily_returns, lookback_returns_shifted,
        ema_signal_shifted, rolling_avg_dvol_shifted, hourly_fundings
    )
    t2 = time.time()
    print(f"  Base strategy completed in {t2-t1:.1f}s")
    print(f"  Daily returns: {len(base_daily_returns)} days")
    print(f"  Mean daily return: {base_daily_returns.mean()*100:.3f}%")
    print(f"  Std daily return: {base_daily_returns.std()*100:.3f}%")

    # Compute base equity curve and metrics
    base_equity = (1 + base_daily_returns).cumprod()
    base_metrics = compute_metrics(base_equity)
    print(f"  Base strategy: Ann={format_pct(base_metrics['annual_return'])}, "
          f"12M={format_pct(base_metrics['last_12m_return'])}, "
          f"Sharpe={format_float(base_metrics['sharpe'])}, "
          f"MaxDD={format_pct(base_metrics['max_dd'])}")

    # Run all adaptive leverage variants
    print("\n[4/4] Running adaptive leverage variants...")
    all_results = []

    # Variant A: Regime-based
    print("  Running Variant A (Regime-based)...")
    eq_a, lev_a, desc_a = variant_a_regime_leverage(base_daily_returns, btc_regime)
    metrics_a = compute_metrics(eq_a)
    all_results.append({
        'variant': 'A',
        'description': desc_a,
        'equity_curve': eq_a,
        'leverage_series': lev_a,
        'avg_leverage': lev_a.mean(),
        **metrics_a,
    })
    print(f"    A: 12M={format_pct(metrics_a['last_12m_return'])}, MaxDD={format_pct(metrics_a['max_dd'])}, Avg Lev={lev_a.mean():.2f}x")

    # Variant B: Drawdown-based
    print("  Running Variant B (Drawdown-based)...")
    eq_b, lev_b, desc_b = variant_b_drawdown_leverage(base_daily_returns)
    metrics_b = compute_metrics(eq_b)
    all_results.append({
        'variant': 'B',
        'description': desc_b,
        'equity_curve': eq_b,
        'leverage_series': lev_b,
        'avg_leverage': lev_b.mean(),
        **metrics_b,
    })
    print(f"    B: 12M={format_pct(metrics_b['last_12m_return'])}, MaxDD={format_pct(metrics_b['max_dd'])}, Avg Lev={lev_b.mean():.2f}x")

    # Variant C: Momentum of momentum
    print("  Running Variant C (Momentum of Momentum)...")
    eq_c, lev_c, desc_c = variant_c_mom_of_mom_leverage(base_daily_returns)
    metrics_c = compute_metrics(eq_c)
    all_results.append({
        'variant': 'C',
        'description': desc_c,
        'equity_curve': eq_c,
        'leverage_series': lev_c,
        'avg_leverage': lev_c.mean(),
        **metrics_c,
    })
    print(f"    C: 12M={format_pct(metrics_c['last_12m_return'])}, MaxDD={format_pct(metrics_c['max_dd'])}, Avg Lev={lev_c.mean():.2f}x")

    # Variant D: Combined
    print("  Running Variant D (Combined Regime + Drawdown)...")
    eq_d, lev_d, desc_d = variant_d_combined_leverage(base_daily_returns, btc_regime)
    metrics_d = compute_metrics(eq_d)
    all_results.append({
        'variant': 'D',
        'description': desc_d,
        'equity_curve': eq_d,
        'leverage_series': lev_d,
        'avg_leverage': lev_d.mean(),
        **metrics_d,
    })
    print(f"    D: 12M={format_pct(metrics_d['last_12m_return'])}, MaxDD={format_pct(metrics_d['max_dd'])}, Avg Lev={lev_d.mean():.2f}x")

    # Variant E: Static leverage (multiple levels)
    for static_lev in [1.0, 1.5, 2.0, 2.5, 3.0]:
        print(f"  Running Variant E (Static {static_lev:.1f}x)...")
        eq_e, lev_e, desc_e = variant_e_static_leverage(base_daily_returns, static_lev)
        metrics_e = compute_metrics(eq_e)
        all_results.append({
            'variant': f'E-{static_lev:.1f}x',
            'description': desc_e,
            'equity_curve': eq_e,
            'leverage_series': lev_e,
            'avg_leverage': lev_e.mean(),
            **metrics_e,
        })
        print(f"    E-{static_lev:.1f}x: 12M={format_pct(metrics_e['last_12m_return'])}, MaxDD={format_pct(metrics_e['max_dd'])}")

    # Generate report
    print("\nGenerating report...")
    report = generate_report(all_results, base_metrics, base_equity, len(tokens), token_list)

    with open(OUTPUT_PATH, 'w') as f:
        f.write(report)
    print(f"Report written to {OUTPUT_PATH}")

    # Print summary
    print("\n" + "=" * 70)
    print("QUICK SUMMARY")
    print("=" * 70)

    print(f"\nBase strategy (1x): 12M={format_pct(base_metrics['last_12m_return'])}, "
          f"MaxDD={format_pct(base_metrics['max_dd'])}, Sharpe={format_float(base_metrics['sharpe'])}")

    print("\nAll variants (sorted by 12M return):")
    for r in sorted(all_results, key=lambda x: x['last_12m_return'], reverse=True):
        target = "<<< TARGET MET" if r['last_12m_return'] >= 3.0 and r['max_dd'] > -0.70 else ""
        print(f"  {r['variant']:10s}: 12M={format_pct(r['last_12m_return']):>8s}, "
              f"MaxDD={format_pct(r['max_dd']):>8s}, "
              f"Sharpe={format_float(r['sharpe']):>6s}, "
              f"Calmar={format_float(r['calmar']):>6s}, "
              f"AvgLev={r['avg_leverage']:.2f}x "
              f"{target}")

    hits = [r for r in all_results if r['last_12m_return'] >= 3.0 and r['max_dd'] > -0.70]
    if hits:
        print(f"\n*** {len(hits)} VARIANT(S) MEET TARGET (300%+ 12M, MaxDD < 70%) ***")
        for r in hits:
            print(f"  >>> {r['variant']}: 12M={format_pct(r['last_12m_return'])}, MaxDD={format_pct(r['max_dd'])}")
    else:
        print("\n*** NO variants meet both targets simultaneously ***")
        close = sorted(all_results, key=lambda x: x['last_12m_return'], reverse=True)
        print(f"  Best 12M return: {close[0]['variant']} = {format_pct(close[0]['last_12m_return'])} (MaxDD={format_pct(close[0]['max_dd'])})")
        dd_ok = [r for r in all_results if r['max_dd'] > -0.70]
        if dd_ok:
            best_dd_ok = sorted(dd_ok, key=lambda x: x['last_12m_return'], reverse=True)[0]
            print(f"  Best 12M with DD<70%: {best_dd_ok['variant']} = {format_pct(best_dd_ok['last_12m_return'])} (MaxDD={format_pct(best_dd_ok['max_dd'])})")

    t_total = time.time() - t0
    print(f"\nTotal runtime: {t_total:.1f}s")


if __name__ == '__main__':
    main()
