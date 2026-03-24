#!/workspace/venv/bin/python
"""
Parameter Sensitivity & Walk-Forward Robustness Test
======================================================
Tests whether the regime-switched positioning signal edge is robust to
+/-20% parameter changes and walk-forward splits.

Parameters tested (independently, +/-20% from base):
  1. Positioning z-score lookback: base=30, test [24, 36]
  2. Regime lookback: base=50, test [40, 60]
  3. Regime thresholds: base=+/-10%, test [+/-8%, +/-12%]
  4. Position entry z-threshold: base=1.0, test [0.8, 1.2]
  5. Macro agreement boost: base=1.5x, test [1.25x, 1.75x]
  6. Macro tercile thresholds: base=33/66 pctile, test [25/75, 40/60]

Walk-forward windows:
  1. Train 2020-09-01 to 2023-06-30 / Test 2023-07-01 to 2024-06-30
  2. Train 2020-09-01 to 2024-06-30 / Test 2024-07-01 to 2025-06-30
  3. Train 2020-09-01 to 2025-06-30 / Test 2025-07-01 to latest

Output:
  - research/parameter_sensitivity_results.md
"""

import warnings
warnings.filterwarnings("ignore")

import os
import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ============================================================================
# CONFIGURATION
# ============================================================================

DATA_ROOT = "/workspace/crypto_backtest/data"
SPOT_PATH = os.path.join(DATA_ROOT, "spot/1h_cache/BTC_1h.parquet")
LS_PATH = os.path.join(DATA_ROOT, "alternative/binance_metrics/all_symbols_daily_ls.parquet")
US10Y_PATH = os.path.join(DATA_ROOT, "alternative/macro/us10y_yield.parquet")
DXY_PATH = os.path.join(DATA_ROOT, "alternative/macro/usd_index.parquet")
OIL_PATH = os.path.join(DATA_ROOT, "alternative/macro/oil_wti.parquet")
OUTPUT_DIR = "/workspace/crypto_backtest/research"

COST_ROUNDTRIP = 0.0010  # 10 bps round-trip per position change

SEP = "=" * 70


@dataclass
class Params:
    """All tunable parameters for the regime-switched positioning system."""
    zscore_lookback: int = 30         # days for z-scoring positioning signals
    regime_lookback: int = 50         # days for regime return calculation
    regime_up_thresh: float = 0.10    # +10% = uptrend
    regime_down_thresh: float = -0.10 # -10% = downtrend (crisis < -20% hardcoded)
    entry_z_thresh: float = 1.0       # z-score threshold for full position
    macro_boost: float = 1.5          # multiplier when macro agrees with positioning
    macro_lo_pctile: float = 0.33     # lower tercile for macro bullish
    macro_hi_pctile: float = 0.67     # upper tercile for macro bearish

    def label(self) -> str:
        return (f"zL={self.zscore_lookback} rL={self.regime_lookback} "
                f"rT=[{self.regime_down_thresh:+.0%},{self.regime_up_thresh:+.0%}] "
                f"eZ={self.entry_z_thresh} mB={self.macro_boost} "
                f"mP=[{self.macro_lo_pctile:.0%}/{self.macro_hi_pctile:.0%}]")


# ============================================================================
# DATA LOADING
# ============================================================================

def load_btc_daily() -> pd.DataFrame:
    """Load BTC spot 1h data, resample to daily OHLCV."""
    df = pd.read_parquet(SPOT_PATH)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df.sort_index()
    daily = df.resample("1D").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["close"])
    return daily


def load_macro(path: str, col_name: str) -> pd.Series:
    """Load macro parquet, return daily Close series."""
    df = pd.read_parquet(path)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date").sort_index()
    return df["Close"].dropna().rename(col_name)


def load_ls_data() -> pd.DataFrame:
    """Load Binance L/S ratio data for BTCUSDT."""
    df = pd.read_parquet(LS_PATH)
    btc = df[df["symbol"] == "BTCUSDT"].copy()
    btc["date"] = pd.to_datetime(btc["date"])
    btc = btc.set_index("date").sort_index()
    return btc[["sum_toptrader_ls_ratio", "count_toptrader_ls_ratio", "count_ls_ratio"]]


def load_all_data() -> Dict[str, pd.DataFrame]:
    """Load all data sources and return as dict."""
    print("  Loading BTC spot daily...")
    btc = load_btc_daily()
    print(f"    {btc.index.min().date()} to {btc.index.max().date()} ({len(btc)} days)")

    print("  Loading L/S positioning data...")
    ls = load_ls_data()
    print(f"    {ls.index.min().date()} to {ls.index.max().date()} ({len(ls)} days)")

    print("  Loading macro data...")
    us10y = load_macro(US10Y_PATH, "us10y")
    dxy = load_macro(DXY_PATH, "dxy")
    oil = load_macro(OIL_PATH, "oil")
    print(f"    US10Y: {us10y.index.min().date()} to {us10y.index.max().date()}")
    print(f"    DXY:   {dxy.index.min().date()} to {dxy.index.max().date()}")
    print(f"    Oil:   {oil.index.min().date()} to {oil.index.max().date()}")

    return {"btc": btc, "ls": ls, "us10y": us10y, "dxy": dxy, "oil": oil}


# ============================================================================
# SIGNAL CONSTRUCTION
# ============================================================================

