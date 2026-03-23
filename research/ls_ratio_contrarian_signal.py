"""
Long/Short Account Ratio — Contrarian Signal Analysis (Bybit Data)
====================================================================
Tests whether the Bybit Long/Short account ratio has predictive power
for crypto forward returns when used as a CONTRARIAN signal.

Hypothesis: When retail accounts are overwhelmingly long (high L/S ratio),
go SHORT. When overwhelmingly short (low L/S ratio), go LONG.
Retail is consistently wrong at extremes.

Data: Bybit daily L/S ratio for 10 tokens, ~2020-2026 (5+ years)
Prior: IS Sharpe 2.85 on 28-day Binance data — BLOCKED due to short history.
This test uses 5+ years of Bybit data for proper validation.

Methodology:
- Strict temporal split: IS (before 2025-07-01) / OOS (after 2025-07-01)
- Parameters calibrated on IS only, evaluated on OOS
- Threshold: |IC| > 0.05 and |t| > 2.0 with matching IS/OOS sign to PASS

Signal variants:
1. Raw L/S ratio (contrarian: negative IC expected)
2. L/S z-score (20d, 30d, 60d rolling) — extreme readings as entry signals
3. L/S rate of change — rapid shifts in positioning
4. L/S cross-token divergence — BTC L/S diverges from altcoin L/S
5. L/S regime — binary: top quartile = bearish, bottom quartile = bullish
6. L/S + funding combo — L/S extreme + funding extreme = strongest contrarian
"""

import pandas as pd
import numpy as np
from scipy import stats
import warnings
import json
from datetime import datetime

warnings.filterwarnings('ignore')

# ==============================================================================
# Configuration
# ==============================================================================
SPLIT_DATE = pd.Timestamp('2025-07-01')
LS_DIR = '/workspace/crypto_backtest/data/alternative/ls_ratio_extended'
PRICE_DIR = '/workspace/crypto_backtest/data/perp/1h_cache'
TOKENS = ['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE', 'ADA', 'AVAX', 'LINK', 'SUI']
SYMBOLS = {t: f'{t}USDT' for t in TOKENS}
FWD_HORIZONS = {'1d': 1, '3d': 3, '7d': 7, '14d': 14}  # in days
IC_THRESHOLD = 0.05
T_THRESHOLD = 2.0
COST_BPS = 5  # one-way transaction cost in bps

# ==============================================================================
# Data Loading
# ==============================================================================

def load_ls_ratio(token):
    """Load Bybit L/S ratio data for a token."""
    symbol = SYMBOLS[token].lower()
    path = f'{LS_DIR}/bybit_{symbol}_ls_ratio.parquet'
    df = pd.read_parquet(path)
    df['date'] = pd.to_datetime(df['timestamp']).dt.normalize()
    df = df.set_index('date').sort_index()
    df = df[~df.index.duplicated(keep='first')]
    return df[['buy_ratio', 'sell_ratio', 'ls_ratio']]


def load_daily_close(token):
    """Load hourly perp data and resample to daily close at 00:00 UTC."""
    path = f'{PRICE_DIR}/{token}_1h.parquet'
    df = pd.read_parquet(path)
    daily = df[['close']].resample('1D').last().dropna()
    daily.index = daily.index.normalize()
    # Also get daily funding rate (sum of hourly)
    if 'funding_1h' in df.columns:
        daily['funding_daily'] = df['funding_1h'].resample('1D').sum()
    return daily


def compute_forward_returns(prices, horizons_days):
    """Compute forward returns at multiple horizons."""
    fwd = pd.DataFrame(index=prices.index)
    for label, days in horizons_days.items():
        fwd[f'fwd_{label}'] = prices.pct_change(days).shift(-days)
    return fwd


# ==============================================================================
# IC Computation
# ==============================================================================

def compute_ic_and_tstat(signal, returns, min_obs=30):
    """Compute Spearman rank IC and t-statistic."""
    aligned = pd.concat([signal.rename('sig'), returns.rename('ret')], axis=1).dropna()
    if len(aligned) < min_obs:
        return np.nan, np.nan, 0, np.nan
    ic, pval = stats.spearmanr(aligned['sig'], aligned['ret'])
    n = len(aligned)
    if abs(ic) >= 1.0:
        t_stat = np.inf * np.sign(ic)
    else:
        t_stat = ic * np.sqrt((n - 2) / (1 - ic**2))
    hit_rate = (np.sign(aligned['sig']) == np.sign(aligned['ret'])).mean()
    return ic, t_stat, n, hit_rate


