"""
Liquidity Analysis and Market Impact Modeling for Crypto Tokens.

Computes:
  1. Amihud Illiquidity Ratio
  2. Kyle's Lambda (price impact per unit volume)
  3. Market Impact Model for a $200K portfolio
  4. Composite Liquidity Score (0-100)
  5. Liquidity Tiers with position sizing constraints
  6. Time-varying (30-day rolling) liquidity analysis with regime detection
"""

import sys
import os
import glob
import warnings

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

warnings.filterwarnings('ignore', category=RuntimeWarning)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PORTFOLIO_USD = 200_000
MAX_ADV_FRACTION = 0.02          # never trade more than 2% of average daily volume
IMPACT_THRESHOLD_BPS = 10        # flag tokens where our trade moves market >10bps
ROLLING_WINDOW = 30              # days for rolling liquidity
LIQUIDITY_DROP_THRESHOLD = 0.40  # 40% drop flags a liquidity regime change

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'real_data')
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs_v2')


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_token_data(data_dir: str = DATA_DIR) -> dict:
    """Load all *_daily.csv files into a dict of {token: DataFrame}."""
    token_data = {}
    csv_files = sorted(glob.glob(os.path.join(data_dir, '*_daily.csv')))
    for fpath in csv_files:
        token = os.path.basename(fpath).replace('_daily.csv', '')
        df = pd.read_csv(fpath, parse_dates=['date'])
        df.sort_values('date', inplace=True)
        df.reset_index(drop=True, inplace=True)
        # Compute derived columns used everywhere
        df['dollar_volume'] = df['close'] * df['volume']
        df['return'] = df['close'].pct_change()
        df['abs_return'] = df['return'].abs()
        df['price_change'] = df['close'].diff().abs()
        token_data[token] = df
    return token_data


# ---------------------------------------------------------------------------
# 1. Amihud Illiquidity Ratio
# ---------------------------------------------------------------------------
def amihud_illiquidity(df: pd.DataFrame) -> float:
    """ILLIQ = mean(|return| / dollar_volume).  Higher = less liquid."""
    valid = df.dropna(subset=['abs_return', 'dollar_volume'])
    valid = valid[valid['dollar_volume'] > 0]
    if len(valid) < 2:
        return np.nan
    illiq = (valid['abs_return'] / valid['dollar_volume']).mean()
    return illiq


# ---------------------------------------------------------------------------
# 2. Kyle's Lambda
# ---------------------------------------------------------------------------
def kyles_lambda(df: pd.DataFrame) -> float:
    """Regress |price_change| on sqrt(volume).  Lambda = slope."""
    valid = df.dropna(subset=['price_change', 'volume'])
    valid = valid[valid['volume'] > 0]
    if len(valid) < 10:
        return np.nan
    y = valid['price_change'].values
    x = np.sqrt(valid['volume'].values)
    # OLS via normal equations: slope = cov(x,y) / var(x)
    x_mean = x.mean()
    y_mean = y.mean()
    cov_xy = ((x - x_mean) * (y - y_mean)).mean()
    var_x = ((x - x_mean) ** 2).mean()
    if var_x == 0:
        return np.nan
    lam = cov_xy / var_x
    return max(lam, 0.0)  # lambda should be non-negative


