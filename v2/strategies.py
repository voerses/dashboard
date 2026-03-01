"""
Elite Trading Strategies v2 — Real Data Edition

5 strategies designed from research on Renaissance, Two Sigma, Jump, Pythagoras:

1. HMM Regime Adaptive - Meta-strategy switching between momentum & mean reversion
2. Momentum Trend Follower - For trending markets (risk-managed)
3. Mean Reversion Z-Score - For sideways markets with ADF confirmation
4. Funding Rate + OI Divergence - Derivatives-driven contrarian signals
5. Multi-Factor Ensemble - Performance-weighted combination of all strategies

Key design: Strategies trade across ALL regimes (regime adjusts sizing, not whether
to trade). This prevents the "barely any trades" problem on real data.
"""

import numpy as np
import pandas as pd
from regime_detector import Regime, RegimeDetector


def _atr(df, period=14):
    """Calculate Average True Range."""
    high = df['high'].values.astype(float)
    low = df['low'].values.astype(float)
    close = df['close'].values.astype(float)
    n = len(close)

    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(high[i] - low[i],
                    abs(high[i] - close[i-1]),
                    abs(low[i] - close[i-1]))

    atr = np.zeros(n)
    if n > period:
        atr[period] = np.mean(tr[1:period+1])
        for i in range(period + 1, n):
            atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
    return atr


def _rsi(close, period=14):
    """Calculate RSI."""
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


def _ema(arr, period):
    """Exponential moving average."""
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
    result[:start + period - 1] = result[start + period - 1]
    return result


def _sma(arr, period):
    """Simple moving average."""
    result = np.full_like(arr, np.nan, dtype=float)
    for i in range(period - 1, len(arr)):
        result[i] = np.mean(arr[i - period + 1:i + 1])
    result[:period - 1] = result[period - 1] if np.isfinite(result[period - 1]) else 0
    return result


def _bollinger(close, period=20, std_mult=2.0):
    """Bollinger Bands."""
    sma = _sma(close, period)
    std = np.full_like(close, 0.0, dtype=float)
    for i in range(period - 1, len(close)):
        std[i] = np.std(close[i - period + 1:i + 1])
    upper = sma + std_mult * std
    lower = sma - std_mult * std
    return sma, upper, lower


def _macd(close, fast=12, slow=26, signal=9):
    """MACD with histogram."""
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _adx(df, period=14):
    """Average Directional Index."""
    high = df['high'].values.astype(float)
    low = df['low'].values.astype(float)
    close = df['close'].values.astype(float)
    n = len(close)

    adx = np.full(n, 20.0)
    if n < period * 3:
        return adx

    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    tr = np.zeros(n)

    for i in range(1, n):
        up = high[i] - high[i-1]
        down = low[i-1] - low[i]
        plus_dm[i] = up if (up > down and up > 0) else 0
        minus_dm[i] = down if (down > up and down > 0) else 0
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))

    atr14 = np.zeros(n)
    plus_di = np.zeros(n)
    minus_di = np.zeros(n)
    dx = np.zeros(n)

    atr14[period] = np.mean(tr[1:period+1])
    sm_plus = np.mean(plus_dm[1:period+1])
    sm_minus = np.mean(minus_dm[1:period+1])

    for i in range(period + 1, n):
        atr14[i] = (atr14[i-1] * (period - 1) + tr[i]) / period
        sm_plus = (sm_plus * (period - 1) + plus_dm[i]) / period
        sm_minus = (sm_minus * (period - 1) + minus_dm[i]) / period

        if atr14[i] > 0:
            plus_di[i] = 100 * sm_plus / atr14[i]
            minus_di[i] = 100 * sm_minus / atr14[i]
        if plus_di[i] + minus_di[i] > 0:
            dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / (plus_di[i] + minus_di[i])

    # Smooth DX into ADX
    start = period * 2
    if start < n:
        adx[start] = np.mean(dx[period+1:start+1])
        for i in range(start + 1, n):
            adx[i] = (adx[i-1] * (period - 1) + dx[i]) / period

    return adx


def _regime_size_mult(regime):
    """Position size multiplier by regime."""
    return {
        Regime.TRENDING_UP: 1.0,
        Regime.TRENDING_DOWN: 0.4,
        Regime.MEAN_REVERTING: 0.6,
        Regime.HIGH_VOL_CHAOS: 0.2,
        Regime.LOW_VOL_ACCUMULATION: 0.7,
    }.get(regime, 0.5)


