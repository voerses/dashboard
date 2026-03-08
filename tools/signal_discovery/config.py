"""Configuration for Signal Discovery Engine."""

from dataclasses import dataclass, field
from typing import List, Optional


# Regime constants (matching v3/engine.py)
CRISIS, QUIET, UPTREND, RANGE, DOWNTREND = 0, 1, 2, 3, 4
REGIME_NAMES = {0: 'CRISIS', 1: 'QUIET', 2: 'UPTREND', 3: 'RANGE', 4: 'DOWNTREND'}

# Base indicators from compute_indicators_fast (excluding raw OHLCV)
BASE_INDICATORS = [
    'ema_10', 'ema_20', 'ema_50',
    'macd', 'macd_signal', 'macd_hist',
    'rsi',
    'bb_upper', 'bb_lower', 'bb_width', 'bb_pct',
    'atr', 'adx', 'plus_di', 'minus_di',
    'vol_ratio', 'ret_1', 'vol_20',
    'donch_high', 'donch_low',
    'taker',
]

# Domain-informed interaction pairs (economic logic, not all N^2)
# (indicator_a, indicator_b, interaction_types)
INTERACTION_PAIRS = [
    # Momentum x Volatility — do trends work in low/high vol?
    ('rsi', 'vol_20', ['product', 'ratio']),
    ('rsi', 'atr', ['product', 'ratio']),
    ('macd_hist', 'vol_20', ['product', 'ratio']),
    ('macd_hist', 'atr', ['product', 'ratio']),
    ('macd', 'vol_20', ['product']),
    ('ret_1', 'vol_20', ['ratio']),  # return per unit vol = pseudo-sharpe

    # Volume x Momentum — volume-confirmed moves
    ('vol_ratio', 'ret_1', ['product']),
    ('vol_ratio', 'rsi', ['product']),
    ('vol_ratio', 'macd_hist', ['product']),
    ('vol_ratio', 'adx', ['product']),
    ('taker', 'ret_1', ['product', 'diff']),
    ('taker', 'rsi', ['product']),
    ('taker', 'vol_ratio', ['product']),

    # Directional x Volatility — DI signals in different vol regimes
    ('plus_di', 'vol_20', ['product', 'ratio']),
    ('minus_di', 'vol_20', ['product', 'ratio']),
    ('adx', 'vol_20', ['product', 'ratio']),
    ('adx', 'atr', ['product']),
    ('plus_di', 'minus_di', ['diff', 'ratio']),

    # Range position x everything — BB%B interactions
    ('bb_pct', 'vol_ratio', ['product']),
    ('bb_pct', 'rsi', ['product', 'diff']),
    ('bb_pct', 'adx', ['product']),
    ('bb_pct', 'vol_20', ['product']),
    ('bb_pct', 'macd_hist', ['product']),
    ('bb_pct', 'ret_1', ['product']),
    ('bb_pct', 'taker', ['product']),

    # Bollinger width x directional — narrow bands + strong trend
    ('bb_width', 'adx', ['product', 'ratio']),
    ('bb_width', 'vol_ratio', ['product']),
    ('bb_width', 'rsi', ['product']),

    # Donchian position
    ('donch_high', 'donch_low', ['diff']),

    # EMA relationships
    ('ema_10', 'ema_50', ['ratio']),
    ('ema_20', 'ema_50', ['ratio']),
    ('ema_10', 'ema_20', ['ratio']),

    # ATR-normalized moves
    ('ret_1', 'atr', ['ratio']),  # return per ATR
    ('macd', 'atr', ['ratio']),
    ('macd_hist', 'bb_width', ['ratio']),
]

# Indicators for momentum/acceleration features
MOMENTUM_INDICATORS = [
    'rsi', 'adx', 'vol_20', 'vol_ratio', 'macd_hist', 'bb_pct',
    'bb_width', 'atr', 'plus_di', 'minus_di', 'taker', 'ret_1',
]

# Lookback windows for momentum/zscore features (in hours)
MOMENTUM_WINDOWS = [24, 48]

# Indicators for cross-timeframe divergence
CROSS_TF_INDICATORS = [
    'rsi', 'macd_hist', 'vol_ratio', 'adx', 'bb_pct', 'vol_20',
    'atr', 'taker', 'ret_1', 'plus_di',
]

# Top features for regime-conditional features
REGIME_COND_INDICATORS = [
    'rsi', 'vol_ratio', 'macd_hist', 'adx', 'bb_pct',
    'atr', 'vol_20', 'ret_1', 'bb_width', 'plus_di',
    'minus_di', 'taker', 'macd', 'ema_10', 'ema_20',
    'ema_50', 'donch_high', 'donch_low', 'bb_upper', 'bb_lower',
]


@dataclass
class DiscoveryConfig:
    """Configuration for a signal discovery run."""

    # Token selection
    market: str = 'perp'
    tokens: Optional[List[str]] = None  # None = all tradeable
    min_bars: int = 8760  # 1 year of 1h bars

    # Forward return horizons (hours)
    horizons: List[int] = field(default_factory=lambda: [1, 4, 24, 72, 168])

    # Walk-forward evaluation
    min_train_bars: int = 8760  # 1 year minimum training
    test_window_bars: int = 2160  # 90 days
    purge_gap_bars: int = 168  # match longest horizon

    # Statistical thresholds
    fdr_alpha: float = 0.05  # Benjamini-Hochberg FDR level
    min_abs_ic: float = 0.02  # minimum |IC| to consider
    min_t_stat: float = 2.0  # minimum t-stat for significance
    bootstrap_samples: int = 1000  # block bootstrap iterations

    # Clustering
    max_corr: float = 0.7  # dedup threshold
    top_per_regime: int = 20  # top signals per regime

    # Computation
    workers: int = 4
    quick: bool = False  # quick mode: fewer tokens, faster

    # Per-token mode
    per_token: bool = False
    primary_horizon: int = 24  # primary horizon for per-token mode

    # Analysis
    analyze: bool = True  # run temporal analysis by default

    # Output
    output_dir: str = 'outputs/signal_discovery'
