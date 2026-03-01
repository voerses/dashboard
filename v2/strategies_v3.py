"""
V3 Strategies — Post-ETF Era, Contrarian, Liquidity-Aware

These strategies are designed based on what ACTUALLY works post-ETF,
informed by causal analysis and signal evaluation.

Key principles:
1. Swim against the market — when everyone uses momentum, use reversion
2. Liquidity constraints — never trade more than 2% of daily volume
3. Cross-sectional — compare tokens against each other, not just vs themselves
4. Multi-scale — use the timeframe where signal is strongest
5. Adaptive — detect when a signal stops working and rotate

Strategies:
6. Volatility Harvesting — sell vol expansion, buy vol compression
7. Cross-Sectional Momentum — long relative winners vs market, short losers
8. Information Flow Leader-Follower — trade lead-lag from transfer entropy
9. Liquidity Provision Timing — trade around liquidity events
10. Adaptive Multi-Scale — use whichever timeframe has strongest signal now
"""

import numpy as np
import pandas as pd
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from regime_detector import Regime, RegimeDetector


# ============================================================================
# Shared utilities
# ============================================================================

def _ema(arr, period):
    result = np.full_like(arr, np.nan, dtype=float)
    alpha = 2.0 / (period + 1)
    start = 0
    for i in range(len(arr)):
        if np.isfinite(arr[i]):
            start = i
            break
    if start + period > len(arr):
        return result
    result[start + period - 1] = np.mean(arr[start:start + period])
    for i in range(start + period, len(arr)):
        if np.isfinite(arr[i]):
            result[i] = alpha * arr[i] + (1 - alpha) * result[i-1]
        else:
            result[i] = result[i-1]
    result[:start + period - 1] = result[start + period - 1] if np.isfinite(result[start + period - 1]) else 0
    return result


def _sma(arr, period):
    result = np.full_like(arr, np.nan, dtype=float)
    for i in range(period - 1, len(arr)):
        result[i] = np.mean(arr[i - period + 1:i + 1])
    result[:period - 1] = result[period - 1] if period - 1 < len(arr) and np.isfinite(result[period - 1]) else 0
    return result


def _atr(df, period=14):
    high = df['high'].values.astype(float)
    low = df['low'].values.astype(float)
    close = df['close'].values.astype(float)
    n = len(close)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
    atr = np.zeros(n)
    if n > period:
        atr[period] = np.mean(tr[1:period+1])
        for i in range(period + 1, n):
            atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
    return atr


def _rsi(close, period=14):
    n = len(close)
    rsi = np.full(n, 50.0)
    if n < period + 1:
        return rsi
    deltas = np.diff(close)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss > 0:
            rs = avg_gain / avg_loss
            rsi[i + 1] = 100 - (100 / (1 + rs))
        else:
            rsi[i + 1] = 100
    return rsi


def _garman_klass_vol(df, period=20):
    """Garman-Klass volatility estimator — better than close-to-close."""
    h = np.log(df['high'].values.astype(float))
    l = np.log(df['low'].values.astype(float))
    c = np.log(df['close'].values.astype(float))
    o = np.log(df['open'].values.astype(float))
    n = len(c)

    gk = 0.5 * (h - l)**2 - (2 * np.log(2) - 1) * (c - o)**2
    gk_vol = np.zeros(n)
    for i in range(period, n):
        gk_vol[i] = np.sqrt(np.mean(gk[i-period:i]) * 252)

    return gk_vol


def _regime_size_mult(regime):
    return {
        Regime.TRENDING_UP: 1.0,
        Regime.TRENDING_DOWN: 0.4,
        Regime.MEAN_REVERTING: 0.6,
        Regime.HIGH_VOL_CHAOS: 0.2,
        Regime.LOW_VOL_ACCUMULATION: 0.7,
    }.get(regime, 0.5)


# ============================================================================
# Strategy 6: Volatility Harvesting
# ============================================================================