# ---------------------------------------------------------------------------
# 3. Market Impact Model
# ---------------------------------------------------------------------------
def market_impact_analysis(df: pd.DataFrame, token: str, portfolio_usd: float = PORTFOLIO_USD) -> dict:
    """
    Estimate price impact and max position for a given token.

    Returns dict with:
      avg_daily_volume_usd, max_trade_usd (2% ADV), estimated_impact_bps,
      max_position_usd, exceeds_adv_limit (bool), market_mover (bool)
    """
    valid = df[df['dollar_volume'] > 0]
    if len(valid) < 5:
        return {
            'token': token,
            'avg_daily_volume_usd': 0,
            'max_trade_usd': 0,
            'estimated_impact_bps': np.nan,
            'max_position_usd': 0,
            'exceeds_adv_limit': True,
            'market_mover': True,
        }

    adv = valid['dollar_volume'].mean()
    max_trade = adv * MAX_ADV_FRACTION  # 2% of ADV

    # Simple square-root market impact model:
    #   impact_bps = kyle_lambda * sqrt(trade_size / ADV) * 10000
    lam = kyles_lambda(df)
    avg_price = valid['close'].mean()

    if lam > 0 and avg_price > 0 and adv > 0:
        # Normalised impact: fraction of price moved per unit participation
        participation = min(max_trade, portfolio_usd) / adv
        impact_frac = lam * np.sqrt(max(participation, 0)) / avg_price
        impact_bps = impact_frac * 10_000
    else:
        impact_bps = np.nan

    max_position = min(max_trade, portfolio_usd)
    exceeds = (portfolio_usd > max_trade)

    market_mover = False
    if not np.isnan(impact_bps) and impact_bps > IMPACT_THRESHOLD_BPS:
        market_mover = True

    return {
        'token': token,
        'avg_daily_volume_usd': adv,
        'max_trade_usd': max_trade,
        'estimated_impact_bps': impact_bps,
        'max_position_usd': max_position,
        'exceeds_adv_limit': exceeds,
        'market_mover': market_mover,
    }


# ---------------------------------------------------------------------------
# 4. Liquidity Score (0-100)
# ---------------------------------------------------------------------------
def corwin_schultz_spread(df: pd.DataFrame) -> float:
    """
    Bid-ask spread proxy from OHLC data.
    spread ~ 2 * sqrt(max(0, -cov(high - close, close - low)))
    """
    valid = df.dropna(subset=['high', 'close', 'low'])
    if len(valid) < 10:
        return np.nan
    hc = valid['high'] - valid['close']
    cl = valid['close'] - valid['low']
    cov_val = np.cov(hc, cl)[0, 1]
    spread = 2.0 * np.sqrt(max(0.0, -cov_val))
    return spread


def compute_liquidity_score(df: pd.DataFrame, all_advs: np.ndarray, all_amihuds: np.ndarray) -> float:
    """
    Composite liquidity score 0-100.
      - Avg daily dollar volume   (40%)
      - Amihud ratio inverse      (30%)
      - Corwin-Schultz spread inv (20%)
      - Volume stability           (10%)
    Each component is ranked as a percentile across all tokens.
    """
    # --- ADV component (40%) ---
    adv = df[df['dollar_volume'] > 0]['dollar_volume'].mean()
    if np.isnan(adv) or adv <= 0:
        adv_pct = 0.0
    else:
        adv_pct = np.searchsorted(np.sort(all_advs[~np.isnan(all_advs)]), adv) / max(len(all_advs[~np.isnan(all_advs)]), 1) * 100

    # --- Amihud inverse component (30%) ---
    amihud = amihud_illiquidity(df)
    if np.isnan(amihud) or amihud <= 0:
        amihud_pct = 0.0
    else:
        # Lower amihud = more liquid = higher score
        valid_amihuds = all_amihuds[~np.isnan(all_amihuds)]
        valid_amihuds = valid_amihuds[valid_amihuds > 0]
        rank = np.searchsorted(np.sort(valid_amihuds), amihud) / max(len(valid_amihuds), 1) * 100
        amihud_pct = 100.0 - rank  # invert: low amihud -> high score

    # --- Corwin-Schultz spread inverse (20%) ---
    spread = corwin_schultz_spread(df)
    if np.isnan(spread) or spread <= 0:
        spread_pct = 50.0  # neutral if can't compute
    else:
        # Lower spread = more liquid = higher score.  Cap at a reasonable range.
        # Use exponential decay: score = 100 * exp(-spread / median_price * scale)
        avg_price = df['close'].mean()
        if avg_price > 0:
            rel_spread = spread / avg_price
            spread_pct = 100.0 * np.exp(-rel_spread * 50)  # scaled
        else:
            spread_pct = 0.0
    spread_pct = np.clip(spread_pct, 0, 100)

    # --- Volume stability (10%) ---
    vol_series = df[df['dollar_volume'] > 0]['dollar_volume']
    if len(vol_series) > 5 and vol_series.mean() > 0:
        vol_cv = vol_series.std() / vol_series.mean()
        stability = max(0.0, 1.0 - vol_cv) * 100
    else:
        stability = 0.0
    stability = np.clip(stability, 0, 100)

    score = 0.40 * adv_pct + 0.30 * amihud_pct + 0.20 * spread_pct + 0.10 * stability
    return np.clip(score, 0, 100)


