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
import queue
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
from v4.hourly_bar_collector import HourlyBarCollector
from v4.live_fetcher import LiveFetcher
from v4.price_monitor import PriceMonitor, _symbol_to_token

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
            cache_max_rows=merged.get("cache_max_rows", 22000),
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
# AC6: Write queue + writer thread for kline persistence
# ---------------------------------------------------------------------------

_1m_write_queue: queue.Queue = queue.Queue(maxsize=10_000)
_1m_writer_thread: threading.Thread | None = None
_1m_writer_started = False
_1m_writer_lock = threading.Lock()


def create_kline_writer(fetcher) -> callable:
    """Create a kline_callback that enqueues bars for background writing.

    Returns a callback suitable for PriceMonitor(kline_callback=...).
    Starts a singleton daemon writer thread that drains the queue and writes
    to parquet. Multiple calls return different callbacks but share one thread.
    """
    global _1m_writer_thread, _1m_writer_started

    with _1m_writer_lock:
        if not _1m_writer_started:
            _1m_writer_started = True

            def _writer_thread():
                while True:
                    try:
                        item = _1m_write_queue.get(timeout=5.0)
                        if item is None:
                            # Sentinel: drain remaining items then exit
                            while True:
                                try:
                                    remaining = _1m_write_queue.get_nowait()
                                except queue.Empty:
                                    break
                                if remaining is not None:
                                    t, b = remaining
                                    try:
                                        fetcher.append_to_1m_parquet(t, [b])
                                    except Exception as e:
                                        logger.warning("1m parquet write failed during drain: %s", e)
                            break
                        token, bar = item
                        fetcher.append_to_1m_parquet(token, [bar])
                    except queue.Empty:
                        continue
                    except Exception as e:
                        logger.warning("1m parquet write failed: %s", e)

            _1m_writer_thread = threading.Thread(
                target=_writer_thread, name="1m-writer", daemon=True,
            )
            _1m_writer_thread.start()

    def kline_callback(token: str, bar: dict) -> None:
        try:
            _1m_write_queue.put_nowait((token, bar))
        except queue.Full:
            logger.warning("1m write queue full, dropping bar for %s", token)

    return kline_callback


def shutdown_kline_writer(timeout: float = 10.0) -> None:
    """Signal the writer thread to drain and stop. Called on graceful shutdown."""
    global _1m_writer_started, _1m_writer_thread
    with _1m_writer_lock:
        if _1m_writer_thread is not None and _1m_writer_thread.is_alive():
            try:
                _1m_write_queue.put(None, timeout=5.0)  # sentinel
            except queue.Full:
                logger.warning("1m write queue full, could not send shutdown sentinel")
            _1m_writer_thread.join(timeout=timeout)
        _1m_writer_thread = None
        _1m_writer_started = False


# ---------------------------------------------------------------------------
# AC10/AC20/AC21: Background 1m gap recovery
# ---------------------------------------------------------------------------

_1m_backfill_thread: threading.Thread | None = None
_1m_backfill_lock = threading.Lock()


def _backfill_1m_background(exchange_cls, ccxt_config: dict, tokens: set[str], data_dir: str = "data") -> threading.Thread | None:
    """Launch 1m gap recovery in a background daemon thread (non-blocking).

    Creates a dedicated exchange instance + LiveFetcher for thread safety
    (ccxt exchange instances are not thread-safe).
    Uses gap_threshold_minutes=5 to detect gaps as small as 5 minutes.
    Skips if a previous backfill thread is still running.
    """
    global _1m_backfill_thread

    with _1m_backfill_lock:
        if _1m_backfill_thread is not None and _1m_backfill_thread.is_alive():
            logger.info("1m backfill still running from previous hour — skipping")
            return None

        def _run():
            try:
                bg_exchange = exchange_cls(ccxt_config)
                bg_fetcher = LiveFetcher(exchange=bg_exchange, data_dir=data_dir)
                results = bg_fetcher.backfill_gaps(
                    tokens, timeframe="1m", gap_threshold_minutes=5,
                )
                if results:
                    logger.info("1m backfill complete: %s", results)
            except Exception as e:
                logger.warning("Background 1m backfill failed: %s", e)

        _1m_backfill_thread = threading.Thread(target=_run, name="1m-backfill", daemon=True)
        _1m_backfill_thread.start()
        return _1m_backfill_thread


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
# WS 1H kline feed helpers (AC7, AC8, AC12, AC13, AC14, AC15, AC16)
# ---------------------------------------------------------------------------

