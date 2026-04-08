"""Mission C Gate 1 rerun — Forward-Forward with multi-class oracle labels.

FIXES from first attempt (mission_c_gate1_ff_walkforward.py KILL):
  Bug 1: negatives by label-shuffle collided ~50% of the time. Fixed by
         shuffling the ONE-HOT label to a different class (7 classes now).
  Bug 2: single 0/1 label in a LayerNormed 11-dim input was invisible.
         Fixed by using 7-class one-hot → label occupies 7/22 of input mass.
  Bug 3: 3200 params on 180 samples/fold. Fixed by smaller arch [32, 16]
         and 12-month training window (~2200 4h samples).

Setup:
  - BTC 1h OHLCV (2020–2026), sample every 4h
  - Train split: L24→L13mo (2024-04-05 → 2025-04-05)
  - Holdout:     L12→L1mo  (2025-04-05 → 2026-03-05)
  - Features:    15 (10 base + 5 setup-sensitive)
  - Labels:      7-class oracle via triple-barrier (see label_bar)
  - Models:      FF (primary), backprop, LightGBM, majority-class floor

Verdict:
  PASS     if FF holdout accuracy > majority baseline + 5pp AND beats backprop
  MARGINAL if FF > majority + 2pp
  KILL     otherwise
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import lightgbm as lgb

REPO = Path("/workspace/crypto_backtest")
sys.path.insert(0, "/workspace")
sys.path.insert(0, str(REPO / "research"))

from ff_trading_v2 import FFNetV2, BackpropBaselineV2  # noqa: E402
import mission_d_gate0 as mdg  # noqa: E402

OHLCV_PATH = REPO / "data/perp/binance/1h_ohlcv/BTC_perp_1h.csv"
OUT_RESULTS = REPO / "research/mission_c_gate1_ff_multiclass_results.json"

SAMPLE_STEP_BARS = 4  # every 4h
COST_ROUND_TRIP = 0.0013  # 13 bps

# Trade classes — (name, direction, tp_pct, sl_pct, horizon_bars)
# Class 0 = no-trade (assigned when no directional trade clears costs)
CLASSES = [
    ("no_trade",       0,  0.000, 0.000,   0),
    ("scalp_long",    +1, 0.005, 0.0025,   6),
    ("scalp_short",   -1, 0.005, 0.0025,   6),
    ("intraday_long", +1, 0.020, 0.010,   48),
    ("intraday_short",-1, 0.020, 0.010,   48),
    ("weekly_long",   +1, 0.050, 0.020,  336),
    ("weekly_short",  -1, 0.050, 0.020,  336),
]
N_CLASSES = len(CLASSES)

BACKTEST_ANCHOR = pd.Timestamp("2026-04-05")
TRAIN_START = BACKTEST_ANCHOR - pd.DateOffset(months=24)
TRAIN_END = BACKTEST_ANCHOR - pd.DateOffset(months=13)
TEST_START = BACKTEST_ANCHOR - pd.DateOffset(months=12)
TEST_END = BACKTEST_ANCHOR - pd.DateOffset(months=1)


# ---------------------------------------------------------------------------
# Data + features
# ---------------------------------------------------------------------------
def load_btc() -> pd.DataFrame:
    df = pd.read_csv(OHLCV_PATH, parse_dates=["datetime"]).set_index("datetime").sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    return df.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})


def compute_rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    s = pd.Series(close)
    delta = s.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return rsi.fillna(50).to_numpy()


def compute_macd_hist(close: np.ndarray) -> np.ndarray:
    s = pd.Series(close)
    ema_f = s.ewm(span=12, adjust=False).mean()
    ema_s = s.ewm(span=26, adjust=False).mean()
    macd = ema_f - ema_s
    sig = macd.ewm(span=9, adjust=False).mean()
    return (macd - sig).to_numpy()


def compute_bb_pctb(close: np.ndarray, period: int = 20) -> np.ndarray:
    s = pd.Series(close)
    ma = s.rolling(period).mean()
    sd = s.rolling(period).std()
    upper = ma + 2 * sd
    lower = ma - 2 * sd
    pctb = (s - lower) / (upper - lower).replace(0, np.nan)
    return pctb.fillna(0.5).to_numpy()


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    close = df["close"].to_numpy()
    vol = df["volume"].to_numpy()
    log_close = np.log(close)

    # 1. log_ret_1h
    log_ret_1h = np.diff(log_close, prepend=log_close[0])
    # 2. log_ret_24h
    log_ret_24h = log_close - np.roll(log_close, 24)
    log_ret_24h[:24] = 0
    # 3. RSI
    rsi14 = compute_rsi(close)
    # 4. MACD histogram
    macd_h = compute_macd_hist(close)
    # 5. BB %B
    bb_pctb = compute_bb_pctb(close)
    # 6. volume z-score 168h
    vs = pd.Series(vol)
    vol_z = ((vs - vs.rolling(168).mean()) / vs.rolling(168).std()).fillna(0).to_numpy()
    # 7. realized vol 24h
    rv24 = pd.Series(log_ret_1h).rolling(24).std().fillna(0).to_numpy()
    # 8-10. FIF, drift, FIF*drift at daily cadence forward-filled
    rets = pd.Series(log_ret_1h, index=df.index).dropna()
    fif_rows = []
    for end in range(mdg.FIF_WINDOW, len(rets), 24):
        try:
            i_f, drift, _ = mdg.compute_fif(rets.iloc[end - mdg.FIF_WINDOW: end].to_numpy())
        except Exception:
            continue
        fif_rows.append({"ts": rets.index[end - 1], "fif": i_f, "drift": drift, "fif_x_drift": i_f * drift})
    fif_df = pd.DataFrame(fif_rows).set_index("ts").sort_index()

    # New setup-sensitive features ----
    # 11. distance to 168h high (%)
    high168 = pd.Series(df["high"].to_numpy()).rolling(168).max().to_numpy()
    dist_hi = (close - high168) / close
    # 12. distance to 168h low (%)
    low168 = pd.Series(df["low"].to_numpy()).rolling(168).min().to_numpy()
    dist_lo = (close - low168) / close
    # 13. rolling sharpe 24h
    r24_mean = pd.Series(log_ret_1h).rolling(24).mean()
    r24_std = pd.Series(log_ret_1h).rolling(24).std()
    sharpe_24 = (r24_mean / r24_std).fillna(0).to_numpy()
    # 14. rolling sharpe 168h
    r168_mean = pd.Series(log_ret_1h).rolling(168).mean()
    r168_std = pd.Series(log_ret_1h).rolling(168).std()
    sharpe_168 = (r168_mean / r168_std).fillna(0).to_numpy()
    # 15. vol ratio 24h / 168h
    vol_ratio = (r24_std / r168_std).fillna(1).to_numpy()
    vol_ratio = np.nan_to_num(vol_ratio, nan=1.0, posinf=1.0, neginf=1.0)

    panel = pd.DataFrame({
        "log_ret_1h": log_ret_1h,
        "log_ret_24h": log_ret_24h,
        "rsi14": rsi14,
        "macd_hist": macd_h,
        "bb_pctb": bb_pctb,
        "vol_z": vol_z,
        "rv24": rv24,
        "dist_hi168": np.nan_to_num(dist_hi, nan=0.0),
        "dist_lo168": np.nan_to_num(dist_lo, nan=0.0),
        "sharpe_24": sharpe_24,
        "sharpe_168": sharpe_168,
        "vol_ratio": vol_ratio,
    }, index=df.index)
    # Join FIF (forward-fill)
    panel = panel.join(fif_df.reindex(panel.index, method="ffill"))
    return panel


# ---------------------------------------------------------------------------
# Oracle labeler (triple-barrier)
# ---------------------------------------------------------------------------
def label_bar(high: np.ndarray, low: np.ndarray, close: np.ndarray, i: int) -> int:
    """Assign a trade class to bar i based on best net-edge triple-barrier win.

    For each non-zero class, simulate a trade entered at close[i]:
      - LONG: TP hit = price reaches (1+tp)*entry, SL hit = price reaches (1-sl)*entry
      - SHORT: mirror
    The class wins if TP is hit BEFORE SL within horizon bars.
    Among winning classes, pick the one with the largest NET edge (TP - cost).
    If no class wins, return 0 (no-trade).
    """
    entry = close[i]
    best_class = 0
    best_net = 0.0
    for cls_idx, (_, direction, tp, sl, horizon) in enumerate(CLASSES):
        if cls_idx == 0:
            continue
        end_i = min(i + horizon, len(close) - 1)
        if end_i <= i:
            continue
        window_hi = high[i+1:end_i+1]
        window_lo = low[i+1:end_i+1]
        if direction == +1:
            tp_price = entry * (1 + tp)
            sl_price = entry * (1 - sl)
            hit_tp = np.where(window_hi >= tp_price)[0]
            hit_sl = np.where(window_lo <= sl_price)[0]
        else:
            tp_price = entry * (1 - tp)
            sl_price = entry * (1 + sl)
            hit_tp = np.where(window_lo <= tp_price)[0]
            hit_sl = np.where(window_hi >= sl_price)[0]
        tp_first = hit_tp[0] if len(hit_tp) else np.inf
        sl_first = hit_sl[0] if len(hit_sl) else np.inf
        if tp_first < sl_first and np.isfinite(tp_first):
            net = tp - COST_ROUND_TRIP
            if net > best_net:
                best_net = net
                best_class = cls_idx
    return best_class


def build_samples(df: pd.DataFrame, panel: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp):
    """Sample panel rows in [start, end) at SAMPLE_STEP_BARS cadence, drop NaN,
    label each with the oracle, return (X, y, timestamps)."""
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    panel_clean = panel.dropna()
    mask = (panel_clean.index >= start) & (panel_clean.index < end)
    sub = panel_clean[mask]
    # Subsample every SAMPLE_STEP_BARS rows
    sub = sub.iloc[::SAMPLE_STEP_BARS]
    # Need ix in full df
    bar_ix = df.index.get_indexer(sub.index)
    # Keep only bars with enough forward horizon for the longest class (weekly = 336)
    keep = bar_ix + 336 < len(df) - 1
    sub = sub[keep]
    bar_ix = bar_ix[keep]

    X = sub.to_numpy(dtype=np.float32)
    y = np.array([label_bar(high, low, close, i) for i in bar_ix], dtype=np.int64)
    return X, y, sub.index


# ---------------------------------------------------------------------------
# FF multi-class wrapper (properly implemented this time)
# ---------------------------------------------------------------------------
def one_hot(y: np.ndarray, n_classes: int) -> np.ndarray:
    out = np.zeros((len(y), n_classes), dtype=np.float32)
    out[np.arange(len(y)), y] = 1.0
    return out


def standardize(X_train: np.ndarray, X_test: np.ndarray):
    mu = X_train.mean(axis=0)
    sd = X_train.std(axis=0) + 1e-8
    return (X_train - mu) / sd, (X_test - mu) / sd, mu, sd


def train_and_eval_ff(X_train, y_train, X_test, y_test, n_features, n_classes, n_epochs=40):
    """FF with multi-class one-hot label embedding.

    Positives: [features | one_hot(true_class)]
    Negatives: [features | one_hot(different_class)]  ← guaranteed wrong
    Inference: try all N class labels, pick argmax goodness.
    """
    Xtr_s, Xte_s, _, _ = standardize(X_train, X_test)
    Xtr = torch.tensor(Xtr_s, dtype=torch.float32)
    Xte = torch.tensor(Xte_s, dtype=torch.float32)

    # Amplify label signal: scale one-hot to magnitude ~3 (comparable to
    # standardized features after LayerNorm).
    LABEL_SCALE = 3.0
    y_pos = one_hot(y_train, n_classes) * LABEL_SCALE
    # For each sample, pick a DIFFERENT class uniformly at random
    wrong = np.random.randint(1, n_classes, size=len(y_train))
    y_neg_idx = (y_train + wrong) % n_classes
    y_neg = one_hot(y_neg_idx, n_classes) * LABEL_SCALE

    pos = torch.cat([Xtr, torch.tensor(y_pos, dtype=torch.float32)], dim=1)
    neg = torch.cat([Xtr, torch.tensor(y_neg, dtype=torch.float32)], dim=1)

    input_dim = n_features + n_classes
    ff = FFNetV2(
        input_dim=input_dim,
        layer_sizes=[input_dim, 48, 24],
        threshold=2.0,
        lr=0.005,
    )
    for _ in range(n_epochs):
        ff.train_epoch(pos, neg, batch_size=min(256, len(pos)))

    # Inference: for each test sample, score all N class labels, argmax
    n_test = len(X_test)
    goodness_by_class = np.zeros((n_test, n_classes), dtype=np.float32)
    for c in range(n_classes):
        lab = np.zeros((n_test, n_classes), dtype=np.float32)
        lab[:, c] = LABEL_SCALE
        inp = torch.cat([Xte, torch.tensor(lab, dtype=torch.float32)], dim=1)
        g = ff.predict(inp).numpy()
        goodness_by_class[:, c] = g
    preds = goodness_by_class.argmax(axis=1)
    acc = float((preds == y_test).mean())
    return acc, preds, goodness_by_class


def train_and_eval_backprop(X_train, y_train, X_test, y_test, n_features, n_classes, n_epochs=40):
    """Multi-class backprop baseline — simple MLP with CrossEntropy."""
    Xtr_s, Xte_s, _, _ = standardize(X_train, X_test)
    Xtr = torch.tensor(Xtr_s, dtype=torch.float32)
    ytr = torch.tensor(y_train, dtype=torch.long)
    Xte = torch.tensor(Xte_s, dtype=torch.float32)
    yte = torch.tensor(y_test, dtype=torch.long)

    import torch.nn as nn
    net = nn.Sequential(
        nn.LayerNorm(n_features),
        nn.Linear(n_features, 48), nn.ReLU(), nn.Dropout(0.1),
        nn.Linear(48, 24), nn.ReLU(),
        nn.Linear(24, n_classes),
    )
    opt = torch.optim.Adam(net.parameters(), lr=0.005, weight_decay=1e-4)
    crit = nn.CrossEntropyLoss()
    for _ in range(n_epochs):
        opt.zero_grad()
        logits = net(Xtr)
        loss = crit(logits, ytr)
        loss.backward()
        opt.step()
    with torch.no_grad():
        preds = net(Xte).argmax(dim=1).numpy()
    acc = float((preds == y_test).mean())
    return acc, preds


def train_and_eval_lgbm(X_train, y_train, X_test, y_test, n_classes):
    train_data = lgb.Dataset(X_train, label=y_train)
    params = {
        "objective": "multiclass",
        "num_class": n_classes,
        "metric": "multi_logloss",
        "num_leaves": 31,
        "learning_rate": 0.05,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "min_data_in_leaf": 10,
        "verbose": -1,
    }
    model = lgb.train(params, train_data, num_boost_round=150)
    probs = model.predict(X_test)
    preds = probs.argmax(axis=1)
    acc = float((preds == y_test).mean())
    return acc, preds


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------
def class_distribution(y: np.ndarray) -> dict:
    out = {}
    for i, (name, *_rest) in enumerate(CLASSES):
        n = int((y == i).sum())
        out[name] = {"count": n, "pct": round(n / len(y) * 100, 2)}
    return out


def per_class_accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    out = {}
    for i, (name, *_rest) in enumerate(CLASSES):
        mask = y_true == i
        if mask.sum() == 0:
            out[name] = None
            continue
        acc = float((y_pred[mask] == i).mean())
        out[name] = {"n": int(mask.sum()), "recall": round(acc, 4)}
    return out


def confusion_matrix(y_true: np.ndarray, y_pred: np.ndarray, n_classes: int) -> list:
    cm = np.zeros((n_classes, n_classes), dtype=int)
    for t, p in zip(y_true, y_pred):
        cm[t, p] += 1
    return cm.tolist()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("Loading BTC 1h OHLCV…")
    df = load_btc()
    print(f"  {len(df)} bars, {df.index[0]} → {df.index[-1]}")

    print("\nBuilding features…")
    panel = build_features(df)
    print(f"  {len(panel.columns)} columns: {list(panel.columns)}")

    print(f"\nLabeling train set ({TRAIN_START.date()} → {TRAIN_END.date()}) via triple-barrier oracle…")
    X_tr, y_tr, ts_tr = build_samples(df, panel, TRAIN_START, TRAIN_END)
    print(f"  train samples: {len(X_tr)} (n_features={X_tr.shape[1]})")
    dist_tr = class_distribution(y_tr)
    print("  train class distribution:")
    for name, d in dist_tr.items():
        print(f"    {name:<15} {d['count']:>5}  ({d['pct']:.1f}%)")

    print(f"\nLabeling holdout set ({TEST_START.date()} → {TEST_END.date()})…")
    X_te, y_te, ts_te = build_samples(df, panel, TEST_START, TEST_END)
    print(f"  holdout samples: {len(X_te)}")
    dist_te = class_distribution(y_te)
    print("  holdout class distribution:")
    for name, d in dist_te.items():
        print(f"    {name:<15} {d['count']:>5}  ({d['pct']:.1f}%)")

    # Baseline: predict majority class
    maj_cls = int(pd.Series(y_tr).value_counts().idxmax())
    maj_acc = float((y_te == maj_cls).mean())
    print(f"\nMajority-class baseline: predict '{CLASSES[maj_cls][0]}' → {maj_acc*100:.2f}% holdout accuracy")

    n_features = X_tr.shape[1]
    print(f"\n=== Training Forward-Forward (PRIMARY) ===")
    ff_acc, ff_preds, _ = train_and_eval_ff(X_tr, y_tr, X_te, y_te, n_features, N_CLASSES)
    print(f"  FF holdout accuracy: {ff_acc*100:.2f}%")

    print(f"\n=== Training Backprop (comparison) ===")
    bp_acc, bp_preds = train_and_eval_backprop(X_tr, y_tr, X_te, y_te, n_features, N_CLASSES)
    print(f"  Backprop holdout accuracy: {bp_acc*100:.2f}%")

    print(f"\n=== Training LightGBM (comparison) ===")
    lgbm_acc, lgbm_preds = train_and_eval_lgbm(X_tr, y_tr, X_te, y_te, N_CLASSES)
    print(f"  LightGBM holdout accuracy: {lgbm_acc*100:.2f}%")

    print(f"\n" + "=" * 70)
    print("HOLDOUT RESULTS — 7-class oracle setup recognition")
    print("=" * 70)
    print(f"{'Model':<25} {'Accuracy':>12} {'vs Majority':>14}")
    print(f"{'Majority baseline':<25} {maj_acc*100:>11.2f}% {'—':>14}")
    print(f"{'Forward-Forward':<25} {ff_acc*100:>11.2f}% {(ff_acc-maj_acc)*100:>+13.2f}pp")
    print(f"{'Backprop':<25} {bp_acc*100:>11.2f}% {(bp_acc-maj_acc)*100:>+13.2f}pp")
    print(f"{'LightGBM':<25} {lgbm_acc*100:>11.2f}% {(lgbm_acc-maj_acc)*100:>+13.2f}pp")

    # Per-class breakdown for FF
    print(f"\n=== FF per-class recall (holdout) ===")
    pc = per_class_accuracy(y_te, ff_preds)
    for name, d in pc.items():
        if d is None:
            print(f"  {name:<15}  (no samples)")
        else:
            print(f"  {name:<15}  n={d['n']:>4}  recall={d['recall']*100:>5.2f}%")

    print(f"\n=== FF confusion matrix (rows=true, cols=pred) ===")
    cm = confusion_matrix(y_te, ff_preds, N_CLASSES)
    header = "       " + "".join(f"{i:>6}" for i in range(N_CLASSES))
    print(header)
    for i, row in enumerate(cm):
        label = CLASSES[i][0][:6]
        print(f"  {label:<5}" + "".join(f"{c:>6}" for c in row))
    print(f"  legend: {', '.join(f'{i}={n[:8]}' for i, (n, *_) in enumerate(CLASSES))}")

    # Verdict
    ff_minus_maj = (ff_acc - maj_acc) * 100
    ff_minus_bp = (ff_acc - bp_acc) * 100
    verdict = "KILL"
    if ff_minus_maj > 5 and ff_minus_bp > 0:
        verdict = "PASS"
    elif ff_minus_maj > 2:
        verdict = "MARGINAL"
    print(f"\nVerdict: {verdict}")
    print(f"  FF − majority: {ff_minus_maj:+.2f}pp")
    print(f"  FF − backprop: {ff_minus_bp:+.2f}pp")

    # Save
    results = {
        "train_range": [str(TRAIN_START.date()), str(TRAIN_END.date())],
        "test_range": [str(TEST_START.date()), str(TEST_END.date())],
        "n_train": int(len(X_tr)),
        "n_test": int(len(X_te)),
        "n_features": int(n_features),
        "n_classes": int(N_CLASSES),
        "class_distribution_train": dist_tr,
        "class_distribution_test": dist_te,
        "majority_baseline_acc": maj_acc,
        "ff_acc": ff_acc,
        "backprop_acc": bp_acc,
        "lgbm_acc": lgbm_acc,
        "ff_per_class": pc,
        "ff_confusion": cm,
        "verdict": verdict,
    }
    OUT_RESULTS.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nSaved → {OUT_RESULTS}")


if __name__ == "__main__":
    main()
