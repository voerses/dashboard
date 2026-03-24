"""
V3 Momentum Prototype — 20/50 EMA + Positioning + VRP Overlays (BTC Only)
============================================================================

Implements the validated V3 momentum strategy as a v4-compatible strategy file.

Strategy definition:
  Base: Long when 20d EMA > 50d EMA, flat otherwise. NO stop losses.
  Positioning overlay: Binance Top Trader L/S + L/S Divergence combined
    z-score (30d rolling) -> sizing multiplier
    (z>1.5->0.3x, z>0.5->0.5x, neutral->1.0x, z<-0.5->1.3x, z<-1.5->1.5x)
  VRP overlay: (IV - RV) z-score over 60d -> sizing multiplier
    (z>1->1.3x, z>-0.5->1.0x, z>-1.5->0.5x, z<-1.5->0.3x)
  Position range: [0, 1.5x]. Weekly rebalance. BTC only. NO hard stop losses.

Validation results (OOS):
  Return +17.52%, Sharpe 0.56, MaxDD -20.2%
  Walk-forward: 5/6 positive, parameter sensitivity: 0 KILL flags

Data dependencies (loaded at strategy call time, cached):
  - Positioning: data/alternative/binance_metrics/all_symbols_daily_ls.parquet
  - DVOL: data/alternative/deribit_options/dvol/btc_dvol_daily.json

NOTE: This is a RESEARCH PROTOTYPE in research/.
Production deployment to strategies/s320_v3_momentum_overlays.py requires:
  1. Moving external data loading into the engine's enriched pipeline
  2. Full Gate 4/5 validation via v4/validation.py
  3. Performance profiling (<1ms/call target)

Status: RESEARCH PROTOTYPE
"""

import os
import json
import numpy as np
import pandas as pd
from pathlib import Path
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND,
                    rolling_mean, rolling_std, rolling_zscore)

# =============================================================================
# Configuration
# =============================================================================

# EMA crossover parameters (daily bars)
FAST_EMA = 20
SLOW_EMA = 50

# Positioning overlay parameters
POS_Z_WINDOW = 30       # Rolling z-score window (days)
POS_HIGH_THRESH = 1.5   # Extreme crowding threshold
POS_MID_THRESH = 0.5    # Moderate crowding threshold

# VRP overlay parameters
VRP_Z_WINDOW = 60       # Rolling z-score window (days)
VRP_HIGH_THRESH = 1.0   # Vol overpriced threshold
VRP_MID_LOW = -0.5      # Vol cheap threshold
VRP_EXTREME_LOW = -1.5  # Extreme turbulence threshold

# Trade management
REBALANCE_BARS = 168    # Weekly rebalance (7 * 24h)
WARMUP_DAILY = 90       # Days of burn-in for EMAs and z-scores
WARMUP_BARS = WARMUP_DAILY * 24

# Position limits
MIN_POSITION = 0.0
MAX_POSITION = 1.5

# Data paths (relative to project root)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_POS_PATH = _PROJECT_ROOT / 'data' / 'alternative' / 'binance_metrics' / 'all_symbols_daily_ls.parquet'
_DVOL_PATH = _PROJECT_ROOT / 'data' / 'alternative' / 'deribit_options' / 'dvol' / 'btc_dvol_daily.json'

# =============================================================================
# Data Loading (cached at module level after first call)
# =============================================================================

_cached_positioning = None
_cached_dvol = None


def _load_positioning():
    """Load Binance positioning data for BTCUSDT. Cached after first call."""
    global _cached_positioning
    if _cached_positioning is not None:
        return _cached_positioning

    if not _POS_PATH.exists():
        _cached_positioning = pd.DataFrame()
        return _cached_positioning

    pos = pd.read_parquet(_POS_PATH)
    pos = pos[pos['symbol'] == 'BTCUSDT'].copy()
    pos['date'] = pd.to_datetime(pos['date'])
    pos = pos.set_index('date').sort_index()
    pos = pos[['sum_toptrader_ls_ratio', 'count_toptrader_ls_ratio', 'count_ls_ratio']].copy()
    pos = pos[~pos.index.duplicated(keep='last')]
    _cached_positioning = pos
    return _cached_positioning


def _load_dvol():
    """Load BTC DVOL from Deribit JSON. Cached after first call."""
    global _cached_dvol
    if _cached_dvol is not None:
        return _cached_dvol

    if not _DVOL_PATH.exists():
        _cached_dvol = pd.Series(dtype=float)
        return _cached_dvol

    with open(_DVOL_PATH) as f:
        data = json.load(f)

    records = []
    for row in data:
        ts = pd.Timestamp(row[0], unit='ms')
        records.append({'date': ts, 'dvol_close': row[4]})

    dvol = pd.DataFrame(records).set_index('date').sort_index()
    dvol = dvol[~dvol.index.duplicated(keep='last')]
    _cached_dvol = dvol['dvol_close']
    return _cached_dvol


