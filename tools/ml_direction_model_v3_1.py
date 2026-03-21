"""
ML Direction Model V3.1 — Iterating on V3
==========================================
V3 showed marginal edge with proper temporal splits:
  - LONG p60=60.2%, p70=60.9% (adaptive target)
  - SHORT p60=55.1%, p70=57.9%
  - SHORT fails in bull markets (Fold1: 44.7%)

V3.1 improvements targeting the weak spots:
  1. More training tokens (20 instead of 15)
  2. Recency-weighted training (exponential decay, halflife=0.3)
  3. Additional features: ret_336, ret_720, vol_momentum, obv_slope
  4. Separate LONG and SHORT models (different regimes favor different sides)
  5. Per-regime precision tracking to understand failure modes
"""

import numpy as np
import pandas as pd
import sys, gc, time, warnings
from pathlib import Path

warnings.filterwarnings('ignore')
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, '/workspace/crypto_backtest')

from sklearn.ensemble import HistGradientBoostingClassifier
import joblib

DATA_DIR = Path('/workspace/crypto_backtest/data/perp/1h_cache')
RESULTS_DIR = Path('/workspace/crypto_backtest/results/v4')

from tools.ml_direction_model_v3 import (
    build_market_context, compute_features, ema_vec,
    rolling_mean_vec, rolling_std_vec, rolling_max_vec, rolling_min_vec,
    undersample_balance, purged_temporal_split,
)


def compute_features_v31(close, high, low, volume, funding=None):
    """Extended features: V3 base (34) + 4 new = 38 base features."""
    features = compute_features(close, high, low, volume, funding)
    n = len(close)

    # New: longer-term momentum
    ret_336 = np.zeros(n)
    ret_336[336:] = np.log(close[336:] / close[:-336])
    features['ret_336'] = ret_336

    ret_720 = np.zeros(n)
    ret_720[720:] = np.log(close[720:] / close[:-720])
    features['ret_720'] = ret_720

    # New: volume momentum (5-bar vs 50-bar volume MA ratio)
    vol_ma5 = rolling_mean_vec(volume, 5)
    vol_ma50 = rolling_mean_vec(volume, 50)
    vol_momentum = np.where(np.nan_to_num(vol_ma50, nan=1) > 0,
                            np.nan_to_num(vol_ma5, nan=0) / np.maximum(np.nan_to_num(vol_ma50, nan=1), 1e-10),
                            1.0)
    features['vol_momentum'] = np.nan_to_num(vol_momentum, nan=1.0)

    # New: OBV (on-balance volume) slope — 20-bar regression slope of cumulative volume
    ret_1 = np.zeros(n)
    ret_1[1:] = np.log(close[1:] / close[:-1])
    signed_vol = np.sign(ret_1) * volume
    obv = np.cumsum(signed_vol)
    obv_norm = obv / np.maximum(np.abs(obv).max(), 1e-10)  # normalize
    obv_ema20 = ema_vec(obv_norm, 20)
    obv_slope = np.zeros(n)
    obv_slope[1:] = obv_norm[1:] - obv_ema20[:-1]
    features['obv_slope'] = np.nan_to_num(obv_slope, nan=0.0)

    return features


