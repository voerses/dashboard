#!/usr/bin/env python3
"""Run 12-month backtest for all 21 portfolios in multi_v4_paper.json."""
import subprocess, json, re, sys, time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG = PROJECT_ROOT / "configs" / "multi_v4_paper.json"

with open(CONFIG) as f:
    cfg = json.load(f)

shared = cfg["shared"]


def parse_result(output):
    metrics = {}
    for line in output.split('\n'):
        for key, pattern in [
            ('return', r'Total Return:.*?([+-]?\d+\.?\d*)%'),
            ('annual', r'Annualized:.*?([+-]?\d+\.?\d*)%'),
            ('calmar', r'Calmar:.*?([+-]?\d+\.?\d*)'),
            ('sharpe', r'Sharpe:.*?([+-]?\d+\.?\d*)'),
            ('sortino', r'Sortino:.*?([+-]?\d+\.?\d*)'),
            ('max_dd', r'Max Drawdown:.*?([+-]?\d+\.?\d*)%'),
            ('trades', r'Total Trades:.*?(\d+)'),
            ('win_rate', r'Win Rate:.*?(\d+\.?\d*)%'),
            ('pf', r'Profit Factor:.*?(\d+\.?\d*)'),
            ('payoff', r'Payoff Ratio:.*?(\d+\.?\d*)'),
            ('fees', r'Total Fees:.*?\$([0-9,]+)'),
            ('avg_hold', r'Avg Hold:.*?(\d+)h'),
        ]:
            if key not in metrics:
                m = re.search(pattern, line)
                if m:
                    val = m.group(1).replace(',', '')
                    metrics[key] = float(val) if '.' in val or key != 'trades' else int(val)
    return metrics


results = []
portfolios = cfg["portfolios"]

for i, port in enumerate(portfolios):
    pool_name = port["pool_name"]
    strats = port["strategies"]
    capital = int(port.get("initial_capital", 200000))
    conc = port.get("concentration_limit", shared.get("concentration_limit", 0.10))
    adv_cap = shared.get("adv_cap_pct", 0.05)
    max_port_pos = port.get("max_portfolio_positions", 40)

    # Build strategy list and per-strategy max positions
    strat_ids = [s["strategy_id"] for s in strats]
    markets = [s.get("market", "combined") for s in strats]
    max_pos = max(s.get("max_positions", 15) for s in strats)

    # Determine market: if all same, use that; otherwise use combined
    unique_markets = set(markets)
    if len(unique_markets) == 1:
        market = list(unique_markets)[0]
    else:
        market = "combined"

    strat_str = ",".join(strat_ids)

    cmd = [
        sys.executable, str(PROJECT_ROOT / "v4" / "portfolio_backtest.py"),
        "--strategy", strat_str,
        "--months", "12",
        "--capital", str(capital),
        "--concentration", str(conc),
        "--adv-cap", str(adv_cap),
        "--max-positions", str(max_pos),
        "--max-portfolio-positions", str(max_port_pos),
        "--market", market,
    ]

    print(f"[{i+1}/{len(portfolios)}] Running {pool_name} ({strat_str}, mkt={market}, conc={conc})...", flush=True)
    t0 = time.time()

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(PROJECT_ROOT))
        metrics = parse_result(proc.stdout + proc.stderr)
        if not metrics:
            metrics = {"error": "no metrics parsed", "stderr": proc.stderr[-200:] if proc.stderr else ""}
    except Exception as e:
        metrics = {"error": str(e)}

    elapsed = time.time() - t0
    metrics["pool_name"] = pool_name
    metrics["strategies"] = strat_str
    metrics["market"] = market
    metrics["concentration"] = conc
    results.append(metrics)

    ann = metrics.get("annual", "ERR")
    cal = metrics.get("calmar", "ERR")
    dd = metrics.get("max_dd", "ERR")
    pf = metrics.get("pf", "ERR")
    wr = metrics.get("win_rate", "ERR")
    trd = metrics.get("trades", 0)
    sha = metrics.get("sharpe", "ERR")
    print(f"  -> Ann={ann:>8} Cal={cal:>6} DD={dd:>7} PF={pf:>5} Sha={sha:>5} WR={wr:>5} Trd={trd:>5} ({elapsed:.1f}s)")

# Sort by annual return
valid = [r for r in results if "annual" in r]
valid.sort(key=lambda x: x.get("annual", float("-inf")), reverse=True)

print(f"\n{'='*140}")
print("RANKED BY ANNUAL RETURN (12mo, $200k)")
print(f"{'='*140}")
print(f"{'#':>2} {'Pool':25s} {'Strategies':30s} {'Mkt':8s} {'Conc':>5} | {'Ann%':>7} {'Cal':>6} {'DD%':>7} {'Sha':>6} {'Sor':>6} {'PF':>5} {'WR%':>5} {'Trd':>5}")
print("-" * 140)
for j, r in enumerate(valid):
    print(f"{j+1:>2} {r['pool_name']:25s} {r['strategies']:30s} {r['market']:8s} {r['concentration']:>5} | "
          f"{r.get('annual','?'):>7} {r.get('calmar','?'):>6} {r.get('max_dd','?'):>7} "
          f"{r.get('sharpe','?'):>6} {r.get('sortino','?'):>6} {r.get('pf','?'):>5} "
          f"{r.get('win_rate','?'):>5} {r.get('trades','?'):>5}")

errors = [r for r in results if "annual" not in r]
if errors:
    print(f"\n*** {len(errors)} ERRORS: ***")
    for r in errors:
        print(f"  {r['pool_name']}: {r.get('error', 'unknown')}")

outfile = PROJECT_ROOT / "results" / "v4" / "all_21_portfolios_12mo.json"
with open(outfile, "w") as f:
    json.dump(results, f, indent=2)
print(f"\nSaved to {outfile}")