class Strategy1_HMMRegimeAdaptive:
    """
    HMM Regime-Adaptive Strategy

    Trades in ALL regimes with different sub-strategies:
    - TRENDING_UP: Momentum (EMA cross + MACD), full size
    - TRENDING_DOWN: Counter-trend bounce buys on oversold, small size
    - MEAN_REVERTING: Bollinger + RSI mean reversion
    - HIGH_VOL_CHAOS: Small positions on extreme oversold only
    - LOW_VOL_ACCUMULATION: Build positions on EMA cross

    Regime adjusts SIZING, not WHETHER to trade.
    """
    name = "HMM Regime Adaptive"

    def generate_signals(self, df, regimes=None, features=None):
        n = len(df)
        close = df['close'].values.astype(float)

        if regimes is None:
            detector = RegimeDetector()
            regimes, features = detector.detect(df)

        signals = pd.DataFrame(index=df.index)
        signals['signal'] = 0
        signals['position_size'] = 0.0
        signals['regime'] = regimes

        atr = _atr(df, 14)
        rsi = _rsi(close, 14)
        ema_10 = _ema(close, 10)
        ema_30 = _ema(close, 30)
        ema_50 = _ema(close, 50)
        sma_200 = _sma(close, 200)
        sma_bb, upper_bb, lower_bb = _bollinger(close, 20, 2.0)
        macd_line, macd_signal, macd_hist = _macd(close)
        adx = _adx(df, 14)

        # Track position for persistence
        prev_signal = 0
        hold_counter = 0  # Minimum hold period

        warmup = 50
        for i in range(warmup, n):
            regime = regimes[i]
            base_size = 0.35  # 35% base position
            regime_mult = _regime_size_mult(regime)
            sig = 0
            size = 0.0

            ema_ok = np.isfinite(ema_10[i]) and np.isfinite(ema_30[i])

            if regime == Regime.TRENDING_UP:
                # Momentum: stay long when trend confirmed
                if ema_ok and ema_10[i] > ema_30[i]:
                    sig = 1
                    # Scale by trend strength
                    strength = 0.7
                    if macd_hist[i] > 0:
                        strength += 0.15
                    if rsi[i] > 50 and rsi[i] < 75:
                        strength += 0.15
                    size = base_size * regime_mult * min(strength, 1.0)
                elif rsi[i] < 35:
                    # Buy the dip in uptrend
                    sig = 1
                    size = base_size * regime_mult * 0.5

            elif regime == Regime.TRENDING_DOWN:
                # Counter-trend bounces only
                if rsi[i] < 25:
                    sig = 1
                    size = base_size * regime_mult * 0.5
                elif rsi[i] < 30 and macd_hist[i] > macd_hist[max(0, i-1)]:
                    # MACD turning up from oversold
                    sig = 1
                    size = base_size * regime_mult * 0.3

            elif regime == Regime.MEAN_REVERTING:
                # Mean reversion: buy low, sell high within range
                if close[i] < lower_bb[i] and rsi[i] < 35:
                    sig = 1
                    size = base_size * regime_mult * 0.8
                elif close[i] < sma_bb[i] and rsi[i] < 40:
                    sig = 1
                    size = base_size * regime_mult * 0.5
                elif close[i] > upper_bb[i] and rsi[i] > 70:
                    sig = 0  # Exit overbought
                elif prev_signal > 0 and rsi[i] < 60:
                    # Hold through mid-range
                    sig = 1
                    size = base_size * regime_mult * 0.3

            elif regime == Regime.HIGH_VOL_CHAOS:
                # Only buy extreme panic
                if rsi[i] < 20:
                    sig = 1
                    size = base_size * regime_mult * 0.5

            elif regime == Regime.LOW_VOL_ACCUMULATION:
                # Accumulate with trend confirmation
                if ema_ok and ema_10[i] > ema_30[i]:
                    sig = 1
                    size = base_size * regime_mult * 0.7
                elif rsi[i] < 40:
                    sig = 1
                    size = base_size * regime_mult * 0.4

            # Minimum hold period (5 days) to reduce churn
            if prev_signal > 0 and sig == 0 and hold_counter < 5:
                sig = prev_signal
                size = signals['position_size'].iloc[i-1] * 0.9  # Slightly reduce
                hold_counter += 1
            elif sig != prev_signal:
                hold_counter = 0

            signals.iloc[i, signals.columns.get_loc('signal')] = sig
            signals.iloc[i, signals.columns.get_loc('position_size')] = max(size, 0)
            prev_signal = sig

        return signals


