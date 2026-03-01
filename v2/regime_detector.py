"""
Advanced Regime Detection Engine

Combines multiple methods:
1. HMM (3-state Gaussian) on returns + vol + volume
2. Bayesian Online Changepoint Detection (BOCPD)
3. Volatility regime clustering (realized vol, vol-of-vol, RV ratio)
4. VPIN (Volume-Synchronized Probability of Informed Trading)
5. Funding rate regime signals (simulated for backtest)

Final regime classification:
- TRENDING_UP: Strong bullish momentum
- TRENDING_DOWN: Strong bearish momentum
- MEAN_REVERTING: Sideways, range-bound
- HIGH_VOL_CHAOS: Crisis/cascade environment
- LOW_VOL_ACCUMULATION: Quiet, pre-breakout compression
"""

import numpy as np
import pandas as pd
from scipy import stats
from enum import IntEnum


class Regime(IntEnum):
    TRENDING_UP = 1
    TRENDING_DOWN = 2
    MEAN_REVERTING = 3
    HIGH_VOL_CHAOS = 4
    LOW_VOL_ACCUMULATION = 5


class GaussianHMM:
    """
    Lightweight 3-state Gaussian HMM using EM algorithm.
    No external dependencies (replaces hmmlearn).
    """

    def __init__(self, n_states=3, n_iter=50, tol=1e-4):
        self.n_states = n_states
        self.n_iter = n_iter
        self.tol = tol
        self.means_ = None
        self.covars_ = None
        self.transmat_ = None
        self.startprob_ = None

    def _init_params(self, X):
        n_samples, n_features = X.shape
        # K-means initialization
        indices = np.linspace(0, n_samples - 1, self.n_states + 2, dtype=int)[1:-1]
        sorted_idx = np.argsort(X[:, 0])
        self.means_ = X[sorted_idx[indices]].copy()

        self.covars_ = np.array([np.var(X, axis=0) + 1e-6 for _ in range(self.n_states)])
        self.transmat_ = np.full((self.n_states, self.n_states), 1.0 / self.n_states)
        np.fill_diagonal(self.transmat_, 0.9)
        self.transmat_ /= self.transmat_.sum(axis=1, keepdims=True)
        self.startprob_ = np.full(self.n_states, 1.0 / self.n_states)

    def _log_gaussian(self, X, mean, var):
        """Log probability of X under diagonal Gaussian."""
        n_features = len(mean)
        log_prob = -0.5 * (n_features * np.log(2 * np.pi) +
                          np.sum(np.log(var + 1e-10)) +
                          np.sum((X - mean) ** 2 / (var + 1e-10), axis=1))
        return log_prob

    def _forward(self, log_emission):
        """Forward algorithm in log space."""
        T = len(log_emission)
        log_alpha = np.full((T, self.n_states), -np.inf)
        log_alpha[0] = np.log(self.startprob_ + 1e-300) + log_emission[0]

        log_trans = np.log(self.transmat_ + 1e-300)
        for t in range(1, T):
            for j in range(self.n_states):
                log_alpha[t, j] = np.logaddexp.reduce(
                    log_alpha[t-1] + log_trans[:, j]
                ) + log_emission[t, j]
        return log_alpha

    def _backward(self, log_emission):
        """Backward algorithm in log space."""
        T = len(log_emission)
        log_beta = np.full((T, self.n_states), -np.inf)
        log_beta[-1] = 0

        log_trans = np.log(self.transmat_ + 1e-300)
        for t in range(T - 2, -1, -1):
            for i in range(self.n_states):
                log_beta[t, i] = np.logaddexp.reduce(
                    log_trans[i] + log_emission[t+1] + log_beta[t+1]
                )
        return log_beta

    def fit(self, X):
        """Fit HMM using EM algorithm."""
        X = np.asarray(X, dtype=np.float64)
        if X.ndim == 1:
            X = X.reshape(-1, 1)

        self._init_params(X)
        T, n_features = X.shape

        prev_ll = -np.inf
        for iteration in range(self.n_iter):
            # E-step
            log_emission = np.zeros((T, self.n_states))
            for k in range(self.n_states):
                log_emission[:, k] = self._log_gaussian(X, self.means_[k], self.covars_[k])

            log_alpha = self._forward(log_emission)
            log_beta = self._backward(log_emission)

            # Log-likelihood
            ll = np.logaddexp.reduce(log_alpha[-1])
            if abs(ll - prev_ll) < self.tol:
                break
            prev_ll = ll

            # Posterior (gamma)
            log_gamma = log_alpha + log_beta
            log_gamma -= np.logaddexp.reduce(log_gamma, axis=1, keepdims=True)
            gamma = np.exp(log_gamma)
            gamma = np.clip(gamma, 1e-300, None)

            # M-step
            for k in range(self.n_states):
                weight = gamma[:, k]
                total_weight = weight.sum() + 1e-10
                self.means_[k] = (weight[:, None] * X).sum(axis=0) / total_weight
                diff = X - self.means_[k]
                self.covars_[k] = (weight[:, None] * diff ** 2).sum(axis=0) / total_weight
                self.covars_[k] = np.maximum(self.covars_[k], 1e-6)

            self.startprob_ = gamma[0] / gamma[0].sum()

            # Transition matrix
            log_trans = np.log(self.transmat_ + 1e-300)
            for i in range(self.n_states):
                for j in range(self.n_states):
                    log_xi_sum = -np.inf
                    for t in range(T - 1):
                        log_xi = (log_alpha[t, i] + log_trans[i, j] +
                                 log_emission[t+1, j] + log_beta[t+1, j])
                        log_xi_sum = np.logaddexp(log_xi_sum, log_xi)
                    self.transmat_[i, j] = np.exp(log_xi_sum)
            self.transmat_ /= self.transmat_.sum(axis=1, keepdims=True) + 1e-10

        return self

    def predict(self, X):
        """Viterbi decoding for most likely state sequence."""
        X = np.asarray(X, dtype=np.float64)
        if X.ndim == 1:
            X = X.reshape(-1, 1)

        T = len(X)
        log_emission = np.zeros((T, self.n_states))
        for k in range(self.n_states):
            log_emission[:, k] = self._log_gaussian(X, self.means_[k], self.covars_[k])

        # Viterbi
        log_delta = np.log(self.startprob_ + 1e-300) + log_emission[0]
        psi = np.zeros((T, self.n_states), dtype=int)
        log_trans = np.log(self.transmat_ + 1e-300)

        for t in range(1, T):
            for j in range(self.n_states):
                candidates = log_delta + log_trans[:, j]
                psi[t, j] = np.argmax(candidates)
                log_delta_new = candidates[psi[t, j]] + log_emission[t, j]
                log_delta[j] = log_delta_new if np.isfinite(log_delta_new) else -1e10

        # Backtrack
        states = np.zeros(T, dtype=int)
        states[-1] = np.argmax(log_delta)
        for t in range(T - 2, -1, -1):
            states[t] = psi[t + 1, states[t + 1]]

        return states

    def predict_proba(self, X):
        """Return posterior probabilities for each state."""
        X = np.asarray(X, dtype=np.float64)
        if X.ndim == 1:
            X = X.reshape(-1, 1)

        T = len(X)
        log_emission = np.zeros((T, self.n_states))
        for k in range(self.n_states):
            log_emission[:, k] = self._log_gaussian(X, self.means_[k], self.covars_[k])

        log_alpha = self._forward(log_emission)
        log_beta = self._backward(log_emission)

        log_gamma = log_alpha + log_beta
        log_gamma -= np.logaddexp.reduce(log_gamma, axis=1, keepdims=True)
        return np.exp(log_gamma)


