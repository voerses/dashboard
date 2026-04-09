"""
CNN Regime Detector v2 for s523c — Daily images + Small CNN + Numerical MLP.

Fixes from v1:
- Daily images (1550 samples) instead of weekly (245) — 7x more data
- SmallChartCNN (~70K params) instead of ResNet18 (11M) — much harder to overfit
- Numerical MLP baseline to check if spatial patterns add value
- Early stopping with patience=10 on validation loss
- Walk-forward: train 2022-2024, validate 2025+

Usage:
    /workspace/venv/bin/python research/s523_cnn_v2_daily.py
"""

import os
import json
import sys
import warnings
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import Counter

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms
from PIL import Image

warnings.filterwarnings('ignore')

# ============================================================
# CONFIG
# ============================================================
BTC_CSV = '/workspace/crypto_backtest/data/perp/binance/1h_ohlcv/BTC_perp_1h.csv'
TRADES_JSON = '/workspace/crypto_backtest/results/v4/s523c_growth_75mo_50k_trades.json'
IMAGE_DIR = '/tmp/chart_images_daily'
MODEL_PATH = '/workspace/crypto_backtest/research/s523_cnn_v2_model.pt'
RESULTS_PATH = '/workspace/crypto_backtest/research/s523_cnn_v2_results.json'

DATE_START = '2022-01-01'
DATE_END = '2026-03-29'
TRAIN_END = '2024-12-31'  # Walk-forward split
LOOKBACK_WEEKS = 26  # 6 months of weekly candles
IMAGE_SIZE = 224
PNL_THRESHOLD = 200
FORWARD_DAYS = 7

BAR_EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)

EMA_PERIODS = [8, 13, 21, 34, 55]
SMA_PERIODS = [20, 50, 100, 200]
EMA_COLORS = ['#FF6B6B', '#FF8E53', '#FFC107', '#66BB6A', '#42A5F5']
SMA_COLORS = ['#FFFFFF', '#AAAAAA', '#777777', '#444444']

CLASS_NAMES = {0: 'LONG', 1: 'SHORT', 2: 'FLAT'}

os.makedirs(IMAGE_DIR, exist_ok=True)


# ============================================================
# DATA LOADING
# ============================================================

def load_btc_data():
    """Load BTC hourly data and create both daily and weekly resamples."""
    print("Loading BTC hourly data...")
    df = pd.read_csv(BTC_CSV)
    df['datetime'] = pd.to_datetime(df['datetime'], utc=True)
    df = df.set_index('datetime')
    df = df[['open', 'high', 'low', 'close', 'volume']].astype(float)

    # Daily OHLCV
    daily = df.resample('D').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()

    # Weekly OHLCV (Monday start)
    weekly = df.resample('W-MON', label='left', closed='left').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()

    # Compute EMAs and SMAs on weekly close
    for p in EMA_PERIODS:
        weekly[f'ema_{p}'] = weekly['close'].ewm(span=p, adjust=False).mean()
    for p in SMA_PERIODS:
        weekly[f'sma_{p}'] = weekly['close'].rolling(p).mean()

    print(f"Daily data: {len(daily)} bars, {daily.index[0].date()} to {daily.index[-1].date()}")
    print(f"Weekly data: {len(weekly)} bars, {weekly.index[0].date()} to {weekly.index[-1].date()}")
    return daily, weekly


def load_trades():
    """Load s523c trades and map entry_bar to datetime."""
    print("Loading s523c trades...")
    with open(TRADES_JSON) as f:
        raw = json.load(f)

    trades = []
    for t in raw:
        bar = t['entry_bar']
        entry_dt = BAR_EPOCH + timedelta(hours=bar)
        trades.append({
            'entry_dt': entry_dt,
            'direction': t['direction'],
            'pnl': float(t['pnl']),
            'token': t['token'],
        })

    df = pd.DataFrame(trades)
    df['entry_dt'] = pd.to_datetime(df['entry_dt'], utc=True)
    print(f"Loaded {len(df)} trades, {df.entry_dt.min().date()} to {df.entry_dt.max().date()}")
    return df