def evaluate_signal(signal_series, fwd_returns, split_date):
    """Evaluate a signal across horizons for IS and OOS periods."""
    results = {}
    for hz in FWD_HORIZONS:
        ret_col = f'fwd_{hz}'
        if ret_col not in fwd_returns.columns:
            continue

        # IS period
        mask_is = signal_series.index < split_date
        is_sig = signal_series[mask_is]
        is_ret = fwd_returns.loc[fwd_returns.index < split_date, ret_col]
        ic_is, t_is, n_is, hr_is = compute_ic_and_tstat(is_sig, is_ret)

        # OOS period
        mask_oos = signal_series.index >= split_date
        oos_sig = signal_series[mask_oos]
        oos_ret = fwd_returns.loc[fwd_returns.index >= split_date, ret_col]
        ic_oos, t_oos, n_oos, hr_oos = compute_ic_and_tstat(oos_sig, oos_ret)

        # PASS criteria: |IC| > threshold, |t| > threshold, same sign IS/OOS
        sign_match = (not np.isnan(ic_is) and not np.isnan(ic_oos) and
                      np.sign(ic_is) == np.sign(ic_oos) and np.sign(ic_is) != 0)
        passed = (abs(ic_is) > IC_THRESHOLD and abs(t_is) > T_THRESHOLD and
                  abs(ic_oos) > IC_THRESHOLD and abs(t_oos) > T_THRESHOLD and
                  sign_match)

        results[hz] = {
            'ic_is': ic_is, 't_is': t_is, 'n_is': n_is, 'hr_is': hr_is,
            'ic_oos': ic_oos, 't_oos': t_oos, 'n_oos': n_oos, 'hr_oos': hr_oos,
            'sign_match': sign_match, 'passed': passed,
        }
    return results


# ==============================================================================
# Signal Constructions
# ==============================================================================

def signal_raw_ls(ls_data):
    """Signal 1: Raw L/S ratio (contrarian: negate so high L/S -> negative signal)."""
    return -ls_data['ls_ratio']