_FUNDING_HOURS = {0, 7, 8, 15, 16, 23}


def _near_funding_interval(hour_utc: int) -> bool:
    """Return True if hour_utc is within a funding settlement window.

    Binance funding is every 8h at 00:00, 08:00, 16:00 UTC.
    We include the hour before each settlement for timely fetches.
    """
    return hour_utc in _FUNDING_HOURS


def _should_fetch_funding(hour_utc: int, first_tick: bool) -> bool:
    """Determine whether to fetch funding rates this tick.

    Always fetch on first tick (bootstrap). Otherwise only near settlements.
    """
    if first_tick:
        return True
    return _near_funding_interval(hour_utc)


def _fetch_batch_funding(session) -> dict[str, list[dict]]:
    """Fetch all perp funding rates in one premiumIndex call.

    Returns dict mapping token → [{"fundingRate": ..., "timestamp": ...}].
    Uses the premiumIndex API which returns all perp pairs at once,
    replacing 236+ sequential funding calls with a single request.
    """
    import requests
    resp = session.get("https://fapi.binance.com/fapi/v1/premiumIndex")
    resp.raise_for_status()
    data = resp.json()
    result: dict[str, list[dict]] = {}
    for item in data:
        symbol = item.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        token = _symbol_to_token(symbol)
        if not token:
            continue
        funding_rate = item.get("lastFundingRate")
        next_funding_time = item.get("nextFundingTime")
        if funding_rate is not None and next_funding_time and next_funding_time > 0:
            timestamp = int(next_funding_time - 8 * 3600 * 1000)  # 8h before next
            result[token] = [{"fundingRate": float(funding_rate), "timestamp": timestamp}]
    return result


def _fetch_missing_tokens(
    fetcher,
    missing: dict[str, set[str]],
    collector,
) -> None:
    """REST fallback: fetch OHLCV for tokens missing from WS delivery.

    Acquires collector's per-token write locks before appending
    to prevent corruption with the collector's writer thread.
    Does NOT fetch funding — that's handled by _fetch_batch_funding.
    """
    for market, tokens in missing.items():
        for token in tokens:
            try:
                bars = fetcher.fetch_ohlcv(token, market, limit=24)
                closed = fetcher.filter_closed_bars(bars)
                if closed:
                    lock = collector._1h_write_locks.setdefault(
                        token, threading.Lock(),
                    )
                    with lock:
                        fetcher.append_to_parquet(token, market, closed)
            except Exception as e:
                logger.warning("_fetch_missing_tokens %s/%s failed: %s", token, market, e)


