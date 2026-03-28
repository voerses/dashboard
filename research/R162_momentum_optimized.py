"""
R162 -- Optimized Momentum Rotation Strategy

Building on R158 findings:
  - L=7 (shortest lookback) dominated for 12mo returns
  - N=7 (weekly rebalance) by far the best
  - K=5 sweet spot
  - 70/30 slightly better than 50/50 for returns
  - Regime filter is CRITICAL

This study pushes BEYOND the R158 parameter space:
  1. Aggressive allocations: 80/20, 90/10 (long-biased)
  2. Shorter lookbacks: L=3, L=5 (even faster momentum)
  3. K=3 concentrated with aggressive allocations
  4. Faster regime filter: EMA(10h)/EMA(30h) vs 20h/50h
  5. Combined filter: regime + volatility (ATR/price > median)

Parameter Sweep (36 combinations, all with N=7, regime filter ON):
  - L: [3, 5, 7]
  - K: [3, 5]
  - Allocation: [(0.70, 0.30), (0.80, 0.20), (0.90, 0.10)]
  - EMA regime: [(10, 30), (20, 50)]
  - Total: 3 * 2 * 3 * 2 = 36

Uses EXACT same data loading, simulation engine, and cost model as R158.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional
import warnings
import time
import os
from itertools import product

warnings.filterwarnings('ignore')

# ============================================================
# CONSTANTS
# ============================================================

DATA_DIR = '/workspace/crypto_backtest/data/perp/1h_cache'
OUTPUT_PATH = '/workspace/crypto_backtest/research/R162_momentum_optimized_results.md'

# Date constraints (same as R158)
DATA_CUTOFF = pd.Timestamp('2024-03-17')
SIM_START = pd.Timestamp('2024-03-17')
SIM_END = pd.Timestamp('2026-03-17')
LAST_12MO_START = pd.Timestamp('2025-03-17')

# Volume filter
MIN_AVG_DAILY_VOLUME_USD = 1_000_000  # $1M

# Cost
FEE_BPS = 7.0  # per side

# Lookback warmup: need max(L) * 24 hours of data before sim start
MAX_LOOKBACK_DAYS = 30

# Hours per year for annualisation
HOURS_PER_YEAR = 8760
DAYS_PER_YEAR = 365

# Parameter sweep -- OPTIMIZED beyond R158
LOOKBACK_DAYS = [3, 5, 7]
REBALANCE_DAYS = [7]  # N=7 is the clear winner
K_VALUES = [3, 5]
ALLOCATIONS = [(0.70, 0.30), (0.80, 0.20), (0.90, 0.10)]
EMA_REGIME_PARAMS = [(10, 30), (20, 50)]  # (fast_hours, slow_hours)
# Regime filter always ON (the clear winner from R158)


# ============================================================
# DATA LOADING (identical to R158)
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

        # Must have data before cutoff
        if df.index.min() >= DATA_CUTOFF:
            continue

        # Volume filter: avg daily dollar volume over trailing 30 days from SIM_START
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


def prepare_daily_data(tokens: Dict[str, pd.DataFrame], ema_params_list: List[Tuple[int, int]]):
    """
    Prepare aligned daily close prices, shifted signals, and hourly funding.

    Returns:
        daily_close: DataFrame of daily close prices (date x token)
        daily_returns: DataFrame of daily returns
        lookback_returns_shifted: Dict[int, DataFrame] keyed by lookback days
        ema_signals_shifted: Dict[(fast,slow), DataFrame] keyed by EMA param tuple
        rolling_avg_dvol_shifted: DataFrame of shifted 30d avg dollar volume
        hourly_fundings: Dict of per-token hourly funding Series
        hourly_closes: Dict of per-token hourly close Series (for ATR calc)
    """
    daily_closes = {}
    daily_volumes = {}
    ema_signals_by_params = {params: {} for params in ema_params_list}
    hourly_fundings = {}
    hourly_closes = {}
    hourly_highs = {}
    hourly_lows = {}

    for ticker, df in tokens.items():
        daily = df['close'].resample('1D').last().dropna()
        daily_closes[ticker] = daily

        dvol = (df['volume'] * df['close']).resample('1D').sum()
        daily_volumes[ticker] = dvol

        # Compute EMA signals for each param set
        for (fast_h, slow_h) in ema_params_list:
            ema_fast = df['close'].ewm(span=fast_h, adjust=False).mean()
            ema_slow = df['close'].ewm(span=slow_h, adjust=False).mean()
            ema_diff = (ema_fast - ema_slow).resample('1D').last()
            ema_signals_by_params[(fast_h, slow_h)][ticker] = ema_diff

        hourly_fundings[ticker] = df['funding_1h'].fillna(0)

        # For ATR calculation
        hourly_closes[ticker] = df['close']
        hourly_highs[ticker] = df['high']
        hourly_lows[ticker] = df['low']

    daily_close = pd.DataFrame(daily_closes)
    daily_volume = pd.DataFrame(daily_volumes)

    # Build EMA signal DataFrames for each param set
    ema_signals_shifted = {}
    for params in ema_params_list:
        ema_df = pd.DataFrame(ema_signals_by_params[params])
        start_with_buffer = SIM_START - pd.Timedelta(days=MAX_LOOKBACK_DAYS + 10)
        ema_df = ema_df.reindex(daily_close.loc[start_with_buffer:SIM_END].index)
        ema_signals_shifted[params] = ema_df.shift(1)

    # Trim to relevant date range (with warmup buffer)
    start_with_buffer = SIM_START - pd.Timedelta(days=MAX_LOOKBACK_DAYS + 10)
    daily_close = daily_close.loc[start_with_buffer:SIM_END]
    daily_volume = daily_volume.reindex(daily_close.index)

    # Daily returns
    daily_returns = daily_close.pct_change()

    # Pre-compute shifted lookback returns for each L value
    lookback_returns_shifted = {}
    for L in LOOKBACK_DAYS:
        lb = daily_close.shift(1) / daily_close.shift(1 + L) - 1
        lookback_returns_shifted[L] = lb

    # Shifted rolling avg dollar volume
    rolling_avg_dvol = daily_volume.rolling(30, min_periods=15).mean()
    rolling_avg_dvol_shifted = rolling_avg_dvol.shift(1)

    # Compute daily ATR/price ratio for volatility filter
    # ATR(14 days) / close price, computed from daily OHLC
    daily_highs = {}
    daily_lows_dict = {}
    for ticker, df in tokens.items():
        daily_highs[ticker] = df['high'].resample('1D').max().dropna()
        daily_lows_dict[ticker] = df['low'].resample('1D').min().dropna()

    df_high = pd.DataFrame(daily_highs).reindex(daily_close.index)
    df_low = pd.DataFrame(daily_lows_dict).reindex(daily_close.index)
    df_close_prev = daily_close.shift(1)

    # True Range = max(H-L, abs(H-Cprev), abs(L-Cprev))
    tr1 = df_high - df_low
    tr2 = (df_high - df_close_prev).abs()
    tr3 = (df_low - df_close_prev).abs()
    true_range = pd.concat([tr1, tr2, tr3]).groupby(level=0).max()
    # Ensure alignment
    true_range = true_range.reindex(daily_close.index)
    atr_14 = true_range.rolling(14, min_periods=7).mean()
    atr_ratio = atr_14 / daily_close  # ATR/price normalized volatility
    atr_ratio_shifted = atr_ratio.shift(1)

    return (daily_close, daily_returns, lookback_returns_shifted,
            ema_signals_shifted, rolling_avg_dvol_shifted, hourly_fundings,
            atr_ratio_shifted)


# ============================================================
# STRATEGY SIMULATION
# ============================================================

@dataclass
class StrategyResult:
    label: str
    lookback: int
    rebalance: int
    k: int
    long_wt: float
    short_wt: float
    ema_fast: int
    ema_slow: int
    vol_filter: bool
    equity_curve: pd.Series = field(default_factory=pd.Series)
    annual_return: float = 0.0
    max_dd: float = 0.0
    sharpe: float = 0.0
    calmar: float = 0.0
    sortino: float = 0.0
    profit_factor: float = 0.0
    last_12m_return: float = 0.0
    trade_count: int = 0
    win_rate: float = 0.0
    total_cost: float = 0.0
    total_funding: float = 0.0


def compute_metrics(equity: pd.Series) -> dict:
    """Compute standard strategy metrics from an equity curve."""
    if len(equity) < 2 or equity.iloc[-1] <= 0:
        return {
            'annual_return': -1.0, 'max_dd': -1.0, 'sharpe': -99.0,
            'calmar': -99.0, 'sortino': -99.0, 'profit_factor': 0.0,
            'last_12m_return': -1.0,
        }

    daily_eq = equity.resample('1D').last().dropna()
    daily_rets = daily_eq.pct_change().dropna()

    if len(daily_rets) < 30:
        return {
            'annual_return': -1.0, 'max_dd': -1.0, 'sharpe': -99.0,
            'calmar': -99.0, 'sortino': -99.0, 'profit_factor': 0.0,
            'last_12m_return': -1.0,
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

    downside = daily_rets[daily_rets < 0]
    downside_std = downside.std() if len(downside) > 0 else 1e-10
    sortino = (mean_daily / downside_std * np.sqrt(DAYS_PER_YEAR)) if downside_std > 0 else 0.0

    gains = daily_rets[daily_rets > 0].sum()
    losses = abs(daily_rets[daily_rets < 0].sum())
    profit_factor = gains / losses if losses > 0 else float('inf')

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
        'sortino': sortino,
        'profit_factor': profit_factor,
        'last_12m_return': last_12m_return,
    }


def run_momentum_rotation(
    daily_close: pd.DataFrame,
    daily_returns: pd.DataFrame,
    lookback_returns_shifted: Dict[int, pd.DataFrame],
    ema_signal_shifted: pd.DataFrame,
    rolling_avg_dvol_shifted: pd.DataFrame,
    hourly_fundings: Dict[str, pd.Series],
    atr_ratio_shifted: pd.DataFrame,
    lookback_days: int,
    rebalance_days: int,
    k: int,
    long_wt: float,
    short_wt: float,
    use_vol_filter: bool = False,
) -> StrategyResult:
    """
    Run a single momentum rotation backtest.
    Identical simulation logic to R158.
    """
    ema_fast_h = 0  # will be set from label
    ema_slow_h = 0

    label = (f"L{lookback_days}_R{rebalance_days}_K{k}_"
             f"{int(long_wt*100)}/{int(short_wt*100)}_"
             f"{'VF' if use_vol_filter else 'RF'}")

    sim_dates = daily_close.loc[SIM_START:SIM_END].index
    if len(sim_dates) < rebalance_days + lookback_days:
        return StrategyResult(label=label, lookback=lookback_days, rebalance=rebalance_days,
                              k=k, long_wt=long_wt, short_wt=short_wt,
                              ema_fast=0, ema_slow=0, vol_filter=use_vol_filter)

    lb_rets_df = lookback_returns_shifted[lookback_days]
    rebalance_indices = set(range(0, len(sim_dates), rebalance_days))

    # Track state
    equity = 1.0
    equity_series = {}
    current_longs = {}
    current_shorts = {}
    prev_longs = set()
    prev_shorts = set()

    trade_count = 0
    daily_pnl_list = []
    total_cost = 0.0
    total_funding = 0.0

    pending_longs = None
    pending_shorts = None

    for i, date in enumerate(sim_dates):
        # ---- Apply pending rebalance from yesterday ----
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

            total_cost += cost * equity
            equity *= (1 - cost)

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
                    f_pnl = -f_slice.sum() * weight
                    daily_pnl += f_pnl
                    total_funding += f_pnl * equity

        for ticker, weight in current_shorts.items():
            if ticker in hourly_fundings:
                f_slice = hourly_fundings[ticker].loc[funding_start:funding_end]
                if len(f_slice) > 0:
                    f_pnl = f_slice.sum() * weight
                    daily_pnl += f_pnl
                    total_funding += f_pnl * equity

        if len(current_longs) > 0 or len(current_shorts) > 0:
            daily_pnl_list.append(daily_pnl)

        equity *= (1 + daily_pnl)
        equity_series[date] = equity

        # ---- Decide rebalance ----
        if i in rebalance_indices:
            lb_rets = lb_rets_df.loc[date].dropna() if date in lb_rets_df.index else pd.Series(dtype=float)

            vol_at_date = rolling_avg_dvol_shifted.loc[date].dropna() if date in rolling_avg_dvol_shifted.index else pd.Series(dtype=float)
            eligible = vol_at_date[vol_at_date >= MIN_AVG_DAILY_VOLUME_USD].index
            lb_rets = lb_rets[lb_rets.index.isin(eligible)]

            # Volatility filter: only trade tokens with ATR/price > median
            if use_vol_filter and date in atr_ratio_shifted.index:
                atr_at_date = atr_ratio_shifted.loc[date].dropna()
                atr_eligible = atr_at_date[atr_at_date.index.isin(lb_rets.index)]
                if len(atr_eligible) > 0:
                    median_atr = atr_eligible.median()
                    high_vol_tokens = atr_eligible[atr_eligible > median_atr].index
                    lb_rets = lb_rets[lb_rets.index.isin(high_vol_tokens)]

            if len(lb_rets) >= 2 * k:
                ranked = lb_rets.sort_values(ascending=False)
                top_k = ranked.head(k).index.tolist()
                bottom_k = ranked.tail(k).index.tolist()

                # Regime filter (always ON)
                ema_at_date = ema_signal_shifted.loc[date].dropna() if date in ema_signal_shifted.index else pd.Series(dtype=float)
                top_k = [t for t in top_k if t in ema_at_date.index and ema_at_date[t] > 0]
                bottom_k = [t for t in bottom_k if t in ema_at_date.index and ema_at_date[t] < 0]

                n_longs = len(top_k) if len(top_k) > 0 else 1
                n_shorts = len(bottom_k) if len(bottom_k) > 0 else 1

                pending_longs = {t: long_wt / n_longs for t in top_k} if top_k else {}
                pending_shorts = {t: short_wt / n_shorts for t in bottom_k} if bottom_k else {}

    # Build equity curve
    eq_series = pd.Series(equity_series)

    win_rate = 0.0
    if len(daily_pnl_list) > 0:
        win_rate = sum(1 for p in daily_pnl_list if p > 0) / len(daily_pnl_list)

    metrics = compute_metrics(eq_series)

    return StrategyResult(
        label=label,
        lookback=lookback_days,
        rebalance=rebalance_days,
        k=k,
        long_wt=long_wt,
        short_wt=short_wt,
        ema_fast=0,
        ema_slow=0,
        vol_filter=use_vol_filter,
        equity_curve=eq_series,
        annual_return=metrics['annual_return'],
        max_dd=metrics['max_dd'],
        sharpe=metrics['sharpe'],
        calmar=metrics['calmar'],
        sortino=metrics['sortino'],
        profit_factor=metrics['profit_factor'],
        last_12m_return=metrics['last_12m_return'],
        trade_count=trade_count,
        win_rate=win_rate,
        total_cost=total_cost,
        total_funding=total_funding,
    )


# ============================================================
# PARAMETER SWEEP
# ============================================================

def run_full_sweep(
    daily_close, daily_returns, lookback_returns_shifted,
    ema_signals_shifted, rolling_avg_dvol_shifted, hourly_fundings,
    atr_ratio_shifted,
) -> List[StrategyResult]:
    """Run all parameter combinations across EMA regimes and vol filter."""
    results = []

    # Main sweep: 36 combos = L(3) * K(2) * Alloc(3) * EMA(2)
    combos = list(product(LOOKBACK_DAYS, K_VALUES, ALLOCATIONS, EMA_REGIME_PARAMS))
    print(f"Running {len(combos)} base parameter combinations (regime filter always ON)...")

    for i, (L, K, (lw, sw), (ema_f, ema_s)) in enumerate(combos):
        if (i + 1) % 10 == 0:
            print(f"  Progress: {i+1}/{len(combos)}")

        ema_key = (ema_f, ema_s)
        ema_signal = ema_signals_shifted[ema_key]

        result = run_momentum_rotation(
            daily_close=daily_close,
            daily_returns=daily_returns,
            lookback_returns_shifted=lookback_returns_shifted,
            ema_signal_shifted=ema_signal,
            rolling_avg_dvol_shifted=rolling_avg_dvol_shifted,
            hourly_fundings=hourly_fundings,
            atr_ratio_shifted=atr_ratio_shifted,
            lookback_days=L,
            rebalance_days=7,
            k=K,
            long_wt=lw,
            short_wt=sw,
            use_vol_filter=False,
        )
        # Fix label and EMA params
        result.ema_fast = ema_f
        result.ema_slow = ema_s
        result.label = (f"L{L}_R7_K{K}_{int(lw*100)}/{int(sw*100)}_"
                       f"EMA{ema_f}/{ema_s}")
        results.append(result)

    # Bonus: test combined regime + volatility filter on best EMA params
    # Run all combos with vol filter ON (another 36)
    print(f"\nRunning {len(combos)} combos with COMBINED regime + volatility filter...")
    for i, (L, K, (lw, sw), (ema_f, ema_s)) in enumerate(combos):
        if (i + 1) % 10 == 0:
            print(f"  Progress: {i+1}/{len(combos)}")

        ema_key = (ema_f, ema_s)
        ema_signal = ema_signals_shifted[ema_key]

        result = run_momentum_rotation(
            daily_close=daily_close,
            daily_returns=daily_returns,
            lookback_returns_shifted=lookback_returns_shifted,
            ema_signal_shifted=ema_signal,
            rolling_avg_dvol_shifted=rolling_avg_dvol_shifted,
            hourly_fundings=hourly_fundings,
            atr_ratio_shifted=atr_ratio_shifted,
            lookback_days=L,
            rebalance_days=7,
            k=K,
            long_wt=lw,
            short_wt=sw,
            use_vol_filter=True,
        )
        result.ema_fast = ema_f
        result.ema_slow = ema_s
        result.vol_filter = True
        result.label = (f"L{L}_R7_K{K}_{int(lw*100)}/{int(sw*100)}_"
                       f"EMA{ema_f}/{ema_s}_VF")
        results.append(result)

    return results


# ============================================================
# REPORTING
# ============================================================

def format_pct(v: float) -> str:
    return f"{v*100:.1f}%"


def format_float(v: float, decimals: int = 2) -> str:
    return f"{v:.{decimals}f}"


def monthly_returns_table(equity: pd.Series) -> str:
    """Generate a monthly returns table as markdown."""
    daily_eq = equity.resample('1D').last().dropna()
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


def generate_report(results: List[StrategyResult], n_tokens: int, token_list: List[str]) -> str:
    """Generate the full results markdown report."""
    lines = []
    lines.append("# R162 -- Optimized Momentum Rotation Results\n")
    lines.append(f"**Generated:** {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Simulation Period:** {SIM_START.date()} to {SIM_END.date()} (~2 years)")
    lines.append(f"**Last 12 Months:** {LAST_12MO_START.date()} to {SIM_END.date()}")
    lines.append(f"**Token Universe:** {n_tokens} tokens passing volume filter (>${MIN_AVG_DAILY_VOLUME_USD/1e6:.0f}M avg daily volume)")
    lines.append(f"**Tokens:** {', '.join(sorted(token_list))}")
    lines.append(f"**Total Combinations:** {len(results)}")
    lines.append(f"**Costs:** {FEE_BPS:.0f} bps per side + hourly funding from parquet")
    lines.append(f"**Look-ahead protection:** All signals shifted by 1 day; new positions applied next day\n")

    lines.append("## Parameter Space (Optimized beyond R158)\n")
    lines.append(f"- Lookback (L): {LOOKBACK_DAYS} days (added L=3, L=5)")
    lines.append(f"- Rebalance (N): {REBALANCE_DAYS} days (fixed at N=7, the clear R158 winner)")
    lines.append(f"- K per leg: {K_VALUES}")
    lines.append(f"- Allocation (Long/Short): 70/30, 80/20, 90/10 (aggressive long-bias)")
    lines.append(f"- EMA Regime: {EMA_REGIME_PARAMS} hours (fast/slow)")
    lines.append(f"- Regime Filter: Always ON (R158 showed critical importance)")
    lines.append(f"- Volatility Filter: ATR(14d)/price > median (tested as add-on)")
    lines.append(f"- Base combos: 36 | With vol filter: 36 | Total: 72")
    lines.append("")

    valid = [r for r in results if r.sharpe > -50]
    if not valid:
        lines.append("**No valid results.**\n")
        return "\n".join(lines)

    # ---- R158 BASELINE COMPARISON ----
    lines.append("---\n")
    lines.append("## R158 Baseline Comparison\n")
    lines.append("Best R158 result: L7_R7_K5_70/30 with regime filter:")
    lines.append("- Last 12M Return: +163%")
    lines.append("- Sharpe: 1.27")
    lines.append("- MaxDD: -55%")
    lines.append("")

    # ---- TOP 15 by LAST 12 MONTH RETURN ----
    lines.append("---\n")
    lines.append("## TOP 15 by Last 12-Month Return\n")

    by_12m = sorted(valid, key=lambda r: r.last_12m_return, reverse=True)[:15]

    lines.append("| Rank | Label | L | K | Alloc | EMA | VF | Ann Ret | MaxDD | Sharpe | Calmar | Sortino | PF | 12M Ret | Trades | WR |")
    lines.append("|------|-------|---|---|-------|-----|----|---------|-------|--------|--------|---------|-----|---------|--------|-----|")

    for rank, r in enumerate(by_12m, 1):
        alloc = f"{int(r.long_wt*100)}/{int(r.short_wt*100)}"
        ema = f"{r.ema_fast}/{r.ema_slow}"
        vf = "Y" if r.vol_filter else "N"
        lines.append(
            f"| {rank} | {r.label} | {r.lookback} | {r.k} | {alloc} | {ema} | {vf} "
            f"| {format_pct(r.annual_return)} | {format_pct(r.max_dd)} | {format_float(r.sharpe)} "
            f"| {format_float(r.calmar)} | {format_float(r.sortino)} | {format_float(r.profit_factor)} "
            f"| **{format_pct(r.last_12m_return)}** | {r.trade_count} | {format_pct(r.win_rate)} |"
        )

    # ---- TOP 15 by SHARPE ----
    lines.append("\n---\n")
    lines.append("## TOP 15 by Full-Period Sharpe Ratio\n")

    by_sharpe = sorted(valid, key=lambda r: r.sharpe, reverse=True)[:15]

    lines.append("| Rank | Label | L | K | Alloc | EMA | VF | Ann Ret | MaxDD | Sharpe | Calmar | Sortino | PF | 12M Ret | Trades | WR |")
    lines.append("|------|-------|---|---|-------|-----|----|---------|-------|--------|--------|---------|-----|---------|--------|-----|")

    for rank, r in enumerate(by_sharpe, 1):
        alloc = f"{int(r.long_wt*100)}/{int(r.short_wt*100)}"
        ema = f"{r.ema_fast}/{r.ema_slow}"
        vf = "Y" if r.vol_filter else "N"
        lines.append(
            f"| {rank} | {r.label} | {r.lookback} | {r.k} | {alloc} | {ema} | {vf} "
            f"| {format_pct(r.annual_return)} | {format_pct(r.max_dd)} | **{format_float(r.sharpe)}** "
            f"| {format_float(r.calmar)} | {format_float(r.sortino)} | {format_float(r.profit_factor)} "
            f"| {format_pct(r.last_12m_return)} | {r.trade_count} | {format_pct(r.win_rate)} |"
        )

    # ---- BEST RISK-ADJUSTED (Sharpe > 1.0 AND 12M > 150%) ----
    lines.append("\n---\n")
    lines.append("## Best Risk-Adjusted: Sharpe > 1.0 AND 12M Return > 150%\n")

    elite = [r for r in valid if r.sharpe > 1.0 and r.last_12m_return > 1.5]
    elite_sorted = sorted(elite, key=lambda r: r.last_12m_return, reverse=True)

    if elite_sorted:
        lines.append("| Rank | Label | L | K | Alloc | EMA | VF | Ann Ret | MaxDD | Sharpe | Sortino | 12M Ret |")
        lines.append("|------|-------|---|---|-------|-----|----|---------|-------|--------|---------|---------|")
        for rank, r in enumerate(elite_sorted, 1):
            alloc = f"{int(r.long_wt*100)}/{int(r.short_wt*100)}"
            ema = f"{r.ema_fast}/{r.ema_slow}"
            vf = "Y" if r.vol_filter else "N"
            lines.append(
                f"| {rank} | {r.label} | {r.lookback} | {r.k} | {alloc} | {ema} | {vf} "
                f"| {format_pct(r.annual_return)} | {format_pct(r.max_dd)} | {format_float(r.sharpe)} "
                f"| {format_float(r.sortino)} | **{format_pct(r.last_12m_return)}** |"
            )
    else:
        lines.append("*No combinations met both criteria.*\n")

    # ---- SUMMARY STATS ----
    lines.append("\n---\n")
    lines.append("## Summary Statistics Across All Combinations\n")

    sharpes = [r.sharpe for r in valid]
    ann_rets = [r.annual_return for r in valid]
    l12m_rets = [r.last_12m_return for r in valid]
    max_dds = [r.max_dd for r in valid]

    lines.append("| Metric | Min | Median | Mean | Max |")
    lines.append("|--------|-----|--------|------|-----|")
    lines.append(f"| Sharpe | {min(sharpes):.2f} | {np.median(sharpes):.2f} | {np.mean(sharpes):.2f} | {max(sharpes):.2f} |")
    lines.append(f"| Ann Return | {format_pct(min(ann_rets))} | {format_pct(np.median(ann_rets))} | {format_pct(np.mean(ann_rets))} | {format_pct(max(ann_rets))} |")
    lines.append(f"| Last 12M | {format_pct(min(l12m_rets))} | {format_pct(np.median(l12m_rets))} | {format_pct(np.mean(l12m_rets))} | {format_pct(max(l12m_rets))} |")
    lines.append(f"| MaxDD | {format_pct(min(max_dds))} | {format_pct(np.median(max_dds))} | {format_pct(np.mean(max_dds))} | {format_pct(max(max_dds))} |")

    # ---- FACTOR ANALYSIS ----
    lines.append("\n---\n")
    lines.append("## Factor Analysis: Average Metrics by Parameter\n")

    lines.append("### By Lookback (L)\n")
    lines.append("| L | Avg Sharpe | Avg Ann Ret | Avg 12M Ret | Avg MaxDD | Count |")
    lines.append("|---|-----------|-------------|-------------|-----------|-------|")
    for L in LOOKBACK_DAYS:
        subset = [r for r in valid if r.lookback == L]
        if subset:
            lines.append(f"| {L} | {np.mean([r.sharpe for r in subset]):.2f} "
                         f"| {format_pct(np.mean([r.annual_return for r in subset]))} "
                         f"| {format_pct(np.mean([r.last_12m_return for r in subset]))} "
                         f"| {format_pct(np.mean([r.max_dd for r in subset]))} "
                         f"| {len(subset)} |")

    lines.append("\n### By K (tokens per leg)\n")
    lines.append("| K | Avg Sharpe | Avg Ann Ret | Avg 12M Ret | Avg MaxDD | Count |")
    lines.append("|---|-----------|-------------|-------------|-----------|-------|")
    for K in K_VALUES:
        subset = [r for r in valid if r.k == K]
        if subset:
            lines.append(f"| {K} | {np.mean([r.sharpe for r in subset]):.2f} "
                         f"| {format_pct(np.mean([r.annual_return for r in subset]))} "
                         f"| {format_pct(np.mean([r.last_12m_return for r in subset]))} "
                         f"| {format_pct(np.mean([r.max_dd for r in subset]))} "
                         f"| {len(subset)} |")

    lines.append("\n### By Allocation\n")
    lines.append("| Allocation | Avg Sharpe | Avg Ann Ret | Avg 12M Ret | Avg MaxDD | Count |")
    lines.append("|-----------|-----------|-------------|-------------|-----------|-------|")
    for lw, sw in ALLOCATIONS:
        alloc = f"{int(lw*100)}/{int(sw*100)}"
        subset = [r for r in valid if r.long_wt == lw and r.short_wt == sw]
        if subset:
            lines.append(f"| {alloc} | {np.mean([r.sharpe for r in subset]):.2f} "
                         f"| {format_pct(np.mean([r.annual_return for r in subset]))} "
                         f"| {format_pct(np.mean([r.last_12m_return for r in subset]))} "
                         f"| {format_pct(np.mean([r.max_dd for r in subset]))} "
                         f"| {len(subset)} |")

    lines.append("\n### By EMA Regime Speed\n")
    lines.append("| EMA (fast/slow) | Avg Sharpe | Avg Ann Ret | Avg 12M Ret | Avg MaxDD | Count |")
    lines.append("|----------------|-----------|-------------|-------------|-----------|-------|")
    for (ef, es) in EMA_REGIME_PARAMS:
        subset = [r for r in valid if r.ema_fast == ef and r.ema_slow == es]
        if subset:
            lines.append(f"| {ef}/{es} | {np.mean([r.sharpe for r in subset]):.2f} "
                         f"| {format_pct(np.mean([r.annual_return for r in subset]))} "
                         f"| {format_pct(np.mean([r.last_12m_return for r in subset]))} "
                         f"| {format_pct(np.mean([r.max_dd for r in subset]))} "
                         f"| {len(subset)} |")

    lines.append("\n### By Volatility Filter\n")
    lines.append("| Vol Filter | Avg Sharpe | Avg Ann Ret | Avg 12M Ret | Avg MaxDD | Count |")
    lines.append("|-----------|-----------|-------------|-------------|-----------|-------|")
    for vf in [False, True]:
        subset = [r for r in valid if r.vol_filter == vf]
        if subset:
            lines.append(f"| {'Yes' if vf else 'No'} | {np.mean([r.sharpe for r in subset]):.2f} "
                         f"| {format_pct(np.mean([r.annual_return for r in subset]))} "
                         f"| {format_pct(np.mean([r.last_12m_return for r in subset]))} "
                         f"| {format_pct(np.mean([r.max_dd for r in subset]))} "
                         f"| {len(subset)} |")

    # ---- COST ANALYSIS ----
    lines.append("\n---\n")
    lines.append("## Cost Analysis (Best 12M Return Variant)\n")
    best_12m_r = by_12m[0]
    lines.append(f"- **Variant:** {best_12m_r.label}")
    lines.append(f"- **Total Trading Cost (as % of initial equity):** {best_12m_r.total_cost*100:.2f}%")
    lines.append(f"- **Total Funding Impact (as % of initial equity):** {best_12m_r.total_funding*100:.2f}%")
    lines.append(f"- **Trade Count:** {best_12m_r.trade_count}")

    # ---- MONTHLY RETURNS: BEST 12M ----
    lines.append("\n---\n")
    lines.append("## Monthly Returns: Best by Last-12M Return\n")
    lines.append(f"**Variant:** {best_12m_r.label}\n")
    if len(best_12m_r.equity_curve) > 0:
        lines.append(monthly_returns_table(best_12m_r.equity_curve))

    # ---- MONTHLY RETURNS: BEST SHARPE ----
    lines.append("\n---\n")
    lines.append("## Monthly Returns: Best by Sharpe\n")
    best_sharpe_r = by_sharpe[0]
    lines.append(f"**Variant:** {best_sharpe_r.label}\n")
    if len(best_sharpe_r.equity_curve) > 0:
        lines.append(monthly_returns_table(best_sharpe_r.equity_curve))

    # ---- LEVERAGE PROJECTION ----
    lines.append("\n---\n")
    lines.append("## Leverage Projection (Top 5 by 12M Return)\n")
    lines.append("*Projected 12M returns at various leverage levels (assuming linear scaling, no liquidation)*\n")
    lines.append("| Variant | 1x | 1.5x | 2x | 2.5x | 3x |")
    lines.append("|---------|----|----|----|----|-----|")
    for r in by_12m[:5]:
        ret_1x = r.last_12m_return
        lines.append(
            f"| {r.label} "
            f"| {format_pct(ret_1x)} "
            f"| {format_pct(ret_1x * 1.5)} "
            f"| {format_pct(ret_1x * 2.0)} "
            f"| {format_pct(ret_1x * 2.5)} "
            f"| {format_pct(ret_1x * 3.0)} |"
        )
    lines.append("\n*Note: Leverage also multiplies MaxDD proportionally. A -55% DD at 2x = -110% (liquidation).*")
    lines.append("*Realistic leverage ceiling is ~1.5-2x given observed drawdowns.*")

    # ---- ALL RESULTS TABLE ----
    lines.append("\n---\n")
    lines.append(f"## All {len(valid)} Combinations (sorted by 12M Return)\n")

    all_sorted = sorted(valid, key=lambda r: r.last_12m_return, reverse=True)

    lines.append("| # | Label | L | K | Alloc | EMA | VF | Ann Ret | MaxDD | Sharpe | Sortino | PF | 12M Ret | Trades | WR |")
    lines.append("|---|-------|---|---|-------|-----|----|---------|-------|--------|---------|-----|---------|--------|-----|")

    for rank, r in enumerate(all_sorted, 1):
        alloc = f"{int(r.long_wt*100)}/{int(r.short_wt*100)}"
        ema = f"{r.ema_fast}/{r.ema_slow}"
        vf = "Y" if r.vol_filter else "N"
        lines.append(
            f"| {rank} | {r.label} | {r.lookback} | {r.k} | {alloc} | {ema} | {vf} "
            f"| {format_pct(r.annual_return)} | {format_pct(r.max_dd)} | {format_float(r.sharpe)} "
            f"| {format_float(r.sortino)} | {format_float(r.profit_factor)} "
            f"| {format_pct(r.last_12m_return)} | {r.trade_count} | {format_pct(r.win_rate)} |"
        )

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():
    t0 = time.time()

    print("=" * 70)
    print("R162 -- Optimized Momentum Rotation Strategy")
    print("Building on R158 findings: aggressive allocations, shorter lookbacks,")
    print("faster regime filter, combined volatility filter")
    print("=" * 70)

    # Load data
    print("\n[1/3] Loading token data...")
    tokens = load_all_tokens()
    token_list = sorted(tokens.keys())
    print(f"  Loaded {len(tokens)} tokens passing volume filter")
    print(f"  Tokens: {', '.join(token_list)}")

    # Prepare daily data
    print("\n[2/3] Preparing daily data panels...")
    (daily_close, daily_returns, lookback_returns_shifted,
     ema_signals_shifted, rolling_avg_dvol_shifted, hourly_fundings,
     atr_ratio_shifted) = prepare_daily_data(tokens, EMA_REGIME_PARAMS)
    print(f"  Daily close shape: {daily_close.shape}")
    print(f"  Date range: {daily_close.index.min().date()} to {daily_close.index.max().date()}")

    # Sanity check
    btc_sim = daily_close['BTC'].loc[SIM_START:SIM_END].dropna()
    if len(btc_sim) > 0:
        btc_ret = btc_sim.iloc[-1] / btc_sim.iloc[0] - 1
        print(f"  BTC total return over sim period: {btc_ret*100:.1f}%")

    # Run parameter sweep
    print("\n[3/3] Running parameter sweep...")
    t1 = time.time()
    results = run_full_sweep(
        daily_close, daily_returns, lookback_returns_shifted,
        ema_signals_shifted, rolling_avg_dvol_shifted, hourly_fundings,
        atr_ratio_shifted,
    )
    t2 = time.time()
    print(f"  Sweep completed in {t2-t1:.1f}s")

    # Generate report
    print("\nGenerating report...")
    report = generate_report(results, len(tokens), token_list)

    with open(OUTPUT_PATH, 'w') as f:
        f.write(report)
    print(f"Report written to {OUTPUT_PATH}")

    # Print summary to stdout
    valid = [r for r in results if r.sharpe > -50]
    print("\n" + "=" * 70)
    print("QUICK SUMMARY")
    print("=" * 70)

    by_12m = sorted(valid, key=lambda r: r.last_12m_return, reverse=True)[:10]
    print("\nTop 10 by Last 12M Return:")
    for r in by_12m:
        alloc = f"{int(r.long_wt*100)}/{int(r.short_wt*100)}"
        ema = f"EMA{r.ema_fast}/{r.ema_slow}"
        vf = "+VF" if r.vol_filter else ""
        print(f"  L{r.lookback}_R7_K{r.k}_{alloc}_{ema}{vf}: "
              f"12M={r.last_12m_return*100:+.1f}%, Ann={r.annual_return*100:+.1f}%, "
              f"Sharpe={r.sharpe:.2f}, MaxDD={r.max_dd*100:.1f}%")

    by_sharpe = sorted(valid, key=lambda r: r.sharpe, reverse=True)[:10]
    print("\nTop 10 by Sharpe:")
    for r in by_sharpe:
        alloc = f"{int(r.long_wt*100)}/{int(r.short_wt*100)}"
        ema = f"EMA{r.ema_fast}/{r.ema_slow}"
        vf = "+VF" if r.vol_filter else ""
        print(f"  L{r.lookback}_R7_K{r.k}_{alloc}_{ema}{vf}: "
              f"Sharpe={r.sharpe:.2f}, Ann={r.annual_return*100:+.1f}%, "
              f"12M={r.last_12m_return*100:+.1f}%, MaxDD={r.max_dd*100:.1f}%")

    # Key comparison vs R158 baseline
    print("\n" + "-" * 70)
    print("R158 BASELINE: L7_R7_K5_70/30_RF: 12M=+163%, Sharpe=1.27, MaxDD=-55%")
    best = by_12m[0]
    improvement = (best.last_12m_return - 1.63) / 1.63 * 100
    print(f"R162 BEST 12M: {best.label}: 12M={best.last_12m_return*100:+.1f}%, "
          f"Sharpe={best.sharpe:.2f}, MaxDD={best.max_dd*100:.1f}%")
    print(f"Improvement: {improvement:+.1f}% over R158 baseline")
    print("-" * 70)

    t_total = time.time() - t0
    print(f"\nTotal runtime: {t_total:.1f}s")


if __name__ == '__main__':
    main()