def label_days(daily, trades_df):
    """For each day, compute forward 7-day s523c PnL by direction and assign label."""
    dates = daily.index[(daily.index >= DATE_START) & (daily.index <= DATE_END)]
    labels = []

    for d in dates:
        d_start = d
        d_end = d + timedelta(days=FORWARD_DAYS)

        mask = (trades_df.entry_dt >= d_start) & (trades_df.entry_dt < d_end)
        window_trades = trades_df[mask]

        long_pnl = window_trades[window_trades.direction == 1].pnl.sum()
        short_pnl = window_trades[window_trades.direction == -1].pnl.sum()

        if long_pnl > PNL_THRESHOLD and long_pnl > short_pnl:
            label = 0  # LONG
        elif short_pnl > PNL_THRESHOLD and short_pnl > long_pnl:
            label = 1  # SHORT
        else:
            label = 2  # FLAT

        labels.append({
            'date': d.strftime('%Y-%m-%d'),
            'label': label,
            'long_pnl': round(long_pnl, 2),
            'short_pnl': round(short_pnl, 2),
            'n_trades': len(window_trades),
        })

    df = pd.DataFrame(labels)
    dist = df.label.value_counts().sort_index()
    print(f"\nLabel distribution ({len(df)} days):")
    for cls, count in dist.items():
        print(f"  {CLASS_NAMES[cls]}: {count} ({100*count/len(df):.1f}%)")
    return df


# ============================================================
# IMAGE GENERATION
# ============================================================

def generate_chart_image(weekly, daily_close, target_date, save_path):
    """Generate a clean 224x224 chart image for the given day.

    Shows last 26 weekly candles + EMAs/SMAs + volume.
    The daily close is shown as a dot on the rightmost position.
    """
    # Find the most recent complete week on or before target_date
    weekly_before = weekly[weekly.index <= target_date]
    if len(weekly_before) < LOOKBACK_WEEKS:
        return False

    window = weekly_before.iloc[-LOOKBACK_WEEKS:].copy()

    fig, (ax_price, ax_vol) = plt.subplots(
        2, 1, figsize=(2.24, 2.24), dpi=100,
        gridspec_kw={'height_ratios': [4, 1]},
        facecolor='black'
    )

    # --- Price chart (log scale) ---
    ax_price.set_facecolor('black')
    ax_price.set_yscale('log')

    x = np.arange(len(window))
    opens = window['open'].values
    highs = window['high'].values
    lows = window['low'].values
    closes = window['close'].values

    up = closes >= opens
    down = ~up

    # Wicks
    for i in range(len(window)):
        color = '#26A69A' if up[i] else '#EF5350'
        ax_price.plot([x[i], x[i]], [lows[i], highs[i]], color=color, linewidth=0.5)

    # Bodies
    body_width = 0.6
    for i in range(len(window)):
        color = '#26A69A' if up[i] else '#EF5350'
        bottom = min(opens[i], closes[i])
        height = abs(closes[i] - opens[i])
        if height < 1:
            height = 1
        rect = Rectangle((x[i] - body_width/2, bottom), body_width, height,
                         facecolor=color, edgecolor='none')
        ax_price.add_patch(rect)

    # Current daily price dot (distinguishes images within same week)
    if daily_close is not None:
        ax_price.plot(x[-1], daily_close, 'o', color='#FFFFFF',
                     markersize=2, zorder=10)

    # EMA ribbon
    for j, p in enumerate(EMA_PERIODS):
        col = f'ema_{p}'
        if col in window.columns:
            vals = window[col].values
            ax_price.plot(x, vals, color=EMA_COLORS[j], linewidth=0.7, alpha=0.8)

    # SMAs (dashed)
    for j, p in enumerate(SMA_PERIODS):
        col = f'sma_{p}'
        if col in window.columns:
            vals = window[col].values
            valid = ~np.isnan(vals)
            if valid.any():
                ax_price.plot(x[valid], vals[valid], color=SMA_COLORS[j],
                            linewidth=0.5, linestyle='--', alpha=0.6)

    ax_price.set_xlim(-0.5, len(window) - 0.5)
    ax_price.set_ylim(lows.min() * 0.98, highs.max() * 1.02)
    ax_price.axis('off')

    # --- Volume subplot ---
    ax_vol.set_facecolor('black')
    vol = window['volume'].values
    colors_vol = ['#26A69A' if up[i] else '#EF5350' for i in range(len(window))]
    ax_vol.bar(x, vol, color=colors_vol, width=0.6, alpha=0.7)
    ax_vol.set_xlim(-0.5, len(window) - 0.5)
    ax_vol.axis('off')

    plt.subplots_adjust(left=0, right=1, top=1, bottom=0, hspace=0.02)
    fig.savefig(save_path, dpi=100, facecolor='black', bbox_inches='tight', pad_inches=0)
    plt.close(fig)
    return True