def _process_sub_hourly_candles(
    engines: list,
    configs: list,
    candle_errors: dict[int, int],
) -> None:
    """Flush completed 1m candles and run sub-hourly exits then entries.

    AC26: Exit-before-entry ordering. Single flush per engine feeds both
    process_sub_hourly_exits and process_sub_hourly_entries.
    """
    for engine, config in zip(engines, configs):
        if engine._candle_aggregator is None:
            continue
        try:
            candles = engine._candle_aggregator.flush_completed()
            if candles:
                engine.process_sub_hourly_exits(candles)
                engine.process_sub_hourly_entries(candles)
                candle_errors[id(engine)] = 0
        except Exception:
            err_key = id(engine)
            candle_errors[err_key] = candle_errors.get(err_key, 0) + 1
            if candle_errors[err_key] <= 3:
                logger.exception(
                    "[%s] Sub-hourly candle processing failed (%d consecutive)",
                    config.pool_name, candle_errors[err_key],
                )
            elif candle_errors[err_key] == 4:
                logger.error(
                    "[%s] Suppressing repeated sub-hourly errors until next tick",
                    config.pool_name,
                )


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
    fetcher=None,
) -> tuple[dict[tuple[str, str], object], list[PaperPortfolioEngine]]:
    """Group engines by (exchange, venue) and create shared PriceMonitors.

    Engines with dedicated_ws=True create their own monitors (not shared).
    For perp venues, creates a kline_callback via create_kline_writer if
    fetcher is provided (AC13: perp monitor gets kline persistence).
    Returns (shared_monitors dict, list of engines in config order).
    """

    # Group non-dedicated configs by (exchange, venue)
    groups: dict[tuple[str, str], list[int]] = {}  # key -> config indices
    for i, cfg in enumerate(configs):
        try:
            strats = getattr(cfg, 'strategies', None)
            if not isinstance(strats, (list, tuple)):
                raise TypeError("strategies not a list")
            resolutions = [s.exit_resolution for s in strats if s.exit_resolution > 0]
            if not resolutions or cfg.dedicated_ws:
                continue
            venue = _market_to_venue(cfg)
            key = (cfg.exchange, venue)
        except (TypeError, AttributeError):
            # Simplified config (e.g., test mock with .market attribute)
            venue = "perp" if getattr(cfg, 'market', 'perp') != "spot" else "spot"
            key = (getattr(cfg, 'exchange', 'binance'), venue)
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
        # AC13: perp monitors get kline_callback for 1m persistence
        kline_cb = None
        if venue == "perp" and fetcher is not None:
            kline_cb = create_kline_writer(fetcher)
        shared_monitors[key] = PriceMonitor(
            callback=fanout, venue=venue, kline_callback=kline_cb,
        )

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
            # Include tokens with armed entry levels (snapshot under lock)
            with engine._armed_tokens_lock:
                armed_snap = dict(engine._armed_tokens)
            for (sid, token) in armed_snap:
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
            # Include tokens with armed entry levels (snapshot under lock)
            with engine._armed_tokens_lock:
                armed_snap = dict(engine._armed_tokens)
            for (sid, token) in armed_snap:
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