class Strategy2_MomentumTrendFollower:
    """
    Risk-Managed Momentum Trend Follower

    Multi-timeframe momentum with ATR trailing stop.
    Trades in trending AND accumulation regimes.
    Uses Chandelier exit with regime-adaptive ATR multiplier.

    Key: Enters on breakouts and momentum signals, trails stop to lock profits.
    """
    name = "Momentum Trend"

    def generate_signals(self, df, regimes=None, features=None):
        n = len(df)
        close = df['close'].values.astype(float)
        high = df['high'].values.astype(float)

        if regimes is None:
            detector = RegimeDetector()
            regimes, features = detector.detect(df)

        signals = pd.DataFrame(index=df.index)
        signals['signal'] = 0
        signals['position_size'] = 0.0
        signals['regime'] = regimes

        atr = _atr(df, 14)
        rsi = _rsi(close, 14)
        ema_10 = _ema(close, 10)
        ema_20 = _ema(close, 20)
        ema_50 = _ema(close, 50)
        macd_line, macd_signal, macd_hist = _macd(close)
        adx = _adx(df, 14)

        # Donchian channel (20-day high)
        high_20 = np.zeros(n)
        low_20 = np.zeros(n)
        for i in range(20, n):
            high_20[i] = np.max(high[i-20:i])
            low_20[i] = np.min(df['low'].values[i-20:i])

        # Position tracking for trailing stop
        in_position = False
        entry_price = 0
        highest_since_entry = 0
        trailing_stop = 0
        bars_held = 0

        warmup = 50
        for i in range(warmup, n):
            regime = regimes[i]
            regime_mult = _regime_size_mult(regime)
            base_size = 0.40

            if in_position:
                bars_held += 1
                highest_since_entry = max(highest_since_entry, close[i])

                # Adaptive trailing stop
                stop_atr_mult = 3.0
                if regime == Regime.TRENDING_UP:
                    stop_atr_mult = 3.5  # Wider in trends
                elif regime == Regime.MEAN_REVERTING:
                    stop_atr_mult = 2.0  # Tighter in ranges
                elif regime == Regime.HIGH_VOL_CHAOS:
                    stop_atr_mult = 2.0

                trailing_stop = highest_since_entry - stop_atr_mult * atr[i]

                # Exit conditions
                should_exit = False
                if close[i] < trailing_stop:
                    should_exit = True
                elif bars_held > 60 and close[i] < ema_20[i]:
                    should_exit = True  # Time + trend loss
                elif regime == Regime.HIGH_VOL_CHAOS and bars_held > 5:
                    should_exit = True

                if should_exit:
                    signals.iloc[i, signals.columns.get_loc('signal')] = 0
                    in_position = False
                    continue

                # Scale position by regime
                size = base_size * regime_mult
                if bars_held > 30:
                    size *= 0.8  # Reduce aging positions
                signals.iloc[i, signals.columns.get_loc('signal')] = 1
                signals.iloc[i, signals.columns.get_loc('position_size')] = size

            else:
                # Entry conditions: multi-signal momentum
                if regime == Regime.HIGH_VOL_CHAOS:
                    continue  # Don't enter in chaos

                score = 0
                # EMA alignment
                if (np.isfinite(ema_10[i]) and np.isfinite(ema_20[i]) and
                    np.isfinite(ema_50[i]) and ema_10[i] > ema_20[i] > ema_50[i]):
                    score += 2
                elif np.isfinite(ema_10[i]) and np.isfinite(ema_20[i]) and ema_10[i] > ema_20[i]:
                    score += 1

                # MACD positive and rising
                if macd_hist[i] > 0:
                    score += 1
                if i > 0 and macd_hist[i] > macd_hist[i-1]:
                    score += 1

                # RSI in bullish zone
                if 45 < rsi[i] < 80:
                    score += 1

                # Near or above 20-day high (breakout)
                if high_20[i] > 0 and close[i] > high_20[i] * 0.97:
                    score += 1

                # ADX trending
                if adx[i] > 20:
                    score += 1

                # Enter with 3+ score
                if score >= 3:
                    strength = min(score / 7.0, 1.0)
                    size = base_size * regime_mult * strength
                    signals.iloc[i, signals.columns.get_loc('signal')] = 1
                    signals.iloc[i, signals.columns.get_loc('position_size')] = size
                    in_position = True
                    entry_price = close[i]
                    highest_since_entry = close[i]
                    bars_held = 0

        return signals


