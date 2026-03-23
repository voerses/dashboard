"""
s220 ML-Adaptive Regime Strategy — V4 Portfolio Strategy
========================================================
Class B (Portfolio): Combines ML direction model with turbulence-based
regime detection and cross-sectional dispersion sizing.

Core hypothesis: The V4 ML model (60% LONG / 57% SHORT precision at p>=0.60)
provides real predictive edge when combined with:
  1. Regime-filtered inference (LONG only in UPTREND/DOWNTREND, SHORT in QUIET/RANGE)
  2. Turbulence Index for fast crisis detection (1-bar lag vs 24h daily)
  3. Cross-sectional dispersion for dynamic position sizing
  4. Carry overlay: when funding direction agrees with ML prediction, boost size

The ML model uses 47 features including market context (BTC stats, dispersion,
breadth, regime) and cross-sectional ranks. As a portfolio strategy, we can
compute all of these.

Key: The model is NOT a standalone alpha source. It's a FILTER that improves
entry quality from ~46% WR to ~60% WR, making leverage profitable.

Target: >300% annual, <20% DD, Calmar >3
Market: PERP (bidirectional)
Status: EXPERIMENTAL
"""

import numpy as np
import pandas as pd
import joblib
import os
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_mean, _rolling_std, _rolling_mean, _ema)

STRATEGY_TYPE = "portfolio"

# ── Model paths ──────────────────────────────────────────────────
_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(_DIR, 'results', 'v4', 'ml_dir_v4_model.joblib')
FEATURES_PATH = os.path.join(_DIR, 'results', 'v4', 'ml_dir_v4_features.joblib')

# ── Configuration ────────────────────────────────────────────────
# ML model thresholds
ML_LONG_THRESHOLD = 0.58          # Probability threshold for LONG entry
ML_SHORT_THRESHOLD = 0.60         # Higher threshold for SHORT (more selective)
ML_HIGH_CONVICTION = 0.65         # High conviction → bigger size

# Regime filtering (from ML model regime-dependent performance)
# LONG works in: UPTREND (61.6%), DOWNTREND (59.2%)
# SHORT works in: QUIET (90.5%), RANGE (63.5%), DOWNTREND (60.4%)
ALLOW_LONG_REGIMES = {UPTREND, RANGE, DOWNTREND}   # Allow in most, block CRISIS/QUIET
ALLOW_SHORT_REGIMES = {QUIET, RANGE, DOWNTREND}     # Short thrives in non-trending

# Turbulence parameters
TURB_WINDOW = 500
TURB_CRISIS_PCTILE = 0.90
TURB_BASKET = 12

# Position sizing
MAX_POSITIONS = 15
BASE_LEVERAGE = 3.0               # Base leverage (amplified by conviction)
MAX_LEVERAGE = 5.0                # Cap leverage
MIN_ADV_USD = 2_000_000
CAP_MULTIPLIER = 8.0

# Trade management
STOP_MULT = 3.5                   # 3.5x ATR (wider for ML-filtered entries)
TRAIL_MULT = 1.5                  # 1.5x ATR trail (proven)
NO_STOP_BARS = 24                 # 24h protection
MAX_HOLD = 336                    # 14 days
MIN_HOLD = 12
EDGE = 0.35
BREAKEVEN_ATR = 0.5

# Dispersion sizing
DISP_LOOKBACK = 24
DISP_WINDOW = 336
DISP_FLOOR = 0.6
DISP_CEIL = 1.8

# Carry overlay
FUNDING_WINDOW = 48
FUNDING_BOOST = 1.4               # Boost when carry direction agrees with ML


