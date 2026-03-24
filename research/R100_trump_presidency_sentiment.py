"""
R100: Trump Presidency Sentiment -> Crypto Returns (Category-Specific)
======================================================================
Focused ONLY on the 2nd Trump presidency period (Jan 20, 2025+).

Key differences from previous analysis (trump_social_sentiment_analysis.py):
1. Presidency-only filter (no candidate/campaign noise)
2. Fine-grained economic category classification (5 categories)
3. Category-specific IC analysis and event studies
4. Cross-asset validation with DXY and US10Y
5. Event day detection (3+ posts in single category = event)

Kill criteria per category:
- IC > 0.05 with |t| > 2.0 to PASS
- <50 event days -> INSUFFICIENT DATA
- Otherwise -> KILL
"""

import json
import re
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

# ============================================================
# PATHS
# ============================================================
DATA_DIR = Path("/workspace/crypto_backtest/data/alternative/trump_social")
MACRO_DIR = Path("/workspace/crypto_backtest/data/alternative/macro")
BTC_PATH = Path("/workspace/crypto_backtest/data/spot/1h_cache/BTC_1h.parquet")
ETH_PATH = Path("/workspace/crypto_backtest/data/spot/1h_cache/ETH_1h.parquet")
OUTPUT_DIR = Path("/workspace/crypto_backtest/research")

PRESIDENCY_START = pd.Timestamp("2025-01-20", tz="UTC")

# ============================================================
# CATEGORY KEYWORD DEFINITIONS
# ============================================================

CATEGORIES = {
    "TARIFF": {
        "keywords": [
            "tariff", "tariffs", "trade war", "import tax", "section 301",
            "usmca", "reciprocal", "reciprocity", "duty", "duties",
            "trade deficit", "trade deal", "trade agreement", "trade balance",
            "fentanyl", "border tax",  # tariffs often justified via fentanyl
            "china trade", "european union trade", "eu trade", "canada trade",
            "mexico trade", "trade surplus", "customs",
        ],
        "exclude": [],  # Some keywords overlap with GEOPOLITICAL for 'china'
    },
    "FED": {
        "keywords": [
            "federal reserve", "the fed ", "the fed,", "the fed.",
            "interest rate", "interest rates", "jerome powell", "powell",
            "rate cut", "rate cuts", "rate hike", "monetary policy",
            "inflation", "deflation", "cpi", "consumer price",
            "quantitative", "money supply", "printing money",
        ],
        "exclude": [],
    },
    "GEOPOLITICAL": {
        "keywords": [
            "russia", "ukraine", "iran", "north korea", "kim jong",
            "nato", "military", "sanction", "sanctions",
            "war", "wars", "missile", "nuclear", "troops",
            "ceasefire", "peace deal", "hostage", "hostages",
            "israel", "hamas", "gaza", "hezbollah", "yemen", "houthi",
            "taiwan", "south china sea",
        ],
        "exclude": [
            "trade war",  # That's TARIFF category
        ],
    },
    "ECONOMIC_CONFIDENCE": {
        "keywords": [
            "jobs", "gdp", "stock market", "economy", "economic",
            "record high", "all time high", "boom", "booming",
            "prosperity", "growth", "strong dollar", "great again",
            "winning", "employment", "unemployment", "manufacturing",
            "energy independence", "energy dominance", "golden age",
            "american dream", "greatest economy",
        ],
        "exclude": [],
    },
    "CRYPTO": {
        "keywords": [
            "bitcoin", "btc", "crypto", "cryptocurrency",
            "digital asset", "digital assets", "stablecoin", "stablecoins",
            "blockchain", "defi", "web3", "nft",
            "crypto reserve", "bitcoin reserve", "strategic reserve",
            "digital gold", "digital currency", "cbdc",
        ],
        "exclude": [],
    },
}


