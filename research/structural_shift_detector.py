"""
Structural Shift Detection Algorithm
=====================================
Computes a "Structural Health Score" from BTC, TOTAL2/TOTAL3, and regime data.
All indicators are daily, all causal (no lookahead).

Run: /workspace/venv/bin/python research/structural_shift_detector.py
"""

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)

# ── Data Loading ──────────────────────────────────────────────────────────────

DATA_DIR = Path("/workspace/crypto_backtest/data")


def load_btc_daily() -> pd.DataFrame:
    """Load BTC 1h OHLCV, resample to daily."""
    df = pd.read_csv(
        DATA_DIR / "perp/binance/1h_ohlcv/BTC_perp_1h.csv",
        parse_dates=["datetime"],
        index_col="datetime",
    )
    daily = df.resample("1D").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
    # Normalize to tz-naive for merging
    if daily.index.tz is not None:
        daily.index = daily.index.tz_localize(None)
    return daily


def load_total2_total3() -> pd.DataFrame:
    """Load TOTAL2/TOTAL3 daily data."""
    df = pd.read_parquet(DATA_DIR / "alternative/total2_total3.parquet")
    return df


def load_regime_signals() -> pd.DataFrame:
    """Load regime signals."""
    df = pd.read_parquet(DATA_DIR / "alternative/regime_signals.parquet")
    # Normalize index to tz-naive for merging
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


# ── Indicator Functions ───────────────────────────────────────────────────────


def compute_btc_alt_correlation(btc_daily: pd.DataFrame, t2t3: pd.DataFrame, window: int = 90) -> pd.Series:
    """
    Indicator 1: Rolling 90d correlation of BTC vs TOTAL2 daily returns.
    Normal: 0.70-0.90.  Structural shift: <0.50 or >0.95.
    Returns a score 0-1 where 0.5 = normal.
    """
    btc_ret = btc_daily["close"].pct_change()
    t2_ret = t2t3["total2_close"].pct_change()

    # Align
    aligned = pd.DataFrame({"btc": btc_ret, "total2": t2_ret}).dropna()
    rolling_corr = aligned["btc"].rolling(window, min_periods=60).corr(aligned["total2"])

    # Score: 0.80 center is ideal (normal), deviations are bad
    # Map: corr < 0.50 -> score 0.0, corr 0.50-0.70 -> score 0.0-0.5,
    #       corr 0.70-0.90 -> score 0.5-1.0 (healthy), corr > 0.95 -> score 0.0
    score = pd.Series(np.nan, index=rolling_corr.index)
    c = rolling_corr

    # Piecewise linear scoring
    # Below 0.50: structural crisis (score 0)
    # 0.50-0.70: transitional (score 0-0.5)
    # 0.70-0.90: healthy (score 0.5-1.0)
    # 0.90-0.95: getting dangerously correlated (score 0.5-0.2)
    # >0.95: liquidation cascade (score 0)
    score = np.where(c < 0.50, 0.0,
            np.where(c < 0.70, (c - 0.50) / 0.20 * 0.5,
            np.where(c < 0.90, 0.5 + (c - 0.70) / 0.20 * 0.5,
            np.where(c < 0.95, 1.0 - (c - 0.90) / 0.05 * 0.8,
            0.0))))

    return pd.Series(score, index=rolling_corr.index, name="corr_score")


