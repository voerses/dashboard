#!/usr/bin/env python3
"""BTC Regime → Alt Basket Strategy: Iterative Parameter Optimization

When BTC trend is bullish (fast EMA > slow EMA), buy a basket of altcoins.
When bearish, go flat. Sweeps 360+ parameter combinations, then refines.
"""
import numpy as np
import pandas as pd
import os
import sys
import warnings
from itertools import product
from pathlib import Path

warnings.filterwarnings('ignore')

DATA_DIR = Path('/workspace/crypto_backtest/data/spot/1h_cache')
PERP_DIR = Path('/workspace/crypto_backtest/data/perp/1h_cache')
CAPITAL = 200_000
COST_BPS = 20  # 0.2% round trip

# Tokens to consider for alt basket (must have spot data)
ALT_TOKENS = [
    'ETH', 'SOL', 'BNB', 'AVAX', 'LINK', 'DOT', 'NEAR', 'ADA', 'XRP',
    'DOGE', 'ATOM', 'UNI', 'INJ', 'FIL', 'AAVE', 'LTC', 'BCH', 'HBAR',
    'XLM', 'TRX'
]


def load_token(token, data_dir=DATA_DIR):
    """Load 1h OHLCV data for a token."""
    path = data_dir / f'{token}_1h.parquet'
    if not path.exists():
        path = PERP_DIR / f'{token}_1h.parquet'
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    if 'close' not in df.columns:
        return None
    return df[['open', 'high', 'low', 'close', 'volume']].copy()


def compute_ema(prices, span):
    """Vectorized EMA."""
    alpha = 2.0 / (span + 1)
    out = np.empty_like(prices)
    out[0] = prices[0]
    for i in range(1, len(prices)):
        out[i] = alpha * prices[i] + (1 - alpha) * out[i - 1]
    return out


def compute_sma(prices, window):
    """Rolling SMA."""
    cs = np.cumsum(np.nan_to_num(np.array(prices, dtype=np.float64), 0.0))
    cs = np.insert(cs, 0, 0.0)
    out = np.full(len(prices), np.nan)
    out[window - 1:] = (cs[window:] - cs[:-window]) / window
    return out


