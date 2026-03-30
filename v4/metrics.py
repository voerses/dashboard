"""
V3 Full Quant Metrics Suite
============================

Takes a trade list + equity data and computes the standard quant validation metrics:
Sharpe, Sortino, Calmar, Beta, Alpha, drawdown, trade quality, distribution stats.

Quant metrics: Sharpe, Sortino, Calmar, profit factor, etc.
"""

import math

import numpy as np
import pandas as pd
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PerformanceMetrics:
    """Full quant metrics for a strategy on a single token."""
    # Returns
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0

    # Risk-adjusted
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0

    # Drawdown
    max_drawdown_pct: float = 0.0
    max_drawdown_duration_days: int = 0
    recovery_factor: float = 0.0

    # Trade quality
    total_trades: int = 0
    win_rate_pct: float = 0.0
    payoff_ratio: float = 0.0
    profit_factor: float = 0.0
    avg_trade_pnl: float = 0.0
    avg_hold_hours: float = 0.0

    # Market-relative (vs BTC)
    beta: float = 0.0
    alpha_annualized: float = 0.0
    correlation: float = 0.0

    # Distribution
    skewness: float = 0.0
    kurtosis: float = 0.0
    tail_ratio: float = 0.0

    # Funding (futures/perp only — zero for spot)
    total_funding_cost: float = 0.0
    avg_funding_per_trade: float = 0.0
    funding_drag_pct: float = 0.0  # funding cost as % of gross PnL (positive = net cost, negative = net earned)

    # Walk-forward efficiency (OOS return / IS return)
    walk_forward_efficiency: Optional[float] = None


def build_equity_curve(trades: list, n_bars: int, capital: float,
                       index: Optional[pd.DatetimeIndex] = None) -> pd.Series:
    """
    Reconstruct equity curve from trade list with entry/exit bars.

    Strategy: start at `capital`, apply each trade's PnL at its exit_bar.
    Forward-fill between trades. Resample to daily if index is provided.
    """
    equity = np.full(n_bars, capital, dtype=np.float64)

    # Sort trades by exit_bar
    sorted_trades = sorted(trades, key=lambda t: t.get('exit_bar', 0))

    running = capital
    last_bar = 0
    for t in sorted_trades:
        exit_bar = t.get('exit_bar', 0)
        if exit_bar < 0 or exit_bar >= n_bars:
            running += t['pnl']
            continue
        # Fill from last update to this exit bar with previous equity
        equity[last_bar:exit_bar] = running
        running += t['pnl']
        equity[exit_bar] = running
        last_bar = exit_bar + 1

    # Fill remaining bars
    if last_bar < n_bars:
        equity[last_bar:] = running

    if index is not None and len(index) == n_bars:
        eq_series = pd.Series(equity, index=index)
        # Resample to daily (use last value per day)
        return eq_series.resample('1D').last().dropna()

    return pd.Series(equity)


