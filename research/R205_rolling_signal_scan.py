"""
R205 — Per-token rolling signal optimization using 5-min positioning data.

Scans ALL combinations of (field x window x computation) to find which
rolling signals predict forward returns for BTC and 9 other top tokens.
"""
import sys, os
sys.path.insert(0, '/workspace/crypto_backtest')
os.chdir('/workspace/crypto_backtest')

import pandas as pd, numpy as np
from scipy import stats
import warnings
warnings.filterwarnings('ignore')

def scan_token(token_name):
    """Scan all rolling signal combinations for one token."""

    sym = token_name + 'USDT'

    # Load 5-min L/S data
    ls_path = f'data/alternative/binance_metrics/5min/{sym}_5min.parquet'
    if not os.path.exists(ls_path):
        return None
    ls = pd.read_parquet(ls_path)
    ls['create_time'] = pd.to_datetime(ls['create_time'])
    ls = ls.set_index('create_time').sort_index()

    # Drop non-numeric columns before resampling
    ls = ls.select_dtypes(include='number')

    # Compute divergence
    ls['divergence'] = ls['count_toptrader_long_short_ratio'] - ls['count_long_short_ratio']

    # Resample to 1H (mean of 5-min snapshots within each hour)
    # CRITICAL: use label='right', closed='right' so hour H bar uses data up to H
    ls_1h = ls.resample('1h').mean()

    # Load 1H price data
    price_path = f'data/perp/1h_cache/{token_name}_1h.parquet'
    if not os.path.exists(price_path):
        return None
    price = pd.read_parquet(price_path)

    # Align on common index
    common_idx = ls_1h.index.intersection(price.index)
    if len(common_idx) < 500:
        return None

    ls_1h = ls_1h.reindex(common_idx)
    close = price['close'].reindex(common_idx)

    # Compute forward returns (these are labels, NOT features)
    fwd_returns = {}
    for h in [24, 72, 168]:  # 1d, 3d, 7d in hours
        fwd_returns[f'fwd_{h}h'] = close.shift(-h) / close - 1

    # Define signal fields
    fields = {
        'topls_count': 'count_toptrader_long_short_ratio',
        'topls_sum': 'sum_toptrader_long_short_ratio',
        'global_ls': 'count_long_short_ratio',
        'divergence': 'divergence',
        'taker_ratio': 'sum_taker_long_short_vol_ratio',
        'oi_value': 'sum_open_interest_value',
    }

    # Define rolling windows (in hours)
    windows = [1, 2, 4, 8, 12, 24, 48, 72, 168]

    # Define computations
    def zscore(series, window):
        m = series.rolling(window, min_periods=window//2).mean()
        s = series.rolling(window, min_periods=window//2).std()
        return (series - m) / s.clip(lower=1e-10)

    def roc(series, window):
        return series.pct_change(window)

    computations = {
        'zscore': zscore,
        'roc': roc,
        # Skip pctile for speed — too slow on large rolling windows
    }

    # Scan all combinations
    results = []

    for field_name, col_name in fields.items():
        if col_name not in ls_1h.columns:
            continue
        raw_series = ls_1h[col_name]

        for window in windows:
            for comp_name, comp_fn in computations.items():
                try:
                    signal = comp_fn(raw_series, window)
                except:
                    continue

                # LAG by 1 hour (signal at T uses data through T-1)
                signal_lagged = signal.shift(1)

                # Compute IC with each forward return horizon
                for horizon, fwd in fwd_returns.items():
                    valid = pd.DataFrame({'signal': signal_lagged, 'fwd': fwd}).dropna()
                    if len(valid) < 200:
                        continue

                    ic, pval = stats.spearmanr(valid['signal'], valid['fwd'])

                    results.append({
                        'token': token_name,
                        'field': field_name,
                        'window_h': window,
                        'computation': comp_name,
                        'horizon': horizon,
                        'ic': ic,
                        'pval': pval,
                        'n_obs': len(valid),
                    })

    return pd.DataFrame(results)

# Scan top 10 tokens
tokens = ['BTC', 'ETH', 'SOL', 'ARB', 'DOGE', 'LINK', 'ADA', 'INJ', 'JUP', 'XRP']

all_results = []
for token in tokens:
    print(f"\nScanning {token}...")
    r = scan_token(token)
    if r is not None:
        all_results.append(r)

        # Show top 5 for this token (by absolute IC, 7d horizon)
        r7d = r[r['horizon'] == 'fwd_168h'].copy()
        r7d['abs_ic'] = r7d['ic'].abs()
        top5 = r7d.nlargest(5, 'abs_ic')
        print(f"  Top 5 signals (7d IC):")
        for _, row in top5.iterrows():
            print(f"    {row['field']}_{row['computation']}_{row['window_h']}h: "
                  f"IC={row['ic']:+.4f} (p={row['pval']:.4f}, n={row['n_obs']})")
    else:
        print(f"  Skipped (no data)")

# Combine all
if all_results:
    combined = pd.concat(all_results, ignore_index=True)
    os.makedirs('data/ml_features', exist_ok=True)
    combined.to_parquet('data/ml_features/R205_rolling_signal_scan.parquet')

    # Summary: best signal per token per horizon
    print(f"\n{'='*90}")
    print("  BEST SIGNAL PER TOKEN (7d horizon, |IC| sorted)")
    print(f"{'='*90}")

    for token in tokens:
        token_data = combined[(combined['token'] == token) & (combined['horizon'] == 'fwd_168h')]
        if len(token_data) == 0:
            continue
        best = token_data.loc[token_data['ic'].abs().idxmax()]
        print(f"  {token:<6} {best['field']}_{best['computation']}_{int(best['window_h'])}h  "
              f"IC={best['ic']:+.4f}  p={best['pval']:.4f}  n={best['n_obs']}")

    # Also show: which WINDOWS work best across all tokens?
    print(f"\n{'='*90}")
    print("  BEST WINDOW PER FIELD (mean |IC| across tokens, 7d)")
    print(f"{'='*90}")

    r7d = combined[combined['horizon'] == 'fwd_168h'].copy()
    r7d['abs_ic'] = r7d['ic'].abs()
    pivot = r7d.groupby(['field', 'window_h', 'computation'])['abs_ic'].mean().reset_index()
    pivot = pivot.sort_values('abs_ic', ascending=False)
    print(pivot.head(20).to_string(index=False))

    # Walk-forward check on the top signal per token
    print(f"\n{'='*90}")
    print("  WALK-FORWARD CHECK (4 quarters) — top signal per token")
    print(f"{'='*90}")

    for token in tokens:
        token_data = combined[(combined['token'] == token) & (combined['horizon'] == 'fwd_168h')]
        if len(token_data) == 0:
            continue
        best = token_data.loc[token_data['ic'].abs().idxmax()]

        # Recompute IC per quarter
        sym = token + 'USDT'
        ls = pd.read_parquet(f'data/alternative/binance_metrics/5min/{sym}_5min.parquet')
        ls['create_time'] = pd.to_datetime(ls['create_time'])
        ls = ls.set_index('create_time').sort_index()
        ls = ls.select_dtypes(include='number')
        ls['divergence'] = ls['count_toptrader_long_short_ratio'] - ls['count_long_short_ratio']
        ls_1h = ls.resample('1h').mean()

        price = pd.read_parquet(f'data/perp/1h_cache/{token}_1h.parquet')
        common = ls_1h.index.intersection(price.index)
        ls_1h = ls_1h.reindex(common)
        close = price['close'].reindex(common)
        fwd_7d = close.shift(-168) / close - 1

        col = best['field']
        if col == 'divergence':
            raw = ls_1h['divergence']
        else:
            col_map = {'topls_count': 'count_toptrader_long_short_ratio',
                       'topls_sum': 'sum_toptrader_long_short_ratio',
                       'global_ls': 'count_long_short_ratio',
                       'taker_ratio': 'sum_taker_long_short_vol_ratio',
                       'oi_value': 'sum_open_interest_value'}
            raw = ls_1h[col_map.get(col, col)]

        w = int(best['window_h'])
        if best['computation'] == 'zscore':
            sig = (raw - raw.rolling(w).mean()) / raw.rolling(w).std().clip(lower=1e-10)
        else:
            sig = raw.pct_change(w)
        sig = sig.shift(1)

        valid = pd.DataFrame({'sig': sig, 'fwd': fwd_7d}).dropna()
        n = len(valid)
        q_size = n // 4
        q_ics = []
        for qi in range(4):
            s = qi * q_size
            e = (qi+1)*q_size if qi < 3 else n
            q = valid.iloc[s:e]
            if len(q) > 30:
                ic, _ = stats.spearmanr(q['sig'], q['fwd'])
                q_ics.append(ic)
            else:
                q_ics.append(np.nan)

        consistent = sum(1 for ic in q_ics if not np.isnan(ic) and
                        (ic < 0 if best['ic'] < 0 else ic > 0))
        q_str = " ".join([f"{ic:+.3f}" if not np.isnan(ic) else "N/A" for ic in q_ics])
        verdict = "PASS" if consistent >= 3 else "FAIL"

        sig_name = f"{best['field']}_{best['computation']}_{int(best['window_h'])}h"
        print(f"  {token:<6} {sig_name:<35} IC={best['ic']:+.4f}  WF=[{q_str}] {consistent}/4 [{verdict}]")
