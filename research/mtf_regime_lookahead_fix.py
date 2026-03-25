#!/usr/bin/env python3
"""Fix look-ahead bias in MTF regime signal.

ISSUE: resample('D').last() gives today's close at midnight.
When forward-filled to hourly, hours 0-22 use FUTURE close data.
Same issue on 4H bars: hours 0-3 within a 4H bar use end-of-bar close.

FIX: Shift signals by 1 period so we use PREVIOUS bar's close.
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


def mtf_regime_BUGGED(btc_hourly_close, fast_d=8, slow_d=54, fast_4h=6, slow_4h=24):
    """ORIGINAL (with look-ahead): for comparison."""
    daily = btc_hourly_close.resample('D').last().dropna()
    dp = np.array(daily.values, dtype=np.float64)
    df_ = compute_ema(dp, fast_d)
    ds = compute_ema(dp, slow_d)
    d_bull = pd.Series((df_ > ds).astype(float), index=daily.index)

    h4 = btc_hourly_close.resample('4h').last().dropna()
    hp = np.array(h4.values, dtype=np.float64)
    hf = compute_ema(hp, fast_4h)
    hs = compute_ema(hp, slow_4h)
    h_bull = pd.Series((hf > hs).astype(float), index=h4.index)

    hourly_idx = btc_hourly_close.index
    d_h = d_bull.reindex(hourly_idx, method='ffill').fillna(0)
    h_h = h_bull.reindex(hourly_idx, method='ffill').fillna(0)
    return pd.Series(d_h.values * h_h.values, index=hourly_idx)


def mtf_regime_FIXED(btc_hourly_close, fast_d=8, slow_d=54, fast_4h=6, slow_4h=24):
    """FIXED: shift signals by 1 period to eliminate look-ahead."""
    # Daily: shift(1) means today's signal uses YESTERDAY's EMA cross
    daily = btc_hourly_close.resample('D').last().dropna()
    dp = np.array(daily.values, dtype=np.float64)
    df_ = compute_ema(dp, fast_d)
    ds = compute_ema(dp, slow_d)
    d_bull = pd.Series((df_ > ds).astype(float), index=daily.index)
    d_bull = d_bull.shift(1).fillna(0)  # ← FIX: use yesterday's signal

    # 4H: shift(1) means current 4H bar uses PREVIOUS 4H bar's cross
    h4 = btc_hourly_close.resample('4h').last().dropna()
    hp = np.array(h4.values, dtype=np.float64)
    hf = compute_ema(hp, fast_4h)
    hs = compute_ema(hp, slow_4h)
    h_bull = pd.Series((hf > hs).astype(float), index=h4.index)
    h_bull = h_bull.shift(1).fillna(0)  # ← FIX: use previous 4H bar's signal

    hourly_idx = btc_hourly_close.index
    d_h = d_bull.reindex(hourly_idx, method='ffill').fillna(0)
    h_h = h_bull.reindex(hourly_idx, method='ffill').fillna(0)
    return pd.Series(d_h.values * h_h.values, index=hourly_idx)


def daily_only_regime_FIXED(btc_hourly_close, fast_d=8, slow_d=54):
    """Single-TF daily regime, properly shifted."""
    daily = btc_hourly_close.resample('D').last().dropna()
    dp = np.array(daily.values, dtype=np.float64)
    df_ = compute_ema(dp, fast_d)
    ds = compute_ema(dp, slow_d)
    d_bull = pd.Series((df_ > ds).astype(float), index=daily.index)
    d_bull = d_bull.shift(1).fillna(0)

    hourly_idx = btc_hourly_close.index
    d_h = d_bull.reindex(hourly_idx, method='ffill').fillna(0)
    return d_h


def compute_metrics(equity_curve, index, capital=CAPITAL):
    eq = pd.Series(equity_curve, index=index)
    daily_eq = eq.resample('D').last().dropna()
    if len(daily_eq) < 30:
        return None
    dr = daily_eq.pct_change().dropna()
    yrs = len(dr) / 365.25
    total_ret = (equity_curve[-1] / capital) - 1
    ann_ret = (1 + total_ret) ** (1 / max(yrs, 0.1)) - 1
    sharpe = dr.mean() / max(dr.std(), 1e-10) * np.sqrt(365.25)
    peak = np.maximum.accumulate(daily_eq.values)
    dd = (daily_eq.values - peak) / peak
    max_dd = np.min(dd)
    calmar = ann_ret / max(abs(max_dd), 0.01)
    neg = dr[dr < 0]
    sortino = dr.mean() / max(neg.std() if len(neg) > 0 else 1e-10, 1e-10) * np.sqrt(365.25)
    return {
        'ann_ret': ann_ret, 'sharpe': sharpe, 'sortino': sortino,
        'max_dd': max_dd, 'calmar': calmar, 'years': yrs,
    }


def backtest(regime_hourly, alt_returns, tokens, leverage=1.0, short_ratio=0.0,
             n_alts=3, momentum_days=90, all_tokens=None, alt_closes=None,
             forward_select=False):
    """General backtest with optional forward-looking selection."""
    regime = regime_hourly.values
    n_bars = len(alt_returns)
    funding_per_hour = FUNDING_RATE_ANNUAL / (365.25 * 24)

    if forward_select and alt_closes is not None:
        # Forward-looking token selection
        closes_arr = alt_closes.values
        rets_arr = alt_returns.values
        token_list = list(alt_returns.columns)
        lookback = momentum_days * 24
        current_sel = list(range(min(n_alts, len(token_list))))
        last_sel = 0
    else:
        # Fixed token set
        rets_arr = alt_returns[tokens].values
        current_sel = None

    equity = CAPITAL
    eq_curve = np.ones(n_bars) * CAPITAL
    prev_r = 0
    trades = 0

    for i in range(1, n_bars):
        r = regime[i]

        if forward_select and alt_closes is not None:
            # Reselect on regime flip or every 14 days
            if (r != prev_r or i - last_sel >= 14 * 24) and i > lookback:
                trail = np.zeros(len(token_list))
                for j in range(len(token_list)):
                    s = closes_arr[i - lookback, j]
                    e = closes_arr[i, j]
                    if s > 0 and not np.isnan(s) and not np.isnan(e):
                        trail[j] = (e / s) - 1
                    else:
                        trail[j] = -999
                valid = trail > -998
                if valid.sum() >= n_alts:
                    new_sel = [x for x in np.argsort(-trail)[:n_alts] if valid[x]]
                    if new_sel != current_sel:
                        n_changed = len(set(new_sel) - set(current_sel))
                        equity *= (1 - COST_BPS / 10000 * n_changed / max(n_alts, 1) * leverage)
                        trades += n_changed
                    current_sel = new_sel
                last_sel = i

        if r != prev_r:
            trades += 1
            equity *= (1 - COST_BPS / 10000 * leverage)

        if forward_select and current_sel:
            port_ret = np.nanmean([rets_arr[i, j] for j in current_sel])
        elif not forward_select:
            port_ret = np.nanmean(rets_arr[i])
        else:
            port_ret = 0

        if r == 1:
            equity *= (1 + port_ret * leverage)
        elif r == 0 and short_ratio > 0:
            equity *= (1 - port_ret * short_ratio * leverage)
            equity *= (1 + funding_per_hour * short_ratio * leverage)

        eq_curve[i] = equity
        prev_r = r

    m = compute_metrics(eq_curve, alt_returns.index)
    if m:
        m['trades'] = trades
        tim_bull = (regime == 1).sum() / n_bars
        m['time_in_market'] = tim_bull if short_ratio == 0 else 1.0
    return m, eq_curve


def main():
    print("=" * 120)
    print("LOOK-AHEAD BIAS FIX: BEFORE vs AFTER")
    print("=" * 120)

    btc = load_token('BTC')
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

    post_etf = '2024-01-01'
    static_top3 = ['ETH', 'SOL', 'BNB']

    # Generate regimes
    regime_bugged = mtf_regime_BUGGED(btc['close'], fast_d=8, slow_d=54, fast_4h=6, slow_4h=24)
    regime_fixed = mtf_regime_FIXED(btc['close'], fast_d=8, slow_d=54, fast_4h=6, slow_4h=24)
    regime_daily = daily_only_regime_FIXED(btc['close'], fast_d=8, slow_d=54)

    # Align to common index
    r_bug = regime_bugged.reindex(common_idx, method='ffill').fillna(0)
    r_fix = regime_fixed.reindex(common_idx, method='ffill').fillna(0)
    r_day = regime_daily.reindex(common_idx, method='ffill').fillna(0)

    # ================================================================
    print("\n  Regime statistics (post-ETF):")
    for name, r in [("MTF Bugged", r_bug), ("MTF Fixed", r_fix), ("Daily-Only Fixed", r_day)]:
        r_post = r.loc[post_etf:]
        print(f"    {name:20s}: TiM={r_post.mean():.1%}, flips={(r_post.diff().fillna(0) != 0).sum()}")

    # ================================================================
    print("\n" + "=" * 120)
    print("COMPARISON: BUGGED vs FIXED vs DAILY-ONLY (Post-ETF)")
    print("=" * 120)

    print(f"\n  {'Config':<50}  {'AnnRet':>8}  {'Sharpe':>7}  {'MaxDD':>8}  {'Calmar':>7}  {'Sortino':>8}  {'Trades':>6}  {'TiM':>5}")
    print("  " + "-" * 110)

    configs = [
        # Static top 3
        (r_bug.loc[post_etf:], "MTF BUGGED Static3 1x LO", static_top3, 1.0, 0.0, False),
        (r_fix.loc[post_etf:], "MTF FIXED  Static3 1x LO", static_top3, 1.0, 0.0, False),
        (r_day.loc[post_etf:], "DAILY ONLY Static3 1x LO", static_top3, 1.0, 0.0, False),

        (r_bug.loc[post_etf:], "MTF BUGGED Static3 1x LS70", static_top3, 1.0, 0.7, False),
        (r_fix.loc[post_etf:], "MTF FIXED  Static3 1x LS70", static_top3, 1.0, 0.7, False),
        (r_day.loc[post_etf:], "DAILY ONLY Static3 1x LS70", static_top3, 1.0, 0.7, False),

        (r_bug.loc[post_etf:], "MTF BUGGED Static3 2x LS70", static_top3, 2.0, 0.7, False),
        (r_fix.loc[post_etf:], "MTF FIXED  Static3 2x LS70", static_top3, 2.0, 0.7, False),
        (r_day.loc[post_etf:], "DAILY ONLY Static3 2x LS70", static_top3, 2.0, 0.7, False),

        # Forward-looking momentum selection
        (r_bug.loc[post_etf:], "MTF BUGGED Mom90 N=3 1x LO", None, 1.0, 0.0, True),
        (r_fix.loc[post_etf:], "MTF FIXED  Mom90 N=3 1x LO", None, 1.0, 0.0, True),
        (r_day.loc[post_etf:], "DAILY ONLY Mom90 N=3 1x LO", None, 1.0, 0.0, True),

        (r_bug.loc[post_etf:], "MTF BUGGED Mom90 N=3 1x LS70", None, 1.0, 0.7, True),
        (r_fix.loc[post_etf:], "MTF FIXED  Mom90 N=3 1x LS70", None, 1.0, 0.7, True),
        (r_day.loc[post_etf:], "DAILY ONLY Mom90 N=3 1x LS70", None, 1.0, 0.7, True),

        (r_bug.loc[post_etf:], "MTF BUGGED Mom90 N=3 1.5x LS70", None, 1.5, 0.7, True),
        (r_fix.loc[post_etf:], "MTF FIXED  Mom90 N=3 1.5x LS70", None, 1.5, 0.7, True),
        (r_day.loc[post_etf:], "DAILY ONLY Mom90 N=3 1.5x LS70", None, 1.5, 0.7, True),

        (r_bug.loc[post_etf:], "MTF BUGGED Mom90 N=3 2x LS70", None, 2.0, 0.7, True),
        (r_fix.loc[post_etf:], "MTF FIXED  Mom90 N=3 2x LS70", None, 2.0, 0.7, True),
        (r_day.loc[post_etf:], "DAILY ONLY Mom90 N=3 2x LS70", None, 2.0, 0.7, True),
    ]

    for regime, label, tokens, lev, sr, fwd in configs:
        post_returns = alt_returns.loc[post_etf:]
        post_closes = alt_closes.loc[post_etf:]

        m, _ = backtest(
            regime, post_returns,
            tokens=tokens if tokens else ALL_TOKENS,
            leverage=lev, short_ratio=sr,
            forward_select=fwd, n_alts=3, momentum_days=90,
            alt_closes=post_closes if fwd else None,
            all_tokens=ALL_TOKENS if fwd else None)

        if m:
            print(f"  {label:<50}  {m['ann_ret']:>+7.1%}  {m['sharpe']:>+6.2f}  {m['max_dd']:>+7.1%}  {m['calmar']:>+6.2f}  {m['sortino']:>+7.2f}  {m.get('trades',0):>5d}  {m.get('time_in_market',0):>4.1%}")

    # ================================================================
    print("\n\n" + "=" * 120)
    print("4H PARAMETER SWEEP WITH FIX (Post-ETF, Static3 1x LO)")
    print("=" * 120)

    print(f"\n  {'4H Fast':>7}  {'4H Slow':>7}  {'AnnRet':>8}  {'Sharpe':>7}  {'MaxDD':>8}  {'Calmar':>7}  {'Trades':>6}  {'TiM':>5}")
    print("  " + "-" * 65)

    h4_close = btc['close'].resample('4h').last().dropna()
    daily = btc['close'].resample('D').last().dropna()
    dp = np.array(daily.values, dtype=np.float64)
    df_ = compute_ema(dp, 8)
    ds = compute_ema(dp, 54)
    d_bull = pd.Series((df_ > ds).astype(float), index=daily.index)
    d_bull_shifted = d_bull.shift(1).fillna(0)

    for f4 in [3, 4, 6, 8, 10, 12]:
        for s4 in [15, 20, 24, 30, 40]:
            if f4 >= s4:
                continue

            hp = np.array(h4_close.values, dtype=np.float64)
            hf = compute_ema(hp, f4)
            hs_ = compute_ema(hp, s4)
            h_bull = pd.Series((hf > hs_).astype(float), index=h4_close.index)
            h_bull_shifted = h_bull.shift(1).fillna(0)

            d_h = d_bull_shifted.reindex(common_idx, method='ffill').fillna(0)
            h_h = h_bull_shifted.reindex(common_idx, method='ffill').fillna(0)
            combined = pd.Series(d_h.values * h_h.values, index=common_idx)

            m, _ = backtest(combined.loc[post_etf:], alt_returns.loc[post_etf:],
                           tokens=static_top3, leverage=1.0, short_ratio=0.0)
            if m:
                print(f"  {f4:>7}  {s4:>7}  {m['ann_ret']:>+7.1%}  {m['sharpe']:>+6.2f}  {m['max_dd']:>+7.1%}  {m['calmar']:>+6.2f}  {m.get('trades',0):>5d}  {m.get('time_in_market',0):>4.1%}")

    # ================================================================
    print("\n\n" + "=" * 120)
    print("WALK-FORWARD WITH FIX")
    print("=" * 120)

    train_end = '2023-12-31'
    train_returns = alt_returns.loc[:train_end]
    train_closes = alt_closes.loc[:train_end]
    test_returns = alt_returns.loc[post_etf:]
    test_closes = alt_closes.loc[post_etf:]

    print(f"\n  {'Config':<40}  {'Train Ann':>10}  {'Test Ann':>10}  {'Train DD':>9}  {'Test DD':>9}  {'WFE':>6}")
    print("  " + "-" * 90)

    for lev, sr, label in [
        (1.0, 0.0, "Mom90 N=3 1x LO"),
        (1.0, 0.7, "Mom90 N=3 1x LS70"),
        (1.5, 0.7, "Mom90 N=3 1.5x LS70"),
        (2.0, 0.7, "Mom90 N=3 2x LS70"),
    ]:
        m_train, _ = backtest(
            r_fix.loc[:train_end], train_returns, tokens=ALL_TOKENS,
            leverage=lev, short_ratio=sr,
            forward_select=True, n_alts=3, momentum_days=90,
            alt_closes=train_closes)

        m_test, _ = backtest(
            r_fix.loc[post_etf:], test_returns, tokens=ALL_TOKENS,
            leverage=lev, short_ratio=sr,
            forward_select=True, n_alts=3, momentum_days=90,
            alt_closes=test_closes)

        if m_train and m_test:
            wfe = m_test['ann_ret'] / max(m_train['ann_ret'], 0.01) * 100
            print(f"  {label:<40}  {m_train['ann_ret']:>+9.1%}  {m_test['ann_ret']:>+9.1%}  {m_train['max_dd']:>+8.1%}  {m_test['max_dd']:>+8.1%}  {wfe:>5.0f}%")

    # ================================================================
    print("\n\n" + "=" * 120)
    print("YEAR-BY-YEAR (FIXED, Mom90 N=3, 1x LS70)")
    print("=" * 120)

    m_full, eq_full = backtest(
        r_fix, alt_returns, tokens=ALL_TOKENS,
        leverage=1.0, short_ratio=0.7,
        forward_select=True, n_alts=3, momentum_days=90,
        alt_closes=alt_closes)

    eq_df = pd.DataFrame({'equity': eq_full}, index=common_idx)

    print(f"\n  {'Year':>6}  {'Return':>8}  {'MaxDD':>8}")
    print("  " + "-" * 28)

    for year in range(2021, 2027):
        mask = eq_df.index.year == year
        if mask.sum() < 100:
            continue
        yr_eq = eq_df.loc[mask, 'equity']
        yr_ret = (yr_eq.iloc[-1] / yr_eq.iloc[0]) - 1
        yr_daily = yr_eq.resample('D').last().dropna()
        yr_pk = np.maximum.accumulate(yr_daily.values)
        yr_dd = np.min((yr_daily.values - yr_pk) / yr_pk) if len(yr_pk) > 0 else 0
        print(f"  {year:6d}  {yr_ret:>+7.1%}  {yr_dd:>+7.1%}")

    # Final verdict
    print("\n\n" + "=" * 120)
    print("EXECUTIVE SUMMARY")
    print("=" * 120)
    print("""
    After fixing look-ahead bias:
    - Compare BUGGED vs FIXED returns above
    - If the drop is <30%, the MTF signal has genuine alpha
    - If the drop is >70%, the signal was mostly look-ahead
    - The DAILY ONLY baseline shows what a properly shifted single-TF signal gives
    """)


if __name__ == '__main__':
    main()