class BOCPDetector:
    """
    Bayesian Online Changepoint Detection.
    Detects when statistical properties of data change.
    Uses Student-t likelihood for robustness to fat tails.
    """

    def __init__(self, hazard_lambda=200, alpha0=0.1, beta0=0.01, kappa0=1.0, mu0=0.0):
        self.hazard_lambda = hazard_lambda
        self.alpha0 = alpha0
        self.beta0 = beta0
        self.kappa0 = kappa0
        self.mu0 = mu0

    def detect(self, data):
        """
        Returns changepoint probability at each time step.
        """
        T = len(data)
        # Run length probabilities (truncated)
        max_run = min(T, 500)
        R = np.zeros((T + 1, max_run + 1))
        R[0, 0] = 1.0

        # Sufficient statistics for Student-t
        mu = np.full(max_run + 1, self.mu0)
        kappa = np.full(max_run + 1, self.kappa0)
        alpha = np.full(max_run + 1, self.alpha0)
        beta = np.full(max_run + 1, self.beta0)

        changepoint_prob = np.zeros(T)

        for t in range(T):
            x = data[t]

            # Predictive probability (Student-t)
            df = 2 * alpha[:max_run]
            scale = np.sqrt(beta[:max_run] * (kappa[:max_run] + 1) /
                          (alpha[:max_run] * kappa[:max_run]) + 1e-10)

            pred_prob = np.zeros(max_run)
            valid = (df > 0) & (scale > 0) & np.isfinite(df) & np.isfinite(scale)
            if np.any(valid):
                pred_prob[valid] = stats.t.pdf((x - mu[:max_run][valid]) / scale[valid],
                                               df[valid]) / (scale[valid] + 1e-10)
            pred_prob = np.maximum(pred_prob, 1e-300)

            # Hazard function (constant)
            H = 1.0 / self.hazard_lambda

            # Growth probabilities
            growth = R[t, :max_run] * pred_prob * (1 - H)
            # Changepoint probability
            cp = np.sum(R[t, :max_run] * pred_prob * H)

            R[t + 1, 0] = cp
            R[t + 1, 1:max_run + 1] = growth[:max_run]

            # Normalize
            total = R[t + 1, :max_run + 1].sum()
            if total > 0:
                R[t + 1, :max_run + 1] /= total

            changepoint_prob[t] = R[t + 1, 0]

            # Update sufficient statistics
            new_mu = (kappa[:max_run] * mu[:max_run] + x) / (kappa[:max_run] + 1)
            new_kappa = kappa[:max_run] + 1
            new_alpha = alpha[:max_run] + 0.5
            new_beta = (beta[:max_run] +
                       kappa[:max_run] * (x - mu[:max_run]) ** 2 / (2 * (kappa[:max_run] + 1)))

            mu[1:max_run + 1] = new_mu
            kappa[1:max_run + 1] = new_kappa
            alpha[1:max_run + 1] = new_alpha
            beta[1:max_run + 1] = new_beta
            mu[0] = self.mu0
            kappa[0] = self.kappa0
            alpha[0] = self.alpha0
            beta[0] = self.beta0

        return changepoint_prob


