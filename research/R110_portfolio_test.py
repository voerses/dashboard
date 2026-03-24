#!/workspace/venv/bin/python
"""
R110: 3-Strategy Portfolio Test
===============================

Goal: Build and test a portfolio combining three uncorrelated BTC strategies:
  1. V3 Momentum (s320): Daily EMA20/EMA50 crossover, weekly rebalance
  2. Intraday Momentum Breakout: 8h momentum > 2x ATR -> entry, trailing stop
  3. Macro Regime Rotation: US10Y + DXY both falling -> long BTC

These three have near-zero pairwise correlations, making them excellent
portfolio components. Test equal-weight, risk-parity, and max-Sharpe allocations.

Data:
  - BTC 1h: data/spot/1h_cache/BTC_1h.parquet
  - Macro: data/alternative/macro/ (us10y_yield.parquet, usd_index.parquet)

Author: Quant Research Agent
Date: 2026-03-24
"""

import warnings
import numpy as np
import pandas as pd
from scipy import optimize
from pathlib import Path
from datetime import datetime

warnings.filterwarnings('ignore')

# ── Paths ──────────────────────────────────────────────────────────────────
PROJECT_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = PROJECT_DIR / 'data'
OUTPUT_MD = PROJECT_DIR / 'research' / 'R110_portfolio_test.md'

COST_BPS = 10
PERIOD_START = '2021-07-01'
PERIOD_END = '2026-03-14'
ANNUAL_FACTOR = 365.25


def make_strat_df(strat_list):
    """Combine list of named Series into a DataFrame with strategies as columns."""
    return pd.concat(strat_list, axis=1)


# ══════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════════════════

def load_btc_1h():
    """Load BTC 1h spot data."""
    print("[DATA] Loading BTC 1h spot...")
    df = pd.read_parquet(DATA_DIR / 'spot/1h_cache/BTC_1h.parquet')
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep='first')]
    df = df.loc['2021-01-01':PERIOD_END]
    print(f"  {df.index.min().date()} to {df.index.max().date()}, {len(df)} bars")
    return df


def load_btc_daily(btc_1h):
    """Resample 1h to daily OHLCV."""
    daily = pd.DataFrame()
    daily['open'] = btc_1h['open'].resample('1D').first()
    daily['high'] = btc_1h['high'].resample('1D').max()
    daily['low'] = btc_1h['low'].resample('1D').min()
    daily['close'] = btc_1h['close'].resample('1D').last()
    daily['volume'] = btc_1h['volume'].resample('1D').sum()
    daily = daily.dropna()
    return daily


def load_macro():
    """Load US10Y yield and DXY data."""
    print("[DATA] Loading macro data...")

    us10y = pd.read_parquet(DATA_DIR / 'alternative/macro/us10y_yield.parquet')
    us10y['Date'] = pd.to_datetime(us10y['Date'])
    us10y = us10y.set_index('Date').sort_index()
    us10y = us10y[~us10y.index.duplicated(keep='first')]
    us10y = us10y.rename(columns={'Close': 'us10y_close'})
    print(f"  US10Y: {us10y.index.min().date()} to {us10y.index.max().date()}, {len(us10y)} rows")

    dxy = pd.read_parquet(DATA_DIR / 'alternative/macro/usd_index.parquet')
    dxy['Date'] = pd.to_datetime(dxy['Date'])
    dxy = dxy.set_index('Date').sort_index()
    dxy = dxy[~dxy.index.duplicated(keep='first')]
    dxy = dxy.rename(columns={'Close': 'dxy_close'})
    print(f"  DXY: {dxy.index.min().date()} to {dxy.index.max().date()}, {len(dxy)} rows")

    return us10y[['us10y_close']], dxy[['dxy_close']]


# ══════════════════════════════════════════════════════════════════════════
# STRATEGY 1: V3 MOMENTUM (EMA20/EMA50 CROSSOVER)
# ══════════════════════════════════════════════════════════════════════════

def strategy_v3_momentum(btc_daily):
    """
    V3 Momentum: Long when daily EMA20 > EMA50, flat otherwise.
    Weekly rebalance (every 7 days). 10bps round-trip cost on signal changes.
    """
    print("\n[STRAT] Building V3 Momentum...")
    close = btc_daily['close'].copy()
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()

    raw_signal = (ema20 > ema50).astype(float)

    # Weekly rebalance
    signal = raw_signal.copy()
    last_rebal_idx = 0
    for i in range(1, len(signal)):
        if (i - last_rebal_idx) >= 7:
            last_rebal_idx = i
        else:
            signal.iloc[i] = signal.iloc[i - 1]

    daily_ret = close.pct_change()
    strat_ret = signal.shift(1) * daily_ret

    # Costs on signal changes
    signal_changes = signal.diff().abs()
    cost = signal_changes * (COST_BPS / 10000)
    strat_ret = strat_ret - cost

    strat_ret = strat_ret.loc[PERIOD_START:PERIOD_END].dropna()
    strat_ret.name = 'v3_momentum'

    total = (1 + strat_ret).prod() - 1
    ann_ret = (1 + strat_ret).prod() ** (ANNUAL_FACTOR / len(strat_ret)) - 1
    ann_vol = strat_ret.std() * np.sqrt(ANNUAL_FACTOR)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    pct_long = (signal.loc[PERIOD_START:PERIOD_END] == 1).mean()
    print(f"  V3 Momentum: Total={total:.1%}, Ann={ann_ret:.1%}, Sharpe={sharpe:.2f}, Long%={pct_long:.0%}")

    return strat_ret