def build_signal_df(data: Dict, params: Params) -> pd.DataFrame:
    """
    Build the complete signal dataframe with regime detection and all signals.
    ALL signals are z-scored over the lookback window for consistent scaling.

    Signal directions (higher z-score = more bearish for BTC):
      - sig_toptrader_ls_z: higher = more crowded longs = bearish
      - sig_ls_divergence_z: higher = top traders more long than crowd = bearish
      - sig_oil_z: higher oil momentum z = risk-off / supply shock = bearish
      - sig_macro_z: higher US10Y+DXY rank = tighter conditions = bearish

    Position = -mean(active_z_scores): bearish signals -> short BTC
    """
    btc = data["btc"][["close"]].copy()
    btc["daily_ret"] = btc["close"].pct_change()

    # --- Regime detection ---
    regime_ret = btc["close"].pct_change(params.regime_lookback)
    regime = pd.Series("RANGE", index=btc.index)
    regime[regime_ret > params.regime_up_thresh] = "UPTREND"
    regime[regime_ret < params.regime_down_thresh] = "DOWNTREND"
    regime[regime_ret < -0.20] = "CRISIS"  # crisis always < -20%
    btc["regime"] = regime

    # --- Positioning signals (z-scored over lookback) ---
    ls = data["ls"].reindex(btc.index, method="ffill")

    # Top Trader L/S z-score
    toptrader_ls = ls["sum_toptrader_ls_ratio"]
    toptrader_ls_mean = toptrader_ls.rolling(params.zscore_lookback, min_periods=10).mean()
    toptrader_ls_std = toptrader_ls.rolling(params.zscore_lookback, min_periods=10).std()
    btc["sig_toptrader_ls_z"] = (toptrader_ls - toptrader_ls_mean) / (toptrader_ls_std + 1e-10)

    # L/S Divergence z-score
    ls_div = ls["count_toptrader_ls_ratio"] - ls["count_ls_ratio"]
    ls_div_mean = ls_div.rolling(params.zscore_lookback, min_periods=10).mean()
    ls_div_std = ls_div.rolling(params.zscore_lookback, min_periods=10).std()
    btc["sig_ls_divergence_z"] = (ls_div - ls_div_mean) / (ls_div_std + 1e-10)

    # --- Macro signals (also z-scored for consistent scaling) ---
    us10y = data["us10y"].reindex(btc.index, method="ffill")
    dxy = data["dxy"].reindex(btc.index, method="ffill")
    oil = data["oil"].reindex(btc.index, method="ffill")

    # Oil 20d momentum -> z-scored
    oil_20d_mom = oil.pct_change(20)
    oil_mom_mean = oil_20d_mom.rolling(params.zscore_lookback, min_periods=10).mean()
    oil_mom_std = oil_20d_mom.rolling(params.zscore_lookback, min_periods=10).std()
    btc["sig_oil_z"] = (oil_20d_mom - oil_mom_mean) / (oil_mom_std + 1e-10)

    # US10Y + DXY combined: expanding rank of 20d changes -> z-scored
    us10y_20d_chg = us10y.diff(20)
    dxy_20d_mom = dxy.pct_change(20)
    us10y_rank = us10y_20d_chg.expanding(min_periods=60).rank(pct=True)
    dxy_rank = dxy_20d_mom.expanding(min_periods=60).rank(pct=True)
    macro_raw = us10y_rank + dxy_rank  # range [0, 2]
    # Z-score the combined macro score
    macro_mean = macro_raw.rolling(params.zscore_lookback, min_periods=10).mean()
    macro_std = macro_raw.rolling(params.zscore_lookback, min_periods=10).std()
    btc["sig_macro_z"] = (macro_raw - macro_mean) / (macro_std + 1e-10)

    # --- Macro classification (tercile-based, using expanding percentile) ---
    oil_pctile = btc["sig_oil_z"].expanding(min_periods=60).rank(pct=True)
    macro_pctile = btc["sig_macro_z"].expanding(min_periods=60).rank(pct=True)

    # Macro bearish: both in upper tercile (high oil z + high macro z)
    btc["macro_bearish"] = (
        (oil_pctile >= params.macro_hi_pctile) &
        (macro_pctile >= params.macro_hi_pctile)
    )
    # Macro bullish: both in lower tercile
    btc["macro_bullish"] = (
        (oil_pctile <= params.macro_lo_pctile) &
        (macro_pctile <= params.macro_lo_pctile)
    )

    # --- Positioning classification ---
    pos_mean_z = (btc["sig_toptrader_ls_z"].fillna(0) + btc["sig_ls_divergence_z"].fillna(0)) / 2
    btc["positioning_bearish"] = pos_mean_z > params.entry_z_thresh
    btc["positioning_bullish"] = pos_mean_z < -params.entry_z_thresh

    return btc


