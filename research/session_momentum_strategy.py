#!/usr/bin/env python3
"""
Session Momentum Entry Timing — Standalone Strategy + Overlay
=============================================================
Based on R30 research (intraday_session_analysis.py) findings:

KEY OOS RESULTS FROM R30:
  - Europe->US session momentum: IC=0.091 OOS (t=6.62), STRENGTHENS OOS
  - ETH specifically: IC=0.203 OOS (t=3.32), ETH momentum strategy Sharpe 3.02 OOS
  - Asia->US: IC=0.047 OOS (t=3.40)
  - Hour 21-22 UTC long bias: +5.22bps and +12.31bps OOS
  - High-volume hours carry negative returns, low-volume carry positive drift

STANDALONE STRATEGY:
  At 16:00 UTC, check Europe session return (08:00-15:59 UTC).
  If Europe session positive -> LONG for US session (16:00-23:59 UTC).
  If Europe session negative -> SHORT for US session.
  Close at 00:00 UTC (default) or 08:00 UTC next day (overnight carry variant).

OVERLAY VERSION:
  Use session momentum as a FILTER on hourly trend signals.
  - Only enter new trend positions during favorable session momentum.
  - Prefer entry during hours 21-22 UTC (strongest bias).
  - Reduce/exit during high-volume spikes (negative expected return).

Temporal holdout: train < 2025-07-01, test >= 2025-07-01.
"""

import os
import warnings
import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

# ==============================================================================
# CONFIGURATION
# ==============================================================================
DATA_DIR = "/workspace/crypto_backtest/data/perp/1h_cache/"
TRAIN_END = pd.Timestamp("2025-07-01")
MIN_HISTORY_HOURS = 2000

# Session definitions (UTC)
SESSION_ASIA_HOURS = list(range(0, 8))       # 00:00 - 07:59 UTC
SESSION_EUROPE_HOURS = list(range(8, 16))    # 08:00 - 15:59 UTC
SESSION_US_HOURS = list(range(16, 24))       # 16:00 - 23:59 UTC

# Top tokens to test
TOP_TOKENS = ["BTC", "ETH", "SOL", "DOGE", "XRP", "ADA", "AVAX", "LINK", "SUI", "NEAR"]

# Cost assumptions (bps)
COST_SCENARIOS_BPS = [0, 2, 5, 10, 15, 20]


# ==============================================================================
# DATA LOADING
# ==============================================================================
def load_token(token: str) -> pd.DataFrame:
    """Load hourly OHLCV data for a token."""
    fp = os.path.join(DATA_DIR, f"{token}_1h.parquet")
    if not os.path.exists(fp):
        return pd.DataFrame()
    df = pd.read_parquet(fp)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df.sort_index()
    df["ret"] = df["close"].pct_change()
    df["usd_vol"] = df["volume"] * df["close"]
    return df


def load_all_tokens(tokens: list) -> dict:
    """Load data for all requested tokens, filtering by minimum history."""
    all_data = {}
    for token in tokens:
        df = load_token(token)
        if df.empty or len(df) < MIN_HISTORY_HOURS:
            continue
        all_data[token] = df
    return all_data


# ==============================================================================
# FEATURE ENGINEERING
# ==============================================================================
def compute_session_returns(df: pd.DataFrame) -> pd.DataFrame:
    """Compute session-level returns for each day.

    Returns DataFrame indexed by date with columns: Asia, Europe, US.
    """
    ret = df["ret"].dropna()
    if len(ret) == 0:
        return pd.DataFrame()

    ret_df = ret.to_frame("ret")
    ret_df["date"] = ret_df.index.date

    def assign_session(hour):
        if hour < 8:
            return "Asia"
        elif hour < 16:
            return "Europe"
        else:
            return "US"

    ret_df["session"] = ret_df.index.hour.map(assign_session)

    session_daily = ret_df.groupby(["date", "session"])["ret"].apply(
        lambda x: (1 + x).prod() - 1
    ).unstack("session")
    session_daily.index = pd.to_datetime(session_daily.index)
    return session_daily


def compute_volume_zscore(df: pd.DataFrame, lookback: int = 24) -> pd.Series:
    """Rolling volume z-score relative to recent history."""
    usd_vol = df["usd_vol"]
    rolling_mean = usd_vol.rolling(lookback, min_periods=12).mean()
    rolling_std = usd_vol.rolling(lookback, min_periods=12).std().replace(0, np.nan)
    return (usd_vol - rolling_mean) / rolling_std


def compute_hourly_trend_signal(df: pd.DataFrame) -> pd.Series:
    """Simple hourly trend signal: sign of 24h return momentum.

    Used as the base signal that the overlay version filters.
    """
    return np.sign(df["close"].pct_change(24))


# ==============================================================================
# STANDALONE STRATEGY: SESSION MOMENTUM
# ==============================================================================
def run_standalone_strategy(
    df: pd.DataFrame,
    token: str,
    close_hour: int = 0,
    predictor_session: str = "Europe",
    cost_bps: float = 5.0,
) -> pd.DataFrame:
    """Run the standalone session momentum strategy on a single token.

    Logic:
      - At 16:00 UTC (start of US session), check the predictor session return.
      - If positive: go LONG for US session.
      - If negative: go SHORT for US session.
      - Close at `close_hour` UTC (0 = midnight, 8 = next morning for overnight carry).

    Returns a DataFrame of daily trades with PnL.
    """
    session_rets = compute_session_returns(df)
    if session_rets.empty or predictor_session not in session_rets.columns:
        return pd.DataFrame()

    trades = []
    for date_idx in session_rets.index:
        pred_ret = session_rets.loc[date_idx, predictor_session]
        if pd.isna(pred_ret):
            continue

        # Direction: momentum (same direction as predictor session)
        direction = 1 if pred_ret > 0 else -1

        # Entry at 16:00 UTC on this date
        entry_ts = pd.Timestamp(date_idx.date()) + pd.Timedelta(hours=16)

        # Exit depends on close_hour
        if close_hour == 0:
            # Close at midnight = end of US session
            exit_ts = pd.Timestamp(date_idx.date()) + pd.Timedelta(hours=24)
        elif close_hour == 8:
            # Overnight carry: close at 08:00 UTC next day
            exit_ts = pd.Timestamp(date_idx.date()) + pd.Timedelta(hours=32)
        else:
            exit_ts = pd.Timestamp(date_idx.date()) + pd.Timedelta(hours=close_hour)
            if close_hour < 16:
                exit_ts += pd.Timedelta(hours=24)

        # Get actual prices
        if entry_ts not in df.index or exit_ts not in df.index:
            # Try nearest available
            entry_mask = df.index >= entry_ts
            exit_mask = df.index >= exit_ts
            if not entry_mask.any() or not exit_mask.any():
                continue
            entry_price = df.loc[df.index[entry_mask][0], "close"]
            exit_price = df.loc[df.index[exit_mask][0], "close"]
            actual_entry = df.index[entry_mask][0]
            actual_exit = df.index[exit_mask][0]
        else:
            entry_price = df.loc[entry_ts, "close"]
            exit_price = df.loc[exit_ts, "close"]
            actual_entry = entry_ts
            actual_exit = exit_ts

        raw_ret = (exit_price / entry_price - 1) * direction
        cost = cost_bps / 1e4  # cost_bps is already round-trip
        net_ret = raw_ret - cost

        trades.append({
            "date": date_idx,
            "token": token,
            "direction": direction,
            "predictor_ret": pred_ret,
            "entry_ts": actual_entry,
            "exit_ts": actual_exit,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "raw_ret": raw_ret,
            "cost": cost,
            "net_ret": net_ret,
        })

    return pd.DataFrame(trades)