class Strategy3_MeanReversionZScore:
    """
    Mean Reversion Z-Score Strategy

    Trades across all regimes but sized for mean reversion.
    Uses z-score of price vs 20-day mean, RSI confirmation, and Bollinger bands.
    Holds positions through reversion, exits at mean.

    Key: Active in MEAN_REVERTING and LOW_VOL regimes with large size,
    small opportunistic buys in other regimes on oversold.
    """
    name = "Mean Reversion Z"

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
        sma_bb, upper_bb, lower_bb = _bollinger(close, 20, 2.0)

        # Track for hold logic
        prev_signal = 0
        entry_z = 0

        warmup = 50
        for i in range(warmup, n):
            regime = regimes[i]
            regime_mult = _regime_size_mult(regime)
            base_size = 0.30

            # Z-score
            window = close[max(0, i-20):i+1]
            mu = np.mean(window)
            sigma = np.std(window)
            if sigma < 1e-10:
                signals.iloc[i, signals.columns.get_loc('signal')] = prev_signal
                if prev_signal > 0:
                    signals.iloc[i, signals.columns.get_loc('position_size')] = 0.1
                continue

            z = (close[i] - mu) / sigma

            sig = 0
            size = 0.0

            # Primary mean reversion signals
            if regime in (Regime.MEAN_REVERTING, Regime.LOW_VOL_ACCUMULATION):
                # Strong buy: z < -1.5 and RSI oversold
                if z < -1.5 and rsi[i] < 35:
                    sig = 1
                    strength = min(abs(z) / 2.5, 1.0)
                    size = base_size * regime_mult * strength
                    entry_z = z
                # Moderate buy: z < -1.0
                elif z < -1.0 and rsi[i] < 45:
                    sig = 1
                    size = base_size * regime_mult * 0.6
                    entry_z = z
                # Hold while reverting to mean
                elif prev_signal > 0 and z < 0.5:
                    sig = 1
                    # Scale down as we approach mean
                    revert_progress = 1.0 - (z - entry_z) / (0.5 - entry_z) if entry_z < 0 else 0.5
                    size = base_size * regime_mult * max(revert_progress * 0.5, 0.1)
                # Short overbought (conservative, mean revert only)
                elif z > 2.0 and rsi[i] > 75 and regime == Regime.MEAN_REVERTING:
                    sig = -1
                    size = base_size * 0.2 * regime_mult

            # Opportunistic in trending regimes
            elif regime == Regime.TRENDING_UP:
                if z < -1.5 and rsi[i] < 30:
                    sig = 1
                    size = base_size * regime_mult * 0.5  # Dip buy in uptrend
                elif prev_signal > 0 and z < 0:
                    sig = 1
                    size = base_size * regime_mult * 0.3

            elif regime == Regime.TRENDING_DOWN:
                if z < -2.0 and rsi[i] < 25:
                    sig = 1
                    size = base_size * regime_mult * 0.3  # Small bounce play

            elif regime == Regime.HIGH_VOL_CHAOS:
                if z < -2.5 and rsi[i] < 20:
                    sig = 1
                    size = base_size * regime_mult * 0.3  # Panic buy

            signals.iloc[i, signals.columns.get_loc('signal')] = sig
            signals.iloc[i, signals.columns.get_loc('position_size')] = max(size, 0)
            prev_signal = sig

        return signals