def compute_dominance_trend(btc_daily: pd.DataFrame, t2t3: pd.DataFrame, window: int = 365) -> pd.Series:
    """
    Indicator 2: BTC Dominance proxy and its 365d range position.
    Dominance = BTC_close / (BTC_close + TOTAL2_close).
    Score 1.0 if within range, 0.0 if outside.
    """
    btc_close = btc_daily["close"]
    t2_close = t2t3["total2_close"]

    aligned = pd.DataFrame({"btc": btc_close, "total2": t2_close}).dropna()
    dominance = aligned["btc"] / (aligned["btc"] + aligned["total2"] / 1e8)  # TOTAL2 is market cap, BTC is price

    # Better proxy: just use relative movement
    # Since TOTAL2 is market cap and BTC is price, we normalize
    # Actually, let's compute dominance change rate as the signal
    dom_pct = dominance.pct_change(30)  # 30d momentum of dominance

    rolling_max = dominance.rolling(window, min_periods=180).max()
    rolling_min = dominance.rolling(window, min_periods=180).min()
    rolling_range = rolling_max - rolling_min

    # Position within range: 0 = at bottom, 1 = at top
    range_position = (dominance - rolling_min) / rolling_range.replace(0, np.nan)

    # Score: middle of range is healthy (0.5), extremes signal structural shift
    # If outside range entirely, that's a structural break
    outside_range = (dominance > rolling_max) | (dominance < rolling_min)

    # Use range position: being near center = healthy
    score = 1.0 - 2.0 * np.abs(range_position - 0.5)
    score = np.clip(score, 0.0, 1.0)
    # If outside range, score = 0
    score = np.where(outside_range, 0.0, score)

    return pd.Series(score, index=dominance.index, name="dom_score")


def compute_alt_depth(t2t3: pd.DataFrame, window: int = 90) -> pd.Series:
    """
    Indicator 3: TOTAL3/TOTAL2 ratio -- are small alts thriving or dying?
    Rising = breadth improving, declining = capital concentrating.
    Score based on 90d momentum of the ratio.
    """
    ratio = t2t3["total3_close"] / t2t3["total2_close"]
    ratio_ma = ratio.rolling(window, min_periods=30).mean()
    ratio_momentum = ratio / ratio_ma - 1.0  # How far from 90d average

    # Score: momentum > 0 = healthy (score 0.5-1.0), momentum < 0 = weakening (0.0-0.5)
    # Clip at +/- 0.20 (20% deviation from average is extreme)
    score = 0.5 + np.clip(ratio_momentum, -0.20, 0.20) / 0.20 * 0.5

    return pd.Series(score.values, index=t2t3.index[:len(score)], name="depth_score")


def compute_vol_structure(btc_daily: pd.DataFrame, t2t3: pd.DataFrame, window: int = 30) -> pd.Series:
    """
    Indicator 4: Rolling 30d vol of TOTAL2 / rolling 30d vol of BTC.
    Normal: 1.5-2.5x.  Structural shift: persistently >3.0 or <1.2.
    """
    btc_ret = btc_daily["close"].pct_change()
    t2_ret = t2t3["total2_close"].pct_change()

    aligned = pd.DataFrame({"btc": btc_ret, "total2": t2_ret}).dropna()

    btc_vol = aligned["btc"].rolling(window, min_periods=20).std() * np.sqrt(365)
    t2_vol = aligned["total2"].rolling(window, min_periods=20).std() * np.sqrt(365)

    vol_ratio = t2_vol / btc_vol.replace(0, np.nan)

    # Score: 1.5-2.5 is healthy (score 1.0 at 2.0), outside is bad
    # <1.2: alts dead (score 0.0)
    # 1.2-1.5: transitional (0.0-0.5)
    # 1.5-2.5: healthy (0.5-1.0, peak at 2.0)
    # 2.5-3.0: transitional (0.5-0.0)
    # >3.0: structural stress (0.0)
    v = vol_ratio
    score = np.where(v < 1.2, 0.0,
            np.where(v < 1.5, (v - 1.2) / 0.3 * 0.5,
            np.where(v < 2.0, 0.5 + (v - 1.5) / 0.5 * 0.5,
            np.where(v < 2.5, 1.0 - (v - 2.0) / 0.5 * 0.5,
            np.where(v < 3.0, 0.5 - (v - 2.5) / 0.5 * 0.5,
            0.0)))))

    return pd.Series(score, index=vol_ratio.index, name="vol_score")