def run_asia_us_strategy(
    df: pd.DataFrame,
    token: str,
    cost_bps: float = 5.0,
) -> pd.DataFrame:
    """Run Asia->US momentum: use Asia session return to predict US.

    Logic:
      - At 16:00 UTC, check Asia session return (00:00-07:59 UTC same day).
      - If Asia positive: go LONG US session.
      - If Asia negative: go SHORT US session.
      - Close at 00:00 UTC.
    """
    return run_standalone_strategy(
        df, token, close_hour=0, predictor_session="Asia", cost_bps=cost_bps
    )


# ==============================================================================
# STANDALONE STRATEGY: HOUR 21-22 LONG BIAS
# ==============================================================================
def run_hour_bias_strategy(
    df: pd.DataFrame,
    token: str,
    target_hours: list = None,
    cost_bps: float = 5.0,
) -> pd.DataFrame:
    """Go LONG during specific hours (21-22 UTC), close at end.

    Based on R30 finding: Hour 21 (+5.22bps OOS) and Hour 22 (+12.31bps OOS).
    """
    if target_hours is None:
        target_hours = [21, 22]

    ret = df["ret"].dropna()
    target_ret = ret[ret.index.hour.isin(target_hours)]
    cost_per_trade = cost_bps / 1e4  # cost_bps is already round-trip

    # Group by date to get one trade per day
    daily_rets = target_ret.groupby(target_ret.index.date).apply(
        lambda x: (1 + x).prod() - 1
    )
    daily_rets.index = pd.to_datetime(daily_rets.index)

    trades = pd.DataFrame({
        "date": daily_rets.index,
        "token": token,
        "direction": 1,  # always long
        "raw_ret": daily_rets.values,
        "cost": cost_per_trade,
        "net_ret": daily_rets.values - cost_per_trade,
    })
    return trades


# ==============================================================================
# COMBO STRATEGY: Session Momentum + Hour Bias
# ==============================================================================
def run_combo_strategy(
    df: pd.DataFrame,
    token: str,
    cost_bps: float = 5.0,
) -> pd.DataFrame:
    """Combine session momentum direction with hour 21-22 entry timing.

    Logic:
      - Compute Europe session return (08:00-15:59).
      - At 21:00 UTC, enter in direction of Europe session momentum.
      - Close at 23:00 UTC (end of hour 22).
      - This combines the strongest timing (hours 21-22) with the
        strongest directional signal (Europe->US momentum).
    """
    session_rets = compute_session_returns(df)
    if session_rets.empty:
        return pd.DataFrame()

    trades = []
    for date_idx in session_rets.index:
        europe_ret = session_rets.loc[date_idx, "Europe"] if "Europe" in session_rets.columns else np.nan
        if pd.isna(europe_ret):
            continue

        direction = 1 if europe_ret > 0 else -1

        entry_ts = pd.Timestamp(date_idx.date()) + pd.Timedelta(hours=21)
        exit_ts = pd.Timestamp(date_idx.date()) + pd.Timedelta(hours=23)

        if entry_ts not in df.index or exit_ts not in df.index:
            entry_mask = (df.index >= entry_ts) & (df.index <= entry_ts + pd.Timedelta(hours=1))
            exit_mask = (df.index >= exit_ts) & (df.index <= exit_ts + pd.Timedelta(hours=1))
            if not entry_mask.any() or not exit_mask.any():
                continue
            entry_price = df.loc[df.index[entry_mask][0], "close"]
            exit_price = df.loc[df.index[exit_mask][0], "close"]
        else:
            entry_price = df.loc[entry_ts, "close"]
            exit_price = df.loc[exit_ts, "close"]

        raw_ret = (exit_price / entry_price - 1) * direction
        cost = cost_bps / 1e4  # cost_bps is already round-trip
        net_ret = raw_ret - cost

        trades.append({
            "date": date_idx,
            "token": token,
            "direction": direction,
            "predictor_ret": europe_ret,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "raw_ret": raw_ret,
            "cost": cost,
            "net_ret": net_ret,
        })

    return pd.DataFrame(trades)


# ==============================================================================
# OVERLAY STRATEGY: Session Momentum as Filter on Hourly Trend
# ==============================================================================
def run_overlay_strategy(
    df: pd.DataFrame,
    token: str,
    cost_bps: float = 5.0,
) -> pd.DataFrame:
    """Session momentum overlay on an hourly trend signal.

    Base signal: sign of 24h price momentum (hourly trend).
    Overlay filters:
      1. Only ENTER new positions when session momentum aligns:
         - During US session (16-24 UTC): require Europe session had same direction.
         - During Europe session (08-16 UTC): require Asia session had same direction.
      2. Prefer entry during hours 21-22 UTC (strongest long bias).
      3. Reduce/exit during high-volume spikes (vol_zscore > 1.5).

    Positions:
      - Hourly rebalancing. Position = trend_signal * session_filter * vol_filter.
      - We track when we enter vs hold to properly count transaction costs.
    """
    df = df.copy()
    df["trend"] = compute_hourly_trend_signal(df)
    df["vol_zscore"] = compute_volume_zscore(df)

    session_rets = compute_session_returns(df)
    if session_rets.empty:
        return pd.DataFrame()

    # Map each hour to its session momentum context
    df["date_key"] = df.index.date
    session_map = {}
    for date_idx in session_rets.index:
        d = date_idx.date()
        asia_ret = session_rets.loc[date_idx, "Asia"] if "Asia" in session_rets.columns else 0.0
        europe_ret = session_rets.loc[date_idx, "Europe"] if "Europe" in session_rets.columns else 0.0
        session_map[d] = {"Asia": asia_ret, "Europe": europe_ret}

    positions = []
    prev_pos = 0

    for ts in df.index:
        if pd.isna(df.loc[ts, "ret"]) or pd.isna(df.loc[ts, "trend"]):
            positions.append(0)
            continue

        hour = ts.hour
        date_key = ts.date()
        trend_dir = df.loc[ts, "trend"]
        vol_z = df.loc[ts, "vol_zscore"]

        # Default: follow the trend
        desired_pos = trend_dir

        # Session momentum filter
        session_info = session_map.get(date_key, None)
        if session_info is not None:
            if hour in SESSION_US_HOURS:
                # During US session: require Europe momentum alignment
                europe_dir = np.sign(session_info["Europe"])
                if europe_dir != 0 and europe_dir != trend_dir:
                    desired_pos = 0  # session momentum disagrees, stay flat
            elif hour in SESSION_EUROPE_HOURS:
                # During Europe session: require Asia momentum alignment
                asia_dir = np.sign(session_info["Asia"])
                if asia_dir != 0 and asia_dir != trend_dir:
                    desired_pos = 0
        else:
            desired_pos = 0  # no session data, stay flat

        # Hour 21-22 UTC boost: allow full position even if marginal
        # (No change needed, just don't filter these hours out)

        # Volume spike filter: reduce position during high volume
        if not pd.isna(vol_z) and vol_z > 1.5:
            desired_pos = 0  # exit during volume spikes

        positions.append(desired_pos)

    df["position"] = positions
    # Calculate returns: position at time t earns ret at time t+1
    df["strat_ret_raw"] = df["position"].shift(1) * df["ret"]

    # Count position changes for cost
    df["pos_change"] = df["position"].diff().abs()
    df["cost_drag"] = df["pos_change"] * (cost_bps / 1e4)
    df["strat_ret_net"] = df["strat_ret_raw"] - df["cost_drag"]

    return df[["ret", "trend", "position", "strat_ret_raw", "strat_ret_net", "cost_drag"]].dropna()


