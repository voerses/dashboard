#!/workspace/venv/bin/python
"""
Skew + Trend Combo Strategy — Backtest
=======================================

Research question: Does realized volatility skew (skew_30d) add value as a
trend-quality filter on top of a simple trend-following system for BTC and ETH?

Prior finding:
  - skew_30d has IC = +0.224 OOS (t = +5.15) for BTC/ETH at 7d horizon
  - Positive skew = momentum/trend signal (NOT contrarian)

Signal definition:
  skew_30d = (mean - median) / std of daily returns over a 30-day rolling window
  Positive => right-skewed distribution => bullish trend quality

Strategy:
  BASELINE: Price above 20d SMA => long, below => short (simple trend following)
  OVERLAY:  Only take trend signals when skew_30d confirms direction:
            - Long only when skew > 0 (positive skew = healthy uptrend)
            - Short only when skew < 0 (negative skew = healthy downtrend)
            - Flat when skew contradicts trend direction

Temporal split:
  Train/optimize: < 2025-07-01
  Test (OOS):    >= 2025-07-01

Tokens: BTC, ETH
"""

import warnings
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass

warnings.filterwarnings("ignore")

# ── Configuration ────────────────────────────────────────────────────────

DATA_DIR = Path("/workspace/crypto_backtest/data/perp/1h_cache/")
TRAIN_END = pd.Timestamp("2025-07-01")
TEST_END = pd.Timestamp("2026-03-17 17:00:00")

STARTING_CAPITAL = 100_000.0
SLIPPAGE_BPS = 5          # per side for BTC/ETH (very liquid)
ANNUAL_FUNDING_DRAG = 0.0  # we model funding separately if needed

TOKENS = ["BTC", "ETH"]

# ── Data Loading ─────────────────────────────────────────────────────────

def load_hourly(symbol: str) -> pd.DataFrame:
    """Load hourly OHLCV data for a token."""
    path = DATA_DIR / f"{symbol}_1h.parquet"
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    return df


def resample_daily(df: pd.DataFrame) -> pd.DataFrame:
    """Resample hourly bars to daily OHLCV."""
    daily = df.resample("1D").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["close"])
    return daily


# ── Signal Construction ──────────────────────────────────────────────────

def compute_skew_30d(daily_close: pd.Series, window: int = 30) -> pd.Series:
    """
    Realized volatility skew over a rolling window.
    skew_30d = (mean - median) / std of daily returns
    Positive = right-skewed = bullish trend quality.
    """
    daily_ret = daily_close.pct_change()

    rolling_mean = daily_ret.rolling(window, min_periods=window).mean()
    rolling_median = daily_ret.rolling(window, min_periods=window).median()
    rolling_std = daily_ret.rolling(window, min_periods=window).std()

    skew = (rolling_mean - rolling_median) / rolling_std
    skew = skew.replace([np.inf, -np.inf], np.nan)
    return skew


def compute_sma(daily_close: pd.Series, window: int = 20) -> pd.Series:
    """Simple moving average."""
    return daily_close.rolling(window, min_periods=window).mean()


# ── Strategy Variants ────────────────────────────────────────────────────

def strategy_baseline_trend(daily: pd.DataFrame, sma_window: int = 20) -> pd.Series:
    """
    Baseline trend following: long when close > SMA, short when close < SMA.
    Returns a position series: +1 (long), -1 (short), 0 (flat/warmup).
    """
    sma = compute_sma(daily["close"], sma_window)
    pos = pd.Series(0.0, index=daily.index)
    pos[daily["close"] > sma] = 1.0
    pos[daily["close"] < sma] = -1.0
    # Warmup period: flat
    pos.iloc[:sma_window] = 0.0
    return pos


def strategy_trend_plus_skew(
    daily: pd.DataFrame,
    sma_window: int = 20,
    skew_window: int = 30,
    skew_threshold: float = 0.0,
) -> pd.Series:
    """
    Trend following with skew overlay:
    - Long when close > SMA AND skew > +threshold (healthy uptrend)
    - Short when close < SMA AND skew < -threshold (healthy downtrend)
    - Flat otherwise (skew contradicts trend => don't trade)
    """
    sma = compute_sma(daily["close"], sma_window)
    skew = compute_skew_30d(daily["close"], skew_window)

    pos = pd.Series(0.0, index=daily.index)

    # Long: price above SMA AND positive skew
    long_cond = (daily["close"] > sma) & (skew > skew_threshold)
    # Short: price below SMA AND negative skew
    short_cond = (daily["close"] < sma) & (skew < -skew_threshold)

    pos[long_cond] = 1.0
    pos[short_cond] = -1.0

    # Warmup
    warmup = max(sma_window, skew_window)
    pos.iloc[:warmup] = 0.0
    return pos


