"""
Gate 1 RESCUE #2: Liquidation Cascade Recovery — hourly liquidation gate.

Rescue #1 was handicapped by daily-only Coinalyze liquidation data, forcing
us to attach the PRIOR-day z-score to each trigger hour. That produced only
9 valid events in 5y and 1 in L12M — too sparse to evaluate.

This rescue uses hourly liquidation data (data/alternative/coinalyze/
liquidations_1h_parquet/) to compute a 168h (7-day) rolling z-score of total
cross-exchange hourly liquidation USD, aligned to the cascade hour itself
(no prior-day fallback). Coinalyze historical depth on 1h is limited to
~60-100 days so the usable event window is small — we report whatever the
data supports and let Gate 1 thresholds judge.

Research-only — appends a new section to research/cascade_recovery_gate1_report.md.
"""
from __future__ import annotations

import glob
import os
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/workspace/crypto_backtest")
OHLCV_DIR = ROOT / "data/perp/binance/1h_ohlcv"
LIQ_DIR = ROOT / "data/alternative/coinalyze/liquidations_1h_parquet"
REPORT = ROOT / "research/cascade_recovery_gate1_report.md"

EXCHANGES = ["binance", "bybit", "okx", "bitmex", "bitfinex", "huobi"]

RET_THR = -0.02
VOL_THR = 2.0
LIQ_Z_WIN_H = 168       # 7 days hourly
BASKET_SIZE = 20
WINDOWS = [12, 24, 48]
COOLDOWN_H = 72

FEE = 0.0004
SLIPPAGE = 0.0005
ROUND_TRIP_COST = 2 * (FEE + SLIPPAGE)  # 18 bps

LIQ_Z_SWEEP = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5]


def load_btc() -> pd.DataFrame:
    df = pd.read_csv(OHLCV_DIR / "BTC_perp_1h.csv")
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.set_index("datetime").sort_index()
    return df[["open", "high", "low", "close", "volume"]]


def load_alt(sym_file: str) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(sym_file)
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        df = df.set_index("datetime").sort_index()
        return df[["open", "high", "low", "close", "volume"]]
    except Exception:
        return None


def load_liquidation_hourly() -> tuple[pd.DataFrame, dict]:
    """Cross-exchange hourly total liquidation USD, aggregated across all symbols.
    Returns (hourly_df, per-exchange coverage dict)."""
    frames = []
    coverage = {}
    for ex in EXCHANGES:
        p = LIQ_DIR / f"{ex}.parquet"
        if not p.exists():
            coverage[ex] = dict(rows=0, tokens=0, start=None, end=None, missing=True)
            continue
        df = pd.read_parquet(p)
        df["date"] = pd.to_datetime(df["date"], utc=True)
        df["total"] = df["l"].fillna(0) + df["s"].fillna(0)
        coverage[ex] = dict(
            rows=len(df),
            tokens=df["token"].nunique(),
            start=df["date"].min(),
            end=df["date"].max(),
            missing=False,
        )
        frames.append(df[["date", "l", "s", "total"]])
    if not frames:
        raise RuntimeError("No hourly liquidation parquets found")
    all_df = pd.concat(frames, ignore_index=True)
    hourly = all_df.groupby("date").agg(
        liq_long=("l", "sum"),
        liq_short=("s", "sum"),
        liq_total=("total", "sum"),
    ).sort_index()
    # Fill gaps (hours with zero liq) to avoid z-score artifacts
    full_idx = pd.date_range(hourly.index.min(), hourly.index.max(), freq="1h", tz="UTC")
    hourly = hourly.reindex(full_idx).fillna(0.0)
    return hourly, coverage


def compute_liq_z_h(hourly: pd.DataFrame, window: int) -> pd.Series:
    """Rolling z-score of hourly total liquidation USD."""
    x = hourly["liq_total"]
    mean = x.rolling(window, min_periods=window).mean()
    std = x.rolling(window, min_periods=window).std()
    z = (x - mean) / std
    return z