class Strategy6_VolatilityHarvesting:
    """
    Volatility Harvesting Strategy

    Key insight: Volatility is mean-reverting. When vol spikes, it will
    compress. When vol compresses, it will expand.

    Post-ETF, this works because:
    1. ETF arbitrage compresses vol around fair value
    2. Vol spikes create opportunities (panic selling → buy cheap)
    3. Vol compression means accumulation → position early

    Signals:
    - Vol ratio (short/long): < 0.7 → vol compressed, buy before breakout
    - Vol ratio > 1.5 → vol expanding, sell into panic or buy the dip
    - Garman-Klass vs close-to-close divergence → informed trading detected
    - Vol-of-vol high → regime change imminent

    Contrarian element: Buy when others are panicking (high vol),
    sell when everyone is complacent (low vol but price extended).
    """
    name = "Volatility Harvesting"

    def generate_signals(self, df, regimes=None, features=None):
        n = len(df)
        close = df['close'].values.astype(float)
        volume = df['volume'].values.astype(float)

        if regimes is None:
            detector = RegimeDetector()
            regimes, features = detector.detect(df)

        signals = pd.DataFrame(index=df.index)
        signals['signal'] = 0
        signals['position_size'] = 0.0
        signals['regime'] = regimes

        atr = _atr(df, 14)
        rsi = _rsi(close, 14)
        gk_vol = _garman_klass_vol(df, 20)
        ema_20 = _ema(close, 20)
        ema_50 = _ema(close, 50)

        # Realized vol at multiple scales
        rets = np.zeros(n)
        rets[1:] = np.diff(np.log(np.maximum(close, 1e-10)))

        vol_5 = np.zeros(n)
        vol_20 = np.zeros(n)
        vol_60 = np.zeros(n)
        for i in range(5, n):
            vol_5[i] = np.std(rets[i-5:i]) * np.sqrt(365)
        for i in range(20, n):
            vol_20[i] = np.std(rets[i-20:i]) * np.sqrt(365)
        for i in range(60, n):
            vol_60[i] = np.std(rets[i-60:i]) * np.sqrt(365)

        # Vol-of-vol (volatility of volatility)
        vol_of_vol = np.zeros(n)
        for i in range(40, n):
            vol_window = vol_20[i-20:i]
            if np.mean(vol_window) > 0:
                vol_of_vol[i] = np.std(vol_window) / np.mean(vol_window)

        base_size = 0.35
        warmup = 60

        for i in range(warmup, n):
            regime = regimes[i]
            regime_mult = _regime_size_mult(regime)
            sig = 0
            size = 0.0

            # Vol ratio: short vol / long vol
            vol_ratio = vol_5[i] / vol_60[i] if vol_60[i] > 0.01 else 1.0
            vol_ratio_20_60 = vol_20[i] / vol_60[i] if vol_60[i] > 0.01 else 1.0

            # Price position relative to trend
            above_ema20 = close[i] > ema_20[i] if np.isfinite(ema_20[i]) else False
            above_ema50 = close[i] > ema_50[i] if np.isfinite(ema_50[i]) else False

            # CONTRARIAN SIGNALS:

            # 1. Vol spike + oversold = buy the panic
            if vol_ratio > 2.0 and rsi[i] < 30:
                sig = 1
                # Larger position on bigger vol spikes (contrarian)
                strength = min(vol_ratio / 3.0, 1.0)
                size = base_size * regime_mult * strength
            # 2. Vol spike + overbought = take profits / short
            elif vol_ratio > 2.0 and rsi[i] > 75:
                sig = 0  # Exit, don't chase
                size = 0
            # 3. Vol compressed + price above trend = accumulation breakout coming
            elif vol_ratio < 0.6 and above_ema20 and above_ema50:
                sig = 1
                size = base_size * regime_mult * 0.7
            # 4. Vol compressed + price below trend = breakdown likely
            elif vol_ratio < 0.6 and not above_ema20 and not above_ema50:
                sig = -1
                size = base_size * regime_mult * 0.3
            # 5. Vol normalizing from spike + trend intact = buy
            elif 1.0 < vol_ratio < 1.5 and vol_ratio_20_60 > 1.2 and above_ema50:
                sig = 1
                size = base_size * regime_mult * 0.5
            # 6. Vol-of-vol spike = regime change imminent, reduce
            elif vol_of_vol[i] > 0.5:
                sig = 0  # Stay flat during transitions
                size = 0
            # 7. Normal vol + trend following
            elif 0.7 < vol_ratio < 1.3:
                if above_ema20 and rsi[i] > 40 and rsi[i] < 70:
                    sig = 1
                    size = base_size * regime_mult * 0.4
                elif not above_ema20 and rsi[i] < 45:
                    # Slight dip in normal vol = opportunity
                    sig = 1
                    size = base_size * regime_mult * 0.2

            signals.iloc[i, signals.columns.get_loc('signal')] = sig
            signals.iloc[i, signals.columns.get_loc('position_size')] = max(size, 0)

        return signals


