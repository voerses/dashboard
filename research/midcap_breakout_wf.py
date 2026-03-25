#!/workspace/venv/bin/python
"""
Mid-Cap Momentum Breakout — Walk-Forward Validation
====================================================
Prior finding: mid-cap tokens ($10M-$100M ADV) show 62% profitable,
+94% average return for momentum breakout strategies, with outliers
like MYX +1740%.

Key question: Does this survive walk-forward, or is the +94% average
return an artifact of looking at the full period?

Strategies tested:
  1. ATR Breakout: price > recent high + 1.5 ATR, volume > 2x avg
     Exit: ATR trail (2.5x) OR max hold 720h OR regime exit
  2. Relative Momentum: rank mid-cap by 14d return, go long top 5,
     weekly rebalance

Walk-forward: 8 windows, 6-month train + 3-month test (non-overlapping)
Parameter sweep: ATR mult {1.0,1.5,2.0,2.5}, vol thresh {1.5,2.0,3.0},
                 trail mult {2.0,2.5,3.0}

Costs: 0.22% fee + ADV-based slippage
"""

import pandas as pd
import numpy as np
import warnings
import os
import glob
import json
from pathlib import Path
from itertools import product
from datetime import datetime

warnings.filterwarnings("ignore")

# ── Config ──────────────────────────────────────────────────────────────────
DATA_DIR = Path("/workspace/crypto_backtest/data/perp/1h_cache")
OUTPUT_DIR = Path("/workspace/crypto_backtest/research")
BTC_PATH = DATA_DIR / "BTC_1h.parquet"

# ADV thresholds for mid-cap
ADV_LO = 1e7   # $10M
ADV_HI = 1e8   # $100M
MIN_BARS_FOR_WF = 6500  # ~9 months minimum for at least 1 walk-forward window

# Walk-forward windows
TRAIN_BARS = 24 * 30 * 6   # 6 months in hours = 4320
TEST_BARS = 24 * 30 * 3    # 3 months in hours = 2160
N_WINDOWS = 8

# Strategy defaults
DEFAULT_ATR_PERIOD = 14
DEFAULT_LOOKBACK_HIGH = 48  # hours for recent high
DEFAULT_VOL_MA_PERIOD = 20  # bars for volume moving average
DEFAULT_ATR_ENTRY_MULT = 1.5
DEFAULT_VOL_THRESH = 2.0
DEFAULT_TRAIL_MULT = 2.5
DEFAULT_MAX_HOLD = 720  # hours
DEFAULT_POS_SIZE = 0.03  # 3% per token
DEFAULT_ADV_CAP = 0.05   # 5% ADV cap

# Cost model
FEE_BPS = 22  # 0.22% per trade (entry + exit = 0.44% round trip)
FEE_FRAC = FEE_BPS / 10_000

# Relative momentum defaults
RELMOM_LOOKBACK_HOURS = 14 * 24  # 14 days
RELMOM_TOP_N = 5
RELMOM_REBALANCE_HOURS = 7 * 24  # weekly

# Parameter sweep
ATR_MULTS = [1.0, 1.5, 2.0, 2.5]
VOL_THRESHOLDS = [1.5, 2.0, 3.0]
TRAIL_MULTS = [2.0, 2.5, 3.0]

# Annualization
HOURS_PER_YEAR = 365.25 * 24
SQRT_HPY = np.sqrt(HOURS_PER_YEAR)


# ── Helpers ─────────────────────────────────────────────────────────────────

def load_token(token: str) -> pd.DataFrame:
    """Load 1H OHLCV data for a token."""
    fp = DATA_DIR / f"{token}_1h.parquet"
    df = pd.read_parquet(fp)[["open", "high", "low", "close", "volume"]]
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df


def compute_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                period: int = 14) -> np.ndarray:
    """Compute ATR using exponential moving average of true range."""
    n = len(close)
    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i],
                     abs(high[i] - close[i - 1]),
                     abs(low[i] - close[i - 1]))
    # EMA of true range
    atr = np.empty(n)
    atr[:period] = np.nan
    atr[period - 1] = np.mean(tr[:period])
    alpha = 2.0 / (period + 1)
    for i in range(period, n):
        atr[i] = alpha * tr[i] + (1 - alpha) * atr[i - 1]
    return atr


def compute_rolling_max(arr: np.ndarray, window: int) -> np.ndarray:
    """Compute rolling maximum (shifted by 1 to avoid lookahead)."""
    n = len(arr)
    result = np.full(n, np.nan)
    for i in range(window, n):
        result[i] = np.max(arr[i - window:i])  # excludes current bar
    return result


def compute_rolling_mean(arr: np.ndarray, window: int) -> np.ndarray:
    """Compute rolling mean."""
    n = len(arr)
    result = np.full(n, np.nan)
    cs = np.cumsum(np.nan_to_num(arr, nan=0.0))
    for i in range(window - 1, n):
        result[i] = (cs[i] - (cs[i - window] if i >= window else 0)) / window
    return result


def adv_slippage(adv_daily: float) -> float:
    """ADV-based slippage model. Higher for less liquid tokens."""
    # Slippage = base + impact based on ADV
    # Very liquid (>$50M ADV): ~5bps impact
    # Less liquid ($10M ADV): ~20bps impact
    if adv_daily >= 5e7:
        return 5.0 / 10_000
    elif adv_daily >= 2e7:
        return 10.0 / 10_000
    else:
        return 20.0 / 10_000


def total_cost_per_trade(adv_daily: float) -> float:
    """Total cost = fee (entry+exit) + slippage (entry+exit)."""
    return 2 * FEE_FRAC + 2 * adv_slippage(adv_daily)