def generate_all_charts(weekly, daily, labels_df):
    """Generate chart images for all labeled days."""
    print("\nGenerating chart images...")
    t0 = time.time()
    generated = 0
    skipped = 0

    for i, row in labels_df.iterrows():
        target_date = pd.Timestamp(row['date'], tz='UTC')
        save_path = os.path.join(IMAGE_DIR, f"{row['date']}.png")

        if os.path.exists(save_path):
            generated += 1
            continue

        # Get daily close for this date
        if target_date in daily.index:
            daily_close = daily.loc[target_date, 'close']
        else:
            daily_close = None

        success = generate_chart_image(weekly, daily_close, target_date, save_path)
        if success:
            generated += 1
        else:
            skipped += 1

        if generated % 100 == 0 and generated > 0:
            elapsed = time.time() - t0
            rate = generated / elapsed
            remaining = (len(labels_df) - generated - skipped) / max(rate, 0.01)
            print(f"  Generated {generated} images... ({rate:.1f}/sec, ~{remaining:.0f}s remaining)")

    elapsed = time.time() - t0
    print(f"Done: {generated} generated, {skipped} skipped in {elapsed:.1f}s")
    return generated


# ============================================================
# NUMERICAL FEATURES
# ============================================================

def extract_numerical_features(weekly, daily, labels_df):
    """Extract numerical features from chart data for each labeled day."""
    features = []

    for _, row in labels_df.iterrows():
        target_date = pd.Timestamp(row['date'], tz='UTC')
        weekly_before = weekly[weekly.index <= target_date]

        if len(weekly_before) < LOOKBACK_WEEKS:
            features.append(None)
            continue

        window = weekly_before.iloc[-LOOKBACK_WEEKS:]
        current_close = window['close'].iloc[-1]

        feat = {}

        # EMA distances from price (as % of price)
        for p in EMA_PERIODS:
            col = f'ema_{p}'
            if col in window.columns:
                val = window[col].iloc[-1]
                feat[f'ema_{p}_dist'] = (current_close - val) / current_close * 100

        # SMA distances from price
        for p in SMA_PERIODS:
            col = f'sma_{p}'
            if col in window.columns:
                val = window[col].iloc[-1]
                if not np.isnan(val):
                    feat[f'sma_{p}_dist'] = (current_close - val) / current_close * 100
                else:
                    feat[f'sma_{p}_dist'] = 0.0

        # EMA alignment score: +1 for each EMA pair in correct order (short above long)
        ema_vals = []
        for p in EMA_PERIODS:
            col = f'ema_{p}'
            if col in window.columns:
                ema_vals.append(window[col].iloc[-1])
        alignment = 0
        for i in range(len(ema_vals) - 1):
            if ema_vals[i] > ema_vals[i+1]:
                alignment += 1
            else:
                alignment -= 1
        feat['ema_alignment'] = alignment

        # Ribbon width: (fastest EMA - slowest EMA) / price
        if len(ema_vals) >= 2:
            feat['ribbon_width'] = (ema_vals[0] - ema_vals[-1]) / current_close * 100
        else:
            feat['ribbon_width'] = 0.0

        # Chop score: count of EMA crossovers in last 8 weeks
        chop = 0
        if len(window) >= 8:
            last8 = window.iloc[-8:]
            if f'ema_{EMA_PERIODS[0]}' in last8.columns and f'ema_{EMA_PERIODS[-1]}' in last8.columns:
                fast = last8[f'ema_{EMA_PERIODS[0]}'].values
                slow = last8[f'ema_{EMA_PERIODS[-1]}'].values
                diff = fast - slow
                signs = np.sign(diff)
                chop = np.sum(np.abs(np.diff(signs)) > 0)
        feat['chop_score'] = float(chop)

        # 3-month (13 week) return
        if len(window) >= 13:
            feat['return_13w'] = (window['close'].iloc[-1] / window['close'].iloc[-13] - 1) * 100
        else:
            feat['return_13w'] = 0.0

        # Last 4 weekly candle body sizes (as % of price)
        for i in range(4):
            idx = -(i + 1)
            if abs(idx) <= len(window):
                body = abs(window['close'].iloc[idx] - window['open'].iloc[idx])
                feat[f'body_{i}'] = body / current_close * 100
            else:
                feat[f'body_{i}'] = 0.0

        features.append(feat)

    # Convert to arrays
    valid_mask = [f is not None for f in features]
    feature_names = list(features[next(i for i, f in enumerate(features) if f is not None)].keys())

    X = np.zeros((len(features), len(feature_names)))
    for i, f in enumerate(features):
        if f is not None:
            for j, name in enumerate(feature_names):
                X[i, j] = f.get(name, 0.0)

    return X, np.array(valid_mask), feature_names


# ============================================================
# MODELS
# ============================================================

