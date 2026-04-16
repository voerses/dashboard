#!/usr/bin/env python3
"""
s523c Rolling IC Recalibration + Regime Detection Backtest
==========================================================
Tests whether DYNAMIC per-token IC recalibration (rolling 90-day Spearman IC
recomputed quarterly/monthly) combined with BTC SMA-200 regime detection
improves s523c_growth across all market regimes (2022-2026).

6 variants tested:
  1. static_signs          — current s521_token_config.json signs (baseline)
  2. rolling_ic_quarterly  — quarterly-recalibrated IC signs, no regime overlay
  3. rolling_ic_monthly    — monthly-recalibrated IC signs, no regime overlay
  4. regime_only           — static signs + BTC SMA-200 regime flip
  5. rolling_quarterly+regime — both layers combined
  6. rolling_monthly+regime   — both layers combined
"""

import json
import os
import sys
import warnings
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

warnings.filterwarnings("ignore")

# ── Paths ──────────────────────────────────────────────────────────────────
BASE = Path("/workspace/crypto_backtest")
OHLCV_DIR = BASE / "data/perp/binance/1h_ohlcv"
METRICS_DIR = BASE / "data/alternative/binance_metrics/5min"
TOKEN_CONFIG = BASE / "data/alternative/s521_token_config.json"
RESULTS_OUT = BASE / "research/s523c_rolling_ic_results.json"

# ── Parameters ─────────────────────────────────────────────────────────────
ZSCORE_WINDOW = 30          # days, matching s523c
IC_LOOKBACK = 90            # days for trailing IC computation
THRESHOLD = 1.0             # composite z-score threshold for signals
HOLD_DAYS_LIST = [14]       # simplified hold period (days)
SLIPPAGE_BPS = 50           # 0.5% one-way slippage
FEE_BPS = 4                 # 4 bps per side
ROUND_TRIP_COST = (SLIPPAGE_BPS + FEE_BPS) * 2 / 10000  # ~0.0108
MAX_CONCURRENT = 30
BTC_SMA_PERIOD = 200 * 24   # 200-day SMA in hourly bars (4800)

BACKTEST_START = pd.Timestamp("2022-01-01")
BACKTEST_END = pd.Timestamp("2026-04-01")

# Quarterly boundaries for IC recalibration
QUARTERLY_DATES = pd.date_range("2021-07-01", "2026-04-01", freq="QS")
MONTHLY_DATES = pd.date_range("2021-07-01", "2026-04-01", freq="MS")


def load_token_config():
    with open(TOKEN_CONFIG) as f:
        return json.load(f)


def load_btc_regime():
    """Load BTC 1h OHLCV and compute daily regime (bull/bear based on SMA-200)."""
    btc_path = OHLCV_DIR / "BTC_perp_1h.csv"
    df = pd.read_csv(btc_path, usecols=["datetime", "close"])
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.sort_values("datetime").reset_index(drop=True)
    df["sma200"] = df["close"].rolling(BTC_SMA_PERIOD, min_periods=BTC_SMA_PERIOD).mean()
    # Resample to daily (use last hourly bar of each day)
    df["date"] = df["datetime"].dt.date
    daily = df.groupby("date").last().reset_index()
    daily["date"] = pd.to_datetime(daily["date"])
    daily["is_bull"] = daily["close"] >= daily["sma200"]
    return daily[["date", "close", "sma200", "is_bull"]].set_index("date")


def load_ohlcv_daily(token):
    """Load 1h OHLCV for a token and compute daily close + forward 1-day return."""
    fname = f"{token}_perp_1h.csv"
    path = OHLCV_DIR / fname
    if not path.exists():
        return None
    df = pd.read_csv(path, usecols=["datetime", "close"])
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.sort_values("datetime").reset_index(drop=True)
    # Forward 24h return: close[t+24] / close[t] - 1
    df["fwd_ret_24h"] = df["close"].shift(-24) / df["close"] - 1
    # Resample to daily (last hourly bar)
    df["date"] = df["datetime"].dt.date
    daily = df.groupby("date").agg({"close": "last", "fwd_ret_24h": "last"}).reset_index()
    daily["date"] = pd.to_datetime(daily["date"])
    daily = daily.set_index("date")
    return daily


