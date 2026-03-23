"""
s200 Adaptive Carry + Selective Momentum — V4 Portfolio Strategy
================================================================
Class B (Portfolio): gate path 0->2->3P->5P->6->7

Core hypothesis: Carry is the only proven real-money edge (s65 paper trading:
+$10.4K, 48% WR, 2.2:1 payoff). Momentum LOSES in current market (s60 paper:
-$32K). This strategy:
  1. Uses carry as the PRIMARY edge (funding imbalance harvesting)
  2. Adds momentum ONLY when turbulence is low + dispersion is high
  3. Replaces daily regime detection (24h lag!) with:
     a. Turbulence Index (Mahalanobis distance) — 1-bar lag crisis detection
     b. CUSUM change-point detection — 1-5 bar transition alerts
  4. Uses cross-sectional dispersion as CONTINUOUS sizing signal
  5. Ranks tokens by funding opportunity quality (cross-sectional carry)

Target: >300% annual, <20% DD, Calmar >3
Market: PERP (bidirectional)
Status: EXPERIMENTAL
"""

import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_mean, _rolling_std)

STRATEGY_TYPE = "portfolio"

# ── Configuration ─────────────────────────────────────────────────
# Carry parameters
FUNDING_FAST_WINDOW = 24          # 24h fast funding MA (faster entry than s65's 72h)
FUNDING_SLOW_WINDOW = 168         # 7d slow funding MA (trend confirmation)
FUNDING_ENTRY_THRESHOLD = 0.00002 # Lower threshold for more trades
FUNDING_STRONG_THRESHOLD = 0.00007 # Strong funding → bigger size

# Momentum parameters (only used when conditions are very favorable)
MOM_RET_THRESHOLD = 0.025         # 2.5% burst (lower than s60's 3% → more entries)
MOM_ADX_THRESHOLD = 22            # ADX > 22 for trend (lower than 25)
MOM_MAX_TURBULENCE = 0.7          # Only enter momentum when turbulence < 70th pctile

# Turbulence parameters
TURB_BASKET_SIZE = 15             # Top 15 tokens for turbulence computation
TURB_COV_WINDOW = 500             # 500 hourly bars (~21 days) covariance window
TURB_CRISIS_PERCENTILE = 0.90     # Turbulence > 90th pctile = crisis
TURB_CALM_PERCENTILE = 0.50       # Turbulence < 50th pctile = calm (safe for momentum)

# CUSUM parameters
CUSUM_SLACK = 0.5                 # Slack parameter (filters minor fluctuations)
CUSUM_THRESHOLD = 4.0             # Change-point detection threshold
CUSUM_WINDOW = 72                 # 72h rolling mean/std for standardization

# Dispersion parameters
DISP_LOOKBACK = 24                # 24h return dispersion
DISP_MEDIAN_WINDOW = 336          # 14-day median for normalization
DISP_LOW = 0.5                    # Low dispersion → reduce sizing (0.5x)
DISP_HIGH = 1.5                   # High dispersion → boost sizing (1.5x)

# Position management
MAX_CARRY_POSITIONS = 12          # More carry positions (s65 had too few trades)
MAX_MOM_POSITIONS = 5             # Limited momentum (only high-conviction)
MIN_ADV_USD = 1_000_000           # Broader universe ($1M ADV, down from s100's $2M)
CONCENTRATION_PER_TOKEN = 0.15    # Max 15% per token

# Trade management
CARRY_STOP = 4.0                  # 4x ATR stop for carry
CARRY_TRAIL = 1.5                 # 1.5x ATR trail (proven)
CARRY_NO_STOP = 36                # 36h protection (shorter than s65's 48h for faster feedback)
CARRY_MAX_HOLD = 336              # 14 days max hold
CARRY_EDGE = 0.30

MOM_STOP = 3.0                    # 3x ATR stop for momentum
MOM_TRAIL = 1.5                   # 1.5x ATR trail (proven)
MOM_NO_STOP = 18                  # 18h protection (shorter → faster exit in adverse moves)
MOM_MAX_HOLD = 168                # 7 days max hold
MOM_EDGE = 0.40