def categorize_post(text):
    """Categorize a post into zero or more economic categories."""
    if not text or len(str(text)) < 3:
        return []

    text_lower = str(text).lower()
    matched = []

    for cat_name, cat_def in CATEGORIES.items():
        # Check excludes first
        excluded = False
        for excl in cat_def.get("exclude", []):
            if excl.lower() in text_lower:
                excluded = True
                break

        # Check keywords
        hit = False
        for kw in cat_def["keywords"]:
            # Use word boundary matching for short keywords to avoid false positives
            if len(kw) <= 4:
                if re.search(r'\b' + re.escape(kw.lower()) + r'\b', text_lower):
                    hit = True
                    break
            else:
                if kw.lower() in text_lower:
                    hit = True
                    break

        # Special handling for GEOPOLITICAL: exclude if also TARIFF-matched
        # (trade war is TARIFF, not GEO)
        if cat_name == "GEOPOLITICAL" and hit:
            # If "trade war" triggered GEO via "war", check if it's just trade war
            if "trade war" in text_lower and not any(
                kw.lower() in text_lower
                for kw in ["russia", "ukraine", "iran", "nato", "military",
                           "sanction", "missile", "nuclear", "troops",
                           "israel", "hamas", "gaza", "ceasefire", "hostage",
                           "taiwan", "yemen", "houthi", "north korea"]
            ):
                hit = False

        if hit and not excluded:
            matched.append(cat_name)

    return matched


def score_sentiment(text):
    """Score overall sentiment of a post (simple positive/negative)."""
    if not text or len(str(text)) < 3:
        return 0

    text_lower = str(text).lower()

    positive = [
        "great", "best", "winning", "amazing", "incredible", "beautiful",
        "record", "boom", "strong", "success", "victory", "excellent",
        "tremendous", "fantastic", "wonderful", "thriving", "soaring",
        "good news", "great news", "proud", "love",
    ]
    negative = [
        "disaster", "crash", "terrible", "horrible", "worst", "failing",
        "failed", "bad", "weak", "pathetic", "stupid", "crooked",
        "corrupt", "radical", "threat", "danger", "crisis", "collapse",
        "recession", "destroying", "destroyed", "shame", "sad",
    ]

    pos_count = sum(1 for kw in positive if kw in text_lower)
    neg_count = sum(1 for kw in negative if kw in text_lower)
    return pos_count - neg_count


# ============================================================
# DATA LOADING
# ============================================================

def load_presidency_posts():
    """Load and filter posts to presidency period only (Jan 20, 2025+)."""
    df = pd.read_csv(DATA_DIR / "posts_scored.csv")
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601", utc=True)

    # Filter to presidency
    pres = df[df["timestamp"] >= PRESIDENCY_START].copy()
    pres = pres.sort_values("timestamp").reset_index(drop=True)

    # Add categories and sentiment
    pres["categories"] = pres["text"].apply(categorize_post)
    pres["n_categories"] = pres["categories"].apply(len)
    pres["sentiment"] = pres["text"].apply(score_sentiment)

    # Create binary category columns
    for cat in CATEGORIES:
        pres[f"is_{cat}"] = pres["categories"].apply(lambda cats: cat in cats)

    print(f"Presidency posts: {len(pres)}")
    print(f"  Date range: {pres['timestamp'].min()} to {pres['timestamp'].max()}")
    print(f"  Posts with text: {(pres['text'].str.len() > 3).sum()}")
    print(f"  Category counts:")
    for cat in CATEGORIES:
        n = pres[f"is_{cat}"].sum()
        print(f"    {cat:25s}: {n:4d} posts ({n/len(pres)*100:.1f}%)")
    uncategorized = (pres["n_categories"] == 0).sum()
    print(f"    {'UNCATEGORIZED':25s}: {uncategorized:4d} posts ({uncategorized/len(pres)*100:.1f}%)")

    return pres


