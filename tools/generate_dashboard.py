#!/usr/bin/env python3
"""
Trading Dashboard Generator
============================
Generates a self-contained HTML dashboard for monitoring simulation runs.
Each simulation run has its own capital, strategies, and tokens.
Trades are enriched with indicator snapshots showing exactly why each trade was triggered.

Usage:
    python tools/generate_dashboard.py --run-backtest --push
    python tools/generate_dashboard.py --run-backtest --months 1
    python tools/generate_dashboard.py --simulations simulations.json --run-backtest --push
"""

import sys, os, json, argparse, subprocess
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "v3"))

import numpy as np
import pandas as pd
from v3.universe import get_fee_rate

PROJECT_ROOT = Path(__file__).parent.parent
DOCS_DIR = PROJECT_ROOT / "docs"
DATA_DIR = PROJECT_ROOT / "data"

DEFAULT_TOKENS = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX",
    "LINK", "DOT", "UNI", "ARB", "OP", "APT", "SUI",
]

EXIT_REASONS = {
    0: "stop", 1: "target_5r", 2: "target", 3: "regime",
    4: "overbought", 5: "mean_reached", 6: "max_hold", 7: "liquidation",
}

REGIME_NAMES = {0: "CRISIS", 1: "QUIET", 2: "UPTREND", 3: "RANGE", 4: "DOWNTREND"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def detect_market(sname):
    if any(k in sname for k in ["hedged", "regime_spot_perp"]):
        return "combined"
    if any(k in sname for k in ["perp", "funding", "leveraged", "carry", "basis"]):
        return "perp"
    return "spot"


def available_tokens(market):
    tokens = set()
    for cd in [DATA_DIR / market / "1h_cache", DATA_DIR / "1h_cache"]:
        if cd.exists():
            for f in cd.glob("*.parquet"):
                tokens.add(f.stem.replace("_1h", ""))
    return tokens


def get_parquet_path(token, mtype):
    pq = DATA_DIR / mtype / "1h_cache" / f"{token}_1h.parquet"
    if not pq.exists():
        pq = DATA_DIR / "1h_cache" / f"{token}_1h.parquet"
    return pq if pq.exists() else None


# ---------------------------------------------------------------------------
# Signal enrichment — attach indicator snapshots to each trade
# ---------------------------------------------------------------------------
def enrich_trades(trades, eng, token, mtype, sname, strat_fn):
    """Add indicator values at entry_bar to each trade for signal transparency."""
    pq = get_parquet_path(token, mtype)
    if pq is None:
        return

    try:
        df = pd.read_parquet(pq)
        ctx = eng._build_context(token, df)
        if ctx is None:
            return
        sr = strat_fn(ctx)
    except Exception:
        return

    ind = ctx.ind_1h
    n = len(ind['close'])

    # Extract per-bar arrays from strategy result
    lev_arr = sr.leverage if isinstance(sr.leverage, np.ndarray) else None
    sm_arr = sr.stop_mult if isinstance(sr.stop_mult, np.ndarray) else None

    for t in trades:
        eb = int(t.get('entry_bar', 0))
        if eb >= n:
            continue

        sig = {}

        # Core 1h indicators
        for key in ['adx', 'rsi', 'vol_ratio', 'atr', 'vol_20', 'ret_1']:
            arr = ind.get(key)
            if arr is not None and eb < len(arr):
                sig[key] = round(float(arr[eb]), 4)

        sig['close'] = round(float(ind['close'][eb]), 2)
        sig['ema_20'] = round(float(ind['ema_20'][eb]), 2)
        sig['above_ema20'] = bool(ind['close'][eb] > ind['ema_20'][eb])

        # Regime
        if hasattr(ctx, 'regime_1h') and ctx.regime_1h is not None and eb < len(ctx.regime_1h):
            sig['regime'] = REGIME_NAMES.get(int(ctx.regime_1h[eb]), '?')

        # 24h return from custom indicators
        if hasattr(ctx, 'custom') and 'ret_24h' in ctx.custom:
            r24 = ctx.custom['ret_24h']
            if eb < len(r24):
                sig['ret_24h'] = round(float(r24[eb]) * 100, 2)

        # Leverage and stops from strategy result
        if lev_arr is not None and eb < len(lev_arr):
            sig['leverage'] = round(float(lev_arr[eb]), 1)
        if sm_arr is not None and eb < len(sm_arr):
            sig['stop_atr'] = round(float(sm_arr[eb]), 2)

        # Direction
        dir_val = t.get('direction', None)
        if dir_val is None and hasattr(sr, 'direction') and eb < len(sr.direction):
            dir_val = int(sr.direction[eb])
            t['direction'] = dir_val

        # Strategy-specific signal text
        if 's11' in sname:
            ret1_pct = sig.get('ret_1', 0) * 100
            sig['signal_short'] = (
                f"Burst {ret1_pct:+.1f}% | ADX {sig.get('adx', 0):.0f} | "
                f"Vol {sig.get('vol_ratio', 0):.1f}x"
            )
        elif 's33' in sname:
            adx_s = max(0, min(1, (sig.get('adx', 0) - 15) / 35))
            mom_s = max(0, min(1, abs(sig.get('ret_24h', 0)) / 15))
            sig['adx_score'] = round(adx_s, 2)
            sig['mom_score'] = round(mom_s, 2)
            d = 'SHORT' if dir_val == -1 else 'LONG'
            lev = sig.get('leverage', 1)
            sig['signal_short'] = (
                f"{d} | ADX {sig.get('adx', 0):.0f} | "
                f"24h {sig.get('ret_24h', 0):+.1f}% | Lev {lev}x"
            )
        else:
            sig['signal_short'] = f"ADX {sig.get('adx', 0):.0f} | RSI {sig.get('rsi', 0):.0f}"

        t['signal'] = sig


# ---------------------------------------------------------------------------
# Backtest runner
# ---------------------------------------------------------------------------
def run_backtest_for_sim(sim, months):
    """Run backtests for a single simulation run and return enriched trade data."""
    from engine import Engine
    from dateutil.relativedelta import relativedelta

    sid = sim["id"]
    cap = sim["capital"]
    strats = sim["strategies"]
    tokens = sim.get("tokens", DEFAULT_TOKENS)
    exchange = sim.get("exchange", "binance")
    cutoff = (datetime.now(timezone.utc) - relativedelta(months=months)).strftime("%Y-%m-%d")

    print(f"\n{'='*60}")
    print(f"Simulation: {sim['name']} (${cap:,.0f}, {len(strats)} strategies)")
    print(f"{'='*60}")

    cap_per_strat = cap / max(len(strats), 1)
    sim_result = {
        "id": sid, "name": sim["name"], "capital": cap,
        "strategies": [], "all_trades": [],
    }

    for sname in strats:
        try:
            mod = __import__(f"strategies.{sname}", fromlist=["strategy"])
            strat_fn = mod.strategy
        except Exception as e:
            print(f"  SKIP {sname}: {e}")
            continue

        mtype = detect_market(sname)
        if mtype == "combined":
            print(f"  SKIP {sname}: combined not supported yet")
            continue

        eng = Engine(data_dir=str(DATA_DIR), market=mtype)
        avail = available_tokens(mtype)
        strat_tokens = [t for t in tokens if t in avail]
        if not strat_tokens:
            print(f"  SKIP {sname}: no {mtype} tokens in cache")
            continue

        all_trades = []
        for token in strat_tokens:
            try:
                result = eng.backtest_token(strat_fn, token)
            except Exception as e:
                print(f"    {token}: ERROR - {e}")
                continue
            if result is None:
                continue
            trades = result.get("trades", [])
            if not trades:
                continue

            # Attach timestamps and metadata
            pq = get_parquet_path(token, mtype)
            try:
                df = pd.read_parquet(pq)
                idx = df.index if isinstance(df.index, pd.DatetimeIndex) else pd.to_datetime(df.iloc[:, 0])
                n_bars = len(idx)
            except Exception:
                idx, n_bars = None, 99999

            for t in trades:
                t["token"] = token
                t["strategy"] = sname
                t["market_type"] = mtype
                t["simulation"] = sid
                if idx is not None:
                    eb, xb = int(t.get("entry_bar", 0)), int(t.get("exit_bar", 0))
                    if eb < len(idx): t["entry_time"] = str(idx[eb])
                    if xb < len(idx): t["exit_time"] = str(idx[xb])
                er = t.get("exit_reason", "")
                if isinstance(er, (int, float)):
                    t["exit_reason"] = EXIT_REASONS.get(int(er), str(er))
                t["status"] = "open" if int(t.get("exit_bar", 0)) >= n_bars - 2 else "closed"

            # Enrich with signal data
            enrich_trades(trades, eng, token, mtype, sname, strat_fn)
            all_trades.extend(trades)
            print(f"    {token}: {len(trades)} trades (signals attached)")

        # Filter to recent window
        recent = [t for t in all_trades if t.get("exit_time", "9999") >= cutoff]

        # Rebase PnL to fixed capital
        tok_set = set(t["token"] for t in recent)
        n_tok = max(len(tok_set), 1)
        equity = cap_per_strat

        for t in sorted(recent, key=lambda x: x.get("entry_bar", 0)):
            ret = t.get("return_pct", 0) / 100.0
            alloc = equity / n_tok
            gross = alloc * ret

            # Per-trade market type (spot/perp) — falls back to strategy-level mtype
            t_mtype = (t.get("market_type") or mtype).lower()
            avg_lev = t.get("signal", {}).get("leverage", 3.0 if t_mtype == "perp" else 1.0)
            notional = alloc * avg_lev
            fee_rate = get_fee_rate(exchange, t_mtype if t_mtype in ("spot", "perp") else "spot")
            exch_fee = notional * fee_rate * 2

            orig_pos = abs(t.get("position_usd", 0))
            orig_fund = abs(t.get("funding_cost", 0))
            fund = orig_fund * (notional / orig_pos) if orig_pos > 0 else 0

            net = gross - exch_fee - fund
            t["pnl"] = net
            t["position_usd"] = notional
            t["exchange_fee"] = exch_fee
            t["funding_cost"] = fund
            equity += net

        strat_result = {
            "name": sname, "market": mtype.upper(),
            "trades": recent, "starting_capital": cap_per_strat,
            "final_equity": equity, "pnl": equity - cap_per_strat,
            "return_pct": (equity - cap_per_strat) / cap_per_strat * 100,
            "n_trades": len(recent),
            "n_open": sum(1 for t in recent if t["status"] == "open"),
            "wins": sum(1 for t in recent if t.get("pnl", 0) > 0),
            "exchange_fees": sum(t.get("exchange_fee", 0) for t in recent),
            "funding_fees": sum(abs(t.get("funding_cost", 0)) for t in recent),
        }
        strat_result["win_rate"] = strat_result["wins"] / max(strat_result["n_trades"], 1) * 100

        sim_result["strategies"].append(strat_result)
        sim_result["all_trades"].extend(recent)

        print(f"  {sname}: {len(all_trades)}->{len(recent)} trades, "
              f"PnL ${strat_result['pnl']:,.0f} ({strat_result['return_pct']:+.1f}%)")

    # Simulation-level aggregation
    sim_result["final_equity"] = sum(s["final_equity"] for s in sim_result["strategies"])
    sim_result["pnl"] = sim_result["final_equity"] - cap
    sim_result["return_pct"] = sim_result["pnl"] / cap * 100 if cap > 0 else 0
    sim_result["n_trades"] = len(sim_result["all_trades"])

    print(f"  SIM TOTAL: equity ${sim_result['final_equity']:,.0f}, "
          f"PnL ${sim_result['pnl']:,.0f} ({sim_result['return_pct']:+.1f}%)")

    return sim_result


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------
def generate_html(sims_data):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Strip redundant data: strategies[].trades is a duplicate of all_trades
    slim = json.loads(json.dumps(sims_data, default=str))
    drop_trade_keys = {'entry_bar', 'exit_bar', 'simulation'}
    for sim in slim:
        for st in sim.get('strategies', []):
            st.pop('trades', None)
        for t in sim.get('all_trades', []):
            for k in drop_trade_keys:
                t.pop(k, None)
            for k in ('pnl', 'position_usd', 'exchange_fee', 'funding_cost'):
                if k in t and isinstance(t[k], float):
                    t[k] = round(t[k], 2)
            sig = t.get('signal', {})
            for k in list(sig.keys()):
                if isinstance(sig[k], float):
                    sig[k] = round(sig[k], 4)
    data_json = json.dumps(slim, separators=(',', ':'))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<title>Trading Dashboard</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:-apple-system,'Segoe UI',system-ui,sans-serif; background:#0a0e17; color:#c9d1d9; font-size:13px; }}

/* Header */
.header {{ background:#0d1117; padding:12px 20px; border-bottom:1px solid #21262d; display:flex; justify-content:space-between; align-items:center; position:sticky; top:0; z-index:100; }}
.header h1 {{ font-size:1.1em; color:#58a6ff; font-weight:600; }}
.header .meta {{ color:#484f58; font-size:0.75em; }}

/* Sim tabs */
.sim-tabs {{ display:flex; gap:4px; padding:8px 20px; background:#0d1117; border-bottom:1px solid #21262d; }}
.stab {{ padding:6px 14px; border-radius:6px; cursor:pointer; background:transparent; color:#484f58; border:1px solid transparent; font-size:0.8em; transition:all 0.15s; }}
.stab:hover {{ color:#c9d1d9; background:#161b22; }}
.stab.active {{ background:#1f6feb; color:#fff; font-weight:600; }}

/* KPIs */
.kpi-row {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:8px; padding:12px 20px; }}
.kpi {{ background:#161b22; border:1px solid #21262d; border-radius:6px; padding:10px; }}
.kpi .lbl {{ color:#484f58; font-size:0.65em; text-transform:uppercase; letter-spacing:0.8px; }}
.kpi .val {{ font-size:1.3em; font-weight:700; margin-top:1px; }}

/* Colors */
.g {{ color:#3fb950; }} .r {{ color:#f85149; }} .b {{ color:#58a6ff; }} .y {{ color:#d29922; }} .m {{ color:#bc8cff; }}

/* Sections */
.sec {{ padding:12px 20px; }}
.sec h2 {{ color:#8b949e; font-size:0.8em; font-weight:600; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:8px; }}
.card {{ background:#161b22; border:1px solid #21262d; border-radius:6px; }}

/* Strategy cards */
.strat-row {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:8px; }}
.sc {{ padding:12px; }}
.sc h3 {{ color:#c9d1d9; font-size:0.85em; margin-bottom:6px; display:flex; align-items:center; gap:6px; }}
.sc .row {{ display:flex; justify-content:space-between; padding:2px 0; font-size:0.8em; }}
.sc .row .k {{ color:#484f58; }}

/* Pills */
.pill {{ display:inline-block; padding:1px 6px; border-radius:8px; font-size:0.65em; font-weight:700; letter-spacing:0.3px; }}
.pill.long {{ background:#0d3520; color:#3fb950; }} .pill.short {{ background:#3d1418; color:#f85149; }}
.pill.spot {{ background:#1a2332; color:#58a6ff; }} .pill.perp {{ background:#2d1f0e; color:#d29922; }}
.pill.open {{ background:#0d3520; color:#3fb950; border:1px solid #238636; }}
.pill.closed {{ background:#161b22; color:#484f58; }}

/* Charts */
.chart {{ padding:8px; }}

/* Tables */
table {{ width:100%; border-collapse:collapse; }}
th {{ background:#161b22; color:#484f58; padding:6px 8px; text-align:left; font-weight:600; font-size:0.65em; text-transform:uppercase; letter-spacing:0.5px; position:sticky; top:0; }}
td {{ padding:5px 8px; border-bottom:1px solid #161b22; font-size:0.8em; }}
tr:hover {{ background:#1c2128; }}
tr.detail-row {{ background:#0d1117; }}
tr.detail-row:hover {{ background:#0d1117; }}
tr.trade-row {{ cursor:pointer; }}

/* Signal detail panel */
.sig-panel {{ padding:8px 12px 12px 40px; }}
.sig-chips {{ display:flex; flex-wrap:wrap; gap:4px; margin-bottom:6px; }}
.chip {{ display:inline-block; padding:2px 8px; border-radius:4px; font-size:0.75em; background:#21262d; color:#8b949e; }}
.chip.regime {{ font-weight:700; }}
.chip.UPTREND {{ background:#0d3520; color:#3fb950; }} .chip.DOWNTREND {{ background:#3d1418; color:#f85149; }}
.chip.CRISIS {{ background:#5c0a0a; color:#ff7b72; }} .chip.RANGE {{ background:#1c2128; color:#8b949e; }}
.chip.QUIET {{ background:#1a2332; color:#58a6ff; }}
.sig-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(140px,1fr)); gap:2px 12px; font-size:0.75em; }}
.sig-grid .si {{ display:flex; justify-content:space-between; padding:1px 0; }}
.sig-grid .si .k {{ color:#484f58; }}

/* Filters */
.frow {{ display:flex; gap:3px; margin-bottom:8px; }}
.fb {{ padding:4px 10px; border-radius:4px; cursor:pointer; background:transparent; color:#484f58; border:1px solid #21262d; font-size:0.75em; }}
.fb:hover {{ color:#c9d1d9; border-color:#30363d; }}
.fb.active {{ background:#1f6feb; color:#fff; border-color:#1f6feb; }}

/* Scrollable table */
.tbl-wrap {{ overflow-x:auto; max-height:700px; overflow-y:auto; }}
</style>
</head>
<body>

<div class="header">
    <h1>Trading Dashboard</h1>
    <div class="meta">Updated: {now}</div>
</div>
<div class="sim-tabs" id="sim-tabs"></div>
<div id="app"></div>

<script>
const SIMS = {data_json};
let activeSim = 0;

const DK = {{
    paper_bgcolor:'transparent', plot_bgcolor:'transparent',
    font:{{color:'#8b949e',size:10}},
    margin:{{t:30,b:30,l:45,r:10}},
    xaxis:{{gridcolor:'#161b22',zerolinecolor:'#21262d'}},
    yaxis:{{gridcolor:'#161b22',zerolinecolor:'#21262d'}},
    legend:{{bgcolor:'transparent',font:{{color:'#8b949e',size:10}},orientation:'h',y:-0.15}},
}};

const fmt = v => v.toLocaleString(undefined,{{maximumFractionDigits:0}});
const fmtPct = v => (v>=0?'+':'')+v.toFixed(1)+'%';
const pc = v => v>=0?'g':'r';
const fmtPrice = v => {{if(!v) return '-'; if(v>=1000) return '$'+fmt(v); if(v>=1) return '$'+v.toFixed(2); return '$'+v.toPrecision(4);}};

/* ---- Tabs ---- */
function renderTabs() {{
    document.getElementById('sim-tabs').innerHTML = SIMS.map((s,i) =>
        `<div class="stab ${{i===activeSim?'active':''}}" onclick="switchSim(${{i}})">${{s.name}}</div>`
    ).join('');
}}
function switchSim(i) {{ activeSim=i; renderTabs(); render(); }}

/* ---- Main render ---- */
function render() {{
    const s = SIMS[activeSim];
    const trades = s.all_trades||[];
    const strats = s.strategies||[];
    const nT = trades.length;
    const openTrades = trades.filter(t=>t.status==='open');
    const closedTrades = trades.filter(t=>t.status!=='open');
    const nO = openTrades.length;
    const nClosed = closedTrades.length;
    const nW = closedTrades.filter(t=>(t.pnl||0)>0).length;
    const wr = nClosed>0?(nW/nClosed*100):0;
    const ef = trades.reduce((a,t)=>a+(t.exchange_fee||0),0);
    const ff = trades.reduce((a,t)=>a+Math.abs(t.funding_cost||0),0);

    // Realized vs unrealized P&L
    const realizedPnl = closedTrades.reduce((a,t)=>a+(t.pnl||0),0);
    const unrealizedPnl = openTrades.reduce((a,t)=>a+(t.pnl||0),0);
    const realizedPct = s.capital>0 ? realizedPnl/s.capital*100 : 0;

    // Deployed vs cash — based on starting capital, not mark-to-market
    const deployed = openTrades.reduce((a,t)=>a+(t.position_usd||0),0);
    const cash = s.capital - deployed;
    const deployedPct = s.capital>0 ? deployed/s.capital*100 : 0;

    // Compute peak & drawdown from CLOSED trades only
    const allDates = new Set();
    closedTrades.forEach(t => {{ const d=(t.exit_time||'').substring(0,10); if(d) allDates.add(d); }});
    const dates = [...allDates].sort();
    const dayPnl = {{}};
    closedTrades.forEach(t => {{ const d=(t.exit_time||'').substring(0,10); if(d) dayPnl[d]=(dayPnl[d]||0)+(t.pnl||0); }});
    let cum=0, peak=0, maxDD=0;
    dates.forEach(d => {{ cum+=(dayPnl[d]||0); if(cum>peak) peak=cum; const dd=peak>0?(cum-peak)/s.capital*100:0; if(dd<maxDD) maxDD=dd; }});

    // Best/worst day from closed trades only
    const dayVals = Object.values(dayPnl);
    const bestDay = dayVals.length>0 ? Math.max(...dayVals) : 0;
    const worstDay = dayVals.length>0 ? Math.min(...dayVals) : 0;

    let h = `
    <div class="kpi-row">
        <div class="kpi"><div class="lbl">Current Value</div><div class="val ${{pc(realizedPnl)}}">$${{fmt(s.final_equity)}}<div style="font-size:0.45em;color:#484f58;margin-top:2px">start $${{fmt(s.capital)}}</div></div></div>
        <div class="kpi"><div class="lbl">Realized P&L</div><div class="val ${{pc(realizedPnl)}}">$${{fmt(realizedPnl)}} (${{fmtPct(realizedPct)}})</div></div>
        <div class="kpi"><div class="lbl">Unrealized P&L</div><div class="val ${{pc(unrealizedPnl)}}">$${{fmt(unrealizedPnl)}}</div></div>
        <div class="kpi"><div class="lbl">Invested / Cash</div><div class="val b">$${{fmt(deployed)}} <span style="font-size:0.55em;color:#484f58">/ $${{fmt(cash)}}</span><div style="font-size:0.45em;color:#484f58;margin-top:2px">${{deployedPct.toFixed(0)}}% deployed</div></div></div>
        ${{(s.spot_funds!=null) ? `<div class="kpi"><div class="lbl">Spot / Perp Split</div><div class="val m">$${{fmt(s.spot_funds)}} <span style="font-size:0.55em;color:#484f58">/ $${{fmt(s.perp_funds||0)}}</span><div style="font-size:0.45em;color:#484f58;margin-top:2px">${{(s.spot_funds/(s.spot_funds+(s.perp_funds||1))*100).toFixed(0)}}% spot, ${{((s.perp_funds||0)/(s.spot_funds+(s.perp_funds||1))*100).toFixed(0)}}% perp</div></div></div>` : ''}}
        <div class="kpi"><div class="lbl">Max Drawdown</div><div class="val r">${{maxDD.toFixed(1)}}%</div></div>
        <div class="kpi"><div class="lbl">Trades</div><div class="val b">${{nT}} <span style="font-size:0.6em;color:${{nO>0?'#3fb950':'#484f58'}}">(${{nO}} open)</span></div></div>
        <div class="kpi"><div class="lbl">Win Rate</div><div class="val ${{wr>=50?'g':'y'}}">${{wr.toFixed(1)}}%<div style="font-size:0.45em;color:#484f58;margin-top:2px">${{nW}}/${{nClosed}} closed</div></div></div>
        <div class="kpi"><div class="lbl">Best / Worst Day</div><div class="val"><span class="${{bestDay>=0?'g':'r'}}">$${{fmt(bestDay)}}</span> / <span class="${{worstDay>=0?'g':'r'}}">$${{fmt(worstDay)}}</span></div></div>
        <div class="kpi"><div class="lbl">Total Fees</div><div class="val r">$${{fmt(ef+ff)}}</div></div>
    </div>`;

    // --- OPEN POSITIONS (prominent, always visible if any) ---
    if (nO > 0) {{
        const openSorted = [...openTrades].sort((a,b) => (b.pnl||0)-(a.pnl||0));
        const openPnl = openTrades.reduce((a,t)=>a+(t.pnl||0),0);
        h += `<div class="sec">
        <h2 style="color:#3fb950">Open Positions (${{nO}}) &mdash; Unrealized P&L: <span class="${{pc(openPnl)}}">$${{fmt(openPnl)}}</span></h2>
        <div class="card tbl-wrap" style="max-height:400px;border-color:#238636">
            <table>
                <thead><tr>
                    <th>Strategy</th><th>Token</th><th>Dir</th><th>Regime</th>
                    <th>Entry Signal</th><th>Entry Time</th><th>Hold</th><th>Lev</th>
                    <th>Size</th><th>Entry $</th><th>Current $</th><th>P&L</th><th>Return</th><th>Fees</th>
                </tr></thead>
                <tbody>${{openSorted.map(t => {{
                    const pnl = t.pnl||0;
                    const dir = t.direction===-1||t.direction==='-1'?'SHORT':'LONG';
                    const sig = t.signal||{{}};
                    const regime = sig.regime||'?';
                    const mt = (t.market_type||'').toLowerCase();
                    const lev = mt==='spot' ? 'N/A' : (sig.leverage ? sig.leverage+'x' : '1x');
                    return `<tr>
                        <td style="color:#8b949e">${{t.strategy||''}}</td>
                        <td><b>${{t.token||''}}</b> <span style="font-size:0.6em;color:#484f58">${{mt.toUpperCase()}}</span></td>
                        <td><span class="pill ${{dir.toLowerCase()}}">${{dir}}</span></td>
                        <td><span class="chip regime ${{regime}}">${{regime}}</span></td>
                        <td style="font-size:0.75em;max-width:250px;white-space:nowrap;overflow:visible">${{sig.signal_short||'-'}}</td>
                        <td>${{(t.entry_time||'').substring(5,16)}}</td>
                        <td>${{t.hold_hours||0}}h</td>
                        <td>${{lev}}</td>
                        <td>$${{fmt(t.position_usd||0)}}</td>
                        <td style="font-size:0.85em">${{fmtPrice(t.entry_price)}}</td>
                        <td style="font-size:0.85em">${{fmtPrice(t.current_price)}}</td>
                        <td class="${{pc(pnl)}}" style="font-weight:700">$${{fmt(pnl)}}</td>
                        <td class="${{pc(pnl)}}">${{(t.return_pct||0).toFixed(1)}}%</td>
                        <td class="r">$${{fmt((t.exchange_fee||0)+Math.abs(t.funding_cost||0))}}</td>
                    </tr>`;
                }}).join('')}}</tbody>
            </table>
        </div></div>`;
    }}

    // --- Rebalance history ---
    const rebalHist = s.rebalance_history||[];
    if (rebalHist.length > 0) {{
        h += `<div class="sec">
        <h2 style="color:#bc8cff">Recent Rebalances (${{rebalHist.length}})</h2>
        <div class="card tbl-wrap" style="max-height:200px">
            <table>
                <thead><tr><th>Time</th><th>Transfer</th><th>Direction</th><th>Spot After</th><th>Perp After</th></tr></thead>
                <tbody>${{[...rebalHist].reverse().map(r => `<tr>
                    <td>${{(r.time||'').substring(5,16)}}</td>
                    <td style="font-weight:700">$${{fmt(r.amount||0)}}</td>
                    <td style="color:#bc8cff">${{r.direction||''}}</td>
                    <td>$${{fmt(r.spot_funds_after||0)}}</td>
                    <td>$${{fmt(r.perp_funds_after||0)}}</td>
                </tr>`).join('')}}</tbody>
            </table>
        </div></div>`;
    }}

    // --- Strategy cards ---
    h += `<div class="sec">
        <h2>Strategies</h2>
        <div class="strat-row">${{strats.map(st => `
            <div class="card sc">
                <h3>${{st.name}} <span class="pill ${{st.market.toLowerCase()}}">${{st.market}}</span></h3>
                <div class="row"><span class="k">Capital</span><span>$${{fmt(st.starting_capital)}}</span></div>
                <div class="row"><span class="k">Value</span><span class="${{pc(st.pnl)}}" style="font-weight:700">$${{fmt(st.final_equity)}}</span></div>
                <div class="row"><span class="k">P&L</span><span class="${{pc(st.pnl)}}">$${{fmt(st.pnl)}} (${{fmtPct(st.return_pct)}})</span></div>
                <div class="row"><span class="k">Trades (open)</span><span>${{st.n_trades}} (${{st.n_open}})</span></div>
                <div class="row"><span class="k">Win Rate</span><span>${{st.win_rate.toFixed(1)}}%</span></div>
                <div class="row"><span class="k">Exch Fees</span><span class="r">$${{fmt(st.exchange_fees)}}</span></div>
                <div class="row"><span class="k">Funding</span><span class="r">$${{fmt(st.funding_fees)}}</span></div>
            </div>`).join('')}}
        </div>
    </div>`;

    // --- Charts: Equity + Daily P&L ---
    h += `<div class="sec">
        <h2>Equity Curve</h2>
        <div class="card chart"><div id="ch-equity" style="height:300px;"></div></div>
    </div>`;

    h += `<div class="sec">
        <h2>Daily P&L — Realized (Last 28 Days)</h2>
        <div class="card chart"><div id="ch-daily" style="height:250px;"></div></div>
    </div>`;

    // --- Trade Log ---
    h += `<div class="sec">
        <h2>Trade Log</h2>
        <div class="frow">
            <div class="fb active" onclick="setF(this,'all')">All (${{nT}})</div>
            <div class="fb" onclick="setF(this,'open')">Open (${{nO}})</div>
            <div class="fb" onclick="setF(this,'closed')">Closed (${{nT-nO}})</div>
        </div>
        <div class="card tbl-wrap">
            <table>
                <thead><tr>
                    <th></th><th>Status</th><th>Strategy</th><th>Token</th><th>Dir</th>
                    <th style="min-width:200px">Entry Signal</th><th>Regime</th><th>Entry</th><th>Exit</th><th>Hold</th>
                    <th>Entry $</th><th>Exit/Cur $</th><th>P&L</th><th>Return</th><th>Size</th><th>Fees</th><th>Exit</th>
                </tr></thead>
                <tbody id="tb-trades"></tbody>
            </table>
            <div id="show-more-wrap" style="text-align:center;padding:8px;display:none">
                <span class="fb" onclick="showMore()" style="cursor:pointer">Show more trades...</span>
            </div>
        </div>
    </div>`;

    // --- Per-Token Summary ---
    h += `<div class="sec">
        <h2>Per-Token Summary</h2>
        <div class="card tbl-wrap">
            <table>
                <thead><tr>
                    <th>Token</th><th>Trades</th><th>Open</th><th>Win Rate</th>
                    <th>P&L</th><th>Best</th><th>Worst</th><th>Fees</th>
                </tr></thead>
                <tbody id="tb-tokens"></tbody>
            </table>
        </div>
    </div>`;

    document.getElementById('app').innerHTML = h;
    renderTrades(trades, 'all');
    renderTokens(trades);
    // Defer chart rendering so tables paint first
    setTimeout(() => {{ drawEquity(trades, strats); drawDaily(trades); }}, 0);
}}

/* ---- Filter ---- */
let curFilter = 'all';
function setF(el, f) {{
    curFilter = f;
    _tradeLimit = 50;
    el.parentElement.querySelectorAll('.fb').forEach(b=>b.classList.remove('active'));
    el.classList.add('active');
    renderTrades(SIMS[activeSim].all_trades||[], f);
}}

/* ---- Trade table — detail panels rendered on click, not upfront ---- */
let _visibleTrades = [];
let _allFiltered = [];
let _tradeLimit = 50;
function renderTrades(trades, filter) {{
    const ft = filter==='all' ? trades : trades.filter(t=>t.status===filter);
    _allFiltered = [...ft].sort((a,b)=>(b.exit_time||b.entry_time||'').localeCompare(a.exit_time||a.entry_time||''));
    _visibleTrades = _allFiltered.slice(0, _tradeLimit);
    const tbody = document.getElementById('tb-trades');
    let rows = '';
    _visibleTrades.forEach((t,i) => {{
        const pnl = t.pnl||0;
        const dir = t.direction===-1||t.direction==='-1'?'SHORT':'LONG';
        const st = t.status==='open';
        const sig = t.signal||{{}};
        const sigText = sig.signal_short||'-';
        const regime = sig.regime||'?';
        const totalFee = (t.exchange_fee||0)+Math.abs(t.funding_cost||0);

        const mt = (t.market_type||'').toLowerCase();
        const mtLabel = mt ? ' <span style=\"font-size:0.6em;color:#484f58\">'+mt.toUpperCase()+'</span>' : '';
        rows += `<tr class="trade-row" onclick="toggleDetail(${{i}})">
            <td style="color:#30363d;font-size:0.7em">&#9660;</td>
            <td>${{st?'<span class=\"pill open\">OPEN</span>':'<span class=\"pill closed\">CLOSED</span>'}}</td>
            <td style="color:#8b949e;font-size:0.7em">${{t.strategy||''}}</td>
            <td><b>${{t.token||''}}</b>${{mtLabel}}</td>
            <td><span class="pill ${{dir.toLowerCase()}}">${{dir}}</span></td>
            <td style="font-size:0.75em;white-space:nowrap">${{sigText}}</td>
            <td><span class="chip regime ${{regime}}" style="font-size:0.7em">${{regime}}</span></td>
            <td>${{(t.entry_time||'').substring(5,16)}}</td>
            <td>${{(t.exit_time||'').substring(5,16)}}</td>
            <td>${{t.hold_hours||0}}h</td>
            <td style="font-size:0.85em">${{fmtPrice(t.entry_price)}}</td>
            <td style="font-size:0.85em">${{fmtPrice(st ? t.current_price : t.exit_price)}}</td>
            <td class="${{pc(pnl)}}" style="font-weight:600">$${{fmt(pnl)}}</td>
            <td class="${{pc(pnl)}}">${{(t.return_pct||0).toFixed(1)}}%</td>
            <td>$${{fmt(t.position_usd||0)}}</td>
            <td class="r">$${{totalFee.toFixed(0)}}</td>
            <td style="color:#484f58">${{t.exit_reason||'-'}}</td>
        </tr>
        <tr class="detail-row" id="det-${{i}}" style="display:none"><td colspan="17"></td></tr>`;
    }});
    tbody.innerHTML = rows;
    const sm = document.getElementById('show-more-wrap');
    if(sm) sm.style.display = _allFiltered.length > _tradeLimit ? 'block' : 'none';
}}

function showMore() {{
    _tradeLimit += 100;
    renderTrades(SIMS[activeSim].all_trades||[], curFilter);
}}

function toggleDetail(i) {{
    const el = document.getElementById('det-'+i);
    if (el.style.display==='none') {{
        // Render on first open
        if (!el.firstChild.innerHTML) {{
            const t = _visibleTrades[i];
            el.firstChild.innerHTML = renderSignalPanel(t.signal||{{}}, t);
        }}
        el.style.display = 'table-row';
    }} else {{
        el.style.display = 'none';
    }}
}}

function renderSignalPanel(sig, t) {{
    if (!sig || !sig.regime) return '<div class="sig-panel" style="color:#484f58">No signal data</div>';

    const mt = (t.market_type||'').toLowerCase();
    let chips = `<span class="chip regime ${{sig.regime}}">${{sig.regime}}</span>`;
    if (mt) chips += `<span class="chip" style="background:#1a2332;color:#58a6ff">${{mt.toUpperCase()}}</span>`;
    if (mt==='perp' && sig.leverage) chips += `<span class="chip" style="background:#2d1f0e;color:#d29922">${{sig.leverage}}x leverage</span>`;

    const dir = t.direction===-1||t.direction==='-1'?'SHORT':'LONG';
    chips += `<span class="chip ${{dir==='SHORT'?'short':'long'}}">${{dir}}</span>`;

    let grid = '';
    const add = (k,v) => {{ if(v!==undefined && v!==null) grid += `<div class="si"><span class="k">${{k}}</span><span>${{v}}</span></div>`; }};

    add('Close', sig.close ? '$'+sig.close.toLocaleString() : '-');
    add('EMA 20', sig.ema_20 ? '$'+sig.ema_20.toLocaleString() : '-');
    add('Above EMA20', sig.above_ema20 ? 'Yes' : 'No');
    add('ADX', sig.adx?.toFixed(1));
    add('RSI', sig.rsi?.toFixed(1));
    add('Vol Ratio', sig.vol_ratio?.toFixed(2)+'x');
    add('ATR', sig.atr ? '$'+sig.atr.toFixed(2) : '-');
    add('Volatility', sig.vol_20 ? (sig.vol_20*100).toFixed(2)+'%' : '-');
    if (sig.ret_1 !== undefined) add('1h Return', (sig.ret_1*100).toFixed(2)+'%');
    if (sig.ret_24h !== undefined) add('24h Return', sig.ret_24h.toFixed(2)+'%');
    if (sig.adx_score !== undefined) add('ADX Score', sig.adx_score.toFixed(2));
    if (sig.mom_score !== undefined) add('Mom Score', sig.mom_score.toFixed(2));
    if (sig.stop_atr !== undefined) add('Stop', sig.stop_atr.toFixed(2)+' ATR');

    return `<div class="sig-panel">
        <div style="color:#484f58;font-size:0.7em;text-transform:uppercase;margin-bottom:4px">Entry Indicators</div>
        <div class="sig-chips">${{chips}}</div>
        <div class="sig-grid">${{grid}}</div>
    </div>`;
}}

/* ---- Per-token table ---- */
function renderTokens(trades) {{
    const m = {{}};
    trades.forEach(t => {{
        const tk = t.token||'?';
        if(!m[tk]) m[tk]={{pnl:0,n:0,open:0,closed:0,wins:0,fees:0,best:-Infinity,worst:Infinity,unrealized:0}};
        const p = t.pnl||0;
        m[tk].pnl += p;
        m[tk].n++;
        m[tk].fees += (t.exchange_fee||0)+Math.abs(t.funding_cost||0);
        if(t.status==='open') {{
            m[tk].open++;
            m[tk].unrealized += p;
        }} else {{
            m[tk].closed++;
            if(p>0) m[tk].wins++;
            if(p>m[tk].best) m[tk].best=p;
            if(p<m[tk].worst) m[tk].worst=p;
        }}
    }});
    const tks = Object.keys(m).sort((a,b)=>m[b].pnl-m[a].pnl);
    document.getElementById('tb-tokens').innerHTML = tks.map(tk => {{
        const d=m[tk];
        const wr = d.closed>0 ? (d.wins/d.closed*100).toFixed(1) : '-';
        const bestStr = d.closed>0 ? `$${{fmt(d.best===-Infinity?0:d.best)}}` : '-';
        const worstStr = d.closed>0 ? `$${{fmt(d.worst===Infinity?0:d.worst)}}` : '-';
        return `<tr><td><b>${{tk}}</b></td><td>${{d.n}}</td><td>${{d.open}}</td>
            <td>${{wr}}${{d.closed>0?'%':''}}</td>
            <td class="${{pc(d.pnl)}}" style="font-weight:600">$${{fmt(d.pnl)}}</td>
            <td class="g">${{bestStr}}</td>
            <td class="r">${{worstStr}}</td>
            <td class="r">$${{fmt(d.fees)}}</td></tr>`;
    }}).join('');
}}

/* ---- Charts ---- */
function drawEquity(trades, strats) {{
    const s = SIMS[activeSim];
    const eh = s.equity_history || [];
    const colors = ['#58a6ff','#3fb950','#d29922','#bc8cff','#f85149','#39d353'];

    if (eh.length > 1) {{
        // Paper trading: equity curve from tick snapshots
        const cap = s.capital || 0;
        const traces = [{{
            x: eh.map(p=>p.t), y: eh.map(p=>p.eq - cap),
            name: s.name||'P&L', type:'scatter', mode:'lines',
            line:{{color:colors[0],width:2}},
            fill:'tozeroy', fillcolor:'rgba(88,166,255,0.1)',
        }}];
        Plotly.newPlot('ch-equity', traces, {{
            ...DK, title:'Cumulative P&L (mark-to-market)',
            xaxis:{{...DK.xaxis, type:'date'}},
            yaxis:{{...DK.yaxis,title:'USD',tickprefix:'$'}},
            shapes:[{{type:'line',x0:eh[0].t,x1:eh[eh.length-1].t,y0:0,y1:0,line:{{color:'#484f58',width:1,dash:'dot'}}}}],
        }}, {{responsive:true}});
    }} else {{
        // Backtest: derive from per-trade P&L
        const sNames = [...new Set(trades.map(t=>t.strategy))];
        const allDates = new Set();
        trades.forEach(t => {{ const d=(t.exit_time||t.entry_time||'').substring(0,10); if(d) allDates.add(d); }});
        const dates = [...allDates].sort();
        const traces = sNames.map((sn,i) => {{
            const dayPnl = {{}};
            trades.filter(t=>t.strategy===sn).forEach(t => {{
                const d = (t.exit_time||t.entry_time||'').substring(0,10);
                if(d) dayPnl[d] = (dayPnl[d]||0) + (t.pnl||0);
            }});
            let cum = 0;
            return {{
                x: dates, y: dates.map(d => {{ cum += (dayPnl[d]||0); return cum; }}),
                name: sn, type:'scatter', mode:'lines',
                line:{{color:colors[i%6],width:2}},
            }};
        }});
        if (sNames.length > 1) {{
            const totalDayPnl = {{}};
            trades.forEach(t => {{ const d=(t.exit_time||t.entry_time||'').substring(0,10); if(d) totalDayPnl[d]=(totalDayPnl[d]||0)+(t.pnl||0); }});
            let tc = 0;
            traces.push({{
                x: dates, y: dates.map(d => {{ tc += (totalDayPnl[d]||0); return tc; }}),
                name:'Total', type:'scatter', mode:'lines',
                line:{{color:'#e0e0e0',width:2,dash:'dot'}},
            }});
        }}
        Plotly.newPlot('ch-equity', traces, {{
            ...DK, title:'Cumulative P&L',
            xaxis:{{...DK.xaxis, type:'date'}},
            yaxis:{{...DK.yaxis,title:'USD',tickprefix:'$'}},
        }}, {{responsive:true}});
    }}
}}


function drawDaily(trades) {{
    // Daily P&L from realized (closed) trades only
    const closedTrades = trades.filter(t=>t.status==='closed');
    const colors = ['#58a6ff','#3fb950','#d29922','#bc8cff','#f85149','#39d353'];

    if (closedTrades.length === 0) {{
        document.getElementById('ch-daily').innerHTML =
            '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#484f58;font-size:0.9em">No closed trades yet — daily P&L will appear when positions are exited</div>';
        return;
    }}

    const daily = {{}};
    closedTrades.forEach(t => {{
        const d = (t.exit_time||'').substring(0,10);
        if(!d) return;
        if(!daily[d]) daily[d]={{}};
        const sn = t.strategy||'?';
        daily[d][sn] = (daily[d][sn]||0) + (t.pnl||0);
    }});
    const days = Object.keys(daily).sort().slice(-28);
    const sNames = [...new Set(closedTrades.map(t=>t.strategy))];
    const traces = sNames.map((sn,i) => ({{
        x:days, y:days.map(d=>(daily[d]&&daily[d][sn])||0),
        name:sn, type:'bar', marker:{{color:colors[i%colors.length]}},
    }}));
    let cum=0;
    traces.push({{
        x:days, y:days.map(d=>{{
            cum += Object.values(daily[d]||{{}}).reduce((a,b)=>a+b,0);
            return cum;
        }}),
        name:'Cumulative', type:'scatter', mode:'lines+markers',
        yaxis:'y2', line:{{color:'#e0e0e0',width:1.5}}, marker:{{size:2}},
    }});
    Plotly.newPlot('ch-daily', traces, {{
        ...DK, barmode:'stack', title:'Daily P&L (Realized)',
        yaxis:{{...DK.yaxis,title:'Daily ($)',tickprefix:'$'}},
        yaxis2:{{title:'Cum ($)',overlaying:'y',side:'right',gridcolor:'transparent',tickprefix:'$'}},
    }}, {{responsive:true}});
}}

renderTabs();
render();
</script>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# Push
# ---------------------------------------------------------------------------
def push_to_ghpages():
    import tempfile, shutil
    print("\nPushing to GitHub Pages...")
    tmp = tempfile.mkdtemp(prefix="dashboard-")
    try:
        subprocess.run(
            ["git", "clone", "--branch", "gh-pages", "--single-branch", "--depth", "1",
             "http://10.100.1.10:8080/git/voerses/dashboard.git", tmp],
            check=True, capture_output=True, text=True)
        dst = Path(tmp) / "docs"
        dst.mkdir(exist_ok=True)
        shutil.copy2(str(DOCS_DIR / "index.html"), str(dst / "index.html"))
        shutil.copy2(str(DOCS_DIR / "index.html"), str(Path(tmp) / "index.html"))
        Path(tmp, ".nojekyll").touch()
        subprocess.run(["git", "-C", tmp, "add", "docs/index.html", "index.html", ".nojekyll"],
                        check=True, capture_output=True)
        r = subprocess.run(["git", "-C", tmp, "diff", "--cached", "--quiet"], capture_output=True)
        if r.returncode == 0:
            print("No changes to push.")
            return
        subprocess.run(["git", "-C", tmp, "commit", "-m",
                         f"Update dashboard {datetime.now().strftime('%Y-%m-%d %H:%M')}"],
                        check=True, capture_output=True, text=True)
        subprocess.run(["git", "-C", tmp, "push", "origin", "gh-pages"],
                        check=True, capture_output=True, text=True)
        print("Pushed to gh-pages.")
    except subprocess.CalledProcessError as e:
        print(f"Push failed: {e.stderr}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def load_paper_trading_data():
    """Load paper trading position data as dashboard-compatible sims."""
    state_dir = PROJECT_ROOT / "state" / "paper_live"
    sims_data = []

    # Import the runner's tracker class
    sys.path.insert(0, str(PROJECT_ROOT))
    from run_paper_live import PaperPositionTracker, RUNS, STATE_DIR as PAPER_STATE, DATA_DIR as PAPER_DATA, EXCHANGE as PAPER_EXCHANGE

    for run_cfg in RUNS:
        label = run_cfg["label"]
        tracker = PaperPositionTracker(
            strategy_id=run_cfg["strategy_id"],
            label=label,
            capital=run_cfg["capital"],
            state_dir=PAPER_STATE,
            data_dir=PAPER_DATA,
            exchange=PAPER_EXCHANGE,
        )
        sim = tracker.to_dashboard_sim()
        n_open = len(tracker.open_positions)
        n_closed = len(tracker.closed_trades)
        print(f"  {label}: {n_open} open, {n_closed} closed, "
              f"equity ${tracker.equity:,.0f}")
        sims_data.append(sim)

    return sims_data


def main():
    parser = argparse.ArgumentParser(description="Trading dashboard generator")
    parser.add_argument("--run-backtest", action="store_true")
    parser.add_argument("--paper", action="store_true",
                        help="Generate dashboard from paper trading data")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--simulations", type=str, default="simulations.json",
                        help="Simulation config JSON (default: simulations.json)")
    parser.add_argument("--months", type=int, default=6)
    args = parser.parse_args()
    os.chdir(PROJECT_ROOT)

    if args.paper:
        print("Loading paper trading data...")
        sims_data = load_paper_trading_data()
        if not sims_data:
            print("No paper trading data found.")
            return
    elif args.run_backtest:
        # Load simulation config
        sf = Path(args.simulations)
        if sf.exists():
            sims = json.loads(sf.read_text())
            print(f"Loaded {len(sims)} simulations from {sf}")
        else:
            print(f"No simulation file at {sf}, using default")
            sims = [{"id": "default", "name": "Default", "capital": 200000,
                      "strategies": [f.stem for f in sorted((PROJECT_ROOT/"strategies").glob("s[0-9]*.py"))
                                     if not f.stem.endswith("_test")]}]
        sims_data = [run_backtest_for_sim(s, args.months) for s in sims]
    else:
        td = DOCS_DIR / "data.json"
        if not td.exists():
            td = DOCS_DIR / "trade_data.json"
        if td.exists():
            sims_data = json.loads(td.read_text())
            if not isinstance(sims_data, list):
                sims_data = [sims_data]
        else:
            print("No cached data. Use --run-backtest or --paper.")
            return

    DOCS_DIR.mkdir(exist_ok=True)
    html = generate_html(sims_data)
    out = DOCS_DIR / "index.html"
    out.write_text(html)
    print(f"\nDashboard: {out} ({len(html)/1024:.0f} KB)")

    if args.push:
        push_to_ghpages()


if __name__ == "__main__":
    main()