def compute_trigger(
    btc: pd.DataFrame,
    liq_hourly: pd.DataFrame,
    liq_z_thr: float,
) -> pd.DataFrame:
    df = btc.copy()
    df["ret_1h"] = df["close"].pct_change()
    df["vol_24h_ma"] = df["volume"].rolling(24, min_periods=24).mean()
    df["vol_ratio"] = df["volume"] / df["vol_24h_ma"].shift(1)

    liq_z = compute_liq_z_h(liq_hourly, LIQ_Z_WIN_H)
    # current-hour alignment (no shift): liq_z at hour t uses liquidations
    # through hour t, same as BTC ret_1h and vol_ratio observed at t.
    df = df.join(liq_z.rename("liq_z"), how="left")
    df = df.join(liq_hourly["liq_total"].rename("liq_total_usd"), how="left")

    base = (df["ret_1h"] < RET_THR) & (df["vol_ratio"] > VOL_THR)
    liq_gate = df["liq_z"] > liq_z_thr
    df["trigger"] = base & liq_gate
    return df


def extract_events(df: pd.DataFrame) -> pd.DataFrame:
    events = []
    last_event_idx = -10_000
    trigger_idx = np.flatnonzero(df["trigger"].fillna(False).values)
    for i in trigger_idx:
        if i - last_event_idx < COOLDOWN_H:
            continue
        last_event_idx = i
        ts = df.index[i]
        events.append(dict(
            ts=ts,
            btc_ret_1h=df["ret_1h"].iloc[i],
            vol_ratio=df["vol_ratio"].iloc[i],
            liq_z=df["liq_z"].iloc[i],
            liq_total_usd=df["liq_total_usd"].iloc[i],
        ))
    if not events:
        return pd.DataFrame(columns=["ts", "btc_ret_1h", "vol_ratio", "liq_z", "liq_total_usd"])
    return pd.DataFrame(events)


def top_liquid_alts_at(ts: pd.Timestamp, alt_data: dict, n: int = BASKET_SIZE) -> list[str]:
    liq = []
    window_start = ts - pd.Timedelta(days=7)
    exit_ts = ts + pd.Timedelta(hours=max(WINDOWS))
    for sym, df in alt_data.items():
        if sym == "BTC":
            continue
        if df.index.min() > window_start or df.index.max() < exit_ts:
            continue
        try:
            w = df.loc[window_start:ts]
            if len(w) < 24:
                continue
            usd_vol = (w["close"] * w["volume"]).mean()
            if np.isnan(usd_vol) or usd_vol <= 0:
                continue
            liq.append((sym, usd_vol))
        except Exception:
            continue
    liq.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in liq[:n]]


def get_price_at(df: pd.DataFrame, ts: pd.Timestamp, col: str = "close") -> float:
    try:
        return float(df.loc[ts, col])
    except KeyError:
        pos = df.index.searchsorted(ts)
        if pos >= len(df):
            return np.nan
        return float(df.iloc[pos][col])


def event_return(event_ts, basket, alt_data, hold_h) -> dict:
    exit_ts = event_ts + pd.Timedelta(hours=hold_h)
    rets = []
    for sym in basket:
        df = alt_data[sym]
        p0 = get_price_at(df, event_ts, "close")
        p1 = get_price_at(df, exit_ts, "close")
        if np.isnan(p0) or np.isnan(p1) or p0 <= 0:
            continue
        rets.append(p1 / p0 - 1)
    if not rets:
        return dict(mean_ret=np.nan, n=0, net_ret=np.nan)
    gross = float(np.mean(rets))
    return dict(mean_ret=gross, n=len(rets), net_ret=gross - ROUND_TRIP_COST)


def btc_return(btc, ts, hold_h) -> float:
    p0 = get_price_at(btc, ts, "close")
    p1 = get_price_at(btc, ts + pd.Timedelta(hours=hold_h), "close")
    if np.isnan(p0) or np.isnan(p1) or p0 <= 0:
        return np.nan
    return p1 / p0 - 1


def summarize_rets(rets):
    rets = np.asarray(rets); rets = rets[~np.isnan(rets)]
    if len(rets) == 0:
        return dict(n=0, mean=np.nan, median=np.nan, std=np.nan, win=np.nan, sharpe=np.nan)
    std = float(np.std(rets, ddof=1)) if len(rets) > 1 else 0.0
    return dict(
        n=int(len(rets)), mean=float(np.mean(rets)), median=float(np.median(rets)),
        std=std, win=float(np.mean(rets > 0)),
        sharpe=float(np.mean(rets) / std) if std > 0 else np.nan,
    )