# ============================================================================
# Strategy 7: Cross-Sectional Momentum (requires market context)
# ============================================================================

class Strategy7_CrossSectionalMomentum:
    """
    Cross-Sectional Momentum — Relative Strength

    Instead of trading momentum of a single token (which is crowded),
    trade RELATIVE momentum: is this token outperforming or underperforming
    the market (BTC)?

    Post-ETF insight: While absolute momentum is degraded, relative
    momentum still works because token-specific catalysts (airdrops,
    protocol upgrades, narratives) create dispersion.

    Signals:
    - Relative strength = token_ret_20d - btc_ret_20d
    - RS > threshold → token outperforming, stay long
    - RS < threshold → token underperforming, go flat
    - RS acceleration → new catalyst, increase size

    Needs BTC data injected as market_returns parameter.
    """
    name = "Cross-Sectional Momentum"

    def __init__(self):
        self._btc_returns = None

    def set_market_returns(self, btc_df):
        """Set BTC returns as market benchmark."""
        self._btc_returns = btc_df['close'].pct_change()

    def generate_signals(self, df, regimes=None, features=None):
        n = len(df)
        close = df['close'].values.astype(float)
        volume = df['volume'].values.astype(float)

        if regimes is None:
            detector = RegimeDetector()
            regimes, features = detector.detect(df)

        signals = pd.DataFrame(index=df.index)
        signals['signal'] = 0
        signals['position_size'] = 0.0
        signals['regime'] = regimes

        rsi = _rsi(close, 14)
        ema_20 = _ema(close, 20)
        atr = _atr(df, 14)

        # Compute token returns at multiple horizons
        ret_5 = np.zeros(n)
        ret_10 = np.zeros(n)
        ret_20 = np.zeros(n)
        ret_60 = np.zeros(n)
        for i in range(5, n):
            ret_5[i] = close[i] / close[i-5] - 1
        for i in range(10, n):
            ret_10[i] = close[i] / close[i-10] - 1
        for i in range(20, n):
            ret_20[i] = close[i] / close[i-20] - 1
        for i in range(60, n):
            ret_60[i] = close[i] / close[i-60] - 1

        # Get BTC returns for relative strength
        btc_ret_20 = np.zeros(n)
        btc_ret_60 = np.zeros(n)
        if self._btc_returns is not None:
            btc_close = (1 + self._btc_returns).cumprod()
            for i in range(20, n):
                date = df.index[i]
                date_20 = df.index[max(0, i-20)]
                if date in btc_close.index and date_20 in btc_close.index:
                    btc_ret_20[i] = btc_close.loc[date] / btc_close.loc[date_20] - 1
                if i >= 60:
                    date_60 = df.index[max(0, i-60)]
                    if date_60 in btc_close.index:
                        btc_ret_60[i] = btc_close.loc[date] / btc_close.loc[date_60] - 1

        # Relative strength
        rs_20 = ret_20 - btc_ret_20
        rs_60 = ret_60 - btc_ret_60

        # Volume-weighted relative strength
        vol_ma = _sma(volume, 20)
        vol_ratio = np.where(vol_ma > 0, volume / vol_ma, 1.0)

        base_size = 0.35
        warmup = 60
        prev_signal = 0
        hold_days = 0

        for i in range(warmup, n):
            regime = regimes[i]
            regime_mult = _regime_size_mult(regime)
            sig = 0
            size = 0.0

            # Relative strength signals
            rs = rs_20[i]
            rs_long = rs_60[i]

            # RS acceleration (is relative strength increasing?)
            rs_accel = rs_20[i] - rs_20[max(0, i-5)] if i >= 5 else 0

            # Volume confirmation (high volume + outperformance = real)
            vol_confirmed = vol_ratio[i] > 1.2

            # ENTRY: Strong relative outperformance
            if rs > 0.05 and rs_accel > 0:
                sig = 1
                strength = min(rs / 0.15, 1.0)
                vol_bonus = 1.2 if vol_confirmed else 1.0
                size = base_size * regime_mult * strength * vol_bonus

            # ENTRY: Moderate RS + long-term RS positive
            elif rs > 0.02 and rs_long > 0.05:
                sig = 1
                size = base_size * regime_mult * 0.5

            # ENTRY: RS recovering from negative (contrarian in RS space)
            elif rs > -0.02 and rs_20[max(0, i-5)] < -0.05 and rs_accel > 0.02:
                sig = 1
                size = base_size * regime_mult * 0.4  # Recovery play

            # HOLD: Still outperforming but weakening
            elif prev_signal > 0 and rs > -0.03 and hold_days < 30:
                sig = 1
                size = base_size * regime_mult * 0.3

            # EXIT: Underperforming with no recovery sign
            elif rs < -0.05 and rs_accel < 0:
                sig = 0
                size = 0

            # SHORT: Strong underperformance (careful, only in down regimes)
            if rs < -0.10 and rs_accel < -0.02 and regime in (Regime.TRENDING_DOWN, Regime.HIGH_VOL_CHAOS):
                sig = -1
                size = base_size * regime_mult * 0.2

            if sig == prev_signal:
                hold_days += 1
            else:
                hold_days = 0

            signals.iloc[i, signals.columns.get_loc('signal')] = sig
            signals.iloc[i, signals.columns.get_loc('position_size')] = max(size, 0)
            prev_signal = sig

        return signals


