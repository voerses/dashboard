"""
Profit Lock-in Overlay — Gate 1 Diagnostic + Rule Sweep
========================================================
Post-hoc overlay on existing baseline trade logs for s523c_growth and
s513_triple_trigger_swing.

Does NOT touch v4/ or strategies/. Reconstructs max favorable excursion (MFE)
from entry price and OHLC data, then tests whether closing (part of) the
position when it becomes unusually profitable unusually fast improves Calmar.

Phase 1: diagnostic — give-back ratio, velocity, time-to-peak, outsized moves.
Phase 2: rule sweep — (trigger × action) grid, post-hoc equity recompute.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from dataclasses import dataclass, field
from collections import defaultdict

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
ROOT = Path("/workspace/crypto_backtest")
OHLC_DIR = ROOT / "data" / "perp" / "binance" / "1h_ohlcv"
OUT_DIR = ROOT / "research"

BASELINES = {
    "s523c": {
        "trades": ROOT / "research/fragility_out/s523c_baseline/s523c_growth_12mo_50k_trades.json",
        "metrics": ROOT / "research/fragility_out/s523c_baseline/s523c_growth_12mo_50k_metrics.json",
        "equity": ROOT / "research/fragility_out/s523c_baseline/s523c_growth_12mo_50k_equity_curve.json",
        "start": pd.Timestamp("2025-04-05T16:00:00+00:00"),
        "capital": 50000.0,
    },
    "s513": {
        "trades": ROOT / "research/fragility_out/s513_baseline/s513_triple_trigger_swing_12mo_50k_trades.json",
        "metrics": ROOT / "research/fragility_out/s513_baseline/s513_triple_trigger_swing_12mo_50k_metrics.json",
        "equity": ROOT / "research/fragility_out/s513_baseline/s513_triple_trigger_swing_12mo_50k_equity_curve.json",
        "start": pd.Timestamp("2025-04-05T16:00:00+00:00"),
        "capital": 50000.0,
    },
}

# Cache of OHLC slices indexed by (token, start_ts)
_ohlc_cache: dict[str, pd.DataFrame] = {}


def load_ohlc(token: str) -> pd.DataFrame | None:
    if token in _ohlc_cache:
        return _ohlc_cache[token]
    p = OHLC_DIR / f"{token}_perp_1h.csv"
    if not p.exists():
        _ohlc_cache[token] = None  # type: ignore
        return None
    df = pd.read_csv(p)
    df["dt"] = pd.to_datetime(df["datetime"])
    _ohlc_cache[token] = df
    return df


def slice_for_trade(token: str, start_ts: pd.Timestamp, entry_bar: int, exit_bar: int) -> pd.DataFrame | None:
    df = load_ohlc(token)
    if df is None:
        return None
    idx0 = df.index[df["dt"] == start_ts]
    if len(idx0) == 0:
        return None
    off = int(idx0[0])
    lo = off + entry_bar
    hi = off + exit_bar + 1
    if hi > len(df):
        hi = len(df)
    if lo >= hi:
        return None
    return df.iloc[lo:hi][["dt", "open", "high", "low", "close"]].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Enrich trades with MFE, velocity, time-to-peak, leverage, ATR
# ---------------------------------------------------------------------------


def enrich_trade(tr: dict, start_ts: pd.Timestamp) -> dict | None:
    token = tr["token"]
    eb = int(tr["entry_bar"])
    xb = int(tr["exit_bar"])
    direction = int(tr["direction"])
    entry_px = float(tr["entry_price"])
    exit_px = float(tr["exit_price"])
    margin = float(tr["margin_usd"])
    pnl = float(tr["pnl"])
    funding = float(tr["funding_cost"])
    fees = float(tr["entry_fee"]) + float(tr["exit_fee"])

    bars = slice_for_trade(token, start_ts, eb, xb)
    if bars is None or len(bars) == 0:
        return None

    highs = bars["high"].to_numpy()
    lows = bars["low"].to_numpy()
    closes = bars["close"].to_numpy()

    # Per-bar cumulative MFE on price (fraction of entry), direction-aware
    if direction == 1:
        fav = (highs - entry_px) / entry_px
        adv = (lows - entry_px) / entry_px
    else:
        fav = (entry_px - lows) / entry_px
        adv = (entry_px - highs) / entry_px
    # Cumulative running max of favorable excursion
    run_mfe = np.maximum.accumulate(fav)
    mfe = float(run_mfe[-1])  # max favorable across the entire hold
    mfe_bar = int(np.argmax(fav))  # bar index (0 = bar after entry)
    mae = float(np.min(adv))  # max adverse (negative)

    # Realized price return at exit
    if direction == 1:
        realized_price = (exit_px - entry_px) / entry_px
    else:
        realized_price = (entry_px - exit_px) / entry_px

    # Infer leverage from pre-fee/pre-funding gross pnl
    gross_pnl = pnl + funding + fees  # approximate: remove costs to get raw price-return pnl
    lev = None
    if abs(realized_price) > 1e-6 and margin > 1e-6:
        lev = gross_pnl / margin / realized_price
        if not (0.5 <= lev <= 50):
            lev = None

    # Compute ATR(14) at entry bar using OHLC starting BEFORE the entry bar
    # Pull 20 bars before entry
    df = load_ohlc(token)
    atr_pct = None
    if df is not None:
        idx0 = df.index[df["dt"] == start_ts]
        if len(idx0):
            off = int(idx0[0])
            lo = off + eb - 14
            hi = off + eb + 1
            if lo >= 0 and hi <= len(df):
                sub = df.iloc[lo:hi]
                h = sub["high"].to_numpy()
                l = sub["low"].to_numpy()
                c = sub["close"].to_numpy()
                pc = np.concatenate([[c[0]], c[:-1]])
                tr_arr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
                atr_abs = float(np.mean(tr_arr[-14:]))
                atr_pct = atr_abs / entry_px  # as fraction of entry price

    # Velocity = mfe / (mfe_bar + 1), in fraction of entry per bar
    velocity = mfe / max(1, mfe_bar + 1)

    return {
        **tr,
        "mfe_price": mfe,       # max favorable as fraction of entry (unlevered price move)
        "mae_price": mae,       # max adverse (negative)
        "mfe_bar": mfe_bar,     # bar index of peak (0 = first bar after entry)
        "hold_bars": len(bars),
        "realized_price": realized_price,
        "leverage_inferred": lev,
        "atr_pct": atr_pct,
        "velocity_px_per_bar": velocity,
        "entry_ts": bars["dt"].iloc[0] if len(bars) else None,
    }


# ---------------------------------------------------------------------------
# Phase 1 diagnostics
# ---------------------------------------------------------------------------


def phase1_diag(enriched: list[dict]) -> dict:
    winners = [t for t in enriched if t["mfe_price"] > 0]
    n = len(enriched)
    nw = len(winners)

    # Give-back ratio: (mfe - realized) / mfe  -- only where mfe > 0
    gb = []
    for t in winners:
        m = t["mfe_price"]
        r = max(0.0, t["realized_price"])  # clip neg realized for interpretability
        if m > 1e-6:
            gb.append((m - r) / m)
    gb = np.array(gb)

    # Velocity across winners
    vel = np.array([t["velocity_px_per_bar"] for t in winners if t["velocity_px_per_bar"] is not None])

    # Time to peak
    tp_bars = np.array([t["mfe_bar"] + 1 for t in winners])
    tp_frac = np.array([(t["mfe_bar"] + 1) / max(1, t["hold_bars"]) for t in winners])

    # Outsized move frequency (unlevered price move)
    thresholds = [0.02, 0.05, 0.08, 0.10, 0.15, 0.20]
    outsized = {}
    for th in thresholds:
        hit = [t for t in enriched if t["mfe_price"] >= th]
        hit_pos = [t for t in hit if float(t["pnl"]) > 0]
        outsized[th] = {
            "pct_trades": len(hit) / max(1, n) * 100,
            "n_hit": len(hit),
            "pct_exit_positive": (len(hit_pos) / max(1, len(hit)) * 100) if hit else 0.0,
        }

    def q(a, ps):
        if len(a) == 0:
            return {p: float("nan") for p in ps}
        return {p: float(np.quantile(a, p)) for p in ps}

    return {
        "n_trades": n,
        "n_winners_mfe": nw,
        "give_back_ratio": {
            "mean": float(gb.mean()) if len(gb) else float("nan"),
            "quantiles": q(gb, [0.25, 0.5, 0.75, 0.9]),
            "n": int(len(gb)),
        },
        "velocity_px_per_bar": {
            "mean": float(vel.mean()) if len(vel) else float("nan"),
            "quantiles": q(vel, [0.25, 0.5, 0.75, 0.9, 0.95]),
        },
        "time_to_peak_bars": {
            "mean": float(tp_bars.mean()) if len(tp_bars) else float("nan"),
            "quantiles": q(tp_bars, [0.25, 0.5, 0.75, 0.9]),
        },
        "time_to_peak_frac_of_hold": {
            "mean": float(tp_frac.mean()) if len(tp_frac) else float("nan"),
            "quantiles": q(tp_frac, [0.25, 0.5, 0.75, 0.9]),
        },
        "outsized_frequency": outsized,
    }


# ---------------------------------------------------------------------------
# Phase 2 overlay
# ---------------------------------------------------------------------------
# Simulation note: we apply overlay per-trade. For a (trigger, action) combo,
# at each bar we check if trigger fires; if yes, apply action and recompute
# realized pnl for the trade. Then we aggregate to a per-day equity curve and
# recompute metrics.


def simulate_overlay(enriched: list[dict], trigger: dict, action: dict, start_ts: pd.Timestamp) -> list[dict]:
    """Return list of trades with modified pnl (overlay applied). Untouched trades
    keep original pnl. Cost basis: use inferred leverage to scale price moves to
    pnl magnitudes; costs (fees/funding) left unchanged (rough proxy)."""
    out = []
    for t in enriched:
        base_pnl = float(t["pnl"])
        lev = t.get("leverage_inferred")
        if lev is None:
            out.append({**t, "ovl_pnl": base_pnl, "ovl_fired": False, "ovl_bar": None})
            continue
        token = t["token"]
        eb = int(t["entry_bar"])
        xb = int(t["exit_bar"])
        direction = int(t["direction"])
        entry_px = float(t["entry_price"])
        margin = float(t["margin_usd"])
        funding = float(t["funding_cost"])
        fees = float(t["entry_fee"]) + float(t["exit_fee"])

        bars = slice_for_trade(token, start_ts, eb, xb)
        if bars is None or len(bars) == 0:
            out.append({**t, "ovl_pnl": base_pnl, "ovl_fired": False, "ovl_bar": None})
            continue

        highs = bars["high"].to_numpy()
        lows = bars["low"].to_numpy()
        closes = bars["close"].to_numpy()

        # Favorable excursion per bar
        if direction == 1:
            fav = (highs - entry_px) / entry_px
            adv = (lows - entry_px) / entry_px
        else:
            fav = (entry_px - lows) / entry_px
            adv = (entry_px - highs) / entry_px

        # Trigger condition: find first bar where unrealized (margin %) >= threshold
        trig_type = trigger["type"]
        if trig_type == "T-ABS":
            px_thresh = trigger["x_pct"] / 100.0 / lev  # convert margin% to price-move%
        elif trig_type == "T-ATR":
            atr_pct = t.get("atr_pct")
            if atr_pct is None or atr_pct <= 0:
                out.append({**t, "ovl_pnl": base_pnl, "ovl_fired": False, "ovl_bar": None})
                continue
            px_thresh = trigger["k"] * atr_pct  # price move threshold
        elif trig_type == "T-TIME":
            px_thresh = trigger["x_pct"] / 100.0 / lev
        elif trig_type == "T-VEL":
            # velocity threshold in fraction-of-entry per bar
            px_thresh = None  # handled specially
        else:
            out.append({**t, "ovl_pnl": base_pnl, "ovl_fired": False, "ovl_bar": None})
            continue

        fire_bar = None
        if trig_type == "T-VEL":
            vth = trigger["v_thresh"]
            min_px = trigger.get("min_px", 0.005)  # need to clear noise floor
            for i in range(len(fav)):
                v = fav[i] / (i + 1)
                if v >= vth and fav[i] >= min_px:
                    fire_bar = i
                    break
        else:
            if trig_type == "T-TIME":
                max_bar = trigger["t_bars"]
            else:
                max_bar = len(fav)
            for i in range(min(len(fav), max_bar)):
                if fav[i] >= px_thresh:
                    fire_bar = i
                    break

        if fire_bar is None:
            out.append({**t, "ovl_pnl": base_pnl, "ovl_fired": False, "ovl_bar": None})
            continue

        # Action: apply at the moment trigger fires. Use fav[fire_bar] as the
        # locked-in price move (conservative: assume fill at threshold price
        # which equals px_thresh or fav if greater).
        lock_fav = fav[fire_bar]  # price move locked in at fire
        if trig_type != "T-VEL" and px_thresh is not None:
            # Assume fill at threshold (intrabar touch), not peak
            lock_fav = px_thresh

        act = action["type"]
        # For "remainder" tracking, walk forward from fire_bar applying new stop
        # rules and use intrabar high/low for trigger tests.

        def remainder_realized(stop_px_move: float, trail_atr_mult: float | None) -> float:
            """Sim from fire_bar+1 to end-of-trade. stop_px_move is a price-move
            threshold (fraction of entry); if adv breaches it, exit at that level.
            If trail_atr_mult provided, also trail from running fav high.
            Otherwise fall back to original exit price."""
            running_fav = lock_fav
            atr_pct = t.get("atr_pct") or 0.01
            for j in range(fire_bar + 1, len(fav)):
                if fav[j] > running_fav:
                    running_fav = fav[j]
                # stop rule
                stop_level = stop_px_move
                if trail_atr_mult is not None:
                    trail_level = running_fav - trail_atr_mult * atr_pct
                    stop_level = max(stop_level, trail_level)
                if adv[j] <= stop_level:
                    return stop_level  # hit stop
            # no stop hit — use original exit price return
            return float(t["realized_price"])

        if act == "A-FULL":
            new_price_return = lock_fav
        elif act == "A-50":
            rem = remainder_realized(stop_px_move=0.0, trail_atr_mult=None)  # BE
            new_price_return = 0.5 * lock_fav + 0.5 * rem
        elif act == "A-75":
            atr_pct = t.get("atr_pct") or 0.01
            rem = remainder_realized(stop_px_move=atr_pct, trail_atr_mult=None)  # entry + 1 ATR
            new_price_return = 0.75 * lock_fav + 0.25 * rem
        elif act == "A-25":
            # tighten trail to 1*ATR
            rem = remainder_realized(stop_px_move=-1.0, trail_atr_mult=1.0)
            new_price_return = 0.25 * lock_fav + 0.75 * rem
        elif act == "A-HALFLOCK":
            # move stop to entry + 0.5 * lock_fav, no partial close
            new_price_return = remainder_realized(stop_px_move=0.5 * lock_fav, trail_atr_mult=None)
        else:
            new_price_return = float(t["realized_price"])

        # Convert back to pnl. Gross pnl_new = margin * lev * new_price_return.
        # Keep funding and fees unchanged. (Note: if trade is cut short, funding
        # would be reduced in reality — this makes overlay slightly pessimistic.)
        new_gross = margin * lev * new_price_return
        new_pnl = new_gross + funding - fees  # funding is already negative; fees subtract
        # But original pnl formula: base_pnl = gross + funding - fees. So ensure
        # consistency: new_pnl = base_pnl + margin*lev*(new_price_return - realized_price)
        new_pnl_consistent = base_pnl + margin * lev * (new_price_return - float(t["realized_price"]))

        out.append({
            **t,
            "ovl_pnl": new_pnl_consistent,
            "ovl_fired": True,
            "ovl_bar": fire_bar,
            "ovl_new_price_return": new_price_return,
        })
    return out


# ---------------------------------------------------------------------------
# Equity / metrics
# ---------------------------------------------------------------------------


def build_equity_curve(trades: list[dict], start_ts: pd.Timestamp, capital: float, pnl_key: str = "pnl") -> pd.Series:
    """Naive: attribute each trade's pnl to its exit date (bar-indexed)."""
    # Exit datetime = start_ts + exit_bar hours
    by_day: dict[pd.Timestamp, float] = defaultdict(float)
    for t in trades:
        exit_ts = start_ts + pd.Timedelta(hours=int(t["exit_bar"]))
        d = exit_ts.floor("D")
        by_day[d] += float(t[pnl_key])
    if not by_day:
        return pd.Series([capital])
    days = sorted(by_day.keys())
    full_days = pd.date_range(days[0], days[-1], freq="D", tz="UTC")
    eq = [capital]
    for d in full_days:
        eq.append(eq[-1] + by_day.get(d, 0.0))
    return pd.Series(eq[1:], index=full_days)