def build_dataset_v31(tokens, market_ctx, horizon=24, threshold_mult=1.5):
    """Build dataset with extended features and adaptive target."""
    X_list, y_list, ts_list = [], [], []
    feat_names = None

    for i, token in enumerate(tokens):
        fpath = DATA_DIR / f'{token}_1h.parquet'
        if not fpath.exists():
            continue
        df = pd.read_parquet(fpath)
        if len(df) < 800:  # need more data for longer features
            del df; continue

        close = df['close'].values.astype(np.float64)
        high = df['high'].values.astype(np.float64)
        low = df['low'].values.astype(np.float64)
        volume = df['volume'].values.astype(np.float64)
        funding = df['funding_rate'].values.astype(np.float64) if 'funding_rate' in df.columns else None
        n = len(close)

        features = compute_features_v31(close, high, low, volume, funding)

        # Adaptive labels
        labels = np.zeros(n, dtype=np.int8)
        if n > horizon:
            ret_1h = np.zeros(n)
            ret_1h[1:] = np.log(close[1:] / close[:-1])
            vol_168 = np.nan_to_num(rolling_std_vec(ret_1h, 168), nan=0.02)
            expected_move = np.maximum(vol_168 * np.sqrt(horizon) * threshold_mult, 0.01)

            fwd_ret = np.zeros(n)
            fwd_ret[:n - horizon] = close[horizon:] / close[:n - horizon] - 1.0
            labels[fwd_ret > expected_move] = 1
            labels[fwd_ret < -expected_move] = -1
            labels[-horizon:] = 0

        # Market context
        ctx = market_ctx.reindex(df.index, method='ffill')
        features['btc_ret_1h'] = np.nan_to_num(ctx['btc_ret_1h'].values, nan=0.0)
        features['btc_ret_24h'] = np.nan_to_num(ctx['btc_ret_24h'].values, nan=0.0)
        features['btc_vol_24h'] = np.nan_to_num(ctx['btc_vol_24h'].values, nan=0.0)
        features['market_regime'] = np.nan_to_num(ctx['market_regime'].values, nan=3.0)
        features['market_breadth_ema20'] = np.nan_to_num(ctx['market_breadth_ema20'].values, nan=0.5)
        features['dispersion_zscore'] = np.nan_to_num(ctx['dispersion_zscore'].values, nan=0.0)

        token_ret_1h = np.zeros(n)
        token_ret_1h[1:] = np.log(close[1:] / close[:-1])
        btc_ret = np.nan_to_num(ctx['_btc_ret_1h_series'].values, nan=0.0)
        corr = pd.Series(token_ret_1h).rolling(168, min_periods=48).corr(pd.Series(btc_ret)).values
        features['token_btc_corr_168h'] = np.nan_to_num(corr, nan=0.0)
        del ctx

        fn = sorted([k for k in features.keys() if not k.startswith('_')])
        X = np.column_stack([features[k] for k in fn]).astype(np.float32)

        valid = np.ones(n, dtype=bool)
        valid[:720] = False  # need 720 bars for ret_720
        valid[-horizon:] = False
        nan_mask = np.any(np.isnan(X), axis=1) | np.any(np.isinf(X), axis=1)
        valid = valid & ~nan_mask

        if valid.sum() > 100:
            X_list.append(X[valid])
            y_list.append(labels[valid])
            ts_list.append(df.index[valid].values)
            if feat_names is None:
                feat_names = fn

        del df, close, high, low, volume, funding, features, X, labels
        gc.collect()

        if (i + 1) % 5 == 0:
            print(f'  Loaded {i+1}/{len(tokens)} tokens')

    X_all = np.vstack(X_list).astype(np.float32)
    y_all = np.concatenate(y_list)
    ts_all = np.concatenate(ts_list)
    del X_list, y_list, ts_list; gc.collect()

    sort_idx = np.argsort(ts_all)
    X_all = X_all[sort_idx]; y_all = y_all[sort_idx]; ts_all = ts_all[sort_idx]
    del sort_idx; gc.collect()

    print(f'  Dataset: {len(y_all):,} samples, {len(feat_names)} features, {X_all.nbytes/1e6:.0f}MB')
    return X_all, y_all, ts_all, feat_names


def compute_sample_weights(n_samples, halflife=0.3):
    """Exponential recency weighting."""
    positions = np.linspace(0, 1, n_samples)
    lam = np.log(2) / (1 - halflife)
    weights = np.exp(lam * (positions - 1))
    return weights / weights.mean()


