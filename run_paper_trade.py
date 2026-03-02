"""
run_paper_trade.py — CLI launcher for the paper trading system.

Wires together SetupValidator, ConfigGenerator, InstanceManager, Monitor,
and CompareInstances into a single CLI with subcommands:
  setup, start, stop, status, list, monitor, compare

Usage:
    python run_paper_trade.py setup
    python run_paper_trade.py start s11 binance
    python run_paper_trade.py stop s11_binance
    python run_paper_trade.py status [instance_id]
    python run_paper_trade.py list
    python run_paper_trade.py monitor [instance_id]
    python run_paper_trade.py compare s11_binance s09_kraken
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

from paper_trading.setup_paper_trading import SetupValidator
from paper_trading.instance_manager import InstanceManager
from paper_trading.monitor import Monitor
from paper_trading.compare_instances import CompareInstances
from freqtrade_bridge.config_generator import (
    ConfigGenerator,
    EXCHANGE_OFFSET,
    STRATEGY_NUM,
    BASE_PORT,
)

LOG_DIR = "paper_trading/logs"
STATE_DIR = "paper_trading"
SWEEP_RESULTS_DIR = "results"
STAKE_CURRENCY = "USDT"


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="run_paper_trade",
        description="Paper trading launcher — manage Freqtrade dry-run instances",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # setup
    sub.add_parser("setup", help="Validate environment and create directories")

    # start
    p_start = sub.add_parser("start", help="Start a paper trading instance")
    p_start.add_argument("strategy", help="Strategy name (e.g., s11)")
    p_start.add_argument("exchange", help="Exchange name (e.g., binance)")
    p_start.add_argument("--resume", action="store_true",
                         help="Resume latest run (default: fresh run with new DB)")
    p_start.add_argument("--run-id", default=None,
                         help="Custom run ID (default: auto-generated timestamp)")

    # stop
    p_stop = sub.add_parser("stop", help="Stop a running instance")
    p_stop.add_argument("instance_id", help="Instance ID (e.g., s11_binance)")

    # status
    p_status = sub.add_parser("status", help="Show instance status")
    p_status.add_argument("instance_id", nargs="?", default=None,
                          help="Instance ID (omit for all)")

    # list
    sub.add_parser("list", help="List all instances")

    # monitor
    p_monitor = sub.add_parser("monitor", help="Show monitoring data")
    p_monitor.add_argument("instance_id", nargs="?", default=None,
                           help="Instance ID (omit for summary)")

    # compare
    p_compare = sub.add_parser("compare", help="Compare two instances head-to-head")
    p_compare.add_argument("instance_a", help="First instance ID")
    p_compare.add_argument("instance_b", help="Second instance ID")

    # runs — list all runs for an instance
    p_runs = sub.add_parser("runs", help="List all runs for an instance")
    p_runs.add_argument("instance_id", help="Instance ID (e.g., s11_binance)")

    # metrics — compute metrics from a specific run
    p_metrics = sub.add_parser("metrics", help="Compute metrics from a run's trade DB")
    p_metrics.add_argument("instance_id", help="Instance ID (e.g., s11_binance)")
    p_metrics.add_argument("--run-id", default=None,
                           help="Run ID (default: latest run)")

    return parser


def get_exchange_credentials(exchange: str) -> dict:
    """Read optional API credentials from environment variables.

    Returns empty dict if no credentials are set.
    Requires both key and secret to be present; partial credentials are ignored.
    """
    exchange = exchange.lower()
    prefix = exchange.upper()
    key = os.environ.get(f"{prefix}_API_KEY")
    secret = os.environ.get(f"{prefix}_API_SECRET")

    if key and secret:
        return {"key": key, "secret": secret}
    return {}


def load_validated_pairs(strategy: str, sweep_dir: str = SWEEP_RESULTS_DIR) -> list:
    """Load validated token pairs from the latest V3 validation results.

    Finds the latest full validation run for the strategy and extracts
    the validated_tokens list. Falls back to BTC/USDT if no data found.

    Returns:
        List of trading pairs, e.g. ['SUI/USDT', 'AVAX/USDT', ...]
        Falls back to ['BTC/USDT'] if no validation data found.
    """
    import glob as globmod

    # Find validation files for this strategy (e.g., validation_s11_*.json)
    pattern = os.path.join(sweep_dir, f"validation_{strategy}_*.json")
    files = sorted(globmod.glob(pattern))
    if not files:
        print(f"  Warning: No validation results for {strategy} in {sweep_dir}, defaulting to BTC/USDT")
        return [f"BTC/{STAKE_CURRENCY}"]

    # Find the latest full validation run (n_total == 49 preferred, else most tokens)
    best_file = None
    best_count = 0
    for fpath in reversed(files):
        with open(fpath) as f:
            data = json.load(f)
        tokens = data.get("validated_tokens", [])
        n_total = data.get("n_total", 0)
        if n_total >= 49 and len(tokens) > 0:
            best_file = data
            break
        if len(tokens) > best_count:
            best_count = len(tokens)
            best_file = data

    if not best_file or not best_file.get("validated_tokens"):
        print(f"  Warning: No validated tokens for {strategy}, defaulting to BTC/USDT")
        return [f"BTC/{STAKE_CURRENCY}"]

    tokens = best_file["validated_tokens"]
    pairs = [f"{token}/{STAKE_CURRENCY}" for token in tokens]
    return pairs


def create_run_dir(instance_dir: str, run_id: str = None) -> tuple:
    """Create an isolated run directory with its own DB.

    Returns (run_dir, run_id) where run_dir is the absolute path.
    """
    if not run_id:
        run_id = datetime.now(timezone.utc).strftime("run_%Y%m%d_%H%M%S")

    runs_dir = os.path.join(instance_dir, "runs")
    run_dir = os.path.join(runs_dir, run_id)
    os.makedirs(run_dir, exist_ok=True)

    # Update 'current' symlink to point to this run
    current_link = os.path.join(instance_dir, "current")
    if os.path.islink(current_link):
        os.unlink(current_link)
    elif os.path.exists(current_link):
        os.remove(current_link)
    os.symlink(os.path.abspath(run_dir), current_link)

    return run_dir, run_id


def get_latest_run(instance_dir: str) -> str:
    """Get the path to the latest run directory."""
    current_link = os.path.join(instance_dir, "current")
    if os.path.islink(current_link):
        return os.readlink(current_link)

    # Fallback: find most recent run_* directory
    runs_dir = os.path.join(instance_dir, "runs")
    if os.path.isdir(runs_dir):
        runs = sorted([d for d in os.listdir(runs_dir)
                       if d.startswith("run_")])
        if runs:
            return os.path.join(runs_dir, runs[-1])

    return instance_dir  # legacy: no runs yet


def list_runs(instance_dir: str) -> list:
    """List all runs for an instance with basic metadata."""
    runs_dir = os.path.join(instance_dir, "runs")
    if not os.path.isdir(runs_dir):
        return []

    current_link = os.path.join(instance_dir, "current")
    current_target = None
    if os.path.islink(current_link):
        current_target = os.readlink(current_link)

    result = []
    for name in sorted(os.listdir(runs_dir)):
        run_path = os.path.join(runs_dir, name)
        if not os.path.isdir(run_path):
            continue
        db_path = os.path.join(run_path, "tradesv3.sqlite")
        trade_count = 0
        if os.path.exists(db_path):
            import sqlite3
            conn = sqlite3.connect(db_path)
            try:
                trade_count = conn.execute(
                    "SELECT COUNT(*) FROM trades"
                ).fetchone()[0]
            except Exception:
                pass
            conn.close()
        is_current = (os.path.abspath(run_path) == current_target)
        result.append({
            "run_id": name,
            "path": run_path,
            "has_db": os.path.exists(db_path),
            "trade_count": trade_count,
            "is_current": is_current,
        })
    return result


def compute_run_metrics(db_path: str, starting_capital: float = 200_000) -> dict:
    """Compute trading metrics from a run's SQLite DB."""
    import sqlite3
    import math

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT close_profit, close_profit_abs, stake_amount, "
        "       open_date, close_date "
        "FROM trades WHERE is_open = 0 ORDER BY close_date"
    ).fetchall()
    open_count = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE is_open = 1"
    ).fetchone()[0]
    conn.close()

    empty = {
        "closed_trades": 0,
        "open_trades": open_count,
        "total_profit": 0.0,
        "return_on_capital_pct": 0.0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "expectancy": 0.0,
        "best_trade": 0.0,
        "worst_trade": 0.0,
        "avg_trade_duration_hours": 0.0,
        "shortest_trade_hours": 0.0,
        "longest_trade_hours": 0.0,
        "sharpe_ratio": 0.0,
        "sortino_ratio": 0.0,
        "max_drawdown": 0.0,
        "max_drawdown_pct": 0.0,
        "max_consecutive_wins": 0,
        "max_consecutive_losses": 0,
        "equity_high": starting_capital,
        "equity_low": starting_capital,
        "equity_final": starting_capital,
    }

    if not rows:
        return empty

    profits_abs = [r[1] or 0.0 for r in rows]
    profits_ratio = [r[0] or 0.0 for r in rows]

    # --- Trade durations (hours) ---
    durations = []
    for r in rows:
        open_dt, close_dt = r[3], r[4]
        if open_dt and close_dt:
            try:
                from datetime import datetime as _dt
                fmt = "%Y-%m-%d %H:%M:%S"
                # Handle pandas Timestamp strings
                o = str(open_dt)[:19]
                c = str(close_dt)[:19]
                dur = (_dt.strptime(c, fmt) - _dt.strptime(o, fmt)).total_seconds() / 3600
                durations.append(dur)
            except Exception:
                pass

    # --- Basic stats ---
    total = sum(profits_abs)
    n = len(rows)
    wins_list = [p for p in profits_abs if p > 0]
    losses_list = [p for p in profits_abs if p < 0]
    n_wins = len(wins_list)
    n_losses = len(losses_list)
    win_rate = n_wins / n

    gross_profit = sum(wins_list)
    gross_loss = abs(sum(losses_list))
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    avg_win = (sum(wins_list) / n_wins) if n_wins else 0.0
    avg_loss = (sum(losses_list) / n_losses) if n_losses else 0.0
    expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)

    best_trade = max(profits_abs)
    worst_trade = min(profits_abs)

    # --- Return on capital ---
    roc_pct = (total / starting_capital) * 100 if starting_capital else 0

    # --- Sharpe ratio ---
    if n > 1:
        mean_r = sum(profits_ratio) / n
        var_r = sum((r - mean_r) ** 2 for r in profits_ratio) / (n - 1)
        std_r = math.sqrt(var_r) if var_r > 0 else 0
        sharpe = (mean_r / std_r * math.sqrt(n)) if std_r > 0 else 0
    else:
        sharpe = 0

    # --- Sortino ratio (downside deviation only) ---
    downside = [r for r in profits_ratio if r < 0]
    if len(downside) > 1 and n > 1:
        mean_r = sum(profits_ratio) / n
        down_var = sum((r - mean_r) ** 2 for r in downside) / (len(downside) - 1)
        down_std = math.sqrt(down_var) if down_var > 0 else 0
        sortino = (mean_r / down_std * math.sqrt(n)) if down_std > 0 else 0
    elif downside:
        sortino = 0
    else:
        sortino = float("inf") if total > 0 else 0

    # --- Max drawdown + equity curve ---
    cum = starting_capital
    peak = starting_capital
    max_dd = 0
    equity_high = starting_capital
    equity_low = starting_capital
    for p in profits_abs:
        cum += p
        if cum > peak:
            peak = cum
        dd = cum - peak
        if dd < max_dd:
            max_dd = dd
        if cum > equity_high:
            equity_high = cum
        if cum < equity_low:
            equity_low = cum

    max_dd_pct = (max_dd / peak * 100) if peak > 0 else 0

    # --- Consecutive wins/losses ---
    max_con_wins = 0
    max_con_losses = 0
    cur_wins = 0
    cur_losses = 0
    for p in profits_abs:
        if p > 0:
            cur_wins += 1
            cur_losses = 0
        elif p < 0:
            cur_losses += 1
            cur_wins = 0
        else:
            cur_wins = 0
            cur_losses = 0
        if cur_wins > max_con_wins:
            max_con_wins = cur_wins
        if cur_losses > max_con_losses:
            max_con_losses = cur_losses

    return {
        "closed_trades": n,
        "open_trades": open_count,
        "total_profit": round(total, 2),
        "return_on_capital_pct": round(roc_pct, 3),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(pf, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "best_trade": round(best_trade, 2),
        "worst_trade": round(worst_trade, 2),
        "avg_trade_duration_hours": round(sum(durations) / len(durations), 1) if durations else 0,
        "shortest_trade_hours": round(min(durations), 1) if durations else 0,
        "longest_trade_hours": round(max(durations), 1) if durations else 0,
        "sharpe_ratio": round(sharpe, 3),
        "sortino_ratio": round(sortino, 3),
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct, 3),
        "max_consecutive_wins": max_con_wins,
        "max_consecutive_losses": max_con_losses,
        "equity_high": round(equity_high, 2),
        "equity_low": round(equity_low, 2),
        "equity_final": round(cum, 2),
    }