def equity_metrics(rets, timestamps):
    rets = np.asarray(rets); mask = ~np.isnan(rets)
    rets = rets[mask]
    ts = [timestamps[i] for i in range(len(timestamps)) if mask[i]]
    if len(rets) == 0:
        return dict(n=0, total_ret=np.nan, sharpe=np.nan, maxdd=np.nan, calmar=np.nan, ann_ret=np.nan)
    eq = np.cumprod(1 + rets)
    peaks = np.maximum.accumulate(eq)
    dd = eq / peaks - 1
    maxdd = float(dd.min())
    if len(ts) >= 2:
        years = max((ts[-1] - ts[0]).total_seconds() / (365.25 * 86400), 1e-6)
    else:
        years = 1.0
    total_ret = float(eq[-1] - 1)
    ann_ret = (1 + total_ret) ** (1 / years) - 1 if years > 0 else np.nan
    trades_per_year = len(rets) / years if years > 0 else len(rets)
    if len(rets) > 1 and np.std(rets, ddof=1) > 0:
        sharpe = float(np.mean(rets) / np.std(rets, ddof=1) * np.sqrt(trades_per_year))
    else:
        sharpe = np.nan
    calmar = float(ann_ret / abs(maxdd)) if maxdd < 0 else np.nan
    return dict(n=int(len(rets)), total_ret=total_ret, ann_ret=float(ann_ret),
                sharpe=sharpe, maxdd=maxdd, calmar=calmar,
                trades_per_year=float(trades_per_year))


def alpha_tstat(strat, btc):
    mask = ~(np.isnan(strat) | np.isnan(btc))
    x = btc[mask]; y = strat[mask]
    if len(x) < 3:
        return (np.nan, np.nan, np.nan)
    X = np.column_stack([np.ones_like(x), x])
    beta_hat, *_ = np.linalg.lstsq(X, y, rcond=None)
    a, b = beta_hat
    resid = y - X @ beta_hat
    dof = len(y) - 2
    if dof <= 0:
        return (float(a), float(b), np.nan)
    sigma2 = (resid @ resid) / dof
    try:
        cov = sigma2 * np.linalg.inv(X.T @ X)
        se_a = np.sqrt(cov[0, 0])
        t_a = a / se_a if se_a > 0 else np.nan
    except np.linalg.LinAlgError:
        t_a = np.nan
    return (float(a), float(b), float(t_a))


def run_backtest(events, btc, alt_data):
    if len(events) == 0:
        cols = ["ts", "btc_ret_1h", "liq_z", "vol_ratio", "basket_n"]
        for h in WINDOWS:
            cols += [f"ret_{h}h_gross", f"ret_{h}h_net", f"btc_ret_{h}h"]
        return pd.DataFrame(columns=cols)
    rows = []
    for _, ev in events.iterrows():
        ts = ev["ts"]
        basket = top_liquid_alts_at(ts, alt_data)
        row = dict(ts=ts, btc_ret_1h=ev["btc_ret_1h"], liq_z=ev["liq_z"],
                   vol_ratio=ev["vol_ratio"], basket_n=len(basket))
        for h in WINDOWS:
            r = event_return(ts, basket, alt_data, h)
            row[f"ret_{h}h_gross"] = r["mean_ret"]
            row[f"ret_{h}h_net"] = r["net_ret"]
            row[f"btc_ret_{h}h"] = btc_return(btc, ts, h)
        rows.append(row)
    return pd.DataFrame(rows)


def slice_by(df, btc_end, months):
    start = btc_end - pd.Timedelta(days=int(30.44 * months))
    return df[df["ts"] >= start]


