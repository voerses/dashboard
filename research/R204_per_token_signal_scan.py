#!/usr/bin/env python3
"""
R204 — Per-Token Signal Scan
=============================
For every token x every feature, compute predictive IC at multiple horizons.
Build a TOKEN x SIGNAL matrix showing where edge exists. Cluster tokens by signal profile.
"""

import sys, os, json
sys.path.insert(0, '/workspace/crypto_backtest')
os.chdir('/workspace/crypto_backtest')

import pandas as pd, numpy as np
from scipy import stats
from collections import defaultdict
import warnings
warnings.filterwarnings('ignore')

# ── Step 1: Load data ──────────────────────────────────────────────────────────
df = pd.read_parquet('data/ml_features/feature_matrix.parquet')
print(f"Loaded: {df.shape}")

if 'token' not in df.columns:
    print("ERROR: No token column. Need to rebuild feature matrix with token IDs.")
    sys.exit(1)

tokens = sorted(df['token'].unique())
features = [c for c in df.columns if c not in ['label_7d_fwd', 'token']]
print(f"Tokens: {len(tokens)}, Features: {len(features)}")

# ── Step 2: Compute IC matrix (token x feature) ───────────────────────────────
ic_matrix = {}
pval_matrix = {}

for token in tokens:
    token_data = df[df['token'] == token].copy()
    token_data = token_data.dropna(subset=['label_7d_fwd'])

    if len(token_data) < 100:
        print(f"  Skipping {token}: only {len(token_data)} rows after dropna")
        continue

    ic_matrix[token] = {}
    pval_matrix[token] = {}

    for feat in features:
        valid = token_data[[feat, 'label_7d_fwd']].dropna()
        if len(valid) < 50:
            ic_matrix[token][feat] = np.nan
            pval_matrix[token][feat] = 1.0
            continue

        ic, pval = stats.spearmanr(valid[feat], valid['label_7d_fwd'])
        ic_matrix[token][feat] = ic
        pval_matrix[token][feat] = pval

ic_df = pd.DataFrame(ic_matrix).T  # rows=tokens, cols=features
pval_df = pd.DataFrame(pval_matrix).T
print(f"\nIC matrix: {ic_df.shape}")

# ── Step 3: Find significant signals per token ────────────────────────────────
print("\n" + "=" * 90)
print("  TOP SIGNALS PER TOKEN (|IC| > 0.05 and p < 0.05)")
print("=" * 90)

token_signal_counts = {}
for token in ic_df.index:
    sig_mask = (ic_df.loc[token].abs() > 0.05) & (pval_df.loc[token] < 0.05)
    sig_features = ic_df.loc[token][sig_mask].sort_values(key=abs, ascending=False)
    token_signal_counts[token] = len(sig_features)

    if len(sig_features) > 0:
        top5 = sig_features.head(5)
        print(f"\n  {token} ({len(sig_features)} significant features):")
        for feat, ic in top5.items():
            print(f"    {feat:<35} IC={ic:+.4f}  p={pval_df.loc[token, feat]:.4f}")

# Summary stats
total_pairs = ic_df.shape[0] * ic_df.shape[1]
sig_pairs = int(((ic_df.abs() > 0.05) & (pval_df < 0.05)).sum().sum())
print(f"\n  IC Matrix Summary: {sig_pairs}/{total_pairs} pairs significant "
      f"({100*sig_pairs/total_pairs:.1f}%)")

# ── Step 4: Walk-forward validate top signals per token ────────────────────────
print("\n" + "=" * 90)
print("  WALK-FORWARD IC STABILITY (4 quarters)")
print("=" * 90)