def compute_rsi(prices, period=14):
    """RSI calculation."""
    delta = np.diff(prices, prepend=prices[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.empty_like(prices)
    avg_loss = np.empty_like(prices)
    avg_gain[:period] = np.nan
    avg_loss[:period] = np.nan
    avg_gain[period] = np.mean(gain[1:period + 1])
    avg_loss[period] = np.mean(loss[1:period + 1])
    for i in range(period + 1, len(prices)):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[i]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[i]) / period
    rs = avg_gain / np.where(avg_loss == 0, 1e-10, avg_loss)
    rsi = 100 - 100 / (1 + rs)
    return rsi


def resample_daily(df):
    """Resample hourly to daily OHLCV."""
    daily = df.resample('D').agg({
        'open': 'first', 'high': 'max', 'low': 'min',
        'close': 'last', 'volume': 'sum'
    }).dropna()
    return daily


def btc_regime_signal(btc_daily_close, fast, slow, signal_type='ema', confirm_hours=0):
    """Compute BTC regime: 1=bullish, 0=bearish."""
    prices = np.array(btc_daily_close.values, dtype=np.float64)
    if signal_type == 'ema':
        fast_line = compute_ema(prices, fast)
        slow_line = compute_ema(prices, slow)
    elif signal_type == 'sma':
        fast_line = compute_sma(prices, fast)
        slow_line = compute_sma(prices, slow)
    else:  # confirmed ema
        fast_line = compute_ema(prices, fast)
        slow_line = compute_ema(prices, slow)

    raw_signal = (fast_line > slow_line).astype(float)
    raw_signal[np.isnan(fast_line) | np.isnan(slow_line)] = 0

    if signal_type == 'confirmed' and confirm_hours > 0:
        # Require N consecutive bars of agreement
        confirm_bars = max(1, confirm_hours // 24)
        confirmed = np.zeros_like(raw_signal)
        count = 0
        for i in range(len(raw_signal)):
            if raw_signal[i] == 1:
                count += 1
                if count >= confirm_bars:
                    confirmed[i] = 1
            else:
                count = 0
                confirmed[i] = 0
        return pd.Series(confirmed, index=btc_daily_close.index)

    return pd.Series(raw_signal, index=btc_daily_close.index)


def backtest_regime_basket(btc_regime_daily, alt_returns_hourly, alt_tokens, n_alts,
                           capital=CAPITAL, cost_bps=COST_BPS,
                           selection='equal', momentum_days=30):
    """
    Backtest: when BTC regime=1, go long top N alts equally weighted.
    When regime=0, flat.

    selection: 'equal' (any N alts) or 'momentum' (top N by trailing return)
    """
    # Align regime to hourly (forward fill daily regime)
    regime_hourly = btc_regime_daily.reindex(alt_returns_hourly.index, method='ffill').fillna(0)

    n_tokens = len(alt_tokens)
    if n_alts > n_tokens:
        n_alts = n_tokens

    # Portfolio equity curve
    equity = np.ones(len(alt_returns_hourly)) * capital
    position = np.zeros(len(alt_returns_hourly))  # 1=long, 0=flat
    total_costs = 0.0
    trades = 0

    # For momentum selection, compute rolling returns
    if selection == 'momentum' and momentum_days > 0:
        lookback = momentum_days * 24
        rolling_ret = alt_returns_hourly.rolling(lookback).sum()  # approx
    else:
        rolling_ret = None

    # Regime changes
    regime_vals = regime_hourly.values
    prev_regime = 0

    # Track daily equity for DD calculation
    daily_equity = []

    # Simple vectorized approach: compute portfolio return when in position
    # First, determine which tokens to hold at each point
    # For simplicity with equal weighting across all alts
    if selection == 'equal':
        # Equal weight across top N by average volume (static)
        selected_tokens = alt_tokens[:n_alts]
        # Portfolio return = mean of selected alt returns
        portfolio_ret = alt_returns_hourly[selected_tokens].mean(axis=1)
    else:
        # This would need dynamic selection - more complex
        # For now just use equal
        selected_tokens = alt_tokens[:n_alts]
        portfolio_ret = alt_returns_hourly[selected_tokens].mean(axis=1)

    # Apply regime filter
    active = regime_vals.astype(bool)

    # Detect regime changes for cost calculation
    regime_changes = np.diff(active.astype(int), prepend=0)
    entries = np.sum(regime_changes == 1)
    exits = np.sum(regime_changes == -1)
    trades = entries + exits

    # Cost per trade: cost_bps/10000 * portfolio_value * n_alts
    cost_per_trade = (cost_bps / 10000) * capital  # approximate

    # Compute equity curve
    ret_when_active = portfolio_ret.values * active
    equity_curve = capital * np.cumprod(1 + ret_when_active)

    # Subtract costs at regime changes
    total_costs = trades * cost_per_trade
    equity_curve = equity_curve * (1 - total_costs / capital)  # rough approximation

    # Better cost model: apply costs at each entry/exit
    cumret = np.cumprod(1 + ret_when_active)
    cost_drag = 1.0
    for i in range(len(regime_changes)):
        if regime_changes[i] != 0:
            cost_drag *= (1 - cost_bps / 10000)
    equity_curve = capital * cumret * cost_drag

    # Metrics
    if len(equity_curve) < 24:
        return None

    # Daily equity for Sharpe/DD
    eq_series = pd.Series(equity_curve, index=alt_returns_hourly.index)
    daily_eq = eq_series.resample('D').last().dropna()

    if len(daily_eq) < 30:
        return None

    daily_rets = daily_eq.pct_change().dropna()
    years = len(daily_rets) / 365.25

    total_ret = (equity_curve[-1] / capital) - 1
    ann_ret = (1 + total_ret) ** (1 / max(years, 0.1)) - 1

    sharpe = daily_rets.mean() / max(daily_rets.std(), 1e-10) * np.sqrt(365.25)

    # Max drawdown
    peak = np.maximum.accumulate(daily_eq.values)
    dd = (daily_eq.values - peak) / peak
    max_dd = np.min(dd)

    calmar = ann_ret / max(abs(max_dd), 0.01)

    # Sortino
    neg_rets = daily_rets[daily_rets < 0]
    downside_std = neg_rets.std() if len(neg_rets) > 0 else 1e-10
    sortino = daily_rets.mean() / max(downside_std, 1e-10) * np.sqrt(365.25)

    # Win rate (daily)
    win_rate = (daily_rets > 0).mean()

    # Time in market
    time_in_market = active.mean()

    return {
        'ann_ret': ann_ret,
        'total_ret': total_ret,
        'sharpe': sharpe,
        'sortino': sortino,
        'max_dd': max_dd,
        'calmar': calmar,
        'trades': trades,
        'win_rate': win_rate,
        'time_in_market': time_in_market,
        'years': years,
    }


def main():
    print("=" * 100)
    print("BTC REGIME → ALT BASKET STRATEGY: ITERATIVE OPTIMIZATION")
    print("=" * 100)

    # ── LOAD DATA ──────────────────────────────────────────────────
    print("\n[1] Loading data...")
    btc = load_token('BTC')
    if btc is None:
        print("ERROR: Cannot load BTC data")
        return

    btc_daily = resample_daily(btc)
    print(f"  BTC: {len(btc)} hourly bars, {btc.index[0]} to {btc.index[-1]}")
    print(f"  BTC daily: {len(btc_daily)} bars")

    # Load alt tokens
    alt_data = {}
    for tok in ALT_TOKENS:
        df = load_token(tok)
        if df is not None and len(df) > 8760:  # At least 1 year
            alt_data[tok] = df
            print(f"  {tok}: {len(df)} bars, {df.index[0].strftime('%Y-%m-%d')} to {df.index[-1].strftime('%Y-%m-%d')}")

    print(f"\n  Loaded {len(alt_data)} alt tokens with >1yr data")

    # Compute hourly returns for all alts, aligned to BTC timeframe
    common_idx = btc.index
    for tok in list(alt_data.keys()):
        common_idx = common_idx.intersection(alt_data[tok].index)

    print(f"  Common timeframe: {len(common_idx)} bars ({common_idx[0]} to {common_idx[-1]})")

    alt_returns = pd.DataFrame(index=common_idx)
    for tok, df in alt_data.items():
        aligned = df.reindex(common_idx)
        alt_returns[tok] = aligned['close'].pct_change().fillna(0)

    available_alts = list(alt_data.keys())

    # ── PHASE 1: FULL PARAMETER SWEEP ──────────────────────────────
    print("\n" + "=" * 100)
    print("PHASE 1: FULL PARAMETER SWEEP (360+ combos)")
    print("=" * 100)

    fast_emas = [10, 15, 20, 30]
    slow_emas = [40, 50, 60, 80, 100]
    signal_types = ['ema', 'sma', 'confirmed']
    basket_sizes = [3, 5, 8, 10, 15, 20]

    results = []
    total = len(fast_emas) * len(slow_emas) * len(signal_types) * len(basket_sizes)
    count = 0

    for fast, slow, sig_type, n_alts in product(fast_emas, slow_emas, signal_types, basket_sizes):
        if fast >= slow:
            continue
        count += 1
        if count % 50 == 0:
            print(f"  Running combo {count}/{total}...")

        regime = btc_regime_signal(btc_daily['close'], fast, slow, sig_type, confirm_hours=24)
        result = backtest_regime_basket(regime, alt_returns, available_alts, n_alts)

        if result is not None:
            result.update({
                'fast': fast, 'slow': slow, 'signal': sig_type, 'n_alts': n_alts
            })
            results.append(result)

    df_results = pd.DataFrame(results)
    print(f"\n  Completed {len(df_results)} valid combos out of {count} tested")

    # Sort by Calmar
    df_sorted = df_results.sort_values('calmar', ascending=False)

    print(f"\n  {'fast':>4}  {'slow':>4}  {'signal':>9}  {'N':>3}  {'ann_ret':>8}  {'sharpe':>7}  {'max_dd':>8}  {'calmar':>7}  {'sortino':>8}  {'trades':>6}  {'TiM':>5}")
    print("  " + "-" * 90)
    for _, r in df_sorted.head(30).iterrows():
        print(f"  {int(r['fast']):4d}  {int(r['slow']):4d}  {r['signal']:>9s}  {int(r['n_alts']):3d}"
              f"  {r['ann_ret']:+7.1%}  {r['sharpe']:+6.2f}  {r['max_dd']:+7.1%}  {r['calmar']:+6.2f}"
              f"  {r['sortino']:+7.2f}  {int(r['trades']):5d}  {r['time_in_market']:4.1%}")

    # Summary stats
    print(f"\n  Positive annual return: {(df_results['ann_ret'] > 0).sum()}/{len(df_results)} combos ({(df_results['ann_ret'] > 0).mean():.0%})")
    print(f"  Calmar > 0.5: {(df_results['calmar'] > 0.5).sum()}/{len(df_results)}")
    print(f"  Calmar > 1.0: {(df_results['calmar'] > 1.0).sum()}/{len(df_results)}")
    print(f"  Ann return > 50%: {(df_results['ann_ret'] > 0.5).sum()}/{len(df_results)}")
    print(f"  Ann return > 100%: {(df_results['ann_ret'] > 1.0).sum()}/{len(df_results)}")

    # ── PHASE 2: REFINE TOP 10 ──────────────────────────────────────
    print("\n" + "=" * 100)
    print("PHASE 2: REFINE TOP 10 COMBOS")
    print("=" * 100)

    top10 = df_sorted.head(10)
    refined_results = []

    for _, base in top10.iterrows():
        # Try ±20% around each parameter
        base_fast = int(base['fast'])
        base_slow = int(base['slow'])
        base_n = int(base['n_alts'])
        sig = base['signal']

        for fast_adj in [-0.2, -0.1, 0, 0.1, 0.2]:
            for slow_adj in [-0.2, -0.1, 0, 0.1, 0.2]:
                fast = max(5, int(base_fast * (1 + fast_adj)))
                slow = max(fast + 5, int(base_slow * (1 + slow_adj)))

                for n_adj in [-2, -1, 0, 1, 2]:
                    n_alts = max(2, min(20, base_n + n_adj))

                    regime = btc_regime_signal(btc_daily['close'], fast, slow, sig, confirm_hours=24)
                    result = backtest_regime_basket(regime, alt_returns, available_alts, n_alts)

                    if result is not None:
                        result.update({
                            'fast': fast, 'slow': slow, 'signal': sig, 'n_alts': n_alts
                        })
                        refined_results.append(result)

    df_refined = pd.DataFrame(refined_results)
    df_refined_sorted = df_refined.sort_values('calmar', ascending=False).drop_duplicates(
        subset=['fast', 'slow', 'signal', 'n_alts'])

    print(f"\n  Refined {len(df_refined_sorted)} combos")
    print(f"\n  TOP 10 REFINED:")
    print(f"  {'fast':>4}  {'slow':>4}  {'signal':>9}  {'N':>3}  {'ann_ret':>8}  {'sharpe':>7}  {'max_dd':>8}  {'calmar':>7}  {'sortino':>8}")
    print("  " + "-" * 75)
    for _, r in df_refined_sorted.head(10).iterrows():
        print(f"  {int(r['fast']):4d}  {int(r['slow']):4d}  {r['signal']:>9s}  {int(r['n_alts']):3d}"
              f"  {r['ann_ret']:+7.1%}  {r['sharpe']:+6.2f}  {r['max_dd']:+7.1%}  {r['calmar']:+6.2f}"
              f"  {r['sortino']:+7.2f}")

    # ── PHASE 3: MOMENTUM-BASED TOKEN SELECTION ─────────────────────
    print("\n" + "=" * 100)
    print("PHASE 3: DYNAMIC TOKEN SELECTION (momentum-ranked)")
    print("=" * 100)

    # Take best regime params from Phase 2
    best = df_refined_sorted.iloc[0]
    best_fast = int(best['fast'])
    best_slow = int(best['slow'])
    best_sig = best['signal']
    best_n = int(best['n_alts'])

    print(f"  Using best params: fast={best_fast}, slow={best_slow}, signal={best_sig}, N={best_n}")

    regime = btc_regime_signal(btc_daily['close'], best_fast, best_slow, best_sig, confirm_hours=24)
    regime_hourly = regime.reindex(common_idx, method='ffill').fillna(0)

    # Dynamic momentum selection: at each regime flip, pick top N by trailing 30d return
    momentum_results = []
    for lookback_days in [7, 14, 30, 60]:
        lookback_hours = lookback_days * 24

        # Compute rolling returns for each alt
        rolling_rets = {}
        for tok in available_alts:
            prices = alt_data[tok].reindex(common_idx)['close']
            rolling_rets[tok] = prices.pct_change(lookback_hours)

        rolling_df = pd.DataFrame(rolling_rets, index=common_idx)

        # Build dynamic portfolio
        regime_vals = regime_hourly.values
        portfolio_ret = np.zeros(len(common_idx))

        # Track currently held tokens
        held_tokens = []
        in_position = False
        trade_count = 0

        for i in range(lookback_hours, len(common_idx)):
            if regime_vals[i] == 1 and not in_position:
                # Entry: select top N by momentum
                mom_scores = rolling_df.iloc[i].dropna().sort_values(ascending=False)
                held_tokens = list(mom_scores.head(best_n).index)
                in_position = True
                trade_count += 1
            elif regime_vals[i] == 0 and in_position:
                in_position = False
                held_tokens = []
                trade_count += 1

            if in_position and len(held_tokens) > 0:
                # Equal weight returns of held tokens
                ret = 0
                for tok in held_tokens:
                    ret += alt_returns[tok].iloc[i] / len(held_tokens)
                portfolio_ret[i] = ret

        # Apply costs
        cost_drag = (1 - COST_BPS / 10000) ** trade_count
        equity_curve = CAPITAL * np.cumprod(1 + portfolio_ret) * cost_drag

        # Metrics
        eq_series = pd.Series(equity_curve, index=common_idx)
        daily_eq = eq_series.resample('D').last().dropna()
        daily_rets = daily_eq.pct_change().dropna()
        years = len(daily_rets) / 365.25

        total_ret = (equity_curve[-1] / CAPITAL) - 1
        ann_ret = (1 + total_ret) ** (1 / max(years, 0.1)) - 1
        sharpe = daily_rets.mean() / max(daily_rets.std(), 1e-10) * np.sqrt(365.25)

        peak = np.maximum.accumulate(daily_eq.values)
        dd = (daily_eq.values - peak) / peak
        max_dd = np.min(dd)
        calmar = ann_ret / max(abs(max_dd), 0.01)

        neg_rets = daily_rets[daily_rets < 0]
        downside_std = neg_rets.std() if len(neg_rets) > 0 else 1e-10
        sortino = daily_rets.mean() / max(downside_std, 1e-10) * np.sqrt(365.25)

        momentum_results.append({
            'lookback_days': lookback_days,
            'ann_ret': ann_ret,
            'sharpe': sharpe,
            'sortino': sortino,
            'max_dd': max_dd,
            'calmar': calmar,
            'trades': trade_count,
        })
        print(f"  Momentum {lookback_days}d: AnnRet={ann_ret:+.1%}  Sharpe={sharpe:+.2f}  MaxDD={max_dd:+.1%}  Calmar={calmar:+.2f}  Trades={trade_count}")

    # Compare with static equal weight
    static_result = backtest_regime_basket(regime, alt_returns, available_alts, best_n)
    if static_result:
        print(f"  Static equal wt: AnnRet={static_result['ann_ret']:+.1%}  Sharpe={static_result['sharpe']:+.2f}  MaxDD={static_result['max_dd']:+.1%}  Calmar={static_result['calmar']:+.2f}")

    # ── PHASE 4: INVERSE VOLATILITY WEIGHTING ───────────────────────
    print("\n" + "=" * 100)
    print("PHASE 4: INVERSE VOLATILITY WEIGHTING")
    print("=" * 100)

    for vol_window in [7, 14, 30]:
        vol_hours = vol_window * 24

        # Compute rolling volatility for each alt
        rolling_vols = {}
        for tok in available_alts:
            hourly_ret = alt_returns[tok]
            rolling_vols[tok] = hourly_ret.rolling(vol_hours).std() * np.sqrt(24)  # daily vol

        vol_df = pd.DataFrame(rolling_vols, index=common_idx)

        # Build portfolio with inverse vol weighting
        regime_vals = regime_hourly.values
        portfolio_ret = np.zeros(len(common_idx))
        in_position = False
        trade_count = 0

        for i in range(vol_hours, len(common_idx)):
            if regime_vals[i] == 1 and not in_position:
                in_position = True
                trade_count += 1
                # Compute inverse vol weights
                vols = vol_df.iloc[i].dropna()
                vols = vols[vols > 0]
                if len(vols) == 0:
                    continue
                inv_vol = 1.0 / vols
                weights = inv_vol / inv_vol.sum()
                selected = weights.nlargest(best_n)
                selected = selected / selected.sum()  # renormalize
            elif regime_vals[i] == 0 and in_position:
                in_position = False
                trade_count += 1

            if in_position and 'selected' in dir():
                ret = 0
                for tok, w in selected.items():
                    if tok in alt_returns.columns:
                        ret += alt_returns[tok].iloc[i] * w
                portfolio_ret[i] = ret

        cost_drag = (1 - COST_BPS / 10000) ** trade_count
        equity_curve = CAPITAL * np.cumprod(1 + portfolio_ret) * cost_drag

        eq_series = pd.Series(equity_curve, index=common_idx)
        daily_eq = eq_series.resample('D').last().dropna()
        daily_rets = daily_eq.pct_change().dropna()
        years = len(daily_rets) / 365.25

        total_ret = (equity_curve[-1] / CAPITAL) - 1
        ann_ret = (1 + total_ret) ** (1 / max(years, 0.1)) - 1
        sharpe = daily_rets.mean() / max(daily_rets.std(), 1e-10) * np.sqrt(365.25)

        peak = np.maximum.accumulate(daily_eq.values)
        dd = (daily_eq.values - peak) / peak
        max_dd = np.min(dd)
        calmar = ann_ret / max(abs(max_dd), 0.01)

        print(f"  InvVol {vol_window}d: AnnRet={ann_ret:+.1%}  Sharpe={sharpe:+.2f}  MaxDD={max_dd:+.1%}  Calmar={calmar:+.2f}  Trades={trade_count}")

    # ── PHASE 5: WALK-FORWARD VALIDATION ────────────────────────────
    print("\n" + "=" * 100)
    print("PHASE 5: WALK-FORWARD VALIDATION")
    print("=" * 100)

    # Test top 3 combos on train/test split
    top3 = df_refined_sorted.head(3)

    splits = [
        ('2020-2023 → 2024-2026', '2020-01-01', '2023-12-31', '2024-01-01', '2026-03-14'),
        ('2020-2022 → 2023-2025', '2020-01-01', '2022-12-31', '2023-01-01', '2025-12-31'),
    ]

    print(f"\n  {'Combo':>30}  {'Split':>20}  {'IS_ret':>8}  {'OOS_ret':>8}  {'IS_sharpe':>9}  {'OOS_sharpe':>10}  {'Degrad':>7}")
    print("  " + "-" * 100)

    for _, combo in top3.iterrows():
        fast = int(combo['fast'])
        slow = int(combo['slow'])
        sig = combo['signal']
        n = int(combo['n_alts'])
        combo_name = f"f{fast}_s{slow}_{sig}_n{n}"

        for split_name, train_start, train_end, test_start, test_end in splits:
            # Train period
            train_mask = (common_idx >= train_start) & (common_idx <= train_end)
            test_mask = (common_idx >= test_start) & (common_idx <= test_end)

            train_idx = common_idx[train_mask]
            test_idx = common_idx[test_mask]

            if len(train_idx) < 1000 or len(test_idx) < 1000:
                continue

            # Compute regime for full period (signals only use past data, so this is fine)
            regime_full = btc_regime_signal(btc_daily['close'], fast, slow, sig, confirm_hours=24)

            # IS backtest
            is_regime = regime_full.loc[regime_full.index <= train_end]
            is_returns = alt_returns.loc[train_idx]
            is_result = backtest_regime_basket(is_regime, is_returns, available_alts, n)

            # OOS backtest
            oos_regime = regime_full.loc[regime_full.index <= test_end]
            oos_returns = alt_returns.loc[test_idx]
            oos_result = backtest_regime_basket(oos_regime, oos_returns, available_alts, n)

            if is_result and oos_result:
                degrad = 1 - (oos_result['ann_ret'] / max(is_result['ann_ret'], 0.01)) if is_result['ann_ret'] > 0 else float('nan')
                print(f"  {combo_name:>30}  {split_name:>20}  {is_result['ann_ret']:+7.1%}  {oos_result['ann_ret']:+7.1%}"
                      f"  {is_result['sharpe']:+8.2f}  {oos_result['sharpe']:+9.2f}  {degrad:+6.0%}" if not np.isnan(degrad)
                      else f"  {combo_name:>30}  {split_name:>20}  {is_result['ann_ret']:+7.1%}  {oos_result['ann_ret']:+7.1%}"
                      f"  {is_result['sharpe']:+8.2f}  {oos_result['sharpe']:+9.2f}     N/A")

    # ── PHASE 6: LEVERAGE SWEEP ON BEST COMBO ───────────────────────
    print("\n" + "=" * 100)
    print("PHASE 6: LEVERAGE SWEEP ON BEST COMBO")
    print("=" * 100)

    best = df_refined_sorted.iloc[0]
    regime = btc_regime_signal(btc_daily['close'], int(best['fast']), int(best['slow']),
                               best['signal'], confirm_hours=24)
    regime_hourly_vals = regime.reindex(common_idx, method='ffill').fillna(0).values

    # Base portfolio return (equal weight, all alts)
    selected_tokens = available_alts[:int(best['n_alts'])]
    base_ret = alt_returns[selected_tokens].mean(axis=1).values
    active = regime_hourly_vals.astype(bool)
    ret_when_active = base_ret * active

    print(f"\n  Using best combo: fast={int(best['fast'])}, slow={int(best['slow'])}, signal={best['signal']}, N={int(best['n_alts'])}")
    print(f"\n  {'Leverage':>8}  {'ann_ret':>8}  {'sharpe':>7}  {'max_dd':>8}  {'calmar':>7}  {'fund_cost':>10}  {'liquidated':>10}")
    print("  " + "-" * 70)

    for leverage in [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]:
        # Leveraged returns (minus funding cost if leverage > 1)
        funding_annual = 0.12 if leverage > 1 else 0.0  # 12% on notional
        funding_hourly = funding_annual / 8760

        leveraged_ret = ret_when_active * leverage - active * funding_hourly * (leverage - 1)

        # Circuit breaker: if DD > 25%, reduce to 1x
        equity = np.ones(len(leveraged_ret)) * CAPITAL
        current_leverage = leverage
        for i in range(1, len(leveraged_ret)):
            peak = np.max(equity[:i])
            dd = (equity[i - 1] - peak) / peak
            if dd < -0.25:
                current_leverage = 1.0
            elif dd > -0.15:
                current_leverage = leverage
            equity[i] = equity[i - 1] * (1 + leveraged_ret[i])

        # Liquidation check
        liquidated = False
        liq_threshold = 1.0 / (leverage + 0.5) if leverage > 1 else 1.0
        peak_eq = np.maximum.accumulate(equity)
        dd_curve = (equity - peak_eq) / peak_eq
        if np.min(dd_curve) < -liq_threshold:
            liquidated = True

        daily_eq = pd.Series(equity, index=common_idx).resample('D').last().dropna()
        daily_rets = daily_eq.pct_change().dropna()
        years = len(daily_rets) / 365.25

        total_ret = (equity[-1] / CAPITAL) - 1
        ann_ret = (1 + total_ret) ** (1 / max(years, 0.1)) - 1
        sharpe = daily_rets.mean() / max(daily_rets.std(), 1e-10) * np.sqrt(365.25)
        max_dd = np.min(dd_curve)
        calmar = ann_ret / max(abs(max_dd), 0.01)
        fund_cost = funding_annual * (leverage - 1) * active.mean()

        print(f"  {leverage:7.1f}x  {ann_ret:+7.1%}  {sharpe:+6.2f}  {max_dd:+7.1%}  {calmar:+6.2f}  {fund_cost:+9.1%}  {'YES' if liquidated else 'no':>10}")

    # ── SUMMARY ─────────────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("SUMMARY")
    print("=" * 100)

    best = df_refined_sorted.iloc[0]
    print(f"\n  Best combo: fast={int(best['fast'])}, slow={int(best['slow'])}, signal={best['signal']}, N={int(best['n_alts'])}")
    print(f"  Annual return: {best['ann_ret']:+.1%}")
    print(f"  Sharpe: {best['sharpe']:+.2f}")
    print(f"  Sortino: {best['sortino']:+.2f}")
    print(f"  Max DD: {best['max_dd']:+.1%}")
    print(f"  Calmar: {best['calmar']:+.2f}")
    print(f"  Trades: {int(best['trades'])}")
    print(f"  Time in market: {best['time_in_market']:.0%}")

    print(f"\n  Overall stats:")
    print(f"  - {(df_results['ann_ret'] > 0).sum()}/{len(df_results)} combos profitable")
    print(f"  - Best ann return: {df_results['ann_ret'].max():+.1%}")
    print(f"  - Best Sharpe: {df_results['sharpe'].max():+.2f}")
    print(f"  - Best Calmar: {df_results['calmar'].max():+.2f}")


if __name__ == '__main__':
    main()