def load_price_data():
    """Load BTC and ETH hourly data, compute daily and forward returns."""
    result = {}
    for name, path in [("BTC", BTC_PATH), ("ETH", ETH_PATH)]:
        df = pd.read_parquet(path)
        df.index = pd.to_datetime(df.index, utc=True)
        df = df.sort_index()

        # Forward returns (hourly)
        for h in [1, 4, 24, 48, 72, 168]:
            df[f"fwd_ret_{h}h"] = df["close"].shift(-h) / df["close"] - 1

        # Daily close (using last hourly bar per day)
        daily = df.resample("1D").last()
        daily["date"] = pd.to_datetime(daily.index.date)  # tz-naive date for merging

        # Daily forward returns
        for d in [1, 3, 7, 14]:
            daily[f"fwd_ret_{d}d"] = daily["close"].shift(-d) / daily["close"] - 1

        # Daily realized vol
        df["ret_1h"] = df["close"].pct_change()
        daily_vol = (df["ret_1h"] ** 2).resample("1D").sum().apply(np.sqrt)
        daily_vol.name = "realized_vol"
        daily = daily.join(daily_vol)

        result[name] = {"hourly": df, "daily": daily}

    return result


def load_macro_data():
    """Load DXY and US10Y data for cross-asset validation."""
    macro = {}

    for name, fname in [("DXY", "usd_index.parquet"), ("US10Y", "us10y_yield.parquet")]:
        path = MACRO_DIR / fname
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.sort_values("Date").reset_index(drop=True)
        df = df.set_index("Date")

        # Forward returns
        for d in [1, 3, 7, 14]:
            df[f"fwd_ret_{d}d"] = df["Close"].shift(-d) / df["Close"] - 1

        # Daily change
        df["daily_chg"] = df["Close"].pct_change()

        macro[name] = df

    return macro


# ============================================================
# SIGNAL CONSTRUCTION (Category-Specific)
# ============================================================

def build_category_daily_signals(posts):
    """Build daily signals for each category."""
    posts = posts.copy()
    posts["date"] = posts["timestamp"].dt.tz_localize(None).dt.normalize()

    all_signals = {}

    for cat in CATEGORIES:
        cat_posts = posts[posts[f"is_{cat}"]].copy()

        if len(cat_posts) == 0:
            continue

        # Daily aggregates
        daily = cat_posts.groupby("date").agg(
            post_count=("text", "count"),
            mean_sentiment=("sentiment", "mean"),
            total_sentiment=("sentiment", "sum"),
            max_sentiment=("sentiment", "max"),
            min_sentiment=("sentiment", "min"),
            mean_market_score=("market_score", "mean"),
        ).reset_index()

        daily["date"] = pd.to_datetime(daily["date"])

        # Event days: 3+ posts in single category
        daily["is_event_day"] = daily["post_count"] >= 3

        # Sentiment intensity = total_sentiment / post_count
        daily["sentiment_intensity"] = daily["total_sentiment"] / daily["post_count"].clip(lower=1)

        # Z-score of post count (rolling 30-day)
        daily = daily.sort_values("date")
        rolling_mean = daily["post_count"].rolling(30, min_periods=5).mean()
        rolling_std = daily["post_count"].rolling(30, min_periods=5).std().clip(lower=0.1)
        daily["count_zscore"] = (daily["post_count"] - rolling_mean) / rolling_std

        all_signals[cat] = daily

    # Also build overall daily signals
    overall = posts.groupby("date").agg(
        total_post_count=("text", "count"),
        overall_sentiment=("sentiment", "mean"),
        n_categories_mean=("n_categories", "mean"),
    ).reset_index()
    overall["date"] = pd.to_datetime(overall["date"])
    all_signals["OVERALL"] = overall

    return all_signals


# ============================================================
# IC COMPUTATION
# ============================================================

def compute_ic(signal, forward_ret, method="spearman"):
    """Compute rank IC (Spearman correlation) with t-stat."""
    mask = signal.notna() & forward_ret.notna()
    n = mask.sum()
    if n < 20:
        return np.nan, np.nan, np.nan, 0

    s = signal[mask].values
    r = forward_ret[mask].values

    # Check for constant signal
    if np.std(s) == 0 or np.std(r) == 0:
        return 0.0, 1.0, 0.0, n

    if method == "spearman":
        ic, pval = stats.spearmanr(s, r)
    else:
        ic, pval = stats.pearsonr(s, r)

    # t-stat
    if abs(ic) < 1 and n > 2:
        t_stat = ic * np.sqrt(n - 2) / np.sqrt(1 - ic**2)
    else:
        t_stat = 0.0

    return ic, pval, t_stat, n