def strategy_trend_skew_scaled(
    daily: pd.DataFrame,
    sma_window: int = 20,
    skew_window: int = 30,
) -> pd.Series:
    """
    Trend following with skew-scaled sizing:
    - Direction from SMA crossover
    - Size proportional to abs(skew), capped at 1.0
    - Sign of position = trend direction * sign(skew)
    - If skew contradicts trend => flat
    """
    sma = compute_sma(daily["close"], sma_window)
    skew = compute_skew_30d(daily["close"], skew_window)

    trend_dir = pd.Series(0.0, index=daily.index)
    trend_dir[daily["close"] > sma] = 1.0
    trend_dir[daily["close"] < sma] = -1.0

    # Scale by skew magnitude (capped at 1.0)
    skew_magnitude = skew.abs().clip(upper=1.0)

    # Only trade when skew confirms trend direction
    skew_sign = np.sign(skew)
    confirmed = (trend_dir == skew_sign) & (trend_dir != 0)

    pos = pd.Series(0.0, index=daily.index)
    pos[confirmed] = trend_dir[confirmed] * skew_magnitude[confirmed]

    warmup = max(sma_window, skew_window)
    pos.iloc[:warmup] = 0.0
    return pos


def strategy_long_only_trend(daily: pd.DataFrame, sma_window: int = 20) -> pd.Series:
    """Long-only trend: long when close > SMA, flat otherwise."""
    sma = compute_sma(daily["close"], sma_window)
    pos = pd.Series(0.0, index=daily.index)
    pos[daily["close"] > sma] = 1.0
    pos.iloc[:sma_window] = 0.0
    return pos


def strategy_long_only_trend_skew(
    daily: pd.DataFrame,
    sma_window: int = 20,
    skew_window: int = 30,
    skew_threshold: float = 0.0,
) -> pd.Series:
    """Long-only trend with skew filter: long when close > SMA AND skew > threshold."""
    sma = compute_sma(daily["close"], sma_window)
    skew = compute_skew_30d(daily["close"], skew_window)
    pos = pd.Series(0.0, index=daily.index)
    long_cond = (daily["close"] > sma) & (skew > skew_threshold)
    pos[long_cond] = 1.0
    warmup = max(sma_window, skew_window)
    pos.iloc[:warmup] = 0.0
    return pos


# ── Backtest Engine ──────────────────────────────────────────────────────

@dataclass
class BacktestResult:
    name: str
    symbol: str
    period: str  # "train" or "test"
    positions: pd.Series
    daily_returns: pd.Series
    equity_curve: pd.Series
    annual_return_pct: float
    sharpe: float
    max_dd_pct: float
    win_rate_pct: float
    n_trades: int
    pct_time_in_market: float
    total_return_pct: float
    avg_daily_return_pct: float
    calmar: float