def allocate_port(strategy: str, exchange: str) -> int:
    """Deterministic port allocation: 8000 + strategy_num * 10 + exchange_offset."""
    strat_num = STRATEGY_NUM[strategy]
    ex_offset = EXCHANGE_OFFSET[exchange.lower()]
    return BASE_PORT + strat_num * 10 + ex_offset


def cmd_setup(args):
    """Run environment setup and validation."""
    validator = SetupValidator()
    result = validator.run_setup()
    print(f"Setup complete: success={result['success']}")
    print(f"  Python: {result['python_version']} (valid={result['python_valid']})")
    if result["created_dirs"]:
        print(f"  Created directories: {', '.join(result['created_dirs'])}")
    else:
        print("  All directories already exist")


def cmd_start(args):
    """Start a paper trading instance."""
    strategy = args.strategy
    exchange = args.exchange.lower()

    # Load validated pairs from sweep results
    pairs = load_validated_pairs(strategy)
    print(f"  Validated pairs ({len(pairs)}): {', '.join(pairs)}")

    # Generate config
    gen = ConfigGenerator()
    config = gen.generate(strategy, exchange, pairs=pairs)

    # Inject proxy if environment has one (needed for environments behind HTTP proxy)
    http_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if http_proxy:
        proxy_conf = {"https": http_proxy, "http": http_proxy}
        config["exchange"].setdefault("ccxt_sync_config", {})
        config["exchange"].setdefault("ccxt_async_config", {})
        config["exchange"]["ccxt_sync_config"]["proxies"] = proxy_conf
        config["exchange"]["ccxt_async_config"]["aiohttp_proxy"] = http_proxy

    # Add Freqtrade runtime config keys not in the base generator
    config.setdefault("entry_pricing", {
        "price_side": "same",
        "use_order_book": True,
        "order_book_top": 1,
    })
    config.setdefault("exit_pricing", {
        "price_side": "same",
        "use_order_book": True,
        "order_book_top": 1,
    })
    config.setdefault("pairlists", [{"method": "StaticPairList"}])
    # Disable WebSocket if behind proxy (WebSocket can't traverse HTTP proxies)
    if http_proxy:
        config["exchange"]["enable_ws"] = False

    # Ensure API server has required auth fields
    if "api_server" in config and config["api_server"].get("enabled"):
        config["api_server"].setdefault("username", "paper")
        config["api_server"].setdefault("password", "paper")
        config["api_server"].setdefault("jwt_secret_key",
                                         f"paper_{strategy}_{exchange}")

    # Read optional credentials (never written to disk)
    creds = get_exchange_credentials(exchange)

    # Register instance
    mgr = InstanceManager(state_dir=STATE_DIR)
    mgr._load_state()
    instance_id = mgr.start(strategy=strategy, exchange=exchange,
                            config_path="runtime")

    # Create isolated run directory (or resume latest)
    instance_dir = os.path.join(STATE_DIR, instance_id)
    os.makedirs(instance_dir, exist_ok=True)

    if getattr(args, "resume", False):
        run_dir = get_latest_run(instance_dir)
        run_id = os.path.basename(run_dir)
        print(f"  Resuming run: {run_id}")
    else:
        run_dir, run_id = create_run_dir(instance_dir,
                                          getattr(args, "run_id", None))
        print(f"  New run: {run_id} (fresh DB)")

    # Write config to run directory (credentials stripped — keys stay empty)
    config_path = os.path.join(run_dir, "config.json")
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    # Also write to instance root for backward compat
    root_config = os.path.join(instance_dir, "config.json")
    with open(root_config, "w") as f:
        json.dump(config, f, indent=2)

    # Set up log file
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = f"{LOG_DIR}/{instance_id}.log"

    # Launch Freqtrade as background subprocess
    port = allocate_port(strategy, exchange)
    env = os.environ.copy()
    if creds:
        env["FREQTRADE__EXCHANGE__KEY"] = creds["key"]
        env["FREQTRADE__EXCHANGE__SECRET"] = creds["secret"]

    # Find freqtrade binary
    ft_bin = shutil.which("freqtrade")
    if not ft_bin:
        # Check the venv bin directory (same directory as the running Python)
        venv_bin = os.path.join(os.path.dirname(sys.executable), "freqtrade")
        if os.path.isfile(venv_bin):
            ft_bin = venv_bin
    if not ft_bin:
        # Check common user-install location
        pylib_bin = os.path.join(
            os.environ.get("PYTHONUSERBASE", ""), "bin", "freqtrade"
        )
        if os.path.isfile(pylib_bin):
            ft_bin = pylib_bin
    if not ft_bin:
        raise FileNotFoundError(
            "freqtrade not found on PATH. Install it with: "
            "pip install freqtrade"
        )

    # Ensure freqtrade subprocess can find its own packages
    if "PYTHONUSERBASE" in os.environ:
        pylib = os.environ["PYTHONUSERBASE"]
        site_pkgs = os.path.join(pylib, "lib", f"python{sys.version_info.major}.{sys.version_info.minor}", "site-packages")
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{site_pkgs}:{existing}" if existing else site_pkgs
        env["PYTHONUSERBASE"] = pylib

    # Resolve paths to absolute for subprocess
    abs_config = os.path.abspath(config_path)
    abs_db = os.path.abspath(os.path.join(run_dir, "tradesv3.sqlite"))
    abs_userdir = os.path.abspath("user_data")

    with open(log_path, "a") as log_file:
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "freqtrade", "trade",
                "--config", abs_config,
                "--strategy", config.get("strategy", "CpcvSwingStrategy"),
                "--userdir", abs_userdir,
                "--db-url", f"sqlite:///{abs_db}",
            ],
            stdout=log_file,
            stderr=log_file,
            env=env,
            start_new_session=True,
        )

    print(f"Started instance: {instance_id}")
    print(f"  Run: {run_id}")
    print(f"  DB: {abs_db}")
    print(f"  Log: {log_path}")
    print(f"  Port: {port}")
    print(f"  PID: {proc.pid}")


