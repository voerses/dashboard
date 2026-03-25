"""
Ideal Trade Analysis — Reverse-Engineering Optimal Entries and Exits

"String Theory Causation" approach:
1. Find the TOP 50 best possible long entries (buy low, sell high within 1-30 day windows)
2. For each ideal entry, compute what indicators/signals were present
3. Find COMMON PATTERNS across the top entries
4. Same analysis for exits
5. Compare with WORST entries to find discriminating features
6. Cross-asset validation (BTC, ETH, SOL, BNB)

Output: Ranked signals at ideal entries/exits, gaps in current strategies, actionable recommendations.
"""

import sys
import warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1: Data Loading
# ══════════════════════════════════════════════════════════════════════════════

def load_spot_data(token: str) -> pd.DataFrame:
    """Load spot 1H data for a token."""
    path = PROJECT_ROOT / "data" / "spot" / "1h_cache" / f"{token}_1h.parquet"
    if not path.exists():
        raise FileNotFoundError(f"No spot data for {token}")
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    return df

def load_perp_data(token: str) -> pd.DataFrame:
    """Load perp 1H data (includes funding rate)."""
    path = PROJECT_ROOT / "data" / "perp" / "1h_cache" / f"{token}_1h.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    return df

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2: Indicator Computation (Vectorized with pandas for speed)
# ══════════════════════════════════════════════════════════════════════════════