def _compute_features_for_token(ctx, btc_data, market_data, cross_sect_data, bar_idx_offset):
    """Compute all 47 features for a single token as arrays."""
    n = len(ctx.ind_1h['close'])

    close = ctx.ind_1h['close']
    high = ctx.ind_1h.get('high', close)
    low = ctx.ind_1h.get('low', close)
    opn = ctx.ind_1h.get('open', close)
    volume = ctx.ind_1h.get('volume', np.ones(n))

    # Pre-computed indicators from engine
    adx = ctx.ind_1h.get('adx', np.full(n, 20.0))
    rsi = ctx.ind_1h.get('rsi', np.full(n, 50.0))
    macd = ctx.ind_1h.get('macd', np.zeros(n))
    bb_pct = ctx.ind_1h.get('bb_pct', np.full(n, 0.5))
    bb_width = ctx.ind_1h.get('bb_width', np.full(n, 0.02))
    atr = ctx.ind_1h.get('atr', np.ones(n) * 0.01)
    plus_di = ctx.ind_1h.get('plus_di', np.full(n, 20.0))
    minus_di = ctx.ind_1h.get('minus_di', np.full(n, 20.0))
    vol_ratio = ctx.ind_1h.get('vol_ratio', np.ones(n))
    ret_1 = ctx.ind_1h.get('ret_1', np.zeros(n))
    vol_20 = ctx.ind_1h.get('vol_20', np.ones(n) * 0.01)
    donch_high = ctx.ind_1h.get('donch_high', high)
    donch_low = ctx.ind_1h.get('donch_low', low)
    ema10 = ctx.ind_1h.get('ema_10', close)
    ema20 = ctx.ind_1h.get('ema_20', close)
    ema50 = ctx.ind_1h.get('ema_50', close)

    funding = ctx.funding_1h if ctx.funding_1h is not None else np.zeros(n)
    regime = ctx.regime_1h

    # ── Derived features ──────────────────────────────────────────
    safe_close = np.maximum(close, 1e-10)
    safe_atr = np.maximum(atr, 1e-10)
    candle_range = np.maximum(high - low, 1e-10)

    atr_pct = atr / safe_close
    body_pct = np.abs(close - opn) / candle_range
    donch_range = np.maximum(donch_high - donch_low, 1e-10)
    donch_pos = (close - donch_low) / donch_range
    dist_high = (close - donch_high) / safe_atr
    dist_low = (close - donch_low) / safe_atr

    ema_dist_10 = (close - ema10) / safe_close
    ema_dist_20 = (close - ema20) / safe_close
    ema_dist_50 = (close - ema50) / safe_close

    # EMA alignment: +1 if 10>20>50, -1 if 10<20<50, 0 mixed
    ema_align = np.where(
        (ema10 > ema20) & (ema20 > ema50), 1.0,
        np.where((ema10 < ema20) & (ema20 < ema50), -1.0, 0.0)
    )

    # MACD normalized
    macd_norm = macd / safe_atr
    # Approximate MACD histogram (signal line via EMA of MACD)
    macd_signal = _ema(macd, 9)
    macd_hist = macd - macd_signal
    macd_hist_norm = macd_hist / safe_atr

    # Funding features
    funding_ma8 = rolling_mean(funding, 8)
    funding_ma48 = rolling_mean(funding, 48)

    # Returns at multiple horizons
    ret_4 = np.zeros(n); ret_4[4:] = (close[4:] - close[:-4]) / np.maximum(close[:-4], 1e-10)
    ret_12 = np.zeros(n); ret_12[12:] = (close[12:] - close[:-12]) / np.maximum(close[:-12], 1e-10)
    ret_24 = np.zeros(n); ret_24[24:] = (close[24:] - close[:-24]) / np.maximum(close[:-24], 1e-10)
    ret_48 = np.zeros(n); ret_48[48:] = (close[48:] - close[:-48]) / np.maximum(close[:-48], 1e-10)
    ret_168 = np.zeros(n); ret_168[168:] = (close[168:] - close[:-168]) / np.maximum(close[:-168], 1e-10)

    # RSI momentum
    rsi_shift = np.zeros(n); rsi_shift[24:] = rsi[:-24]
    rsi_mom = rsi - rsi_shift

    # Volatility at multiple windows
    log_ret = np.log(np.maximum(close[1:] / np.maximum(close[:-1], 1e-10), 1e-10))
    log_ret = np.concatenate([[0.0], log_ret])
    vol_5 = _rolling_std(log_ret, 5)
    vol_50 = _rolling_std(log_ret, 50)
    vol_5 = np.nan_to_num(vol_5, nan=0.01)
    vol_50 = np.nan_to_num(vol_50, nan=0.01)
    vol_ratio_5_20 = vol_5 / np.maximum(vol_20, 1e-10)

    # Wick ratio
    upper_wick = high - np.maximum(close, opn)
    wick_ratio = upper_wick / candle_range

    # Consecutive bars (same direction)
    consec = np.zeros(n)
    for i in range(1, n):
        if ret_1[i] > 0 and ret_1[i-1] > 0:
            consec[i] = consec[i-1] + 1
        elif ret_1[i] < 0 and ret_1[i-1] < 0:
            consec[i] = consec[i-1] - 1
        else:
            consec[i] = 0

    # BTC features (from market data, aligned to this token's length)
    btc_ret_1h = _align_market(btc_data.get('ret_1h'), n, bar_idx_offset)
    btc_ret_24h = _align_market(btc_data.get('ret_24h'), n, bar_idx_offset)
    btc_vol_24h = _align_market(btc_data.get('vol_24h'), n, bar_idx_offset)

    # Market context (aligned)
    dispersion_zscore = _align_market(market_data.get('dispersion_zscore'), n, bar_idx_offset)
    market_breadth = _align_market(market_data.get('breadth_ema20'), n, bar_idx_offset)
    market_funding = _align_market(market_data.get('funding_mean'), n, bar_idx_offset)
    market_regime_arr = _align_market(market_data.get('regime'), n, bar_idx_offset)

    # Cross-sectional ranks (aligned)
    xsect_ret1h = _align_market(cross_sect_data.get(ctx.ticker, {}).get('ret1h_rank'), n, bar_idx_offset)
    xsect_ret24h = _align_market(cross_sect_data.get(ctx.ticker, {}).get('ret24h_rank'), n, bar_idx_offset)
    xsect_rsi = _align_market(cross_sect_data.get(ctx.ticker, {}).get('rsi_rank'), n, bar_idx_offset)
    xsect_vol = _align_market(cross_sect_data.get(ctx.ticker, {}).get('vol_rank'), n, bar_idx_offset)
    rel_strength = _align_market(cross_sect_data.get(ctx.ticker, {}).get('rel_strength_24h'), n, bar_idx_offset)

    # BTC correlation
    token_btc_corr = np.zeros(n)
    if btc_data.get('close') is not None:
        btc_close = _align_market(btc_data.get('close'), n, bar_idx_offset)
        btc_log_ret = np.log(np.maximum(btc_close[1:] / np.maximum(btc_close[:-1], 1e-10), 1e-10))
        btc_log_ret = np.concatenate([[0.0], btc_log_ret])
        # Rolling 168h correlation
        for i in range(168, n):
            a = log_ret[i-168:i]
            b = btc_log_ret[i-168:i]
            c = np.corrcoef(a, b)[0, 1]
            token_btc_corr[i] = 0.0 if np.isnan(c) else c

    # ── Stack features in model order ─────────────────────────────
    # Feature order must match training!
    features = np.column_stack([
        adx, atr_pct, bb_pct, bb_width, body_pct,
        btc_ret_1h, btc_ret_24h, btc_vol_24h,
        consec, dispersion_zscore, dist_high, dist_low, donch_pos,
        ema_align, ema_dist_10, ema_dist_20, ema_dist_50,
        funding, funding_ma48, funding_ma8,
        macd_hist_norm, macd_norm,
        market_breadth, market_funding, market_regime_arr,
        minus_di, plus_di,
        rel_strength,
        ret_1, ret_12, ret_168, ret_24, ret_4, ret_48,
        rsi, rsi_mom,
        token_btc_corr,
        vol_20, vol_5, vol_50, vol_ratio, vol_ratio_5_20,
        wick_ratio,
        xsect_ret1h, xsect_ret24h, xsect_rsi, xsect_vol,
    ])  # shape (n, 47)

    return features, regime, funding_ma48


