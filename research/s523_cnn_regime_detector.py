"""
CNN-based regime detector for s523c using BTC weekly chart images.
Trains a ResNet18 to predict LONG/SHORT/FLAT from visual chart patterns.
"""

import os
import json
import sys
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from torchvision import models, transforms
from PIL import Image
from collections import Counter

warnings.filterwarnings('ignore')

# ============================================================
# CONFIG
# ============================================================
BTC_CSV = '/workspace/crypto_backtest/data/perp/binance/1h_ohlcv/BTC_perp_1h.csv'
TRADES_JSON = '/workspace/crypto_backtest/results/v4/s523c_growth_75mo_50k_trades.json'
IMAGE_DIR = '/tmp/chart_images'
MODEL_PATH = '/workspace/crypto_backtest/research/s523_cnn_regime_model.pt'
PRED_PATH = '/workspace/crypto_backtest/research/s523_cnn_regime_predictions.json'

WEEK_START = '2021-06-01'
WEEK_END = '2026-03-01'
TRAIN_END = '2024-12-31'  # Walk-forward split
LOOKBACK_WEEKS = 52
IMAGE_SIZE = 224
PNL_THRESHOLD = 500

# Bar epoch: 2020-01-01 00:00:00 UTC, 1 bar = 1 hour
BAR_EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)

EMA_PERIODS = [8, 13, 21, 34, 55, 89]
SMA_PERIODS = [20, 50, 100, 200]
EMA_COLORS = ['#FF6B6B', '#FF8E53', '#FFC107', '#66BB6A', '#42A5F5', '#7E57C2']
SMA_COLORS = ['#FFFFFF', '#AAAAAA', '#777777', '#444444']

CLASS_NAMES = {0: 'LONG', 1: 'SHORT', 2: 'FLAT'}

os.makedirs(IMAGE_DIR, exist_ok=True)


def load_btc_weekly():
    """Load BTC hourly data and resample to weekly OHLCV."""
    print("Loading BTC hourly data...")
    df = pd.read_csv(BTC_CSV)
    df['datetime'] = pd.to_datetime(df['datetime'], utc=True)
    df = df.set_index('datetime')
    df = df[['open', 'high', 'low', 'close', 'volume']].astype(float)

    # Resample to weekly (Monday start)
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

    print(f"Weekly data: {len(weekly)} bars, {weekly.index[0].date()} to {weekly.index[-1].date()}")
    return weekly


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
            'direction': t['direction'],  # 1=long, -1=short
            'pnl': float(t['pnl']),
            'token': t['token'],
        })

    df = pd.DataFrame(trades)
    df['entry_dt'] = pd.to_datetime(df['entry_dt'], utc=True)
    print(f"Loaded {len(df)} trades, {df.entry_dt.min().date()} to {df.entry_dt.max().date()}")
    return df


def label_weeks(weekly, trades_df):
    """For each week, compute forward 4-week s523c PnL by direction and assign label."""
    weeks = weekly.index[(weekly.index >= WEEK_START) & (weekly.index <= WEEK_END)]
    labels = []

    for w in weeks:
        w_start = w + timedelta(weeks=1)
        w_end = w + timedelta(weeks=5)  # [W+1, W+4] inclusive

        mask = (trades_df.entry_dt >= w_start) & (trades_df.entry_dt < w_end)
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
            'week': w.strftime('%Y-%m-%d'),
            'label': label,
            'long_pnl': round(long_pnl, 2),
            'short_pnl': round(short_pnl, 2),
            'n_trades': len(window_trades),
        })

    df = pd.DataFrame(labels)
    dist = df.label.value_counts().sort_index()
    print(f"\nLabel distribution ({len(df)} weeks):")
    for cls, count in dist.items():
        print(f"  {CLASS_NAMES[cls]}: {count} ({100*count/len(df):.1f}%)")
    return df


def generate_chart_image(weekly, target_date, save_path):
    """Generate a clean 224x224 chart image for the given week."""
    # Get lookback window
    idx = weekly.index.get_indexer([target_date], method='ffill')[0]
    if idx < LOOKBACK_WEEKS:
        return False

    window = weekly.iloc[idx - LOOKBACK_WEEKS + 1: idx + 1].copy()
    if len(window) < LOOKBACK_WEEKS // 2:
        return False

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

    # Candlesticks (vectorized)
    up = closes >= opens
    down = ~up

    # Wicks
    for i in range(len(window)):
        color = '#26A69A' if up[i] else '#EF5350'
        ax_price.plot([x[i], x[i]], [lows[i], highs[i]], color=color, linewidth=0.5)

    # Bodies - use rectangles for speed
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


