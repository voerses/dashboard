#!/workspace/venv/bin/python
"""
R170: Optimized Multi-Strategy Portfolio
=========================================

Combines:
  Component A: R162 Cross-sectional momentum rotation (HIGH ALPHA)
  Component B: R167-Optimized R160 Volatility breakout rotation (LOW DD, BOOSTED RETURN)

Using R167's OPTIMIZED parameters for Component B:
  - max_positions=5, mom_days=7, trail_sma=15 (key changes from R160 defaults)

Sweeps 224 configurations (7 allocations x 8 leverages x 4 DD controls).

Target: 300%+ last 12mo return, MaxDD < 20%, Calmar > 3.
"""

import sys
import warnings
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import time
from itertools import product

warnings.filterwarnings('ignore')

def log(msg):
    print(msg, flush=True)

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = PROJECT_DIR / 'data' / 'perp' / '1h_cache'
OUTPUT_MD = PROJECT_DIR / 'research' / 'R170_optimized_portfolio_results.md'

# ── Time Constants ────────────────────────────────────────────────────────────
SIM_START = pd.Timestamp('2024-03-17')
SIM_END = pd.Timestamp('2026-03-17')
L12M_START = pd.Timestamp('2025-03-17')
DATA_CUTOFF = pd.Timestamp('2024-03-17')
DAYS_PER_YEAR = 365

# ── Cost & Universe ───────────────────────────────────────────────────────────
FEE_BPS = 7
FEE_FRAC = FEE_BPS / 10_000
MIN_AVG_DAILY_VOL = 1_000_000  # $1M

# ── Sweep Grid ────────────────────────────────────────────────────────────────
ALLOCATIONS = [(0.80, 0.20), (0.70, 0.30), (0.60, 0.40), (0.50, 0.50),
               (0.40, 0.60), (0.30, 0.70), (0.20, 0.80)]
LEVERAGES = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0]
DD_WINDOWS = [None, 15, 20, 30]


# ==============================================================================
# DATA LOADING
# ==============================================================================

def load_universe() -> Dict[str, pd.DataFrame]:
    """Load all tokens with data before cutoff and >$1M avg daily volume."""
    tokens = {}
    for f in sorted(DATA_DIR.glob('*_1h.parquet')):
        ticker = f.stem.replace('_1h', '')
        df = pd.read_parquet(f)
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        df = df[~df.index.duplicated(keep='first')]

        if df.index.min() >= DATA_CUTOFF:
            continue

        pre_sim = df[df.index < SIM_START]
        if len(pre_sim) < 30 * 24:
            continue

        last_30d = pre_sim.tail(30 * 24)
        daily_dvol = (last_30d['volume'] * last_30d['close']).resample('1D').sum()
        avg_dvol = daily_dvol.mean()

        if avg_dvol < MIN_AVG_DAILY_VOL:
            continue

        tokens[ticker] = df
    return tokens


# ==============================================================================
# COMPONENT A: R162 Cross-Sectional Momentum Rotation
# ==============================================================================