def compute_recovery_speed(t2t3: pd.DataFrame) -> pd.Series:
    """
    Indicator 5: After a 20% drawdown in TOTAL2, how fast does it recover?
    Compare days to recover 50% of drawdown vs 2-year rolling average.
    Returns a daily score (interpolated between recovery events).
    """
    t2_close = t2t3["total2_close"].dropna()

    # Compute running max and drawdown
    running_max = t2_close.expanding().max()
    drawdown = t2_close / running_max - 1.0

    # Find drawdown events exceeding -20%
    in_drawdown = drawdown < -0.20
    recovery_days = []

    i = 0
    dates = t2_close.index
    values = t2_close.values
    max_vals = running_max.values
    dd_vals = drawdown.values

    while i < len(dates):
        if dd_vals[i] < -0.20:
            # Find the trough of this drawdown
            trough_idx = i
            while trough_idx + 1 < len(dates) and dd_vals[trough_idx + 1] < dd_vals[trough_idx]:
                trough_idx += 1

            trough_dd = dd_vals[trough_idx]
            half_recovery_level = max_vals[trough_idx] * (1.0 + trough_dd * 0.5)

            # Find when price recovers to 50% of the drawdown
            j = trough_idx + 1
            while j < len(dates) and values[j] < half_recovery_level:
                j += 1

            if j < len(dates):
                days_to_recover = (dates[j] - dates[trough_idx]).days
                recovery_days.append({
                    "date": dates[trough_idx],
                    "recovery_date": dates[j],
                    "days": days_to_recover,
                    "drawdown_pct": trough_dd * 100,
                })

            i = max(j, trough_idx + 1)
        else:
            i += 1

    if not recovery_days:
        return pd.Series(0.5, index=t2_close.index, name="recovery_score")

    rec_df = pd.DataFrame(recovery_days).set_index("date")

    # Rolling 2-year average recovery time
    rec_df["avg_recovery_2y"] = rec_df["days"].rolling("730D", min_periods=1).mean()

    # Score: faster than average = healthy (>0.5), slower = weak (<0.5)
    # Ratio: avg / actual  (>1 means faster than average)
    rec_df["speed_ratio"] = rec_df["avg_recovery_2y"] / rec_df["days"].replace(0, np.nan)
    rec_df["score"] = np.clip(rec_df["speed_ratio"] / 2.0, 0.0, 1.0)  # normalize so 1x avg = 0.5

    # Forward-fill to get daily score
    daily_score = rec_df["score"].reindex(t2_close.index).ffill().fillna(0.5)

    return daily_score.rename("recovery_score")


def compute_breadth_persistence(regime: pd.DataFrame, window: int = 180) -> pd.Series:
    """
    Bonus indicator: rolling 180d average of alt_breadth_50d.
    Score based on breadth level.
    """
    if "alt_breadth_50d" not in regime.columns:
        return pd.Series(0.5, index=regime.index, name="breadth_score")

    breadth = regime["alt_breadth_50d"]
    rolling_breadth = breadth.rolling(window, min_periods=60).mean()

    # Score: >0.50 breadth = healthy, <0.20 = structural crisis
    score = np.clip(rolling_breadth / 0.60, 0.0, 1.0)  # 0.60 breadth = score 1.0

    return pd.Series(score.values, index=regime.index, name="breadth_score")


# ── Composite Score ───────────────────────────────────────────────────────────


def compute_structural_health(
    btc_daily: pd.DataFrame,
    t2t3: pd.DataFrame,
    regime: pd.DataFrame,
) -> pd.DataFrame:
    """Compute all indicators and the weighted composite score."""

    print("Computing indicators...")

    corr = compute_btc_alt_correlation(btc_daily, t2t3)
    dom = compute_dominance_trend(btc_daily, t2t3)
    depth = compute_alt_depth(t2t3)
    vol = compute_vol_structure(btc_daily, t2t3)
    recovery = compute_recovery_speed(t2t3)
    breadth = compute_breadth_persistence(regime)

    # Merge all on a common daily index
    result = pd.DataFrame({
        "corr_score": corr,
        "dom_score": dom,
        "depth_score": depth,
        "vol_score": vol,
        "recovery_score": recovery,
        "breadth_score": breadth,
    })

    # Forward fill to handle alignment gaps
    result = result.ffill()

    # Weights (sum to 1.0)
    weights = {
        "corr_score": 0.20,     # Correlation regime is a strong structural signal
        "dom_score": 0.15,      # Dominance trend matters
        "depth_score": 0.15,    # Alt market depth
        "vol_score": 0.15,      # Vol structure
        "recovery_score": 0.15, # Recovery speed
        "breadth_score": 0.20,  # Breadth persistence is key for alt strategies
    }

    result["composite"] = sum(result[k] * w for k, w in weights.items())

    # Filter to 2022+ (enough lookback data)
    result = result.loc["2022-01-01":]

    return result