class Strategy4_FundingOIDivergence:
    """
    Funding Rate + Open Interest Divergence Strategy

    Uses funding rate and OI from the data (synthetic for backtesting,
    real from exchange APIs in live trading).

    Signal matrix:
    - High funding + rising OI + flat price → SHORT (longs overleveraged)
    - Low funding + rising OI + flat price → LONG (shorts trapped)
    - Extreme funding + falling OI → deleveraging, go flat
    - Neutral funding + trend alignment → follow trend with size

    Works in ALL regimes as a contrarian + trend overlay.
    """
    name = "Funding+OI Divergence"

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
        ema_20 = _ema(close, 20)

        # Use funding_rate and open_interest from dataframe if available
        has_funding = 'funding_rate' in df.columns
        has_oi = 'open_interest' in df.columns

        # Build funding/OI arrays
        if has_funding:
            funding = df['funding_rate'].values.astype(float)
        else:
            # Proxy from momentum
            funding = np.zeros(n)
            for i in range(7, n):
                ret_7d = (close[i] / close[i-7] - 1) if close[i-7] > 0 else 0
                vol_ratio = volume[i] / (np.mean(volume[max(0,i-20):i]) + 1e-10)
                funding[i] = ret_7d * 0.05 + (vol_ratio - 1) * 0.01

        if has_oi:
            oi = df['open_interest'].values.astype(float)
        else:
            oi = volume.copy()  # Fallback

        # OI change rate
        oi_change = np.zeros(n)
        for i in range(14, n):
            oi_prev = np.mean(oi[max(0,i-14):i])
            if oi_prev > 0:
                oi_change[i] = (oi[i] / oi_prev) - 1.0

        base_size = 0.30
        prev_signal = 0

        warmup = 50
        for i in range(warmup, n):
            regime = regimes[i]
            regime_mult = _regime_size_mult(regime)

            # Funding rate z-score (rolling 30-day)
            fund_window = funding[max(0, i-30):i+1]
            if len(fund_window) < 15:
                continue
            fund_mean = np.mean(fund_window)
            fund_std = np.std(fund_window)
            if fund_std < 1e-10:
                fund_z = 0
            else:
                fund_z = (funding[i] - fund_mean) / fund_std

            # Price momentum
            ret_5d = (close[i] / close[max(0, i-5)] - 1) if close[max(0, i-5)] > 0 else 0
            price_flat = abs(ret_5d) < 0.03

            sig = 0
            size = 0.0

            # Core signal matrix
            if fund_z > 1.5 and oi_change[i] > 0.03:
                if price_flat:
                    # Longs overleveraged, short
                    sig = -1
                    strength = min(abs(fund_z) / 3.0, 1.0)
                    size = base_size * regime_mult * strength * 0.4
                elif ret_5d < -0.03:
                    # Already dropping with high funding, follow
                    sig = -1
                    size = base_size * regime_mult * 0.3

            elif fund_z < -1.5 and oi_change[i] > 0.03:
                if price_flat:
                    # Shorts trapped, long
                    sig = 1
                    strength = min(abs(fund_z) / 3.0, 1.0)
                    size = base_size * regime_mult * strength * 0.6
                elif ret_5d > 0.03:
                    # Already rising with low funding, follow
                    sig = 1
                    size = base_size * regime_mult * 0.5

            elif abs(fund_z) > 2.5 and oi_change[i] < -0.05:
                # Deleveraging event, go flat
                sig = 0

            # Neutral funding: follow trend
            elif abs(fund_z) < 0.8:
                if regime in (Regime.TRENDING_UP, Regime.LOW_VOL_ACCUMULATION):
                    if np.isfinite(ema_20[i]) and close[i] > ema_20[i]:
                        sig = 1
                        size = base_size * regime_mult * 0.4
                elif regime == Regime.MEAN_REVERTING:
                    if rsi[i] < 35:
                        sig = 1
                        size = base_size * regime_mult * 0.3

            # Hold logic
            if sig == 0 and prev_signal != 0:
                # Check if exit conditions really warrant closing
                if prev_signal > 0 and rsi[i] < 65 and abs(fund_z) < 2.0:
                    sig = prev_signal
                    size = signals['position_size'].iloc[i-1] * 0.9
                elif prev_signal < 0 and rsi[i] > 35 and abs(fund_z) < 2.0:
                    sig = prev_signal
                    size = signals['position_size'].iloc[i-1] * 0.9

            signals.iloc[i, signals.columns.get_loc('signal')] = sig
            signals.iloc[i, signals.columns.get_loc('position_size')] = max(size, 0)
            prev_signal = sig

        return signals