# ============================================================================
# Strategy 8: Liquidity-Informed Contrarian
# ============================================================================

class Strategy8_LiquidityContrarian:
    """
    Liquidity-Informed Contrarian Strategy

    Key insight: Market impact is the hidden cost that kills most strategies.
    This strategy USES liquidity as a signal, not just a constraint.

    Contrarian signals from liquidity:
    1. Volume spike + price drop = forced selling (buy opportunity)
    2. Low volume + price rise = no conviction rally (don't chase)
    3. Volume divergence (price up, volume down) = distribution (exit)
    4. Liquidity vacuum (sudden vol drop) = calm before storm

    Post-ETF: Institutional order flow creates predictable patterns.
    ETF rebalancing creates periodic liquidity events.
    """
    name = "Liquidity Contrarian"

    def generate_signals(self, df, regimes=None, features=None):
        n = len(df)
        close = df['close'].values.astype(float)
        volume = df['volume'].values.astype(float)
        high = df['high'].values.astype(float)
        low = df['low'].values.astype(float)

        if regimes is None:
            detector = RegimeDetector()
            regimes, features = detector.detect(df)

        signals = pd.DataFrame(index=df.index)
        signals['signal'] = 0
        signals['position_size'] = 0.0
        signals['regime'] = regimes

        rsi = _rsi(close, 14)
        atr = _atr(df, 14)
        ema_20 = _ema(close, 20)

        # Dollar volume
        dollar_vol = close * volume

        # Volume metrics
        vol_sma_20 = _sma(volume, 20)
        vol_sma_5 = _sma(volume, 5)
        dv_sma_20 = _sma(dollar_vol, 20)

        # Amihud illiquidity (rolling)
        rets_abs = np.zeros(n)
        rets_abs[1:] = np.abs(np.diff(close) / close[:-1])
        amihud = np.zeros(n)
        for i in range(10, n):
            dv = dollar_vol[i-10:i]
            ra = rets_abs[i-10:i]
            valid = dv > 0
            if valid.sum() > 5:
                amihud[i] = np.mean(ra[valid] / dv[valid])

        # OBV (On-Balance Volume)
        obv = np.zeros(n)
        for i in range(1, n):
            if close[i] > close[i-1]:
                obv[i] = obv[i-1] + volume[i]
            elif close[i] < close[i-1]:
                obv[i] = obv[i-1] - volume[i]
            else:
                obv[i] = obv[i-1]

        # OBV slope (divergence signal)
        obv_slope = np.zeros(n)
        for i in range(10, n):
            x = np.arange(10)
            y = obv[i-10:i]
            if np.std(y) > 0:
                obv_slope[i] = np.polyfit(x, y, 1)[0]

        # Price slope
        price_slope = np.zeros(n)
        for i in range(10, n):
            x = np.arange(10)
            y = close[i-10:i]
            if np.std(y) > 0:
                price_slope[i] = np.polyfit(x, y, 1)[0] / close[i]

        # Close location within day's range (where did it close?)
        close_loc = np.zeros(n)
        for i in range(n):
            rng = high[i] - low[i]
            if rng > 0:
                close_loc[i] = (close[i] - low[i]) / rng

        base_size = 0.35
        warmup = 30

        for i in range(warmup, n):
            regime = regimes[i]
            regime_mult = _regime_size_mult(regime)
            sig = 0
            size = 0.0

            vol_ratio = volume[i] / vol_sma_20[i] if vol_sma_20[i] > 0 else 1.0
            ret_1d = (close[i] / close[i-1] - 1) if close[i-1] > 0 else 0
            ret_5d = (close[i] / close[max(0, i-5)] - 1) if close[max(0, i-5)] > 0 else 0

            # OBV-price divergence
            obv_up_price_down = obv_slope[i] > 0 and price_slope[i] < 0
            obv_down_price_up = obv_slope[i] < 0 and price_slope[i] > 0

            # CONTRARIAN SIGNALS:

            # 1. High volume + price drop + low close = FORCED SELLING (buy)
            if vol_ratio > 2.0 and ret_1d < -0.03 and close_loc[i] < 0.3:
                sig = 1
                # Bigger vol spike = more conviction (contrarian)
                strength = min(vol_ratio / 3.0, 1.0)
                size = base_size * regime_mult * strength * 0.7

            # 2. High volume + price drop + high close = ABSORPTION (strong buy)
            elif vol_ratio > 1.5 and ret_1d < -0.02 and close_loc[i] > 0.7:
                sig = 1
                size = base_size * regime_mult * 0.8  # Strong: sellers being absorbed

            # 3. Low volume + price rise = NO CONVICTION (don't chase)
            elif vol_ratio < 0.5 and ret_5d > 0.05:
                sig = 0  # Skip unconvincing rallies

            # 4. OBV divergence: volume flowing in but price flat/down (accumulation)
            elif obv_up_price_down and rsi[i] < 45:
                sig = 1
                size = base_size * regime_mult * 0.6

            # 5. OBV divergence: volume flowing out but price up (distribution)
            elif obv_down_price_up and rsi[i] > 60:
                sig = 0  # Exit distribution

            # 6. Liquidity improving + trend intact
            elif (vol_ratio > 0.8 and np.isfinite(ema_20[i]) and close[i] > ema_20[i]
                  and close_loc[i] > 0.5 and rsi[i] > 40 and rsi[i] < 70):
                sig = 1
                size = base_size * regime_mult * 0.4

            # 7. Amihud spike (illiquidity crisis) + oversold = contrarian buy
            if i > 30:
                amihud_ratio = amihud[i] / np.mean(amihud[max(0,i-30):i]) if np.mean(amihud[max(0,i-30):i]) > 0 else 1
                if amihud_ratio > 3.0 and rsi[i] < 30:
                    sig = 1
                    size = base_size * regime_mult * 0.5  # Small size (illiquid!)

            signals.iloc[i, signals.columns.get_loc('signal')] = sig
            signals.iloc[i, signals.columns.get_loc('position_size')] = max(size, 0)

        return signals