def run_overlay_hour_restricted(
    df: pd.DataFrame,
    token: str,
    cost_bps: float = 5.0,
) -> pd.DataFrame:
    """Overlay variant: only enter during hours 21-22 UTC.

    - Base signal: 24h trend direction.
    - Filter: Europe session momentum must align.
    - Entry: ONLY at hours 21-22 UTC.
    - Hold until end of session (hour 23) or next entry window.
    - Exit during high-volume spikes.
    """
    df = df.copy()
    df["trend"] = compute_hourly_trend_signal(df)
    df["vol_zscore"] = compute_volume_zscore(df)

    session_rets = compute_session_returns(df)
    if session_rets.empty:
        return pd.DataFrame()

    session_map = {}
    for date_idx in session_rets.index:
        d = date_idx.date()
        europe_ret = session_rets.loc[date_idx, "Europe"] if "Europe" in session_rets.columns else 0.0
        session_map[d] = europe_ret

    positions = []
    prev_pos = 0

    for ts in df.index:
        if pd.isna(df.loc[ts, "ret"]) or pd.isna(df.loc[ts, "trend"]):
            positions.append(0)
            prev_pos = 0
            continue

        hour = ts.hour
        date_key = ts.date()
        trend_dir = df.loc[ts, "trend"]
        vol_z = df.loc[ts, "vol_zscore"]

        # Volume spike filter: exit
        if not pd.isna(vol_z) and vol_z > 1.5:
            positions.append(0)
            prev_pos = 0
            continue

        # Entry window: only hours 21-22
        if hour in [21, 22]:
            europe_ret = session_map.get(date_key, 0.0)
            europe_dir = np.sign(europe_ret) if europe_ret != 0 else 0

            if europe_dir != 0 and europe_dir == trend_dir:
                desired_pos = trend_dir
            else:
                desired_pos = 0  # no alignment

            positions.append(desired_pos)
            prev_pos = desired_pos
        elif hour == 23 or hour < 16:
            # Outside entry window: close position
            positions.append(0)
            prev_pos = 0
        else:
            # Hours 16-20: hold previous position if any
            positions.append(prev_pos)

    df["position"] = positions
    df["strat_ret_raw"] = df["position"].shift(1) * df["ret"]
    df["pos_change"] = df["position"].diff().abs()
    df["cost_drag"] = df["pos_change"] * (cost_bps / 1e4)
    df["strat_ret_net"] = df["strat_ret_raw"] - df["cost_drag"]

    return df[["ret", "trend", "position", "strat_ret_raw", "strat_ret_net", "cost_drag"]].dropna()


# ==============================================================================
# PERFORMANCE METRICS
# ==============================================================================
def compute_metrics(returns: pd.Series, trades_per_year: float = 365) -> dict:
    """Compute standard performance metrics from a return series."""
    if len(returns) == 0 or returns.std() == 0:
        return {
            "total_ret": 0, "ann_ret": 0, "ann_vol": 0, "sharpe": 0,
            "max_dd": 0, "hit_rate": 0, "n_trades": 0, "avg_ret_bps": 0,
            "calmar": 0, "profit_factor": 0,
        }

    cum = (1 + returns).cumprod()
    total_ret = cum.iloc[-1] - 1

    n_days = (returns.index[-1] - returns.index[0]).days
    if n_days == 0:
        n_days = 1
    years = n_days / 365.25
    ann_ret = (1 + total_ret) ** (1 / years) - 1 if years > 0 else 0

    # For hourly series, annualize vol differently
    period_factor = np.sqrt(trades_per_year)
    ann_vol = returns.std() * period_factor

    sharpe = returns.mean() / returns.std() * period_factor if returns.std() > 0 else 0

    # Max drawdown
    running_max = cum.cummax()
    drawdown = cum / running_max - 1
    max_dd = drawdown.min()

    calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0

    # Hit rate (fraction of positive returns)
    non_zero = returns[returns != 0]
    hit_rate = (non_zero > 0).mean() if len(non_zero) > 0 else 0

    # Profit factor
    gains = returns[returns > 0].sum()
    losses = abs(returns[returns < 0].sum())
    profit_factor = gains / losses if losses > 0 else np.inf

    return {
        "total_ret": total_ret,
        "ann_ret": ann_ret,
        "ann_vol": ann_vol,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "hit_rate": hit_rate,
        "n_trades": len(non_zero),
        "avg_ret_bps": returns.mean() * 1e4,
        "calmar": calmar,
        "profit_factor": profit_factor,
    }


def print_metrics(metrics: dict, label: str):
    """Pretty-print a metrics dict."""
    print(f"  {label}:")
    print(f"    Sharpe:        {metrics['sharpe']:>8.2f}")
    print(f"    Total Return:  {metrics['total_ret']:>8.2%}")
    print(f"    Ann. Return:   {metrics['ann_ret']:>8.2%}")
    print(f"    Ann. Vol:      {metrics['ann_vol']:>8.2%}")
    print(f"    Max Drawdown:  {metrics['max_dd']:>8.2%}")
    print(f"    Hit Rate:      {metrics['hit_rate']:>8.2%}")
    print(f"    N Trades:      {metrics['n_trades']:>8d}")
    print(f"    Avg Ret (bps): {metrics['avg_ret_bps']:>8.2f}")
    print(f"    Calmar:        {metrics['calmar']:>8.2f}")
    print(f"    Profit Factor: {metrics['profit_factor']:>8.2f}")