def compute_metrics(trades: list, equity_curve: pd.Series,
                    benchmark_returns: Optional[pd.Series] = None,
                    capital: float = 200_000) -> PerformanceMetrics:
    """Compute full quant metrics from trades and equity curve."""
    m = PerformanceMetrics()

    if len(equity_curve) < 2:
        m.total_trades = len(trades)
        return m

    # --- Returns ---
    total_return = (equity_curve.iloc[-1] / equity_curve.iloc[0]) - 1.0
    m.total_return_pct = total_return * 100

    n_days = len(equity_curve)
    years = max(n_days / 365.0, 0.01)
    m.annualized_return_pct = ((1 + total_return) ** (1.0 / years) - 1) * 100

    # Daily returns
    daily_returns = equity_curve.pct_change().dropna()
    daily_returns = daily_returns.replace([np.inf, -np.inf], 0.0).fillna(0.0)

    if len(daily_returns) < 2:
        m.total_trades = len(trades)
        return m

    mean_daily = daily_returns.mean()
    std_daily = daily_returns.std()

    # --- Sharpe (annualized, daily returns) ---
    if std_daily > 1e-10:
        m.sharpe_ratio = (mean_daily / std_daily) * np.sqrt(365)
    else:
        m.sharpe_ratio = 0.0

    # --- Sortino (downside deviation only) ---
    negative_returns = daily_returns[daily_returns < 0]
    if len(negative_returns) > 0:
        downside_std = np.sqrt(np.mean(negative_returns ** 2))
        if downside_std > 1e-10:
            m.sortino_ratio = (mean_daily / downside_std) * np.sqrt(365)

    # --- Drawdown ---
    cummax = equity_curve.cummax()
    drawdown = (equity_curve - cummax) / cummax
    m.max_drawdown_pct = float(drawdown.min()) * 100

    # Max drawdown duration
    underwater = drawdown < 0
    if underwater.any():
        groups = (~underwater).cumsum()
        underwater_groups = underwater.groupby(groups).sum()
        m.max_drawdown_duration_days = int(underwater_groups.max())

    # --- Calmar ---
    if abs(m.max_drawdown_pct) > 0.01:
        m.calmar_ratio = m.annualized_return_pct / abs(m.max_drawdown_pct)

    # --- Recovery Factor ---
    total_pnl = equity_curve.iloc[-1] - capital
    if abs(m.max_drawdown_pct) > 0.01:
        # Use peak equity (not initial capital) for max DD absolute value
        peak_equity = float(cummax.max())
        max_dd_abs = abs(m.max_drawdown_pct / 100.0 * peak_equity)
        if max_dd_abs > 0:
            m.recovery_factor = total_pnl / max_dd_abs

    # --- Trade Quality ---
    m.total_trades = len(trades)
    if trades:
        wins = [t for t in trades if t['pnl'] > 0]
        losers = [t for t in trades if t['pnl'] <= 0]
        m.win_rate_pct = len(wins) / len(trades) * 100

        avg_win = np.mean([t['pnl'] for t in wins]) if wins else 0
        avg_loss = abs(np.mean([t['pnl'] for t in losers])) if losers else 1
        m.payoff_ratio = avg_win / max(avg_loss, 1)

        gross_profit = sum(t['pnl'] for t in wins)
        gross_loss = abs(sum(t['pnl'] for t in losers))
        m.profit_factor = gross_profit / max(gross_loss, 1)

        m.avg_trade_pnl = np.mean([t['pnl'] for t in trades])
        m.avg_hold_hours = np.mean([t.get('hold_hours', 0) for t in trades])

    # --- Market-Relative (Beta, Alpha) ---
    if benchmark_returns is not None and len(benchmark_returns) > 10:
        # Align dates
        common_idx = daily_returns.index.intersection(benchmark_returns.index)
        if len(common_idx) > 10:
            strat_r = daily_returns.reindex(common_idx).fillna(0.0)
            bench_r = benchmark_returns.reindex(common_idx).fillna(0.0)

            cov = np.cov(strat_r.values, bench_r.values)
            var_bench = cov[1, 1]
            if var_bench > 1e-15:
                m.beta = cov[0, 1] / var_bench
                m.alpha_annualized = (strat_r.mean() - m.beta * bench_r.mean()) * 365 * 100

            corr = np.corrcoef(strat_r.values, bench_r.values)
            m.correlation = float(corr[0, 1])

    # --- Distribution ---
    if len(daily_returns) > 3:
        m.skewness = float(daily_returns.skew())
        m.kurtosis = float(daily_returns.kurtosis())

        p95 = np.percentile(daily_returns, 95)
        p5 = np.percentile(daily_returns, 5)
        if abs(p5) > 1e-10:
            m.tail_ratio = p95 / abs(p5)

    # --- Funding metrics (perp/combined only) ---
    if trades:
        funding_costs = [t.get('funding_cost', 0) for t in trades]
        total_funding = sum(funding_costs)
        if total_funding != 0:
            m.total_funding_cost = total_funding
            trades_with_funding = [f for f in funding_costs if f != 0]
            if trades_with_funding:
                m.avg_funding_per_trade = total_funding / len(trades_with_funding)
            # Use gross profit (sum of winning trades) as denominator for drag %
            # This avoids sign ambiguity when total PnL is negative
            gross_profit_all = sum(t['pnl'] for t in trades if t['pnl'] > 0)
            if gross_profit_all > 0.01:
                m.funding_drag_pct = total_funding / gross_profit_all * 100

    return m