# ── Known Structural Events (for labeling) ────────────────────────────────────

KNOWN_EVENTS = {
    "2022-05": "LUNA/UST Collapse",
    "2022-06": "Celsius/3AC Contagion",
    "2022-11": "FTX Collapse",
    "2024-01": "BTC ETF Launch (structural strength)",
    "2024-03": "BTC ETF-driven ATH",
    "2024-07": "ETH ETF Launch",
    "2025-01": "AI Token Mania Peak",
}


# ── Reporting ─────────────────────────────────────────────────────────────────


def print_monthly_report(health: pd.DataFrame) -> None:
    """Print monthly structural health scores and flag shifts."""

    monthly = health.resample("ME").mean()

    print("\n" + "=" * 100)
    print("MONTHLY STRUCTURAL HEALTH SCORE (2022-2026)")
    print("=" * 100)
    print(f"{'Month':<10} {'Corr':>6} {'Dom':>6} {'Depth':>6} {'Vol':>6} {'Recov':>6} {'Brdth':>6} {'COMPOSITE':>10} {'Flag':>20}")
    print("-" * 100)

    shift_months = []

    for date, row in monthly.iterrows():
        month_key = date.strftime("%Y-%m")
        composite = row["composite"]

        # Flag structural shifts
        flag = ""
        if composite < 0.30:
            flag = "** CRISIS **"
            shift_months.append((month_key, composite, "CRISIS"))
        elif composite < 0.40:
            flag = "* WEAK *"
            shift_months.append((month_key, composite, "WEAK"))
        elif composite > 0.70:
            flag = "++ STRONG ++"
            shift_months.append((month_key, composite, "STRONG"))
        elif composite > 0.60:
            flag = "+ HEALTHY +"

        # Check for known events
        event = KNOWN_EVENTS.get(month_key, "")
        if event:
            flag = f"{flag} [{event}]"

        print(f"{month_key:<10} {row['corr_score']:>6.2f} {row['dom_score']:>6.2f} "
              f"{row['depth_score']:>6.2f} {row['vol_score']:>6.2f} "
              f"{row['recovery_score']:>6.2f} {row['breadth_score']:>6.2f} "
              f"{composite:>10.3f} {flag}")

    print("\n" + "=" * 100)
    print("STRUCTURAL SHIFT DETECTIONS")
    print("=" * 100)

    if not shift_months:
        print("No structural shifts detected.")
    else:
        for month, score, label in shift_months:
            event = KNOWN_EVENTS.get(month, "Unknown/new event")
            print(f"  {month}  Score={score:.3f}  ({label})  — {event}")

    # Summary statistics
    print("\n" + "=" * 100)
    print("SUMMARY STATISTICS BY YEAR")
    print("=" * 100)
    yearly = health.resample("YE").mean()
    for date, row in yearly.iterrows():
        year = date.year
        print(f"  {year}: Composite={row['composite']:.3f}  "
              f"Corr={row['corr_score']:.2f}  Dom={row['dom_score']:.2f}  "
              f"Depth={row['depth_score']:.2f}  Vol={row['vol_score']:.2f}  "
              f"Recovery={row['recovery_score']:.2f}  Breadth={row['breadth_score']:.2f}")