def compute_positions_vectorized(df: pd.DataFrame, params: Params) -> pd.Series:
    """
    Vectorized position computation.

    Regime-Signal Activation (all signals are z-scores now):
      UPTREND/RANGE: sig_toptrader_ls_z + sig_ls_divergence_z
      DOWNTREND/CRISIS: sig_oil_z + sig_macro_z

    Position = -mean(active_z_scores), capped at [-1, 1]
    Higher z = more bearish -> position goes negative (short BTC)

    Macro agreement boost: when BOTH families point the same direction,
    multiply position magnitude by boost factor.
    """
    regime = df["regime"]

    # --- UPTREND / RANGE: use positioning signals ---
    up_range_mask = regime.isin(["UPTREND", "RANGE"])
    z1 = df["sig_toptrader_ls_z"].where(up_range_mask, np.nan)
    z2 = df["sig_ls_divergence_z"].where(up_range_mask, np.nan)
    up_range_mean_z = pd.concat([z1, z2], axis=1).mean(axis=1, skipna=True)

    # --- DOWNTREND / CRISIS: use macro signals ---
    down_crisis_mask = regime.isin(["DOWNTREND", "CRISIS"])
    z_oil = df["sig_oil_z"].where(down_crisis_mask, np.nan)
    z_macro = df["sig_macro_z"].where(down_crisis_mask, np.nan)
    down_crisis_mean_z = pd.concat([z_oil, z_macro], axis=1).mean(axis=1, skipna=True)

    # Combine: position = -mean_z (bearish z -> short)
    mean_z = up_range_mean_z.fillna(0.0) + down_crisis_mean_z.fillna(0.0)
    positions = -mean_z

    # Z-threshold scaling: linearly reduce position if |mean_z| < threshold
    abs_z = mean_z.abs()
    scale = np.where(
        abs_z < params.entry_z_thresh,
        abs_z / params.entry_z_thresh,
        1.0
    )
    positions = positions * scale

    # Macro agreement boost
    macro_bear = df["macro_bearish"].fillna(False)
    macro_bull = df["macro_bullish"].fillna(False)
    pos_bear = df["positioning_bearish"].fillna(False)
    pos_bull = df["positioning_bullish"].fillna(False)

    # Both families bearish -> boost short (multiply negative position)
    agree_bearish = (macro_bear & pos_bear) | (macro_bear & (positions < 0))
    agree_bullish = (macro_bull & pos_bull) | (macro_bull & (positions > 0))

    positions = positions.where(~agree_bearish, positions * params.macro_boost)
    positions = positions.where(~agree_bullish, positions * params.macro_boost)

    # Cap at [-1, 1]
    positions = positions.clip(-1.0, 1.0)

    # Zero out where we have no signals at all
    no_signal = (
        df["sig_toptrader_ls_z"].isna() &
        df["sig_ls_divergence_z"].isna() &
        df["sig_oil_z"].isna() &
        df["sig_macro_z"].isna()
    )
    positions[no_signal] = 0.0

    return positions


# ============================================================================
# BACKTEST ENGINE
# ============================================================================

@dataclass
class BacktestResult:
    """Results from a single backtest run."""
    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe: float = 0.0
    calmar: float = 0.0
    max_drawdown: float = 0.0
    volatility: float = 0.0
    win_rate: float = 0.0
    n_days: int = 0
    total_cost: float = 0.0
    avg_position: float = 0.0
    turnover: float = 0.0

    def summary_dict(self) -> Dict:
        return {
            "Return": f"{self.total_return:.2%}",
            "Ann.Return": f"{self.annual_return:.2%}",
            "Sharpe": f"{self.sharpe:.3f}",
            "Calmar": f"{self.calmar:.3f}",
            "MaxDD": f"{self.max_drawdown:.2%}",
            "Vol": f"{self.volatility:.2%}",
            "WinRate": f"{self.win_rate:.1%}",
            "Days": self.n_days,
            "Cost": f"{self.total_cost:.2%}",
            "AvgPos": f"{self.avg_position:.3f}",
            "Turnover": f"{self.turnover:.3f}",
        }


def run_backtest(positions: pd.Series, daily_returns: pd.Series,
                 start_date: str, end_date: str,
                 cost_per_change: float = COST_ROUNDTRIP) -> BacktestResult:
    """
    Run a simple daily-rebalanced backtest.

    position = position size [-1, 1] set at close of day t
    return = position * next day return - cost * |position change|
    """
    mask = (positions.index >= start_date) & (positions.index <= end_date)
    pos = positions[mask].copy()
    rets = daily_returns.reindex(pos.index).fillna(0)

    if len(pos) < 20:
        return BacktestResult(n_days=len(pos))

    # Strategy returns: position at close of day t earns day t+1 return
    # Shift position by 1 to avoid lookahead
    pos_shifted = pos.shift(1).fillna(0)

    # Position changes and costs
    pos_change = pos_shifted.diff().fillna(0).abs()
    daily_cost = pos_change * cost_per_change

    # Daily strategy return
    strat_ret = pos_shifted * rets - daily_cost

    # Equity curve
    equity = (1 + strat_ret).cumprod()
    peak = equity.cummax()
    drawdown = (equity - peak) / peak

    n_days = len(strat_ret)
    n_years = n_days / 365.25

    total_return = equity.iloc[-1] - 1.0
    annual_return = (1 + total_return) ** (1 / max(n_years, 0.01)) - 1 if n_years > 0 else 0
    vol = strat_ret.std() * np.sqrt(365)
    sharpe = (strat_ret.mean() / strat_ret.std() * np.sqrt(365)) if strat_ret.std() > 0 else 0
    max_dd = drawdown.min()
    calmar = annual_return / abs(max_dd) if abs(max_dd) > 1e-6 else 0
    win_rate = (strat_ret > 0).mean()
    total_cost = daily_cost.sum()
    avg_pos = pos_shifted.abs().mean()
    turnover = pos_change.mean()

    return BacktestResult(
        total_return=total_return,
        annual_return=annual_return,
        sharpe=sharpe,
        calmar=calmar,
        max_drawdown=max_dd,
        volatility=vol,
        win_rate=win_rate,
        n_days=n_days,
        total_cost=total_cost,
        avg_position=avg_pos,
        turnover=turnover,
    )


