"""Configuration for Signal Portfolio system."""

from dataclasses import dataclass, field
from typing import List, Optional


# Regime constants (matching v3/engine.py and signal_discovery)
CRISIS, QUIET, UPTREND, RANGE, DOWNTREND = 0, 1, 2, 3, 4
REGIME_NAMES = {0: 'CRISIS', 1: 'QUIET', 2: 'UPTREND', 3: 'RANGE', 4: 'DOWNTREND'}

# Signal horizon → trade parameter defaults
HORIZON_DEFAULTS = {
    4:   {'min_hold': 4,  'max_hold': 48,  'stop_mult': 2.5, 'trail_mult': 2.0},
    24:  {'min_hold': 18, 'max_hold': 168, 'stop_mult': 3.0, 'trail_mult': 2.5},
    72:  {'min_hold': 48, 'max_hold': 360, 'stop_mult': 3.0, 'trail_mult': 2.5},
    168: {'min_hold': 72, 'max_hold': 720, 'stop_mult': 3.5, 'trail_mult': 3.0},
}


@dataclass
class SignalPortfolioConfig:
    """Configuration for signal portfolio pipeline."""

    # Data paths
    signal_dir: str = 'outputs/signal_discovery'
    data_dir: str = 'data'
    output_dir: str = 'outputs/signal_portfolio'

    # Market settings
    market: str = 'perp'
    exchange: str = 'binance'
    capital: float = 200_000

    # Clustering
    min_clusters: int = 3
    max_clusters: int = 6

    # Signal filtering
    min_abs_ic: float = 0.02
    max_signal_corr: float = 0.7  # decorrelation guard threshold
    max_signals_per_group: int = 5

    # Optimization
    max_drawdown_limit: float = -0.30  # hard constraint
    train_pct: float = 0.60
    val_pct: float = 0.20
    # test_pct = 1 - train_pct - val_pct = 0.20

    # Portfolio
    max_token_pct: float = 0.15
    min_position_usd: float = 1_000
    allocation_method: str = 'inverse_vol'  # equal, ic_weighted, inverse_vol, max_return

    # Computation
    workers: int = 4

    # Optimizer grid
    entry_thresholds: List[float] = field(default_factory=lambda: [1.0, 1.5, 2.0, 2.5])
    min_vol_ratios: List[float] = field(default_factory=lambda: [0.5, 1.0, 1.5])
    stop_mults: List[float] = field(default_factory=lambda: [2.0, 2.5, 3.0])
    trail_mults: List[float] = field(default_factory=lambda: [1.5, 2.0, 2.5, 3.0])
    min_holds: List[int] = field(default_factory=lambda: [4, 12, 24])