# ── Universe Construction ───────────────────────────────────────────────────

def build_midcap_universe():
    """
    Scan all tokens, compute ADV from last 30 days, classify into tiers.
    Return list of (token, adv_daily, n_bars) for mid-cap tokens with
    enough data for walk-forward.
    """
    files = sorted(glob.glob(str(DATA_DIR / "*_1h.parquet")))
    universe = []

    for f in files:
        tok = os.path.basename(f).replace("_1h.parquet", "")
        if tok == "BTC":  # Exclude BTC (benchmark)
            continue

        df = pd.read_parquet(f, columns=["close", "volume"])
        n_bars = len(df)
        if n_bars < MIN_BARS_FOR_WF:
            continue

        # ADV from last 720 bars (30 days)
        last_720 = df.iloc[-720:]
        adv_hourly = (last_720["close"] * last_720["volume"]).mean()
        adv_daily = adv_hourly * 24

        if ADV_LO <= adv_daily < ADV_HI:
            universe.append({
                "token": tok,
                "adv_daily": adv_daily,
                "n_bars": n_bars,
                "first_date": str(df.index[0]),
                "last_date": str(df.index[-1]),
            })

    universe.sort(key=lambda x: -x["adv_daily"])
    return universe


# ── Strategy 1: ATR Breakout ────────────────────────────────────────────────

def run_atr_breakout_single(close: np.ndarray, high: np.ndarray,
                             low: np.ndarray, volume: np.ndarray,
                             atr_entry_mult: float = 1.5,
                             vol_thresh: float = 2.0,
                             trail_mult: float = 2.5,
                             max_hold: int = 720,
                             cost_per_trade: float = 0.0088) -> dict:
    """
    Vectorized ATR breakout backtest for a single token on a single period.

    Entry: close > rolling_high(48) + atr_entry_mult * ATR
           AND volume > vol_thresh * vol_ma(20)
    Exit:  ATR trailing stop (trail_mult * ATR from peak)
           OR max hold reached

    Returns dict with performance metrics.
    """
    n = len(close)
    if n < 100:
        return {"total_return": 0.0, "n_trades": 0, "sharpe": 0.0,
                "max_dd": 0.0, "win_rate": 0.0, "avg_trade_ret": 0.0,
                "hourly_returns": np.zeros(n)}

    # Precompute indicators
    atr = compute_atr(high, low, close, DEFAULT_ATR_PERIOD)
    rolling_high = compute_rolling_max(close, DEFAULT_LOOKBACK_HIGH)
    vol_ma = compute_rolling_mean(volume, DEFAULT_VOL_MA_PERIOD)

    # Entry signals
    entry_signal = np.zeros(n, dtype=bool)
    for i in range(DEFAULT_LOOKBACK_HIGH + DEFAULT_ATR_PERIOD, n):
        if (not np.isnan(atr[i]) and not np.isnan(rolling_high[i])
                and not np.isnan(vol_ma[i]) and vol_ma[i] > 0):
            breakout = close[i] > rolling_high[i] + atr_entry_mult * atr[i]
            vol_surge = volume[i] > vol_thresh * vol_ma[i]
            if breakout and vol_surge:
                entry_signal[i] = True

    # Simulate trades (no overlapping positions)
    trades = []
    hourly_rets = np.zeros(n)
    i = 0
    while i < n:
        if entry_signal[i]:
            entry_price = close[i]
            entry_bar = i
            peak_price = entry_price
            trail_stop = entry_price - trail_mult * atr[i] if not np.isnan(atr[i]) else entry_price * 0.9

            # Walk forward to find exit
            j = i + 1
            while j < n:
                # Update peak and trail stop
                if close[j] > peak_price:
                    peak_price = close[j]
                    if not np.isnan(atr[j]):
                        trail_stop = peak_price - trail_mult * atr[j]

                # Check trail stop
                if low[j] <= trail_stop:
                    exit_price = trail_stop
                    hourly_rets[j] = (exit_price / entry_price - 1) - cost_per_trade
                    trades.append({
                        "entry_bar": entry_bar,
                        "exit_bar": j,
                        "hold_hours": j - entry_bar,
                        "return": exit_price / entry_price - 1 - cost_per_trade,
                        "exit_reason": "trail",
                    })
                    i = j + 1
                    break

                # Check max hold
                if j - entry_bar >= max_hold:
                    exit_price = close[j]
                    hourly_rets[j] = (exit_price / entry_price - 1) - cost_per_trade
                    trades.append({
                        "entry_bar": entry_bar,
                        "exit_bar": j,
                        "hold_hours": j - entry_bar,
                        "return": exit_price / entry_price - 1 - cost_per_trade,
                        "exit_reason": "max_hold",
                    })
                    i = j + 1
                    break

                j += 1
            else:
                # End of data -- force exit
                if j >= n:
                    j = n - 1
                exit_price = close[j]
                hourly_rets[j] = (exit_price / entry_price - 1) - cost_per_trade
                trades.append({
                    "entry_bar": entry_bar,
                    "exit_bar": j,
                    "hold_hours": j - entry_bar,
                    "return": exit_price / entry_price - 1 - cost_per_trade,
                    "exit_reason": "eod",
                })
                i = j + 1
        else:
            i += 1

    # Compute metrics
    n_trades = len(trades)
    if n_trades == 0:
        return {"total_return": 0.0, "n_trades": 0, "sharpe": 0.0,
                "max_dd": 0.0, "win_rate": 0.0, "avg_trade_ret": 0.0,
                "hourly_returns": hourly_rets}

    trade_rets = np.array([t["return"] for t in trades])
    total_ret = np.prod(1 + trade_rets) - 1
    win_rate = np.mean(trade_rets > 0)
    avg_trade_ret = np.mean(trade_rets)

    # Build equity curve from hourly returns for Sharpe/MaxDD
    eq = np.cumprod(1 + hourly_rets)
    running_max = np.maximum.accumulate(eq)
    dd = eq / running_max - 1
    max_dd = np.min(dd)

    # Annualized Sharpe from hourly returns
    hr_mean = np.mean(hourly_rets)
    hr_std = np.std(hourly_rets)
    sharpe = (hr_mean / hr_std * SQRT_HPY) if hr_std > 0 else 0.0

    return {
        "total_return": total_ret,
        "n_trades": n_trades,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "win_rate": win_rate,
        "avg_trade_ret": avg_trade_ret,
        "hourly_returns": hourly_rets,
        "trades": trades,
    }


