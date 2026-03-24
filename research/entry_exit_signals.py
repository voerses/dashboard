"""
Entry/Exit Signal Discovery — 9 Signal Families
Tests on BTC 1h, then generalizes to ETH, SOL, BNB.

Signals:
  A. Mean-Reversion: BB squeeze breakout, RSI divergence, Volume spike reversal
  B. Momentum: Dual TF momentum, Consolidation breakout, Funding extreme
  C. Exits: Volume exhaustion, Regime change, Volatility expansion
"""

import pandas as pd
import numpy as np
from scipy.stats import spearmanr
import warnings, json, os
warnings.filterwarnings("ignore")

DATA_DIR = "/workspace/crypto_backtest/data/perp/1h_cache"
OUT_PATH = "/workspace/crypto_backtest/research/entry_exit_signal_results.md"

# ── Helpers ──────────────────────────────────────────────────────────────────

def load_1h(symbol: str) -> pd.DataFrame:
    path = f"{DATA_DIR}/{symbol}_1h.parquet"
    df = pd.read_parquet(path)
    df = df.sort_index()
    # Ensure numeric
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close", "volume"])
    return df


def resample_4h(df: pd.DataFrame) -> pd.DataFrame:
    """Resample 1h bars to 4h bars."""
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    df4 = df.resample("4h").agg(agg).dropna()
    return df4


def forward_returns(df: pd.DataFrame, horizons: list[int] = [4, 8, 24, 72]) -> pd.DataFrame:
    """Compute forward returns at various bar horizons (1h bars)."""
    fwd = pd.DataFrame(index=df.index)
    for h in horizons:
        fwd[f"fwd_{h}h"] = df["close"].shift(-h) / df["close"] - 1
    return fwd


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast).mean()
    ema_slow = series.ewm(span=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal).mean()
    return macd_line, signal_line


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def bollinger_bands(series: pd.Series, period: int = 20, num_std: float = 2.0):
    sma = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = sma + num_std * std
    lower = sma - num_std * std
    width = (upper - lower) / sma
    return upper, lower, width


def split_is_oos(df: pd.DataFrame, is_frac: float = 0.6):
    n = len(df)
    cutoff = int(n * is_frac)
    return df.iloc[:cutoff], df.iloc[cutoff:]


def evaluate_signal(
    entries: pd.DataFrame,  # must have 'direction' (+1/-1) column
    fwd: pd.DataFrame,
    horizons: list[str],
    label: str,
) -> dict:
    """Evaluate an entry signal using forward returns."""
    results = {"signal": label}

    # Merge
    merged = entries.join(fwd, how="inner")
    if len(merged) < 10:
        results["status"] = "SKIP: <10 trades"
        return results

    results["n_trades"] = len(merged)
    results["n_long"] = int((merged["direction"] == 1).sum())
    results["n_short"] = int((merged["direction"] == -1).sum())

    for h in horizons:
        col = h  # e.g. "fwd_4h"
        if col not in merged.columns:
            continue

        # Directional return: direction * forward return
        dir_ret = merged["direction"] * merged[col]
        dir_ret = dir_ret.dropna()

        if len(dir_ret) < 10:
            continue

        hit_rate = (dir_ret > 0).mean()
        avg_profit = dir_ret.mean()
        wins = dir_ret[dir_ret > 0]
        losses = dir_ret[dir_ret <= 0]
        gross_profit = wins.sum() if len(wins) > 0 else 0
        gross_loss = abs(losses.sum()) if len(losses) > 0 else 1e-10
        pf = gross_profit / gross_loss if gross_loss > 0 else 999.0

        # IC: spearman correlation between signal strength and forward return
        if "strength" in merged.columns:
            valid = merged[["strength", col]].dropna()
            if len(valid) > 10:
                ic, _ = spearmanr(valid["strength"], valid[col])
            else:
                ic = np.nan
        else:
            ic = np.nan

        results[f"{col}_hit"] = round(hit_rate, 4)
        results[f"{col}_avg"] = round(avg_profit, 6)
        results[f"{col}_pf"] = round(pf, 3)
        results[f"{col}_ic"] = round(ic, 4) if not np.isnan(ic) else None

    return results


