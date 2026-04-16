"""
Funding Rate Predictive Power Analysis
=======================================
Hypothesis: Extreme funding rates (high positive or high negative) predict
mean-reversion in the underlying price.

- High positive funding => crowd paying to be long => price tends to fall
- High negative funding => crowd paying to be short => price tends to rise

This implies a NEGATIVE information coefficient (IC) between funding rate
and forward returns.

Methodology:
- Use 1h OHLCV + funding data from the 1h_cache
- Top 20 perp tokens by average dollar volume (excluding non-crypto)
- Point-in-time: funding rate at time T, forward return from T+1 onward
- Forward return horizons: 4h, 8h, 12h, 24h, 48h
- Spearman rank IC between funding_rate and forward_return
- Quintile analysis: extreme funding quintiles vs returns
- Cross-sectional IC: each timestamp, rank across tokens
"""

import pandas as pd
import numpy as np
from scipy import stats
import glob
import os
import warnings
warnings.filterwarnings('ignore')

DATA_DIR = '/workspace/crypto_backtest/data/perp/1h_cache'
HORIZONS = [4, 8, 12, 24, 48]  # hours

# Top 20 crypto perp tokens by dollar volume (excluding commodities XAG, XAU)
TOP_TOKENS = [
    'BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'PEPE', 'BNB', 'TRUMP', 'SUI',
    'ADA', 'WIF', 'SHIB', 'HYPE', 'ENA', 'FARTCOIN', 'AVAX', 'LINK',
    'LTC', 'WLD', 'DOT'
]


def load_token(symbol: str) -> pd.DataFrame:
    """Load 1h data for a token, returning OHLCV + funding."""
    path = os.path.join(DATA_DIR, f'{symbol}_1h.parquet')
    df = pd.read_parquet(path)
    df.index.name = 'timestamp'
    return df


def compute_forward_returns(close: pd.Series, horizons: list) -> pd.DataFrame:
    """
    Compute forward returns at various horizons.
    Point-in-time: return from T+1 to T+1+horizon.
    Using log returns for additivity.
    """
    fwd = {}
    for h in horizons:
        # Forward return: log(close[t+h] / close[t])
        # Shift by -h means we look h bars into the future
        fwd[f'fwd_{h}h'] = np.log(close.shift(-h) / close)
    return pd.DataFrame(fwd, index=close.index)


def compute_rolling_funding(funding_rate: pd.Series) -> pd.DataFrame:
    """
    Compute rolling funding metrics.
    funding_rate is the 8h rate, forward-filled into 1h bars.
    - rolling_8h: the raw 8h funding rate (already in data)
    - rolling_24h: sum of last 3 funding settlements (24h cumulative)
    - rolling_3d: sum of last 9 funding settlements (72h cumulative)
    - z_score: z-score of funding rate over trailing 30-day window
    """
    result = pd.DataFrame(index=funding_rate.index)
    result['funding_raw'] = funding_rate

    # 24h cumulative: sum of 3 8h periods = sum over 24 1h bars of (rate/8)
    # Since the rate is stepped every 8h, summing 24 hourly values and dividing
    # gives us the 24h cumulative. But simpler: rolling 24h mean * 3
    # Actually, the funding_rate is the 8h rate. 24h cumulative = sum of
    # the 3 most recent distinct 8h rates.
    # Easier: rolling sum of hourly contribution = funding_rate/8 per hour
    hourly_cost = funding_rate / 8.0
    result['cum_24h'] = hourly_cost.rolling(24, min_periods=16).sum()
    result['cum_72h'] = hourly_cost.rolling(72, min_periods=48).sum()

    # Z-score of raw funding over trailing 30 days (720 hours)
    roll_mean = funding_rate.rolling(720, min_periods=360).mean()
    roll_std = funding_rate.rolling(720, min_periods=360).std()
    result['funding_zscore'] = (funding_rate - roll_mean) / roll_std.replace(0, np.nan)

    return result