def _protect_from_oom() -> None:
    """No-op. Removed: oom_score_adj is ineffective inside cgroup memory limits."""
    pass


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    # Try to protect from OOM killer
    _protect_from_oom()

    # Load multi-portfolio config
    configs = load_multi_config(args.config)
    for config in configs:
        validate_paper_config(config)

    # Write per-portfolio config.json for dashboard
    write_portfolio_configs(configs)

    # Create shared fetcher
    import ccxt
    exchange_cls = getattr(ccxt, configs[0].exchange, None) or ccxt.binance
    ccxt_config: dict = {"enableRateLimit": True}
    proxy = os.environ.get("HTTPS_PROXY", os.environ.get("https_proxy", ""))
    if proxy:
        ccxt_config["proxies"] = {"https": proxy, "http": proxy}
    exchange = exchange_cls(ccxt_config)
    shared_fetcher = LiveFetcher(exchange=exchange, data_dir="data")

    # Create engines with shared PriceMonitors (grouped by exchange+venue)
    shared_monitors, engines = _setup_shared_monitors(configs, fetcher=shared_fetcher)
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
                        engines, configs, runner_status="starting", include_armed=True,
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

    # ---------------------------------------------------------------------------
    # AC7/AC14: Create HourlyBarCollector + dedicated 1H PriceMonitors
    # ---------------------------------------------------------------------------
    # Discover ALL universe tokens for data collection
    all_perp_tokens: set[str] = set()
    all_spot_tokens: set[str] = set()
    for cfg in configs:
        for spec in cfg.strategies:
            try:
                tokens = discover_tokens(spec.market)
                if spec.market == "spot":
                    all_spot_tokens.update(tokens)
                else:
                    all_perp_tokens.update(tokens)
            except Exception:
                logger.exception("discover_tokens(%s) failed", spec.market)

    collector = None
    perp_1h_monitor = None
    spot_1h_monitor = None
    ws_available = False

    if all_perp_tokens or all_spot_tokens:
        expected = {}
        if all_perp_tokens:
            expected["perp"] = all_perp_tokens
        if all_spot_tokens:
            expected["spot"] = all_spot_tokens
        collector = HourlyBarCollector(
            expected_tokens=expected,
            fetcher=shared_fetcher,
        )
        try:
            if all_perp_tokens:
                perp_1h_monitor = PriceMonitor(
                    callback=lambda *a: None,  # no-op: no real-time price from 1h
                    venue="perp",
                    streams=["kline_1h"],
                    kline_1h_callback=lambda token, bar: collector.on_bar(token, bar, "perp"),
                )
                perp_1h_monitor.connect(list(all_perp_tokens))
                logger.info("1H perp PriceMonitor started (%d tokens)", len(all_perp_tokens))
            if all_spot_tokens:
                spot_1h_monitor = PriceMonitor(
                    callback=lambda *a: None,
                    venue="spot",
                    streams=["kline_1h"],
                    kline_1h_callback=lambda token, bar: collector.on_bar(token, bar, "spot"),
                )
                spot_1h_monitor.connect(list(all_spot_tokens))
                logger.info("1H spot PriceMonitor started (%d tokens)", len(all_spot_tokens))
            ws_available = True
        except Exception:
            logger.warning("1H PriceMonitor connection failed — will use REST fallback", exc_info=True)
            ws_available = False

    logger.info("1H WS setup: perp=%d tokens, spot=%d tokens, ws_available=%s, collector=%s",
                len(all_perp_tokens), len(all_spot_tokens), ws_available, collector is not None)

    # AC9: Startup data maintenance — backfill 1h gaps, promote.
    # skip_1m=True: s501 uses live WS 1m candles, not historical 1m parquets.
    try:
        maint_summary = ensure_data_fresh(
            exchange=exchange,
            data_dir="data",
            caller="runner_startup",
            skip_1m=True,
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
                    write_dashboard_state(engines, configs, include_sentinel=True, include_armed=True)
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

    first_tick = True
    _last_settlement_ts = 0  # Track last applied funding settlement timestamp
    try:
        while not shutdown.is_set():
            # Step 1: Wait for 1H data via WS or fall back to REST
            _heartbeat_stop = threading.Event()
            if live_dashboard:
                def _heartbeat():
                    while not _heartbeat_stop.wait(1.0):
                        try:
                            write_dashboard_state(
                                engines, configs, runner_status="fetching", include_armed=True,
                            )
                        except Exception:
                            pass
                _hb = threading.Thread(target=_heartbeat, daemon=True)
                _hb.start()

            try:
                if first_tick:
                    # Startup just ran ensure_data_fresh() which backfilled all
                    # 1H gaps and promoted live→hist. Data is already current —
                    # skip the redundant REST fetch (~2 min for 236 tokens).
                    logger.info("First tick — skipping data fetch (startup backfill is fresh)")
                elif ws_available and collector is not None:
                    # AC14: Event-driven wait on WS-delivered 1H bars
                    logger.info("WS path: waiting for collector ready (timeout=120s)...")
                    got_data = collector.wait_for_ready(timeout=120)
                    logger.info("WS collector ready=%s", got_data)
                    if got_data:
                        # AC7a: Drain write queue before reading parquets
                        collector._write_queue.join()
                        received = collector.consume()
                        total_ws = sum(len(v) for v in received.values())
                        logger.info("WS received: %d tokens across %d markets (%s)",
                                    total_ws, len(received),
                                    {m: len(t) for m, t in received.items()})
                        # Compute missing from consumed data (not get_missing_tokens()
                        # which sees the NEXT hour's empty _received after replay)
                        missing = {}
                        for mkt, expected_set in collector._expected.items():
                            got = received.get(mkt, set())
                            diff = expected_set - got
                            if diff:
                                missing[mkt] = diff
                        if missing and any(missing.values()):
                            n_missing = sum(len(v) for v in missing.values())
                            logger.info(
                                "REST fallback for %d missing tokens: %s",
                                n_missing,
                                {m: sorted(list(t))[:10] for m, t in missing.items()},
                            )
                            _fetch_missing_tokens(shared_fetcher, missing, collector)
                        else:
                            logger.info("WS complete — no REST fallback needed")
                        # Check a sample token was written to live/
                        import glob as _glob
                        sample_files = _glob.glob("data/perp/live/*.parquet")
                        if sample_files:
                            import os as _os
                            newest = max(sample_files, key=_os.path.getmtime)
                            age_s = time.time() - _os.path.getmtime(newest)
                            logger.info("Live data check: %d files in data/perp/live/, newest=%s (%.0fs ago)",
                                        len(sample_files), _os.path.basename(newest), age_s)
                    else:
                        # AC10 timeout: full REST fallback
                        logger.warning("Collector timeout — full REST fallback")
                        fetch_all_data(shared_fetcher, configs)
                else:
                    # AC20: WS unavailable — timer-based tick with full REST fetch
                    logger.warning("NO WS path: ws_available=%s, collector=%s — doing full REST fetch",
                                   ws_available, collector is not None)
                    fetch_ok, fetch_err, bars_appended, _ = fetch_all_data(shared_fetcher, configs)
                    logger.info("REST fetch done: ok=%d err=%d bars=%d", fetch_ok, fetch_err, bars_appended)
                    # Stale-data guard
                    if bars_appended == 0 and fetch_ok == 0 and fetch_err > 0:
                        logger.warning(
                            "All %d fetches failed — network down? Skipping tick, "
                            "retrying in 5 minutes", fetch_err,
                        )
                        if shutdown.wait(300):
                            break
                        continue
            except Exception:
                logger.exception("Data fetch failed")
                _heartbeat_stop.set()
                if shutdown.wait(60):
                    break
                continue
            finally:
                _heartbeat_stop.set()

            # AC12/AC12a/AC13: Batch funding via premiumIndex
            try:
                hour_utc = time.gmtime().tm_hour
                if _should_fetch_funding(hour_utc, first_tick):
                    import requests
                    session = requests.Session()
                    try:
                        batch_funding = _fetch_batch_funding(session)
                    finally:
                        session.close()
                    for token, rates in batch_funding.items():
                        if hasattr(shared_fetcher, 'merge_funding_into_parquet'):
                            shared_fetcher.merge_funding_into_parquet(token, rates)
                    logger.info("Batch funding: %d tokens updated", len(batch_funding))
                    # Apply funding settlement — deduplicate by settlement timestamp
                    # to avoid double-applying when _should_fetch_funding fires
                    # at both pre-settlement and post-settlement hours.
                    settle_ts = 0
                    for rates in batch_funding.values():
                        if rates:
                            settle_ts = int(rates[0].get("timestamp", 0))
                            break
                    if settle_ts and settle_ts != _last_settlement_ts:
                        _last_settlement_ts = settle_ts
                        for engine in engines:
                            try:
                                n = engine.apply_funding_settlement(batch_funding)
                                if n:
                                    logger.info("[%s] Funding settlement @%d: %d positions",
                                                engine.config.pool_name, settle_ts, n)
                            except Exception:
                                logger.exception("[%s] Funding settlement failed",
                                                 engine.config.pool_name)
            except Exception as e:
                logger.warning("Batch funding fetch failed: %s — continuing without fresh funding", e)

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
                    write_dashboard_state(engines, configs, include_sentinel=True, include_armed=True)
                except Exception:
                    logger.exception("Live dashboard state write failed (non-fatal)")

            # Memory management: clear per-tick caches to prevent unbounded growth.
            # Engine context caches are cleared in signals.py after each precompute call.
            # Module-level positioning/dvol caches accumulate across ticks — clear them.
            try:
                from v4.engine import clear_module_caches
                clear_module_caches()
                import gc
                gc.collect()
            except Exception:
                logger.warning("Post-tick cache clearing failed", exc_info=True)

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
                    tokens_promoted = promote_summary.get("tokens_promoted", 0)
                    logger.info(
                        "4h promotion: %d tokens promoted",
                        tokens_promoted,
                    )
                    # Invalidate hist_cache after promotion: promoted bars
                    # moved from live buffer to historical parquet on disk,
                    # so cached historical DataFrames are now stale.
                    if tokens_promoted > 0:
                        for engine in engines:
                            engine._hist_cache.clear()
                        logger.debug("Cleared hist_cache on %d engines after promotion", len(engines))
                except Exception as e:
                    logger.warning("4h promotion failed: %s", e)
                    last_promote_time = time.time()  # Prevent retry spam on persistent failure

            # AC11: Backfill 1m data — DISABLED for paper trading.
            # s501's entry_resolution=1 uses live WS 1m candles, not historical
            # 1m parquets.  The 1m backfill is only needed for backtesting.
            # Skipping saves ~5-8 min per tick and reduces memory pressure.
            # To re-enable: uncomment the block below.
            # try:
            #     from v4.data_maintenance import discover_perp_1m_tokens
            #     all_1m_tokens = set(discover_perp_1m_tokens("data"))
            #     if all_1m_tokens:
            #         _backfill_1m_background(exchange_cls, ccxt_config, all_1m_tokens)
            # except Exception as e:
            #     logger.warning("1m background backfill launch failed: %s", e)

            # Step 4: Update PriceMonitor subscriptions for sub-hourly exits
            # Shared monitors: update with union of all engines' tokens (AC11, AC13)
            _update_shared_subscriptions(shared_monitors, engine_groups)
            # Dedicated monitors: update individually (owns_price_monitor=True)
            for engine in engines:
                if engine._price_monitor is not None and engine._owns_price_monitor:
                    engine._update_ws_subscriptions()

            first_tick = False

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
                            write_dashboard_state(engines, configs, include_sentinel=True, include_armed=True)
                        except Exception:
                            logger.exception("Live dashboard state write failed (non-fatal)")
                    logger.info("Price refresh complete (%.1fs total)", time.time() - t0)

                # Sub-hourly: flush_completed from _candle_aggregator → process_sub_hourly_exits → entries (AC26)
                _process_sub_hourly_candles(engines, configs, _candle_errors)

                # Live dashboard: write state.json with current prices
                if live_dashboard:
                    try:
                        write_dashboard_state(engines, configs, include_sentinel=False, include_armed=True)
                    except Exception:
                        pass  # non-fatal, no log spam at 1s interval

            if shutdown.is_set():
                break

    finally:
        # AC11a: Shutdown HourlyBarCollector (drain writer thread)
        if collector is not None:
            try:
                collector.shutdown()
            except Exception:
                logger.warning("Collector shutdown failed", exc_info=True)
        # Disconnect 1H data-collection monitors
        for mon in (perp_1h_monitor, spot_1h_monitor):
            if mon is not None:
                try:
                    mon.disconnect()
                except Exception:
                    pass
        # Drain 1m write queue before disconnecting monitors
        shutdown_kline_writer(timeout=10.0)
        # Wait for background 1m backfill to finish current write
        if _1m_backfill_thread is not None and _1m_backfill_thread.is_alive():
            logger.info("Waiting for 1m backfill thread to finish...")
            _1m_backfill_thread.join(timeout=5.0)
        # Disconnect shared PriceMonitors on shutdown (AC14)
        _disconnect_shared_monitors(shared_monitors)
        # Cleanup dedicated engines (disconnect their owned monitors)
        for engine in engines:
            engine.cleanup()
        lock_file.close()
        logger.info("Multi-portfolio paper trading stopped")


if __name__ == "__main__":
    main()