class SmallChartCNN(nn.Module):
    """Tiny CNN for chart images (~70K parameters)."""
    def __init__(self, n_classes=3):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(3, 16, 5, stride=2, padding=2),   # 224->112
            nn.BatchNorm2d(16), nn.ReLU(),
            nn.Conv2d(16, 32, 3, stride=2, padding=1),  # 112->56
            nn.BatchNorm2d(32), nn.ReLU(),
            nn.Conv2d(32, 64, 3, stride=2, padding=1),  # 56->28
            nn.BatchNorm2d(64), nn.ReLU(),
            nn.AdaptiveAvgPool2d(4),                     # 28->4
        )
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Linear(64*4*4, 64), nn.ReLU(), nn.Dropout(0.3),
            nn.Linear(64, n_classes),
        )

    def forward(self, x):
        return self.classifier(self.features(x))


class NumericalMLP(nn.Module):
    """Small MLP for numerical features (~1.5K parameters)."""
    def __init__(self, n_features, n_classes=3):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(n_features, 32),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(32, 16),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(16, n_classes),
        )

    def forward(self, x):
        return self.net(x)


# ============================================================
# DATASETS
# ============================================================

class ChartImageDataset(Dataset):
    def __init__(self, labels_df, image_dir, transform=None):
        self.labels = labels_df.reset_index(drop=True)
        self.image_dir = image_dir
        self.transform = transform

        # Filter to only existing images
        valid = []
        for i, row in self.labels.iterrows():
            path = os.path.join(image_dir, f"{row['date']}.png")
            if os.path.exists(path):
                valid.append(i)
        self.labels = self.labels.iloc[valid].reset_index(drop=True)

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        row = self.labels.iloc[idx]
        path = os.path.join(self.image_dir, f"{row['date']}.png")
        img = Image.open(path).convert('RGB').resize((IMAGE_SIZE, IMAGE_SIZE))
        if self.transform:
            img = self.transform(img)
        return img, row['label']


class NumericalDataset(Dataset):
    def __init__(self, X, y):
        self.X = torch.FloatTensor(X)
        self.y = torch.LongTensor(y)

    def __len__(self):
        return len(self.y)

    def __getitem__(self, idx):
        return self.X[idx], self.y[idx]


# ============================================================
# TRAINING
# ============================================================

def compute_class_weights(labels):
    """Inverse frequency class weights."""
    counts = Counter(labels)
    total = sum(counts.values())
    n_classes = 3
    weights = [total / (n_classes * counts.get(c, 1)) for c in range(n_classes)]
    return weights


def train_model(model, train_loader, val_loader, class_weights, n_epochs=50, lr=0.0005, patience=10, model_name="Model"):
    """Train with early stopping on validation loss."""
    weights = torch.FloatTensor(class_weights)
    criterion = nn.CrossEntropyLoss(weight=weights)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)

    best_val_loss = float('inf')
    best_state = None
    patience_counter = 0
    history = {'train_loss': [], 'val_loss': [], 'val_acc': []}

    print(f"\nTraining {model_name}...")
    n_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"  Parameters: {n_params:,}")

    for epoch in range(n_epochs):
        # Training
        model.train()
        epoch_loss = 0
        n_batches = 0
        for inputs, labels in train_loader:
            optimizer.zero_grad()
            outputs = model(inputs)
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()
            epoch_loss += loss.item()
            n_batches += 1

        avg_train_loss = epoch_loss / max(n_batches, 1)
        history['train_loss'].append(avg_train_loss)

        # Validation
        model.eval()
        val_loss = 0
        val_batches = 0
        correct = 0
        total = 0
        with torch.no_grad():
            for inputs, labels in val_loader:
                outputs = model(inputs)
                loss = criterion(outputs, labels)
                val_loss += loss.item()
                val_batches += 1
                _, predicted = torch.max(outputs, 1)
                total += labels.size(0)
                correct += (predicted == labels).sum().item()

        avg_val_loss = val_loss / max(val_batches, 1)
        val_acc = correct / max(total, 1)
        history['val_loss'].append(avg_val_loss)
        history['val_acc'].append(val_acc)

        # Early stopping
        if avg_val_loss < best_val_loss:
            best_val_loss = avg_val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            patience_counter = 0
            marker = " *"
        else:
            patience_counter += 1
            marker = ""

        if (epoch + 1) % 5 == 0 or epoch == 0 or patience_counter == 0:
            print(f"  Epoch {epoch+1:3d}: train_loss={avg_train_loss:.4f}, "
                  f"val_loss={avg_val_loss:.4f}, val_acc={val_acc:.3f}{marker}")

        if patience_counter >= patience:
            print(f"  Early stopping at epoch {epoch+1} (patience={patience})")
            break

    # Restore best model
    if best_state is not None:
        model.load_state_dict(best_state)
        print(f"  Restored best model (val_loss={best_val_loss:.4f})")

    return model, history