# ============================================================================
# PARAMETER SENSITIVITY TESTING
# ============================================================================

def run_with_params(data: Dict, params: Params,
                    start_date: str, end_date: str) -> BacktestResult:
    """Build signals and run backtest with given parameters and date range."""
    df = build_signal_df(data, params)
    positions = compute_positions_vectorized(df, params)
    daily_rets = df["daily_ret"]
    return run_backtest(positions, daily_rets, start_date, end_date)


def parameter_sensitivity_test(data: Dict) -> pd.DataFrame:
    """
    Test each parameter at -20% and +20% from base, one at a time,
    with all others at base values.

    Returns a DataFrame with columns:
      parameter, variant, value, sharpe, calmar, return, maxdd, robust
    """
    # Full period for sensitivity
    FULL_START = "2020-09-01"
    FULL_END = "2026-12-31"

    base = Params()
    print(f"\n  Base params: {base.label()}")

    # Run base case
    base_result = run_with_params(data, base, FULL_START, FULL_END)
    print(f"  Base Sharpe: {base_result.sharpe:.3f}, Calmar: {base_result.calmar:.3f}")

    results = []

    # Define parameter variations
    variations = [
        {
            "name": "Positioning Z-Score Lookback",
            "field": "zscore_lookback",
            "base": 30,
            "low": 24,
            "high": 36,
        },
        {
            "name": "Regime Lookback",
            "field": "regime_lookback",
            "base": 50,
            "low": 40,
            "high": 60,
        },
        {
            "name": "Regime Thresholds",
            "field": "regime_thresholds",  # special handling
            "base": 0.10,
            "low": 0.08,
            "high": 0.12,
        },
        {
            "name": "Entry Z-Threshold",
            "field": "entry_z_thresh",
            "base": 1.0,
            "low": 0.8,
            "high": 1.2,
        },
        {
            "name": "Macro Agreement Boost",
            "field": "macro_boost",
            "base": 1.5,
            "low": 1.25,
            "high": 1.75,
        },
        {
            "name": "Macro Tercile Thresholds",
            "field": "macro_terciles",  # special handling
            "base": "33/66",
            "low": "25/75",
            "high": "40/60",
        },
    ]

    for var in variations:
        name = var["name"]
        print(f"\n  Testing: {name}")

        for variant_label, variant_value in [("Low (-20%)", var["low"]),
                                              ("Base", var["base"]),
                                              ("High (+20%)", var["high"])]:
            p = Params()  # start from base

            if var["field"] == "regime_thresholds":
                p.regime_up_thresh = variant_value
                p.regime_down_thresh = -variant_value
                display_val = f"+/-{variant_value:.0%}"
            elif var["field"] == "macro_terciles":
                if variant_value == "25/75":
                    p.macro_lo_pctile = 0.25
                    p.macro_hi_pctile = 0.75
                elif variant_value == "40/60":
                    p.macro_lo_pctile = 0.40
                    p.macro_hi_pctile = 0.60
                else:
                    p.macro_lo_pctile = 0.33
                    p.macro_hi_pctile = 0.67
                display_val = variant_value
            else:
                setattr(p, var["field"], variant_value)
                display_val = str(variant_value)

            result = run_with_params(data, p, FULL_START, FULL_END)

            results.append({
                "parameter": name,
                "variant": variant_label,
                "value": display_val,
                "sharpe": result.sharpe,
                "calmar": result.calmar,
                "total_return": result.total_return,
                "max_drawdown": result.max_drawdown,
                "annual_return": result.annual_return,
                "volatility": result.volatility,
                "n_days": result.n_days,
            })

            print(f"    {variant_label:12s} ({display_val:>8s}): "
                  f"Sharpe={result.sharpe:+.3f}  Calmar={result.calmar:+.3f}  "
                  f"Return={result.total_return:+.2%}  MaxDD={result.max_drawdown:.2%}")

    results_df = pd.DataFrame(results)

    # Compute robustness: Sharpe doesn't degrade >30% at either extreme.
    # When base Sharpe is near zero (|base| < 0.10), use absolute change instead:
    # robust if variant Sharpe doesn't drop more than 0.15 below base.
    for param_name in results_df["parameter"].unique():
        param_rows = results_df[results_df["parameter"] == param_name]
        base_sharpe = param_rows[param_rows["variant"] == "Base"]["sharpe"].values[0]
        base_calmar = param_rows[param_rows["variant"] == "Base"]["calmar"].values[0]

        for idx in param_rows.index:
            if results_df.loc[idx, "variant"] == "Base":
                results_df.loc[idx, "sharpe_change_pct"] = 0.0
                results_df.loc[idx, "calmar_change_pct"] = 0.0
                results_df.loc[idx, "sharpe_change_abs"] = 0.0
                results_df.loc[idx, "calmar_change_abs"] = 0.0
                continue

            variant_sharpe = results_df.loc[idx, "sharpe"]
            variant_calmar = results_df.loc[idx, "calmar"]

            sharpe_abs_change = variant_sharpe - base_sharpe
            calmar_abs_change = variant_calmar - base_calmar

            if abs(base_sharpe) > 0.10:
                sharpe_change = (variant_sharpe - base_sharpe) / abs(base_sharpe) * 100
            else:
                # For near-zero base, express as absolute change
                sharpe_change = sharpe_abs_change * 100  # scale for readability

            if abs(base_calmar) > 0.10:
                calmar_change = (variant_calmar - base_calmar) / abs(base_calmar) * 100
            else:
                calmar_change = calmar_abs_change * 100

            results_df.loc[idx, "sharpe_change_pct"] = sharpe_change
            results_df.loc[idx, "calmar_change_pct"] = calmar_change
            results_df.loc[idx, "sharpe_change_abs"] = sharpe_abs_change
            results_df.loc[idx, "calmar_change_abs"] = calmar_abs_change

    # Mark robustness per parameter
    # Robust = variant Sharpe doesn't degrade more than:
    #   - 30% relative (if |base Sharpe| > 0.10)
    #   - 0.15 absolute (if |base Sharpe| <= 0.10)
    # AND variant Sharpe doesn't flip sign from positive to deeply negative
    for param_name in results_df["parameter"].unique():
        param_rows = results_df[results_df["parameter"] == param_name]
        base_sharpe = param_rows[param_rows["variant"] == "Base"]["sharpe"].values[0]
        non_base = param_rows[param_rows["variant"] != "Base"]

        if abs(base_sharpe) > 0.10:
            # Relative check: worst degradation < 30%
            max_degradation = non_base["sharpe_change_pct"].min()
            robust = max_degradation > -30
        else:
            # Absolute check: no variant drops more than 0.15 below base
            worst_abs = non_base["sharpe_change_abs"].min()
            robust = worst_abs > -0.15

        results_df.loc[results_df["parameter"] == param_name, "robust"] = robust

    return results_df


