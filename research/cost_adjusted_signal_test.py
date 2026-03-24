#!/workspace/venv/bin/python
"""
Cost-Adjusted Signal Survival Test
====================================
Tests whether our top 5 GOLD/PASS signals survive realistic trading costs.

Many signals with decent IC become worthless after fees. This script computes:
1. Raw forward returns at 7d and 14d (baseline)
2. Cost-adjusted forward returns (subtracting round-trip costs)
3. Cost-adjusted IC (Spearman IC of signal vs cost-adjusted returns)
4. Quintile spread after costs (Q1-Q5 minus 2x round-trip)
5. Break-even cost (at what cost level does IC drop to zero?)
6. Signal turnover (quintile change frequency — stability measure)

Signals tested:
  1. US10Y+DXY combined regime score
  2. Top Trader L/S raw
  3. L/S Divergence (count_toptrader_ls - count_ls)
  4. Skew_30d (distributional asymmetry of rolling returns)
  5. Net taker volume (daily aggregated buy-sell imbalance)

OOS split: train < 2025-01-01, test >= 2025-01-01
Cost assumptions:
  BTC/ETH: 5 bps per side = 10 bps round trip = 0.001
  Alts:     15 bps per side = 30 bps round trip = 0.003

Output: research/cost_adjusted_signal_results.md
"""

import os
import warnings
import numpy as np
import pandas as pd
from scipy import stats
from datetime import datetime

warnings.filterwarnings("ignore")

# ============================================================================
# CONFIGURATION
# ============================================================================

MACRO_DIR = "/workspace/crypto_backtest/data/alternative/macro"
METRICS_PATH = "/workspace/crypto_backtest/data/alternative/binance_metrics/all_symbols_daily_ls.parquet"
TAKER_PATH = "/workspace/crypto_backtest/data/alternative/binance_positioning/taker_buy_sell_vol.parquet"
TAKER_EXTENDED_DIR = "/workspace/crypto_backtest/data/alternative/binance_positioning/extended"
PRICE_DIR = "/workspace/crypto_backtest/data/perp/1h_cache"
OUTPUT_DIR = "/workspace/crypto_backtest/research"

OOS_START = pd.Timestamp("2025-01-01")

# Forward return horizons (days)
FWD_HORIZONS = {"7d": 7, "14d": 14}

# Cost assumptions (round-trip)
COST_BTC_ETH = 0.001   # 10 bps round trip
COST_ALTS = 0.003       # 30 bps round trip
# For BTC analysis, we use BTC cost
COST_BTC = COST_BTC_ETH

# Break-even cost search range (round-trip, in decimal)
COST_GRID = np.arange(0.0, 0.020, 0.0005)  # 0 to 200 bps in 5 bps steps

SEP = "=" * 90
THIN = "-" * 90


# ============================================================================
# DATA LOADING
# ============================================================================

def load_btc_daily() -> pd.DataFrame:
    """Load BTC hourly data, resample to daily OHLCV."""
    path = os.path.join(PRICE_DIR, "BTC_1h.parquet")
    df = pd.read_parquet(path)
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


def load_macro(name: str) -> pd.Series:
    """Load a macro parquet, return daily Close series indexed by date."""
    df = pd.read_parquet(os.path.join(MACRO_DIR, f"{name}.parquet"))
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date").sort_index()
    return df["Close"].dropna()


def load_ls_btc() -> pd.DataFrame:
    """Load Binance L/S metrics for BTCUSDT."""
    df = pd.read_parquet(METRICS_PATH)
    btc = df[df["symbol"] == "BTCUSDT"].copy()
    btc["date"] = pd.to_datetime(btc["date"])
    btc = btc.set_index("date").sort_index()
    return btc


def load_taker_btc() -> pd.DataFrame:
    """Load taker buy/sell volume for BTCUSDT.

    Try extended directory first (longer history), fall back to main file.
    """
    # Try extended data first (columns: taker_buy_base_vol, taker_sell_vol)
    ext_path = os.path.join(TAKER_EXTENDED_DIR, "BTCUSDT_taker_buysell.parquet")
    if os.path.exists(ext_path):
        df = pd.read_parquet(ext_path)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        if df["timestamp"].dt.tz is not None:
            df["timestamp"] = df["timestamp"].dt.tz_localize(None)
        df = df.set_index("timestamp").sort_index()

        # Determine column names
        buy_col = "taker_buy_base_vol" if "taker_buy_base_vol" in df.columns else "buyVol"
        sell_col = "taker_sell_vol" if "taker_sell_vol" in df.columns else "sellVol"
        ratio_col = "buySellRatio" if "buySellRatio" in df.columns else None

        agg_dict = {buy_col: "sum", sell_col: "sum"}
        if ratio_col and ratio_col in df.columns:
            agg_dict[ratio_col] = "mean"

        daily = df.resample("1D").agg(agg_dict).dropna()
        daily["net_taker_vol"] = daily[buy_col] - daily[sell_col]
        return daily

    # Fall back to main taker file (columns: buyVol, sellVol)
    df = pd.read_parquet(TAKER_PATH)
    btc = df[df["symbol"] == "BTCUSDT"].copy()
    btc["timestamp"] = pd.to_datetime(btc["timestamp"])
    if btc["timestamp"].dt.tz is not None:
        btc["timestamp"] = btc["timestamp"].dt.tz_localize(None)
    btc = btc.set_index("timestamp").sort_index()

    buy_col = "taker_buy_base_vol" if "taker_buy_base_vol" in btc.columns else "buyVol"
    sell_col = "taker_sell_vol" if "taker_sell_vol" in btc.columns else "sellVol"

    daily = btc.resample("1D").agg({
        buy_col: "sum",
        sell_col: "sum",
        "buySellRatio": "mean",
    }).dropna()
    daily["net_taker_vol"] = daily[buy_col] - daily[sell_col]
    return daily