def generate_all_charts(weekly, labels_df):
    """Generate chart images for all labeled weeks."""
    print("\nGenerating chart images...")
    generated = 0
    skipped = 0

    for _, row in labels_df.iterrows():
        week_date = pd.Timestamp(row['week'], tz='UTC')
        save_path = os.path.join(IMAGE_DIR, f"{row['week']}.png")

        if os.path.exists(save_path):
            generated += 1
            continue

        success = generate_chart_image(weekly, week_date, save_path)
        if success:
            generated += 1
        else:
            skipped += 1

        if generated % 50 == 0:
            print(f"  Generated {generated} images...")

    print(f"Done: {generated} generated, {skipped} skipped")
    return generated


# ============================================================
# DATASET + MODEL
# ============================================================

class ChartDataset(Dataset):
    def __init__(self, labels_df, image_dir, transform=None):
        self.labels = labels_df.reset_index(drop=True)
        self.image_dir = image_dir
        self.transform = transform

        # Filter to only existing images
        valid = []
        for i, row in self.labels.iterrows():
            path = os.path.join(image_dir, f"{row['week']}.png")
            if os.path.exists(path):
                valid.append(i)
        self.labels = self.labels.iloc[valid].reset_index(drop=True)

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        row = self.labels.iloc[idx]
        path = os.path.join(self.image_dir, f"{row['week']}.png")
        img = Image.open(path).convert('RGB').resize((IMAGE_SIZE, IMAGE_SIZE))
        if self.transform:
            img = self.transform(img)
        return img, row['label']


def build_model():
    """ResNet18 pretrained, fine-tune last 2 blocks + FC."""
    model = models.resnet18(weights=models.ResNet18_Weights.IMAGENET1K_V1)

    # Freeze all layers
    for param in model.parameters():
        param.requires_grad = False

    # Unfreeze layer3, layer4, fc
    for name, param in model.named_parameters():
        if 'layer3' in name or 'layer4' in name or 'fc' in name:
            param.requires_grad = True

    # Replace FC
    model.fc = nn.Linear(512, 3)
    return model


def train_model(train_dataset, val_dataset, class_weights):
    """Train the CNN with walk-forward split."""
    model = build_model()

    train_loader = DataLoader(train_dataset, batch_size=16, shuffle=False)  # No shuffle for time series
    val_loader = DataLoader(val_dataset, batch_size=16, shuffle=False)

    weights = torch.FloatTensor(class_weights)
    criterion = nn.CrossEntropyLoss(weight=weights)
    optimizer = torch.optim.Adam(filter(lambda p: p.requires_grad, model.parameters()), lr=0.001)

    train_losses = []
    val_accs = []

    print("\nTraining CNN...")
    for epoch in range(30):
        model.train()
        epoch_loss = 0
        n_batches = 0
        for imgs, labels in train_loader:
            optimizer.zero_grad()
            outputs = model(imgs)
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()
            epoch_loss += loss.item()
            n_batches += 1

        avg_loss = epoch_loss / max(n_batches, 1)
        train_losses.append(avg_loss)

        # Validation accuracy
        model.eval()
        correct = 0
        total = 0
        with torch.no_grad():
            for imgs, labels in val_loader:
                outputs = model(imgs)
                _, predicted = torch.max(outputs, 1)
                total += labels.size(0)
                correct += (predicted == labels).sum().item()

        val_acc = correct / max(total, 1)
        val_accs.append(val_acc)

        if (epoch + 1) % 5 == 0 or epoch == 0:
            print(f"  Epoch {epoch+1:2d}: loss={avg_loss:.4f}, val_acc={val_acc:.3f}")

    return model, train_losses, val_accs


def evaluate_model(model, dataset, labels_df_subset):
    """Full evaluation with confusion matrix and PnL analysis."""
    loader = DataLoader(dataset, batch_size=16, shuffle=False)

    model.eval()
    all_preds = []
    all_probs = []
    all_labels = []

    with torch.no_grad():
        for imgs, labels in loader:
            outputs = model(imgs)
            probs = torch.softmax(outputs, dim=1)
            _, predicted = torch.max(outputs, 1)
            all_preds.extend(predicted.numpy().tolist())
            all_probs.extend(probs.numpy().tolist())
            all_labels.extend(labels.numpy().tolist())

    all_preds = np.array(all_preds)
    all_labels = np.array(all_labels)
    all_probs = np.array(all_probs)

    return all_preds, all_labels, all_probs


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