def kill_check(is_res: dict, oos_res: dict, horizon: str = "fwd_24h") -> tuple[bool, str]:
    """Return (alive, reason)."""
    h = horizon

    # Need enough trades
    if is_res.get("n_trades", 0) < 50:
        return False, f"IS trades={is_res.get('n_trades', 0)}<50"
    if oos_res.get("n_trades", 0) < 20:
        return False, f"OOS trades={oos_res.get('n_trades', 0)}<20"

    is_hit = is_res.get(f"{h}_hit")
    oos_hit = oos_res.get(f"{h}_hit")
    is_pf = is_res.get(f"{h}_pf")
    oos_pf = oos_res.get(f"{h}_pf")
    is_avg = is_res.get(f"{h}_avg", 0)
    oos_avg = oos_res.get(f"{h}_avg", 0)

    if is_hit is None or oos_hit is None:
        return False, "Missing metrics"

    reasons = []
    if oos_hit < 0.48:
        reasons.append(f"OOS hit={oos_hit:.3f}<0.48")
    if oos_pf is not None and oos_pf < 1.0:
        reasons.append(f"OOS PF={oos_pf:.2f}<1.0")

    # Sign flip: IS positive but OOS negative
    if is_avg > 0 and oos_avg < 0:
        reasons.append(f"Sign flip: IS avg={is_avg:.5f}, OOS avg={oos_avg:.5f}")

    if reasons:
        return False, "; ".join(reasons)
    return True, "PASS"


def get_trade_examples(entries: pd.DataFrame, df: pd.DataFrame, fwd: pd.DataFrame, n: int = 5) -> list[dict]:
    """Get first n trade examples with entry price and forward returns."""
    merged = entries.join(fwd, how="inner").join(df[["close"]], how="inner", rsuffix="_price")
    examples = []
    for i, (idx, row) in enumerate(merged.head(n).iterrows()):
        ex = {
            "time": str(idx),
            "direction": "LONG" if row["direction"] == 1 else "SHORT",
            "entry_price": round(row["close"], 2),
        }
        for col in ["fwd_4h", "fwd_8h", "fwd_24h", "fwd_72h"]:
            if col in row and not pd.isna(row[col]):
                ex[col] = f"{row[col]*100:.3f}%"
        examples.append(ex)
    return examples


# ── Signal Generators ────────────────────────────────────────────────────────

def signal_1_bb_squeeze_breakout(df: pd.DataFrame) -> pd.DataFrame:
    """BB squeeze breakout: BB width < 20th pctl for 24 bars, then break outside."""
    upper, lower, width = bollinger_bands(df["close"], period=20, num_std=2.0)

    # Rolling 20th percentile of BB width over trailing 200 bars
    width_pctl = width.rolling(200).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)

    # Squeeze: width in bottom 20th percentile for >= 24 consecutive bars
    squeeze = (width_pctl < 0.20).astype(int)
    # Count consecutive squeeze bars
    streak = squeeze.copy()
    for i in range(1, len(streak)):
        if streak.iloc[i] == 1:
            streak.iloc[i] = streak.iloc[i-1] + 1
        else:
            streak.iloc[i] = 0

    was_squeezed = streak.shift(1) >= 24  # previous bar was in extended squeeze

    # Breakout: price crosses above upper or below lower
    break_up = (df["close"] > upper) & was_squeezed
    break_down = (df["close"] < lower) & was_squeezed

    entries = pd.DataFrame(index=df.index)
    entries["direction"] = 0
    entries.loc[break_up, "direction"] = 1
    entries.loc[break_down, "direction"] = -1
    entries["strength"] = width_pctl.shift(1)  # how tight the squeeze was

    return entries[entries["direction"] != 0].copy()