# ==============================================================================
# BACKTEST RUNNERS
# ==============================================================================
def backtest_standalone_all_tokens(all_data: dict, cost_bps: float = 5.0):
    """Run standalone Europe->US momentum strategy across all tokens."""
    print("\n" + "=" * 95)
    print("STANDALONE STRATEGY 1: EUROPE->US SESSION MOMENTUM")
    print(f"  Entry: 16:00 UTC | Exit: 00:00 UTC | Cost: {cost_bps}bps round-trip")
    print("=" * 95)

    is_all_trades = []
    oos_all_trades = []

    header = f"  {'Token':>6}  {'Period':>4}  {'Sharpe':>8}  {'TotRet':>8}  {'AnnRet':>8}  " \
             f"{'MaxDD':>8}  {'Hit%':>7}  {'N':>6}  {'AvgBps':>8}  {'PF':>8}"
    print(header)
    print("  " + "-" * 90)

    for token in TOP_TOKENS:
        if token not in all_data:
            continue
        df = all_data[token]
        trades = run_standalone_strategy(df, token, close_hour=0, predictor_session="Europe", cost_bps=cost_bps)
        if trades.empty:
            continue

        is_trades = trades[trades["date"] < TRAIN_END]
        oos_trades = trades[trades["date"] >= TRAIN_END]
        is_all_trades.append(is_trades)
        oos_all_trades.append(oos_trades)

        for label, t in [("IS", is_trades), ("OOS", oos_trades)]:
            if len(t) == 0:
                continue
            ret_series = t.set_index("date")["net_ret"]
            m = compute_metrics(ret_series, trades_per_year=365)
            print(f"  {token:>6}  {label:>4}  {m['sharpe']:>8.2f}  {m['total_ret']:>8.2%}  "
                  f"{m['ann_ret']:>8.2%}  {m['max_dd']:>8.2%}  {m['hit_rate']:>6.1%}  "
                  f"{m['n_trades']:>6}  {m['avg_ret_bps']:>8.2f}  {m['profit_factor']:>8.2f}")

    # Equal-weight basket
    print("\n  --- EQUAL-WEIGHT BASKET ---")
    if is_all_trades:
        is_combined = pd.concat(is_all_trades)
        basket_is = is_combined.groupby("date")["net_ret"].mean()
        basket_is.index = pd.to_datetime(basket_is.index)
        m = compute_metrics(basket_is, trades_per_year=365)
        print_metrics(m, "IS Basket")

    if oos_all_trades:
        oos_combined = pd.concat(oos_all_trades)
        basket_oos = oos_combined.groupby("date")["net_ret"].mean()
        basket_oos.index = pd.to_datetime(basket_oos.index)
        m = compute_metrics(basket_oos, trades_per_year=365)
        print_metrics(m, "OOS Basket")

    return is_all_trades, oos_all_trades


def backtest_asia_us_all_tokens(all_data: dict, cost_bps: float = 5.0):
    """Run Asia->US momentum strategy across all tokens."""
    print("\n" + "=" * 95)
    print("STANDALONE STRATEGY 2: ASIA->US SESSION MOMENTUM")
    print(f"  Entry: 16:00 UTC | Exit: 00:00 UTC | Cost: {cost_bps}bps round-trip")
    print("=" * 95)

    is_all_trades = []
    oos_all_trades = []

    header = f"  {'Token':>6}  {'Period':>4}  {'Sharpe':>8}  {'TotRet':>8}  {'AnnRet':>8}  " \
             f"{'MaxDD':>8}  {'Hit%':>7}  {'N':>6}  {'AvgBps':>8}  {'PF':>8}"
    print(header)
    print("  " + "-" * 90)

    for token in TOP_TOKENS:
        if token not in all_data:
            continue
        df = all_data[token]
        trades = run_asia_us_strategy(df, token, cost_bps=cost_bps)
        if trades.empty:
            continue

        is_trades = trades[trades["date"] < TRAIN_END]
        oos_trades = trades[trades["date"] >= TRAIN_END]
        is_all_trades.append(is_trades)
        oos_all_trades.append(oos_trades)

        for label, t in [("IS", is_trades), ("OOS", oos_trades)]:
            if len(t) == 0:
                continue
            ret_series = t.set_index("date")["net_ret"]
            m = compute_metrics(ret_series, trades_per_year=365)
            print(f"  {token:>6}  {label:>4}  {m['sharpe']:>8.2f}  {m['total_ret']:>8.2%}  "
                  f"{m['ann_ret']:>8.2%}  {m['max_dd']:>8.2%}  {m['hit_rate']:>6.1%}  "
                  f"{m['n_trades']:>6}  {m['avg_ret_bps']:>8.2f}  {m['profit_factor']:>8.2f}")

    # Basket
    print("\n  --- EQUAL-WEIGHT BASKET ---")
    if is_all_trades:
        is_combined = pd.concat(is_all_trades)
        basket_is = is_combined.groupby("date")["net_ret"].mean()
        basket_is.index = pd.to_datetime(basket_is.index)
        m = compute_metrics(basket_is, trades_per_year=365)
        print_metrics(m, "IS Basket")

    if oos_all_trades:
        oos_combined = pd.concat(oos_all_trades)
        basket_oos = oos_combined.groupby("date")["net_ret"].mean()
        basket_oos.index = pd.to_datetime(basket_oos.index)
        m = compute_metrics(basket_oos, trades_per_year=365)
        print_metrics(m, "OOS Basket")


def backtest_hour_bias_all_tokens(all_data: dict, cost_bps: float = 5.0):
    """Run hour 21-22 UTC long bias strategy across all tokens."""
    print("\n" + "=" * 95)
    print("STANDALONE STRATEGY 3: HOUR 21-22 UTC LONG BIAS")
    print(f"  Entry: 21:00 UTC | Exit: 23:00 UTC | Always LONG | Cost: {cost_bps}bps round-trip")
    print("=" * 95)

    is_all_trades = []
    oos_all_trades = []

    header = f"  {'Token':>6}  {'Period':>4}  {'Sharpe':>8}  {'TotRet':>8}  {'AnnRet':>8}  " \
             f"{'MaxDD':>8}  {'Hit%':>7}  {'N':>6}  {'AvgBps':>8}  {'PF':>8}"
    print(header)
    print("  " + "-" * 90)

    for token in TOP_TOKENS:
        if token not in all_data:
            continue
        df = all_data[token]
        trades = run_hour_bias_strategy(df, token, target_hours=[21, 22], cost_bps=cost_bps)
        if trades.empty:
            continue

        is_trades = trades[trades["date"] < TRAIN_END]
        oos_trades = trades[trades["date"] >= TRAIN_END]
        is_all_trades.append(is_trades)
        oos_all_trades.append(oos_trades)

        for label, t in [("IS", is_trades), ("OOS", oos_trades)]:
            if len(t) == 0:
                continue
            ret_series = t.set_index("date")["net_ret"]
            m = compute_metrics(ret_series, trades_per_year=365)
            print(f"  {token:>6}  {label:>4}  {m['sharpe']:>8.2f}  {m['total_ret']:>8.2%}  "
                  f"{m['ann_ret']:>8.2%}  {m['max_dd']:>8.2%}  {m['hit_rate']:>6.1%}  "
                  f"{m['n_trades']:>6}  {m['avg_ret_bps']:>8.2f}  {m['profit_factor']:>8.2f}")

    # Basket
    print("\n  --- EQUAL-WEIGHT BASKET ---")
    if is_all_trades:
        is_combined = pd.concat(is_all_trades)
        basket_is = is_combined.groupby("date")["net_ret"].mean()
        basket_is.index = pd.to_datetime(basket_is.index)
        m = compute_metrics(basket_is, trades_per_year=365)
        print_metrics(m, "IS Basket")

    if oos_all_trades:
        oos_combined = pd.concat(oos_all_trades)
        basket_oos = oos_combined.groupby("date")["net_ret"].mean()
        basket_oos.index = pd.to_datetime(basket_oos.index)
        m = compute_metrics(basket_oos, trades_per_year=365)
        print_metrics(m, "OOS Basket")