# ============================================================================
# Strategy 9: Adaptive Multi-Scale
# ============================================================================

class Strategy9_AdaptiveMultiScale:
    """
    Adaptive Multi-Scale Strategy

    Inspired by renormalization group theory: price dynamics operate
    at multiple time scales. The key is finding which scale has the
    strongest signal RIGHT NOW and trading that.

    Scales: 5d (noise), 10d (swing), 20d (position), 40d (trend), 80d (macro)

    For each scale:
    - Compute momentum and mean-reversion signals
    - Compute signal IC over rolling 60-day window
    - Weight scales by recent IC

    Post-ETF: Short scales became noisier (HFT + ETF arb).
    Medium scales (10-20d) may carry more signal now.
    """
    name = "Adaptive Multi-Scale"

    def generate_signals(self, df, regimes=None, features=None):
        n = len(df)
        close = df['close'].values.astype(float)
        volume = df['volume'].values.astype(float)

        if regimes is None:
            detector = RegimeDetector()
            regimes, features = detector.detect(df)

        signals = pd.DataFrame(index=df.index)
        signals['signal'] = 0
        signals['position_size'] = 0.0
        signals['regime'] = regimes

        rsi = _rsi(close, 14)
        ema_20 = _ema(close, 20)

        scales = [5, 10, 20, 40, 80]

        # Compute multi-scale signals
        momenta = {}
        zscores = {}
        for scale in scales:
            mom = np.zeros(n)
            zsc = np.zeros(n)
            for i in range(scale, n):
                mom[i] = close[i] / close[i - scale] - 1
                window = close[max(0, i-scale):i+1]
                mu = np.mean(window)
                std = np.std(window)
                if std > 1e-10:
                    zsc[i] = (close[i] - mu) / std
            momenta[scale] = mom
            zscores[scale] = zsc

        # Rolling IC computation for adaptive weighting
        # IC = correlation between signal[t] and future 5-day return
        fwd_5d = np.zeros(n)
        for i in range(n - 5):
            fwd_5d[i] = close[i + 5] / close[i] - 1

        # Scale weights (adaptive)
        ic_window = 60
        scale_weights_mom = {s: np.zeros(n) for s in scales}
        scale_weights_mr = {s: np.zeros(n) for s in scales}

        for i in range(max(scales) + ic_window, n - 5):
            for scale in scales:
                # Momentum IC
                sig_window = momenta[scale][i-ic_window:i]
                fwd_window = fwd_5d[i-ic_window:i]
                if np.std(sig_window) > 1e-10 and np.std(fwd_window) > 1e-10:
                    ic = np.corrcoef(sig_window, fwd_window)[0, 1]
                    scale_weights_mom[scale][i] = ic if np.isfinite(ic) else 0

                # Mean reversion IC (negative z-score should predict positive return)
                sig_window_z = -zscores[scale][i-ic_window:i]  # Negative for MR
                if np.std(sig_window_z) > 1e-10:
                    ic_mr = np.corrcoef(sig_window_z, fwd_window)[0, 1]
                    scale_weights_mr[scale][i] = ic_mr if np.isfinite(ic_mr) else 0

        base_size = 0.35
        warmup = max(scales) + ic_window + 10

        for i in range(warmup, n):
            regime = regimes[i]
            regime_mult = _regime_size_mult(regime)

            # Compute IC-weighted momentum signal
            total_mom_ic = sum(abs(scale_weights_mom[s][i]) for s in scales)
            total_mr_ic = sum(abs(scale_weights_mr[s][i]) for s in scales)

            weighted_mom = 0
            weighted_mr = 0

            if total_mom_ic > 0.01:
                for s in scales:
                    w = abs(scale_weights_mom[s][i]) / total_mom_ic
                    # Use the sign of IC to determine direction
                    if scale_weights_mom[s][i] > 0:
                        weighted_mom += w * momenta[s][i]
                    else:
                        weighted_mom -= w * momenta[s][i]  # Contrarian

            if total_mr_ic > 0.01:
                for s in scales:
                    w = abs(scale_weights_mr[s][i]) / total_mr_ic
                    if scale_weights_mr[s][i] > 0:
                        weighted_mr += w * (-zscores[s][i])  # Negative z → buy
                    else:
                        weighted_mr -= w * (-zscores[s][i])

            # Combine: use whichever has stronger recent IC
            use_momentum = total_mom_ic > total_mr_ic

            if use_momentum:
                combined_signal = weighted_mom
                ic_strength = total_mom_ic / len(scales)
            else:
                combined_signal = weighted_mr
                ic_strength = total_mr_ic / len(scales)

            sig = 0
            size = 0.0

            # Signal threshold scaled by IC strength
            threshold = max(0.01, 0.05 * (1 - ic_strength * 5))

            if combined_signal > threshold:
                sig = 1
                strength = min(abs(combined_signal) / 0.1, 1.0)
                size = base_size * regime_mult * strength * min(ic_strength * 10, 1.0)
            elif combined_signal < -threshold:
                sig = -1
                strength = min(abs(combined_signal) / 0.1, 1.0)
                size = base_size * regime_mult * strength * min(ic_strength * 10, 1.0) * 0.5

            signals.iloc[i, signals.columns.get_loc('signal')] = sig
            signals.iloc[i, signals.columns.get_loc('position_size')] = max(size, 0)

        return signals