def cmd_stop(args):
    """Stop a running instance and generate a run report."""
    instance_id = args.instance_id
    instance_dir = os.path.join(STATE_DIR, instance_id)

    # Generate report before stopping (trades DB is still current)
    report = None
    if os.path.isdir(instance_dir):
        run_dir = get_latest_run(instance_dir)
        report = generate_run_report(instance_id, run_dir)

    mgr = InstanceManager(state_dir=STATE_DIR)
    mgr._load_state()
    mgr.stop(instance_id)

    print(f"Stopped instance: {instance_id}")
    if report and "error" not in report:
        report_path = os.path.join(run_dir, "report.json")
        m = report["metrics"]
        print(f"  Report saved: {report_path}")
        print(f"  --- Performance ---")
        print(f"  Trades:          {m['closed_trades']} closed, {m['open_trades']} open")
        print(f"  Total profit:    ${m['total_profit']:,.2f} ({m['return_on_capital_pct']:.3f}%)")
        print(f"  Win rate:        {m['win_rate']:.1%}")
        print(f"  Profit factor:   {m['profit_factor']}")
        print(f"  Expectancy:      ${m['expectancy']:,.2f}/trade")
        print(f"  Best / Worst:    ${m['best_trade']:,.2f} / ${m['worst_trade']:,.2f}")
        print(f"  Avg win / loss:  ${m['avg_win']:,.2f} / ${m['avg_loss']:,.2f}")
        print(f"  --- Risk ---")
        print(f"  Sharpe:          {m['sharpe_ratio']}")
        print(f"  Sortino:         {m['sortino_ratio']}")
        print(f"  Max drawdown:    ${m['max_drawdown']:,.2f} ({m['max_drawdown_pct']:.3f}%)")
        print(f"  Consec. W/L:     {m['max_consecutive_wins']} / {m['max_consecutive_losses']}")
        print(f"  --- Equity ---")
        print(f"  High / Low:      ${m['equity_high']:,.2f} / ${m['equity_low']:,.2f}")
        print(f"  Final:           ${m['equity_final']:,.2f}")
        print(f"  --- Duration ---")
        print(f"  Avg:             {m['avg_trade_duration_hours']:.1f}h")
        print(f"  Range:           {m['shortest_trade_hours']:.1f}h - {m['longest_trade_hours']:.1f}h")
        if report.get("per_pair"):
            print(f"  --- Per Pair ---")
            for pair, stats in sorted(report["per_pair"].items()):
                print(f"    {pair}: {stats['trades']} trades, "
                      f"${stats['profit']:,.2f}, "
                      f"WR={stats['win_rate']:.0%}")


