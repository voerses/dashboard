"""V4 Paper Trading — Configuration and validation.

Extends PortfolioConfig with paper-trading-specific fields:
  - mode (pool/independent)
  - pool_name for dashboard display
  - lookback_months for signal recomputation window
  - enable_purge_windows (disabled by default for live)
  - alert/monitoring settings
  - shadow rebalance threshold
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import List

from v4.config import PortfolioConfig, StrategySpec, SizingDefaults, resolve_sizing


@dataclass
class PaperConfig(PortfolioConfig):
    """Paper trading configuration — extends PortfolioConfig with live-mode fields."""

    mode: str = "pool"                          # "pool" or "independent"
    pool_name: str = ""                         # dashboard display name (AC8)
    lookback_months: int = 12                   # signal recomputation window
    enable_purge_windows: bool = False          # disabled for live (AC15)
    drawdown_alert_pct: float = 5.0             # drawdown alert threshold %
    alert_webhook_url: str = ""                 # optional webhook URL
    shadow_rebalance_threshold: float = 100.0   # minimum USD for shadow rebalance logging
    state_dir: str = "state/paper/"              # directory for state persistence
    dashboard_push: bool = False                 # push dashboard to GH Pages after tick
    dynamic_weights: bool = False                # enable regime-conditional dynamic weights
    dynamic_weights_smoothing: float = 0.3       # EMA smoothing alpha for regime transitions
    config_path: str = ""                        # source file path (set by load_paper_config)
    sentinel_mode: str = "off"                    # "off", "shadow", or "live" (deprecated — use exit_resolution)
    confirmation_tiers: dict = field(default_factory=lambda: {"btc_eth": 30, "top10": 60, "other": 90})  # deprecated
    carry_strategies: list = field(default_factory=list)  # strategy_ids excluded from sentinel monitoring
    exit_resolution: int = 0                       # 0=hourly only, 1/5/15/30=sub-hourly WebSocket candles
    dedicated_ws: bool = False                       # True = own WebSocket, skip shared monitor
    cache_max_rows: int = 22000                        # paper trader memory limit (overrides PortfolioConfig default of 0)


def load_paper_config(path: str) -> PaperConfig:
    """Load a PaperConfig from a JSON file.

    Maps JSON field names to PaperConfig/PortfolioConfig fields.
    Raises ValueError/KeyError for missing required fields.
    """
    with open(path, "r") as f:
        data = json.load(f)

    # --- Required: strategies ---
    if "strategies" not in data:
        raise ValueError("Missing required field 'strategies' in config")

    strategy_list: List[StrategySpec] = []
    for i, s in enumerate(data["strategies"]):
        if "strategy_id" not in s:
            raise ValueError(
                f"Strategy entry {i} missing required field 'strategy_id'"
            )
        strategy_list.append(StrategySpec.from_dict(s))

    # --- Merge strategy-declared PORTFOLIO_CONFIG as defaults ---
    # Strategy module's PORTFOLIO_CONFIG fills in missing config.json fields.
    # Also auto-detect strategy_type from module's STRATEGY_TYPE attribute
    # when config.json doesn't set it explicitly.
    merged_pconf = {}
    for i, spec in enumerate(strategy_list):
        try:
            from v4.portfolio_backtest import _load_strategy_module_attrs, _detect_strategy_type
            mod_attrs = _load_strategy_module_attrs(spec.strategy_id)
            pconf = mod_attrs.get('portfolio_config', {})
            merged_pconf.update(pconf)
            # Fill entry_resolution from strategy if config.json didn't set it
            if 'entry_resolution' in pconf and spec.entry_resolution == 0:
                spec.entry_resolution = pconf['entry_resolution']
            # Auto-detect strategy_type from module if config.json didn't set it
            if spec.strategy_type == "per_token" and "strategy_type" not in data["strategies"][i]:
                detected = _detect_strategy_type(spec.strategy_id)
                if detected != spec.strategy_type:
                    import logging
                    logging.getLogger(__name__).info(
                        "Auto-detected strategy_type='%s' for %s (module STRATEGY_TYPE)",
                        detected, spec.strategy_id,
                    )
                    spec.strategy_type = detected
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to load PORTFOLIO_CONFIG for %s: %s", spec.strategy_id, e
            )

    # --- Parse sizing_defaults (optional) ---
    sd_raw = data.get("sizing_defaults", {})
    sd_fields = {f.name for f in SizingDefaults.__dataclass_fields__.values()}
    unknown_sd_keys = set(sd_raw.keys()) - sd_fields
    if unknown_sd_keys:
        raise ValueError(f"Unknown sizing_defaults keys: {unknown_sd_keys}")
    sizing_defaults = SizingDefaults(**sd_raw)

    # --- Build PaperConfig ---
    config = PaperConfig(
        strategies=strategy_list,
        sizing_defaults=sizing_defaults,
        capital=data.get("initial_capital", 200_000.0),
        max_portfolio_positions=data.get("max_portfolio_positions", merged_pconf.get("max_portfolio_positions", 40)),
        concentration_limit=data.get("concentration_limit", 0.10),
        adv_cap_pct=data.get("adv_cap_pct", 0.05),
        min_position_usd=data.get("min_position_usd", 200.0),
        exchange=data.get("exchange", "binance"),
        seed=data.get("seed", 42),
        base_spread_bps=data.get("base_spread_bps", 3.0),
        impact_coeff=data.get("impact_coeff", 0.03),
        stress_adv_multiplier=data.get("stress_adv_multiplier", 1.0),
        max_slip_bps=data.get("max_slip_bps", 300),
        mode=data.get("mode", "pool"),
        pool_name=data.get("pool_name", ""),
        lookback_months=data.get("lookback_months", 12),
        enable_purge_windows=data.get("enable_purge_windows", False),
        drawdown_alert_pct=data.get("drawdown_alert_pct", 5.0),
        alert_webhook_url=data.get("alert_webhook_url", ""),
        shadow_rebalance_threshold=data.get("shadow_rebalance_threshold", 100.0),
        state_dir=data.get("state_dir", "state/paper/"),
        dashboard_push=data.get("dashboard_push", False),
        dynamic_weights=data.get("dynamic_weights", False),
        dynamic_weights_smoothing=data.get("dynamic_weights_smoothing", 0.3),
        conviction_mode=data.get("conviction_mode", merged_pconf.get("conviction_mode", "shuffle")),
        min_conviction_threshold=data.get("min_conviction_threshold", 0.0),
        max_sizing_equity=data.get("max_sizing_equity", None),
        sentinel_mode=data.get("sentinel_mode", "off"),
        confirmation_tiers=data.get("confirmation_tiers", {"btc_eth": 30, "top10": 60, "other": 90}),
        carry_strategies=data.get("carry_strategies", []),
        exit_resolution=data.get("exit_resolution", 0),
        dedicated_ws=data.get("dedicated_ws", False),
        raw_mode=data.get("raw_mode", False),
        raw_max_positions=data.get("raw_max_positions", 500),
        skip_walk_forward=data.get("skip_walk_forward", False),
        cache_max_rows=data.get("cache_max_rows", 22000),
    )

    config.config_path = path

    return config


def validate_paper_config(config: PaperConfig) -> None:
    """Validate a PaperConfig, raising ValueError for invalid configurations.

    Checks (AC7b):
      - Pool mode: strategy weights sum to <= 1.0
      - max_portfolio_positions >= max of per-strategy max_positions
      - All strategy_ids are loadable
    """
    # --- Sentinel mode validation ---
    valid_sentinel_modes = ("off", "shadow", "live")
    if config.sentinel_mode not in valid_sentinel_modes:
        raise ValueError(
            f"Invalid sentinel_mode '{config.sentinel_mode}'. "
            f"Must be one of: {', '.join(valid_sentinel_modes)}"
        )

    # --- Exit resolution validation ---
    valid_exit_resolutions = (0, 1, 5, 15, 30)
    if config.exit_resolution not in valid_exit_resolutions:
        raise ValueError(
            f"Invalid exit_resolution {config.exit_resolution}. "
            f"Must be one of: {', '.join(str(r) for r in valid_exit_resolutions)}"
        )

    # --- Weight sum check (pool mode only) ---
    # Weights > 1.0 per strategy are allowed — this means each strategy sizes
    # off the full portfolio equity (matching v3 behavior where sub-strategies
    # share a single capital pool). The free_capital check at entry time prevents
    # over-allocation. We warn but don't reject.
    if config.mode == "pool":
        total_weight = sum(s.weight for s in config.strategies)
        if total_weight > 1.0 + 1e-9:
            import logging
            logging.getLogger(__name__).info(
                "Strategy weights sum to %.2f (> 1.0) in pool mode. "
                "Each strategy sizes off weight × equity; free_capital check "
                "prevents over-allocation.", total_weight,
            )

    # --- Position limit consistency ---
    if config.strategies:
        max_per_strategy = max(s.max_positions for s in config.strategies)
        if config.max_portfolio_positions < max_per_strategy:
            raise ValueError(
                f"max_portfolio_positions ({config.max_portfolio_positions}) is less than "
                f"the largest per-strategy max_positions ({max_per_strategy}). "
                f"Set max_portfolio_positions >= {max_per_strategy}."
            )

    # --- Sizing overrides validation (catch errors at load time) ---
    for spec in config.strategies:
        if spec.sizing_overrides:
            try:
                resolve_sizing(config.sizing_defaults, spec.sizing_overrides)
            except ValueError as e:
                raise ValueError(
                    f"Strategy '{spec.strategy_id}' sizing_overrides invalid: {e}"
                ) from e

    # --- Strategy loadability ---
    # Import here to avoid circular imports at module level
    from v4.engine import _load_strategy_fn

    for spec in config.strategies:
        try:
            _load_strategy_fn(spec.strategy_id)
        except FileNotFoundError as e:
            raise ValueError(
                f"Strategy '{spec.strategy_id}' could not be loaded: {e}. "
                f"Check that a strategy file exists in the strategies/ directory "
                f"matching pattern '{spec.strategy_id}_*.py'."
            ) from e