def compute_indicators(df: pd.DataFrame, df_perp: pd.DataFrame = None) -> pd.DataFrame:
    """Compute a comprehensive set of indicators for analysis."""
    ind = pd.DataFrame(index=df.index)
    c = df['close']
    h = df['high']
    l = df['low']
    o = df['open']
    v = df['volume']

    # --- Trend: EMA positions ---
    for p in [10, 20, 50, 100, 200]:
        ema = c.ewm(span=p, adjust=False).mean()
        ind[f'ema_{p}'] = ema
        ind[f'price_vs_ema_{p}'] = (c - ema) / ema  # Distance from EMA as %

    # EMA alignment flags
    ind['ema_bull_align'] = ((ind['ema_10'] > ind['ema_20']) &
                              (ind['ema_20'] > ind['ema_50'])).astype(float)
    ind['ema_bear_align'] = ((ind['ema_10'] < ind['ema_20']) &
                              (ind['ema_20'] < ind['ema_50'])).astype(float)
    ind['ema_stack_bull'] = ((ind['ema_10'] > ind['ema_20']) &
                              (ind['ema_20'] > ind['ema_50']) &
                              (ind['ema_50'] > ind['ema_100']) &
                              (ind['ema_100'] > ind['ema_200'])).astype(float)

    # --- Momentum: RSI ---
    for p in [7, 14, 21]:
        delta = c.diff()
        gain = delta.where(delta > 0, 0.0).ewm(span=p, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0.0)).ewm(span=p, adjust=False).mean()
        rs = gain / (loss + 1e-10)
        ind[f'rsi_{p}'] = 100 - 100 / (1 + rs)

    # --- MACD ---
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    ind['macd'] = macd
    ind['macd_signal'] = signal
    ind['macd_hist'] = macd - signal
    ind['macd_positive'] = (macd > 0).astype(float)
    ind['macd_hist_rising'] = (ind['macd_hist'] > ind['macd_hist'].shift(1)).astype(float)

    # MACD zero-line crossover
    ind['macd_cross_bull'] = ((macd > 0) & (macd.shift(1) <= 0)).astype(float)
    ind['macd_cross_bear'] = ((macd < 0) & (macd.shift(1) >= 0)).astype(float)

    # --- ADX and DI ---
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    atr_14 = tr.ewm(span=14, adjust=False).mean()
    ind['atr_14'] = atr_14
    ind['atr_pct'] = atr_14 / c  # ATR as % of price

    up_move = h - h.shift(1)
    down_move = l.shift(1) - l
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(span=14, adjust=False).mean() / (atr_14 + 1e-10)
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(span=14, adjust=False).mean() / (atr_14 + 1e-10)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
    adx = dx.ewm(span=14, adjust=False).mean()
    ind['adx'] = adx
    ind['plus_di'] = plus_di
    ind['minus_di'] = minus_di
    ind['di_diff'] = plus_di - minus_di

    # --- Bollinger Bands ---
    sma20 = c.rolling(20).mean()
    std20 = c.rolling(20).std()
    bb_upper = sma20 + 2 * std20
    bb_lower = sma20 - 2 * std20
    ind['bb_pct'] = (c - bb_lower) / (bb_upper - bb_lower + 1e-10)
    ind['bb_width'] = (bb_upper - bb_lower) / sma20
    bb_width_avg = ind['bb_width'].rolling(240).mean()
    ind['bb_squeeze'] = (ind['bb_width'] < bb_width_avg * 0.8).astype(float)

    # --- Volume ---
    vol_sma20 = v.rolling(20).mean()
    vol_sma50 = v.rolling(50).mean()
    ind['vol_ratio_20'] = v / (vol_sma20 + 1)
    ind['vol_ratio_50'] = v / (vol_sma50 + 1)
    # Volume trend
    ind['vol_increasing'] = (vol_sma20 > vol_sma50).astype(float)

    # OBV
    obv = (np.sign(c.diff()) * v).fillna(0).cumsum()
    ind['obv'] = obv
    obv_ema20 = obv.ewm(span=20, adjust=False).mean()
    ind['obv_above_ema'] = (obv > obv_ema20).astype(float)

    # --- Volatility ---
    log_ret = np.log(c / c.shift(1))
    for p in [20, 50]:
        ind[f'realized_vol_{p}'] = log_ret.rolling(p).std() * np.sqrt(8760)

    # Garman-Klass vol
    log_hl = np.log(h / l) ** 2
    log_co = np.log(c / o) ** 2
    gk = 0.5 * log_hl - (2 * np.log(2) - 1) * log_co
    ind['gk_vol_20'] = gk.rolling(20).mean().apply(lambda x: np.sqrt(abs(x) * 365) if not np.isnan(x) else np.nan)

    # --- Drawdown from local high ---
    for lookback in [24, 72, 168, 336, 720]:
        rolling_high = h.rolling(lookback).max()
        ind[f'drawdown_{lookback}h'] = (c - rolling_high) / rolling_high

    # --- Returns at multiple horizons ---
    for p in [1, 4, 12, 24, 48, 72, 168]:
        ind[f'ret_{p}h'] = c.pct_change(p)

    # --- Support/Resistance proximity ---
    # Use recent swing highs/lows as proxy
    for lookback in [48, 168]:
        rolling_high = h.rolling(lookback).max()
        rolling_low = l.rolling(lookback).min()
        range_width = rolling_high - rolling_low + 1e-10
        ind[f'sr_position_{lookback}h'] = (c - rolling_low) / range_width  # 0 = at support, 1 = at resistance

    # --- Statistical features ---
    for p in [20, 50]:
        ind[f'skewness_{p}'] = log_ret.rolling(p).skew()
        ind[f'kurtosis_{p}'] = log_ret.rolling(p).kurt()

    # Z-score of price
    for p in [20, 50]:
        ind[f'zscore_{p}'] = (c - c.rolling(p).mean()) / (c.rolling(p).std() + 1e-10)

    # --- Stochastic ---
    k_period = 14
    hh = h.rolling(k_period).max()
    ll = l.rolling(k_period).min()
    ind['stoch_k'] = 100 * (c - ll) / (hh - ll + 1e-10)
    ind['stoch_d'] = ind['stoch_k'].rolling(3).mean()

    # --- MFI ---
    tp = (h + l + c) / 3
    mf = tp * v
    tp_diff = tp.diff()
    pos_mf = mf.where(tp_diff > 0, 0).rolling(14).sum()
    neg_mf = mf.where(tp_diff < 0, 0).rolling(14).sum()
    ind['mfi_14'] = 100 - 100 / (1 + pos_mf / (neg_mf + 1e-10))

    # --- Candle patterns ---
    body = (c - o).abs()
    full_range = h - l + 1e-10
    ind['body_ratio'] = body / full_range
    ind['upper_shadow_ratio'] = (h - pd.concat([c, o], axis=1).max(axis=1)) / full_range
    ind['lower_shadow_ratio'] = (pd.concat([c, o], axis=1).min(axis=1) - l) / full_range
    ind['is_bullish_candle'] = (c > o).astype(float)

    # Consecutive down/up bars
    up = (c > c.shift(1)).astype(int)
    down = (c < c.shift(1)).astype(int)
    # Count consecutive downs before current bar
    consec_down = down.copy()
    for i in range(1, 10):
        consec_down = consec_down + (down.rolling(i+1).sum() == i+1).astype(int)
    ind['consec_down_bars'] = consec_down.clip(0, 10)

    consec_up = up.copy()
    for i in range(1, 10):
        consec_up = consec_up + (up.rolling(i+1).sum() == i+1).astype(int)
    ind['consec_up_bars'] = consec_up.clip(0, 10)

    # --- Funding rate (if perp data available) ---
    if df_perp is not None and 'funding_1h' in df_perp.columns:
        # Align to spot index
        funding = df_perp['funding_1h'].reindex(df.index, method='ffill').fillna(0)
        ind['funding_1h'] = funding
        ind['funding_8h'] = funding.rolling(8).sum()
        ind['funding_24h'] = funding.rolling(24).sum()
        ind['funding_zscore'] = (funding - funding.rolling(168).mean()) / (funding.rolling(168).std() + 1e-10)
    else:
        ind['funding_1h'] = 0
        ind['funding_8h'] = 0
        ind['funding_24h'] = 0
        ind['funding_zscore'] = 0

    # --- Regime (simple: EMA 50 slope + ADX) ---
    ema50_slope = ind['ema_50'].pct_change(24)
    ind['regime_uptrend'] = ((ema50_slope > 0.005) & (adx > 20)).astype(float)
    ind['regime_downtrend'] = ((ema50_slope < -0.005) & (adx > 20)).astype(float)
    ind['regime_range'] = ((adx <= 20) | ((ema50_slope.abs() < 0.005) & (adx > 15))).astype(float)

    # --- Hurst exponent (simplified rolling R/S) ---
    # Simplified: just use autocorrelation of returns as proxy
    ind['ret_autocorr_20'] = log_ret.rolling(20).apply(
        lambda x: pd.Series(x).autocorr(lag=1) if len(x) > 2 else 0, raw=False
    )

    # --- Dollar volume ---
    dollar_vol = c * v
    ind['dollar_vol_24h'] = dollar_vol.rolling(24).sum()

    return ind


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3: Find Ideal Trades (Max Forward Return)
# ══════════════════════════════════════════════════════════════════════════════

