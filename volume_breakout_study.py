"""
Volume Breakout Signal Study
=============================
Hypothesis: Volume spikes + price breakouts predict continuation (institutional flow).

Signals tested:
  A: vol_ratio > 2.0 AND close > rolling_high_20h
  B: vol_ratio > 2.0 AND ret_1h > 2%
  C: volume_acceleration (4h/24h*6) > 2.0 AND close > ema_20
  D: vol_ratio > 1.5 AND close > donchian_high_48h

OOS split: Train < 2025-07-01, Test >= 2025-07-01
Kill metric: avg forward 24h return > +1.0% with >50 OOS occurrences.
"""

import pandas as pd
import numpy as np
from pathlib import Path
import warnings
warnings.filterwarnings("ignore")

# ── Config ──────────────────────────────────────────────────────────────────
DATA_DIR = Path("/workspace/crypto_backtest/data/perp/1h_cache")
OOS_SPLIT = pd.Timestamp("2025-07-01")
FORWARD_HORIZONS = [4, 8, 24]  # hours
MIN_OOS_COUNT = 50

# Top 20 tokens by file size
TOP20 = [
    "BTC", "ETH", "BNB", "DOGE", "XLM", "SOL", "ADA", "BCH", "TRX", "LINK",
    "AVAX", "ETC", "XRP", "ENJ", "SAND", "NEO", "KAVA", "CHZ", "XMR", "ATOM"
]


# ── Data Loading ────────────────────────────────────────────────────────────
def load_token(token: str) -> pd.DataFrame:
    fp = DATA_DIR / f"{token}_1h.parquet"
    df = pd.read_parquet(fp)[["open", "high", "low", "close", "volume"]]
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add all features needed for signal construction and forward returns."""
    d = df.copy()

    # Volume features
    d["vol_ma20"] = d["volume"].rolling(20, min_periods=15).mean()
    d["vol_ratio"] = d["volume"] / d["vol_ma20"]
    d["vol_4h"] = d["volume"].rolling(4, min_periods=3).mean()
    d["vol_24h"] = d["volume"].rolling(24, min_periods=18).mean()
    d["vol_accel"] = (d["vol_4h"] / d["vol_24h"]) * 6  # normalized acceleration

    # Price features
    d["rolling_high_20"] = d["close"].rolling(20, min_periods=15).max()
    # For breakout, close must exceed the PREVIOUS rolling high (avoid lookahead)
    d["prev_rolling_high_20"] = d["rolling_high_20"].shift(1)
    d["ret_1h"] = d["close"].pct_change(1)
    d["ema_20"] = d["close"].ewm(span=20, min_periods=15).mean()
    d["donchian_high_48"] = d["high"].rolling(48, min_periods=36).max().shift(1)

    # Forward returns (what we're predicting)
    for h in FORWARD_HORIZONS:
        d[f"fwd_{h}h"] = d["close"].pct_change(h).shift(-h)

    return d


def compute_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all four signals. Returns boolean columns."""
    d = df.copy()

    # Signal A: vol_ratio > 2.0 AND close > prev rolling high 20h
    d["sig_A"] = (d["vol_ratio"] > 2.0) & (d["close"] > d["prev_rolling_high_20"])

    # Signal B: vol_ratio > 2.0 AND ret_1h > 2%
    d["sig_B"] = (d["vol_ratio"] > 2.0) & (d["ret_1h"] > 0.02)

    # Signal C: volume acceleration > 2.0 AND close > ema_20
    d["sig_C"] = (d["vol_accel"] > 2.0) & (d["close"] > d["ema_20"])

    # Signal D: vol_ratio > 1.5 AND close > donchian high 48h
    d["sig_D"] = (d["vol_ratio"] > 1.5) & (d["close"] > d["donchian_high_48"])

    return d


# ── Main Analysis ───────────────────────────────────────────────────────────
print("=" * 90)
print("VOLUME BREAKOUT SIGNAL STUDY")
print("=" * 90)

# Load and process all tokens
all_data = {}
for token in TOP20:
    try:
        df = load_token(token)
        df = build_features(df)
        df = compute_signals(df)
        df["token"] = token
        all_data[token] = df
        print(f"  Loaded {token}: {len(df)} rows, {df.index.min().date()} to {df.index.max().date()}")
    except Exception as e:
        print(f"  FAILED {token}: {e}")

# Combine
combined = pd.concat(all_data.values())
print(f"\nTotal rows: {len(combined):,}")
print(f"Train period: before {OOS_SPLIT.date()}")
print(f"Test period:  {OOS_SPLIT.date()} onwards")

train = combined[combined.index < OOS_SPLIT]
test = combined[combined.index >= OOS_SPLIT]
print(f"Train rows: {len(train):,}  |  Test rows: {len(test):,}")


# ── STEP 3: Signal Performance (Train vs Test) ─────────────────────────────
print("\n" + "=" * 90)
print("STEP 3: SIGNAL PERFORMANCE — TRAIN vs TEST")
print("=" * 90)

