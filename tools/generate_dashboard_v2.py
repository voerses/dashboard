#!/usr/bin/env python3
"""
Paper Trading Dashboard V2 Generator
======================================
Generates a self-contained HTML dashboard for monitoring v4 paper trading runs.
Reads paper trading state files (state.json, trades.jsonl, equity.csv) and
produces an HTML page with:
  - KPIs: equity, P&L, trades, win rate, drawdown
  - Fund Allocation panel: shadow spot/perp pools, imbalance, blocked entries (AC26)
  - Equity curve from equity.csv snapshots
  - Rebalance history table (AC26)
  - Trade log with filters
  - Stale data warning (AC11)
  - Pool-level aggregation (AC12)

Usage:
    python tools/generate_dashboard_v2.py --config /path/to/paper_config.json
    python tools/generate_dashboard_v2.py --config config.json --push
    python tools/generate_dashboard_v2.py --state-dir /path/to/state/
"""

import sys
import os
import json
import csv
import argparse
import subprocess
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "v4"))

PROJECT_ROOT = Path(__file__).parent.parent
DOCS_DIR = PROJECT_ROOT / "docs"


# ---------------------------------------------------------------------------
# Data loading from paper trading state files
# ---------------------------------------------------------------------------

def load_trades_jsonl(path: str) -> list[dict]:
    """Load trades from a JSONL file."""
    trades = []
    if not os.path.exists(path):
        return trades
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                trades.append(json.loads(line))
    return trades


def load_equity_csv(path: str) -> list[dict]:
    """Load equity snapshots from equity.csv."""
    snapshots = []
    if not os.path.exists(path):
        return snapshots
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            snapshots.append({
                "timestamp": row.get("timestamp", ""),
                "tick": int(row.get("tick", 0)),
                "portfolio_equity": float(row.get("portfolio_equity", 0)),
                "mark_to_market_equity": float(row.get("mark_to_market_equity", 0)),
                "free_capital": float(row.get("free_capital", 0)),
                "open_positions": int(row.get("open_positions", 0)),
                "spot_shadow_free": float(row.get("spot_shadow_free", 0)),
                "perp_shadow_free": float(row.get("perp_shadow_free", 0)),
                "spot_deployed": float(row.get("spot_deployed", 0)),
                "perp_deployed": float(row.get("perp_deployed", 0)),
                "imbalance_pct": float(row.get("imbalance_pct", 0)),
            })
    # Sort by timestamp to prevent backwards-tick display issues on dashboard
    snapshots.sort(key=lambda s: s["timestamp"])
    return snapshots


def load_rebalances_jsonl(path: str) -> list[dict]:
    """Load rebalance log from rebalances.jsonl."""
    rebalances = []
    if not os.path.exists(path):
        return rebalances
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                rebalances.append(json.loads(line))
    return rebalances


def load_state_json(path: str) -> dict:
    """Load current state from state.json."""
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)


