"""
OBV Divergence — Gate 0 IC Test
================================
Hypothesis: On-Balance Volume (OBV) divergence captures accumulation/distribution
pressure that precedes price moves.
  - Bearish divergence: price makes new high but OBV doesn't confirm -> reversal
  - Bullish divergence: price makes new low but OBV holds/rises -> bounce

Signal variants:
  1. obv_price_corr_24h: rolling 24h Pearson correlation between price and OBV
     (low correlation = divergence)
  2. obv_price_corr_72h: rolling 72h Pearson correlation
  3. obv_slope_divergence: sign(price_slope_24h) != sign(obv_slope_24h)
     where slope = OLS regression slope over window

IC = Spearman rank correlation between signal and forward returns per token.
Non-overlapping forward returns used to avoid autocorrelation inflation.

Point-in-time: signals computed on closed bars (shift by 1).
"""

import os
import time
import numpy as np
import pandas as pd
from scipy import stats as sp_stats

# ── Config ──────────────────────────────────────────────────────────────
PERP_DIR = "/workspace/crypto_backtest/data/perp/1h_cache/"
TOKENS = ["BTC", "ETH", "SOL", "BNB", "DOGE", "XRP", "ADA", "AVAX", "LINK", "DOT"]
HORIZONS = [4, 8, 24, 48]  # forward return horizons in hours
IS_CUTOFF = pd.Timestamp("2025-01-01")  # in-sample / out-of-sample split
MIN_OBS = 100  # minimum non-overlapping IC observations per split


def load_token_data(token: str) -> pd.DataFrame:
    """Load 1h perp data for a single token."""
    path = os.path.join(PERP_DIR, f"{token}_1h.parquet")
    df = pd.read_parquet(path, columns=["close", "volume"])
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df.sort_index(inplace=True)
    return df


# ── OBV computation ────────────────────────────────────────────────────