def generate_component_a_returns(tokens: Dict[str, pd.DataFrame]) -> pd.Series:
    """
    R162 momentum rotation: L=7, K=3, 80/20 long/short, EMA(10/30) regime,
    ATR(336h) volatility filter, weekly rebalance.
    Returns daily PnL series (as fraction of component capital).
    """
    L = 7         # lookback days
    K = 3         # tokens per leg
    LONG_WT = 0.80
    SHORT_WT = 0.20
    REBALANCE_DAYS = 7
    EMA_FAST = 10
    EMA_SLOW = 30
    ATR_HOURS = 336  # 14 days

    # Prepare daily close, returns, signals
    daily_closes = {}
    daily_volumes = {}
    ema_signals = {}
    hourly_fundings = {}
    atr_ratios = {}

    for ticker, df in tokens.items():
        daily_close = df['close'].resample('1D').last().dropna()
        daily_closes[ticker] = daily_close

        dvol = (df['volume'] * df['close']).resample('1D').sum()
        daily_volumes[ticker] = dvol

        ema_fast = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
        ema_slow = df['close'].ewm(span=EMA_SLOW, adjust=False).mean()
        ema_diff = (ema_fast - ema_slow).resample('1D').last()
        ema_signals[ticker] = ema_diff

        hourly_fundings[ticker] = df['funding_1h'].fillna(0)

        # ATR for volatility filter
        high_d = df['high'].resample('1D').max().dropna()
        low_d = df['low'].resample('1D').min().dropna()
        close_d = df['close'].resample('1D').last().dropna()
        close_prev = close_d.shift(1)
        tr = pd.concat([high_d - low_d, (high_d - close_prev).abs(), (low_d - close_prev).abs()], axis=1).max(axis=1)
        atr_14 = tr.rolling(14, min_periods=7).mean()
        atr_ratios[ticker] = atr_14 / close_d

    dc = pd.DataFrame(daily_closes)
    dv = pd.DataFrame(daily_volumes)

    start_buf = SIM_START - pd.Timedelta(days=40)
    dc = dc.loc[start_buf:SIM_END]
    dv = dv.reindex(dc.index)

    daily_rets = dc.pct_change()

    # Lookback returns (shifted to avoid lookahead)
    lb_rets = dc.shift(1) / dc.shift(1 + L) - 1

    # Rolling avg dollar volume (shifted)
    rolling_dvol = dv.rolling(30, min_periods=15).mean().shift(1)

    # EMA signals (shifted)
    ema_df = pd.DataFrame(ema_signals).reindex(dc.index).shift(1)

    # ATR ratio (shifted)
    atr_df = pd.DataFrame(atr_ratios).reindex(dc.index).shift(1)

    # Simulation
    sim_dates = dc.loc[SIM_START:SIM_END].index
    rebalance_set = set(range(0, len(sim_dates), REBALANCE_DAYS))

    current_longs = {}   # ticker -> weight
    current_shorts = {}
    prev_long_set = set()
    prev_short_set = set()
    pending_longs = None
    pending_shorts = None

    daily_pnl = {}

    for i, date in enumerate(sim_dates):
        # Apply pending rebalance
        if pending_longs is not None:
            new_long_set = set(pending_longs.keys())
            new_short_set = set(pending_shorts.keys())

            entries_exits = (new_long_set - prev_long_set) | (prev_long_set - new_long_set) | \
                            (new_short_set - prev_short_set) | (prev_short_set - new_short_set)
            n_trades = len(entries_exits)
            trade_cost = n_trades * FEE_FRAC * 2  # entry + exit = 2 sides

            current_longs = pending_longs
            current_shorts = pending_shorts
            prev_long_set = new_long_set
            prev_short_set = new_short_set
            pending_longs = None
            pending_shorts = None
        else:
            trade_cost = 0.0

        # Daily PnL
        day_ret = daily_rets.loc[date] if date in daily_rets.index else pd.Series(dtype=float)
        pnl = -trade_cost

        for ticker, weight in current_longs.items():
            if ticker in day_ret.index and not np.isnan(day_ret[ticker]):
                pnl += weight * day_ret[ticker]

        for ticker, weight in current_shorts.items():
            if ticker in day_ret.index and not np.isnan(day_ret[ticker]):
                pnl -= weight * day_ret[ticker]

        # Funding
        f_start = date
        f_end = date + pd.Timedelta(hours=23)
        for ticker, weight in current_longs.items():
            if ticker in hourly_fundings:
                f_slice = hourly_fundings[ticker].loc[f_start:f_end]
                if len(f_slice) > 0:
                    pnl -= f_slice.sum() * weight  # longs pay positive funding

        for ticker, weight in current_shorts.items():
            if ticker in hourly_fundings:
                f_slice = hourly_fundings[ticker].loc[f_start:f_end]
                if len(f_slice) > 0:
                    pnl += f_slice.sum() * weight  # shorts collect positive funding

        daily_pnl[date] = pnl

        # Rebalance decision
        if i in rebalance_set:
            if date not in lb_rets.index:
                continue
            lb = lb_rets.loc[date].dropna()
            vol = rolling_dvol.loc[date].dropna() if date in rolling_dvol.index else pd.Series(dtype=float)
            eligible = vol[vol >= MIN_AVG_DAILY_VOL].index
            lb = lb[lb.index.isin(eligible)]

            # Volatility filter: ATR/price > universe median
            if date in atr_df.index:
                atr_at_date = atr_df.loc[date].dropna()
                atr_eligible = atr_at_date[atr_at_date.index.isin(lb.index)]
                if len(atr_eligible) > 0:
                    median_atr = atr_eligible.median()
                    high_vol = atr_eligible[atr_eligible > median_atr].index
                    lb = lb[lb.index.isin(high_vol)]

            if len(lb) >= 2 * K:
                ranked = lb.sort_values(ascending=False)
                top_k = ranked.head(K).index.tolist()
                bottom_k = ranked.tail(K).index.tolist()

                # Regime filter
                ema_at = ema_df.loc[date].dropna() if date in ema_df.index else pd.Series(dtype=float)
                top_k = [t for t in top_k if t in ema_at.index and ema_at[t] > 0]
                bottom_k = [t for t in bottom_k if t in ema_at.index and ema_at[t] < 0]

                n_l = max(len(top_k), 1)
                n_s = max(len(bottom_k), 1)
                pending_longs = {t: LONG_WT / n_l for t in top_k} if top_k else {}
                pending_shorts = {t: SHORT_WT / n_s for t in bottom_k} if bottom_k else {}

    return pd.Series(daily_pnl).sort_index()


# ==============================================================================
# COMPONENT B: R167-Optimized R160 Volatility Breakout Rotation
# ==============================================================================