def signal_2_rsi_divergence(df: pd.DataFrame) -> pd.DataFrame:
    """RSI divergence on 4h bars: price new high + RSI lower = bearish, and vice versa."""
    df4 = resample_4h(df)
    rsi_4h = rsi(df4["close"], period=14)

    lookback = 20  # 4h bars (80 hours)

    # Rolling highs/lows
    price_high = df4["close"].rolling(lookback).max()
    price_low = df4["close"].rolling(lookback).min()
    rsi_at_price_high = rsi_4h.copy()
    rsi_at_price_low = rsi_4h.copy()

    # Bearish divergence: price makes new high, RSI doesn't
    new_high = df4["close"] >= price_high
    rsi_high_prev = rsi_4h.rolling(lookback).max()
    bearish_div = new_high & (rsi_4h < rsi_high_prev * 0.95) & (rsi_4h > 50)

    # Bullish divergence: price makes new low, RSI doesn't
    new_low = df4["close"] <= price_low
    rsi_low_prev = rsi_4h.rolling(lookback).min()
    bullish_div = new_low & (rsi_4h > rsi_low_prev * 1.05) & (rsi_4h < 50)

    entries = pd.DataFrame(index=df4.index)
    entries["direction"] = 0
    entries.loc[bearish_div, "direction"] = -1
    entries.loc[bullish_div, "direction"] = 1
    entries["strength"] = abs(rsi_4h - 50) / 50  # normalized distance from 50

    result = entries[entries["direction"] != 0].copy()
    # Reindex to 1h to align with forward returns
    result = result.reindex(df.index, method="ffill", limit=3)
    # Only keep the first bar after signal
    result = result[result["direction"] != 0]
    # Deduplicate: keep only first occurrence in each 4h window
    result = result[~result.index.duplicated(keep="first")]
    # Actually deduplicate properly — only keep signals that are new
    result["is_new"] = result["direction"].diff().fillna(1) != 0
    result = result[result["is_new"]].drop(columns=["is_new"])

    return result


def signal_3_volume_spike_reversal(df: pd.DataFrame) -> pd.DataFrame:
    """Volume > 3x 20-period avg + reversal candle (wick > 60% of range)."""
    vol_ma = df["volume"].rolling(20).mean()
    vol_spike = df["volume"] > 3 * vol_ma

    body = abs(df["close"] - df["open"])
    full_range = df["high"] - df["low"]
    full_range = full_range.replace(0, np.nan)

    # Long signal: long lower wick (bullish reversal)
    lower_wick = pd.concat([df["open"], df["close"]], axis=1).min(axis=1) - df["low"]
    lower_wick_pct = lower_wick / full_range

    # Short signal: long upper wick (bearish reversal)
    upper_wick = df["high"] - pd.concat([df["open"], df["close"]], axis=1).max(axis=1)
    upper_wick_pct = upper_wick / full_range

    long_signal = vol_spike & (lower_wick_pct > 0.6)
    short_signal = vol_spike & (upper_wick_pct > 0.6)

    entries = pd.DataFrame(index=df.index)
    entries["direction"] = 0
    entries.loc[long_signal, "direction"] = 1
    entries.loc[short_signal, "direction"] = -1
    entries["strength"] = (df["volume"] / vol_ma).clip(upper=10) / 10  # normalized volume spike

    return entries[entries["direction"] != 0].copy()


def signal_4_dual_tf_momentum(df: pd.DataFrame) -> pd.DataFrame:
    """4h RSI > 60 AND 1h MACD crosses above signal → long. Reverse for short."""
    df4 = resample_4h(df)
    rsi_4h = rsi(df4["close"], period=14)

    # Reindex 4h RSI to 1h (forward fill)
    rsi_4h_1h = rsi_4h.reindex(df.index, method="ffill")

    # 1h MACD
    macd_line, signal_line = macd(df["close"])
    macd_cross_up = (macd_line > signal_line) & (macd_line.shift(1) <= signal_line.shift(1))
    macd_cross_down = (macd_line < signal_line) & (macd_line.shift(1) >= signal_line.shift(1))

    long_signal = (rsi_4h_1h > 60) & macd_cross_up
    short_signal = (rsi_4h_1h < 40) & macd_cross_down

    entries = pd.DataFrame(index=df.index)
    entries["direction"] = 0
    entries.loc[long_signal, "direction"] = 1
    entries.loc[short_signal, "direction"] = -1
    entries["strength"] = abs(rsi_4h_1h - 50) / 50

    return entries[entries["direction"] != 0].copy()