# =============================================================================
# Signal Construction (vectorized on daily bars)
# =============================================================================

def _build_ema_base_signal(daily_close):
    """
    Base trend signal: long when fast EMA > slow EMA, flat otherwise.
    Returns numpy array of 0.0 or 1.0 aligned to daily bars.
    """
    n = len(daily_close)
    fast_ema = pd.Series(daily_close).ewm(span=FAST_EMA, adjust=False).mean().values
    slow_ema = pd.Series(daily_close).ewm(span=SLOW_EMA, adjust=False).mean().values

    base = np.where(fast_ema > slow_ema, 1.0, 0.0)
    # Warmup guard: no signal during EMA burn-in
    base[:SLOW_EMA] = 0.0
    return base


def _build_positioning_multiplier(daily_idx):
    """
    Positioning overlay: combined z-score of Top Trader L/S + L/S Divergence.
    Returns numpy array of multipliers aligned to daily_idx.

    High crowding (z > 1.5) -> 0.3x (reduce exposure)
    Moderate crowding (z > 0.5) -> 0.5x
    Neutral (-0.5 < z < 0.5) -> 1.0x
    Moderate contrarian (z < -0.5) -> 1.3x (increase exposure)
    Extreme contrarian (z < -1.5) -> 1.5x (max overlay)
    """
    positioning = _load_positioning()
    n = len(daily_idx)

    if positioning.empty:
        return np.ones(n, dtype=np.float64)

    # Align positioning to daily index
    pos = positioning.reindex(daily_idx).ffill()

    # Z-scores of each component
    toptrader_ls = pos['sum_toptrader_ls_ratio'].values.astype(np.float64)
    count_toptrader = pos['count_toptrader_ls_ratio'].values.astype(np.float64)
    count_ls = pos['count_ls_ratio'].values.astype(np.float64)

    # Divergence: top trader positioning vs retail
    divergence = count_toptrader - count_ls

    # Rolling z-scores (30d window)
    z_toptrader = rolling_zscore(toptrader_ls, POS_Z_WINDOW)
    z_divergence = rolling_zscore(divergence, POS_Z_WINDOW)

    # Combined z-score (equal weight)
    combined_z = (z_toptrader + z_divergence) / 2.0

    # Map z-score to multiplier (vectorized)
    multiplier = np.where(
        combined_z > POS_HIGH_THRESH, 0.3,
        np.where(combined_z > POS_MID_THRESH, 0.5,
                 np.where(combined_z > -POS_MID_THRESH, 1.0,
                          np.where(combined_z > -POS_HIGH_THRESH, 1.3,
                                   1.5))))

    # NaN -> neutral
    multiplier = np.where(np.isnan(combined_z), 1.0, multiplier)
    return multiplier


def _build_vrp_multiplier(daily_close, daily_idx):
    """
    VRP (Volatility Risk Premium) overlay: (IV - RV) z-score -> sizing multiplier.

    High VRP z > 1.0 -> 1.3x (vol overpriced, market complacent, size up)
    Normal -0.5 < z < 1.0 -> 1.0x
    Low z < -0.5 -> 0.5x (vol cheap, turbulence expected, reduce)
    Very low z < -1.5 -> 0.3x (extreme stress, minimum size)
    """
    n = len(daily_close)
    dvol = _load_dvol()

    # Realized vol: 20d rolling std of daily log returns, annualized
    log_ret = np.zeros(n, dtype=np.float64)
    log_ret[1:] = np.log(daily_close[1:] / np.maximum(daily_close[:-1], 1e-10))
    rv_20d = rolling_std(log_ret, 20) * np.sqrt(365) * 100

    # Implied vol: Deribit DVOL if available, else RV proxy
    if dvol.empty or len(dvol) < 30:
        # Proxy: 90d RV * 1.2 (typical IV/RV ratio)
        rv_90d = rolling_std(log_ret, 90) * np.sqrt(365) * 100
        iv = rv_90d * 1.2
    else:
        iv_series = dvol.reindex(daily_idx).ffill()
        iv = iv_series.values.astype(np.float64)

    # VRP = IV - RV (positive = vol overpriced)
    vrp = iv - rv_20d

    # 60d rolling z-score of VRP
    vrp_z = rolling_zscore(vrp, VRP_Z_WINDOW)

    # Map z-score to multiplier (vectorized)
    multiplier = np.where(
        vrp_z > VRP_HIGH_THRESH, 1.3,
        np.where(vrp_z > VRP_MID_LOW, 1.0,
                 np.where(vrp_z > VRP_EXTREME_LOW, 0.5,
                          0.3)))

    # NaN -> neutral
    multiplier = np.where(np.isnan(vrp_z), 1.0, multiplier)
    return multiplier