def load_metrics_daily(token):
    """Load 5min metrics for token, resample to daily (last value)."""
    symbol = token + "USDT"
    path = METRICS_DIR / f"{symbol}_5min.parquet"
    if not path.exists():
        # Try alternate naming
        for suffix in ["_5min.parquet"]:
            alt = METRICS_DIR / f"{symbol}{suffix}"
            if alt.exists():
                path = alt
                break
        else:
            return None
    df = pd.read_parquet(path)
    df = df.sort_values("create_time").reset_index(drop=True)
    df["date"] = df["create_time"].dt.date

    # Resample to daily (last value of each day)
    daily = df.groupby("date").agg({
        "sum_open_interest": "last",
        "sum_toptrader_long_short_ratio": "last",
        "sum_taker_long_short_vol_ratio": "last",
    }).reset_index()
    daily["date"] = pd.to_datetime(daily["date"])
    daily = daily.set_index("date")
    daily.columns = ["oi", "pos", "flow"]
    return daily


def compute_zscores(metrics_daily):
    """Compute rolling z-scores for each metric (30-day window)."""
    result = pd.DataFrame(index=metrics_daily.index)
    for col in ["oi", "pos", "flow"]:
        series = metrics_daily[col]
        roll_mean = series.rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW).mean()
        roll_std = series.rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW).std()
        result[f"{col}_z"] = (series - roll_mean) / roll_std.replace(0, np.nan)
    return result


def compute_rolling_ic(zscores, fwd_returns, recalib_dates):
    """
    At each recalibration date, compute trailing 90-day Spearman IC
    between each metric z-score and forward 1-day return.
    Uses data from [date - 90d, date - 1d] to avoid look-ahead.

    Returns dict: {date: {metric: (sign, magnitude)}}
    """
    merged = zscores.join(fwd_returns, how="inner")
    ic_schedule = {}

    for cal_date in recalib_dates:
        cal_date = pd.Timestamp(cal_date)
        start = cal_date - pd.Timedelta(days=IC_LOOKBACK)
        end = cal_date - pd.Timedelta(days=1)
        window = merged.loc[start:end].dropna()

        if len(window) < 30:  # need minimum data
            continue

        ic_dict = {}
        for metric in ["oi_z", "pos_z", "flow_z"]:
            if window[metric].std() == 0 or window["fwd_ret_24h"].std() == 0:
                ic_dict[metric] = (0, 0.0)
                continue
            corr, _ = spearmanr(window[metric].values, window["fwd_ret_24h"].values)
            if np.isnan(corr):
                ic_dict[metric] = (0, 0.0)
            else:
                ic_dict[metric] = (int(np.sign(corr)) if corr != 0 else 0, abs(corr))
        ic_schedule[cal_date] = ic_dict

    return ic_schedule


def get_ic_signs_for_date(ic_schedule, date, recalib_dates):
    """Look up the most recent IC calibration for a given date."""
    # Find most recent calibration date <= date
    valid = [d for d in recalib_dates if d <= date and d in ic_schedule]
    if not valid:
        return None
    latest = max(valid)
    return ic_schedule[latest]


def run_simplified_backtest(daily_signals, hold_days=14):
    """
    Simplified backtest: enter on signal day at close + slippage, hold for
    hold_days, exit at close - slippage. Max MAX_CONCURRENT positions.

    daily_signals: DataFrame with columns 'date', 'token', 'direction' (+1 or -1), 'close'
    Returns: DataFrame of trades with PnL
    """
    trades = []
    active_positions = []  # list of (exit_date, token)

    signals_by_date = daily_signals.sort_values("date")
    all_dates = signals_by_date["date"].unique()

    for date in all_dates:
        # Remove expired positions
        active_positions = [(ed, tok) for ed, tok in active_positions if ed > date]

        # Get new signals for this date
        day_sigs = signals_by_date[signals_by_date["date"] == date]

        for _, sig in day_sigs.iterrows():
            if len(active_positions) >= MAX_CONCURRENT:
                break

            entry_price = sig["close"]
            exit_date = date + pd.Timedelta(days=hold_days)
            direction = sig["direction"]
            token = sig["token"]

            # Check if already in this token
            if any(tok == token for _, tok in active_positions):
                continue

            active_positions.append((exit_date, token))

            trades.append({
                "token": token,
                "entry_date": date,
                "exit_date": exit_date,
                "direction": direction,
                "entry_price": entry_price,
            })

    return trades