def metrics(eq: pd.Series, capital: float) -> dict:
    if len(eq) < 2:
        return {"total_return_pct": 0, "sharpe": 0, "max_dd_pct": 0, "calmar": 0}
    total_return = (eq.iloc[-1] / capital - 1) * 100
    daily_ret = eq.pct_change().dropna()
    if daily_ret.std() > 0:
        sharpe = daily_ret.mean() / daily_ret.std() * math.sqrt(365)
    else:
        sharpe = 0.0
    running_max = eq.cummax()
    dd = (eq / running_max - 1) * 100
    max_dd = float(dd.min())
    ann_ret = total_return  # ~1yr window
    calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0.0
    return {
        "total_return_pct": float(total_return),
        "sharpe": float(sharpe),
        "max_dd_pct": float(max_dd),
        "calmar": float(calmar),
    }


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def build_trigger_grid(phase1_stats: dict) -> list[dict]:
    # Velocity thresholds from Phase 1 distribution
    vq = phase1_stats["velocity_px_per_bar"]["quantiles"]
    v90 = vq.get(0.9, 0.01) if vq else 0.01
    v95 = vq.get(0.95, 0.02) if vq else 0.02
    triggers = []
    # Absolute PnL triggers (margin %)
    for x in (5, 8, 12, 15, 20):
        triggers.append({"type": "T-ABS", "x_pct": x, "name": f"ABS{x}"})
    # ATR triggers (price space)
    for k in (2, 3, 4, 5, 6):
        triggers.append({"type": "T-ATR", "k": k, "name": f"ATR{k}"})
    # Time-boxed ABS triggers
    for x, T in [(8, 8), (12, 8), (8, 24), (12, 24), (15, 48)]:
        triggers.append({"type": "T-TIME", "x_pct": x, "t_bars": T, "name": f"TIME{x}_{T}b"})
    # Velocity
    triggers.append({"type": "T-VEL", "v_thresh": v90, "name": "VEL90"})
    triggers.append({"type": "T-VEL", "v_thresh": v95, "name": "VEL95"})
    return triggers