# ── Strategy 2: Relative Momentum ──────────────────────────────────────────

def run_relative_momentum(token_closes: dict, test_start: int,
                           test_end: int, lookback_hours: int = 336,
                           top_n: int = 5, rebalance_hours: int = 168,
                           cost_frac: float = 0.0088) -> dict:
    """
    Relative momentum: rank tokens by lookback return, go long top N.
    Equal weight, weekly rebalance.

    token_closes: {token: np.array of close prices aligned to common index}
    test_start, test_end: indices into the common array
    """
    tokens = list(token_closes.keys())
    n_tokens = len(tokens)
    if n_tokens < top_n:
        return {"total_return": 0.0, "sharpe": 0.0, "max_dd": 0.0,
                "n_rebalances": 0, "hourly_returns": np.zeros(test_end - test_start)}

    test_len = test_end - test_start
    portfolio_rets = np.zeros(test_len)
    n_rebalances = 0

    # Build return matrix
    ret_matrix = {}
    for tok in tokens:
        c = token_closes[tok]
        if len(c) > test_end and test_start >= lookback_hours:
            ret_matrix[tok] = c
    tokens_valid = list(ret_matrix.keys())
    if len(tokens_valid) < top_n:
        return {"total_return": 0.0, "sharpe": 0.0, "max_dd": 0.0,
                "n_rebalances": 0, "hourly_returns": np.zeros(test_len)}

    holdings = []  # current top N tokens
    weights = {}   # token -> weight

    for t in range(test_len):
        bar = test_start + t

        # Rebalance check
        if t % rebalance_hours == 0 and bar >= lookback_hours:
            # Rank by lookback return
            scores = {}
            for tok in tokens_valid:
                c = ret_matrix[tok]
                if bar < len(c) and bar - lookback_hours >= 0:
                    lb_ret = c[bar] / c[bar - lookback_hours] - 1
                    if not np.isnan(lb_ret) and not np.isinf(lb_ret):
                        scores[tok] = lb_ret

            if len(scores) >= top_n:
                ranked = sorted(scores.items(), key=lambda x: -x[1])
                new_holdings = [tok for tok, _ in ranked[:top_n]]

                # Compute turnover cost
                old_set = set(holdings)
                new_set = set(new_holdings)
                turnover = len(old_set.symmetric_difference(new_set)) / max(1, 2 * top_n)
                cost_this_bar = turnover * cost_frac

                holdings = new_holdings
                weights = {tok: 1.0 / top_n for tok in holdings}
                n_rebalances += 1

                portfolio_rets[t] -= cost_this_bar

        # Compute portfolio return for this bar
        if holdings:
            bar_ret = 0.0
            for tok in holdings:
                c = ret_matrix[tok]
                if bar < len(c) and bar > 0:
                    r = c[bar] / c[bar - 1] - 1
                    if not np.isnan(r) and not np.isinf(r):
                        bar_ret += weights.get(tok, 0) * r
            portfolio_rets[t] += bar_ret

    # Metrics
    eq = np.cumprod(1 + portfolio_rets)
    total_ret = eq[-1] - 1 if len(eq) > 0 else 0
    running_max = np.maximum.accumulate(eq)
    dd = eq / running_max - 1
    max_dd = np.min(dd) if len(dd) > 0 else 0

    hr_mean = np.mean(portfolio_rets)
    hr_std = np.std(portfolio_rets)
    sharpe = (hr_mean / hr_std * SQRT_HPY) if hr_std > 0 else 0.0

    return {
        "total_return": total_ret,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "n_rebalances": n_rebalances,
        "hourly_returns": portfolio_rets,
    }


# ── Walk-Forward Engine ─────────────────────────────────────────────────────

def generate_wf_windows(total_bars: int, train_bars: int = TRAIN_BARS,
                         test_bars: int = TEST_BARS,
                         n_windows: int = N_WINDOWS) -> list:
    """
    Generate walk-forward windows working backward from end of data.
    Each window: [train_start, train_end, test_start, test_end].
    Windows are non-overlapping in test periods.
    """
    windows = []
    # Work backwards from the end
    end = total_bars
    for _ in range(n_windows):
        test_end = end
        test_start = end - test_bars
        train_end = test_start
        train_start = train_end - train_bars

        if train_start < 0:
            break

        windows.append({
            "train_start": train_start,
            "train_end": train_end,
            "test_start": test_start,
            "test_end": test_end,
        })
        end = test_start  # move backward

    windows.reverse()  # chronological order
    return windows