def resolve_trade_pnl(trades, ohlcv_cache):
    """Resolve exit prices from OHLCV data and compute PnL."""
    results = []
    for t in trades:
        token = t["token"]
        if token not in ohlcv_cache or ohlcv_cache[token] is None:
            continue
        ohlcv = ohlcv_cache[token]
        exit_date = t["exit_date"]
        # Find closest date >= exit_date
        valid_exits = ohlcv.index[ohlcv.index >= exit_date]
        if len(valid_exits) == 0:
            continue
        actual_exit = valid_exits[0]
        exit_price = ohlcv.loc[actual_exit, "close"]
        entry_price = t["entry_price"]
        direction = t["direction"]

        raw_ret = direction * (exit_price / entry_price - 1)
        net_ret = raw_ret - ROUND_TRIP_COST  # deduct costs

        results.append({
            "token": token,
            "entry_date": t["entry_date"],
            "exit_date": actual_exit,
            "direction": direction,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "raw_ret": raw_ret,
            "net_ret": net_ret,
        })
    return pd.DataFrame(results)


def compute_daily_returns(trade_results):
    """
    Compute daily portfolio returns from resolved trades.
    Equal-weight: each position gets 1/MAX_CONCURRENT of capital.
    Daily contribution = (per-trade daily return) / MAX_CONCURRENT.
    """
    if trade_results.empty:
        return pd.Series(dtype=float)

    all_dates = pd.date_range(BACKTEST_START, BACKTEST_END, freq="D")
    daily_rets = pd.Series(0.0, index=all_dates)

    for _, tr in trade_results.iterrows():
        entry = tr["entry_date"]
        exit_d = tr["exit_date"]
        if pd.isna(entry) or pd.isna(exit_d):
            continue
        hold_days = (exit_d - entry).days
        if hold_days <= 0:
            continue
        # Each position gets 1/MAX_CONCURRENT of portfolio
        # Distribute that position's return evenly across hold days
        daily_contribution = tr["net_ret"] / hold_days / MAX_CONCURRENT
        hold_range = pd.date_range(entry, exit_d - pd.Timedelta(days=1), freq="D")
        for d in hold_range:
            if d in daily_rets.index:
                daily_rets[d] += daily_contribution

    return daily_rets


def compute_stats(daily_rets, trade_results):
    """Compute performance statistics."""
    if daily_rets.empty or len(trade_results) == 0:
        return {"total_return": 0, "sharpe": 0, "max_dd": 0, "calmar": 0,
                "n_trades": 0, "win_rate": 0}

    # Equity curve
    equity = (1 + daily_rets).cumprod()
    total_ret = equity.iloc[-1] - 1 if len(equity) > 0 else 0

    # Sharpe (annualized)
    mean_daily = daily_rets.mean()
    std_daily = daily_rets.std()
    sharpe = (mean_daily / std_daily * np.sqrt(365)) if std_daily > 0 else 0

    # Max drawdown
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    max_dd = drawdown.min() if len(drawdown) > 0 else 0

    # Calmar
    years = len(daily_rets) / 365
    annual_ret = (1 + total_ret) ** (1 / years) - 1 if years > 0 else 0
    calmar = annual_ret / abs(max_dd) if max_dd != 0 else 0

    # Win rate
    n_trades = len(trade_results)
    win_rate = (trade_results["net_ret"] > 0).mean() if n_trades > 0 else 0

    return {
        "total_return": round(total_ret * 100, 2),
        "sharpe": round(sharpe, 3),
        "max_dd": round(max_dd * 100, 2),
        "calmar": round(calmar, 3),
        "n_trades": n_trades,
        "win_rate": round(win_rate * 100, 2),
    }


