"""
R158 -- Cross-Sectional Momentum Rotation Strategy

Dynamically selects which tokens to long and short based on recent performance
across the entire crypto perpetual futures universe.

Signal: Cross-Sectional Momentum
  At each rebalance (every N days, at close of day T):
  1. Compute each token's return over the past L days using close[T-1] / close[T-1-L]
     (shifted by 1 day to avoid look-ahead bias)
  2. Rank all tokens by this return
  3. Long the top K tokens (strongest upward momentum)
  4. Short the bottom K tokens (strongest downward momentum)
  5. Equal weight within each leg
  6. New positions take effect on day T+1 (first return applied is close[T+1]/close[T])

Per-Token Regime Filter (optional):
  - Only LONG tokens where their OWN EMA(20h) > EMA(50h) as of yesterday
  - Only SHORT tokens where their OWN EMA(20h) < EMA(50h) as of yesterday

Position Sizing:
  - Market neutral (50/50) or long-biased (70/30)

Costs:
  - 7 bps per side per trade (applied per-token per-weight)
  - Funding from parquet (funding_1h column)

Parameter Sweep (144 combinations):
  - Lookback L: 7, 14, 21, 30 days
  - Rebalance N: 3, 7, 14 days
  - K: 3, 5, 10 tokens per leg
  - Allocation: 50/50, 70/30
  - With and without per-token regime filter
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
OUTPUT_PATH = '/workspace/crypto_backtest/research/R158_momentum_rotation_results.md'

# Date constraints
DATA_CUTOFF = pd.Timestamp('2024-03-17')   # tokens must have data before this
SIM_START = pd.Timestamp('2024-03-17')      # simulation starts here
SIM_END = pd.Timestamp('2026-03-17')        # simulation ends here
LAST_12MO_START = pd.Timestamp('2025-03-17')

# Volume filter
MIN_AVG_DAILY_VOLUME_USD = 1_000_000  # $1M

# Cost
FEE_BPS = 7.0  # per side

# EMA regime filter params (hourly)
EMA_FAST_H = 20
EMA_SLOW_H = 50

# Lookback warmup: need max(L) * 24 hours of data before sim start
MAX_LOOKBACK_DAYS = 30

# Hours per year for annualisation
HOURS_PER_YEAR = 8760
DAYS_PER_YEAR = 365

# Parameter sweep
LOOKBACK_DAYS = [7, 14, 21, 30]
REBALANCE_DAYS = [3, 7, 14]
K_VALUES = [3, 5, 10]
ALLOCATIONS = [(0.50, 0.50), (0.70, 0.30)]  # (long_weight, short_weight)
REGIME_FILTER = [False, True]


# ============================================================
# DATA LOADING
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


def prepare_daily_data(tokens: Dict[str, pd.DataFrame]):
    """
    Prepare aligned daily close prices, shifted signals, and hourly funding.

    All signals are SHIFTED by 1 day to avoid look-ahead bias:
    - lookback_returns[T] uses close[T-1] / close[T-1-L] - 1
    - ema_signal[T] uses EMA values as of day T-1

    Returns:
        daily_close: DataFrame of daily close prices (date x token)
        daily_returns: DataFrame of daily returns (close[T]/close[T-1] - 1)
        lookback_returns_shifted: Dict[int, DataFrame] keyed by lookback days
        ema_signal_shifted: DataFrame of shifted (EMA20-EMA50) signals
        rolling_avg_dvol_shifted: DataFrame of shifted 30d avg dollar volume
        hourly_fundings: Dict of per-token hourly funding Series
    """
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

    # Trim to relevant date range (with warmup buffer)
    start_with_buffer = SIM_START - pd.Timedelta(days=MAX_LOOKBACK_DAYS + 10)
    daily_close = daily_close.loc[start_with_buffer:SIM_END]
    daily_volume = daily_volume.reindex(daily_close.index)
    ema_signal = ema_signal.reindex(daily_close.index)

    # Daily returns: close[T] / close[T-1] - 1
    daily_returns = daily_close.pct_change()

    # Pre-compute shifted lookback returns for each L value
    # lookback_returns_shifted[L] at date T = close[T-1] / close[T-1-L] - 1
    # This is close.shift(1) / close.shift(1+L) - 1
    lookback_returns_shifted = {}
    for L in LOOKBACK_DAYS:
        lb = daily_close.shift(1) / daily_close.shift(1 + L) - 1
        lookback_returns_shifted[L] = lb

    # Shifted EMA signal: value at T is from day T-1
    ema_signal_shifted = ema_signal.shift(1)

    # Shifted rolling avg dollar volume
    rolling_avg_dvol = daily_volume.rolling(30, min_periods=15).mean()
    rolling_avg_dvol_shifted = rolling_avg_dvol.shift(1)

    return (daily_close, daily_returns, lookback_returns_shifted,
            ema_signal_shifted, rolling_avg_dvol_shifted, hourly_fundings)


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
    regime_filter: bool
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
    lookback_days: int,
    rebalance_days: int,
    k: int,
    long_wt: float,
    short_wt: float,
    use_regime_filter: bool,
) -> StrategyResult:
    """
    Run a single momentum rotation backtest.

    Position changes are decided at the close of rebalance day T using data
    available up to T-1 (shifted signals). New positions take effect on day T+1.
    """
    label = (f"L{lookback_days}_R{rebalance_days}_K{k}_"
             f"{int(long_wt*100)}/{int(short_wt*100)}_"
             f"RF{'Y' if use_regime_filter else 'N'}")

    sim_dates = daily_close.loc[SIM_START:SIM_END].index
    if len(sim_dates) < rebalance_days + lookback_days:
        return StrategyResult(label=label, lookback=lookback_days, rebalance=rebalance_days,
                              k=k, long_wt=long_wt, short_wt=short_wt, regime_filter=use_regime_filter)

    lb_rets_df = lookback_returns_shifted[lookback_days]
    rebalance_indices = set(range(0, len(sim_dates), rebalance_days))

    # Track state
    equity = 1.0
    equity_series = {}
    current_longs = {}   # ticker -> weight (fraction of equity)
    current_shorts = {}  # ticker -> weight (fraction of equity)
    prev_longs = set()
    prev_shorts = set()

    trade_count = 0
    daily_pnl_list = []  # for win rate
    total_cost = 0.0
    total_funding = 0.0

    # Pending: new positions decided today, applied starting tomorrow
    pending_longs = None
    pending_shorts = None

    for i, date in enumerate(sim_dates):
        # ---- Apply pending rebalance from yesterday ----
        if pending_longs is not None:
            new_longs_set = set(pending_longs.keys())
            new_shorts_set = set(pending_shorts.keys())

            # Count trades
            long_entries = new_longs_set - prev_longs
            long_exits = prev_longs - new_longs_set
            short_entries = new_shorts_set - prev_shorts
            short_exits = prev_shorts - new_shorts_set
            n_trades = len(long_entries) + len(long_exits) + len(short_entries) + len(short_exits)
            trade_count += n_trades

            # Trading cost: each trade is one side, cost = FEE_BPS per side
            # Each token traded has a weight, cost is proportional to weight
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

        # ---- Decide rebalance (using shifted signals, to be applied tomorrow) ----
        if i in rebalance_indices:
            lb_rets = lb_rets_df.loc[date].dropna() if date in lb_rets_df.index else pd.Series(dtype=float)

            vol_at_date = rolling_avg_dvol_shifted.loc[date].dropna() if date in rolling_avg_dvol_shifted.index else pd.Series(dtype=float)
            eligible = vol_at_date[vol_at_date >= MIN_AVG_DAILY_VOLUME_USD].index
            lb_rets = lb_rets[lb_rets.index.isin(eligible)]

            if len(lb_rets) >= 2 * k:
                ranked = lb_rets.sort_values(ascending=False)
                top_k = ranked.head(k).index.tolist()
                bottom_k = ranked.tail(k).index.tolist()

                if use_regime_filter:
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
        regime_filter=use_regime_filter,
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
    ema_signal_shifted, rolling_avg_dvol_shifted, hourly_fundings
) -> List[StrategyResult]:
    """Run all 144 parameter combinations."""
    combos = list(product(LOOKBACK_DAYS, REBALANCE_DAYS, K_VALUES, ALLOCATIONS, REGIME_FILTER))
    print(f"Running {len(combos)} parameter combinations...")

    results = []
    for i, (L, N, K, (lw, sw), rf) in enumerate(combos):
        if (i + 1) % 20 == 0:
            print(f"  Progress: {i+1}/{len(combos)}")

        result = run_momentum_rotation(
            daily_close=daily_close,
            daily_returns=daily_returns,
            lookback_returns_shifted=lookback_returns_shifted,
            ema_signal_shifted=ema_signal_shifted,
            rolling_avg_dvol_shifted=rolling_avg_dvol_shifted,
            hourly_fundings=hourly_fundings,
            lookback_days=L,
            rebalance_days=N,
            k=K,
            long_wt=lw,
            short_wt=sw,
            use_regime_filter=rf,
        )
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
    lines.append("# R158 -- Cross-Sectional Momentum Rotation Results\n")
    lines.append(f"**Generated:** {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Simulation Period:** {SIM_START.date()} to {SIM_END.date()} (~2 years)")
    lines.append(f"**Last 12 Months:** {LAST_12MO_START.date()} to {SIM_END.date()}")
    lines.append(f"**Token Universe:** {n_tokens} tokens passing volume filter (>${MIN_AVG_DAILY_VOLUME_USD/1e6:.0f}M avg daily volume)")
    lines.append(f"**Tokens:** {', '.join(sorted(token_list))}")
    lines.append(f"**Total Combinations:** {len(results)}")
    lines.append(f"**Costs:** {FEE_BPS:.0f} bps per side + hourly funding from parquet")
    lines.append(f"**Look-ahead protection:** All signals shifted by 1 day; new positions applied next day\n")

    lines.append("## Parameter Space\n")
    lines.append(f"- Lookback (L): {LOOKBACK_DAYS} days")
    lines.append(f"- Rebalance (N): {REBALANCE_DAYS} days")
    lines.append(f"- K per leg: {K_VALUES}")
    lines.append(f"- Allocation (Long/Short): 50/50, 70/30")
    lines.append(f"- Regime Filter (EMA 20h/50h): Yes/No")
    lines.append("")

    valid = [r for r in results if r.sharpe > -50]
    if not valid:
        lines.append("**No valid results.**\n")
        return "\n".join(lines)

    # ---- TOP 10 by LAST 12 MONTH RETURN ----
    lines.append("---\n")
    lines.append("## TOP 10 by Last 12-Month Return\n")

    by_12m = sorted(valid, key=lambda r: r.last_12m_return, reverse=True)[:10]

    lines.append("| Rank | Label | L | N | K | Alloc | RF | Ann Ret | MaxDD | Sharpe | Calmar | Sortino | PF | 12M Ret | Trades | WR |")
    lines.append("|------|-------|---|---|---|-------|----|---------|-------|--------|--------|---------|-----|---------|--------|-----|")

    for rank, r in enumerate(by_12m, 1):
        alloc = f"{int(r.long_wt*100)}/{int(r.short_wt*100)}"
        rf = "Y" if r.regime_filter else "N"
        lines.append(
            f"| {rank} | {r.label} | {r.lookback} | {r.rebalance} | {r.k} | {alloc} | {rf} "
            f"| {format_pct(r.annual_return)} | {format_pct(r.max_dd)} | {format_float(r.sharpe)} "
            f"| {format_float(r.calmar)} | {format_float(r.sortino)} | {format_float(r.profit_factor)} "
            f"| **{format_pct(r.last_12m_return)}** | {r.trade_count} | {format_pct(r.win_rate)} |"
        )

    # ---- TOP 10 by SHARPE ----
    lines.append("\n---\n")
    lines.append("## TOP 10 by Full-Period Sharpe Ratio\n")

    by_sharpe = sorted(valid, key=lambda r: r.sharpe, reverse=True)[:10]

    lines.append("| Rank | Label | L | N | K | Alloc | RF | Ann Ret | MaxDD | Sharpe | Calmar | Sortino | PF | 12M Ret | Trades | WR |")
    lines.append("|------|-------|---|---|---|-------|----|---------|-------|--------|--------|---------|-----|---------|--------|-----|")

    for rank, r in enumerate(by_sharpe, 1):
        alloc = f"{int(r.long_wt*100)}/{int(r.short_wt*100)}"
        rf = "Y" if r.regime_filter else "N"
        lines.append(
            f"| {rank} | {r.label} | {r.lookback} | {r.rebalance} | {r.k} | {alloc} | {rf} "
            f"| {format_pct(r.annual_return)} | {format_pct(r.max_dd)} | **{format_float(r.sharpe)}** "
            f"| {format_float(r.calmar)} | {format_float(r.sortino)} | {format_float(r.profit_factor)} "
            f"| {format_pct(r.last_12m_return)} | {r.trade_count} | {format_pct(r.win_rate)} |"
        )

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
    lines.append("## Factor Analysis: Average Sharpe by Parameter\n")

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

    lines.append("\n### By Rebalance Frequency (N)\n")
    lines.append("| N | Avg Sharpe | Avg Ann Ret | Avg 12M Ret | Avg MaxDD | Count |")
    lines.append("|---|-----------|-------------|-------------|-----------|-------|")
    for N in REBALANCE_DAYS:
        subset = [r for r in valid if r.rebalance == N]
        if subset:
            lines.append(f"| {N} | {np.mean([r.sharpe for r in subset]):.2f} "
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

    lines.append("\n### By Regime Filter\n")
    lines.append("| RF | Avg Sharpe | Avg Ann Ret | Avg 12M Ret | Avg MaxDD | Count |")
    lines.append("|----|-----------|-------------|-------------|-----------|-------|")
    for rf in REGIME_FILTER:
        subset = [r for r in valid if r.regime_filter == rf]
        if subset:
            lines.append(f"| {'Yes' if rf else 'No'} | {np.mean([r.sharpe for r in subset]):.2f} "
                         f"| {format_pct(np.mean([r.annual_return for r in subset]))} "
                         f"| {format_pct(np.mean([r.last_12m_return for r in subset]))} "
                         f"| {format_pct(np.mean([r.max_dd for r in subset]))} "
                         f"| {len(subset)} |")

    # ---- COST ANALYSIS ----
    lines.append("\n---\n")
    lines.append("## Cost Analysis (Best Sharpe Variant)\n")
    best_sharpe = by_sharpe[0]
    lines.append(f"- **Variant:** {best_sharpe.label}")
    lines.append(f"- **Total Trading Cost (as % of initial equity):** {best_sharpe.total_cost*100:.2f}%")
    lines.append(f"- **Total Funding Impact (as % of initial equity):** {best_sharpe.total_funding*100:.2f}%")
    lines.append(f"- **Trade Count:** {best_sharpe.trade_count}")

    # ---- MONTHLY RETURNS ----
    lines.append("\n---\n")
    lines.append("## Monthly Returns: Best by Last-12M Return\n")
    best_12m = by_12m[0]
    lines.append(f"**Variant:** {best_12m.label}\n")
    if len(best_12m.equity_curve) > 0:
        lines.append(monthly_returns_table(best_12m.equity_curve))

    lines.append("\n---\n")
    lines.append("## Monthly Returns: Best by Sharpe\n")
    lines.append(f"**Variant:** {best_sharpe.label}\n")
    if len(best_sharpe.equity_curve) > 0:
        lines.append(monthly_returns_table(best_sharpe.equity_curve))

    # ---- ALL RESULTS TABLE ----
    lines.append("\n---\n")
    lines.append("## All 144 Combinations (sorted by Sharpe)\n")

    all_sorted = sorted(valid, key=lambda r: r.sharpe, reverse=True)

    lines.append("| # | Label | L | N | K | Alloc | RF | Ann Ret | MaxDD | Sharpe | Sortino | PF | 12M Ret | Trades | WR |")
    lines.append("|---|-------|---|---|---|-------|----|---------|-------|--------|---------|-----|---------|--------|-----|")

    for rank, r in enumerate(all_sorted, 1):
        alloc = f"{int(r.long_wt*100)}/{int(r.short_wt*100)}"
        rf = "Y" if r.regime_filter else "N"
        lines.append(
            f"| {rank} | {r.label} | {r.lookback} | {r.rebalance} | {r.k} | {alloc} | {rf} "
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
    print("R158 -- Cross-Sectional Momentum Rotation Strategy")
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
     ema_signal_shifted, rolling_avg_dvol_shifted, hourly_fundings) = prepare_daily_data(tokens)
    print(f"  Daily close shape: {daily_close.shape}")
    print(f"  Date range: {daily_close.index.min().date()} to {daily_close.index.max().date()}")

    # Sanity check: print BTC total return over sim period
    btc_sim = daily_close['BTC'].loc[SIM_START:SIM_END].dropna()
    if len(btc_sim) > 0:
        btc_ret = btc_sim.iloc[-1] / btc_sim.iloc[0] - 1
        print(f"  BTC total return over sim period: {btc_ret*100:.1f}%")

    # Run parameter sweep
    print("\n[3/3] Running parameter sweep...")
    t1 = time.time()
    results = run_full_sweep(
        daily_close, daily_returns, lookback_returns_shifted,
        ema_signal_shifted, rolling_avg_dvol_shifted, hourly_fundings
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

    by_12m = sorted(valid, key=lambda r: r.last_12m_return, reverse=True)[:5]
    print("\nTop 5 by Last 12M Return:")
    for r in by_12m:
        alloc = f"{int(r.long_wt*100)}/{int(r.short_wt*100)}"
        rf = "RF" if r.regime_filter else "noRF"
        print(f"  L{r.lookback}_R{r.rebalance}_K{r.k}_{alloc}_{rf}: "
              f"12M={r.last_12m_return*100:+.1f}%, Ann={r.annual_return*100:+.1f}%, "
              f"Sharpe={r.sharpe:.2f}, MaxDD={r.max_dd*100:.1f}%")

    by_sharpe = sorted(valid, key=lambda r: r.sharpe, reverse=True)[:5]
    print("\nTop 5 by Sharpe:")
    for r in by_sharpe:
        alloc = f"{int(r.long_wt*100)}/{int(r.short_wt*100)}"
        rf = "RF" if r.regime_filter else "noRF"
        print(f"  L{r.lookback}_R{r.rebalance}_K{r.k}_{alloc}_{rf}: "
              f"Sharpe={r.sharpe:.2f}, Ann={r.annual_return*100:+.1f}%, "
              f"12M={r.last_12m_return*100:+.1f}%, MaxDD={r.max_dd*100:.1f}%")

    t_total = time.time() - t0
    print(f"\nTotal runtime: {t_total:.1f}s")


if __name__ == '__main__':
    main()