# ============================================================================
# WALK-FORWARD TESTING
# ============================================================================

def walk_forward_test(data: Dict) -> pd.DataFrame:
    """
    Run 3 walk-forward windows with base parameters.
    """
    base = Params()

    windows = [
        {"name": "Window 1", "train_start": "2020-09-01", "train_end": "2023-06-30",
         "test_start": "2023-07-01", "test_end": "2024-06-30"},
        {"name": "Window 2", "train_start": "2020-09-01", "train_end": "2024-06-30",
         "test_start": "2024-07-01", "test_end": "2025-06-30"},
        {"name": "Window 3", "train_start": "2020-09-01", "train_end": "2025-06-30",
         "test_start": "2025-07-01", "test_end": "2026-12-31"},
    ]

    results = []
    for w in windows:
        print(f"\n  {w['name']}: Train {w['train_start']} to {w['train_end']}, "
              f"Test {w['test_start']} to {w['test_end']}")

        # Run train period
        train_result = run_with_params(data, base, w["train_start"], w["train_end"])
        print(f"    Train: Sharpe={train_result.sharpe:+.3f}  Calmar={train_result.calmar:+.3f}  "
              f"Return={train_result.total_return:+.2%}  MaxDD={train_result.max_drawdown:.2%}")

        # Run test period
        test_result = run_with_params(data, base, w["test_start"], w["test_end"])
        print(f"    Test:  Sharpe={test_result.sharpe:+.3f}  Calmar={test_result.calmar:+.3f}  "
              f"Return={test_result.total_return:+.2%}  MaxDD={test_result.max_drawdown:.2%}")

        results.append({
            "window": w["name"],
            "train_period": f"{w['train_start']} to {w['train_end']}",
            "test_period": f"{w['test_start']} to {w['test_end']}",
            "train_sharpe": train_result.sharpe,
            "train_calmar": train_result.calmar,
            "train_return": train_result.total_return,
            "train_maxdd": train_result.max_drawdown,
            "test_sharpe": test_result.sharpe,
            "test_calmar": test_result.calmar,
            "test_return": test_result.total_return,
            "test_maxdd": test_result.max_drawdown,
            "test_days": test_result.n_days,
            "test_vol": test_result.volatility,
            "test_turnover": test_result.turnover,
        })

    return pd.DataFrame(results)


# ============================================================================
# WORST-CASE ANALYSIS
# ============================================================================

