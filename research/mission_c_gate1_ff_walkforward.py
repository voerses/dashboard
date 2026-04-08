"""Mission C Gate 1 — Forward-Forward walk-forward test on real BTC features.

PRIMARY MODEL: Forward-Forward (Hinton 2022) from /workspace/ff_trading_v2.py.
The user's earlier prototype showed FF-temporal beating backprop in volatile
regimes (53.2% vs 51.1%) on synthetic data. This script wires it to REAL
BTC features and runs proper walk-forward validation.

COMPARISON MODELS: LightGBM (R200-R207 pipeline), Logistic Regression baseline.
These exist for context only — Forward-Forward is the model under test.

Setup:
  - Single token: BTC 1h OHLCV (~6 years history)
  - Features: 10 hand-crafted (log returns, RSI, MACD, BB%B, vol-z,
              realized vol, FIF, drift, FIF×drift)
  - Target: binary direction of forward 24h log return
  - Sample step: 24 bars (daily cadence to match Mission D Gate 0)
  - Walk-forward: 180-day train / 30-day test / 30-day step

Verdict:
  - PASS if FF accuracy > 53% on aggregate OOS AND beats backprop
  - MARGINAL if FF > 51% but < 53%
  - KILL if FF ~50% (random)
"""
from __future__ import annotations

import sys
from pathlib import Path
import json

import numpy as np
import pandas as pd
import torch
import lightgbm as lgb
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from scipy.stats import pearsonr

REPO = Path("/workspace/crypto_backtest")
sys.path.insert(0, "/workspace")  # for ff_trading_v2
sys.path.insert(0, str(REPO / "research"))

from ff_trading_v2 import FFNetV2, BackpropBaselineV2  # noqa: E402
import mission_d_gate0 as mdg  # noqa: E402

OHLCV_PATH = REPO / "data/perp/binance/1h_ohlcv/BTC_perp_1h.csv"
OUT_REPORT = REPO / "research/mission_c_gate1_report.md"
OUT_RESULTS = REPO / "research/mission_c_gate1_results.json"

SAMPLE_STEP_BARS = 24  # daily cadence
TARGET_HORIZON_BARS = 24  # 24-hour forward return
FEATURE_BURN_IN = 200  # bars needed before all features are valid


def load_btc_close() -> pd.Series:
    df = pd.read_csv(OHLCV_PATH, parse_dates=["datetime"]).set_index("datetime").sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    return df["close"].astype(np.float64)


def load_btc_full() -> pd.DataFrame:
    df = pd.read_csv(OHLCV_PATH, parse_dates=["datetime"]).set_index("datetime").sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    return df.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})


def compute_rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    deltas = np.diff(close)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.zeros(len(close))
    avg_loss = np.zeros(len(close))
    avg_gain[period] = np.mean(gains[:period])
    avg_loss[period] = np.mean(losses[:period])
    for i in range(period + 1, len(close)):
        avg_gain[i] = (avg_gain[i-1] * (period - 1) + gains[i-1]) / period
        avg_loss[i] = (avg_loss[i-1] * (period - 1) + losses[i-1]) / period
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 0)
    rsi = 100 - 100 / (1 + rs)
    rsi[:period] = 50
    return rsi


def compute_macd(close: np.ndarray, fast: int = 12, slow: int = 26, sig: int = 9) -> np.ndarray:
    s = pd.Series(close)
    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal = macd.ewm(span=sig, adjust=False).mean()
    return (macd - signal).to_numpy()


def compute_bbands_pctb(close: np.ndarray, period: int = 20, mult: float = 2.0) -> np.ndarray:
    s = pd.Series(close)
    ma = s.rolling(period).mean()
    sd = s.rolling(period).std()
    upper = ma + mult * sd
    lower = ma - mult * sd
    pctb = (s - lower) / (upper - lower).replace(0, np.nan)
    return pctb.fillna(0.5).to_numpy()