signals = ["sig_A", "sig_B", "sig_C", "sig_D"]
signal_names = {
    "sig_A": "A: Vol>2x + Price Breakout 20h",
    "sig_B": "B: Vol>2x + Ret>2%",
    "sig_C": "C: Vol Accel>2x + Above EMA20",
    "sig_D": "D: Vol>1.5x + Donchian 48h Break"
}

# Unconditional base rates
print("\n--- Unconditional Base Rates (Average Forward Return) ---")
for period_name, subset in [("TRAIN", train), ("TEST", test)]:
    rates = []
    for h in FORWARD_HORIZONS:
        col = f"fwd_{h}h"
        mean_ret = subset[col].mean() * 100
        rates.append(f"{h}h: {mean_ret:+.4f}%")
    print(f"  {period_name}: {' | '.join(rates)}")

# Per-signal stats
print("\n--- Per-Signal Statistics ---")
header = f"{'Signal':<38} {'Period':<7} {'Count':>7} {'4h':>10} {'8h':>10} {'24h':>10} {'%Pos24h':>8}"
print(header)
print("-" * len(header))

verdicts = {}

for sig in signals:
    for period_name, subset in [("TRAIN", train), ("TEST", test)]:
        mask = subset[sig] == True
        count = mask.sum()
        if count == 0:
            print(f"{signal_names[sig]:<38} {period_name:<7} {count:>7}")
            continue

        sig_data = subset[mask]
        fwd_returns = {}
        for h in FORWARD_HORIZONS:
            col = f"fwd_{h}h"
            mean_ret = sig_data[col].mean() * 100
            fwd_returns[h] = mean_ret

        pct_pos_24h = (sig_data["fwd_24h"] > 0).mean() * 100

        print(f"{signal_names[sig]:<38} {period_name:<7} {count:>7} "
              f"{fwd_returns[4]:>+9.3f}% {fwd_returns[8]:>+9.3f}% {fwd_returns[24]:>+9.3f}% {pct_pos_24h:>7.1f}%")

        if period_name == "TEST":
            # Verdict
            avg_24h = fwd_returns[24]
            if count >= MIN_OOS_COUNT and avg_24h > 1.0:
                verdicts[sig] = "EDGE"
            elif count >= MIN_OOS_COUNT and avg_24h > 0.3:
                verdicts[sig] = "MARGINAL"
            else:
                verdicts[sig] = "NO EDGE"
    print()

print("\n--- VERDICTS ---")
for sig in signals:
    v = verdicts.get(sig, "INSUFFICIENT DATA")
    print(f"  {signal_names[sig]}: {v}")


# ── STEP 4: Per-Month OOS Breakdown ────────────────────────────────────────
print("\n" + "=" * 90)
print("STEP 4: PER-MONTH OOS BREAKDOWN")
print("=" * 90)

test_with_month = test.copy()
test_with_month["year_month"] = test_with_month.index.to_period("M")

for sig in signals:
    print(f"\n--- {signal_names[sig]} ---")
    mask = test_with_month[sig] == True
    sig_data = test_with_month[mask]

    if len(sig_data) == 0:
        print("  No signals fired OOS.")
        continue

    months = sorted(sig_data["year_month"].unique())
    header = f"  {'Month':<12} {'Count':>6} {'4h':>10} {'8h':>10} {'24h':>10} {'%Pos24h':>8} {'Med24h':>10}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    for m in months:
        m_data = sig_data[sig_data["year_month"] == m]
        count = len(m_data)
        row_parts = [f"  {str(m):<12} {count:>6}"]
        for h in FORWARD_HORIZONS:
            col = f"fwd_{h}h"
            mean_ret = m_data[col].mean() * 100
            row_parts.append(f"{mean_ret:>+9.3f}%")
        pct_pos = (m_data["fwd_24h"] > 0).mean() * 100
        med_24h = m_data["fwd_24h"].median() * 100
        row_parts.append(f"{pct_pos:>7.1f}%")
        row_parts.append(f"{med_24h:>+9.3f}%")
        print(" ".join(row_parts))

    # Trend check: compare first half vs second half of OOS
    total_months = len(months)
    if total_months >= 4:
        mid = total_months // 2
        first_half_months = months[:mid]
        second_half_months = months[mid:]

        first_data = sig_data[sig_data["year_month"].isin(first_half_months)]
        second_data = sig_data[sig_data["year_month"].isin(second_half_months)]

        first_ret = first_data["fwd_24h"].mean() * 100
        second_ret = second_data["fwd_24h"].mean() * 100

        decay = "DECAYING" if second_ret < first_ret * 0.5 else (
            "STABLE" if second_ret > first_ret * 0.7 else "WEAKENING"
        )
        print(f"\n  Edge stability: First half avg 24h: {first_ret:+.3f}%, Second half: {second_ret:+.3f}% -> {decay}")


# ── STEP 5: Cross-Token Analysis ───────────────────────────────────────────
print("\n" + "=" * 90)
print("STEP 5: CROSS-TOKEN ANALYSIS (OOS PERIOD)")
print("=" * 90)

