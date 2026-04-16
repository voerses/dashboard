"""
Gate 1 RESCUE: Liquidation Cascade Recovery — replace funding-z gate
with a liquidation-$ z-score gate using Coinalyze cross-exchange data.

IMPORTANT DATA LIMITATION: The Coinalyze liquidation parquets are DAILY
resolution only (columns: date, l, s, token, symbol). The hypothesis
specifies a 168h (hourly) rolling z-score of hourly liquidation $.
We adapt to daily resolution: for each trigger hour, we use the 7-day
rolling z-score of TOTAL cross-exchange daily liquidation $ evaluated
on the PRIOR completed UTC day (no look-ahead). We also sweep a 30-day
window as a more regime-stable proxy.

Research-only — appends a section to research/cascade_recovery_gate1_report.md.
"""
from __future__ import annotations

import glob
import os
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/workspace/crypto_backtest")
OHLCV_DIR = ROOT / "data/perp/binance/1h_ohlcv"
FUNDING_DIR = ROOT / "data/perp/binance/funding"
LIQ_DIR = ROOT / "data/alternative/coinalyze/liquidations_parquet"
REPORT = ROOT / "research/cascade_recovery_gate1_report.md"

EXCHANGES = ["binance", "bybit", "okx", "bitmex", "bitfinex", "huobi"]

# Trigger base conditions (kept)
RET_THR = -0.02          # BTC 1h ret < -2%
VOL_THR = 2.0            # vol > 2x trailing 24h avg
LIQ_Z_THR = 2.0          # daily liq z-score > 2.0
LIQ_Z_WIN = 7            # 7-day rolling window (primary, matches 168h)
FUND_Z_THR_CONJ = 0.5    # conjunctive gate funding-z threshold
BASKET_SIZE = 20
WINDOWS = [12, 24, 48]
COOLDOWN_H = 72

# Costs
FEE = 0.0004
SLIPPAGE = 0.0005
ROUND_TRIP_COST = 2 * (FEE + SLIPPAGE)  # 18 bps

# Sensitivity sweep thresholds
LIQ_Z_SWEEP = [1.0, 1.5, 2.0, 2.5, 3.0]


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


def load_funding_market() -> pd.Series:
    files = glob.glob(str(FUNDING_DIR / "*_funding.csv"))
    series_list = []
    for f in files:
        try:
            df = pd.read_csv(f)
            df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
            df = df.set_index("datetime")["funding_rate"].astype(float)
            series_list.append(df)
        except Exception:
            continue
    all_df = pd.concat(series_list, axis=1)
    mkt = all_df.mean(axis=1)
    full_idx = pd.date_range(mkt.index.min(), mkt.index.max(), freq="1h", tz="UTC")
    mkt_h = mkt.reindex(mkt.index.union(full_idx)).sort_index().ffill().reindex(full_idx)
    return mkt_h


def load_liquidation_daily() -> pd.DataFrame:
    """Cross-exchange total liquidation USD per UTC day, summed across all symbols."""
    frames = []
    per_ex_coverage = {}
    for ex in EXCHANGES:
        p = LIQ_DIR / f"{ex}.parquet"
        df = pd.read_parquet(p)
        df["date"] = pd.to_datetime(df["date"])
        df["total"] = df["l"].fillna(0) + df["s"].fillna(0)
        per_ex_coverage[ex] = dict(
            rows=len(df),
            tokens=df["token"].nunique(),
            start=df["date"].min(),
            end=df["date"].max(),
        )
        frames.append(df[["date", "l", "s", "total"]])
    all_df = pd.concat(frames, ignore_index=True)
    daily = all_df.groupby("date").agg(
        liq_long=("l", "sum"),
        liq_short=("s", "sum"),
        liq_total=("total", "sum"),
    ).sort_index()
    return daily, per_ex_coverage


def compute_liq_z(daily: pd.DataFrame, window: int) -> pd.Series:
    """Rolling z-score of daily total liquidation, indexed by UTC date."""
    x = daily["liq_total"]
    mean = x.rolling(window, min_periods=window).mean()
    std = x.rolling(window, min_periods=window).std()
    z = (x - mean) / std
    return z