def run_backtest(
    name: str,
    symbol: str,
    daily: pd.DataFrame,
    positions: pd.Series,
    period: str = "test",
    capital: float = STARTING_CAPITAL,
) -> BacktestResult:
    """
    Run a daily-rebalanced backtest.
    positions: +1 long, -1 short, 0 flat (can be fractional for scaled strategies).
    Returns are computed on daily close-to-close basis.
    """
    # Select period
    if period == "test":
        mask = daily.index >= TRAIN_END
    elif period == "train":
        mask = daily.index < TRAIN_END
    else:
        mask = pd.Series(True, index=daily.index)

    daily_sub = daily.loc[mask].copy()
    pos_sub = positions.loc[mask].copy()

    # Daily returns of the asset
    asset_ret = daily_sub["close"].pct_change()

    # Strategy returns = position(t-1) * asset_return(t) - transaction costs
    pos_shifted = pos_sub.shift(1).fillna(0)  # yesterday's position determines today's return
    strat_ret = pos_shifted * asset_ret

    # Transaction costs: slippage on position changes
    pos_change = pos_sub.diff().abs().fillna(0)
    cost = pos_change * (SLIPPAGE_BPS / 10_000)  # cost as fraction of notional
    strat_ret = strat_ret - cost

    # Drop NaN
    strat_ret = strat_ret.dropna()

    if len(strat_ret) == 0:
        return BacktestResult(
            name=name, symbol=symbol, period=period,
            positions=pos_sub, daily_returns=strat_ret,
            equity_curve=pd.Series(dtype=float),
            annual_return_pct=0, sharpe=0, max_dd_pct=0,
            win_rate_pct=0, n_trades=0, pct_time_in_market=0,
            total_return_pct=0, avg_daily_return_pct=0, calmar=0,
        )

    # Equity curve
    equity = capital * (1 + strat_ret).cumprod()

    # Metrics
    total_return = equity.iloc[-1] / capital - 1
    n_days = len(strat_ret)
    annual_factor = 365.0 / max(n_days, 1)
    annual_return = (1 + total_return) ** annual_factor - 1

    # Sharpe
    if strat_ret.std() > 0:
        sharpe = (strat_ret.mean() / strat_ret.std()) * np.sqrt(365)
    else:
        sharpe = 0.0

    # Max drawdown
    peak = equity.cummax()
    dd = (equity - peak) / peak
    max_dd = dd.min()

    # Calmar ratio
    calmar = annual_return / abs(max_dd) if abs(max_dd) > 0.001 else 0.0

    # Win rate (daily)
    active_days = strat_ret[pos_shifted != 0]
    if len(active_days) > 0:
        win_rate = (active_days > 0).mean() * 100
    else:
        win_rate = 0.0

    # Number of trades (position changes)
    trades = (pos_sub.diff().abs() > 0.01).sum()

    # Time in market
    pct_in_market = (pos_sub.abs() > 0.01).mean() * 100

    return BacktestResult(
        name=name,
        symbol=symbol,
        period=period,
        positions=pos_sub,
        daily_returns=strat_ret,
        equity_curve=equity,
        annual_return_pct=annual_return * 100,
        sharpe=sharpe,
        max_dd_pct=max_dd * 100,
        win_rate_pct=win_rate,
        n_trades=int(trades),
        pct_time_in_market=pct_in_market,
        total_return_pct=total_return * 100,
        avg_daily_return_pct=strat_ret.mean() * 100,
        calmar=calmar,
    )


# ── Parameter Optimization (Train Period Only) ──────────────────────────

def optimize_sma_window(daily: pd.DataFrame) -> int:
    """Find best SMA window on train set only. Search: 10, 15, 20, 25, 30, 40, 50."""
    best_sharpe = -999
    best_window = 20
    for w in [10, 15, 20, 25, 30, 40, 50]:
        pos = strategy_baseline_trend(daily, sma_window=w)
        result = run_backtest(f"opt_sma_{w}", "opt", daily, pos, period="train")
        if result.sharpe > best_sharpe:
            best_sharpe = result.sharpe
            best_window = w
    return best_window


def optimize_skew_params(daily: pd.DataFrame, sma_window: int) -> tuple:
    """Find best skew window and threshold on train set."""
    best_sharpe = -999
    best_skew_w = 30
    best_thresh = 0.0
    for sw in [20, 30, 45, 60]:
        for thresh in [0.0, 0.05, 0.10, 0.15, 0.20]:
            pos = strategy_trend_plus_skew(daily, sma_window=sma_window,
                                            skew_window=sw, skew_threshold=thresh)
            result = run_backtest(f"opt_skew_{sw}_{thresh}", "opt", daily, pos, period="train")
            if result.sharpe > best_sharpe:
                best_sharpe = result.sharpe
                best_skew_w = sw
                best_thresh = thresh
    return best_skew_w, best_thresh


# ── Reporting ────────────────────────────────────────────────────────────

def print_result(r: BacktestResult, indent: str = "  "):
    """Print formatted backtest result."""
    print(f"{indent}Annual Return:    {r.annual_return_pct:>+8.1f}%")
    print(f"{indent}Total Return:     {r.total_return_pct:>+8.1f}%")
    print(f"{indent}Sharpe Ratio:     {r.sharpe:>8.2f}")
    print(f"{indent}Calmar Ratio:     {r.calmar:>8.2f}")
    print(f"{indent}Max Drawdown:     {r.max_dd_pct:>8.1f}%")
    print(f"{indent}Win Rate (daily): {r.win_rate_pct:>8.1f}%")
    print(f"{indent}Num Trades:       {r.n_trades:>8d}")
    print(f"{indent}Time in Market:   {r.pct_time_in_market:>8.1f}%")