def _align_market(arr, target_len, offset):
    """Align market-level array to token-level array using offset."""
    if arr is None:
        return np.zeros(target_len)
    result = np.zeros(target_len)
    src_start = max(0, offset)
    dst_start = max(0, -offset)
    copy_len = min(len(arr) - src_start, target_len - dst_start)
    if copy_len > 0:
        result[dst_start:dst_start + copy_len] = arr[src_start:src_start + copy_len]
    return result


def strategy(contexts: dict) -> dict:
    """ML-adaptive regime strategy with turbulence detection."""

    # ── Load ML model ─────────────────────────────────────────────
    try:
        model = joblib.load(MODEL_PATH)
        feature_names = joblib.load(FEATURES_PATH)
    except Exception as e:
        print(f"ML model load failed: {e}")
        return {}

    token_list = sorted(contexts.keys())
    if len(token_list) < 20:
        return {}

    # ── Step 1: Extract token data ────────────────────────────────
    token_data = {}
    btc_ctx = None

    for token in token_list:
        ctx_pair = contexts[token]
        if isinstance(ctx_pair, tuple):
            ctx_spot, ctx_perp = ctx_pair
            ctx = ctx_perp if ctx_perp is not None else ctx_spot
        else:
            ctx = ctx_pair

        close = ctx.ind_1h['close']
        n = len(close)
        if n < 600:
            continue

        token_data[token] = {'ctx': ctx, 'n': n}
        if token == 'BTC':
            btc_ctx = ctx

    eligible = sorted(token_data.keys())
    if len(eligible) < 20:
        return {}

    max_bars = max(token_data[t]['n'] for t in eligible)

    # ── Step 2: Compute market-level features ─────────────────────
    # BTC data
    btc_data = {}
    if btc_ctx is not None:
        btc_close = btc_ctx.ind_1h['close']
        btc_n = len(btc_close)
        btc_ret_1h = btc_ctx.ind_1h.get('ret_1', np.zeros(btc_n))
        btc_ret_24h = np.zeros(btc_n)
        btc_ret_24h[24:] = (btc_close[24:] - btc_close[:-24]) / np.maximum(btc_close[:-24], 1e-10)
        btc_log_ret = np.log(np.maximum(btc_close[1:] / np.maximum(btc_close[:-1], 1e-10), 1e-10))
        btc_log_ret = np.concatenate([[0.0], btc_log_ret])
        btc_vol_24h = _rolling_std(btc_log_ret, 24)
        btc_vol_24h = np.nan_to_num(btc_vol_24h, nan=0.01)

        btc_data = {
            'close': btc_close,
            'ret_1h': btc_ret_1h,
            'ret_24h': btc_ret_24h,
            'vol_24h': btc_vol_24h,
        }

    # Market breadth, funding mean, dispersion
    market_data = {}

    # Build return + funding + rsi + vol matrices for cross-sectional features
    all_ret1h = np.full((max_bars, len(eligible)), np.nan)
    all_ret24h = np.full((max_bars, len(eligible)), np.nan)
    all_rsi = np.full((max_bars, len(eligible)), np.nan)
    all_vol = np.full((max_bars, len(eligible)), np.nan)
    all_close = np.full((max_bars, len(eligible)), np.nan)
    all_ema20 = np.full((max_bars, len(eligible)), np.nan)
    all_funding = np.full((max_bars, len(eligible)), np.nan)

    offsets = {}
    for j, token in enumerate(eligible):
        ctx = token_data[token]['ctx']
        n = token_data[token]['n']
        offset = max_bars - n
        offsets[token] = offset

        ret1 = ctx.ind_1h.get('ret_1', np.zeros(n))
        all_ret1h[offset:offset+n, j] = ret1

        close = ctx.ind_1h['close']
        ret24 = np.zeros(n)
        ret24[24:] = (close[24:] - close[:-24]) / np.maximum(close[:-24], 1e-10)
        all_ret24h[offset:offset+n, j] = ret24

        rsi = ctx.ind_1h.get('rsi', np.full(n, 50.0))
        all_rsi[offset:offset+n, j] = rsi

        vol = ctx.ind_1h.get('vol_20', np.full(n, 0.01))
        all_vol[offset:offset+n, j] = vol

        all_close[offset:offset+n, j] = close
        ema20 = ctx.ind_1h.get('ema_20', close)
        all_ema20[offset:offset+n, j] = ema20

        funding = ctx.funding_1h if ctx.funding_1h is not None else np.zeros(n)
        all_funding[offset:offset+n, j] = funding

    # Market breadth: fraction of tokens above EMA20
    above_ema = all_close > all_ema20
    valid_counts = np.sum(~np.isnan(all_close), axis=1)
    market_data['breadth_ema20'] = np.nansum(above_ema, axis=1) / np.maximum(valid_counts, 1)

    # Market funding mean
    market_data['funding_mean'] = np.nanmean(all_funding, axis=1)

    # Market regime (from BTC if available)
    if btc_ctx is not None:
        btc_offset = max_bars - len(btc_ctx.regime_1h)
        regime_arr = np.full(max_bars, RANGE)
        regime_arr[btc_offset:] = btc_ctx.regime_1h
        market_data['regime'] = regime_arr.astype(float)
    else:
        market_data['regime'] = np.full(max_bars, float(RANGE))

    # Cross-sectional dispersion
    disp = np.zeros(max_bars)
    for i in range(DISP_LOOKBACK, max_bars):
        window = all_ret1h[i-DISP_LOOKBACK:i, :]
        valid = np.sum(~np.isnan(window), axis=1) >= 5
        if np.any(valid):
            disp[i] = np.nanmean(np.nanstd(window, axis=1)[valid])
    disp_mu = pd.Series(disp).rolling(DISP_WINDOW, min_periods=100).mean().values
    disp_std = pd.Series(disp).rolling(DISP_WINDOW, min_periods=100).std().values
    disp_std = np.maximum(np.nan_to_num(disp_std, nan=0.001), 1e-10)
    market_data['dispersion_zscore'] = (disp - np.nan_to_num(disp_mu)) / disp_std

    # Cross-sectional ranks (per bar)
    cross_sect_data = {token: {} for token in eligible}
    # Compute ranks at each bar (vectorized per bar is slow, but we can downsample)
    RANK_INTERVAL = 4  # Compute ranks every 4h, forward fill
    for i in range(200, max_bars, RANK_INTERVAL):
        ret1h_vals = all_ret1h[i, :]
        ret24h_vals = all_ret24h[i, :]
        rsi_vals = all_rsi[i, :]
        vol_vals = all_vol[i, :]

        valid = ~np.isnan(ret1h_vals)
        if np.sum(valid) < 10:
            continue

        n_valid = np.sum(valid)
        for metric, vals, key in [
            ('ret1h', ret1h_vals, 'ret1h_rank'),
            ('ret24h', ret24h_vals, 'ret24h_rank'),
            ('rsi', rsi_vals, 'rsi_rank'),
            ('vol', vol_vals, 'vol_rank'),
        ]:
            ranks = np.full(len(eligible), np.nan)
            v = vals.copy()
            v[~valid] = np.nan
            order = np.argsort(np.nan_to_num(v, nan=-999))
            for rank_idx, j in enumerate(order):
                if valid[j]:
                    ranks[j] = rank_idx / max(n_valid - 1, 1)

            for j, token in enumerate(eligible):
                if token not in cross_sect_data:
                    cross_sect_data[token] = {}
                if key not in cross_sect_data[token]:
                    cross_sect_data[token][key] = np.full(max_bars, 0.5)
                end = min(i + RANK_INTERVAL, max_bars)
                cross_sect_data[token][key][i:end] = ranks[j] if not np.isnan(ranks[j]) else 0.5

        # Relative strength (ret24h vs median)
        med_ret24 = np.nanmedian(ret24h_vals[valid])
        for j, token in enumerate(eligible):
            if 'rel_strength_24h' not in cross_sect_data[token]:
                cross_sect_data[token]['rel_strength_24h'] = np.zeros(max_bars)
            if valid[j]:
                end = min(i + RANK_INTERVAL, max_bars)
                cross_sect_data[token]['rel_strength_24h'][i:end] = ret24h_vals[j] - med_ret24

    # Dispersion sizing multiplier
    disp_median = pd.Series(disp).rolling(DISP_WINDOW, min_periods=100).median().values
    disp_ratio = np.where(
        np.nan_to_num(disp_median, nan=1.0) > 0,
        disp / np.maximum(np.nan_to_num(disp_median, nan=1.0), 1e-10),
        1.0
    )
    disp_sizing = np.clip(disp_ratio, DISP_FLOOR, DISP_CEIL)

    # ── Step 3: Compute features & predict per token ──────────────
    results = {}

    for token in eligible:
        d = token_data[token]
        ctx = d['ctx']
        n = d['n']
        offset = offsets[token]

        try:
            features, regime, funding_ma48 = _compute_features_for_token(
                ctx, btc_data, market_data, cross_sect_data, offset
            )
        except Exception as e:
            continue

        # Replace NaN/Inf
        features = np.nan_to_num(features, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)

        # Skip warmup
        valid_start = 250
        if n <= valid_start:
            continue

        # Predict in batch for efficiency
        try:
            proba = model.predict_proba(features[valid_start:])
        except Exception:
            continue

        # Model classes: [-1, 1] → SHORT, LONG
        classes = model.classes_
        long_idx = np.where(classes == 1)[0]
        short_idx = np.where(classes == -1)[0]

        if len(long_idx) == 0 or len(short_idx) == 0:
            continue

        long_prob = np.zeros(n)
        short_prob = np.zeros(n)
        long_prob[valid_start:] = proba[:, long_idx[0]]
        short_prob[valid_start:] = proba[:, short_idx[0]]

        # ── Entry logic ───────────────────────────────────────────
        entry_mask = np.zeros(n, dtype=bool)
        direction = np.zeros(n, dtype=np.int8)
        size_mult = np.ones(n, dtype=np.float64)

        for i in range(valid_start, n):
            regime_val = int(regime[i])

            # LONG entry
            if (long_prob[i] >= ML_LONG_THRESHOLD and
                regime_val in ALLOW_LONG_REGIMES):
                entry_mask[i] = True
                direction[i] = 1
                # Conviction-based sizing
                conv = (long_prob[i] - ML_LONG_THRESHOLD) / (1.0 - ML_LONG_THRESHOLD)
                size_mult[i] = 1.0 + conv * 2.0  # 1x to 3x based on conviction

            # SHORT entry (higher threshold)
            elif (short_prob[i] >= ML_SHORT_THRESHOLD and
                  regime_val in ALLOW_SHORT_REGIMES):
                entry_mask[i] = True
                direction[i] = -1
                conv = (short_prob[i] - ML_SHORT_THRESHOLD) / (1.0 - ML_SHORT_THRESHOLD)
                size_mult[i] = 1.0 + conv * 1.5  # 1x to 2.5x

        if not np.any(entry_mask):
            continue

        # ── Carry overlay: boost when funding agrees ──────────────
        if funding_ma48 is not None:
            for i in range(valid_start, n):
                if entry_mask[i]:
                    f = funding_ma48[i]
                    # Long + negative funding = collecting carry = boost
                    if direction[i] == 1 and f < -0.00003:
                        size_mult[i] *= FUNDING_BOOST
                    # Short + positive funding = collecting carry = boost
                    elif direction[i] == -1 and f > 0.00003:
                        size_mult[i] *= FUNDING_BOOST

        # ── Dispersion sizing ─────────────────────────────────────
        token_disp = disp_sizing[offset:offset + n]
        size_mult *= token_disp

        # Cap
        size_mult = np.minimum(size_mult, 8.0)

        # ── Liquidity mask ────────────────────────────────────────
        liq = ctx.liquidity_mask
        if liq is not None:
            entry_mask &= liq

        # Warmup guard
        entry_mask[:300] = False

        if not np.any(entry_mask):
            continue

        # Dynamic leverage based on average conviction
        avg_conv = np.mean(size_mult[entry_mask])
        leverage = min(BASE_LEVERAGE * (avg_conv / 2.0), MAX_LEVERAGE)

        results[token] = StrategyResult(
            entry_mask=entry_mask,
            direction=direction,
            market_type=MarketType.PERP,
            leverage=leverage,
            stop_mult=STOP_MULT,
            trail_mult=TRAIL_MULT,
            target_mult=999,
            no_stop_bars=NO_STOP_BARS,
            min_hold=MIN_HOLD,
            max_hold=MAX_HOLD,
            edge=EDGE,
            exit_regimes={CRISIS},
            exchange='binance',
            name='s220_ml_adaptive',
            breakeven_atr=BREAKEVEN_ATR,
            size_multiplier=size_mult,
            cap_multiplier=CAP_MULTIPLIER,
        )

    return results
