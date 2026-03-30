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
from v4.config import StrategySpec, SizingDefaults
from v4.paper_engine import PaperPortfolioEngine
from v4.signals import discover_tokens
from v4.paper_utils import restore_state, acquire_pid_lock, compute_sleep_until_next_hour
from v4.dashboard_state import write_dashboard_state
from v4.data_maintenance import ensure_data_fresh

logger = logging.getLogger(__name__)

# AC10: Promote live→historical every 4 hours
PROMOTE_INTERVAL_S = 14400


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

    # Parse shared sizing_defaults
    sd_fields = {f.name for f in SizingDefaults.__dataclass_fields__.values()}
    shared_sd_raw = shared.get("sizing_defaults", {})
    unknown_shared = set(shared_sd_raw.keys()) - sd_fields
    if unknown_shared:
        raise ValueError(f"Unknown shared sizing_defaults keys: {unknown_shared}")

    configs: list[PaperConfig] = []
    for i, pf in enumerate(portfolios):
        merged = {**shared, **pf}

        if "strategies" not in merged:
            raise ValueError(f"Portfolio {i} missing 'strategies'")

        strategy_list = []
        for j, s in enumerate(merged["strategies"]):
            if "strategy_id" not in s:
                raise ValueError(f"Portfolio {i}, strategy {j} missing 'strategy_id'")
            strategy_list.append(StrategySpec.from_dict(s))

        # Per-portfolio sizing_defaults can override shared
        pf_sd_raw = pf.get("sizing_defaults", {})
        unknown_pf = set(pf_sd_raw.keys()) - sd_fields
        if unknown_pf:
            raise ValueError(f"Portfolio {i}: unknown sizing_defaults keys: {unknown_pf}")
        sizing_defaults = SizingDefaults(**{**shared_sd_raw, **pf_sd_raw})

        config = PaperConfig(
            strategies=strategy_list,
            sizing_defaults=sizing_defaults,
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
            max_sizing_equity=merged.get("max_sizing_equity", None),
            sentinel_mode=merged.get("sentinel_mode", "off"),
            confirmation_tiers=merged.get("confirmation_tiers", {"btc_eth": 30, "top10": 60, "other": 90}),
            carry_strategies=merged.get("carry_strategies", []),
            exit_resolution=merged.get("exit_resolution", 0),
            dedicated_ws=merged.get("dedicated_ws", False),
            skip_walk_forward=merged.get("skip_walk_forward", False),
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
                 "market": s.market, "max_positions": s.max_positions,
                 "exit_resolution": s.exit_resolution}
                for s in config.strategies
            ],
        }
        cfg_path = os.path.join(config.state_dir, "config.json")
        with open(cfg_path, "w") as f:
            json.dump(cfg_data, f, indent=2)


# ---------------------------------------------------------------------------
# AC11: 1m fetch for tokens with open positions
# ---------------------------------------------------------------------------

def fetch_1m_for_open_positions(fetcher, engines: list) -> None:
    """Fetch 1m data for perp tokens with open positions across all engines.

    Called after each hourly fetch. Only perp tokens with open positions get
    1m data — spot positions are skipped (1m is for perp sub-hourly exits only).

    Acquires the maintenance lock to prevent data loss from concurrent
    parquet writes (e.g., if standalone data_maintenance runs in parallel).
    """
    from v4.data_maintenance import _maintenance_lock

    tokens_to_fetch = set()
    for engine in engines:
        try:
            for state in engine._get_all_states():
                for pos in state.position_manager.open_positions:
                    # Only fetch 1m for perp positions (1m data is perp-only)
                    if getattr(pos, "is_perp", True):
                        tokens_to_fetch.add(pos.token)
        except Exception as e:
            logger.warning("Error collecting open positions: %s", e)

    if not tokens_to_fetch:
        return

    with _maintenance_lock("data", timeout_s=5.0) as acquired:
        if not acquired:
            logger.warning("1m fetch skipped — could not acquire maintenance lock")
            return

        for token in sorted(tokens_to_fetch):
            try:
                bars = fetcher.fetch_ohlcv(token, market="perp", timeframe="1m", limit=48)
                closed = fetcher.filter_closed_bars_1m(bars)
                if closed:
                    fetcher.append_to_1m_parquet(token, closed)
                    logger.debug("1m fetch: %s — %d bars", token, len(closed))
            except Exception as e:
                logger.warning("1m fetch failed for %s: %s", token, e)


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
            logger.warning("Funding %s failed: %s", token, e)

    logger.info(
        "Data fetch: %d ok, %d err, %d bars appended, %d funding merged",
        fetch_ok, fetch_err, bars_appended, funding_merged,
    )
    return fetch_ok, fetch_err, bars_appended, funding_merged