def generate_component_b_returns(tokens: Dict[str, pd.DataFrame]) -> pd.Series:
    """
    R167-optimized R160 volatility breakout rotation on 4H bars.
    OPTIMIZED params: max_positions=5, mom_days=7, trail_sma=15.
    BB(20,2.0), vol_mult=1.5, partial_atr=3.0.
    Returns daily PnL series (as fraction of component capital).
    """
    BB_PERIOD = 20
    BB_STD = 2.0
    VOL_MULT = 1.5
    MAX_POS = 5
    MOM_DAYS = 7
    TRAIL_SMA = 15
    PARTIAL_ATR = 3.0
    PARTIAL_FRAC = 0.5
    MAX_HOLD_BARS = 180  # 30 days of 4H bars
    TOP_N = 5
    POS_SIZE = 1.0 / MAX_POS

    MOM_BARS_4H = MOM_DAYS * 6  # 4H bars

    # Resample all tokens to 4H
    token_4h = {}
    for ticker, df in tokens.items():
        df_4h = df.resample('4h').agg({
            'open': 'first', 'high': 'max', 'low': 'min',
            'close': 'last', 'volume': 'sum',
        }).dropna(subset=['close'])

        if 'funding_1h' in df.columns:
            df_4h['funding_4h'] = df['funding_1h'].resample('4h').sum()
        else:
            df_4h['funding_4h'] = 0.0

        if len(df_4h) < BB_PERIOD + 50:
            continue

        # Bollinger Bands
        df_4h['sma_bb'] = df_4h['close'].rolling(BB_PERIOD).mean()
        df_4h['bb_std'] = df_4h['close'].rolling(BB_PERIOD).std()
        df_4h['bb_upper'] = df_4h['sma_bb'] + BB_STD * df_4h['bb_std']
        df_4h['bb_lower'] = df_4h['sma_bb'] - BB_STD * df_4h['bb_std']

        # Volume avg
        df_4h['avg_vol'] = df_4h['volume'].rolling(BB_PERIOD).mean()

        # ATR
        tr_hl = df_4h['high'] - df_4h['low']
        tr_hc = (df_4h['high'] - df_4h['close'].shift(1)).abs()
        tr_lc = (df_4h['low'] - df_4h['close'].shift(1)).abs()
        df_4h['atr'] = pd.concat([tr_hl, tr_hc, tr_lc], axis=1).max(axis=1).rolling(20).mean()

        # Trail SMA
        df_4h['trail_sma'] = df_4h['close'].rolling(TRAIL_SMA).mean()

        # Momentum
        df_4h['mom_ret'] = df_4h['close'].pct_change(MOM_BARS_4H)

        # Breakout signals
        df_4h['vol_ok'] = df_4h['volume'] > VOL_MULT * df_4h['avg_vol']
        df_4h['long_sig'] = (df_4h['close'] > df_4h['bb_upper']) & df_4h['vol_ok'] & (df_4h['mom_ret'] > 0)
        df_4h['short_sig'] = (df_4h['close'] < df_4h['bb_lower']) & df_4h['vol_ok'] & (df_4h['mom_ret'] < 0)

        # Breakout strength
        df_4h['long_str'] = ((df_4h['close'] - df_4h['bb_upper']) / df_4h['close']).clip(lower=0)
        df_4h['short_str'] = ((df_4h['bb_lower'] - df_4h['close']) / df_4h['close']).clip(lower=0)

        # Dollar volume for filtering
        df_4h['dvol'] = df_4h['volume'] * df_4h['close']
        df_4h['avg_dvol_30d'] = df_4h['dvol'].rolling(180).mean() * 6

        token_4h[ticker] = df_4h

    if not token_4h:
        return pd.Series(dtype=float)

    # Build aligned time index
    all_times = sorted(set().union(*(set(df.index) for df in token_4h.values())))

    # Build per-token numpy arrays for fast access
    token_idx_map = {}
    token_arr = {}
    for ticker, df in token_4h.items():
        idx_map = {t: i for i, t in enumerate(df.index)}
        token_idx_map[ticker] = idx_map
        token_arr[ticker] = {col: df[col].values for col in df.columns}
        token_arr[ticker]['_index'] = df.index

    # Position tracking
    # Each position: {token, direction, entry_price, entry_atr, entry_bar, bars_held, partial_taken, remaining_frac, breakeven}
    positions = []
    equity = 1.0
    equity_curve = {}

    warmup_time = SIM_START
    bar_pnl_accum = {}  # date -> list of pnl contributions

    for ti, ct in enumerate(all_times):
        if ct < warmup_time:
            equity_curve[ct] = equity
            continue
        if equity <= 0.01:
            equity_curve[ct] = max(equity, 0.0)
            continue

        # -- Check exits for existing positions --
        to_remove = []
        for pidx, pos in enumerate(positions):
            token = pos['token']
            if ct not in token_idx_map[token]:
                pos['bars_held'] += 1
                continue

            bi = token_idx_map[token][ct]
            arr = token_arr[token]
            close_p = arr['close'][bi]
            trail_v = arr['trail_sma'][bi]
            atr_v = arr['atr'][bi]

            if np.isnan(close_p) or np.isnan(trail_v):
                pos['bars_held'] += 1
                continue

            pos['bars_held'] += 1
            exit_reason = None

            # Partial profit
            if not pos['partial_taken'] and atr_v > 0 and pos['entry_atr'] > 0:
                target = pos['entry_price'] + pos['direction'] * PARTIAL_ATR * pos['entry_atr']
                if (pos['direction'] == 1 and close_p >= target) or \
                   (pos['direction'] == -1 and close_p <= target):
                    if pos['direction'] == 1:
                        partial_ret = (close_p / pos['entry_price'] - 1.0)
                    else:
                        partial_ret = (1.0 - close_p / pos['entry_price'])
                    partial_cost = FEE_FRAC
                    # Funding accumulated
                    f_sum = pos.get('funding_accum', 0.0)
                    partial_pnl = (partial_ret - partial_cost) * POS_SIZE * PARTIAL_FRAC
                    partial_pnl -= f_sum * POS_SIZE * PARTIAL_FRAC
                    equity += partial_pnl
                    pos['partial_taken'] = True
                    pos['remaining_frac'] = 1.0 - PARTIAL_FRAC
                    pos['breakeven'] = True
                    pos['funding_accum'] = f_sum * (1 - PARTIAL_FRAC)  # remaining funding for other half

            # Trail stop
            if pos['direction'] == 1 and close_p < trail_v:
                exit_reason = 'trail_stop'
            elif pos['direction'] == -1 and close_p > trail_v:
                exit_reason = 'trail_stop'

            # Breakeven stop after partial
            if exit_reason is None and pos.get('breakeven', False):
                if pos['direction'] == 1 and close_p < pos['entry_price']:
                    exit_reason = 'breakeven'
                elif pos['direction'] == -1 and close_p > pos['entry_price']:
                    exit_reason = 'breakeven'

            # Max hold
            if exit_reason is None and pos['bars_held'] >= MAX_HOLD_BARS:
                exit_reason = 'max_hold'

            if exit_reason is not None:
                if pos['direction'] == 1:
                    trade_ret = (close_p / pos['entry_price'] - 1.0)
                else:
                    trade_ret = (1.0 - close_p / pos['entry_price'])
                exit_cost = FEE_FRAC
                f_sum = pos.get('funding_accum', 0.0)
                rem = pos['remaining_frac']
                pnl = (trade_ret - exit_cost) * POS_SIZE * rem
                pnl -= f_sum * POS_SIZE * rem
                equity += pnl
                to_remove.append(pidx)

            # Accumulate funding
            if ct in token_idx_map[token]:
                bi2 = token_idx_map[token][ct]
                fund_val = arr['funding_4h'][bi2] if not np.isnan(arr['funding_4h'][bi2]) else 0.0
                if pos['direction'] == 1:
                    pos['funding_accum'] = pos.get('funding_accum', 0.0) + fund_val  # longs pay positive funding
                else:
                    pos['funding_accum'] = pos.get('funding_accum', 0.0) - fund_val  # shorts collect

        for idx in sorted(to_remove, reverse=True):
            positions.pop(idx)

        # -- Scan for new entries --
        candidates_long = []
        candidates_short = []

        for ticker, arr in token_arr.items():
            if ct not in token_idx_map[ticker]:
                continue
            bi = token_idx_map[ticker][ct]

            # Check volume filter
            avg_dv = arr['avg_dvol_30d'][bi]
            if np.isnan(avg_dv) or avg_dv < MIN_AVG_DAILY_VOL:
                continue

            # Already have position?
            already = any(p['token'] == ticker for p in positions)
            if already:
                continue

            if arr['long_sig'][bi] and not np.isnan(arr['atr'][bi]) and arr['atr'][bi] > 0:
                candidates_long.append((ticker, arr['long_str'][bi], arr['close'][bi], arr['atr'][bi]))
            if arr['short_sig'][bi] and not np.isnan(arr['atr'][bi]) and arr['atr'][bi] > 0:
                candidates_short.append((ticker, arr['short_str'][bi], arr['close'][bi], arr['atr'][bi]))

        # Rank and enter
        slots = MAX_POS - len(positions)
        if slots > 0:
            all_cands = [(t, s, p, a, 1) for t, s, p, a in candidates_long] + \
                        [(t, s, p, a, -1) for t, s, p, a in candidates_short]
            all_cands.sort(key=lambda x: x[1], reverse=True)

            for token, strength, price, atr_val, direction in all_cands[:min(TOP_N, slots)]:
                entry_cost = FEE_FRAC
                equity -= entry_cost * POS_SIZE
                positions.append({
                    'token': token,
                    'direction': direction,
                    'entry_price': price,
                    'entry_atr': atr_val,
                    'entry_bar': ti,
                    'bars_held': 0,
                    'partial_taken': False,
                    'remaining_frac': 1.0,
                    'breakeven': False,
                    'funding_accum': 0.0,
                })
                slots -= 1
                if slots <= 0:
                    break

        equity_curve[ct] = equity

    # Convert to daily returns
    eq_series = pd.Series(equity_curve).sort_index()
    eq_daily = eq_series.resample('1D').last().dropna()
    daily_rets = eq_daily.pct_change().dropna()

    return daily_rets