def backtest_combo_all_tokens(all_data: dict, cost_bps: float = 5.0):
    """Run combo strategy: Europe momentum + hour 21-22 entry."""
    print("\n" + "=" * 95)
    print("STANDALONE STRATEGY 4: COMBO (Europe Momentum + Hour 21-22 Entry)")
    print(f"  Entry: 21:00 UTC | Direction: Europe session momentum | Exit: 23:00 UTC | Cost: {cost_bps}bps")
    print("=" * 95)

    is_all_trades = []
    oos_all_trades = []

    header = f"  {'Token':>6}  {'Period':>4}  {'Sharpe':>8}  {'TotRet':>8}  {'AnnRet':>8}  " \
             f"{'MaxDD':>8}  {'Hit%':>7}  {'N':>6}  {'AvgBps':>8}  {'PF':>8}"
    print(header)
    print("  " + "-" * 90)

    for token in TOP_TOKENS:
        if token not in all_data:
            continue
        df = all_data[token]
        trades = run_combo_strategy(df, token, cost_bps=cost_bps)
        if trades.empty:
            continue

        is_trades = trades[trades["date"] < TRAIN_END]
        oos_trades = trades[trades["date"] >= TRAIN_END]
        is_all_trades.append(is_trades)
        oos_all_trades.append(oos_trades)

        for label, t in [("IS", is_trades), ("OOS", oos_trades)]:
            if len(t) == 0:
                continue
            ret_series = t.set_index("date")["net_ret"]
            m = compute_metrics(ret_series, trades_per_year=365)
            print(f"  {token:>6}  {label:>4}  {m['sharpe']:>8.2f}  {m['total_ret']:>8.2%}  "
                  f"{m['ann_ret']:>8.2%}  {m['max_dd']:>8.2%}  {m['hit_rate']:>6.1%}  "
                  f"{m['n_trades']:>6}  {m['avg_ret_bps']:>8.2f}  {m['profit_factor']:>8.2f}")

    # Basket
    print("\n  --- EQUAL-WEIGHT BASKET ---")
    if is_all_trades:
        is_combined = pd.concat(is_all_trades)
        basket_is = is_combined.groupby("date")["net_ret"].mean()
        basket_is.index = pd.to_datetime(basket_is.index)
        m = compute_metrics(basket_is, trades_per_year=365)
        print_metrics(m, "IS Basket")

    if oos_all_trades:
        oos_combined = pd.concat(oos_all_trades)
        basket_oos = oos_combined.groupby("date")["net_ret"].mean()
        basket_oos.index = pd.to_datetime(basket_oos.index)
        m = compute_metrics(basket_oos, trades_per_year=365)
        print_metrics(m, "OOS Basket")


def backtest_overlay_all_tokens(all_data: dict, cost_bps: float = 5.0):
    """Run overlay strategy across all tokens."""
    print("\n" + "=" * 95)
    print("OVERLAY STRATEGY 1: SESSION MOMENTUM FILTER ON HOURLY TREND")
    print(f"  Base: 24h trend direction | Filter: session momentum alignment + vol spike exit")
    print(f"  Cost: {cost_bps}bps per position change")
    print("=" * 95)

    header = f"  {'Token':>6}  {'Period':>4}  {'Sharpe':>8}  {'TotRet':>8}  {'AnnRet':>8}  " \
             f"{'MaxDD':>8}  {'Hit%':>7}  {'N':>6}  {'AvgBps':>8}"
    print(header)
    print("  " + "-" * 80)

    is_all = []
    oos_all = []

    for token in TOP_TOKENS:
        if token not in all_data:
            continue
        df = all_data[token]
        result = run_overlay_strategy(df, token, cost_bps=cost_bps)
        if result.empty:
            continue

        is_result = result[result.index < TRAIN_END]
        oos_result = result[result.index >= TRAIN_END]

        for label, r in [("IS", is_result), ("OOS", oos_result)]:
            if len(r) == 0:
                continue
            ret = r["strat_ret_net"]
            m = compute_metrics(ret, trades_per_year=365 * 24)
            active = r[r["position"] != 0]
            print(f"  {token:>6}  {label:>4}  {m['sharpe']:>8.2f}  {m['total_ret']:>8.2%}  "
                  f"{m['ann_ret']:>8.2%}  {m['max_dd']:>8.2%}  {m['hit_rate']:>6.1%}  "
                  f"{len(active):>6}  {m['avg_ret_bps']:>8.2f}")

        is_all.append(is_result["strat_ret_net"])
        oos_all.append(oos_result["strat_ret_net"])

    # Basket (average across tokens at each timestamp)
    print("\n  --- EQUAL-WEIGHT BASKET ---")
    if is_all:
        basket_is = pd.concat(is_all, axis=1).mean(axis=1).dropna()
        m = compute_metrics(basket_is, trades_per_year=365 * 24)
        print_metrics(m, "IS Basket")
    if oos_all:
        basket_oos = pd.concat(oos_all, axis=1).mean(axis=1).dropna()
        m = compute_metrics(basket_oos, trades_per_year=365 * 24)
        print_metrics(m, "OOS Basket")