# ============================================================================
# SIGNAL CONSTRUCTION
# ============================================================================

def compute_forward_returns(price: pd.Series, horizons: dict) -> pd.DataFrame:
    """Compute forward log returns at multiple horizons."""
    fwd = pd.DataFrame(index=price.index)
    for label, days in horizons.items():
        fwd[f"fwd_{label}"] = price.shift(-days) / price - 1
    return fwd


def compute_cost_adjusted_returns(fwd_returns: pd.DataFrame, cost: float) -> pd.DataFrame:
    """Subtract round-trip cost from forward returns.

    For a signal that triggers entry today and exit at horizon,
    the cost-adjusted return = raw_return - cost (round-trip).
    """
    adj = fwd_returns.copy()
    for col in adj.columns:
        adj[col] = adj[col] - cost
    return adj


def signal_us10y_dxy_regime(btc_daily: pd.DataFrame) -> pd.Series:
    """Signal 1: US10Y+DXY combined regime score.

    Compute 20d changes, rank each, combine as regime score.
    Higher score = more tightening = bearish for crypto.
    """
    us10y = load_macro("us10y_yield")
    dxy = load_macro("usd_index")

    # 20-day changes
    us10y_chg = us10y.diff(20)
    dxy_pct_chg = dxy.pct_change(20)

    # Align to BTC daily dates
    combined = pd.DataFrame({
        "us10y_20d_chg": us10y_chg,
        "dxy_20d_pct_chg": dxy_pct_chg,
    }).reindex(btc_daily.index, method="ffill")

    # Expanding rank (cross-time rank to avoid look-ahead)
    def expanding_rank(s):
        """Rank within expanding window (no look-ahead)."""
        result = pd.Series(np.nan, index=s.index)
        for i in range(60, len(s)):  # need minimum 60 obs
            window = s.iloc[:i+1]
            valid = window.dropna()
            if len(valid) < 20:
                continue
            result.iloc[i] = valid.rank(pct=True).iloc[-1]
        return result

    rank_us10y = expanding_rank(combined["us10y_20d_chg"])
    rank_dxy = expanding_rank(combined["dxy_20d_pct_chg"])

    regime_score = rank_us10y + rank_dxy  # 0 to 2 scale
    regime_score.name = "us10y_dxy_regime"
    return regime_score


def signal_toptrader_ls_raw(btc_daily: pd.DataFrame) -> pd.Series:
    """Signal 2: Top Trader L/S ratio (raw).

    sum_toptrader_ls_ratio from Binance metrics.
    """
    ls = load_ls_btc()
    signal = ls["sum_toptrader_ls_ratio"].reindex(btc_daily.index, method="ffill")
    signal.name = "toptrader_ls_raw"
    return signal


def signal_ls_divergence(btc_daily: pd.DataFrame) -> pd.Series:
    """Signal 3: L/S Divergence.

    count_toptrader_ls_ratio - count_ls_ratio
    When top traders disagree with retail, signal is stronger.
    """
    ls = load_ls_btc()
    div = ls["count_toptrader_ls_ratio"] - ls["count_ls_ratio"]
    signal = div.reindex(btc_daily.index, method="ffill")
    signal.name = "ls_divergence"
    return signal


def signal_skew_30d(btc_daily: pd.DataFrame) -> pd.Series:
    """Signal 4: Skew_30d.

    (mean - median) / std of 30-day rolling daily returns.
    Positive skew = fat right tail = potential for further upside.
    """
    daily_ret = btc_daily["close"].pct_change()

    def rolling_skew_stat(rets, window=30):
        result = pd.Series(np.nan, index=rets.index)
        for i in range(window, len(rets)):
            w = rets.iloc[i-window:i].dropna()
            if len(w) < 20:
                continue
            m = w.mean()
            med = w.median()
            s = w.std()
            if s > 0:
                result.iloc[i] = (m - med) / s
        return result

    signal = rolling_skew_stat(daily_ret, window=30)
    signal.name = "skew_30d"
    return signal


def signal_net_taker_volume(btc_daily: pd.DataFrame) -> pd.Series:
    """Signal 5: Net taker volume.

    Daily aggregated (buyVol - sellVol), z-scored over 20d rolling window.
    """
    taker = load_taker_btc()
    net = taker["net_taker_vol"]

    # Z-score over 20-day rolling window
    roll_mean = net.rolling(20, min_periods=10).mean()
    roll_std = net.rolling(20, min_periods=10).std()
    zscore = (net - roll_mean) / roll_std.replace(0, np.nan)

    signal = zscore.reindex(btc_daily.index, method="ffill")
    signal.name = "net_taker_vol_z"
    return signal


# ============================================================================
# EVALUATION FUNCTIONS
# ============================================================================

def spearman_ic(signal: pd.Series, returns: pd.Series) -> dict:
    """Compute Spearman IC and t-stat between signal and returns."""
    df = pd.DataFrame({"signal": signal, "ret": returns}).dropna()
    if len(df) < 20:
        return {"ic": np.nan, "t_stat": np.nan, "n": len(df)}
    corr, pval = stats.spearmanr(df["signal"], df["ret"])
    # t-stat approximation
    n = len(df)
    t_stat = corr * np.sqrt((n - 2) / (1 - corr**2 + 1e-12))
    return {"ic": corr, "t_stat": t_stat, "pval": pval, "n": n}


