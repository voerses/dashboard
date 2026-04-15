"""
Conditional Exit Probability Surface
Maps: given unrealized PnL at hour H, what is P(trade ends as winner)?
"""
import json
import sys
import numpy as np
import pandas as pd
from pathlib import Path
from collections import defaultdict

# ── Config ──────────────────────────────────────────────────────────────
TRADE_LOG = Path("/workspace/crypto_backtest/results/v4/s524o_clean_baseline_48mo_100k_trades.json")
DATA_DIR = Path("/workspace/crypto_backtest/data/perp/1h_cache")
OUTPUT_JSON = Path("/workspace/crypto_backtest/research/exit_probability_surface.json")

CHECKPOINTS = [1, 4, 8, 24, 48, 72, 120, 168, 240, 336]

PNL_BINS = [-np.inf, -10, -7, -5, -3, -1, 1, 3, 5, 7, 10, np.inf]
PNL_LABELS = ["<-10%", "-10:-7%", "-7:-5%", "-5:-3%", "-3:-1%",
              "-1:+1%", "+1:+3%", "+3:+5%", "+5:+7%", "+7:+10%", ">+10%"]

WR_CUTOFFS = [0.25, 0.20, 0.15]

SIM_MONTHS = 48
SIM_END = pd.Timestamp("2026-04-05 16:00:00")

# ── Load data ───────────────────────────────────────────────────────────
print("Loading BTC index and regime...")
btc = pd.read_parquet(DATA_DIR / "BTC_1h.parquet")
global_idx = btc.index
sim_start = SIM_END - pd.DateOffset(months=SIM_MONTHS)
sim_start_bar = global_idx.get_indexer([sim_start], method="nearest")[0]
print(f"  sim_start={global_idx[sim_start_bar]}  sim_start_bar={sim_start_bar}")

# Bear/bull regime
btc_close = btc["close"].values
sma200 = pd.Series(btc_close).rolling(200 * 24, min_periods=100 * 24).mean().values
bear_mask = btc_close < sma200  # True = bear

print("Loading trade log...")
with open(TRADE_LOG) as f:
    trades_raw = json.load(f)
print(f"  {len(trades_raw)} trades loaded")

# ── Cache token OHLCV ──────────────────────────────────────────────────
token_cache = {}

def get_token_close(token):
    if token not in token_cache:
        p = DATA_DIR / f"{token}_1h.parquet"
        if not p.exists():
            token_cache[token] = None
        else:
            df = pd.read_parquet(p, columns=["close"])
            token_cache[token] = df
    return token_cache[token]

# ── Process each trade ──────────────────────────────────────────────────
print("Processing trades...")
max_checkpoint = max(CHECKPOINTS)

records = []  # list of dicts with: regime_dir, checkpoint->unrealized_pct, is_winner
skipped = 0

for i, t in enumerate(trades_raw):
    token = t["token"]
    direction = int(t["direction"])
    entry_bar_rel = int(t["entry_bar"])
    exit_bar_rel = int(t["exit_bar"])
    entry_price = float(t["entry_price"])
    pnl = float(t["pnl"])
    hold_hours = exit_bar_rel - entry_bar_rel

    # Absolute bar in global index
    abs_entry = sim_start_bar + entry_bar_rel

    # Regime
    if abs_entry >= len(bear_mask):
        skipped += 1
        continue
    is_bear = bool(bear_mask[abs_entry])
    dir_label = "long" if direction == 1 else "short"
    regime_dir = f"{'bear' if is_bear else 'bull'}_{dir_label}"

    # Get token data
    tok_df = get_token_close(token)
    if tok_df is None:
        skipped += 1
        continue

    # Map entry timestamp to token index
    entry_ts = global_idx[abs_entry]
    tok_idx = tok_df.index
    tok_entry_pos = tok_idx.get_indexer([entry_ts], method="nearest")[0]

    tok_close = tok_df["close"].values
    is_winner = pnl > 0

    # Compute unrealized PnL at each checkpoint
    unrealized = {}
    for h in CHECKPOINTS:
        if h > hold_hours:
            break  # trade already exited
        pos = tok_entry_pos + h
        if pos >= len(tok_close):
            break
        price_at_h = tok_close[pos]
        if direction == 1:
            unr = (price_at_h - entry_price) / entry_price * 100
        else:
            unr = (entry_price - price_at_h) / entry_price * 100
        unrealized[h] = unr

    if unrealized:
        records.append({
            "regime_dir": regime_dir,
            "is_winner": is_winner,
            "pnl": pnl,
            "margin_usd": float(t["margin_usd"]),
            "unrealized": unrealized,
            "hold_hours": hold_hours,
        })

    if (i + 1) % 500 == 0:
        print(f"  processed {i+1}/{len(trades_raw)}...")