def time_series_ic(signal: pd.Series, fwd_ret: pd.Series) -> dict:
    """
    Compute time-series Spearman rank IC between signal and forward return.
    Returns IC, t-stat, and hit rate.
    """
    # Align and drop NaNs
    combined = pd.concat([signal.rename('signal'), fwd_ret.rename('fwd')], axis=1).dropna()
    if len(combined) < 100:
        return {'ic': np.nan, 't_stat': np.nan, 'hit_rate': np.nan, 'n': len(combined)}

    ic, pval = stats.spearmanr(combined['signal'], combined['fwd'])

    # t-stat: IC * sqrt(N-2) / sqrt(1 - IC^2)
    n = len(combined)
    if abs(ic) < 1.0:
        t_stat = ic * np.sqrt(n - 2) / np.sqrt(1 - ic**2)
    else:
        t_stat = np.inf * np.sign(ic)

    # Hit rate: when signal > 0, does fwd < 0? (mean reversion hypothesis)
    # For mean-reversion: positive funding => negative return
    pos_signal = combined['signal'] > 0
    neg_signal = combined['signal'] < 0
    hits = 0
    total = 0
    if pos_signal.sum() > 0:
        hits += (combined.loc[pos_signal, 'fwd'] < 0).sum()
        total += pos_signal.sum()
    if neg_signal.sum() > 0:
        hits += (combined.loc[neg_signal, 'fwd'] > 0).sum()
        total += neg_signal.sum()
    hit_rate = hits / total if total > 0 else np.nan

    return {'ic': ic, 't_stat': t_stat, 'hit_rate': hit_rate, 'n': n}


def quintile_analysis(signal: pd.Series, fwd_ret: pd.Series, n_quantiles=5) -> pd.DataFrame:
    """
    Split signal into quintiles and compute mean forward return per quintile.
    """
    combined = pd.concat([signal.rename('signal'), fwd_ret.rename('fwd')], axis=1).dropna()
    if len(combined) < 500:
        return pd.DataFrame()

    combined['quintile'] = pd.qcut(combined['signal'], n_quantiles, labels=False, duplicates='drop')

    result = combined.groupby('quintile').agg(
        mean_return=('fwd', 'mean'),
        median_return=('fwd', 'median'),
        std_return=('fwd', 'std'),
        count=('fwd', 'count'),
        mean_signal=('signal', 'mean'),
    )
    # Annualize (rough): multiply by sqrt(8760/horizon) -- but we'll keep raw
    result['sharpe'] = result['mean_return'] / result['std_return'] * np.sqrt(8760)
    result['spread_vs_q2'] = result['mean_return'] - result.loc[2, 'mean_return']

    return result


def cross_sectional_ic(panel: dict, horizon: str) -> dict:
    """
    Compute cross-sectional IC: at each timestamp, rank tokens by funding,
    rank by forward return, compute Spearman correlation.
    Vectorized using pandas rank() to avoid Python loop over timestamps.
    """
    # Build wide dataframes: rows=timestamps, cols=tokens
    funding_wide = pd.DataFrame({sym: panel[sym]['funding_raw'] for sym in panel})
    fwd_wide = pd.DataFrame({sym: panel[sym][f'fwd_{horizon}'] for sym in panel})

    # Count non-NaN per row -- need at least 8 tokens
    valid_mask = funding_wide.notna() & fwd_wide.notna()
    valid_count = valid_mask.sum(axis=1)
    enough = valid_count >= 8

    # Mask out rows without enough data
    f_masked = funding_wide.loc[enough].copy()
    r_masked = fwd_wide.loc[enough].copy()

    # Set positions where either is NaN to NaN
    both_valid = f_masked.notna() & r_masked.notna()
    f_masked = f_masked.where(both_valid)
    r_masked = r_masked.where(both_valid)

    # Rank across tokens (columns) for each timestamp
    f_rank = f_masked.rank(axis=1, method='average')
    r_rank = r_masked.rank(axis=1, method='average')

    # Spearman = Pearson of ranks. Compute row-wise correlation.
    n = both_valid.sum(axis=1)
    f_demean = f_rank.sub(f_rank.mean(axis=1), axis=0)
    r_demean = r_rank.sub(r_rank.mean(axis=1), axis=0)
    cov = (f_demean * r_demean).sum(axis=1)
    f_std = (f_demean ** 2).sum(axis=1).pow(0.5)
    r_std = (r_demean ** 2).sum(axis=1).pow(0.5)
    ics = cov / (f_std * r_std)
    ics = ics.replace([np.inf, -np.inf], np.nan).dropna()

    if len(ics) < 100:
        return {'mean_ic': np.nan, 't_stat': np.nan, 'n_periods': len(ics)}

    mean_ic = ics.mean()
    se = ics.std() / np.sqrt(len(ics))
    t_stat = mean_ic / se if se > 0 else np.nan

    return {
        'mean_ic': mean_ic,
        't_stat': t_stat,
        'n_periods': len(ics),
        'ic_std': ics.std(),
        'pct_negative': (ics < 0).mean(),
    }


