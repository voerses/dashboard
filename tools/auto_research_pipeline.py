#!/usr/bin/env python3
"""
Auto-Research Pipeline — Automated Strategy Discovery Engine
=============================================================

3-stage pipeline for discovering new profitable strategies:

  Stage 1: SIGNAL GENERATION — Generate 30+ signal variants from a rich library
  Stage 2: FAST SCREEN — Run 12mo portfolio backtests in parallel, dual-window gate
  Stage 3: RANK & REPORT — Rank survivors, auto-log kills, output actionable report

Dual-Window Gate (critical for 2026):
  - Window 1: 12 months overall — must be profitable
  - Window 2: 3 months 2026 — must be profitable in current sideways regime
  - Both windows must pass. This kills strategies that only work in trending markets.

Usage:
    python tools/auto_research_pipeline.py                     # Full run, 4 workers
    python tools/auto_research_pipeline.py --workers 1         # Sequential (debug)
    python tools/auto_research_pipeline.py --stage screen      # Skip to screen stage
    python tools/auto_research_pipeline.py --signals momentum  # Only momentum signals
    python tools/auto_research_pipeline.py --top 5             # Only report top 5

Performance Target: Calmar > 3, MaxDD < 20%, Return > 300%, not overfitting.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

STRATEGY_DIR = PROJECT_ROOT / "strategies"
RESULTS_DIR = PROJECT_ROOT / "results" / "v4"
FINDINGS_FILE = PROJECT_ROOT / "findings" / "strategy-findings.jsonl"
GRAVEYARD_FILE = PROJECT_ROOT / "strategies" / "GRAVEYARD.md"
VENV_PYTHON = "/workspace/venv/bin/python"

# Import sweep framework
sys.path.insert(0, str(PROJECT_ROOT / "tools"))
from sweep_framework import parse_result, BacktestResult


# =============================================================================
# SIGNAL LIBRARY — 40+ signal variants organized by family
# =============================================================================
# Each signal is a dict with:
#   name: unique identifier
#   family: category (momentum, volatility, funding, microstructure, cross_asset)
#   code: numpy vectorized code block (injected into strategy template)
#   direction: "long_only", "short_only", or "bidirectional"
#   expected_hold: typical holding period in hours
#   mechanism: one-line economic rationale

SIGNAL_LIBRARY = {

    # =========================================================================
    # MOMENTUM FAMILY — proven in crypto, but our Tier A is saturated here
    # =========================================================================

    "momentum_burst_3pct": {
        "family": "momentum",
        "code": """
    ret_1 = ctx.ind_1h['ret_1']
    ema20 = ctx.ind_1h['ema_20']
    adx = ctx.ind_1h['adx']
    vol_ratio = ctx.ind_1h['vol_ratio']
    entry_long = (ret_1 > 0.03) & (close > ema20) & (adx > 25) & (vol_ratio > 1.0) & (regime != CRISIS)
    entry_short = (ret_1 < -0.03) & (close < ema20) & (adx > 25) & (vol_ratio > 1.0) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 24,
        "mechanism": "Strong single-bar moves predict continuation in trending markets",
    },

    "momentum_acceleration": {
        "family": "momentum",
        "code": """
    ret_1 = ctx.ind_1h['ret_1']
    ret_1_prev = np.roll(ret_1, 1); ret_1_prev[0] = 0
    accel = ret_1 - ret_1_prev
    ema20 = ctx.ind_1h['ema_20']
    adx = ctx.ind_1h['adx']
    entry_long = (accel > 0.02) & (ret_1 > 0.01) & (close > ema20) & (adx > 20) & (regime != CRISIS)
    entry_short = (accel < -0.02) & (ret_1 < -0.01) & (close < ema20) & (adx > 20) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 18,
        "mechanism": "Accelerating returns predict stronger continuation than constant momentum",
    },

    "dual_momentum_ema_stack": {
        "family": "momentum",
        "code": """
    ema10 = ctx.ind_1h['ema_10']
    ema20 = ctx.ind_1h['ema_20']
    ema50 = ctx.ind_1h['ema_50']
    adx = ctx.ind_1h['adx']
    vol_ratio = ctx.ind_1h['vol_ratio']
    entry_long = (ema10 > ema20) & (ema20 > ema50) & (adx > 25) & (vol_ratio > 1.0) & (regime != CRISIS)
    entry_short = (ema10 < ema20) & (ema20 < ema50) & (adx > 25) & (vol_ratio > 1.0) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 48,
        "mechanism": "Triple EMA alignment confirms trend strength across timeframes",
    },

    "macd_hist_acceleration": {
        "family": "momentum",
        "code": """
    hist = ctx.ind_1h['macd_hist']
    hp = np.roll(hist, 1); hp[0] = np.nan
    hp2 = np.roll(hist, 2); hp2[:2] = np.nan
    adx = ctx.ind_1h['adx']
    ema20 = ctx.ind_1h['ema_20']
    entry_long = (hist > hp) & (hp > hp2) & (hist > 0) & (adx > 20) & (close > ema20) & (regime != CRISIS)
    entry_short = (hist < hp) & (hp < hp2) & (hist < 0) & (adx > 20) & (close < ema20) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 24,
        "mechanism": "MACD histogram acceleration captures momentum ignition points",
    },

    "donchian_breakout_adx": {
        "family": "momentum",
        "code": """
    dh = ctx.ind_1h['donch_high']
    dl = ctx.ind_1h['donch_low']
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    dh_prev = np.roll(dh, 1); dh_prev[0] = np.nan
    dl_prev = np.roll(dl, 1); dl_prev[0] = np.nan
    close_prev = np.roll(close, 1); close_prev[0] = np.nan
    entry_long = (close > dh_prev) & (close_prev <= dh_prev) & (adx > 20) & (plus_di > minus_di) & (regime != CRISIS)
    entry_short = (close < dl_prev) & (close_prev >= dl_prev) & (adx > 20) & (minus_di > plus_di) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 36,
        "mechanism": "Channel breakouts with directional confirmation capture trend initiations",
    },

    "bb_squeeze_breakout": {
        "family": "momentum",
        "code": """
    bb_width = ctx.ind_1h['bb_width']
    bb_upper = ctx.ind_1h['bb_upper']
    bb_lower = ctx.ind_1h['bb_lower']
    adx = ctx.ind_1h['adx']
    vol_ratio = ctx.ind_1h['vol_ratio']
    from engine import rolling_mean
    avg_width = rolling_mean(bb_width, 120)
    squeeze = bb_width < avg_width * 0.5
    squeeze_prev = np.roll(squeeze.astype(np.float64), 1).astype(bool); squeeze_prev[0] = False
    bb_prev_u = np.roll(bb_upper, 1); bb_prev_u[0] = np.nan
    bb_prev_l = np.roll(bb_lower, 1); bb_prev_l[0] = np.nan
    close_prev = np.roll(close, 1); close_prev[0] = np.nan
    entry_long = squeeze_prev & (close > bb_prev_u) & (close_prev <= bb_prev_u) & (vol_ratio > 1.2) & (regime != CRISIS)
    entry_short = squeeze_prev & (close < bb_prev_l) & (close_prev >= bb_prev_l) & (vol_ratio > 1.2) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 24,
        "mechanism": "Bollinger squeeze followed by breakout = volatility expansion after compression",
    },

    # =========================================================================
    # VOLATILITY FAMILY — exploit vol-of-vol and regime transitions
    # =========================================================================

    "vol_spike_reversal": {
        "family": "volatility",
        "code": """
    atr = ctx.ind_1h['atr']
    from engine import rolling_mean, rolling_std
    atr_mean = rolling_mean(atr, 168)
    atr_std = rolling_std(atr, 168)
    atr_z = np.where(atr_std > 0, (atr - atr_mean) / atr_std, 0.0)
    rsi = ctx.ind_1h['rsi']
    entry_long = (atr_z > 2.0) & (rsi < 35) & (regime != CRISIS)
    entry_short = (atr_z > 2.0) & (rsi > 65) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 16,
        "mechanism": "Extreme vol spikes with RSI divergence predict short-term mean reversion",
    },

    "vol_compression_breakout": {
        "family": "volatility",
        "code": """
    atr = ctx.ind_1h['atr']
    from engine import rolling_mean
    atr_ratio = atr / np.where(rolling_mean(atr, 168) > 0, rolling_mean(atr, 168), np.nan)
    ret_1 = ctx.ind_1h['ret_1']
    adx = ctx.ind_1h['adx']
    # Low vol followed by breakout
    low_vol = atr_ratio < 0.6
    low_vol_prev = np.roll(low_vol.astype(np.float64), 1).astype(bool); low_vol_prev[0] = False
    entry_long = low_vol_prev & (ret_1 > 0.02) & (adx > 15) & (regime != CRISIS)
    entry_short = low_vol_prev & (ret_1 < -0.02) & (adx > 15) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 24,
        "mechanism": "Low vol regimes precede explosive moves in crypto (coiled spring effect)",
    },

    "range_contraction_expansion": {
        "family": "volatility",
        "code": """
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']
    bar_range = high - low
    from engine import rolling_mean
    avg_range = rolling_mean(bar_range, 24)
    narrow_bars = np.zeros(len(close), dtype=int)
    for_count = (bar_range < avg_range * 0.5).astype(np.int8)
    # Count consecutive narrow bars (vectorized via cumsum trick)
    groups = np.cumsum(~for_count.astype(bool))
    counts = np.zeros(len(close), dtype=int)
    # Simplified: just use rolling sum of narrow bars
    from engine import rolling_mean as rm
    narrow_pct = rm(for_count.astype(np.float64), 6)
    ret_1 = ctx.ind_1h['ret_1']
    entry_long = (narrow_pct > 0.6) & (ret_1 > 0.015) & (regime != CRISIS)
    entry_short = (narrow_pct > 0.6) & (ret_1 < -0.015) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 18,
        "mechanism": "6+ consecutive narrow-range bars predict volatility expansion",
    },

    "vol_of_vol_signal": {
        "family": "volatility",
        "code": """
    atr = ctx.ind_1h['atr']
    from engine import rolling_std, rolling_mean
    vol_of_vol = rolling_std(atr, 48)
    vol_of_vol_avg = rolling_mean(vol_of_vol, 168)
    vov_ratio = np.where(vol_of_vol_avg > 0, vol_of_vol / vol_of_vol_avg, 1.0)
    ret_1 = ctx.ind_1h['ret_1']
    ema20 = ctx.ind_1h['ema_20']
    # High vol-of-vol = uncertain regime, trade breakouts more aggressively
    entry_long = (vov_ratio > 1.5) & (ret_1 > 0.025) & (close > ema20) & (regime != CRISIS)
    entry_short = (vov_ratio > 1.5) & (ret_1 < -0.025) & (close < ema20) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 20,
        "mechanism": "High volatility-of-volatility creates wider moves that momentum can capture",
    },

    # =========================================================================
    # FUNDING / CARRY FAMILY — exploit funding rate dynamics
    # =========================================================================

    "funding_extreme_reversal": {
        "family": "funding",
        "code": """
    funding = ctx.funding_1h
    if funding is None:
        funding = np.zeros(len(close))
    from engine import rolling_mean, rolling_std
    fund_mean = rolling_mean(funding, 168)
    fund_std = rolling_std(funding, 168)
    fund_z = np.where(fund_std > 0, (funding - fund_mean) / fund_std, 0.0)
    adx = ctx.ind_1h['adx']
    # Extreme positive funding = overcrowded longs, fade them
    entry_short = (fund_z > 2.5) & (adx < 30) & (regime != CRISIS)
    entry_long = (fund_z < -2.5) & (adx < 30) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 24,
        "mechanism": "Extreme funding rates mean overcrowded positioning, which reverts",
    },

    "funding_momentum_alignment": {
        "family": "funding",
        "code": """
    funding = ctx.funding_1h
    if funding is None:
        funding = np.zeros(len(close))
    from engine import rolling_mean
    fund_avg = rolling_mean(funding, 24)
    ret_1 = ctx.ind_1h['ret_1']
    ema20 = ctx.ind_1h['ema_20']
    adx = ctx.ind_1h['adx']
    # Positive funding + momentum = trend confirmed by positioning
    entry_long = (fund_avg > 0.0001) & (ret_1 > 0.02) & (close > ema20) & (adx > 20) & (regime != CRISIS)
    entry_short = (fund_avg < -0.0001) & (ret_1 < -0.02) & (close < ema20) & (adx > 20) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 30,
        "mechanism": "Funding rate confirms directional bias — aligned momentum is higher quality",
    },

    "funding_carry_zscore": {
        "family": "funding",
        "code": """
    funding = ctx.funding_1h
    if funding is None:
        funding = np.zeros(len(close))
    from engine import rolling_mean, rolling_std
    fund_mean = rolling_mean(funding, 720)
    fund_std = rolling_std(funding, 720)
    fund_z = np.where(fund_std > 0, (funding - fund_mean) / fund_std, 0.0)
    # High funding z-score = rich carry opportunity (short perp, earn funding)
    entry_short = (fund_z > 1.5) & (regime != CRISIS)
    entry_long = (fund_z < -1.5) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 50,
        "mechanism": "Funding z-score >1.5 captures extreme carry periods with mean-reversion",
    },

    # =========================================================================
    # MICROSTRUCTURE FAMILY — exploit order flow and volume patterns
    # =========================================================================

    "taker_buy_surge": {
        "family": "microstructure",
        "code": """
    taker = ctx.ind_1h['taker']
    from engine import rolling_mean
    taker_avg = rolling_mean(taker, 48)
    taker_ratio = np.where(taker_avg > 0, taker / taker_avg, 1.0)
    adx = ctx.ind_1h['adx']
    ema20 = ctx.ind_1h['ema_20']
    entry_long = (taker_ratio > 1.3) & (close > ema20) & (adx > 20) & (regime != CRISIS)
    entry_short = (taker_ratio < 0.7) & (close < ema20) & (adx > 20) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 20,
        "mechanism": "Abnormal taker buy/sell ratio reveals aggressive directional positioning",
    },

    "volume_climax_reversal": {
        "family": "microstructure",
        "code": """
    vol = ctx.ind_1h['volume']
    from engine import rolling_mean, rolling_std
    vol_mean = rolling_mean(vol, 168)
    vol_std = rolling_std(vol, 168)
    vol_z = np.where(vol_std > 0, (vol - vol_mean) / vol_std, 0.0)
    rsi = ctx.ind_1h['rsi']
    # Extreme volume + extreme RSI = capitulation/euphoria reversal
    entry_long = (vol_z > 3.0) & (rsi < 30) & (regime != CRISIS)
    entry_short = (vol_z > 3.0) & (rsi > 70) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 12,
        "mechanism": "Volume climax with RSI extreme = exhaustion point, expect reversal",
    },

    "obv_divergence": {
        "family": "microstructure",
        "code": """
    vol = ctx.ind_1h['volume']
    ret_sign = np.sign(ctx.ind_1h['ret_1'])
    obv = np.cumsum(ret_sign * vol)
    from engine import rolling_mean
    obv_slope = obv - np.roll(obv, 24); obv_slope[:24] = 0
    price_slope = close - np.roll(close, 24); price_slope[:24] = 0
    price_dir = np.sign(price_slope)
    obv_dir = np.sign(obv_slope)
    adx = ctx.ind_1h['adx']
    # OBV divergence: volume leading price
    entry_long = (obv_dir > 0) & (price_dir < 0) & (adx > 15) & (regime != CRISIS)
    entry_short = (obv_dir < 0) & (price_dir > 0) & (adx > 15) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 24,
        "mechanism": "OBV-price divergence = smart money accumulation/distribution before reversal",
    },

    "vpin_regime": {
        "family": "microstructure",
        "code": """
    vol = ctx.ind_1h['volume']
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']
    mid = (high + low) / 2
    bar_range = np.where(high - low > 0, high - low, 1e-10)
    buy_frac = (close - mid) / bar_range
    buy_frac = np.clip(buy_frac, -1, 1)
    buy_vol = (0.5 + 0.5 * buy_frac) * vol
    sell_vol = vol - buy_vol
    from engine import rolling_mean
    imbalance_avg = rolling_mean(np.abs(buy_vol - sell_vol), 48)
    total_avg = rolling_mean(vol, 48)
    vpin = np.where(total_avg > 0, imbalance_avg / total_avg, 0.5)
    ema20 = ctx.ind_1h['ema_20']
    # High VPIN = informed trading, follow the direction
    entry_long = (vpin > 0.6) & (close > ema20) & (regime != CRISIS)
    entry_short = (vpin > 0.6) & (close < ema20) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 18,
        "mechanism": "High VPIN = informed flow imbalance, price will move in dominant direction",
    },

    # =========================================================================
    # MEAN REVERSION FAMILY — historically weak in crypto but testing variants
    # =========================================================================

    "rsi_extreme_with_vol_confirm": {
        "family": "mean_reversion",
        "code": """
    rsi = ctx.ind_1h['rsi']
    atr = ctx.ind_1h['atr']
    from engine import rolling_mean
    atr_avg = rolling_mean(atr, 168)
    vol_spike = atr > atr_avg * 1.5
    ema50 = ctx.ind_1h['ema_50']
    entry_long = (rsi < 25) & vol_spike & (close > ema50 * 0.95) & (regime != CRISIS)
    entry_short = (rsi > 75) & vol_spike & (close < ema50 * 1.05) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 12,
        "mechanism": "RSI extremes with volume spike = overreaction, short-term bounce expected",
    },

    "zscore_bb_extreme": {
        "family": "mean_reversion",
        "code": """
    bb_pct = ctx.ind_1h['bb_pct']
    rsi = ctx.ind_1h['rsi']
    vol_ratio = ctx.ind_1h['vol_ratio']
    entry_long = (bb_pct < 0.05) & (rsi < 30) & (vol_ratio > 1.5) & (regime != CRISIS)
    entry_short = (bb_pct > 0.95) & (rsi > 70) & (vol_ratio > 1.5) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 8,
        "mechanism": "Extreme BB position + RSI extreme + high volume = short-term overextension",
    },

    # =========================================================================
    # MULTI-TIMEFRAME FAMILY — combine 1H signals with 4H/daily context
    # =========================================================================

    "mtf_trend_pullback": {
        "family": "multi_timeframe",
        "code": """
    # Daily trend via 4H indicators
    ema50_4h = ctx.align_4h_to_1h(ctx.ind_4h['ema_50'])
    adx_4h = ctx.align_4h_to_1h(ctx.ind_4h['adx'])
    # 1H pullback
    rsi = ctx.ind_1h['rsi']
    ema20 = ctx.ind_1h['ema_20']
    ret_1 = ctx.ind_1h['ret_1']
    # Long: 4H uptrend + 1H pullback
    entry_long = (close > ema50_4h) & (adx_4h > 20) & (rsi < 40) & (ret_1 < -0.01) & (regime != CRISIS)
    # Short: 4H downtrend + 1H bounce
    entry_short = (close < ema50_4h) & (adx_4h > 20) & (rsi > 60) & (ret_1 > 0.01) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 24,
        "mechanism": "Buy pullbacks in higher-timeframe uptrends, sell bounces in downtrends",
    },

    "mtf_ema_alignment_breakout": {
        "family": "multi_timeframe",
        "code": """
    ema20_4h = ctx.align_4h_to_1h(ctx.ind_4h['ema_20'])
    ema50_4h = ctx.align_4h_to_1h(ctx.ind_4h['ema_50'])
    ema10 = ctx.ind_1h['ema_10']
    ema20 = ctx.ind_1h['ema_20']
    adx = ctx.ind_1h['adx']
    vol_ratio = ctx.ind_1h['vol_ratio']
    entry_long = (close > ema20_4h) & (ema20_4h > ema50_4h) & (ema10 > ema20) & (adx > 25) & (vol_ratio > 1.0) & (regime != CRISIS)
    entry_short = (close < ema20_4h) & (ema20_4h < ema50_4h) & (ema10 < ema20) & (adx > 25) & (vol_ratio > 1.0) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 36,
        "mechanism": "Multi-timeframe EMA alignment confirms trend on multiple scales",
    },

    # =========================================================================
    # ADX / TREND STRENGTH FAMILY — filter by trend quality
    # =========================================================================

    "adx_rising_trend": {
        "family": "trend_strength",
        "code": """
    adx = ctx.ind_1h['adx']
    adx_prev = np.roll(adx, 6); adx_prev[:6] = np.nan
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    ema20 = ctx.ind_1h['ema_20']
    vol_ratio = ctx.ind_1h['vol_ratio']
    # Rising ADX = strengthening trend
    entry_long = (adx > 25) & (adx > adx_prev) & (plus_di > minus_di) & (close > ema20) & (vol_ratio > 1.0) & (regime != CRISIS)
    entry_short = (adx > 25) & (adx > adx_prev) & (minus_di > plus_di) & (close < ema20) & (vol_ratio > 1.0) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 30,
        "mechanism": "Rising ADX + directional confirmation = strengthening trend worth riding",
    },

    "supertrend_adx_filter": {
        "family": "trend_strength",
        "code": """
    ema10 = ctx.ind_1h['ema_10']
    ema20 = ctx.ind_1h['ema_20']
    atr = ctx.ind_1h['atr']
    adx = ctx.ind_1h['adx']
    # Supertrend approximation: EMA10 +/- 2*ATR
    upper_band = ema10 + 2 * atr
    lower_band = ema10 - 2 * atr
    close_prev = np.roll(close, 1); close_prev[0] = np.nan
    entry_long = (close > upper_band) & (close_prev <= upper_band) & (adx > 25) & (regime != CRISIS)
    entry_short = (close < lower_band) & (close_prev >= lower_band) & (adx > 25) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 24,
        "mechanism": "Supertrend breakout with ADX filter captures trend initiations",
    },

    # =========================================================================
    # CROSS-ASSET FAMILY — use BTC as regime/sentiment indicator
    # =========================================================================

    "btc_regime_alt_momentum": {
        "family": "cross_asset",
        "code": """
    # Use BTC regime to time alt entries
    ret_1 = ctx.ind_1h['ret_1']
    adx = ctx.ind_1h['adx']
    vol_ratio = ctx.ind_1h['vol_ratio']
    ema20 = ctx.ind_1h['ema_20']
    # Only trade alts in BTC uptrend (regime 2) or range (regime 3)
    btc_uptrend = (regime == 2) | (regime == 3)
    entry_long = btc_uptrend & (ret_1 > 0.02) & (close > ema20) & (adx > 20) & (vol_ratio > 1.0)
    entry_short = (~btc_uptrend) & (ret_1 < -0.02) & (close < ema20) & (adx > 20) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 24,
        "mechanism": "BTC regime drives alt markets — only go long alts when BTC is positive",
    },

    # =========================================================================
    # HYBRID / COMPOSITE FAMILY — combine multiple signal types
    # =========================================================================

    "momentum_vol_squeeze": {
        "family": "hybrid",
        "code": """
    bb_width = ctx.ind_1h['bb_width']
    from engine import rolling_mean
    avg_width = rolling_mean(bb_width, 120)
    squeeze = bb_width < avg_width * 0.6
    ret_1 = ctx.ind_1h['ret_1']
    adx = ctx.ind_1h['adx']
    vol_ratio = ctx.ind_1h['vol_ratio']
    ema20 = ctx.ind_1h['ema_20']
    entry_long = squeeze & (ret_1 > 0.015) & (adx > 20) & (vol_ratio > 1.0) & (close > ema20) & (regime != CRISIS)
    entry_short = squeeze & (ret_1 < -0.015) & (adx > 20) & (vol_ratio > 1.0) & (close < ema20) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 20,
        "mechanism": "Momentum breakout during BB squeeze = high-conviction directional move",
    },

    "funding_vol_momentum": {
        "family": "hybrid",
        "code": """
    funding = ctx.funding_1h
    if funding is None:
        funding = np.zeros(len(close))
    from engine import rolling_mean, rolling_std
    fund_z = np.where(rolling_std(funding, 168) > 0,
                      (funding - rolling_mean(funding, 168)) / rolling_std(funding, 168), 0.0)
    atr = ctx.ind_1h['atr']
    atr_ratio = atr / np.where(rolling_mean(atr, 168) > 0, rolling_mean(atr, 168), np.nan)
    ret_1 = ctx.ind_1h['ret_1']
    ema20 = ctx.ind_1h['ema_20']
    # High funding + low vol + momentum = strong setup
    entry_long = (fund_z > 0) & (atr_ratio < 0.8) & (ret_1 > 0.02) & (close > ema20) & (regime != CRISIS)
    entry_short = (fund_z < 0) & (atr_ratio < 0.8) & (ret_1 < -0.02) & (close < ema20) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 24,
        "mechanism": "Funding bias + low vol + initial momentum = triple-confirmed setup",
    },

    "rsi_macd_divergence": {
        "family": "hybrid",
        "code": """
    rsi = ctx.ind_1h['rsi']
    macd = ctx.ind_1h['macd']
    macd_sig = ctx.ind_1h['macd_signal']
    adx = ctx.ind_1h['adx']
    # RSI oversold + MACD bullish cross
    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50
    macd_prev = np.roll(macd, 1); macd_prev[0] = 0
    macd_sig_prev = np.roll(macd_sig, 1); macd_sig_prev[0] = 0
    entry_long = (rsi < 35) & (rsi > rsi_prev) & (macd > macd_sig) & (macd_prev <= macd_sig_prev) & (regime != CRISIS)
    entry_short = (rsi > 65) & (rsi < rsi_prev) & (macd < macd_sig) & (macd_prev >= macd_sig_prev) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 18,
        "mechanism": "RSI extreme + MACD cross = dual-confirmed reversal/continuation point",
    },

    "multi_indicator_consensus": {
        "family": "hybrid",
        "code": """
    ema10 = ctx.ind_1h['ema_10']
    ema20 = ctx.ind_1h['ema_20']
    rsi = ctx.ind_1h['rsi']
    macd = ctx.ind_1h['macd']
    macd_sig = ctx.ind_1h['macd_signal']
    adx = ctx.ind_1h['adx']
    vol_ratio = ctx.ind_1h['vol_ratio']
    # Score: count how many indicators agree
    bull_score = ((ema10 > ema20).astype(np.float64) +
                  (rsi > 50).astype(np.float64) +
                  (macd > macd_sig).astype(np.float64) +
                  (adx > 20).astype(np.float64) +
                  (vol_ratio > 1.0).astype(np.float64))
    entry_long = (bull_score >= 4) & (regime != CRISIS)
    bear_score = ((ema10 < ema20).astype(np.float64) +
                  (rsi < 50).astype(np.float64) +
                  (macd < macd_sig).astype(np.float64) +
                  (adx > 20).astype(np.float64) +
                  (vol_ratio > 1.0).astype(np.float64))
    entry_short = (bear_score >= 4) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 30,
        "mechanism": "4+ indicators agreeing = high-conviction entry with multiple confirmations",
    },

    "ret_skew_momentum": {
        "family": "hybrid",
        "code": """
    from engine import rolling_skew
    ret_1 = ctx.ind_1h['ret_1']
    skew_48 = rolling_skew(ret_1, 48)
    ema20 = ctx.ind_1h['ema_20']
    adx = ctx.ind_1h['adx']
    vol_ratio = ctx.ind_1h['vol_ratio']
    # Positive skew + uptrend = momentum continuation expected
    entry_long = (skew_48 > 0.5) & (close > ema20) & (adx > 20) & (vol_ratio > 1.0) & (regime != CRISIS)
    entry_short = (skew_48 < -0.5) & (close < ema20) & (adx > 20) & (vol_ratio > 1.0) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 24,
        "mechanism": "Return skew captures asymmetric momentum — positive skew predicts continuation",
    },

    # =========================================================================
    # REGIME-ADAPTIVE FAMILY — different behavior per regime
    # =========================================================================

    "regime_adaptive_entry": {
        "family": "regime_adaptive",
        "code": """
    ret_1 = ctx.ind_1h['ret_1']
    rsi = ctx.ind_1h['rsi']
    ema20 = ctx.ind_1h['ema_20']
    adx = ctx.ind_1h['adx']
    vol_ratio = ctx.ind_1h['vol_ratio']
    # Uptrend: momentum entries
    up_long = (regime == 2) & (ret_1 > 0.02) & (close > ema20) & (adx > 25)
    # Range: mean reversion entries
    range_long = (regime == 3) & (rsi < 30) & (vol_ratio > 1.5)
    range_short = (regime == 3) & (rsi > 70) & (vol_ratio > 1.5)
    # Downtrend: short momentum
    down_short = (regime == 4) & (ret_1 < -0.02) & (close < ema20) & (adx > 25)
    # Quiet: breakout entries
    quiet_long = (regime == 1) & (ret_1 > 0.025) & (vol_ratio > 1.5)
    quiet_short = (regime == 1) & (ret_1 < -0.025) & (vol_ratio > 1.5)
    entry_long = up_long | range_long | quiet_long
    entry_short = down_short | range_short | quiet_short
""",
        "direction": "bidirectional",
        "expected_hold": 20,
        "mechanism": "Different entry logic per regime maximizes edge in each market condition",
    },

    # =========================================================================
    # RESEARCH-DERIVED SIGNALS (2025-2026 papers)
    # =========================================================================

    "kaufman_efficiency_mom": {
        "family": "momentum",
        "code": """
    # Kaufman Efficiency Ratio — filters choppy vs trending markets
    n_per = 10
    direction_move = np.abs(close - np.roll(close, n_per))
    direction_move[:n_per] = 0
    volatility_sum = np.zeros_like(close)
    abs_ret = np.abs(close - np.roll(close, 1))
    abs_ret[0] = 0
    from engine import rolling_mean
    volatility_sum = rolling_mean(abs_ret, n_per) * n_per
    er = np.where(volatility_sum > 0, direction_move / volatility_sum, 0.0)
    ema20 = ctx.ind_1h['ema_20']
    adx = ctx.ind_1h['adx']
    # High efficiency = strong trend, enter on momentum
    entry_long = (er > 0.6) & (close > ema20) & (adx > 20) & (regime != CRISIS)
    entry_short = (er > 0.6) & (close < ema20) & (adx > 20) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 24,
        "mechanism": "High Kaufman efficiency = clean trend, enter in direction of EMA alignment",
    },

    "donchian_ensemble_trend": {
        "family": "momentum",
        "code": """
    # Donchian Ensemble: combine multiple lookbacks (Sharpe 1.58 in 2025 paper)
    dh20 = ctx.ind_1h['donch_high']
    dl20 = ctx.ind_1h['donch_low']
    from engine import rolling_max, rolling_min
    dh50 = rolling_max(high, 50)
    dl50 = rolling_min(low, 50)
    # Score: how many channels confirm breakout
    long_score = (close > dh20).astype(np.float64) + (close > dh50).astype(np.float64)
    short_score = (close < dl20).astype(np.float64) + (close < dl50).astype(np.float64)
    adx = ctx.ind_1h['adx']
    entry_long = (long_score >= 2) & (adx > 20) & (regime != CRISIS)
    entry_short = (short_score >= 2) & (adx > 20) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 36,
        "mechanism": "Multi-lookback Donchian consensus filters false breakouts",
    },

    "parkinson_vol_ratio": {
        "family": "volatility",
        "code": """
    # Parkinson volatility (high-low range) vs close-close vol
    from engine import rolling_mean, rolling_std
    log_hl = np.log(np.maximum(high, 1e-10) / np.maximum(low, 1e-10))
    parkinson = rolling_mean(log_hl ** 2, 24) / (4 * np.log(2))
    parkinson = np.sqrt(np.maximum(parkinson, 0))
    cc_vol = rolling_std(ctx.ind_1h['ret_1'], 24)
    vol_ratio_pk = np.where(cc_vol > 0, parkinson / cc_vol, 1.0)
    ema20 = ctx.ind_1h['ema_20']
    # High Parkinson/CC ratio = intrabar volatility expanding = breakout imminent
    entry_long = (vol_ratio_pk > 1.5) & (close > ema20) & (regime != CRISIS)
    entry_short = (vol_ratio_pk > 1.5) & (close < ema20) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 18,
        "mechanism": "Parkinson/CC vol divergence detects hidden volatility before breakout",
    },

    "funding_roc_momentum": {
        "family": "funding",
        "code": """
    # Funding Rate of Change — directional momentum in funding rate
    funding = ctx.funding_1h
    if funding is None:
        funding = np.zeros(len(close))
    from engine import rolling_mean
    fund_8h = rolling_mean(funding, 8)
    fund_48h = rolling_mean(funding, 48)
    fund_roc = fund_8h - fund_48h  # short-term vs long-term funding
    ema20 = ctx.ind_1h['ema_20']
    adx = ctx.ind_1h['adx']
    # Rising funding + uptrend = longs crowded, expect continuation then reversal
    entry_long = (fund_roc < -0.0005) & (close > ema20) & (adx > 15) & (regime != CRISIS)
    entry_short = (fund_roc > 0.0005) & (close < ema20) & (adx > 15) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 24,
        "mechanism": "Funding rate momentum divergence = crowding signal for contrarian entry",
    },

    "funding_trend_divergence": {
        "family": "funding",
        "code": """
    # Funding-Trend Divergence: price trending but funding says otherwise
    funding = ctx.funding_1h
    if funding is None:
        funding = np.zeros(len(close))
    ema20 = ctx.ind_1h['ema_20']
    from engine import rolling_mean
    price_trend = (close > ema20).astype(np.float64) * 2 - 1  # +1 or -1
    fund_24h = rolling_mean(funding, 24)
    fund_sign = np.sign(fund_24h)
    # Divergence: price up but funding negative = longs underweight, room to run
    entry_long = (price_trend > 0) & (fund_sign < 0) & (regime != CRISIS)
    entry_short = (price_trend < 0) & (fund_sign > 0) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 30,
        "mechanism": "Price-funding divergence reveals underweight positioning in trend direction",
    },

    "volume_price_trend_confirm": {
        "family": "microstructure",
        "code": """
    # Volume-Price Trend Confirmation: rising price on rising volume
    vol = ctx.ind_1h['volume']
    from engine import rolling_mean
    vol_20 = rolling_mean(vol, 20)
    vol_rising = vol > vol_20 * 1.2
    ema10 = ctx.ind_1h['ema_10']
    ema20 = ctx.ind_1h['ema_20']
    adx = ctx.ind_1h['adx']
    price_up = (ema10 > ema20) & (close > ema10)
    price_dn = (ema10 < ema20) & (close < ema10)
    entry_long = price_up & vol_rising & (adx > 20) & (regime != CRISIS)
    entry_short = price_dn & vol_rising & (adx > 20) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 24,
        "mechanism": "Trend moves on expanding volume are more likely to persist",
    },

    "atr_channel_breakout": {
        "family": "volatility",
        "code": """
    # ATR Channel Breakout — Keltner-style with dynamic width
    atr = ctx.ind_1h['atr']
    ema20 = ctx.ind_1h['ema_20']
    adx = ctx.ind_1h['adx']
    upper = ema20 + 2.5 * atr
    lower = ema20 - 2.5 * atr
    prev_close = np.roll(close, 1); prev_close[0] = close[0]
    entry_long = (close > upper) & (prev_close <= upper) & (adx > 20) & (regime != CRISIS)
    entry_short = (close < lower) & (prev_close >= lower) & (adx > 20) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 30,
        "mechanism": "ATR channel breakout = volatility-adjusted trend initiation signal",
    },

    "carry_momentum_composite": {
        "family": "hybrid",
        "code": """
    # Carry-Momentum Composite (arXiv 2025, reported Sharpe 6.45)
    funding = ctx.funding_1h
    if funding is None:
        funding = np.zeros(len(close))
    ret_1 = ctx.ind_1h['ret_1']
    from engine import rolling_mean, rolling_std
    # Z-score of funding (carry)
    fund_std = rolling_std(funding, 168)
    fund_z = np.where(fund_std > 0, (funding - rolling_mean(funding, 168)) / fund_std, 0.0)
    # Z-score of momentum (24h return)
    ret_24 = np.zeros_like(close)
    ret_24[24:] = (close[24:] - close[:-24]) / np.where(close[:-24] > 0, close[:-24], 1.0)
    ret_std = rolling_std(ret_24, 168)
    ret_z = np.where(ret_std > 0, (ret_24 - rolling_mean(ret_24, 168)) / ret_std, 0.0)
    # Composite: both carry and momentum aligned
    composite = 0.5 * ret_z + 0.5 * (-fund_z)  # negative funding = bullish carry
    adx = ctx.ind_1h['adx']
    entry_long = (composite > 1.0) & (adx > 15) & (regime != CRISIS)
    entry_short = (composite < -1.0) & (adx > 15) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 48,
        "mechanism": "Carry + momentum alignment compound two independent alpha sources",
    },

    "multi_timescale_coherence": {
        "family": "multi_timeframe",
        "code": """
    # Multi-Timescale Coherence — all timeframes must agree
    ema10 = ctx.ind_1h['ema_10']
    ema20 = ctx.ind_1h['ema_20']
    ema50 = ctx.ind_1h['ema_50']
    # 4h alignment
    close_4h = ctx.ind_4h['close']
    ema20_4h = ctx.ind_4h['ema_20']
    align_4h = ctx.align_4h_to_1h((close_4h > ema20_4h).astype(np.float64))
    # Daily alignment
    close_d = ctx.ind_d['close']
    ema20_d = ctx.ind_d['ema_20']
    align_d = ctx.align_daily_to_1h((close_d > ema20_d).astype(np.float64))
    # Coherence score: all 3 TFs must agree
    bull_1h = (ema10 > ema20) & (ema20 > ema50)
    bear_1h = (ema10 < ema20) & (ema20 < ema50)
    adx = ctx.ind_1h['adx']
    entry_long = bull_1h & (align_4h > 0.5) & (align_d > 0.5) & (adx > 20) & (regime != CRISIS)
    entry_short = bear_1h & (align_4h < 0.5) & (align_d < 0.5) & (adx > 20) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 48,
        "mechanism": "Triple-timeframe trend coherence = high-conviction trend following",
    },

    "rsi_mean_reversion_range": {
        "family": "mean_reversion",
        "code": """
    # RSI mean reversion — ONLY in RANGE regime (where MR works)
    rsi = ctx.ind_1h['rsi']
    bb_pct = ctx.ind_1h['bb_pct']
    atr = ctx.ind_1h['atr']
    from engine import rolling_mean
    atr_avg = rolling_mean(atr, 168)
    vol_low = atr < atr_avg * 0.8  # low vol = range-bound
    # Only trade mean reversion in RANGE or QUIET regime
    range_ok = (regime == RANGE) | (regime == QUIET)
    entry_long = (rsi < 25) & (bb_pct < 0.1) & vol_low & range_ok
    entry_short = (rsi > 75) & (bb_pct > 0.9) & vol_low & range_ok
""",
        "direction": "bidirectional",
        "expected_hold": 8,
        "mechanism": "Mean reversion in range/quiet regimes where it actually works",
    },

    "ema_ribbon_expansion": {
        "family": "trend_strength",
        "code": """
    # EMA Ribbon Expansion — trend strength from EMA spread widening
    ema10 = ctx.ind_1h['ema_10']
    ema20 = ctx.ind_1h['ema_20']
    ema50 = ctx.ind_1h['ema_50']
    from engine import rolling_mean
    spread_10_20 = (ema10 - ema20) / np.where(ema20 > 0, ema20, 1.0)
    spread_20_50 = (ema20 - ema50) / np.where(ema50 > 0, ema50, 1.0)
    spread_avg = rolling_mean(np.abs(spread_10_20), 48)
    # Expansion: spread widening from average
    expanding = np.abs(spread_10_20) > spread_avg * 1.5
    adx = ctx.ind_1h['adx']
    entry_long = expanding & (spread_10_20 > 0) & (spread_20_50 > 0) & (adx > 20) & (regime != CRISIS)
    entry_short = expanding & (spread_10_20 < 0) & (spread_20_50 < 0) & (adx > 20) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 36,
        "mechanism": "EMA ribbon expansion captures trend acceleration phase",
    },

    "rsi_divergence_trend": {
        "family": "hybrid",
        "code": """
    # RSI-Price Divergence with trend filter — bullish/bearish divergence
    rsi = ctx.ind_1h['rsi']
    from engine import rolling_min, rolling_max
    # 24-bar price low vs RSI low (bullish divergence: lower price low, higher RSI low)
    price_low_24 = rolling_min(close, 24)
    rsi_low_24 = rolling_min(rsi, 24)
    price_low_48 = rolling_min(close, 48)
    rsi_low_48 = rolling_min(rsi, 48)
    bull_div = (price_low_24 < price_low_48) & (rsi_low_24 > rsi_low_48) & (rsi < 40)
    # Bear divergence
    price_hi_24 = rolling_max(close, 24)
    rsi_hi_24 = rolling_max(rsi, 24)
    price_hi_48 = rolling_max(close, 48)
    rsi_hi_48 = rolling_max(rsi, 48)
    bear_div = (price_hi_24 > price_hi_48) & (rsi_hi_24 < rsi_hi_48) & (rsi > 60)
    adx = ctx.ind_1h['adx']
    entry_long = bull_div & (adx > 15) & (regime != CRISIS)
    entry_short = bear_div & (adx > 15) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 18,
        "mechanism": "RSI-price divergence detects momentum exhaustion and reversal points",
    },

    "vol_regime_switch": {
        "family": "volatility",
        "code": """
    # Volatility Regime Switch — enter when vol transitions from low to high
    atr = ctx.ind_1h['atr']
    from engine import rolling_mean, rolling_std
    atr_mean = rolling_mean(atr, 168)
    atr_std = rolling_std(atr, 168)
    atr_z = np.where(atr_std > 0, (atr - atr_mean) / atr_std, 0.0)
    atr_z_prev = np.roll(atr_z, 6); atr_z_prev[:6] = 0
    # Transition: was low vol (z < -0.5), now expanding (z > 0.5)
    vol_expanding = (atr_z > 0.5) & (atr_z_prev < -0.5)
    ema20 = ctx.ind_1h['ema_20']
    entry_long = vol_expanding & (close > ema20) & (regime != CRISIS)
    entry_short = vol_expanding & (close < ema20) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 24,
        "mechanism": "Vol regime transition from calm to expanding = breakout in progress",
    },

    "price_momentum_quality": {
        "family": "momentum",
        "code": """
    # Quality-filtered momentum — only take momentum when trend is clean
    ret_1 = ctx.ind_1h['ret_1']
    ema10 = ctx.ind_1h['ema_10']
    ema20 = ctx.ind_1h['ema_20']
    adx = ctx.ind_1h['adx']
    plus_di = ctx.ind_1h['plus_di']
    minus_di = ctx.ind_1h['minus_di']
    vol_ratio = ctx.ind_1h['vol_ratio']
    # Quality: ADX strong + DI separation + volume
    quality = (adx > 30) & (vol_ratio > 1.0)
    di_bull = plus_di > minus_di + 10  # strong directional separation
    di_bear = minus_di > plus_di + 10
    entry_long = (ret_1 > 0.02) & quality & di_bull & (close > ema20) & (regime != CRISIS)
    entry_short = (ret_1 < -0.02) & quality & di_bear & (close < ema20) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 24,
        "mechanism": "Quality-filtered momentum: ADX>30 + DI separation ensures clean trend entry",
    },

    "bb_width_percentile_breakout": {
        "family": "volatility",
        "code": """
    # BB Width Percentile Breakout — ultra-tight BB then expansion
    bb_width = ctx.ind_1h['bb_width']
    from engine import rolling_mean
    avg_width = rolling_mean(bb_width, 240)
    # Width at historic low = extreme compression
    compression = bb_width < avg_width * 0.4
    # Breakout: close moves outside BB
    bb_upper = ctx.ind_1h['bb_upper']
    bb_lower = ctx.ind_1h['bb_lower']
    adx = ctx.ind_1h['adx']
    entry_long = compression & (close > bb_upper) & (adx > 15) & (regime != CRISIS)
    entry_short = compression & (close < bb_lower) & (adx > 15) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 20,
        "mechanism": "Extreme BB compression + breakout = high-conviction volatility expansion trade",
    },

    # =========================================================================
    # ITERATION 3: PERP-SPECIFIC + RSI VARIANT SIGNALS (2026-03-21)
    # Based on v2 findings: RSI mean-reversion works, momentum fails
    # =========================================================================

    "rsi_macd_wide": {
        "family": "hybrid",
        "code": """
    # RSI-MACD with wider RSI band (40 vs 35) — more trades
    rsi = ctx.ind_1h['rsi']
    macd = ctx.ind_1h['macd']
    macd_sig = ctx.ind_1h['macd_signal']
    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50
    macd_prev = np.roll(macd, 1); macd_prev[0] = 0
    macd_sig_prev = np.roll(macd_sig, 1); macd_sig_prev[0] = 0
    entry_long = (rsi < 40) & (rsi > rsi_prev) & (macd > macd_sig) & (macd_prev <= macd_sig_prev) & (regime != CRISIS)
    entry_short = (rsi > 60) & (rsi < rsi_prev) & (macd < macd_sig) & (macd_prev >= macd_sig_prev) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 18,
        "mechanism": "Wider RSI band captures more mean-reversion opportunities with MACD confirmation",
    },

    "rsi_macd_tight": {
        "family": "hybrid",
        "code": """
    # RSI-MACD with tighter RSI band (25) — fewer but higher-quality trades
    rsi = ctx.ind_1h['rsi']
    macd = ctx.ind_1h['macd']
    macd_sig = ctx.ind_1h['macd_signal']
    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50
    macd_prev = np.roll(macd, 1); macd_prev[0] = 0
    macd_sig_prev = np.roll(macd_sig, 1); macd_sig_prev[0] = 0
    entry_long = (rsi < 25) & (rsi > rsi_prev) & (macd > macd_sig) & (macd_prev <= macd_sig_prev) & (regime != CRISIS)
    entry_short = (rsi > 75) & (rsi < rsi_prev) & (macd < macd_sig) & (macd_prev >= macd_sig_prev) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 12,
        "mechanism": "Tighter RSI thresholds for deeper oversold/overbought = higher win rate but fewer trades",
    },

    "rsi_bb_bounce": {
        "family": "mean_reversion",
        "code": """
    # RSI oversold + price touching lower BB = double confirmation bounce
    rsi = ctx.ind_1h['rsi']
    bb_lower = ctx.ind_1h['bb_lower']
    bb_upper = ctx.ind_1h['bb_upper']
    bb_pct = ctx.ind_1h['bb_pct']
    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50
    # Long: RSI < 30, turning up, close near lower BB
    entry_long = (rsi < 30) & (rsi > rsi_prev) & (bb_pct < 0.15) & (regime != CRISIS)
    entry_short = (rsi > 70) & (rsi < rsi_prev) & (bb_pct > 0.85) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 12,
        "mechanism": "RSI oversold + lower BB touch = price at statistical extreme, expect bounce",
    },

    "rsi_stoch_cross": {
        "family": "mean_reversion",
        "code": """
    # RSI + Stochastic-like signal using BB %B as stochastic proxy
    rsi = ctx.ind_1h['rsi']
    bb_pct = ctx.ind_1h['bb_pct']
    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50
    bb_prev = np.roll(bb_pct, 1); bb_prev[0] = 0.5
    # Both RSI and BB%B turning from oversold
    entry_long = (rsi < 35) & (rsi > rsi_prev) & (bb_pct < 0.2) & (bb_pct > bb_prev) & (regime != CRISIS)
    entry_short = (rsi > 65) & (rsi < rsi_prev) & (bb_pct > 0.8) & (bb_pct < bb_prev) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 10,
        "mechanism": "Dual mean-reversion confirmation (RSI + BB%B both turning) = higher-probability bounce",
    },

    "funding_contrarian_rsi": {
        "family": "funding",
        "code": """
    # Funding contrarian + RSI: high funding = longs crowded, wait for RSI oversold to buy cheap
    funding = ctx.funding_1h
    if funding is None:
        funding = np.zeros(len(close))
    from engine import rolling_mean
    fund_24h = rolling_mean(funding, 24)
    rsi = ctx.ind_1h['rsi']
    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50
    # Negative funding (shorts pay) + RSI oversold = market too pessimistic, buy
    entry_long = (fund_24h < -0.0001) & (rsi < 35) & (rsi > rsi_prev) & (regime != CRISIS)
    # Positive funding (longs pay) + RSI overbought = market too optimistic, short
    entry_short = (fund_24h > 0.0003) & (rsi > 65) & (rsi < rsi_prev) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 18,
        "mechanism": "Funding rate contrarian signal: when crowded side pays up AND price at extreme, fade the crowd",
    },

    "perp_premium_reversal": {
        "family": "funding",
        "code": """
    # Perp premium/discount as contrarian signal
    # When perp trades at premium (funding high), expect mean reversion
    funding = ctx.funding_1h
    if funding is None:
        funding = np.zeros(len(close))
    from engine import rolling_mean, rolling_std
    fund_z = np.where(rolling_std(funding, 168) > 0,
                      (funding - rolling_mean(funding, 168)) / rolling_std(funding, 168), 0.0)
    rsi = ctx.ind_1h['rsi']
    # Extreme premium (z > 2) + overbought RSI = top forming
    entry_short = (fund_z > 2.0) & (rsi > 60) & (regime != CRISIS)
    # Extreme discount (z < -2) + oversold RSI = bottom forming
    entry_long = (fund_z < -2.0) & (rsi < 40) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 24,
        "mechanism": "Extreme perp premium/discount z-score = market positioning extreme, expect reversal",
    },

    "liquidation_cascade_momentum": {
        "family": "momentum",
        "code": """
    # Liquidation cascade proxy: large moves on high volume = forced selling/buying
    # In futures, big moves trigger liquidations which cascade into bigger moves
    ret_1 = ctx.ind_1h['ret_1']
    vol_ratio = ctx.ind_1h['vol_ratio']
    atr = ctx.ind_1h['atr']
    from engine import rolling_mean
    atr_avg = rolling_mean(atr, 168)
    atr_spike = atr > atr_avg * 2.0  # vol spike = liquidation event
    adx = ctx.ind_1h['adx']
    # After a big down move with vol spike, expect continuation (liquidation cascade)
    entry_short = (ret_1 < -0.03) & atr_spike & (vol_ratio > 1.5) & (adx > 20) & (regime != CRISIS)
    # After a big up move with vol spike, expect continuation (short squeeze cascade)
    entry_long = (ret_1 > 0.03) & atr_spike & (vol_ratio > 1.5) & (adx > 20) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 12,
        "mechanism": "Futures liquidation cascades: big moves + vol spike = forced buying/selling continues",
    },

    "regime_mean_reversion": {
        "family": "regime_adaptive",
        "code": """
    # Mean reversion ONLY in range-bound regimes, skip trending markets
    rsi = ctx.ind_1h['rsi']
    bb_pct = ctx.ind_1h['bb_pct']
    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50
    adx = ctx.ind_1h['adx']
    # Low ADX = range-bound = mean reversion works
    low_adx = adx < 20
    entry_long = (rsi < 30) & (rsi > rsi_prev) & (bb_pct < 0.15) & low_adx & ((regime == RANGE) | (regime == QUIET))
    entry_short = (rsi > 70) & (rsi < rsi_prev) & (bb_pct > 0.85) & low_adx & ((regime == RANGE) | (regime == QUIET))
""",
        "direction": "bidirectional",
        "expected_hold": 8,
        "mechanism": "Mean reversion only in confirmed range/quiet regime with low ADX = highest MR success rate",
    },

    "multi_tf_rsi_bounce": {
        "family": "multi_timeframe",
        "code": """
    # Multi-TF RSI: 1h RSI oversold + 4h RSI not extreme = pullback in intact trend
    rsi = ctx.ind_1h['rsi']
    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50
    # 4h RSI
    rsi_4h_raw = ctx.ind_4h['rsi']
    rsi_4h = ctx.align_4h_to_1h(rsi_4h_raw)
    adx = ctx.ind_1h['adx']
    # 1h oversold but 4h neutral-bullish = pullback in uptrend
    entry_long = (rsi < 30) & (rsi > rsi_prev) & (rsi_4h > 40) & (rsi_4h < 65) & (adx > 15) & (regime != CRISIS)
    # 1h overbought but 4h neutral-bearish = rally in downtrend
    entry_short = (rsi > 70) & (rsi < rsi_prev) & (rsi_4h < 60) & (rsi_4h > 35) & (adx > 15) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 14,
        "mechanism": "1h oversold in intact 4h trend = pullback buy, not trend reversal",
    },

    "atr_mean_reversion": {
        "family": "volatility",
        "code": """
    # ATR-based mean reversion: price moved > 2 ATR from EMA, expect snap-back
    atr = ctx.ind_1h['atr']
    ema20 = ctx.ind_1h['ema_20']
    rsi = ctx.ind_1h['rsi']
    deviation = (close - ema20) / np.where(atr > 0, atr, 1.0)
    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50
    # Price extended below EMA by > 2 ATR + RSI turning up
    entry_long = (deviation < -2.0) & (rsi < 35) & (rsi > rsi_prev) & (regime != CRISIS)
    entry_short = (deviation > 2.0) & (rsi > 65) & (rsi < rsi_prev) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 12,
        "mechanism": "Price > 2 ATR from EMA = statistical extreme, expect reversion to mean",
    },

    "funding_flip_signal": {
        "family": "funding",
        "code": """
    # Funding rate sign flip = sentiment regime change
    funding = ctx.funding_1h
    if funding is None:
        funding = np.zeros(len(close))
    from engine import rolling_mean
    fund_8h = rolling_mean(funding, 8)
    fund_48h = rolling_mean(funding, 48)
    # Short-term funding crosses above long-term = longs piling in
    fund_prev_8h = np.roll(fund_8h, 1); fund_prev_8h[0] = 0
    fund_prev_48h = np.roll(fund_48h, 1); fund_prev_48h[0] = 0
    # Funding flips positive (bearish → bullish positioning) = contrarian short
    bull_flip = (fund_8h > 0) & (fund_prev_8h <= 0) & (fund_48h < 0)
    # Funding flips negative (bullish → bearish) = contrarian long
    bear_flip = (fund_8h < 0) & (fund_prev_8h >= 0) & (fund_48h > 0)
    rsi = ctx.ind_1h['rsi']
    entry_long = bear_flip & (rsi < 45) & (regime != CRISIS)
    entry_short = bull_flip & (rsi > 55) & (regime != CRISIS)
""",
        "direction": "bidirectional",
        "expected_hold": 24,
        "mechanism": "Funding rate sign flip against longer-term trend = crowding reversal signal",
    },

    "rsi_triple_bottom": {
        "family": "mean_reversion",
        "code": """
    # RSI triple bottom pattern — RSI dips below 30 three times = exhaustion
    rsi = ctx.ind_1h['rsi']
    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = 50
    # Count recent RSI oversold instances
    rsi_os = (rsi < 30).astype(np.float64)
    from engine import rolling_mean
    rsi_os_rate = rolling_mean(rsi_os, 72)  # % of last 72 bars oversold
    # Multiple oversold instances + now turning up = exhaustion bounce
    entry_long = (rsi_os_rate > 0.15) & (rsi < 35) & (rsi > rsi_prev) & (regime != CRISIS)
    entry_short = False  # Long-only for this exhaustion pattern
    entry_short = np.zeros(len(close), dtype=bool)
""",
        "direction": "bidirectional",
        "expected_hold": 14,
        "mechanism": "Multiple RSI oversold dips = selling exhaustion, high-probability bounce",
    },
}


# =============================================================================
# STRATEGY TEMPLATES — bidirectional (perp) and long-only variants
# =============================================================================

# Bidirectional template: uses entry_long/entry_short from signal code
TEMPLATE_BIDIR = '''"""Auto-research: {name}"""
import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_max, rolling_min, rolling_mean)

WARMUP = {warmup}

def strategy(ctx: StrategyContext) -> StrategyResult:
    close = ctx.ind_1h['close']
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']
    n = len(close)
    regime = ctx.regime_1h

{signal_code}

    entry_mask = entry_long | entry_short
    direction = np.where(entry_long, 1, np.where(entry_short, -1, 0)).astype(np.int8)
    entry_mask[:WARMUP] = False
    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,
        market_type=MarketType.PERP,
        leverage={leverage},
        stop_mult={stop},
        trail_mult={trail},
        target_mult=999.0,
        no_stop_bars={grace},
        min_hold={min_hold},
        max_hold={max_hold},
        edge=0.40,
        exit_regimes={{{exit_regimes}}},
        breakeven_atr={breakeven},
        max_trade_pct={max_trade_pct},
        exchange='binance',
        name='{name}',
    )
'''

# Long-only template: only uses entry_long from signal code
TEMPLATE_LONG = '''"""Auto-research: {name}"""
import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_max, rolling_min, rolling_mean)

WARMUP = {warmup}

def strategy(ctx: StrategyContext) -> StrategyResult:
    close = ctx.ind_1h['close']
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']
    n = len(close)
    regime = ctx.regime_1h

{signal_code}

    entry_mask = entry_long.copy()
    direction = np.ones(n, dtype=np.int8)
    entry_mask[:WARMUP] = False
    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,
        stop_mult={stop},
        trail_mult={trail},
        target_mult=999.0,
        no_stop_bars={grace},
        min_hold={min_hold},
        max_hold={max_hold},
        edge=0.40,
        exit_regimes={{CRISIS, DOWNTREND}},
        breakeven_atr={breakeven},
        max_trade_pct={max_trade_pct},
        exchange='binance',
        name='{name}',
    )
'''

# Short-only template for downtrend strategies
TEMPLATE_SHORT = '''"""Auto-research: {name}"""
import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_max, rolling_min, rolling_mean)

WARMUP = {warmup}

def strategy(ctx: StrategyContext) -> StrategyResult:
    close = ctx.ind_1h['close']
    high = ctx.ind_1h['high']
    low = ctx.ind_1h['low']
    n = len(close)
    regime = ctx.regime_1h

{signal_code}

    # Short-only: restrict to DOWNTREND regime for safety
    entry_mask = entry_short & (regime == DOWNTREND)
    direction = np.full(n, -1, dtype=np.int8)
    entry_mask[:WARMUP] = False
    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,
        market_type=MarketType.PERP,
        leverage={leverage},
        stop_mult={stop},
        trail_mult={trail},
        target_mult=999.0,
        no_stop_bars={grace},
        min_hold={min_hold},
        max_hold={max_hold},
        edge=0.40,
        exit_regimes={{CRISIS}},
        breakeven_atr={breakeven},
        max_trade_pct={max_trade_pct},
        exchange='binance',
        name='{name}',
    )
'''

TEMPLATES = {
    'bidir': TEMPLATE_BIDIR,
    'long': TEMPLATE_LONG,
    'short': TEMPLATE_SHORT,
}

# Parameter configs — matching proven Tier A patterns
# (leverage, stop, trail, grace, min_hold, max_hold, breakeven, warmup,
#  max_trade_pct, exit_regimes, template, label)
PARAM_CONFIGS = [
    # ---- s11-like: long-only, 1x, wide trail (proven Tier A pattern) ----
    (1.0, 3.0, 3.0, 24, 18, 720, 0.5, 200, 0.12, "CRISIS, DOWNTREND", "long", "s11_long"),
    # ---- Long-only: 1x, standard trail ----
    (1.0, 3.0, 1.5, 24, 12, 504, 0.5, 200, 0.12, "CRISIS, DOWNTREND", "long", "std_long"),
    # ---- Bidirectional perp: 1x, standard ----
    (1.0, 3.0, 1.5, 24, 12, 504, 0.5, 200, 0.12, "CRISIS", "bidir", "std_bidir"),
    # ---- Bidirectional perp: 1x, wide trail ----
    (1.0, 3.0, 3.0, 24, 12, 720, 0.5, 200, 0.12, "CRISIS", "bidir", "wide_bidir"),
    # ---- Short-only perp: for 2026 regime (downtrend shorts) ----
    (1.0, 3.0, 1.5, 12, 6, 336, 0.5, 200, 0.12, "CRISIS", "short", "short_only"),
    # ---- Mild leverage bidirectional: 1.5x for strong signals ----
    (1.5, 3.0, 2.0, 24, 12, 504, 0.5, 200, 0.10, "CRISIS", "bidir", "mild_lev"),
]


# =============================================================================
# PIPELINE STAGES
# =============================================================================

def stage_1_generate_candidates(
    families: Optional[list[str]] = None,
) -> list[dict]:
    """Stage 1: Generate all signal x param combinations.

    Returns list of candidate dicts ready for backtesting.
    """
    candidates = []
    for sig_name, sig_info in SIGNAL_LIBRARY.items():
        # Filter by family if specified
        if families and sig_info['family'] not in families:
            continue

        for lev, stop, trail, grace, min_h, max_h, be, warmup, mtp, exit_r, tmpl, plabel in PARAM_CONFIGS:
            label = f"{sig_name}__{plabel}"
            candidates.append({
                'label': label,
                'signal': sig_name,
                'family': sig_info['family'],
                'signal_code': sig_info['code'],
                'mechanism': sig_info['mechanism'],
                'leverage': lev,
                'stop': stop,
                'trail': trail,
                'grace': grace,
                'min_hold': min_h,
                'max_hold': max_h,
                'breakeven': be,
                'warmup': warmup,
                'max_trade_pct': mtp,
                'exit_regimes': exit_r,
                'template': tmpl,
                'name': label,
            })

    print(f"\n{'='*70}")
    print(f"  STAGE 1: Generated {len(candidates)} candidates")
    print(f"  Signals: {len(SIGNAL_LIBRARY)} | Param configs: {len(PARAM_CONFIGS)}")
    if families:
        print(f"  Filtered to families: {families}")
    print(f"{'='*70}\n")
    return candidates


def _run_one_candidate(args: tuple) -> dict:
    """Worker function: write strategy file, run backtest, parse results.

    Each worker uses a unique strategy ID (sW{index}) to avoid filename
    collisions when running in parallel. The strategy file is written and
    cleaned up per-run.
    """
    candidate, worker_index, months = args
    # Unique strategy ID per worker — avoids _load_strategy_fn picking wrong file
    strat_id = f"sW{worker_index:03d}"
    strat_file = STRATEGY_DIR / f"{strat_id}_auto.py"
    result = {**candidate, 'error': '', 'runtime_s': 0}

    try:
        # Generate strategy code using the appropriate template
        template = TEMPLATES.get(candidate.get('template', 'bidir'), TEMPLATE_BIDIR)
        code = template.format(**candidate)
        with open(strat_file, 'w') as f:
            f.write(code)

        # Run portfolio backtest with unique strategy ID
        cmd = [
            VENV_PYTHON, 'v4/portfolio_backtest.py',
            '--strategy', strat_id,
            '--months', str(months),
            '--capital', '200000',
            '--market', 'perp',
        ]
        t0 = time.perf_counter()
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=300, cwd=str(PROJECT_ROOT),
        )
        result['runtime_s'] = time.perf_counter() - t0

        metrics = parse_result(proc.stdout + proc.stderr)
        result.update(metrics)

        if not metrics:
            result['error'] = f"No metrics. stderr[-300]: {proc.stderr[-300:]}"

    except subprocess.TimeoutExpired:
        result['error'] = "Timeout"
    except Exception as e:
        result['error'] = str(e)
    finally:
        # Always cleanup the temp strategy file
        if strat_file.exists():
            strat_file.unlink()

    return result


def stage_2_screen(
    candidates: list[dict],
    workers: int = 4,
    months_12: int = 12,
    months_3: int = 3,
    verbose: bool = True,
) -> tuple[list[dict], list[dict]]:
    """Stage 2: Run portfolio backtests, dual-window gate.

    First runs 12-month backtests in parallel.
    Then runs 3-month (2026) backtests on 12-month survivors.

    Returns (survivors, kills).
    """
    total = len(candidates)
    print(f"\n{'='*70}")
    print(f"  STAGE 2: Screening {total} candidates (12mo window)")
    print(f"  Workers: {workers}")
    print(f"{'='*70}\n")

    # --- Window 1: 12-month backtest ---
    results_12mo = []

    if workers <= 1:
        for i, c in enumerate(candidates):
            r = _run_one_candidate((c, i, months_12))
            results_12mo.append(r)
            if verbose:
                _print_result(i + 1, total, r, "12mo")
    else:
        # Parallel: each candidate gets a unique worker index for its strategy ID
        work_items = [(c, i, months_12) for i, c in enumerate(candidates)]

        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_run_one_candidate, item): i
                       for i, item in enumerate(work_items)}
            done = 0
            for future in as_completed(futures):
                done += 1
                r = future.result()
                results_12mo.append(r)
                if verbose:
                    _print_result(done, total, r, "12mo")

    # --- Gate 1: 12-month thresholds ---
    # Relaxed for screening: let marginal candidates through to 2026 gate
    survivors_12 = []
    kills = []
    for r in results_12mo:
        ann_ret = r.get('annualized_return', 0)
        if r.get('error'):
            r['kill_reason'] = f"Error: {r['error'][:100]}"
            kills.append(r)
        elif r.get('total_trades', 0) < 20:
            r['kill_reason'] = f"Too few trades ({r.get('total_trades', 0)})"
            kills.append(r)
        elif ann_ret < 0:
            r['kill_reason'] = f"Negative return ({ann_ret:.1f}%)"
            kills.append(r)
        elif r.get('max_dd_pct', -100) < -40:
            r['kill_reason'] = f"MaxDD too high ({r.get('max_dd_pct', 0):.1f}%)"
            kills.append(r)
        elif r.get('profit_factor', 0) < 1.0:
            r['kill_reason'] = f"PF below breakeven ({r.get('profit_factor', 0):.2f})"
            kills.append(r)
        else:
            survivors_12.append(r)

    print(f"\n  12mo gate: {len(survivors_12)} survive, {len(kills)} killed")

    if not survivors_12:
        print("  No survivors from 12mo window. Returning all kills.")
        return [], kills

    # --- Window 2: 3-month 2026 backtest on survivors ---
    print(f"\n{'='*70}")
    print(f"  STAGE 2b: 2026 regime test ({len(survivors_12)} survivors, 3mo window)")
    print(f"{'='*70}\n")

    results_3mo = []
    total_s2 = len(survivors_12)

    if workers <= 1:
        for i, c in enumerate(survivors_12):
            r = _run_one_candidate((c, i, months_3))
            results_3mo.append(r)
            if verbose:
                _print_result(i + 1, total_s2, r, "3mo")
    else:
        work_items = [(c, i, months_3) for i, c in enumerate(survivors_12)]

        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_run_one_candidate, item): i
                       for i, item in enumerate(work_items)}
            done = 0
            for future in as_completed(futures):
                done += 1
                r = future.result()
                results_3mo.append(r)
                if verbose:
                    _print_result(done, total_s2, r, "3mo")

    # --- Gate 2: 2026 regime thresholds ---
    final_survivors = []
    for r12, r3 in zip(survivors_12, results_3mo):
        if r3.get('error'):
            r12['kill_reason'] = f"2026 error: {r3['error'][:100]}"
            kills.append(r12)
        elif r3.get('annualized_return_pct', 0) < 0:
            r12['kill_reason'] = f"2026 negative return ({r3.get('annualized_return_pct', 0):.1f}%)"
            r12['metrics_3mo'] = r3
            kills.append(r12)
        elif r3.get('calmar', 0) < 0.3:
            r12['kill_reason'] = f"2026 Calmar < 0.3 ({r3.get('calmar', 0):.2f})"
            r12['metrics_3mo'] = r3
            kills.append(r12)
        else:
            r12['metrics_3mo'] = r3
            final_survivors.append(r12)

    print(f"\n  2026 gate: {len(final_survivors)} survive, {len(kills)} total killed")

    return final_survivors, kills


def stage_3_report(
    survivors: list[dict],
    kills: list[dict],
    top_n: int = 20,
) -> list[dict]:
    """Stage 3: Rank survivors, log findings, output report.

    Returns ranked survivors.
    """
    # Sort by Calmar (primary), then Sortino (secondary)
    survivors.sort(key=lambda r: (r.get('calmar', 0), r.get('sortino', 0)), reverse=True)

    print(f"\n{'='*110}")
    print(f"  STAGE 3: FINAL RANKINGS — Top {min(top_n, len(survivors))} of {len(survivors)} survivors")
    print(f"{'='*110}")

    if survivors:
        header = (
            f"{'#':>3} {'Signal':35s} {'Params':10s} {'Ann%':>9} {'Cal':>6} "
            f"{'DD%':>7} {'Sharpe':>7} {'Sort':>7} {'PF':>5} "
            f"{'Trd':>5} {'WR%':>5} | {'3mo Ann%':>9} {'3mo Cal':>7}"
        )
        print(header)
        print('-' * 130)
        for i, r in enumerate(survivors[:top_n]):
            m3 = r.get('metrics_3mo', {})
            sig = r.get('signal', '?')
            params = r.get('label', '').split('__')[-1] if '__' in r.get('label', '') else '?'
            print(
                f"{i+1:>3} {sig:35s} {params:10s} "
                f"{r.get('annualized_return_pct', 0):>9.1f} "
                f"{r.get('calmar', 0):>6.2f} "
                f"{r.get('max_dd_pct', 0):>7.1f} "
                f"{r.get('sharpe', 0):>7.2f} "
                f"{r.get('sortino', 0):>7.2f} "
                f"{r.get('profit_factor', 0):>5.2f} "
                f"{r.get('total_trades', 0):>5d} "
                f"{r.get('win_rate', 0):>5.1f}"
                f" | {m3.get('annualized_return_pct', 0):>9.1f} "
                f"{m3.get('calmar', 0):>7.2f}"
            )
    else:
        print("  No survivors. All candidates killed.")

    # Kill summary by family
    kill_families = {}
    for k in kills:
        fam = k.get('family', 'unknown')
        kill_families[fam] = kill_families.get(fam, 0) + 1

    print(f"\n  Kill summary by family:")
    for fam, count in sorted(kill_families.items(), key=lambda x: -x[1]):
        print(f"    {fam:20s}: {count} killed")

    # Log findings
    _log_findings(survivors, kills)

    # Save full results
    _save_results(survivors, kills)

    return survivors


def _print_result(done: int, total: int, r: dict, window: str):
    """Print a single result line."""
    err = f" ERR:{r['error'][:40]}" if r.get('error') else ""
    print(
        f"  [{done:>3}/{total}] {window} {r.get('label', '?'):40s} | "
        f"Ann={r.get('annualized_return_pct', 0):>8.1f}% "
        f"Cal={r.get('calmar', 0):>6.2f} "
        f"DD={r.get('max_dd_pct', 0):>6.1f}% "
        f"PF={r.get('profit_factor', 0):>5.2f} "
        f"Trd={r.get('total_trades', 0):>4} "
        f"({r.get('runtime_s', 0):.0f}s)"
        f"{err}"
    )


def _log_findings(survivors: list[dict], kills: list[dict]):
    """Append findings to strategy-findings.jsonl."""
    os.makedirs(FINDINGS_FILE.parent, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with open(FINDINGS_FILE, 'a') as f:
        # Log survivors
        for s in survivors[:5]:
            finding = {
                "ts": ts,
                "strategy": f"auto_{s.get('signal', '?')}",
                "gate": 2,
                "outcome": "pass",
                "metrics": {
                    "calmar": s.get('calmar', 0),
                    "sharpe": s.get('sharpe', 0),
                    "max_dd": s.get('max_dd_pct', 0),
                    "annual": s.get('annualized_return_pct', 0),
                    "trades": s.get('total_trades', 0),
                },
                "finding": f"Signal {s.get('signal', '?')} ({s.get('family', '?')}) passed dual-window screen. Calmar={s.get('calmar', 0):.2f}",
                "category": "signal",
                "affects": ["tools/auto_research_pipeline.py"],
            }
            f.write(json.dumps(finding) + "\n")

        # Log kill summary
        if kills:
            kill_summary = {}
            for k in kills:
                reason = k.get('kill_reason', 'unknown')[:50]
                kill_summary[reason] = kill_summary.get(reason, 0) + 1

            finding = {
                "ts": ts,
                "strategy": "auto_research_batch",
                "gate": 2,
                "outcome": "kill",
                "metrics": {"total_killed": len(kills), "total_survived": len(survivors)},
                "finding": f"Batch screen: {len(survivors)} survived, {len(kills)} killed. Top kill reasons: {dict(sorted(kill_summary.items(), key=lambda x: -x[1])[:3])}",
                "category": "process",
                "affects": ["tools/auto_research_pipeline.py"],
            }
            f.write(json.dumps(finding) + "\n")


def _save_results(survivors: list[dict], kills: list[dict]):
    """Save full results to JSON."""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    outpath = RESULTS_DIR / f"auto_research_{ts}.json"

    # Clean non-serializable fields
    def clean(d):
        return {k: v for k, v in d.items()
                if not callable(v) and k != 'signal_code'}

    data = {
        "timestamp": ts,
        "total_candidates": len(survivors) + len(kills),
        "survivors": len(survivors),
        "kills": len(kills),
        "results": [clean(s) for s in survivors],
        "kills_summary": [
            {"signal": k.get('signal'), "family": k.get('family'),
             "kill_reason": k.get('kill_reason', 'unknown')}
            for k in kills
        ],
    }
    with open(outpath, 'w') as f:
        json.dump(data, f, indent=2, default=str)
    print(f"\n  Full results saved to {outpath}")


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Auto-Research Pipeline")
    parser.add_argument("--workers", type=int, default=4,
                        help="Parallel workers (default: 4)")
    parser.add_argument("--signals", type=str, default=None,
                        help="Comma-separated signal families to test (default: all)")
    parser.add_argument("--top", type=int, default=20,
                        help="Show top N results (default: 20)")
    parser.add_argument("--stage", type=str, default="all",
                        choices=["all", "generate", "screen"],
                        help="Which stage to run")
    parser.add_argument("--months-12", type=int, default=12,
                        help="Long window months (default: 12)")
    parser.add_argument("--months-3", type=int, default=3,
                        help="Short window months for 2026 regime (default: 3)")
    args = parser.parse_args()

    families = args.signals.split(",") if args.signals else None

    print("=" * 70)
    print("  AUTO-RESEARCH PIPELINE — Strategy Discovery Engine")
    print("=" * 70)
    print(f"  Workers: {args.workers}")
    print(f"  Families: {families or 'ALL'}")
    print(f"  Windows: {args.months_12}mo + {args.months_3}mo (2026)")
    print(f"  Targets: Calmar>3, DD<20%, Return>300%")
    print()

    t_start = time.time()

    # Stage 1: Generate
    candidates = stage_1_generate_candidates(families)

    if args.stage == "generate":
        print(f"\n  Generated {len(candidates)} candidates. Use --stage all to screen.")
        return

    # Stage 2: Screen
    survivors, kills = stage_2_screen(
        candidates,
        workers=args.workers,
        months_12=args.months_12,
        months_3=args.months_3,
    )

    # Stage 3: Report
    ranked = stage_3_report(survivors, kills, top_n=args.top)

    elapsed = time.time() - t_start
    print(f"\n  Total pipeline time: {elapsed:.0f}s ({elapsed/60:.1f}m)")
    print(f"  Throughput: {len(candidates)/(elapsed/60):.1f} candidates/minute")

    return ranked


if __name__ == "__main__":
    main()
