"""
ML V3 Adversarial Validation
==============================
Three tests designed to BREAK the V3 model:

TEST 1: Permutation test — shuffle y labels, retrain with same temporal split.
        If shuffled precision ~= real precision, the model has no real edge.
        PASS: real precision > shuffled p95 (one-tailed, alpha=0.05)

TEST 2: Cross-token OOS — train on tokens 1-10, test on tokens 11-15.
        If model can't generalize across tokens, the "edge" is token-specific noise.
        PASS: cross-token precision > 52% (above random + transaction cost threshold)

TEST 3: Embargo sensitivity — compare 48h vs 168h embargo.
        If increasing embargo kills the edge, the model was leaking through
        the embargo boundary (autocorrelation in labels).
        PASS: precision drop < 3pp when embargo increases from 48h to 168h
"""

import numpy as np
import pandas as pd
import sys, gc, time, warnings
from pathlib import Path

warnings.filterwarnings('ignore')
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, '/workspace/crypto_backtest')

from sklearn.ensemble import HistGradientBoostingClassifier
from tools.ml_direction_model_v3 import (
    build_market_context, build_dataset, compute_features,
    purged_temporal_split, undersample_balance,
    DATA_DIR, ema_vec, rolling_mean_vec, rolling_std_vec,
)

# ==================== Shared Config ====================

TOP_N = 15
HORIZON = 24
TARGET_TYPE = 'adaptive'
THRESHOLD_MULT = 1.5
SEED = 42
N_PERMUTATIONS = 20  # more permutations for tighter p-value estimates

HP = {
    'max_iter': 400, 'max_depth': 6, 'learning_rate': 0.05,
    'min_samples_leaf': 50, 'max_leaf_nodes': 31,
    'l2_regularization': 1.0, 'max_bins': 128,
}


def get_tokens():
    """Return top_n tokens by file size (same logic as V3)."""
    token_files = sorted(DATA_DIR.glob('*_1h.parquet'),
                         key=lambda f: f.stat().st_size, reverse=True)
    return [f.stem.replace('_1h', '') for f in token_files[:TOP_N]]


def train_single_fold(X_nz, y_nz, ts_nz, train_pct, test_pct, embargo_hours):
    """Train one fold and return (long_p60_prec, short_p60_prec, n_long, n_short)."""
    train_mask, test_mask = purged_temporal_split(ts_nz, train_pct, test_pct, embargo_hours)
    X_train = X_nz[train_mask]; y_train = y_nz[train_mask]
    X_test = X_nz[test_mask]; y_test = y_nz[test_mask]

    if len(X_test) < 50 or len(X_train) < 50:
        return None

    rng = np.random.RandomState(SEED)
    X_bal, y_bal = undersample_balance(X_train, y_train, rng)

    model = HistGradientBoostingClassifier(
        max_iter=HP['max_iter'], max_depth=HP['max_depth'],
        learning_rate=HP['learning_rate'],
        min_samples_leaf=HP['min_samples_leaf'],
        max_leaf_nodes=HP['max_leaf_nodes'],
        l2_regularization=HP['l2_regularization'],
        max_bins=HP['max_bins'],
        early_stopping=True, validation_fraction=0.15,
        n_iter_no_change=20, random_state=SEED, verbose=0,
    )
    model.fit(X_bal, y_bal)

    y_proba = model.predict_proba(X_test)
    classes = list(model.classes_)

    results = {}
    for direction, label in [(1, 'LONG'), (-1, 'SHORT')]:
        if direction not in classes:
            results[label] = (0.5, 0)
            continue
        cls_idx = classes.index(direction)
        probs = y_proba[:, cls_idx]
        signals = probs >= 0.60
        n_sig = signals.sum()
        if n_sig == 0:
            results[label] = (0.5, 0)
        else:
            correct = (y_test[signals] == direction).sum()
            results[label] = (correct / n_sig, int(n_sig))

    del model, X_train, y_train, X_test, y_test, X_bal, y_bal, y_proba
    gc.collect()
    return results