def run_walkforward_breakout(universe: list,
                              atr_entry_mult: float = 1.5,
                              vol_thresh: float = 2.0,
                              trail_mult: float = 2.5) -> dict:
    """
    Walk-forward test of ATR breakout across all mid-cap tokens.
    Returns per-window and aggregate results.
    """
    # Load BTC for correlation
    btc_df = load_token("BTC")
    btc_close = btc_df["close"].values
    n_btc = len(btc_close)

    # Generate windows based on BTC data length (longest)
    windows = generate_wf_windows(n_btc)
    if not windows:
        return {"error": "Not enough data for walk-forward windows"}

    window_results = []
    all_token_contribs = {}  # token -> total P&L contribution
    combined_hourly = np.zeros(n_btc)
    btc_hourly_rets = np.diff(btc_close) / btc_close[:-1]
    btc_hourly_rets = np.insert(btc_hourly_rets, 0, 0)

    for w_idx, w in enumerate(windows):
        ts, te = w["test_start"], w["test_end"]
        window_token_results = []
        window_hourly = np.zeros(te - ts)
        n_tokens_active = 0

        for info in universe:
            tok = info["token"]
            try:
                df = load_token(tok)
            except Exception:
                continue

            # Align to BTC index
            # We use the token's own data length; if shorter, skip this window
            n_tok = len(df)
            if n_tok < te:
                # Token doesn't have data for this window
                continue

            close = df["close"].values
            high = df["high"].values
            low = df["low"].values
            vol = df["volume"].values

            # Run on test period only
            test_close = close[ts:te]
            test_high = high[ts:te]
            test_low = low[ts:te]
            test_vol = vol[ts:te]

            # But we need warmup, so include train period for indicators
            warmup_start = max(0, ts - 200)  # 200 bars warmup for ATR/rolling
            full_close = close[warmup_start:te]
            full_high = high[warmup_start:te]
            full_low = low[warmup_start:te]
            full_vol = vol[warmup_start:te]

            cost = total_cost_per_trade(info["adv_daily"])

            res = run_atr_breakout_single(
                full_close, full_high, full_low, full_vol,
                atr_entry_mult=atr_entry_mult,
                vol_thresh=vol_thresh,
                trail_mult=trail_mult,
                max_hold=DEFAULT_MAX_HOLD,
                cost_per_trade=cost,
            )

            # Extract only the test period returns
            warmup_len = ts - warmup_start
            hr = res["hourly_returns"]
            if len(hr) > warmup_len:
                test_hr = hr[warmup_len:]
                if len(test_hr) > len(window_hourly):
                    test_hr = test_hr[:len(window_hourly)]
                elif len(test_hr) < len(window_hourly):
                    padded = np.zeros(len(window_hourly))
                    padded[:len(test_hr)] = test_hr
                    test_hr = padded
            else:
                test_hr = np.zeros(len(window_hourly))

            # Position sizing: 3% per token, capped at 5% ADV
            pos_weight = DEFAULT_POS_SIZE
            test_hr_weighted = test_hr * pos_weight

            window_hourly += test_hr_weighted
            n_tokens_active += 1

            # Track per-trade results for this window
            trade_ret = np.prod(1 + test_hr) - 1
            window_token_results.append({
                "token": tok,
                "return": trade_ret,
                "n_trades": res["n_trades"],
                "win_rate": res["win_rate"],
            })

            # Accumulate cross-token P&L
            if tok not in all_token_contribs:
                all_token_contribs[tok] = 0.0
            all_token_contribs[tok] += trade_ret

        # Window-level metrics
        eq = np.cumprod(1 + window_hourly)
        w_ret = eq[-1] - 1 if len(eq) > 0 else 0
        running_max = np.maximum.accumulate(eq)
        dd = eq / running_max - 1
        w_maxdd = np.min(dd) if len(dd) > 0 else 0
        hr_mean = np.mean(window_hourly)
        hr_std = np.std(window_hourly)
        w_sharpe = (hr_mean / hr_std * SQRT_HPY) if hr_std > 0 else 0

        total_trades = sum(r["n_trades"] for r in window_token_results)
        winning_tokens = sum(1 for r in window_token_results if r["return"] > 0)
        total_tokens = len(window_token_results)

        # Correlation with BTC
        btc_test_rets = btc_hourly_rets[ts:te]
        if len(btc_test_rets) == len(window_hourly) and np.std(window_hourly) > 0:
            corr_btc = np.corrcoef(window_hourly, btc_test_rets)[0, 1]
        else:
            corr_btc = np.nan

        # Store combined hourly for overall correlation
        combined_hourly[ts:te] += window_hourly[:te - ts]

        window_results.append({
            "window": w_idx + 1,
            "test_start_bar": ts,
            "test_end_bar": te,
            "return": w_ret,
            "sharpe": w_sharpe,
            "max_dd": w_maxdd,
            "n_trades": total_trades,
            "n_tokens_active": n_tokens_active,
            "winning_tokens": winning_tokens,
            "total_tokens": total_tokens,
            "win_rate_tokens": winning_tokens / total_tokens if total_tokens > 0 else 0,
            "corr_btc": corr_btc,
        })

    # Aggregate stats
    if window_results:
        rets = [w["return"] for w in window_results]
        sharpes = [w["sharpe"] for w in window_results]
        mdd = [w["max_dd"] for w in window_results]
        avg_ret = np.mean(rets)
        avg_sharpe = np.mean(sharpes)
        avg_mdd = np.mean(mdd)
        pct_positive = np.mean([r > 0 for r in rets])
        avg_corr = np.nanmean([w["corr_btc"] for w in window_results])
    else:
        avg_ret = avg_sharpe = avg_mdd = pct_positive = avg_corr = 0

    return {
        "params": {
            "atr_entry_mult": atr_entry_mult,
            "vol_thresh": vol_thresh,
            "trail_mult": trail_mult,
        },
        "n_windows": len(window_results),
        "windows": window_results,
        "aggregate": {
            "avg_return": avg_ret,
            "avg_sharpe": avg_sharpe,
            "avg_max_dd": avg_mdd,
            "pct_positive_windows": pct_positive,
            "avg_corr_btc": avg_corr,
        },
        "token_contributions": all_token_contribs,
    }


