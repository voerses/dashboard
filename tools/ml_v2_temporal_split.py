"""
V2 Model: Proper Temporal Split (67/33 by date, no gap)
========================================================
This tests whether the model has ANY predictive power when temporal leakage
is eliminated. Uses the same 67/33 split ratio as V2 walk-forward but splits
by TIMESTAMP instead of row index.
"""
import numpy as np
import pandas as pd
import sys, gc, time, warnings
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
    print('V2 MODEL: PROPER TEMPORAL SPLIT (67/33 by date)')
    print('=' * 70)

    market_ctx = build_market_context(DATA_DIR, top_n=15)

    token_files = sorted(DATA_DIR.glob('*_1h.parquet'),
                         key=lambda f: f.stat().st_size, reverse=True)
    tokens = [f.stem.replace('_1h', '') for f in token_files[:15]]
    horizon = 24

    # Scan timestamps
    all_ts = []
    for token in tokens:
        df = pd.read_parquet(DATA_DIR / f'{token}_1h.parquet')
        if len(df) >= 500:
            all_ts.append(df.index[200:-horizon].values)
        del df; gc.collect()

    ts_sorted = np.sort(np.concatenate(all_ts))
    del all_ts; gc.collect()

    n_ts = len(ts_sorted)

    # Three splits to test:
    # A: 67/33 (no gap) — comparable to V2 walk-forward
    # B: 50/50 (no gap) — more challenging
    # C: First 8 months / last 4 months of actual data range
    splits = {
        '67/33 temporal': (int(n_ts * 0.67), int(n_ts * 0.67)),
        '50/50 temporal': (int(n_ts * 0.50), int(n_ts * 0.50)),
    }

    # Also compute date-based split for last 12 months
    latest = pd.Timestamp(ts_sorted[-1])
    earliest = pd.Timestamp(ts_sorted[0])
    data_range = (latest - earliest).days
    month8_cutoff = earliest + pd.Timedelta(days=int(data_range * 8/12))

    results = {}

    for split_name, (train_idx, test_idx) in splits.items():
        train_cutoff = pd.Timestamp(ts_sorted[train_idx])
        test_begin = pd.Timestamp(ts_sorted[test_idx])
        del_days = (test_begin - train_cutoff).days

        print(f'\n{"#"*70}')
        print(f'SPLIT: {split_name}')
        print(f'  Train: up to {train_cutoff.date()}, Test: from {test_begin.date()} (gap={del_days}d)')
        print(f'{"#"*70}')

        train_X_list, train_y_list = [], []
        test_X_list, test_y_list = [], []
        feat_names = None

        for token in tokens:
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

            labels = np.zeros(n, dtype=np.int8)
            fwd_ret = np.zeros(n)
            fwd_ret[:n - horizon] = close[horizon:] / close[:n - horizon] - 1.0
            labels[fwd_ret > 0.03] = 1
            labels[fwd_ret < -0.03] = -1
            labels[-horizon:] = 0

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

            fn = sorted([k for k in features.keys()
                         if not k.startswith('_') and k != 'market_funding_mean'])
            X = np.column_stack([features[k] for k in fn]).astype(np.float32)

            valid = np.ones(n, dtype=bool)
            valid[:200] = False; valid[-horizon:] = False
            nan_mask = np.any(np.isnan(X), axis=1) | np.any(np.isinf(X), axis=1)
            nz = (labels == 1) | (labels == -1)
            valid = valid & ~nan_mask & nz

            train_mask = valid & (df.index <= train_cutoff)
            test_mask = valid & (df.index > test_begin)

            if train_mask.sum() > 0:
                train_X_list.append(X[train_mask])
                train_y_list.append(labels[train_mask])
            if test_mask.sum() > 0:
                test_X_list.append(X[test_mask])
                test_y_list.append(labels[test_mask])
            if feat_names is None:
                feat_names = fn

            del df, close, high, low, volume, funding, features, X, labels
            gc.collect()

        X_train = np.vstack(train_X_list); y_train = np.concatenate(train_y_list)
        del train_X_list, train_y_list; gc.collect()
        X_test = np.vstack(test_X_list); y_test = np.concatenate(test_y_list)
        del test_X_list, test_y_list; gc.collect()

        print(f'  Train: {len(y_train):,}  Test: {len(y_test):,}')

        rng = np.random.RandomState(42)
        X_bal, y_bal = undersample_balance(X_train, y_train, rng)
        del X_train, y_train; gc.collect()

        model = HistGradientBoostingClassifier(
            max_iter=400, max_depth=6, learning_rate=0.05,
            min_samples_leaf=50, max_leaf_nodes=31,
            l2_regularization=1.0, max_bins=128,
            early_stopping=True, validation_fraction=0.15,
            n_iter_no_change=20, random_state=42, verbose=0,
        )
        model.fit(X_bal, y_bal)
        del X_bal, y_bal; gc.collect()

        y_proba = model.predict_proba(X_test)
        classes = list(model.classes_)
        split_metrics = {}

        for direction, label in [(1, 'LONG'), (-1, 'SHORT')]:
            if direction not in classes:
                continue
            cls_idx = classes.index(direction)
            probs = y_proba[:, cls_idx]

            print(f'\n  {label}:')
            for thr in [0.55, 0.60, 0.65, 0.70, 0.75]:
                signals = probs >= thr
                n_sig = signals.sum()
                if n_sig == 0:
                    continue
                correct = (y_test[signals] == direction).sum()
                prec = correct / n_sig
                print(f'    p>={thr:.2f}  n={n_sig:>5}  prec={prec:.4f}')
                split_metrics[f'{label}_p{int(thr*100)}'] = prec

        # Feature importances
        n_features = model.n_features_in_
        imp = np.zeros(n_features)
        for tree_list in model._predictors:
            for tree in tree_list:
                for node in tree.nodes:
                    if not node['is_leaf']:
                        fi = node['feature_idx']
                        if 0 <= fi < n_features:
                            imp[fi] += 1
        if imp.sum() > 0:
            imp = imp / imp.sum()
        top_idx = np.argsort(imp)[::-1][:10]
        print(f'\n  Top 10 features:')
        for rank, idx in enumerate(top_idx):
            print(f'    {rank+1:2d}. {feat_names[idx]:<25s} {imp[idx]:.4f}')

        results[split_name] = split_metrics
        del X_test, y_test, y_proba, model; gc.collect()

    del ts_sorted; gc.collect()

    # Summary
    print(f'\n{"="*70}')
    print('SUMMARY: Precision by Split Method')
    print(f'{"="*70}')

    v2_wf = {'LONG_p60': 0.8261, 'LONG_p70': 0.9150, 'SHORT_p60': 0.7610, 'SHORT_p70': 0.8520}
    print(f'{"Split":<25} {"L p60":>8} {"L p70":>8} {"S p60":>8} {"S p70":>8}')
    print(f'{"-"*57}')
    print(f'{"V2 WF (row-based)":<25} {v2_wf["LONG_p60"]:>8.4f} {v2_wf["LONG_p70"]:>8.4f} {v2_wf["SHORT_p60"]:>8.4f} {v2_wf["SHORT_p70"]:>8.4f}')

    for name, m in results.items():
        lp60 = m.get('LONG_p60', 0)
        lp70 = m.get('LONG_p70', 0)
        sp60 = m.get('SHORT_p60', 0)
        sp70 = m.get('SHORT_p70', 0)
        print(f'{name:<25} {lp60:>8.4f} {lp70:>8.4f} {sp60:>8.4f} {sp70:>8.4f}')

    print(f'\nConclusion:')
    print(f'  If temporal splits show ~80%+ precision: walk-forward was valid')
    print(f'  If temporal splits show ~50-55%: walk-forward had temporal leakage')
    print(f'  If temporal splits show 55-70%: model has some edge but WF was inflated')
    print(f'\nTotal time: {time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