def pnl_analysis(preds, labels_df_subset, title="PnL Analysis"):
    """Analyze PnL when following CNN predictions."""
    print(f"\n{title}:")
    df = labels_df_subset.copy().reset_index(drop=True)
    df['pred'] = preds

    # When CNN says LONG, what's the actual long PnL?
    long_mask = df.pred == 0
    short_mask = df.pred == 1
    flat_mask = df.pred == 2

    if long_mask.sum() > 0:
        long_pnl = df[long_mask].long_pnl.sum()
        print(f"  CNN predicts LONG  ({long_mask.sum()} weeks): actual long_pnl = ${long_pnl:,.0f}, "
              f"actual short_pnl = ${df[long_mask].short_pnl.sum():,.0f}")
    if short_mask.sum() > 0:
        short_pnl = df[short_mask].short_pnl.sum()
        print(f"  CNN predicts SHORT ({short_mask.sum()} weeks): actual short_pnl = ${short_pnl:,.0f}, "
              f"actual long_pnl = ${df[short_mask].long_pnl.sum():,.0f}")
    if flat_mask.sum() > 0:
        print(f"  CNN predicts FLAT  ({flat_mask.sum()} weeks): avoided long_pnl = ${df[flat_mask].long_pnl.sum():,.0f}, "
              f"avoided short_pnl = ${df[flat_mask].short_pnl.sum():,.0f}")

    # CNN-gated vs ungated
    total_long_pnl = df.long_pnl.sum()
    total_short_pnl = df.short_pnl.sum()
    ungated_total = total_long_pnl + total_short_pnl

    gated_long = df[long_mask].long_pnl.sum() if long_mask.sum() > 0 else 0
    gated_short = df[short_mask].short_pnl.sum() if short_mask.sum() > 0 else 0
    # In FLAT weeks, take neither longs nor shorts
    # In LONG weeks, take only longs (skip shorts)
    # In SHORT weeks, take only shorts (skip longs)
    gated_total = gated_long + gated_short

    print(f"\n  Ungated total PnL: ${ungated_total:,.0f}")
    print(f"  CNN-gated PnL:     ${gated_total:,.0f}")
    if ungated_total != 0:
        print(f"  Improvement:       {100*(gated_total - ungated_total)/abs(ungated_total):+.1f}%")

    return gated_total, ungated_total


def per_year_analysis(preds, labels_df_subset, title="Per-Year Analysis"):
    """Break down predictions and PnL by year."""
    print(f"\n{title}:")
    df = labels_df_subset.copy().reset_index(drop=True)
    df['pred'] = preds
    df['year'] = pd.to_datetime(df['week']).dt.year

    print(f"  {'Year':>6} {'Weeks':>6} {'Acc':>6} {'LONG':>5} {'SHORT':>6} {'FLAT':>5} "
          f"{'Gated PnL':>10} {'Ungated PnL':>12}")
    print(f"  {'-'*68}")

    for year, grp in df.groupby('year'):
        acc = (grp.pred == grp.label).mean()
        n_long = (grp.pred == 0).sum()
        n_short = (grp.pred == 1).sum()
        n_flat = (grp.pred == 2).sum()

        gated = grp[grp.pred == 0].long_pnl.sum() + grp[grp.pred == 1].short_pnl.sum()
        ungated = grp.long_pnl.sum() + grp.short_pnl.sum()

        print(f"  {year:>6} {len(grp):>6} {acc:>6.3f} {n_long:>5} {n_short:>6} {n_flat:>5} "
              f"  ${gated:>9,.0f}   ${ungated:>9,.0f}")