def print_current_assessment(health: pd.DataFrame) -> None:
    """Print the most recent structural health assessment."""

    latest = health.iloc[-1]
    recent_30d = health.tail(30).mean()

    print("\n" + "=" * 100)
    print("CURRENT STRUCTURAL ASSESSMENT (Latest Available)")
    print("=" * 100)
    print(f"  Date:             {health.index[-1].strftime('%Y-%m-%d')}")
    print(f"  Composite Score:  {latest['composite']:.3f}")
    print(f"  30d Average:      {recent_30d['composite']:.3f}")
    print()
    print("  Component Scores (latest / 30d avg):")
    for col in ["corr_score", "dom_score", "depth_score", "vol_score", "recovery_score", "breadth_score"]:
        label = col.replace("_score", "").replace("_", " ").title()
        print(f"    {label:<15} {latest[col]:.3f} / {recent_30d[col]:.3f}")

    print()
    if latest["composite"] < 0.30:
        print("  ASSESSMENT: STRUCTURAL CRISIS — Multiple indicators flagging.")
        print("  Implication: Historical patterns may be broken. Reduce exposure, tighten stops.")
    elif latest["composite"] < 0.40:
        print("  ASSESSMENT: STRUCTURAL WEAKNESS — Market structure deteriorating.")
        print("  Implication: Caution warranted. Alt strategies face headwinds.")
    elif latest["composite"] < 0.50:
        print("  ASSESSMENT: BELOW NORMAL — Some structural concerns.")
        print("  Implication: Monitor closely. Not crisis, but not healthy either.")
    elif latest["composite"] < 0.60:
        print("  ASSESSMENT: NORMAL — Cyclical behavior within known structure.")
        print("  Implication: Standard strategy parameters should work.")
    elif latest["composite"] < 0.70:
        print("  ASSESSMENT: HEALTHY — Market structure supportive.")
        print("  Implication: Good conditions for trend-following and momentum strategies.")
    else:
        print("  ASSESSMENT: STRUCTURAL STRENGTH — All indicators positive.")
        print("  Implication: Broad opportunity set. Consider increasing exposure.")


def print_recovery_events(t2t3: pd.DataFrame) -> None:
    """Print the detected recovery events for context."""
    t2_close = t2t3["total2_close"].dropna()
    running_max = t2_close.expanding().max()
    drawdown = t2_close / running_max - 1.0

    print("\n" + "=" * 100)
    print("MAJOR DRAWDOWN EVENTS (>20% in TOTAL2)")
    print("=" * 100)

    i = 0
    dates = t2_close.index
    values = t2_close.values
    max_vals = running_max.values
    dd_vals = drawdown.values
    event_num = 0

    while i < len(dates):
        if dd_vals[i] < -0.20:
            trough_idx = i
            while trough_idx + 1 < len(dates) and dd_vals[trough_idx + 1] < dd_vals[trough_idx]:
                trough_idx += 1

            trough_dd = dd_vals[trough_idx]
            half_recovery_level = max_vals[trough_idx] * (1.0 + trough_dd * 0.5)

            j = trough_idx + 1
            while j < len(dates) and values[j] < half_recovery_level:
                j += 1

            event_num += 1
            if j < len(dates):
                days = (dates[j] - dates[trough_idx]).days
                print(f"  Event {event_num}: Trough {dates[trough_idx].strftime('%Y-%m-%d')} "
                      f"(DD={trough_dd*100:.1f}%)  →  50% recovery in {days}d "
                      f"({dates[j].strftime('%Y-%m-%d')})")
            else:
                print(f"  Event {event_num}: Trough {dates[trough_idx].strftime('%Y-%m-%d')} "
                      f"(DD={trough_dd*100:.1f}%)  →  NOT YET RECOVERED to 50%")

            i = max(j, trough_idx + 1)
        else:
            i += 1


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    print("Loading data...")
    btc_daily = load_btc_daily()
    t2t3 = load_total2_total3()
    regime = load_regime_signals()

    print(f"  BTC daily: {btc_daily.index.min().date()} to {btc_daily.index.max().date()} ({len(btc_daily)} days)")
    print(f"  TOTAL2/3:  {t2t3.index.min().date()} to {t2t3.index.max().date()} ({len(t2t3)} days)")
    print(f"  Regime:    {regime.index.min().date()} to {regime.index.max().date()} ({len(regime)} days)")

    health = compute_structural_health(btc_daily, t2t3, regime)
    print(f"  Health scores computed: {len(health)} days ({health.index.min().date()} to {health.index.max().date()})")

    print_monthly_report(health)
    print_recovery_events(t2t3)
    print_current_assessment(health)

    print("\nDone.")


if __name__ == "__main__":
    main()