# ============================================================================
# Strategy 10: Meta-Adaptive Ensemble (V3 Master)
# ============================================================================

class Strategy10_MetaAdaptiveEnsemble:
    """
    Meta-Adaptive Ensemble — The V3 Master Strategy

    Combines V2 and V3 strategies with:
    1. Rolling IC-based weighting (which sub-strategy works NOW?)
    2. Regime-conditional strategy selection
    3. Liquidity-adjusted position sizing
    4. Drawdown-based risk management

    This is the "fund of strategies" approach.
    """
    name = "Meta-Adaptive Ensemble"

    def __init__(self):
        self.sub_strategies = [
            Strategy6_VolatilityHarvesting(),
            Strategy7_CrossSectionalMomentum(),
            Strategy8_LiquidityContrarian(),
            Strategy9_AdaptiveMultiScale(),
        ]

    def set_market_returns(self, btc_df):
        """Pass BTC data to cross-sectional strategy."""
        for s in self.sub_strategies:
            if hasattr(s, 'set_market_returns'):
                s.set_market_returns(btc_df)

    def generate_signals(self, df, regimes=None, features=None):
        if regimes is None:
            detector = RegimeDetector()
            regimes, features = detector.detect(df)

        # Generate all sub-strategy signals
        sub_signals = []
        for strat in self.sub_strategies:
            try:
                sig = strat.generate_signals(df, regimes, features)
                sub_signals.append(sig)
            except Exception:
                neutral = pd.DataFrame(index=df.index)
                neutral['signal'] = 0
                neutral['position_size'] = 0.0
                sub_signals.append(neutral)

        n = len(df)
        close = df['close'].values.astype(float)

        signals = pd.DataFrame(index=df.index)
        signals['signal'] = 0
        signals['position_size'] = 0.0
        signals['regime'] = regimes

        # Dynamic weighting based on recent performance
        weights = np.ones(len(sub_signals)) / len(sub_signals)
        recent_pnls = [[] for _ in sub_signals]

        # Equity tracking for drawdown-based risk
        peak_equity = 1.0
        equity = 1.0

        for i in range(1, n):
            # Track sub-strategy P&L
            price_ret = close[i] / close[i-1] - 1 if close[i-1] > 0 else 0

            for j, sig in enumerate(sub_signals):
                s = sig['signal'].iloc[i-1] if i-1 < len(sig) else 0
                ps = sig['position_size'].iloc[i-1] if i-1 < len(sig) else 0
                recent_pnls[j].append(s * ps * price_ret)

            # Reweight every 30 bars using rolling Sharpe
            if i > 90 and i % 30 == 0:
                for j in range(len(sub_signals)):
                    rets_j = np.array(recent_pnls[j][-90:])
                    if len(rets_j) > 0 and np.std(rets_j) > 1e-10:
                        sharpe_j = np.mean(rets_j) / np.std(rets_j)
                        # Softmax-like weighting: better Sharpe gets more weight
                        weights[j] = np.exp(sharpe_j * 2)
                    else:
                        weights[j] = 1.0

                total_w = weights.sum()
                if total_w > 0:
                    weights = weights / total_w

            # Aggregate signals
            weighted_signal = 0
            weighted_size = 0

            for j, sig in enumerate(sub_signals):
                if i >= len(sig):
                    continue
                s = sig['signal'].iloc[i]
                ps = sig['position_size'].iloc[i]
                weighted_signal += weights[j] * s
                if s != 0:
                    weighted_size += weights[j] * ps

            # Drawdown-based risk scaling
            equity *= (1 + price_ret * weighted_signal * weighted_size)
            peak_equity = max(peak_equity, equity)
            drawdown = 1 - equity / peak_equity

            # Reduce size during drawdown (protect capital)
            dd_scale = 1.0
            if drawdown > 0.10:
                dd_scale = 0.5  # Half size at 10% DD
            if drawdown > 0.20:
                dd_scale = 0.25  # Quarter size at 20% DD
            if drawdown > 0.30:
                dd_scale = 0.0  # Flat at 30% DD (circuit breaker)

            # Final signal
            if weighted_signal > 0.2:
                signals.iloc[i, signals.columns.get_loc('signal')] = 1
                signals.iloc[i, signals.columns.get_loc('position_size')] = min(weighted_size * dd_scale, 0.50)
            elif weighted_signal < -0.2:
                signals.iloc[i, signals.columns.get_loc('signal')] = -1
                signals.iloc[i, signals.columns.get_loc('position_size')] = min(abs(weighted_size) * dd_scale * 0.5, 0.25)
            # Lower threshold for partial position
            elif weighted_signal > 0.05:
                signals.iloc[i, signals.columns.get_loc('signal')] = 1
                signals.iloc[i, signals.columns.get_loc('position_size')] = min(weighted_size * dd_scale * 0.5, 0.25)

        return signals


def get_all_v3_strategies():
    """Return all v3 strategy instances."""
    return [
        Strategy6_VolatilityHarvesting(),
        Strategy7_CrossSectionalMomentum(),
        Strategy8_LiquidityContrarian(),
        Strategy9_AdaptiveMultiScale(),
        Strategy10_MetaAdaptiveEnsemble(),
    ]