wf_results = {}
for token in ic_df.index:
    sig_mask = (ic_df.loc[token].abs() > 0.05) & (pval_df.loc[token] < 0.05)
    top_feats = ic_df.loc[token][sig_mask].sort_values(key=abs, ascending=False).head(5).index

    token_data = df[df['token'] == token].dropna(subset=['label_7d_fwd'])
    n = len(token_data)
    q_size = n // 4

    if q_size < 20:
        continue

    wf_results[token] = {}
    for feat in top_feats:
        q_ics = []
        for qi in range(4):
            s = qi * q_size
            e = (qi + 1) * q_size if qi < 3 else n
            q_data = token_data.iloc[s:e]
            valid = q_data[[feat, 'label_7d_fwd']].dropna()
            if len(valid) < 20:
                q_ics.append(np.nan)
                continue
            ic, _ = stats.spearmanr(valid[feat], valid['label_7d_fwd'])
            q_ics.append(ic)

        # Check sign consistency
        valid_ics = [ic for ic in q_ics if not np.isnan(ic)]
        if not valid_ics:
            continue
        full_ic = ic_df.loc[token, feat]
        # Count quarters where IC has same sign as full-sample IC
        positive_quarters = sum(
            1 for ic in valid_ics
            if (ic < 0) == (full_ic < 0)
        )
        same_sign = all(ic > 0 for ic in valid_ics) or all(ic < 0 for ic in valid_ics)

        wf_results[token][feat] = {
            'full_ic': float(full_ic),
            'quarter_ics': q_ics,
            'consistent_quarters': positive_quarters,
            'sign_consistent': same_sign,
        }

    # Print WF results for this token
    if wf_results[token]:
        stable = [f for f, r in wf_results[token].items() if r['consistent_quarters'] >= 3]
        if stable:
            print(f"\n  {token} — {len(stable)}/{len(wf_results[token])} top features WF-stable (>=3/4 consistent):")
            for feat in stable:
                res = wf_results[token][feat]
                q_str = " ".join([f"{ic:+.3f}" if not np.isnan(ic) else "  N/A" for ic in res['quarter_ics']])
                print(f"    {feat:<35} IC={res['full_ic']:+.4f}  WF={res['consistent_quarters']}/4  Q=[{q_str}]")

# ── Step 5: Cluster tokens by signal profile ──────────────────────────────────
binary_matrix = ((ic_df.abs() > 0.05) & (pval_df < 0.05)).astype(int)

# Group features into categories
positioning_feats = [f for f in features if any(k in f for k in ['topls', 'div_', 'taker', 'oi_', 'cs_topls', 'cs_oi'])]
macro_feats = [f for f in features if any(k in f for k in ['sp500', 'dxy', 'vix', 'gold', 'oil', 'us10y'])]
vol_feats = [f for f in features if any(k in f for k in ['dvol', 'vol_', 'bb_'])]
price_feats = [f for f in features if any(k in f for k in ['ret_', 'rsi', 'macd', 'range_pct'])]
funding_feats = [f for f in features if 'fund' in f]
liq_feats = [f for f in features if 'liq' in f]
fg_feats = [f for f in features if 'fg_' in f]

print("\n" + "=" * 90)
print("  TOKEN SIGNAL PROFILE (significant features per category)")
print("=" * 90)
print(f"  {'Token':<10} {'Pos':>5} {'Macro':>6} {'Vol':>5} {'Price':>6} {'Fund':>5} {'Liq':>5} {'FG':>4} {'Total':>6}")
print("  " + "-" * 55)

for token in sorted(ic_df.index):
    counts = {
        'pos': int(binary_matrix.loc[token, [f for f in positioning_feats if f in binary_matrix.columns]].sum()) if positioning_feats else 0,
        'macro': int(binary_matrix.loc[token, [f for f in macro_feats if f in binary_matrix.columns]].sum()) if macro_feats else 0,
        'vol': int(binary_matrix.loc[token, [f for f in vol_feats if f in binary_matrix.columns]].sum()) if vol_feats else 0,
        'price': int(binary_matrix.loc[token, [f for f in price_feats if f in binary_matrix.columns]].sum()) if price_feats else 0,
        'fund': int(binary_matrix.loc[token, [f for f in funding_feats if f in binary_matrix.columns]].sum()) if funding_feats else 0,
        'liq': int(binary_matrix.loc[token, [f for f in liq_feats if f in binary_matrix.columns]].sum()) if liq_feats else 0,
        'fg': int(binary_matrix.loc[token, [f for f in fg_feats if f in binary_matrix.columns]].sum()) if fg_feats else 0,
    }
    total = sum(counts.values())
    print(f"  {token:<10} {counts['pos']:>5} {counts['macro']:>6} {counts['vol']:>5} {counts['price']:>6} "
          f"{counts['fund']:>5} {counts['liq']:>5} {counts['fg']:>4} {total:>6}")