def print_monthly_breakdown(r: BacktestResult, indent: str = "  "):
    """Print monthly return breakdown for test period."""
    if len(r.daily_returns) == 0:
        print(f"{indent}No returns to show.")
        return
    monthly = r.daily_returns.resample("ME").sum() * 100  # approximate monthly %
    print(f"{indent}{'Month':<12} {'Return%':>10}")
    print(f"{indent}{'-'*25}")
    for dt, ret in monthly.items():
        print(f"{indent}{dt.strftime('%Y-%m'):<12} {ret:>+10.2f}%")


def compare_strategies(baseline: BacktestResult, overlay: BacktestResult) -> dict:
    """Compare overlay vs baseline and compute improvement."""
    sharpe_delta = overlay.sharpe - baseline.sharpe
    dd_improvement = abs(baseline.max_dd_pct) - abs(overlay.max_dd_pct)
    return_delta = overlay.annual_return_pct - baseline.annual_return_pct
    calmar_delta = overlay.calmar - baseline.calmar
    return {
        "sharpe_delta": sharpe_delta,
        "dd_improvement_pct": dd_improvement,
        "return_delta_pct": return_delta,
        "calmar_delta": calmar_delta,
    }


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 90)
    print("SKEW + TREND COMBO STRATEGY — BACKTEST")
    print("=" * 90)
    print()
    print("Research question: Does realized vol skew (skew_30d) improve trend following?")
    print(f"Tokens: {TOKENS}")
    print(f"Train: < {TRAIN_END.date()} | Test (OOS): >= {TRAIN_END.date()}")
    print(f"Capital: ${STARTING_CAPITAL:,.0f} | Slippage: {SLIPPAGE_BPS}bps/side")
    print(f"skew_30d = (mean - median) / std of daily returns over 30d rolling window")
    print()

    # ── Load data ──
    print("-- Loading data --")
    all_daily = {}
    for token in TOKENS:
        hourly = load_hourly(token)
        daily = resample_daily(hourly)
        all_daily[token] = daily
        print(f"  {token}: {daily.shape[0]} daily bars, "
              f"{daily.index.min().date()} to {daily.index.max().date()}")

    # ── Skew signal analysis ──
    print("\n" + "=" * 90)
    print("SKEW SIGNAL ANALYSIS")
    print("=" * 90)

    for token in TOKENS:
        daily = all_daily[token]
        skew = compute_skew_30d(daily["close"], 30)
        daily_ret = daily["close"].pct_change()
        fwd_7d = daily["close"].pct_change(7).shift(-7)

        # Split train/test
        train_mask = daily.index < TRAIN_END
        test_mask = daily.index >= TRAIN_END

        for label, mask in [("Train", train_mask), ("Test (OOS)", test_mask)]:
            s = skew[mask].dropna()
            f = fwd_7d[mask].dropna()
            # Align
            common = s.index.intersection(f.index)
            if len(common) < 30:
                print(f"\n  {token} {label}: insufficient data ({len(common)} obs)")
                continue
            s_aligned = s.loc[common]
            f_aligned = f.loc[common]
            ic = s_aligned.corr(f_aligned)
            # t-stat
            n = len(common)
            t_stat = ic * np.sqrt(n - 2) / np.sqrt(1 - ic**2) if abs(ic) < 1 else 0

            print(f"\n  {token} {label} (n={n}):")
            print(f"    Skew mean: {s_aligned.mean():.4f}, std: {s_aligned.std():.4f}")
            print(f"    IC (skew vs 7d fwd ret): {ic:+.4f} (t={t_stat:+.2f})")
            print(f"    Skew > 0: {(s_aligned > 0).mean()*100:.1f}% of days")

    # ── Optimization on train set ──
    print("\n" + "=" * 90)
    print("PARAMETER OPTIMIZATION (TRAIN SET ONLY)")
    print("=" * 90)

    optimized_params = {}
    for token in TOKENS:
        daily = all_daily[token]
        print(f"\n  {token}:")

        # Optimize SMA window
        best_sma = optimize_sma_window(daily)
        print(f"    Best SMA window: {best_sma}")

        # Optimize skew params
        best_skew_w, best_thresh = optimize_skew_params(daily, best_sma)
        print(f"    Best skew window: {best_skew_w}, threshold: {best_thresh}")

        optimized_params[token] = {
            "sma_window": best_sma,
            "skew_window": best_skew_w,
            "skew_threshold": best_thresh,
        }

    # Also run with fixed default params for robustness check
    default_params = {"sma_window": 20, "skew_window": 30, "skew_threshold": 0.0}

    # ── Run all strategy variants on OOS ──
    print("\n" + "=" * 90)
    print("OUT-OF-SAMPLE BACKTEST RESULTS")
    print("=" * 90)

    all_results = []

    for token in TOKENS:
        daily = all_daily[token]

        print(f"\n{'─'*90}")
        print(f"  {token}")
        print(f"{'─'*90}")

        # ── 1. Buy & Hold benchmark ──
        pos_bh = pd.Series(1.0, index=daily.index)
        r_bh = run_backtest("Buy & Hold", token, daily, pos_bh, period="test")
        print(f"\n  [1] BUY & HOLD")
        print_result(r_bh)
        all_results.append(r_bh)

        # ── 2. Baseline trend (default params) ──
        pos_bt_def = strategy_baseline_trend(daily, sma_window=20)
        r_bt_def = run_backtest("Trend SMA20 (L/S)", token, daily, pos_bt_def, period="test")
        print(f"\n  [2] BASELINE TREND — SMA 20 (default, long/short)")
        print_result(r_bt_def)
        all_results.append(r_bt_def)

        # ── 3. Baseline trend (optimized params) ──
        opt_sma = optimized_params[token]["sma_window"]
        pos_bt_opt = strategy_baseline_trend(daily, sma_window=opt_sma)
        r_bt_opt = run_backtest(f"Trend SMA{opt_sma} (L/S)", token, daily, pos_bt_opt, period="test")
        print(f"\n  [3] BASELINE TREND — SMA {opt_sma} (optimized, long/short)")
        print_result(r_bt_opt)
        all_results.append(r_bt_opt)

        # ── 4. Trend + Skew (default params) ──
        pos_ts_def = strategy_trend_plus_skew(daily, sma_window=20, skew_window=30, skew_threshold=0.0)
        r_ts_def = run_backtest("Trend+Skew (default)", token, daily, pos_ts_def, period="test")
        print(f"\n  [4] TREND + SKEW FILTER — default (SMA20, skew30, thresh=0.0)")
        print_result(r_ts_def)
        all_results.append(r_ts_def)

        # ── 5. Trend + Skew (optimized params) ──
        opt = optimized_params[token]
        pos_ts_opt = strategy_trend_plus_skew(
            daily, sma_window=opt["sma_window"],
            skew_window=opt["skew_window"],
            skew_threshold=opt["skew_threshold"],
        )
        r_ts_opt = run_backtest(
            f"Trend+Skew (opt SMA{opt['sma_window']},sw{opt['skew_window']},t{opt['skew_threshold']})",
            token, daily, pos_ts_opt, period="test",
        )
        print(f"\n  [5] TREND + SKEW FILTER — optimized (SMA{opt['sma_window']}, "
              f"skew{opt['skew_window']}, thresh={opt['skew_threshold']})")
        print_result(r_ts_opt)
        all_results.append(r_ts_opt)

        # ── 6. Trend + Skew scaled ──
        pos_tss = strategy_trend_skew_scaled(daily, sma_window=opt["sma_window"],
                                              skew_window=opt["skew_window"])
        r_tss = run_backtest("Trend+Skew Scaled", token, daily, pos_tss, period="test")
        print(f"\n  [6] TREND + SKEW SCALED — position size proportional to |skew|")
        print_result(r_tss)
        all_results.append(r_tss)

        # ── 7. Long-only trend baseline ──
        pos_lo = strategy_long_only_trend(daily, sma_window=opt["sma_window"])
        r_lo = run_backtest(f"Long-Only Trend SMA{opt['sma_window']}", token, daily, pos_lo, period="test")
        print(f"\n  [7] LONG-ONLY TREND — SMA {opt['sma_window']}")
        print_result(r_lo)
        all_results.append(r_lo)

        # ── 8. Long-only trend + skew ──
        pos_lo_skew = strategy_long_only_trend_skew(
            daily, sma_window=opt["sma_window"],
            skew_window=opt["skew_window"],
            skew_threshold=opt["skew_threshold"],
        )
        r_lo_skew = run_backtest("Long-Only Trend+Skew", token, daily, pos_lo_skew, period="test")
        print(f"\n  [8] LONG-ONLY TREND + SKEW — SMA{opt['sma_window']}, "
              f"skew{opt['skew_window']}, thresh={opt['skew_threshold']}")
        print_result(r_lo_skew)
        all_results.append(r_lo_skew)

    # ── Comparison Tables ────────────────────────────────────────────────
    print("\n" + "=" * 90)
    print("COMPARISON TABLE — ALL STRATEGIES (OOS)")
    print("=" * 90)

    header = (f"{'Token':<5} {'Strategy':<45} {'AnnRet%':>9} {'Sharpe':>8} "
              f"{'MaxDD%':>8} {'Calmar':>8} {'WinR%':>7} {'InMkt%':>7} {'Trades':>7}")
    print(header)
    print("-" * len(header))

    for r in all_results:
        print(f"{r.symbol:<5} {r.name:<45} {r.annual_return_pct:>+9.1f} "
              f"{r.sharpe:>8.2f} {r.max_dd_pct:>8.1f} {r.calmar:>8.2f} "
              f"{r.win_rate_pct:>7.1f} {r.pct_time_in_market:>7.1f} {r.n_trades:>7}")

    # ── Head-to-Head: Baseline Trend vs Trend+Skew ──
    print("\n" + "=" * 90)
    print("HEAD-TO-HEAD: DOES SKEW ADD VALUE?")
    print("=" * 90)

    for token in TOKENS:
        token_results = [r for r in all_results if r.symbol == token]

        # Find the key comparisons
        baseline_ls = [r for r in token_results if "Trend SMA20 (L/S)" in r.name]
        overlay_ls = [r for r in token_results if "Trend+Skew (default)" in r.name]
        baseline_lo = [r for r in token_results if "Long-Only Trend SMA" in r.name]
        overlay_lo = [r for r in token_results if "Long-Only Trend+Skew" in r.name]

        print(f"\n  {token} — Long/Short Comparison:")
        if baseline_ls and overlay_ls:
            b, o = baseline_ls[0], overlay_ls[0]
            delta = compare_strategies(b, o)
            print(f"    Baseline Sharpe:  {b.sharpe:>+.2f}  |  Overlay Sharpe:  {o.sharpe:>+.2f}  |  Delta: {delta['sharpe_delta']:>+.2f}")
            print(f"    Baseline MaxDD:   {b.max_dd_pct:>.1f}%  |  Overlay MaxDD:   {o.max_dd_pct:>.1f}%  |  DD Improvement: {delta['dd_improvement_pct']:>+.1f}%")
            print(f"    Baseline Return:  {b.annual_return_pct:>+.1f}%  |  Overlay Return:  {o.annual_return_pct:>+.1f}%  |  Delta: {delta['return_delta_pct']:>+.1f}%")
            print(f"    Baseline Calmar:  {b.calmar:>.2f}  |  Overlay Calmar:  {o.calmar:>.2f}  |  Delta: {delta['calmar_delta']:>+.2f}")

            # Verdict
            sharpe_pass = delta["sharpe_delta"] > 0.3
            dd_pass = delta["dd_improvement_pct"] > 10
            print(f"\n    Sharpe improvement > 0.3? {'YES' if sharpe_pass else 'NO'} ({delta['sharpe_delta']:+.2f})")
            print(f"    DD improvement > 10%?     {'YES' if dd_pass else 'NO'} ({delta['dd_improvement_pct']:+.1f}%)")
            if sharpe_pass or dd_pass:
                print(f"    >>> VERDICT: SKEW OVERLAY ADDS VALUE for {token} (long/short)")
            else:
                print(f"    >>> VERDICT: Marginal or no improvement for {token} (long/short)")

        print(f"\n  {token} — Long-Only Comparison:")
        if baseline_lo and overlay_lo:
            b, o = baseline_lo[0], overlay_lo[0]
            delta = compare_strategies(b, o)
            print(f"    Baseline Sharpe:  {b.sharpe:>+.2f}  |  Overlay Sharpe:  {o.sharpe:>+.2f}  |  Delta: {delta['sharpe_delta']:>+.2f}")
            print(f"    Baseline MaxDD:   {b.max_dd_pct:>.1f}%  |  Overlay MaxDD:   {o.max_dd_pct:>.1f}%  |  DD Improvement: {delta['dd_improvement_pct']:>+.1f}%")
            print(f"    Baseline Return:  {b.annual_return_pct:>+.1f}%  |  Overlay Return:  {o.annual_return_pct:>+.1f}%  |  Delta: {delta['return_delta_pct']:>+.1f}%")
            print(f"    Baseline Calmar:  {b.calmar:>.2f}  |  Overlay Calmar:  {o.calmar:>.2f}  |  Delta: {delta['calmar_delta']:>+.2f}")

            sharpe_pass = delta["sharpe_delta"] > 0.3
            dd_pass = delta["dd_improvement_pct"] > 10
            print(f"\n    Sharpe improvement > 0.3? {'YES' if sharpe_pass else 'NO'} ({delta['sharpe_delta']:+.2f})")
            print(f"    DD improvement > 10%?     {'YES' if dd_pass else 'NO'} ({delta['dd_improvement_pct']:+.1f}%)")
            if sharpe_pass or dd_pass:
                print(f"    >>> VERDICT: SKEW OVERLAY ADDS VALUE for {token} (long-only)")
            else:
                print(f"    >>> VERDICT: Marginal or no improvement for {token} (long-only)")

    # ── Monthly Breakdown for Best Strategies ──
    print("\n" + "=" * 90)
    print("MONTHLY RETURNS — KEY STRATEGIES (OOS)")
    print("=" * 90)

    for token in TOKENS:
        token_results = [r for r in all_results if r.symbol == token]
        key_strats = [r for r in token_results
                      if any(k in r.name for k in ["Buy & Hold", "Trend SMA20", "Trend+Skew (default)"])]
        for r in key_strats:
            print(f"\n  {token} — {r.name}:")
            print_monthly_breakdown(r)

    # ── Robustness: Parameter Sensitivity ──
    print("\n" + "=" * 90)
    print("ROBUSTNESS: SMA WINDOW SENSITIVITY (OOS)")
    print("=" * 90)

    for token in TOKENS:
        daily = all_daily[token]
        print(f"\n  {token}:")
        print(f"  {'SMA':>6} {'Baseline':>12} {'Baseline':>10} {'Overlay':>12} {'Overlay':>10} {'Sharpe':>10}")
        print(f"  {'Win':>6} {'Sharpe':>12} {'MaxDD%':>10} {'Sharpe':>12} {'MaxDD%':>10} {'Delta':>10}")
        print(f"  {'-'*65}")

        for sma_w in [10, 15, 20, 25, 30, 40, 50]:
            pos_b = strategy_baseline_trend(daily, sma_window=sma_w)
            pos_o = strategy_trend_plus_skew(daily, sma_window=sma_w, skew_window=30, skew_threshold=0.0)
            r_b = run_backtest(f"base_{sma_w}", token, daily, pos_b, period="test")
            r_o = run_backtest(f"overlay_{sma_w}", token, daily, pos_o, period="test")
            delta = r_o.sharpe - r_b.sharpe
            print(f"  {sma_w:>6} {r_b.sharpe:>+12.2f} {r_b.max_dd_pct:>10.1f} "
                  f"{r_o.sharpe:>+12.2f} {r_o.max_dd_pct:>10.1f} {delta:>+10.2f}")

    # ── Robustness: Skew Threshold Sensitivity ──
    print("\n" + "=" * 90)
    print("ROBUSTNESS: SKEW THRESHOLD SENSITIVITY (OOS, SMA=20, SkewW=30)")
    print("=" * 90)

    for token in TOKENS:
        daily = all_daily[token]
        print(f"\n  {token}:")
        print(f"  {'Thresh':>8} {'Sharpe':>10} {'AnnRet%':>10} {'MaxDD%':>10} {'InMkt%':>10} {'Trades':>8}")
        print(f"  {'-'*60}")

        for thresh in [0.0, 0.02, 0.05, 0.08, 0.10, 0.15, 0.20, 0.30]:
            pos = strategy_trend_plus_skew(daily, sma_window=20, skew_window=30, skew_threshold=thresh)
            r = run_backtest(f"thresh_{thresh}", token, daily, pos, period="test")
            print(f"  {thresh:>8.2f} {r.sharpe:>+10.2f} {r.annual_return_pct:>+10.1f} "
                  f"{r.max_dd_pct:>10.1f} {r.pct_time_in_market:>10.1f} {r.n_trades:>8}")

    # ── Combined Portfolio (Equal Weight BTC+ETH) ──
    print("\n" + "=" * 90)
    print("COMBINED PORTFOLIO — EQUAL WEIGHT BTC + ETH (OOS)")
    print("=" * 90)

    portfolio_strats = [
        ("Buy & Hold", lambda d, t: pd.Series(1.0, index=d.index)),
        ("Trend SMA20 (L/S)", lambda d, t: strategy_baseline_trend(d, 20)),
        ("Trend+Skew (default)", lambda d, t: strategy_trend_plus_skew(d, 20, 30, 0.0)),
        ("Long-Only Trend SMA20", lambda d, t: strategy_long_only_trend(d, 20)),
        ("Long-Only Trend+Skew", lambda d, t: strategy_long_only_trend_skew(d, 20, 30, 0.0)),
    ]

    print(f"\n  {'Strategy':<35} {'AnnRet%':>9} {'Sharpe':>8} {'MaxDD%':>8} {'Calmar':>8}")
    print(f"  {'-'*75}")

    for strat_name, strat_fn in portfolio_strats:
        combined_ret = None
        for token in TOKENS:
            daily = all_daily[token]
            pos = strat_fn(daily, token)
            r = run_backtest(strat_name, token, daily, pos, period="test")
            if combined_ret is None:
                combined_ret = r.daily_returns / len(TOKENS)
            else:
                # Align indices
                common_idx = combined_ret.index.intersection(r.daily_returns.index)
                combined_ret = combined_ret.loc[common_idx] + r.daily_returns.loc[common_idx] / len(TOKENS)

        # Compute portfolio metrics
        equity = STARTING_CAPITAL * (1 + combined_ret).cumprod()
        total_return = equity.iloc[-1] / STARTING_CAPITAL - 1
        n_days = len(combined_ret)
        annual_factor = 365.0 / max(n_days, 1)
        annual_return = (1 + total_return) ** annual_factor - 1
        sharpe = (combined_ret.mean() / combined_ret.std()) * np.sqrt(365) if combined_ret.std() > 0 else 0
        peak = equity.cummax()
        dd = (equity - peak) / peak
        max_dd = dd.min()
        calmar = annual_return / abs(max_dd) if abs(max_dd) > 0.001 else 0

        print(f"  {strat_name:<35} {annual_return*100:>+9.1f} {sharpe:>8.2f} "
              f"{max_dd*100:>8.1f} {calmar:>8.2f}")

    # ── Final Summary ──
    print("\n" + "=" * 90)
    print("FINAL SUMMARY & VERDICT")
    print("=" * 90)

    print("""
    METHODOLOGY:
    - Baseline: Simple trend following (price vs SMA, long/short)
    - Overlay: Same trend system, but only take signals when skew_30d confirms
      (positive skew => long OK, negative skew => short OK, else flat)
    - skew_30d = (mean - median) / std of daily returns over 30d
    - STRICT temporal split: optimize on <2025-07-01, test on >=2025-07-01
    - Transaction costs: 5bps per side slippage

    EVALUATION CRITERIA (from research brief):
    - Sharpe improvement > 0.3 => worth pursuing
    - Drawdown improvement > 10% => worth pursuing

    See comparison tables above for per-token and portfolio-level results.
    """)

    # Compute final verdicts
    for token in TOKENS:
        token_results = [r for r in all_results if r.symbol == token]
        b = [r for r in token_results if "Trend SMA20 (L/S)" in r.name]
        o = [r for r in token_results if "Trend+Skew (default)" in r.name]
        if b and o:
            delta = compare_strategies(b[0], o[0])
            worth = delta["sharpe_delta"] > 0.3 or delta["dd_improvement_pct"] > 10
            print(f"    {token} (L/S): Sharpe delta = {delta['sharpe_delta']:+.2f}, "
                  f"DD improvement = {delta['dd_improvement_pct']:+.1f}% "
                  f"=> {'WORTH PURSUING' if worth else 'NOT SUFFICIENT'}")

        b_lo = [r for r in token_results if "Long-Only Trend SMA" in r.name]
        o_lo = [r for r in token_results if "Long-Only Trend+Skew" in r.name]
        if b_lo and o_lo:
            delta = compare_strategies(b_lo[0], o_lo[0])
            worth = delta["sharpe_delta"] > 0.3 or delta["dd_improvement_pct"] > 10
            print(f"    {token} (Long-Only): Sharpe delta = {delta['sharpe_delta']:+.2f}, "
                  f"DD improvement = {delta['dd_improvement_pct']:+.1f}% "
                  f"=> {'WORTH PURSUING' if worth else 'NOT SUFFICIENT'}")

    print("\n" + "=" * 90)
    print("BACKTEST COMPLETE")
    print("=" * 90)


if __name__ == "__main__":
    main()