def build_feature_panel(df: pd.DataFrame) -> pd.DataFrame:
    """Build per-bar features from OHLCV. Sample at daily cadence in caller."""
    close = df["close"].to_numpy()
    volume = df["volume"].to_numpy()
    log_close = np.log(close)

    # Returns
    log_ret_1h = np.diff(log_close, prepend=log_close[0])
    log_ret_24h = log_close - np.roll(log_close, 24)
    log_ret_24h[:24] = 0

    # RSI, MACD, BB%B
    rsi14 = compute_rsi(close, 14)
    macd_hist = compute_macd(close)
    bb_pctb = compute_bbands_pctb(close)

    # Volume z-score (rolling 168h = 1 week)
    vol_s = pd.Series(volume)
    vol_z = ((vol_s - vol_s.rolling(168).mean()) / vol_s.rolling(168).std()).fillna(0).to_numpy()

    # Realized vol (rolling 24h std of log returns)
    rv24 = pd.Series(log_ret_1h).rolling(24).std().fillna(0).to_numpy()

    panel = pd.DataFrame({
        "log_ret_1h": log_ret_1h,
        "log_ret_24h": log_ret_24h,
        "rsi14": rsi14,
        "macd_hist": macd_hist,
        "bb_pctb": bb_pctb,
        "vol_z": vol_z,
        "rv24": rv24,
    }, index=df.index)
    return panel


def add_fif_features(panel: pd.DataFrame, close: pd.Series) -> pd.DataFrame:
    """Add FIF, drift, FIF×drift features at daily cadence (forward-filled in between)."""
    rets = mdg.log_returns(close).dropna()
    n = len(rets)
    fif_rows = []
    for end in range(mdg.FIF_WINDOW, n, SAMPLE_STEP_BARS):
        slice_r = rets.iloc[end - mdg.FIF_WINDOW: end].to_numpy()
        try:
            i_f, drift, _ = mdg.compute_fif(slice_r)
        except Exception:
            continue
        fif_rows.append({
            "ts": rets.index[end - 1],
            "fif": i_f,
            "drift": drift,
            "fif_x_drift": i_f * drift,
        })
    fif_df = pd.DataFrame(fif_rows).set_index("ts").sort_index()
    # Forward-fill onto the per-bar panel
    fif_aligned = fif_df.reindex(panel.index, method="ffill")
    panel = panel.join(fif_aligned)
    return panel


def build_samples(panel: pd.DataFrame, sample_step: int = SAMPLE_STEP_BARS, target_horizon: int = TARGET_HORIZON_BARS) -> tuple[np.ndarray, np.ndarray, pd.DatetimeIndex]:
    """Sample panel at daily cadence and build (X, y, timestamps).
    y is binary: 1 if forward 24h log return > 0, else 0.
    """
    # Drop early bars where any feature is NaN
    panel_clean = panel.dropna()
    # Sample
    sample_idx = panel_clean.index[FEATURE_BURN_IN::sample_step]
    sub = panel_clean.loc[sample_idx]
    feature_cols = [c for c in panel_clean.columns]
    X = sub[feature_cols].to_numpy(dtype=np.float32)

    # Target = forward 24h return at each sample timestamp
    close_aligned = pd.read_csv(OHLCV_PATH, parse_dates=["datetime"]).set_index("datetime")["close"]
    if close_aligned.index.tz is not None:
        close_aligned.index = close_aligned.index.tz_convert(None)
    y_list = []
    forward_ret_list = []
    for ts in sample_idx:
        try:
            ix = close_aligned.index.get_indexer([ts], method="pad")[0]
            if ix < 0 or ix + target_horizon >= len(close_aligned):
                y_list.append(np.nan)
                forward_ret_list.append(np.nan)
                continue
            r = np.log(close_aligned.iloc[ix + target_horizon] / close_aligned.iloc[ix])
            y_list.append(1.0 if r > 0 else 0.0)
            forward_ret_list.append(r)
        except Exception:
            y_list.append(np.nan)
            forward_ret_list.append(np.nan)
    y = np.array(y_list, dtype=np.float32)
    forward_ret = np.array(forward_ret_list, dtype=np.float32)
    valid = ~np.isnan(y)
    return X[valid], y[valid], sample_idx[valid], forward_ret[valid]


