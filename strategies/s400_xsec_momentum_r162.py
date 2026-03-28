"""
s400 Cross-Sectional Momentum Rotation (R162 Faithful Reproduction)

Port of R168's R162 component to v4 engine. Original achieved:
  - Full: Sharpe 1.39, L12M +289.6%
  - Config: L=7d, N=7d rebalance, K=3 top/bottom, 80/20 L/S
  - Triple filter: EMA(10h/30h) regime, volume, ATR volatility

Class B portfolio strategy — ranks across all tokens simultaneously.
Entries/exits at weekly rebalance. No mechanical stops (hold to rebalance).

Market: PERP (for short capability)
Status: EXPERIMENTAL (R172 validated in standalone, porting to v4)
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, rolling_mean)

STRATEGY_TYPE = "portfolio"

# ── R162 Configuration (from R168 best variant) ─────────────────
LOOKBACK_DAYS = 7           # Return lookback for ranking
LOOKBACK_BARS = LOOKBACK_DAYS * 24  # 168 1H bars
REBALANCE_DAYS = 7          # Rebalance frequency
REBALANCE_BARS = REBALANCE_DAYS * 24  # 168 1H bars
K = 3                       # Top/bottom K tokens
LONG_WT = 0.80              # 80% to longs
SHORT_WT = 0.20             # 20% to shorts
EMA_FAST = 10               # hours (EMA span)
EMA_SLOW = 30               # hours (EMA span)
MIN_VOL_USD = 5_000_000     # Min daily dollar volume ($5M for liquidity)
WARMUP = 200                # Warmup bars before first signal
LEVERAGE = 2.5              # Matching R172 standalone

# ── Sizing overrides (read by portfolio_backtest.py) ─────────────
# Aggressive Kelly params so size_multiplier-based weights are not diluted.
# edge=1.0 in StrategyResult + kelly_mult=0.50 + target_vol=0.02 makes
# the Kelly formula produce positions close to (size_multiplier × equity).
# target_vol=0.02 keeps worst-case vol_adj = 0.02/0.005 = 4x (was 10x at 0.05).
# cap_pct=0.27 matches research allocation: 80%/3 longs = 26.7% per position.
SIZING_OVERRIDES = {
    "kelly_mult_override": 0.50,
    "target_vol": 0.02,
    "cap_pct_override": 0.27,
}

# ── Engine feature overrides (read by portfolio_backtest.py) ──────
# DD scaling disabled — too aggressive for weekly-rebalance cross-sectional
# strategy. Early losers trigger scaling that suppresses all subsequent entries.
# Re-enable with tuned thresholds after baseline validation.
DD_SCALING = []


def _ema(arr, span):
    """Compute EMA using numpy (vectorized)."""
    alpha = 2.0 / (span + 1)
    out = np.empty_like(arr, dtype=np.float64)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
    return out


def strategy(contexts):
    """Cross-sectional momentum: rank all tokens, long top K, short bottom K."""
    token_list = sorted(contexts.keys())
    if len(token_list) < 6:
        return {}

    # ── Build per-token data ─────────────────────────────────────
    token_data = {}
    for token in token_list:
        ctx_pair = contexts[token]
        if isinstance(ctx_pair, tuple):
            ctx = ctx_pair[1] if ctx_pair[1] is not None else ctx_pair[0]
        else:
            ctx = ctx_pair

        close = ctx.ind_1h["close"]
        # Require minimum data history (30 days + lookback + warmup)
        min_bars = LOOKBACK_BARS + WARMUP + 30 * 24
        n = len(close)
        if n < min_bars:
            continue

        # Compute trailing N-day return (shifted by 1 to avoid look-ahead)
        close_f64 = close.astype(np.float64)
        ret = np.full(n, np.nan, dtype=np.float64)
        ret[LOOKBACK_BARS + 1:] = (
            close_f64[LOOKBACK_BARS + 1:]
            / np.maximum(close_f64[1:n - LOOKBACK_BARS], 1e-10)
            - 1.0
        )

        # EMA regime signal (fast - slow on hourly close, shifted 1 bar)
        ema_fast = _ema(close_f64, EMA_FAST)
        ema_slow = _ema(close_f64, EMA_SLOW)
        ema_diff = ema_fast - ema_slow
        ema_diff_shifted = np.roll(ema_diff, 1)
        ema_diff_shifted[0] = 0.0

        # Rolling 30d avg dollar volume (shifted 1 bar, use 1H volume * close)
        vol_usd = ctx.ind_1h.get("volume", np.zeros(n)) * close_f64
        dvol_30d = rolling_mean(vol_usd, 24 * 30) * 24  # hourly avg * 24h = daily
        dvol_shifted = np.roll(dvol_30d, 1)
        dvol_shifted[0] = 0.0

        # ATR/price ratio (for volatility filter, shifted 1 bar)
        atr = ctx.ind_1h["atr"].astype(np.float64)
        atr_ratio = atr / np.maximum(close_f64, 1e-10)
        atr_ratio_shifted = np.roll(atr_ratio, 1)
        atr_ratio_shifted[0] = 0.0

        token_data[token] = {
            "close": close, "n": n, "ctx": ctx, "ctx_pair": ctx_pair,
            "ret": ret, "ema_diff": ema_diff_shifted,
            "dvol": dvol_shifted, "atr_ratio": atr_ratio_shifted,
        }

    eligible_tokens = sorted(token_data.keys())
    if len(eligible_tokens) < 2 * K:
        return {}

    # ── Right-align into matrices ────────────────────────────────
    max_bars = max(d["n"] for d in token_data.values())
    n_tokens = len(eligible_tokens)

    ret_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)
    ema_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)
    dvol_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)
    atr_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)

    offsets = {}
    for j, token in enumerate(eligible_tokens):
        d = token_data[token]
        n = d["n"]
        offset = max_bars - n
        offsets[token] = offset
        ret_matrix[offset:offset + n, j] = d["ret"]
        ema_matrix[offset:offset + n, j] = d["ema_diff"]
        dvol_matrix[offset:offset + n, j] = d["dvol"]
        atr_matrix[offset:offset + n, j] = d["atr_ratio"]

    # ── Compute rebalance schedule ───────────────────────────────
    first_bar = LOOKBACK_BARS + WARMUP
    rebalance_bars = list(range(first_bar, max_bars, REBALANCE_BARS))

    # Track which tokens are long/short at each rebalance
    long_membership = np.zeros((max_bars, n_tokens), dtype=bool)
    short_membership = np.zeros((max_bars, n_tokens), dtype=bool)
    long_weights = np.zeros((max_bars, n_tokens), dtype=np.float64)
    short_weights = np.zeros((max_bars, n_tokens), dtype=np.float64)

    for rb in rebalance_bars:
        rets = ret_matrix[rb, :]
        dvol = dvol_matrix[rb, :]
        ema = ema_matrix[rb, :]
        atr_r = atr_matrix[rb, :]

        # Volume filter: daily dollar volume > MIN_VOL_USD
        vol_ok = ~np.isnan(dvol) & (dvol >= MIN_VOL_USD)
        ret_ok = ~np.isnan(rets)
        eligible = vol_ok & ret_ok

        if np.sum(eligible) < 2 * K:
            continue

        eligible_idx = np.where(eligible)[0]
        eligible_rets = rets[eligible_idx]
        eligible_atr = atr_r[eligible_idx]

        # ATR volatility filter: keep only above-median ATR/price tokens
        valid_atr = ~np.isnan(eligible_atr) & (eligible_atr > 0)
        if np.sum(valid_atr) > 4:
            median_atr = np.nanmedian(eligible_atr[valid_atr])
            high_vol = eligible_atr >= median_atr
            eligible_idx = eligible_idx[high_vol]
            eligible_rets = rets[eligible_idx]

        if len(eligible_idx) < 2 * K:
            continue

        # Rank by return
        sorted_order = np.argsort(eligible_rets)
        top_k_idx = eligible_idx[sorted_order[-K:]]
        bottom_k_idx = eligible_idx[sorted_order[:K]]

        # EMA regime filter: only go long if EMA_fast > EMA_slow for that token
        top_k_ema = ema[top_k_idx]
        top_k_filtered = top_k_idx[~np.isnan(top_k_ema) & (top_k_ema > 0)]

        # Only go short if EMA_fast < EMA_slow
        bottom_k_ema = ema[bottom_k_idx]
        bottom_k_filtered = bottom_k_idx[~np.isnan(bottom_k_ema) & (bottom_k_ema < 0)]

        n_l = max(len(top_k_filtered), 1)
        n_s = max(len(bottom_k_filtered), 1)

        if len(top_k_filtered) > 0:
            long_membership[rb, top_k_filtered] = True
            long_weights[rb, top_k_filtered] = LONG_WT / n_l

        if len(bottom_k_filtered) > 0:
            short_membership[rb, bottom_k_filtered] = True
            short_weights[rb, bottom_k_filtered] = SHORT_WT / n_s

    # ── Build per-token StrategyResults ──────────────────────────
    # Each token gets ONE StrategyResult with per-bar direction (+1/-1)
    # and per-bar size_multiplier encoding the weight at each rebalance.
    results = {}
    for j, token in enumerate(eligible_tokens):
        d = token_data[token]
        ctx = d["ctx"]
        n = d["n"]
        offset = offsets[token]

        is_long = long_membership[offset:offset + n, j]
        is_short = short_membership[offset:offset + n, j]
        lw = long_weights[offset:offset + n, j]
        sw = short_weights[offset:offset + n, j]

        entry_mask = is_long | is_short
        if not np.any(entry_mask):
            continue

        entry_mask[:50] = False  # warmup guard

        # Per-bar direction: +1 for longs, -1 for shorts
        direction = np.zeros(n, dtype=np.int8)
        direction[is_long] = 1
        direction[is_short] = -1
        # Fill non-entry bars with +1 (default, won't be used)
        direction[direction == 0] = 1

        # Per-bar size_multiplier: weight from long or short bucket
        sm = np.ones(n, dtype=np.float32)
        sm[is_long] = lw[is_long].astype(np.float32)
        sm[is_short] = sw[is_short].astype(np.float32)

        results[token] = StrategyResult(
            entry_mask=entry_mask,
            direction=direction,
            market_type=MarketType.PERP,
            leverage=LEVERAGE,
            stop_mult=999.0,
            trail_mult=999.0,
            target_mult=999.0,
            no_stop_bars=REBALANCE_BARS,
            min_hold=1,
            max_hold=REBALANCE_BARS,
            # Sizing: edge=1.0 so kelly_frac = 0.50 × 1.0 × sm ≈ sm/2.
            # Combined with target_vol=0.02 and cap_pct=0.27,
            # the Kelly formula produces positions close to sm × equity.
            edge=1.0,
            exit_regimes=set(),
            size_multiplier=sm,
            cap_multiplier=5.0,       # Lift capital cap so it doesn't bind
            max_trade_pct=0.30,       # Hard upper bound per token
            name="s400_xsec_momentum_r162",
            breakeven_atr=0.0,
        )

    return results
