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
import subprocess
import sys

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

    # Generate config
    gen = ConfigGenerator()
    config = gen.generate(strategy, exchange, pairs=["BTC/USDT"])

    # Read optional credentials (never written to disk)
    creds = get_exchange_credentials(exchange)

    # Register instance
    mgr = InstanceManager(state_dir=STATE_DIR)
    mgr._load_state()
    instance_id = mgr.start(strategy=strategy, exchange=exchange,
                            config_path="runtime")

    # Write config to file for Freqtrade (credentials stripped — keys stay empty)
    config_dir = os.path.join(STATE_DIR, instance_id)
    os.makedirs(config_dir, exist_ok=True)
    config_path = os.path.join(config_dir, "config.json")
    with open(config_path, "w") as f:
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

    with open(log_path, "a") as log_file:
        proc = subprocess.Popen(
            [
                "freqtrade", "trade",
                "--config", config_path,
                "--strategy", strategy,
                "--db-url", f"sqlite:///{config_dir}/tradesv3.sqlite",
                "--api-server-port", str(port),
            ],
            stdout=log_file,
            stderr=log_file,
            env=env,
            start_new_session=True,
        )

    print(f"Started instance: {instance_id}")
    print(f"  Log: {log_path}")
    print(f"  Port: {port}")
    print(f"  PID: {proc.pid}")


def cmd_stop(args):
    """Stop a running instance."""
    mgr = InstanceManager(state_dir=STATE_DIR)
    mgr._load_state()
    mgr.stop(args.instance_id)
    print(f"Stopped instance: {args.instance_id}")


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