def find_ideal_entries(df: pd.DataFrame, n_top: int = 50, max_window_days: int = 30) -> pd.DataFrame:
    """
    For each bar, compute the maximum forward return over 1-30 day windows.
    Return the top N best possible long entries.

    For each entry, also record the optimal exit bar (where max return was achieved).
    """
    c = df['close'].values
    n = len(c)
    max_window_bars = max_window_days * 24  # 1H bars

    # Compute max forward return and optimal exit bar for each entry
    max_fwd_return = np.full(n, np.nan)
    optimal_exit_bar = np.full(n, -1, dtype=int)
    min_fwd_return = np.full(n, np.nan)
    worst_exit_bar = np.full(n, -1, dtype=int)

    # Vectorized approach: for each lookforward window, compute the return
    for i in range(n - 24):  # Need at least 24h forward
        end = min(i + max_window_bars, n)
        forward_prices = c[i+1:end]
        if len(forward_prices) == 0:
            continue
        returns = (forward_prices - c[i]) / c[i]
        best_idx = np.argmax(returns)
        worst_idx = np.argmin(returns)
        max_fwd_return[i] = returns[best_idx]
        optimal_exit_bar[i] = i + 1 + best_idx
        min_fwd_return[i] = returns[worst_idx]
        worst_exit_bar[i] = i + 1 + worst_idx

    results = pd.DataFrame({
        'max_fwd_return': max_fwd_return,
        'optimal_exit_bar': optimal_exit_bar,
        'min_fwd_return': min_fwd_return,
        'worst_exit_bar': worst_exit_bar,
    }, index=df.index)

    # Optimal holding period
    results['optimal_hold_hours'] = results['optimal_exit_bar'] - np.arange(n)

    return results


def find_ideal_short_entries(df: pd.DataFrame, n_top: int = 50, max_window_days: int = 30) -> pd.DataFrame:
    """Find the best short entries (max decline within forward window)."""
    c = df['close'].values
    n = len(c)
    max_window_bars = max_window_days * 24

    max_short_return = np.full(n, np.nan)
    optimal_short_exit = np.full(n, -1, dtype=int)

    for i in range(n - 24):
        end = min(i + max_window_bars, n)
        forward_prices = c[i+1:end]
        if len(forward_prices) == 0:
            continue
        # Short return = (entry - exit) / entry = 1 - exit/entry
        returns = (c[i] - forward_prices) / c[i]
        best_idx = np.argmax(returns)
        max_short_return[i] = returns[best_idx]
        optimal_short_exit[i] = i + 1 + best_idx

    results = pd.DataFrame({
        'max_short_return': max_short_return,
        'optimal_short_exit': optimal_short_exit,
    }, index=df.index)

    return results


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4: Analyze Signal Profiles at Ideal Entry/Exit Points
# ══════════════════════════════════════════════════════════════════════════════

def profile_entries(ind: pd.DataFrame, entry_indices: pd.DatetimeIndex,
                    label: str = "ideal") -> dict:
    """Compute summary statistics of all indicators at entry points."""
    entry_data = ind.loc[entry_indices].copy()

    profile = {}
    for col in ind.columns:
        if col.startswith('ema_') and not col.startswith('ema_bull') and not col.startswith('ema_bear') and not col.startswith('ema_stack'):
            continue  # Skip raw EMA values, keep derived features
        vals = entry_data[col].dropna()
        if len(vals) < 5:
            continue
        profile[col] = {
            'mean': vals.mean(),
            'median': vals.median(),
            'std': vals.std(),
            'q25': vals.quantile(0.25),
            'q75': vals.quantile(0.75),
            'pct_above_0': (vals > 0).mean() if vals.min() < 0 or vals.max() > 1 else None,
            'pct_above_50': (vals > 50).mean() if col.startswith('rsi') or col.startswith('mfi') or col.startswith('stoch') else None,
        }
    return profile


def compare_profiles(best_profile: dict, worst_profile: dict, all_profile: dict) -> pd.DataFrame:
    """Compare indicator profiles between best, worst, and all bars.
    Return discriminating power for each feature."""
    rows = []
    for col in best_profile:
        if col not in worst_profile or col not in all_profile:
            continue
        best_mean = best_profile[col]['mean']
        worst_mean = worst_profile[col]['mean']
        all_mean = all_profile[col]['mean']
        all_std = all_profile[col].get('std', 1e-10)
        if all_std < 1e-10:
            all_std = 1e-10

        # Discrimination score: how different are best vs worst (in units of overall std)
        disc_score = abs(best_mean - worst_mean) / all_std
        direction = "HIGHER at best" if best_mean > worst_mean else "LOWER at best"

        rows.append({
            'indicator': col,
            'best_mean': best_mean,
            'worst_mean': worst_mean,
            'all_mean': all_mean,
            'discrimination_score': disc_score,
            'direction': direction,
            'best_median': best_profile[col]['median'],
            'worst_median': worst_profile[col]['median'],
        })

    result = pd.DataFrame(rows)
    result = result.sort_values('discrimination_score', ascending=False)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5: Cross-Asset Analysis
# ══════════════════════════════════════════════════════════════════════════════

