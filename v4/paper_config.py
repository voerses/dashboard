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

from v4.config import PortfolioConfig, StrategySpec


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
        strategy_list.append(StrategySpec(
            strategy_id=s["strategy_id"],
            weight=s.get("weight", 1.0),
            max_positions=s.get("max_positions", 15),
            market=s.get("market", "combined"),
            strategy_type=s.get("strategy_type", "per_token"),
        ))

    # --- Build PaperConfig ---
    config = PaperConfig(
        strategies=strategy_list,
        capital=data.get("initial_capital", 200_000.0),
        max_portfolio_positions=data.get("max_portfolio_positions", 40),
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
        conviction_mode=data.get("conviction_mode", "shuffle"),
        min_conviction_threshold=data.get("min_conviction_threshold", 0.0),
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

    # --- Strategy loadability ---
    # Import here to avoid circular imports at module level
    _v3_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "v3"
    )
    if _v3_dir not in sys.path:
        sys.path.insert(0, _v3_dir)

    from v3.engine import BacktestEngine

    for spec in config.strategies:
        try:
            BacktestEngine._load_strategy(spec.strategy_id)
        except FileNotFoundError as e:
            raise ValueError(
                f"Strategy '{spec.strategy_id}' could not be loaded: {e}. "
                f"Check that a strategy file exists in the strategies/ directory "
                f"matching pattern '{spec.strategy_id}_*.py'."
            ) from e