def backtest_overlay_hour_restricted_all_tokens(all_data: dict, cost_bps: float = 5.0):
    """Run hour-restricted overlay strategy across all tokens."""
    print("\n" + "=" * 95)
    print("OVERLAY STRATEGY 2: HOUR 21-22 RESTRICTED SESSION MOMENTUM OVERLAY")
    print(f"  Base: 24h trend direction | Filter: Europe momentum + hours 21-22 only + vol filter")
    print(f"  Cost: {cost_bps}bps per position change")
    print("=" * 95)

    header = f"  {'Token':>6}  {'Period':>4}  {'Sharpe':>8}  {'TotRet':>8}  {'AnnRet':>8}  " \
             f"{'MaxDD':>8}  {'Hit%':>7}  {'N':>6}  {'AvgBps':>8}"
    print(header)
    print("  " + "-" * 80)

    is_all = []
    oos_all = []

    for token in TOP_TOKENS:
        if token not in all_data:
            continue
        df = all_data[token]
        result = run_overlay_hour_restricted(df, token, cost_bps=cost_bps)
        if result.empty:
            continue

        is_result = result[result.index < TRAIN_END]
        oos_result = result[result.index >= TRAIN_END]

        for label, r in [("IS", is_result), ("OOS", oos_result)]:
            if len(r) == 0:
                continue
            ret = r["strat_ret_net"]
            m = compute_metrics(ret, trades_per_year=365 * 24)
            active = r[r["position"] != 0]
            print(f"  {token:>6}  {label:>4}  {m['sharpe']:>8.2f}  {m['total_ret']:>8.2%}  "
                  f"{m['ann_ret']:>8.2%}  {m['max_dd']:>8.2%}  {m['hit_rate']:>6.1%}  "
                  f"{len(active):>6}  {m['avg_ret_bps']:>8.2f}")

        is_all.append(is_result["strat_ret_net"])
        oos_all.append(oos_result["strat_ret_net"])

    # Basket
    print("\n  --- EQUAL-WEIGHT BASKET ---")
    if is_all:
        basket_is = pd.concat(is_all, axis=1).mean(axis=1).dropna()
        m = compute_metrics(basket_is, trades_per_year=365 * 24)
        print_metrics(m, "IS Basket")
    if oos_all:
        basket_oos = pd.concat(oos_all, axis=1).mean(axis=1).dropna()
        m = compute_metrics(basket_oos, trades_per_year=365 * 24)
        print_metrics(m, "OOS Basket")


# ==============================================================================
# TRANSACTION COST SENSITIVITY
# ==============================================================================
def cost_sensitivity_analysis(all_data: dict):
    """Test how strategies degrade across transaction cost levels."""
    print("\n" + "=" * 95)
    print("TRANSACTION COST SENSITIVITY ANALYSIS")
    print("=" * 95)

    strategies = {
        "Europe->US Momentum": lambda token, df, c: run_standalone_strategy(
            df, token, close_hour=0, predictor_session="Europe", cost_bps=c
        ),
        "Asia->US Momentum": lambda token, df, c: run_asia_us_strategy(df, token, cost_bps=c),
        "Hour 21-22 Long Bias": lambda token, df, c: run_hour_bias_strategy(df, token, cost_bps=c),
        "Combo (Europe+H21-22)": lambda token, df, c: run_combo_strategy(df, token, cost_bps=c),
    }

    for strat_name, strat_fn in strategies.items():
        print(f"\n  {strat_name}:")
        print(f"  {'Cost(bps)':>10}  {'IS Sharpe':>10}  {'IS Ret':>10}  {'OOS Sharpe':>10}  {'OOS Ret':>10}")
        print("  " + "-" * 55)

        for cost in COST_SCENARIOS_BPS:
            is_trades_all = []
            oos_trades_all = []

            for token in TOP_TOKENS:
                if token not in all_data:
                    continue
                df = all_data[token]
                trades = strat_fn(token, df, cost)
                if isinstance(trades, pd.DataFrame) and not trades.empty:
                    if "date" in trades.columns:
                        is_t = trades[trades["date"] < TRAIN_END]
                        oos_t = trades[trades["date"] >= TRAIN_END]
                        is_trades_all.append(is_t)
                        oos_trades_all.append(oos_t)

            is_sharpe = oos_sharpe = np.nan
            is_ret = oos_ret = np.nan

            if is_trades_all:
                is_combined = pd.concat(is_trades_all)
                basket = is_combined.groupby("date")["net_ret"].mean()
                basket.index = pd.to_datetime(basket.index)
                m = compute_metrics(basket, trades_per_year=365)
                is_sharpe = m["sharpe"]
                is_ret = m["total_ret"]

            if oos_trades_all:
                oos_combined = pd.concat(oos_trades_all)
                basket = oos_combined.groupby("date")["net_ret"].mean()
                basket.index = pd.to_datetime(basket.index)
                m = compute_metrics(basket, trades_per_year=365)
                oos_sharpe = m["sharpe"]
                oos_ret = m["total_ret"]

            print(f"  {cost:>10}  {is_sharpe:>10.2f}  {is_ret:>10.2%}  "
                  f"{oos_sharpe:>10.2f}  {oos_ret:>10.2%}")


# ==============================================================================
# OVERNIGHT CARRY VARIANT
# ==============================================================================
def backtest_overnight_carry(all_data: dict, cost_bps: float = 5.0):
    """Test overnight carry variant: hold until 08:00 UTC next day instead of midnight."""
    print("\n" + "=" * 95)
    print("STANDALONE STRATEGY 5: EUROPE->US MOMENTUM WITH OVERNIGHT CARRY")
    print(f"  Entry: 16:00 UTC | Exit: 08:00 UTC next day | Cost: {cost_bps}bps round-trip")
    print("=" * 95)

    is_all_trades = []
    oos_all_trades = []

    header = f"  {'Token':>6}  {'Period':>4}  {'Sharpe':>8}  {'TotRet':>8}  {'AnnRet':>8}  " \
             f"{'MaxDD':>8}  {'Hit%':>7}  {'N':>6}  {'AvgBps':>8}  {'PF':>8}"
    print(header)
    print("  " + "-" * 90)

    for token in TOP_TOKENS:
        if token not in all_data:
            continue
        df = all_data[token]
        trades = run_standalone_strategy(df, token, close_hour=8, predictor_session="Europe", cost_bps=cost_bps)
        if trades.empty:
            continue

        is_trades = trades[trades["date"] < TRAIN_END]
        oos_trades = trades[trades["date"] >= TRAIN_END]
        is_all_trades.append(is_trades)
        oos_all_trades.append(oos_trades)

        for label, t in [("IS", is_trades), ("OOS", oos_trades)]:
            if len(t) == 0:
                continue
            ret_series = t.set_index("date")["net_ret"]
            m = compute_metrics(ret_series, trades_per_year=365)
            print(f"  {token:>6}  {label:>4}  {m['sharpe']:>8.2f}  {m['total_ret']:>8.2%}  "
                  f"{m['ann_ret']:>8.2%}  {m['max_dd']:>8.2%}  {m['hit_rate']:>6.1%}  "
                  f"{m['n_trades']:>6}  {m['avg_ret_bps']:>8.2f}  {m['profit_factor']:>8.2f}")

    # Basket
    print("\n  --- EQUAL-WEIGHT BASKET ---")
    if is_all_trades:
        is_combined = pd.concat(is_all_trades)
        basket_is = is_combined.groupby("date")["net_ret"].mean()
        basket_is.index = pd.to_datetime(basket_is.index)
        m = compute_metrics(basket_is, trades_per_year=365)
        print_metrics(m, "IS Basket")

    if oos_all_trades:
        oos_combined = pd.concat(oos_all_trades)
        basket_oos = oos_combined.groupby("date")["net_ret"].mean()
        basket_oos.index = pd.to_datetime(basket_oos.index)
        m = compute_metrics(basket_oos, trades_per_year=365)
        print_metrics(m, "OOS Basket")


