"""
Trump Social Media Sentiment → BTC Returns Signal Analysis
===========================================================
Investigates whether Trump's social media posts (Truth Social + Twitter)
can predict short-term crypto returns.

Data sources:
- Truth Social: CNN-hosted archive (ix.cnn.io) — Feb 2022 to Mar 2026
- Twitter: CompleteTrumpTweetsArchive (GitHub) — 2017 to Jan 2021
- BTC hourly: spot/1h_cache/BTC_1h.parquet — 2020 to Mar 2026
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

DATA_DIR = Path("/workspace/crypto_backtest/data/alternative/trump_social")
BTC_PATH = Path("/workspace/crypto_backtest/data/spot/1h_cache/BTC_1h.parquet")
OUTPUT_DIR = Path("/workspace/crypto_backtest/research")

# ============================================================
# STEP 1: Parse and clean Truth Social data
# ============================================================

def parse_truth_social():
    """Parse CNN-hosted Truth Social JSON archive."""
    path = DATA_DIR / "truth_archive_raw.json"
    with open(path) as f:
        posts = json.load(f)

    records = []
    for p in posts:
        # Strip HTML tags from content
        text = re.sub(r'<[^>]+>', ' ', p.get('content', ''))
        text = re.sub(r'\s+', ' ', text).strip()
        # Decode HTML entities
        text = text.replace('&amp;', '&').replace('&#39;', "'").replace('&quot;', '"')
        text = text.replace('&lt;', '<').replace('&gt;', '>')

        ts = p.get('created_at', '')
        if ts:
            try:
                dt = pd.to_datetime(ts, utc=True)
            except:
                continue
            records.append({
                'timestamp': dt,
                'text': text,
                'source': 'truth_social',
                'replies': p.get('replies_count', 0),
                'reblogs': p.get('reblogs_count', 0),
                'favorites': p.get('favourites_count', 0),
                'has_media': len(p.get('media', [])) > 0,
            })

    df = pd.DataFrame(records)
    df = df.sort_values('timestamp').reset_index(drop=True)
    print(f"Truth Social posts: {len(df)}")
    print(f"  Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
    print(f"  Posts with text: {(df['text'].str.len() > 0).sum()}")
    return df


def parse_twitter():
    """Parse Trump Twitter CSV archives."""
    records = []
    for fname in ['trump_tweets_in_office.csv', 'trump_tweets_bf_office.csv']:
        path = DATA_DIR / fname
        if not path.exists():
            continue
        # Read CSV — format: ID, Time, Tweet URL, Tweet Text
        # Tweet text may contain commas, so use maxsplit on raw lines
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
            # Skip header
            for line in lines[1:]:
                line = line.strip()
                if not line:
                    continue
                # Split on first 3 commas: ID, Time, URL, rest-is-text
                parts = line.split(',', 3)
                if len(parts) < 4:
                    continue
                ts_str = parts[1].strip()
                text = parts[3].strip().strip('"')
                if ts_str and text:
                    try:
                        dt = pd.to_datetime(ts_str, utc=True)
                    except:
                        try:
                            dt = pd.to_datetime(ts_str).tz_localize('UTC')
                        except:
                            continue
                    records.append({
                        'timestamp': dt,
                        'text': text,
                        'source': 'twitter',
                        'replies': 0,
                        'reblogs': 0,
                        'favorites': 0,
                        'has_media': False,
                    })
        except Exception as e:
            print(f"  Error reading {fname}: {e}")

    df = pd.DataFrame(records)
    if len(df) > 0:
        df = df.sort_values('timestamp').reset_index(drop=True)
        # Only keep tweets from 2020 onward (overlap with BTC data)
        df = df[df['timestamp'] >= '2020-01-01'].reset_index(drop=True)
    print(f"Twitter posts (2020+): {len(df)}")
    if len(df) > 0:
        print(f"  Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
    return df


# ============================================================
# STEP 2: Keyword-based sentiment scoring
# ============================================================

# Keywords organized by category
CRYPTO_POSITIVE = [
    'bitcoin', 'btc', 'crypto', 'cryptocurrency', 'digital asset', 'digital gold',
    'blockchain', 'strategic reserve', 'crypto reserve', 'bitcoin reserve',
    'defi', 'web3', 'nft', 'digital currency', 'stablecoin',
]

CRYPTO_NEGATIVE = [
    'crypto crash', 'crypto ponzi', 'crypto scam', 'bitcoin scam',
]

PRO_MARKET = [
    'great economy', 'stock market', 'all time high', 'ath', 'boom',
    'winning', 'prosperity', 'growth', 'jobs', 'strong dollar',
    'cut taxes', 'tax cut', 'deregulat', 'innovation',
]

ANTI_MARKET = [
    'tariff', 'trade war', 'china', 'sanction', 'ban',
    'disaster', 'crash', 'recession', 'inflation', 'failing',
    'war', 'attack', 'threat', 'enemy', 'radical',
]

FED_KEYWORDS = [
    'federal reserve', 'fed ', 'interest rate', 'jerome powell', 'powell',
    'rate cut', 'rate hike', 'monetary policy', 'printing money',
]

POLICY_UNCERTAINTY = [
    'executive order', 'emergency', 'national security', 'invoke',
    'terminate', 'end', 'cancel', 'reverse', 'shut down', 'close',
]


def keyword_count(text, keywords):
    """Count keyword occurrences in text (case-insensitive) using word boundaries."""
    text_lower = text.lower()
    count = 0
    for kw in keywords:
        count += len(re.findall(r'\b' + re.escape(kw.lower()) + r'\b', text_lower))
    return count


def score_post(text):
    """Score a single post across multiple dimensions."""
    if not text or len(text) < 3:
        return {
            'crypto_mention': False,
            'crypto_score': 0,
            'market_score': 0,
            'fed_mention': False,
            'tariff_mention': False,
            'uncertainty_score': 0,
            'text_length': 0,
        }

    text_lower = text.lower()

    crypto_pos = keyword_count(text, CRYPTO_POSITIVE)
    crypto_neg = keyword_count(text, CRYPTO_NEGATIVE)
    pro_market = keyword_count(text, PRO_MARKET)
    anti_market = keyword_count(text, ANTI_MARKET)
    fed_count = keyword_count(text, FED_KEYWORDS)
    uncertainty = keyword_count(text, POLICY_UNCERTAINTY)

    return {
        'crypto_mention': crypto_pos > 0 or crypto_neg > 0,
        'crypto_score': crypto_pos - crypto_neg,
        'market_score': pro_market - anti_market,
        'fed_mention': fed_count > 0,
        'tariff_mention': 'tariff' in text_lower or 'trade war' in text_lower,
        'uncertainty_score': uncertainty + keyword_count(text, ANTI_MARKET),
        'text_length': len(text),
    }


def build_daily_signals(posts_df):
    """Aggregate post-level scores into daily signals."""
    # Score each post
    scores = posts_df['text'].apply(score_post)
    score_df = pd.DataFrame(scores.tolist())
    posts_scored = pd.concat([posts_df, score_df], axis=1)

    # Floor timestamps to date
    posts_scored['date'] = posts_scored['timestamp'].dt.date

    # Daily aggregations
    daily = posts_scored.groupby('date').agg(
        post_count=('text', 'count'),
        avg_text_length=('text_length', 'mean'),
        crypto_post_count=('crypto_mention', 'sum'),
        crypto_score_sum=('crypto_score', 'sum'),
        market_score_sum=('market_score', 'sum'),
        fed_post_count=('fed_mention', 'sum'),
        tariff_post_count=('tariff_mention', 'sum'),
        uncertainty_score_sum=('uncertainty_score', 'sum'),
        total_engagement=('favorites', 'sum'),
    ).reset_index()

    daily['date'] = pd.to_datetime(daily['date'])

    # Derived signals
    daily['crypto_intensity'] = daily['crypto_score_sum'] / daily['post_count'].clip(lower=1)
    daily['market_sentiment'] = daily['market_score_sum'] / daily['post_count'].clip(lower=1)
    daily['uncertainty_intensity'] = daily['uncertainty_score_sum'] / daily['post_count'].clip(lower=1)
    daily['post_count_zscore'] = (daily['post_count'] - daily['post_count'].rolling(30).mean()) / daily['post_count'].rolling(30).std().clip(lower=0.1)
    daily['engagement_zscore'] = (daily['total_engagement'] - daily['total_engagement'].rolling(30).mean()) / daily['total_engagement'].rolling(30).std().clip(lower=1)

    return posts_scored, daily


# ============================================================
# STEP 3: Merge with BTC returns and compute ICs
# ============================================================

def load_btc_data():
    """Load BTC hourly data and compute various forward returns."""
    btc = pd.read_parquet(BTC_PATH)
    btc.index = pd.to_datetime(btc.index, utc=True)
    btc = btc.sort_index()

    # Hourly returns
    btc['ret_1h'] = btc['close'].pct_change()

    # Forward returns at various horizons
    for h in [1, 4, 24, 72, 168]:
        btc[f'fwd_ret_{h}h'] = btc['close'].shift(-h) / btc['close'] - 1

    # Daily close (use 00:00 UTC as daily close proxy)
    btc_daily = btc.resample('1D').last()
    btc_daily['date'] = btc_daily.index.date
    btc_daily['date'] = pd.to_datetime(btc_daily['date'])

    # Daily forward returns
    for h in [1, 2, 3, 5, 7]:
        btc_daily[f'fwd_ret_{h}d'] = btc_daily['close'].shift(-h) / btc_daily['close'] - 1

    # Daily realized volatility (using hourly returns)
    btc['ret_sq'] = btc['ret_1h'] ** 2
    daily_vol = btc['ret_sq'].resample('1D').sum().apply(np.sqrt)
    daily_vol.name = 'realized_vol'
    btc_daily = btc_daily.join(daily_vol)

    return btc, btc_daily


def compute_ic(signal, forward_ret, method='spearman'):
    """Compute rank IC (Spearman correlation)."""
    mask = signal.notna() & forward_ret.notna()
    if mask.sum() < 30:
        return np.nan, np.nan, 0
    s = signal[mask]
    r = forward_ret[mask]
    if method == 'spearman':
        ic, pval = stats.spearmanr(s, r)
    else:
        ic, pval = stats.pearsonr(s, r)
    return ic, pval, mask.sum()


def rolling_ic(signal, forward_ret, window=60):
    """Compute rolling IC over a window."""
    ics = []
    dates = signal.index
    for i in range(window, len(dates)):
        s_win = signal.iloc[i-window:i]
        r_win = forward_ret.iloc[i-window:i]
        mask = s_win.notna() & r_win.notna()
        if mask.sum() >= 20:
            ic, _ = stats.spearmanr(s_win[mask], r_win[mask])
            ics.append({'date': dates[i], 'ic': ic})
    return pd.DataFrame(ics)


def ic_analysis(daily_merged, signal_cols, return_cols, label="Full Sample"):
    """Comprehensive IC analysis across signals and horizons."""
    results = []
    for sig in signal_cols:
        for ret in return_cols:
            ic, pval, n = compute_ic(daily_merged[sig], daily_merged[ret])
            # IS/OOS split
            mid = len(daily_merged) // 2
            ic_is, pval_is, n_is = compute_ic(
                daily_merged[sig].iloc[:mid],
                daily_merged[ret].iloc[:mid]
            )
            ic_oos, pval_oos, n_oos = compute_ic(
                daily_merged[sig].iloc[mid:],
                daily_merged[ret].iloc[mid:]
            )

            # Sign consistency
            sign_flip = (np.sign(ic_is) != np.sign(ic_oos)) if (
                not np.isnan(ic_is) and not np.isnan(ic_oos) and ic_is != 0 and ic_oos != 0
            ) else True

            # t-stat (approximate)
            t_stat = ic * np.sqrt(n - 2) / np.sqrt(1 - ic**2) if abs(ic) < 1 and n > 2 else 0

            results.append({
                'signal': sig,
                'return_horizon': ret,
                'IC_full': ic,
                'pval_full': pval,
                't_stat': t_stat,
                'n_obs': n,
                'IC_IS': ic_is,
                'IC_OOS': ic_oos,
                'sign_flip': sign_flip,
                'sample': label,
            })

    return pd.DataFrame(results)


# ============================================================
# STEP 4: Event study around specific post types
# ============================================================

def event_study(posts_scored, btc_hourly, keyword_filter, label="Event"):
    """
    Event study: measure BTC returns in windows around specific post types.
    Compare vs random windows for significance.
    """
    # Filter posts matching keyword
    event_posts = posts_scored[keyword_filter(posts_scored)].copy()
    if len(event_posts) == 0:
        return None

    # Ensure timestamps are aligned
    btc_hourly = btc_hourly.copy()
    btc_hourly.index = pd.to_datetime(btc_hourly.index, utc=True)
    event_posts['timestamp'] = pd.to_datetime(event_posts['timestamp'], utc=True)

    # For each event, get BTC returns in windows
    windows = {
        'pre_1h': (-1, 0),
        'post_1h': (0, 1),
        'post_4h': (0, 4),
        'post_24h': (0, 24),
        'post_48h': (0, 48),
    }

    event_returns = []
    for _, post in event_posts.iterrows():
        ts = post['timestamp']
        # Round to nearest hour
        ts_hour = ts.floor('h')

        row = {}
        row['timestamp'] = ts
        row['text_preview'] = post['text'][:100]

        for wname, (start_h, end_h) in windows.items():
            t0 = ts_hour + pd.Timedelta(hours=start_h)
            t1 = ts_hour + pd.Timedelta(hours=end_h)

            # Get close prices using index.asof for nearest preceding timestamp
            try:
                if t0 >= btc_hourly.index[0]:
                    idx0 = btc_hourly.index.asof(t0)
                    p0 = btc_hourly.loc[idx0, 'close'] if pd.notna(idx0) else np.nan
                else:
                    p0 = np.nan
                if t1 <= btc_hourly.index[-1]:
                    idx1 = btc_hourly.index.asof(t1)
                    p1 = btc_hourly.loc[idx1, 'close'] if pd.notna(idx1) else np.nan
                else:
                    p1 = np.nan
            except:
                p0, p1 = np.nan, np.nan

            if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                row[wname] = (p1 / p0) - 1
            else:
                row[wname] = np.nan

        event_returns.append(row)

    event_df = pd.DataFrame(event_returns)

    # Summary stats
    summary = {}
    for wname in windows:
        vals = event_df[wname].dropna()
        if len(vals) < 5:
            continue
        mean_ret = vals.mean()
        std_ret = vals.std()
        t_stat_ev = mean_ret / (std_ret / np.sqrt(len(vals))) if std_ret > 0 else 0
        pval_ev = 2 * (1 - stats.t.cdf(abs(t_stat_ev), df=len(vals)-1))

        # Compare vs unconditional returns over same horizon
        h = windows[wname][1] - windows[wname][0]
        fwd_col = f'fwd_ret_{h}h' if f'fwd_ret_{h}h' in btc_hourly.columns else None
        uncond_mean = btc_hourly[fwd_col].mean() if fwd_col and fwd_col in btc_hourly.columns else 0

        summary[wname] = {
            'n_events': len(vals),
            'mean_ret': mean_ret,
            'median_ret': vals.median(),
            'std_ret': std_ret,
            't_stat': t_stat_ev,
            'pval': pval_ev,
            'uncond_mean': uncond_mean,
            'excess_ret': mean_ret - uncond_mean,
            'pct_positive': (vals > 0).mean(),
        }

    return {
        'label': label,
        'n_events': len(event_posts),
        'n_with_returns': len(event_df),
        'event_df': event_df,
        'summary': summary,
    }


# ============================================================
# STEP 5: Build hourly signal (post-level, not just daily)
# ============================================================

def build_hourly_signals(posts_scored, btc_hourly):
    """Build hourly-resolution signals from posts."""
    posts_scored = posts_scored.copy()
    posts_scored['hour'] = posts_scored['timestamp'].dt.floor('h')

    hourly_agg = posts_scored.groupby('hour').agg(
        post_count=('text', 'count'),
        crypto_score=('crypto_score', 'sum'),
        market_score=('market_score', 'sum'),
        uncertainty_score=('uncertainty_score', 'sum'),
    ).reindex(btc_hourly.index, fill_value=0)

    # Rolling signals
    hourly_agg['post_count_6h'] = hourly_agg['post_count'].rolling(6).sum()
    hourly_agg['post_count_24h'] = hourly_agg['post_count'].rolling(24).sum()
    hourly_agg['market_score_24h'] = hourly_agg['market_score'].rolling(24).sum()
    hourly_agg['uncertainty_24h'] = hourly_agg['uncertainty_score'].rolling(24).sum()

    return hourly_agg


# ============================================================
# MAIN EXECUTION
# ============================================================

def main():
    print("=" * 70)
    print("TRUMP SOCIAL MEDIA SENTIMENT → BTC RETURNS ANALYSIS")
    print("=" * 70)

    # ---- Load data ----
    print("\n[1] Loading and parsing social media data...")
    ts_df = parse_truth_social()
    tw_df = parse_twitter()

    # Combine sources
    all_posts = pd.concat([ts_df, tw_df], ignore_index=True)
    all_posts = all_posts.sort_values('timestamp').reset_index(drop=True)
    print(f"\nTotal combined posts: {len(all_posts)}")
    print(f"  Date range: {all_posts['timestamp'].min()} to {all_posts['timestamp'].max()}")

    # Posts per year
    all_posts['year'] = all_posts['timestamp'].dt.year
    print("\nPosts per year:")
    print(all_posts.groupby('year').size().to_string())

    print("\n[2] Loading BTC data...")
    btc_hourly, btc_daily = load_btc_data()
    print(f"  BTC hourly: {len(btc_hourly)} rows, {btc_hourly.index.min()} to {btc_hourly.index.max()}")
    print(f"  BTC daily: {len(btc_daily)} rows")

    # ---- Score posts ----
    print("\n[3] Scoring posts with keyword sentiment...")
    posts_scored, daily_signals = build_daily_signals(all_posts)

    # Print some stats
    print(f"\n  Posts mentioning crypto: {posts_scored['crypto_mention'].sum()}")
    print(f"  Posts mentioning Fed: {posts_scored['fed_mention'].sum()}")
    print(f"  Posts mentioning tariffs: {posts_scored['tariff_mention'].sum()}")
    print(f"  Avg daily post count: {daily_signals['post_count'].mean():.1f}")
    print(f"  Max daily post count: {daily_signals['post_count'].max()}")

    # Show some crypto posts
    crypto_posts = posts_scored[posts_scored['crypto_mention']].sort_values('timestamp', ascending=False)
    print(f"\n  Recent crypto-mentioning posts ({len(crypto_posts)} total):")
    for _, p in crypto_posts.head(10).iterrows():
        print(f"    [{p['timestamp'].strftime('%Y-%m-%d %H:%M')}] {p['text'][:120]}...")

    # Show some tariff posts
    tariff_posts = posts_scored[posts_scored['tariff_mention']].sort_values('timestamp', ascending=False)
    print(f"\n  Recent tariff-mentioning posts ({len(tariff_posts)} total):")
    for _, p in tariff_posts.head(10).iterrows():
        print(f"    [{p['timestamp'].strftime('%Y-%m-%d %H:%M')}] {p['text'][:120]}...")

    # ---- Merge with BTC daily ----
    print("\n[4] Computing daily IC analysis...")
    merged = pd.merge(daily_signals, btc_daily[['date', 'close', 'realized_vol',
                       'fwd_ret_1d', 'fwd_ret_2d', 'fwd_ret_3d', 'fwd_ret_5d', 'fwd_ret_7d']],
                       left_on='date', right_on='date', how='inner')
    merged = merged.sort_values('date').reset_index(drop=True)
    print(f"  Merged daily observations: {len(merged)}")
    print(f"  Date range: {merged['date'].min()} to {merged['date'].max()}")

    signal_cols = [
        'post_count', 'crypto_intensity', 'market_sentiment',
        'uncertainty_intensity', 'post_count_zscore', 'engagement_zscore',
        'crypto_post_count', 'tariff_post_count', 'fed_post_count',
    ]
    return_cols = ['fwd_ret_1d', 'fwd_ret_2d', 'fwd_ret_3d', 'fwd_ret_5d', 'fwd_ret_7d']

    ic_results = ic_analysis(merged, signal_cols, return_cols, label="Full Sample (Daily)")
    print("\n  IC Results (sorted by |IC|):")
    ic_results['abs_IC'] = ic_results['IC_full'].abs()
    ic_sorted = ic_results.sort_values('abs_IC', ascending=False).head(25)
    for _, row in ic_sorted.iterrows():
        flip_mark = " [SIGN FLIP]" if row['sign_flip'] else ""
        star = " *" if row['pval_full'] < 0.05 else ""
        print(f"    {row['signal']:25s} → {row['return_horizon']:12s}: "
              f"IC={row['IC_full']:+.4f} (t={row['t_stat']:+.2f}, p={row['pval_full']:.3f}) "
              f"IS={row['IC_IS']:+.4f} OOS={row['IC_OOS']:+.4f}{flip_mark}{star}")

    # ---- Truth Social only analysis (2022+) ----
    print("\n[5] Truth Social-only analysis (2022-2026)...")
    ts_posts = posts_scored[posts_scored['source'] == 'truth_social'].copy()
    ts_daily_scored, ts_daily = build_daily_signals(ts_posts)
    ts_merged = pd.merge(ts_daily, btc_daily[['date', 'close', 'realized_vol',
                         'fwd_ret_1d', 'fwd_ret_2d', 'fwd_ret_3d', 'fwd_ret_5d', 'fwd_ret_7d']],
                         left_on='date', right_on='date', how='inner')
    ts_merged = ts_merged.sort_values('date').reset_index(drop=True)
    print(f"  Truth Social merged observations: {len(ts_merged)}")

    ts_ic_results = ic_analysis(ts_merged, signal_cols, return_cols, label="Truth Social Only")
    print("\n  Truth Social IC Results (top 25):")
    ts_ic_results['abs_IC'] = ts_ic_results['IC_full'].abs()
    ts_ic_sorted = ts_ic_results.sort_values('abs_IC', ascending=False).head(25)
    for _, row in ts_ic_sorted.iterrows():
        flip_mark = " [SIGN FLIP]" if row['sign_flip'] else ""
        star = " *" if row['pval_full'] < 0.05 else ""
        print(f"    {row['signal']:25s} → {row['return_horizon']:12s}: "
              f"IC={row['IC_full']:+.4f} (t={row['t_stat']:+.2f}, p={row['pval_full']:.3f}) "
              f"IS={row['IC_IS']:+.4f} OOS={row['IC_OOS']:+.4f}{flip_mark}{star}")

    # ---- Event Studies ----
    print("\n[6] Event Studies...")

    # 6a. Crypto mentions
    crypto_event = event_study(
        posts_scored, btc_hourly,
        lambda df: df['crypto_mention'] == True,
        "Crypto Mentions"
    )

    # 6b. Tariff mentions
    tariff_event = event_study(
        posts_scored, btc_hourly,
        lambda df: df['tariff_mention'] == True,
        "Tariff Mentions"
    )

    # 6c. Fed mentions
    fed_event = event_study(
        posts_scored, btc_hourly,
        lambda df: df['fed_mention'] == True,
        "Fed Mentions"
    )

    # 6d. High activity days (>2 std above mean)
    post_count_mean = posts_scored.groupby(posts_scored['timestamp'].dt.date).size().mean()
    post_count_std = posts_scored.groupby(posts_scored['timestamp'].dt.date).size().std()
    threshold = post_count_mean + 2 * post_count_std

    daily_counts = posts_scored.groupby(posts_scored['timestamp'].dt.date).size()
    high_activity_dates = set(daily_counts[daily_counts > threshold].index)

    high_activity_event = event_study(
        posts_scored, btc_hourly,
        lambda df: df['timestamp'].dt.date.isin(high_activity_dates),
        f"High Activity Days (>{threshold:.0f} posts)"
    )

    # 6e. Negative sentiment posts (market_score < -2)
    neg_event = event_study(
        posts_scored, btc_hourly,
        lambda df: df['market_score'] < -2,
        "Strongly Negative Sentiment Posts"
    )

    for evt in [crypto_event, tariff_event, fed_event, high_activity_event, neg_event]:
        if evt is None:
            continue
        print(f"\n  --- {evt['label']} ---")
        print(f"  Events: {evt['n_events']}, with return data: {evt['n_with_returns']}")
        for wname, s in evt.get('summary', {}).items():
            sig_mark = " ***" if s['pval'] < 0.01 else (" **" if s['pval'] < 0.05 else (" *" if s['pval'] < 0.10 else ""))
            print(f"    {wname:12s}: mean={s['mean_ret']*100:+.3f}% median={s['median_ret']*100:+.3f}% "
                  f"excess={s['excess_ret']*100:+.3f}% t={s['t_stat']:+.2f} p={s['pval']:.3f} "
                  f"(n={s['n_events']}, %pos={s['pct_positive']:.1%}){sig_mark}")

    # ---- Hourly IC Analysis (Truth Social period only) ----
    print("\n[7] Hourly IC Analysis (Truth Social period)...")
    ts_hourly_signals = build_hourly_signals(
        posts_scored[posts_scored['source'] == 'truth_social'],
        btc_hourly[btc_hourly.index >= '2022-02-01']
    )

    btc_ts_period = btc_hourly[btc_hourly.index >= '2022-02-01'].copy()
    hourly_merged = btc_ts_period.join(ts_hourly_signals, how='inner')

    hourly_signal_cols = ['post_count', 'post_count_6h', 'post_count_24h',
                          'crypto_score', 'market_score', 'uncertainty_score',
                          'market_score_24h', 'uncertainty_24h']
    hourly_return_cols = ['fwd_ret_1h', 'fwd_ret_4h', 'fwd_ret_24h', 'fwd_ret_72h', 'fwd_ret_168h']

    available_hourly_signals = [c for c in hourly_signal_cols if c in hourly_merged.columns]
    available_hourly_returns = [c for c in hourly_return_cols if c in hourly_merged.columns]

    if available_hourly_signals and available_hourly_returns:
        hourly_ic_results = ic_analysis(hourly_merged, available_hourly_signals, available_hourly_returns, "Hourly")
        hourly_ic_results['abs_IC'] = hourly_ic_results['IC_full'].abs()
        hourly_ic_sorted = hourly_ic_results.sort_values('abs_IC', ascending=False).head(25)
        print("\n  Hourly IC Results (top 25):")
        for _, row in hourly_ic_sorted.iterrows():
            flip_mark = " [SIGN FLIP]" if row['sign_flip'] else ""
            star = " *" if row['pval_full'] < 0.05 else ""
            print(f"    {row['signal']:25s} → {row['return_horizon']:12s}: "
                  f"IC={row['IC_full']:+.4f} (t={row['t_stat']:+.2f}, p={row['pval_full']:.3f}) "
                  f"IS={row['IC_IS']:+.4f} OOS={row['IC_OOS']:+.4f}{flip_mark}{star}")
    else:
        hourly_ic_results = pd.DataFrame()
        print("  Insufficient hourly data for IC analysis")

    # ---- Volatility impact ----
    print("\n[8] Volatility Impact Analysis...")
    # Does Trump posting activity predict next-day volatility?
    vol_signal_cols = ['post_count', 'uncertainty_intensity', 'tariff_post_count', 'fed_post_count']
    vol_results = []
    for sig in vol_signal_cols:
        if sig not in merged.columns:
            continue
        # Next-day vol
        ic_vol, pval_vol, n_vol = compute_ic(merged[sig], merged['realized_vol'].shift(-1))
        print(f"  {sig:25s} → next-day vol: IC={ic_vol:+.4f} (p={pval_vol:.3f}, n={n_vol})")
        vol_results.append({'signal': sig, 'IC': ic_vol, 'pval': pval_vol, 'n': n_vol})

    # ---- Summary and Verdict ----
    print("\n" + "=" * 70)
    print("SUMMARY AND VERDICT")
    print("=" * 70)

    # Check kill criteria for all IC results
    all_ic = pd.concat([ic_results, ts_ic_results], ignore_index=True)
    if len(hourly_ic_results) > 0:
        all_ic = pd.concat([all_ic, hourly_ic_results], ignore_index=True)

    # Any signals passing thresholds?
    passing = all_ic[(all_ic['IC_full'].abs() >= 0.02) &
                     (all_ic['t_stat'].abs() >= 2.0) &
                     (all_ic['sign_flip'] == False)]

    if len(passing) > 0:
        print(f"\nPASSING SIGNALS ({len(passing)}):")
        for _, row in passing.iterrows():
            print(f"  {row['signal']:25s} → {row['return_horizon']:12s}: "
                  f"IC={row['IC_full']:+.4f} t={row['t_stat']:+.2f} "
                  f"IS={row['IC_IS']:+.4f} OOS={row['IC_OOS']:+.4f} [{row['sample']}]")
    else:
        print("\nNo signals pass all three criteria (|IC|>=0.02, |t|>=2.0, no sign flip).")

    # Check for marginally interesting signals
    marginal = all_ic[(all_ic['IC_full'].abs() >= 0.015) & (all_ic['pval_full'] < 0.10)]
    if len(marginal) > 0:
        print(f"\nMARGINAL SIGNALS (|IC|>=0.015, p<0.10): {len(marginal)}")
        for _, row in marginal.head(10).iterrows():
            flip = " [FLIP]" if row['sign_flip'] else ""
            print(f"  {row['signal']:25s} → {row['return_horizon']:12s}: "
                  f"IC={row['IC_full']:+.4f} t={row['t_stat']:+.2f}{flip} [{row['sample']}]")

    # Check event studies
    significant_events = []
    for evt in [crypto_event, tariff_event, fed_event, high_activity_event, neg_event]:
        if evt is None:
            continue
        for wname, s in evt.get('summary', {}).items():
            if s['pval'] < 0.05 and abs(s['excess_ret']) > 0.001:
                significant_events.append((evt['label'], wname, s))

    if significant_events:
        print(f"\nSIGNIFICANT EVENT STUDY RESULTS:")
        for label, window, s in significant_events:
            print(f"  {label} — {window}: excess_ret={s['excess_ret']*100:+.3f}% "
                  f"(t={s['t_stat']:+.2f}, p={s['pval']:.3f}, n={s['n_events']})")

    # Final verdict
    print("\n" + "-" * 70)
    if len(passing) >= 3:
        verdict = "PASS"
        reason = f"{len(passing)} signals pass all criteria."
    elif len(passing) >= 1:
        verdict = "WEAK PASS"
        reason = f"Only {len(passing)} signal(s) pass all criteria. Marginal utility."
    elif len(significant_events) >= 2:
        verdict = "NEEDS_DATA (event-driven only)"
        reason = "No continuous signal passes, but event studies show significance. May work as event-driven overlay."
    elif len(marginal) >= 3:
        verdict = "NEEDS_DATA"
        reason = "Marginal signals exist but none robust enough. More data or better NLP may help."
    else:
        verdict = "KILL"
        reason = "No continuous signal or event study shows reliable predictive power."

    print(f"VERDICT: {verdict}")
    print(f"REASON: {reason}")

    # Return all results for report generation
    return {
        'ic_results': ic_results,
        'ts_ic_results': ts_ic_results,
        'hourly_ic_results': hourly_ic_results,
        'events': {
            'crypto': crypto_event,
            'tariff': tariff_event,
            'fed': fed_event,
            'high_activity': high_activity_event,
            'negative': neg_event,
        },
        'vol_results': vol_results,
        'posts_scored': posts_scored,
        'daily_signals': daily_signals,
        'merged': merged,
        'passing': passing,
        'marginal': marginal,
        'significant_events': significant_events,
        'verdict': verdict,
        'reason': reason,
    }


if __name__ == '__main__':
    results = main()

    # Save IC results
    results['ic_results'].to_csv(DATA_DIR / 'ic_results_daily.csv', index=False)
    results['ts_ic_results'].to_csv(DATA_DIR / 'ic_results_truthsocial.csv', index=False)
    if len(results['hourly_ic_results']) > 0:
        results['hourly_ic_results'].to_csv(DATA_DIR / 'ic_results_hourly.csv', index=False)

    # Save scored posts
    results['posts_scored'][['timestamp', 'text', 'source', 'crypto_mention', 'crypto_score',
                              'market_score', 'fed_mention', 'tariff_mention',
                              'uncertainty_score']].to_csv(DATA_DIR / 'posts_scored.csv', index=False)

    print("\nResults saved to", DATA_DIR)