def main():
    print("=" * 80)
    print("FUNDING RATE PREDICTIVE POWER ANALYSIS")
    print("=" * 80)
    print()

    # ---- Load data ----
    panel = {}
    for sym in TOP_TOKENS:
        try:
            df = load_token(sym)
            funding = compute_rolling_funding(df['funding_rate'])
            fwd = compute_forward_returns(df['close'], HORIZONS)
            merged = pd.concat([funding, fwd], axis=1)
            panel[sym] = merged
            print(f"  Loaded {sym}: {len(df)} bars, "
                  f"funding NaN: {df['funding_rate'].isna().sum()}, "
                  f"date range: {df.index[0].date()} to {df.index[-1].date()}")
        except Exception as e:
            print(f"  SKIP {sym}: {e}")

    print(f"\nLoaded {len(panel)} tokens")
    print()

    # ========================================================================
    # 1. TIME-SERIES IC: Per-token, funding_raw vs forward returns
    # ========================================================================
    print("=" * 80)
    print("1. TIME-SERIES SPEARMAN IC: funding_rate vs forward return (per token)")
    print("   Hypothesis: NEGATIVE IC (high funding => low future return)")
    print("=" * 80)
    print()

    signals = ['funding_raw', 'cum_24h', 'cum_72h', 'funding_zscore']

    for signal_name in signals:
        print(f"--- Signal: {signal_name} ---")
        header = f"{'Token':>12s}"
        for h in HORIZONS:
            header += f"  {'IC_'+str(h)+'h':>8s}  {'t_'+str(h)+'h':>7s}"
        print(header)

        all_ics = {h: [] for h in HORIZONS}
        all_ts = {h: [] for h in HORIZONS}

        for sym in sorted(panel.keys()):
            row = f"{sym:>12s}"
            for h in HORIZONS:
                result = time_series_ic(panel[sym][signal_name], panel[sym][f'fwd_{h}h'])
                ic = result['ic']
                t = result['t_stat']
                all_ics[h].append(ic)
                all_ts[h].append(t)
                ic_str = f"{ic:+.4f}" if not np.isnan(ic) else "    NaN"
                t_str = f"{t:+.1f}" if not np.isnan(t) else "   NaN"
                row += f"  {ic_str:>8s}  {t_str:>7s}"
            print(row)

        # Print mean across tokens
        row = f"{'MEAN':>12s}"
        for h in HORIZONS:
            mean_ic = np.nanmean(all_ics[h])
            mean_t = np.nanmean(all_ts[h])
            row += f"  {mean_ic:+.4f}  {mean_t:+.1f}"
        print(row)
        print()

    # ========================================================================
    # 2. CROSS-SECTIONAL IC: rank tokens by funding at each timestamp
    # ========================================================================
    print("=" * 80)
    print("2. CROSS-SECTIONAL IC: rank tokens by funding at each timestamp")
    print("   (Spearman IC across tokens at each point in time)")
    print("=" * 80)
    print()

    for signal_name in signals:
        print(f"--- Signal: {signal_name} ---")
        # Build panel for cross-sectional analysis
        cs_panel = {}
        for sym in panel:
            cs_panel[sym] = panel[sym][[signal_name] + [f'fwd_{h}h' for h in HORIZONS]].copy()
            cs_panel[sym].columns = ['funding_raw'] + [f'fwd_{h}h' for h in HORIZONS]

        print(f"{'Horizon':>10s}  {'Mean IC':>8s}  {'t-stat':>7s}  {'IC Std':>7s}  {'%Neg':>6s}  {'N':>8s}")
        for h in HORIZONS:
            result = cross_sectional_ic(cs_panel, f'{h}h')
            print(f"{str(h)+'h':>10s}  {result['mean_ic']:+.4f}  {result['t_stat']:+.1f}  "
                  f"{result.get('ic_std', 0):.4f}  {result.get('pct_negative', 0):.1%}  "
                  f"{result['n_periods']:>8d}")
        print()

    # ========================================================================
    # 3. QUINTILE ANALYSIS: extreme funding quintiles
    # ========================================================================
    print("=" * 80)
    print("3. QUINTILE ANALYSIS: mean forward return by funding quintile")
    print("   Q0 = most negative funding, Q4 = most positive funding")
    print("   Mean-reversion => Q0 should have positive returns, Q4 negative")
    print("=" * 80)
    print()

    # Pool all tokens for quintile analysis
    for signal_name in ['funding_raw', 'funding_zscore']:
        print(f"--- Signal: {signal_name} (pooled across all tokens) ---")
        for h in HORIZONS:
            all_signal = []
            all_fwd = []
            for sym in panel:
                s = panel[sym][signal_name].copy()
                f = panel[sym][f'fwd_{h}h'].copy()
                # Normalize forward returns by token volatility for fair pooling
                vol = f.rolling(720, min_periods=360).std()
                f_norm = f / vol
                valid = s.notna() & f_norm.notna()
                all_signal.append(s[valid])
                all_fwd.append(f_norm[valid])

            pooled_signal = pd.concat(all_signal)
            pooled_fwd = pd.concat(all_fwd)

            qa = quintile_analysis(pooled_signal, pooled_fwd)
            if len(qa) > 0:
                print(f"\n  Horizon: {h}h  (N={len(pooled_signal):,})")
                print(f"  {'Q':>4s}  {'MeanSig':>10s}  {'MeanRet':>10s}  {'MedRet':>10s}  {'Sharpe':>8s}  {'Spread':>8s}  {'Count':>8s}")
                for q in qa.index:
                    print(f"  {q:>4d}  {qa.loc[q, 'mean_signal']:>10.6f}  "
                          f"{qa.loc[q, 'mean_return']:>10.4f}  "
                          f"{qa.loc[q, 'median_return']:>10.4f}  "
                          f"{qa.loc[q, 'sharpe']:>8.2f}  "
                          f"{qa.loc[q, 'spread_vs_q2']:>+8.4f}  "
                          f"{qa.loc[q, 'count']:>8.0f}")
                # Long-short spread
                ls_ret = qa.loc[0, 'mean_return'] - qa.loc[qa.index.max(), 'mean_return']
                print(f"  Long-Short (Q0-Q4) spread: {ls_ret:+.4f}")
        print()

    # ========================================================================
    # 4. CONDITIONAL ANALYSIS: extreme funding events
    # ========================================================================
    print("=" * 80)
    print("4. EXTREME FUNDING EVENT ANALYSIS")
    print("   Events: funding_rate > 95th or < 5th percentile")
    print("=" * 80)
    print()

    for sym in ['BTC', 'ETH', 'SOL', 'DOGE', 'PEPE']:
        if sym not in panel:
            continue
        data = panel[sym]
        fr = data['funding_raw']
        fr_clean = fr.dropna()
        p95 = fr_clean.quantile(0.95)
        p05 = fr_clean.quantile(0.05)

        print(f"--- {sym} ---")
        print(f"  Funding p5={p05:.6f}, p95={p95:.6f}, mean={fr_clean.mean():.6f}")

        for label, mask_series in [('Extreme Positive (>p95)', fr > p95),
                            ('Extreme Negative (<p05)', fr < p05),
                            ('Normal (p25-p75)', (fr > fr_clean.quantile(0.25)) & (fr < fr_clean.quantile(0.75)))]:
            events = data.loc[mask_series.fillna(False)]
            if len(events) < 20:
                continue
            row = f"  {label:>30s}  N={len(events):>5d}  "
            for h in HORIZONS:
                col = f'fwd_{h}h'
                mean_r = events[col].mean()
                row += f"  {h}h: {mean_r*100:+.3f}%"
            print(row)
        print()

    # ========================================================================
    # 5. SUMMARY & VERDICT
    # ========================================================================
    print("=" * 80)
    print("5. SUMMARY & VERDICT")
    print("=" * 80)
    print()

    # Compute the key summary stats
    summary_rows = []
    for signal_name in ['funding_raw', 'cum_24h', 'funding_zscore']:
        for h in HORIZONS:
            ics = []
            for sym in panel:
                result = time_series_ic(panel[sym][signal_name], panel[sym][f'fwd_{h}h'])
                if not np.isnan(result['ic']):
                    ics.append(result['ic'])
            mean_ic = np.mean(ics)
            # t-stat of the mean IC across tokens
            se = np.std(ics) / np.sqrt(len(ics)) if len(ics) > 1 else np.nan
            t_cross = mean_ic / se if se > 0 else np.nan
            summary_rows.append({
                'signal': signal_name,
                'horizon': f'{h}h',
                'mean_ic': mean_ic,
                't_stat_cross': t_cross,
                'n_tokens': len(ics),
                'pct_negative_ic': np.mean([ic < 0 for ic in ics]),
            })

    summary_df = pd.DataFrame(summary_rows)
    print("Mean IC across tokens (time-series IC averaged):")
    print(f"{'Signal':>18s}  {'Horizon':>8s}  {'Mean IC':>8s}  {'t(cross)':>9s}  {'%Neg IC':>8s}")
    for _, r in summary_df.iterrows():
        verdict = ""
        if abs(r['mean_ic']) > 0.03 and abs(r['t_stat_cross']) > 2.0:
            verdict = " <-- SIGNIFICANT"
        elif abs(r['mean_ic']) > 0.02:
            verdict = " <-- marginal"
        print(f"{r['signal']:>18s}  {r['horizon']:>8s}  {r['mean_ic']:+.4f}  "
              f"{r['t_stat_cross']:>+9.2f}  {r['pct_negative_ic']:>8.1%}{verdict}")

    print()
    print("Threshold: |IC| > 0.03 with |t| > 2.0 => worth advancing")
    print()

    # Final verdict
    sig_count = ((summary_df['mean_ic'].abs() > 0.03) & (summary_df['t_stat_cross'].abs() > 2.0)).sum()
    marginal_count = ((summary_df['mean_ic'].abs() > 0.02) & (summary_df['t_stat_cross'].abs() > 1.5)).sum()

    if sig_count > 0:
        print(f"VERDICT: {sig_count} signal/horizon combinations meet significance threshold.")
        print("Funding rate has predictive power. Worth building a strategy around.")
    elif marginal_count > 0:
        print(f"VERDICT: {marginal_count} signal/horizon combinations are marginal.")
        print("Some weak signal exists. May be worth combining with other signals.")
    else:
        print("VERDICT: No signal/horizon combination meets the threshold.")
        print("Funding rate alone does not predict returns at these horizons.")


if __name__ == '__main__':
    main()