# ── Step 6: Identify token clusters ───────────────────────────────────────────
clusters = defaultdict(list)
for token in ic_df.index:
    cat_counts = {}
    for cat_name, cat_feats in [('positioning', positioning_feats), ('macro', macro_feats),
                                ('vol', vol_feats), ('price', price_feats), ('funding', funding_feats)]:
        cat_cols = [f for f in cat_feats if f in binary_matrix.columns]
        if cat_cols:
            cat_counts[cat_name] = int(binary_matrix.loc[token, cat_cols].sum())

    if cat_counts:
        dominant = max(cat_counts, key=cat_counts.get)
        if cat_counts[dominant] > 0:
            clusters[dominant].append(token)
        else:
            clusters['none'].append(token)

print("\n" + "=" * 90)
print("  TOKEN CLUSTERS BY DOMINANT SIGNAL TYPE")
print("=" * 90)
for cluster, members in sorted(clusters.items()):
    print(f"\n  {cluster.upper()} ({len(members)} tokens): {', '.join(members)}")

# ── Step 7: Summary — actionable strategy candidates ──────────────────────────
print("\n" + "=" * 90)
print("  BEST WF-VALIDATED SIGNAL PER TOKEN")
print("=" * 90)

strategy_candidates = []
for token in wf_results:
    best_feat = None
    best_score = 0
    for feat, res in wf_results[token].items():
        if res['consistent_quarters'] >= 3:
            score = abs(res['full_ic']) * res['consistent_quarters']
            if score > best_score:
                best_score = score
                best_feat = feat

    if best_feat:
        res = wf_results[token][best_feat]
        strategy_candidates.append({
            'token': token, 'feature': best_feat,
            'ic': res['full_ic'], 'wf_quarters': res['consistent_quarters'],
        })
        q_str = " ".join([f"{ic:+.3f}" if not np.isnan(ic) else "  N/A" for ic in res['quarter_ics']])
        print(f"  {token:<10} {best_feat:<35} IC={res['full_ic']:+.4f}  WF={res['consistent_quarters']}/4  Q=[{q_str}]")

print(f"\n  Total strategy candidates: {len(strategy_candidates)} tokens with WF-validated signals")

# ── Save outputs ──────────────────────────────────────────────────────────────
os.makedirs('data/ml_features', exist_ok=True)
ic_df.to_parquet('data/ml_features/R204_ic_matrix.parquet')
pval_df.to_parquet('data/ml_features/R204_pval_matrix.parquet')

# Save strategy candidates as JSON
with open('data/ml_features/R204_strategy_candidates.json', 'w') as f:
    json.dump(strategy_candidates, f, indent=2)

# Save WF results as JSON (convert numpy types)
def convert_numpy(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, dict):
        return {k: convert_numpy(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [convert_numpy(i) for i in obj]
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj

with open('data/ml_features/R204_wf_results.json', 'w') as f:
    json.dump(convert_numpy(wf_results), f, indent=2)

print(f"\nSaved: data/ml_features/R204_ic_matrix.parquet")
print(f"Saved: data/ml_features/R204_pval_matrix.parquet")
print(f"Saved: data/ml_features/R204_strategy_candidates.json")
print(f"Saved: data/ml_features/R204_wf_results.json")
print("\nDone.")