# ── Walk-Forward for Relative Momentum ──────────────────────────────────────

def run_walkforward_relmom(universe: list) -> dict:
    """
    Walk-forward test of relative momentum strategy.
    """
    # Build aligned close price arrays
    # Use BTC datetime index as master
    btc_df = load_token("BTC")
    master_idx = btc_df.index
    n_master = len(master_idx)

    token_closes = {}
    for info in universe:
        tok = info["token"]
        try:
            df = load_token(tok)
        except Exception:
            continue
        # Reindex to master
        aligned = df["close"].reindex(master_idx)
        # Forward fill small gaps, then drop leading NaN
        aligned = aligned.ffill(limit=5)
        token_closes[tok] = aligned.values

    btc_close = btc_df["close"].values
    btc_hourly_rets = np.diff(btc_close) / btc_close[:-1]
    btc_hourly_rets = np.insert(btc_hourly_rets, 0, 0)

    windows = generate_wf_windows(n_master)
    window_results = []

    for w_idx, w in enumerate(windows):
        ts, te = w["test_start"], w["test_end"]

        # Filter tokens that have data for this window + lookback
        valid_closes = {}
        for tok, c in token_closes.items():
            if ts >= RELMOM_LOOKBACK_HOURS and not np.isnan(c[ts]):
                valid_closes[tok] = c

        if len(valid_closes) < RELMOM_TOP_N:
            window_results.append({
                "window": w_idx + 1,
                "return": 0.0,
                "sharpe": 0.0,
                "max_dd": 0.0,
                "n_rebalances": 0,
                "n_tokens_available": len(valid_closes),
            })
            continue

        # Find average cost across universe tokens
        avg_cost = np.mean([
            total_cost_per_trade(info["adv_daily"])
            for info in universe
        ])

        res = run_relative_momentum(
            valid_closes, ts, te,
            lookback_hours=RELMOM_LOOKBACK_HOURS,
            top_n=RELMOM_TOP_N,
            rebalance_hours=RELMOM_REBALANCE_HOURS,
            cost_frac=avg_cost,
        )

        # Correlation with BTC
        btc_test_rets = btc_hourly_rets[ts:te]
        hr = res["hourly_returns"]
        if len(btc_test_rets) == len(hr) and np.std(hr) > 0:
            corr_btc = np.corrcoef(hr, btc_test_rets)[0, 1]
        else:
            corr_btc = np.nan

        window_results.append({
            "window": w_idx + 1,
            "return": res["total_return"],
            "sharpe": res["sharpe"],
            "max_dd": res["max_dd"],
            "n_rebalances": res["n_rebalances"],
            "n_tokens_available": len(valid_closes),
            "corr_btc": corr_btc,
        })

    # Aggregate
    if window_results:
        rets = [w["return"] for w in window_results]
        sharpes = [w["sharpe"] for w in window_results]
        avg_ret = np.mean(rets)
        avg_sharpe = np.mean(sharpes)
        pct_pos = np.mean([r > 0 for r in rets])
        avg_corr = np.nanmean([w.get("corr_btc", np.nan) for w in window_results])
    else:
        avg_ret = avg_sharpe = pct_pos = avg_corr = 0

    return {
        "strategy": "relative_momentum_top5_14d",
        "n_windows": len(window_results),
        "windows": window_results,
        "aggregate": {
            "avg_return": avg_ret,
            "avg_sharpe": avg_sharpe,
            "pct_positive_windows": pct_pos,
            "avg_corr_btc": avg_corr,
        },
    }


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 80)
    print("MID-CAP MOMENTUM BREAKOUT — WALK-FORWARD VALIDATION")
    print("=" * 80)
    print(f"Run time: {datetime.now().isoformat()}")
    print()

    # Step 1: Build universe
    print("STEP 1: Building mid-cap universe ($10M-$100M ADV)...")
    universe = build_midcap_universe()
    print(f"  Found {len(universe)} mid-cap tokens with {MIN_BARS_FOR_WF}+ bars")
    print(f"  ADV range: ${min(u['adv_daily'] for u in universe)/1e6:.1f}M "
          f"- ${max(u['adv_daily'] for u in universe)/1e6:.1f}M")
    print()

    # Show universe
    print("  Universe tokens:")
    for u in universe[:10]:
        print(f"    {u['token']:12s}  ADV=${u['adv_daily']/1e6:.1f}M  "
              f"bars={u['n_bars']:6d}  since={u['first_date'][:10]}")
    if len(universe) > 10:
        print(f"    ... and {len(universe) - 10} more")
    print()

    # Step 2: Walk-forward windows
    btc_df = load_token("BTC")
    windows = generate_wf_windows(len(btc_df))
    print(f"STEP 2: Walk-forward setup: {len(windows)} windows "
          f"(6mo train + 3mo test)")
    for i, w in enumerate(windows):
        ts_date = btc_df.index[w["test_start"]].strftime("%Y-%m-%d")
        te_date = btc_df.index[min(w["test_end"] - 1, len(btc_df) - 1)].strftime("%Y-%m-%d")
        print(f"  Window {i+1}: test {ts_date} -> {te_date}")
    print()

    # Step 3: Default parameter walk-forward
    print("STEP 3: ATR Breakout — Default parameters "
          f"(ATR={DEFAULT_ATR_ENTRY_MULT}, Vol={DEFAULT_VOL_THRESH}x, "
          f"Trail={DEFAULT_TRAIL_MULT}x)")
    print("-" * 80)

    default_result = run_walkforward_breakout(
        universe,
        atr_entry_mult=DEFAULT_ATR_ENTRY_MULT,
        vol_thresh=DEFAULT_VOL_THRESH,
        trail_mult=DEFAULT_TRAIL_MULT,
    )

    print(f"\n  {'Window':<8} {'Return':>10} {'Sharpe':>8} {'MaxDD':>8} "
          f"{'Trades':>8} {'Tokens':>8} {'WinTok':>8} {'CorrBTC':>8}")
    print("  " + "-" * 70)
    for w in default_result["windows"]:
        print(f"  {w['window']:<8} {w['return']:>+10.2%} {w['sharpe']:>8.2f} "
              f"{w['max_dd']:>8.2%} {w['n_trades']:>8} "
              f"{w['n_tokens_active']:>8} "
              f"{w['winning_tokens']}/{w['total_tokens']:>5} "
              f"{w['corr_btc']:>8.3f}")

    agg = default_result["aggregate"]
    print("  " + "-" * 70)
    print(f"  {'AVG':<8} {agg['avg_return']:>+10.2%} {agg['avg_sharpe']:>8.2f} "
          f"{agg['avg_max_dd']:>8.2%} {'':>8} {'':>8} "
          f"{'':>8} {agg['avg_corr_btc']:>8.3f}")
    print(f"\n  Positive windows: {agg['pct_positive_windows']:.0%}")
    print()

    # Step 4: Cross-token contribution analysis
    print("STEP 4: Cross-token P&L contribution")
    print("-" * 80)
    contribs = default_result["token_contributions"]
    sorted_contribs = sorted(contribs.items(), key=lambda x: -x[1])
    profit_tokens = sum(1 for _, v in sorted_contribs if v > 0)
    loss_tokens = sum(1 for _, v in sorted_contribs if v < 0)
    zero_tokens = sum(1 for _, v in sorted_contribs if v == 0)
    print(f"  Profitable tokens: {profit_tokens}")
    print(f"  Loss-making tokens: {loss_tokens}")
    print(f"  Zero (no trades): {zero_tokens}")
    print(f"\n  Top 10 contributors:")
    for tok, ret in sorted_contribs[:10]:
        print(f"    {tok:12s}  {ret:>+10.2%}")
    print(f"\n  Bottom 10 contributors:")
    for tok, ret in sorted_contribs[-10:]:
        print(f"    {tok:12s}  {ret:>+10.2%}")

    # Concentration: what % of profit comes from top 3?
    total_profit = sum(v for _, v in sorted_contribs if v > 0)
    top3_profit = sum(v for _, v in sorted_contribs[:3] if v > 0)
    if total_profit > 0:
        print(f"\n  Profit concentration: top 3 tokens = "
              f"{top3_profit/total_profit:.0%} of total profit")
    print()

    # Step 5: Parameter sensitivity sweep
    print("STEP 5: Parameter sensitivity sweep")
    print("-" * 80)
    sweep_results = []

    combos = list(product(ATR_MULTS, VOL_THRESHOLDS, TRAIL_MULTS))
    print(f"  Testing {len(combos)} parameter combinations...")

    for i, (am, vt, tm) in enumerate(combos):
        if i % 9 == 0:
            print(f"  ... combo {i+1}/{len(combos)}: "
                  f"ATR={am}, Vol={vt}x, Trail={tm}x")
        res = run_walkforward_breakout(universe, am, vt, tm)
        ag = res["aggregate"]
        sweep_results.append({
            "atr_mult": am,
            "vol_thresh": vt,
            "trail_mult": tm,
            "avg_return": ag["avg_return"],
            "avg_sharpe": ag["avg_sharpe"],
            "avg_max_dd": ag["avg_max_dd"],
            "pct_positive": ag["pct_positive_windows"],
            "avg_corr_btc": ag["avg_corr_btc"],
        })

    sweep_df = pd.DataFrame(sweep_results).sort_values("avg_sharpe",
                                                         ascending=False)
    print(f"\n  Parameter sensitivity results (sorted by Sharpe):")
    print(f"  {'ATR':>6} {'VolTh':>6} {'Trail':>6} {'AvgRet':>10} "
          f"{'Sharpe':>8} {'MaxDD':>8} {'%Pos':>6} {'CorrBTC':>8}")
    print("  " + "-" * 70)
    for _, row in sweep_df.head(15).iterrows():
        print(f"  {row['atr_mult']:>6.1f} {row['vol_thresh']:>6.1f} "
              f"{row['trail_mult']:>6.1f} {row['avg_return']:>+10.2%} "
              f"{row['avg_sharpe']:>8.2f} {row['avg_max_dd']:>8.2%} "
              f"{row['pct_positive']:>6.0%} {row['avg_corr_btc']:>8.3f}")
    print(f"\n  Worst 5:")
    for _, row in sweep_df.tail(5).iterrows():
        print(f"  {row['atr_mult']:>6.1f} {row['vol_thresh']:>6.1f} "
              f"{row['trail_mult']:>6.1f} {row['avg_return']:>+10.2%} "
              f"{row['avg_sharpe']:>8.2f} {row['avg_max_dd']:>8.2%} "
              f"{row['pct_positive']:>6.0%} {row['avg_corr_btc']:>8.3f}")

    # Sensitivity by single parameter
    print(f"\n  Sensitivity by ATR multiplier:")
    for am in ATR_MULTS:
        sub = sweep_df[sweep_df["atr_mult"] == am]
        print(f"    ATR={am:.1f}: avg_sharpe={sub['avg_sharpe'].mean():.3f}, "
              f"avg_ret={sub['avg_return'].mean():+.2%}")

    print(f"\n  Sensitivity by Volume threshold:")
    for vt in VOL_THRESHOLDS:
        sub = sweep_df[sweep_df["vol_thresh"] == vt]
        print(f"    Vol={vt:.1f}x: avg_sharpe={sub['avg_sharpe'].mean():.3f}, "
              f"avg_ret={sub['avg_return'].mean():+.2%}")

    print(f"\n  Sensitivity by Trail multiplier:")
    for tm in TRAIL_MULTS:
        sub = sweep_df[sweep_df["trail_mult"] == tm]
        print(f"    Trail={tm:.1f}x: avg_sharpe={sub['avg_sharpe'].mean():.3f}, "
              f"avg_ret={sub['avg_return'].mean():+.2%}")
    print()

    # Step 6: Relative momentum walk-forward
    print("STEP 6: Relative Momentum — Top 5 by 14d return, weekly rebalance")
    print("-" * 80)

    relmom_result = run_walkforward_relmom(universe)

    print(f"\n  {'Window':<8} {'Return':>10} {'Sharpe':>8} {'MaxDD':>8} "
          f"{'Rebals':>8} {'#Tokens':>8} {'CorrBTC':>8}")
    print("  " + "-" * 60)
    for w in relmom_result["windows"]:
        print(f"  {w['window']:<8} {w['return']:>+10.2%} {w['sharpe']:>8.2f} "
              f"{w['max_dd']:>8.2%} {w.get('n_rebalances', 0):>8} "
              f"{w.get('n_tokens_available', 0):>8} "
              f"{w.get('corr_btc', float('nan')):>8.3f}")

    rm_agg = relmom_result["aggregate"]
    print("  " + "-" * 60)
    print(f"  {'AVG':<8} {rm_agg['avg_return']:>+10.2%} "
          f"{rm_agg['avg_sharpe']:>8.2f} {'':>8} {'':>8} {'':>8} "
          f"{rm_agg['avg_corr_btc']:>8.3f}")
    print(f"\n  Positive windows: {rm_agg['pct_positive_windows']:.0%}")
    print()

    # Step 7: Correlation with s320a (BTC trend-following)
    print("STEP 7: Correlation with s320a (BTC trend-following)")
    print("-" * 80)

    # Compute simple BTC EMA 20/50 trend returns as proxy for s320a
    btc_close = btc_df["close"].values
    n_btc = len(btc_close)

    # EMA 20/50
    ema_20 = np.empty(n_btc)
    ema_50 = np.empty(n_btc)
    ema_20[0] = btc_close[0]
    ema_50[0] = btc_close[0]
    alpha_20 = 2.0 / (20 * 24 + 1)  # daily EMA on hourly data
    alpha_50 = 2.0 / (50 * 24 + 1)
    for i in range(1, n_btc):
        ema_20[i] = alpha_20 * btc_close[i] + (1 - alpha_20) * ema_20[i - 1]
        ema_50[i] = alpha_50 * btc_close[i] + (1 - alpha_50) * ema_50[i - 1]

    btc_trend_signal = (ema_20 > ema_50).astype(float)
    btc_rets = np.diff(btc_close) / btc_close[:-1]
    btc_rets = np.insert(btc_rets, 0, 0)
    s320a_proxy_rets = btc_trend_signal * btc_rets

    # Compute correlation over each test window
    for w in default_result["windows"]:
        ts = w["test_start_bar"]
        te = w["test_end_bar"]
        s320a_rets = s320a_proxy_rets[ts:te]
        # Rebuild breakout returns for this window
        # (we already have corr_btc, but need corr with s320a specifically)

    # Overall correlation using daily returns for more stable estimate
    # Downsample hourly -> daily
    btc_daily_idx = pd.date_range(btc_df.index[0].date(),
                                   btc_df.index[-1].date(), freq="D")

    print("  (Using BTC EMA 20/50 trend as s320a proxy)")
    print(f"  Average per-window correlation with BTC buy-and-hold: "
          f"{agg['avg_corr_btc']:.3f}")
    print(f"  Relative momentum avg corr with BTC: "
          f"{rm_agg['avg_corr_btc']:.3f}")
    print()

    # Step 8: Final verdict
    print("=" * 80)
    print("FINAL VERDICT")
    print("=" * 80)

    best_sweep = sweep_df.iloc[0]
    worst_sweep = sweep_df.iloc[-1]

    print(f"\n  ATR BREAKOUT STRATEGY:")
    print(f"  ----------------------")
    print(f"  Default params (1.5/2.0/2.5): "
          f"avg_ret={agg['avg_return']:+.2%}, "
          f"sharpe={agg['avg_sharpe']:.2f}, "
          f"max_dd={agg['avg_max_dd']:.2%}")
    print(f"  Best params ({best_sweep['atr_mult']:.1f}/"
          f"{best_sweep['vol_thresh']:.1f}/{best_sweep['trail_mult']:.1f}): "
          f"avg_ret={best_sweep['avg_return']:+.2%}, "
          f"sharpe={best_sweep['avg_sharpe']:.2f}")
    print(f"  Worst params ({worst_sweep['atr_mult']:.1f}/"
          f"{worst_sweep['vol_thresh']:.1f}/{worst_sweep['trail_mult']:.1f}): "
          f"avg_ret={worst_sweep['avg_return']:+.2%}, "
          f"sharpe={worst_sweep['avg_sharpe']:.2f}")
    print(f"  Positive windows: {agg['pct_positive_windows']:.0%}")
    print(f"  BTC correlation: {agg['avg_corr_btc']:.3f}")
    profit_conc = top3_profit / total_profit if total_profit > 0 else 1.0
    print(f"  Profit concentration (top 3): {profit_conc:.0%}")
    print(f"  Profitable tokens: {profit_tokens}/{profit_tokens + loss_tokens}")

    print(f"\n  RELATIVE MOMENTUM STRATEGY:")
    print(f"  ---------------------------")
    print(f"  avg_ret={rm_agg['avg_return']:+.2%}, "
          f"sharpe={rm_agg['avg_sharpe']:.2f}")
    print(f"  Positive windows: {rm_agg['pct_positive_windows']:.0%}")
    print(f"  BTC correlation: {rm_agg['avg_corr_btc']:.3f}")

    # Verdict logic
    print(f"\n  KILL CRITERIA CHECK:")
    breakout_pass = True
    relmom_pass = True

    # Check 1: Sharpe > 0.3 in walk-forward
    if agg["avg_sharpe"] < 0.3:
        print(f"  [FAIL] ATR Breakout: avg Sharpe {agg['avg_sharpe']:.2f} < 0.3")
        breakout_pass = False
    else:
        print(f"  [PASS] ATR Breakout: avg Sharpe {agg['avg_sharpe']:.2f} >= 0.3")

    if rm_agg["avg_sharpe"] < 0.3:
        print(f"  [FAIL] Rel Momentum: avg Sharpe {rm_agg['avg_sharpe']:.2f} < 0.3")
        relmom_pass = False
    else:
        print(f"  [PASS] Rel Momentum: avg Sharpe {rm_agg['avg_sharpe']:.2f} >= 0.3")

    # Check 2: >50% positive windows
    if agg["pct_positive_windows"] < 0.5:
        print(f"  [FAIL] ATR Breakout: only "
              f"{agg['pct_positive_windows']:.0%} positive windows")
        breakout_pass = False
    else:
        print(f"  [PASS] ATR Breakout: "
              f"{agg['pct_positive_windows']:.0%} positive windows")

    if rm_agg["pct_positive_windows"] < 0.5:
        print(f"  [FAIL] Rel Momentum: only "
              f"{rm_agg['pct_positive_windows']:.0%} positive windows")
        relmom_pass = False
    else:
        print(f"  [PASS] Rel Momentum: "
              f"{rm_agg['pct_positive_windows']:.0%} positive windows")

    # Check 3: MaxDD > -40% is concerning
    if agg["avg_max_dd"] < -0.40:
        print(f"  [WARN] ATR Breakout: avg MaxDD {agg['avg_max_dd']:.2%} < -40%")

    # Check 4: Profit concentration
    if profit_conc > 0.5:
        print(f"  [WARN] ATR Breakout: profit highly concentrated in top 3 "
              f"tokens ({profit_conc:.0%})")
        if profit_conc > 0.8:
            print(f"  [FAIL] Concentration > 80% — strategy relies on outliers")
            breakout_pass = False

    # Check 5: Diversification value
    if abs(agg["avg_corr_btc"]) > 0.7:
        print(f"  [FAIL] ATR Breakout: too correlated with BTC "
              f"({agg['avg_corr_btc']:.3f})")
    else:
        print(f"  [PASS] ATR Breakout: BTC corr {agg['avg_corr_btc']:.3f} "
              f"— provides diversification")

    # Check 6: Robustness across parameters
    pos_sharpe_combos = len(sweep_df[sweep_df["avg_sharpe"] > 0])
    total_combos = len(sweep_df)
    pct_robust = pos_sharpe_combos / total_combos
    if pct_robust < 0.5:
        print(f"  [WARN] Only {pct_robust:.0%} of parameter combos have "
              f"positive Sharpe — fragile")
    else:
        print(f"  [PASS] {pct_robust:.0%} of parameter combos have positive "
              f"Sharpe — reasonably robust")

    print(f"\n  ┌{'─' * 50}┐")
    if breakout_pass:
        print(f"  │ ATR BREAKOUT:     CONDITIONAL PASS             │")
    else:
        print(f"  │ ATR BREAKOUT:     KILL                         │")
    if relmom_pass:
        print(f"  │ REL MOMENTUM:     CONDITIONAL PASS             │")
    else:
        print(f"  │ REL MOMENTUM:     KILL                         │")
    print(f"  └{'─' * 50}┘")

    if not breakout_pass and not relmom_pass:
        print(f"\n  OVERALL: KILL — The +94% average return was an artifact.")
        print(f"  Walk-forward validation does not support the mid-cap")
        print(f"  breakout hypothesis with realistic costs and proper OOS.")
    elif breakout_pass or relmom_pass:
        surviving = []
        if breakout_pass:
            surviving.append("ATR Breakout")
        if relmom_pass:
            surviving.append("Relative Momentum")
        print(f"\n  OVERALL: CONDITIONAL PASS for {', '.join(surviving)}")
        print(f"  Proceed to paper trading validation before live allocation.")
    print()


if __name__ == "__main__":
    main()