def compute_trigger(
    btc: pd.DataFrame,
    mkt_fund_h: pd.Series,
    liq_daily: pd.DataFrame,
    liq_z_window: int,
    liq_z_thr: float,
    require_funding: bool = False,
    fund_z_thr: float = 0.5,
) -> pd.DataFrame:
    df = btc.copy()
    df["ret_1h"] = df["close"].pct_change()
    df["vol_24h_ma"] = df["volume"].rolling(24, min_periods=24).mean()
    df["vol_ratio"] = df["volume"] / df["vol_24h_ma"].shift(1)

    # Market funding z (for conjunctive test)
    mf = mkt_fund_h.reindex(df.index).ffill()
    df["mkt_fund"] = mf
    mean = mf.rolling(168, min_periods=168).mean()
    std = mf.rolling(168, min_periods=168).std()
    df["fund_z"] = (mf - mean) / std

    # Liquidation z per day; attach PRIOR-DAY value to each hour (no lookahead)
    liq_z = compute_liq_z(liq_daily, liq_z_window)
    # Shift by 1 day so we use only data known before the event hour
    liq_z_shifted = liq_z.shift(1)
    # Build hourly series by forward-filling prior-day z across all hours
    # Map each hour's UTC date to the liq_z_shifted value
    dates = pd.to_datetime(df.index.date)
    # Need to map hour to day; use .map
    liq_z_map = liq_z_shifted.to_dict()
    df["liq_z"] = [liq_z_map.get(d, np.nan) for d in dates]

    base = (df["ret_1h"] < RET_THR) & (df["vol_ratio"] > VOL_THR)
    liq_gate = df["liq_z"] > liq_z_thr

    if require_funding:
        df["trigger"] = base & liq_gate & (df["fund_z"] > fund_z_thr)
    else:
        df["trigger"] = base & liq_gate
    return df


def extract_events(df: pd.DataFrame) -> pd.DataFrame:
    events = []
    last_event_idx = -10_000
    trigger_idx = np.flatnonzero(df["trigger"].values)
    for i in trigger_idx:
        if i - last_event_idx < COOLDOWN_H:
            continue
        last_event_idx = i
        ts = df.index[i]
        events.append(
            dict(
                ts=ts,
                btc_ret_1h=df["ret_1h"].iloc[i],
                vol_ratio=df["vol_ratio"].iloc[i],
                fund_z=df["fund_z"].iloc[i],
                liq_z=df["liq_z"].iloc[i],
            )
        )
    if not events:
        return pd.DataFrame(columns=["ts", "btc_ret_1h", "vol_ratio", "fund_z", "liq_z"])
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


def event_return(
    event_ts: pd.Timestamp,
    basket: list[str],
    alt_data: dict,
    hold_h: int,
) -> dict:
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


def btc_return(btc: pd.DataFrame, ts: pd.Timestamp, hold_h: int) -> float:
    p0 = get_price_at(btc, ts, "close")
    p1 = get_price_at(btc, ts + pd.Timedelta(hours=hold_h), "close")
    if np.isnan(p0) or np.isnan(p1) or p0 <= 0:
        return np.nan
    return p1 / p0 - 1


def summarize_rets(rets: np.ndarray) -> dict:
    rets = rets[~np.isnan(rets)]
    if len(rets) == 0:
        return dict(n=0, mean=np.nan, median=np.nan, std=np.nan, win=np.nan, sharpe=np.nan)
    std = float(np.std(rets, ddof=1)) if len(rets) > 1 else 0.0
    return dict(
        n=int(len(rets)),
        mean=float(np.mean(rets)),
        median=float(np.median(rets)),
        std=std,
        win=float(np.mean(rets > 0)),
        sharpe=float(np.mean(rets) / std) if std > 0 else np.nan,
    )


def equity_metrics(rets: np.ndarray, timestamps: list[pd.Timestamp]) -> dict:
    rets = np.array(rets)
    mask = ~np.isnan(rets)
    rets = rets[mask]
    ts = [timestamps[i] for i in range(len(timestamps)) if mask[i]]
    if len(rets) == 0:
        return dict(n=0, total_ret=np.nan, sharpe=np.nan, maxdd=np.nan, calmar=np.nan, ann_ret=np.nan)
    eq = np.cumprod(1 + rets)
    peaks = np.maximum.accumulate(eq)
    dd = eq / peaks - 1
    maxdd = float(dd.min())
    if len(ts) >= 2:
        years = (ts[-1] - ts[0]).total_seconds() / (365.25 * 86400)
        years = max(years, 1e-6)
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
    return dict(
        n=int(len(rets)),
        total_ret=total_ret,
        ann_ret=float(ann_ret),
        sharpe=sharpe,
        maxdd=maxdd,
        calmar=calmar,
        trades_per_year=float(trades_per_year),
    )