# ---------------------------------------------------------------------------
# 5. Liquidity Tiers
# ---------------------------------------------------------------------------
def assign_tier(score: float) -> int:
    if score >= 80:
        return 1
    elif score >= 50:
        return 2
    elif score >= 20:
        return 3
    else:
        return 4


def tier_label(tier: int) -> str:
    labels = {
        1: 'Tier 1 (Full position)',
        2: 'Tier 2 (Max 50%)',
        3: 'Tier 3 (Max 25%)',
        4: 'Tier 4 (DO NOT TRADE)',
    }
    return labels.get(tier, 'Unknown')


def position_multiplier(tier: int) -> float:
    return {1: 1.0, 2: 0.5, 3: 0.25, 4: 0.0}.get(tier, 0.0)


# ---------------------------------------------------------------------------
# 6. Time-varying liquidity analysis
# ---------------------------------------------------------------------------
def rolling_liquidity_score(df: pd.DataFrame, window: int = ROLLING_WINDOW) -> pd.Series:
    """30-day rolling simplified liquidity score based on ADV and volume stability."""
    if len(df) < window:
        return pd.Series(np.nan, index=df.index)

    rolling_adv = df['dollar_volume'].rolling(window).mean()
    rolling_std = df['dollar_volume'].rolling(window).std()

    # Normalise ADV to 0-100 using log scale relative to the token's own history
    log_adv = np.log1p(rolling_adv)
    adv_min = log_adv.min()
    adv_range = log_adv.max() - adv_min
    if adv_range > 0:
        adv_component = ((log_adv - adv_min) / adv_range) * 100
    else:
        adv_component = pd.Series(50.0, index=df.index)

    # Volume stability component
    with np.errstate(divide='ignore', invalid='ignore'):
        cv = rolling_std / rolling_adv
    stability = (1 - cv.clip(0, 2) / 2) * 100  # map cv 0->100, cv>=2->0

    rolling_score = 0.7 * adv_component + 0.3 * stability
    return rolling_score.clip(0, 100)


def detect_liquidity_regimes(df: pd.DataFrame, window: int = ROLLING_WINDOW,
                             drop_threshold: float = LIQUIDITY_DROP_THRESHOLD) -> list:
    """
    Detect periods where the rolling liquidity score dropped by more than
    `drop_threshold` fraction from its trailing peak.

    Returns list of dicts: {date, score, peak_score, drop_pct}
    """
    scores = rolling_liquidity_score(df, window)
    events = []
    peak = -np.inf
    for i in range(len(scores)):
        s = scores.iloc[i]
        if np.isnan(s):
            continue
        if s > peak:
            peak = s
        if peak > 0:
            drop = (peak - s) / peak
            if drop >= drop_threshold:
                events.append({
                    'date': df['date'].iloc[i],
                    'score': round(s, 2),
                    'peak_score': round(peak, 2),
                    'drop_pct': round(drop * 100, 1),
                })
    return events


def check_post_etf_liquidity(df: pd.DataFrame, etf_date: str = '2024-01-11') -> dict:
    """
    Compare average liquidity score in 90 days before vs 90 days after the
    Bitcoin spot ETF approval date.  Returns None if insufficient data.
    """
    etf_dt = pd.Timestamp(etf_date)
    pre = df[(df['date'] >= etf_dt - pd.Timedelta(days=90)) & (df['date'] < etf_dt)]
    post = df[(df['date'] >= etf_dt) & (df['date'] < etf_dt + pd.Timedelta(days=90))]
    if len(pre) < 20 or len(post) < 20:
        return None

    pre_adv = pre['dollar_volume'].mean()
    post_adv = post['dollar_volume'].mean()
    if pre_adv == 0:
        return None
    change_pct = (post_adv - pre_adv) / pre_adv * 100
    return {
        'pre_etf_adv': pre_adv,
        'post_etf_adv': post_adv,
        'change_pct': round(change_pct, 1),
        'dropped': change_pct < -20,
    }