def signal_5_consolidation_breakout(df: pd.DataFrame) -> pd.DataFrame:
    """48h range < ATR(20) * 0.5, then break above/below."""
    atr_20 = atr(df, period=20)

    # 48-bar rolling range
    range_48 = df["high"].rolling(48).max() - df["low"].rolling(48).min()
    range_high = df["high"].rolling(48).max()
    range_low = df["low"].rolling(48).min()

    consolidating = range_48 < atr_20 * 0.5 * 48  # scale ATR to 48-bar range
    # Actually, ATR is per-bar. Range for 48 bars vs per-bar ATR doesn't compare well.
    # Better: range / 48 < ATR * 0.5 (average per-bar range is less than half ATR)
    avg_bar_range = range_48 / 48
    tight_consolidation = avg_bar_range < atr_20 * 0.5

    was_consolidating = tight_consolidation.shift(1).fillna(False)

    break_up = (df["close"] > range_high.shift(1)) & was_consolidating
    break_down = (df["close"] < range_low.shift(1)) & was_consolidating

    entries = pd.DataFrame(index=df.index)
    entries["direction"] = 0
    entries.loc[break_up, "direction"] = 1
    entries.loc[break_down, "direction"] = -1
    entries["strength"] = (atr_20 / avg_bar_range).clip(upper=5) / 5  # tighter = stronger

    result = entries[entries["direction"] != 0].copy()
    # Deduplicate: min 4h between signals
    if len(result) > 0:
        min_gap = pd.Timedelta(hours=4)
        keep = [True]
        for i in range(1, len(result)):
            if result.index[i] - result.index[keep[-1] and i-1 or 0] >= min_gap:
                keep.append(True)
            else:
                keep.append(False)
        # Proper dedup
        keep_idx = []
        last_kept = result.index[0]
        keep_idx.append(0)
        for i in range(1, len(result)):
            if result.index[i] - last_kept >= min_gap:
                keep_idx.append(i)
                last_kept = result.index[i]
        result = result.iloc[keep_idx]

    return result


def signal_6_funding_extreme(df: pd.DataFrame) -> pd.DataFrame:
    """Funding rate extreme: >0.05% → short, <-0.03% → long."""
    if "funding_rate" not in df.columns:
        return pd.DataFrame(columns=["direction", "strength"])

    fr = df["funding_rate"].copy()

    long_signal = fr < -0.0003   # < -0.03%
    short_signal = fr > 0.0005   # > 0.05%

    entries = pd.DataFrame(index=df.index)
    entries["direction"] = 0
    entries.loc[long_signal, "direction"] = 1
    entries.loc[short_signal, "direction"] = -1
    entries["strength"] = abs(fr) * 1000  # scale for readability

    result = entries[entries["direction"] != 0].copy()

    # Deduplicate: at least 8h between funding signals
    if len(result) > 0:
        min_gap = pd.Timedelta(hours=8)
        keep_idx = [0]
        last_kept = result.index[0]
        for i in range(1, len(result)):
            if result.index[i] - last_kept >= min_gap:
                keep_idx.append(i)
                last_kept = result.index[i]
        result = result.iloc[keep_idx]

    return result


# ── Exit Signal Evaluators ───────────────────────────────────────────────────

def exit_signal_7_volume_exhaustion(df: pd.DataFrame) -> pd.Series:
    """Volume declining 3+ bars while price continues in direction → exit flag."""
    vol_declining = (
        (df["volume"] < df["volume"].shift(1)) &
        (df["volume"].shift(1) < df["volume"].shift(2)) &
        (df["volume"].shift(2) < df["volume"].shift(3))
    )
    # Price still trending up
    price_up = df["close"] > df["close"].shift(3)
    price_down = df["close"] < df["close"].shift(3)

    # Exit long when volume declining + price still up (exhaustion)
    # Exit short when volume declining + price still down
    exit_long = vol_declining & price_up
    exit_short = vol_declining & price_down

    exit_flag = pd.Series(0, index=df.index)
    exit_flag[exit_long] = 1   # exit longs
    exit_flag[exit_short] = -1  # exit shorts
    return exit_flag


def exit_signal_8_regime_change(df: pd.DataFrame) -> pd.Series:
    """20-period EMA slope flips sign → exit."""
    ema20 = df["close"].ewm(span=20).mean()
    slope = ema20 - ema20.shift(1)
    slope_flip = (slope * slope.shift(1)) < 0  # sign changed

    # Direction of the flip tells us what to exit
    exit_flag = pd.Series(0, index=df.index)
    # Slope went from positive to negative → exit longs
    exit_flag[(slope < 0) & (slope.shift(1) > 0)] = 1
    # Slope went from negative to positive → exit shorts
    exit_flag[(slope > 0) & (slope.shift(1) < 0)] = -1
    return exit_flag


def exit_signal_9_vol_expansion(df: pd.DataFrame) -> pd.Series:
    """ATR(14) > 2x ATR(14) from 48h ago → exit."""
    atr_14 = atr(df, period=14)
    atr_48h_ago = atr_14.shift(48)
    vol_expansion = atr_14 > 2 * atr_48h_ago

    exit_flag = pd.Series(0, index=df.index)
    exit_flag[vol_expansion] = 1  # exit all positions
    return exit_flag