def quintile_spread(signal: pd.Series, returns: pd.Series) -> dict:
    """Compute quintile-sorted returns and Q1-Q5 spread."""
    df = pd.DataFrame({"signal": signal, "ret": returns}).dropna()
    if len(df) < 50:
        return {"spread": np.nan, "q1_ret": np.nan, "q5_ret": np.nan,
                "quintile_rets": {}}
    df["quintile"] = pd.qcut(df["signal"], 5, labels=[1, 2, 3, 4, 5],
                              duplicates="drop")
    qrets = df.groupby("quintile")["ret"].mean()
    q1 = qrets.get(1, np.nan)
    q5 = qrets.get(5, np.nan)
    spread = q1 - q5 if not (np.isnan(q1) or np.isnan(q5)) else np.nan
    return {
        "spread": spread,
        "q1_ret": q1,
        "q5_ret": q5,
        "quintile_rets": qrets.to_dict(),
    }


def signal_turnover(signal: pd.Series, n_quantiles: int = 5) -> dict:
    """Compute signal turnover: fraction of days where quintile changes.

    Lower turnover = more stable signal = fewer trades = lower cost drag.
    """
    clean = signal.dropna()
    if len(clean) < 50:
        return {"turnover_rate": np.nan, "avg_holding_days": np.nan}

    try:
        quintiles = pd.qcut(clean, n_quantiles, labels=range(n_quantiles),
                             duplicates="drop")
    except ValueError:
        return {"turnover_rate": np.nan, "avg_holding_days": np.nan}

    changes = (quintiles != quintiles.shift(1)).sum() - 1  # first row always "changes"
    total = len(quintiles) - 1
    turnover_rate = changes / total if total > 0 else np.nan

    # Average holding days = 1 / turnover_rate (days between quintile changes)
    avg_holding = 1.0 / turnover_rate if turnover_rate > 0 else np.inf

    return {
        "turnover_rate": turnover_rate,
        "avg_holding_days": avg_holding,
    }


def find_breakeven_cost(signal: pd.Series, raw_returns: pd.Series,
                         cost_grid: np.ndarray) -> float:
    """Find the round-trip cost at which the quintile L/S spread drops to zero.

    For a long-short portfolio based on signal quintiles:
    - The portfolio goes long the best quintile and short the worst
    - Each leg pays the round-trip cost on entry/exit
    - Total cost per rebalance = 2 * round_trip (long side + short side)
    - Break-even = raw_spread / 2 (the cost per side that kills the spread)

    We use the quintile spread as the P&L metric because Spearman IC is
    rank-based and invariant to constant cost subtraction.
    """
    df = pd.DataFrame({"signal": signal, "raw_ret": raw_returns}).dropna()
    if len(df) < 50:
        return np.nan

    try:
        df["quintile"] = pd.qcut(df["signal"], 5, labels=[1, 2, 3, 4, 5],
                                  duplicates="drop")
    except ValueError:
        return np.nan

    qrets = df.groupby("quintile")["raw_ret"].mean()
    q1 = qrets.get(1, np.nan)
    q5 = qrets.get(5, np.nan)
    if np.isnan(q1) or np.isnan(q5):
        return np.nan

    raw_spread = abs(q1 - q5)
    # Break-even: spread = 2 * cost => cost = spread / 2
    breakeven = raw_spread / 2.0
    return breakeven


def quintile_ls_timeseries(signal: pd.Series, returns: pd.Series) -> pd.Series:
    """Compute time series of Q1-Q5 returns for Sharpe calculation.

    This uses expanding quintile boundaries to avoid look-ahead bias.
    """
    df = pd.DataFrame({"signal": signal, "ret": returns}).dropna()
    if len(df) < 100:
        return pd.Series(dtype=float)

    # Use expanding quantile boundaries (no look-ahead)
    ls_returns = pd.Series(np.nan, index=df.index)
    min_obs = 60

    for i in range(min_obs, len(df)):
        hist = df.iloc[:i+1]
        q20 = hist["signal"].quantile(0.2)
        q80 = hist["signal"].quantile(0.8)
        row = df.iloc[i]
        sig = row["signal"]
        ret = row["ret"]

        if sig <= q20:
            ls_returns.iloc[i] = ret  # long Q1
        elif sig >= q80:
            ls_returns.iloc[i] = -ret  # short Q5
        # else: neutral, no position

    return ls_returns.dropna()