def category_ic_analysis(cat_daily, price_daily, cat_name, asset_name):
    """Run IC analysis for a single category's signals vs an asset's returns."""
    # Merge on date
    merged = pd.merge(
        cat_daily,
        price_daily[["date", "close", "realized_vol",
                      "fwd_ret_1d", "fwd_ret_3d", "fwd_ret_7d", "fwd_ret_14d"]],
        on="date", how="inner"
    )
    merged = merged.sort_values("date").reset_index(drop=True)

    if len(merged) < 30:
        return pd.DataFrame(), merged

    signal_cols = ["post_count", "mean_sentiment", "sentiment_intensity",
                   "count_zscore"]
    # Only use columns that exist
    signal_cols = [c for c in signal_cols if c in merged.columns]

    return_horizons = ["fwd_ret_1d", "fwd_ret_3d", "fwd_ret_7d", "fwd_ret_14d"]

    results = []
    for sig in signal_cols:
        for ret in return_horizons:
            ic, pval, t_stat, n = compute_ic(merged[sig], merged[ret])

            # IS/OOS split (first half / second half)
            mid = len(merged) // 2
            ic_is, _, t_is, n_is = compute_ic(merged[sig].iloc[:mid], merged[ret].iloc[:mid])
            ic_oos, _, t_oos, n_oos = compute_ic(merged[sig].iloc[mid:], merged[ret].iloc[mid:])

            sign_flip = False
            if not np.isnan(ic_is) and not np.isnan(ic_oos) and ic_is != 0 and ic_oos != 0:
                sign_flip = np.sign(ic_is) != np.sign(ic_oos)

            results.append({
                "category": cat_name,
                "asset": asset_name,
                "signal": sig,
                "return_horizon": ret,
                "IC": ic,
                "pval": pval,
                "t_stat": t_stat,
                "n_obs": n,
                "IC_IS": ic_is,
                "IC_OOS": ic_oos,
                "sign_flip": sign_flip,
            })

    return pd.DataFrame(results), merged


# ============================================================
# EVENT STUDY
# ============================================================

def event_study_daily(cat_daily, price_daily, cat_name, asset_name):
    """
    Event study: compare returns on event days (3+ cat posts) vs non-event days.
    """
    merged = pd.merge(
        cat_daily,
        price_daily[["date", "close", "fwd_ret_1d", "fwd_ret_3d", "fwd_ret_7d", "fwd_ret_14d"]],
        on="date", how="inner"
    )

    if "is_event_day" not in merged.columns:
        return None

    event_days = merged[merged["is_event_day"]]
    non_event_days = merged[~merged["is_event_day"]]

    n_event = len(event_days)
    n_non_event = len(non_event_days)

    if n_event < 5:
        return {
            "category": cat_name,
            "asset": asset_name,
            "n_event_days": n_event,
            "n_non_event_days": n_non_event,
            "horizons": {},
            "status": "INSUFFICIENT_DATA",
        }

    horizons = {}
    for ret_col in ["fwd_ret_1d", "fwd_ret_3d", "fwd_ret_7d", "fwd_ret_14d"]:
        e_rets = event_days[ret_col].dropna()
        ne_rets = non_event_days[ret_col].dropna()

        if len(e_rets) < 5 or len(ne_rets) < 5:
            continue

        e_mean = e_rets.mean()
        ne_mean = ne_rets.mean()
        diff = e_mean - ne_mean

        # Two-sample t-test (Welch)
        t_stat, pval = stats.ttest_ind(e_rets, ne_rets, equal_var=False)

        # Also do Mann-Whitney U test (non-parametric)
        u_stat, u_pval = stats.mannwhitneyu(e_rets, ne_rets, alternative="two-sided")

        horizons[ret_col] = {
            "event_mean": e_mean,
            "event_median": e_rets.median(),
            "non_event_mean": ne_mean,
            "non_event_median": ne_rets.median(),
            "diff_mean": diff,
            "t_stat": t_stat,
            "pval_t": pval,
            "pval_mwu": u_pval,
            "n_event": len(e_rets),
            "n_non_event": len(ne_rets),
            "event_pct_positive": (e_rets > 0).mean(),
            "non_event_pct_positive": (ne_rets > 0).mean(),
        }

    return {
        "category": cat_name,
        "asset": asset_name,
        "n_event_days": n_event,
        "n_non_event_days": n_non_event,
        "horizons": horizons,
        "status": "OK" if n_event >= 50 else "LOW_DATA",
    }


