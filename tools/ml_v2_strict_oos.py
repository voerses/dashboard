"""
Strict Temporal OOS Test for V2 Model
======================================
Retrains model on first 50% of data (by date), tests on last 25%.
Gap of 25% between train and test prevents any leakage.
Compares precision to V2 walk-forward results to detect overfitting.
Memory-optimized: 15 tokens, float32, token-by-token processing.
"""
import numpy as np
import pandas as pd
import os, sys, gc, time, warnings
from pathlib import Path

warnings.filterwarnings('ignore')
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, '/workspace/crypto_backtest')

from sklearn.ensemble import HistGradientBoostingClassifier
from tools.ml_direction_model_v2_1 import (
    build_market_context, compute_features, undersample_balance,
)

DATA_DIR = Path('/workspace/crypto_backtest/data/perp/1h_cache')


def main():
    t0 = time.time()
    print('=' * 70)
    print('ML V2 STRICT TEMPORAL OUT-OF-SAMPLE TEST')
    print('=' * 70)

    market_ctx = build_market_context(DATA_DIR, top_n=15)

    token_files = sorted(DATA_DIR.glob('*_1h.parquet'),
                         key=lambda f: f.stat().st_size, reverse=True)
    tokens = [f.stem.replace('_1h', '') for f in token_files[:15]]
    print(f'\nTokens ({len(tokens)}): {tokens[:5]}...')

    horizon = 24

    # Pass 1: Scan timestamps to find cutoff dates
    print(f'\n--- Scanning timestamps ---')
    all_ts = []
    for token in tokens:
        df = pd.read_parquet(DATA_DIR / f'{token}_1h.parquet')
        if len(df) >= 500:
            all_ts.append(df.index[200:-horizon].values)
        del df; gc.collect()

    ts_sorted = np.sort(np.concatenate(all_ts))
    del all_ts; gc.collect()

    n_ts = len(ts_sorted)
    train_cutoff = pd.Timestamp(ts_sorted[int(n_ts * 0.50)])
    test_start = pd.Timestamp(ts_sorted[int(n_ts * 0.75)])
    gap_days = (test_start - train_cutoff).days
    print(f'  Train cutoff: {train_cutoff.date()}, Test start: {test_start.date()}, Gap: {gap_days} days')
    del ts_sorted; gc.collect()

    # Pass 2: Build train and test sets token by token
    print(f'\n--- Building datasets (token by token) ---')
    train_X_list, train_y_list = [], []
    test_X_list, test_y_list, test_regime_list = [], [], []
    feat_names = None

    for i, token in enumerate(tokens):
        df = pd.read_parquet(DATA_DIR / f'{token}_1h.parquet')
        if len(df) < 500:
            continue

        close = df['close'].values.astype(np.float64)
        high = df['high'].values.astype(np.float64)
        low = df['low'].values.astype(np.float64)
        volume = df['volume'].values.astype(np.float64)
        funding = df['funding_rate'].values.astype(np.float64) if 'funding_rate' in df.columns else None
        n = len(close)

        features = compute_features(close, high, low, volume, funding)

        # Labels (fixed 3% target like V2)
        labels = np.zeros(n, dtype=np.int8)
        fwd_ret = np.zeros(n)
        fwd_ret[:n - horizon] = close[horizon:] / close[:n - horizon] - 1.0
        labels[fwd_ret > 0.03] = 1
        labels[fwd_ret < -0.03] = -1
        labels[-horizon:] = 0

        # Market context (V2 has 7 market features = 41 total)
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

        # Exclude market_funding_mean (V2 has 41 features, not 42)
        fn = sorted([k for k in features.keys()
                     if not k.startswith('_') and k != 'market_funding_mean'])
        X = np.column_stack([features[k] for k in fn]).astype(np.float32)

        valid = np.ones(n, dtype=bool)
        valid[:200] = False; valid[-horizon:] = False
        nan_mask = np.any(np.isnan(X), axis=1) | np.any(np.isinf(X), axis=1)
        nz = (labels == 1) | (labels == -1)
        valid = valid & ~nan_mask & nz

        timestamps = df.index
        train_mask = valid & (timestamps <= train_cutoff)
        test_mask = valid & (timestamps >= test_start)

        if train_mask.sum() > 0:
            train_X_list.append(X[train_mask])
            train_y_list.append(labels[train_mask])

        if test_mask.sum() > 0:
            test_X_list.append(X[test_mask])
            test_y_list.append(labels[test_mask])
            regime_col = fn.index('market_regime') if 'market_regime' in fn else None
            if regime_col is not None:
                test_regime_list.append(X[test_mask, regime_col])

        if feat_names is None:
            feat_names = fn

        del df, close, high, low, volume, funding, features, X, labels
        gc.collect()

        if (i + 1) % 5 == 0:
            print(f'  Processed {i+1}/{len(tokens)} tokens')

    del market_ctx; gc.collect()

    X_train = np.vstack(train_X_list); y_train = np.concatenate(train_y_list)
    del train_X_list, train_y_list; gc.collect()
    X_test = np.vstack(test_X_list); y_test = np.concatenate(test_y_list)
    regime_test = np.concatenate(test_regime_list) if test_regime_list else None
    del test_X_list, test_y_list, test_regime_list; gc.collect()

    print(f'\n  Train: {len(y_train):,} (L={( y_train==1).sum():,}, S={(y_train==-1).sum():,})')
    print(f'  Test:  {len(y_test):,} (L={(y_test==1).sum():,}, S={(y_test==-1).sum():,})')

    # Train
    rng = np.random.RandomState(42)
    X_train_bal, y_train_bal = undersample_balance(X_train, y_train, rng)
    del X_train, y_train; gc.collect()

    print(f'\n  Training HistGBM (balanced: {len(y_train_bal):,})...')
    model = HistGradientBoostingClassifier(
        max_iter=400, max_depth=6, learning_rate=0.05,
        min_samples_leaf=50, max_leaf_nodes=31,
        l2_regularization=1.0, max_bins=128,
        early_stopping=True, validation_fraction=0.15,
        n_iter_no_change=20, random_state=42, verbose=0,
    )
    model.fit(X_train_bal, y_train_bal)
    del X_train_bal, y_train_bal; gc.collect()
    print(f'  Trained (iter={model.n_iter_})')

    # Evaluate
    y_proba = model.predict_proba(X_test)
    classes = list(model.classes_)
    oos_metrics = {}

    for direction, label in [(1, 'LONG'), (-1, 'SHORT')]:
        if direction not in classes:
            continue
        cls_idx = classes.index(direction)
        probs = y_proba[:, cls_idx]

        print(f'\n  {label} (Strict OOS):')
        print(f'  {"Thr":<8} {"N":>6} {"Correct":>8} {"Precision":>10}')
        print(f'  {"-"*34}')

        for thr in [0.55, 0.60, 0.65, 0.70, 0.75]:
            signals = probs >= thr
            n_sig = signals.sum()
            if n_sig == 0:
                print(f'  p>={thr:.2f}  {0:>6}')
                continue
            correct = (y_test[signals] == direction).sum()
            prec = correct / n_sig
            oos_metrics[f'{label}_p{int(thr*100)}'] = {'precision': float(prec), 'n': int(n_sig)}
            print(f'  p>={thr:.2f}  {n_sig:>6} {correct:>8} {prec:>10.4f}')

    # Per-regime at p>=0.60
    if regime_test is not None:
        print(f'\n  Per-Regime (Strict OOS, p>=0.60):')
        for r_val, r_name in [(0, 'CRISIS'), (1, 'QUIET'), (2, 'UPTREND'), (3, 'RANGE'), (4, 'DOWNTREND')]:
            r_mask = regime_test == r_val
            if r_mask.sum() == 0:
                continue
            for direction, label in [(1, 'LONG'), (-1, 'SHORT')]:
                if direction not in classes:
                    continue
                cls_idx = classes.index(direction)
                signals = (y_proba[:, cls_idx] >= 0.60) & r_mask
                n_sig = signals.sum()
                if n_sig == 0:
                    continue
                correct = (y_test[signals] == direction).sum()
                print(f'    {r_name:<12} {label:<6} n={n_sig:<5} prec={correct/n_sig:.4f}')

    # Comparison
    print(f'\n{"="*70}')
    print('OVERFITTING ASSESSMENT: Strict OOS vs V2 Walk-Forward')
    print(f'{"="*70}')

    v2_reported = {
        'LONG_p60': 0.8261, 'LONG_p65': 0.8724, 'LONG_p70': 0.9150, 'LONG_p75': 0.9511,
        'SHORT_p60': 0.7610, 'SHORT_p65': 0.8052, 'SHORT_p70': 0.8520, 'SHORT_p75': 0.8919,
    }

    print(f'\n{"Metric":<15} {"V2 WF":>8} {"Strict OOS":>12} {"Delta":>8} {"Verdict":>10}')
    print(f'{"-"*55}')

    any_overfit = False
    for key in ['LONG_p60', 'LONG_p65', 'LONG_p70', 'LONG_p75',
                'SHORT_p60', 'SHORT_p65', 'SHORT_p70', 'SHORT_p75']:
        v2 = v2_reported[key]
        oos = oos_metrics.get(key, {})
        oos_prec = oos.get('precision', 0)
        oos_n = oos.get('n', 0)

        if oos_n > 0:
            delta = oos_prec - v2
            verdict = 'OVERFIT' if delta < -0.05 else ('CAUTION' if delta < -0.02 else 'OK')
            if delta < -0.05:
                any_overfit = True
        else:
            delta = 0; verdict = 'N/A'

        print(f'{key:<15} {v2:>8.4f} {oos_prec:>8.4f} (n={oos_n:>4}) {delta:>+7.4f} {verdict:>10}')

    print(f'\n{"="*70}')
    if any_overfit:
        print('WARNING: >5pp degradation detected — possible overfitting')
    else:
        print('PASS: No significant overfitting detected')
    print(f'{"="*70}')
    print(f'\nTotal time: {time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