def analyze_token(token: str, lookback_months: int = 12, n_top: int = 50) -> dict:
    """Full analysis pipeline for one token."""
    print(f"\n{'='*70}")
    print(f"  Analyzing {token}")
    print(f"{'='*70}")

    # Load data
    df_spot = load_spot_data(token)
    df_perp = load_perp_data(token)

    # Last 12 months
    end_date = df_spot.index[-1]
    start_date = end_date - pd.DateOffset(months=lookback_months)
    df = df_spot[df_spot.index >= start_date].copy()

    if df_perp is not None:
        df_perp = df_perp[df_perp.index >= start_date].copy()

    print(f"  Date range: {df.index[0]} to {df.index[-1]} ({len(df)} bars)")

    # Compute indicators
    ind = compute_indicators(df, df_perp)

    # Find ideal LONG entries
    fwd = find_ideal_entries(df, n_top=n_top)
    short_fwd = find_ideal_short_entries(df, n_top=n_top)

    # Filter to valid bars (enough forward data + indicators warmed up)
    valid = fwd['max_fwd_return'].notna()
    valid_idx = fwd.index[valid]

    # Top N best long entries (non-overlapping: at least 48h apart)
    sorted_entries = fwd.loc[valid_idx].sort_values('max_fwd_return', ascending=False)
    best_entries = []
    used_bars = set()
    for idx, row in sorted_entries.iterrows():
        bar_num = df.index.get_loc(idx)
        # Check no overlap with existing selections (48h buffer)
        if any(abs(bar_num - b) < 48 for b in used_bars):
            continue
        best_entries.append(idx)
        used_bars.add(bar_num)
        if len(best_entries) >= n_top:
            break

    # Worst N long entries (smallest max forward return = worst possible timing)
    sorted_worst = fwd.loc[valid_idx].sort_values('max_fwd_return', ascending=True)
    worst_entries = []
    used_bars_w = set()
    for idx, row in sorted_worst.iterrows():
        bar_num = df.index.get_loc(idx)
        if any(abs(bar_num - b) < 48 for b in used_bars_w):
            continue
        worst_entries.append(idx)
        used_bars_w.add(bar_num)
        if len(worst_entries) >= n_top:
            break

    best_idx = pd.DatetimeIndex(best_entries)
    worst_idx = pd.DatetimeIndex(worst_entries)

    # Get optimal exit points for best entries
    best_exit_bars = fwd.loc[best_idx, 'optimal_exit_bar'].values.astype(int)
    best_exit_times = [df.index[min(b, len(df)-1)] for b in best_exit_bars if b >= 0 and b < len(df)]
    exit_idx = pd.DatetimeIndex(best_exit_times)

    print(f"  Top {len(best_idx)} long entries | avg max return: {fwd.loc[best_idx, 'max_fwd_return'].mean():.1%}")
    print(f"  Worst {len(worst_idx)} long entries | avg max return: {fwd.loc[worst_idx, 'max_fwd_return'].mean():.1%}")
    print(f"  Avg optimal hold: {fwd.loc[best_idx, 'optimal_hold_hours'].mean():.0f}h ({fwd.loc[best_idx, 'optimal_hold_hours'].mean()/24:.1f}d)")

    # Profile indicators
    best_profile = profile_entries(ind, best_idx, "best")
    worst_profile = profile_entries(ind, worst_idx, "worst")
    all_profile = profile_entries(ind, ind.index[200:], "all")  # skip warmup
    exit_profile = profile_entries(ind, exit_idx, "exit") if len(exit_idx) > 5 else {}

    # Compare
    comparison = compare_profiles(best_profile, worst_profile, all_profile)

    # Entry stats for best
    entry_stats = {}
    for idx in best_idx:
        if idx in ind.index:
            row = ind.loc[idx]
            entry_stats[idx] = {
                'max_return': fwd.loc[idx, 'max_fwd_return'],
                'hold_hours': fwd.loc[idx, 'optimal_hold_hours'],
                'rsi_14': row.get('rsi_14', np.nan),
                'adx': row.get('adx', np.nan),
                'bb_pct': row.get('bb_pct', np.nan),
                'bb_squeeze': row.get('bb_squeeze', np.nan),
                'vol_ratio_20': row.get('vol_ratio_20', np.nan),
                'drawdown_168h': row.get('drawdown_168h', np.nan),
                'sr_position_168h': row.get('sr_position_168h', np.nan),
                'macd_positive': row.get('macd_positive', np.nan),
                'ema_bull_align': row.get('ema_bull_align', np.nan),
                'funding_zscore': row.get('funding_zscore', np.nan),
                'ret_24h': row.get('ret_24h', np.nan),
                'stoch_k': row.get('stoch_k', np.nan),
                'mfi_14': row.get('mfi_14', np.nan),
                'regime_uptrend': row.get('regime_uptrend', np.nan),
                'regime_downtrend': row.get('regime_downtrend', np.nan),
            }

    return {
        'token': token,
        'n_bars': len(df),
        'best_entries': best_idx,
        'worst_entries': worst_idx,
        'exit_times': exit_idx,
        'comparison': comparison,
        'best_profile': best_profile,
        'worst_profile': worst_profile,
        'exit_profile': exit_profile,
        'all_profile': all_profile,
        'entry_stats': entry_stats,
        'fwd_returns': fwd,
        'indicators': ind,
    }


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6: Print Report
# ══════════════════════════════════════════════════════════════════════════════

def print_section(title: str, char: str = '='):
    width = 80
    print(f"\n{char * width}")
    print(f"  {title}")
    print(f"{char * width}")