# =============================================================================
# Main Strategy Function
# =============================================================================

def strategy(ctx: StrategyContext) -> StrategyResult:
    """
    V3 Momentum: 20/50 EMA base + Positioning overlay + VRP overlay.
    BTC only, spot market, weekly rebalance, no stop losses.

    Signal stack:
      Layer 1: Regime filter -- exclude CRISIS
      Layer 2: EMA crossover base (long when 20d EMA > 50d EMA)
      Layer 3: Positioning overlay (sizing multiplier 0.3x-1.5x)
      Layer 4: VRP overlay (sizing multiplier 0.3x-1.3x)
      Compose: base * pos_mult * vrp_mult, clipped to [0, 1.5]
    """
    n = len(ctx.ind_1h['close'])

    # ── BTC-ONLY FILTER ──────────────────────────────────────────────
    if ctx.ticker != 'BTC':
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.ones(n, dtype=np.int8),
            name='v3_momentum_overlays',
        )

    # ── DAILY BAR SIGNALS ────────────────────────────────────────────
    daily_close = ctx.ind_d['close']
    n_daily = len(daily_close)
    daily_idx = ctx.idx_d

    if n_daily < WARMUP_DAILY:
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.ones(n, dtype=np.int8),
            name='v3_momentum_overlays',
        )

    # Layer 2: EMA base signal (daily)
    base_signal = _build_ema_base_signal(daily_close)

    # Layer 3: Positioning overlay (daily)
    pos_multiplier = _build_positioning_multiplier(daily_idx)

    # Layer 4: VRP overlay (daily)
    vrp_multiplier = _build_vrp_multiplier(daily_close, daily_idx)

    # ── COMPOSE FINAL POSITION (daily) ───────────────────────────────
    # Final position = base * positioning * vrp, clipped to [0, 1.5]
    final_daily = np.clip(base_signal * pos_multiplier * vrp_multiplier,
                          MIN_POSITION, MAX_POSITION)

    # ── ALIGN TO HOURLY BARS ─────────────────────────────────────────
    # Forward-fill daily position to 1H bars
    final_hourly = ctx.align_daily_to_1h(final_daily)

    # ── LAYER 1: REGIME FILTER ───────────────────────────────────────
    regime_ok = ctx.regime_1h != CRISIS

    # Apply regime filter: zero position during crisis
    final_hourly = np.where(regime_ok, final_hourly, 0.0)

    # ── ENTRY MASK ───────────────────────────────────────────────────
    # Entry whenever final position is > 0 (the simulator handles sizing)
    # We implement weekly rebalance by only marking entries at rebalance points
    entry_mask = np.zeros(n, dtype=bool)

    # Mark entry at weekly intervals when position is non-zero
    for rb in range(WARMUP_BARS, n, REBALANCE_BARS):
        if final_hourly[rb] > 0:
            end = min(rb + REBALANCE_BARS, n)
            entry_mask[rb:end] = True

    # Warmup guard
    entry_mask[:WARMUP_BARS] = False

    # Liquidity filter
    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    # ── DIRECTION ────────────────────────────────────────────────────
    # Always long (V3 is long-only based on EMA crossover)
    direction = np.ones(n, dtype=np.int8)

    # ── SIZE MULTIPLIER ──────────────────────────────────────────────
    # Use the final composite position as the sizing signal
    # This ranges from 0.0 to 1.5, giving the simulator the overlay info
    size_mult = final_hourly.copy()
    # Ensure minimum sizing when entry is active
    size_mult = np.where(entry_mask & (size_mult < 0.1), 0.1, size_mult)

    # ── CONVICTION SCORE ─────────────────────────────────────────────
    # Normalize size_mult to [0, 1] for entry prioritization
    conviction = np.clip(size_mult / MAX_POSITION, 0.0, 1.0)

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,

        # Trade management — NO HARD STOPS (core V3 design)
        stop_mult=99.0,          # Effectively disabled
        trail_mult=99.0,         # Effectively disabled
        target_mult=999.0,       # No fixed target
        no_stop_bars=REBALANCE_BARS,  # Protect full holding period
        min_hold=24,             # Minimum 1 day
        max_hold=REBALANCE_BARS, # 7 days (weekly rebalance)
        edge=0.35,               # Conservative Kelly edge

        exit_regimes={CRISIS},   # Force exit on crisis only
        name='v3_momentum_overlays',

        # Sizing
        size_multiplier=size_mult,
        conviction_score=conviction,
        cap_multiplier=1.5,      # Slightly relaxed ADV cap for BTC
        max_trade_pct=0.15,      # 15% max per position (BTC is liquid)

        # Spot market, no leverage
        market_type=MarketType.SPOT,
        leverage=1.0,
        exchange='binance',

        # No breakeven ratchet (position is overlay-managed, not stop-managed)
        breakeven_atr=0.0,
    )