# ==============================================================================
# PORTFOLIO CONSTRUCTION & SIMULATION
# ==============================================================================

def compute_metrics(daily_rets: pd.Series, period_label: str = "full") -> dict:
    """Compute standard metrics from daily return series."""
    if len(daily_rets) < 10:
        return {'total_return': 0, 'ann_return': 0, 'sharpe': 0, 'max_dd': 0,
                'calmar': 0, 'sortino': 0}

    equity = (1 + daily_rets).cumprod()
    total_return = equity.iloc[-1] / equity.iloc[0] - 1 if equity.iloc[0] > 0 else 0
    n_days = (daily_rets.index[-1] - daily_rets.index[0]).days
    n_days = max(n_days, 1)
    ann_return = (1 + total_return) ** (DAYS_PER_YEAR / n_days) - 1

    running_max = equity.cummax()
    dd = equity / running_max - 1
    max_dd = dd.min()

    mean_d = daily_rets.mean()
    std_d = daily_rets.std()
    sharpe = (mean_d / std_d * np.sqrt(DAYS_PER_YEAR)) if std_d > 0 else 0

    calmar = ann_return / abs(max_dd) if max_dd != 0 else 0

    downside = daily_rets[daily_rets < 0]
    ds_std = downside.std() if len(downside) > 0 else 1e-10
    sortino = (mean_d / ds_std * np.sqrt(DAYS_PER_YEAR)) if ds_std > 0 else 0

    return {
        'total_return': total_return,
        'ann_return': ann_return,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'calmar': calmar,
        'sortino': sortino,
    }