ACTIONS = [
    {"type": "A-FULL", "name": "FULL"},
    {"type": "A-50", "name": "50BE"},
    {"type": "A-75", "name": "75_1ATR"},
    {"type": "A-25", "name": "25_tight"},
    {"type": "A-HALFLOCK", "name": "HALFLOCK"},
]


def run_for_strategy(strat: str) -> dict:
    cfg = BASELINES[strat]
    trades_raw = json.load(open(cfg["trades"]))
    start = cfg["start"]
    capital = cfg["capital"]

    print(f"\n=== {strat}: enriching {len(trades_raw)} trades ===")
    enriched = []
    missing = 0
    for tr in trades_raw:
        e = enrich_trade(tr, start)
        if e is None:
            missing += 1
            continue
        enriched.append(e)
    print(f"  enriched: {len(enriched)}, missing: {missing}")

    # Baseline metrics from enriched trades (sanity)
    base_eq = build_equity_curve(enriched, start, capital, pnl_key="pnl")
    base_m = metrics(base_eq, capital)
    print(f"  reconstructed baseline metrics: {base_m}")

    # Phase 1 diagnostics
    p1 = phase1_diag(enriched)
    print(f"  Phase 1 give-back median={p1['give_back_ratio']['quantiles'][0.5]:.3f} "
          f"p90={p1['give_back_ratio']['quantiles'][0.9]:.3f}")

    # Phase 2 grid
    triggers = build_trigger_grid(p1)
    results = []

    print(f"  Phase 2: sweeping {len(triggers)} triggers x {len(ACTIONS)} actions...")
    for trig in triggers:
        for act in ACTIONS:
            ov = simulate_overlay(enriched, trig, act, start)
            eq = build_equity_curve(ov, start, capital, pnl_key="ovl_pnl")
            m = metrics(eq, capital)
            n_fired = sum(1 for t in ov if t["ovl_fired"])
            results.append({
                "trigger": trig["name"],
                "action": act["name"],
                "trigger_cfg": trig,
                "action_cfg": act,
                "n_fired": n_fired,
                **m,
            })

    # Rank by calmar improvement (with tie break on return)
    base_calmar = base_m["calmar"]
    base_ret = base_m["total_return_pct"]
    base_dd = base_m["max_dd_pct"]
    for r in results:
        r["calmar_delta_pct"] = (r["calmar"] / base_calmar - 1) * 100 if base_calmar else 0.0
        r["return_delta_pct"] = (r["total_return_pct"] - base_ret)  # absolute delta in return pct
        r["return_ratio"] = r["total_return_pct"] / base_ret if base_ret else 0.0
        r["dd_delta_pct"] = r["max_dd_pct"] - base_dd  # less negative is better => positive delta good
    results.sort(key=lambda r: (r["calmar_delta_pct"], r["return_ratio"]), reverse=True)

    return {
        "baseline_metrics": base_m,
        "phase1": p1,
        "results": results,
        "n_enriched": len(enriched),
        "n_missing": missing,
    }