def evaluate_exit_signal(
    exit_flags: pd.Series,
    df: pd.DataFrame,
    horizons: list[int] = [4, 8, 24],
    label: str = "",
) -> dict:
    """
    Evaluate an exit signal by comparing 'exit at signal' vs 'hold through'.
    For each exit flag bar, look at forward returns after exiting vs holding.
    If exiting avoids drawdown, the signal is useful.
    """
    results = {"signal": label}

    triggered = exit_flags[exit_flags != 0].index
    results["n_triggers"] = len(triggered)

    if len(triggered) < 20:
        results["status"] = f"SKIP: {len(triggered)}<20 triggers"
        return results

    for h in horizons:
        # Forward return if you HOLD through
        fwd_hold = df["close"].shift(-h) / df["close"] - 1

        # At exit signal bars, what happens if you hold?
        hold_returns = fwd_hold.loc[triggered].dropna()

        if len(hold_returns) < 10:
            continue

        # If exit is flagged and forward return is negative → exit was good (for longs)
        # We measure: avg absolute forward return at exit triggers
        # If avg is negative → exiting longs was correct (avoided loss)
        avg_fwd = hold_returns.mean()
        pct_negative = (hold_returns < 0).mean()

        results[f"hold_{h}h_avg"] = round(avg_fwd, 6)
        results[f"hold_{h}h_pct_neg"] = round(pct_negative, 4)
        # "Value of exit" = how much you avoid by exiting
        # If avg forward return is negative, exit saves you that amount
        results[f"exit_value_{h}h"] = round(-avg_fwd, 6)

    return results


# ── Main Test Loop ───────────────────────────────────────────────────────────