def run_portfolio_sweep(
    ret_a: pd.Series,
    ret_b: pd.Series,
) -> List[dict]:
    """
    Sweep all 224 configurations.
    For each config: combine daily returns, apply leverage + DD control, compute metrics.
    """
    # Align dates
    common_idx = ret_a.index.intersection(ret_b.index)
    common_idx = common_idx[(common_idx >= SIM_START) & (common_idx <= SIM_END)]
    common_idx = common_idx.sort_values()

    ra = ret_a.reindex(common_idx).fillna(0)
    rb = ret_b.reindex(common_idx).fillna(0)

    results = []

    for w_a, w_b in ALLOCATIONS:
        for lev in LEVERAGES:
            for dd_win in DD_WINDOWS:
                # Daily borrow cost
                borrow_daily = max(0, lev - 1) * 0.05 / DAYS_PER_YEAR

                # Build equity curve with DD control
                equity = np.ones(len(common_idx) + 1)
                effective_levs = np.zeros(len(common_idx))

                for i in range(len(common_idx)):
                    # DD control overlay
                    if dd_win is not None and i >= dd_win:
                        trailing_peak = equity[max(0, i - dd_win):i + 1].max()
                    elif dd_win is not None:
                        trailing_peak = equity[:i + 1].max()
                    else:
                        trailing_peak = None

                    if trailing_peak is not None and trailing_peak > 0:
                        current_dd = equity[i] / trailing_peak - 1
                        if current_dd > -0.05:
                            scale = 1.0
                        elif current_dd > -0.10:
                            scale = 0.75
                        elif current_dd > -0.15:
                            scale = 0.50
                        elif current_dd > -0.20:
                            scale = 0.25
                        else:
                            scale = 0.0
                    else:
                        scale = 1.0

                    eff_lev = lev * scale
                    effective_levs[i] = eff_lev

                    raw_ret = w_a * ra.iloc[i] + w_b * rb.iloc[i]
                    port_ret = eff_lev * raw_ret - borrow_daily
                    equity[i + 1] = equity[i] * (1 + port_ret)

                    # Floor at 0.001 to avoid negative equity
                    if equity[i + 1] < 0.001:
                        equity[i + 1] = 0.001

                # Build result series
                eq_series = pd.Series(equity[1:], index=common_idx)

                # Full period metrics
                full_rets = eq_series.pct_change().dropna()
                # We need to prepend the first return from equity[0]=1 to equity[1]
                first_ret = eq_series.iloc[0] - 1.0
                full_rets_all = pd.concat([pd.Series([first_ret], index=[common_idx[0]]), full_rets])

                m_full = compute_metrics(full_rets_all, "full")

                # Last 12M metrics
                l12m_mask = common_idx >= L12M_START
                if l12m_mask.sum() > 10:
                    eq_12m = eq_series[l12m_mask]
                    rets_12m = eq_12m.pct_change().dropna()
                    first_12m_idx = eq_12m.index[0]
                    # Get equity value at start of 12M period
                    pre_idx = common_idx[common_idx < L12M_START]
                    if len(pre_idx) > 0:
                        eq_before = eq_series[pre_idx[-1]]
                        first_12m_ret = eq_12m.iloc[0] / eq_before - 1
                    else:
                        first_12m_ret = eq_12m.iloc[0] - 1
                    rets_12m_all = pd.concat([pd.Series([first_12m_ret], index=[first_12m_idx]), rets_12m])
                    m_12m = compute_metrics(rets_12m_all, "12m")
                else:
                    m_12m = {'total_return': 0, 'ann_return': 0, 'sharpe': 0,
                             'max_dd': 0, 'calmar': 0, 'sortino': 0}

                avg_eff_lev = effective_levs.mean()

                results.append({
                    'w_a': w_a, 'w_b': w_b, 'leverage': lev,
                    'dd_control': dd_win,
                    'full_return': m_full['total_return'],
                    'full_ann_return': m_full['ann_return'],
                    'full_sharpe': m_full['sharpe'],
                    'full_max_dd': m_full['max_dd'],
                    'full_calmar': m_full['calmar'],
                    'full_sortino': m_full['sortino'],
                    '12m_return': m_12m['total_return'],
                    '12m_ann_return': m_12m['ann_return'],
                    '12m_sharpe': m_12m['sharpe'],
                    '12m_max_dd': m_12m['max_dd'],
                    '12m_calmar': m_12m['calmar'],
                    '12m_sortino': m_12m['sortino'],
                    'avg_leverage': avg_eff_lev,
                    'equity_curve': eq_series,
                })

    return results


def monthly_returns_table(eq: pd.Series) -> str:
    """Build monthly returns markdown table from equity series."""
    monthly_eq = eq.resample('ME').last().dropna()
    monthly_rets = monthly_eq.pct_change().dropna()

    years = sorted(monthly_rets.index.year.unique())
    mnames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
              'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

    lines = ["| Year | " + " | ".join(mnames) + " | Annual |",
             "|------|" + "|".join(["-------"] * 12) + "|--------|"]

    for yr in years:
        row = [f"| {yr} "]
        yr_ret = 1.0
        for m in range(1, 13):
            mask = (monthly_rets.index.year == yr) & (monthly_rets.index.month == m)
            vals = monthly_rets[mask]
            if len(vals) > 0:
                r = vals.iloc[0]
                yr_ret *= (1 + r)
                row.append(f" {r*100:+.1f}% ")
            else:
                row.append("  --  ")
        row.append(f" {(yr_ret-1)*100:+.1f}% ")
        lines.append("|".join(row) + "|")

    return "\n".join(lines)


def pct(v): return f"{v*100:+.1f}%"
def pct2(v): return f"{v*100:.1f}%"
def f2(v): return f"{v:.2f}"


# ==============================================================================
# REPORT GENERATION
# ==============================================================================