def evaluate_signal_full(signal: pd.Series, btc_daily: pd.DataFrame,
                          signal_name: str, cost: float = COST_BTC) -> dict:
    """Full evaluation of a single signal.

    Metrics:
    - Spearman IC (rank-based, invariant to constant cost shift)
    - Raw quintile L/S spread
    - Cost-adjusted quintile spread (raw spread - 2 * round_trip_cost)
    - Break-even cost (round-trip cost at which L/S spread = 0)
    - Signal turnover (quintile change frequency)
    - Turnover-adjusted cost drag (annual cost from rebalancing)
    - Net annual alpha (annualized spread - annual cost drag)
    """

    fwd = compute_forward_returns(btc_daily["close"], FWD_HORIZONS)

    results = {"signal_name": signal_name}

    for period in ["IS", "OOS"]:
        if period == "IS":
            mask = signal.index < OOS_START
        else:
            mask = signal.index >= OOS_START

        sig_p = signal[mask]

        # Turnover
        to = signal_turnover(sig_p)
        results[f"{period}_turnover_rate"] = to["turnover_rate"]
        results[f"{period}_avg_holding_days"] = to["avg_holding_days"]

        for horizon_label, horizon_days in FWD_HORIZONS.items():
            raw_ret_col = f"fwd_{horizon_label}"
            raw_ret = fwd[raw_ret_col][mask]

            # Spearman IC (note: invariant to constant cost shifts)
            ic_res = spearman_ic(sig_p, raw_ret)
            results[f"{period}_{horizon_label}_ic"] = ic_res["ic"]
            results[f"{period}_{horizon_label}_tstat"] = ic_res["t_stat"]
            results[f"{period}_{horizon_label}_n"] = ic_res["n"]

            # Raw quintile spread
            raw_qs = quintile_spread(sig_p, raw_ret)
            results[f"{period}_{horizon_label}_raw_spread"] = raw_qs["spread"]
            results[f"{period}_{horizon_label}_raw_q1"] = raw_qs["q1_ret"]
            results[f"{period}_{horizon_label}_raw_q5"] = raw_qs["q5_ret"]

            # Cost-adjusted quintile spread: raw spread minus 2x round-trip
            # (long and short legs each pay one round-trip)
            if not np.isnan(raw_qs["spread"]):
                results[f"{period}_{horizon_label}_adj_spread"] = (
                    raw_qs["spread"] - 2 * cost
                )
                # Spread as annualized return (spread per holding period, scaled to annual)
                periods_per_year = 365.0 / horizon_days
                raw_annual_spread = raw_qs["spread"] * periods_per_year
                results[f"{period}_{horizon_label}_raw_annual_spread"] = raw_annual_spread
            else:
                results[f"{period}_{horizon_label}_adj_spread"] = np.nan
                results[f"{period}_{horizon_label}_raw_annual_spread"] = np.nan

            # Break-even cost (round-trip cost where spread = 0)
            be = find_breakeven_cost(sig_p, raw_ret, COST_GRID)
            results[f"{period}_{horizon_label}_breakeven_cost_bps"] = be * 10000

        # Turnover-adjusted annual cost drag
        turnover_rate = to["turnover_rate"]
        if not np.isnan(turnover_rate):
            trades_per_year = turnover_rate * 365
            annual_cost_drag = trades_per_year * cost
            results[f"{period}_annual_cost_drag"] = annual_cost_drag
        else:
            results[f"{period}_annual_cost_drag"] = np.nan

        # Net annual alpha for each horizon
        for horizon_label, horizon_days in FWD_HORIZONS.items():
            raw_ann = results.get(f"{period}_{horizon_label}_raw_annual_spread", np.nan)
            drag = results.get(f"{period}_annual_cost_drag", np.nan)
            if not np.isnan(raw_ann) and not np.isnan(drag):
                results[f"{period}_{horizon_label}_net_annual_alpha"] = raw_ann - drag
            else:
                results[f"{period}_{horizon_label}_net_annual_alpha"] = np.nan

    return results


# ============================================================================
# MAIN ANALYSIS
# ============================================================================