def cmd_status(args):
    """Show instance status."""
    mgr = InstanceManager(state_dir=STATE_DIR)
    mgr._load_state()

    if args.instance_id:
        st = mgr.status(args.instance_id)
        print(f"{args.instance_id}: {st}")
    else:
        instances = mgr.list()
        if not instances:
            print("No instances found")
        else:
            for inst in instances:
                print(f"{inst['instance_id']}: {inst['status']}")


def cmd_list(args):
    """List all instances."""
    mgr = InstanceManager(state_dir=STATE_DIR)
    mgr._load_state()
    instances = mgr.list()

    if not instances:
        print("No instances found")
    else:
        for inst in instances:
            print(f"{inst['instance_id']}  {inst['exchange']}  {inst['status']}")


def cmd_monitor(args):
    """Show monitoring data."""
    mon = Monitor()

    if args.instance_id:
        view = mon.single_instance_view(args.instance_id)
        for k, v in view.items():
            print(f"  {k}: {v}")
    else:
        table = mon.summary_table()
        for row in table:
            print(f"{row.get('instance_id', 'unknown')}  "
                  f"equity={row.get('equity', 'N/A')}  "
                  f"drawdown={row.get('drawdown', 'N/A')}")


def cmd_compare(args):
    """Compare two instances head-to-head."""
    comp = CompareInstances()
    result = comp.head_to_head(args.instance_a, args.instance_b)

    print(f"Comparison: {args.instance_a} vs {args.instance_b}")
    if "winner" in result:
        print(f"  Winner: {result['winner']}")
    for k, v in result.items():
        if k != "winner":
            print(f"  {k}: {v}")