def train_and_eval_v31(X_all, y_all, ts_all, feat_names, experiment_name,
                        use_recency=False, separate_models=False):
    """Train with purged temporal walk-forward."""
    print(f'\n{"="*70}')
    print(f'EXPERIMENT: {experiment_name}')
    print(f'{"="*70}')

    nz_mask = (y_all == 1) | (y_all == -1)
    X_nz = X_all[nz_mask]; y_nz = y_all[nz_mask]; ts_nz = ts_all[nz_mask]
    n = len(y_nz)
    print(f'  Non-neutral: {n:,} (L={(y_nz==1).sum():,}, S={(y_nz==-1).sum():,})')

    regime_col_idx = feat_names.index('market_regime') if 'market_regime' in feat_names else None
    regime_names = {0: 'CRISIS', 1: 'QUIET', 2: 'UPTREND', 3: 'RANGE', 4: 'DOWNTREND'}

    folds = [
        ('Fold1: 0-50%/50-67%', 0.50, 0.67),
        ('Fold2: 0-67%/67-83%', 0.67, 0.83),
        ('Fold3: 0-83%/83-100%', 0.83, 1.00),
    ]

    all_fold_metrics = []
    best_model = None

    for fold_name, train_pct, test_pct in folds:
        train_mask, test_mask = purged_temporal_split(ts_nz, train_pct, test_pct, 48)

        X_train = X_nz[train_mask]; y_train = y_nz[train_mask]
        X_test = X_nz[test_mask]; y_test = y_nz[test_mask]

        if len(X_test) < 100 or len(X_train) < 100:
            continue

        train_end = pd.Timestamp(ts_nz[train_mask][-1]).date()
        test_start = pd.Timestamp(ts_nz[test_mask][0]).date()
        test_end = pd.Timestamp(ts_nz[test_mask][-1]).date()

        rng = np.random.RandomState(42)

        if separate_models:
            # Train separate LONG and SHORT models
            # LONG model: train on LONG vs (SHORT+NEUTRAL), predict LONG probability
            # SHORT model: train on SHORT vs (LONG+NEUTRAL), predict SHORT probability
            models = {}
            for direction, label in [(1, 'LONG'), (-1, 'SHORT')]:
                y_binary = (y_train == direction).astype(np.int8)
                # Balance
                pos_idx = np.where(y_binary == 1)[0]
                neg_idx = np.where(y_binary == 0)[0]
                n_min = min(len(pos_idx), len(neg_idx))
                if n_min == 0:
                    continue
                pos_s = rng.choice(pos_idx, n_min, replace=False)
                neg_s = rng.choice(neg_idx, n_min, replace=False)
                bal_idx = np.sort(np.concatenate([pos_s, neg_s]))
                X_bal = X_train[bal_idx]; y_bal = y_binary[bal_idx]

                model = HistGradientBoostingClassifier(
                    max_iter=400, max_depth=6, learning_rate=0.05,
                    min_samples_leaf=50, max_leaf_nodes=31,
                    l2_regularization=1.0, max_bins=128,
                    early_stopping=True, validation_fraction=0.15,
                    n_iter_no_change=20, random_state=42, verbose=0,
                )
                if use_recency:
                    weights = compute_sample_weights(len(y_bal))
                    model.fit(X_bal, y_bal, sample_weight=weights)
                else:
                    model.fit(X_bal, y_bal)
                models[direction] = model
                del X_bal, y_bal

            # Evaluate
            fold_metrics = {}
            print(f'\n  {fold_name} (test {test_start}→{test_end})')
            for direction, label in [(1, 'LONG'), (-1, 'SHORT')]:
                if direction not in models:
                    continue
                model = models[direction]
                y_proba = model.predict_proba(X_test)
                classes = list(model.classes_)
                if 1 not in classes:
                    continue
                cls_idx = classes.index(1)  # probability of being this direction
                probs = y_proba[:, cls_idx]

                for thr in [0.55, 0.60, 0.65, 0.70, 0.75]:
                    signals = probs >= thr
                    n_sig = signals.sum()
                    if n_sig == 0:
                        continue
                    correct = (y_test[signals] == direction).sum()
                    prec = correct / n_sig
                    key = f'{label}_p{int(thr*100)}'
                    fold_metrics[key] = {'precision': float(prec), 'n': int(n_sig)}

                # Per-regime
                if regime_col_idx is not None:
                    regimes = X_test[:, regime_col_idx]
                    for r_val, r_name in regime_names.items():
                        r_mask = regimes == r_val
                        r_signals = (probs >= 0.60) & r_mask
                        n_r = r_signals.sum()
                        if n_r > 20:
                            correct_r = (y_test[r_signals] == direction).sum()
                            fold_metrics[f'{label}_regime_{r_name}_p60'] = {
                                'precision': float(correct_r / n_r), 'n': int(n_r)}

            if fold_name.startswith('Fold3'):
                best_model = models.get(1)  # LONG model for production

        else:
            # Standard: single multi-class model
            X_bal, y_bal = undersample_balance(X_train, y_train, rng)

            model = HistGradientBoostingClassifier(
                max_iter=400, max_depth=6, learning_rate=0.05,
                min_samples_leaf=50, max_leaf_nodes=31,
                l2_regularization=1.0, max_bins=128,
                early_stopping=True, validation_fraction=0.15,
                n_iter_no_change=20, random_state=42, verbose=0,
            )
            if use_recency:
                weights = compute_sample_weights(len(y_bal))
                model.fit(X_bal, y_bal, sample_weight=weights)
            else:
                model.fit(X_bal, y_bal)
            del X_bal, y_bal

            y_proba = model.predict_proba(X_test)
            classes = list(model.classes_)

            fold_metrics = {}
            print(f'\n  {fold_name} (test {test_start}→{test_end})')
            for direction, label in [(1, 'LONG'), (-1, 'SHORT')]:
                if direction not in classes:
                    continue
                cls_idx = classes.index(direction)
                probs = y_proba[:, cls_idx]

                for thr in [0.55, 0.60, 0.65, 0.70, 0.75]:
                    signals = probs >= thr
                    n_sig = signals.sum()
                    if n_sig == 0:
                        continue
                    correct = (y_test[signals] == direction).sum()
                    prec = correct / n_sig
                    key = f'{label}_p{int(thr*100)}'
                    fold_metrics[key] = {'precision': float(prec), 'n': int(n_sig)}

                # Per-regime
                if regime_col_idx is not None:
                    regimes = X_test[:, regime_col_idx]
                    for r_val, r_name in regime_names.items():
                        r_mask = regimes == r_val
                        r_signals = (probs >= 0.60) & r_mask
                        n_r = r_signals.sum()
                        if n_r > 20:
                            correct_r = (y_test[r_signals] == direction).sum()
                            fold_metrics[f'{label}_regime_{r_name}_p60'] = {
                                'precision': float(correct_r / n_r), 'n': int(n_r)}

            if fold_name.startswith('Fold3'):
                best_model = model

        all_fold_metrics.append(fold_metrics)

        # Print fold summary
        for direction, label in [(1, 'LONG'), (-1, 'SHORT')]:
            parts = []
            for thr in [0.55, 0.60, 0.65, 0.70, 0.75]:
                key = f'{label}_p{int(thr*100)}'
                if key in fold_metrics:
                    m = fold_metrics[key]
                    parts.append(f'p{int(thr*100)}={m["precision"]:.3f}({m["n"]})')
            if parts:
                print(f'    {label}: {" | ".join(parts)}')

        # Print per-regime
        if regime_col_idx is not None:
            for direction, label in [(1, 'LONG'), (-1, 'SHORT')]:
                regime_parts = []
                for r_val, r_name in regime_names.items():
                    key = f'{label}_regime_{r_name}_p60'
                    if key in fold_metrics:
                        m = fold_metrics[key]
                        regime_parts.append(f'{r_name}={m["precision"]:.3f}(n={m["n"]})')
                if regime_parts:
                    print(f'    {label} regimes: {" | ".join(regime_parts)}')

        del X_train, y_train, X_test, y_test
        gc.collect()

    del X_nz, y_nz, ts_nz; gc.collect()

    # Average metrics across folds
    avg_metrics = {}
    all_keys = set()
    for fm in all_fold_metrics:
        all_keys.update(fm.keys())

    for key in sorted(all_keys):
        precs = [fm[key]['precision'] for fm in all_fold_metrics if key in fm]
        ns = [fm[key]['n'] for fm in all_fold_metrics if key in fm]
        if precs:
            avg_metrics[key] = {
                'mean_precision': float(np.mean(precs)),
                'std_precision': float(np.std(precs)),
                'min_precision': float(np.min(precs)),
                'max_precision': float(np.max(precs)),
                'total_n': int(sum(ns)),
                'n_folds': len(precs),
            }

    print(f'\n  === Averaged across {len(all_fold_metrics)} folds ===')
    for direction, label in [(1, 'LONG'), (-1, 'SHORT')]:
        parts = []
        for thr in [0.55, 0.60, 0.65, 0.70, 0.75]:
            key = f'{label}_p{int(thr*100)}'
            if key in avg_metrics:
                m = avg_metrics[key]
                parts.append(f'p{int(thr*100)}={m["mean_precision"]:.3f}±{m["std_precision"]:.3f} '
                             f'[min={m["min_precision"]:.3f}]')
        if parts:
            print(f'    {label}: {" | ".join(parts)}')

    return avg_metrics, best_model, feat_names