# ---------------------------------------------------------------------------
# Shared WebSocket PriceMonitor — fan-out and lifecycle helpers
# ---------------------------------------------------------------------------

class _PriceFanOut:
    """Routes price updates from one PriceMonitor to multiple CandleAggregators.

    Each callback is invoked with (token, price, timestamp_ms).
    Exceptions in individual callbacks are caught and logged — one broken
    engine does not block others from receiving price updates.
    """

    def __init__(self, callbacks: list):
        self._callbacks = callbacks
        self._error_counts: dict[int, int] = {}

    def __call__(self, token: str, price: float, timestamp_ms: int) -> None:
        for i, cb in enumerate(self._callbacks):
            try:
                cb(token, price, timestamp_ms)
                self._error_counts[i] = 0
            except Exception:
                count = self._error_counts.get(i, 0) + 1
                self._error_counts[i] = count
                if count <= 3:
                    logger.error("Fan-out callback %d failed", i, exc_info=True)
                elif count == 4:
                    logger.error("Suppressing repeated fan-out errors for callback %d", i)


def _market_to_venue(config: PaperConfig) -> str:
    """Derive WebSocket venue from a portfolio's strategy market types.

    'perp' and 'combined' both use the perp WebSocket feed.
    'spot' uses the spot WebSocket feed.
    """
    markets = {s.market for s in config.strategies}
    if "spot" in markets and "perp" not in markets and "combined" not in markets:
        return "spot"
    return "perp"


def _setup_shared_monitors(
    configs: list[PaperConfig],
) -> tuple[dict[tuple[str, str], object], list[PaperPortfolioEngine]]:
    """Group engines by (exchange, venue) and create shared PriceMonitors.

    Engines with dedicated_ws=True create their own monitors (not shared).
    Returns (shared_monitors dict, list of engines in config order).
    """
    from v4.price_monitor import PriceMonitor

    # Group non-dedicated configs by (exchange, venue)
    groups: dict[tuple[str, str], list[int]] = {}  # key -> config indices
    for i, cfg in enumerate(configs):
        resolutions = [s.exit_resolution for s in cfg.strategies if s.exit_resolution > 0]
        if not resolutions or cfg.dedicated_ws:
            continue
        venue = _market_to_venue(cfg)
        key = (cfg.exchange, venue)
        groups.setdefault(key, []).append(i)

    # Create one shared PriceMonitor per (exchange, venue) group.
    # callback_lists holds mutable lists that _PriceFanOut references directly —
    # engine CandleAggregator callbacks are appended after engine creation below.
    shared_monitors: dict[tuple[str, str], object] = {}
    callback_lists: dict[tuple[str, str], list] = {}
    for key in groups:
        callback_lists[key] = []
        fanout = _PriceFanOut(callback_lists[key])
        _exchange, venue = key
        shared_monitors[key] = PriceMonitor(callback=fanout, venue=venue)

    # Create engines — shared get injected monitor, dedicated create their own
    engines: list[PaperPortfolioEngine] = []
    for i, cfg in enumerate(configs):
        venue = _market_to_venue(cfg)
        key = (cfg.exchange, venue)
        if key in shared_monitors and not cfg.dedicated_ws:
            engine = PaperPortfolioEngine(cfg, price_monitor=shared_monitors[key])
            # Register this engine's CandleAggregator in the fan-out
            if engine._candle_aggregator is not None:
                callback_lists[key].append(engine._candle_aggregator.on_price)
        else:
            engine = PaperPortfolioEngine(cfg)
        engines.append(engine)

    return shared_monitors, engines


def _connect_shared_monitors(
    shared_monitors: dict[tuple[str, str], object],
    engine_groups: dict[tuple[str, str], list],
) -> None:
    """Connect shared monitors at startup with all open tokens from their engines."""
    for key, monitor in shared_monitors.items():
        engines = engine_groups.get(key, [])
        all_tokens = set()
        for engine in engines:
            for st in engine._get_all_states():
                for pos in st.position_manager.open_positions:
                    all_tokens.add(pos.token)
            # Include tokens with pending entry candidates
            for (sid, token) in engine._pending_entry_candidates:
                all_tokens.add(token)
        if all_tokens:
            try:
                monitor.connect(list(all_tokens))
                logger.info("[shared:%s:%s] PriceMonitor started (%d tokens)",
                            key[0], key[1], len(all_tokens))
            except Exception:
                logger.warning("[shared:%s:%s] PriceMonitor start failed", key[0], key[1])