def evaluate_model(model, loader):
    """Evaluate model and return predictions."""
    model.eval()
    all_preds = []
    all_probs = []
    all_labels = []

    with torch.no_grad():
        for inputs, labels in loader:
            outputs = model(inputs)
            probs = torch.softmax(outputs, dim=1)
            _, predicted = torch.max(outputs, 1)
            all_preds.extend(predicted.numpy().tolist())
            all_probs.extend(probs.numpy().tolist())
            all_labels.extend(labels.numpy().tolist())

    return np.array(all_preds), np.array(all_labels), np.array(all_probs)


# ============================================================
# EVALUATION
# ============================================================

def print_confusion_matrix(preds, labels, title="Confusion Matrix"):
    """Print confusion matrix."""
    print(f"\n{title}:")
    print(f"{'':>12} {'Pred LONG':>10} {'Pred SHORT':>11} {'Pred FLAT':>10}")
    for true_cls in range(3):
        row = []
        for pred_cls in range(3):
            count = ((labels == true_cls) & (preds == pred_cls)).sum()
            row.append(count)
        print(f"  True {CLASS_NAMES[true_cls]:>5}: {row[0]:>10} {row[1]:>11} {row[2]:>10}")

    acc = (preds == labels).mean()
    print(f"\n  Overall accuracy: {acc:.3f}")

    for cls in range(3):
        mask = labels == cls
        if mask.sum() > 0:
            cls_acc = (preds[mask] == labels[mask]).mean()
            print(f"  {CLASS_NAMES[cls]} accuracy: {cls_acc:.3f} ({mask.sum()} samples)")

    return acc


def pnl_gating_analysis(preds, labels_df_subset, title="PnL Gating Analysis"):
    """The money test: CNN-gated vs ungated PnL."""
    print(f"\n{title}:")
    df = labels_df_subset.copy().reset_index(drop=True)
    df['pred'] = preds

    # To avoid double-counting (daily labels overlap), we need to
    # aggregate to weekly predictions (majority vote per week)
    df['week'] = pd.to_datetime(df['date']).dt.to_period('W').dt.start_time

    weekly_agg = []
    for week, grp in df.groupby('week'):
        # Majority vote for prediction
        pred_counts = grp.pred.value_counts()
        majority_pred = pred_counts.index[0]
        # Use the first day's PnL data (forward-looking already accounts for the week)
        first = grp.iloc[0]
        weekly_agg.append({
            'week': week,
            'pred': majority_pred,
            'long_pnl': first['long_pnl'],
            'short_pnl': first['short_pnl'],
            'label': first['label'],
        })

    wdf = pd.DataFrame(weekly_agg)

    long_mask = wdf.pred == 0
    short_mask = wdf.pred == 1
    flat_mask = wdf.pred == 2

    if long_mask.sum() > 0:
        print(f"  Pred LONG  ({long_mask.sum()} weeks): long_pnl=${wdf[long_mask].long_pnl.sum():,.0f}, "
              f"short_pnl=${wdf[long_mask].short_pnl.sum():,.0f}")
    if short_mask.sum() > 0:
        print(f"  Pred SHORT ({short_mask.sum()} weeks): short_pnl=${wdf[short_mask].short_pnl.sum():,.0f}, "
              f"long_pnl=${wdf[short_mask].long_pnl.sum():,.0f}")
    if flat_mask.sum() > 0:
        print(f"  Pred FLAT  ({flat_mask.sum()} weeks): avoided long=${wdf[flat_mask].long_pnl.sum():,.0f}, "
              f"short=${wdf[flat_mask].short_pnl.sum():,.0f}")

    ungated = wdf.long_pnl.sum() + wdf.short_pnl.sum()
    gated_long = wdf[long_mask].long_pnl.sum() if long_mask.any() else 0
    gated_short = wdf[short_mask].short_pnl.sum() if short_mask.any() else 0
    gated = gated_long + gated_short

    print(f"\n  Ungated PnL: ${ungated:,.0f}")
    print(f"  Gated PnL:   ${gated:,.0f}")
    if ungated != 0:
        print(f"  Improvement: {100*(gated - ungated)/abs(ungated):+.1f}%")

    return gated, ungated