def generate_report(
    ret_a: pd.Series,
    ret_b: pd.Series,
    results: List[dict],
    n_tokens_a: int,
    n_tokens_b: int,
    correlation: float,
) -> str:
    lines = []
    lines.append("# R170 -- Optimized Multi-Strategy Portfolio Results\n")
    lines.append(f"**Generated:** {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Period:** {SIM_START.date()} to {SIM_END.date()} (~2 years)")
    lines.append(f"**Last 12 Months:** {L12M_START.date()} to {SIM_END.date()}")
    lines.append(f"**Component A tokens (R162 momentum):** {n_tokens_a}")
    lines.append(f"**Component B tokens (R167-opt R160 vol breakout):** {n_tokens_b}")
    lines.append(f"**Total configs tested:** {len(results)}")
    lines.append("")

    # ── 1. Component standalone metrics ──
    lines.append("---\n")
    lines.append("## 1. Component Standalone Metrics (1x, No Leverage)\n")

    # Component A
    common = ret_a.index.intersection(ret_b.index)
    common = common[(common >= SIM_START) & (common <= SIM_END)].sort_values()

    for label, ret in [("A (R162 Momentum)", ret_a), ("B (R167-opt R160 Vol Breakout)", ret_b)]:
        full_ret = ret[(ret.index >= SIM_START) & (ret.index <= SIM_END)]
        m_full = compute_metrics(full_ret, "full")

        l12m_ret = ret[(ret.index >= L12M_START) & (ret.index <= SIM_END)]
        m_12m = compute_metrics(l12m_ret, "12m")

        lines.append(f"### Component {label}\n")
        lines.append(f"| Period | Return | Ann Return | Sharpe | MaxDD | Calmar | Sortino |")
        lines.append(f"|--------|--------|------------|--------|-------|--------|---------|")
        lines.append(f"| Full | {pct(m_full['total_return'])} | {pct(m_full['ann_return'])} | {f2(m_full['sharpe'])} | {pct(m_full['max_dd'])} | {f2(m_full['calmar'])} | {f2(m_full['sortino'])} |")
        lines.append(f"| 12M | {pct(m_12m['total_return'])} | {pct(m_12m['ann_return'])} | {f2(m_12m['sharpe'])} | {pct(m_12m['max_dd'])} | {f2(m_12m['calmar'])} | {f2(m_12m['sortino'])} |")
        lines.append("")

    # ── 2. Correlation ──
    lines.append("---\n")
    lines.append("## 2. Daily Return Correlation\n")
    ra_aligned = ret_a.reindex(common).fillna(0)
    rb_aligned = ret_b.reindex(common).fillna(0)
    corr = ra_aligned.corr(rb_aligned)
    lines.append(f"**Correlation (Component A vs B):** {corr:.4f}\n")
    lines.append(f"Interpretation: {'Very low' if abs(corr) < 0.1 else 'Low' if abs(corr) < 0.3 else 'Moderate' if abs(corr) < 0.5 else 'High'} correlation -- {'excellent' if abs(corr) < 0.2 else 'good' if abs(corr) < 0.4 else 'moderate'} diversification benefit.\n")

    # ── 3. All 224 configs ──
    lines.append("---\n")
    lines.append("## 3. ALL 224 Configurations (sorted by 12M Return)\n")

    sorted_all = sorted(results, key=lambda x: x['12m_return'], reverse=True)

    lines.append("| # | A:B | Lev | DD_ctrl | 12M Ret | 12M Sharpe | 12M MaxDD | 12M Calmar | 12M Sort | AvgLev | Full Ret | Full DD |")
    lines.append("|---|-----|-----|---------|---------|------------|-----------|------------|----------|--------|----------|---------|")

    for i, r in enumerate(sorted_all):
        dd_str = f"{r['dd_control']}d" if r['dd_control'] else "None"
        alloc = f"{int(r['w_a']*100)}:{int(r['w_b']*100)}"
        lines.append(
            f"| {i+1} | {alloc} | {r['leverage']:.1f}x | {dd_str} "
            f"| {pct(r['12m_return'])} | {f2(r['12m_sharpe'])} | {pct(r['12m_max_dd'])} "
            f"| {f2(r['12m_calmar'])} | {f2(r['12m_sortino'])} | {r['avg_leverage']:.2f} "
            f"| {pct(r['full_return'])} | {pct(r['full_max_dd'])} |"
        )

    # ── 4. Pareto frontier ──
    lines.append("\n---\n")
    lines.append("## 4. Pareto Frontier (Return vs MaxDD)\n")
    lines.append("Non-dominated configs: highest return for a given MaxDD level.\n")

    # Compute Pareto front
    pareto = []
    for r in sorted_all:
        ret_val = r['12m_return']
        dd_val = abs(r['12m_max_dd'])
        dominated = False
        for r2 in sorted_all:
            if r2 is r:
                continue
            if r2['12m_return'] >= ret_val and abs(r2['12m_max_dd']) <= dd_val:
                if r2['12m_return'] > ret_val or abs(r2['12m_max_dd']) < dd_val:
                    dominated = True
                    break
        if not dominated:
            pareto.append(r)

    pareto.sort(key=lambda x: x['12m_return'], reverse=True)

    lines.append("| # | A:B | Lev | DD_ctrl | 12M Ret | 12M MaxDD | 12M Sharpe | 12M Calmar | Full Ret |")
    lines.append("|---|-----|-----|---------|---------|-----------|------------|------------|----------|")

    for i, r in enumerate(pareto[:30]):
        dd_str = f"{r['dd_control']}d" if r['dd_control'] else "None"
        alloc = f"{int(r['w_a']*100)}:{int(r['w_b']*100)}"
        lines.append(
            f"| {i+1} | {alloc} | {r['leverage']:.1f}x | {dd_str} "
            f"| {pct(r['12m_return'])} | {pct(r['12m_max_dd'])} "
            f"| {f2(r['12m_sharpe'])} | {f2(r['12m_calmar'])} "
            f"| {pct(r['full_return'])} |"
        )

    # ── 5. Target zone configs ──
    lines.append("\n---\n")
    lines.append("## 5. Target Zone: 300%+ Return AND MaxDD < 25%\n")

    target = [r for r in sorted_all if r['12m_return'] >= 3.0 and r['12m_max_dd'] > -0.25]
    target.sort(key=lambda x: x['12m_return'], reverse=True)

    if target:
        lines.append(f"**{len(target)} configs meet target.**\n")
        lines.append("| # | A:B | Lev | DD_ctrl | 12M Ret | 12M Sharpe | 12M MaxDD | 12M Calmar | 12M Sort | AvgLev | Full Ret | Full DD |")
        lines.append("|---|-----|-----|---------|---------|------------|-----------|------------|----------|--------|----------|---------|")
        for i, r in enumerate(target):
            dd_str = f"{r['dd_control']}d" if r['dd_control'] else "None"
            alloc = f"{int(r['w_a']*100)}:{int(r['w_b']*100)}"
            lines.append(
                f"| {i+1} | {alloc} | {r['leverage']:.1f}x | {dd_str} "
                f"| **{pct(r['12m_return'])}** | {f2(r['12m_sharpe'])} | {pct(r['12m_max_dd'])} "
                f"| {f2(r['12m_calmar'])} | {f2(r['12m_sortino'])} | {r['avg_leverage']:.2f} "
                f"| {pct(r['full_return'])} | {pct(r['full_max_dd'])} |"
            )
    else:
        lines.append("*No configs meet 300%+ return with MaxDD < 25%.*\n")

        # Relaxed target
        lines.append("\n### Relaxed Target: 300%+ Return, any DD\n")
        target_relaxed = [r for r in sorted_all if r['12m_return'] >= 3.0]
        target_relaxed.sort(key=lambda x: abs(x['12m_max_dd']))
        if target_relaxed:
            lines.append(f"**{len(target_relaxed)} configs with 300%+ return (sorted by lowest DD):**\n")
            lines.append("| # | A:B | Lev | DD_ctrl | 12M Ret | 12M MaxDD | 12M Sharpe | 12M Calmar | AvgLev |")
            lines.append("|---|-----|-----|---------|---------|-----------|------------|------------|--------|")
            for i, r in enumerate(target_relaxed[:20]):
                dd_str = f"{r['dd_control']}d" if r['dd_control'] else "None"
                alloc = f"{int(r['w_a']*100)}:{int(r['w_b']*100)}"
                lines.append(
                    f"| {i+1} | {alloc} | {r['leverage']:.1f}x | {dd_str} "
                    f"| {pct(r['12m_return'])} | {pct(r['12m_max_dd'])} "
                    f"| {f2(r['12m_sharpe'])} | {f2(r['12m_calmar'])} | {r['avg_leverage']:.2f} |"
                )
        else:
            lines.append("*No configs reach 300%+ return at any DD level.*\n")

    # ── 6. Low-DD configs ──
    lines.append("\n---\n")
    lines.append("## 6. Low-DD Configs: MaxDD < 20% (sorted by return)\n")

    low_dd = [r for r in sorted_all if r['12m_max_dd'] > -0.20]
    low_dd.sort(key=lambda x: x['12m_return'], reverse=True)

    if low_dd:
        lines.append(f"**{len(low_dd)} configs with MaxDD < 20%.**\n")
        lines.append("| # | A:B | Lev | DD_ctrl | 12M Ret | 12M Sharpe | 12M MaxDD | 12M Calmar | AvgLev | Full Ret |")
        lines.append("|---|-----|-----|---------|---------|------------|-----------|------------|--------|----------|")
        for i, r in enumerate(low_dd[:30]):
            dd_str = f"{r['dd_control']}d" if r['dd_control'] else "None"
            alloc = f"{int(r['w_a']*100)}:{int(r['w_b']*100)}"
            lines.append(
                f"| {i+1} | {alloc} | {r['leverage']:.1f}x | {dd_str} "
                f"| {pct(r['12m_return'])} | {f2(r['12m_sharpe'])} | {pct(r['12m_max_dd'])} "
                f"| {f2(r['12m_calmar'])} | {r['avg_leverage']:.2f} | {pct(r['full_return'])} |"
            )
    else:
        lines.append("*No configs have MaxDD < 20%.*\n")

    # ── 7. Monthly returns for top 3 nearest target ──
    lines.append("\n---\n")
    lines.append("## 7. Monthly Returns for Top 3 Configs Near Target\n")
    lines.append("Configs closest to 300%+ return with lowest DD.\n")

    # Score: prioritize reaching 300% return, penalize DD
    scored = [(r, r['12m_return'] - 5 * max(0, abs(r['12m_max_dd']) - 0.20)) for r in sorted_all]
    scored.sort(key=lambda x: x[1], reverse=True)

    for rank, (r, score) in enumerate(scored[:3]):
        dd_str = f"{r['dd_control']}d" if r['dd_control'] else "None"
        alloc = f"{int(r['w_a']*100)}:{int(r['w_b']*100)}"
        lines.append(f"### Config #{rank+1}: {alloc} @ {r['leverage']:.1f}x, DD_ctrl={dd_str}")
        lines.append(f"- 12M Return: {pct(r['12m_return'])}")
        lines.append(f"- 12M MaxDD: {pct(r['12m_max_dd'])}")
        lines.append(f"- 12M Sharpe: {f2(r['12m_sharpe'])}")
        lines.append(f"- 12M Calmar: {f2(r['12m_calmar'])}")
        lines.append(f"- Avg Effective Leverage: {r['avg_leverage']:.2f}")
        lines.append("")
        if 'equity_curve' in r and len(r['equity_curve']) > 0:
            lines.append(monthly_returns_table(r['equity_curve']))
        lines.append("")

    # ── 8. Comparison with old R164 ──
    lines.append("\n---\n")
    lines.append("## 8. Comparison with Old R164 Baseline Portfolio\n")
    lines.append("R164 used the OLD R160 parameters (max_pos=10, mom_days=14, trail_sma=20).\n")
    lines.append("R170 uses R167-OPTIMIZED parameters (max_pos=5, mom_days=7, trail_sma=15).\n")

    lines.append("### R164 Winner (Config B: 70:30 @ 1.5x Static)")
    lines.append("| Metric | R164 Value |")
    lines.append("|--------|-----------|")
    lines.append("| 12M Return | +346.3% |")
    lines.append("| 12M MaxDD | -49.3% |")
    lines.append("| 12M Sharpe | 1.83 |")
    lines.append("| 12M Calmar | 7.03 |")
    lines.append("| Avg Leverage | 1.50x |")
    lines.append("")

    # Find closest comparable config: 70:30, 1.5x, no DD control
    comparable = [r for r in results if r['w_a'] == 0.70 and r['w_b'] == 0.30
                  and r['leverage'] == 1.5 and r['dd_control'] is None]
    if comparable:
        r = comparable[0]
        lines.append("### R170 Comparable (70:30 @ 1.5x, No DD Control)")
        lines.append("| Metric | R170 Value | vs R164 |")
        lines.append("|--------|-----------|---------|")
        lines.append(f"| 12M Return | {pct(r['12m_return'])} | {pct(r['12m_return'] - 3.463)} |")
        lines.append(f"| 12M MaxDD | {pct(r['12m_max_dd'])} | {pct(r['12m_max_dd'] - (-0.493))} |")
        lines.append(f"| 12M Sharpe | {f2(r['12m_sharpe'])} | {f2(r['12m_sharpe'] - 1.83)} |")
        lines.append(f"| 12M Calmar | {f2(r['12m_calmar'])} | {f2(r['12m_calmar'] - 7.03)} |")
        lines.append(f"| Avg Leverage | {r['avg_leverage']:.2f} | -- |")
    else:
        lines.append("*No exact comparable config found.*")

    lines.append("")

    # Best overall R170 config
    best = sorted_all[0]
    dd_str = f"{best['dd_control']}d" if best['dd_control'] else "None"
    alloc = f"{int(best['w_a']*100)}:{int(best['w_b']*100)}"
    lines.append(f"### R170 Best Overall: {alloc} @ {best['leverage']:.1f}x, DD_ctrl={dd_str}")
    lines.append("| Metric | R170 Best | R164 Winner |")
    lines.append("|--------|-----------|------------|")
    lines.append(f"| 12M Return | {pct(best['12m_return'])} | +346.3% |")
    lines.append(f"| 12M MaxDD | {pct(best['12m_max_dd'])} | -49.3% |")
    lines.append(f"| 12M Sharpe | {f2(best['12m_sharpe'])} | 1.83 |")
    lines.append(f"| 12M Calmar | {f2(best['12m_calmar'])} | 7.03 |")

    # ── Summary ──
    lines.append("\n---\n")
    lines.append("## Summary\n")

    # Count configs meeting various thresholds
    ret_300 = len([r for r in results if r['12m_return'] >= 3.0])
    ret_200 = len([r for r in results if r['12m_return'] >= 2.0])
    dd_20 = len([r for r in results if r['12m_max_dd'] > -0.20])
    dd_25 = len([r for r in results if r['12m_max_dd'] > -0.25])
    both = len([r for r in results if r['12m_return'] >= 3.0 and r['12m_max_dd'] > -0.25])
    calmar_3 = len([r for r in results if r['12m_calmar'] > 3.0])

    lines.append(f"- Configs with 300%+ 12M return: **{ret_300}/{len(results)}**")
    lines.append(f"- Configs with 200%+ 12M return: **{ret_200}/{len(results)}**")
    lines.append(f"- Configs with MaxDD < 20%: **{dd_20}/{len(results)}**")
    lines.append(f"- Configs with MaxDD < 25%: **{dd_25}/{len(results)}**")
    lines.append(f"- Configs meeting target (300%+ AND DD < 25%): **{both}/{len(results)}**")
    lines.append(f"- Configs with Calmar > 3: **{calmar_3}/{len(results)}**")
    lines.append(f"- Correlation (A vs B): **{corr:.4f}**")
    lines.append("")

    return "\n".join(lines)