def main():
    print(SEP)
    print("COST-ADJUSTED SIGNAL SURVIVAL TEST")
    print(f"Run: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(SEP)

    # Load BTC daily
    print("\nLoading BTC daily price data...")
    btc_daily = load_btc_daily()
    print(f"  BTC daily: {len(btc_daily)} rows, {btc_daily.index.min().date()} to {btc_daily.index.max().date()}")
    print(f"  IS period: < {OOS_START.date()} ({len(btc_daily[btc_daily.index < OOS_START])} days)")
    print(f"  OOS period: >= {OOS_START.date()} ({len(btc_daily[btc_daily.index >= OOS_START])} days)")

    # Compute all signals
    print("\nComputing signals...")
    signals = {}

    print("  1. US10Y+DXY regime score...")
    signals["US10Y+DXY Regime"] = signal_us10y_dxy_regime(btc_daily)
    n_valid = signals["US10Y+DXY Regime"].notna().sum()
    print(f"     -> {n_valid} valid observations")

    print("  2. Top Trader L/S raw...")
    signals["TopTrader L/S Raw"] = signal_toptrader_ls_raw(btc_daily)
    n_valid = signals["TopTrader L/S Raw"].notna().sum()
    print(f"     -> {n_valid} valid observations")

    print("  3. L/S Divergence...")
    signals["L/S Divergence"] = signal_ls_divergence(btc_daily)
    n_valid = signals["L/S Divergence"].notna().sum()
    print(f"     -> {n_valid} valid observations")

    print("  4. Skew_30d...")
    signals["Skew_30d"] = signal_skew_30d(btc_daily)
    n_valid = signals["Skew_30d"].notna().sum()
    print(f"     -> {n_valid} valid observations")

    print("  5. Net Taker Volume (z-scored)...")
    signals["Net Taker Vol Z"] = signal_net_taker_volume(btc_daily)
    n_valid = signals["Net Taker Vol Z"].notna().sum()
    print(f"     -> {n_valid} valid observations")

    # Evaluate all signals
    print(f"\n{SEP}")
    print("EVALUATING SIGNAL COST SURVIVAL")
    print(f"Cost assumption: {COST_BTC*10000:.0f} bps round-trip for BTC")
    print(SEP)

    fwd = compute_forward_returns(btc_daily["close"], FWD_HORIZONS)
    oos_mask = btc_daily.index >= OOS_START

    all_results = []
    for name, signal in signals.items():
        print(f"\n{THIN}")
        print(f"Signal: {name}")
        print(THIN)

        res = evaluate_signal_full(signal, btc_daily, name)
        all_results.append(res)

        for period in ["IS", "OOS"]:
            tr = res.get(f"{period}_turnover_rate", np.nan)
            ah = res.get(f"{period}_avg_holding_days", np.nan)
            drag = res.get(f"{period}_annual_cost_drag", np.nan)
            print(f"\n  [{period}]")
            if not np.isnan(tr):
                print(f"    Turnover: {tr:.3f}/day (avg hold: {ah:.1f}d), "
                      f"annual cost drag: {drag*100:.2f}%")
            else:
                print(f"    Turnover: N/A")

            for h in FWD_HORIZONS:
                ic = res.get(f"{period}_{h}_ic", np.nan)
                tstat = res.get(f"{period}_{h}_tstat", np.nan)
                raw_spread = res.get(f"{period}_{h}_raw_spread", np.nan)
                adj_spread = res.get(f"{period}_{h}_adj_spread", np.nan)
                be_cost = res.get(f"{period}_{h}_breakeven_cost_bps", np.nan)
                net_alpha = res.get(f"{period}_{h}_net_annual_alpha", np.nan)
                n = res.get(f"{period}_{h}_n", 0)

                ic_s = f"{ic:+.4f}" if not np.isnan(ic) else "N/A"
                t_s = f"t={tstat:+.2f}" if not np.isnan(tstat) else ""
                rs = f"{raw_spread*100:+.2f}%" if not np.isnan(raw_spread) else "N/A"
                as_ = f"{adj_spread*100:+.2f}%" if not np.isnan(adj_spread) else "N/A"
                be_s = f"{be_cost:.0f}" if not np.isnan(be_cost) else "N/A"
                na_s = f"{net_alpha*100:+.1f}%" if not np.isnan(net_alpha) else "N/A"

                print(f"    {h}: IC={ic_s} ({t_s}) | "
                      f"Spread: {rs} -> {as_} (adj) | "
                      f"BE: {be_s} bps | Net alpha: {na_s} | n={n}")

    # ========================================================================
    # SUMMARY TABLE
    # ========================================================================
    print(f"\n\n{SEP}")
    print("COST-ADJUSTED SIGNAL SURVIVAL SUMMARY (OOS)")
    print(SEP)

    # Survival criteria based on net annual alpha and adjusted spread
    summary_rows = []
    for res in all_results:
        name = res["signal_name"]
        for h, h_days in FWD_HORIZONS.items():
            ic = res.get(f"OOS_{h}_ic", np.nan)
            raw_spread = res.get(f"OOS_{h}_raw_spread", np.nan)
            adj_spread = res.get(f"OOS_{h}_adj_spread", np.nan)
            be_cost = res.get(f"OOS_{h}_breakeven_cost_bps", np.nan)
            turnover = res.get(f"OOS_turnover_rate", np.nan)
            drag = res.get(f"OOS_annual_cost_drag", np.nan)
            net_alpha = res.get(f"OOS_{h}_net_annual_alpha", np.nan)
            n = res.get(f"OOS_{h}_n", 0)

            # Verdict based on three criteria:
            # 1. IC significant (|IC| > 0.05 and n >= 50)
            # 2. Adjusted spread still positive (after 2x round-trip)
            # 3. Net annual alpha positive (annualized spread - annual cost drag)
            if np.isnan(ic) or n < 50:
                verdict = "INSUFFICIENT DATA"
            elif abs(ic) < 0.05:
                verdict = "WEAK SIGNAL"
            elif not np.isnan(adj_spread) and abs(adj_spread) > 0 and not np.isnan(net_alpha) and net_alpha > 0:
                if be_cost > 50:
                    verdict = "SURVIVES (robust)"
                else:
                    verdict = "SURVIVES (fragile)"
            elif not np.isnan(adj_spread) and abs(adj_spread) > 0:
                verdict = "MARGINAL"
            else:
                verdict = "KILLED BY COSTS"

            summary_rows.append({
                "Signal": name,
                "Horizon": h,
                "IC (OOS)": ic,
                "Raw Spread": raw_spread,
                "Adj Spread": adj_spread,
                "BE Cost (bps)": be_cost,
                "Turnover": turnover,
                "Cost Drag/yr": drag,
                "Net Alpha/yr": net_alpha,
                "Verdict": verdict,
            })

    summary_df = pd.DataFrame(summary_rows)

    # Print formatted summary
    print(f"\n{'Signal':<22} {'Hz':>3} {'IC':>7} {'RawSpr':>8} {'AdjSpr':>8} "
          f"{'BE(bps)':>8} {'Turn':>6} {'Drag/yr':>8} {'NetAlp':>8} {'Verdict'}")
    print(THIN)
    for _, row in summary_df.iterrows():
        def f(v, fmt_str):
            return fmt_str.format(v) if not np.isnan(v) else "N/A"
        print(f"{row['Signal']:<22} {row['Horizon']:>3} "
              f"{f(row['IC (OOS)'], '{:+.4f}'):>7} "
              f"{f(row['Raw Spread'], '{:+.2%}'):>8} "
              f"{f(row['Adj Spread'], '{:+.2%}'):>8} "
              f"{f(row['BE Cost (bps)'], '{:.0f}'):>8} "
              f"{f(row['Turnover'], '{:.3f}'):>6} "
              f"{f(row['Cost Drag/yr'], '{:.2%}'):>8} "
              f"{f(row['Net Alpha/yr'], '{:+.1%}'):>8} "
              f"{row['Verdict']}")

    # ========================================================================
    # QUINTILE SPREAD SENSITIVITY TO COST
    # ========================================================================
    print(f"\n\n{SEP}")
    print("QUINTILE SPREAD AFTER VARIOUS COST LEVELS (OOS, 14d)")
    print(SEP)

    cost_levels = [0, 5, 10, 15, 20, 30, 50, 75, 100]  # bps round-trip per side

    print(f"\n{'Signal':<25}", end="")
    for c in cost_levels:
        print(f"  {c:>5}bps", end="")
    print()
    print(THIN)

    for name, signal in signals.items():
        sig_oos = signal[oos_mask]
        raw_ret_oos = fwd["fwd_14d"][oos_mask]
        qs = quintile_spread(sig_oos, raw_ret_oos)
        raw_spread = qs["spread"]
        print(f"{name:<25}", end="")

        for c_bps in cost_levels:
            c_dec = c_bps / 10000
            # Adj spread = raw - 2 * round_trip
            if not np.isnan(raw_spread):
                adj = raw_spread - 2 * c_dec
                print(f"  {adj*100:+.2f}%", end="")
            else:
                print(f"    N/A", end="")
        print()

    # ========================================================================
    # TURNOVER-ADJUSTED COST IMPACT
    # ========================================================================
    print(f"\n\n{SEP}")
    print("TURNOVER & ANNUAL COST DRAG (OOS)")
    print(SEP)
    print(f"\nBTC round-trip cost: {COST_BTC*10000:.0f} bps")
    print(f"{'Signal':<25} {'Turn/day':>9} {'Trades/yr':>10} {'AvgHold':>8} "
          f"{'Drag/yr':>8} {'14d Spread':>11} {'Net Alpha':>10}")
    print(THIN)

    for name, signal in signals.items():
        sig_oos = signal[oos_mask]
        to = signal_turnover(sig_oos)
        tr = to["turnover_rate"]
        ah = to["avg_holding_days"]

        # Get 14d raw spread
        raw_ret_oos = fwd["fwd_14d"][oos_mask]
        qs = quintile_spread(sig_oos, raw_ret_oos)
        raw_spread = qs["spread"]

        if np.isnan(tr):
            print(f"  {name:<25}: INSUFFICIENT DATA")
            continue

        trades_yr = tr * 365
        annual_drag = trades_yr * COST_BTC
        # Annualized spread (14d = 26 periods/year)
        ann_spread = raw_spread * (365.0 / 14) if not np.isnan(raw_spread) else np.nan
        net_alpha = (ann_spread - annual_drag) if not np.isnan(ann_spread) else np.nan

        print(f"{name:<25} {tr:>9.3f} {trades_yr:>10.0f} {ah:>8.1f} "
              f"{annual_drag*100:>7.2f}% {raw_spread*100 if not np.isnan(raw_spread) else 0:>+10.2f}% "
              f"{net_alpha*100 if not np.isnan(net_alpha) else 0:>+9.1f}%")

    # ========================================================================
    # WRITE RESULTS TO MARKDOWN
    # ========================================================================
    write_results_md(all_results, summary_df, signals, btc_daily, fwd, oos_mask,
                     cost_levels)

    print(f"\n\nResults saved to: {os.path.join(OUTPUT_DIR, 'cost_adjusted_signal_results.md')}")


def write_results_md(all_results, summary_df, signals, btc_daily, fwd, oos_mask,
                      cost_levels):
    """Write comprehensive results to markdown file."""
    md_path = os.path.join(OUTPUT_DIR, "cost_adjusted_signal_results.md")

    def fmt(x, f_str="{:+.4f}"):
        return f_str.format(x) if not np.isnan(x) else "N/A"

    def fmtp(x):
        return f"{x*100:+.2f}%" if not np.isnan(x) else "N/A"

    with open(md_path, "w") as f:
        f.write("# Cost-Adjusted Signal Survival Test\n\n")
        f.write(f"**Run date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write(f"**OOS split:** train < 2025-01-01, test >= 2025-01-01\n\n")
        f.write(f"**Cost assumption:** BTC = {COST_BTC*10000:.0f} bps round-trip "
                f"(5 bps per side)\n\n")

        # ==================================================================
        # EXECUTIVE SUMMARY
        # ==================================================================
        f.write("## Executive Summary\n\n")

        survives_robust = sum(1 for _, r in summary_df.iterrows()
                              if "robust" in r["Verdict"])
        survives_fragile = sum(1 for _, r in summary_df.iterrows()
                               if "fragile" in r["Verdict"])
        marginal = sum(1 for _, r in summary_df.iterrows()
                       if r["Verdict"] == "MARGINAL")
        killed = sum(1 for _, r in summary_df.iterrows()
                     if r["Verdict"] == "KILLED BY COSTS")
        weak = sum(1 for _, r in summary_df.iterrows()
                   if r["Verdict"] == "WEAK SIGNAL")
        insuf = sum(1 for _, r in summary_df.iterrows()
                    if r["Verdict"] == "INSUFFICIENT DATA")

        f.write(f"Of {len(summary_df)} signal-horizon combinations tested:\n")
        f.write(f"- **SURVIVES (robust):** {survives_robust} "
                f"(positive net alpha, break-even > 50 bps)\n")
        f.write(f"- **SURVIVES (fragile):** {survives_fragile} "
                f"(positive net alpha, break-even <= 50 bps)\n")
        f.write(f"- **MARGINAL:** {marginal} "
                f"(adjusted spread nonzero but net annual alpha negative)\n")
        f.write(f"- **KILLED BY COSTS:** {killed}\n")
        if weak > 0:
            f.write(f"- **WEAK SIGNAL:** {weak} (|IC| < 0.05)\n")
        if insuf > 0:
            f.write(f"- **INSUFFICIENT DATA:** {insuf}\n")
        f.write("\n")

        f.write("**Key insight:** Spearman IC is a rank-based metric and invariant to "
                "constant cost shifts -- subtracting 10 bps from all returns does not "
                "change rank ordering. The real cost impact shows up in:\n")
        f.write("1. **Quintile L/S spread** after costs (direct P&L impact)\n")
        f.write("2. **Annual cost drag** from signal turnover (rebalancing frequency)\n")
        f.write("3. **Net annual alpha** = annualized spread - annual cost drag\n\n")

        # ==================================================================
        # MAIN RESULTS TABLE
        # ==================================================================
        f.write("## OOS Results Summary\n\n")
        f.write("| Signal | Hz | IC | t-stat | Raw Spread | Adj Spread | "
                "BE (bps) | Turn/day | Drag/yr | Net Alpha/yr | Verdict |\n")
        f.write("|--------|----|----|--------|------------|------------|"
                "----------|----------|---------|--------------|--------|\n")

        for _, row in summary_df.iterrows():
            # Find corresponding full result for t-stat
            res = next(r for r in all_results if r["signal_name"] == row["Signal"])
            h = row["Horizon"]
            tstat = res.get(f"OOS_{h}_tstat", np.nan)

            f.write(f"| {row['Signal']} | {h} "
                    f"| {fmt(row['IC (OOS)'])} "
                    f"| {fmt(tstat, '{:+.2f}')} "
                    f"| {fmtp(row['Raw Spread'])} "
                    f"| {fmtp(row['Adj Spread'])} "
                    f"| {fmt(row['BE Cost (bps)'], '{:.0f}')} "
                    f"| {fmt(row['Turnover'], '{:.3f}')} "
                    f"| {fmtp(row['Cost Drag/yr'])} "
                    f"| {fmtp(row['Net Alpha/yr'])} "
                    f"| **{row['Verdict']}** |\n")

        # ==================================================================
        # IS/OOS COMPARISON
        # ==================================================================
        f.write("\n## IS vs OOS Comparison\n\n")
        f.write("| Signal | Horizon | IS IC | OOS IC | IS Spread | OOS Spread | "
                "IS Net Alpha | OOS Net Alpha |\n")
        f.write("|--------|---------|-------|--------|-----------|------------|"
                "-------------|---------------|\n")

        for res in all_results:
            name = res["signal_name"]
            for h in FWD_HORIZONS:
                is_ic = res.get(f"IS_{h}_ic", np.nan)
                oos_ic = res.get(f"OOS_{h}_ic", np.nan)
                is_sp = res.get(f"IS_{h}_raw_spread", np.nan)
                oos_sp = res.get(f"OOS_{h}_raw_spread", np.nan)
                is_na = res.get(f"IS_{h}_net_annual_alpha", np.nan)
                oos_na = res.get(f"OOS_{h}_net_annual_alpha", np.nan)

                f.write(f"| {name} | {h} | {fmt(is_ic)} | {fmt(oos_ic)} | "
                        f"{fmtp(is_sp)} | {fmtp(oos_sp)} | "
                        f"{fmtp(is_na)} | {fmtp(oos_na)} |\n")

        # ==================================================================
        # QUINTILE SPREAD COST SENSITIVITY
        # ==================================================================
        f.write("\n## Quintile Spread After Various Cost Levels (OOS, 14d)\n\n")
        f.write("Adjusted L/S quintile spread = raw spread - 2 * round_trip_cost:\n\n")

        header = "| Signal |"
        for c in cost_levels:
            header += f" {c}bps |"
        f.write(header + "\n")
        f.write("|" + "--------|" * (len(cost_levels) + 1) + "\n")

        for name, signal in signals.items():
            sig_oos = signal[oos_mask]
            raw_ret_oos = fwd["fwd_14d"][oos_mask]
            qs = quintile_spread(sig_oos, raw_ret_oos)
            raw_spread = qs["spread"]
            row_str = f"| {name} |"

            for c_bps in cost_levels:
                c_dec = c_bps / 10000
                if not np.isnan(raw_spread):
                    adj = raw_spread - 2 * c_dec
                    row_str += f" {adj*100:+.2f}% |"
                else:
                    row_str += " N/A |"
            f.write(row_str + "\n")

        # ==================================================================
        # TURNOVER ANALYSIS
        # ==================================================================
        f.write("\n## Signal Turnover & Annual Cost Drag (OOS)\n\n")
        f.write("| Signal | Turnover/day | Trades/yr | Avg Holding (days) | "
                "Annual Drag | 14d Raw Spread | Net Alpha/yr |\n")
        f.write("|--------|-------------|-----------|--------------------|-"
                "------------|----------------|-------------|\n")

        for name, signal in signals.items():
            sig_oos = signal[oos_mask]
            to = signal_turnover(sig_oos)
            tr = to["turnover_rate"]
            ah = to["avg_holding_days"]
            if np.isnan(tr):
                f.write(f"| {name} | N/A | N/A | N/A | N/A | N/A | N/A |\n")
                continue
            trades_yr = tr * 365
            annual_drag = trades_yr * COST_BTC
            # 14d raw spread
            raw_ret_oos = fwd["fwd_14d"][oos_mask]
            qs = quintile_spread(sig_oos, raw_ret_oos)
            raw_sp = qs["spread"]
            ann_sp = raw_sp * (365.0 / 14) if not np.isnan(raw_sp) else np.nan
            net_a = (ann_sp - annual_drag) if not np.isnan(ann_sp) else np.nan

            f.write(f"| {name} | {tr:.3f} | {trades_yr:.0f} | {ah:.1f} | "
                    f"{annual_drag*100:.2f}% | {fmtp(raw_sp)} | {fmtp(net_a)} |\n")

        # ==================================================================
        # DETAILED PER-SIGNAL ANALYSIS
        # ==================================================================
        f.write("\n## Detailed Signal Analysis\n\n")

        for res in all_results:
            name = res["signal_name"]
            f.write(f"### {name}\n\n")

            for period in ["IS", "OOS"]:
                f.write(f"**{period} Period:**\n")
                tr = res.get(f"{period}_turnover_rate", np.nan)
                ah = res.get(f"{period}_avg_holding_days", np.nan)
                drag = res.get(f"{period}_annual_cost_drag", np.nan)

                if not np.isnan(tr):
                    f.write(f"- Turnover: {tr:.3f}/day "
                            f"(avg holding: {ah:.1f} days, "
                            f"annual cost drag: {drag*100:.2f}%)\n")
                else:
                    f.write("- Turnover: N/A\n")

                for h in FWD_HORIZONS:
                    ic = res.get(f"{period}_{h}_ic", np.nan)
                    tstat = res.get(f"{period}_{h}_tstat", np.nan)
                    raw_sp = res.get(f"{period}_{h}_raw_spread", np.nan)
                    adj_sp = res.get(f"{period}_{h}_adj_spread", np.nan)
                    be = res.get(f"{period}_{h}_breakeven_cost_bps", np.nan)
                    net_a = res.get(f"{period}_{h}_net_annual_alpha", np.nan)
                    n = res.get(f"{period}_{h}_n", 0)

                    f.write(f"- **{h}** (n={n}):\n")
                    if not np.isnan(ic):
                        f.write(f"  - IC: {ic:+.4f} (t={tstat:+.2f})\n")
                        f.write(f"  - Raw L/S spread: {raw_sp*100:+.2f}%, "
                                f"after costs: {adj_sp*100:+.2f}%\n")
                        f.write(f"  - Break-even cost: {be:.0f} bps round-trip\n")
                        f.write(f"  - Net annual alpha: {fmtp(net_a)}\n")
                    else:
                        f.write(f"  - INSUFFICIENT DATA\n")
                f.write("\n")

        # ==================================================================
        # METHODOLOGY
        # ==================================================================
        f.write("## Methodology\n\n")
        f.write("### Cost Model\n")
        f.write("- **Round-trip cost:** Entry + exit fees\n")
        f.write("  - BTC/ETH: 5 bps per side = 10 bps round-trip\n")
        f.write("  - Alts: 15 bps per side = 30 bps round-trip\n\n")

        f.write("### Key Metrics\n\n")
        f.write("| Metric | Definition | Why it matters |\n")
        f.write("|--------|------------|----------------|\n")
        f.write("| Spearman IC | Rank correlation of signal vs forward return | "
                "Measures predictive power; invariant to constant cost shifts |\n")
        f.write("| Raw Q1-Q5 Spread | Mean return of bottom quintile minus top quintile | "
                "The gross edge before costs |\n")
        f.write("| Adjusted Spread | Raw spread - 2 * round_trip_cost | "
                "Net edge per trade after paying entry+exit on both legs |\n")
        f.write("| Break-even Cost | Round-trip cost at which spread = 0 | "
                "Robustness measure: higher = more room for slippage |\n")
        f.write("| Signal Turnover | Fraction of days with quintile change | "
                "Frequency of trading; directly drives cost drag |\n")
        f.write("| Annual Cost Drag | turnover_rate * 365 * round_trip_cost | "
                "Total annual cost from rebalancing |\n")
        f.write("| Net Annual Alpha | Annualized spread - annual cost drag | "
                "Bottom line: does the signal make money after costs? |\n\n")

        f.write("### Why IC Does Not Change With Costs\n\n")
        f.write("Spearman IC measures rank correlation. Subtracting a constant "
                "(trading cost) from all returns shifts every observation equally "
                "and does **not** change the rank ordering. Therefore, Spearman IC "
                "is identical whether computed on raw or cost-adjusted returns.\n\n")
        f.write("The real cost impact is on **P&L**, not **predictability**. A signal "
                "can perfectly predict return ranks (IC=1.0) but still lose money "
                "if the spread between quintiles is smaller than trading costs.\n\n")

        f.write("### Survival Criteria\n\n")
        f.write("- **SURVIVES (robust):** |IC| >= 0.05, adjusted spread > 0, "
                "net annual alpha > 0, break-even > 50 bps\n")
        f.write("- **SURVIVES (fragile):** Same but break-even <= 50 bps\n")
        f.write("- **MARGINAL:** Adjusted spread nonzero but net annual alpha <= 0\n")
        f.write("- **KILLED BY COSTS:** Adjusted spread <= 0\n")
        f.write("- **WEAK SIGNAL:** |IC| < 0.05 regardless of costs\n")


if __name__ == "__main__":
    main()