# ============================================================
# NEGATIVE SENTIMENT DEEP DIVE (for TARIFF)
# ============================================================

def negative_sentiment_event_study(posts, price_hourly, cat_name, asset_name):
    """
    For a given category, look at posts with negative sentiment and measure
    returns in the hours after.
    """
    cat_posts = posts[posts[f"is_{cat_name}"]].copy()
    neg_posts = cat_posts[cat_posts["sentiment"] < 0].copy()

    if len(neg_posts) < 10:
        return None

    price_hourly = price_hourly.copy()
    price_hourly.index = pd.to_datetime(price_hourly.index, utc=True)
    neg_posts["timestamp"] = pd.to_datetime(neg_posts["timestamp"], utc=True)

    windows = {
        "post_1h": (0, 1),
        "post_4h": (0, 4),
        "post_24h": (0, 24),
        "post_48h": (0, 48),
    }

    event_returns = []
    for _, post in neg_posts.iterrows():
        ts_hour = post["timestamp"].floor("h")
        row = {"timestamp": post["timestamp"]}

        for wname, (start_h, end_h) in windows.items():
            t0 = ts_hour + pd.Timedelta(hours=start_h)
            t1 = ts_hour + pd.Timedelta(hours=end_h)

            try:
                if t0 >= price_hourly.index[0] and t1 <= price_hourly.index[-1]:
                    idx0 = price_hourly.index.asof(t0)
                    idx1 = price_hourly.index.asof(t1)
                    if pd.notna(idx0) and pd.notna(idx1):
                        p0 = price_hourly.loc[idx0, "close"]
                        p1 = price_hourly.loc[idx1, "close"]
                        row[wname] = (p1 / p0) - 1
                    else:
                        row[wname] = np.nan
                else:
                    row[wname] = np.nan
            except:
                row[wname] = np.nan

        event_returns.append(row)

    edf = pd.DataFrame(event_returns)

    summary = {}
    for wname in windows:
        vals = edf[wname].dropna()
        if len(vals) < 5:
            continue
        mean_ret = vals.mean()
        std_ret = vals.std()
        t_stat = mean_ret / (std_ret / np.sqrt(len(vals))) if std_ret > 0 else 0
        pval = 2 * (1 - stats.t.cdf(abs(t_stat), df=len(vals) - 1))

        summary[wname] = {
            "n": len(vals),
            "mean_ret": mean_ret,
            "median_ret": vals.median(),
            "std_ret": std_ret,
            "t_stat": t_stat,
            "pval": pval,
            "pct_negative": (vals < 0).mean(),
        }

    return {
        "category": cat_name,
        "asset": asset_name,
        "n_neg_posts": len(neg_posts),
        "summary": summary,
    }


# ============================================================
# CROSS-ASSET VALIDATION
# ============================================================