def monthly_analysis(preds, labels_df_subset, title="Monthly Analysis"):
    """Break down predictions and PnL by month."""
    print(f"\n{title}:")
    df = labels_df_subset.copy().reset_index(drop=True)
    df['pred'] = preds
    df['month'] = pd.to_datetime(df['date']).dt.to_period('M')

    print(f"  {'Month':>8} {'Days':>5} {'Acc':>5} {'L':>3} {'S':>3} {'F':>3} "
          f"{'Gated$':>9} {'Ungated$':>10}")
    print(f"  {'-'*55}")

    for month, grp in df.groupby('month'):
        acc = (grp.pred == grp.label).mean()
        n_l = (grp.pred == 0).sum()
        n_s = (grp.pred == 1).sum()
        n_f = (grp.pred == 2).sum()

        # Simple daily sum (approximate, some overlap)
        gated = grp[grp.pred == 0].long_pnl.sum() + grp[grp.pred == 1].short_pnl.sum()
        ungated = grp.long_pnl.sum() + grp.short_pnl.sum()

        print(f"  {str(month):>8} {len(grp):>5} {acc:>5.2f} {n_l:>3} {n_s:>3} {n_f:>3} "
              f"  ${gated:>8,.0f}   ${ungated:>8,.0f}")


def regime_timeline(preds, labels_df_subset, n=30):
    """Print regime prediction timeline."""
    df = labels_df_subset.copy().reset_index(drop=True)
    df['pred'] = preds
    df['pred_name'] = [CLASS_NAMES[p] for p in preds]

    print(f"\nRegime Timeline (last {n} days):")
    for _, row in df.tail(n).iterrows():
        match = '>>>' if row['pred'] == row['label'] else '   '
        print(f"  {row['date']}  pred={row['pred_name']:>5}  "
              f"actual={CLASS_NAMES[row['label']]:>5}  "
              f"long=${row['long_pnl']:>7,.0f}  short=${row['short_pnl']:>7,.0f}  {match}")


# ============================================================
# MAIN
# ============================================================