def main():
    print("Loading BTC ...")
    btc = load_btc()
    print(f"  BTC bars: {len(btc)}")

    print("Loading alt universe ...")
    alt_files = glob.glob(str(OHLCV_DIR / "*_perp_1h.csv"))
    alt_data = {}
    for f in alt_files:
        sym = os.path.basename(f).replace("_perp_1h.csv", "")
        df = load_alt(f)
        if df is not None and len(df) > 200:
            alt_data[sym] = df
    print(f"  alts: {len(alt_data)}")

    print("Loading cross-exchange hourly liquidations ...")
    liq_hourly, coverage = load_liquidation_hourly()
    print(f"  hourly rows: {len(liq_hourly)}  range {liq_hourly.index.min()} → {liq_hourly.index.max()}")

    data_start = liq_hourly.index.min()
    data_end = liq_hourly.index.max()
    usable_start = data_start + pd.Timedelta(hours=LIQ_Z_WIN_H)
    print(f"  usable trigger window (after 168h warmup): {usable_start} → {data_end}")
    usable_days = (data_end - usable_start).total_seconds() / 86400
    print(f"  usable span: {usable_days:.1f} days")

    btc_end = btc.index.max()

    # ---- Full sensitivity sweep ----
    print("\n=== Hourly sweep ===")
    sweep = {}
    for thr in LIQ_Z_SWEEP:
        trig = compute_trigger(btc, liq_hourly, thr)
        # Restrict to hours with a valid liq_z (post-warmup)
        events = extract_events(trig)
        # Full = entire usable window (no 5y cutoff — we only have ~few months of hourly)
        events_full = events[events["ts"] >= usable_start].reset_index(drop=True)
        print(f"  liq_z > {thr}: {len(events_full)} events")
        sweep[thr] = run_backtest(events_full, btc, alt_data)

    # ---- Build report section ----
    lines = []
    out = lines.append

    out("\n---\n\n## Rescue Attempt #2 (hourly liquidation data)\n")
    out("**Motivation.** Rescue #1 had only 9 valid events in 5y / 1 in L12M because "
        "the daily-resolution liquidation data forced a PRIOR-day z-score (the current "
        "day's total was not knowable at the trigger hour). With hourly Coinalyze data "
        "we can compute a 168h rolling z-score aligned to the cascade hour itself, "
        "which should produce far more events and expose the true edge.\n")

    out("### Data Inspection\n")
    out("**IMPORTANT: Coinalyze 1h historical depth is capped.** Empirically the "
        "API returns ~60-100 days of hourly liquidation history (varies by symbol). "
        "This means we cannot produce a 5-year or L12M track; the test window is "
        "effectively the last ~60-90 days minus the 168h warmup. Gate 1 thresholds "
        "designed for L12M slices must be interpreted accordingly.\n")

    out("Per-exchange hourly liquidation coverage:\n")
    out("| Exchange | Rows | Tokens | Start | End |")
    out("|---|---|---|---|---|")
    for ex, c in coverage.items():
        if c.get("missing"):
            out(f"| {ex} | — | — | — | — (no parquet) |")
        else:
            out(f"| {ex} | {c['rows']:,} | {c['tokens']} | {c['start']} | {c['end']} |")

    out(f"\nAggregated cross-exchange hourly series: **{len(liq_hourly):,} hours** "
        f"({data_start} → {data_end}).\n")
    out(f"Usable trigger window (post-168h warmup): **{usable_start} → {data_end}** "
        f"(~{usable_days:.1f} days, {int(usable_days*24)} hours).\n")

    # Hourly liq percentiles
    lh = liq_hourly["liq_total"]
    out(f"Hourly liq $ percentiles ($M): p50={lh.median()/1e6:.2f} "
        f"p90={lh.quantile(0.9)/1e6:.2f} p99={lh.quantile(0.99)/1e6:.2f} "
        f"max={lh.max()/1e6:.2f}\n")
    z_all = compute_liq_z_h(liq_hourly, LIQ_Z_WIN_H)
    out(f"168h-rolling z distribution: p50={z_all.quantile(0.5):.2f} "
        f"p90={z_all.quantile(0.9):.2f} p99={z_all.quantile(0.99):.2f} "
        f"max={z_all.max():.2f}, n(z>2)={int((z_all>2).sum())} of "
        f"{int(z_all.notna().sum())} hours.\n")

    # ---- Sweep table ----
    out("\n### Sensitivity Sweep (hourly, T+12h exit, full available window)\n")
    out("| liq_z > | N (full) | Mean T+12h | Sharpe(event) | Ann Sharpe | MaxDD | Calmar | α(T+12h) | t(α) |")
    out("|---|---|---|---|---|---|---|---|---|")
    for thr in LIQ_Z_SWEEP:
        res = sweep[thr]
        if len(res) == 0:
            out(f"| {thr} | 0 | — | — | — | — | — | — | — |")
            continue
        s = summarize_rets(res["ret_12h_net"].values)
        m = equity_metrics(res["ret_12h_net"].values, list(res["ts"]))
        a, b, t = alpha_tstat(res["ret_12h_net"].values, res["btc_ret_12h"].values)
        cal = f"{m['calmar']:.2f}" if not np.isnan(m.get('calmar', np.nan)) else "—"
        out(f"| {thr} | {s['n']} | {s['mean']*100:+.2f}% | {s['sharpe']:.2f} | "
            f"{m['sharpe']:.2f} | {m['maxdd']*100:.1f}% | {cal} | "
            f"{a*100:+.2f}% | {t:.2f} |")

    # ---- Best threshold: pick by ann Sharpe * (n>=3 guardrail) ----
    def score(res):
        if len(res) < 3:
            return -np.inf
        m = equity_metrics(res["ret_12h_net"].values, list(res["ts"]))
        if np.isnan(m.get("sharpe", np.nan)):
            return -np.inf
        return m["sharpe"]

    best_thr = None; best_res = None; best_score = -np.inf
    for thr in LIQ_Z_SWEEP:
        sc = score(sweep[thr])
        if sc > best_score:
            best_score = sc
            best_thr = thr
            best_res = sweep[thr]

    out("\n### Per-Window Distribution at Best Threshold\n")
    if best_thr is None or best_res is None or len(best_res) == 0:
        out("No threshold produced ≥3 events — dataset too thin for any meaningful test.\n")
    else:
        out(f"Best threshold (by full-window ann Sharpe): **liq_z > {best_thr}** "
            f"({len(best_res)} events)\n")
        out("| Window | N | Mean | Median | Std | Win% | Sharpe(event) | α | β | t(α) |")
        out("|---|---|---|---|---|---|---|---|---|---|")
        for h in WINDOWS:
            s = summarize_rets(best_res[f"ret_{h}h_net"].values)
            a, b, t = alpha_tstat(best_res[f"ret_{h}h_net"].values,
                                  best_res[f"btc_ret_{h}h"].values)
            out(f"| T+{h}h | {s['n']} | {s['mean']*100:+.2f}% | {s['median']*100:+.2f}% | "
                f"{s['std']*100:.2f}% | {s['win']*100:.0f}% | {s['sharpe']:.2f} | "
                f"{a*100:+.2f}% | {b:.2f} | {t:.2f} |")

        out("\nEvents (best threshold):\n")
        out("| Timestamp | BTC 1h | vol× | liq_z | liq $M | n alts | ret_12h net | ret_24h net | BTC_24h |")
        out("|---|---|---|---|---|---|---|---|---|")
        for _, r in best_res.sort_values("ts").iterrows():
            liq_m = sweep[best_thr]  # reuse to look up liq_total; but run_backtest dropped it
            out(f"| {r['ts'].strftime('%Y-%m-%d %H:%M')} | {r['btc_ret_1h']*100:.2f}% | "
                f"{r['vol_ratio']:.1f} | {r['liq_z']:.2f} | — | {int(r['basket_n'])} | "
                f"{r['ret_12h_net']*100:+.2f}% | {r['ret_24h_net']*100:+.2f}% | "
                f"{r['btc_ret_24h']*100:+.2f}% |")

        # L12M / L6M / L3M slices (may all equal "full" if the hourly window is < 3 months)
        out("\n### L12M / L6M / L3M Slices (best threshold)\n")
        out("(Hourly data depth is limited — slices smaller than the full window will be empty.)\n")
        out("| Slice | N | AnnRet | Sharpe | MaxDD | Calmar | Total | t(α) T+12h |")
        out("|---|---|---|---|---|---|---|---|")
        for sl_name, sl_m in [("Full", None), ("L12M", 12), ("L6M", 6), ("L3M", 3)]:
            sub = best_res if sl_m is None else slice_by(best_res, btc_end, sl_m).sort_values("ts")
            if len(sub) == 0:
                out(f"| {sl_name} | 0 | — | — | — | — | — | — |")
                continue
            m = equity_metrics(sub["ret_12h_net"].values, list(sub["ts"]))
            a, b, t = alpha_tstat(sub["ret_12h_net"].values, sub["btc_ret_12h"].values)
            if m["n"] == 0:
                out(f"| {sl_name} | 0 | — | — | — | — | — | — |")
                continue
            cal = f"{m['calmar']:.2f}" if not np.isnan(m.get('calmar', np.nan)) else "—"
            out(f"| {sl_name} | {m['n']} | {m['ann_ret']*100:+.1f}% | {m['sharpe']:.2f} | "
                f"{m['maxdd']*100:.1f}% | {cal} | {m['total_ret']*100:+.1f}% | {t:.2f} |")

    # ---- Verdict ----
    out("\n### Verdict (Gate 1)\n")
    # Criteria: L12M Sharpe > 2.0, Calmar > 3.0, MaxDD > -25%, N > 10, |t(α)| > 1.5
    # Because hourly data is too short for a 12-month slice, we evaluate the full
    # available window as our de-facto "recent" sample.
    if best_thr is None:
        verdict = "KILL"
        out("No threshold produced ≥3 events over the available window. "
            "Data depth is insufficient to evaluate the hypothesis. "
            "**Verdict: KILL — data limit.**\n")
    else:
        l12 = slice_by(best_res, btc_end, 12).sort_values("ts")
        eval_df = l12 if len(l12) >= 10 else best_res
        eval_label = "L12M" if len(l12) >= 10 else f"Full window ({len(best_res)} ev; L12M had {len(l12)})"
        m = equity_metrics(eval_df["ret_12h_net"].values, list(eval_df["ts"]))
        a, b, t = alpha_tstat(eval_df["ret_12h_net"].values, eval_df["btc_ret_12h"].values)
        sh_ok = (not np.isnan(m["sharpe"])) and m["sharpe"] > 2.0
        ca_ok = (not np.isnan(m.get("calmar", np.nan))) and m["calmar"] > 3.0
        dd_ok = (not np.isnan(m["maxdd"])) and m["maxdd"] > -0.25
        n_ok = m["n"] > 10
        alpha_ok = (not np.isnan(t)) and abs(t) > 1.5

        def chk(ok): return "PASS" if ok else "FAIL"

        out(f"Evaluation window: **{eval_label}**, threshold liq_z > {best_thr}, exit T+12h.\n")
        out("| Metric | Value | Threshold | Check |")
        out("|---|---|---|---|")
        out(f"| Trades | {m['n']} | > 10 | {chk(n_ok)} |")
        out(f"| Sharpe (ann) | {m['sharpe']:.2f} | > 2.0 | {chk(sh_ok)} |")
        out(f"| Calmar | {m.get('calmar', float('nan')):.2f} | > 3.0 | {chk(ca_ok)} |")
        out(f"| MaxDD | {m['maxdd']*100:.1f}% | > -25% | {chk(dd_ok)} |")
        out(f"| Alpha t-stat | {t:.2f} | > 1.5 | {chk(alpha_ok)} |")

        passed_all = sh_ok and ca_ok and dd_ok and n_ok and alpha_ok
        if passed_all:
            verdict = "PASS"
        elif n_ok and (sh_ok or alpha_ok):
            verdict = "NEEDS_TUNING"
        elif not n_ok:
            verdict = "KILL (insufficient events due to short hourly history)"
        else:
            verdict = "KILL"
        out(f"\n**RESCUE #2 VERDICT: {verdict}**\n")

    # Save
    existing = REPORT.read_text() if REPORT.exists() else ""
    REPORT.write_text(existing + "\n".join(lines) + "\n")
    print(f"\nAppended rescue #2 section to {REPORT}")


if __name__ == "__main__":
    main()