class Strategy5_MultiFactorEnsemble:
    """
    Multi-Factor Ensemble with Performance-Weighted Voting

    Combines all 4 strategies with dynamic weighting based on recent Sharpe.
    Trades when 2+ strategies agree. Size is weighted average.

    This is the "meta-strategy" — it benefits from diversification across
    the other strategies' different alpha sources.
    """
    name = "Multi-Factor Ensemble"

    def __init__(self):
        self.sub_strategies = [
            Strategy1_HMMRegimeAdaptive(),
            Strategy2_MomentumTrendFollower(),
            Strategy3_MeanReversionZScore(),
            Strategy4_FundingOIDivergence(),
        ]

    def generate_signals(self, df, regimes=None, features=None):
        if regimes is None:
            detector = RegimeDetector()
            regimes, features = detector.detect(df)

        # Generate signals from all sub-strategies
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
        signals = pd.DataFrame(index=df.index)
        signals['signal'] = 0
        signals['position_size'] = 0.0
        signals['regime'] = regimes

        # Dynamic weights
        weights = np.ones(len(sub_signals)) / len(sub_signals)
        recent_returns = [[] for _ in sub_signals]

        for i in range(1, n):
            # Track sub-strategy returns for weighting
            price_return = df['close'].iloc[i] / df['close'].iloc[i-1] - 1

            for j, sig in enumerate(sub_signals):
                s = sig['signal'].iloc[i-1] if i-1 < len(sig) else 0
                ps = sig['position_size'].iloc[i-1] if i-1 < len(sig) else 0
                recent_returns[j].append(s * ps * price_return)

            # Reweight every 20 bars
            if i > 60 and i % 20 == 0:
                for j in range(len(sub_signals)):
                    rets = np.array(recent_returns[j][-60:])
                    if len(rets) > 0 and np.std(rets) > 1e-10:
                        sharpe = np.mean(rets) / np.std(rets)
                        weights[j] = max(sharpe + 1, 0.1)
                    else:
                        weights[j] = 0.25
                total = weights.sum()
                if total > 0:
                    weights = weights / total

            # Count votes
            long_votes = 0
            short_votes = 0
            long_sizes = []
            short_sizes = []

            for j, sig in enumerate(sub_signals):
                if i >= len(sig):
                    continue
                s = sig['signal'].iloc[i]
                ps = sig['position_size'].iloc[i]
                if s > 0:
                    long_votes += 1
                    long_sizes.append(ps * weights[j])
                elif s < 0:
                    short_votes += 1
                    short_sizes.append(ps * weights[j])

            # 2+ agreement for entry
            if long_votes >= 2 and long_votes > short_votes:
                avg_size = np.mean(long_sizes) if long_sizes else 0
                signals.iloc[i, signals.columns.get_loc('signal')] = 1
                signals.iloc[i, signals.columns.get_loc('position_size')] = min(avg_size * 1.5, 0.50)

            elif short_votes >= 2 and short_votes > long_votes:
                avg_size = np.mean(short_sizes) if short_sizes else 0
                signals.iloc[i, signals.columns.get_loc('signal')] = -1
                signals.iloc[i, signals.columns.get_loc('position_size')] = min(avg_size * 1.2, 0.30)

            # 1 signal with no opposition = small position
            elif long_votes >= 1 and short_votes == 0:
                avg_size = np.mean(long_sizes) if long_sizes else 0
                signals.iloc[i, signals.columns.get_loc('signal')] = 1
                signals.iloc[i, signals.columns.get_loc('position_size')] = min(avg_size, 0.25)

            elif short_votes >= 1 and long_votes == 0:
                avg_size = np.mean(short_sizes) if short_sizes else 0
                signals.iloc[i, signals.columns.get_loc('signal')] = -1
                signals.iloc[i, signals.columns.get_loc('position_size')] = min(avg_size * 0.5, 0.15)

        return signals


def get_all_v2_strategies():
    """Return all v2 strategy instances."""
    return [
        Strategy1_HMMRegimeAdaptive(),
        Strategy2_MomentumTrendFollower(),
        Strategy3_MeanReversionZScore(),
        Strategy4_FundingOIDivergence(),
        Strategy5_MultiFactorEnsemble(),
    ]
