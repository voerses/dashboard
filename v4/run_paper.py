"""V4 Paper Trading — CLI runner.

Usage:
    python -m v4.run_paper --config config.json [--once] [--status]
"""
from __future__ import annotations

import argparse
import sys
import time

from v4.paper_config import load_paper_config, validate_paper_config
from v4.paper_engine import PaperPortfolioEngine


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="V4 Paper Trading Engine",
        prog="run_paper",
    )
    parser.add_argument(
        "--config", required=True,
        help="Path to JSON config file",
    )
    parser.add_argument(
        "--once", action="store_true", default=False,
        help="Process a single tick, then exit",
    )
    parser.add_argument(
        "--status", action="store_true", default=False,
        help="Print current state and exit",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    args = parse_args(argv)

    # Load and validate config
    config = load_paper_config(args.config)
    validate_paper_config(config)

    # Create engine
    engine = PaperPortfolioEngine(config)

    if args.status:
        # Print status and exit
        # Use state directly if available (pool mode), otherwise aggregate (independent)
        if engine.state is not None:
            equity = engine.state.portfolio_equity
            open_pos = engine.state.position_manager.total_open()
            pnl = engine.state.realized_pnl
            fees = engine.state.total_fees
            funding = engine.state.total_funding
        else:
            all_states = engine._get_all_states()
            equity = sum(s.portfolio_equity for s in all_states)
            open_pos = sum(s.position_manager.total_open() for s in all_states)
            pnl = sum(s.realized_pnl for s in all_states)
            fees = sum(s.total_fees for s in all_states)
            funding = sum(s.total_funding for s in all_states)
        tick = engine.tick_counter
        last_ts = getattr(engine, 'last_timestamp', 'N/A')

        print(f"Paper Trading Status")
        print(f"  Tick counter:      {tick}")
        print(f"  Last timestamp:    {last_ts}")
        print(f"  Portfolio equity:  ${equity:,.2f}")
        print(f"  Open positions:    {open_pos}")
        print(f"  Realized P&L:      ${pnl:,.2f}")
        print(f"  Total fees:        ${fees:,.2f}")
        print(f"  Total funding:     ${funding:,.2f}")
        return

    if args.once:
        # Process single tick and exit
        result = engine.tick()
        error = getattr(result, 'error', None)
        if error is not None and isinstance(error, str):
            print(f"Error: {error}", file=sys.stderr)
            sys.exit(1)
        return

    # Continuous mode (not yet fully implemented — requires live data)
    print("Continuous mode not yet implemented. Use --once for single tick.")


if __name__ == "__main__":
    main()