def cross_asset_validation(cat_daily_signals, macro_data):
    """Check if tariff/fed posts correlate with DXY/US10Y moves."""
    results = []

    for cat_name, cat_daily in cat_daily_signals.items():
        if cat_name in ("OVERALL",):
            continue

        for macro_name, macro_df in macro_data.items():
            # Merge on date
            cat_daily_copy = cat_daily.copy()
            cat_daily_copy["date"] = pd.to_datetime(cat_daily_copy["date"])

            macro_df_copy = macro_df.copy()
            macro_df_copy = macro_df_copy.reset_index()
            macro_df_copy = macro_df_copy.rename(columns={"Date": "date"})
            macro_df_copy["date"] = pd.to_datetime(macro_df_copy["date"])

            merged = pd.merge(
                cat_daily_copy,
                macro_df_copy[["date", "fwd_ret_1d", "fwd_ret_3d", "fwd_ret_7d", "daily_chg"]],
                on="date", how="inner",
                suffixes=("", f"_{macro_name}")
            )

            if len(merged) < 20:
                continue

            for sig_col in ["post_count", "mean_sentiment", "sentiment_intensity"]:
                if sig_col not in merged.columns:
                    continue
                for ret_col in ["fwd_ret_1d", "fwd_ret_3d", "fwd_ret_7d"]:
                    ic, pval, t_stat, n = compute_ic(merged[sig_col], merged[ret_col])
                    results.append({
                        "category": cat_name,
                        "macro_asset": macro_name,
                        "signal": sig_col,
                        "return_horizon": ret_col,
                        "IC": ic,
                        "pval": pval,
                        "t_stat": t_stat,
                        "n_obs": n,
                    })

    return pd.DataFrame(results)


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 80)
    print("R100: TRUMP PRESIDENCY SENTIMENT -> CRYPTO RETURNS (CATEGORY ANALYSIS)")
    print(f"Period: Jan 20, 2025 to present")
    print("=" * 80)

    # ---- Load data ----
    print("\n[1] Loading presidency-period posts...")
    posts = load_presidency_posts()

    print("\n[2] Loading price data...")
    prices = load_price_data()
    for asset in prices:
        h = prices[asset]["hourly"]
        d = prices[asset]["daily"]
        print(f"  {asset}: {len(h)} hourly bars, {len(d)} daily bars")
        print(f"    Range: {h.index.min()} to {h.index.max()}")

    print("\n[3] Loading macro data...")
    macro = load_macro_data()
    for name, df in macro.items():
        print(f"  {name}: {len(df)} daily bars, last: {df.index.max()}")

    # ---- Build category signals ----
    print("\n[4] Building category-specific daily signals...")
    cat_signals = build_category_daily_signals(posts)

    for cat, daily in cat_signals.items():
        n_days = len(daily)
        if "is_event_day" in daily.columns:
            n_events = daily["is_event_day"].sum()
            print(f"  {cat:25s}: {n_days:4d} active days, {n_events:3d} event days (3+ posts)")
        else:
            print(f"  {cat:25s}: {n_days:4d} active days")

    # ---- IC Analysis per category ----
    print("\n[5] Category-specific IC Analysis...")
    all_ic_results = []
    all_merged = {}

    for cat_name, cat_daily in cat_signals.items():
        if cat_name == "OVERALL":
            continue
        for asset_name in ["BTC", "ETH"]:
            price_daily = prices[asset_name]["daily"]
            ic_df, merged = category_ic_analysis(cat_daily, price_daily, cat_name, asset_name)
            if len(ic_df) > 0:
                all_ic_results.append(ic_df)
                all_merged[f"{cat_name}_{asset_name}"] = merged

    if all_ic_results:
        ic_all = pd.concat(all_ic_results, ignore_index=True)
    else:
        ic_all = pd.DataFrame()

    # Print IC results
    print("\n  Top IC results (sorted by |IC|):")
    if len(ic_all) > 0:
        ic_all["abs_IC"] = ic_all["IC"].abs()
        ic_sorted = ic_all.sort_values("abs_IC", ascending=False)
        for _, row in ic_sorted.head(40).iterrows():
            flip = " [FLIP]" if row["sign_flip"] else ""
            star = " ***" if abs(row["t_stat"]) >= 2.0 else (" *" if row["pval"] < 0.10 else "")
            print(f"    {row['category']:20s} {row['asset']:4s} {row['signal']:25s} -> "
                  f"{row['return_horizon']:12s}: IC={row['IC']:+.4f} t={row['t_stat']:+.2f} "
                  f"p={row['pval']:.3f} n={row['n_obs']:4.0f} "
                  f"IS={row['IC_IS']:+.4f} OOS={row['IC_OOS']:+.4f}{flip}{star}")

    # ---- Event Studies ----
    print("\n[6] Event Studies (event day = 3+ posts in category)...")
    all_event_results = []

    for cat_name, cat_daily in cat_signals.items():
        if cat_name == "OVERALL":
            continue
        for asset_name in ["BTC", "ETH"]:
            price_daily = prices[asset_name]["daily"]
            event_result = event_study_daily(cat_daily, price_daily, cat_name, asset_name)
            if event_result:
                all_event_results.append(event_result)

                status = event_result["status"]
                n_ev = event_result["n_event_days"]
                print(f"\n  {cat_name:20s} {asset_name:4s}: {n_ev} event days [{status}]")
                for ret, h in event_result["horizons"].items():
                    sig = " ***" if h["pval_t"] < 0.01 else (" **" if h["pval_t"] < 0.05 else (" *" if h["pval_t"] < 0.10 else ""))
                    print(f"    {ret:12s}: event={h['event_mean']*100:+.3f}% vs non-event={h['non_event_mean']*100:+.3f}% "
                          f"diff={h['diff_mean']*100:+.3f}% t={h['t_stat']:+.2f} p={h['pval_t']:.3f}{sig}")

    # ---- Negative Sentiment Deep Dive ----
    print("\n[7] Negative Sentiment Deep Dive (hourly event study)...")
    neg_results = []

    for cat_name in ["TARIFF", "FED", "GEOPOLITICAL"]:
        for asset_name in ["BTC"]:
            result = negative_sentiment_event_study(
                posts, prices[asset_name]["hourly"], cat_name, asset_name
            )
            if result:
                neg_results.append(result)
                print(f"\n  {cat_name} negative posts -> {asset_name}: {result['n_neg_posts']} events")
                for wname, s in result["summary"].items():
                    sig = " ***" if s["pval"] < 0.01 else (" **" if s["pval"] < 0.05 else (" *" if s["pval"] < 0.10 else ""))
                    print(f"    {wname:12s}: mean={s['mean_ret']*100:+.4f}% t={s['t_stat']:+.2f} "
                          f"p={s['pval']:.3f} %neg={s['pct_negative']:.1%} n={s['n']}{sig}")

    # ---- Cross-Asset Validation ----
    print("\n[8] Cross-Asset Validation (DXY, US10Y)...")
    cross_asset = cross_asset_validation(cat_signals, macro)

    if len(cross_asset) > 0:
        cross_asset["abs_IC"] = cross_asset["IC"].abs()
        cross_sorted = cross_asset.sort_values("abs_IC", ascending=False)
        print("\n  Top cross-asset correlations:")
        for _, row in cross_sorted.head(20).iterrows():
            star = " ***" if abs(row["t_stat"]) >= 2.0 else (" *" if row["pval"] < 0.10 else "")
            print(f"    {row['category']:20s} -> {row['macro_asset']:5s} {row['signal']:25s} "
                  f"{row['return_horizon']:12s}: IC={row['IC']:+.4f} t={row['t_stat']:+.2f} "
                  f"p={row['pval']:.3f}{star}")

    # ============================================================
    # VERDICTS PER CATEGORY
    # ============================================================
    print("\n" + "=" * 80)
    print("CATEGORY VERDICTS")
    print("=" * 80)

    verdicts = {}
    for cat_name in CATEGORIES:
        print(f"\n--- {cat_name} ---")

        # Check IC criteria
        if len(ic_all) > 0:
            cat_ic = ic_all[ic_all["category"] == cat_name]
            passing_ic = cat_ic[(cat_ic["IC"].abs() > 0.05) & (cat_ic["t_stat"].abs() > 2.0)]
            marginal_ic = cat_ic[(cat_ic["IC"].abs() > 0.03) & (cat_ic["pval"] < 0.10)]
        else:
            passing_ic = pd.DataFrame()
            marginal_ic = pd.DataFrame()

        # Check event study criteria
        cat_events = [e for e in all_event_results if e["category"] == cat_name]
        significant_events = []
        for evt in cat_events:
            for ret, h in evt["horizons"].items():
                if h["pval_t"] < 0.05:
                    significant_events.append((evt["asset"], ret, h))

        # Check data sufficiency
        n_event_days = 0
        for evt in cat_events:
            n_event_days = max(n_event_days, evt["n_event_days"])

        # Verdict
        if len(passing_ic) >= 1 and len(significant_events) >= 1:
            verdict = "PASS"
            reason = (f"{len(passing_ic)} IC signal(s) pass threshold, "
                      f"{len(significant_events)} significant event study result(s)")
        elif len(passing_ic) >= 1:
            verdict = "WEAK PASS"
            reason = f"{len(passing_ic)} IC signal(s) pass, but event study not significant"
        elif len(significant_events) >= 2:
            verdict = "WEAK PASS (event-driven)"
            reason = f"IC below threshold, but {len(significant_events)} significant event results"
        elif n_event_days < 50:
            if len(marginal_ic) > 0 or len(significant_events) > 0:
                verdict = "INSUFFICIENT DATA"
                reason = f"Only {n_event_days} event days. Marginal signals exist but need more data."
            else:
                verdict = "INSUFFICIENT DATA"
                reason = f"Only {n_event_days} event days. Cannot make determination."
        elif len(marginal_ic) >= 2:
            verdict = "NEEDS MORE DATA"
            reason = f"Marginal IC signals ({len(marginal_ic)}) but none robust"
        else:
            verdict = "KILL"
            reason = "No IC > 0.05 with |t| > 2.0, no significant event study"

        verdicts[cat_name] = {"verdict": verdict, "reason": reason}
        print(f"  VERDICT: {verdict}")
        print(f"  REASON: {reason}")
        print(f"  Event days: {n_event_days}")
        if len(passing_ic) > 0:
            print(f"  Passing ICs:")
            for _, r in passing_ic.iterrows():
                print(f"    {r['asset']} {r['signal']} -> {r['return_horizon']}: IC={r['IC']:+.4f} t={r['t_stat']:+.2f}")
        if significant_events:
            print(f"  Significant events:")
            for asset, ret, h in significant_events:
                print(f"    {asset} {ret}: diff={h['diff_mean']*100:+.3f}% t={h['t_stat']:+.2f} p={h['pval_t']:.3f}")

    # ---- Overall Verdict ----
    print("\n" + "=" * 80)
    print("OVERALL SUMMARY")
    print("=" * 80)

    for cat, v in verdicts.items():
        print(f"  {cat:25s}: {v['verdict']:20s} | {v['reason']}")

    any_pass = any(v["verdict"] in ("PASS", "WEAK PASS", "WEAK PASS (event-driven)")
                   for v in verdicts.values())
    if any_pass:
        print(f"\n  OVERALL: At least one category shows signal. Worth pursuing category-specific strategies.")
    else:
        all_insuff = all(v["verdict"].startswith("INSUFFICIENT") for v in verdicts.values())
        if all_insuff:
            print(f"\n  OVERALL: Insufficient data across all categories. Revisit after more presidency time.")
        else:
            print(f"\n  OVERALL: KILL. No category shows reliable predictive power.")

    return {
        "ic_all": ic_all,
        "event_results": all_event_results,
        "neg_results": neg_results,
        "cross_asset": cross_asset,
        "verdicts": verdicts,
        "posts": posts,
        "cat_signals": cat_signals,
    }


if __name__ == "__main__":
    results = main()