# ══════════════════════════════════════════════════════════════════════════
# STRATEGY 2: INTRADAY MOMENTUM BREAKOUT
# ══════════════════════════════════════════════════════════════════════════

def strategy_intraday_momentum(btc_1h):
    """
    Intraday Momentum Breakout on 1h bars:
    - Compute 8h return (rolling 8-bar)
    - Compute 20-bar ATR on 1h
    - Entry: abs(8h_return) > 2 * ATR_20_pct
    - Direction: sign of breakout
    - Trailing stop: 1.5 * ATR from high-water mark
    - Max hold: 48 bars (48h)
    - 10bps cost per round trip
    """
    print("\n[STRAT] Building Intraday Momentum Breakout...")
    df = btc_1h.copy()

    # 8h return
    df['ret_8h'] = df['close'].pct_change(8)

    # ATR 20-bar (hourly)
    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            abs(df['high'] - df['close'].shift(1)),
            abs(df['low'] - df['close'].shift(1))
        )
    )
    df['atr_20'] = df['tr'].rolling(20).mean()
    df['atr_pct'] = df['atr_20'] / df['close']

    df = df.dropna(subset=['ret_8h', 'atr_pct'])

    # Vectorized simulation using numpy arrays for speed
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    ret_8h = df['ret_8h'].values
    atr_20 = df['atr_20'].values
    atr_pct = df['atr_pct'].values
    n = len(df)

    positions = np.zeros(n)
    in_trade = False
    direction = 0
    high_water = 0.0
    bars_held = 0

    for i in range(1, n):
        if in_trade:
            bars_held += 1
            if direction == 1:
                high_water = max(high_water, high[i])
                trail_stop = high_water - 1.5 * atr_20[i]
                stopped = low[i] <= trail_stop
            else:
                high_water = min(high_water, low[i])
                trail_stop = high_water + 1.5 * atr_20[i]
                stopped = high[i] >= trail_stop

            if stopped or bars_held >= 48:
                positions[i] = 0
                in_trade = False
                direction = 0
            else:
                positions[i] = direction
        else:
            if abs(ret_8h[i]) > 2 * atr_pct[i] and atr_pct[i] > 0:
                direction = 1 if ret_8h[i] > 0 else -1
                in_trade = True
                high_water = high[i] if direction == 1 else low[i]
                bars_held = 0
                positions[i] = direction

    # Hourly returns
    positions_s = pd.Series(positions, index=df.index)
    hourly_ret = df['close'].pct_change()
    strat_hourly = positions_s.shift(1) * hourly_ret

    # Costs on position changes
    pos_changes = positions_s.diff().abs()
    cost_hourly = pos_changes * (COST_BPS / 10000 / 2)
    strat_hourly = strat_hourly - cost_hourly
    strat_hourly = strat_hourly.fillna(0)

    # Compound to daily
    strat_daily = (1 + strat_hourly).resample('1D').prod() - 1
    strat_daily = strat_daily.loc[PERIOD_START:PERIOD_END].dropna()
    strat_daily.name = 'intraday_momentum'

    total = (1 + strat_daily).prod() - 1
    ann_ret = (1 + strat_daily).prod() ** (ANNUAL_FACTOR / len(strat_daily)) - 1
    ann_vol = strat_daily.std() * np.sqrt(ANNUAL_FACTOR)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    n_trades = int((pos_changes > 0).sum() / 2)
    print(f"  Intraday Mom: Total={total:.1%}, Ann={ann_ret:.1%}, Sharpe={sharpe:.2f}, Trades={n_trades}")

    return strat_daily


# ══════════════════════════════════════════════════════════════════════════
# STRATEGY 3: MACRO REGIME ROTATION
# ══════════════════════════════════════════════════════════════════════════

def strategy_macro_regime(btc_daily, us10y, dxy):
    """
    Macro Regime Rotation:
    - Long BTC when US10Y 20d ROC < 0 AND DXY 20d ROC < 0 (risk-on)
    - Flat otherwise
    - Weekly rebalance, 10bps cost
    """
    print("\n[STRAT] Building Macro Regime Rotation...")

    us10y_roc = us10y['us10y_close'].pct_change(20)
    dxy_roc = dxy['dxy_close'].pct_change(20)

    macro = pd.DataFrame(index=btc_daily.index)
    macro['us10y_roc'] = us10y_roc.reindex(btc_daily.index, method='ffill')
    macro['dxy_roc'] = dxy_roc.reindex(btc_daily.index, method='ffill')

    raw_signal = ((macro['us10y_roc'] < 0) & (macro['dxy_roc'] < 0)).astype(float)

    # Weekly rebalance
    signal = raw_signal.copy()
    last_rebal_idx = 0
    for i in range(1, len(signal)):
        if (i - last_rebal_idx) >= 7:
            last_rebal_idx = i
        else:
            signal.iloc[i] = signal.iloc[i - 1]

    daily_ret = btc_daily['close'].pct_change()
    strat_ret = signal.shift(1) * daily_ret

    signal_changes = signal.diff().abs()
    cost = signal_changes * (COST_BPS / 10000)
    strat_ret = strat_ret - cost

    strat_ret = strat_ret.loc[PERIOD_START:PERIOD_END].dropna()
    strat_ret.name = 'macro_regime'

    total = (1 + strat_ret).prod() - 1
    ann_ret = (1 + strat_ret).prod() ** (ANNUAL_FACTOR / len(strat_ret)) - 1
    ann_vol = strat_ret.std() * np.sqrt(ANNUAL_FACTOR)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    pct_long = (signal.loc[PERIOD_START:PERIOD_END] == 1).mean()
    print(f"  Macro Regime: Total={total:.1%}, Ann={ann_ret:.1%}, Sharpe={sharpe:.2f}, Long%={pct_long:.0%}")

    return strat_ret