def _update_shared_subscriptions(
    shared_monitors: dict[tuple[str, str], object],
    engine_groups: dict[tuple[str, str], list],
) -> None:
    """Update shared monitor subscriptions with union of all engines' open tokens.

    Handles lazy connect: if the monitor was never started (no positions at boot),
    calls connect() instead of update_subscriptions() when tokens first appear.
    """
    for key, monitor in shared_monitors.items():
        engines = engine_groups.get(key, [])
        all_tokens = set()
        for engine in engines:
            for st in engine._get_all_states():
                for pos in st.position_manager.open_positions:
                    all_tokens.add(pos.token)
            # Include tokens with pending entry candidates
            for (sid, token) in engine._pending_entry_candidates:
                all_tokens.add(token)
        # Lazy connect: if monitor was never started (no positions at boot),
        # start it now before updating subscriptions.
        ws_thread = getattr(monitor, '_ws_thread', None)
        if all_tokens and (ws_thread is None or not ws_thread.is_alive()):
            try:
                monitor.connect(list(all_tokens))
                logger.info("[shared:%s:%s] PriceMonitor lazy-connected (%d tokens)",
                            key[0], key[1], len(all_tokens))
            except Exception:
                logger.warning("[shared:%s:%s] PriceMonitor lazy-connect failed", key[0], key[1])
        monitor.update_subscriptions(all_tokens)


def _disconnect_shared_monitors(shared_monitors: dict[tuple[str, str], object]) -> None:
    """Disconnect all shared monitors on shutdown."""
    for key, monitor in shared_monitors.items():
        try:
            monitor.disconnect()
        except Exception:
            pass


