#!/usr/bin/env python3
"""
Pre-compute comprehensive regime signals for alt perp trading.

Produces daily-frequency, causal (no look-ahead) signals:
- BTC SMA regimes (50-week, 20-week)
- BTC rolling returns (30d, 45d, 90d)
- Equal-weight alt index returns
- Alt-BTC spread
- Alt breadth (% above own 50d/20d SMA)
- Halving cycle bear flag
- Composite bear regime
- Alt bleed flag

Output: data/alternative/regime_signals.parquet
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "perp" / "binance" / "1h_ohlcv"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "alternative"
OUT_PATH = OUT_DIR / "regime_signals.parquet"

HALVING_DATES = [
    pd.Timestamp("2012-11-28", tz="UTC"),
    pd.Timestamp("2016-07-09", tz="UTC"),
    pd.Timestamp("2020-05-11", tz="UTC"),
    pd.Timestamp("2024-04-20", tz="UTC"),
]

EXCLUDE_TOKENS = {"BTC"}

# Tokens that are stock/equity tickers, not crypto alts
STOCK_TICKERS = {"AMZN", "AAPL", "GOOG", "GOOGL", "MSFT", "TSLA", "META", "NVDA", "AMD", "COIN"}

MIN_ALT_INDEX_TOKENS = 20


def load_daily_close(filepath: Path) -> pd.Series:
    """Load 1h CSV → daily close. Returns Series indexed by date."""
    df = pd.read_csv(filepath, usecols=["datetime", "close"], parse_dates=["datetime"])
    df = df.set_index("datetime").sort_index()
    # Resample to daily using last available close each day
    daily = df["close"].resample("1D").last().dropna()
    daily.index = daily.index.normalize()
    daily.index.name = "date"
    return daily


def load_all_tokens():
    """Load daily close for BTC and all alts. Returns dict of {token: Series}."""
    csvs = sorted(DATA_DIR.glob("*_perp_1h.csv"))
    token_data = {}
    for fp in csvs:
        token = fp.stem.replace("_perp_1h", "")
        if token in STOCK_TICKERS:
            continue
        try:
            s = load_daily_close(fp)
            if len(s) < 30:
                continue
            token_data[token] = s
        except Exception as e:
            print(f"  WARN: skip {token}: {e}", file=sys.stderr)
    return token_data


def compute_btc_signals(btc_close: pd.Series) -> pd.DataFrame:
    """BTC SMA regimes and rolling returns."""
    df = pd.DataFrame(index=btc_close.index)
    df["btc_close"] = btc_close
    df["btc_sma350"] = btc_close.rolling(350, min_periods=350).mean()
    df["btc_sma140"] = btc_close.rolling(140, min_periods=140).mean()
    df["btc_above_sma50"] = btc_close > df["btc_sma350"]
    df["btc_above_sma20"] = btc_close > df["btc_sma140"]

    for d in [30, 45, 90]:
        df[f"btc_ret_{d}d"] = btc_close.pct_change(d)

    return df


def compute_alt_index(token_data: dict, btc_index: pd.DatetimeIndex) -> pd.DataFrame:
    """Equal-weight alt index from top 20 alts by data length. Also returns 30d return."""
    # Exclude BTC
    alts = {k: v for k, v in token_data.items() if k not in EXCLUDE_TOKENS}

    # Select top 20 by data length for index construction
    by_len = sorted(alts.items(), key=lambda kv: len(kv[1]), reverse=True)
    top20_tokens = [k for k, _ in by_len[:20]]
    print(f"  Alt index tokens ({len(top20_tokens)}): {', '.join(top20_tokens)}")

    # Build aligned daily returns for top 20
    returns = pd.DataFrame(index=btc_index)
    for tok in top20_tokens:
        s = alts[tok].reindex(btc_index)
        returns[tok] = s.pct_change()

    # Equal-weight daily return (require at least MIN_ALT_INDEX_TOKENS non-NaN)
    mask = returns.notna().sum(axis=1) >= MIN_ALT_INDEX_TOKENS
    alt_daily_ret = returns.mean(axis=1).where(mask)

    # Cumulative alt index level
    alt_index = (1 + alt_daily_ret.fillna(0)).cumprod()
    alt_index_ret_30d = alt_index.pct_change(30)

    df = pd.DataFrame(index=btc_index)
    df["alt_index_ret_30d"] = alt_index_ret_30d
    return df


def compute_alt_breadth(token_data: dict, btc_index: pd.DatetimeIndex) -> pd.DataFrame:
    """% of alts with price above their own 50d/20d SMA. Expanding universe."""
    alts = {k: v for k, v in token_data.items() if k not in EXCLUDE_TOKENS}

    cols_50 = {}
    cols_20 = {}

    for tok, s in alts.items():
        aligned = s.reindex(btc_index)
        sma50 = aligned.rolling(50, min_periods=50).mean()
        sma20 = aligned.rolling(20, min_periods=20).mean()
        cols_50[tok] = (aligned > sma50).astype(float).where(aligned.notna() & sma50.notna())
        cols_20[tok] = (aligned > sma20).astype(float).where(aligned.notna() & sma20.notna())

    above_50 = pd.DataFrame(cols_50, index=btc_index)
    above_20 = pd.DataFrame(cols_20, index=btc_index)

    # Breadth = fraction above SMA, computed per day across all available tokens
    df = pd.DataFrame(index=btc_index)
    df["alt_breadth_50d"] = above_50.mean(axis=1)
    df["alt_breadth_20d"] = above_20.mean(axis=1)
    return df


def compute_halving_bear(index: pd.DatetimeIndex) -> pd.Series:
    """True when 365-1095 days after last halving."""
    result = pd.Series(False, index=index, dtype=bool)
    for i, dt in enumerate(index):
        # Find last halving before this date
        past_halvings = [h for h in HALVING_DATES if h <= dt]
        if not past_halvings:
            continue
        last_halving = max(past_halvings)
        days_since = (dt - last_halving).days
        if 365 <= days_since < 1095:
            result.iloc[i] = True
    return result


def main():
    print("Loading token data...")
    token_data = load_all_tokens()
    print(f"  Loaded {len(token_data)} tokens")

    if "BTC" not in token_data:
        print("ERROR: BTC data not found", file=sys.stderr)
        sys.exit(1)

    btc_close = token_data["BTC"]
    btc_index = btc_close.index

    print("Computing BTC signals...")
    btc_df = compute_btc_signals(btc_close)

    print("Computing alt index...")
    alt_idx_df = compute_alt_index(token_data, btc_index)

    print("Computing alt breadth...")
    breadth_df = compute_alt_breadth(token_data, btc_index)

    print("Computing halving cycle bear...")
    halving_bear = compute_halving_bear(btc_index)

    # Assemble
    print("Assembling signals...")
    signals = btc_df.copy()
    signals = signals.join(alt_idx_df)
    signals = signals.join(breadth_df)

    # Alt-BTC spread
    signals["alt_btc_spread_30d"] = signals["alt_index_ret_30d"] - signals["btc_ret_30d"]

    # Halving cycle bear
    signals["halving_cycle_bear"] = halving_bear

    # Composite bear
    signals["composite_bear"] = (
        (~signals["btc_above_sma50"])
        | signals["halving_cycle_bear"]
        | (signals["btc_ret_90d"] < -0.20)
    )

    # Alt bleed
    signals["alt_bleed"] = (
        (signals["alt_breadth_50d"] < 0.30)
        & (signals["alt_btc_spread_30d"] < -0.05)
    )

    # Select output columns in specified order
    out_cols = [
        "btc_close", "btc_sma350", "btc_sma140",
        "btc_ret_30d", "btc_ret_45d", "btc_ret_90d",
        "btc_above_sma50", "btc_above_sma20",
        "alt_index_ret_30d",
        "alt_btc_spread_30d",
        "alt_breadth_50d", "alt_breadth_20d",
        "halving_cycle_bear",
        "composite_bear",
        "alt_bleed",
    ]
    signals = signals[out_cols]

    # Save
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    signals.to_parquet(OUT_PATH)
    print(f"\nSaved {OUT_PATH} ({len(signals)} rows, {signals.shape[1]} cols)")

    # Monthly summary table (2022-2026)
    print("\n" + "=" * 120)
    print("MONTHLY REGIME SUMMARY (2022-2026)")
    print("=" * 120)

    subset = signals.loc["2022":"2026"].copy()

    # Classify regime per row
    def classify_regime(row):
        if row["alt_bleed"]:
            return "ALT_BLEED"
        if row["composite_bear"]:
            if row["btc_above_sma20"] and row["alt_breadth_20d"] is not None and row.get("alt_breadth_20d", 0) > 0.40:
                return "RECOVERY"
            return "BEAR"
        spread = row["alt_btc_spread_30d"]
        if pd.notna(spread) and spread > 0.03:
            return "ALT_SEASON"
        if pd.notna(spread) and spread < -0.03:
            return "BTC_SEASON"
        return "NEUTRAL"

    subset["regime"] = subset.apply(classify_regime, axis=1)

    monthly = subset.resample("MS").agg({
        "btc_close": "last",
        "btc_above_sma50": "last",
        "btc_above_sma20": "last",
        "btc_ret_30d": "last",
        "btc_ret_90d": "last",
        "alt_index_ret_30d": "last",
        "alt_btc_spread_30d": "last",
        "alt_breadth_50d": "last",
        "alt_breadth_20d": "last",
        "halving_cycle_bear": "last",
        "composite_bear": "last",
        "alt_bleed": "last",
    })

    # Regime = most common regime that month
    regime_monthly = subset["regime"].resample("MS").agg(lambda x: x.value_counts().index[0] if len(x) > 0 else "N/A")
    monthly["regime"] = regime_monthly

    # Format for display
    pd.set_option("display.max_rows", 100)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)

    fmt = monthly[["btc_close", "btc_above_sma50", "btc_ret_30d", "btc_ret_90d",
                    "alt_index_ret_30d", "alt_btc_spread_30d",
                    "alt_breadth_50d", "alt_breadth_20d",
                    "halving_cycle_bear", "composite_bear", "alt_bleed", "regime"]].copy()

    fmt["btc_close"] = fmt["btc_close"].map(lambda x: f"${x:,.0f}" if pd.notna(x) else "")
    for col in ["btc_ret_30d", "btc_ret_90d", "alt_index_ret_30d", "alt_btc_spread_30d"]:
        fmt[col] = fmt[col].map(lambda x: f"{x:+.1%}" if pd.notna(x) else "")
    for col in ["alt_breadth_50d", "alt_breadth_20d"]:
        fmt[col] = fmt[col].map(lambda x: f"{x:.0%}" if pd.notna(x) else "")
    for col in ["btc_above_sma50", "halving_cycle_bear", "composite_bear", "alt_bleed"]:
        fmt[col] = fmt[col].map(lambda x: "Y" if x else "." if pd.notna(x) else "")

    fmt.index = fmt.index.strftime("%Y-%m")
    print(fmt.to_string())

    print(f"\nRegime distribution:")
    print(subset["regime"].value_counts().to_string())
    print()


if __name__ == "__main__":
    main()
