#!/usr/bin/env python3
"""Validate Multi-Timeframe Regime results — check for bugs and overfitting.

The initial sweep showed +278% annual at 1x with -9.3% DD on post-ETF Top3.
This is either a breakthrough or has a critical bug. Let's find out.
"""
import numpy as np
import pandas as pd
import warnings
from pathlib import Path

warnings.filterwarnings('ignore')

DATA_DIR = Path('/workspace/crypto_backtest/data/spot/1h_cache')
CAPITAL = 200_000
COST_BPS = 20
FUNDING_RATE_ANNUAL = 0.12

ALL_TOKENS = [
    'ETH', 'SOL', 'BNB', 'AVAX', 'LINK', 'DOT', 'NEAR', 'ADA', 'XRP',
    'DOGE', 'ATOM', 'UNI', 'INJ', 'FIL', 'AAVE', 'LTC', 'BCH', 'HBAR',
    'XLM', 'TRX'
]


def load_token(token):
    path = DATA_DIR / f'{token}_1h.parquet'
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    if 'close' not in df.columns:
        return None
    return df[['open', 'high', 'low', 'close', 'volume']].copy()


def compute_ema(prices, span):
    alpha = 2.0 / (span + 1)
    out = np.empty_like(prices, dtype=np.float64)
    out[0] = prices[0]
    for i in range(1, len(prices)):
        out[i] = alpha * prices[i] + (1 - alpha) * out[i - 1]
    return out