def _build_engine_groups(
    engines: list[PaperPortfolioEngine],
    configs: list[PaperConfig],
    shared_monitors: dict[tuple[str, str], object],
) -> dict[tuple[str, str], list]:
    """Build mapping of (exchange, venue) -> engines sharing that monitor.

    Only includes engines with a CandleAggregator (sub-hourly). Hourly-only
    engines have no need for WebSocket data and are excluded.
    """
    groups: dict[tuple[str, str], list] = {}
    for engine, cfg in zip(engines, configs):
        if engine._candle_aggregator is None:
            continue  # hourly-only, no benefit from WebSocket data
        venue = _market_to_venue(cfg)
        key = (cfg.exchange, venue)
        if key in shared_monitors and not cfg.dedicated_ws:
            groups.setdefault(key, []).append(engine)
    return groups


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
    parser.add_argument("--refresh", action="store_true",
                        help="Send SIGUSR1 to running process to trigger a live price refresh + dashboard push.")
    parser.add_argument("--no-live-dashboard", action="store_true",
                        help="Disable live state.json writes (1s polling dashboard)")
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

    # Create engines with shared PriceMonitors (grouped by exchange+venue)
    shared_monitors, engines = _setup_shared_monitors(configs)
    for engine, config in zip(engines, configs):
        restore_state(engine, config)
    engine_groups = _build_engine_groups(engines, configs, shared_monitors)

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

    if args.refresh:
        # Send SIGUSR1 to the running paper trader to trigger a price refresh
        lock_dir = "state/v4_paper_multi/"
        pid_file = os.path.join(lock_dir, "paper.pid")
        if not os.path.exists(pid_file):
            print("No running paper trader found (no paper.pid)")
            sys.exit(1)
        with open(pid_file) as f:
            pid = int(f.read().strip())
        try:
            os.kill(pid, signal.SIGUSR1)
            print(f"Sent SIGUSR1 to PID {pid} — price refresh + dashboard push triggered")
        except ProcessLookupError:
            print(f"PID {pid} not running — stale paper.pid")
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

    # Graceful shutdown + price refresh signal
    shutdown = threading.Event()
    refresh_requested = threading.Event()

    def shutdown_handler(signum, frame):
        shutdown.set()

    def refresh_handler(signum, frame):
        refresh_requested.set()
        logger.info("SIGUSR1 received — price refresh requested")

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGUSR1, refresh_handler)

    live_dashboard = not args.no_live_dashboard

    portfolio_names = [c.pool_name for c in configs]
    logger.info(
        "Multi-portfolio paper trading started — portfolios=%s, capital=$%.0f each",
        portfolio_names, configs[0].capital,
    )

    # Start dashboard heartbeat during startup (backfill + first fetch + first tick).
    # Engines are initialized with restored state so dashboard can show current status.
    _startup_heartbeat_stop = threading.Event()
    if live_dashboard:
        def _startup_heartbeat():
            while not _startup_heartbeat_stop.wait(1.0):
                try:
                    write_dashboard_state(
                        engines, configs, runner_status="starting",
                    )
                except Exception:
                    pass
        _startup_hb = threading.Thread(target=_startup_heartbeat, daemon=True)
        _startup_hb.start()

    # Start shared PriceMonitor WebSockets early for live dashboard prices.
    _connect_shared_monitors(shared_monitors, engine_groups)
    # Start dedicated engines' monitors (they own their own)
    for engine, config in zip(engines, configs):
        if engine._price_monitor is not None and engine._owns_price_monitor:
            dedic_tokens = set()
            for st in engine._get_all_states():
                for pos in st.position_manager.open_positions:
                    dedic_tokens.add(pos.token)
            if dedic_tokens:
                try:
                    engine._price_monitor.connect(list(dedic_tokens))
                    logger.info(
                        "[%s] Dedicated PriceMonitor started (%d tokens, %dm resolution)",
                        config.pool_name, len(dedic_tokens), engine._effective_exit_resolution,
                    )
                except Exception:
                    logger.warning("[%s] Dedicated PriceMonitor start failed", config.pool_name)

    # AC9: Startup data maintenance — backfill gaps, promote, fetch 1m
    try:
        maint_summary = ensure_data_fresh(
            exchange=exchange,
            data_dir="data",
            caller="runner_startup",
        )
        logger.info(
            "Data maintenance: %d gaps filled, %d bars fetched, %d tokens promoted",
            maint_summary.get("gaps_filled", 0),
            maint_summary.get("bars_fetched", 0),
            maint_summary.get("tokens_promoted", 0),
        )
    except Exception as e:
        logger.warning("Startup data maintenance failed: %s — continuing with existing data", e)

    if args.once:
        # Single tick — wrap in try/finally to ensure cleanup on failure
        try:
            fetch_all_data(shared_fetcher, configs)
            for engine, config in zip(engines, configs):
                result = engine.tick()
                logger.info(
                    "[%s] Tick %d — entries=%d exits=%d open=%d equity=$%.0f mtm=$%.0f",
                    config.pool_name, result.tick_counter, result.entries, result.exits,
                    result.open_positions, result.portfolio_equity, result.mark_to_market_equity,
                )
        finally:
            _startup_heartbeat_stop.set()
            if live_dashboard:
                try:
                    write_dashboard_state(engines, configs, include_sentinel=True)
                except Exception:
                    logger.exception("Live dashboard state write failed (non-fatal)")
            _disconnect_shared_monitors(shared_monitors)
            for engine in engines:
                engine.cleanup()
            lock_file.close()
        return

    # Stop startup heartbeat — main loop has its own
    _startup_heartbeat_stop.set()

    # AC10: Track last promotion time for periodic 4h promote
    last_promote_time = time.time()

    try:
        while not shutdown.is_set():
            # Step 1: Fetch data once for all portfolios.
            # Keep dashboard alive during fetch via background heartbeat —
            # engines are idle so it's safe to read their state from another thread.
            _heartbeat_stop = threading.Event()
            if live_dashboard:
                def _heartbeat():
                    while not _heartbeat_stop.wait(1.0):
                        try:
                            write_dashboard_state(
                                engines, configs, runner_status="fetching",
                            )
                        except Exception:
                            pass
                _hb = threading.Thread(target=_heartbeat, daemon=True)
                _hb.start()

            try:
                fetch_ok, fetch_err, bars_appended, _ = fetch_all_data(shared_fetcher, configs)
            except Exception:
                logger.exception("Data fetch failed")
                _heartbeat_stop.set()
                if shutdown.wait(60):
                    break
                continue
            finally:
                _heartbeat_stop.set()

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

                # Write state between ticks so dashboard stays alive
                if live_dashboard:
                    try:
                        write_dashboard_state(
                            engines, configs, runner_status="ticking",
                        )
                    except Exception:
                        pass

            # Step 3: Write live dashboard state
            if any_success and live_dashboard:
                try:
                    write_dashboard_state(engines, configs, include_sentinel=True)
                except Exception:
                    logger.exception("Live dashboard state write failed (non-fatal)")

            # AC10: Promote live→historical every 4h (bounded to 30s max)
            if time.time() - last_promote_time >= PROMOTE_INTERVAL_S:
                try:
                    promote_summary = ensure_data_fresh(
                        exchange=exchange,
                        data_dir="data",
                        caller="runner_4h",
                        promote_only=True,
                        max_duration_s=30,
                    )
                    last_promote_time = time.time()
                    logger.info(
                        "4h promotion: %d tokens promoted",
                        promote_summary.get("tokens_promoted", 0),
                    )
                except Exception as e:
                    logger.warning("4h promotion failed: %s", e)
                    last_promote_time = time.time()  # Prevent retry spam on persistent failure

            # AC11: Fetch 1m data for tokens with open positions after hourly fetch
            try:
                fetch_1m_for_open_positions(shared_fetcher, engines)
            except Exception as e:
                logger.warning("1m fetch for open positions failed: %s", e)

            # Step 4: Update PriceMonitor subscriptions for sub-hourly exits
            # Shared monitors: update with union of all engines' tokens (AC11, AC13)
            _update_shared_subscriptions(shared_monitors, engine_groups)
            # Dedicated monitors: update individually (owns_price_monitor=True)
            for engine in engines:
                if engine._price_monitor is not None and engine._owns_price_monitor:
                    engine._update_ws_subscriptions()

            # Reset consecutive error counters for sub-hourly checks
            _candle_errors: dict[int, int] = {}

            # Sleep until next hour (wake on shutdown or refresh signal)
            sleep_s = compute_sleep_until_next_hour(time.time())
            logger.info("Sleeping %.0fs until next hour", sleep_s)
            deadline = time.time() + sleep_s
            while time.time() < deadline and not shutdown.is_set():
                # Short waits for live dashboard updates + SIGUSR1 pickup
                shutdown.wait(1.0)

                if refresh_requested.is_set():
                    refresh_requested.clear()
                    logger.info("Price refresh starting...")
                    t0 = time.time()
                    for engine, config in zip(engines, configs):
                        try:
                            info = engine.update_prices(exchange)
                            logger.info(
                                "[%s] Price refresh — %d tokens updated, mtm=$%.0f (%.1fs)",
                                config.pool_name, info["tokens"],
                                info["mtm"], info.get("elapsed", 0),
                            )
                        except Exception:
                            logger.exception("[%s] Price refresh failed", config.pool_name)
                    if live_dashboard:
                        try:
                            write_dashboard_state(engines, configs, include_sentinel=True)
                        except Exception:
                            logger.exception("Live dashboard state write failed (non-fatal)")
                    logger.info("Price refresh complete (%.1fs total)", time.time() - t0)

                # Sub-hourly exit + entry check: flush completed candles and process
                for engine, config in zip(engines, configs):
                    if engine._candle_aggregator is not None:
                        try:
                            candles = engine._candle_aggregator.flush_completed()
                            if candles:
                                engine.process_sub_hourly_exits(candles)
                                engine.process_sub_hourly_entries(candles)
                                _candle_errors[id(engine)] = 0
                        except Exception:
                            err_key = id(engine)
                            _candle_errors[err_key] = _candle_errors.get(err_key, 0) + 1
                            if _candle_errors[err_key] <= 3:
                                logger.exception(
                                    "[%s] Sub-hourly check failed (%d consecutive)",
                                    config.pool_name, _candle_errors[err_key],
                                )
                            elif _candle_errors[err_key] == 4:
                                logger.error(
                                    "[%s] Suppressing repeated sub-hourly errors until next tick",
                                    config.pool_name,
                                )

                # Live dashboard: write state.json with current prices
                if live_dashboard:
                    try:
                        write_dashboard_state(engines, configs, include_sentinel=False)
                    except Exception:
                        pass  # non-fatal, no log spam at 1s interval

            if shutdown.is_set():
                break

    finally:
        # Disconnect shared PriceMonitors on shutdown (AC14)
        _disconnect_shared_monitors(shared_monitors)
        # Cleanup dedicated engines (disconnect their owned monitors)
        for engine in engines:
            engine.cleanup()
        lock_file.close()
        logger.info("Multi-portfolio paper trading stopped")


if __name__ == "__main__":
    main()