def _compute_turbulence_index(ret_matrix, window=TURB_COV_WINDOW):
    """Compute Mahalanobis-distance turbulence index (Kritzman & Li 2010).

    Turbulence measures how unusual the current cross-asset return vector is
    relative to historical norms. High turbulence = correlation breakdown = crisis.

    Uses regularized covariance (shrinkage) for numerical stability.
    """
    n_bars, n_assets = ret_matrix.shape
    turb = np.zeros(n_bars, dtype=np.float64)

    for i in range(window, n_bars):
        # Rolling window of returns
        R = ret_matrix[i - window:i, :]

        # Skip if too many NaNs
        valid_cols = np.sum(~np.isnan(R), axis=0) > window * 0.5
        if np.sum(valid_cols) < 5:
            continue

        R_clean = R[:, valid_cols].copy()
        R_clean = np.nan_to_num(R_clean, nan=0.0)

        mu = np.mean(R_clean, axis=0)
        r_t = ret_matrix[i, valid_cols].copy()
        r_t = np.nan_to_num(r_t, nan=0.0)

        # Regularized covariance (Ledoit-Wolf shrinkage approximation)
        cov = np.cov(R_clean, rowvar=False)
        n_cols = cov.shape[0]

        # Shrink toward diagonal (simple but effective)
        shrinkage = 0.1
        cov_reg = (1 - shrinkage) * cov + shrinkage * np.diag(np.diag(cov))

        try:
            cov_inv = np.linalg.inv(cov_reg)
            diff = r_t - mu
            # Mahalanobis distance
            turb[i] = float(diff @ cov_inv @ diff) / n_cols
        except np.linalg.LinAlgError:
            turb[i] = turb[i - 1] if i > 0 else 0.0

    return turb