print(f"  {len(records)} trades with data, {skipped} skipped")

# ── Step 2: Build probability surface ───────────────────────────────────
print("\nBuilding probability surface...")

regime_dirs = ["bear_long", "bear_short", "bull_long", "bull_short"]
surface = {}  # regime_dir -> checkpoint -> bin_label -> {wins, total}

for rd in regime_dirs:
    surface[rd] = {}
    rd_records = [r for r in records if r["regime_dir"] == rd]

    for h in CHECKPOINTS:
        surface[rd][h] = {}
        for label in PNL_LABELS:
            surface[rd][h][label] = {"wins": 0, "total": 0}

        for r in rd_records:
            if h not in r["unrealized"]:
                continue
            unr = r["unrealized"][h]
            # Find bin
            bin_idx = np.searchsorted(PNL_BINS[1:], unr, side="right")
            bin_idx = min(bin_idx, len(PNL_LABELS) - 1)
            label = PNL_LABELS[bin_idx]
            surface[rd][h][label]["total"] += 1
            if r["is_winner"]:
                surface[rd][h][label]["wins"] += 1

# ── Step 3: Print tables ───────────────────────────────────────────────
def fmt_cell(wins, total):
    if total == 0:
        return "   ---   "
    wr = wins / total * 100
    return f"{wr:4.0f}%({total:>3d})"

for rd in regime_dirs:
    print(f"\n{'='*120}")
    print(f"=== {rd.upper()} ===")
    header = f"{'Hour':>5} | " + " | ".join(f"{l:>9}" for l in PNL_LABELS)
    print(header)
    print("-" * len(header))

    for h in CHECKPOINTS:
        cells = []
        for label in PNL_LABELS:
            d = surface[rd][h][label]
            cells.append(fmt_cell(d["wins"], d["total"]))
        print(f"{h:5d} | " + " | ".join(cells))

# ── Step 3b: Exit thresholds ───────────────────────────────────────────
print(f"\n{'='*120}")
print("=== EXIT THRESHOLDS (unrealized PnL % below which win_rate < X%) ===\n")

# For this, we need finer-grained bins to find precise thresholds
# Use the raw data to compute thresholds via sorting
threshold_data = {}  # rd -> cutoff -> h -> threshold_pct

for rd in regime_dirs:
    threshold_data[rd] = {}
    rd_records = [r for r in records if r["regime_dir"] == rd]

    for cutoff in WR_CUTOFFS:
        threshold_data[rd][cutoff] = {}
        for h in CHECKPOINTS:
            # Gather (unrealized, is_winner) pairs
            pairs = []
            for r in rd_records:
                if h in r["unrealized"]:
                    pairs.append((r["unrealized"][h], r["is_winner"]))
            if len(pairs) < 10:
                threshold_data[rd][cutoff][h] = None
                continue

            # Sort by unrealized PnL
            pairs.sort(key=lambda x: x[0])
            # Sliding window: find the threshold where cumulative win_rate crosses cutoff
            # Walk from lowest PnL upward, tracking cumulative win rate
            total_wins = sum(1 for _, w in pairs if w)
            total_n = len(pairs)

            # Find threshold: for trades below threshold, win_rate < cutoff
            # Try each trade's unrealized as a potential threshold
            cum_wins = 0
            cum_n = 0
            threshold = None
            for unr, win in pairs:
                cum_n += 1
                if win:
                    cum_wins += 1
                wr = cum_wins / cum_n
                if wr < cutoff:
                    threshold = unr
                else:
                    # Win rate crossed above cutoff, the threshold is here
                    break

            threshold_data[rd][cutoff][h] = threshold

for cutoff in WR_CUTOFFS:
    print(f"--- Cutoff: win_rate < {cutoff*100:.0f}% ---")
    header = f"{'Regime+Dir':>14} | " + " | ".join(f"Hour {h:>3}" for h in CHECKPOINTS)
    print(header)
    for rd in regime_dirs:
        cells = []
        for h in CHECKPOINTS:
            t = threshold_data[rd][cutoff][h]
            if t is None:
                cells.append(f"{'n/a':>8}")
            else:
                cells.append(f"{t:>+7.1f}%")
        print(f"{rd:>14} | " + " | ".join(cells))
    print()

# ── Step 4: Expected improvement ────────────────────────────────────────
print(f"{'='*120}")
print("=== EXPECTED IMPROVEMENT ===\n")