def eval_precision_fold3(X_nz, y_nz, ts_nz, embargo_hours=48):
    """Evaluate precision using fold 3 (0-83% train, 83-100% test). Returns dict."""
    return train_single_fold(X_nz, y_nz, ts_nz, 0.83, 1.00, embargo_hours)


# ==================== TEST 1: Permutation Test ====================

def test_permutation(X_all, y_all, ts_all):
    """
    Shuffle y labels N times, retrain, compare precision to real model.
    PASS criteria: real precision > 95th percentile of shuffled distribution.
    """
    t0 = time.time()
    print('\n' + '=' * 70)
    print('TEST 1: PERMUTATION TEST (label shuffling)')
    print('=' * 70)
    print(f'  N_PERMUTATIONS={N_PERMUTATIONS}, using Fold3 (83-100% test)')
    print(f'  PASS criteria: real_precision > shuffled_p95')

    # Filter to non-neutral
    nz_mask = (y_all == 1) | (y_all == -1)
    X_nz = X_all[nz_mask]; y_nz = y_all[nz_mask]; ts_nz = ts_all[nz_mask]
    print(f'  Non-neutral samples: {len(y_nz):,}')

    # Real model
    print('\n  Training REAL model...')
    real_results = eval_precision_fold3(X_nz, y_nz, ts_nz)
    if real_results is None:
        print('  SKIP: insufficient data for fold')
        return {'status': 'SKIP', 'reason': 'insufficient data'}

    real_long_prec = real_results['LONG'][0]
    real_short_prec = real_results['SHORT'][0]
    real_long_n = real_results['LONG'][1]
    real_short_n = real_results['SHORT'][1]
    print(f'  Real LONG  p60: {real_long_prec:.4f} (n={real_long_n})')
    print(f'  Real SHORT p60: {real_short_prec:.4f} (n={real_short_n})')

    # Permuted models
    perm_long_precs = []
    perm_short_precs = []
    perm_long_ns = []
    perm_short_ns = []

    for i in range(N_PERMUTATIONS):
        rng = np.random.RandomState(SEED + i + 1)
        # Shuffle labels globally to break the feature->label mapping
        y_perm = y_nz.copy()
        rng.shuffle(y_perm)

        perm_results = eval_precision_fold3(X_nz, y_perm, ts_nz)
        if perm_results is None:
            continue

        perm_long_precs.append(perm_results['LONG'][0])
        perm_short_precs.append(perm_results['SHORT'][0])
        perm_long_ns.append(perm_results['LONG'][1])
        perm_short_ns.append(perm_results['SHORT'][1])
        print(f'    Perm {i+1}/{N_PERMUTATIONS}: LONG={perm_results["LONG"][0]:.4f}(n={perm_results["LONG"][1]}) '
              f'SHORT={perm_results["SHORT"][0]:.4f}(n={perm_results["SHORT"][1]})')

    if len(perm_long_precs) < 3:
        print('  SKIP: too few permutations succeeded')
        return {'status': 'SKIP', 'reason': 'too few permutations'}

    perm_long_precs = np.array(perm_long_precs)
    perm_short_precs = np.array(perm_short_precs)

    long_p95 = np.percentile(perm_long_precs, 95)
    short_p95 = np.percentile(perm_short_precs, 95)
    long_pvalue = (perm_long_precs >= real_long_prec).mean()
    short_pvalue = (perm_short_precs >= real_short_prec).mean()

    # Also compute p-value as (count >= real + 1) / (N + 1) for proper permutation test
    long_pvalue_proper = ((perm_long_precs >= real_long_prec).sum() + 1) / (len(perm_long_precs) + 1)
    short_pvalue_proper = ((perm_short_precs >= real_short_prec).sum() + 1) / (len(perm_short_precs) + 1)

    print(f'\n  Permuted LONG  distribution: mean={perm_long_precs.mean():.4f}, '
          f'std={perm_long_precs.std():.4f}, p95={long_p95:.4f}')
    print(f'  Permuted SHORT distribution: mean={perm_short_precs.mean():.4f}, '
          f'std={perm_short_precs.std():.4f}, p95={short_p95:.4f}')
    print(f'  LONG  edge: real={real_long_prec:.4f} vs p95={long_p95:.4f} '
          f'(p-value={long_pvalue_proper:.3f})')
    print(f'  SHORT edge: real={real_short_prec:.4f} vs p95={short_p95:.4f} '
          f'(p-value={short_pvalue_proper:.3f})')

    long_pass = real_long_prec > long_p95
    short_pass = real_short_prec > short_p95
    overall_pass = long_pass or short_pass  # at least one direction significant

    # CRITICAL DIAGNOSTIC: check if permuted models produce meaningful signal counts
    mean_perm_long_n = np.mean(perm_long_ns) if perm_long_ns else 0
    mean_perm_short_n = np.mean(perm_short_ns) if perm_short_ns else 0
    print(f'\n  SIGNAL COUNT DIAGNOSTIC:')
    print(f'    Real model LONG signals at p60:  n={real_long_n}')
    print(f'    Permuted avg LONG signals at p60: n={mean_perm_long_n:.0f} '
          f'(range {min(perm_long_ns)}-{max(perm_long_ns)})')
    print(f'    Real model SHORT signals at p60:  n={real_short_n}')
    print(f'    Permuted avg SHORT signals at p60: n={mean_perm_short_n:.0f} '
          f'(range {min(perm_short_ns)}-{max(perm_short_ns)})')

    if mean_perm_long_n < 10:
        print(f'    WARNING: Permuted models produce almost no signals at p60 threshold.')
        print(f'    This means precision comparison is unreliable (small sample noise).')
        print(f'    The real signal is that permuted models CANNOT produce confident predictions,')
        print(f'    while the real model produces {real_long_n} signals. This IS evidence of an edge.')

    status = 'PASS' if overall_pass else 'FAIL'
    print(f'\n  LONG  test: {"PASS" if long_pass else "FAIL"}')
    print(f'  SHORT test: {"PASS" if short_pass else "FAIL"}')
    print(f'  OVERALL: {status}')
    print(f'  Time: {time.time()-t0:.1f}s')

    return {
        'status': status,
        'real_long_p60': real_long_prec,
        'real_short_p60': real_short_prec,
        'perm_long_mean': float(perm_long_precs.mean()),
        'perm_short_mean': float(perm_short_precs.mean()),
        'perm_long_p95': float(long_p95),
        'perm_short_p95': float(short_p95),
        'long_pvalue': float(long_pvalue_proper),
        'short_pvalue': float(short_pvalue_proper),
        'long_pass': long_pass,
        'short_pass': short_pass,
        'n_permutations': len(perm_long_precs),
        'real_long_n': real_long_n,
        'real_short_n': real_short_n,
        'perm_long_n_mean': float(mean_perm_long_n),
        'perm_short_n_mean': float(mean_perm_short_n),
    }