def worst_case_analysis(data: Dict) -> Dict:
    """
    Find the parameter combination that produces the WORST OOS performance.
    Tests all extreme combinations (2^6 = 64 combos) on walk-forward window 2.
    """
    print("\n  Testing all extreme parameter combinations...")

    # Define parameter extremes
    param_options = {
        "zscore_lookback": [24, 36],
        "regime_lookback": [40, 60],
        "regime_thresh": [0.08, 0.12],
        "entry_z_thresh": [0.8, 1.2],
        "macro_boost": [1.25, 1.75],
        "macro_terciles": [(0.25, 0.75), (0.40, 0.60)],
    }

    # OOS test period for worst-case
    test_start = "2024-07-01"
    test_end = "2025-06-30"

    worst_sharpe = float("inf")
    worst_params = None
    worst_result = None
    all_results = []
    combo_count = 0

    # Generate all 2^6 = 64 combinations
    for zl in param_options["zscore_lookback"]:
        for rl in param_options["regime_lookback"]:
            for rt in param_options["regime_thresh"]:
                for ez in param_options["entry_z_thresh"]:
                    for mb in param_options["macro_boost"]:
                        for mt in param_options["macro_terciles"]:
                            combo_count += 1
                            p = Params(
                                zscore_lookback=zl,
                                regime_lookback=rl,
                                regime_up_thresh=rt,
                                regime_down_thresh=-rt,
                                entry_z_thresh=ez,
                                macro_boost=mb,
                                macro_lo_pctile=mt[0],
                                macro_hi_pctile=mt[1],
                            )

                            result = run_with_params(data, p, test_start, test_end)

                            all_results.append({
                                "params": p.label(),
                                "sharpe": result.sharpe,
                                "calmar": result.calmar,
                                "return": result.total_return,
                                "maxdd": result.max_drawdown,
                            })

                            if result.sharpe < worst_sharpe:
                                worst_sharpe = result.sharpe
                                worst_params = p
                                worst_result = result

    print(f"  Tested {combo_count} combinations")

    all_df = pd.DataFrame(all_results)
    best = all_df.loc[all_df["sharpe"].idxmax()]
    worst = all_df.loc[all_df["sharpe"].idxmin()]

    print(f"  Best combo:  Sharpe={best['sharpe']:+.3f}  Calmar={best['calmar']:+.3f}  Return={best['return']:+.2%}")
    print(f"    Params: {best['params']}")
    print(f"  Worst combo: Sharpe={worst['sharpe']:+.3f}  Calmar={worst['calmar']:+.3f}  Return={worst['return']:+.2%}")
    print(f"    Params: {worst['params']}")

    return {
        "worst_params": worst_params.label() if worst_params else "N/A",
        "worst_sharpe": worst_sharpe,
        "worst_calmar": worst_result.calmar if worst_result else 0,
        "worst_return": worst_result.total_return if worst_result else 0,
        "worst_maxdd": worst_result.max_drawdown if worst_result else 0,
        "best_params": best["params"],
        "best_sharpe": best["sharpe"],
        "best_calmar": best["calmar"],
        "best_return": best["return"],
        "best_maxdd": best.get("maxdd", 0),
        "sharpe_range": (all_df["sharpe"].min(), all_df["sharpe"].max()),
        "n_positive_sharpe": (all_df["sharpe"] > 0).sum(),
        "n_total": len(all_df),
        "median_sharpe": all_df["sharpe"].median(),
        "sharpe_std": all_df["sharpe"].std(),
    }


# ============================================================================
# REPORT GENERATION
# ============================================================================