def _compute_cusum(returns, window=CUSUM_WINDOW, slack=CUSUM_SLACK, threshold=CUSUM_THRESHOLD):
    """CUSUM change-point detection on returns (Page 1954).

    Detects sustained shifts in mean return level. Returns:
    - cusum_up: detects positive mean shifts
    - cusum_down: detects negative mean shifts
    - change_points: boolean array where change detected
    """
    n = len(returns)
    cusum_up = np.zeros(n, dtype=np.float64)
    cusum_down = np.zeros(n, dtype=np.float64)
    change_points = np.zeros(n, dtype=bool)

    # Rolling standardization
    ret_clean = np.nan_to_num(returns, nan=0.0)
    mu = pd.Series(ret_clean).rolling(window, min_periods=window // 2).mean().values
    sigma = pd.Series(ret_clean).rolling(window, min_periods=window // 2).std().values
    sigma = np.maximum(sigma, 1e-10)

    z = (ret_clean - np.nan_to_num(mu, nan=0.0)) / np.nan_to_num(sigma, nan=1.0)

    for i in range(1, n):
        cusum_up[i] = max(0.0, cusum_up[i - 1] + z[i] - slack)
        cusum_down[i] = max(0.0, cusum_down[i - 1] - z[i] - slack)

        if cusum_up[i] > threshold or cusum_down[i] > threshold:
            change_points[i] = True
            cusum_up[i] = 0.0
            cusum_down[i] = 0.0

    return cusum_up, cusum_down, change_points


def _compute_dispersion(ret_matrix, lookback=DISP_LOOKBACK):
    """Cross-sectional return dispersion — continuous sizing signal."""
    n_bars = ret_matrix.shape[0]
    dispersion = np.zeros(n_bars, dtype=np.float64)

    for i in range(lookback, n_bars):
        window = ret_matrix[i - lookback:i, :]
        valid = np.sum(~np.isnan(window), axis=1)
        bar_stds = np.nanstd(window, axis=1)
        mask = valid >= 5
        if np.any(mask):
            dispersion[i] = np.mean(bar_stds[mask])

    return dispersion


def _rank_funding_opportunities(token_data, eligible_tokens, max_bars, bar_idx):
    """Rank tokens by carry opportunity quality at a given bar.

    Quality = funding magnitude × funding persistence × inverse volatility.
    Positive funding → short opportunity. Negative funding → long opportunity.
    """
    opportunities = []

    for token in eligible_tokens:
        d = token_data[token]
        n = d['n']
        offset = max_bars - n
        local_bar = bar_idx - offset

        if local_bar < 0 or local_bar >= n:
            continue

        funding_fast = d.get('funding_fast')
        funding_slow = d.get('funding_slow')
        if funding_fast is None or funding_slow is None:
            continue

        ff = funding_fast[local_bar]
        fs = funding_slow[local_bar]

        if abs(ff) < FUNDING_ENTRY_THRESHOLD:
            continue

        # Both fast and slow must agree on direction
        if np.sign(ff) != np.sign(fs):
            continue

        # Quality score: magnitude × persistence × vol-adjusted
        vol = d['vol'][local_bar] if d['vol'] is not None else 0.01
        vol = max(vol, 1e-6)

        quality = abs(ff) * abs(fs) / vol * 1e6  # Scale for readability
        direction = -1 if ff > 0 else 1  # Opposite to funding

        opportunities.append((token, quality, direction, local_bar))

    # Sort by quality (best first)
    opportunities.sort(key=lambda x: -x[1])
    return opportunities


def _rank_momentum_candidates(token_data, eligible_tokens, max_bars, bar_idx,
                               turbulence_level, dispersion_level):
    """Rank tokens by momentum signal strength with MACD confirmation.

    Uses s98's proven MACD zero-cross + BB squeeze + DI alignment pattern.
    Only called when turbulence is low and dispersion is high.
    """
    candidates = []

    for token in eligible_tokens:
        d = token_data[token]
        n = d['n']
        offset = max_bars - n
        local_bar = bar_idx - offset

        if local_bar < 2 or local_bar >= n:
            continue

        ret_1h = d['ret_1h'][local_bar]
        adx = d['adx'][local_bar]
        close = d['close'][local_bar]
        ema20 = d['ema20'][local_bar]
        ema50 = d['ema50'][local_bar]
        vol_ratio = d['vol_ratio'][local_bar]
        macd = d.get('macd')
        plus_di = d.get('plus_di')
        minus_di = d.get('minus_di')
        bb_width = d.get('bb_width')

        # Must have trend and volume
        if adx < MOM_ADX_THRESHOLD or vol_ratio < 0.7:
            continue

        # MACD zero-cross detection (from s98)
        macd_cross_up = False
        macd_cross_down = False
        if macd is not None and local_bar >= 2:
            macd_cross_up = macd[local_bar] > 0 and macd[local_bar - 1] <= 0
            macd_cross_down = macd[local_bar] < 0 and macd[local_bar - 1] >= 0

        # BB squeeze detection (volatility compression → breakout)
        bb_squeeze = False
        if bb_width is not None and local_bar >= 240:
            bb_avg = np.mean(bb_width[max(0, local_bar - 240):local_bar])
            bb_squeeze = bb_width[local_bar] < bb_avg * 0.8

        # DI alignment
        di_long = plus_di is not None and minus_di is not None and plus_di[local_bar] > minus_di[local_bar]
        di_short = plus_di is not None and minus_di is not None and minus_di[local_bar] > plus_di[local_bar]

        # Combined entry: momentum burst + MACD confirmation OR BB squeeze
        has_macd = macd_cross_up or macd_cross_down
        has_squeeze = bb_squeeze
        has_burst = abs(ret_1h) > MOM_RET_THRESHOLD

        if not (has_burst or has_macd or has_squeeze):
            continue

        # Direction
        if (ret_1h > 0 or macd_cross_up) and close > ema20 and (di_long or plus_di is None):
            direction = 1
            signal_strength = abs(ret_1h) * adx / 25.0
            # Boost for MACD confirmation
            if macd_cross_up:
                signal_strength *= 1.5
            if bb_squeeze:
                signal_strength *= 1.3
        elif (ret_1h < 0 or macd_cross_down) and close < ema20 and (di_short or minus_di is None):
            direction = -1
            signal_strength = abs(ret_1h) * adx / 25.0
            if macd_cross_down:
                signal_strength *= 1.5
            if bb_squeeze:
                signal_strength *= 1.3
        else:
            continue

        quality = signal_strength * dispersion_level
        candidates.append((token, quality, direction, local_bar))

    candidates.sort(key=lambda x: -x[1])
    return candidates[:MAX_MOM_POSITIONS]


def strategy(contexts: dict) -> dict:
    """Adaptive carry + selective momentum with turbulence-based regime."""

    token_list = sorted(contexts.keys())
    if len(token_list) < 20:
        return {}

    # ── Step 1: Build token data ──────────────────────────────────
    token_data = {}
    btc_data = None

    for token in token_list:
        ctx_pair = contexts[token]
        if isinstance(ctx_pair, tuple):
            ctx_spot, ctx_perp = ctx_pair
            ctx = ctx_perp if ctx_perp is not None else ctx_spot
        else:
            ctx = ctx_pair

        close = ctx.ind_1h['close']
        n = len(close)
        if n < 600:  # Need at least 25 days
            continue

        funding = ctx.funding_1h
        ret_1h = ctx.ind_1h.get('ret_1')
        adx = ctx.ind_1h.get('adx')
        ema20 = ctx.ind_1h.get('ema_20')
        ema50 = ctx.ind_1h.get('ema_50')
        vol_ratio = ctx.ind_1h.get('vol_ratio')
        vol_20 = ctx.ind_1h.get('vol_20')
        regime = ctx.regime_1h
        macd = ctx.ind_1h.get('macd')
        plus_di = ctx.ind_1h.get('plus_di')
        minus_di = ctx.ind_1h.get('minus_di')
        bb_width = ctx.ind_1h.get('bb_width')

        # Compute fast and slow funding averages
        funding_fast = None
        funding_slow = None
        if funding is not None:
            funding_fast = rolling_mean(funding, FUNDING_FAST_WINDOW)
            funding_slow = rolling_mean(funding, FUNDING_SLOW_WINDOW)

        d = {
            'close': close,
            'n': n,
            'ret_1h': ret_1h if ret_1h is not None else np.zeros(n),
            'adx': adx if adx is not None else np.zeros(n),
            'ema20': ema20 if ema20 is not None else close,
            'ema50': ema50 if ema50 is not None else close,
            'vol_ratio': vol_ratio if vol_ratio is not None else np.ones(n),
            'vol': vol_20,
            'macd': macd,
            'plus_di': plus_di,
            'minus_di': minus_di,
            'bb_width': bb_width,
            'regime': regime,
            'funding': funding,
            'funding_fast': funding_fast,
            'funding_slow': funding_slow,
            'ctx': ctx,
            'ctx_pair': ctx_pair,
        }
        token_data[token] = d

        if token == 'BTC':
            btc_data = d

    eligible_tokens = sorted(token_data.keys())
    if len(eligible_tokens) < 20:
        return {}

    # ── Step 2: Compute market-level signals ──────────────────────
    max_bars = max(d['n'] for d in token_data.values())

    # Select top tokens by data availability for turbulence basket
    basket_tokens = sorted(
        [t for t in eligible_tokens if token_data[t]['n'] > max_bars * 0.8],
        key=lambda t: -token_data[t]['n']
    )[:TURB_BASKET_SIZE]

    # Build return matrix for turbulence + dispersion
    n_basket = len(basket_tokens)
    ret_matrix = np.full((max_bars, n_basket), np.nan, dtype=np.float64)
    for j, token in enumerate(basket_tokens):
        d = token_data[token]
        n = d['n']
        offset = max_bars - n
        ret_matrix[offset:offset + n, j] = d['ret_1h']

    # Full return matrix for dispersion (all tokens)
    n_all = len(eligible_tokens)
    full_ret_matrix = np.full((max_bars, n_all), np.nan, dtype=np.float64)
    for j, token in enumerate(eligible_tokens):
        d = token_data[token]
        n = d['n']
        offset = max_bars - n
        full_ret_matrix[offset:offset + n, j] = d['ret_1h']

    # Turbulence Index
    turbulence = _compute_turbulence_index(ret_matrix, window=TURB_COV_WINDOW)

    # Rolling turbulence percentiles for regime classification
    turb_series = pd.Series(turbulence)
    turb_p90 = turb_series.expanding(min_periods=200).quantile(0.90).values
    turb_p50 = turb_series.expanding(min_periods=200).quantile(0.50).values

    # Turbulence regime: crisis if > 90th pctile, calm if < 50th
    turb_crisis = turbulence > np.nan_to_num(turb_p90, nan=999)
    turb_calm = turbulence < np.nan_to_num(turb_p50, nan=0)

    # CUSUM on BTC returns for transition detection
    btc_ret = np.zeros(max_bars, dtype=np.float64)
    if btc_data is not None:
        offset = max_bars - btc_data['n']
        btc_ret[offset:] = btc_data['ret_1h']

    _, _, btc_change_points = _compute_cusum(btc_ret)

    # Cross-sectional dispersion
    dispersion = _compute_dispersion(full_ret_matrix)
    disp_median = pd.Series(dispersion).rolling(
        DISP_MEDIAN_WINDOW, min_periods=DISP_MEDIAN_WINDOW // 2
    ).median().values

    # Normalized dispersion for sizing (0.5 to 1.5 range)
    disp_ratio = np.where(
        np.nan_to_num(disp_median, nan=1.0) > 0,
        dispersion / np.maximum(np.nan_to_num(disp_median, nan=1.0), 1e-10),
        1.0
    )
    disp_sizing = np.clip(disp_ratio, DISP_LOW, DISP_HIGH)

    # ── Step 3: Generate signals per bar ──────────────────────────
    # We'll scan every bar for carry opportunities and periodically for momentum

    results = {}

    # Pre-allocate per-token result arrays
    for token in eligible_tokens:
        d = token_data[token]
        n = d['n']
        d['entry_mask'] = np.zeros(n, dtype=bool)
        d['direction_arr'] = np.zeros(n, dtype=np.int8)
        d['size_mult'] = np.ones(n, dtype=np.float64)
        d['is_carry'] = np.zeros(n, dtype=bool)
        d['is_momentum'] = np.zeros(n, dtype=bool)

    # Scan every CARRY_CHECK_INTERVAL bars for carry opportunities
    CARRY_CHECK_INTERVAL = 4  # Check every 4 hours
    MOM_CHECK_INTERVAL = 1    # Check every hour for momentum

    warmup = max(TURB_COV_WINDOW + 50, DISP_MEDIAN_WINDOW + 50)

    for bar_idx in range(warmup, max_bars):
        # Skip if turbulence indicates crisis
        if turb_crisis[bar_idx]:
            continue

        # Skip if recent CUSUM change point (transition period — wait for clarity)
        recent_change = np.any(btc_change_points[max(0, bar_idx - 6):bar_idx])
        if recent_change:
            continue

        # Get current dispersion sizing multiplier
        current_disp = disp_sizing[bar_idx]

        # ── CARRY ENTRIES (every 4h) ──────────────────────────────
        if bar_idx % CARRY_CHECK_INTERVAL == 0:
            carry_opps = _rank_funding_opportunities(
                token_data, eligible_tokens, max_bars, bar_idx
            )

            for token, quality, direction, local_bar in carry_opps[:MAX_CARRY_POSITIONS]:
                d = token_data[token]
                if d['entry_mask'][local_bar]:
                    continue  # Already has entry signal

                d['entry_mask'][local_bar] = True
                d['direction_arr'][local_bar] = direction
                d['is_carry'][local_bar] = True

                # Sizing: quality × dispersion × regime
                funding_mag = abs(d['funding_fast'][local_bar])
                fund_size = 1.0
                if funding_mag > FUNDING_STRONG_THRESHOLD:
                    fund_size = 1.5
                elif funding_mag > FUNDING_ENTRY_THRESHOLD * 2:
                    fund_size = 1.2

                d['size_mult'][local_bar] = fund_size * current_disp

        # ── MOMENTUM ENTRIES (every hour, only when calm + high dispersion) ──
        if turb_calm[bar_idx] and current_disp > 1.0:
            mom_candidates = _rank_momentum_candidates(
                token_data, eligible_tokens, max_bars, bar_idx,
                turbulence[bar_idx], current_disp
            )

            for token, quality, direction, local_bar in mom_candidates:
                d = token_data[token]
                if d['entry_mask'][local_bar]:
                    continue

                d['entry_mask'][local_bar] = True
                d['direction_arr'][local_bar] = direction
                d['is_momentum'][local_bar] = True
                d['size_mult'][local_bar] = quality * current_disp

    # ── Step 4: Build StrategyResults ─────────────────────────────
    for token in eligible_tokens:
        d = token_data[token]
        n = d['n']

        if not np.any(d['entry_mask']):
            continue

        # Warmup guard
        d['entry_mask'][:200] = False

        # Liquidity mask
        liq = d['ctx'].liquidity_mask
        if liq is not None:
            d['entry_mask'] &= liq

        if not np.any(d['entry_mask']):
            continue

        # Determine trade params based on whether mostly carry or momentum
        carry_count = np.sum(d['is_carry'] & d['entry_mask'])
        mom_count = np.sum(d['is_momentum'] & d['entry_mask'])

        # Use carry params if majority carry, else momentum params
        if carry_count >= mom_count:
            stop = CARRY_STOP
            trail = CARRY_TRAIL
            no_stop = CARRY_NO_STOP
            max_hold = CARRY_MAX_HOLD
            edge = CARRY_EDGE
        else:
            stop = MOM_STOP
            trail = MOM_TRAIL
            no_stop = MOM_NO_STOP
            max_hold = MOM_MAX_HOLD
            edge = MOM_EDGE

        # Cap size multiplier — higher cap with leverage
        d['size_mult'] = np.minimum(d['size_mult'], 8.0)

        results[token] = StrategyResult(
            entry_mask=d['entry_mask'],
            direction=d['direction_arr'],
            market_type=MarketType.PERP,
            leverage=3.0,  # 3x leverage (carry can tolerate more than momentum)
            stop_mult=stop,
            trail_mult=trail,
            target_mult=999,
            no_stop_bars=no_stop,
            min_hold=12,
            max_hold=max_hold,
            edge=edge,
            exit_regimes={CRISIS},
            exchange='binance',
            name='s200_adaptive_carry_momentum',
            breakeven_atr=0.5,
            size_multiplier=d['size_mult'],
            cap_multiplier=10.0,  # More aggressive ADV cap
        )

    return results