def run_all_signals():
    symbols = ["BTC", "ETH", "SOL", "BNB"]
    horizons = ["fwd_4h", "fwd_8h", "fwd_24h", "fwd_72h"]

    entry_signals = {
        "1_BB_Squeeze_Breakout": signal_1_bb_squeeze_breakout,
        "2_RSI_Divergence": signal_2_rsi_divergence,
        "3_Volume_Spike_Reversal": signal_3_volume_spike_reversal,
        "4_Dual_TF_Momentum": signal_4_dual_tf_momentum,
        "5_Consolidation_Breakout": signal_5_consolidation_breakout,
        "6_Funding_Extreme": signal_6_funding_extreme,
    }

    all_results = []
    all_examples = []
    exit_results = []

    for sym in symbols:
        print(f"\n{'='*60}")
        print(f"Processing {sym}")
        print(f"{'='*60}")

        df = load_1h(sym)
        fwd = forward_returns(df)

        df_is, df_oos = split_is_oos(df)
        fwd_is, fwd_oos = split_is_oos(fwd)

        print(f"  Total bars: {len(df)}, IS: {len(df_is)}, OOS: {len(df_oos)}")

        # ── Entry Signals ──
        for name, signal_fn in entry_signals.items():
            print(f"\n  Signal: {name}")
            try:
                entries = signal_fn(df)
                print(f"    Total entries: {len(entries)}")

                if len(entries) == 0:
                    all_results.append({
                        "symbol": sym, "signal": name,
                        "status": "NO ENTRIES", "n_trades": 0,
                    })
                    continue

                # Split entries IS/OOS
                entries_is = entries[entries.index < df_oos.index[0]]
                entries_oos = entries[entries.index >= df_oos.index[0]]

                print(f"    IS entries: {len(entries_is)}, OOS entries: {len(entries_oos)}")

                is_res = evaluate_signal(entries_is, fwd_is, horizons, f"{name}_IS")
                oos_res = evaluate_signal(entries_oos, fwd_oos, horizons, f"{name}_OOS")

                # Kill check
                alive, reason = kill_check(is_res, oos_res)

                result = {
                    "symbol": sym,
                    "signal": name,
                    "n_trades_is": is_res.get("n_trades", 0),
                    "n_trades_oos": oos_res.get("n_trades", 0),
                    "n_long_is": is_res.get("n_long", 0),
                    "n_short_is": is_res.get("n_short", 0),
                    "alive": alive,
                    "kill_reason": reason,
                }

                for h in horizons:
                    for prefix, res in [("is", is_res), ("oos", oos_res)]:
                        result[f"{prefix}_{h}_hit"] = res.get(f"{h}_hit")
                        result[f"{prefix}_{h}_avg"] = res.get(f"{h}_avg")
                        result[f"{prefix}_{h}_pf"] = res.get(f"{h}_pf")
                        result[f"{prefix}_{h}_ic"] = res.get(f"{h}_ic")

                all_results.append(result)

                # Trade examples (BTC only)
                if sym == "BTC" and len(entries) > 0:
                    examples = get_trade_examples(entries, df, fwd)
                    all_examples.append({"signal": name, "examples": examples})

                status = "ALIVE" if alive else f"KILLED ({reason})"
                print(f"    Status: {status}")
                if oos_res.get("fwd_24h_hit"):
                    print(f"    OOS 24h: hit={oos_res['fwd_24h_hit']:.3f}, pf={oos_res.get('fwd_24h_pf', 'N/A')}, avg={oos_res.get('fwd_24h_avg', 'N/A')}")

            except Exception as e:
                print(f"    ERROR: {e}")
                import traceback
                traceback.print_exc()
                all_results.append({
                    "symbol": sym, "signal": name,
                    "status": f"ERROR: {str(e)}", "n_trades_is": 0,
                })

        # ── Exit Signals (BTC only for full eval, quick check on alts) ──
        print(f"\n  Exit Signals:")
        exit_fns = {
            "7_Volume_Exhaustion": exit_signal_7_volume_exhaustion,
            "8_Regime_Change": exit_signal_8_regime_change,
            "9_Volatility_Expansion": exit_signal_9_vol_expansion,
        }

        for name, exit_fn in exit_fns.items():
            try:
                flags = exit_fn(df)
                flags_is = flags[flags.index < df_oos.index[0]]
                flags_oos = flags[flags.index >= df_oos.index[0]]

                is_res = evaluate_exit_signal(flags_is, df_is, label=f"{name}_IS")
                oos_res = evaluate_exit_signal(flags_oos, df_oos, label=f"{name}_OOS")

                exit_result = {
                    "symbol": sym,
                    "signal": name,
                    "n_triggers_is": is_res.get("n_triggers", 0),
                    "n_triggers_oos": oos_res.get("n_triggers", 0),
                }

                for h in [4, 8, 24]:
                    for prefix, res in [("is", is_res), ("oos", oos_res)]:
                        exit_result[f"{prefix}_hold_{h}h_avg"] = res.get(f"hold_{h}h_avg")
                        exit_result[f"{prefix}_hold_{h}h_pct_neg"] = res.get(f"hold_{h}h_pct_neg")
                        exit_result[f"{prefix}_exit_value_{h}h"] = res.get(f"exit_value_{h}h")

                exit_results.append(exit_result)

                print(f"    {name}: IS triggers={is_res.get('n_triggers',0)}, OOS triggers={oos_res.get('n_triggers',0)}")
                if oos_res.get("hold_24h_avg") is not None:
                    print(f"      OOS 24h: hold_avg={oos_res['hold_24h_avg']:.5f}, pct_neg={oos_res.get('hold_24h_pct_neg','N/A')}")

            except Exception as e:
                print(f"    {name} ERROR: {e}")
                import traceback
                traceback.print_exc()

    return all_results, all_examples, exit_results