def build_sims_from_state_dir(state_dir: str, config_path: str = "") -> dict:
    """Build a SIMS-compatible dict from paper trading state files.

    Reads:
      - state.json (current engine state)
      - trades.jsonl (closed trades)
      - equity.csv (equity snapshots)
      - rebalances.jsonl (shadow rebalance log)
      - config.json (auto-discovered in state_dir, or explicit config_path)
    """
    state_dir = Path(state_dir)

    # Load raw data
    state = load_state_json(str(state_dir / "state.json"))
    trades = load_trades_jsonl(str(state_dir / "trades.jsonl"))
    equity = load_equity_csv(str(state_dir / "equity.csv"))
    rebalances = load_rebalances_jsonl(str(state_dir / "rebalances.jsonl"))

    # Load config: explicit path first, then auto-discover in state_dir
    config = {}
    if config_path and os.path.exists(config_path):
        with open(config_path) as f:
            config = json.load(f)
    elif (state_dir / "config.json").exists():
        with open(state_dir / "config.json") as f:
            config = json.load(f)

    # Extract key values
    initial_capital = state.get("initial_capital", config.get("initial_capital", 200_000.0))
    realized_pnl = state.get("realized_pnl", 0.0)
    total_fees = state.get("total_fees", 0.0)
    total_funding = state.get("total_funding", 0.0)
    tick_counter = state.get("tick_counter", 0)
    last_timestamp = state.get("last_timestamp", "")
    pool_name = config.get("pool_name", "paper_trading")
    mode = config.get("mode", "pool")

    # Portfolio equity = initial + realized - fees - funding
    portfolio_equity = initial_capital + realized_pnl - total_fees - total_funding

    # Open positions from state
    open_positions = state.get("open_positions", [])
    n_open = len(open_positions)

    # Strategy info
    strategy_specs = config.get("strategies", [])
    if mode == "pool" and pool_name:
        strategies = [{
            "id": pool_name,
            "name": pool_name,
            "weight": sum(s.get("weight", 1.0) for s in strategy_specs),
            "final_equity": portfolio_equity,
            "trade_count": len(trades),
            "open_positions": n_open,
            "strategies": [s.get("strategy_id", "") for s in strategy_specs],
        }]
    else:
        strategies = [{
            "id": s.get("strategy_id", f"s{i}"),
            "name": s.get("strategy_id", f"s{i}"),
            "weight": s.get("weight", 1.0),
        } for i, s in enumerate(strategy_specs)]

    # Build all_trades from trades.jsonl (closed trades)
    all_trades = []
    for t in trades:
        all_trades.append({
            "token": t.get("token", ""),
            "strategy": t.get("strategy_id", ""),
            "market_type": "perp" if t.get("is_perp", False) else "spot",
            "direction": t.get("direction", 1),
            "pnl": t.get("pnl", 0.0),
            "exit_reason": t.get("exit_reason", ""),
            "signal": {
                "entry_bar": t.get("entry_bar", 0),
                "exit_bar": t.get("exit_bar", 0),
                "entry_price": t.get("entry_price", 0),
                "exit_price": t.get("exit_price", 0),
                "hold_bars": t.get("hold_bars", 0),
            },
            "entry_price": t.get("entry_price", 0),
            "exit_price": t.get("exit_price", 0),
            "margin_usd": t.get("margin_usd", 0),
            "hold_bars": t.get("hold_bars", 0),
            "funding_cost": t.get("funding_cost", 0),
            "entry_fee": t.get("entry_fee", 0),
            "exit_fee": t.get("exit_fee", 0),
            "entry_timestamp": t.get("entry_timestamp", ""),
        })

    # Add open positions from state.json (AC28)
    entry_fees_map = state.get("entry_fees_by_pos", {})
    last_prices = state.get("last_known_prices", {})
    last_regimes = state.get("last_known_regimes", {})
    regime_names = {0: "CRISIS", 1: "QUIET", 2: "UPTREND", 3: "RANGE", 4: "DOWNTREND"}
    for pos in open_positions:
        pos_id = pos.get("position_id", "")
        entry_fee = entry_fees_map.get(pos_id, 0.0)
        entry_price = pos.get("entry_price", 0)
        token = pos.get("token", "")
        direction = pos.get("direction", 1)
        quantity = pos.get("quantity", 0)
        current_price = last_prices.get(token, entry_price)
        unrealized_pnl = quantity * (current_price - entry_price)
        stop_price = pos.get("stop_price", 0)
        # % distance to stop (positive = room, negative = breached)
        if stop_price and current_price:
            if direction == 1:  # long: stop below
                pct_to_stop = (current_price - stop_price) / current_price * 100
            else:  # short: stop above
                pct_to_stop = (stop_price - current_price) / current_price * 100
        else:
            pct_to_stop = 0
        regime_id = last_regimes.get(token, -1)
        regime_name = regime_names.get(regime_id, "N/A")
        exit_regimes = pos.get("exit_regimes", [])
        exit_regime_names = [regime_names.get(r, str(r)) for r in exit_regimes]
        all_trades.append({
            "token": token,
            "strategy": pos.get("strategy_id", ""),
            "market_type": "perp" if pos.get("is_perp", False) else "spot",
            "direction": direction,
            "status": "open",
            "entry_price": entry_price,
            "current_price": current_price,
            "unrealized_pnl": unrealized_pnl,
            "margin_usd": pos.get("margin_usd", 0),
            "entry_bar": pos.get("entry_bar", 0),
            "entry_fee": entry_fee,
            "cumulative_funding": pos.get("cumulative_funding", 0),
            "hold_bars": tick_counter - pos.get("entry_bar", 0),
            "stop_price": stop_price,
            "pct_to_stop": round(pct_to_stop, 2),
            "regime": regime_name,
            "exit_regimes": exit_regime_names,
            "entry_timestamp": pos.get("entry_timestamp", ""),
        })

    # Equity history from equity.csv
    equity_history = [
        {"timestamp": e["timestamp"], "mark_to_market_equity": e["mark_to_market_equity"]}
        for e in equity
    ]

    # Shadow pools from latest equity snapshot
    shadow_pools = {
        "spot_funds": 0.0, "perp_funds": 0.0,
        "spot_deployed": 0.0, "perp_deployed": 0.0,
        "imbalance_pct": 0.0, "blocked_entries_count": 0,
    }
    if equity:
        latest = equity[-1]
        spot_free = latest["spot_shadow_free"]
        perp_free = latest["perp_shadow_free"]
        spot_deployed = latest["spot_deployed"]
        perp_deployed = latest["perp_deployed"]
        spot_total = spot_free + spot_deployed
        perp_total = perp_free + perp_deployed
        shadow_pools = {
            "spot_funds": spot_total,
            "perp_funds": perp_total,
            "spot_deployed": spot_deployed,
            "perp_deployed": perp_deployed,
            "imbalance_pct": latest["imbalance_pct"],
            "blocked_entries_count": sum(
                r.get("would_have_blocked_entries", 0) for r in rebalances
            ),
        }

    # Stale data check
    is_stale = False
    if last_timestamp:
        try:
            ts_dt = datetime.strptime(last_timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
            age = datetime.now(timezone.utc) - ts_dt
            from datetime import timedelta
            is_stale = age > timedelta(hours=2)
        except (ValueError, TypeError):
            is_stale = True

    return {
        "id": pool_name,
        "name": pool_name,
        "capital": initial_capital,
        "strategies": strategies,
        "all_trades": all_trades,
        "equity_history": equity_history,
        "shadow_pools": shadow_pools,
        "rebalance_history": rebalances[-50:],  # last 50
        "last_updated": last_timestamp,
        "is_stale": is_stale,
        "tick_counter": tick_counter,
        "portfolio_equity": portfolio_equity,
        "open_positions": n_open,
        "realized_pnl": realized_pnl,
        "total_fees": total_fees,
        "total_funding": total_funding,
    }


def build_sims_from_engine(config_path: str) -> dict:
    """Build SIMS dict by loading the engine directly (if state dir is in config)."""
    from v4.paper_config import load_paper_config
    from v4.paper_engine import PaperPortfolioEngine

    config = load_paper_config(config_path)
    engine = PaperPortfolioEngine(config)
    return engine.to_dashboard_sim()


# ---------------------------------------------------------------------------
# HTML Generation
# ---------------------------------------------------------------------------

def generate_html(sims_data: list[dict], source: str = "manual") -> str:
    """Generate self-contained HTML dashboard for paper trading.

    Args:
        sims_data: List of SIMS dicts (one per pool/tab).
        source: Origin marker — "runner" from live tick loop, "manual" from CLI.
                Used by push_to_ghpages staleness guard to prevent manual pushes
                from overwriting runner-generated dashboards.
    """
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Slim down data for embedding
    slim = json.loads(json.dumps(sims_data, default=str))
    for sim in slim:
        for t in sim.get("all_trades", []):
            for k in ("pnl", "margin_usd", "funding_cost", "entry_fee", "exit_fee"):
                if k in t and isinstance(t[k], float):
                    t[k] = round(t[k], 2)
            sig = t.get("signal", {})
            for k in list(sig.keys()):
                if isinstance(sig[k], float):
                    sig[k] = round(sig[k], 4)
        # Round equity history
        for e in sim.get("equity_history", []):
            if "mark_to_market_equity" in e and isinstance(e["mark_to_market_equity"], float):
                e["mark_to_market_equity"] = round(e["mark_to_market_equity"], 2)

    data_json = json.dumps(slim, separators=(",", ":"))

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    n_pools = len(sims_data)

    html = f"""<!DOCTYPE html>
<html lang="en" data-generated-at="{generated_at}" data-source="{source}" data-pools="{n_pools}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<title>Paper Trading Dashboard V2</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:-apple-system,'Segoe UI',system-ui,sans-serif; background:#0a0e17; color:#c9d1d9; font-size:13px; }}

.header {{ background:#0d1117; padding:12px 20px; border-bottom:1px solid #21262d; display:flex; justify-content:space-between; align-items:center; position:sticky; top:0; z-index:100; }}
.header h1 {{ font-size:1.1em; color:#bc8cff; font-weight:600; }}
.header .meta {{ color:#484f58; font-size:0.75em; }}

.stale-banner {{ background:#5c0a0a; border:1px solid #f85149; color:#f85149; padding:8px 20px; font-size:0.85em; font-weight:600; display:none; }}
.stale-banner.visible {{ display:block; }}

.sim-tabs {{ display:flex; flex-wrap:wrap; gap:8px; padding:12px 20px; background:#0d1117; border-bottom:1px solid #21262d; }}
.stab {{ padding:12px 16px; border-radius:8px; cursor:pointer; background:#161b22; color:#c9d1d9; border:1px solid #21262d; transition:all 0.15s; min-width:170px; }}
.stab:hover {{ border-color:#30363d; background:#1c2128; }}
.stab.active {{ background:#1f6feb; color:#fff; border-color:#1f6feb; }}
.stab .tab-name {{ font-weight:700; font-size:1.1em; margin-bottom:4px; white-space:nowrap; }}
.stab .tab-pnl {{ font-size:1.3em; font-weight:700; margin-bottom:4px; }}
.stab .tab-row {{ display:flex; justify-content:space-between; align-items:baseline; gap:10px; }}
.stab .tab-equity {{ font-size:0.9em; font-weight:600; color:#8b949e; }} .stab.active .tab-equity {{ color:rgba(255,255,255,0.7); }}
.stab .tab-pnl.pos {{ color:#3fb950; }} .stab.active .tab-pnl.pos {{ color:#a5f3c0; }}
.stab .tab-pnl.neg {{ color:#f85149; }} .stab.active .tab-pnl.neg {{ color:#ffa198; }}
.stab .tab-days {{ font-size:0.85em; color:#8b949e; }} .stab.active .tab-days {{ color:rgba(255,255,255,0.7); }}

.kpi-row {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:8px; padding:12px 20px; }}
.kpi {{ background:#161b22; border:1px solid #21262d; border-radius:6px; padding:10px; }}
.kpi .lbl {{ color:#484f58; font-size:0.65em; text-transform:uppercase; letter-spacing:0.8px; }}
.kpi .val {{ font-size:1.3em; font-weight:700; margin-top:1px; }}

.g {{ color:#3fb950; }} .r {{ color:#f85149; }} .b {{ color:#58a6ff; }} .y {{ color:#d29922; }} .m {{ color:#bc8cff; }}

.sec {{ padding:12px 20px; }}
.sec h2 {{ color:#8b949e; font-size:0.8em; font-weight:600; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:8px; }}
.card {{ background:#161b22; border:1px solid #21262d; border-radius:6px; }}
.chart {{ padding:8px; }}

/* Fund allocation panel */
.fund-panel {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; padding:12px; }}
.fund-bar {{ height:20px; border-radius:4px; display:flex; overflow:hidden; margin:6px 0; }}
.fund-bar .spot {{ background:#1f6feb; }} .fund-bar .perp {{ background:#d29922; }}
.fund-stat {{ display:flex; justify-content:space-between; padding:2px 0; font-size:0.8em; }}
.fund-stat .k {{ color:#484f58; }}
.imb-green {{ color:#3fb950; }} .imb-yellow {{ color:#d29922; }} .imb-red {{ color:#f85149; }}

.pill {{ display:inline-block; padding:1px 6px; border-radius:8px; font-size:0.65em; font-weight:700; letter-spacing:0.3px; }}
.pill.long {{ background:#0d3520; color:#3fb950; }} .pill.short {{ background:#3d1418; color:#f85149; }}
.pill.spot {{ background:#1a2332; color:#58a6ff; }} .pill.perp {{ background:#2d1f0e; color:#d29922; }}

table {{ width:100%; border-collapse:collapse; }}
th {{ background:#161b22; color:#484f58; padding:6px 8px; text-align:left; font-weight:600; font-size:0.65em; text-transform:uppercase; letter-spacing:0.5px; position:sticky; top:0; }}
td {{ padding:5px 8px; border-bottom:1px solid #161b22; font-size:0.8em; }}
tr:hover {{ background:#1c2128; }}
tr.trade-row {{ cursor:pointer; }}

.tbl-wrap {{ overflow-x:auto; max-height:700px; overflow-y:auto; }}
.frow {{ display:flex; gap:3px; margin-bottom:8px; }}
.fb {{ padding:4px 10px; border-radius:4px; cursor:pointer; background:transparent; color:#484f58; border:1px solid #21262d; font-size:0.75em; }}
.fb:hover {{ color:#c9d1d9; border-color:#30363d; }}
.fb.active {{ background:#1f6feb; color:#fff; border-color:#1f6feb; }}

.strat-row {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:8px; }}
.sc {{ padding:12px; }}
.sc h3 {{ color:#c9d1d9; font-size:0.85em; margin-bottom:6px; }}
.sc .row {{ display:flex; justify-content:space-between; padding:2px 0; font-size:0.8em; }}
.sc .row .k {{ color:#484f58; }}
</style>
</head>
<body>

<div class="header">
    <h1>Paper Trading Dashboard V2</h1>
    <div class="meta" id="hdr-time">Updated: {now_iso}</div>
</div>
<div class="stale-banner" id="stale-banner">
    Data is stale — last update was more than 2 hours ago. Check paper trading engine.
</div>
<div class="sim-tabs" id="sim-tabs"></div>
<div id="app"></div>

<script>
const SIMS = {data_json};
let activeSim = (() => {{const h=decodeURIComponent(location.hash.slice(1)); if(!h) return 0; const i=SIMS.findIndex(s=>s.name===h); return i>=0?i:0;}})();
/* Convert header time to user locale */
{{const el=document.getElementById('hdr-time');if(el){{const m=el.textContent.match(/Updated:\\s*(.+)/);if(m){{const d=new Date(m[1]);if(!isNaN(d))el.textContent='Updated: '+d.toLocaleString();}}}}}}

const DK = {{
    paper_bgcolor:'transparent', plot_bgcolor:'transparent',
    font:{{color:'#8b949e',size:10}},
    margin:{{t:30,b:30,l:55,r:10}},
    xaxis:{{gridcolor:'#161b22',zerolinecolor:'#21262d'}},
    yaxis:{{gridcolor:'#161b22',zerolinecolor:'#21262d'}},
    legend:{{bgcolor:'transparent',font:{{color:'#8b949e',size:10}},orientation:'h',y:-0.15}},
}};

const fmt = v => v.toLocaleString(undefined,{{maximumFractionDigits:0}});
const fmtPct = v => (v>=0?'+':'')+v.toFixed(1)+'%';
const pc = v => v>=0?'g':'r';
const fmtPrice = v => {{if(!v) return '-'; if(v>=1000) return '$'+v.toLocaleString(undefined,{{minimumFractionDigits:2,maximumFractionDigits:2}}); if(v>=100) return '$'+v.toFixed(2); if(v>=1) return '$'+v.toFixed(4); if(v>=0.01) return '$'+v.toFixed(6); return '$'+v.toPrecision(4);}};

/* ---- Tabs ---- */
function renderTabs() {{
    document.getElementById('sim-tabs').innerHTML = SIMS.map((s,i) => {{
        const eh = s.equity_history||[];
        const cap = s.capital||200000;
        const mtm = eh.length > 0 ? eh[eh.length-1].mark_to_market_equity : (s.portfolio_equity||cap);
        const pnlPct = cap > 0 ? (mtm - cap) / cap * 100 : 0;
        const pnlStr = (pnlPct >= 0 ? '+' : '') + pnlPct.toFixed(1) + '%';
        const pnlCls = pnlPct >= 0 ? 'pos' : 'neg';
        let daysStr = '';
        if (eh.length > 0) {{
            const first = new Date(eh[0].timestamp);
            const days = Math.max(0, Math.floor((Date.now() - first.getTime()) / 86400000));
            daysStr = days + 'd';
        }}
        return `<div class="stab ${{i===activeSim?'active':''}}" onclick="switchSim(${{i}})">
            <div class="tab-name">${{s.name}}</div>
            <div class="tab-pnl ${{pnlCls}}">${{pnlStr}}</div>
            <div class="tab-row">
                <span class="tab-equity">$${{(mtm/1000).toFixed(1)}}k</span>
                ${{daysStr ? `<span class="tab-days">${{daysStr}}</span>` : ''}}
            </div>
        </div>`;
    }}).join('');
}}
function switchSim(i) {{ activeSim=i; location.hash=encodeURIComponent(SIMS[i].name); renderTabs(); render(); }}

/* ---- Imbalance color ---- */
function imbClass(pct) {{
    if (pct < 20) return 'imb-green';
    if (pct < 40) return 'imb-yellow';
    return 'imb-red';
}}

/* ---- Main render ---- */
function render() {{
    const s = SIMS[activeSim];
    const trades = s.all_trades||[];
    const closedTrades = trades.filter(t=>t.status!=='open');
    const nT = closedTrades.length;
    const nW = closedTrades.filter(t=>(t.pnl||0)>0).length;
    const wr = nT>0?(nW/nT*100):0;
    const totalPnl = s.realized_pnl !== undefined ? s.realized_pnl : trades.reduce((a,t)=>a+(t.pnl||0),0);
    const totalFees = s.total_fees || trades.reduce((a,t)=>a+(t.entry_fee||0)+(t.exit_fee||0),0);
    const totalFunding = s.total_funding || trades.reduce((a,t)=>a+Math.abs(t.funding_cost||0),0);
    const equity = s.portfolio_equity || s.capital;
    const returnPct = s.capital>0 ? (equity - s.capital)/s.capital*100 : 0;
    const nOpen = s.open_positions || 0;

    // Stale banner
    const banner = document.getElementById('stale-banner');
    if (s.is_stale) banner.classList.add('visible');
    else banner.classList.remove('visible');

    // Drawdown from equity history
    let maxDD = 0;
    const eh = s.equity_history||[];
    if (eh.length > 1) {{
        let peak = 0;
        eh.forEach(p => {{
            const eq = p.mark_to_market_equity||0;
            if (eq > peak) peak = eq;
            const dd = peak > 0 ? (eq - peak) / peak * 100 : 0;
            if (dd < maxDD) maxDD = dd;
        }});
    }}

    // Shadow pools
    const sp = s.shadow_pools || {{}};
    const spotTotal = (sp.spot_funds||0);
    const perpTotal = (sp.perp_funds||0);
    const poolTotal = spotTotal + perpTotal;
    const spotPct = poolTotal > 0 ? spotTotal / poolTotal * 100 : 50;
    const perpPct = poolTotal > 0 ? perpTotal / poolTotal * 100 : 50;
    const imbPct = sp.imbalance_pct || 0;
    const blockedCount = sp.blocked_entries_count || 0;

    const openTrades = trades.filter(t=>t.status==='open');
    const unrealizedPnl = openTrades.reduce((a,t)=>a+(t.unrealized_pnl||0),0);
    const totalInvested = openTrades.reduce((a,t)=>a+(t.margin_usd||0),0);
    const mtmEquity = eh.length > 0 ? eh[eh.length-1].mark_to_market_equity : equity;
    const investedPct = mtmEquity > 0 ? (totalInvested / mtmEquity * 100) : 0;

    let h = `
    <div class="kpi-row">
        <div class="kpi"><div class="lbl">Portfolio Equity</div><div class="val ${{pc(mtmEquity - s.capital)}}">$${{fmt(mtmEquity)}}<div style="font-size:0.45em;color:#484f58;margin-top:2px">start $${{fmt(s.capital)}}</div></div></div>
        <div class="kpi"><div class="lbl">Invested Now</div><div class="val m">$${{fmt(totalInvested)}}<div style="font-size:0.45em;color:#484f58;margin-top:2px">${{investedPct.toFixed(1)}}% of equity &middot; ${{nOpen}} pos</div></div></div>
        <div class="kpi"><div class="lbl">Realized P&L</div><div class="val ${{pc(totalPnl)}}">$${{fmt(totalPnl)}}</div></div>
        <div class="kpi"><div class="lbl">Unrealized P&L</div><div class="val ${{pc(unrealizedPnl)}}">$${{fmt(unrealizedPnl)}}</div></div>
        <div class="kpi"><div class="lbl">Max Drawdown</div><div class="val r">${{maxDD.toFixed(2)}}%</div></div>
        <div class="kpi"><div class="lbl">Trades</div><div class="val b">${{nT}} <span style="font-size:0.6em;color:${{nOpen>0?'#3fb950':'#484f58'}}">(${{nOpen}} open)</span></div></div>
        <div class="kpi"><div class="lbl">Win Rate</div><div class="val ${{wr>=50?'g':'y'}}">${{wr.toFixed(1)}}%<div style="font-size:0.45em;color:#484f58;margin-top:2px">${{nW}}/${{nT}}</div></div></div>
        <div class="kpi"><div class="lbl">Total Fees</div><div class="val r">$${{fmt(totalFees)}}</div></div>
        <div class="kpi"><div class="lbl">Funding Paid</div><div class="val r">$${{fmt(totalFunding)}}</div></div>
        <div class="kpi"><div class="lbl">Tick</div><div class="val b">${{s.tick_counter||0}}<div style="font-size:0.45em;color:#484f58;margin-top:2px">${{s.last_updated ? new Date(s.last_updated).toLocaleString() : 'N/A'}}</div></div></div>
    </div>`;

    // ---- Fund Allocation Panel (AC26) ----
    if (poolTotal > 0) {{
        h += `<div class="sec">
        <h2 style="color:#bc8cff">Fund Allocation (Shadow Pools)</h2>
        <div class="card">
            <div class="fund-panel">
                <div>
                    <div style="font-size:0.75em;color:#484f58;margin-bottom:4px">POOL DISTRIBUTION</div>
                    <div class="fund-bar" title="Spot ${{spotPct.toFixed(1)}}% / Perp ${{perpPct.toFixed(1)}}%">
                        <div class="spot" style="width:${{spotPct}}%"></div>
                        <div class="perp" style="width:${{perpPct}}%"></div>
                    </div>
                    <div class="fund-stat"><span class="k">Spot Pool</span><span class="b">$${{fmt(spotTotal)}}</span></div>
                    <div class="fund-stat"><span class="k">Perp Pool</span><span class="y">$${{fmt(perpTotal)}}</span></div>
                    <div class="fund-stat"><span class="k">Spot Deployed</span><span>$${{fmt(sp.spot_deployed||0)}}</span></div>
                    <div class="fund-stat"><span class="k">Perp Deployed</span><span>$${{fmt(sp.perp_deployed||0)}}</span></div>
                </div>
                <div>
                    <div style="font-size:0.75em;color:#484f58;margin-bottom:4px">HEALTH</div>
                    <div class="fund-stat"><span class="k">Imbalance</span><span class="${{imbClass(imbPct)}}" style="font-weight:700">${{imbPct.toFixed(1)}}%</span></div>
                    <div class="fund-stat"><span class="k">Shadow Blocked Entries</span><span style="font-weight:700">${{blockedCount}}</span></div>
                    <div style="margin-top:8px;font-size:0.7em;color:#484f58">
                        Imbalance: <span class="imb-green">green &lt;20%</span> &middot;
                        <span class="imb-yellow">yellow 20-40%</span> &middot;
                        <span class="imb-red">red &gt;40%</span>
                    </div>
                </div>
            </div>
        </div></div>`;
    }}

    // ---- Rebalance History ----
    const rebalHist = s.rebalance_history||[];
    if (rebalHist.length > 0) {{
        h += `<div class="sec">
        <h2 style="color:#bc8cff">Theoretical Rebalance History (${{rebalHist.length}})</h2>
        <div class="card tbl-wrap" style="max-height:250px">
            <table>
                <thead><tr><th>Time</th><th>Transfer Needed</th><th>Direction</th><th>Would Block</th></tr></thead>
                <tbody>${{[...rebalHist].reverse().slice(0,50).map(r => `<tr>
                    <td>${{(r.timestamp||'').substring(0,16)}}</td>
                    <td style="font-weight:700">$${{fmt(r.transfer_needed_usd||0)}}</td>
                    <td style="color:#bc8cff">${{r.direction||''}}</td>
                    <td>${{r.would_have_blocked_entries||0}}</td>
                </tr>`).join('')}}</tbody>
            </table>
        </div></div>`;
    }}

    // ---- Strategy Cards ----
    const strats = s.strategies||[];
    h += `<div class="sec">
        <h2>Strategies</h2>
        <div class="strat-row">${{strats.map(st => `
            <div class="card sc">
                <h3>${{st.name}}</h3>
                <div class="row"><span class="k">Weight</span><span>${{(st.weight*100).toFixed(0)}}%</span></div>
                ${{st.final_equity ? `<div class="row"><span class="k">Equity</span><span class="${{pc((st.final_equity||0)-s.capital)}}" style="font-weight:700">$${{fmt(st.final_equity)}}</span></div>` : ''}}
                ${{st.trade_count !== undefined ? `<div class="row"><span class="k">Trades</span><span>${{st.trade_count}}</span></div>` : ''}}
                ${{st.open_positions !== undefined ? `<div class="row"><span class="k">Open</span><span>${{st.open_positions}}</span></div>` : ''}}
                ${{st.strategies ? `<div class="row"><span class="k">Contains</span><span>${{st.strategies.join(', ')}}</span></div>` : ''}}
            </div>`).join('')}}
        </div>
    </div>`;

    // ---- Equity Chart ----
    h += `<div class="sec">
        <h2>Equity Curve (Mark-to-Market)</h2>
        <div class="card chart"><div id="ch-equity" style="height:300px;"></div></div>
    </div>`;

    // ---- Trade Log ----
    h += `<div class="sec">
        <h2>Trade Log</h2>
        <div class="frow">
            <div class="fb active" onclick="setF(this,'open')">Open (${{nOpen}})</div>
            <div class="fb" onclick="setF(this,'all')">All (${{trades.length}})</div>
            <div class="fb" onclick="setF(this,'winners')">Winners (${{nW}})</div>
            <div class="fb" onclick="setF(this,'losers')">Losers (${{nT-nW}})</div>
        </div>
        <div class="card tbl-wrap">
            <table>
                <thead><tr>
                    <th>Strategy</th><th>Token</th><th>Dir</th><th>Mkt</th>
                    <th>Entered</th><th>Hold</th><th>Entry $</th><th>Exit/Now $</th>
                    <th>Stop $</th><th>% to Stop</th><th>Regime</th>
                    <th>Size</th><th>P&L</th><th>Fees</th><th>Exit</th>
                </tr></thead>
                <tbody id="tb-trades"></tbody>
            </table>
            <div id="show-more-wrap" style="text-align:center;padding:8px;display:none">
                <span class="fb" onclick="showMore()" style="cursor:pointer">Show more trades...</span>
            </div>
        </div>
    </div>`;

    // ---- Per-Token Summary ----
    h += `<div class="sec">
        <h2>Per-Token Summary</h2>
        <div class="card tbl-wrap">
            <table>
                <thead><tr>
                    <th>Token</th><th>Trades</th><th>Win Rate</th>
                    <th>P&L</th><th>Best</th><th>Worst</th><th>Fees</th>
                </tr></thead>
                <tbody id="tb-tokens"></tbody>
            </table>
        </div>
    </div>`;

    document.getElementById('app').innerHTML = h;
    renderTrades(trades, 'open');
    renderTokens(trades);
    setTimeout(() => drawEquity(), 0);
}}

/* ---- Filter ---- */
let curFilter = 'open';
function setF(el, f) {{
    curFilter = f;
    _tradeLimit = 50;
    el.parentElement.querySelectorAll('.fb').forEach(b=>b.classList.remove('active'));
    el.classList.add('active');
    renderTrades(SIMS[activeSim].all_trades||[], f);
}}

/* ---- Trade table ---- */
let _allFiltered = [];
let _tradeLimit = 50;
function renderTrades(trades, filter) {{
    let ft = trades;
    if (filter==='winners') ft = trades.filter(t=>t.status!=='open'&&(t.pnl||0)>0);
    else if (filter==='losers') ft = trades.filter(t=>t.status!=='open'&&(t.pnl||0)<=0);
    else if (filter==='open') ft = trades.filter(t=>t.status==='open');
    // Sort: open positions first, then by exit_bar desc
    _allFiltered = [...ft].sort((a,b)=>{{
        if (a.status==='open' && b.status!=='open') return -1;
        if (b.status==='open' && a.status!=='open') return 1;
        return (b.signal?.exit_bar||b.entry_bar||0)-(a.signal?.exit_bar||a.entry_bar||0);
    }});
    const visible = _allFiltered.slice(0, _tradeLimit);
    const tbody = document.getElementById('tb-trades');
    let rows = '';
    visible.forEach(t => {{
        const isOpen = t.status==='open';
        const pnl = isOpen ? (t.unrealized_pnl||0) : (t.pnl||0);
        const dir = t.direction===-1?'SHORT':'LONG';
        const mt = (t.market_type||'').toLowerCase();
        const totalFee = (t.entry_fee||0)+(t.exit_fee||0)+Math.abs(t.funding_cost||0)+Math.abs(t.cumulative_funding||0);
        const exitCol = isOpen ? fmtPrice(t.current_price) + ' <span style="color:#3fb950;font-size:0.7em">LIVE</span>' : fmtPrice(t.exit_price);
        const reasonCol = isOpen ? '<span class="pill" style="background:#1f3d1f;color:#3fb950">LIVE</span>' : (t.exit_reason||'-');
        const pnlLabel = isOpen ? '~' : '';
        // Stop / regime columns (open positions only)
        let stopCol = '-';
        let pctStopCol = '-';
        let regimeCol = '-';
        if (isOpen) {{
            if (t.stop_price) stopCol = fmtPrice(t.stop_price);
            const pts = t.pct_to_stop;
            if (pts !== undefined && pts !== null) {{
                const ptsClass = pts < 2 ? 'r' : pts < 5 ? 'y' : 'g';
                pctStopCol = `<span class="${{ptsClass}}" style="font-weight:700">${{pts.toFixed(1)}}%</span>`;
            }}
            if (t.regime) {{
                const rc = {{'CRISIS':'r','DOWNTREND':'r','RANGE':'y','QUIET':'b','UPTREND':'g'}}[t.regime]||'';
                const exitR = t.exit_regimes||[];
                const wouldExit = exitR.includes(t.regime);
                regimeCol = `<span class="${{rc}}" style="font-weight:600">${{t.regime}}</span>`;
                if (exitR.length) regimeCol += `<div style="font-size:0.6em;color:#484f58">exits: ${{exitR.join(',')}}</div>`;
            }}
        }}
        rows += `<tr class="trade-row" style="${{isOpen?'background:#0d1f0d;':''}}">
            <td style="color:#8b949e;font-size:0.7em">${{t.strategy||''}}</td>
            <td><b>${{t.token||''}}</b></td>
            <td><span class="pill ${{dir.toLowerCase()}}">${{dir}}</span></td>
            <td><span class="pill ${{mt}}">${{mt.toUpperCase()}}</span></td>
            <td style="font-size:0.75em;color:#8b949e">${{t.entry_timestamp ? new Date(t.entry_timestamp).toLocaleString() : '—'}}</td>
            <td>${{(() => {{ if (isOpen && t.entry_timestamp) {{ const hrs = (Date.now() - new Date(t.entry_timestamp).getTime()) / 3600000; return hrs < 1 ? Math.round(hrs*60)+'m' : hrs.toFixed(1)+'h'; }} return (t.hold_bars||0)+'h'; }})()}}</td>
            <td style="font-size:0.85em">${{fmtPrice(t.entry_price)}}</td>
            <td style="font-size:0.85em">${{exitCol}}</td>
            <td style="font-size:0.85em">${{stopCol}}</td>
            <td>${{pctStopCol}}</td>
            <td style="font-size:0.75em">${{regimeCol}}</td>
            <td>$${{fmt(t.margin_usd||0)}}</td>
            <td class="${{pc(pnl)}}" style="font-weight:600">${{pnlLabel}}$${{fmt(pnl)}}</td>
            <td class="r">$${{totalFee.toFixed(0)}}</td>
            <td style="color:#484f58">${{reasonCol}}</td>
        </tr>`;
    }});
    tbody.innerHTML = rows;
    const sm = document.getElementById('show-more-wrap');
    if(sm) sm.style.display = _allFiltered.length > _tradeLimit ? 'block' : 'none';
}}

function showMore() {{
    _tradeLimit += 100;
    renderTrades(SIMS[activeSim].all_trades||[], curFilter);
}}

/* ---- Per-token table ---- */
function renderTokens(trades) {{
    const m = {{}};
    trades.filter(t=>t.status!=='open').forEach(t => {{
        const tk = t.token||'?';
        if(!m[tk]) m[tk]={{pnl:0,n:0,wins:0,fees:0,best:-Infinity,worst:Infinity}};
        const p = t.pnl||0;
        m[tk].pnl += p;
        m[tk].n++;
        m[tk].fees += (t.entry_fee||0)+(t.exit_fee||0)+Math.abs(t.funding_cost||0);
        if(p>0) m[tk].wins++;
        if(p>0 && p>m[tk].best) m[tk].best=p;
        if(p<0 && p<m[tk].worst) m[tk].worst=p;
    }});
    const tks = Object.keys(m).sort((a,b)=>m[b].pnl-m[a].pnl);
    document.getElementById('tb-tokens').innerHTML = tks.map(tk => {{
        const d=m[tk];
        const wr = d.n>0 ? (d.wins/d.n*100).toFixed(1) : '-';
        const bestStr = d.best!==-Infinity ? `$${{fmt(d.best)}}` : '-';
        const worstStr = d.worst!==Infinity ? `$${{fmt(d.worst)}}` : '-';
        return `<tr><td><b>${{tk}}</b></td><td>${{d.n}}</td>
            <td>${{wr}}${{d.n>0?'%':''}}</td>
            <td class="${{pc(d.pnl)}}" style="font-weight:600">$${{fmt(d.pnl)}}</td>
            <td class="g">${{bestStr}}</td>
            <td class="r">${{worstStr}}</td>
            <td class="r">$${{fmt(d.fees)}}</td></tr>`;
    }}).join('');
}}

/* ---- Equity Chart ---- */
function drawEquity() {{
    const s = SIMS[activeSim];
    const eh = s.equity_history || [];
    if (eh.length < 2) {{
        document.getElementById('ch-equity').innerHTML =
            '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#484f58;font-size:0.9em">No equity data yet — will appear after paper trading produces snapshots</div>';
        return;
    }}
    const cap = s.capital || 0;
    const traces = [{{
        x: eh.map(p=>p.timestamp),
        y: eh.map(p=>(p.mark_to_market_equity||0) - cap),
        name: s.name||'P&L', type:'scatter', mode:'lines',
        line:{{color:'#58a6ff',width:2}},
        fill:'tozeroy', fillcolor:'rgba(88,166,255,0.1)',
    }}];
    Plotly.newPlot('ch-equity', traces, {{
        ...DK, title:'Cumulative P&L (mark-to-market)',
        xaxis:{{...DK.xaxis, type:'date'}},
        yaxis:{{...DK.yaxis,title:'USD',tickprefix:'$'}},
        shapes:[{{type:'line',x0:eh[0].timestamp,x1:eh[eh.length-1].timestamp,y0:0,y1:0,line:{{color:'#484f58',width:1,dash:'dot'}}}}],
    }}, {{responsive:true}});
}}

renderTabs();
render();

/* ---- Auto-refresh: poll for new dashboard version every 60s ---- */
(function() {{
    const cur = document.documentElement.getAttribute('data-generated-at');
    if (!cur) return;
    setInterval(async () => {{
        try {{
            const r = await fetch(location.href, {{cache:'no-store'}});
            const txt = await r.text();
            const m = txt.match(/data-generated-at="([^"]+)"/);
            if (m && m[1] !== cur) location.reload();
        }} catch(e) {{}}
    }}, 60000);
}})();
</script>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# GitHub Pages push
# ---------------------------------------------------------------------------

def _extract_dashboard_meta(html_text: str) -> dict:
    """Extract data-generated-at, data-source, data-pools from dashboard HTML."""
    import re
    meta = {}
    for attr in ("generated-at", "source", "pools"):
        m = re.search(rf'data-{attr}="([^"]+)"', html_text)
        meta[attr] = m.group(1) if m else ""
    return meta


def push_to_ghpages(html_path: Path, force: bool = False):
    """Push dashboard HTML to gh-pages branch under /docs.

    Staleness guard (prevents manual pushes from clobbering runner output):
      1. If existing dashboard was generated by "runner" and new one is "manual",
         BLOCK — manual CLI pushes must not overwrite the live runner's dashboard.
      2. If existing dashboard has more pools than new one (regardless of source),
         BLOCK — a single-pool push must not overwrite a multi-pool dashboard.
      3. If existing is newer by timestamp (same source), BLOCK.

    Pass force=True to bypass all guards.
    """
    print("\nPushing to GitHub Pages (/docs/)")
    tmp = tempfile.mkdtemp(prefix="dashboard-")
    try:
        subprocess.run(
            ["git", "clone", "--branch", "gh-pages", "--single-branch", "--depth", "1",
             "http://10.100.1.10:8080/git/voerses/dashboard.git", tmp],
            check=True, capture_output=True, text=True)

        # --- Staleness guard ---
        existing_index = Path(tmp) / "docs" / "index.html"
        new_html_text = html_path.read_text()
        new_meta = _extract_dashboard_meta(new_html_text)
        if force:
            print("  --force: bypassing staleness guards")
        elif existing_index.exists():
            old_meta = _extract_dashboard_meta(existing_index.read_text())

            # Guard 1: manual must not overwrite runner
            if old_meta.get("source") == "runner" and new_meta.get("source") == "manual":
                print(f"BLOCKED: refusing to overwrite runner dashboard with manual push. "
                      f"Use --force to override.")
                return

            # Guard 2: fewer pools must not overwrite more pools
            old_pools = int(old_meta.get("pools") or 0)
            new_pools = int(new_meta.get("pools") or 0)
            if old_pools > 1 and new_pools < old_pools:
                print(f"BLOCKED: existing dashboard has {old_pools} pools, "
                      f"new has {new_pools}. Not overwriting multi-pool dashboard "
                      f"with fewer pools. Use --force to override.")
                return

            # Guard 3: timestamp check (same source only)
            old_ts = old_meta.get("generated-at", "")
            new_ts = new_meta.get("generated-at", "")
            if old_ts and new_ts and old_ts > new_ts:
                print(f"SKIP: existing dashboard is newer ({old_ts}) than ours ({new_ts}).")
                return

        # Remove old v1/v2 subdirectories if present
        for old_dir in ["v1", "v2"]:
            old_path = Path(tmp) / old_dir
            if old_path.exists():
                shutil.rmtree(str(old_path))

        # Remove root-level index.html (moved to docs/)
        root_index = Path(tmp) / "index.html"
        if root_index.exists():
            root_index.unlink()

        # Dashboard under /docs
        docs_dir = Path(tmp) / "docs"
        docs_dir.mkdir(exist_ok=True)
        shutil.copy2(str(html_path), str(docs_dir / "index.html"))
        Path(tmp, ".nojekyll").touch()

        subprocess.run(
            ["git", "-C", tmp, "add", "-A"],
            check=True, capture_output=True)

        r = subprocess.run(
            ["git", "-C", tmp, "diff", "--cached", "--quiet"],
            capture_output=True)
        if r.returncode == 0:
            print("No changes to push.")
            return

        ts_label = new_meta.get("generated-at", "") or datetime.now().strftime('%Y-%m-%d %H:%M')
        subprocess.run(
            ["git", "-C", tmp, "commit", "-m",
             f"Update dashboard {ts_label}"],
            check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "-C", tmp, "push", "origin", "gh-pages"],
            check=True, capture_output=True, text=True)
        print(f"Pushed to gh-pages (generated at {ts_label}).")
    except subprocess.CalledProcessError as e:
        print(f"Push failed: {e.stderr}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def discover_all_pools(base_dir: str = "state") -> list[dict]:
    """Auto-discover all v4_paper* state directories and build SIMS data for each.

    Looks for directories matching state/v4_paper* that contain state.json.
    Each pool's config.json (if present) provides pool_name and strategy info.
    Returns list of SIMS dicts sorted by pool_name.
    """
    base = Path(base_dir)
    sims = []
    for d in sorted(base.glob("v4_paper*")):
        if not d.is_dir():
            continue
        if not (d / "state.json").exists():
            continue
        try:
            sim = build_sims_from_state_dir(str(d))
            sims.append(sim)
            n_trades = len(sim.get("all_trades", []))
            equity = sim.get("portfolio_equity", sim.get("capital", 0))
            print(f"  {sim['name']}: {n_trades} trades, equity ${equity:,.0f}")
        except Exception as e:
            print(f"  {d.name}: FAILED ({e})")
    return sims


DEFAULT_MULTI_CONFIG = "configs/multi_v4_paper.json"


def load_pools_from_multi_config(config_path: str) -> list[dict]:
    """Load pools from a multi-portfolio config — same path as the runner.

    This is the single source of truth for pool ordering and membership.
    Both the runner and CLI use this to produce identical dashboards.
    """
    with open(config_path) as f:
        data = json.load(f)

    portfolios = data.get("portfolios", [])
    sims = []
    for pf in portfolios:
        state_dir = pf.get("state_dir", "")
        if not state_dir or not Path(state_dir).exists():
            continue
        if not (Path(state_dir) / "state.json").exists():
            continue
        cfg_path = os.path.join(state_dir, "config.json")
        try:
            sim = build_sims_from_state_dir(state_dir, cfg_path)
            sims.append(sim)
            n_trades = len(sim.get("all_trades", []))
            equity = sim.get("portfolio_equity", sim.get("capital", 0))
            print(f"  {sim['name']}: {n_trades} trades, equity ${equity:,.0f}")
        except Exception as e:
            print(f"  {pf.get('pool_name', state_dir)}: FAILED ({e})")
    return sims


def main():
    parser = argparse.ArgumentParser(
        description="Paper Trading Dashboard V2 Generator"
    )
    parser.add_argument(
        "--config", type=str, default="",
        help="Path to multi-portfolio config JSON (default: configs/multi_v4_paper.json)"
    )
    parser.add_argument(
        "--state-dir", type=str, default="",
        help="Path to a single state directory (state.json, trades.jsonl, equity.csv)"
    )
    parser.add_argument(
        "--push", action="store_true",
        help="Push to GitHub Pages after generating"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Force push, bypassing staleness guards"
    )
    parser.add_argument(
        "--output", type=str, default="",
        help="Output HTML path (default: docs/index.html)"
    )
    args = parser.parse_args()
    os.chdir(PROJECT_ROOT)

    # Build SIMS data
    sims_data = []

    if args.state_dir:
        # Single state directory mode
        print(f"Loading paper trading data from {args.state_dir}...")
        sim = build_sims_from_state_dir(args.state_dir, args.config)
        sims_data.append(sim)
        n_trades = len(sim.get("all_trades", []))
        equity = sim.get("portfolio_equity", sim.get("capital", 0))
        print(f"  {sim['name']}: {n_trades} trades, equity ${equity:,.0f}")

    else:
        # Load from multi-config (same path as the runner)
        config_path = args.config or DEFAULT_MULTI_CONFIG
        if not os.path.exists(config_path):
            print(f"Config not found: {config_path}")
            return
        print(f"Loading pools from {config_path}...")
        sims_data = load_pools_from_multi_config(config_path)

    if not sims_data:
        print("No paper trading data found.")
        return

    # Generate HTML — CLI always uses source="runner" since it uses the
    # same config and ordering as the runner (single deployment path)
    html = generate_html(sims_data, source="runner")

    # Write output
    if args.output:
        out = Path(args.output)
    else:
        out = DOCS_DIR / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    print(f"\nDashboard: {out} ({len(html)/1024:.0f} KB)")

    if args.push:
        push_to_ghpages(out, force=args.force)


if __name__ == "__main__":
    main()