for cutoff in WR_CUTOFFS:
    print(f"--- {cutoff*100:.0f}% WR cutoff ---")
    print(f"{'Regime+Dir':>14} | {'Hour':>4} | {'Exit early':>10} | {'Avg saved':>10} | "
          f"{'Kept':>5} | {'Avg kept PnL':>12} | {'Actual total':>12} | {'Net delta':>10}")
    print("-" * 110)

    for rd in regime_dirs:
        rd_records = [r for r in records if r["regime_dir"] == rd]
        if not rd_records:
            continue

        actual_total = sum(r["pnl"] for r in rd_records)

        # Find best checkpoint (highest net delta)
        best_delta = -np.inf
        best_row = None

        for h in CHECKPOINTS:
            thr = threshold_data[rd][cutoff].get(h)
            if thr is None:
                continue

            exited = []
            kept = []
            for r in rd_records:
                if h in r["unrealized"] and r["unrealized"][h] <= thr:
                    exited.append(r)
                else:
                    kept.append(r)

            if not exited:
                continue

            # For exited trades: the "saved" amount is the difference between
            # what they actually lost vs what they would lose if exited at hour h
            # Approximate: exited at hour h means unrealized at h becomes realized
            # saved_loss = actual_pnl - (unrealized_at_h / 100 * margin_usd)
            # But simpler: avg final pnl of exited trades (negative) vs avg unrealized
            avg_exited_final_pnl = np.mean([r["pnl"] for r in exited])
            avg_exited_unrealized = np.mean([r["unrealized"][h] / 100 * r["margin_usd"]
                                             for r in exited if h in r["unrealized"]])

            # If we exit at hour h, we get unrealized PnL instead of final PnL
            counterfactual_total = (sum(r["unrealized"][h] / 100 * r["margin_usd"]
                                       for r in exited if h in r["unrealized"])
                                   + sum(r["pnl"] for r in kept))
            net_delta = counterfactual_total - actual_total

            if net_delta > best_delta:
                best_delta = net_delta
                avg_saved = np.mean([(r["unrealized"][h] / 100 * r["margin_usd"] - r["pnl"])
                                     for r in exited if h in r["unrealized"]])
                best_row = {
                    "h": h,
                    "n_exited": len(exited),
                    "avg_saved": avg_saved,
                    "n_kept": len(kept),
                    "avg_kept_pnl": np.mean([r["pnl"] for r in kept]) if kept else 0,
                    "actual_total": actual_total,
                    "net_delta": net_delta,
                }

        if best_row:
            r = best_row
            print(f"{rd:>14} | {r['h']:>4} | {r['n_exited']:>10} | "
                  f"${r['avg_saved']:>+9,.0f} | {r['n_kept']:>5} | "
                  f"${r['avg_kept_pnl']:>+11,.0f} | ${r['actual_total']:>+11,.0f} | "
                  f"${r['net_delta']:>+9,.0f}")
        else:
            print(f"{rd:>14} | no viable exit point found")

    print()

# ── Save JSON ───────────────────────────────────────────────────────────
print("Saving probability surface JSON...")

output = {
    "metadata": {
        "trade_log": str(TRADE_LOG),
        "sim_months": SIM_MONTHS,
        "sim_end": str(SIM_END),
        "n_trades": len(records),
        "checkpoints": CHECKPOINTS,
        "pnl_bins": PNL_LABELS,
    },
    "surface": {},
    "thresholds": {},
    "trade_counts": {},
}

for rd in regime_dirs:
    output["surface"][rd] = {}
    rd_records = [r for r in records if r["regime_dir"] == rd]
    output["trade_counts"][rd] = len(rd_records)

    for h in CHECKPOINTS:
        output["surface"][rd][str(h)] = {}
        for label in PNL_LABELS:
            d = surface[rd][h][label]
            wr = d["wins"] / d["total"] if d["total"] > 0 else None
            output["surface"][rd][str(h)][label] = {
                "win_rate": round(wr, 4) if wr is not None else None,
                "wins": d["wins"],
                "total": d["total"],
            }

    output["thresholds"][rd] = {}
    for cutoff in WR_CUTOFFS:
        key = f"wr_lt_{int(cutoff*100)}"
        output["thresholds"][rd][key] = {}
        for h in CHECKPOINTS:
            t = threshold_data[rd][cutoff].get(h)
            output["thresholds"][rd][key][str(h)] = round(t, 2) if t is not None else None

with open(OUTPUT_JSON, "w") as f:
    json.dump(output, f, indent=2)

print(f"Saved to {OUTPUT_JSON}")
print("\nDone.")