def main():
    t_start = time.time()
    print('=' * 70)
    print('ML DIRECTION MODEL V3.1 — ITERATION 2')
    print('=' * 70)

    market_ctx = build_market_context(DATA_DIR, top_n=15)

    token_files = sorted(DATA_DIR.glob('*_1h.parquet'),
                         key=lambda f: f.stat().st_size, reverse=True)
    tokens = [f.stem.replace('_1h', '') for f in token_files[:20]]  # 20 tokens now

    print(f'\nBuilding extended dataset (20 tokens, adaptive target)...')
    X, y, ts, fn = build_dataset_v31(tokens, market_ctx, horizon=24, threshold_mult=1.5)
    del market_ctx; gc.collect()

    # ==========================================
    # G: V3 config + new features (no recency)
    # ==========================================
    metrics_g, model_g, feats_g = train_and_eval_v31(
        X, y, ts, fn, 'G: Extended features (45), no recency')

    # ==========================================
    # H: V3.1 with recency weighting
    # ==========================================
    metrics_h, model_h, feats_h = train_and_eval_v31(
        X, y, ts, fn, 'H: Extended features + recency weighting',
        use_recency=True)

    # ==========================================
    # I: Separate LONG/SHORT models
    # ==========================================
    metrics_i, model_i, feats_i = train_and_eval_v31(
        X, y, ts, fn, 'I: Separate LONG/SHORT models',
        separate_models=True)

    # ==========================================
    # J: Separate models + recency
    # ==========================================
    metrics_j, model_j, feats_j = train_and_eval_v31(
        X, y, ts, fn, 'J: Separate models + recency',
        use_recency=True, separate_models=True)

    del X, y, ts; gc.collect()

    # ==========================================
    # Final Comparison (including V3 results)
    # ==========================================
    print(f'\n{"="*70}')
    print('COMPARISON: V3 vs V3.1 Experiments')
    print(f'{"="*70}')

    # V3 best result for reference
    v3_ref = {
        'LONG_p60': {'mean_precision': 0.6021},
        'LONG_p70': {'mean_precision': 0.6094},
        'SHORT_p60': {'mean_precision': 0.5514},
        'SHORT_p70': {'mean_precision': 0.5791},
    }

    all_results = [
        ('V3 B: Adaptive (ref)', v3_ref),
        ('G: Extended feats', metrics_g),
        ('H: Extended + recency', metrics_h),
        ('I: Separate models', metrics_i),
        ('J: Separate + recency', metrics_j),
    ]

    print(f'{"Experiment":<30} {"L p60":>8} {"L p70":>8} {"S p60":>8} {"S p70":>8} {"Avg":>8}')
    print(f'{"-"*72}')

    best_name = None
    best_score = 0

    for name, m in all_results:
        lp60 = m.get('LONG_p60', {}).get('mean_precision', 0)
        lp70 = m.get('LONG_p70', {}).get('mean_precision', 0)
        sp60 = m.get('SHORT_p60', {}).get('mean_precision', 0)
        sp70 = m.get('SHORT_p70', {}).get('mean_precision', 0)
        avg = np.mean([lp60, lp70, sp60, sp70]) if all([lp60, lp70, sp60, sp70]) else 0
        print(f'{name:<30} {lp60:>8.4f} {lp70:>8.4f} {sp60:>8.4f} {sp70:>8.4f} {avg:>8.4f}')
        if avg > best_score:
            best_score = avg
            best_name = name

    print(f'\nBest: {best_name} (avg={best_score:.4f})')

    # Check per-regime performance for best
    print(f'\n  Per-regime precision (best model, p>=0.60):')
    best_metrics = {'G': metrics_g, 'H': metrics_h, 'I': metrics_i, 'J': metrics_j}
    best_key = best_name[0] if best_name and best_name[0] in best_metrics else 'G'
    bm = best_metrics.get(best_key, metrics_g)

    for direction, label in [(1, 'LONG'), (-1, 'SHORT')]:
        for r_name in ['CRISIS', 'QUIET', 'UPTREND', 'RANGE', 'DOWNTREND']:
            key = f'{label}_regime_{r_name}_p60'
            if key in bm:
                m = bm[key]
                status = 'OK' if m['mean_precision'] > 0.55 else 'WEAK'
                print(f'    {label} {r_name:<12}: {m["mean_precision"]:.3f} '
                      f'(min={m.get("min_precision", m["mean_precision"]):.3f}) [{status}]')

    elapsed = time.time() - t_start
    print(f'\nTotal time: {elapsed:.0f}s')
    print(f'{"="*70}')


if __name__ == '__main__':
    main()