class VPINCalculator:
    """
    Volume-Synchronized Probability of Informed Trading.
    Detects informed order flow toxicity.
    """

    def __init__(self, bucket_size_frac=50, n_buckets=50):
        self.bucket_size_frac = bucket_size_frac
        self.n_buckets = n_buckets

    def calculate(self, df):
        """Calculate VPIN from OHLCV data using Bulk Volume Classification."""
        close = df['close'].values
        volume = df['volume'].values
        n = len(close)

        # Daily VPIN approximation using BVC
        vpin = np.full(n, 0.5)  # neutral default

        if n < self.n_buckets + 10:
            return vpin

        for i in range(self.n_buckets, n):
            window = slice(i - self.n_buckets, i)
            closes = close[window]
            volumes = volume[window]
            opens = np.concatenate([[close[i - self.n_buckets - 1]], closes[:-1]])

            # BVC: classify volume as buy/sell
            sigma = np.std(np.diff(np.log(closes + 1e-10))) + 1e-10
            z = (np.log(closes + 1e-10) - np.log(opens + 1e-10)) / sigma
            buy_frac = stats.norm.cdf(z)

            buy_vol = volumes * buy_frac
            sell_vol = volumes * (1 - buy_frac)

            total_vol = volumes.sum() + 1e-10
            vpin[i] = np.sum(np.abs(buy_vol - sell_vol)) / total_vol

        return vpin


