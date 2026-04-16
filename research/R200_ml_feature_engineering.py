"""
R200 — ML Feature Matrix Builder
=================================
Build a comprehensive feature matrix combining all available data sources
with STRICT lag discipline (no look-ahead bias).

Output: data/ml_features/feature_matrix.parquet
"""

import os
import sys
import glob
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)

BASE = Path("/workspace/crypto_backtest")
DATA = BASE / "data"
OUT_DIR = DATA / "ml_features"

# Top 30 tokens by typical perp volume (BTC first as proof-of-concept)
TOP_TOKENS = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT",
    "MATIC", "UNI", "LTC", "ATOM", "FIL", "APT", "ARB", "OP", "NEAR", "SUI",
    "AAVE", "MKR", "INJ", "TIA", "SEI", "PEPE", "WIF", "JUP", "RUNE", "FTM",
]


# ─── Utility functions ───────────────────────────────────────────────

def rolling_zscore(s: pd.Series, window: int) -> pd.Series:
    """Backward-looking rolling z-score. No center, no bfill."""
    m = s.rolling(window, min_periods=max(window // 2, 2)).mean()
    sd = s.rolling(window, min_periods=max(window // 2, 2)).std()
    return (s - m) / sd.replace(0, np.nan)


def rolling_pctrank(s: pd.Series, window: int) -> pd.Series:
    """Backward-looking percentile rank (0-1)."""
    return s.rolling(window, min_periods=max(window // 2, 2)).apply(
        lambda x: (x.iloc[-1] > x[:-1]).mean(), raw=False
    )


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI indicator, backward-looking."""
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).rolling(period, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd_signal(close: pd.Series, fast: int = 12, slow: int = 26, sig: int = 9) -> pd.Series:
    """MACD histogram (signal line subtracted)."""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=sig, adjust=False).mean()
    return macd_line - signal_line


def bollinger_position(close: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.Series:
    """Position within Bollinger bands: 0 = lower, 1 = upper."""
    sma = close.rolling(window, min_periods=window).mean()
    std = close.rolling(window, min_periods=window).std()
    upper = sma + num_std * std
    lower = sma - num_std * std
    return (close - lower) / (upper - lower).replace(0, np.nan)


# ─── Data loaders ────────────────────────────────────────────────────

def load_price_1h(token: str) -> pd.DataFrame:
    """Load 1H price data for a token."""
    path = DATA / f"perp/1h_cache/{token}_1h.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    return df


def load_binance_metrics() -> pd.DataFrame:
    """Load daily positioning metrics (all symbols)."""
    path = DATA / "alternative/binance_metrics/all_symbols_daily_ls.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"])
    return df


def load_coinalyze_liquidations() -> pd.DataFrame:
    """Load and aggregate liquidation data across exchanges."""
    files = glob.glob(str(DATA / "alternative/coinalyze/liquidations_parquet/*.parquet"))
    if not files:
        return pd.DataFrame()
    dfs = [pd.read_parquet(f) for f in files]
    df = pd.concat(dfs, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    # Aggregate across exchanges per (date, token)
    agg = df.groupby(["date", "token"])[["l", "s"]].sum().reset_index()
    agg.rename(columns={"l": "long_liq", "s": "short_liq"}, inplace=True)
    return agg


def load_coinalyze_oi() -> pd.DataFrame:
    """Load and aggregate OI data across exchanges."""
    files = glob.glob(str(DATA / "alternative/coinalyze/open_interest_parquet/*.parquet"))
    if not files:
        return pd.DataFrame()
    dfs = [pd.read_parquet(f) for f in files]
    df = pd.concat(dfs, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    # Sum OI close across exchanges per (date, token)
    agg = df.groupby(["date", "token"])["c"].sum().reset_index()
    agg.rename(columns={"c": "coinalyze_oi"}, inplace=True)
    return agg


def load_macro() -> dict:
    """Load macro data series. Returns dict of {name: Series(date-indexed, Close)}."""
    mapping = {
        "dxy": "usd_index.parquet",
        "us10y": "us10y_yield.parquet",
        "vix": "vix.parquet",
        "gold": "gold.parquet",
        "oil": "oil_wti.parquet",
        "sp500": "sp500.parquet",
    }
    result = {}
    for name, fname in mapping.items():
        path = DATA / f"alternative/macro/{fname}"
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date").sort_index()
        result[name] = df["Close"]
    return result


def load_dvol() -> pd.DataFrame:
    """Load BTC and ETH DVOL (implied vol index)."""
    frames = {}
    for coin in ["btc", "eth"]:
        path = DATA / f"alternative/deribit_options/dvol/{coin}_dvol_daily.json"
        if not path.exists():
            continue
        with open(path) as f:
            data = json.load(f)
        # Format: [[timestamp_ms, open, high, low, close], ...]
        df = pd.DataFrame(data, columns=["ts", "open", "high", "low", "close"])
        df["date"] = pd.to_datetime(df["ts"], unit="ms")
        df = df.set_index("date").sort_index()
        frames[coin] = df["close"].rename(f"dvol_{coin}")
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames.values(), axis=1)


def load_funding_proxy(token: str) -> pd.DataFrame:
    """Load pre-computed funding proxy features for a token."""
    path = DATA / f"alternative/funding_ls_proxy/{token}_funding_proxy.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    return df


def load_etf_flows() -> pd.Series:
    """Load BTC ETF daily inflows."""
    path = DATA / "alternative/etf_flows/btc_etf_daily.parquet"
    if not path.exists():
        return pd.Series(dtype=float)
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["total_inflow_mm"].sort_index()


def load_fear_greed() -> pd.Series:
    """Load Fear & Greed Index."""
    path = DATA / "alternative/fear_greed/fear_greed_index.parquet"
    if not path.exists():
        return pd.Series(dtype=float)
    df = pd.read_parquet(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
    return df.set_index("timestamp")["value"].astype(float).sort_index()


# ─── Feature builders ────────────────────────────────────────────────

def build_price_features(token: str) -> pd.DataFrame:
    """Build daily price-based features from 1H data."""
    df_1h = load_price_1h(token)
    if df_1h.empty:
        print(f"  [SKIP] No 1H price data for {token}")
        return pd.DataFrame()

    # Resample to daily
    daily = df_1h.resample("1D").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna(subset=["close"])

    close = daily["close"]
    feats = pd.DataFrame(index=daily.index)

    # Returns at multiple horizons
    for w in [1, 3, 7, 14, 30]:
        feats[f"ret_{w}d"] = close.pct_change(w)

    # Volatility
    daily_ret = close.pct_change()
    feats["vol_7d"] = daily_ret.rolling(7, min_periods=5).std() * np.sqrt(365)
    feats["vol_30d"] = daily_ret.rolling(30, min_periods=20).std() * np.sqrt(365)
    feats["vol_ratio_7_30"] = feats["vol_7d"] / feats["vol_30d"].replace(0, np.nan)

    # RSI
    feats["rsi_14"] = rsi(close, 14)

    # MACD histogram
    feats["macd_hist"] = macd_signal(close)

    # Bollinger position
    feats["boll_pos_20"] = bollinger_position(close, 20)

    # Volume z-score
    feats["vol_zscore_14"] = rolling_zscore(daily["volume"], 14)

    # Intraday range (high-low / close)
    feats["range_pct"] = (daily["high"] - daily["low"]) / daily["close"]

    # Funding rate features from 1H data
    if "funding_rate" in df_1h.columns:
        fr = df_1h["funding_rate"]
        daily_fr = fr.resample("1D").mean()
        feats["funding_daily_mean"] = daily_fr
        feats["funding_cum_7d"] = fr.rolling(7 * 24, min_periods=7 * 12).sum().resample("1D").last()
        feats["funding_z7"] = rolling_zscore(daily_fr, 7)
        feats["funding_z30"] = rolling_zscore(daily_fr, 30)

    return feats


def build_positioning_features(token: str, metrics_df: pd.DataFrame) -> pd.DataFrame:
    """Build positioning features from Binance metrics."""
    symbol = f"{token}USDT"
    sub = metrics_df[metrics_df["symbol"] == symbol].copy()
    if sub.empty:
        print(f"  [SKIP] No positioning data for {token}")
        return pd.DataFrame()

    sub = sub.set_index("date").sort_index()
    feats = pd.DataFrame(index=sub.index)

    # Raw series
    topls = sub["count_toptrader_ls_ratio"]
    global_ls = sub["count_ls_ratio"]
    divergence = topls - global_ls
    taker_ratio = sub["taker_buy_sell_ratio"]
    oi_value = sub["sum_open_interest_value"]

    # Z-scores at multiple windows
    for w in [7, 14, 30, 60]:
        feats[f"topls_z{w}"] = rolling_zscore(topls, w)
        feats[f"div_z{w}"] = rolling_zscore(divergence, w)
        feats[f"taker_z{w}"] = rolling_zscore(taker_ratio, w)
        feats[f"oi_val_z{w}"] = rolling_zscore(oi_value, w)

    # Rate of change
    for w in [3, 7, 14]:
        feats[f"topls_roc{w}"] = topls.pct_change(w)
        feats[f"div_roc{w}"] = divergence.diff(w)
        feats[f"oi_roc{w}"] = oi_value.pct_change(w)

    # Percentile rank
    feats["topls_pctile90"] = rolling_pctrank(topls, 90)
    feats["div_pctile90"] = rolling_pctrank(divergence, 90)

    return feats


def build_liquidation_features(token: str, liq_df: pd.DataFrame) -> pd.DataFrame:
    """Build liquidation features from Coinalyze data."""
    sub = liq_df[liq_df["token"] == token].copy()
    if sub.empty:
        print(f"  [SKIP] No liquidation data for {token}")
        return pd.DataFrame()

    sub = sub.set_index("date").sort_index()
    # Deduplicate in case of overlaps
    sub = sub[~sub.index.duplicated(keep="last")]

    feats = pd.DataFrame(index=sub.index)
    long_liq = sub["long_liq"]
    short_liq = sub["short_liq"]
    total_liq = long_liq + short_liq

    # Z-scores
    for w in [7, 14, 30]:
        feats[f"long_liq_z{w}"] = rolling_zscore(long_liq, w)
        feats[f"short_liq_z{w}"] = rolling_zscore(short_liq, w)

    # Liquidation ratio (long skew)
    feats["liq_ratio"] = long_liq / total_liq.replace(0, np.nan)

    # Spike detection
    liq_mean = total_liq.rolling(30, min_periods=15).mean()
    liq_std = total_liq.rolling(30, min_periods=15).std()
    feats["liq_spike"] = (total_liq > liq_mean + 2 * liq_std).astype(float)

    return feats


def build_coinalyze_oi_features(token: str, oi_df: pd.DataFrame) -> pd.DataFrame:
    """Build OI features from Coinalyze data."""
    sub = oi_df[oi_df["token"] == token].copy()
    if sub.empty:
        return pd.DataFrame()

    sub = sub.set_index("date").sort_index()
    sub = sub[~sub.index.duplicated(keep="last")]
    oi = sub["coinalyze_oi"]

    feats = pd.DataFrame(index=sub.index)
    for w in [7, 14, 30]:
        feats[f"ca_oi_z{w}"] = rolling_zscore(oi, w)
        feats[f"ca_oi_roc{w}"] = oi.pct_change(w)

    return feats


def build_funding_proxy_features(token: str) -> pd.DataFrame:
    """Load pre-computed funding proxy and select key columns."""
    df = load_funding_proxy(token)
    if df.empty:
        print(f"  [SKIP] No funding proxy for {token}")
        return pd.DataFrame()

    # Select most useful pre-computed features
    cols = [
        "fr_zscore_7d", "fr_zscore_30d", "cum_funding_7d", "cum_funding_30d",
        "fr_momentum_7d", "fr_pctrank_90d", "extreme_long", "extreme_short",
    ]
    available = [c for c in cols if c in df.columns]
    feats = df[available].copy()
    # Prefix to avoid collision with price-derived funding
    feats.columns = ["fp_" + c for c in feats.columns]
    return feats


def build_macro_features(macro_dict: dict) -> pd.DataFrame:
    """Build macro features (applied to all tokens as BTC-level context)."""
    feats = pd.DataFrame()
    for name, series in macro_dict.items():
        series = series.dropna()
        if series.empty:
            continue
        for w in [14, 30, 60]:
            feats[f"{name}_z{w}"] = rolling_zscore(series, w)
        for w in [5, 14]:
            feats[f"{name}_roc{w}"] = series.pct_change(w)
    return feats


def build_dvol_features(dvol_df: pd.DataFrame) -> pd.DataFrame:
    """Build DVOL (implied vol) features."""
    if dvol_df.empty:
        return pd.DataFrame()
    feats = pd.DataFrame(index=dvol_df.index)
    for col in dvol_df.columns:
        series = dvol_df[col]
        feats[f"{col}_z14"] = rolling_zscore(series, 14)
        feats[f"{col}_z30"] = rolling_zscore(series, 30)
        feats[f"{col}_roc5"] = series.pct_change(5)
    return feats


def build_etf_features(etf_flows: pd.Series) -> pd.DataFrame:
    """Build ETF flow features."""
    if etf_flows.empty:
        return pd.DataFrame()
    feats = pd.DataFrame(index=etf_flows.index)
    feats["etf_flow_z14"] = rolling_zscore(etf_flows, 14)
    feats["etf_flow_z30"] = rolling_zscore(etf_flows, 30)
    feats["etf_flow_cum7"] = etf_flows.rolling(7, min_periods=3).sum()
    feats["etf_flow_cum30"] = etf_flows.rolling(30, min_periods=15).sum()
    return feats


def build_fear_greed_features(fg: pd.Series) -> pd.DataFrame:
    """Build Fear & Greed index features."""
    if fg.empty:
        return pd.DataFrame()
    feats = pd.DataFrame(index=fg.index)
    feats["fg_value"] = fg
    feats["fg_z14"] = rolling_zscore(fg, 14)
    feats["fg_z30"] = rolling_zscore(fg, 30)
    feats["fg_roc5"] = fg.diff(5)
    return feats


def build_label(token: str) -> pd.Series:
    """Forward 7-day return as label (daily frequency)."""
    df_1h = load_price_1h(token)
    if df_1h.empty:
        return pd.Series(dtype=float)
    daily_close = df_1h["close"].resample("1D").last().dropna()
    label = daily_close.shift(-7) / daily_close - 1
    label.name = "label_7d_fwd"
    return label


# ─── Cross-sectional features ────────────────────────────────────────

def cross_sectional_rank(panel: pd.DataFrame) -> pd.DataFrame:
    """Rank each column (token) cross-sectionally at each date (0-1)."""
    return panel.rank(axis=1, pct=True)


# ─── Main pipeline ───────────────────────────────────────────────────

def build_token_features(
    token: str,
    metrics_df: pd.DataFrame,
    liq_df: pd.DataFrame,
    oi_df: pd.DataFrame,
    macro_feats: pd.DataFrame,
    dvol_feats: pd.DataFrame,
    etf_feats: pd.DataFrame,
    fg_feats: pd.DataFrame,
) -> pd.DataFrame:
    """Build all features for a single token, aligned to daily frequency."""
    print(f"\n{'='*60}")
    print(f"Building features for {token}")
    print(f"{'='*60}")

    # A. Price features
    price_feats = build_price_features(token)
    print(f"  Price features: {price_feats.shape[1] if not price_feats.empty else 0} cols")

    # B. Positioning features (daily, lag 1 day inherent — data published D for D-1)
    pos_feats = build_positioning_features(token, metrics_df)
    print(f"  Positioning features: {pos_feats.shape[1] if not pos_feats.empty else 0} cols")

    # C. Liquidation features
    liq_feats = build_liquidation_features(token, liq_df)
    print(f"  Liquidation features: {liq_feats.shape[1] if not liq_feats.empty else 0} cols")

    # D. Coinalyze OI features
    ca_oi_feats = build_coinalyze_oi_features(token, oi_df)
    print(f"  Coinalyze OI features: {ca_oi_feats.shape[1] if not ca_oi_feats.empty else 0} cols")

    # E. Funding proxy features
    fp_feats = build_funding_proxy_features(token)
    print(f"  Funding proxy features: {fp_feats.shape[1] if not fp_feats.empty else 0} cols")

    # Combine token-specific daily features
    all_daily = [price_feats, pos_feats, liq_feats, ca_oi_feats, fp_feats]
    non_empty = [df for df in all_daily if not df.empty]
    if not non_empty:
        print(f"  [SKIP] No features for {token}")
        return pd.DataFrame()

    # Align all to daily date index via outer join
    combined = non_empty[0]
    for df in non_empty[1:]:
        combined = combined.join(df, how="outer")

    # Add macro/global features (same for all tokens)
    for global_feats in [macro_feats, dvol_feats, etf_feats, fg_feats]:
        if not global_feats.empty:
            combined = combined.join(global_feats, how="left")

    # ──────────────────────────────────────────────
    # CRITICAL: Apply 1-day lag to ALL features
    # Features at day D use data from <= D-1
    # ──────────────────────────────────────────────
    combined = combined.shift(1)

    # Add token identifier
    combined["token"] = token

    print(f"  Total features (after lag): {combined.shape[1] - 1} cols, {combined.shape[0]} rows")
    return combined


def build_cross_sectional_features(
    token_features: dict, feature_names: list
) -> dict:
    """Add cross-sectional percentile rank features across tokens."""
    if len(token_features) < 3:
        print("  [SKIP] Cross-sectional features (need >= 3 tokens)")
        return token_features

    cs_source_cols = [
        "topls_z30", "oi_val_z30", "ret_7d", "vol_30d",
        "funding_z30", "long_liq_z7",
    ]

    for col in cs_source_cols:
        # Build panel: index=date, columns=token
        panels = {}
        for tok, df in token_features.items():
            if col in df.columns:
                s = df[col].copy()
                # Deduplicate index (keep last)
                s = s[~s.index.duplicated(keep="last")]
                panels[tok] = s

        if len(panels) < 3:
            continue

        panel = pd.DataFrame(panels)
        ranked = cross_sectional_rank(panel)

        # Write back to each token's df
        cs_col_name = f"cs_{col}_rank"
        for tok in panels:
            if tok in token_features:
                rank_s = ranked[tok]
                # Reindex to match token's original index
                token_features[tok][cs_col_name] = rank_s.reindex(
                    token_features[tok].index[~token_features[tok].index.duplicated(keep="last")]
                ).reindex(token_features[tok].index)

    return token_features


def add_interaction_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add selected interaction features."""
    interactions = {
        "pos_x_mom": ("topls_z30", "ret_7d"),
        "pos_x_vol": ("topls_z30", "vol_30d"),
        "pos_x_fund": ("div_z30", "funding_z7"),
        "liq_x_pos": ("long_liq_z7", "topls_z30"),
        "fund_x_vol": ("funding_z30", "vol_30d"),
        "oi_x_ret": ("oi_val_z30", "ret_7d"),
    }
    for name, (col_a, col_b) in interactions.items():
        if col_a in df.columns and col_b in df.columns:
            df[name] = df[col_a] * df[col_b]
    return df


def main():
    print("=" * 70)
    print("R200 — ML Feature Matrix Builder")
    print("=" * 70)

    # ── Step 1: Load shared data sources ──
    print("\n[Step 1] Loading shared data sources...")

    metrics_df = load_binance_metrics()
    print(f"  Binance metrics: {metrics_df.shape}")

    liq_df = load_coinalyze_liquidations()
    print(f"  Coinalyze liquidations: {liq_df.shape}")

    oi_df = load_coinalyze_oi()
    print(f"  Coinalyze OI: {oi_df.shape}")

    macro_dict = load_macro()
    macro_feats = build_macro_features(macro_dict)
    print(f"  Macro features: {macro_feats.shape}")

    dvol_df = load_dvol()
    dvol_feats = build_dvol_features(dvol_df)
    print(f"  DVOL features: {dvol_feats.shape}")

    etf_flows = load_etf_flows()
    etf_feats = build_etf_features(etf_flows)
    print(f"  ETF flow features: {etf_feats.shape}")

    fg = load_fear_greed()
    fg_feats = build_fear_greed_features(fg)
    print(f"  Fear & Greed features: {fg_feats.shape}")

    # ── Step 2: Build features per token ──
    print("\n[Step 2] Building per-token features...")

    # Determine which tokens have 1H data
    available_tokens = []
    for token in TOP_TOKENS:
        path = DATA / f"perp/1h_cache/{token}_1h.parquet"
        if path.exists():
            available_tokens.append(token)
    print(f"  Available tokens: {len(available_tokens)}/{len(TOP_TOKENS)}")

    token_features = {}
    for token in available_tokens:
        try:
            feats = build_token_features(
                token, metrics_df, liq_df, oi_df,
                macro_feats, dvol_feats, etf_feats, fg_feats,
            )
            if not feats.empty:
                token_features[token] = feats
        except Exception as e:
            print(f"  [ERROR] {token}: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n  Successfully built features for {len(token_features)} tokens")

    # ── Step 3: Cross-sectional features ──
    print("\n[Step 3] Adding cross-sectional features...")
    feature_names = set()
    for df in token_features.values():
        feature_names.update(df.columns)
    token_features = build_cross_sectional_features(
        token_features, list(feature_names)
    )

    # ── Step 4: Add interaction features ──
    print("\n[Step 4] Adding interaction features...")
    for token in token_features:
        token_features[token] = add_interaction_features(token_features[token])

    # ── Step 5: Build labels and combine ──
    print("\n[Step 5] Building labels and combining...")
    all_rows = []
    for token, feats in token_features.items():
        label = build_label(token)
        if label.empty:
            continue
        # Merge features + label on date
        combined = feats.join(label, how="inner")
        all_rows.append(combined)

    if not all_rows:
        print("[ERROR] No data to combine!")
        sys.exit(1)

    final = pd.concat(all_rows, axis=0)
    final.index.name = "date"

    # Drop rows where label is NaN
    pre_drop = len(final)
    final = final.dropna(subset=["label_7d_fwd"])
    print(f"  Dropped {pre_drop - len(final)} rows with NaN labels")

    # ── Step 6: Feature quality check ──
    print("\n[Step 6] Feature quality check...")
    feature_cols = [c for c in final.columns if c not in ["token", "label_7d_fwd"]]

    # Missing values
    missing_pct = final[feature_cols].isnull().mean().sort_values(ascending=False)
    high_missing = missing_pct[missing_pct > 0.5]
    if len(high_missing) > 0:
        print(f"\n  WARNING: {len(high_missing)} features with >50% missing:")
        for feat, pct in high_missing.head(10).items():
            print(f"    {feat}: {pct:.1%} missing")
        # Drop features with >70% missing
        drop_cols = missing_pct[missing_pct > 0.7].index.tolist()
        if drop_cols:
            print(f"\n  Dropping {len(drop_cols)} features with >70% missing")
            final = final.drop(columns=drop_cols)
            feature_cols = [c for c in feature_cols if c not in drop_cols]

    # ── Step 7: Save ──
    print("\n[Step 7] Saving feature matrix...")
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = OUT_DIR / "feature_matrix.parquet"
    final.to_parquet(out_path)
    print(f"  Saved to: {out_path}")

    # ── Step 8: Summary statistics ──
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Total rows:       {len(final):,}")
    print(f"  Date range:       {final.index.min()} to {final.index.max()}")
    print(f"  Tokens:           {final['token'].nunique()} ({', '.join(sorted(final['token'].unique()))})")
    print(f"  Feature columns:  {len(feature_cols)}")
    print(f"  Label column:     label_7d_fwd")
    print(f"  File size:        {out_path.stat().st_size / 1e6:.1f} MB")

    # IC (Information Coefficient) — rank correlation with label
    print("\n  Top 20 features by absolute IC (Spearman rank corr with 7d fwd return):")
    ics = {}
    label = final["label_7d_fwd"]
    for col in feature_cols:
        try:
            valid = final[[col, "label_7d_fwd"]].dropna()
            if len(valid) > 100:
                ic = valid[col].corr(valid["label_7d_fwd"], method="spearman")
                ics[col] = ic
        except Exception:
            pass

    ic_series = pd.Series(ics).sort_values(key=abs, ascending=False)
    for feat, ic_val in ic_series.head(20).items():
        print(f"    {feat:40s}  IC = {ic_val:+.4f}")

    print(f"\n  Mean absolute IC: {ic_series.abs().mean():.4f}")
    print(f"  Features with |IC| > 0.03: {(ic_series.abs() > 0.03).sum()}")
    print(f"  Features with |IC| > 0.05: {(ic_series.abs() > 0.05).sum()}")

    # Label distribution
    print(f"\n  Label stats:")
    print(f"    Mean:   {label.mean():.4f}")
    print(f"    Std:    {label.std():.4f}")
    print(f"    Median: {label.median():.4f}")
    print(f"    Min:    {label.min():.4f}")
    print(f"    Max:    {label.max():.4f}")

    print("\n[DONE] Feature matrix built successfully.")


if __name__ == "__main__":
    main()