def cmd_runs(args):
    """List all runs for an instance."""
    instance_dir = os.path.join(STATE_DIR, args.instance_id)
    if not os.path.isdir(instance_dir):
        print(f"Instance not found: {args.instance_id}")
        return

    runs = list_runs(instance_dir)
    if not runs:
        # Check for legacy (non-run) DB
        legacy_db = os.path.join(instance_dir, "tradesv3.sqlite")
        if os.path.exists(legacy_db):
            print(f"Legacy mode (no run isolation): {legacy_db}")
        else:
            print("No runs found")
        return

    print(f"Runs for {args.instance_id}:")
    for r in runs:
        marker = " <- current" if r["is_current"] else ""
        print(f"  {r['run_id']}  trades={r['trade_count']}"
              f"  db={'yes' if r['has_db'] else 'no'}{marker}")


def cmd_metrics(args):
    """Compute metrics from a run's trade DB."""
    instance_dir = os.path.join(STATE_DIR, args.instance_id)
    if not os.path.isdir(instance_dir):
        print(f"Instance not found: {args.instance_id}")
        return

    # Find the DB
    if args.run_id:
        run_dir = os.path.join(instance_dir, "runs", args.run_id)
    else:
        run_dir = get_latest_run(instance_dir)

    db_path = os.path.join(run_dir, "tradesv3.sqlite")
    if not os.path.exists(db_path):
        # Fallback to legacy location
        db_path = os.path.join(instance_dir, "tradesv3.sqlite")
    if not os.path.exists(db_path):
        print(f"No trade DB found for {args.instance_id}")
        return

    run_id = os.path.basename(run_dir)
    metrics = compute_run_metrics(db_path)

    m = metrics
    print(f"Metrics for {args.instance_id} (run: {run_id}):")
    print(f"  Closed trades:       {m['closed_trades']}")
    print(f"  Open trades:         {m['open_trades']}")
    print(f"  Total profit:        ${m['total_profit']:,.2f}")
    print(f"  Return on capital:   {m['return_on_capital_pct']:.3f}%")
    print(f"  Win rate:            {m['win_rate']:.1%}")
    print(f"  Profit factor:       {m['profit_factor']}")
    print(f"  Avg win:             ${m['avg_win']:,.2f}")
    print(f"  Avg loss:            ${m['avg_loss']:,.2f}")
    print(f"  Expectancy:          ${m['expectancy']:,.2f}")
    print(f"  Best trade:          ${m['best_trade']:,.2f}")
    print(f"  Worst trade:         ${m['worst_trade']:,.2f}")
    print(f"  Avg duration:        {m['avg_trade_duration_hours']:.1f}h")
    print(f"  Shortest trade:      {m['shortest_trade_hours']:.1f}h")
    print(f"  Longest trade:       {m['longest_trade_hours']:.1f}h")
    print(f"  Sharpe ratio:        {m['sharpe_ratio']}")
    print(f"  Sortino ratio:       {m['sortino_ratio']}")
    print(f"  Max drawdown:        ${m['max_drawdown']:,.2f} ({m['max_drawdown_pct']:.3f}%)")
    print(f"  Consec. wins:        {m['max_consecutive_wins']}")
    print(f"  Consec. losses:      {m['max_consecutive_losses']}")
    print(f"  Equity high:         ${m['equity_high']:,.2f}")
    print(f"  Equity low:          ${m['equity_low']:,.2f}")
    print(f"  Equity final:        ${m['equity_final']:,.2f}")