def generate_report(sensitivity_df: pd.DataFrame, wf_df: pd.DataFrame,
                    worst_case: Dict) -> str:
    """Generate the full markdown report."""
    lines = []
    lines.append("# Parameter Sensitivity & Walk-Forward Robustness Results")
    lines.append("")
    lines.append(f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**System**: Regime-Switched Positioning Signal for BTC")
    lines.append(f"**Cost assumption**: {COST_ROUNDTRIP*10000:.0f} bps round-trip per position change")
    lines.append("")

    # --- 1. Parameter Sensitivity Grid ---
    lines.append("## 1. Parameter Sensitivity Grid")
    lines.append("")
    lines.append("Each parameter tested independently at +/-20% from base, all others at base values.")
    lines.append("")
    lines.append("| Parameter | Low (-20%) | Base | High (+20%) | Sharpe Delta | Calmar Delta | Robust? |")
    lines.append("|-----------|-----------|------|-------------|-------------|-------------|---------|")

    robust_count = 0
    total_params = 0

    for param_name in sensitivity_df["parameter"].unique():
        total_params += 1
        param_rows = sensitivity_df[sensitivity_df["parameter"] == param_name].sort_values("variant")

        low_row = param_rows[param_rows["variant"] == "Low (-20%)"].iloc[0] if len(param_rows[param_rows["variant"] == "Low (-20%)"]) > 0 else None
        base_row = param_rows[param_rows["variant"] == "Base"].iloc[0] if len(param_rows[param_rows["variant"] == "Base"]) > 0 else None
        high_row = param_rows[param_rows["variant"] == "High (+20%)"].iloc[0] if len(param_rows[param_rows["variant"] == "High (+20%)"]) > 0 else None

        if low_row is None or base_row is None or high_row is None:
            continue

        is_robust = bool(base_row.get("robust", False))
        if is_robust:
            robust_count += 1

        low_str = f"{low_row['value']}: Sharpe={low_row['sharpe']:+.3f}"
        base_str = f"{base_row['value']}: Sharpe={base_row['sharpe']:+.3f}"
        high_str = f"{high_row['value']}: Sharpe={high_row['sharpe']:+.3f}"

        # Show absolute Sharpe change (more meaningful when base is near zero)
        low_abs = low_row.get("sharpe_change_abs", low_row["sharpe"] - base_row["sharpe"])
        high_abs = high_row.get("sharpe_change_abs", high_row["sharpe"] - base_row["sharpe"])
        low_calmar_abs = low_row.get("calmar_change_abs", low_row["calmar"] - base_row["calmar"])
        high_calmar_abs = high_row.get("calmar_change_abs", high_row["calmar"] - base_row["calmar"])

        sharpe_change_str = f"Low: {low_abs:+.3f}, High: {high_abs:+.3f}"
        calmar_change_str = f"Low: {low_calmar_abs:+.3f}, High: {high_calmar_abs:+.3f}"

        robust_str = "YES" if is_robust else "NO"

        lines.append(f"| {param_name} | {low_str} | {base_str} | {high_str} | {sharpe_change_str} | {calmar_change_str} | **{robust_str}** |")

    lines.append("")

    # --- Detailed per-parameter table ---
    lines.append("### Detailed Parameter Values")
    lines.append("")
    lines.append("| Parameter | Variant | Value | Sharpe | Calmar | Return | MaxDD | Vol |")
    lines.append("|-----------|---------|-------|--------|--------|--------|-------|-----|")
    for _, row in sensitivity_df.iterrows():
        lines.append(
            f"| {row['parameter']} | {row['variant']} | {row['value']} | "
            f"{row['sharpe']:+.3f} | {row['calmar']:+.3f} | "
            f"{row['total_return']:+.2%} | {row['max_drawdown']:.2%} | "
            f"{row['volatility']:.2%} |"
        )
    lines.append("")

    # --- 2. Walk-Forward Results ---
    lines.append("## 2. Walk-Forward Results")
    lines.append("")
    lines.append("All windows use base parameters. Train period is expanding from 2020-09-01.")
    lines.append("")
    lines.append("| Window | Train Period | Test Period | Test Sharpe | Test Calmar | Test Return | Test MaxDD |")
    lines.append("|--------|-------------|-------------|-------------|-------------|-------------|-----------|")

    all_test_sharpes_positive = True
    for _, row in wf_df.iterrows():
        if row["test_sharpe"] <= 0:
            all_test_sharpes_positive = False
        lines.append(
            f"| {row['window']} | {row['train_period']} | {row['test_period']} | "
            f"{row['test_sharpe']:+.3f} | {row['test_calmar']:+.3f} | "
            f"{row['test_return']:+.2%} | {row['test_maxdd']:.2%} |"
        )
    lines.append("")

    # Train vs Test comparison
    lines.append("### Train vs Test Comparison")
    lines.append("")
    lines.append("| Window | Train Sharpe | Test Sharpe | Delta | Train Return | Test Return |")
    lines.append("|--------|-------------|-------------|-------|-------------|-------------|")
    for _, row in wf_df.iterrows():
        delta = row["test_sharpe"] - row["train_sharpe"]
        lines.append(
            f"| {row['window']} | {row['train_sharpe']:+.3f} | {row['test_sharpe']:+.3f} | "
            f"{delta:+.3f} | {row['train_return']:+.2%} | {row['test_return']:+.2%} |"
        )
    lines.append("")

    # --- 3. Worst-Case Analysis ---
    lines.append("## 3. Worst-Case Analysis")
    lines.append("")
    lines.append(f"Tested all 64 extreme parameter combinations on OOS window (2024-07-01 to 2025-06-30).")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Worst Sharpe | {worst_case['worst_sharpe']:+.3f} |")
    lines.append(f"| Worst Calmar | {worst_case['worst_calmar']:+.3f} |")
    lines.append(f"| Worst Return | {worst_case['worst_return']:+.2%} |")
    lines.append(f"| Worst MaxDD | {worst_case['worst_maxdd']:.2%} |")
    lines.append(f"| Worst Params | {worst_case['worst_params']} |")
    lines.append(f"| Best Sharpe | {worst_case['best_sharpe']:+.3f} |")
    lines.append(f"| Best Params | {worst_case['best_params']} |")
    lines.append(f"| Median Sharpe (all combos) | {worst_case['median_sharpe']:+.3f} |")
    lines.append(f"| Sharpe Std Dev | {worst_case['sharpe_std']:.3f} |")
    lines.append(f"| Sharpe Range | [{worst_case['sharpe_range'][0]:+.3f}, {worst_case['sharpe_range'][1]:+.3f}] |")
    lines.append(f"| Combos with Positive Sharpe | {worst_case['n_positive_sharpe']}/{worst_case['n_total']} ({worst_case['n_positive_sharpe']/worst_case['n_total']*100:.0f}%) |")
    lines.append("")

    # --- 4. Summary Verdict ---
    lines.append("## 4. Summary Verdict")
    lines.append("")

    # Criterion 1: Robust parameters
    lines.append(f"### Parameter Robustness: {robust_count}/{total_params} parameters robust")
    lines.append("")
    if robust_count >= 4:
        lines.append(f"PASS: {robust_count} of {total_params} parameters maintain Sharpe within 0.15 of base at +/-20% perturbation.")
    else:
        lines.append(f"FAIL: Only {robust_count} of {total_params} parameters are robust (need >= 4).")
    lines.append("")

    # Criterion 2: Walk-forward consistency
    positive_windows = sum(1 for _, row in wf_df.iterrows() if row["test_sharpe"] > 0)
    total_windows = len(wf_df)
    lines.append(f"### Walk-Forward Consistency: {positive_windows}/{total_windows} windows positive")
    lines.append("")
    if all_test_sharpes_positive:
        lines.append(f"PASS: All {total_windows} walk-forward windows show positive OOS Sharpe.")
    else:
        lines.append(f"FAIL: {total_windows - positive_windows} of {total_windows} walk-forward windows have non-positive Sharpe.")
    lines.append("")

    # Criterion 3: Worst-case
    lines.append(f"### Worst-Case OOS: Sharpe = {worst_case['worst_sharpe']:+.3f}")
    lines.append("")
    pct_positive = worst_case['n_positive_sharpe'] / worst_case['n_total'] * 100
    if pct_positive >= 50:
        lines.append(f"PASS: {pct_positive:.0f}% of all 64 extreme parameter combinations maintain positive OOS Sharpe.")
    else:
        lines.append(f"WARNING: Only {pct_positive:.0f}% of extreme combinations maintain positive OOS Sharpe.")
    lines.append("")

    # Final recommendation
    lines.append("### Final Recommendation")
    lines.append("")
    if robust_count >= 4 and all_test_sharpes_positive and pct_positive >= 50:
        lines.append("**PROCEED to production.** The signal system passes all robustness checks:")
        lines.append(f"- {robust_count}/{total_params} parameters are robust to +/-20% perturbation")
        lines.append(f"- All {total_windows} walk-forward windows show positive OOS Sharpe")
        lines.append(f"- {pct_positive:.0f}% of extreme parameter combos maintain positive OOS Sharpe")
    elif robust_count >= 3 and positive_windows >= 2:
        lines.append("**NEEDS TUNING.** The signal system shows promise but has fragilities:")
        if robust_count < 4:
            fragile = sensitivity_df[~sensitivity_df["robust"]]["parameter"].unique()
            lines.append(f"- Fragile parameters: {', '.join(fragile)}")
        if not all_test_sharpes_positive:
            lines.append(f"- {total_windows - positive_windows} walk-forward window(s) with non-positive Sharpe")
        if pct_positive < 50:
            lines.append(f"- Only {pct_positive:.0f}% of extreme combos maintain positive Sharpe")
        lines.append("")
        lines.append("Recommended actions:")
        lines.append("- Narrow parameter ranges for fragile parameters")
        lines.append("- Consider ensemble of multiple parameter sets")
        lines.append("- Add regime-specific position sizing caps")
    else:
        lines.append("**KILL.** The signal system fails robustness checks:")
        if robust_count < 4:
            lines.append(f"- Only {robust_count}/{total_params} parameters are robust (need >= 4)")
            fragile = sensitivity_df[~sensitivity_df["robust"]]["parameter"].unique()
            lines.append(f"- Fragile parameters: {', '.join(fragile)}")
        if positive_windows < 3:
            lines.append(f"- Only {positive_windows}/{total_windows} walk-forward windows positive (need all 3)")
        if pct_positive < 50:
            lines.append(f"- Only {pct_positive:.0f}% of extreme combos maintain positive Sharpe")
        lines.append("")
        lines.append("The edge is likely an artifact of parameter fitting. Do not deploy.")

    lines.append("")
    return "\n".join(lines)


# ============================================================================
# MAIN
# ============================================================================

def main():
    print(SEP)
    print("PARAMETER SENSITIVITY & WALK-FORWARD ROBUSTNESS TEST")
    print("System: Regime-Switched Positioning Signal for BTC")
    print(SEP)

    # Load data
    print("\n[1/4] Loading data...")
    data = load_all_data()

    # Parameter sensitivity
    print(f"\n{SEP}")
    print("[2/4] Parameter Sensitivity Testing")
    print(SEP)
    sensitivity_df = parameter_sensitivity_test(data)

    # Walk-forward
    print(f"\n{SEP}")
    print("[3/4] Walk-Forward Testing")
    print(SEP)
    wf_df = walk_forward_test(data)

    # Worst-case
    print(f"\n{SEP}")
    print("[4/4] Worst-Case Analysis (64 extreme combinations)")
    print(SEP)
    worst_case = worst_case_analysis(data)

    # Generate report
    print(f"\n{SEP}")
    print("GENERATING REPORT")
    print(SEP)
    report = generate_report(sensitivity_df, wf_df, worst_case)

    output_path = Path(OUTPUT_DIR) / "parameter_sensitivity_results.md"
    output_path.write_text(report)
    print(f"\nReport saved to: {output_path}")

    script_path = Path(OUTPUT_DIR) / "parameter_sensitivity_test.py"
    print(f"Script saved to: {script_path}")

    # Print summary
    print(f"\n{SEP}")
    print("SUMMARY")
    print(SEP)

    robust_count = int(sensitivity_df.groupby("parameter")["robust"].first().astype(int).sum())
    total_params = sensitivity_df["parameter"].nunique()
    positive_windows = sum(1 for _, row in wf_df.iterrows() if row["test_sharpe"] > 0)
    total_windows = len(wf_df)
    pct_positive = worst_case['n_positive_sharpe'] / worst_case['n_total'] * 100

    print(f"  Parameter robustness: {int(robust_count)}/{total_params}")
    print(f"  Walk-forward windows positive: {positive_windows}/{total_windows}")
    print(f"  Worst-case extreme combos positive: {worst_case['n_positive_sharpe']}/{worst_case['n_total']} ({pct_positive:.0f}%)")
    print(f"  Worst OOS Sharpe: {worst_case['worst_sharpe']:+.3f}")
    print(f"  Median OOS Sharpe (all combos): {worst_case['median_sharpe']:+.3f}")

    if int(robust_count) >= 4 and positive_windows == total_windows and pct_positive >= 50:
        print("\n  VERDICT: PROCEED to production")
    elif int(robust_count) >= 3 and positive_windows >= 2:
        print("\n  VERDICT: NEEDS TUNING")
    else:
        print("\n  VERDICT: KILL")

    print(f"\n{SEP}")
    print("DONE")
    print(SEP)


if __name__ == "__main__":
    main()