def main():
    t_start = time.time()
    print("=" * 70)
    print("CNN REGIME DETECTOR v2 — DAILY IMAGES + SMALL CNN")
    print("=" * 70)
    print(f"Fixes: daily images (not weekly), SmallCNN (not ResNet18),")
    print(f"       numerical MLP baseline, early stopping")
    print()

    # ---- Step 1: Load data ----
    daily, weekly = load_btc_data()
    trades_df = load_trades()

    # ---- Step 2: Label days ----
    labels_df = label_days(daily, trades_df)

    # ---- Step 3: Generate chart images ----
    n_generated = generate_all_charts(weekly, daily, labels_df)
    print(f"Total images available: {n_generated}")

    # ---- Step 4: Extract numerical features ----
    print("\nExtracting numerical features...")
    X_num, valid_mask, feature_names = extract_numerical_features(weekly, daily, labels_df)
    print(f"Features: {len(feature_names)} ({', '.join(feature_names[:5])}...)")

    # ---- Step 5: Walk-forward split ----
    train_mask = pd.to_datetime(labels_df.date) <= TRAIN_END
    val_mask = pd.to_datetime(labels_df.date) > TRAIN_END
    train_labels = labels_df[train_mask].copy()
    val_labels = labels_df[val_mask].copy()
    print(f"\nTrain: {len(train_labels)} days ({train_labels.date.iloc[0]} to {train_labels.date.iloc[-1]})")
    print(f"Val:   {len(val_labels)} days ({val_labels.date.iloc[0]} to {val_labels.date.iloc[-1]})")

    # ---- Step 6: Train SmallChartCNN ----
    print("\n" + "=" * 70)
    print("MODEL 1: SmallChartCNN (image-based)")
    print("=" * 70)

    train_transform = transforms.Compose([
        transforms.Resize((IMAGE_SIZE, IMAGE_SIZE)),
        transforms.RandomHorizontalFlip(p=0.5),
        transforms.ColorJitter(brightness=0.1, contrast=0.1),
        transforms.RandomResizedCrop(IMAGE_SIZE, scale=(0.9, 1.0)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])
    val_transform = transforms.Compose([
        transforms.Resize((IMAGE_SIZE, IMAGE_SIZE)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])

    train_img_dataset = ChartImageDataset(train_labels, IMAGE_DIR, train_transform)
    val_img_dataset = ChartImageDataset(val_labels, IMAGE_DIR, val_transform)
    print(f"Image dataset: train={len(train_img_dataset)}, val={len(val_img_dataset)}")

    class_weights = compute_class_weights(train_labels.label.values)
    print(f"Class weights: {[f'{w:.2f}' for w in class_weights]}")

    train_img_loader = DataLoader(train_img_dataset, batch_size=32, shuffle=True)
    val_img_loader = DataLoader(val_img_dataset, batch_size=32, shuffle=False)

    cnn_model = SmallChartCNN(n_classes=3)
    cnn_model, cnn_history = train_model(
        cnn_model, train_img_loader, val_img_loader, class_weights,
        n_epochs=50, lr=0.0005, patience=10, model_name="SmallChartCNN"
    )

    # Save CNN model
    torch.save(cnn_model.state_dict(), MODEL_PATH)
    print(f"CNN model saved to {MODEL_PATH}")

    # Evaluate CNN
    cnn_val_preds, cnn_val_labels, cnn_val_probs = evaluate_model(cnn_model, val_img_loader)
    cnn_val_acc = print_confusion_matrix(cnn_val_preds, cnn_val_labels, "SmallChartCNN — Validation Confusion Matrix")

    # ---- Step 7: Train NumericalMLP ----
    print("\n" + "=" * 70)
    print("MODEL 2: NumericalMLP (feature-based)")
    print("=" * 70)

    # Prepare numerical data (standardize using train stats)
    train_idx = np.where(train_mask.values & valid_mask)[0]
    val_idx = np.where(val_mask.values & valid_mask)[0]

    X_train = X_num[train_idx]
    y_train = labels_df.label.values[train_idx]
    X_val = X_num[val_idx]
    y_val = labels_df.label.values[val_idx]

    # Standardize
    train_mean = X_train.mean(axis=0)
    train_std = X_train.std(axis=0) + 1e-8
    X_train_norm = (X_train - train_mean) / train_std
    X_val_norm = (X_val - train_mean) / train_std

    print(f"Numerical dataset: train={len(X_train)}, val={len(X_val)}, features={X_train.shape[1]}")

    train_num_dataset = NumericalDataset(X_train_norm, y_train)
    val_num_dataset = NumericalDataset(X_val_norm, y_val)
    train_num_loader = DataLoader(train_num_dataset, batch_size=32, shuffle=True)
    val_num_loader = DataLoader(val_num_dataset, batch_size=32, shuffle=False)

    mlp_model = NumericalMLP(n_features=X_train.shape[1], n_classes=3)
    mlp_model, mlp_history = train_model(
        mlp_model, train_num_loader, val_num_loader, class_weights,
        n_epochs=50, lr=0.001, patience=10, model_name="NumericalMLP"
    )

    # Evaluate MLP
    mlp_val_preds, mlp_val_labels, mlp_val_probs = evaluate_model(mlp_model, val_num_loader)
    mlp_val_acc = print_confusion_matrix(mlp_val_preds, mlp_val_labels, "NumericalMLP — Validation Confusion Matrix")

    # ---- Step 8: The money test ----
    print("\n" + "=" * 70)
    print("THE MONEY TEST — CNN-Gated vs Ungated vs MLP-Gated (2025 OOS)")
    print("=" * 70)

    # CNN gating (use val_img_dataset's filtered labels)
    cnn_val_labels_df = val_labels[val_labels.date.isin(val_img_dataset.labels.date.values)].copy()
    cnn_gated, cnn_ungated = pnl_gating_analysis(cnn_val_preds, cnn_val_labels_df, "SmallChartCNN Gating (2025)")

    # MLP gating
    mlp_val_labels_df = val_labels.iloc[val_idx - val_mask.values.argmax()].copy().reset_index(drop=True)
    # Safer: reconstruct from val_idx
    mlp_val_labels_df = labels_df.iloc[val_idx].copy()
    mlp_gated, mlp_ungated = pnl_gating_analysis(mlp_val_preds, mlp_val_labels_df, "NumericalMLP Gating (2025)")

    # Monthly breakdown
    monthly_analysis(cnn_val_preds, cnn_val_labels_df, "SmallChartCNN — Monthly (2025)")
    monthly_analysis(mlp_val_preds, mlp_val_labels_df, "NumericalMLP — Monthly (2025)")

    # Regime timeline
    regime_timeline(cnn_val_preds, cnn_val_labels_df, n=30)

    # ---- Step 9: Comparison table ----
    print("\n" + "=" * 70)
    print("COMPARISON: SmallCNN vs NumericalMLP vs v1 ResNet")
    print("=" * 70)

    # Random baseline
    random_acc = 1.0 / 3.0

    print(f"\n  {'Model':>20} {'Params':>10} {'Train Size':>11} {'OOS Acc':>8} "
          f"{'Gated PnL':>10} {'Ungated PnL':>12} {'Improve':>8}")
    print(f"  {'-'*83}")
    print(f"  {'v1 ResNet18':>20} {'11.2M':>10} {'~190':>11} {'0.450':>8} "
          f"{'N/A':>10} {'N/A':>12} {'N/A':>8}")
    print(f"  {'SmallChartCNN':>20} {'~70K':>10} {f'~{len(train_img_dataset)}':>11} {cnn_val_acc:>8.3f} "
          f"  ${cnn_gated:>8,.0f}   ${cnn_ungated:>9,.0f}  "
          f"{100*(cnn_gated-cnn_ungated)/abs(cnn_ungated) if cnn_ungated!=0 else 0:+.1f}%")
    print(f"  {'NumericalMLP':>20} {'~1.5K':>10} {f'~{len(X_train)}':>11} {mlp_val_acc:>8.3f} "
          f"  ${mlp_gated:>8,.0f}   ${mlp_ungated:>9,.0f}  "
          f"{100*(mlp_gated-mlp_ungated)/abs(mlp_ungated) if mlp_ungated!=0 else 0:+.1f}%")
    print(f"  {'Random':>20} {'0':>10} {'N/A':>11} {random_acc:>8.3f} "
          f"{'N/A':>10} {'N/A':>12} {'N/A':>8}")

    # Key insight
    print(f"\n  Key insight:")
    if mlp_val_acc >= cnn_val_acc:
        print(f"  NumericalMLP >= SmallCNN accuracy ({mlp_val_acc:.3f} vs {cnn_val_acc:.3f})")
        print(f"  => Spatial chart patterns do NOT add value beyond numerical features")
    else:
        print(f"  SmallCNN > NumericalMLP accuracy ({cnn_val_acc:.3f} vs {mlp_val_acc:.3f})")
        print(f"  => Spatial chart patterns provide ADDITIONAL signal")

    # ---- Step 10: Save results ----
    results = {
        'config': {
            'date_start': DATE_START,
            'date_end': DATE_END,
            'train_end': TRAIN_END,
            'lookback_weeks': LOOKBACK_WEEKS,
            'pnl_threshold': PNL_THRESHOLD,
            'forward_days': FORWARD_DAYS,
            'image_size': IMAGE_SIZE,
        },
        'data': {
            'total_days': len(labels_df),
            'train_days': len(train_labels),
            'val_days': len(val_labels),
            'label_distribution': {CLASS_NAMES[k]: int(v) for k, v in
                                   labels_df.label.value_counts().sort_index().items()},
        },
        'cnn': {
            'model': 'SmallChartCNN',
            'params': sum(p.numel() for p in cnn_model.parameters()),
            'val_accuracy': float(cnn_val_acc),
            'val_per_class': {
                CLASS_NAMES[c]: float((cnn_val_preds[cnn_val_labels == c] == c).mean())
                if (cnn_val_labels == c).sum() > 0 else 0.0
                for c in range(3)
            },
            'gated_pnl': float(cnn_gated),
            'ungated_pnl': float(cnn_ungated),
            'training_epochs': len(cnn_history['train_loss']),
            'best_val_loss': float(min(cnn_history['val_loss'])),
        },
        'mlp': {
            'model': 'NumericalMLP',
            'params': sum(p.numel() for p in mlp_model.parameters()),
            'val_accuracy': float(mlp_val_acc),
            'val_per_class': {
                CLASS_NAMES[c]: float((mlp_val_preds[mlp_val_labels == c] == c).mean())
                if (mlp_val_labels == c).sum() > 0 else 0.0
                for c in range(3)
            },
            'gated_pnl': float(mlp_gated),
            'ungated_pnl': float(mlp_ungated),
            'training_epochs': len(mlp_history['train_loss']),
            'best_val_loss': float(min(mlp_history['val_loss'])),
        },
        'v1_reference': {
            'model': 'ResNet18 (pretrained)',
            'params': 11_200_000,
            'train_samples': 190,
            'val_accuracy': 0.45,
            'notes': 'Memorized by epoch 15, random OOS, destroyed value in 2025',
        },
        'predictions_2025': [],
    }

    # Save per-day predictions for 2025
    for i in range(len(cnn_val_preds)):
        row = cnn_val_labels_df.iloc[i]
        results['predictions_2025'].append({
            'date': row['date'],
            'cnn_pred': CLASS_NAMES[int(cnn_val_preds[i])],
            'cnn_probs': [round(float(p), 4) for p in cnn_val_probs[i]],
            'actual_label': CLASS_NAMES[row['label']],
            'long_pnl': float(row['long_pnl']),
            'short_pnl': float(row['short_pnl']),
        })

    with open(RESULTS_PATH, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {RESULTS_PATH}")

    elapsed = time.time() - t_start
    print(f"\nTotal runtime: {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)


if __name__ == '__main__':
    main()