# ==================== TEST 2: Cross-Token OOS ====================

def test_cross_token(market_ctx):
    """
    Train on tokens 1-10 (first 83% of time), test on tokens 11-15 (last 17% of time).
    BOTH token AND time are held out -- the hardest possible test.
    This prevents leakage through shared market context features at the same timestamps.
    PASS criteria: precision > 52% on unseen tokens in unseen time period.
    """
    t0 = time.time()
    print('\n' + '=' * 70)
    print('TEST 2: CROSS-TOKEN OUT-OF-SAMPLE (with temporal split)')
    print('=' * 70)

    token_files = sorted(DATA_DIR.glob('*_1h.parquet'),
                         key=lambda f: f.stat().st_size, reverse=True)
    all_tokens = [f.stem.replace('_1h', '') for f in token_files[:TOP_N]]

    train_tokens = all_tokens[:10]
    test_tokens = all_tokens[10:15]
    print(f'  Train tokens ({len(train_tokens)}): {", ".join(train_tokens)}')
    print(f'  Test tokens  ({len(test_tokens)}): {", ".join(test_tokens)}')
    print(f'  NOTE: Test uses BOTH unseen tokens AND unseen time period')

    # Build separate datasets
    print('\n  Building TRAIN dataset (tokens 1-10)...')
    X_train_all, y_train_all, ts_train_all, fn = build_dataset(
        train_tokens, market_ctx, horizon=HORIZON,
        target_type=TARGET_TYPE, threshold_mult=THRESHOLD_MULT)

    print('  Building TEST dataset (tokens 11-15)...')
    X_test_all, y_test_all, ts_test_all, fn2 = build_dataset(
        test_tokens, market_ctx, horizon=HORIZON,
        target_type=TARGET_TYPE, threshold_mult=THRESHOLD_MULT)

    # Filter to non-neutral for both
    nz_train = (y_train_all == 1) | (y_train_all == -1)
    X_tr = X_train_all[nz_train]
    y_tr = y_train_all[nz_train]
    ts_tr = ts_train_all[nz_train]

    nz_test = (y_test_all == 1) | (y_test_all == -1)
    X_te = X_test_all[nz_test]
    y_te = y_test_all[nz_test]
    ts_te = ts_test_all[nz_test]

    # TEMPORAL SPLIT: train on first 83% of train token time,
    # test on last 17% of test token time -- with embargo
    # This way BOTH token and time are held out.
    n_tr = len(X_tr)
    train_cutoff_idx = int(n_tr * 0.83)
    train_cutoff_date = pd.Timestamp(ts_tr[train_cutoff_idx])
    embargo_date = train_cutoff_date + pd.Timedelta(hours=168)  # 7-day embargo

    # Training: first 83% of train tokens
    X_tr_use = X_tr[:train_cutoff_idx]
    y_tr_use = y_tr[:train_cutoff_idx]

    # Testing: test tokens AFTER embargo date only
    test_after_embargo = np.array([pd.Timestamp(t) >= embargo_date for t in ts_te])
    X_te_use = X_te[test_after_embargo]
    y_te_use = y_te[test_after_embargo]

    print(f'  Train cutoff: {train_cutoff_date.date()}')
    print(f'  Embargo until: {embargo_date.date()}')
    print(f'  Train non-neutral: {len(y_tr_use):,} | Test non-neutral: {len(y_te_use):,}')

    if len(X_te_use) < 50 or len(X_tr_use) < 100:
        print('  SKIP: insufficient data after temporal + token split')
        return {'status': 'SKIP', 'reason': 'insufficient data'}

    rng = np.random.RandomState(SEED)
    X_bal, y_bal = undersample_balance(X_tr_use, y_tr_use, rng)

    print(f'  Training on {len(y_bal):,} balanced samples...')
    model = HistGradientBoostingClassifier(
        max_iter=HP['max_iter'], max_depth=HP['max_depth'],
        learning_rate=HP['learning_rate'],
        min_samples_leaf=HP['min_samples_leaf'],
        max_leaf_nodes=HP['max_leaf_nodes'],
        l2_regularization=HP['l2_regularization'],
        max_bins=HP['max_bins'],
        early_stopping=True, validation_fraction=0.15,
        n_iter_no_change=20, random_state=SEED, verbose=0,
    )
    model.fit(X_bal, y_bal)

    # Test on completely unseen tokens in unseen time period
    y_proba = model.predict_proba(X_te_use)
    classes = list(model.classes_)

    print(f'\n  Cross-token + temporal OOS results:')
    results = {}
    for direction, label in [(1, 'LONG'), (-1, 'SHORT')]:
        if direction not in classes:
            print(f'    {label}: class not found')
            results[label] = (0.5, 0)
            continue
        cls_idx = classes.index(direction)
        probs = y_proba[:, cls_idx]

        for thr in [0.55, 0.60, 0.65, 0.70]:
            signals = probs >= thr
            n_sig = signals.sum()
            if n_sig == 0:
                continue
            correct = (y_te_use[signals] == direction).sum()
            prec = correct / n_sig
            tag = f'{label}_p{int(thr*100)}'
            results[tag] = (float(prec), int(n_sig))
            print(f'    {tag}: precision={prec:.4f} (n={n_sig})')

    # Primary metric: p60 for either direction
    long_p60 = results.get('LONG_p60', (0.5, 0))[0]
    short_p60 = results.get('SHORT_p60', (0.5, 0))[0]
    long_n = results.get('LONG_p60', (0, 0))[1]
    short_n = results.get('SHORT_p60', (0, 0))[1]

    best_prec = max(long_p60, short_p60)

    overall_pass = best_prec > 0.52
    status = 'PASS' if overall_pass else 'FAIL'

    print(f'\n  Best cross-token+temporal p60: {best_prec:.4f}')
    print(f'  Threshold for PASS: > 0.52')
    print(f'  OVERALL: {status}')
    print(f'  Time: {time.time()-t0:.1f}s')

    del X_train_all, y_train_all, ts_train_all
    del X_test_all, y_test_all, ts_test_all
    del X_tr, y_tr, X_te, y_te, X_bal, y_bal, model, y_proba
    gc.collect()

    return {
        'status': status,
        'long_p60': float(long_p60),
        'short_p60': float(short_p60),
        'long_n': long_n,
        'short_n': short_n,
        'best_prec': float(best_prec),
        'all_results': {k: {'precision': v[0], 'n': v[1]} for k, v in results.items()
                        if isinstance(v, tuple) and len(v) == 2},
    }


