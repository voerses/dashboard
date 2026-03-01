"""
Causal Composite Strategy

Uses Transfer Entropy, Mutual Information, Persistent Homology, and CCM
to build a dynamically-weighted trading strategy targeting 60%+ win rate.

Key improvements over basic composite:
1. Only uses indicators with PROVEN causal relationship to returns (not just correlation)
2. Weights indicators by their causal strength (TE + MI + CCM)
3. Uses topological regime detection to switch strategy modes
4. Filters trades by causal confidence threshold
"""
import numpy as np
import pandas as pd
from causal_engine import (
    compute_causal_scores, TopologicalRegimeDetector,
    rolling_transfer_entropy, rolling_mutual_information,
    transfer_entropy, mutual_information
)
from indicators import compute_all_indicators


class CausalCompositeStrategy:
    """
    Strategy that uses causal analysis to select and weight indicators.

    Phase 1: Compute causal scores for all 109 indicators
    Phase 2: Select top-N indicators by composite causal score
    Phase 3: Dynamically weight signals by rolling causal strength
    Phase 4: Filter by topological regime (avoid CHAOTIC regimes)
    Phase 5: Require minimum causal confidence for entry
    """

    def __init__(self, top_n=15, causal_lookback=200, min_confidence=0.15,
                 position_size=0.25, regime_filter=True):
        self.top_n = top_n
        self.causal_lookback = causal_lookback
        self.min_confidence = min_confidence
        self.position_size = position_size
        self.regime_filter = regime_filter
        self.name = "Causal Composite"

    def generate_signals(self, df):
        """Generate trading signals using causal indicator weighting."""
        # Step 1: Compute all indicators
        indicators = compute_all_indicators(df)
        close = df['close']
        returns = np.log(close / close.shift(1)).fillna(0).values

        n = len(df)
        signals = pd.DataFrame(index=df.index)
        signals['signal'] = 0
        signals['position_size'] = 0.0

        # Step 2: Compute causal scores (on training window to avoid look-ahead)
        train_end = min(self.causal_lookback + 200, n // 3)
        if train_end < self.causal_lookback + 50:
            train_end = min(n // 2, n - 100)
        causal_scores = compute_causal_scores(
            indicators.iloc[:train_end], close.iloc[:train_end],
            horizon=5, window=min(self.causal_lookback, train_end - 50), top_n=self.top_n
        )

        if len(causal_scores) == 0:
            return signals

        # Select top indicators by causal composite score
        top_indicators = causal_scores.head(self.top_n)
        selected_names = top_indicators['indicator'].tolist()
        selected_weights = top_indicators['composite_score'].values
        selected_weights = selected_weights / selected_weights.sum()
        selected_directions = top_indicators['direction'].values

        # Step 3: Topological regime detection
        if self.regime_filter:
            regime_detector = TopologicalRegimeDetector(window=50, embedding_dim=3)
            regimes, tda_features = regime_detector.detect_regimes(close.values, returns)
        else:
            regimes = np.ones(n)

        # Step 4: Generate signals using causally-weighted indicators
        lookback = 60
        for i in range(max(lookback, train_end), n):
            # Skip chaotic regimes (topological pre-crash warning)
            if self.regime_filter and regimes[i] == 4:
                signals.iloc[i, signals.columns.get_loc('signal')] = 0
                continue

            # Compute weighted signal from causal indicators
            weighted_signal = 0.0
            total_weight = 0.0
            agreement_count = 0

            for j, ind_name in enumerate(selected_names):
                if ind_name not in indicators.columns:
                    continue

                ind_val = indicators[ind_name].iloc[i]
                if np.isnan(ind_val):
                    continue

                # Normalize indicator to [-1, 1] using recent window
                window_vals = indicators[ind_name].iloc[max(0, i-lookback):i].values
                window_vals = window_vals[~np.isnan(window_vals)]
                if len(window_vals) < 10:
                    continue

                mu = np.mean(window_vals)
                sigma = np.std(window_vals) + 1e-10
                z = (ind_val - mu) / sigma

                # Clip to [-3, 3] and scale to [-1, 1]
                z = np.clip(z, -3, 3) / 3

                # Apply direction from causal analysis
                directed_signal = z * selected_directions[j]

                weighted_signal += directed_signal * selected_weights[j]
                total_weight += selected_weights[j]

                if abs(directed_signal) > 0.1:
                    agreement_count += 1

            if total_weight < 1e-10:
                continue

            final_signal = weighted_signal / total_weight

            # Confidence = agreement ratio among selected indicators
            confidence = agreement_count / len(selected_names)

            # Regime-adjusted thresholds
            regime = regimes[i]
            if regime == 1:  # TREND_UP
                long_threshold = 0.05
                short_threshold = -0.3
            elif regime == 2:  # TREND_DOWN
                long_threshold = 0.3
                short_threshold = -0.05
            elif regime == 3:  # MEAN_REVERT
                long_threshold = -0.15  # Buy on oversold
                short_threshold = 0.15   # Sell on overbought
            else:
                long_threshold = 0.15
                short_threshold = -0.15

            # Signal generation with confidence filter
            if regime == 3:
                # Mean reversion mode
                if final_signal < long_threshold and confidence >= self.min_confidence * 0.7:
                    signals.iloc[i, signals.columns.get_loc('signal')] = 1
                    signals.iloc[i, signals.columns.get_loc('position_size')] = self.position_size * min(confidence * 2, 1.0)
                elif final_signal > short_threshold and confidence >= self.min_confidence * 0.7:
                    signals.iloc[i, signals.columns.get_loc('signal')] = -1
                    signals.iloc[i, signals.columns.get_loc('position_size')] = self.position_size * min(confidence * 2, 1.0)
            else:
                # Trend following mode
                if final_signal > long_threshold and confidence >= self.min_confidence:
                    signals.iloc[i, signals.columns.get_loc('signal')] = 1
                    signals.iloc[i, signals.columns.get_loc('position_size')] = self.position_size * min(confidence * 2, 1.0)
                elif final_signal < short_threshold and confidence >= self.min_confidence:
                    signals.iloc[i, signals.columns.get_loc('signal')] = -1
                    signals.iloc[i, signals.columns.get_loc('position_size')] = self.position_size * min(confidence * 2, 1.0)

        return signals


class TransferEntropyMomentumStrategy:
    """
    Uses Transfer Entropy to identify which momentum indicators
    are currently CAUSING price movements, then trades based only
    on those indicators.

    Key innovation: TE is recalculated every recalc_period days,
    so the strategy adapts as market dynamics change.
    """

    def __init__(self, recalc_period=30, position_size=0.3, min_te=0.01):
        self.recalc_period = recalc_period
        self.position_size = position_size
        self.min_te = min_te
        self.name = "TE Momentum"

    def generate_signals(self, df):
        indicators = compute_all_indicators(df)
        close = df['close']
        returns = np.log(close / close.shift(1)).fillna(0).values

        n = len(df)
        signals = pd.DataFrame(index=df.index)
        signals['signal'] = 0
        signals['position_size'] = 0.0

        # Momentum indicators to track with TE
        momentum_indicators = [c for c in indicators.columns
                               if any(p in c for p in ['rsi', 'macd', 'momentum', 'cci',
                                                        'stoch', 'tsi', 'roc', 'williams'])]

        current_weights = {}
        window = 150

        for i in range(window, n):
            # Recalculate TE weights periodically
            if i == window or (i - window) % self.recalc_period == 0:
                current_weights = {}
                for ind_name in momentum_indicators:
                    if ind_name not in indicators.columns:
                        continue
                    ind_vals = indicators[ind_name].values[max(0, i-window):i]
                    ret_vals = returns[max(0, i-window):i]
                    te = transfer_entropy(ind_vals, ret_vals, lag=1, bins=5)
                    if te > self.min_te:
                        current_weights[ind_name] = te

                # Normalize weights
                total = sum(current_weights.values())
                if total > 0:
                    current_weights = {k: v/total for k, v in current_weights.items()}

            if not current_weights:
                continue

            # Compute weighted signal from TE-selected indicators
            weighted_signal = 0.0
            lookback = 40

            for ind_name, weight in current_weights.items():
                ind_val = indicators[ind_name].iloc[i]
                if np.isnan(ind_val):
                    continue

                window_vals = indicators[ind_name].iloc[max(0, i-lookback):i].values
                window_vals = window_vals[~np.isnan(window_vals)]
                if len(window_vals) < 10:
                    continue

                mu = np.mean(window_vals)
                sigma = np.std(window_vals) + 1e-10
                z = np.clip((ind_val - mu) / sigma, -3, 3) / 3

                weighted_signal += z * weight

            # Trade based on weighted signal
            if weighted_signal > 0.1:
                signals.iloc[i, signals.columns.get_loc('signal')] = 1
                signals.iloc[i, signals.columns.get_loc('position_size')] = self.position_size
            elif weighted_signal < -0.1:
                signals.iloc[i, signals.columns.get_loc('signal')] = -1
                signals.iloc[i, signals.columns.get_loc('position_size')] = self.position_size * 0.5

        return signals


class TopologicalTrendStrategy:
    """
    Uses persistent homology to detect regime shifts BEFORE they happen,
    combined with trend-following during clean trends.

    Key insight from TDA research: topological complexity increases
    before market crashes. High Betti-1 (loops) = instability.
    """

    def __init__(self, position_size=0.3, vol_scale=True):
        self.position_size = position_size
        self.vol_scale = vol_scale
        self.name = "Topological Trend"

    def generate_signals(self, df):
        indicators = compute_all_indicators(df)
        close = df['close']
        returns = np.log(close / close.shift(1)).fillna(0).values

        n = len(df)
        signals = pd.DataFrame(index=df.index)
        signals['signal'] = 0
        signals['position_size'] = 0.0

        # Detect regimes using TDA
        detector = TopologicalRegimeDetector(window=50, embedding_dim=3)
        regimes, tda_features = detector.detect_regimes(close.values, returns)

        # Trend indicators
        ema_20 = indicators['ema_20'].values if 'ema_20' in indicators.columns else np.full(n, np.nan)
        ema_50 = indicators['ema_50'].values if 'ema_50' in indicators.columns else np.full(n, np.nan)
        adx = indicators['adx_14'].values if 'adx_14' in indicators.columns else np.full(n, np.nan)
        rsi = indicators['rsi_14'].values if 'rsi_14' in indicators.columns else np.full(n, np.nan)

        for i in range(60, n):
            regime = regimes[i]

            # CHAOTIC regime: go flat (avoid the mess)
            if regime == 4:
                signals.iloc[i, signals.columns.get_loc('signal')] = 0
                signals.iloc[i, signals.columns.get_loc('position_size')] = 0
                continue

            # Vol scaling
            if self.vol_scale and i >= 30:
                recent_vol = np.std(returns[i-30:i]) * np.sqrt(365)
                target_vol = 0.5  # Target 50% annual vol
                vol_scalar = min(target_vol / (recent_vol + 0.01), 2.0)
            else:
                vol_scalar = 1.0

            # TREND_UP: follow with confirmation
            if regime == 1:
                if (not np.isnan(ema_20[i]) and not np.isnan(ema_50[i]) and
                    ema_20[i] > ema_50[i] and
                    (np.isnan(adx[i]) or adx[i] > 20)):
                    signals.iloc[i, signals.columns.get_loc('signal')] = 1
                    signals.iloc[i, signals.columns.get_loc('position_size')] = self.position_size * vol_scalar

            # TREND_DOWN: go flat (avoid shorting crypto in trending markets)
            elif regime == 2:
                signals.iloc[i, signals.columns.get_loc('signal')] = 0
                signals.iloc[i, signals.columns.get_loc('position_size')] = 0

            # MEAN_REVERT: RSI-based mean reversion
            elif regime == 3:
                if not np.isnan(rsi[i]):
                    if rsi[i] < 30:
                        signals.iloc[i, signals.columns.get_loc('signal')] = 1
                        signals.iloc[i, signals.columns.get_loc('position_size')] = self.position_size * vol_scalar * 0.5
                    elif rsi[i] > 70:
                        signals.iloc[i, signals.columns.get_loc('signal')] = -1
                        signals.iloc[i, signals.columns.get_loc('position_size')] = self.position_size * vol_scalar * 0.5

        return signals


class CausalEnsembleStrategy:
    """
    Ensemble of all three causal strategies with dynamic weighting.

    Weights each sub-strategy by its recent risk-adjusted performance.
    Requires majority agreement for entries (2 of 3 agree).
    """

    def __init__(self, position_size=0.25, reweight_period=30):
        self.position_size = position_size
        self.reweight_period = reweight_period
        self.name = "Causal Ensemble"

        self.sub_strategies = [
            CausalCompositeStrategy(top_n=12, position_size=0.3, min_confidence=0.35),
            TransferEntropyMomentumStrategy(recalc_period=25, position_size=0.3),
            TopologicalTrendStrategy(position_size=0.3, vol_scale=True)
        ]

    def generate_signals(self, df):
        # Generate signals from all sub-strategies
        sub_signals = []
        for strat in self.sub_strategies:
            try:
                sig = strat.generate_signals(df)
                sub_signals.append(sig)
            except Exception as e:
                print(f"  Sub-strategy {strat.name} failed: {e}")
                # Create neutral signals on failure
                neutral = pd.DataFrame(index=df.index)
                neutral['signal'] = 0
                neutral['position_size'] = 0.0
                sub_signals.append(neutral)

        n = len(df)
        signals = pd.DataFrame(index=df.index)
        signals['signal'] = 0
        signals['position_size'] = 0.0

        # Majority voting with performance-weighted combination
        weights = np.ones(len(sub_signals)) / len(sub_signals)
        recent_returns = [[] for _ in sub_signals]

        for i in range(1, n):
            # Track sub-strategy returns
            price_return = df['close'].iloc[i] / df['close'].iloc[i-1] - 1
            for j, sig in enumerate(sub_signals):
                s = sig['signal'].iloc[i-1]
                recent_returns[j].append(s * price_return)

            # Reweight based on recent Sharpe
            if i > 60 and i % self.reweight_period == 0:
                for j in range(len(sub_signals)):
                    rets = np.array(recent_returns[j][-60:])
                    if np.std(rets) > 1e-10:
                        sharpe = np.mean(rets) / np.std(rets)
                        weights[j] = max(sharpe + 1, 0.1)  # +1 to keep positive
                    else:
                        weights[j] = 0.1
                weights = weights / weights.sum()

            # Majority vote
            votes = sum(1 for sig in sub_signals if sig['signal'].iloc[i] > 0)
            down_votes = sum(1 for sig in sub_signals if sig['signal'].iloc[i] < 0)

            if votes >= 2:
                signals.iloc[i, signals.columns.get_loc('signal')] = 1
                # Position size = weighted average
                avg_size = sum(
                    sig['position_size'].iloc[i] * w
                    for sig, w in zip(sub_signals, weights)
                    if sig['signal'].iloc[i] > 0
                )
                signals.iloc[i, signals.columns.get_loc('position_size')] = min(avg_size, self.position_size)
            elif down_votes >= 2:
                signals.iloc[i, signals.columns.get_loc('signal')] = -1
                avg_size = sum(
                    sig['position_size'].iloc[i] * w
                    for sig, w in zip(sub_signals, weights)
                    if sig['signal'].iloc[i] < 0
                )
                signals.iloc[i, signals.columns.get_loc('position_size')] = min(avg_size, self.position_size)

        return signals


def get_all_causal_strategies():
    """Return all causal strategy instances."""
    return [
        CausalCompositeStrategy(top_n=15, min_confidence=0.15, position_size=0.25),
        TransferEntropyMomentumStrategy(recalc_period=25, position_size=0.3, min_te=0.005),
        TopologicalTrendStrategy(position_size=0.3, vol_scale=True),
        CausalEnsembleStrategy(position_size=0.25, reweight_period=30),
    ]