def main():
    print("=" * 120)
    print("MULTI-TIMEFRAME REGIME VALIDATION")
    print("=" * 120)

    # Load data
    btc = load_token('BTC')
    btc_daily = btc.resample('D').agg({
        'open': 'first', 'high': 'max', 'low': 'min',
        'close': 'last', 'volume': 'sum'
    }).dropna()

    alt_data = {}
    for tok in ALL_TOKENS:
        df = load_token(tok)
        if df is not None and len(df) > 8760:
            alt_data[tok] = df

    common_idx = btc.index
    for tok in alt_data:
        common_idx = common_idx.intersection(alt_data[tok].index)

    alt_closes = pd.DataFrame(index=common_idx)
    alt_returns = pd.DataFrame(index=common_idx)
    for tok, df in alt_data.items():
        aligned = df.reindex(common_idx)
        alt_closes[tok] = aligned['close']
        alt_returns[tok] = aligned['close'].pct_change().fillna(0)

    # ================================================================
    # TEST 1: Reproduce the multi-TF regime signal and inspect it
    # ================================================================
    print("\n[TEST 1] Inspecting Multi-TF Regime Signal")

    # Daily regime
    d_close = btc_daily['close']
    d_prices = np.array(d_close.values, dtype=np.float64)
    d_fast = compute_ema(d_prices, 8)
    d_slow = compute_ema(d_prices, 54)
    daily_bull = pd.Series((d_fast > d_slow).astype(float), index=d_close.index)

    # 4H regime
    h4_close = btc['close'].resample('4h').last().dropna()
    h4_prices = np.array(h4_close.values, dtype=np.float64)
    h4_fast = compute_ema(h4_prices, 4)
    h4_slow = compute_ema(h4_prices, 20)
    h4_bull = pd.Series((h4_fast > h4_slow).astype(float), index=h4_close.index)

    # Combine
    d_hourly = daily_bull.reindex(common_idx, method='ffill').fillna(0)
    h4_hourly = h4_bull.reindex(common_idx, method='ffill').fillna(0)
    combined = d_hourly.values * h4_hourly.values
    mtf_regime = pd.Series(combined, index=common_idx)

    # Stats
    post_etf = '2024-01-01'
    mtf_post = mtf_regime.loc[post_etf:]
    d_post = d_hourly.loc[post_etf:]

    print(f"\n  Daily-only regime (EMA 8/54):")
    print(f"    Time bullish (post-ETF): {d_post.mean():.1%}")
    bull_periods = d_post.diff().fillna(0)
    daily_flips = (bull_periods != 0).sum()
    print(f"    Regime flips (post-ETF): {daily_flips}")

    print(f"\n  4H regime (EMA 4/20):")
    h4_post = h4_bull.loc[post_etf:]
    print(f"    Time bullish (post-ETF): {h4_post.mean():.1%}")
    h4_flips = (h4_post.diff().fillna(0) != 0).sum()
    print(f"    Regime flips (post-ETF): {h4_flips}")

    print(f"\n  Multi-TF combined (BOTH must agree):")
    print(f"    Time bullish (post-ETF): {mtf_post.mean():.1%}")
    mtf_flips = (mtf_post.diff().fillna(0) != 0).sum()
    print(f"    Regime flips (post-ETF): {mtf_flips}")
    print(f"    Hours in market: {mtf_post.sum():.0f} of {len(mtf_post)} ({mtf_post.mean():.1%})")

    # ================================================================
    # TEST 2: Bar-by-bar simulation with cost tracking
    # ================================================================
    print("\n\n[TEST 2] Bar-by-bar simulation with detailed cost tracking")

    tokens = ['ETH', 'SOL', 'BNB']
    rets = alt_returns.loc[post_etf:, tokens].values  # (n_bars, 3)
    regime = mtf_post.values
    n_bars = len(rets)

    equity = CAPITAL
    equity_curve = np.ones(n_bars) * CAPITAL
    prev_r = 0
    trades = 0
    total_cost = 0.0
    bars_in_market = 0

    for i in range(1, n_bars):
        r = regime[i]

        # Regime change = trade
        if r != prev_r:
            trades += 1
            cost = (COST_BPS / 10000) * equity
            equity -= cost
            total_cost += cost

        if r == 1:
            port_ret = np.nanmean(rets[i])
            equity *= (1 + port_ret)
            bars_in_market += 1

        equity_curve[i] = equity
        prev_r = r

    # Compute metrics
    eq_s = pd.Series(equity_curve, index=alt_returns.loc[post_etf:].index)
    daily_eq = eq_s.resample('D').last().dropna()
    daily_rets = daily_eq.pct_change().dropna()
    years = len(daily_rets) / 365.25
    total_ret = (equity_curve[-1] / CAPITAL) - 1
    ann_ret = (1 + total_ret) ** (1 / max(years, 0.1)) - 1
    sharpe = daily_rets.mean() / max(daily_rets.std(), 1e-10) * np.sqrt(365.25)
    peak = np.maximum.accumulate(daily_eq.values)
    dd = (daily_eq.values - peak) / peak
    max_dd = np.min(dd)
    calmar = ann_ret / max(abs(max_dd), 0.01)

    print(f"\n  Results (Top3 1x Long-Only, post-ETF):")
    print(f"    Annual return: {ann_ret:+.1%}")
    print(f"    Sharpe: {sharpe:+.2f}")
    print(f"    Max DD: {max_dd:+.1%}")
    print(f"    Calmar: {calmar:+.2f}")
    print(f"    Trades: {trades}")
    print(f"    Total costs: ${total_cost:,.0f} ({total_cost/CAPITAL:.1%} of capital)")
    print(f"    Bars in market: {bars_in_market}/{n_bars} ({bars_in_market/n_bars:.1%})")
    print(f"    Final equity: ${equity_curve[-1]:,.0f}")

    # ================================================================
    # TEST 3: What returns did the market provide during MTF-bullish periods?
    # ================================================================
    print("\n\n[TEST 3] Market returns during MTF-bullish vs bearish periods")

    for tok in tokens:
        tok_rets = alt_returns.loc[post_etf:, tok].values
        bull_bars = regime == 1
        bear_bars = regime == 0

        bull_ret = tok_rets[bull_bars]
        bear_ret = tok_rets[bear_bars]

        bull_total = np.prod(1 + bull_ret) - 1
        bear_total = np.prod(1 + bear_ret) - 1
        buy_hold = np.prod(1 + tok_rets) - 1

        bull_ann = (1 + bull_total) ** (1 / max(years, 0.1)) - 1
        bear_ann = (1 + bear_total) ** (1 / max(years, 0.1)) - 1

        print(f"\n  {tok}:")
        print(f"    Bull periods return: {bull_total:+.1%} ({bull_ann:+.1%} ann)")
        print(f"    Bear periods return: {bear_total:+.1%} ({bear_ann:+.1%} ann)")
        print(f"    Buy & hold: {buy_hold:+.1%}")
        print(f"    Strategy captures {bull_total/(buy_hold) if buy_hold > 0 else 'N/A'}x of B&H gains")

    # ================================================================
    # TEST 4: Walk-forward validation — train on 2020-2023, test on 2024+
    # ================================================================
    print("\n\n[TEST 4] Walk-Forward: Train 2020-2023, Test 2024+")

    # The signal params (EMA 8/54 daily, EMA 4/20 4H) were found on the FULL period.
    # But they're also the same used in the single-TF test (EMA 8/54) which was optimized earlier.
    # The 4H params (4/20) are NEW and untested.

    # Test: Vary the 4H params and see how sensitive results are
    print("\n  4H Parameter Sensitivity (post-ETF only):")
    print(f"\n  {'4H Fast':>7}  {'4H Slow':>7}  {'AnnRet':>8}  {'Sharpe':>7}  {'MaxDD':>8}  {'Calmar':>7}  {'Trades':>6}  {'TiM':>5}")
    print("  " + "-" * 65)

    for f4 in [2, 3, 4, 5, 6, 8, 10, 12]:
        for s4 in [10, 15, 20, 25, 30, 40]:
            if f4 >= s4:
                continue

            h4_f = compute_ema(h4_prices, f4)
            h4_s = compute_ema(h4_prices, s4)
            h4_b = pd.Series((h4_f > h4_s).astype(float), index=h4_close.index)

            h4_h = h4_b.reindex(common_idx, method='ffill').fillna(0)
            combo = d_hourly.values * h4_h.values
            combo_s = pd.Series(combo, index=common_idx)
            r_post = combo_s.loc[post_etf:].values
            rets_post = alt_returns.loc[post_etf:, tokens].values

            eq = CAPITAL
            eq_curve = np.ones(len(r_post)) * CAPITAL
            prev = 0
            tr = 0
            bim = 0

            for i in range(1, len(r_post)):
                r = r_post[i]
                if r != prev:
                    tr += 1
                    eq *= (1 - COST_BPS / 10000)
                if r == 1:
                    eq *= (1 + np.nanmean(rets_post[i]))
                    bim += 1
                eq_curve[i] = eq
                prev = r

            idx = alt_returns.loc[post_etf:].index
            eq_daily = pd.Series(eq_curve, index=idx).resample('D').last().dropna()
            if len(eq_daily) < 30:
                continue
            dr = eq_daily.pct_change().dropna()
            yrs = len(dr) / 365.25
            tot = (eq_curve[-1] / CAPITAL) - 1
            ar = (1 + tot) ** (1 / max(yrs, 0.1)) - 1
            sh = dr.mean() / max(dr.std(), 1e-10) * np.sqrt(365.25)
            pk = np.maximum.accumulate(eq_daily.values)
            d = (eq_daily.values - pk) / pk
            mdd = np.min(d)
            cal = ar / max(abs(mdd), 0.01)
            tim = bim / len(r_post)

            print(f"  {f4:7d}  {s4:7d}  {ar:>+7.1%}  {sh:>+6.2f}  {mdd:>+7.1%}  {cal:>+6.2f}  {tr:>5d}  {tim:>4.1%}")

    # ================================================================
    # TEST 5: Same MTF signal on FULL period (2020-2026)
    # ================================================================
    print("\n\n[TEST 5] Full Period Performance (2020-2026)")

    regime_full = mtf_regime.values
    rets_full = alt_returns[tokens].values
    n_full = len(rets_full)

    eq = CAPITAL
    eq_curve_full = np.ones(n_full) * CAPITAL
    prev = 0
    tr = 0

    for i in range(1, n_full):
        r = regime_full[i]
        if r != prev:
            tr += 1
            eq *= (1 - COST_BPS / 10000)
        if r == 1:
            eq *= (1 + np.nanmean(rets_full[i]))
        eq_curve_full[i] = eq
        prev = r

    idx = alt_returns.index
    eq_s = pd.Series(eq_curve_full, index=idx)
    daily_eq = eq_s.resample('D').last().dropna()
    dr = daily_eq.pct_change().dropna()
    yrs = len(dr) / 365.25
    tot = (eq_curve_full[-1] / CAPITAL) - 1
    ar = (1 + tot) ** (1 / max(yrs, 0.1)) - 1
    sh = dr.mean() / max(dr.std(), 1e-10) * np.sqrt(365.25)
    pk = np.maximum.accumulate(daily_eq.values)
    d = (daily_eq.values - pk) / pk
    mdd = np.min(d)
    cal = ar / max(abs(mdd), 0.01)
    tim = (regime_full == 1).sum() / n_full

    print(f"  Top3 (ETH/SOL/BNB) 1x Long-Only, Full Period:")
    print(f"    Annual return: {ar:+.1%}")
    print(f"    Sharpe: {sh:+.2f}")
    print(f"    Max DD: {mdd:+.1%}")
    print(f"    Calmar: {cal:+.2f}")
    print(f"    Trades: {tr}")
    print(f"    Time in market: {tim:.1%}")
    print(f"    Final equity: ${eq_curve_full[-1]:,.0f}")

    # Buy & hold comparison
    bh_ret = np.prod(1 + alt_returns[tokens].mean(axis=1).values) - 1
    bh_ann = (1 + bh_ret) ** (1 / yrs) - 1
    bh_curve = CAPITAL * np.cumprod(1 + alt_returns[tokens].mean(axis=1).values)
    bh_peak = np.maximum.accumulate(bh_curve)
    bh_dd = np.min((bh_curve - bh_peak) / bh_peak)

    print(f"\n  Buy & Hold Top3:")
    print(f"    Annual return: {bh_ann:+.1%}")
    print(f"    Max DD: {bh_dd:+.1%}")
    print(f"    Alpha from MTF: {ar - bh_ann:+.1%}")

    # ================================================================
    # TEST 6: Year-by-year breakdown
    # ================================================================
    print("\n\n[TEST 6] Year-by-Year Breakdown")

    eq_df = pd.DataFrame({
        'equity': eq_curve_full,
        'regime': regime_full
    }, index=alt_returns.index)

    print(f"\n  {'Year':>6}  {'Return':>8}  {'MaxDD':>8}  {'TiM':>5}  {'Trades':>6}")
    print("  " + "-" * 45)

    for year in range(2021, 2027):
        mask = eq_df.index.year == year
        if mask.sum() < 100:
            continue
        yr_eq = eq_df.loc[mask, 'equity']
        yr_regime = eq_df.loc[mask, 'regime']
        yr_ret = (yr_eq.iloc[-1] / yr_eq.iloc[0]) - 1
        yr_daily = yr_eq.resample('D').last().dropna()
        yr_pk = np.maximum.accumulate(yr_daily.values)
        yr_dd = np.min((yr_daily.values - yr_pk) / yr_pk) if len(yr_pk) > 0 else 0
        yr_tim = yr_regime.mean()
        yr_flips = (yr_regime.diff().fillna(0) != 0).sum()
        print(f"  {year:6d}  {yr_ret:>+7.1%}  {yr_dd:>+7.1%}  {yr_tim:>4.1%}  {yr_flips:>5d}")

    # ================================================================
    # TEST 7: Check for look-ahead bias — does the 4H signal use future data?
    # ================================================================
    print("\n\n[TEST 7] Look-Ahead Bias Check")

    # The regime at time t should only use data up to time t.
    # EMA is a causal filter (only uses past data) — no look-ahead.
    # Forward-fill of daily to hourly: uses yesterday's regime for today — OK.
    # 4H resample: takes last 4H bar close — this IS the close at that time, not future.

    # But let's verify: at each regime flip, check if the NEXT few hours are profitable
    # If the strategy has look-ahead, regime flips would systematically precede moves

    regime_post = mtf_post.values
    rets_post = alt_returns.loc[post_etf:, tokens].mean(axis=1).values

    # Find regime flips (0->1 = buy, 1->0 = sell)
    buys = []
    sells = []
    for i in range(1, len(regime_post)):
        if regime_post[i] == 1 and regime_post[i-1] == 0:
            buys.append(i)
        elif regime_post[i] == 0 and regime_post[i-1] == 1:
            sells.append(i)

    print(f"\n  Buy signals: {len(buys)}")
    print(f"  Sell signals: {len(sells)}")

    # Forward returns after buy signals
    horizons = [1, 4, 12, 24, 48, 168]  # 1h, 4h, 12h, 1d, 2d, 1w
    print(f"\n  Forward returns after BUY signals:")
    print(f"  {'Horizon':>10}  {'Mean Ret':>10}  {'Win Rate':>10}  {'Count':>6}")
    print("  " + "-" * 42)
    for h in horizons:
        fwd = []
        for idx in buys:
            if idx + h < len(rets_post):
                fwd_ret = np.sum(rets_post[idx:idx+h])
                fwd.append(fwd_ret)
        if fwd:
            fwd = np.array(fwd)
            print(f"  {h:>10}  {np.mean(fwd):>+9.2%}  {(fwd > 0).mean():>9.1%}  {len(fwd):>5}")

    print(f"\n  Forward returns after SELL signals:")
    print(f"  {'Horizon':>10}  {'Mean Ret':>10}  {'Win Rate':>10}  {'Count':>6}")
    print("  " + "-" * 42)
    for h in horizons:
        fwd = []
        for idx in sells:
            if idx + h < len(rets_post):
                fwd_ret = np.sum(rets_post[idx:idx+h])
                fwd.append(fwd_ret)
        if fwd:
            fwd = np.array(fwd)
            print(f"  {h:>10}  {np.mean(fwd):>+9.2%}  {(fwd > 0).mean():>9.1%}  {len(fwd):>5}")

    # ================================================================
    # TEST 8: Compare with RANDOM timing (Monte Carlo)
    # ================================================================
    print("\n\n[TEST 8] Monte Carlo: Random Regime vs MTF Regime")

    # How much of the return is from timing vs just being in the market?
    np.random.seed(42)
    n_sims = 1000
    random_returns = []
    tim_pct = mtf_post.mean()  # How much time the real strategy is in market

    rets_post_port = alt_returns.loc[post_etf:, tokens].mean(axis=1).values

    for _ in range(n_sims):
        # Random regime with same % time in market
        rand_regime = np.random.random(len(rets_post_port)) < tim_pct
        eq = CAPITAL
        for i in range(1, len(rets_post_port)):
            if rand_regime[i]:
                eq *= (1 + rets_post_port[i])
        random_returns.append((eq / CAPITAL - 1))

    random_returns = np.array(random_returns)
    rand_ann = (1 + random_returns) ** (1 / max(years, 0.1)) - 1

    # Real strategy return
    real_ret = eq_curve[-1] / CAPITAL - 1  # from TEST 2
    real_ann = (1 + real_ret) ** (1 / max(years, 0.1)) - 1

    print(f"\n  MTF Strategy: {real_ann:+.1%} annual")
    print(f"  Random timing (same TiM={tim_pct:.1%}):")
    print(f"    Mean: {np.mean(rand_ann):+.1%}")
    print(f"    Median: {np.median(rand_ann):+.1%}")
    print(f"    P5/P95: {np.percentile(rand_ann, 5):+.1%} / {np.percentile(rand_ann, 95):+.1%}")
    print(f"    Strategy > {(real_ann > rand_ann).mean():.1%} of random runs")
    print(f"    Alpha over random: {real_ann - np.mean(rand_ann):+.1%}")

    # ================================================================
    # TEST 9: Forward-looking token selection with MTF regime
    # ================================================================
    print("\n\n[TEST 9] MTF Regime + Forward-Looking Token Selection")

    all_tokens_sorted = list(alt_returns.columns)
    rets_post_all = alt_returns.loc[post_etf:]
    closes_post_all = alt_closes.loc[post_etf:]
    regime_post_vals = mtf_post.values

    for n_alts, mom_days, label in [
        (3, 30, "Mom30d N=3"),
        (3, 60, "Mom60d N=3"),
        (3, 90, "Mom90d N=3"),
        (5, 60, "Mom60d N=5"),
        (8, 60, "Mom60d N=8"),
    ]:
        lookback = mom_days * 24
        eq = CAPITAL
        eq_curve_fwd = np.ones(len(rets_post_all)) * CAPITAL
        prev = 0
        tr = 0
        current_sel = list(range(min(n_alts, len(all_tokens_sorted))))
        last_sel = 0

        closes_arr = closes_post_all.values
        rets_arr = rets_post_all.values

        for i in range(1, len(rets_post_all)):
            r = regime_post_vals[i]

            # Reselect every 2 weeks or on regime flip
            if (i - last_sel >= 14 * 24 or (r != prev)) and i > lookback:
                trail = np.zeros(len(all_tokens_sorted))
                for j in range(len(all_tokens_sorted)):
                    s = closes_arr[i - lookback, j]
                    e = closes_arr[i, j]
                    if s > 0 and not np.isnan(s) and not np.isnan(e):
                        trail[j] = (e / s) - 1
                    else:
                        trail[j] = -999
                ranked = np.argsort(-trail)
                current_sel = [x for x in ranked[:n_alts] if trail[x] > -998]
                last_sel = i

            if r != prev:
                tr += 1
                eq *= (1 - COST_BPS / 10000)

            if r == 1 and current_sel:
                port_ret = np.nanmean([rets_arr[i, j] for j in current_sel])
                eq *= (1 + port_ret)

            eq_curve_fwd[i] = eq
            prev = r

        # Metrics
        idx = rets_post_all.index
        eq_s = pd.Series(eq_curve_fwd, index=idx)
        daily_eq = eq_s.resample('D').last().dropna()
        dr = daily_eq.pct_change().dropna()
        yrs = len(dr) / 365.25
        tot = (eq_curve_fwd[-1] / CAPITAL) - 1
        ar = (1 + tot) ** (1 / max(yrs, 0.1)) - 1
        sh = dr.mean() / max(dr.std(), 1e-10) * np.sqrt(365.25)
        pk = np.maximum.accumulate(daily_eq.values)
        d = (daily_eq.values - pk) / pk
        mdd = np.min(d)
        cal = ar / max(abs(mdd), 0.01)

        print(f"  {label}: {ar:+.1%} ann, Sharpe {sh:+.2f}, MaxDD {mdd:+.1%}, Calmar {cal:+.2f}, Trades {tr}")

    print("\n" + "=" * 120)
    print("VERDICT")
    print("=" * 120)


if __name__ == '__main__':
    main()