def fmt_pct(x, digits=2):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x:.{digits}f}"


def write_report(all_results: dict):
    lines = []
    lines.append("# Profit Lock-in Overlay — Gate 1 Report")
    lines.append("")
    lines.append(f"_Generated {pd.Timestamp.now(tz='UTC').isoformat()}_")
    lines.append("")
    lines.append("Post-hoc overlay study: does locking in profits when a trade becomes unusually")
    lines.append("profitable unusually fast improve Calmar? No changes to v4/ or strategies/.")
    lines.append("MFE reconstructed from OHLC + entry_bar/exit_bar; leverage inferred from")
    lines.append("gross pnl / margin / realized_price_return per trade.")
    lines.append("")

    # Baselines
    lines.append("## Baselines")
    lines.append("")
    lines.append("| Strategy | Total Return % | Sharpe | MaxDD % | Calmar | Trades (enriched) | Missing |")
    lines.append("|----------|---------------:|-------:|--------:|-------:|------------------:|--------:|")
    for s, r in all_results.items():
        bm = r["baseline_metrics"]
        lines.append(f"| {s} | {bm['total_return_pct']:.2f} | {bm['sharpe']:.2f} | {bm['max_dd_pct']:.2f} | {bm['calmar']:.2f} | {r['n_enriched']} | {r['n_missing']} |")
    lines.append("")
    lines.append("Note: 'reconstructed' baselines are built by summing per-trade pnl into daily buckets,")
    lines.append("so they differ modestly from the portfolio simulator's own equity/Sharpe/Calmar which")
    lines.append("track margin utilisation intrabar. Absolute numbers: s523c official Calmar 19.4 (37% DD),")
    lines.append("s513 official Calmar 12.9 (16.7% DD). Overlay *deltas* are the meaningful signal.")
    lines.append("")

    # Phase 1 per strategy
    for s, r in all_results.items():
        p1 = r["phase1"]
        lines.append(f"## Phase 1 Diagnostic — {s}")
        lines.append("")
        lines.append(f"Total trades: {p1['n_trades']}, winners (positive MFE): {p1['n_winners_mfe']}")
        lines.append("")

        gb = p1["give_back_ratio"]
        lines.append("### Give-back ratio (max_unrealized − realized) / max_unrealized")
        lines.append("")
        lines.append("| stat | value |")
        lines.append("|------|------:|")
        lines.append(f"| n | {gb['n']} |")
        lines.append(f"| mean | {gb['mean']:.3f} |")
        for q, v in gb["quantiles"].items():
            lines.append(f"| p{int(q*100)} | {v:.3f} |")
        lines.append("")

        v = p1["velocity_px_per_bar"]
        lines.append("### Velocity (MFE price-move fraction per bar, winners only)")
        lines.append("")
        lines.append("| stat | value |")
        lines.append("|------|------:|")
        lines.append(f"| mean | {v['mean']:.5f} |")
        for q, val in v["quantiles"].items():
            lines.append(f"| p{int(q*100)} | {val:.5f} |")
        lines.append("")

        tp = p1["time_to_peak_bars"]
        tpf = p1["time_to_peak_frac_of_hold"]
        lines.append("### Time to peak (bars from entry to MFE)")
        lines.append("")
        lines.append("| stat | bars | frac of hold |")
        lines.append("|------|-----:|-------------:|")
        lines.append(f"| mean | {tp['mean']:.1f} | {tpf['mean']:.3f} |")
        for q in [0.25, 0.5, 0.75, 0.9]:
            lines.append(f"| p{int(q*100)} | {tp['quantiles'][q]:.1f} | {tpf['quantiles'][q]:.3f} |")
        lines.append("")

        lines.append("### Outsized-move frequency (unlevered price-move MFE)")
        lines.append("")
        lines.append("| threshold (price move) | % of trades reached | n | % of hit trades exit positive |")
        lines.append("|-----------------------:|--------------------:|---:|------------------------------:|")
        for th, stats in p1["outsized_frequency"].items():
            lines.append(f"| {th*100:.0f}% | {stats['pct_trades']:.1f}% | {stats['n_hit']} | {stats['pct_exit_positive']:.1f}% |")
        lines.append("")

    # Phase 2 results
    for s, r in all_results.items():
        lines.append(f"## Phase 2 Rule Sweep — {s}")
        lines.append("")
        lines.append("Top 10 (trigger × action) combos sorted by Calmar improvement % vs reconstructed baseline.")
        lines.append("Calmar delta < 0 means worse than baseline. Return ratio = new_return / baseline_return.")
        lines.append("")
        lines.append("| Trigger | Action | Fired | Return % | Sharpe | MaxDD % | Calmar | ΔCalmar % | Ret Ratio |")
        lines.append("|---------|--------|------:|---------:|-------:|--------:|-------:|----------:|----------:|")
        for row in r["results"][:10]:
            lines.append(
                f"| {row['trigger']} | {row['action']} | {row['n_fired']} | "
                f"{row['total_return_pct']:.1f} | {row['sharpe']:.2f} | {row['max_dd_pct']:.2f} | "
                f"{row['calmar']:.2f} | {row['calmar_delta_pct']:+.1f} | {row['return_ratio']:.2f} |"
            )
        lines.append("")

        # Best combo details
        best = r["results"][0]
        bm = r["baseline_metrics"]
        lines.append(f"### Best combo for {s}: `{best['trigger']} × {best['action']}`")
        lines.append("")
        lines.append("| Metric | Baseline | Overlay | Delta |")
        lines.append("|--------|---------:|--------:|------:|")
        lines.append(f"| Total Return % | {bm['total_return_pct']:.2f} | {best['total_return_pct']:.2f} | {best['total_return_pct'] - bm['total_return_pct']:+.2f} |")
        lines.append(f"| Sharpe | {bm['sharpe']:.2f} | {best['sharpe']:.2f} | {best['sharpe'] - bm['sharpe']:+.2f} |")
        lines.append(f"| MaxDD % | {bm['max_dd_pct']:.2f} | {best['max_dd_pct']:.2f} | {best['max_dd_pct'] - bm['max_dd_pct']:+.2f} |")
        lines.append(f"| Calmar | {bm['calmar']:.2f} | {best['calmar']:.2f} | {best['calmar'] - bm['calmar']:+.2f} |")
        lines.append(f"| Trades fired | — | {best['n_fired']} | — |")
        lines.append("")

    # Verdict
    s523 = all_results["s523c"]
    s513 = all_results["s513"]
    best_523 = s523["results"][0]
    best_513 = s513["results"][0]

    lines.append("## Verdict")
    lines.append("")
    pass_523 = best_523["calmar_delta_pct"] >= 15 and best_523["return_ratio"] >= 0.8
    pass_513 = best_513["calmar_delta_pct"] >= 15 and best_513["return_ratio"] >= 0.8

    if pass_523 or pass_513:
        verdict = "PASS"
    else:
        # NEEDS_TUNING check
        dd_improve_523 = best_523["dd_delta_pct"] >= 0.25 * abs(s523["baseline_metrics"]["max_dd_pct"])
        dd_improve_513 = best_513["dd_delta_pct"] >= 0.25 * abs(s513["baseline_metrics"]["max_dd_pct"])
        if dd_improve_523 or dd_improve_513:
            verdict = "NEEDS_TUNING"
        else:
            verdict = "KILL"

    lines.append(f"**{verdict}**")
    lines.append("")
    lines.append(f"- s523c best: `{best_523['trigger']} × {best_523['action']}` → "
                 f"Calmar Δ {best_523['calmar_delta_pct']:+.1f}%, return ratio {best_523['return_ratio']:.2f}, DD Δ {best_523['dd_delta_pct']:+.2f}pp")
    lines.append(f"- s513  best: `{best_513['trigger']} × {best_513['action']}` → "
                 f"Calmar Δ {best_513['calmar_delta_pct']:+.1f}%, return ratio {best_513['return_ratio']:.2f}, DD Δ {best_513['dd_delta_pct']:+.2f}pp")
    lines.append("")

    if verdict == "PASS":
        lines.append("### Gate 2 dedup check")
        lines.append("")
        lines.append("Before promoting: verify the lock-in trigger does not double-fire with existing")
        lines.append("exit handlers in `v4/`:")
        lines.append("")
        lines.append("- `BreakevenRatchet`: moves stop to breakeven after X R multiple. A-50/A-HALFLOCK")
        lines.append("  conflict directly — if ratchet is already active at X=1R and overlay fires on ABS5,")
        lines.append("  the stop-to-BE would be applied twice.")
        lines.append("- `Trailing` stop handler: A-25 (tighten trail to 1×ATR) would override the default")
        lines.append("  5×ATR trail. Verify per-strategy whether overlay trail is strictly tighter.")
        lines.append("- Max-hold exit: untouched (overlay fires earlier).")
        lines.append("")
        lines.append("### Gate 3 prototype path")
        lines.append("")
        lines.append("Add a new exit handler `v4/exits/profit_lockin.py` implementing the winning combo as a")
        lines.append("per-position on_bar hook. Mount in the s523c_growth strategy config behind a feature")
        lines.append("flag. Run full walk-forward before any paper-trade wiring.")
    elif verdict == "NEEDS_TUNING":
        lines.append("Edge exists on DD but return cost too high. Suggest: narrower trigger (T-TIME with")
        lines.append("tighter bar cap), or A-25/A-HALFLOCK instead of A-FULL to preserve upside.")
    else:
        lines.append("No combo meaningfully improves Calmar without wrecking return. Kill the mission.")
    lines.append("")

    report_path = OUT_DIR / "profit_lockin_gate1_report.md"
    report_path.write_text("\n".join(lines))
    print(f"\nWrote {report_path}")
    return report_path


def main():
    all_results = {}
    for strat in ("s523c", "s513"):
        all_results[strat] = run_for_strategy(strat)
    write_report(all_results)

    # Print headline
    print("\n=== HEADLINE ===")
    for s, r in all_results.items():
        bm = r["baseline_metrics"]
        best = r["results"][0]
        p1 = r["phase1"]
        gb = p1["give_back_ratio"]
        print(f"{s}: baseline Calmar {bm['calmar']:.2f}  →  best {best['trigger']}/{best['action']} "
              f"Calmar {best['calmar']:.2f} (Δ {best['calmar_delta_pct']:+.1f}%)  "
              f"ret_ratio={best['return_ratio']:.2f}  "
              f"give-back median={gb['quantiles'][0.5]:.3f} p90={gb['quantiles'][0.9]:.3f}")


if __name__ == "__main__":
    main()