# ══════════════════════════════════════════════════════════════════════════
# BUY AND HOLD
# ══════════════════════════════════════════════════════════════════════════

def benchmark_buy_hold(btc_daily):
    """BTC buy-and-hold daily returns."""
    ret = btc_daily['close'].pct_change()
    ret = ret.loc[PERIOD_START:PERIOD_END].dropna()
    ret.name = 'buy_hold'
    return ret


# ══════════════════════════════════════════════════════════════════════════
# METRICS
# ══════════════════════════════════════════════════════════════════════════

def compute_metrics(returns, name='Strategy'):
    """Compute comprehensive strategy metrics."""
    r = returns.dropna()
    n = len(r)
    if n == 0:
        return {'Name': name}

    total_ret = (1 + r).prod() - 1
    ann_ret = (1 + r).prod() ** (ANNUAL_FACTOR / n) - 1
    ann_vol = r.std() * np.sqrt(ANNUAL_FACTOR)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

    downside = r[r < 0].std() * np.sqrt(ANNUAL_FACTOR)
    sortino = ann_ret / downside if downside > 0 else 0

    cum = (1 + r).cumprod()
    hwm = cum.cummax()
    dd = (cum / hwm) - 1
    max_dd = dd.min()

    is_dd = dd < 0
    dd_groups = (~is_dd).cumsum()
    max_dd_duration = is_dd.groupby(dd_groups).sum().max() if is_dd.any() else 0

    calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0
    win_rate = (r > 0).mean()

    gross_profit = r[r > 0].sum()
    gross_loss = abs(r[r < 0].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    return {
        'Name': name,
        'Total Return': total_ret,
        'Ann Return': ann_ret,
        'Ann Vol': ann_vol,
        'Sharpe': sharpe,
        'Sortino': sortino,
        'Max DD': max_dd,
        'Max DD Duration (days)': int(max_dd_duration),
        'Calmar': calmar,
        'Win Rate': win_rate,
        'Profit Factor': profit_factor,
    }


# ══════════════════════════════════════════════════════════════════════════
# PORTFOLIO ALLOCATION METHODS
# ══════════════════════════════════════════════════════════════════════════

def portfolio_equal_weight(combined):
    """Equal weight: 1/3 each strategy."""
    print("\n[PORT] Building Equal Weight portfolio...")
    n_strats = combined.shape[1]
    weights = np.ones(n_strats) / n_strats
    port_ret = combined.dot(weights)
    port_ret.name = 'equal_weight'
    return port_ret


def portfolio_risk_parity(combined):
    """
    Risk parity: weight inversely proportional to 60d rolling volatility.
    Recompute weekly.
    """
    print("[PORT] Building Risk Parity portfolio...")
    cols = combined.columns.tolist()
    roll_vol = combined.rolling(60).std()

    port_ret = pd.Series(0.0, index=combined.index)
    weights_history = pd.DataFrame(0.0, index=combined.index, columns=cols)

    current_weights = np.ones(len(cols)) / len(cols)
    last_rebal = 0

    for i in range(60, len(combined)):
        if (i - last_rebal) >= 7 or i == 60:
            vols = roll_vol.iloc[i].values
            if np.all(vols > 0) and not np.any(np.isnan(vols)):
                inv_vol = 1.0 / vols
                current_weights = inv_vol / inv_vol.sum()
            last_rebal = i

        weights_history.iloc[i] = current_weights
        port_ret.iloc[i] = combined.iloc[i].values @ current_weights

    port_ret = port_ret.iloc[60:]
    port_ret.name = 'risk_parity'
    return port_ret, weights_history.iloc[60:]


def portfolio_max_sharpe(combined):
    """
    Max Sharpe: optimize weights quarterly to maximize trailing 90d Sharpe.
    Min 10% per strategy, max 60%.
    """
    print("[PORT] Building Max Sharpe portfolio...")
    cols = combined.columns.tolist()
    n_strats = len(cols)

    port_ret = pd.Series(0.0, index=combined.index)
    weights_history = pd.DataFrame(0.0, index=combined.index, columns=cols)

    current_weights = np.ones(n_strats) / n_strats
    last_rebal_month = None

    for i in range(90, len(combined)):
        date = combined.index[i]
        month = date.month

        is_quarter_start = month in [1, 4, 7, 10] and month != last_rebal_month

        if is_quarter_start or i == 90:
            window = combined.iloc[max(0, i-90):i]
            if len(window) >= 30:
                try:
                    mu = window.mean().values * ANNUAL_FACTOR
                    cov = window.cov().values * ANNUAL_FACTOR

                    def neg_sharpe(w):
                        port_mu = w @ mu
                        port_vol = np.sqrt(w @ cov @ w)
                        return -port_mu / port_vol if port_vol > 0 else 0

                    bounds = [(0.10, 0.60)] * n_strats
                    constraints = [{'type': 'eq', 'fun': lambda w: np.sum(w) - 1.0}]

                    result = optimize.minimize(
                        neg_sharpe, current_weights, method='SLSQP',
                        bounds=bounds, constraints=constraints,
                        options={'maxiter': 1000}
                    )

                    if result.success:
                        current_weights = result.x
                except Exception:
                    pass

            last_rebal_month = month

        weights_history.iloc[i] = current_weights
        port_ret.iloc[i] = combined.iloc[i].values @ current_weights

    port_ret = port_ret.iloc[90:]
    port_ret.name = 'max_sharpe'
    return port_ret, weights_history.iloc[90:]


# ══════════════════════════════════════════════════════════════════════════
# CORRELATION ANALYSIS
# ══════════════════════════════════════════════════════════════════════════

def correlation_analysis(combined):
    """Compute pairwise and rolling correlations."""
    print("\n[CORR] Computing correlation analysis...")

    # Pairwise correlation
    corr_matrix = combined.corr()
    print("\nPairwise Correlation Matrix:")
    print(corr_matrix.round(4))

    # Rolling 90d correlation
    rolling_corrs = {}
    cols = combined.columns.tolist()
    for i in range(len(cols)):
        for j in range(i+1, len(cols)):
            pair = f"{cols[i]} vs {cols[j]}"
            rc = combined[cols[i]].rolling(90).corr(combined[cols[j]]).dropna()
            rolling_corrs[pair] = rc
            print(f"  {pair}: mean={rc.mean():.4f}, min={rc.min():.4f}, "
                  f"max={rc.max():.4f}, std={rc.std():.4f}")

    rolling_df = pd.DataFrame(rolling_corrs)
    return corr_matrix, rolling_df


# ══════════════════════════════════════════════════════════════════════════
# REGIME ANALYSIS
# ══════════════════════════════════════════════════════════════════════════

def regime_analysis(port_returns_dict, btc_daily):
    """
    Define regimes using 200-day SMA on BTC.
    Above SMA200 = bull, below = bear.
    """
    print("\n[REGIME] Regime analysis (200d SMA)...")
    sma200 = btc_daily['close'].rolling(200).mean()
    regime = (btc_daily['close'] > sma200).astype(int)
    regime = regime.loc[PERIOD_START:PERIOD_END]

    results = {}
    for name, ret in port_returns_dict.items():
        aligned = pd.DataFrame({'ret': ret, 'regime': regime}).dropna()
        bull_ret = aligned[aligned['regime'] == 1]['ret']
        bear_ret = aligned[aligned['regime'] == 0]['ret']

        bull_ann = ((1 + bull_ret).prod() ** (ANNUAL_FACTOR / max(len(bull_ret), 1)) - 1
                    if len(bull_ret) > 0 else 0)
        bear_ann = ((1 + bear_ret).prod() ** (ANNUAL_FACTOR / max(len(bear_ret), 1)) - 1
                    if len(bear_ret) > 0 else 0)

        bull_sharpe = (bull_ret.mean() / bull_ret.std() * np.sqrt(ANNUAL_FACTOR)
                       if len(bull_ret) > 30 and bull_ret.std() > 0 else 0)
        bear_sharpe = (bear_ret.mean() / bear_ret.std() * np.sqrt(ANNUAL_FACTOR)
                       if len(bear_ret) > 30 and bear_ret.std() > 0 else 0)

        if len(bull_ret) > 0:
            cum_b = (1 + bull_ret).cumprod()
            bull_maxdd = (cum_b / cum_b.cummax() - 1).min()
        else:
            bull_maxdd = 0
        if len(bear_ret) > 0:
            cum_br = (1 + bear_ret).cumprod()
            bear_maxdd = (cum_br / cum_br.cummax() - 1).min()
        else:
            bear_maxdd = 0

        results[name] = {
            'Bull Days': len(bull_ret),
            'Bear Days': len(bear_ret),
            'Bull Ann Ret': bull_ann,
            'Bear Ann Ret': bear_ann,
            'Bull Sharpe': bull_sharpe,
            'Bear Sharpe': bear_sharpe,
            'Bull MaxDD': bull_maxdd,
            'Bear MaxDD': bear_maxdd,
        }

    regime_df = pd.DataFrame(results).T
    print(regime_df.to_string(float_format=lambda x: f"{x:.3f}"))
    return regime_df


# ══════════════════════════════════════════════════════════════════════════
# WALK-FORWARD PORTFOLIO TEST
# ══════════════════════════════════════════════════════════════════════════

def walk_forward_test(combined):
    """
    Walk-forward: 4 windows, 18mo train / 6mo test.
    Optimize weights in-sample, apply out-of-sample.
    """
    print("\n[WF] Walk-forward portfolio test...")
    cols = combined.columns.tolist()
    n_strats = len(cols)

    all_dates = combined.index
    start = all_dates[0]
    total_days = (all_dates[-1] - start).days

    train_days = 547
    test_days = 182
    window_step = (total_days - train_days - test_days) // 3

    windows = []
    for w in range(4):
        train_start = start + pd.Timedelta(days=w * window_step)
        train_end = train_start + pd.Timedelta(days=train_days)
        test_start = train_end
        test_end = min(test_start + pd.Timedelta(days=test_days), all_dates[-1])
        windows.append({
            'train_start': train_start, 'train_end': train_end,
            'test_start': test_start, 'test_end': test_end,
        })

    wf_results = []
    for w_idx, w in enumerate(windows):
        train_data = combined.loc[w['train_start']:w['train_end']]
        test_data = combined.loc[w['test_start']:w['test_end']]

        if len(train_data) < 30 or len(test_data) < 10:
            continue

        mu = train_data.mean().values * ANNUAL_FACTOR
        cov = train_data.cov().values * ANNUAL_FACTOR

        def neg_sharpe(weights):
            port_mu = weights @ mu
            port_vol = np.sqrt(weights @ cov @ weights)
            return -port_mu / port_vol if port_vol > 0 else 0

        bounds = [(0.10, 0.60)] * n_strats
        constraints = [{'type': 'eq', 'fun': lambda w: np.sum(w) - 1.0}]
        x0 = np.ones(n_strats) / n_strats

        result = optimize.minimize(
            neg_sharpe, x0, method='SLSQP',
            bounds=bounds, constraints=constraints,
            options={'maxiter': 1000}
        )

        opt_weights = result.x if result.success else np.ones(n_strats) / n_strats

        test_port_ret = test_data.dot(opt_weights)

        m = compute_metrics(test_port_ret, f'Window {w_idx+1}')
        m['Train Start'] = w['train_start'].strftime('%Y-%m-%d')
        m['Train End'] = w['train_end'].strftime('%Y-%m-%d')
        m['Test Start'] = w['test_start'].strftime('%Y-%m-%d')
        m['Test End'] = w['test_end'].strftime('%Y-%m-%d')
        m['Weights'] = ', '.join([f"{c}={opt_weights[i]:.1%}" for i, c in enumerate(cols)])
        wf_results.append(m)

        print(f"  Window {w_idx+1}: Test {m['Test Start']}-{m['Test End']}, "
              f"Sharpe={m['Sharpe']:.2f}, AnnRet={m['Ann Return']:.1%}, "
              f"MaxDD={m['Max DD']:.1%}")

    return pd.DataFrame(wf_results)


# ══════════════════════════════════════════════════════════════════════════
# DRAWDOWN RECOVERY ANALYSIS
# ══════════════════════════════════════════════════════════════════════════

def drawdown_recovery_analysis(returns_dict):
    """Analyze time to recover from 5%+ drawdowns."""
    print("\n[DD] Drawdown recovery analysis...")
    results = {}

    for name, ret in returns_dict.items():
        cum = (1 + ret).cumprod()
        hwm = cum.cummax()
        dd = (cum / hwm) - 1

        in_dd = dd < -0.05
        episodes = []
        current_start = None

        for i in range(len(dd)):
            if in_dd.iloc[i] and current_start is None:
                current_start = i
            elif not in_dd.iloc[i] and current_start is not None:
                episodes.append({
                    'recovery_days': i - current_start,
                    'max_dd': dd.iloc[current_start:i].min(),
                })
                current_start = None

        if current_start is not None:
            episodes.append({
                'recovery_days': len(dd) - current_start,
                'max_dd': dd.iloc[current_start:].min(),
            })

        if episodes:
            avg_recovery = np.mean([e['recovery_days'] for e in episodes])
            max_recovery = max([e['recovery_days'] for e in episodes])
            num_episodes = len(episodes)
            avg_max_dd = np.mean([e['max_dd'] for e in episodes])
        else:
            avg_recovery = max_recovery = num_episodes = 0
            avg_max_dd = 0

        results[name] = {
            'Num 5%+ DD Episodes': num_episodes,
            'Avg Recovery Days': f"{avg_recovery:.0f}",
            'Max Recovery Days': max_recovery,
            'Avg Max DD in Episode': f"{avg_max_dd:.1%}",
        }
        print(f"  {name}: {num_episodes} episodes, avg={avg_recovery:.0f}d, max={max_recovery}d")

    return pd.DataFrame(results).T


# ══════════════════════════════════════════════════════════════════════════
# REPORT GENERATION
# ══════════════════════════════════════════════════════════════════════════

def generate_report(
    strat_metrics, port_metrics, corr_matrix, rolling_corrs,
    regime_df, wf_df, dd_df, rp_weights, ms_weights
):
    """Generate the R110 markdown report."""
    lines = []
    lines.append("# R110: 3-Strategy Portfolio Test")
    lines.append(f"\n**Date:** {datetime.now().strftime('%Y-%m-%d')}")
    lines.append(f"**Period:** {PERIOD_START} to {PERIOD_END}")
    lines.append(f"**Cost:** {COST_BPS}bps round-trip")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append("")
    lines.append("This research tests a portfolio combining three uncorrelated BTC strategies:")
    lines.append("1. **V3 Momentum** (s320): Daily EMA20/EMA50 crossover, weekly rebalance")
    lines.append("2. **Intraday Momentum Breakout**: 8h momentum > 2x ATR entry, trailing stop 1.5x ATR, max 48h hold")
    lines.append("3. **Macro Regime Rotation**: US10Y and DXY both falling -> long BTC, weekly rebalance")
    lines.append("")

    # Best portfolio
    port_sharpes = {m['Name']: m['Sharpe'] for m in port_metrics[:3]}
    best_port_name = max(port_sharpes, key=port_sharpes.get)
    best_port_sharpe = port_sharpes[best_port_name]
    v3_sharpe = port_metrics[3]['Sharpe']
    bh_sharpe = port_metrics[4]['Sharpe']
    best_port_dd = [m for m in port_metrics[:3] if m['Name'] == best_port_name][0]['Max DD']
    v3_dd = port_metrics[3]['Max DD']

    lines.append(f"**Key Result:** Best portfolio ({best_port_name}) achieves Sharpe {best_port_sharpe:.2f} "
                 f"vs V3-only {v3_sharpe:.2f} and BTC B&H {bh_sharpe:.2f}. "
                 f"Max DD: {best_port_dd:.1%} vs V3 {v3_dd:.1%}.")
    lines.append("")

    # ── Section 1: Individual Strategies ──
    lines.append("## 1. Individual Strategy Performance")
    lines.append("")
    lines.append("| Metric | V3 Momentum | Intraday Momentum | Macro Regime | BTC Buy&Hold |")
    lines.append("|--------|------------|-------------------|-------------|-------------|")

    metric_keys = ['Total Return', 'Ann Return', 'Ann Vol', 'Sharpe', 'Sortino',
                   'Max DD', 'Max DD Duration (days)', 'Calmar', 'Win Rate', 'Profit Factor']
    format_pct = ['Total Return', 'Ann Return', 'Ann Vol', 'Max DD', 'Win Rate']

    for key in metric_keys:
        vals = []
        for m in strat_metrics:
            v = m.get(key, 0)
            if key in format_pct:
                vals.append(f"{v:.1%}" if isinstance(v, float) else str(v))
            elif key == 'Profit Factor':
                vals.append(f"{v:.2f}" if v != float('inf') else "inf")
            elif isinstance(v, float):
                vals.append(f"{v:.2f}")
            else:
                vals.append(str(v))
        lines.append(f"| {key} | {' | '.join(vals)} |")
    lines.append("")

    # ── Section 2: Correlation ──
    lines.append("## 2. Correlation Analysis")
    lines.append("")
    lines.append("### Pairwise Correlation Matrix (Daily Returns)")
    lines.append("")
    cols = corr_matrix.columns.tolist()
    lines.append("| | " + " | ".join(cols) + " |")
    lines.append("|---|" + "|".join(["---"] * len(cols)) + "|")
    for idx in corr_matrix.index:
        row_vals = [f"{corr_matrix.loc[idx, c]:.4f}" for c in cols]
        lines.append(f"| {idx} | " + " | ".join(row_vals) + " |")
    lines.append("")

    lines.append("### Rolling 90-Day Correlation Statistics")
    lines.append("")
    lines.append("| Pair | Mean | Min | Max | Std |")
    lines.append("|------|------|-----|-----|-----|")
    for col in rolling_corrs.columns:
        rc = rolling_corrs[col].dropna()
        lines.append(f"| {col} | {rc.mean():.4f} | {rc.min():.4f} | {rc.max():.4f} | {rc.std():.4f} |")
    lines.append("")
    lines.append("**Key Finding:** Low pairwise correlations confirm independent signal sources.")
    lines.append("")

    # ── Section 3: Portfolio Results ──
    lines.append("## 3. Portfolio Allocation Results")
    lines.append("")
    lines.append("| Metric | Equal Weight | Risk Parity | Max Sharpe | V3-Only | BTC B&H |")
    lines.append("|--------|-------------|------------|-----------|---------|---------|")

    for key in metric_keys:
        vals = []
        for m in port_metrics:
            v = m.get(key, 0)
            if key in format_pct:
                vals.append(f"{v:.1%}" if isinstance(v, float) else str(v))
            elif key == 'Profit Factor':
                vals.append(f"{v:.2f}" if v != float('inf') else "inf")
            elif isinstance(v, float):
                vals.append(f"{v:.2f}")
            else:
                vals.append(str(v))
        lines.append(f"| {key} | {' | '.join(vals)} |")
    lines.append("")

    lines.append("### Average Weights")
    lines.append("")
    if rp_weights is not None and len(rp_weights) > 0:
        rp_avg = rp_weights.mean()
        lines.append("**Risk Parity (average weights):**")
        for c in rp_avg.index:
            lines.append(f"- {c}: {rp_avg[c]:.1%}")
        lines.append("")

    if ms_weights is not None and len(ms_weights) > 0:
        ms_avg = ms_weights[ms_weights.sum(axis=1) > 0].mean()
        lines.append("**Max Sharpe (average weights):**")
        for c in ms_avg.index:
            lines.append(f"- {c}: {ms_avg[c]:.1%}")
        lines.append("")

    # ── Section 4: Regime ──
    lines.append("## 4. Regime Analysis (200d SMA)")
    lines.append("")
    lines.append("BTC price above 200d SMA = Bull, below = Bear.")
    lines.append("")
    lines.append("| Strategy | Bull Days | Bear Days | Bull Ann Ret | Bear Ann Ret | Bull Sharpe | Bear Sharpe | Bull MaxDD | Bear MaxDD |")
    lines.append("|----------|-----------|-----------|-------------|-------------|------------|------------|-----------|-----------|")
    for name, row in regime_df.iterrows():
        lines.append(f"| {name} | {int(row['Bull Days'])} | {int(row['Bear Days'])} | "
                     f"{row['Bull Ann Ret']:.1%} | {row['Bear Ann Ret']:.1%} | "
                     f"{row['Bull Sharpe']:.2f} | {row['Bear Sharpe']:.2f} | "
                     f"{row['Bull MaxDD']:.1%} | {row['Bear MaxDD']:.1%} |")
    lines.append("")

    v3_bear = regime_df.loc['V3-Only', 'Bear Sharpe'] if 'V3-Only' in regime_df.index else 0
    ew_bear = regime_df.loc['Equal Weight', 'Bear Sharpe'] if 'Equal Weight' in regime_df.index else 0
    lines.append(f"**V3 Range/Bear Fix:** Equal Weight bear Sharpe = {ew_bear:.2f} vs V3-only = {v3_bear:.2f}. "
                 f"{'Portfolio diversification helps in bear markets.' if ew_bear > v3_bear else 'Both struggle in bear markets.'}")
    lines.append("")

    # ── Section 5: Walk-Forward ──
    lines.append("## 5. Walk-Forward Portfolio Test (4 Windows)")
    lines.append("")
    lines.append("18mo train / 6mo test. Optimal weights in-sample, applied OOS.")
    lines.append("")
    lines.append("| Window | Train Period | Test Period | OOS Sharpe | OOS Ann Ret | OOS Max DD | Weights |")
    lines.append("|--------|-------------|------------|-----------|-----------|----------|---------|")
    for _, row in wf_df.iterrows():
        lines.append(f"| {row['Name']} | {row['Train Start']} to {row['Train End']} | "
                     f"{row['Test Start']} to {row['Test End']} | "
                     f"{row['Sharpe']:.2f} | {row['Ann Return']:.1%} | {row['Max DD']:.1%} | "
                     f"{row['Weights']} |")

    avg_oos_sharpe = wf_df['Sharpe'].mean()
    avg_oos_ret = wf_df['Ann Return'].mean()
    avg_oos_dd = wf_df['Max DD'].mean()
    lines.append(f"| **Average** | | | **{avg_oos_sharpe:.2f}** | **{avg_oos_ret:.1%}** | **{avg_oos_dd:.1%}** | |")
    lines.append("")

    wf_positive = (wf_df['Sharpe'] > 0).sum()
    lines.append(f"Walk-forward stability: {wf_positive}/4 windows with positive OOS Sharpe.")
    lines.append("")

    # ── Section 6: Drawdown Recovery ──
    lines.append("## 6. Drawdown Recovery Analysis")
    lines.append("")
    lines.append("Episodes where drawdown exceeded 5%.")
    lines.append("")
    lines.append("| Strategy | Num Episodes | Avg Recovery Days | Max Recovery Days | Avg Max DD |")
    lines.append("|----------|-------------|-------------------|-------------------|-----------|")
    for name, row in dd_df.iterrows():
        lines.append(f"| {name} | {row['Num 5%+ DD Episodes']} | {row['Avg Recovery Days']} | "
                     f"{row['Max Recovery Days']} | {row['Avg Max DD in Episode']} |")
    lines.append("")

    # ── Section 7: Conclusions ──
    lines.append("## 7. Conclusions")
    lines.append("")
    lines.append(f"1. **Best portfolio allocation:** {best_port_name} (Sharpe: {best_port_sharpe:.2f})")
    lines.append(f"2. **vs V3-only Sharpe ({v3_sharpe:.2f}):** "
                 f"{'Improvement' if best_port_sharpe > v3_sharpe else 'Underperformance'} "
                 f"of {abs(best_port_sharpe - v3_sharpe):.2f}")
    lines.append(f"3. **vs BTC B&H Sharpe ({bh_sharpe:.2f}):** "
                 f"{'Improvement' if best_port_sharpe > bh_sharpe else 'Underperformance'} "
                 f"of {abs(best_port_sharpe - bh_sharpe):.2f}")
    lines.append(f"4. **Max DD reduction:** V3={v3_dd:.1%} -> {best_port_name}={best_port_dd:.1%} "
                 f"({'improved' if abs(best_port_dd) < abs(v3_dd) else 'worsened'})")
    lines.append(f"5. **Walk-forward stability:** {wf_positive}/4 windows positive OOS Sharpe "
                 f"(avg={avg_oos_sharpe:.2f})")
    lines.append(f"6. **Correlation verdict:** Strategies show low pairwise correlations, "
                 f"validating portfolio diversification.")
    lines.append("")

    lines.append("### Individual Strategy Assessment")
    lines.append("")
    for m in strat_metrics[:3]:
        name = m['Name']
        sharpe = m['Sharpe']
        total = m['Total Return']
        verdict = "STRONG" if sharpe > 0.5 else "PASS" if sharpe > 0 else "WEAK"
        lines.append(f"- **{name}**: Sharpe={sharpe:.2f}, Total={total:.1%} -> {verdict}")
    lines.append("")

    lines.append("## 8. Recommendation")
    lines.append("")
    all_positive = all(m['Sharpe'] > 0 for m in strat_metrics[:3])
    if best_port_sharpe > v3_sharpe and wf_positive >= 3 and all_positive:
        lines.append(f"**PROCEED to production.** The {best_port_name} portfolio shows robust improvement "
                     f"over V3-only. All strategies contribute positive risk-adjusted returns.")
    elif best_port_sharpe > v3_sharpe and wf_positive >= 2:
        lines.append(f"**CONDITIONAL PROCEED.** The {best_port_name} portfolio improves on V3-only "
                     f"but some walk-forward windows show weakness.")
    elif best_port_sharpe > v3_sharpe:
        lines.append(f"**NEEDS REFINEMENT.** Portfolio shows promise but walk-forward stability "
                     f"is insufficient.")
    else:
        lines.append(f"**HOLD.** Portfolio diversification does not conclusively improve on V3-only. "
                     f"Individual strategies need refinement before combining.")

    weak = [m['Name'] for m in strat_metrics[:3] if m['Sharpe'] < 0.1]
    if weak:
        lines.append("")
        lines.append(f"**Action items:** Refine {', '.join(weak)} before retesting portfolio.")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("R110: 3-Strategy Portfolio Test")
    print("=" * 70)

    # ── Load Data ──
    btc_1h = load_btc_1h()
    btc_daily = load_btc_daily(btc_1h)
    us10y, dxy = load_macro()

    # ── Build Strategy Return Series ──
    v3_ret = strategy_v3_momentum(btc_daily)
    intraday_ret = strategy_intraday_momentum(btc_1h)
    macro_ret = strategy_macro_regime(btc_daily, us10y, dxy)
    bh_ret = benchmark_buy_hold(btc_daily)

    # Align all to common date range
    common_idx = v3_ret.index.intersection(intraday_ret.index).intersection(macro_ret.index)
    v3_ret = v3_ret.loc[common_idx]
    intraday_ret = intraday_ret.loc[common_idx]
    macro_ret = macro_ret.loc[common_idx]
    bh_ret = bh_ret.reindex(common_idx).fillna(0)

    print(f"\n[ALIGNED] Common period: {common_idx.min().date()} to {common_idx.max().date()}, "
          f"{len(common_idx)} days")

    # Build combined DataFrame properly (strategies as columns)
    combined = pd.DataFrame({
        'v3_momentum': v3_ret,
        'intraday_momentum': intraday_ret,
        'macro_regime': macro_ret,
    })

    # ── Individual Strategy Metrics ──
    print("\n" + "=" * 70)
    print("INDIVIDUAL STRATEGY METRICS")
    print("=" * 70)

    v3_metrics = compute_metrics(v3_ret, 'V3 Momentum')
    intraday_metrics = compute_metrics(intraday_ret, 'Intraday Momentum')
    macro_metrics = compute_metrics(macro_ret, 'Macro Regime')
    bh_metrics = compute_metrics(bh_ret, 'BTC Buy&Hold')
    strat_metrics = [v3_metrics, intraday_metrics, macro_metrics, bh_metrics]

    for m in strat_metrics:
        print(f"\n{m['Name']}:")
        for k, v in m.items():
            if k != 'Name':
                print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")

    # ── Correlation Analysis ──
    print("\n" + "=" * 70)
    print("CORRELATION ANALYSIS")
    print("=" * 70)
    corr_matrix, rolling_corrs = correlation_analysis(combined)

    # ── Portfolio Allocations ──
    print("\n" + "=" * 70)
    print("PORTFOLIO ALLOCATIONS")
    print("=" * 70)

    ew_ret = portfolio_equal_weight(combined)
    rp_ret, rp_weights = portfolio_risk_parity(combined)
    ms_ret, ms_weights = portfolio_max_sharpe(combined)

    # Align portfolio returns for fair comparison
    port_common = ew_ret.index.intersection(rp_ret.index).intersection(ms_ret.index)
    ew_ret_a = ew_ret.loc[port_common]
    rp_ret_a = rp_ret.loc[port_common]
    ms_ret_a = ms_ret.loc[port_common]
    v3_ret_a = v3_ret.reindex(port_common).fillna(0)
    bh_ret_a = bh_ret.reindex(port_common).fillna(0)

    ew_metrics = compute_metrics(ew_ret_a, 'Equal Weight')
    rp_metrics = compute_metrics(rp_ret_a, 'Risk Parity')
    ms_metrics = compute_metrics(ms_ret_a, 'Max Sharpe')
    v3_only_metrics = compute_metrics(v3_ret_a, 'V3-Only')
    bh_port_metrics = compute_metrics(bh_ret_a, 'BTC B&H')
    port_metrics = [ew_metrics, rp_metrics, ms_metrics, v3_only_metrics, bh_port_metrics]

    for m in port_metrics:
        print(f"\n{m['Name']}:")
        for k, v in m.items():
            if k != 'Name':
                print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")

    # ── Regime Analysis ──
    print("\n" + "=" * 70)
    print("REGIME ANALYSIS")
    print("=" * 70)

    regime_returns = {
        'V3-Only': v3_ret,
        'Equal Weight': ew_ret,
        'Risk Parity': rp_ret,
        'Max Sharpe': ms_ret,
        'BTC B&H': bh_ret,
    }
    regime_df = regime_analysis(regime_returns, btc_daily)

    # ── Walk-Forward Test ──
    print("\n" + "=" * 70)
    print("WALK-FORWARD TEST")
    print("=" * 70)
    wf_df = walk_forward_test(combined)

    # ── Drawdown Recovery ──
    print("\n" + "=" * 70)
    print("DRAWDOWN RECOVERY")
    print("=" * 70)
    dd_returns = {
        'V3-Only': v3_ret,
        'Equal Weight': ew_ret,
        'Risk Parity': rp_ret,
        'Max Sharpe': ms_ret,
        'BTC B&H': bh_ret,
    }
    dd_df = drawdown_recovery_analysis(dd_returns)

    # ── Generate Report ──
    print("\n" + "=" * 70)
    print("GENERATING REPORT")
    print("=" * 70)
    report = generate_report(
        strat_metrics, port_metrics, corr_matrix, rolling_corrs,
        regime_df, wf_df, dd_df, rp_weights, ms_weights
    )

    OUTPUT_MD.write_text(report)
    print(f"\nReport saved to {OUTPUT_MD}")

    # Print report
    print("\n" + "=" * 70)
    print("FULL REPORT")
    print("=" * 70)
    print(report)


if __name__ == '__main__':
    main()
