"""V4 Paper Trading — Multi-portfolio runner.

Runs multiple PaperPortfolioEngine instances sharing one LiveFetcher.
Data is fetched once per tick, then each engine processes independently.
Dashboard shows all portfolios as separate tabs.

Usage:
    python -m v4.run_paper_multi --config configs/multi_v4_paper.json
    python -m v4.run_paper_multi --config configs/multi_v4_paper.json --once
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time

from v4.paper_config import PaperConfig, validate_paper_config
from v4.config import StrategySpec
from v4.paper_engine import PaperPortfolioEngine
from v4.signals import discover_tokens
from v4.paper_utils import restore_state, acquire_pid_lock, compute_sleep_until_next_hour

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Multi-portfolio config loading
# ---------------------------------------------------------------------------

def load_multi_config(path: str) -> list[PaperConfig]:
    """Load a multi-portfolio config.

    Format:
        {
          "shared": { ... common fields ... },
          "portfolios": [ { ... per-portfolio ... }, ... ]
        }
    """
    with open(path) as f:
        data = json.load(f)

    shared = data.get("shared", {})
    portfolios = data.get("portfolios", [])

    if not portfolios:
        raise ValueError("No portfolios defined in config")

    configs: list[PaperConfig] = []
    for i, pf in enumerate(portfolios):
        merged = {**shared, **pf}

        if "strategies" not in merged:
            raise ValueError(f"Portfolio {i} missing 'strategies'")

        strategy_list = []
        for j, s in enumerate(merged["strategies"]):
            if "strategy_id" not in s:
                raise ValueError(f"Portfolio {i}, strategy {j} missing 'strategy_id'")
            strategy_list.append(StrategySpec(
                strategy_id=s["strategy_id"],
                weight=s.get("weight", 1.0),
                max_positions=s.get("max_positions", 15),
                market=s.get("market", "combined"),
                strategy_type=s.get("strategy_type", "per_token"),
            ))

        config = PaperConfig(
            strategies=strategy_list,
            capital=merged.get("initial_capital", 200_000.0),
            max_portfolio_positions=merged.get("max_portfolio_positions", 40),
            concentration_limit=merged.get("concentration_limit", 0.10),
            adv_cap_pct=merged.get("adv_cap_pct", 0.05),
            min_position_usd=merged.get("min_position_usd", 200.0),
            exchange=merged.get("exchange", "binance"),
            seed=merged.get("seed", 42),
            base_spread_bps=merged.get("base_spread_bps", 3.0),
            impact_coeff=merged.get("impact_coeff", 0.03),
            stress_adv_multiplier=merged.get("stress_adv_multiplier", 1.0),
            max_slip_bps=merged.get("max_slip_bps", 300),
            mode=merged.get("mode", "pool"),
            pool_name=merged.get("pool_name", f"portfolio_{i}"),
            lookback_months=merged.get("lookback_months", 12),
            enable_purge_windows=merged.get("enable_purge_windows", False),
            drawdown_alert_pct=merged.get("drawdown_alert_pct", 5.0),
            alert_webhook_url=merged.get("alert_webhook_url", ""),
            shadow_rebalance_threshold=merged.get("shadow_rebalance_threshold", 100.0),
            state_dir=merged.get("state_dir", f"state/v4_paper_{i}/"),
            dashboard_push=merged.get("dashboard_push", False),
            dynamic_weights=merged.get("dynamic_weights", False),
            dynamic_weights_smoothing=merged.get("dynamic_weights_smoothing", 0.3),
            conviction_mode=merged.get("conviction_mode", "shuffle"),
            min_conviction_threshold=merged.get("min_conviction_threshold", 0.0),
        )
        config.config_path = path
        configs.append(config)

    return configs


def write_portfolio_configs(configs: list[PaperConfig]) -> None:
    """Write per-portfolio config.json into each state_dir.

    The dashboard reads these for pool_name, strategy info, etc.
    """
    for config in configs:
        os.makedirs(config.state_dir, exist_ok=True)
        cfg_data = {
            "pool_name": config.pool_name,
            "initial_capital": config.capital,
            "mode": config.mode,
            "strategies": [
                {"strategy_id": s.strategy_id, "weight": s.weight,
                 "market": s.market, "max_positions": s.max_positions}
                for s in config.strategies
            ],
        }
        cfg_path = os.path.join(config.state_dir, "config.json")
        with open(cfg_path, "w") as f:
            json.dump(cfg_data, f, indent=2)


# ---------------------------------------------------------------------------
# Shared data fetch
# ---------------------------------------------------------------------------

def fetch_all_data(fetcher, all_configs: list[PaperConfig]) -> tuple[int, int, int, int]:
    """Fetch data for all tokens across all portfolios (once).

    Returns (fetch_ok, fetch_err, bars_appended, funding_merged).
    """
    # Collect all unique tokens across all portfolios
    all_tokens: set[str] = set()
    for config in all_configs:
        for spec in config.strategies:
            try:
                tokens = discover_tokens(spec.market)
                all_tokens.update(tokens)
            except Exception:
                pass

    fetch_ok = fetch_err = bars_appended = funding_merged = 0

    for token in all_tokens:
        for market in ("spot", "perp"):
            try:
                bars = fetcher.fetch_ohlcv(token, market, limit=24)
                closed = fetcher.filter_closed_bars(bars)
                if closed:
                    fetcher.append_to_parquet(token, market, closed)
                    bars_appended += len(closed)
                fetch_ok += 1
            except Exception as e:
                fetch_err += 1
                if fetch_err <= 3:
                    logger.warning("Fetch %s/%s failed: %s", token, market, e)

        # Funding rates for perp
        try:
            rates = fetcher.fetch_funding_rates(token, limit=24)
            if rates and hasattr(fetcher, 'merge_funding_into_parquet'):
                fetcher.merge_funding_into_parquet(token, rates)
                funding_merged += 1
        except Exception as e:
            if fetch_err <= 3:
                logger.warning("Funding %s failed: %s", token, e)

    logger.info(
        "Data fetch: %d ok, %d err, %d bars appended, %d funding merged",
        fetch_ok, fetch_err, bars_appended, funding_merged,
    )
    return fetch_ok, fetch_err, bars_appended, funding_merged


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def push_combined_dashboard(configs: list[PaperConfig]) -> None:
    """Generate dashboard with all portfolios as tabs and push to gh-pages."""
    try:
        from tools.generate_dashboard_v2 import (
            build_sims_from_state_dir, generate_html, push_to_ghpages,
        )
        from pathlib import Path

        all_sims = []
        for config in configs:
            cfg_path = os.path.join(config.state_dir, "config.json")
            sim = build_sims_from_state_dir(config.state_dir, cfg_path)
            all_sims.append(sim)

        html = generate_html(all_sims, source="runner")
        out_path = Path("docs") / "index.html"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html)
        push_to_ghpages(out_path)
        logger.info("Dashboard pushed to gh-pages (%d tabs)", len(all_sims))
    except Exception:
        logger.exception("Dashboard push failed (non-fatal)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="V4 Multi-Portfolio Paper Trading",
        prog="run_paper_multi",
    )
    parser.add_argument("--config", required=True, help="Multi-portfolio config JSON")
    parser.add_argument("--once", action="store_true", help="Single tick then exit")
    parser.add_argument("--status", action="store_true", help="Print status and exit")
    parser.add_argument("--fetch-only", action="store_true",
                        help="Fetch latest data only (no tick). No PID lock needed.")
    parser.add_argument("--prices", action="store_true",
                        help="Fetch and print current prices for all tokens. No PID lock needed.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    # Load multi-portfolio config
    configs = load_multi_config(args.config)
    for config in configs:
        validate_paper_config(config)

    # Write per-portfolio config.json for dashboard
    write_portfolio_configs(configs)

    # Create shared fetcher
    import ccxt
    from v4.live_fetcher import LiveFetcher
    exchange_cls = getattr(ccxt, configs[0].exchange, None) or ccxt.binance
    ccxt_config: dict = {"enableRateLimit": True}
    proxy = os.environ.get("HTTPS_PROXY", os.environ.get("https_proxy", ""))
    if proxy:
        ccxt_config["proxies"] = {"https": proxy, "http": proxy}
    exchange = exchange_cls(ccxt_config)
    shared_fetcher = LiveFetcher(exchange=exchange, data_dir="data")

    # Create engines + restore state
    engines: list[PaperPortfolioEngine] = []
    for config in configs:
        engine = PaperPortfolioEngine(config)
        # Don't set fetcher — we handle fetch centrally
        restore_state(engine, config)
        engines.append(engine)

    if args.status:
        for i, (engine, config) in enumerate(zip(engines, configs)):
            all_states = engine._get_all_states()
            equity = sum(s.portfolio_equity for s in all_states)
            open_pos = sum(s.position_manager.total_open() for s in all_states)
            pnl = sum(s.realized_pnl for s in all_states)
            print(f"[{config.pool_name}] tick={engine.tick_counter} "
                  f"equity=${equity:,.0f} open={open_pos} pnl=${pnl:,.0f}")
        return

    if args.fetch_only:
        # Fetch data only — no tick, no PID lock
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
        ok, err, bars, funding = fetch_all_data(shared_fetcher, configs)
        pct = ok / max(ok + err, 1) * 100
        print(f"\nFetch complete: {ok} ok, {err} err ({pct:.0f}% success), "
              f"{bars} bars appended, {funding} funding merged")
        if err > ok:
            print("WARNING: majority of fetches failed — check network/proxy")
            sys.exit(1)
        return

    if args.prices:
        # Fetch and print current prices — no tick, no PID lock
        from v4.signals import discover_tokens
        all_tokens: set[str] = set()
        for config in configs:
            for spec in config.strategies:
                try:
                    all_tokens.update(discover_tokens(spec.market))
                except Exception:
                    pass
        print(f"Fetching prices for {len(all_tokens)} tokens...")
        prices = {}
        errors = 0
        for token in sorted(all_tokens):
            try:
                ticker = exchange.fetch_ticker(f"{token}/USDT")
                prices[token] = ticker.get("last", 0.0)
            except Exception:
                errors += 1
        if prices:
            max_name = max(len(t) for t in prices)
            for token, price in sorted(prices.items()):
                print(f"  {token:<{max_name}} ${price:>12,.6f}")
            print(f"\n{len(prices)} prices fetched, {errors} errors")
        else:
            print(f"All fetches failed ({errors} errors) — check network/proxy")
            sys.exit(1)
        return

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # PID lock (shared across all portfolios)
    lock_dir = "state/v4_paper_multi/"
    os.makedirs(lock_dir, exist_ok=True)
    lock_file = acquire_pid_lock(lock_dir)

    # Graceful shutdown
    shutdown = threading.Event()

    def shutdown_handler(signum, frame):
        shutdown.set()

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    portfolio_names = [c.pool_name for c in configs]
    logger.info(
        "Multi-portfolio paper trading started — portfolios=%s, capital=$%.0f each",
        portfolio_names, configs[0].capital,
    )

    if args.once:
        # Single tick
        fetch_all_data(shared_fetcher, configs)
        for engine, config in zip(engines, configs):
            result = engine.tick()
            logger.info(
                "[%s] Tick %d — entries=%d exits=%d open=%d equity=$%.0f mtm=$%.0f",
                config.pool_name, result.tick_counter, result.entries, result.exits,
                result.open_positions, result.portfolio_equity, result.mark_to_market_equity,
            )
        push_combined_dashboard(configs)
        lock_file.close()
        return

    try:
        while not shutdown.is_set():
            try:
                # Step 1: Fetch data once for all portfolios
                fetch_ok, fetch_err, bars_appended, _ = fetch_all_data(shared_fetcher, configs)
            except Exception:
                logger.exception("Data fetch failed")
                if shutdown.wait(60):
                    break
                continue

            # Stale-data guard: skip tick if no new bars were appended
            # (all fetches failed = network down, no point ticking on stale data)
            if bars_appended == 0 and fetch_ok == 0 and fetch_err > 0:
                logger.warning(
                    "All %d fetches failed — network down? Skipping tick, "
                    "retrying in 5 minutes", fetch_err,
                )
                if shutdown.wait(300):
                    break
                continue

            # Step 2: Tick each engine (no fetcher — data already in parquet)
            any_success = False
            for engine, config in zip(engines, configs):
                try:
                    result = engine.tick()
                except Exception:
                    logger.exception("[%s] Tick failed", config.pool_name)
                    continue

                if result.error:
                    logger.error("[%s] Tick %d error: %s",
                                config.pool_name, result.tick_counter, result.error)
                elif result.skipped:
                    logger.info("[%s] Tick %d skipped", config.pool_name, result.tick_counter)
                else:
                    any_success = True
                    logger.info(
                        "[%s] Tick %d — entries=%d exits=%d open=%d "
                        "equity=$%.0f mtm=$%.0f rss=%.0fMB %.1fs",
                        config.pool_name, result.tick_counter,
                        result.entries, result.exits, result.open_positions,
                        result.portfolio_equity, result.mark_to_market_equity,
                        result.peak_rss_mb, result.processing_time_s,
                    )

                for alert in result.alerts:
                    logger.warning("[%s] ALERT: %s", config.pool_name, alert)

            # Step 3: Push combined dashboard
            if any_success:
                push_combined_dashboard(configs)

            # Sleep until next hour
            sleep_s = compute_sleep_until_next_hour(time.time())
            logger.info("Sleeping %.0fs until next hour", sleep_s)
            if shutdown.wait(sleep_s):
                break

    finally:
        lock_file.close()
        logger.info("Multi-portfolio paper trading stopped")


if __name__ == "__main__":
    main()