def signal_zscore(ls_data, window):
    """Signal 2: Z-score of L/S ratio (negated for contrarian)."""
    ls = ls_data['ls_ratio']
    rolling_mean = ls.rolling(window, min_periods=max(window // 2, 10)).mean()
    rolling_std = ls.rolling(window, min_periods=max(window // 2, 10)).std()
    z = (ls - rolling_mean) / rolling_std.replace(0, np.nan)
    return -z  # contrarian: high z-score (extremely long) -> go short


def signal_roc(ls_data, window):
    """Signal 3: Rate of change of L/S ratio (negated for contrarian)."""
    ls = ls_data['ls_ratio']
    roc = ls.pct_change(window)
    return -roc  # contrarian: rapid increase in longs -> go short


def signal_regime(ls_data, lookback=60):
    """Signal 5: Quartile-based regime signal.
    Top quartile L/S = bearish (-1), bottom quartile = bullish (+1), middle = 0."""
    ls = ls_data['ls_ratio']
    q25 = ls.rolling(lookback, min_periods=lookback // 2).quantile(0.25)
    q75 = ls.rolling(lookback, min_periods=lookback // 2).quantile(0.75)
    regime = pd.Series(0.0, index=ls.index)
    regime[ls >= q75] = -1.0  # extremely long retail -> bearish
    regime[ls <= q25] = 1.0   # extremely short retail -> bullish
    return regime


def signal_cross_token_divergence(all_ls_data, token):
    """Signal 4: Divergence of a token's L/S from BTC L/S.
    If altcoin L/S much higher than BTC L/S -> altcoin retail more bullish -> bearish signal."""
    if token == 'BTC' or 'BTC' not in all_ls_data:
        return None
    btc_ls = all_ls_data['BTC']['ls_ratio']
    token_ls = all_ls_data[token]['ls_ratio']
    # Normalize both to z-scores first
    btc_z = (btc_ls - btc_ls.rolling(30, min_periods=15).mean()) / btc_ls.rolling(30, min_periods=15).std()
    tok_z = (token_ls - token_ls.rolling(30, min_periods=15).mean()) / token_ls.rolling(30, min_periods=15).std()
    # Align dates
    aligned = pd.concat([tok_z.rename('tok'), btc_z.rename('btc')], axis=1).dropna()
    divergence = aligned['tok'] - aligned['btc']
    return -divergence  # contrarian: altcoin more long than BTC -> bearish for altcoin


def signal_ls_funding_combo(ls_data, funding_daily):
    """Signal 6: Combined L/S extreme + funding rate extreme.
    Both signals agree -> stronger contrarian signal."""
    ls = ls_data['ls_ratio']
    # Z-score both
    ls_z = (ls - ls.rolling(30, min_periods=15).mean()) / ls.rolling(30, min_periods=15).std()
    fund_z = (funding_daily - funding_daily.rolling(30, min_periods=15).mean()) / funding_daily.rolling(30, min_periods=15).std()
    # Align
    aligned = pd.concat([(-ls_z).rename('ls_sig'), (-fund_z).rename('fund_sig')], axis=1).dropna()
    # Average of the two contrarian signals
    combo = (aligned['ls_sig'] + aligned['fund_sig']) / 2
    return combo


# ==============================================================================
# Backtest (for PASS signals)
# ==============================================================================

def backtest_quartile_ls(all_ls_data, all_prices, split_date, cost_bps=5):
    """Simple quartile long/short backtest across tokens.
    Long bottom quartile L/S tokens, short top quartile L/S tokens daily."""
    # Build panel of L/S ratios and returns
    ls_panel = pd.DataFrame()
    ret_panel = pd.DataFrame()

    for token in TOKENS:
        if token in all_ls_data and token in all_prices:
            ls_panel[token] = all_ls_data[token]['ls_ratio']
            ret_panel[token] = all_prices[token]['close'].pct_change(1)

    # Align dates
    common_idx = ls_panel.dropna(how='all').index.intersection(ret_panel.dropna(how='all').index)
    ls_panel = ls_panel.loc[common_idx]
    ret_panel = ret_panel.loc[common_idx]

    # Cross-sectional rank each day (rank the L/S across tokens)
    # Low rank = low L/S = bullish (go long), High rank = high L/S = bearish (go short)
    n_tokens = ls_panel.count(axis=1)
    ranks = ls_panel.rank(axis=1, pct=True)

    # Position: -1 for top quartile (>0.75), +1 for bottom quartile (<0.25), 0 otherwise
    positions = pd.DataFrame(0.0, index=ranks.index, columns=ranks.columns)
    positions[ranks <= 0.25] = 1.0
    positions[ranks >= 0.75] = -1.0

    # Shift positions by 1 day (signal at close t, trade at close t+1)
    positions = positions.shift(1)

    # Daily P&L
    daily_pnl = (positions * ret_panel).mean(axis=1)  # equal weight across active positions

    # Transaction costs: proportional to turnover
    turnover = positions.diff().abs().sum(axis=1)
    n_active = (positions != 0).sum(axis=1).replace(0, 1)
    cost = (turnover / n_active) * (cost_bps / 10000)
    daily_pnl_net = daily_pnl - cost

    # Split IS/OOS
    is_pnl = daily_pnl_net[daily_pnl_net.index < split_date].dropna()
    oos_pnl = daily_pnl_net[daily_pnl_net.index >= split_date].dropna()

    results = {}
    for label, pnl in [('IS', is_pnl), ('OOS', oos_pnl)]:
        if len(pnl) < 30:
            results[label] = {'sharpe': np.nan, 'total_ret': np.nan, 'win_rate': np.nan, 'max_dd': np.nan, 'n_days': len(pnl)}
            continue
        ann_sharpe = pnl.mean() / pnl.std() * np.sqrt(365) if pnl.std() > 0 else 0
        cum_ret = (1 + pnl).cumprod()
        total_ret = cum_ret.iloc[-1] - 1
        max_dd = (cum_ret / cum_ret.cummax() - 1).min()
        win_rate = (pnl > 0).mean()
        results[label] = {
            'sharpe': ann_sharpe,
            'total_ret': total_ret,
            'win_rate': win_rate,
            'max_dd': max_dd,
            'n_days': len(pnl),
        }
    return results, daily_pnl_net, positions


def backtest_single_token_signal(signal_series, prices, split_date, cost_bps=5):
    """Backtest a single-token signal: long when signal > 0, short when < 0."""
    daily_ret = prices['close'].pct_change(1)
    # Signal -> position: sign of signal, shifted by 1
    position = np.sign(signal_series).shift(1)

    # Align
    aligned = pd.concat([position.rename('pos'), daily_ret.rename('ret')], axis=1).dropna()
    pnl = aligned['pos'] * aligned['ret']

    # Costs
    turnover = aligned['pos'].diff().abs()
    cost = turnover * (cost_bps / 10000)
    pnl_net = pnl - cost

    results = {}
    for label, mask in [('IS', aligned.index < split_date), ('OOS', aligned.index >= split_date)]:
        sub = pnl_net[mask].dropna()
        if len(sub) < 30:
            results[label] = {'sharpe': np.nan, 'total_ret': np.nan, 'win_rate': np.nan, 'max_dd': np.nan, 'n_days': len(sub)}
            continue
        ann_sharpe = sub.mean() / sub.std() * np.sqrt(365) if sub.std() > 0 else 0
        cum = (1 + sub).cumprod()
        results[label] = {
            'sharpe': ann_sharpe,
            'total_ret': cum.iloc[-1] - 1,
            'win_rate': (sub > 0).mean(),
            'max_dd': (cum / cum.cummax() - 1).min(),
            'n_days': len(sub),
        }
    return results


# ==============================================================================
# Main Analysis
# ==============================================================================

def main():
    print("=" * 80)
    print("L/S RATIO CONTRARIAN SIGNAL ANALYSIS — BYBIT DATA")
    print("=" * 80)
    print(f"Split date: {SPLIT_DATE.date()}")
    print(f"IC threshold: {IC_THRESHOLD}, t-stat threshold: {T_THRESHOLD}")
    print(f"Tokens: {', '.join(TOKENS)}")
    print()

    # ---- Load all data ----
    print("--- Loading Data ---")
    all_ls = {}
    all_prices = {}
    all_fwd = {}

    for token in TOKENS:
        try:
            ls = load_ls_ratio(token)
            pr = load_daily_close(token)
            fwd = compute_forward_returns(pr['close'], FWD_HORIZONS)
            all_ls[token] = ls
            all_prices[token] = pr
            all_fwd[token] = fwd
            n_is = (ls.index < SPLIT_DATE).sum()
            n_oos = (ls.index >= SPLIT_DATE).sum()
            print(f"  {token}: L/S {ls.index.min().date()} to {ls.index.max().date()}, "
                  f"{len(ls)} days (IS: {n_is}, OOS: {n_oos}), "
                  f"L/S range: {ls['ls_ratio'].min():.2f} - {ls['ls_ratio'].max():.2f}, "
                  f"mean: {ls['ls_ratio'].mean():.2f}")
        except Exception as e:
            print(f"  {token}: FAILED — {e}")

    print()

    # ---- Define all signal variants ----
    signal_defs = {
        'raw_ls': ('Raw L/S Contrarian', lambda ls, pr, tok: signal_raw_ls(ls)),
        'zscore_20d': ('Z-Score 20d', lambda ls, pr, tok: signal_zscore(ls, 20)),
        'zscore_30d': ('Z-Score 30d', lambda ls, pr, tok: signal_zscore(ls, 30)),
        'zscore_60d': ('Z-Score 60d', lambda ls, pr, tok: signal_zscore(ls, 60)),
        'roc_5d': ('Rate of Change 5d', lambda ls, pr, tok: signal_roc(ls, 5)),
        'roc_10d': ('Rate of Change 10d', lambda ls, pr, tok: signal_roc(ls, 10)),
        'roc_20d': ('Rate of Change 20d', lambda ls, pr, tok: signal_roc(ls, 20)),
        'regime_60d': ('Regime Quartile 60d', lambda ls, pr, tok: signal_regime(ls, 60)),
        'regime_90d': ('Regime Quartile 90d', lambda ls, pr, tok: signal_regime(ls, 90)),
        'divergence_30d': ('Cross-Token Divergence 30d',
                           lambda ls, pr, tok: signal_cross_token_divergence(all_ls, tok)),
    }

    # Funding combo — only for tokens with funding data
    signal_defs['ls_funding_combo'] = (
        'L/S + Funding Combo',
        lambda ls, pr, tok: signal_ls_funding_combo(ls, pr['funding_daily'])
        if 'funding_daily' in pr.columns else None
    )

    # ---- Evaluate all signals across all tokens ----
    print("=" * 80)
    print("SIGNAL EVALUATION — INFORMATION COEFFICIENT ANALYSIS")
    print("=" * 80)

    all_results = []  # list of dicts for final table

    for sig_key, (sig_name, sig_func) in signal_defs.items():
        print(f"\n--- {sig_name} ({sig_key}) ---")
        for token in TOKENS:
            if token not in all_ls:
                continue
            try:
                sig = sig_func(all_ls[token], all_prices[token], token)
                if sig is None:
                    continue
                res = evaluate_signal(sig, all_fwd[token], SPLIT_DATE)
                for hz, metrics in res.items():
                    row = {
                        'signal': sig_key,
                        'signal_name': sig_name,
                        'token': token,
                        'horizon': hz,
                        **metrics,
                    }
                    all_results.append(row)
                    flag = "PASS" if metrics['passed'] else "    "
                    print(f"  {token:6s} {hz:3s}: IC_IS={metrics['ic_is']:+.4f} (t={metrics['t_is']:+.2f}, n={metrics['n_is']:4d}) | "
                          f"IC_OOS={metrics['ic_oos']:+.4f} (t={metrics['t_oos']:+.2f}, n={metrics['n_oos']:4d}) | "
                          f"Sign={'+' if metrics['sign_match'] else '-'} | [{flag}]")
            except Exception as e:
                print(f"  {token}: ERROR — {e}")

    results_df = pd.DataFrame(all_results)

    # ---- Summary: Aggregated IC across tokens ----
    print("\n" + "=" * 80)
    print("AGGREGATED IC BY SIGNAL x HORIZON (mean across tokens)")
    print("=" * 80)

    if len(results_df) > 0:
        agg = results_df.groupby(['signal', 'signal_name', 'horizon']).agg(
            ic_is_mean=('ic_is', 'mean'),
            ic_is_std=('ic_is', 'std'),
            t_is_mean=('t_is', 'mean'),
            ic_oos_mean=('ic_oos', 'mean'),
            ic_oos_std=('ic_oos', 'std'),
            t_oos_mean=('t_oos', 'mean'),
            n_tokens=('token', 'count'),
            n_pass=('passed', 'sum'),
            sign_match_pct=('sign_match', 'mean'),
        ).reset_index()

        # Sort for display
        horizon_order = {'1d': 0, '3d': 1, '7d': 2, '14d': 3}
        agg['hz_order'] = agg['horizon'].map(horizon_order)
        agg = agg.sort_values(['signal', 'hz_order'])

        for _, row in agg.iterrows():
            print(f"  {row['signal_name']:30s} | {row['horizon']:3s} | "
                  f"IC_IS={row['ic_is_mean']:+.4f}+/-{row['ic_is_std']:.4f} (t={row['t_is_mean']:+.2f}) | "
                  f"IC_OOS={row['ic_oos_mean']:+.4f}+/-{row['ic_oos_std']:.4f} (t={row['t_oos_mean']:+.2f}) | "
                  f"Sign%={row['sign_match_pct']:.0%} | Pass={int(row['n_pass'])}/{int(row['n_tokens'])}")

    # ---- Per-token analysis: which tokens show strongest contrarian effect? ----
    print("\n" + "=" * 80)
    print("PER-TOKEN CONTRARIAN STRENGTH (best signal across all variants at 7d horizon)")
    print("=" * 80)

    if len(results_df) > 0:
        token_7d = results_df[results_df['horizon'] == '7d'].copy()
        if len(token_7d) > 0:
            best_per_token = token_7d.loc[token_7d.groupby('token')['ic_is'].apply(lambda x: x.abs().idxmax())]
            best_per_token = best_per_token.sort_values('ic_is')
            for _, row in best_per_token.iterrows():
                flag = "PASS" if row['passed'] else "FAIL"
                print(f"  {row['token']:6s}: best={row['signal_name']:30s} | "
                      f"IC_IS={row['ic_is']:+.4f} (t={row['t_is']:+.2f}) | "
                      f"IC_OOS={row['ic_oos']:+.4f} (t={row['t_oos']:+.2f}) | [{flag}]")

    # ---- Identify PASS signals ----
    pass_signals = results_df[results_df['passed']].copy() if len(results_df) > 0 else pd.DataFrame()
    n_pass = len(pass_signals)
    n_total = len(results_df)

    print(f"\n{'=' * 80}")
    print(f"PASS/FAIL SUMMARY: {n_pass}/{n_total} signal-token-horizon combinations PASSED")
    print(f"{'=' * 80}")

    if n_pass > 0:
        print("\nPASSED signal-token-horizon combinations:")
        pass_signals_sorted = pass_signals.sort_values('ic_is', key=abs, ascending=False)
        for _, row in pass_signals_sorted.iterrows():
            print(f"  {row['signal_name']:30s} | {row['token']:6s} | {row['horizon']:3s} | "
                  f"IC_IS={row['ic_is']:+.4f} (t={row['t_is']:+.2f}) | "
                  f"IC_OOS={row['ic_oos']:+.4f} (t={row['t_oos']:+.2f})")

    # ---- Backtest PASS signals ----
    print(f"\n{'=' * 80}")
    print("BACKTEST — Cross-Sectional Quartile L/S Strategy (all tokens)")
    print(f"{'=' * 80}")

    bt_results, bt_pnl, bt_positions = backtest_quartile_ls(all_ls, all_prices, SPLIT_DATE, COST_BPS)
    for period, metrics in bt_results.items():
        print(f"\n  {period}:")
        print(f"    Sharpe Ratio:   {metrics['sharpe']:.3f}")
        print(f"    Total Return:   {metrics['total_ret']:.2%}")
        print(f"    Win Rate:       {metrics['win_rate']:.2%}")
        print(f"    Max Drawdown:   {metrics['max_dd']:.2%}")
        print(f"    N Days:         {metrics['n_days']}")

    # ---- Backtest best single-token signals for tokens that PASSED ----
    if n_pass > 0:
        print(f"\n{'=' * 80}")
        print("BACKTEST — Single-Token Best Signals (tokens with PASS)")
        print(f"{'=' * 80}")

        # For each token that had at least one PASS, backtest its best signal
        pass_tokens = pass_signals['token'].unique()
        for token in pass_tokens:
            token_passes = pass_signals[pass_signals['token'] == token]
            best_row = token_passes.loc[token_passes['ic_is'].abs().idxmax()]
            sig_key = best_row['signal']
            sig_name = best_row['signal_name']
            sig_func = signal_defs[sig_key][1]

            sig = sig_func(all_ls[token], all_prices[token], token)
            if sig is not None:
                bt = backtest_single_token_signal(sig, all_prices[token], SPLIT_DATE, COST_BPS)
                print(f"\n  {token} — {sig_name}:")
                for period, metrics in bt.items():
                    print(f"    {period}: Sharpe={metrics['sharpe']:.3f}, "
                          f"Return={metrics['total_ret']:.2%}, "
                          f"Win={metrics['win_rate']:.2%}, "
                          f"MaxDD={metrics['max_dd']:.2%}, "
                          f"N={metrics['n_days']}")

    # ---- Year-by-year stability analysis ----
    print(f"\n{'=' * 80}")
    print("YEAR-BY-YEAR IC STABILITY — Z-Score 30d Signal (all tokens pooled, 7d horizon)")
    print(f"{'=' * 80}")

    yearly_ics = []
    for token in TOKENS:
        if token not in all_ls:
            continue
        sig = signal_zscore(all_ls[token], 30)
        fwd = all_fwd[token]
        if 'fwd_7d' not in fwd.columns:
            continue
        aligned = pd.concat([sig.rename('sig'), fwd['fwd_7d']], axis=1).dropna()
        aligned['year'] = aligned.index.year
        for year, grp in aligned.groupby('year'):
            if len(grp) >= 30:
                ic, _ = stats.spearmanr(grp['sig'], grp['fwd_7d'])
                yearly_ics.append({'token': token, 'year': year, 'ic': ic, 'n': len(grp)})

    if yearly_ics:
        yearly_df = pd.DataFrame(yearly_ics)
        yearly_agg = yearly_df.groupby('year').agg(
            ic_mean=('ic', 'mean'), ic_std=('ic', 'std'), n_tokens=('token', 'count')
        )
        for year, row in yearly_agg.iterrows():
            print(f"  {year}: IC_mean={row['ic_mean']:+.4f} +/- {row['ic_std']:.4f} (n_tokens={int(row['n_tokens'])})")

    # ---- Data distribution analysis ----
    print(f"\n{'=' * 80}")
    print("DATA DISTRIBUTION — L/S Ratio Statistics by Token")
    print(f"{'=' * 80}")

    for token in TOKENS:
        if token not in all_ls:
            continue
        ls = all_ls[token]['ls_ratio']
        print(f"  {token:6s}: mean={ls.mean():.2f}, median={ls.median():.2f}, "
              f"std={ls.std():.2f}, skew={ls.skew():.2f}, "
              f"min={ls.min():.2f}, max={ls.max():.2f}, "
              f"q25={ls.quantile(0.25):.2f}, q75={ls.quantile(0.75):.2f}")

    # ---- Final verdict ----
    print(f"\n{'=' * 80}")
    print("FINAL VERDICT")
    print(f"{'=' * 80}")

    any_pass = n_pass > 0
    # Check for signals that pass on multiple tokens/horizons (robust)
    if any_pass:
        robust_signals = pass_signals.groupby('signal').agg(
            n_pass=('passed', 'sum'),
            tokens=('token', lambda x: list(x.unique())),
            horizons=('horizon', lambda x: list(x.unique())),
        )
        robust_signals = robust_signals[robust_signals['n_pass'] >= 3]  # at least 3 pass combos
        has_robust = len(robust_signals) > 0
    else:
        has_robust = False

    if has_robust:
        print(f"\n  VERDICT: PASS — {n_pass} signal-token-horizon combos passed thresholds.")
        print(f"  Robust signals (3+ pass combos):")
        for sig, row in robust_signals.iterrows():
            print(f"    {sig}: {row['n_pass']} passes, tokens={row['tokens']}, horizons={row['horizons']}")
        print(f"\n  Cross-sectional backtest IS Sharpe: {bt_results.get('IS', {}).get('sharpe', float('nan')):.3f}")
        print(f"  Cross-sectional backtest OOS Sharpe: {bt_results.get('OOS', {}).get('sharpe', float('nan')):.3f}")
    elif any_pass:
        print(f"\n  VERDICT: WEAK PASS — {n_pass} combos passed but insufficient robustness (< 3 combos).")
        print("  Signal exists but is not robust across tokens/horizons. Proceed with caution.")
    else:
        print(f"\n  VERDICT: FAIL — No signal-token-horizon combination met all thresholds.")
        print("  The L/S ratio contrarian signal does NOT have robust predictive power")
        print("  across 5+ years of Bybit data.")

    # ---- Generate results markdown ----
    generate_results_md(results_df, pass_signals, bt_results, yearly_ics, all_ls, has_robust, any_pass, n_pass, n_total)

    return results_df, pass_signals, bt_results


def generate_results_md(results_df, pass_signals, bt_results, yearly_ics, all_ls, has_robust, any_pass, n_pass, n_total):
    """Generate markdown results file."""
    lines = []
    lines.append("# L/S Ratio Contrarian Signal — Research Results")
    lines.append(f"\nAnalysis date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"Data source: Bybit daily L/S account ratio")
    lines.append(f"Tokens: {', '.join(TOKENS)}")
    lines.append(f"IS/OOS split: 2025-07-01")
    lines.append("")

    # Verdict
    lines.append("## Verdict")
    if has_robust:
        lines.append(f"**PASS** -- {n_pass}/{n_total} signal-token-horizon combos passed (|IC|>{IC_THRESHOLD}, |t|>{T_THRESHOLD}, IS/OOS sign match).")
    elif any_pass:
        lines.append(f"**WEAK PASS** -- {n_pass}/{n_total} combos passed but insufficient robustness.")
    else:
        lines.append(f"**FAIL** -- 0/{n_total} combos passed. No robust contrarian signal found in 5+ years of Bybit L/S data.")
    lines.append("")

    # Signal summary table
    lines.append("## Signal IC Summary (mean across tokens)")
    lines.append("")
    lines.append("| Signal | Horizon | IC IS | t IS | IC OOS | t OOS | Sign% | Pass Rate |")
    lines.append("|--------|---------|-------|------|--------|-------|-------|-----------|")

    if len(results_df) > 0:
        agg = results_df.groupby(['signal_name', 'horizon']).agg(
            ic_is=('ic_is', 'mean'), t_is=('t_is', 'mean'),
            ic_oos=('ic_oos', 'mean'), t_oos=('t_oos', 'mean'),
            sign_pct=('sign_match', 'mean'), n_pass=('passed', 'sum'),
            n_total=('passed', 'count'),
        ).reset_index()
        horizon_order = {'1d': 0, '3d': 1, '7d': 2, '14d': 3}
        agg['hz_order'] = agg['horizon'].map(horizon_order)
        agg = agg.sort_values(['signal_name', 'hz_order'])

        for _, row in agg.iterrows():
            lines.append(f"| {row['signal_name']} | {row['horizon']} | "
                         f"{row['ic_is']:+.4f} | {row['t_is']:+.2f} | "
                         f"{row['ic_oos']:+.4f} | {row['t_oos']:+.2f} | "
                         f"{row['sign_pct']:.0%} | {int(row['n_pass'])}/{int(row['n_total'])} |")

    lines.append("")

    # Backtest results
    lines.append("## Backtest — Cross-Sectional Quartile Strategy")
    lines.append(f"Cost: {COST_BPS}bps one-way")
    lines.append("")
    lines.append("| Period | Sharpe | Total Return | Win Rate | Max DD | N Days |")
    lines.append("|--------|--------|--------------|----------|--------|--------|")
    for period, m in bt_results.items():
        lines.append(f"| {period} | {m['sharpe']:.3f} | {m['total_ret']:.2%} | "
                     f"{m['win_rate']:.2%} | {m['max_dd']:.2%} | {m['n_days']} |")
    lines.append("")

    # Year-by-year
    if yearly_ics:
        lines.append("## Year-by-Year IC Stability (Z-Score 30d, 7d horizon)")
        lines.append("")
        lines.append("| Year | IC Mean | IC Std | N Tokens |")
        lines.append("|------|---------|--------|----------|")
        ydf = pd.DataFrame(yearly_ics)
        yagg = ydf.groupby('year').agg(ic_mean=('ic', 'mean'), ic_std=('ic', 'std'), n=('token', 'count'))
        for year, row in yagg.iterrows():
            lines.append(f"| {year} | {row['ic_mean']:+.4f} | {row['ic_std']:.4f} | {int(row['n'])} |")
        lines.append("")

    # Per-token
    lines.append("## Per-Token L/S Ratio Statistics")
    lines.append("")
    lines.append("| Token | Mean | Median | Std | Min | Max | Start Date | N Days |")
    lines.append("|-------|------|--------|-----|-----|-----|------------|--------|")
    for token in TOKENS:
        if token in all_ls:
            ls = all_ls[token]['ls_ratio']
            lines.append(f"| {token} | {ls.mean():.2f} | {ls.median():.2f} | "
                         f"{ls.std():.2f} | {ls.min():.2f} | {ls.max():.2f} | "
                         f"{all_ls[token].index.min().date()} | {len(ls)} |")
    lines.append("")

    # Passed signals detail
    if len(pass_signals) > 0:
        lines.append("## Passed Signal Details")
        lines.append("")
        lines.append("| Signal | Token | Horizon | IC IS | t IS | IC OOS | t OOS |")
        lines.append("|--------|-------|---------|-------|------|--------|-------|")
        for _, row in pass_signals.sort_values('ic_is', key=abs, ascending=False).iterrows():
            lines.append(f"| {row['signal_name']} | {row['token']} | {row['horizon']} | "
                         f"{row['ic_is']:+.4f} | {row['t_is']:+.2f} | "
                         f"{row['ic_oos']:+.4f} | {row['t_oos']:+.2f} |")
        lines.append("")

    # Methodology note
    lines.append("## Methodology Notes")
    lines.append("- **Signal direction**: All signals are CONTRARIAN — high L/S ratio (retail overly long) maps to negative signal (go short)")
    lines.append("- **IC**: Spearman rank correlation between signal and forward returns")
    lines.append("- **IS period**: All data before 2025-07-01")
    lines.append("- **OOS period**: All data from 2025-07-01 onward (no parameter optimization)")
    lines.append("- **PASS criteria**: |IC| > 0.05, |t-stat| > 2.0, same sign IS vs OOS")
    lines.append("- **Backtest**: Long bottom quartile L/S tokens, short top quartile, daily rebalance, 5bps costs")
    lines.append("- **Prior context**: IS Sharpe 2.85 on 28 days of Binance data was BLOCKED for insufficient history")
    lines.append("")

    md_path = '/workspace/crypto_backtest/research/ls_ratio_contrarian_results.md'
    with open(md_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f"\nResults saved to {md_path}")


if __name__ == '__main__':
    results_df, pass_signals, bt_results = main()