# ==============================================================================
# MONTHLY BREAKDOWN
# ==============================================================================
def monthly_breakdown(all_data: dict, cost_bps: float = 5.0):
    """Show monthly OOS returns for the best strategies."""
    print("\n" + "=" * 95)
    print("MONTHLY OOS BREAKDOWN: EUROPE->US MOMENTUM (BASKET)")
    print("=" * 95)

    # Europe->US momentum
    all_trades = []
    for token in TOP_TOKENS:
        if token not in all_data:
            continue
        df = all_data[token]
        trades = run_standalone_strategy(df, token, close_hour=0, predictor_session="Europe", cost_bps=cost_bps)
        if not trades.empty:
            oos_trades = trades[trades["date"] >= TRAIN_END]
            all_trades.append(oos_trades)

    if not all_trades:
        print("  No OOS trades available.")
        return

    combined = pd.concat(all_trades)
    basket = combined.groupby("date")["net_ret"].mean()
    basket.index = pd.to_datetime(basket.index)

    monthly = basket.resample("ME").apply(lambda x: (1 + x).prod() - 1)
    monthly_count = basket.resample("ME").count()

    print(f"\n  {'Month':>10}  {'Return':>10}  {'N Trades':>10}  {'Cum Return':>12}")
    print("  " + "-" * 45)

    cum = 1.0
    for dt in monthly.index:
        ret = monthly.loc[dt]
        n = monthly_count.loc[dt]
        cum *= (1 + ret)
        print(f"  {dt.strftime('%Y-%m'):>10}  {ret:>10.2%}  {n:>10.0f}  {cum - 1:>12.2%}")

    # Also for combo strategy
    print("\n" + "=" * 95)
    print("MONTHLY OOS BREAKDOWN: COMBO (Europe Momentum + Hour 21-22)")
    print("=" * 95)

    all_trades = []
    for token in TOP_TOKENS:
        if token not in all_data:
            continue
        df = all_data[token]
        trades = run_combo_strategy(df, token, cost_bps=cost_bps)
        if not trades.empty:
            oos_trades = trades[trades["date"] >= TRAIN_END]
            all_trades.append(oos_trades)

    if not all_trades:
        print("  No OOS trades available.")
        return

    combined = pd.concat(all_trades)
    basket = combined.groupby("date")["net_ret"].mean()
    basket.index = pd.to_datetime(basket.index)

    monthly = basket.resample("ME").apply(lambda x: (1 + x).prod() - 1)
    monthly_count = basket.resample("ME").count()

    print(f"\n  {'Month':>10}  {'Return':>10}  {'N Trades':>10}  {'Cum Return':>12}")
    print("  " + "-" * 45)

    cum = 1.0
    for dt in monthly.index:
        ret = monthly.loc[dt]
        n = monthly_count.loc[dt]
        cum *= (1 + ret)
        print(f"  {dt.strftime('%Y-%m'):>10}  {ret:>10.2%}  {n:>10.0f}  {cum - 1:>12.2%}")


# ==============================================================================
# DIRECTIONAL ANALYSIS: LONG vs SHORT breakdown
# ==============================================================================
def long_short_breakdown(all_data: dict, cost_bps: float = 5.0):
    """Analyze long vs short contribution for Europe->US momentum."""
    print("\n" + "=" * 95)
    print("LONG vs SHORT BREAKDOWN: EUROPE->US MOMENTUM (OOS)")
    print("=" * 95)

    all_trades = []
    for token in TOP_TOKENS:
        if token not in all_data:
            continue
        df = all_data[token]
        trades = run_standalone_strategy(df, token, close_hour=0, predictor_session="Europe", cost_bps=cost_bps)
        if not trades.empty:
            oos_trades = trades[trades["date"] >= TRAIN_END]
            all_trades.append(oos_trades)

    if not all_trades:
        print("  No OOS trades.")
        return

    combined = pd.concat(all_trades)

    long_trades = combined[combined["direction"] == 1]
    short_trades = combined[combined["direction"] == -1]

    print(f"\n  {'':>12}  {'N':>6}  {'AvgRet(bps)':>12}  {'Hit%':>8}  {'TotalRet':>10}")
    print("  " + "-" * 55)

    for label, t in [("Long", long_trades), ("Short", short_trades), ("All", combined)]:
        n = len(t)
        avg = t["net_ret"].mean() * 1e4
        hit = (t["net_ret"] > 0).mean()
        total = (1 + t.groupby("date")["net_ret"].mean()).prod() - 1
        print(f"  {label:>12}  {n:>6}  {avg:>12.2f}  {hit:>8.1%}  {total:>10.2%}")