class RegimeDetector:
    """
    Meta-classifier combining all regime detection methods.

    Priority system:
    1. BOCPD changepoint → HIGH_VOL_CHAOS override
    2. VPIN toxicity → HIGH_VOL_CHAOS override
    3. HMM state + volatility regime consensus
    """

    def __init__(self, hmm_lookback=250, bocpd_lambda=200,
                 min_regime_hold=3, transition_threshold=0.7):
        self.hmm_lookback = hmm_lookback
        self.bocpd_lambda = bocpd_lambda
        self.min_regime_hold = min_regime_hold
        self.transition_threshold = transition_threshold

    def detect(self, df):
        """
        Detect regime for each bar. Returns array of Regime enums and features dict.
        """
        close = df['close'].values.astype(float)
        volume = df['volume'].values.astype(float)
        n = len(close)

        # Calculate returns and features
        log_returns = np.diff(np.log(close + 1e-10))
        log_returns = np.concatenate([[0], log_returns])

        # Rolling features
        rv_14 = self._rolling_std(log_returns, 14) * np.sqrt(365)
        rv_7 = self._rolling_std(log_returns, 7) * np.sqrt(365)
        rv_30 = self._rolling_std(log_returns, 30) * np.sqrt(365)
        rv_ratio = np.where(rv_30 > 0.01, rv_7 / rv_30, 1.0)
        vol_of_vol = self._rolling_std(rv_14, 30)

        intraday_range = np.log(df['high'].values / (df['low'].values + 1e-10) + 1e-10)

        vol_20_raw = self._rolling_mean(volume, 20)
        vol_ratio = np.where(vol_20_raw > 0, volume / (vol_20_raw + 1e-10), 1.0)

        # 1. HMM Regime Detection
        features = np.column_stack([log_returns, rv_14 / 10, vol_ratio / 5])

        hmm_states = np.full(n, 1, dtype=int)  # default: mean-reverting
        hmm_probs = np.full((n, 3), 1.0 / 3)

        warmup = min(self.hmm_lookback, n - 10)
        if warmup >= 60:
            hmm = GaussianHMM(n_states=3, n_iter=30)
            train_features = features[:warmup]
            # Remove NaN/Inf
            valid = np.all(np.isfinite(train_features), axis=1)
            if valid.sum() > 30:
                hmm.fit(train_features[valid])

                # Predict on all data
                all_valid = np.all(np.isfinite(features), axis=1)
                if all_valid.sum() > 0:
                    valid_features = features[all_valid]
                    valid_states = hmm.predict(valid_features)
                    valid_probs = hmm.predict_proba(valid_features)
                    hmm_states[all_valid] = valid_states
                    hmm_probs[all_valid] = valid_probs

                    # Label states by mean return
                    state_returns = {}
                    for s in range(3):
                        mask = (hmm_states == s)
                        if mask.sum() > 0:
                            state_returns[s] = np.mean(log_returns[mask])
                        else:
                            state_returns[s] = 0

                    sorted_states = sorted(state_returns, key=state_returns.get)
                    state_map = {}
                    state_map[sorted_states[0]] = 0  # bearish
                    state_map[sorted_states[1]] = 1  # sideways
                    state_map[sorted_states[2]] = 2  # bullish

                    hmm_states = np.array([state_map.get(s, 1) for s in hmm_states])

        # 2. BOCPD
        valid_returns = log_returns.copy()
        valid_returns[~np.isfinite(valid_returns)] = 0
        bocpd = BOCPDetector(hazard_lambda=self.bocpd_lambda)
        changepoint_prob = bocpd.detect(valid_returns)

        # 3. VPIN
        vpin_calc = VPINCalculator()
        vpin = vpin_calc.calculate(df)

        # 4. Volatility regime clustering
        vol_regime = np.full(n, 1)  # 0=low, 1=normal, 2=high, 3=extreme
        median_rv = np.nanmedian(rv_14[rv_14 > 0]) if np.any(rv_14 > 0) else 0.5
        for i in range(n):
            if rv_14[i] < median_rv * 0.5:
                vol_regime[i] = 0  # low vol
            elif rv_14[i] < median_rv * 1.5:
                vol_regime[i] = 1  # normal
            elif rv_14[i] < median_rv * 2.5:
                vol_regime[i] = 2  # high
            else:
                vol_regime[i] = 3  # extreme

        # 5. Trend detection (simple EMA cross)
        ema_20 = self._ema(close, 20)
        ema_50 = self._ema(close, 50)
        sma_200 = self._rolling_mean(close, 200)

        # ADX calculation
        adx = self._calculate_adx(df, 14)

        # Composite regime classification
        regimes = np.full(n, Regime.MEAN_REVERTING)
        regime_confidence = np.zeros(n)

        for i in range(60, n):
            # Priority 1: Crisis override
            if changepoint_prob[i] > 0.5 or vpin[i] > 0.8:
                regimes[i] = Regime.HIGH_VOL_CHAOS
                regime_confidence[i] = max(changepoint_prob[i], vpin[i])
                continue

            # Priority 2: Extreme volatility
            if vol_regime[i] == 3:
                regimes[i] = Regime.HIGH_VOL_CHAOS
                regime_confidence[i] = 0.8
                continue

            # Priority 3: HMM + trend + ADX consensus
            hmm_s = hmm_states[i]
            is_uptrend = ema_20[i] > ema_50[i]
            is_downtrend = ema_20[i] < ema_50[i]
            adx_val = adx[i] if np.isfinite(adx[i]) else 20
            trending = adx_val > 25

            # Recent return momentum
            ret_20 = 0
            if i >= 20:
                ret_20 = (close[i] / close[i-20] - 1)

            if trending and is_uptrend and ret_20 > 0.02:
                regimes[i] = Regime.TRENDING_UP
                regime_confidence[i] = 0.7
            elif trending and is_downtrend and ret_20 < -0.02:
                regimes[i] = Regime.TRENDING_DOWN
                regime_confidence[i] = 0.7
            elif vol_regime[i] == 0 and not trending:
                regimes[i] = Regime.LOW_VOL_ACCUMULATION
                regime_confidence[i] = 0.6
            elif not trending or (hmm_s == 1):
                regimes[i] = Regime.MEAN_REVERTING
                regime_confidence[i] = 0.5
            elif hmm_s == 2:
                regimes[i] = Regime.TRENDING_UP
                regime_confidence[i] = 0.6
            elif hmm_s == 0:
                regimes[i] = Regime.TRENDING_DOWN
                regime_confidence[i] = 0.6
            else:
                regimes[i] = Regime.MEAN_REVERTING
                regime_confidence[i] = 0.5

        # Apply minimum hold period (hysteresis)
        regimes = self._apply_hysteresis(regimes, self.min_regime_hold)

        features_dict = {
            'log_returns': log_returns,
            'rv_14': rv_14,
            'rv_7': rv_7,
            'rv_30': rv_30,
            'rv_ratio': rv_ratio,
            'vol_of_vol': vol_of_vol,
            'vpin': vpin,
            'changepoint_prob': changepoint_prob,
            'hmm_states': hmm_states,
            'hmm_probs': hmm_probs,
            'vol_regime': vol_regime,
            'adx': adx,
            'ema_20': ema_20,
            'ema_50': ema_50,
            'sma_200': sma_200,
            'regime_confidence': regime_confidence,
        }

        return regimes, features_dict

    def _apply_hysteresis(self, regimes, min_hold):
        """Don't flip-flop between regimes too fast."""
        result = regimes.copy()
        hold_count = 0
        current = regimes[0]

        for i in range(len(regimes)):
            if regimes[i] == current:
                hold_count += 1
                result[i] = current
            else:
                hold_count += 1
                if hold_count >= min_hold:
                    current = regimes[i]
                    hold_count = 0
                result[i] = current

        return result

    @staticmethod
    def _rolling_std(arr, window):
        result = np.full_like(arr, np.nan, dtype=float)
        for i in range(window, len(arr)):
            result[i] = np.std(arr[i-window:i])
        # Fill initial NaNs
        first_valid = window
        if first_valid < len(result):
            result[:first_valid] = result[first_valid] if np.isfinite(result[first_valid]) else 0
        return result

    @staticmethod
    def _rolling_mean(arr, window):
        result = np.full_like(arr, np.nan, dtype=float)
        for i in range(window, len(arr)):
            result[i] = np.mean(arr[i-window:i])
        first_valid = window
        if first_valid < len(result):
            result[:first_valid] = result[first_valid] if np.isfinite(result[first_valid]) else 0
        return result

    @staticmethod
    def _ema(arr, period):
        result = np.full_like(arr, np.nan, dtype=float)
        alpha = 2.0 / (period + 1)
        # Find first valid
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
        # Backfill
        result[:start + period - 1] = result[start + period - 1]
        return result

    @staticmethod
    def _calculate_adx(df, period=14):
        """Calculate ADX from OHLC data."""
        high = df['high'].values.astype(float)
        low = df['low'].values.astype(float)
        close = df['close'].values.astype(float)
        n = len(close)

        adx = np.full(n, 25.0)  # neutral default

        if n < period * 3:
            return adx

        # True Range
        tr = np.zeros(n)
        plus_dm = np.zeros(n)
        minus_dm = np.zeros(n)

        for i in range(1, n):
            h_l = high[i] - low[i]
            h_pc = abs(high[i] - close[i-1])
            l_pc = abs(low[i] - close[i-1])
            tr[i] = max(h_l, h_pc, l_pc)

            up_move = high[i] - high[i-1]
            down_move = low[i-1] - low[i]

            plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0
            minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0

        # Smoothed averages
        atr = np.zeros(n)
        plus_di = np.zeros(n)
        minus_di = np.zeros(n)

        atr[period] = np.mean(tr[1:period+1])
        sm_plus = np.mean(plus_dm[1:period+1])
        sm_minus = np.mean(minus_dm[1:period+1])

        for i in range(period + 1, n):
            atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
            sm_plus = (sm_plus * (period - 1) + plus_dm[i]) / period
            sm_minus = (sm_minus * (period - 1) + minus_dm[i]) / period

            plus_di[i] = 100 * sm_plus / (atr[i] + 1e-10)
            minus_di[i] = 100 * sm_minus / (atr[i] + 1e-10)

        # DX and ADX
        dx = np.zeros(n)
        for i in range(period, n):
            di_sum = plus_di[i] + minus_di[i]
            if di_sum > 0:
                dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / di_sum

        # Smooth ADX
        start = period * 2
        if start < n:
            adx[start] = np.mean(dx[period:start])
            for i in range(start + 1, n):
                adx[i] = (adx[i-1] * (period - 1) + dx[i]) / period

        return adx