def write_report(all_results, all_examples, exit_results):
    """Write markdown report."""
    lines = []
    lines.append("# Entry/Exit Signal Discovery Results")
    lines.append(f"\nGenerated: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    # ── Summary table ──
    lines.append("## 1. Entry Signal Summary (24h horizon)")
    lines.append("")
    lines.append("| Signal | Symbol | IS Trades | OOS Trades | IS Hit | OOS Hit | IS PF | OOS PF | IS Avg | OOS Avg | Status |")
    lines.append("|--------|--------|-----------|------------|--------|---------|-------|--------|--------|---------|--------|")

    for r in sorted(all_results, key=lambda x: (x["signal"], x["symbol"])):
        sig = r.get("signal", "?")
        sym = r.get("symbol", "?")
        n_is = r.get("n_trades_is", 0)
        n_oos = r.get("n_trades_oos", 0)

        is_hit = r.get("is_fwd_24h_hit")
        oos_hit = r.get("oos_fwd_24h_hit")
        is_pf = r.get("is_fwd_24h_pf")
        oos_pf = r.get("oos_fwd_24h_pf")
        is_avg = r.get("is_fwd_24h_avg")
        oos_avg = r.get("oos_fwd_24h_avg")
        alive = r.get("alive", False)
        reason = r.get("kill_reason", r.get("status", ""))

        status = "ALIVE" if alive else f"KILLED"

        fmt = lambda v, d=4: f"{v:.{d}f}" if v is not None else "N/A"

        lines.append(f"| {sig} | {sym} | {n_is} | {n_oos} | {fmt(is_hit)} | {fmt(oos_hit)} | {fmt(is_pf, 2)} | {fmt(oos_pf, 2)} | {fmt(is_avg, 5)} | {fmt(oos_avg, 5)} | {status} |")

    # ── Multi-horizon detail for BTC ──
    lines.append("")
    lines.append("## 2. Multi-Horizon Detail (BTC)")
    lines.append("")

    btc_results = [r for r in all_results if r.get("symbol") == "BTC"]
    for r in btc_results:
        sig = r.get("signal", "?")
        lines.append(f"### {sig}")
        lines.append(f"- IS trades: {r.get('n_trades_is', 0)} (Long: {r.get('n_long_is', 0)}, Short: {r.get('n_short_is', 0)})")
        lines.append(f"- OOS trades: {r.get('n_trades_oos', 0)}")
        lines.append(f"- Status: {'ALIVE' if r.get('alive') else 'KILLED'} — {r.get('kill_reason', r.get('status', ''))}")
        lines.append("")

        lines.append("| Horizon | IS Hit | OOS Hit | IS PF | OOS PF | IS Avg Ret | OOS Avg Ret | IS IC | OOS IC |")
        lines.append("|---------|--------|---------|-------|--------|------------|-------------|-------|--------|")

        for h in ["fwd_4h", "fwd_8h", "fwd_24h", "fwd_72h"]:
            fmt = lambda v, d=4: f"{v:.{d}f}" if v is not None else "N/A"
            lines.append(
                f"| {h} | {fmt(r.get(f'is_{h}_hit'))} | {fmt(r.get(f'oos_{h}_hit'))} | "
                f"{fmt(r.get(f'is_{h}_pf'), 2)} | {fmt(r.get(f'oos_{h}_pf'), 2)} | "
                f"{fmt(r.get(f'is_{h}_avg'), 5)} | {fmt(r.get(f'oos_{h}_avg'), 5)} | "
                f"{fmt(r.get(f'is_{h}_ic'))} | {fmt(r.get(f'oos_{h}_ic'))} |"
            )
        lines.append("")

    # ── Trade Examples ──
    lines.append("## 3. Trade Examples (BTC, first 5 trades)")
    lines.append("")
    for ex_group in all_examples:
        lines.append(f"### {ex_group['signal']}")
        if not ex_group["examples"]:
            lines.append("No trades generated.")
            lines.append("")
            continue

        lines.append("| Time | Dir | Entry Price | 4h Ret | 8h Ret | 24h Ret | 72h Ret |")
        lines.append("|------|-----|-------------|--------|--------|---------|---------|")
        for ex in ex_group["examples"]:
            lines.append(
                f"| {ex.get('time', '')} | {ex.get('direction', '')} | "
                f"{ex.get('entry_price', '')} | {ex.get('fwd_4h', 'N/A')} | "
                f"{ex.get('fwd_8h', 'N/A')} | {ex.get('fwd_24h', 'N/A')} | "
                f"{ex.get('fwd_72h', 'N/A')} |"
            )
        lines.append("")

    # ── Exit Signals ──
    lines.append("## 4. Exit Signal Results")
    lines.append("")
    lines.append("Exit signals measure: if you EXIT when the signal fires, how much drawdown do you avoid?")
    lines.append("- `hold_avg`: average forward return if you HOLD through (negative = exit was correct)")
    lines.append("- `exit_value`: = -hold_avg (positive = exit saves this much)")
    lines.append("- `pct_neg`: fraction of times holding through resulted in negative return")
    lines.append("")

    lines.append("| Signal | Symbol | IS Triggers | OOS Triggers | OOS Hold 4h Avg | OOS Hold 24h Avg | OOS Pct Neg 24h | OOS Exit Value 24h |")
    lines.append("|--------|--------|-------------|--------------|-----------------|------------------|-----------------|-------------------|")

    for r in sorted(exit_results, key=lambda x: (x["signal"], x["symbol"])):
        fmt = lambda v, d=5: f"{v:.{d}f}" if v is not None else "N/A"
        lines.append(
            f"| {r.get('signal', '')} | {r.get('symbol', '')} | "
            f"{r.get('n_triggers_is', 0)} | {r.get('n_triggers_oos', 0)} | "
            f"{fmt(r.get('oos_hold_4h_avg'))} | {fmt(r.get('oos_hold_24h_avg'))} | "
            f"{fmt(r.get('oos_hold_24h_pct_neg'), 4)} | {fmt(r.get('oos_exit_value_24h'))} |"
        )

    # ── Verdicts ──
    lines.append("")
    lines.append("## 5. Signal Verdicts")
    lines.append("")

    signal_names = sorted(set(r.get("signal", "") for r in all_results))

    for sig in signal_names:
        sig_results = [r for r in all_results if r.get("signal") == sig]
        btc_res = [r for r in sig_results if r.get("symbol") == "BTC"]
        alt_alive = [r for r in sig_results if r.get("symbol") != "BTC" and r.get("alive")]

        btc_alive = btc_res[0].get("alive", False) if btc_res else False

        if btc_alive and len(alt_alive) >= 2:
            verdict = "STRONG — passes BTC + 2+ alts"
        elif btc_alive and len(alt_alive) >= 1:
            verdict = "MODERATE — passes BTC + 1 alt"
        elif btc_alive:
            verdict = "WEAK — passes BTC only"
        else:
            btc_reason = btc_res[0].get("kill_reason", btc_res[0].get("status", "unknown")) if btc_res else "no data"
            verdict = f"KILLED — {btc_reason}"

        lines.append(f"- **{sig}**: {verdict}")

    lines.append("")

    # Exit verdicts
    for sig in sorted(set(r.get("signal", "") for r in exit_results)):
        sig_res = [r for r in exit_results if r.get("signal") == sig]
        # Check if OOS exit value is positive across symbols
        oos_values = [r.get("oos_exit_value_24h", 0) or 0 for r in sig_res]
        avg_exit_val = np.mean(oos_values) if oos_values else 0

        if avg_exit_val > 0.001:
            verdict = f"USEFUL — avg OOS exit value = {avg_exit_val:.5f}"
        elif avg_exit_val > 0:
            verdict = f"MARGINAL — avg OOS exit value = {avg_exit_val:.5f}"
        else:
            verdict = f"NOT USEFUL — avg OOS exit value = {avg_exit_val:.5f}"

        lines.append(f"- **{sig}**: {verdict}")

    # ── Ranking ──
    lines.append("")
    lines.append("## 6. Top Signals Ranked by OOS Profit Factor (24h)")
    lines.append("")

    ranked = []
    for r in all_results:
        oos_pf = r.get("oos_fwd_24h_pf")
        if oos_pf is not None and r.get("alive"):
            ranked.append({
                "signal": r.get("signal"),
                "symbol": r.get("symbol"),
                "oos_pf": oos_pf,
                "oos_hit": r.get("oos_fwd_24h_hit"),
                "oos_avg": r.get("oos_fwd_24h_avg"),
                "n_oos": r.get("n_trades_oos"),
            })

    ranked.sort(key=lambda x: x["oos_pf"], reverse=True)

    if ranked:
        lines.append("| Rank | Signal | Symbol | OOS PF | OOS Hit | OOS Avg Ret | OOS Trades |")
        lines.append("|------|--------|--------|--------|---------|-------------|------------|")
        for i, r in enumerate(ranked[:15], 1):
            lines.append(
                f"| {i} | {r['signal']} | {r['symbol']} | {r['oos_pf']:.2f} | "
                f"{r['oos_hit']:.4f} | {r['oos_avg']:.5f} | {r['n_oos']} |"
            )
    else:
        lines.append("No signals survived the kill filter.")

    lines.append("")
    lines.append("---")
    lines.append("*End of report*")

    report = "\n".join(lines)
    with open(OUT_PATH, "w") as f:
        f.write(report)
    print(f"\nReport written to {OUT_PATH}")
    return report


if __name__ == "__main__":
    all_results, all_examples, exit_results = run_all_signals()
    report = write_report(all_results, all_examples, exit_results)
    print("\n" + "="*60)
    print("DONE")