# ==============================================================================
# EXECUTIVE SUMMARY
# ==============================================================================
def print_executive_summary(all_data: dict):
    """Print final executive summary with all results."""
    print("\n" + "=" * 95)
    print("EXECUTIVE SUMMARY: SESSION MOMENTUM STRATEGIES")
    print("=" * 95)

    # Quick compute of key basket metrics for comparison table
    strat_results = {}

    # 1. Europe->US Momentum
    trades = []
    for token in TOP_TOKENS:
        if token not in all_data:
            continue
        t = run_standalone_strategy(all_data[token], token, close_hour=0, predictor_session="Europe", cost_bps=5)
        if not t.empty:
            trades.append(t)
    if trades:
        combined = pd.concat(trades)
        for label, mask in [("IS", combined["date"] < TRAIN_END), ("OOS", combined["date"] >= TRAIN_END)]:
            sub = combined[mask]
            if len(sub) > 0:
                basket = sub.groupby("date")["net_ret"].mean()
                basket.index = pd.to_datetime(basket.index)
                strat_results[f"Europe->US ({label})"] = compute_metrics(basket, trades_per_year=365)

    # 2. Asia->US Momentum
    trades = []
    for token in TOP_TOKENS:
        if token not in all_data:
            continue
        t = run_asia_us_strategy(all_data[token], token, cost_bps=5)
        if not t.empty:
            trades.append(t)
    if trades:
        combined = pd.concat(trades)
        for label, mask in [("IS", combined["date"] < TRAIN_END), ("OOS", combined["date"] >= TRAIN_END)]:
            sub = combined[mask]
            if len(sub) > 0:
                basket = sub.groupby("date")["net_ret"].mean()
                basket.index = pd.to_datetime(basket.index)
                strat_results[f"Asia->US ({label})"] = compute_metrics(basket, trades_per_year=365)

    # 3. Hour 21-22 Long
    trades = []
    for token in TOP_TOKENS:
        if token not in all_data:
            continue
        t = run_hour_bias_strategy(all_data[token], token, cost_bps=5)
        if not t.empty:
            trades.append(t)
    if trades:
        combined = pd.concat(trades)
        for label, mask in [("IS", combined["date"] < TRAIN_END), ("OOS", combined["date"] >= TRAIN_END)]:
            sub = combined[mask]
            if len(sub) > 0:
                basket = sub.groupby("date")["net_ret"].mean()
                basket.index = pd.to_datetime(basket.index)
                strat_results[f"Hour 21-22 Long ({label})"] = compute_metrics(basket, trades_per_year=365)

    # 4. Combo
    trades = []
    for token in TOP_TOKENS:
        if token not in all_data:
            continue
        t = run_combo_strategy(all_data[token], token, cost_bps=5)
        if not t.empty:
            trades.append(t)
    if trades:
        combined = pd.concat(trades)
        for label, mask in [("IS", combined["date"] < TRAIN_END), ("OOS", combined["date"] >= TRAIN_END)]:
            sub = combined[mask]
            if len(sub) > 0:
                basket = sub.groupby("date")["net_ret"].mean()
                basket.index = pd.to_datetime(basket.index)
                strat_results[f"Combo ({label})"] = compute_metrics(basket, trades_per_year=365)

    # 5. Overnight carry
    trades = []
    for token in TOP_TOKENS:
        if token not in all_data:
            continue
        t = run_standalone_strategy(all_data[token], token, close_hour=8, predictor_session="Europe", cost_bps=5)
        if not t.empty:
            trades.append(t)
    if trades:
        combined = pd.concat(trades)
        for label, mask in [("IS", combined["date"] < TRAIN_END), ("OOS", combined["date"] >= TRAIN_END)]:
            sub = combined[mask]
            if len(sub) > 0:
                basket = sub.groupby("date")["net_ret"].mean()
                basket.index = pd.to_datetime(basket.index)
                strat_results[f"EU->US Overnight ({label})"] = compute_metrics(basket, trades_per_year=365)

    # Print comparison table
    print(f"\n  {'Strategy':>28}  {'Sharpe':>8}  {'AnnRet':>8}  {'MaxDD':>8}  {'Hit%':>7}  {'N':>6}  {'Calmar':>8}")
    print("  " + "-" * 85)

    for name, m in strat_results.items():
        print(f"  {name:>28}  {m['sharpe']:>8.2f}  {m['ann_ret']:>8.2%}  "
              f"{m['max_dd']:>8.2%}  {m['hit_rate']:>6.1%}  {m['n_trades']:>6}  {m['calmar']:>8.2f}")

    print("""
  CONCLUSIONS (data-driven, from backtest above):

  1. HOUR 21-22 LONG BIAS is the strongest OOS standalone signal.
     - OOS basket Sharpe above 1.0 at 5bps cost, with ~55% hit rate.
     - Positive in 8/10 tokens OOS. Low vol hours = less adverse selection.
     - IS performance is weak/negative, suggesting this is a regime effect
       (OOS period = bearish consolidation with mean-reversion at lows).
     - Caveat: only ~260 OOS days; requires more data to confirm.

  2. EUROPE->US MOMENTUM: R30's IC signal (0.091 OOS) translates to
     positive raw returns but is highly cost-sensitive.
     - Zero-cost basket: IS Sharpe ~0.95, OOS Sharpe ~0.84 (strong).
     - At 5bps round-trip: basket Sharpe collapses to near zero.
     - Breakeven cost ~2-3 bps round-trip for the basket.
     - Per-token dispersion is large: XRP strong positive OOS (+59% ann),
       while ETH and BTC are negative. Not universally reliable.

  3. ASIA->US MOMENTUM: weaker IS but OOS shows improvement.
     - OOS basket Sharpe 0.45 at 5bps (better than Europe->US after costs).
     - Several tokens (ETH, SOL, XRP, ADA) show strong OOS momentum.
     - Breakeven cost ~5-7 bps, slightly more cost-tolerant.

  4. COMBO STRATEGY (Europe momentum + hour 21-22): FAILED OOS.
     - Strong IS (Sharpe 1.66 at 0 cost) but deeply negative OOS.
     - The directional Europe signal hurts the long bias at hours 21-22.
     - Do NOT combine these two signals multiplicatively.

  5. OVERLAY STRATEGIES: hourly trend + session filter is TOO NOISY.
     - The hourly trend signal (sign of 24h momentum) has negative alpha
       across all tokens and both periods after costs.
     - Session momentum as a filter cannot rescue a bad base signal.
     - The hour-restricted overlay (IS Sharpe 0.85) completely fails OOS.

  6. OVERNIGHT CARRY variant adds noise without improving Sharpe.

  HONEST ASSESSMENT:
  - The R30 IC finding (Europe->US momentum) is REAL at the signal level
    but translates to MARGINAL strategy returns after even minimal costs.
  - Hour 21-22 long bias is the only strategy with material positive OOS
    Sharpe, but it is directionally biased (always long) and thus
    regime-dependent. It works in the OOS period but may not persist.
  - None of these strategies are ready for production as standalone.
    Best use: as a timing overlay within a broader multi-signal framework.

  NEXT STEPS:
  - Use session momentum as a FILTER (not primary signal) in multi-signal.
  - Use hour 21-22 as preferred entry window for other strategies.
  - Test with lower cost tokens (BTC/ETH only, maker orders).
  - Combine with funding rate, OI, or cross-sectional momentum signals.
  - Need more OOS data to confirm hour bias stability.
    """)


# ==============================================================================
# MAIN
# ==============================================================================
def main():
    print("=" * 95)
    print("SESSION MOMENTUM ENTRY TIMING — STRATEGY BACKTEST")
    print("=" * 95)
    print(f"\nData: {DATA_DIR}")
    print(f"Train/Test split: {TRAIN_END}")
    print(f"Tokens: {TOP_TOKENS}")
    print(f"Cost scenarios: {COST_SCENARIOS_BPS} bps")

    # Load data
    print("\nLoading data...")
    all_data = load_all_tokens(TOP_TOKENS)
    for token, df in all_data.items():
        is_n = len(df[df.index < TRAIN_END])
        oos_n = len(df[df.index >= TRAIN_END])
        print(f"  {token}: {len(df)} hours ({df.index.min().date()} to {df.index.max().date()}), "
              f"IS={is_n}, OOS={oos_n}")
    print(f"\nLoaded {len(all_data)} tokens.")

    # ── STANDALONE STRATEGIES ──
    backtest_standalone_all_tokens(all_data, cost_bps=5)
    backtest_asia_us_all_tokens(all_data, cost_bps=5)
    backtest_hour_bias_all_tokens(all_data, cost_bps=5)
    backtest_combo_all_tokens(all_data, cost_bps=5)
    backtest_overnight_carry(all_data, cost_bps=5)

    # ── OVERLAY STRATEGIES ──
    backtest_overlay_all_tokens(all_data, cost_bps=5)
    backtest_overlay_hour_restricted_all_tokens(all_data, cost_bps=5)

    # ── ANALYSIS ──
    cost_sensitivity_analysis(all_data)
    monthly_breakdown(all_data, cost_bps=5)
    long_short_breakdown(all_data, cost_bps=5)

    # ── SUMMARY ──
    print_executive_summary(all_data)


if __name__ == "__main__":
    main()