def generate_run_report(instance_id: str, run_dir: str) -> dict:
    """Generate a full report when a run is stopped.

    Reads all trades from the run's DB and produces a JSON report with:
    - Run metadata (start/end time, instance, strategy, exchange)
    - All trades with entry/exit details
    - Aggregate metrics (win rate, Sharpe, profit factor, etc.)
    - Per-pair breakdown
    """
    import sqlite3

    db_path = os.path.join(run_dir, "tradesv3.sqlite")
    if not os.path.exists(db_path):
        return {"error": "No trade DB found", "instance_id": instance_id}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # All trades
    trades_raw = conn.execute(
        "SELECT * FROM trades ORDER BY open_date"
    ).fetchall()
    trades = [dict(t) for t in trades_raw]

    # Timestamps from KeyValueStore (may not exist in all DBs)
    bot_start = None
    try:
        kvs = conn.execute("SELECT * FROM KeyValueStore").fetchall()
        for row in kvs:
            row_dict = dict(row)
            if row_dict.get("key") == "bot_start_time":
                bot_start = (row_dict.get("string_value")
                             or row_dict.get("value"))
    except Exception:
        pass

    conn.close()

    # Compute metrics
    metrics = compute_run_metrics(db_path)

    # Per-pair breakdown
    pair_stats = {}
    for t in trades:
        if t.get("is_open"):
            continue
        pair = t.get("pair", "unknown")
        if pair not in pair_stats:
            pair_stats[pair] = {"trades": 0, "profit": 0.0, "wins": 0}
        pair_stats[pair]["trades"] += 1
        profit = t.get("close_profit_abs") or 0.0
        pair_stats[pair]["profit"] += profit
        if profit > 0:
            pair_stats[pair]["wins"] += 1
    for pair in pair_stats:
        s = pair_stats[pair]
        s["win_rate"] = round(s["wins"] / s["trades"], 4) if s["trades"] else 0
        s["profit"] = round(s["profit"], 2)

    # Build trade details list
    trade_details = []
    for t in trades:
        trade_details.append({
            "id": t.get("id"),
            "pair": t.get("pair"),
            "is_open": bool(t.get("is_open")),
            "open_rate": t.get("open_rate"),
            "close_rate": t.get("close_rate"),
            "stake_amount": t.get("stake_amount"),
            "profit_ratio": t.get("close_profit"),
            "profit_abs": t.get("close_profit_abs"),
            "open_date": t.get("open_date"),
            "close_date": t.get("close_date"),
            "exit_reason": t.get("exit_reason"),
            "strategy": t.get("strategy"),
        })

    run_id = os.path.basename(run_dir)
    report = {
        "instance_id": instance_id,
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bot_start_time": bot_start,
        "metrics": metrics,
        "per_pair": pair_stats,
        "trades": trade_details,
    }

    # Save report
    report_path = os.path.join(run_dir, "report.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    return report


def main(argv=None) -> int:
    """Entry point. Returns exit code (0=success, 1=error)."""
    parser = build_parser()

    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return e.code if e.code else 0

    commands = {
        "setup": cmd_setup,
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "list": cmd_list,
        "monitor": cmd_monitor,
        "compare": cmd_compare,
        "runs": cmd_runs,
        "metrics": cmd_metrics,
    }

    handler = commands.get(args.command)
    if not handler:
        print(f"Error: unknown command '{args.command}'", file=sys.stderr)
        return 1

    try:
        handler(args)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