def save_predictions(all_preds, all_probs, labels_df):
    """Save predictions to JSON."""
    results = []
    df = labels_df.reset_index(drop=True)
    for i in range(len(df)):
        row = df.iloc[i]
        pred_cls = int(all_preds[i])
        results.append({
            'week': row['week'],
            'pred': CLASS_NAMES[pred_cls],
            'prob_long': round(float(all_probs[i][0]), 4),
            'prob_short': round(float(all_probs[i][1]), 4),
            'prob_flat': round(float(all_probs[i][2]), 4),
            'actual_long_pnl': row['long_pnl'],
            'actual_short_pnl': row['short_pnl'],
            'actual_label': CLASS_NAMES[row['label']],
        })

    with open(PRED_PATH, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nPredictions saved to {PRED_PATH}")


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 70)
    print("CNN REGIME DETECTOR FOR s523c")
    print("=" * 70)

    # Step 1: Load data
    weekly = load_btc_weekly()
    trades_df = load_trades()

    # Step 2: Label weeks
    labels_df = label_weeks(weekly, trades_df)

    # Step 3: Generate chart images
    n_generated = generate_all_charts(weekly, labels_df)
    print(f"Total images available: {n_generated}")

    # Step 4: Split train/val (walk-forward)
    train_mask = pd.to_datetime(labels_df.week) <= TRAIN_END
    val_mask = pd.to_datetime(labels_df.week) > TRAIN_END
    train_labels = labels_df[train_mask].copy()
    val_labels = labels_df[val_mask].copy()
    print(f"\nTrain: {len(train_labels)} weeks ({train_labels.week.iloc[0]} to {train_labels.week.iloc[-1]})")
    print(f"Val:   {len(val_labels)} weeks ({val_labels.week.iloc[0]} to {val_labels.week.iloc[-1]})")

    # Transforms
    train_transform = transforms.Compose([
        transforms.Resize((IMAGE_SIZE, IMAGE_SIZE)),
        transforms.RandomHorizontalFlip(p=0.5),
        transforms.ColorJitter(brightness=0.1, contrast=0.1),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])
    val_transform = transforms.Compose([
        transforms.Resize((IMAGE_SIZE, IMAGE_SIZE)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])

    train_dataset = ChartDataset(train_labels, IMAGE_DIR, train_transform)
    val_dataset = ChartDataset(val_labels, IMAGE_DIR, val_transform)
    print(f"Train dataset: {len(train_dataset)}, Val dataset: {len(val_dataset)}")

    # Class weights (inverse frequency)
    train_label_counts = Counter(train_labels.label.values)
    total = sum(train_label_counts.values())
    n_classes = 3
    class_weights = [total / (n_classes * train_label_counts.get(c, 1)) for c in range(n_classes)]
    print(f"Class weights: {[f'{w:.2f}' for w in class_weights]}")

    # Step 5: Train
    model, train_losses, val_accs = train_model(train_dataset, val_dataset, class_weights)

    # Save model
    torch.save(model.state_dict(), MODEL_PATH)
    print(f"\nModel saved to {MODEL_PATH}")

    # Print training curve
    print("\nTraining loss curve:")
    for i, loss in enumerate(train_losses):
        bar = '#' * int(loss * 20)
        print(f"  Epoch {i+1:2d}: {loss:.4f} {bar}")

    # Step 6: Evaluate on validation set
    print("\n" + "=" * 70)
    print("VALIDATION SET RESULTS (2025+)")
    print("=" * 70)

    val_preds, val_labels_arr, val_probs = evaluate_model(model, val_dataset, val_labels)
    print_confusion_matrix(val_preds, val_labels_arr, "Validation Confusion Matrix")
    pnl_analysis(val_preds, val_labels[val_labels.week.isin(val_dataset.labels.week.values)],
                 "Validation PnL Analysis (2025)")

    # Step 7: Full dataset evaluation
    print("\n" + "=" * 70)
    print("FULL DATASET RESULTS")
    print("=" * 70)

    full_dataset = ChartDataset(labels_df, IMAGE_DIR, val_transform)
    full_preds, full_labels_arr, full_probs = evaluate_model(model, full_dataset, labels_df)
    print_confusion_matrix(full_preds, full_labels_arr, "Full Dataset Confusion Matrix")

    # Use the dataset's filtered labels for PnL analysis
    full_labels_filtered = labels_df[labels_df.week.isin(full_dataset.labels.week.values)]
    per_year_analysis(full_preds, full_labels_filtered, "Per-Year Prediction Summary")
    pnl_analysis(full_preds, full_labels_filtered, "Full Dataset PnL Analysis")

    # Step 8: Save predictions
    save_predictions(full_preds, full_probs, full_labels_filtered)

    # Step 9: The money question
    print("\n" + "=" * 70)
    print("THE MONEY QUESTION")
    print("=" * 70)
    print("If we only took s523c longs when CNN said LONG,")
    print("and only shorts when CNN said SHORT:")
    per_year_analysis(full_preds, full_labels_filtered,
                      "Per-Year CNN-Gated vs Ungated PnL")

    # Also show val-only money question
    print("\n--- Out-of-sample only (2025, walk-forward) ---")
    val_labels_filtered = val_labels[val_labels.week.isin(val_dataset.labels.week.values)]
    val_gated, val_ungated = pnl_analysis(
        val_preds, val_labels_filtered,
        "2025 OOS: CNN-Gated vs Ungated"
    )

    # Regime timeline
    print("\n\nCNN Regime Timeline (last 20 weeks):")
    fl = full_labels_filtered.reset_index(drop=True)
    fl['pred'] = full_preds
    fl['pred_name'] = [CLASS_NAMES[p] for p in full_preds]
    for _, row in fl.tail(20).iterrows():
        marker = '>>>' if row['pred'] == row['label'] else '   '
        print(f"  {row['week']}  pred={row['pred_name']:>5}  "
              f"actual={CLASS_NAMES[row['label']]:>5}  "
              f"long_pnl=${row['long_pnl']:>8,.0f}  short_pnl=${row['short_pnl']:>8,.0f}  {marker}")

    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)


if __name__ == '__main__':
    main()