# ==================== TEST 3: Embargo Sensitivity ====================

def test_embargo_sensitivity(X_all, y_all, ts_all):
    """
    Compare results with 48h embargo vs 168h embargo.
    If increasing embargo drops precision significantly, the model was
    exploiting autocorrelation across the embargo boundary.
    PASS criteria: precision drop < 3pp when embargo goes 48h -> 168h.
    """
    t0 = time.time()
    print('\n' + '=' * 70)
    print('TEST 3: EMBARGO SENSITIVITY (48h vs 168h)')
    print('=' * 70)
    print(f'  PASS criteria: precision drop < 3pp when embargo 48h -> 168h')

    nz_mask = (y_all == 1) | (y_all == -1)
    X_nz = X_all[nz_mask]; y_nz = y_all[nz_mask]; ts_nz = ts_all[nz_mask]

    # Test with 48h embargo (the V3 default)
    print('\n  Training with 48h embargo (V3 default)...')
    r48 = eval_precision_fold3(X_nz, y_nz, ts_nz, embargo_hours=48)

    # Test with 168h embargo (7 days - very conservative)
    print('  Training with 168h embargo (7 days)...')
    r168 = eval_precision_fold3(X_nz, y_nz, ts_nz, embargo_hours=168)

    # Test with 336h embargo (14 days - extreme)
    print('  Training with 336h embargo (14 days)...')
    r336 = eval_precision_fold3(X_nz, y_nz, ts_nz, embargo_hours=336)

    if r48 is None or r168 is None or r336 is None:
        print('  SKIP: insufficient data for some embargo levels')
        return {'status': 'SKIP', 'reason': 'insufficient data'}

    print(f'\n  Results summary:')
    print(f'  {"Embargo":<12} {"LONG p60":>12} {"(n)":>6} {"SHORT p60":>12} {"(n)":>6}')
    print(f'  {"-"*50}')
    for label, r in [('48h', r48), ('168h', r168), ('336h', r336)]:
        print(f'  {label:<12} {r["LONG"][0]:>12.4f} {r["LONG"][1]:>6} '
              f'{r["SHORT"][0]:>12.4f} {r["SHORT"][1]:>6}')

    # Check drop from 48h to 168h
    long_drop = r48['LONG'][0] - r168['LONG'][0]
    short_drop = r48['SHORT'][0] - r168['SHORT'][0]

    # Also check 48h to 336h for extreme test
    long_drop_extreme = r48['LONG'][0] - r336['LONG'][0]
    short_drop_extreme = r48['SHORT'][0] - r336['SHORT'][0]

    print(f'\n  48h -> 168h drop: LONG={long_drop:+.4f}, SHORT={short_drop:+.4f}')
    print(f'  48h -> 336h drop: LONG={long_drop_extreme:+.4f}, SHORT={short_drop_extreme:+.4f}')

    # PASS if neither direction drops more than 3pp
    max_drop_168 = max(long_drop, short_drop)
    max_drop_336 = max(long_drop_extreme, short_drop_extreme)

    pass_168 = max_drop_168 < 0.03
    pass_336 = max_drop_336 < 0.05  # more lenient for extreme embargo

    status = 'PASS' if pass_168 else 'FAIL'
    print(f'\n  Max drop 48h->168h: {max_drop_168:+.4f} (threshold: <0.03)')
    print(f'  Max drop 48h->336h: {max_drop_336:+.4f} (threshold: <0.05)')
    print(f'  168h test: {"PASS" if pass_168 else "FAIL"}')
    print(f'  336h test: {"PASS" if pass_336 else "FAIL"} (informational)')
    print(f'  OVERALL: {status}')
    print(f'  Time: {time.time()-t0:.1f}s')

    del X_nz, y_nz, ts_nz
    gc.collect()

    return {
        'status': status,
        'embargo_48h': {
            'long_p60': float(r48['LONG'][0]), 'long_n': r48['LONG'][1],
            'short_p60': float(r48['SHORT'][0]), 'short_n': r48['SHORT'][1],
        },
        'embargo_168h': {
            'long_p60': float(r168['LONG'][0]), 'long_n': r168['LONG'][1],
            'short_p60': float(r168['SHORT'][0]), 'short_n': r168['SHORT'][1],
        },
        'embargo_336h': {
            'long_p60': float(r336['LONG'][0]), 'long_n': r336['LONG'][1],
            'short_p60': float(r336['SHORT'][0]), 'short_n': r336['SHORT'][1],
        },
        'drop_48_168_long': float(long_drop),
        'drop_48_168_short': float(short_drop),
        'drop_48_336_long': float(long_drop_extreme),
        'drop_48_336_short': float(short_drop_extreme),
        'pass_168': pass_168,
        'pass_336': pass_336,
    }


