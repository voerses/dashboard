"""
Base strategies from the original backtest (for comparison).
"""
import numpy as np
import pandas as pd
from indicators import compute_all_indicators


class RSIMACDMomentum:
    name = "RSI+MACD Momentum"

    def generate_signals(self, df):
        ind = compute_all_indicators(df)
        n = len(df)
        signals = pd.DataFrame(index=df.index)
        signals['signal'] = 0
        signals['position_size'] = 0.0

        for i in range(50, n):
            rsi = ind['rsi_14'].iloc[i] if 'rsi_14' in ind.columns else 50
            macd_hist = ind['macd_hist_12_26'].iloc[i] if 'macd_hist_12_26' in ind.columns else 0

            if np.isnan(rsi) or np.isnan(macd_hist):
                continue

            if rsi > 55 and macd_hist > 0:
                signals.iloc[i, signals.columns.get_loc('signal')] = 1
                signals.iloc[i, signals.columns.get_loc('position_size')] = 0.25
            elif rsi < 45 and macd_hist < 0:
                signals.iloc[i, signals.columns.get_loc('signal')] = -1
                signals.iloc[i, signals.columns.get_loc('position_size')] = 0.15
            # Stay long if RSI > 50 with positive MACD (relaxed hold condition)
            elif rsi > 50 and macd_hist > 0:
                signals.iloc[i, signals.columns.get_loc('signal')] = 1
                signals.iloc[i, signals.columns.get_loc('position_size')] = 0.20

        return signals


class AdaptiveRegime:
    name = "Adaptive Regime"

    def generate_signals(self, df):
        ind = compute_all_indicators(df)
        n = len(df)
        signals = pd.DataFrame(index=df.index)
        signals['signal'] = 0
        signals['position_size'] = 0.0

        for i in range(100, n):
            adx = ind['adx_14'].iloc[i] if 'adx_14' in ind.columns else 25
            rsi = ind['rsi_14'].iloc[i] if 'rsi_14' in ind.columns else 50
            ema_20 = ind['ema_20'].iloc[i] if 'ema_20' in ind.columns else df['close'].iloc[i]
            ema_50 = ind['ema_50'].iloc[i] if 'ema_50' in ind.columns else df['close'].iloc[i]

            if any(np.isnan(v) for v in [adx, rsi, ema_20, ema_50]):
                continue

            if adx > 25:  # Trending
                if ema_20 > ema_50:
                    signals.iloc[i, signals.columns.get_loc('signal')] = 1
                    signals.iloc[i, signals.columns.get_loc('position_size')] = 0.3
                else:
                    signals.iloc[i, signals.columns.get_loc('signal')] = -1
                    signals.iloc[i, signals.columns.get_loc('position_size')] = 0.3
            else:  # Mean reverting
                if rsi < 30:
                    signals.iloc[i, signals.columns.get_loc('signal')] = 1
                    signals.iloc[i, signals.columns.get_loc('position_size')] = 0.2
                elif rsi > 70:
                    signals.iloc[i, signals.columns.get_loc('signal')] = -1
                    signals.iloc[i, signals.columns.get_loc('position_size')] = 0.2

        return signals


class SignalWeighted:
    """The Signal-Weighted strategy that crushed it on SOL."""
    name = "Signal Weighted"

    def generate_signals(self, df):
        ind = compute_all_indicators(df)
        n = len(df)
        signals = pd.DataFrame(index=df.index)
        signals['signal'] = 0
        signals['position_size'] = 0.0

        # Use momentum + volume indicators
        momentum_cols = [c for c in ind.columns if any(p in c for p in ['rsi', 'macd_hist', 'momentum', 'cci', 'roc'])]

        for i in range(60, n):
            # Equal-weighted signal
            bullish = 0
            total = 0
            for col in momentum_cols:
                val = ind[col].iloc[i]
                if np.isnan(val):
                    continue
                # Normalize
                window = ind[col].iloc[max(0, i-40):i].values
                window = window[~np.isnan(window)]
                if len(window) < 10:
                    continue
                z = (val - np.mean(window)) / (np.std(window) + 1e-10)
                total += 1
                if z > 0.3:
                    bullish += 1
                elif z < -0.3:
                    bullish -= 1

            if total > 0:
                ratio = bullish / total
                if ratio > 0.3:
                    signals.iloc[i, signals.columns.get_loc('signal')] = 1
                    signals.iloc[i, signals.columns.get_loc('position_size')] = 0.25
                elif ratio < -0.3:
                    signals.iloc[i, signals.columns.get_loc('signal')] = -1
                    signals.iloc[i, signals.columns.get_loc('position_size')] = 0.25

        return signals