def compute_yearly_returns(daily_rets):
    """Compute per-year returns."""
    yearly = {}
    equity = (1 + daily_rets).cumprod()
    for year in range(2022, 2027):
        mask = daily_rets.index.year == year
        if mask.any():
            yr_equity = (1 + daily_rets[mask]).cumprod()
            yearly[str(year)] = round((yr_equity.iloc[-1] - 1) * 100, 2)
    return yearly


def compute_monthly_returns(daily_rets):
    """Compute monthly returns."""
    monthly = {}
    for period, grp in daily_rets.groupby(daily_rets.index.to_period("M")):
        eq = (1 + grp).cumprod()
        monthly[str(period)] = round((eq.iloc[-1] - 1) * 100, 2)
    return monthly


def main():
    print("=" * 80)
    print("s523c Rolling IC Recalibration + Regime Detection Backtest")
    print("=" * 80)

    # ── Load static config ────────────────────────────────────────────────
    token_config = load_token_config()
    tokens = list(token_config.keys())
    print(f"\nTokens in config: {len(tokens)}")

    # ── Load BTC regime ───────────────────────────────────────────────────
    print("Loading BTC regime data...")
    btc_regime = load_btc_regime()
    bull_pct = btc_regime.loc[BACKTEST_START:BACKTEST_END, "is_bull"].mean() * 100
    print(f"  Bull regime: {bull_pct:.1f}% of backtest period")
    print(f"  BTC SMA-200 available from: {btc_regime.dropna().index.min()}")

    # ── Load token data ───────────────────────────────────────────────────
    print("\nLoading token OHLCV and metrics data...")
    ohlcv_cache = {}
    metrics_cache = {}
    zscores_cache = {}
    fwd_ret_cache = {}
    loaded = 0

    for token in tokens:
        ohlcv = load_ohlcv_daily(token)
        metrics = load_metrics_daily(token)
        if ohlcv is not None and metrics is not None:
            ohlcv_cache[token] = ohlcv
            metrics_cache[token] = metrics
            zscores_cache[token] = compute_zscores(metrics)
            fwd_ret_cache[token] = ohlcv[["fwd_ret_24h"]]
            loaded += 1

    print(f"  Loaded {loaded}/{len(tokens)} tokens with both OHLCV and metrics")

    # If too many tokens, take top 50 by data length
    if loaded > 60:
        data_lengths = {t: len(zscores_cache[t].dropna()) for t in zscores_cache}
        top_tokens = sorted(data_lengths, key=data_lengths.get, reverse=True)[:50]
        print(f"  Subsampling to top 50 tokens by data availability")
    else:
        top_tokens = list(zscores_cache.keys())

    print(f"  Using {len(top_tokens)} tokens for backtest")

    # ── Compute rolling IC schedules per token ────────────────────────────
    print("\nComputing rolling IC schedules...")
    quarterly_ic = {}   # {token: {date: {metric: (sign, mag)}}}
    monthly_ic = {}

    for token in top_tokens:
        zs = zscores_cache[token]
        fr = fwd_ret_cache[token]
        quarterly_ic[token] = compute_rolling_ic(zs, fr, QUARTERLY_DATES)
        monthly_ic[token] = compute_rolling_ic(zs, fr, MONTHLY_DATES)

    # Stats on IC availability
    q_counts = [len(quarterly_ic[t]) for t in top_tokens]
    print(f"  Quarterly IC: avg {np.mean(q_counts):.1f} calibration points per token")
    m_counts = [len(monthly_ic[t]) for t in top_tokens]
    print(f"  Monthly IC:   avg {np.mean(m_counts):.1f} calibration points per token")

    # ── Generate signals for each variant ─────────────────────────────────
    print("\nGenerating signals for 6 variants...")

    variant_names = [
        "static_signs",
        "rolling_ic_quarterly",
        "rolling_ic_monthly",
        "regime_only",
        "rolling_quarterly+regime",
        "rolling_monthly+regime",
    ]

    all_dates = pd.date_range(BACKTEST_START, BACKTEST_END, freq="D")
    variant_signals = {v: [] for v in variant_names}

    for token in top_tokens:
        cfg = token_config[token]
        static_oi_sign = cfg["oi_sign"]
        static_pos_sign = cfg["pos_sign"]
        static_flow_sign = cfg["flow_sign"]
        w_oi = cfg["oi_weight"]
        w_pos = cfg["pos_weight"]
        w_flow = cfg["flow_weight"]

        zs = zscores_cache[token]
        ohlcv = ohlcv_cache[token]

        for date in all_dates:
            if date not in zs.index:
                continue

            # Get z-scores (use previous day for 1-day lag)
            prev_date = date - pd.Timedelta(days=1)
            if prev_date not in zs.index:
                continue

            oi_z = zs.loc[prev_date, "oi_z"] if "oi_z" in zs.columns else np.nan
            pos_z = zs.loc[prev_date, "pos_z"] if "pos_z" in zs.columns else np.nan
            flow_z = zs.loc[prev_date, "flow_z"] if "flow_z" in zs.columns else np.nan

            if np.isnan(oi_z) or np.isnan(pos_z) or np.isnan(flow_z):
                continue

            # Get close price for entry
            if date not in ohlcv.index:
                continue
            close = ohlcv.loc[date, "close"]

            # Get regime
            is_bull = True  # default
            if date in btc_regime.index and not pd.isna(btc_regime.loc[date, "is_bull"]):
                is_bull = bool(btc_regime.loc[date, "is_bull"])
            regime_flip = 1 if is_bull else -1

            # ── Variant 1: static_signs ───────────────────────────────
            comp_static = (w_oi * oi_z * static_oi_sign +
                           w_pos * pos_z * static_pos_sign +
                           w_flow * flow_z * static_flow_sign)

            if abs(comp_static) > THRESHOLD:
                direction = 1 if comp_static > THRESHOLD else -1
                variant_signals["static_signs"].append({
                    "date": date, "token": token, "direction": direction, "close": close
                })

            # ── Variant 4: regime_only ────────────────────────────────
            comp_regime = comp_static * regime_flip
            if abs(comp_regime) > THRESHOLD:
                direction = 1 if comp_regime > THRESHOLD else -1
                variant_signals["regime_only"].append({
                    "date": date, "token": token, "direction": direction, "close": close
                })

            # ── Rolling IC variants ───────────────────────────────────
            for freq_name, ic_data, recalib_dates in [
                ("rolling_ic_quarterly", quarterly_ic, QUARTERLY_DATES),
                ("rolling_ic_monthly", monthly_ic, MONTHLY_DATES),
            ]:
                ic_signs = get_ic_signs_for_date(
                    ic_data.get(token, {}), date, recalib_dates
                )
                if ic_signs is None:
                    continue

                r_oi_sign = ic_signs.get("oi_z", (0, 0))[0]
                r_pos_sign = ic_signs.get("pos_z", (0, 0))[0]
                r_flow_sign = ic_signs.get("flow_z", (0, 0))[0]

                comp_rolling = (w_oi * oi_z * r_oi_sign +
                                w_pos * pos_z * r_pos_sign +
                                w_flow * flow_z * r_flow_sign)

                # Without regime
                if abs(comp_rolling) > THRESHOLD:
                    direction = 1 if comp_rolling > THRESHOLD else -1
                    variant_signals[freq_name].append({
                        "date": date, "token": token, "direction": direction, "close": close
                    })

                # With regime
                comp_rolling_regime = comp_rolling * regime_flip
                regime_key = freq_name.replace("rolling_ic_", "rolling_") + "+regime"
                if abs(comp_rolling_regime) > THRESHOLD:
                    direction = 1 if comp_rolling_regime > THRESHOLD else -1
                    variant_signals[regime_key].append({
                        "date": date, "token": token, "direction": direction, "close": close
                    })

    for v in variant_names:
        print(f"  {v}: {len(variant_signals[v])} raw signals")

    # ── Run simplified backtests ──────────────────────────────────────────
    print("\nRunning simplified backtests (hold=14d)...")
    all_results = {}

    for variant in variant_names:
        sigs_df = pd.DataFrame(variant_signals[variant])
        if sigs_df.empty:
            all_results[variant] = {
                "stats": {"total_return": 0, "sharpe": 0, "max_dd": 0,
                          "calmar": 0, "n_trades": 0, "win_rate": 0},
                "yearly": {},
                "monthly": {},
            }
            continue

        sigs_df["date"] = pd.to_datetime(sigs_df["date"])
        trades = run_simplified_backtest(sigs_df, hold_days=14)
        trade_results = resolve_trade_pnl(trades, ohlcv_cache)

        if trade_results.empty:
            all_results[variant] = {
                "stats": {"total_return": 0, "sharpe": 0, "max_dd": 0,
                          "calmar": 0, "n_trades": 0, "win_rate": 0},
                "yearly": {},
                "monthly": {},
            }
            continue

        daily_rets = compute_daily_returns(trade_results)
        stats = compute_stats(daily_rets, trade_results)
        yearly = compute_yearly_returns(daily_rets)
        monthly = compute_monthly_returns(daily_rets)

        all_results[variant] = {
            "stats": stats,
            "yearly": yearly,
            "monthly": monthly,
        }

    # ── Regime stats ──────────────────────────────────────────────────────
    regime_stats = {}
    bt_regime = btc_regime.loc[BACKTEST_START:BACKTEST_END]
    bull_days = bt_regime["is_bull"].sum()
    bear_days = (~bt_regime["is_bull"]).sum()
    total_days = len(bt_regime)
    regime_stats["bull_pct"] = round(bull_days / total_days * 100, 1) if total_days > 0 else 0
    regime_stats["bear_pct"] = round(bear_days / total_days * 100, 1) if total_days > 0 else 0

    # ── IC recalibration stats ────────────────────────────────────────────
    ic_recalib_stats = {}
    # Count tokens with valid IC per quarter
    for cal_date in QUARTERLY_DATES:
        if cal_date < BACKTEST_START or cal_date > BACKTEST_END:
            continue
        n_valid = sum(1 for t in top_tokens
                      if cal_date in quarterly_ic.get(t, {}))
        ic_recalib_stats[str(cal_date.date())] = n_valid

    # ── Print comparison table ────────────────────────────────────────────
    print("\n" + "=" * 120)
    print("RESULTS COMPARISON TABLE")
    print("=" * 120)

    header = f"{'Variant':<30} {'Total%':>8} {'Sharpe':>8} {'MaxDD%':>8} {'Calmar':>8} {'Trades':>8} {'WinR%':>8}"
    yearly_years = ["2022", "2023", "2024", "2025", "2026"]
    for y in yearly_years:
        header += f" {y:>8}"
    print(header)
    print("-" * 120)

    for variant in variant_names:
        r = all_results[variant]
        s = r["stats"]
        y = r["yearly"]
        row = f"{variant:<30} {s['total_return']:>8.1f} {s['sharpe']:>8.3f} {s['max_dd']:>8.1f} {s['calmar']:>8.3f} {s['n_trades']:>8d} {s['win_rate']:>8.1f}"
        for yr in yearly_years:
            val = y.get(yr, 0)
            row += f" {val:>8.1f}"
        print(row)

    print(f"\nRegime: Bull {regime_stats['bull_pct']}% / Bear {regime_stats['bear_pct']}%")

    print(f"\nTokens with valid IC per quarterly recalibration:")
    for dt, n in sorted(ic_recalib_stats.items()):
        print(f"  {dt}: {n}/{len(top_tokens)} tokens")

    # ── Find winning variant ──────────────────────────────────────────────
    # Winner = best Sharpe
    best_variant = max(variant_names,
                       key=lambda v: all_results[v]["stats"]["sharpe"])
    print(f"\n{'=' * 80}")
    print(f"WINNING VARIANT: {best_variant}")
    print(f"  Sharpe: {all_results[best_variant]['stats']['sharpe']:.3f}")
    print(f"  Total Return: {all_results[best_variant]['stats']['total_return']:.1f}%")
    print(f"  Max DD: {all_results[best_variant]['stats']['max_dd']:.1f}%")
    print(f"{'=' * 80}")

    # ── Monthly breakdown of winner ───────────────────────────────────────
    print(f"\nMonthly returns for {best_variant}:")
    monthly = all_results[best_variant]["monthly"]
    if monthly:
        for period in sorted(monthly.keys()):
            ret = monthly[period]
            bar = "+" * max(0, int(ret)) + "-" * max(0, int(-ret))
            print(f"  {period}: {ret:>8.2f}%  {bar}")

    # ── IC flip analysis for winner ───────────────────────────────────────
    if "rolling" in best_variant:
        print(f"\nIC Sign Flip Analysis (quarterly recalibrations):")
        freq = "quarterly" if "quarterly" in best_variant else "monthly"
        ic_data = quarterly_ic if freq == "quarterly" else monthly_ic
        recalib = QUARTERLY_DATES if freq == "quarterly" else MONTHLY_DATES

        prev_signs = {}
        for cal_date in sorted(recalib):
            if cal_date < BACKTEST_START or cal_date > BACKTEST_END:
                # Still track signs for comparison
                for token in top_tokens:
                    if cal_date in ic_data.get(token, {}):
                        key = token
                        prev_signs[key] = ic_data[token][cal_date]
                continue

            flipped = 0
            entered = 0
            exited = 0
            for token in top_tokens:
                curr = ic_data.get(token, {}).get(cal_date)
                prev = prev_signs.get(token)
                if curr is not None and prev is None:
                    entered += 1
                elif curr is None and prev is not None:
                    exited += 1
                elif curr is not None and prev is not None:
                    # Check if any metric flipped
                    for metric in ["oi_z", "pos_z", "flow_z"]:
                        if (curr.get(metric, (0,))[0] != prev.get(metric, (0,))[0]):
                            flipped += 1
                            break

                if curr is not None:
                    prev_signs[token] = curr
                elif token in prev_signs:
                    del prev_signs[token]

            print(f"  {cal_date.date()}: {flipped} tokens flipped signs, "
                  f"{entered} entered, {exited} exited universe")

    # ── Also show second-best for comparison ──────────────────────────────
    sorted_variants = sorted(variant_names,
                             key=lambda v: all_results[v]["stats"]["sharpe"],
                             reverse=True)
    if len(sorted_variants) > 1:
        second = sorted_variants[1]
        print(f"\nSecond best: {second}")
        print(f"  Sharpe: {all_results[second]['stats']['sharpe']:.3f}")
        print(f"  Total Return: {all_results[second]['stats']['total_return']:.1f}%")

    # ── Save results ──────────────────────────────────────────────────────
    output = {
        "variants": {},
        "regime_stats": regime_stats,
        "ic_recalib_stats": ic_recalib_stats,
        "best_variant": best_variant,
        "tokens_used": top_tokens,
        "parameters": {
            "zscore_window": ZSCORE_WINDOW,
            "ic_lookback": IC_LOOKBACK,
            "threshold": THRESHOLD,
            "hold_days": 14,
            "round_trip_cost_bps": int(ROUND_TRIP_COST * 10000),
            "max_concurrent": MAX_CONCURRENT,
            "btc_sma_period_hours": BTC_SMA_PERIOD,
        }
    }

    for v in variant_names:
        r = all_results[v]
        output["variants"][v] = {
            "stats": r["stats"],
            "yearly_returns": r["yearly"],
            "monthly_returns": r["monthly"],
        }

    with open(RESULTS_OUT, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to {RESULTS_OUT}")


if __name__ == "__main__":
    main()