def load_benchmark_returns(data_dir: str = 'data', market: str = 'perp') -> Optional[pd.Series]:
    """Load BTC daily returns as market benchmark for beta/alpha."""
    btc_path = os.path.join(data_dir, market, '1h_cache/BTC_1h.parquet')
    if not os.path.exists(btc_path):
        return None

    df_1h = pd.read_parquet(btc_path)
    # Aggregate to daily
    daily = df_1h.resample('1D').agg({'close': 'last'}).dropna()
    returns = daily['close'].pct_change().dropna()
    returns = returns.replace([np.inf, -np.inf], 0.0)
    return returns


def deflated_sharpe_ratio(
    sharpe: float,
    n_obs: int,
    skewness: float,
    kurtosis: float,
    n_trials: int,
) -> float:
    """Deflated Sharpe Ratio per Bailey & Lopez de Prado (2014).

    Computes the probability that the observed Sharpe ratio exceeds the
    expected maximum Sharpe under multiple testing, accounting for
    non-normal return distributions.

    Args:
        sharpe: Observed annualized Sharpe ratio.
        n_obs: Number of return observations (e.g., 252 daily).
        skewness: Sample skewness of returns (gamma_3).
        kurtosis: Sample kurtosis of returns (gamma_4, NOT excess).
        n_trials: Number of independent strategy trials tested.

    Returns:
        Probability in [0, 1] that the Sharpe ratio is significant
        after correcting for multiple testing.
    """
    from scipy.stats import norm

    if n_trials < 1:
        n_trials = 1

    # Variance of the Sharpe ratio (Lo, 2002 / Bailey & LdP, 2014)
    # Var(SR) = (1 - skew * SR + (kurt - 1)/4 * SR^2) / (T - 1)
    var_sr = (1 - skewness * sharpe + (kurtosis - 1) / 4 * sharpe ** 2) / max(n_obs - 1, 1)

    # Expected maximum Sharpe ratio under n_trials independent tests
    # E[max(SR)] ≈ sqrt(2 * ln(N)) * (1 - ln(ln(N)) / (2 * ln(N))) + euler_gamma / sqrt(2 * ln(N))
    # Simplified form (Bailey & LdP):
    if n_trials <= 1:
        e_max_sr = 0.0
    else:
        ln_n = math.log(n_trials)
        e_max_sr = math.sqrt(2 * ln_n) * (1 - math.log(ln_n) / (2 * ln_n))

    # z-score: how far the observed SR is from the expected max
    std_sr = math.sqrt(max(var_sr, 1e-20))
    z = (sharpe - e_max_sr) / std_sr

    # DSR = Phi(z) — probability that observed SR exceeds expected max
    return float(norm.cdf(z))


def walk_forward_efficiency(
    oos_annualized_return: float,
    is_annualized_return: float,
) -> Optional[float]:
    """Compute Walk-Forward Efficiency (WFE).

    WFE = oos_annualized_return / is_annualized_return

    Args:
        oos_annualized_return: Out-of-sample annualized return.
        is_annualized_return: In-sample annualized return.

    Returns:
        WFE as a float, or None if IS return <= 0.
    """
    import warnings

    if is_annualized_return <= 0:
        warnings.warn(
            "IS not profitable — WFE undefined",
            UserWarning,
            stacklevel=2,
        )
        return None

    wfe = oos_annualized_return / is_annualized_return

    if wfe < 0.50:
        warnings.warn(
            f"WFE={wfe:.1%} is below 50% — strategy may be overfit",
            UserWarning,
            stacklevel=2,
        )

    return wfe