for sig in signals:
    print(f"\n--- {signal_names[sig]} ---")
    test_sig = test[test[sig] == True]

    if len(test_sig) == 0:
        print("  No signals.")
        continue

    token_stats = []
    for token in TOP20:
        t_data = test_sig[test_sig["token"] == token]
        if len(t_data) < 3:
            continue

        avg_4h = t_data["fwd_4h"].mean() * 100
        avg_8h = t_data["fwd_8h"].mean() * 100
        avg_24h = t_data["fwd_24h"].mean() * 100
        pct_pos = (t_data["fwd_24h"] > 0).mean() * 100
        count = len(t_data)
        token_stats.append({
            "token": token, "count": count,
            "avg_4h": avg_4h, "avg_8h": avg_8h, "avg_24h": avg_24h,
            "pct_pos": pct_pos
        })

    if not token_stats:
        print("  Insufficient data per token.")
        continue

    token_df = pd.DataFrame(token_stats).sort_values("avg_24h", ascending=False)

    header = f"  {'Token':<8} {'Count':>6} {'4h':>10} {'8h':>10} {'24h':>10} {'%Pos24h':>8}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for _, row in token_df.iterrows():
        print(f"  {row['token']:<8} {row['count']:>6} "
              f"{row['avg_4h']:>+9.3f}% {row['avg_8h']:>+9.3f}% {row['avg_24h']:>+9.3f}% {row['pct_pos']:>7.1f}%")

    # Concentration analysis
    pos_tokens = token_df[token_df["avg_24h"] > 0]
    neg_tokens = token_df[token_df["avg_24h"] <= 0]
    print(f"\n  Tokens with positive 24h avg: {len(pos_tokens)}/{len(token_df)} "
          f"({len(pos_tokens)/len(token_df)*100:.0f}%)")

    if len(pos_tokens) > 0:
        top3 = token_df.head(3)
        top3_count = top3["count"].sum()
        total_count = token_df["count"].sum()
        print(f"  Top 3 tokens contribute {top3_count}/{total_count} signals "
              f"({top3_count/total_count*100:.0f}% of volume)")


# ── FINAL SUMMARY ──────────────────────────────────────────────────────────
print("\n" + "=" * 90)
print("FINAL SUMMARY TABLE")
print("=" * 90)

# Get unconditional test base rates
base_rates = {}
for h in FORWARD_HORIZONS:
    base_rates[h] = test[f"fwd_{h}h"].mean() * 100

print(f"\nUnconditional OOS base rate: 4h={base_rates[4]:+.4f}%, "
      f"8h={base_rates[8]:+.4f}%, 24h={base_rates[24]:+.4f}%")

print(f"\n{'Signal':<38} {'Train24h':>10} {'Test24h':>10} {'OOScount':>9} "
      f"{'vs Base':>10} {'Verdict':<12}")
print("-" * 100)

for sig in signals:
    # Train stats
    train_mask = train[sig] == True
    train_24h = train[train_mask]["fwd_24h"].mean() * 100 if train_mask.sum() > 0 else float("nan")

    # Test stats
    test_mask = test[sig] == True
    test_count = test_mask.sum()
    test_24h = test[test_mask]["fwd_24h"].mean() * 100 if test_count > 0 else float("nan")

    excess = test_24h - base_rates[24] if not np.isnan(test_24h) else float("nan")
    verdict = verdicts.get(sig, "N/A")

    print(f"{signal_names[sig]:<38} {train_24h:>+9.3f}% {test_24h:>+9.3f}% {test_count:>9} "
          f"{excess:>+9.3f}% {verdict:<12}")

print(f"\nKill metric: avg forward 24h return > +1.0% with >50 OOS occurrences")
print(f"Signals passing kill metric: ", end="")
passing = [signal_names[s] for s in signals if verdicts.get(s) == "EDGE"]
print(", ".join(passing) if passing else "NONE")

# Statistical significance check via bootstrap
print("\n" + "=" * 90)
print("BOOTSTRAP SIGNIFICANCE TEST (OOS, 10000 resamples)")
print("=" * 90)

np.random.seed(42)
N_BOOT = 10_000

for sig in signals:
    test_mask = test[sig] == True
    test_count = test_mask.sum()
    if test_count < 20:
        print(f"\n{signal_names[sig]}: Insufficient OOS signals ({test_count})")
        continue

    sig_returns = test[test_mask]["fwd_24h"].dropna().values
    n = len(sig_returns)

    # Bootstrap mean
    boot_means = np.array([
        np.random.choice(sig_returns, size=n, replace=True).mean()
        for _ in range(N_BOOT)
    ])

    ci_lo = np.percentile(boot_means, 2.5) * 100
    ci_hi = np.percentile(boot_means, 97.5) * 100
    mean_val = sig_returns.mean() * 100
    pct_above_zero = (boot_means > 0).mean() * 100

    print(f"\n{signal_names[sig]}:")
    print(f"  Mean 24h return: {mean_val:+.3f}%")
    print(f"  95% CI: [{ci_lo:+.3f}%, {ci_hi:+.3f}%]")
    print(f"  P(mean > 0): {pct_above_zero:.1f}%")
    print(f"  N = {n}")

print("\n" + "=" * 90)
print("STUDY COMPLETE")
print("=" * 90)