# ---------------------------------------------------------------------------
# Model training — Forward-Forward is PRIMARY, others are comparison only
# ---------------------------------------------------------------------------
def standardize(X_train: np.ndarray, X_test: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mu = X_train.mean(axis=0)
    sd = X_train.std(axis=0) + 1e-8
    return (X_train - mu) / sd, (X_test - mu) / sd


def train_and_eval_ff(X_train, y_train, X_test, y_test, n_features_with_label, n_epochs=20):
    """PRIMARY: Forward-Forward (Hinton 2022) per ff_trading_v2.FFNetV2.

    For each test sample, builds two candidates (label=0 and label=1) and
    picks the one with higher goodness. The "confidence" is goodness_pos − goodness_neg.
    """
    Xtr_std, Xte_std = standardize(X_train, X_test)
    Xtr = torch.tensor(Xtr_std, dtype=torch.float32)
    ytr = torch.tensor(y_train, dtype=torch.float32)
    Xte = torch.tensor(Xte_std, dtype=torch.float32)
    yte = torch.tensor(y_test, dtype=torch.float32)

    ff = FFNetV2(
        input_dim=n_features_with_label,
        layer_sizes=[n_features_with_label, 64, 32, 16],
        threshold=2.0,
        lr=0.01,
    )
    # Positives: real (features, label) pairs
    pos = torch.cat([Xtr, ytr.unsqueeze(1)], dim=1)
    # Negatives: shuffled labels — incorrect (features, wrong-label) pairs
    idx = torch.randperm(len(ytr))
    neg = torch.cat([Xtr, ytr[idx].unsqueeze(1)], dim=1)
    for _ in range(n_epochs):
        ff.train_epoch(pos, neg, batch_size=min(256, len(pos)))

    n_test = len(X_test)
    goodness_pos = ff.predict(torch.cat([Xte, torch.ones(n_test, 1)], dim=1))
    goodness_neg = ff.predict(torch.cat([Xte, torch.zeros(n_test, 1)], dim=1))
    pred = (goodness_pos > goodness_neg).float()
    acc = (pred == yte).float().mean().item()
    confidence = (goodness_pos - goodness_neg).numpy()
    return float(acc), pred.numpy(), confidence


def train_and_eval_backprop(X_train, y_train, X_test, y_test, n_features, n_epochs=20):
    """COMPARISON: BackpropBaselineV2 from ff_trading_v2 (the FF prototype's own baseline)."""
    Xtr_std, Xte_std = standardize(X_train, X_test)
    Xtr = torch.tensor(Xtr_std, dtype=torch.float32)
    ytr = torch.tensor(y_train, dtype=torch.float32)
    Xte = torch.tensor(Xte_std, dtype=torch.float32)
    yte = torch.tensor(y_test, dtype=torch.float32)

    model = BackpropBaselineV2(input_dim=n_features)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.01)
    criterion = torch.nn.BCEWithLogitsLoss()
    for _ in range(n_epochs):
        optimizer.zero_grad()
        logits = model(Xtr).squeeze()
        loss = criterion(logits, ytr)
        loss.backward()
        optimizer.step()
    with torch.no_grad():
        test_logits = model(Xte).squeeze()
        test_probs = torch.sigmoid(test_logits)
        pred = (test_probs > 0.5).float()
        acc = (pred == yte).float().mean().item()
    return float(acc), pred.numpy(), test_probs.numpy()