def alpha_tstat(strat_rets: np.ndarray, btc_rets: np.ndarray) -> tuple[float, float, float]:
    mask = ~(np.isnan(strat_rets) | np.isnan(btc_rets))
    x = btc_rets[mask]
    y = strat_rets[mask]
    if len(x) < 3:
        return (np.nan, np.nan, np.nan)
    X = np.column_stack([np.ones_like(x), x])
    beta_hat, *_ = np.linalg.lstsq(X, y, rcond=None)
    a, b = beta_hat
    resid = y - X @ beta_hat
    dof = len(y) - 2
    sigma2 = (resid @ resid) / dof
    try:
        cov = sigma2 * np.linalg.inv(X.T @ X)
        se_a = np.sqrt(cov[0, 0])
        t_a = a / se_a if se_a > 0 else np.nan
    except np.linalg.LinAlgError:
        t_a = np.nan
    return (float(a), float(b), float(t_a))


def run_backtest(
    events: pd.DataFrame, btc: pd.DataFrame, alt_data: dict
) -> pd.DataFrame:
    if len(events) == 0:
        cols = ["ts", "btc_ret_1h", "fund_z", "liq_z", "vol_ratio", "basket_n"]
        for h in WINDOWS:
            cols += [f"ret_{h}h_gross", f"ret_{h}h_net", f"btc_ret_{h}h"]
        return pd.DataFrame(columns=cols)
    rows = []
    for _, ev in events.iterrows():
        ts = ev["ts"]
        basket = top_liquid_alts_at(ts, alt_data)
        row = dict(
            ts=ts,
            btc_ret_1h=ev["btc_ret_1h"],
            fund_z=ev["fund_z"],
            liq_z=ev["liq_z"],
            vol_ratio=ev["vol_ratio"],
            basket_n=len(basket),
        )
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
    print("Loading BTC...")
    btc = load_btc()
    print(f"  BTC bars: {len(btc)}")

    print("Loading market funding...")
    mkt_fund = load_funding_market()

    print("Loading alt universe...")
    alt_files = glob.glob(str(OHLCV_DIR / "*_perp_1h.csv"))
    alt_data = {}
    for f in alt_files:
        sym = os.path.basename(f).replace("_perp_1h.csv", "")
        df = load_alt(f)
        if df is not None and len(df) > 200:
            alt_data[sym] = df
    print(f"  alts: {len(alt_data)}")

    print("Loading cross-exchange liquidations...")
    liq_daily, coverage = load_liquidation_daily()
    print(f"  daily rows: {len(liq_daily)}  range {liq_daily.index.min().date()} -> {liq_daily.index.max().date()}")

    btc_end = btc.index.max()
    cutoff = btc_end - pd.Timedelta(days=365 * 5)

    # ------------ Primary spec: 7-day rolling z, liq_z > 2.0 ------------
    print("\n=== Primary: 7d-z > 2.0 (liquidation gate ONLY, ret<-2%, vol>2x) ===")
    trig = compute_trigger(btc, mkt_fund, liq_daily, liq_z_window=LIQ_Z_WIN, liq_z_thr=LIQ_Z_THR)
    events = extract_events(trig)
    events = events[events["ts"] >= cutoff].reset_index(drop=True)
    print(f"  events last 5y: {len(events)}")
    res_primary = run_backtest(events, btc, alt_data)

    # ------------ Conjunctive: liq_z > 2.0 AND fund_z > 0.5 ------------
    print("\n=== Conjunctive: liq_z > 2.0 AND fund_z > 0.5 ===")
    trig_c = compute_trigger(btc, mkt_fund, liq_daily, LIQ_Z_WIN, LIQ_Z_THR,
                             require_funding=True, fund_z_thr=FUND_Z_THR_CONJ)
    events_c = extract_events(trig_c)
    events_c = events_c[events_c["ts"] >= cutoff].reset_index(drop=True)
    print(f"  events last 5y: {len(events_c)}")
    res_conj = run_backtest(events_c, btc, alt_data)

    # ------------ Sensitivity sweep on liq_z threshold ------------
    print("\n=== Sensitivity sweep ===")
    sweep_results = {}
    for thr in LIQ_Z_SWEEP:
        trig_s = compute_trigger(btc, mkt_fund, liq_daily, LIQ_Z_WIN, thr)
        ev_s = extract_events(trig_s)
        ev_s = ev_s[ev_s["ts"] >= cutoff].reset_index(drop=True)
        print(f"  liq_z > {thr}: {len(ev_s)} events")
        if len(ev_s) > 0:
            sweep_results[thr] = run_backtest(ev_s, btc, alt_data)
        else:
            sweep_results[thr] = pd.DataFrame()

    # ------------ Report assembly ------------
    lines = []
    out = lines.append

    out("\n---\n\n## Rescue Attempt (liquidation-$ gate)\n")
    out("**Hypothesis.** Replace funding-z > 1.5 with liquidation-$ z-score > 2.0 using "
        "cross-exchange Coinalyze data. Funding-z broke post-2021 because funding rates were "
        "moderated; liquidation $ scaled ~100x with market cap and should be a more "
        "regime-stable stress indicator.\n")

    out("### Data Inspection\n")
    out("**CRITICAL DATA LIMITATION**: Coinalyze liquidation parquets are **daily** "
        "resolution (columns: `date`, `l`, `s`, `token`, `symbol`). The hypothesis "
        "specifies an hourly 168h rolling z-score; hourly liquidation data is not "
        "available in this repo. We adapt to daily resolution with a 7-day rolling "
        "z-score of cross-exchange total daily liquidation $, attaching the **prior "
        "completed day's** z-score to each candidate trigger hour (no look-ahead). "
        "This is a weaker version of the hypothesis — a daily stress gate instead of "
        "an intraday stress gate.\n")
    out("Per-exchange coverage:\n")
    out("| Exchange | Rows | Tokens | Start | End |")
    out("|---|---|---|---|---|")
    for ex, c in coverage.items():
        out(f"| {ex} | {c['rows']:,} | {c['tokens']} | {c['start'].date()} | {c['end'].date()} |")
    out(f"\nAggregated cross-exchange daily series: **{len(liq_daily):,} days** "
        f"({liq_daily.index.min().date()} → {liq_daily.index.max().date()}).\n")
    out(f"Daily liq $ percentiles (all years, $M): "
        f"p50={liq_daily['liq_total'].median()/1e6:.1f}  "
        f"p90={liq_daily['liq_total'].quantile(0.9)/1e6:.1f}  "
        f"p99={liq_daily['liq_total'].quantile(0.99)/1e6:.1f}  "
        f"max={liq_daily['liq_total'].max()/1e6:.1f}\n")
    z_all = compute_liq_z(liq_daily, LIQ_Z_WIN)
    out(f"7d-rolling z distribution: p50={z_all.quantile(0.5):.2f} "
        f"p90={z_all.quantile(0.9):.2f} p99={z_all.quantile(0.99):.2f} "
        f"max={z_all.max():.2f}  "
        f"n(z>2.0)={int((z_all>2.0).sum())} over {int(z_all.notna().sum())} days.\n")

    # Primary trigger event count + table
    out("### New Trigger (liq_z > 2.0, 7d window) — Primary\n")
    out(f"Base filters unchanged: BTC 1h ret < -2%, vol > 2× 24h avg, 72h cooldown.\n")
    out(f"**Events in last 5y: {len(res_primary)}** (vs 61 for `matched_72` funding-z spec, 1 for `strict_paper`).\n")

    if len(res_primary) > 0:
        res_primary["year"] = res_primary["ts"].dt.year
        out("\nEvents by year:\n")
        out("| Year | Events |\n|---|---|")
        for y, c in res_primary.groupby("year").size().items():
            out(f"| {y} | {c} |")

        out("\nEvent list:\n")
        out("| Timestamp (UTC) | BTC 1h ret | vol×24h | liq_z | fund_z | n alts | ret_12h net | ret_24h net | BTC_24h |")
        out("|---|---|---|---|---|---|---|---|---|")
        for _, r in res_primary.sort_values("ts").iterrows():
            fz = f"{r['fund_z']:.2f}" if not np.isnan(r['fund_z']) else "—"
            out(f"| {r['ts'].strftime('%Y-%m-%d %H:%M')} | {r['btc_ret_1h']*100:.2f}% | "
                f"{r['vol_ratio']:.1f} | {r['liq_z']:.2f} | {fz} | {int(r['basket_n'])} | "
                f"{r['ret_12h_net']*100:+.2f}% | {r['ret_24h_net']*100:+.2f}% | "
                f"{r['btc_ret_24h']*100:+.2f}% |")

    # Per-window distribution (full 5y)
    out("\n### Per-Window Return Distributions (primary, net of costs, last 5y)\n")
    out("| Window | N | Mean | Median | Stdev | Win% | Sharpe(event) | α | β | t(α) |")
    out("|---|---|---|---|---|---|---|---|---|---|")
    for h in WINDOWS:
        if len(res_primary) == 0:
            out(f"| T+{h}h | 0 | — | — | — | — | — | — | — | — |")
            continue
        s = summarize_rets(res_primary[f"ret_{h}h_net"].values)
        a, b, t = alpha_tstat(res_primary[f"ret_{h}h_net"].values,
                              res_primary[f"btc_ret_{h}h"].values)
        out(f"| T+{h}h | {s['n']} | {s['mean']*100:+.2f}% | {s['median']*100:+.2f}% | "
            f"{s['std']*100:.2f}% | {s['win']*100:.0f}% | {s['sharpe']:.2f} | "
            f"{a*100:+.2f}% | {b:.2f} | {t:.2f} |")

    # Sliced metrics
    def sliced_table(res):
        slices = [("L12M", 12), ("L6M", 6), ("L3M", 3)]
        tbl = []
        for sl_name, sl_m in slices:
            sub = slice_by(res, btc_end, sl_m).sort_values("ts") if len(res) else res
            tbl.append((sl_name, sub))
        return tbl

    out("\n### Sliced Performance (primary, event-level annualized)\n")
    for sl_name, sub in sliced_table(res_primary):
        out(f"\n**{sl_name}** — {len(sub)} events\n")
        out("| Window | N | AnnRet | Sharpe | MaxDD | Calmar | Total | t(α) |")
        out("|---|---|---|---|---|---|---|---|")
        for h in WINDOWS:
            if len(sub) == 0:
                out(f"| T+{h}h | 0 | — | — | — | — | — | — |")
                continue
            m = equity_metrics(sub[f"ret_{h}h_net"].values, list(sub["ts"]))
            a, b, t = alpha_tstat(sub[f"ret_{h}h_net"].values,
                                  sub[f"btc_ret_{h}h"].values)
            if m["n"] == 0:
                out(f"| T+{h}h | 0 | — | — | — | — | — | — |")
                continue
            cal = f"{m['calmar']:.2f}" if m.get('calmar') is not None and not np.isnan(m.get('calmar', np.nan)) else "—"
            out(f"| T+{h}h | {m['n']} | {m['ann_ret']*100:+.1f}% | {m['sharpe']:.2f} | "
                f"{m['maxdd']*100:.1f}% | {cal} | {m['total_ret']*100:+.1f}% | {t:.2f} |")

    # Conjunctive
    out("\n### Conjunctive Gate — liq_z > 2.0 AND fund_z > 0.5\n")
    out(f"Events in last 5y: {len(res_conj)}\n")
    if len(res_conj) > 0:
        out("| Window | N (5y) | Mean | Sharpe(event) | α | β | t(α) |")
        out("|---|---|---|---|---|---|---|")
        for h in WINDOWS:
            s = summarize_rets(res_conj[f"ret_{h}h_net"].values)
            a, b, t = alpha_tstat(res_conj[f"ret_{h}h_net"].values,
                                  res_conj[f"btc_ret_{h}h"].values)
            out(f"| T+{h}h | {s['n']} | {s['mean']*100:+.2f}% | {s['sharpe']:.2f} | "
                f"{a*100:+.2f}% | {b:.2f} | {t:.2f} |")
        out("\nL12M:\n")
        out("| Window | N | AnnRet | Sharpe | MaxDD | Calmar |")
        out("|---|---|---|---|---|---|")
        sub = slice_by(res_conj, btc_end, 12).sort_values("ts")
        for h in WINDOWS:
            if len(sub) == 0:
                out(f"| T+{h}h | 0 | — | — | — | — |")
                continue
            m = equity_metrics(sub[f"ret_{h}h_net"].values, list(sub["ts"]))
            if m["n"] == 0:
                out(f"| T+{h}h | 0 | — | — | — | — |")
                continue
            cal = f"{m['calmar']:.2f}" if m.get('calmar') is not None and not np.isnan(m.get('calmar', np.nan)) else "—"
            out(f"| T+{h}h | {m['n']} | {m['ann_ret']*100:+.1f}% | {m['sharpe']:.2f} | "
                f"{m['maxdd']*100:.1f}% | {cal} |")

    # Sensitivity sweep
    out("\n### Sensitivity Sweep on liq_z Threshold (7d window, T+12h exit)\n")
    out("| liq_z > | N (5y) | Mean T+12h | Sharpe(event) T+12h | L12M N | L12M Sharpe(ann) | L12M Calmar | L12M MaxDD | L12M t(α) |")
    out("|---|---|---|---|---|---|---|---|---|")
    for thr in LIQ_Z_SWEEP:
        res_s = sweep_results[thr]
        if len(res_s) == 0:
            out(f"| {thr} | 0 | — | — | 0 | — | — | — | — |")
            continue
        s = summarize_rets(res_s["ret_12h_net"].values)
        sub = slice_by(res_s, btc_end, 12).sort_values("ts")
        if len(sub) == 0:
            out(f"| {thr} | {s['n']} | {s['mean']*100:+.2f}% | {s['sharpe']:.2f} | 0 | — | — | — | — |")
            continue
        m = equity_metrics(sub["ret_12h_net"].values, list(sub["ts"]))
        a, b, t = alpha_tstat(sub["ret_12h_net"].values, sub["btc_ret_12h"].values)
        cal = f"{m['calmar']:.2f}" if not np.isnan(m.get('calmar', np.nan)) else "—"
        out(f"| {thr} | {s['n']} | {s['mean']*100:+.2f}% | {s['sharpe']:.2f} | "
            f"{m['n']} | {m['sharpe']:.2f} | {cal} | {m['maxdd']*100:.1f}% | {t:.2f} |")

    # Verdict
    out("\n### Verdict\n")
    # Evaluate primary at best L12M window by Sharpe
    l12 = slice_by(res_primary, btc_end, 12).sort_values("ts") if len(res_primary) else pd.DataFrame()
    best = None
    best_h = None
    best_t = np.nan
    best_b = np.nan
    for h in WINDOWS:
        if len(l12) == 0:
            continue
        m = equity_metrics(l12[f"ret_{h}h_net"].values, list(l12["ts"]))
        if m["n"] == 0:
            continue
        if best is None or (not np.isnan(m.get("sharpe", np.nan)) and
                            (np.isnan(best["sharpe"]) or m["sharpe"] > best["sharpe"])):
            best = m
            best_h = h
            a, b, t = alpha_tstat(l12[f"ret_{h}h_net"].values, l12[f"btc_ret_{h}h"].values)
            best_t = t
            best_b = b

    if best is None:
        out("**No valid L12M events under primary spec.**\n")
        verdict = "KILL"
    else:
        sh_ok = (not np.isnan(best["sharpe"])) and best["sharpe"] > 2.0
        ca_ok = (not np.isnan(best.get("calmar", np.nan))) and best["calmar"] > 3.0
        dd_ok = (not np.isnan(best["maxdd"])) and best["maxdd"] > -0.25
        n_ok = best["n"] > 10
        alpha_ok = (not np.isnan(best_t)) and abs(best_t) > 1.5

        def chk(ok): return "PASS" if ok else "FAIL"

        out(f"Best L12M window: **T+{best_h}h** (primary spec).\n")
        out("| Metric | Value | Threshold | Check |")
        out("|---|---|---|---|")
        out(f"| Trades | {best['n']} | > 10 | {chk(n_ok)} |")
        out(f"| Sharpe (ann) | {best['sharpe']:.2f} | > 2.0 | {chk(sh_ok)} |")
        out(f"| Calmar | {best['calmar']:.2f} | > 3.0 | {chk(ca_ok)} |")
        out(f"| MaxDD | {best['maxdd']*100:.1f}% | > -25% | {chk(dd_ok)} |")
        out(f"| Alpha t-stat | {best_t:.2f} | > 1.5 | {chk(alpha_ok)} |")

        passed_all = sh_ok and ca_ok and dd_ok and n_ok and alpha_ok
        if passed_all:
            verdict = "PASS"
        elif n_ok and (sh_ok or alpha_ok):
            verdict = "NEEDS_TUNING"
        else:
            verdict = "KILL"

    out(f"\n**RESCUE VERDICT: {verdict}**\n")

    # Save
    existing = REPORT.read_text() if REPORT.exists() else ""
    REPORT.write_text(existing + "\n".join(lines))
    print(f"\nAppended rescue section to {REPORT}")
    print(f"VERDICT: {verdict}")
    if best is not None:
        print(f"L12M best window T+{best_h}h: n={best['n']} sharpe={best['sharpe']:.2f} "
              f"calmar={best.get('calmar', float('nan')):.2f} maxdd={best['maxdd']*100:.1f}% "
              f"t(α)={best_t:.2f}")


if __name__ == "__main__":
    main()