# ==============================================================================
# MAIN
# ==============================================================================

def main():
    t0 = time.time()

    log("=" * 70)
    log("R170: Optimized Multi-Strategy Portfolio")
    log("Component A: R162 Momentum Rotation")
    log("Component B: R167-Optimized R160 Vol Breakout")
    log("Sweep: 7 allocations x 8 leverages x 4 DD controls = 224 configs")
    log("=" * 70)

    # Load data
    log("\n[1/5] Loading universe...")
    tokens = load_universe()
    log(f"  Loaded {len(tokens)} tokens")

    # Generate Component A returns
    log("\n[2/5] Generating Component A (R162 Momentum) daily returns...")
    t1 = time.time()
    ret_a = generate_component_a_returns(tokens)
    log(f"  Component A: {len(ret_a)} daily observations, {time.time()-t1:.1f}s")
    if len(ret_a) > 0:
        eq_a = (1 + ret_a).cumprod()
        log(f"  A total return (full): {pct(eq_a.iloc[-1] - 1)}")
        a_12m = ret_a[ret_a.index >= L12M_START]
        if len(a_12m) > 0:
            eq_a_12m = (1 + a_12m).cumprod()
            log(f"  A 12M return: {pct(eq_a_12m.iloc[-1] - 1)}")

    # Generate Component B returns
    log("\n[3/5] Generating Component B (R167-opt R160 Vol Breakout) daily returns...")
    t2 = time.time()
    ret_b = generate_component_b_returns(tokens)
    log(f"  Component B: {len(ret_b)} daily observations, {time.time()-t2:.1f}s")
    if len(ret_b) > 0:
        eq_b = (1 + ret_b).cumprod()
        log(f"  B total return (full): {pct(eq_b.iloc[-1] - 1)}")
        b_12m = ret_b[ret_b.index >= L12M_START]
        if len(b_12m) > 0:
            eq_b_12m = (1 + b_12m).cumprod()
            log(f"  B 12M return: {pct(eq_b_12m.iloc[-1] - 1)}")

    # Correlation
    common = ret_a.index.intersection(ret_b.index)
    corr = ret_a.reindex(common).fillna(0).corr(ret_b.reindex(common).fillna(0))
    log(f"\n  Daily return correlation: {corr:.4f}")

    # Run sweep
    log("\n[4/5] Running 224-config portfolio sweep...")
    t3 = time.time()
    results = run_portfolio_sweep(ret_a, ret_b)
    log(f"  Sweep complete: {len(results)} configs, {time.time()-t3:.1f}s")

    # Generate report
    log("\n[5/5] Generating report...")
    report = generate_report(ret_a, ret_b, results, len(tokens), len(tokens), corr)

    with open(OUTPUT_MD, 'w') as f:
        f.write(report)
    log(f"\nReport saved to {OUTPUT_MD}")

    # Print report
    log("\n" + "=" * 70)
    log("FULL RESULTS")
    log("=" * 70)
    print(report)

    t_total = time.time() - t0
    log(f"\nTotal runtime: {t_total:.1f}s")


if __name__ == '__main__':
    main()