def train_and_eval_lgbm(X_train, y_train, X_test, y_test):
    """LightGBM gradient-boosted trees with conservative hyperparameters for small data."""
    train_data = lgb.Dataset(X_train, label=y_train)
    params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "num_leaves": 15,
        "learning_rate": 0.05,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "min_data_in_leaf": 20,
        "verbose": -1,
    }
    model = lgb.train(params, train_data, num_boost_round=100)
    probs = model.predict(X_test)
    pred = (probs > 0.5).astype(np.float32)
    acc = float((pred == y_test).mean())
    feature_importance = model.feature_importance(importance_type="gain")
    return acc, pred, probs, feature_importance


def train_and_eval_logreg(X_train, y_train, X_test, y_test):
    """Logistic regression baseline. If LGBM doesn't beat this, no signal."""
    scaler = StandardScaler()
    Xtr_s = scaler.fit_transform(X_train)
    Xte_s = scaler.transform(X_test)
    model = LogisticRegression(max_iter=1000, C=1.0)
    model.fit(Xtr_s, y_train)
    probs = model.predict_proba(Xte_s)[:, 1]
    pred = (probs > 0.5).astype(np.float32)
    acc = float((pred == y_test).mean())
    return acc, pred, probs


# ---------------------------------------------------------------------------
# Walk-forward
# ---------------------------------------------------------------------------
def walk_forward(X: np.ndarray, y: np.ndarray, ts: pd.DatetimeIndex, fwd_ret: np.ndarray,
                 feature_names: list[str],
                 train_days: int = 180, test_days: int = 30, step_days: int = 30) -> dict:
    """Walk forward: 180-day train, 30-day test, 30-day step."""
    n_features = X.shape[1]
    fold_results = []
    feature_importance_agg = np.zeros(n_features)

    days = (ts - ts[0]).days.to_numpy()
    max_day = days.max()

    fold_idx = 0
    train_start = 0
    while True:
        train_end = train_start + train_days
        test_end = train_end + test_days
        if test_end > max_day:
            break
        train_mask = (days >= train_start) & (days < train_end)
        test_mask = (days >= train_end) & (days < test_end)
        if train_mask.sum() < 50 or test_mask.sum() < 5:
            train_start += step_days
            continue

        Xtr = X[train_mask]
        Xte = X[test_mask]
        ytr = y[train_mask]
        yte = y[test_mask]
        fwd_te = fwd_ret[test_mask]

        # PRIMARY: Forward-Forward
        ff_acc, ff_pred, ff_conf = train_and_eval_ff(Xtr, ytr, Xte, yte, n_features_with_label=n_features + 1)
        # COMPARISON: backprop (the FF prototype's own baseline)
        bp_acc, bp_pred, bp_prob = train_and_eval_backprop(Xtr, ytr, Xte, yte, n_features=n_features)
        # COMPARISON: LightGBM (the R200-R207 model class)
        lgbm_acc, lgbm_pred, lgbm_prob, lgbm_imp = train_and_eval_lgbm(Xtr, ytr, Xte, yte)
        feature_importance_agg += lgbm_imp
        # COMPARISON: LogReg baseline (sanity floor)
        lr_acc, lr_pred, lr_prob = train_and_eval_logreg(Xtr, ytr, Xte, yte)

        # IC vs forward returns (signed model output, not just predicted class)
        ff_ic = float(pearsonr(ff_conf, fwd_te)[0]) if np.std(ff_conf) > 0 else 0.0
        bp_ic = float(pearsonr(bp_prob, fwd_te)[0]) if np.std(bp_prob) > 0 else 0.0
        lgbm_ic = float(pearsonr(lgbm_prob, fwd_te)[0]) if np.std(lgbm_prob) > 0 else 0.0
        lr_ic = float(pearsonr(lr_prob, fwd_te)[0]) if np.std(lr_prob) > 0 else 0.0

        fold_results.append({
            "fold": fold_idx,
            "train_start_day": int(train_start),
            "test_end_day": int(test_end),
            "n_train": int(train_mask.sum()),
            "n_test": int(test_mask.sum()),
            "class_balance_train": float(ytr.mean()),
            "class_balance_test": float(yte.mean()),
            "ff_acc": float(ff_acc),
            "bp_acc": float(bp_acc),
            "lgbm_acc": float(lgbm_acc),
            "lr_acc": float(lr_acc),
            "ff_ic": ff_ic,
            "bp_ic": bp_ic,
            "lgbm_ic": lgbm_ic,
            "lr_ic": lr_ic,
        })
        fold_idx += 1
        train_start += step_days
        if fold_idx % 5 == 0:
            print(f"  fold {fold_idx} done")

    if not fold_results:
        return {"error": "no folds"}

    ff_accs = np.array([f["ff_acc"] for f in fold_results])
    bp_accs = np.array([f["bp_acc"] for f in fold_results])
    lgbm_accs = np.array([f["lgbm_acc"] for f in fold_results])
    lr_accs = np.array([f["lr_acc"] for f in fold_results])
    ff_ics = np.array([f["ff_ic"] for f in fold_results])
    bp_ics = np.array([f["bp_ic"] for f in fold_results])
    lgbm_ics = np.array([f["lgbm_ic"] for f in fold_results])
    lr_ics = np.array([f["lr_ic"] for f in fold_results])

    feature_importance_agg /= len(fold_results)
    importance_dict = sorted(
        zip(feature_names, feature_importance_agg.tolist()),
        key=lambda x: x[1], reverse=True,
    )

    def stats(name, arr):
        return {
            f"{name}_acc_mean": float(arr.mean()),
            f"{name}_acc_std": float(arr.std()),
            f"{name}_acc_pct_above_50": float((arr > 0.50).mean() * 100),
            f"{name}_acc_pct_above_53": float((arr > 0.53).mean() * 100),
        }

    out = {"n_folds": len(fold_results)}
    out.update(stats("ff", ff_accs))
    out.update(stats("bp", bp_accs))
    out.update(stats("lgbm", lgbm_accs))
    out.update(stats("lr", lr_accs))
    out["ff_ic_mean"] = float(ff_ics.mean())
    out["bp_ic_mean"] = float(bp_ics.mean())
    out["lgbm_ic_mean"] = float(lgbm_ics.mean())
    out["lr_ic_mean"] = float(lr_ics.mean())
    out["ff_ic_pct_positive"] = float((ff_ics > 0).mean() * 100)
    out["ff_minus_bp_acc"] = float(ff_accs.mean() - bp_accs.mean())
    out["ff_minus_lgbm_acc"] = float(ff_accs.mean() - lgbm_accs.mean())
    out["ff_minus_lr_acc"] = float(ff_accs.mean() - lr_accs.mean())
    out["feature_importance"] = importance_dict
    out["fold_results"] = fold_results
    return out