# ==================== Main ====================

def main():
    t_start = time.time()
    print('=' * 70)
    print('ML V3 ADVERSARIAL VALIDATION')
    print('Objective: Break the model. Find flaws in the claimed edge.')
    print('=' * 70)

    # Build shared data
    print('\n--- Building market context ---')
    market_ctx = build_market_context(DATA_DIR, top_n=TOP_N)

    tokens = get_tokens()
    print(f'\nTokens: {", ".join(tokens)}')

    print('\n--- Building dataset (Experiment B config: adaptive 1.5sig, 24h) ---')
    X_all, y_all, ts_all, feat_names = build_dataset(
        tokens, market_ctx, horizon=HORIZON,
        target_type=TARGET_TYPE, threshold_mult=THRESHOLD_MULT)

    label_counts = {
        'long': int((y_all == 1).sum()),
        'short': int((y_all == -1).sum()),
        'neutral': int((y_all == 0).sum()),
    }
    print(f'  Labels: LONG={label_counts["long"]:,}, SHORT={label_counts["short"]:,}, '
          f'NEUTRAL={label_counts["neutral"]:,}')

    # ============ RUN TESTS ============
    results = {}

    # TEST 1: Permutation
    results['test1_permutation'] = test_permutation(X_all, y_all, ts_all)

    # TEST 3: Embargo sensitivity (run before cross-token to reuse X_all)
    results['test3_embargo'] = test_embargo_sensitivity(X_all, y_all, ts_all)

    # Free memory before cross-token test
    del X_all, y_all, ts_all
    gc.collect()

    # TEST 2: Cross-token OOS
    results['test2_cross_token'] = test_cross_token(market_ctx)

    del market_ctx; gc.collect()

    # ============ FINAL REPORT ============
    print('\n' + '=' * 70)
    print('ADVERSARIAL VALIDATION — FINAL REPORT')
    print('=' * 70)

    tests = [
        ('TEST 1: Permutation test', 'test1_permutation',
         'Real precision must exceed 95th percentile of label-shuffled models'),
        ('TEST 2: Cross-token OOS', 'test2_cross_token',
         'Precision > 52% on tokens never seen during training'),
        ('TEST 3: Embargo sensitivity', 'test3_embargo',
         'Precision drop < 3pp when embargo increases 48h -> 168h'),
    ]

    n_pass = 0
    n_fail = 0
    n_skip = 0

    for name, key, criteria in tests:
        r = results[key]
        status = r['status']
        if status == 'PASS':
            n_pass += 1
            marker = 'PASS'
        elif status == 'FAIL':
            n_fail += 1
            marker = '** FAIL **'
        else:
            n_skip += 1
            marker = 'SKIP'

        print(f'\n  {name}: {marker}')
        print(f'    Criteria: {criteria}')

        if key == 'test1_permutation' and status != 'SKIP':
            print(f'    Real LONG p60:  {r["real_long_p60"]:.4f} vs shuffled p95: {r["perm_long_p95"]:.4f} '
                  f'(p={r["long_pvalue"]:.3f})')
            print(f'    Real SHORT p60: {r["real_short_p60"]:.4f} vs shuffled p95: {r["perm_short_p95"]:.4f} '
                  f'(p={r["short_pvalue"]:.3f})')
            print(f'    LONG {"beats" if r["long_pass"] else "does NOT beat"} shuffled distribution')
            print(f'    SHORT {"beats" if r["short_pass"] else "does NOT beat"} shuffled distribution')

        elif key == 'test2_cross_token' and status != 'SKIP':
            print(f'    LONG p60:  {r["long_p60"]:.4f} (n={r["long_n"]})')
            print(f'    SHORT p60: {r["short_p60"]:.4f} (n={r["short_n"]})')
            print(f'    Best direction: {r["best_prec"]:.4f} (threshold: 0.52)')

        elif key == 'test3_embargo' and status != 'SKIP':
            e48 = r['embargo_48h']
            e168 = r['embargo_168h']
            e336 = r['embargo_336h']
            print(f'    48h:  LONG={e48["long_p60"]:.4f} SHORT={e48["short_p60"]:.4f}')
            print(f'    168h: LONG={e168["long_p60"]:.4f} SHORT={e168["short_p60"]:.4f}')
            print(f'    336h: LONG={e336["long_p60"]:.4f} SHORT={e336["short_p60"]:.4f}')
            print(f'    48h->168h drop: LONG={r["drop_48_168_long"]:+.4f} SHORT={r["drop_48_168_short"]:+.4f}')

    print(f'\n  {"="*50}')
    print(f'  SUMMARY: {n_pass} PASS / {n_fail} FAIL / {n_skip} SKIP')

    if n_fail == 0 and n_skip == 0:
        print(f'  VERDICT: Model survives all adversarial tests.')
        print(f'  The edge appears statistically real (not noise or leakage).')
    elif n_fail == 0:
        print(f'  VERDICT: Model passes all run tests (some skipped).')
    elif n_fail <= 1:
        print(f'  VERDICT: Model has weaknesses. Edge may be partially real.')
    else:
        print(f'  VERDICT: Model FAILS multiple tests. Edge is likely spurious.')
    print(f'  {"="*50}')

    elapsed = time.time() - t_start
    print(f'\nTotal adversarial validation time: {elapsed:.0f}s')

    return results


if __name__ == '__main__':
    results = main()