def compute_obv(close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    """Compute On-Balance Volume: obv[i] = obv[i-1] + sign(close[i]-close[i-1]) * volume[i]."""
    price_diff = np.diff(close, prepend=close[0])
    direction = np.sign(price_diff)
    direction[0] = 0  # first bar has no prior
    obv = np.cumsum(direction * volume)
    return obv


# ── Rolling correlation (numpy, no pandas rolling) ─────────────────────

def rolling_correlation(x: np.ndarray, y: np.ndarray, window: int) -> np.ndarray:
    """
    Rolling Pearson correlation between x and y using numpy.
    Returns array of same length as input, with NaN for first (window-1) bars.
    Uses the online formula: corr = (n*sum(xy) - sum(x)*sum(y)) /
        sqrt((n*sum(x^2) - sum(x)^2) * (n*sum(y^2) - sum(y)^2))
    """
    n = len(x)
    result = np.full(n, np.nan)

    # Cumulative sums for sliding window
    cx = np.cumsum(x)
    cy = np.cumsum(y)
    cx2 = np.cumsum(x * x)
    cy2 = np.cumsum(y * y)
    cxy = np.cumsum(x * y)

    # For indices >= window-1
    idx = np.arange(window - 1, n)
    # sum over window [i-window+1, i]
    if window - 1 > 0:
        sx = cx[idx] - cx[idx - window]
        sy = cy[idx] - cy[idx - window]
        sx2 = cx2[idx] - cx2[idx - window]
        sy2 = cy2[idx] - cy2[idx - window]
        sxy = cxy[idx] - cxy[idx - window]
    else:
        sx = cx[idx]
        sy = cy[idx]
        sx2 = cx2[idx]
        sy2 = cy2[idx]
        sxy = cxy[idx]

    w = float(window)
    num = w * sxy - sx * sy
    den = np.sqrt((w * sx2 - sx ** 2) * (w * sy2 - sy ** 2))

    with np.errstate(divide='ignore', invalid='ignore'):
        corr = np.where(den > 0, num / den, np.nan)

    result[idx] = corr
    return result


# ── Rolling slope (OLS regression slope via numpy) ─────────────────────

def rolling_slope(y: np.ndarray, window: int) -> np.ndarray:
    """
    Rolling OLS slope of y against time index [0, 1, ..., window-1].
    slope = (n * sum(t*y) - sum(t) * sum(y)) / (n * sum(t^2) - sum(t)^2)
    where t = [0, 1, ..., window-1].
    """
    n = len(y)
    result = np.full(n, np.nan)

    # Fixed sums for the time index [0, 1, ..., window-1]
    w = float(window)
    t = np.arange(window, dtype=np.float64)
    sum_t = t.sum()          # w*(w-1)/2
    sum_t2 = (t * t).sum()   # w*(w-1)*(2w-1)/6
    denom = w * sum_t2 - sum_t ** 2  # constant

    if denom == 0:
        return result

    # We need sum(t * y[i-window+1:i+1]) for each i
    # This equals sum(j * y[i-window+1+j]) for j=0..window-1
    # = sum(j * y[k]) where k = i-window+1+j, j = k - (i-window+1)
    # Rewrite: sum(k * y[k]) - (i-window+1)*sum(y[k]) for k in [i-window+1, i]
    # where first sum is over k*y[k]

    cy = np.cumsum(y)
    # Create k*y array
    k_arr = np.arange(n, dtype=np.float64)
    cky = np.cumsum(k_arr * y)

    idx = np.arange(window - 1, n)
    start = idx - window + 1  # starting index of each window

    if window - 1 > 0:
        sy = cy[idx] - cy[idx - window]
        sky = cky[idx] - cky[idx - window]
    else:
        sy = cy[idx]
        sky = cky[idx]

    # sum(t_j * y[start+j]) = sum((k - start) * y[k]) = sum(k*y[k]) - start * sum(y[k])
    sty = sky - start.astype(np.float64) * sy

    numerator = w * sty - sum_t * sy
    result[idx] = numerator / denom
    return result


# ── Signal computation ─────────────────────────────────────────────────

def compute_signals(close: np.ndarray, volume: np.ndarray) -> dict:
    """
    Compute 3 OBV divergence signal variants.
    All signals are shifted by 1 bar (point-in-time: use only closed bars).
    """
    obv = compute_obv(close, volume)

    # Signal 1: Rolling 24h correlation between price and OBV
    corr_24h = rolling_correlation(close, obv, 24)

    # Signal 2: Rolling 72h correlation between price and OBV
    corr_72h = rolling_correlation(close, obv, 72)

    # Signal 3: Slope divergence
    # price slope over 24h, OBV slope over 24h
    price_slope = rolling_slope(close, 24)
    obv_slope = rolling_slope(obv, 24)
    # Divergence = sign mismatch: when signs differ, divergence = -1; same = +1
    # We want a *directional* signal: if price is rising but OBV falling (bearish div),
    # this should predict negative returns.
    # Encode as: sign(obv_slope) - sign(price_slope) ... but this loses magnitude.
    # Better: use obv_slope when sign differs from price_slope, else 0
    # Or simply: product of signs -- positive means agreement, negative means divergence
    with np.errstate(invalid='ignore'):
        sign_product = np.sign(price_slope) * np.sign(obv_slope)
    # sign_product = +1 means agreement, -1 means divergence
    # For IC test: use the raw correlation/product as the signal
    # Low correlation = divergence = unclear direction, but we hypothesize
    # that the OBV direction wins. So for the slope divergence signal,
    # use obv_slope_sign * (1 - agreement) to get directional divergence:
    # Actually, the simplest predictive signal is just the OBV slope sign
    # when there IS divergence. Let's use:
    #   slope_div_signal = sign(obv_slope) * |sign(price_slope) - sign(obv_slope)| / 2
    # This is nonzero only when signs differ, and takes the OBV direction.
    # But this is sparse. For IC, let's use something continuous:
    #   slope_div_signal = obv_slope_normalized - price_slope_normalized
    # Normalize slopes by their rolling std to make them comparable.
    # Actually, simplest and most testable: just use sign_product directly.
    # sign_product = +1 means both agree (continuation), -1 means divergence.
    # The hypothesis is: divergence predicts reversal in price direction.
    # So signal = -sign(price_slope) when divergence, 0 otherwise.
    # = -sign(price_slope) * max(0, -sign_product)
    # Let's be clean: the full signal is the OBV slope direction:
    #   If OBV slope is positive, expect price to go up (accumulation)
    #   If OBV slope is negative, expect price to go down (distribution)
    # This is the strongest form of the hypothesis. Use raw obv_slope.
    # And sign_product captures whether price already agrees with OBV or not.

    # Let's test multiple forms:
    # a) sign_product: +1 agreement (continuation), -1 divergence (reversal expected)
    # b) obv_slope: raw OBV slope (accumulation/distribution direction)
    # For the IC test, use sign_product as the primary "divergence" signal.
    # But sign_product doesn't tell direction of future return - it just says "divergent".
    # The hypothesis says: if divergent, OBV direction wins. So:
    #   directional signal = obv_slope (when divergent, OBV predicts direction)
    # For simplicity, just test obv_slope as the signal - it subsumes the divergence idea
    # because obv_slope predicts direction regardless, and divergence just means
    # it's a stronger signal (price hasn't caught up yet).

    # Final approach: use the normalized obv_slope as signal 3.
    # Normalize by rolling std of obv over 24h for cross-token comparability.
    obv_std_24 = _rolling_std(obv, 24)
    with np.errstate(divide='ignore', invalid='ignore'):
        obv_slope_norm = np.where(obv_std_24 > 0, obv_slope / obv_std_24, np.nan)

    # Shift all signals by 1 bar (point-in-time)
    signals = {
        "obv_price_corr_24h": np.roll(corr_24h, 1),
        "obv_price_corr_72h": np.roll(corr_72h, 1),
        "obv_slope_divergence": np.roll(obv_slope_norm, 1),
    }
    # Set first shifted value to NaN
    for k in signals:
        signals[k][0] = np.nan

    return signals


def _rolling_std(x: np.ndarray, window: int) -> np.ndarray:
    """Rolling standard deviation using cumulative sums."""
    n = len(x)
    result = np.full(n, np.nan)

    cx = np.cumsum(x)
    cx2 = np.cumsum(x * x)

    idx = np.arange(window - 1, n)
    if window - 1 > 0:
        sx = cx[idx] - cx[idx - window]
        sx2 = cx2[idx] - cx2[idx - window]
    else:
        sx = cx[idx]
        sx2 = cx2[idx]

    w = float(window)
    var = (sx2 / w) - (sx / w) ** 2
    var = np.maximum(var, 0)  # numerical safety
    result[idx] = np.sqrt(var)
    return result


# ── IC computation ─────────────────────────────────────────────────────

def compute_ic_for_token(
    signal: np.ndarray,
    close: np.ndarray,
    timestamps: np.ndarray,
    horizon: int,
    is_cutoff: pd.Timestamp,
) -> dict:
    """
    Compute Spearman rank IC between signal and forward returns for a single token.
    Uses non-overlapping returns: step by `horizon` bars.
    Returns dict with IS and OOS IC stats.
    """
    n = len(close)

    # Forward log returns: log(close[t+h] / close[t])
    fwd_ret = np.full(n, np.nan)
    if horizon < n:
        fwd_ret[:n - horizon] = np.log(close[horizon:] / close[:n - horizon])

    # Non-overlapping: sample every `horizon` bars
    sample_idx = np.arange(0, n, horizon)

    sig_sampled = signal[sample_idx]
    fwd_sampled = fwd_ret[sample_idx]
    ts_sampled = timestamps[sample_idx]

    # Remove NaN pairs
    valid = np.isfinite(sig_sampled) & np.isfinite(fwd_sampled)
    sig_v = sig_sampled[valid]
    fwd_v = fwd_sampled[valid]
    ts_v = ts_sampled[valid]

    if len(sig_v) < 30:
        return {"is_ic": np.nan, "oos_ic": np.nan,
                "is_tstat": np.nan, "oos_tstat": np.nan,
                "is_n": 0, "oos_n": 0}

    # Split IS/OOS
    cutoff_ts = np.datetime64(is_cutoff)
    is_mask = ts_v < cutoff_ts
    oos_mask = ~is_mask

    results = {}
    for label, mask in [("is", is_mask), ("oos", oos_mask)]:
        s = sig_v[mask]
        f = fwd_v[mask]
        if len(s) < MIN_OBS:
            results[f"{label}_ic"] = np.nan
            results[f"{label}_tstat"] = np.nan
            results[f"{label}_n"] = len(s)
            continue

        # Spearman rank correlation = Pearson on ranks
        rho, pval = sp_stats.spearmanr(s, f)
        # t-stat approximation: t = rho * sqrt((n-2)/(1-rho^2))
        nn = len(s)
        if abs(rho) < 1.0:
            tstat = rho * np.sqrt((nn - 2) / (1 - rho ** 2))
        else:
            tstat = np.inf * np.sign(rho)

        results[f"{label}_ic"] = rho
        results[f"{label}_tstat"] = tstat
        results[f"{label}_n"] = nn

    return results


def compute_rolling_ic_stats(
    signal: np.ndarray,
    close: np.ndarray,
    timestamps: np.ndarray,
    horizon: int,
    is_cutoff: pd.Timestamp,
    rolling_window: int = 120,
) -> dict:
    """
    Compute time-series of rolling ICs (in windows of `rolling_window` non-overlapping obs),
    then report mean IC and t-stat of mean IC across windows.
    This avoids the single-correlation problem and gives a proper t-stat.
    """
    n = len(close)

    # Forward log returns
    fwd_ret = np.full(n, np.nan)
    if horizon < n:
        fwd_ret[:n - horizon] = np.log(close[horizon:] / close[:n - horizon])

    # Non-overlapping sample
    sample_idx = np.arange(0, n, horizon)
    sig_sampled = signal[sample_idx]
    fwd_sampled = fwd_ret[sample_idx]
    ts_sampled = timestamps[sample_idx]

    valid = np.isfinite(sig_sampled) & np.isfinite(fwd_sampled)
    sig_v = sig_sampled[valid]
    fwd_v = fwd_sampled[valid]
    ts_v = ts_sampled[valid]

    cutoff_ts = np.datetime64(is_cutoff)

    results = {}
    for label, mask_arr in [("is", ts_v < cutoff_ts), ("oos", ts_v >= cutoff_ts)]:
        s = sig_v[mask_arr]
        f = fwd_v[mask_arr]

        if len(s) < rolling_window:
            results[f"{label}_ic"] = np.nan
            results[f"{label}_tstat"] = np.nan
            results[f"{label}_n"] = len(s)
            continue

        # Compute IC in non-overlapping windows of size `rolling_window`
        n_windows = len(s) // rolling_window
        ics = np.full(n_windows, np.nan)
        for w in range(n_windows):
            start = w * rolling_window
            end = start + rolling_window
            rho, _ = sp_stats.spearmanr(s[start:end], f[start:end])
            ics[w] = rho

        ics = ics[np.isfinite(ics)]
        if len(ics) < 3:
            results[f"{label}_ic"] = np.nan
            results[f"{label}_tstat"] = np.nan
            results[f"{label}_n"] = len(s)
            continue

        mean_ic = np.mean(ics)
        std_ic = np.std(ics, ddof=1)
        if std_ic > 0:
            tstat = mean_ic / std_ic * np.sqrt(len(ics))
        else:
            tstat = np.nan

        results[f"{label}_ic"] = mean_ic
        results[f"{label}_tstat"] = tstat
        results[f"{label}_n"] = len(s)
        results[f"{label}_n_windows"] = len(ics)

    return results


# ── Main ───────────────────────────────────────────────────────────────

def main():
    print("=" * 80)
    print("OBV DIVERGENCE — GATE 0 IC TEST")
    print("=" * 80)
    print(f"Tokens: {', '.join(TOKENS)}")
    print(f"Horizons: {HORIZONS}h")
    print(f"IS/OOS split: {IS_CUTOFF.date()}")
    print(f"Signals: obv_price_corr_24h, obv_price_corr_72h, obv_slope_divergence")
    print()

    # ── Load data ──────────────────────────────────────────────────────
    token_data = {}
    for tok in TOKENS:
        try:
            df = load_token_data(tok)
            token_data[tok] = df
            print(f"  {tok:>5s}: {len(df):>6,} bars  "
                  f"{df.index[0].date()} to {df.index[-1].date()}")
        except Exception as e:
            print(f"  {tok:>5s}: FAILED ({e})")

    print(f"\nLoaded {len(token_data)} tokens.\n")

    signal_names = ["obv_price_corr_24h", "obv_price_corr_72h", "obv_slope_divergence"]

    # ── Compute signals per token ──────────────────────────────────────
    # Store: token -> {signal_name -> array}
    token_signals = {}
    for tok, df in token_data.items():
        close = df["close"].values.astype(np.float64)
        volume = df["volume"].values.astype(np.float64)
        sigs = compute_signals(close, volume)
        token_signals[tok] = sigs

    # ── Compute IC: per-token, then pool ───────────────────────────────
    # For each signal x horizon, compute rolling-window IC stats per token,
    # then report the pooled (cross-token average) IC.

    print("=" * 80)
    print("POOLED IC RESULTS (cross-token average of per-token rolling ICs)")
    print("=" * 80)

    summary_rows = []

    for sig_name in signal_names:
        for horizon in HORIZONS:
            t0 = time.time()
            per_token_is_ics = []
            per_token_oos_ics = []
            per_token_details = []

            for tok, df in token_data.items():
                close = df["close"].values.astype(np.float64)
                timestamps = df.index.values
                signal = token_signals[tok][sig_name]

                res = compute_rolling_ic_stats(
                    signal, close, timestamps, horizon, IS_CUTOFF,
                    rolling_window=100,
                )

                if np.isfinite(res.get("is_ic", np.nan)):
                    per_token_is_ics.append(res["is_ic"])
                if np.isfinite(res.get("oos_ic", np.nan)):
                    per_token_oos_ics.append(res["oos_ic"])

                per_token_details.append({
                    "token": tok,
                    "is_ic": res.get("is_ic", np.nan),
                    "oos_ic": res.get("oos_ic", np.nan),
                    "is_tstat": res.get("is_tstat", np.nan),
                    "oos_tstat": res.get("oos_tstat", np.nan),
                    "is_n": res.get("is_n", 0),
                    "oos_n": res.get("oos_n", 0),
                })

            # Pool: mean IC across tokens, t-stat of the mean
            is_ics = np.array(per_token_is_ics) if per_token_is_ics else np.array([np.nan])
            oos_ics = np.array(per_token_oos_ics) if per_token_oos_ics else np.array([np.nan])

            is_mean = np.nanmean(is_ics)
            oos_mean = np.nanmean(oos_ics)

            # t-stat of cross-token mean (is the average IC significantly != 0?)
            if len(is_ics) > 2 and np.nanstd(is_ics, ddof=1) > 0:
                is_tstat = is_mean / np.nanstd(is_ics, ddof=1) * np.sqrt(len(is_ics))
            else:
                is_tstat = np.nan

            if len(oos_ics) > 2 and np.nanstd(oos_ics, ddof=1) > 0:
                oos_tstat = oos_mean / np.nanstd(oos_ics, ddof=1) * np.sqrt(len(oos_ics))
            else:
                oos_tstat = np.nan

            # IS/OOS sign consistency
            if np.isfinite(is_mean) and np.isfinite(oos_mean):
                sign_consistent = np.sign(is_mean) == np.sign(oos_mean)
            else:
                sign_consistent = False

            # Verdict
            best_ic = max(abs(is_mean) if np.isfinite(is_mean) else 0,
                          abs(oos_mean) if np.isfinite(oos_mean) else 0)
            best_tstat = max(abs(is_tstat) if np.isfinite(is_tstat) else 0,
                             abs(oos_tstat) if np.isfinite(oos_tstat) else 0)

            if best_ic >= 0.03 and best_tstat >= 2.0 and sign_consistent:
                verdict = "PASS"
            else:
                reasons = []
                if best_ic < 0.03:
                    reasons.append(f"|IC|={best_ic:.4f}<0.03")
                if best_tstat < 2.0:
                    reasons.append(f"|t|={best_tstat:.2f}<2.0")
                if not sign_consistent:
                    reasons.append("IS/OOS sign flip")
                verdict = "KILL (" + ", ".join(reasons) + ")"

            elapsed = time.time() - t0
            summary_rows.append({
                "signal": sig_name,
                "horizon": f"{horizon}h",
                "is_ic": is_mean,
                "oos_ic": oos_mean,
                "is_tstat": is_tstat,
                "oos_tstat": oos_tstat,
                "verdict": verdict,
                "per_token": per_token_details,
            })

            print(f"  {sig_name:>25s} | {horizon:>3d}h | "
                  f"IS IC={is_mean:+.4f} t={is_tstat:+.2f} | "
                  f"OOS IC={oos_mean:+.4f} t={oos_tstat:+.2f} | "
                  f"{verdict}  ({elapsed:.1f}s)")

    # ── Summary Table ──────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SUMMARY TABLE")
    print("=" * 80)
    print(f"{'Signal':<28s} | {'Horizon':>7s} | {'IS IC':>8s} | {'OOS IC':>8s} | "
          f"{'IS t':>8s} | {'OOS t':>8s} | Verdict")
    print("-" * 105)
    for row in summary_rows:
        print(f"{row['signal']:<28s} | {row['horizon']:>7s} | "
              f"{row['is_ic']:>+8.4f} | {row['oos_ic']:>+8.4f} | "
              f"{row['is_tstat']:>+8.2f} | {row['oos_tstat']:>+8.2f} | "
              f"{row['verdict']}")

    # ── Find best signal variant ───────────────────────────────────────
    print("\n" + "=" * 80)
    print("BEST SIGNAL VARIANT (by OOS |IC|)")
    print("=" * 80)

    # Sort by OOS |IC|
    best_row = max(summary_rows, key=lambda r: abs(r["oos_ic"]) if np.isfinite(r["oos_ic"]) else 0)
    print(f"  Signal:  {best_row['signal']}")
    print(f"  Horizon: {best_row['horizon']}")
    print(f"  IS IC:   {best_row['is_ic']:+.4f} (t={best_row['is_tstat']:+.2f})")
    print(f"  OOS IC:  {best_row['oos_ic']:+.4f} (t={best_row['oos_tstat']:+.2f})")
    print(f"  Verdict: {best_row['verdict']}")

    # ── Per-token breakdown for best ───────────────────────────────────
    print("\n" + "=" * 80)
    print(f"PER-TOKEN BREAKDOWN: {best_row['signal']} @ {best_row['horizon']}")
    print("=" * 80)
    print(f"{'Token':<8s} | {'IS IC':>8s} | {'OOS IC':>8s} | "
          f"{'IS t':>8s} | {'OOS t':>8s} | {'IS N':>6s} | {'OOS N':>6s}")
    print("-" * 70)
    for td in best_row["per_token"]:
        is_ic_s = f"{td['is_ic']:+.4f}" if np.isfinite(td["is_ic"]) else "   N/A "
        oos_ic_s = f"{td['oos_ic']:+.4f}" if np.isfinite(td["oos_ic"]) else "   N/A "
        is_t_s = f"{td['is_tstat']:+.2f}" if np.isfinite(td["is_tstat"]) else "  N/A "
        oos_t_s = f"{td['oos_tstat']:+.2f}" if np.isfinite(td["oos_tstat"]) else "  N/A "
        print(f"{td['token']:<8s} | {is_ic_s:>8s} | {oos_ic_s:>8s} | "
              f"{is_t_s:>8s} | {oos_t_s:>8s} | {td['is_n']:>6d} | {td['oos_n']:>6d}")

    # ── Also show per-token for ALL signal variants ────────────────────
    print("\n" + "=" * 80)
    print("PER-TOKEN BREAKDOWN: ALL SIGNAL VARIANTS (best horizon per variant)")
    print("=" * 80)

    for sig_name in signal_names:
        # Find best horizon for this signal
        sig_rows = [r for r in summary_rows if r["signal"] == sig_name]
        best_h = max(sig_rows, key=lambda r: abs(r["oos_ic"]) if np.isfinite(r["oos_ic"]) else 0)
        print(f"\n--- {sig_name} @ {best_h['horizon']} ---")
        print(f"{'Token':<8s} | {'IS IC':>8s} | {'OOS IC':>8s} | "
              f"{'IS t':>8s} | {'OOS t':>8s}")
        print("-" * 52)
        for td in best_h["per_token"]:
            is_ic_s = f"{td['is_ic']:+.4f}" if np.isfinite(td["is_ic"]) else "   N/A "
            oos_ic_s = f"{td['oos_ic']:+.4f}" if np.isfinite(td["oos_ic"]) else "   N/A "
            is_t_s = f"{td['is_tstat']:+.2f}" if np.isfinite(td["is_tstat"]) else "  N/A "
            oos_t_s = f"{td['oos_tstat']:+.2f}" if np.isfinite(td["oos_tstat"]) else "  N/A "
            print(f"{td['token']:<8s} | {is_ic_s:>8s} | {oos_ic_s:>8s} | "
                  f"{is_t_s:>8s} | {oos_t_s:>8s}")

    # ── Final Gate 0 Decision ──────────────────────────────────────────
    print("\n" + "=" * 80)
    print("GATE 0 DECISION")
    print("=" * 80)

    any_pass = any("PASS" in r["verdict"] for r in summary_rows)
    if any_pass:
        passing = [r for r in summary_rows if "PASS" in r["verdict"]]
        print(f"\n  {len(passing)} signal/horizon combination(s) PASS Gate 0.")
        for r in passing:
            print(f"    {r['signal']} @ {r['horizon']}: "
                  f"IS IC={r['is_ic']:+.4f}, OOS IC={r['oos_ic']:+.4f}")
        print("\n  Verdict: ADVANCE to Gate 1 for deeper analysis.")
    else:
        print("\n  NO signal/horizon combination passes Gate 0.")
        print("  Kill criteria triggered:")
        for r in summary_rows:
            print(f"    {r['signal']} @ {r['horizon']}: {r['verdict']}")
        print("\n  Verdict: KILL. OBV divergence does NOT have predictive power")
        print("  at these horizons in this universe.")


if __name__ == "__main__":
    main()