def main():
    print("Loading BTC 1h OHLCV…")
    df = load_btc_full()
    close = df["close"]
    print(f"  {len(df)} bars, {df.index[0]} → {df.index[-1]}")

    print("Building feature panel…")
    panel = build_feature_panel(df)
    panel = add_fif_features(panel, close)
    print(f"  features: {list(panel.columns)}")
    print(f"  panel shape: {panel.shape}")

    print("Building daily samples + 24h forward target…")
    X, y, ts, fwd = build_samples(panel)
    print(f"  samples: {len(X)} (n_features={X.shape[1]})")
    print(f"  class balance (target=1): {y.mean():.3f}")
    print(f"  date range: {ts[0]} → {ts[-1]}")

    feature_names = list(panel.columns)
    print("\nRunning walk-forward (180d train / 30d test / 30d step)…")
    result = walk_forward(X, y, ts, fwd, feature_names, train_days=180, test_days=30, step_days=30)
    if "error" in result:
        print(f"ERROR: {result['error']}")
        return

    print(f"\nFolds: {result['n_folds']}")
    print(f"\n=== Forward-Forward (PRIMARY, Hinton 2022) ===")
    print(f"  Mean accuracy:    {result['ff_acc_mean']*100:.2f}% (stdev {result['ff_acc_std']*100:.2f}%)")
    print(f"  % folds > 50%:    {result['ff_acc_pct_above_50']:.0f}%")
    print(f"  % folds > 53%:    {result['ff_acc_pct_above_53']:.0f}%")
    print(f"  Mean IC vs ret:   {result['ff_ic_mean']:+.4f}")
    print(f"  % folds IC > 0:   {result['ff_ic_pct_positive']:.0f}%")

    print(f"\n=== Backprop (comparison — ff_trading_v2 baseline) ===")
    print(f"  Mean accuracy:    {result['bp_acc_mean']*100:.2f}% (stdev {result['bp_acc_std']*100:.2f}%)")
    print(f"  % folds > 50%:    {result['bp_acc_pct_above_50']:.0f}%")
    print(f"  % folds > 53%:    {result['bp_acc_pct_above_53']:.0f}%")
    print(f"  Mean IC vs ret:   {result['bp_ic_mean']:+.4f}")

    print(f"\n=== LightGBM (comparison — R200-R207 pipeline) ===")
    print(f"  Mean accuracy:    {result['lgbm_acc_mean']*100:.2f}% (stdev {result['lgbm_acc_std']*100:.2f}%)")
    print(f"  % folds > 50%:    {result['lgbm_acc_pct_above_50']:.0f}%")
    print(f"  % folds > 53%:    {result['lgbm_acc_pct_above_53']:.0f}%")
    print(f"  Mean IC vs ret:   {result['lgbm_ic_mean']:+.4f}")

    print(f"\n=== LogReg (comparison — sanity floor) ===")
    print(f"  Mean accuracy:    {result['lr_acc_mean']*100:.2f}% (stdev {result['lr_acc_std']*100:.2f}%)")
    print(f"  % folds > 50%:    {result['lr_acc_pct_above_50']:.0f}%")
    print(f"  Mean IC vs ret:   {result['lr_ic_mean']:+.4f}")

    print(f"\n=== Head-to-head (FF vs comparisons) ===")
    print(f"  FF − Backprop:  {result['ff_minus_bp_acc']*100:+.2f}%")
    print(f"  FF − LightGBM:  {result['ff_minus_lgbm_acc']*100:+.2f}%")
    print(f"  FF − LogReg:    {result['ff_minus_lr_acc']*100:+.2f}%")

    print(f"\n=== Feature importance (LightGBM, avg gain across folds) ===")
    for i, (feat, imp) in enumerate(result["feature_importance"][:15]):
        print(f"  {i+1:>2}. {feat:<15} {imp:>10.1f}")

    # Verdict — based on PRIMARY model (Forward-Forward)
    verdict = "KILL"
    if result["ff_acc_mean"] > 0.53 and result["ff_minus_bp_acc"] > 0:
        verdict = "PASS"
    elif result["ff_acc_mean"] > 0.51:
        verdict = "MARGINAL"

    print(f"\nVerdict (primary = Forward-Forward): {verdict}")
    if verdict == "PASS":
        print("  → FF > 53% on aggregate AND beats backprop baseline. Promote to Gate 2.")
    elif verdict == "MARGINAL":
        print("  → FF > 51% but < 53%. Worth more features or longer history before commit.")
    else:
        print("  → FF accuracy too close to random. Either FF doesn't transfer to real data,")
        print("     bad features, or 24h direction is too noisy for this architecture.")

    OUT_RESULTS.write_text(json.dumps(result, indent=2, default=str))
    print(f"\nSaved → {OUT_RESULTS}")


if __name__ == "__main__":
    main()