def print_report(results: list[dict]):
    """Print comprehensive analysis report."""

    print_section("IDEAL TRADE ANALYSIS REPORT", '=')
    print("Methodology: 'String theory causation' — find best trades first,")
    print("then reverse-engineer what signals they share.")
    print(f"Tokens analyzed: {', '.join(r['token'] for r in results)}")

    # ── 1. Cross-asset discrimination summary ──
    print_section("1. TOP DISCRIMINATING SIGNALS (Best vs Worst Entries)")
    print("   Ranked by how differently a signal behaves at ideal vs terrible entry points.\n")

    # Aggregate comparisons across all tokens
    all_comparisons = []
    for r in results:
        comp = r['comparison'].copy()
        comp['token'] = r['token']
        all_comparisons.append(comp)

    agg = pd.concat(all_comparisons)

    # Average discrimination score across tokens
    avg_disc = agg.groupby('indicator').agg({
        'discrimination_score': 'mean',
        'best_mean': 'mean',
        'worst_mean': 'mean',
        'all_mean': 'mean',
        'direction': 'first',
    }).sort_values('discrimination_score', ascending=False)

    # Count how many tokens agree on direction
    direction_agreement = agg.groupby('indicator')['direction'].apply(
        lambda x: x.value_counts().iloc[0] / len(x) if len(x) > 0 else 0
    )
    avg_disc['agreement'] = direction_agreement

    print(f"{'Rank':<5} {'Indicator':<28} {'Disc.Score':>10} {'Best Mean':>10} {'Worst Mean':>10} {'Direction':<25} {'Agree%':>7}")
    print("-" * 100)
    for i, (idx, row) in enumerate(avg_disc.head(30).iterrows()):
        print(f"{i+1:<5} {idx:<28} {row['discrimination_score']:>10.3f} {row['best_mean']:>10.4f} {row['worst_mean']:>10.4f} {row['direction']:<25} {row['agreement']:>6.0%}")

    # ── 2. Ideal entry signal profile ──
    print_section("2. SIGNAL PROFILE AT IDEAL LONG ENTRIES")
    print("   What do the best buy points look like across all tokens?\n")

    # Key indicators to highlight
    key_indicators = [
        'rsi_14', 'rsi_7', 'adx', 'bb_pct', 'bb_squeeze', 'vol_ratio_20',
        'drawdown_168h', 'drawdown_72h', 'drawdown_24h',
        'sr_position_168h', 'sr_position_48h',
        'macd_positive', 'macd_hist_rising', 'macd_cross_bull',
        'ema_bull_align', 'ema_bear_align', 'ema_stack_bull',
        'price_vs_ema_20', 'price_vs_ema_50', 'price_vs_ema_200',
        'funding_zscore', 'funding_24h',
        'stoch_k', 'mfi_14',
        'regime_uptrend', 'regime_downtrend',
        'ret_24h', 'ret_72h', 'ret_168h',
        'realized_vol_20', 'atr_pct',
        'zscore_20', 'zscore_50',
        'skewness_20', 'kurtosis_20',
        'vol_increasing', 'obv_above_ema',
        'body_ratio', 'lower_shadow_ratio',
        'consec_down_bars',
        'ret_autocorr_20',
    ]

    for indicator in key_indicators:
        values = []
        for r in results:
            if indicator in r['best_profile']:
                bp = r['best_profile'][indicator]
                values.append({
                    'token': r['token'],
                    'mean': bp['mean'],
                    'median': bp['median'],
                    'q25': bp['q25'],
                    'q75': bp['q75'],
                })
        if not values:
            continue

        avg_mean = np.mean([v['mean'] for v in values])
        avg_median = np.mean([v['median'] for v in values])
        print(f"  {indicator:<28}  mean={avg_mean:>8.4f}  median={avg_median:>8.4f}  "
              f"[{np.mean([v['q25'] for v in values]):>8.4f} - {np.mean([v['q75'] for v in values]):>8.4f}]")

    # ── 3. Ideal EXIT signal profile ──
    print_section("3. SIGNAL PROFILE AT IDEAL EXITS (Where to Take Profit)")

    for indicator in key_indicators:
        values = []
        for r in results:
            if indicator in r.get('exit_profile', {}):
                bp = r['exit_profile'][indicator]
                values.append({
                    'token': r['token'],
                    'mean': bp['mean'],
                    'median': bp['median'],
                })
        if not values:
            continue
        avg_mean = np.mean([v['mean'] for v in values])
        avg_median = np.mean([v['median'] for v in values])
        print(f"  {indicator:<28}  mean={avg_mean:>8.4f}  median={avg_median:>8.4f}")

    # ── 4. Entry vs Exit comparison ──
    print_section("4. ENTRY vs EXIT SIGNAL COMPARISON")
    print("   Positive delta = signal INCREASES from entry to exit\n")

    for indicator in key_indicators:
        entry_vals = []
        exit_vals = []
        for r in results:
            if indicator in r.get('best_profile', {}) and indicator in r.get('exit_profile', {}):
                entry_vals.append(r['best_profile'][indicator]['mean'])
                exit_vals.append(r['exit_profile'][indicator]['mean'])
        if not entry_vals:
            continue
        entry_avg = np.mean(entry_vals)
        exit_avg = np.mean(exit_vals)
        delta = exit_avg - entry_avg
        print(f"  {indicator:<28}  entry={entry_avg:>8.4f}  exit={exit_avg:>8.4f}  delta={delta:>+8.4f}")

    # ── 5. Frequency analysis for categorical signals ──
    print_section("5. CATEGORICAL SIGNAL FREQUENCY AT IDEAL ENTRIES")
    print("   How often is each condition TRUE at ideal entry points?\n")

    categorical_signals = [
        'bb_squeeze', 'macd_positive', 'macd_hist_rising', 'macd_cross_bull',
        'ema_bull_align', 'ema_bear_align', 'ema_stack_bull',
        'vol_increasing', 'obv_above_ema',
        'regime_uptrend', 'regime_downtrend',
    ]

    print(f"  {'Signal':<28} {'Best Entries':>14} {'Worst Entries':>14} {'All Bars':>14} {'Disc.':>8}")
    print("  " + "-" * 82)

    for sig in categorical_signals:
        best_freqs = []
        worst_freqs = []
        all_freqs = []
        for r in results:
            if sig in r['best_profile'] and sig in r['worst_profile'] and sig in r['all_profile']:
                best_freqs.append(r['best_profile'][sig]['mean'])
                worst_freqs.append(r['worst_profile'][sig]['mean'])
                all_freqs.append(r['all_profile'][sig]['mean'])
        if not best_freqs:
            continue
        b = np.mean(best_freqs)
        w = np.mean(worst_freqs)
        a = np.mean(all_freqs)
        disc = abs(b - w)
        print(f"  {sig:<28} {b:>13.1%} {w:>13.1%} {a:>13.1%} {disc:>8.3f}")

    # ── 6. Optimal holding period analysis ──
    print_section("6. OPTIMAL HOLDING PERIOD DISTRIBUTION")

    for r in results:
        fwd = r['fwd_returns']
        best = fwd.loc[r['best_entries']]
        hold_hours = best['optimal_hold_hours']
        print(f"\n  {r['token']}:")
        print(f"    Mean hold: {hold_hours.mean():.0f}h ({hold_hours.mean()/24:.1f}d)")
        print(f"    Median hold: {hold_hours.median():.0f}h ({hold_hours.median()/24:.1f}d)")
        print(f"    Range: {hold_hours.min():.0f}h - {hold_hours.max():.0f}h")
        # Distribution buckets
        buckets = [(0, 24, '< 1d'), (24, 72, '1-3d'), (72, 168, '3-7d'),
                    (168, 336, '7-14d'), (336, 720, '14-30d')]
        for lo, hi, label in buckets:
            pct = ((hold_hours >= lo) & (hold_hours < hi)).mean()
            print(f"    {label:>8}: {pct:>6.1%}")

    # ── 7. Top individual trade examples ──
    print_section("7. TOP 10 INDIVIDUAL IDEAL TRADES (per token)")

    for r in results:
        print(f"\n  {r['token']}:")
        stats = r['entry_stats']
        sorted_stats = sorted(stats.items(), key=lambda x: x[1]['max_return'], reverse=True)

        print(f"  {'Date':<22} {'Return':>8} {'Hold':>6} {'RSI14':>7} {'ADX':>7} {'BB%':>7} {'Squeeze':>8} {'DD168h':>8} {'SR168h':>8} {'MACD+':>6} {'EMA↑':>6} {'FundZ':>7}")
        print(f"  {'-'*115}")
        for ts, s in sorted_stats[:10]:
            print(f"  {str(ts)[:19]:<22} {s['max_return']:>7.1%} {s['hold_hours']:>5.0f}h "
                  f"{s['rsi_14']:>7.1f} {s['adx']:>7.1f} {s['bb_pct']:>7.3f} {s['bb_squeeze']:>8.0f} "
                  f"{s['drawdown_168h']:>8.2%} {s['sr_position_168h']:>8.3f} {s['macd_positive']:>6.0f} "
                  f"{s['ema_bull_align']:>6.0f} {s['funding_zscore']:>7.2f}")

    # ── 8. What current strategies are missing ──
    print_section("8. GAPS IN CURRENT STRATEGIES")
    print("""
Current strategies (s98, s101, etc.) primarily use:
  - MACD zero-line crossover
  - BB squeeze (width < 80% of 240h average)
  - ADX > 20 + DI confirmation
  - Regime state (UPTREND/DOWNTREND)
  - EMA alignment (10 > 20 > 50)
  - ADV liquidity filter ($1B+)

Analysis of ideal trades reveals these ADDITIONAL patterns that current strategies MISS:
""")

    # Derive gaps from the discrimination analysis
    # Get top discriminating features not used by current strategies
    current_signals = {'adx', 'bb_squeeze', 'bb_pct', 'macd_positive', 'ema_bull_align',
                       'ema_bear_align', 'di_diff', 'plus_di', 'minus_di', 'vol_ratio_20',
                       'regime_uptrend', 'regime_downtrend', 'macd_cross_bull', 'macd_cross_bear',
                       'bb_width', 'macd_hist', 'macd', 'macd_signal'}

    print("  MISSED DISCRIMINATING SIGNALS (not in current strategies, ranked by discrimination power):\n")
    print(f"  {'Rank':<6} {'Indicator':<28} {'Disc.Score':>10} {'Best Mean':>10} {'Worst Mean':>10} {'Direction':<25}")
    print("  " + "-" * 95)
    rank = 0
    for _, row in avg_disc.iterrows():
        name = row.name
        # Skip if it's already used
        if any(s in name for s in current_signals):
            continue
        rank += 1
        if rank > 20:
            break
        print(f"  {rank:<6} {name:<28} {row['discrimination_score']:>10.3f} {row['best_mean']:>10.4f} {row['worst_mean']:>10.4f} {row['direction']:<25}")

    # ── 9. Actionable Recommendations ──
    print_section("9. ACTIONABLE RECOMMENDATIONS FOR NEW ENTRY/EXIT SIGNALS")

    # Analyze the top discriminating features and create recommendations
    top_disc = avg_disc.head(20)

    recommendations = []

    # Check drawdown features
    for dd_col in ['drawdown_168h', 'drawdown_72h', 'drawdown_24h']:
        if dd_col in avg_disc.index:
            row = avg_disc.loc[dd_col]
            if row['discrimination_score'] > 0.3:
                recommendations.append({
                    'signal': f"Recent drawdown ({dd_col})",
                    'description': f"Ideal entries have avg drawdown of {row['best_mean']:.2%} (vs {row['worst_mean']:.2%} for worst). "
                                   f"Add a drawdown filter: require price to be {abs(row['best_mean']):.1%}-{abs(row['best_mean'])*1.5:.1%} below recent high.",
                    'priority': 'HIGH',
                    'disc_score': row['discrimination_score'],
                })

    # Check returns
    for ret_col in ['ret_24h', 'ret_72h', 'ret_168h']:
        if ret_col in avg_disc.index:
            row = avg_disc.loc[ret_col]
            if row['discrimination_score'] > 0.3:
                recommendations.append({
                    'signal': f"Recent return ({ret_col})",
                    'description': f"Best entries have avg recent return of {row['best_mean']:.2%} (vs {row['worst_mean']:.2%} for worst). "
                                   f"{'Buy after dips' if row['best_mean'] < row['worst_mean'] else 'Buy momentum'} is the correct approach.",
                    'priority': 'HIGH',
                    'disc_score': row['discrimination_score'],
                })

    # Check support/resistance
    for sr_col in ['sr_position_168h', 'sr_position_48h']:
        if sr_col in avg_disc.index:
            row = avg_disc.loc[sr_col]
            if row['discrimination_score'] > 0.2:
                recommendations.append({
                    'signal': f"S/R position ({sr_col})",
                    'description': f"Best entries avg position: {row['best_mean']:.3f} (0=support, 1=resistance). "
                                   f"{'Near support' if row['best_mean'] < 0.4 else 'Mid-range' if row['best_mean'] < 0.6 else 'Near resistance'}. "
                                   f"Filter entries to this range.",
                    'priority': 'MEDIUM',
                    'disc_score': row['discrimination_score'],
                })

    # Check RSI
    if 'rsi_14' in avg_disc.index:
        row = avg_disc.loc['rsi_14']
        if row['discrimination_score'] > 0.2:
            recommendations.append({
                'signal': "RSI entry zone",
                'description': f"Best entries avg RSI: {row['best_mean']:.1f} (vs {row['worst_mean']:.1f} for worst). "
                               f"Optimal RSI zone for entry: {avg_disc.loc['rsi_14']['best_mean'] - 10:.0f}-{avg_disc.loc['rsi_14']['best_mean'] + 10:.0f}.",
                'priority': 'MEDIUM',
                'disc_score': row['discrimination_score'],
            })

    # Check volume
    if 'vol_ratio_20' in avg_disc.index:
        row = avg_disc.loc['vol_ratio_20']
        if row['discrimination_score'] > 0.2:
            recommendations.append({
                'signal': "Volume ratio filter",
                'description': f"Best entries avg vol ratio: {row['best_mean']:.2f}x (vs {row['worst_mean']:.2f}x). "
                               f"{'High volume' if row['best_mean'] > 1.0 else 'Low volume'} at entry is a strong filter.",
                'priority': 'MEDIUM',
                'disc_score': row['discrimination_score'],
            })

    # Check funding
    if 'funding_zscore' in avg_disc.index:
        row = avg_disc.loc['funding_zscore']
        if row['discrimination_score'] > 0.15:
            recommendations.append({
                'signal': "Funding rate zscore",
                'description': f"Best entries avg funding zscore: {row['best_mean']:.2f} (vs {row['worst_mean']:.2f}). "
                               f"{'Negative funding (shorts paying longs)' if row['best_mean'] < 0 else 'Positive funding'} at entry is favorable.",
                'priority': 'MEDIUM',
                'disc_score': row['discrimination_score'],
            })

    # Check volatility
    if 'realized_vol_20' in avg_disc.index:
        row = avg_disc.loc['realized_vol_20']
        if row['discrimination_score'] > 0.2:
            recommendations.append({
                'signal': "Realized volatility filter",
                'description': f"Best entries avg vol: {row['best_mean']:.2%} (vs {row['worst_mean']:.2%}). "
                               f"{'Higher' if row['best_mean'] > row['worst_mean'] else 'Lower'} vol at entry is better.",
                'priority': 'MEDIUM',
                'disc_score': row['discrimination_score'],
            })

    # Check statistical
    for stat_col in ['skewness_20', 'kurtosis_20', 'zscore_20', 'zscore_50']:
        if stat_col in avg_disc.index:
            row = avg_disc.loc[stat_col]
            if row['discrimination_score'] > 0.3:
                recommendations.append({
                    'signal': f"Statistical filter ({stat_col})",
                    'description': f"Best: {row['best_mean']:.3f}, Worst: {row['worst_mean']:.3f}. "
                                   f"Strong discriminator (score={row['discrimination_score']:.2f}). Add as entry filter.",
                    'priority': 'HIGH',
                    'disc_score': row['discrimination_score'],
                })

    # Check candle patterns
    for candle_col in ['lower_shadow_ratio', 'body_ratio', 'consec_down_bars']:
        if candle_col in avg_disc.index:
            row = avg_disc.loc[candle_col]
            if row['discrimination_score'] > 0.15:
                recommendations.append({
                    'signal': f"Candle pattern ({candle_col})",
                    'description': f"Best: {row['best_mean']:.3f}, Worst: {row['worst_mean']:.3f}. "
                                   f"Candle structure differs at ideal vs bad entries.",
                    'priority': 'LOW',
                    'disc_score': row['discrimination_score'],
                })

    # Check exit-specific signals
    exit_recs = []
    for indicator in key_indicators:
        entry_vals_list = []
        exit_vals_list = []
        for r in results:
            if indicator in r.get('best_profile', {}) and indicator in r.get('exit_profile', {}):
                entry_vals_list.append(r['best_profile'][indicator]['mean'])
                exit_vals_list.append(r['exit_profile'][indicator]['mean'])
        if len(entry_vals_list) >= 2:
            entry_avg = np.mean(entry_vals_list)
            exit_avg = np.mean(exit_vals_list)
            all_std_val = 1.0
            for r in results:
                if indicator in r.get('all_profile', {}):
                    all_std_val = r['all_profile'][indicator].get('std', 1.0)
                    break
            if all_std_val < 1e-10:
                all_std_val = 1e-10
            exit_shift = abs(exit_avg - entry_avg) / all_std_val
            if exit_shift > 0.3:
                exit_recs.append({
                    'signal': indicator,
                    'entry_val': entry_avg,
                    'exit_val': exit_avg,
                    'shift': exit_shift,
                    'direction': 'INCREASES' if exit_avg > entry_avg else 'DECREASES',
                })

    # Sort and print recommendations
    recommendations.sort(key=lambda x: x['disc_score'], reverse=True)

    print("\n  ENTRY SIGNAL RECOMMENDATIONS:\n")
    for i, rec in enumerate(recommendations):
        print(f"  [{rec['priority']}] #{i+1}: {rec['signal']} (disc={rec['disc_score']:.2f})")
        print(f"       {rec['description']}")
        print()

    if exit_recs:
        print("\n  EXIT SIGNAL RECOMMENDATIONS:\n")
        exit_recs.sort(key=lambda x: x['shift'], reverse=True)
        for i, rec in enumerate(exit_recs[:10]):
            print(f"  #{i+1}: {rec['signal']} (shift={rec['shift']:.2f})")
            print(f"       {rec['direction']} from {rec['entry_val']:.4f} at entry to {rec['exit_val']:.4f} at exit")
            print()

    # ── 10. Quantified summary ──
    print_section("10. QUANTIFIED SUMMARY")

    # Print the "ideal entry recipe"
    print("\n  THE IDEAL LONG ENTRY RECIPE (cross-asset consensus):\n")

    recipe_signals = []
    for indicator in key_indicators:
        best_vals = []
        worst_vals = []
        for r in results:
            if indicator in r['best_profile']:
                best_vals.append(r['best_profile'][indicator]['mean'])
            if indicator in r['worst_profile']:
                worst_vals.append(r['worst_profile'][indicator]['mean'])
        if best_vals and worst_vals:
            recipe_signals.append({
                'indicator': indicator,
                'ideal_value': np.mean(best_vals),
                'worst_value': np.mean(worst_vals),
                'diff': abs(np.mean(best_vals) - np.mean(worst_vals)),
            })

    recipe_signals.sort(key=lambda x: x['diff'], reverse=True)

    for i, s in enumerate(recipe_signals[:20]):
        direction = ">" if s['ideal_value'] > s['worst_value'] else "<"
        threshold = (s['ideal_value'] + s['worst_value']) / 2
        print(f"  {i+1:>2}. {s['indicator']:<28} ideal={s['ideal_value']:>8.4f}  worst={s['worst_value']:>8.4f}  "
              f"filter: {direction} {threshold:>8.4f}")

    print("\n  THE IDEAL EXIT RECIPE (cross-asset consensus):\n")

    exit_recipe = []
    for indicator in key_indicators:
        exit_vals = []
        entry_vals = []
        for r in results:
            if indicator in r.get('exit_profile', {}) and indicator in r.get('best_profile', {}):
                exit_vals.append(r['exit_profile'][indicator]['mean'])
                entry_vals.append(r['best_profile'][indicator]['mean'])
        if exit_vals:
            exit_recipe.append({
                'indicator': indicator,
                'entry_value': np.mean(entry_vals),
                'exit_value': np.mean(exit_vals),
                'shift': abs(np.mean(exit_vals) - np.mean(entry_vals)),
            })

    exit_recipe.sort(key=lambda x: x['shift'], reverse=True)

    for i, s in enumerate(exit_recipe[:15]):
        direction = "rose to" if s['exit_value'] > s['entry_value'] else "fell to"
        print(f"  {i+1:>2}. {s['indicator']:<28} entry={s['entry_value']:>8.4f}  {direction} exit={s['exit_value']:>8.4f}  "
              f"(shift={s['shift']:>6.4f})")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    TOKENS = ['BTC', 'ETH', 'SOL', 'BNB']
    N_TOP = 50

    print("=" * 80)
    print("  IDEAL TRADE ANALYSIS")
    print("  Reverse-engineering optimal entry/exit signals from perfect hindsight")
    print("=" * 80)

    results = []
    for token in TOKENS:
        try:
            r = analyze_token(token, lookback_months=12, n_top=N_TOP)
            results.append(r)
        except Exception as e:
            print(f"  ERROR: {token} - {e}")
            import traceback
            traceback.print_exc()
            continue

    if results:
        print_report(results)
    else:
        print("No results to report!")