# ---------------------------------------------------------------------------
# Main analysis pipeline
# ---------------------------------------------------------------------------
def run_analysis():
    print('=' * 80)
    print('LIQUIDITY ANALYSIS AND MARKET IMPACT MODEL')
    print('=' * 80)
    print()

    # Load data
    token_data = load_token_data()
    tokens = sorted(token_data.keys())
    print(f'Loaded {len(tokens)} tokens from {DATA_DIR}')
    print()

    # ------------------------------------------------------------------
    # Pre-compute cross-token arrays for percentile ranking
    # ------------------------------------------------------------------
    all_advs = np.array([
        token_data[t][token_data[t]['dollar_volume'] > 0]['dollar_volume'].mean()
        for t in tokens
    ])
    all_amihuds = np.array([amihud_illiquidity(token_data[t]) for t in tokens])

    # ------------------------------------------------------------------
    # Compute per-token metrics
    # ------------------------------------------------------------------
    rows = []
    impact_rows = []
    regime_events = {}
    etf_flags = []

    for token in tokens:
        df = token_data[token]

        amihud = amihud_illiquidity(df)
        lam = kyles_lambda(df)
        impact = market_impact_analysis(df, token)
        score = compute_liquidity_score(df, all_advs, all_amihuds)
        tier = assign_tier(score)

        rows.append({
            'token': token,
            'days': len(df),
            'avg_daily_volume_usd': impact['avg_daily_volume_usd'],
            'amihud_illiq': amihud,
            'kyles_lambda': lam,
            'cs_spread': corwin_schultz_spread(df),
            'liquidity_score': round(score, 2),
            'tier': tier,
            'tier_label': tier_label(tier),
            'position_multiplier': position_multiplier(tier),
            'max_position_usd': impact['max_position_usd'],
            'estimated_impact_bps': impact['estimated_impact_bps'],
            'exceeds_adv_limit': impact['exceeds_adv_limit'],
            'market_mover': impact['market_mover'],
        })

        if impact['market_mover']:
            impact_rows.append(impact)

        # Time-varying analysis
        events = detect_liquidity_regimes(df)
        if events:
            regime_events[token] = events

        etf_info = check_post_etf_liquidity(df)
        if etf_info and etf_info['dropped']:
            etf_flags.append({'token': token, **etf_info})

    results = pd.DataFrame(rows)
    results.sort_values('liquidity_score', ascending=False, inplace=True)
    results.reset_index(drop=True, inplace=True)

    # ------------------------------------------------------------------
    # Print Summary Table
    # ------------------------------------------------------------------
    print('-' * 80)
    print('LIQUIDITY SUMMARY (sorted by score)')
    print('-' * 80)
    display_cols = ['token', 'liquidity_score', 'tier', 'avg_daily_volume_usd',
                    'amihud_illiq', 'kyles_lambda', 'estimated_impact_bps',
                    'max_position_usd']
    pd.set_option('display.float_format', lambda x: f'{x:.6g}')
    pd.set_option('display.max_rows', 200)
    pd.set_option('display.width', 160)
    print(results[display_cols].to_string(index=False))
    print()

    # ------------------------------------------------------------------
    # Tier Distribution
    # ------------------------------------------------------------------
    print('-' * 80)
    print('TIER DISTRIBUTION')
    print('-' * 80)
    for t in [1, 2, 3, 4]:
        count = (results['tier'] == t).sum()
        pct = count / len(results) * 100
        print(f'  {tier_label(t):30s}: {count:3d} tokens ({pct:5.1f}%)')
    print()

    # ------------------------------------------------------------------
    # Untradeable Tokens
    # ------------------------------------------------------------------
    untradeable = results[results['tier'] == 4]
    print('-' * 80)
    print(f'UNTRADEABLE TOKENS FOR ${PORTFOLIO_USD:,.0f} PORTFOLIO ({len(untradeable)} tokens)')
    print('-' * 80)
    if len(untradeable) > 0:
        for _, row in untradeable.iterrows():
            adv_str = f"${row['avg_daily_volume_usd']:,.0f}" if row['avg_daily_volume_usd'] > 0 else 'N/A'
            print(f"  {row['token']:15s}  Score: {row['liquidity_score']:5.1f}  ADV: {adv_str}")
    else:
        print('  None -- all tokens have sufficient liquidity.')
    print()

    # ------------------------------------------------------------------
    # Market Movers (>10bps impact)
    # ------------------------------------------------------------------
    movers = results[results['market_mover'] == True]
    print('-' * 80)
    print(f'TOKENS WHERE OUR TRADES WOULD MOVE MARKET >10bps ({len(movers)} tokens)')
    print('-' * 80)
    if len(movers) > 0:
        for _, row in movers.iterrows():
            bps = row['estimated_impact_bps']
            bps_str = f"{bps:.1f}bps" if not np.isnan(bps) else 'N/A'
            print(f"  {row['token']:15s}  Impact: {bps_str}  "
                  f"ADV: ${row['avg_daily_volume_usd']:,.0f}  "
                  f"Tier: {row['tier']}")
    else:
        print('  None -- portfolio is small enough for all tokens.')
    print()

    # ------------------------------------------------------------------
    # Tokens exceeding 2% ADV constraint
    # ------------------------------------------------------------------
    adv_exceed = results[results['exceeds_adv_limit'] == True]
    print('-' * 80)
    print(f'TOKENS WHERE ${PORTFOLIO_USD:,.0f} EXCEEDS 2% ADV ({len(adv_exceed)} tokens)')
    print('-' * 80)
    if len(adv_exceed) > 0:
        for _, row in adv_exceed.iterrows():
            max_t = row['max_position_usd']
            print(f"  {row['token']:15s}  Max trade: ${max_t:,.0f}  "
                  f"ADV: ${row['avg_daily_volume_usd']:,.0f}")
    else:
        print('  None.')
    print()

    # ------------------------------------------------------------------
    # Liquidity Regime Changes
    # ------------------------------------------------------------------
    print('-' * 80)
    print(f'LIQUIDITY REGIME CHANGES (>{LIQUIDITY_DROP_THRESHOLD*100:.0f}% drop from peak)')
    print('-' * 80)
    tokens_with_crises = sorted(regime_events.keys())
    if tokens_with_crises:
        for token in tokens_with_crises[:20]:  # cap output
            events = regime_events[token]
            first = events[0]
            last = events[-1]
            print(f"  {token:15s}  {len(events):3d} crisis days  "
                  f"First: {str(first['date'].date()):10s} ({first['drop_pct']:.0f}% drop)  "
                  f"Latest: {str(last['date'].date()):10s} ({last['drop_pct']:.0f}% drop)")
        if len(tokens_with_crises) > 20:
            print(f'  ... and {len(tokens_with_crises) - 20} more tokens')
    else:
        print('  No significant liquidity regime changes detected.')
    print()

    # ------------------------------------------------------------------
    # Post-ETF Liquidity Drops
    # ------------------------------------------------------------------
    print('-' * 80)
    print('POST-ETF LIQUIDITY DROPS (>20% ADV decline after 2024-01-11)')
    print('-' * 80)
    if etf_flags:
        for info in sorted(etf_flags, key=lambda x: x['change_pct']):
            print(f"  {info['token']:15s}  Change: {info['change_pct']:+.1f}%  "
                  f"Pre-ETF ADV: ${info['pre_etf_adv']:,.0f}  "
                  f"Post-ETF ADV: ${info['post_etf_adv']:,.0f}")
    else:
        print('  No tokens showed significant post-ETF liquidity drops.')
    print()

    # ------------------------------------------------------------------
    # Save results
    # ------------------------------------------------------------------
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, 'liquidity_scores.csv')
    results.to_csv(out_path, index=False)
    print(f'Saved liquidity scores to {out_path}')
    print()

    return results


if __name__ == '__main__':
    run_analysis()
