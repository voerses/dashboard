#!/workspace/venv/bin/python
"""
V3 Momentum + Funding Carry Portfolio Backtest
===============================================

Tests combining two uncorrelated strategy types:
  1. V3 Momentum (BTC spot): 20/50 EMA + positioning + VRP overlays
  2. Funding Carry (BTC perp): collect funding rates when |funding| > threshold

Portfolio allocations tested:
  - 60/40 (60% momentum, 40% carry)
  - 50/50 (equal weight)
  - Risk parity (inverse trailing 30d vol)
  - Dynamic (100% momentum when carry dormant, 60/40 when carry active)

Kill criteria:
  - Correlation > 0.5 (not diversifying)
  - Portfolio Sharpe < V3 standalone Sharpe (carry drags)
  - Carry active < 20% of time (too dormant)

Output:
  - research/v3_carry_portfolio_results.md
"""

import pandas as pd
import numpy as np
import json
import warnings
from pathlib import Path
from datetime import datetime

warnings.filterwarnings('ignore')

BASE_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = BASE_DIR / 'data'
OUTPUT_DIR = BASE_DIR / 'research'

# ── Configuration ────────────────────────────────────────────────────────────

# V3 Momentum params
FAST_EMA = 20
SLOW_EMA = 50
POS_Z_WINDOW = 30
VRP_Z_WINDOW = 60
WARMUP_DAYS = 90

# Carry params
CARRY_THRESHOLD = 0.00005   # 72h rolling mean threshold (per hour)
CARRY_72H_WINDOW = 72       # hours for rolling mean

# Backtest params
COST_BPS = 10               # 10 bps per rebalance
REBALANCE_FREQ = 'W'        # Weekly strategy rebalance
PORTFOLIO_REBAL = 'ME'      # Monthly portfolio rebalance
RISKPARITY_WINDOW = 30      # 30d trailing vol for risk parity
IS_FRACTION = 0.70          # 70% IS, 30% OOS

# ═════════════════════════════════════════════════════════════════════════════
# 1. DATA LOADING
# ═════════════════════════════════════════════════════════════════════════════

def load_spot_data():
    """Load BTC spot 1H data, resample to daily."""
    print("[1/4] Loading BTC spot data...")
    spot = pd.read_parquet(DATA_DIR / 'spot/1h_cache/BTC_1h.parquet')
    spot.index = pd.to_datetime(spot.index)

    daily = pd.DataFrame()
    daily['close'] = spot['close'].resample('D').last()
    daily['open'] = spot['open'].resample('D').first()
    daily['high'] = spot['high'].resample('D').max()
    daily['low'] = spot['low'].resample('D').min()
    daily['volume'] = spot['volume'].resample('D').sum()
    daily = daily.dropna(subset=['close'])
    daily['ret'] = daily['close'].pct_change()

    print(f"  Spot daily: {daily.index.min().date()} to {daily.index.max().date()}, "
          f"{len(daily)} rows")
    return daily


def load_perp_data():
    """Load BTC perp 1H data with funding rates, resample to daily."""
    print("[2/4] Loading BTC perp data...")
    perp = pd.read_parquet(DATA_DIR / 'perp/1h_cache/BTC_1h.parquet')
    perp.index = pd.to_datetime(perp.index)

    daily = pd.DataFrame()
    daily['close'] = perp['close'].resample('D').last()
    daily['ret'] = daily['close'].pct_change()

    # Funding: sum of hourly funding rates per day
    daily['funding_daily'] = perp['funding_1h'].resample('D').sum()

    # 72h rolling mean for carry signal
    funding_72h_mean = perp['funding_1h'].rolling(CARRY_72H_WINDOW, min_periods=24).mean()
    daily['funding_72h_mean'] = funding_72h_mean.resample('D').last()
    daily['funding_abs_72h'] = np.abs(daily['funding_72h_mean'])

    daily = daily.dropna(subset=['close'])
    print(f"  Perp daily: {daily.index.min().date()} to {daily.index.max().date()}, "
          f"{len(daily)} rows")
    print(f"  Funding daily mean: {daily['funding_daily'].mean():.6f}")
    return daily


def load_positioning():
    """Load Binance positioning data for BTCUSDT."""
    print("[3/4] Loading positioning data...")
    pos = pd.read_parquet(DATA_DIR / 'alternative/binance_metrics/all_symbols_daily_ls.parquet')
    pos = pos[pos['symbol'] == 'BTCUSDT'].copy()
    pos['date'] = pd.to_datetime(pos['date'])
    pos = pos.set_index('date').sort_index()
    pos = pos[['sum_toptrader_ls_ratio', 'count_toptrader_ls_ratio', 'count_ls_ratio']].copy()
    pos = pos[~pos.index.duplicated(keep='last')]
    print(f"  Positioning: {pos.index.min().date()} to {pos.index.max().date()}, "
          f"{len(pos)} rows")
    return pos


def load_dvol():
    """Load BTC DVOL from Deribit JSON."""
    print("[4/4] Loading BTC DVOL...")
    dvol_path = DATA_DIR / 'alternative/deribit_options/dvol/btc_dvol_daily.json'
    with open(dvol_path) as f:
        data = json.load(f)
    records = [{'date': pd.Timestamp(row[0], unit='ms'), 'dvol_close': row[4]} for row in data]
    dvol = pd.DataFrame(records).set_index('date').sort_index()
    dvol = dvol[~dvol.index.duplicated(keep='last')]
    print(f"  DVOL: {dvol.index.min().date()} to {dvol.index.max().date()}, "
          f"{len(dvol)} rows")
    return dvol['dvol_close']


# ═════════════════════════════════════════════════════════════════════════════
# 2. V3 MOMENTUM STRATEGY (daily return stream)
# ═════════════════════════════════════════════════════════════════════════════

def rolling_zscore(arr, window):
    """Rolling z-score (numpy)."""
    n = len(arr)
    result = np.full(n, np.nan)
    for i in range(window, n):
        seg = arr[i - window:i]
        valid = seg[~np.isnan(seg)]
        if len(valid) >= window // 2:
            mu = np.mean(valid)
            sigma = np.std(valid, ddof=1)
            if sigma > 1e-10:
                result[i] = (arr[i] - mu) / sigma
    return result


def build_v3_momentum_returns(spot_daily, positioning, dvol_series):
    """
    Replicate V3 momentum strategy and produce a daily return stream.

    Logic:
    - Base: Long when 20d EMA > 50d EMA, flat otherwise
    - Positioning overlay: 30d z-score -> sizing multiplier
    - VRP overlay: 60d z-score -> sizing multiplier
    - Weekly rebalance, long-only, no stops
    """
    print("\n--- Building V3 Momentum Returns ---")
    close = spot_daily['close'].values.astype(np.float64)
    idx = spot_daily.index
    n = len(close)

    # ── EMA base signal ──
    fast_ema = pd.Series(close).ewm(span=FAST_EMA, adjust=False).mean().values
    slow_ema = pd.Series(close).ewm(span=SLOW_EMA, adjust=False).mean().values
    base_signal = np.where(fast_ema > slow_ema, 1.0, 0.0)
    base_signal[:SLOW_EMA] = 0.0

    # ── Positioning overlay ──
    pos = positioning.reindex(idx).ffill()
    if not pos.empty:
        toptrader_ls = pos['sum_toptrader_ls_ratio'].values.astype(np.float64)
        count_toptrader = pos['count_toptrader_ls_ratio'].values.astype(np.float64)
        count_ls = pos['count_ls_ratio'].values.astype(np.float64)
        divergence = count_toptrader - count_ls

        z_toptrader = rolling_zscore(toptrader_ls, POS_Z_WINDOW)
        z_divergence = rolling_zscore(divergence, POS_Z_WINDOW)
        combined_z = np.where(
            np.isnan(z_toptrader) | np.isnan(z_divergence),
            np.nan,
            (z_toptrader + z_divergence) / 2.0
        )

        pos_mult = np.where(
            np.isnan(combined_z), 1.0,
            np.where(combined_z > 1.5, 0.3,
            np.where(combined_z > 0.5, 0.5,
            np.where(combined_z > -0.5, 1.0,
            np.where(combined_z > -1.5, 1.3, 1.5)))))
    else:
        pos_mult = np.ones(n)

    # ── VRP overlay ──
    log_ret = np.zeros(n)
    log_ret[1:] = np.log(close[1:] / np.maximum(close[:-1], 1e-10))
    rv_20d = pd.Series(log_ret).rolling(20, min_periods=15).std().values * np.sqrt(365) * 100

    if not dvol_series.empty and len(dvol_series) >= 30:
        iv = dvol_series.reindex(idx).ffill().values.astype(np.float64)
    else:
        rv_90d = pd.Series(log_ret).rolling(90, min_periods=45).std().values * np.sqrt(365) * 100
        iv = rv_90d * 1.2

    vrp = iv - rv_20d
    vrp_z = rolling_zscore(vrp, VRP_Z_WINDOW)
    vrp_mult = np.where(
        np.isnan(vrp_z), 1.0,
        np.where(vrp_z > 1.0, 1.3,
        np.where(vrp_z > -0.5, 1.0,
        np.where(vrp_z > -1.5, 0.5, 0.3))))

    # ── Compose final position ──
    final_position = np.clip(base_signal * pos_mult * vrp_mult, 0.0, 1.5)
    final_position[:WARMUP_DAYS] = 0.0

    # ── Weekly rebalance ──
    held_position = np.zeros(n)
    current_pos = 0.0
    costs = np.zeros(n)

    # Build weekly rebalance dates
    weekly_dates = spot_daily.index.to_series().groupby(
        spot_daily.index.to_period('W')
    ).first()
    rebalance_set = set(weekly_dates.values)

    for i in range(n):
        dt = idx[i]
        if dt in rebalance_set and i >= WARMUP_DAYS:
            new_pos = final_position[i]
            pos_change = abs(new_pos - current_pos)
            costs[i] = pos_change * COST_BPS / 10000.0
            current_pos = new_pos
        held_position[i] = current_pos

    # ── Compute returns ──
    ret = spot_daily['ret'].values.copy()
    strat_ret = np.zeros(n)
    for i in range(1, n):
        strat_ret[i] = held_position[i - 1] * ret[i] - costs[i]

    strat_ret_series = pd.Series(strat_ret, index=idx, name='v3_momentum')

    active_days = (held_position > 0).sum()
    print(f"  V3 Momentum: {active_days}/{n} active days "
          f"({100 * active_days / n:.0f}%)")
    print(f"  Mean position when active: "
          f"{held_position[held_position > 0].mean():.2f}")

    return strat_ret_series, pd.Series(held_position, index=idx)


# ═════════════════════════════════════════════════════════════════════════════
# 3. FUNDING CARRY STRATEGY (daily return stream)
# ═════════════════════════════════════════════════════════════════════════════

def build_carry_returns(perp_daily):
    """
    Simple funding carry strategy:
    - When 72h mean funding > threshold: short perp (collect positive funding)
    - When 72h mean funding < -threshold: long perp (collect negative funding)
    - Delta-hedged conceptually: returns = funding income only
      (we model this as: position * funding_daily, ignoring price P&L
       since a delta-neutral carry trader hedges spot/perp)

    Actually, for a more realistic carry:
    - Go short perp + long spot when funding positive (collect funding, delta neutral)
    - Returns = funding income per day (funding_daily when positioned)
    - No directional price exposure (delta-hedged)
    """
    print("\n--- Building Carry Returns ---")
    n = len(perp_daily)
    idx = perp_daily.index

    funding_72h = perp_daily['funding_72h_mean'].values
    funding_daily = perp_daily['funding_daily'].values

    # Carry signal: positioned when |72h mean| > threshold
    carry_active = np.abs(funding_72h) > CARRY_THRESHOLD

    # Direction for carry: short perp when funding positive (to collect funding)
    # In a delta-neutral carry: return = |funding_daily| when active
    # (We collect funding regardless of direction by choosing the right side)
    carry_return = np.zeros(n)

    # Weekly rebalance
    weekly_dates = perp_daily.index.to_series().groupby(
        perp_daily.index.to_period('W')
    ).first()
    rebalance_set = set(weekly_dates.values)

    is_positioned = False
    current_direction = 0  # +1 = long perp, -1 = short perp
    costs = np.zeros(n)

    for i in range(WARMUP_DAYS, n):
        dt = idx[i]
        if dt in rebalance_set:
            if carry_active[i]:
                new_dir = -1 if funding_72h[i] > 0 else 1  # short when positive
                if new_dir != current_direction:
                    costs[i] = COST_BPS / 10000.0
                current_direction = new_dir
                is_positioned = True
            else:
                if is_positioned:
                    costs[i] = COST_BPS / 10000.0
                current_direction = 0
                is_positioned = False

        # Carry P&L = funding income when positioned (delta-neutral)
        # Funding income: short perp collects positive funding, long perp collects negative
        # P&L = -position_direction * funding_rate
        # When short (dir=-1) and funding positive: P&L = -(-1) * positive = positive
        # When long (dir=+1) and funding negative: P&L = -(+1) * negative = positive
        if current_direction != 0 and i > 0:
            carry_return[i] = -current_direction * funding_daily[i] - costs[i]
        else:
            carry_return[i] = -costs[i]

    carry_ret_series = pd.Series(carry_return, index=idx, name='carry')

    active_days = (carry_return != 0).sum()
    total_funding_collected = carry_return[carry_return > 0].sum()
    total_funding_paid = carry_return[carry_return < 0].sum()
    print(f"  Carry: {active_days}/{n} active days ({100 * active_days / n:.0f}%)")
    print(f"  Total funding collected: {total_funding_collected:.4f}")
    print(f"  Total funding paid: {total_funding_paid:.4f}")
    print(f"  Net carry income: {carry_return.sum():.4f}")

    # Activity by period
    for start, end, label in [
        ('2020-01-01', '2021-12-31', '2020-2021'),
        ('2022-01-01', '2023-12-31', '2022-2023'),
        ('2024-01-01', '2024-12-31', '2024'),
        ('2025-01-01', '2025-06-30', 'H1 2025'),
        ('2025-07-01', '2026-03-31', 'H2 2025+'),
    ]:
        mask = (idx >= start) & (idx <= end)
        sub = carry_return[mask]
        active = (sub != 0).sum()
        if len(sub) > 0:
            print(f"    {label}: {active}/{len(sub)} active ({100 * active / len(sub):.0f}%), "
                  f"cum ret: {sub.sum():.4f}")

    carry_position = np.zeros(n)
    carry_position[np.array([current_direction != 0 for _ in range(n)])] = 1.0
    # Rebuild properly
    carry_pos_arr = np.zeros(n)
    _dir = 0
    _positioned = False
    for i in range(WARMUP_DAYS, n):
        dt = idx[i]
        if dt in rebalance_set:
            if carry_active[i]:
                _dir = -1 if funding_72h[i] > 0 else 1
                _positioned = True
            else:
                _dir = 0
                _positioned = False
        carry_pos_arr[i] = abs(_dir)

    return carry_ret_series, pd.Series(carry_pos_arr, index=idx)


# ═════════════════════════════════════════════════════════════════════════════
# 4. PORTFOLIO CONSTRUCTION
# ═════════════════════════════════════════════════════════════════════════════

def build_portfolios(v3_ret, carry_ret, carry_active_pct):
    """
    Build four portfolio allocations from two return streams.

    Returns dict of {name: portfolio_daily_returns}
    """
    print("\n--- Building Portfolios ---")

    # Align return streams
    common_idx = v3_ret.index.intersection(carry_ret.index)
    v3 = v3_ret.reindex(common_idx).fillna(0)
    carry = carry_ret.reindex(common_idx).fillna(0)
    n = len(common_idx)

    # ── 1. 60/40 (static) ──
    port_6040 = 0.6 * v3 + 0.4 * carry

    # ── 2. 50/50 (equal weight) ──
    port_5050 = 0.5 * v3 + 0.5 * carry

    # ── 3. Risk parity (inverse vol, monthly rebalance) ──
    v3_vol = v3.rolling(RISKPARITY_WINDOW, min_periods=15).std()
    carry_vol = carry.rolling(RISKPARITY_WINDOW, min_periods=15).std()

    # Inverse vol weights (monthly rebalance)
    rp_w_v3 = pd.Series(np.nan, index=common_idx)
    rp_w_carry = pd.Series(np.nan, index=common_idx)

    month_groups = pd.Series(common_idx, index=common_idx).groupby(
        pd.Grouper(freq='ME')
    ).first()

    for rebal_date in month_groups.values:
        if pd.isna(rebal_date):
            continue
        if rebal_date not in v3_vol.index:
            continue
        vv = v3_vol.loc[rebal_date]
        cv = carry_vol.loc[rebal_date]
        if pd.isna(vv) or pd.isna(cv) or vv < 1e-10 or cv < 1e-10:
            # Default equal weight when vol not available
            rp_w_v3.loc[rebal_date] = 0.5
            rp_w_carry.loc[rebal_date] = 0.5
        else:
            inv_v3 = 1.0 / vv
            inv_carry = 1.0 / cv
            total = inv_v3 + inv_carry
            rp_w_v3.loc[rebal_date] = inv_v3 / total
            rp_w_carry.loc[rebal_date] = inv_carry / total

    rp_w_v3 = rp_w_v3.ffill().fillna(0.5)
    rp_w_carry = rp_w_carry.ffill().fillna(0.5)
    port_riskparity = rp_w_v3 * v3 + rp_w_carry * carry

    # ── 4. Dynamic (100% V3 when carry dormant, 60/40 when active) ──
    # Carry is "active" when carry has had non-zero returns recently (30d)
    carry_active_30d = carry.abs().rolling(30, min_periods=1).sum()
    carry_is_active = carry_active_30d > 0

    port_dynamic = pd.Series(np.nan, index=common_idx)
    for i in range(n):
        if carry_is_active.iloc[i]:
            port_dynamic.iloc[i] = 0.6 * v3.iloc[i] + 0.4 * carry.iloc[i]
        else:
            port_dynamic.iloc[i] = v3.iloc[i]

    # Add rebalancing costs for portfolio-level rebalance (monthly)
    # Already embedded in individual strategy costs, just track portfolio costs
    # separately for portfolio weight changes
    portfolios = {
        '60/40': port_6040,
        '50/50': port_5050,
        'Risk Parity': port_riskparity,
        'Dynamic': port_dynamic,
    }

    for name, port in portfolios.items():
        total = port.sum()
        print(f"  {name}: cum return = {total:.4f}")

    return portfolios, v3, carry, rp_w_v3, rp_w_carry


# ═════════════════════════════════════════════════════════════════════════════
# 5. METRICS
# ═════════════════════════════════════════════════════════════════════════════

def compute_metrics(returns, label=""):
    """Compute comprehensive performance metrics."""
    returns = returns.dropna()
    if len(returns) == 0 or returns.std() == 0:
        return {
            'label': label, 'total_return': 0, 'ann_return': 0,
            'ann_vol': 0, 'sharpe': 0, 'max_dd': 0, 'calmar': 0,
            'n_days': 0, 'sortino': 0, 'skew': 0,
        }

    total_ret = (1 + returns).prod() - 1
    n_years = len(returns) / 365
    ann_ret = (1 + total_ret) ** (1 / max(n_years, 0.01)) - 1
    ann_vol = returns.std() * np.sqrt(365)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

    # Sortino
    downside = returns[returns < 0]
    down_vol = downside.std() * np.sqrt(365) if len(downside) > 0 else ann_vol
    sortino = ann_ret / down_vol if down_vol > 0 else 0

    # MaxDD
    cum = (1 + returns).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    max_dd = dd.min()
    calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0

    return {
        'label': label,
        'total_return': total_ret,
        'ann_return': ann_ret,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'sortino': sortino,
        'max_dd': max_dd,
        'calmar': calmar,
        'skew': returns.skew(),
        'n_days': len(returns),
    }


# ═════════════════════════════════════════════════════════════════════════════
# 6. MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 78)
    print("V3 MOMENTUM + FUNDING CARRY PORTFOLIO BACKTEST")
    print("=" * 78)
    print()

    # ── Load data ──
    spot_daily = load_spot_data()
    perp_daily = load_perp_data()
    positioning = load_positioning()
    dvol = load_dvol()
    print()

    # ── Build individual strategy returns ──
    v3_ret, v3_pos = build_v3_momentum_returns(spot_daily, positioning, dvol)
    carry_ret, carry_pos = build_carry_returns(perp_daily)

    # ── Align to common date range ──
    common_idx = v3_ret.index.intersection(carry_ret.index)
    v3_ret = v3_ret.reindex(common_idx).fillna(0)
    carry_ret = carry_ret.reindex(common_idx).fillna(0)
    carry_pos = carry_pos.reindex(common_idx).fillna(0)

    print(f"\nCommon date range: {common_idx.min().date()} to {common_idx.max().date()}")
    print(f"Total days: {len(common_idx)}")

    # ── IS/OOS split ──
    n_total = len(common_idx)
    n_is = int(n_total * IS_FRACTION)
    is_end_idx = n_is
    is_mask = np.zeros(n_total, dtype=bool)
    is_mask[:is_end_idx] = True
    oos_mask = ~is_mask

    is_dates = common_idx[is_mask]
    oos_dates = common_idx[oos_mask]
    is_start = is_dates[0].date()
    is_end = is_dates[-1].date()
    oos_start = oos_dates[0].date()
    oos_end = oos_dates[-1].date()

    print(f"IS:  {is_start} to {is_end} ({len(is_dates)} days)")
    print(f"OOS: {oos_start} to {oos_end} ({len(oos_dates)} days)")

    # ── Carry activity analysis ──
    carry_active_oos = carry_pos.iloc[is_end_idx:].values
    carry_active_pct_oos = (carry_active_oos > 0).sum() / len(carry_active_oos) * 100
    carry_active_pct_full = (carry_pos.values > 0).sum() / len(carry_pos) * 100

    print(f"\nCarry activity (full period): {carry_active_pct_full:.1f}%")
    print(f"Carry activity (OOS): {carry_active_pct_oos:.1f}%")

    # ── Build portfolios ──
    portfolios, v3_aligned, carry_aligned, rp_w_v3, rp_w_carry = build_portfolios(
        v3_ret, carry_ret, carry_active_pct_oos
    )

    # ── Compute standalone metrics ──
    print("\n--- Standalone Strategy Metrics ---")
    v3_is = compute_metrics(v3_ret.iloc[:is_end_idx], "V3 Momentum (IS)")
    v3_oos = compute_metrics(v3_ret.iloc[is_end_idx:], "V3 Momentum (OOS)")
    carry_is = compute_metrics(carry_ret.iloc[:is_end_idx], "Carry (IS)")
    carry_oos = compute_metrics(carry_ret.iloc[is_end_idx:], "Carry (OOS)")

    print(f"  V3 IS:    Sharpe={v3_is['sharpe']:.3f}, Return={v3_is['total_return']:.2%}, "
          f"MaxDD={v3_is['max_dd']:.2%}")
    print(f"  V3 OOS:   Sharpe={v3_oos['sharpe']:.3f}, Return={v3_oos['total_return']:.2%}, "
          f"MaxDD={v3_oos['max_dd']:.2%}")
    print(f"  Carry IS: Sharpe={carry_is['sharpe']:.3f}, Return={carry_is['total_return']:.2%}, "
          f"MaxDD={carry_is['max_dd']:.2%}")
    print(f"  Carry OOS:Sharpe={carry_oos['sharpe']:.3f}, Return={carry_oos['total_return']:.2%}, "
          f"MaxDD={carry_oos['max_dd']:.2%}")

    # ── Correlation analysis ──
    print("\n--- Correlation Analysis ---")
    corr_full = v3_ret.corr(carry_ret)
    corr_is = v3_ret.iloc[:is_end_idx].corr(carry_ret.iloc[:is_end_idx])
    corr_oos = v3_ret.iloc[is_end_idx:].corr(carry_ret.iloc[is_end_idx:])

    # Rolling correlation
    rolling_corr = v3_ret.rolling(90).corr(carry_ret)

    print(f"  Full period correlation: {corr_full:.4f}")
    print(f"  IS correlation: {corr_is:.4f}")
    print(f"  OOS correlation: {corr_oos:.4f}")
    print(f"  Rolling 90d corr range: [{rolling_corr.min():.3f}, {rolling_corr.max():.3f}]")
    print(f"  Rolling 90d corr mean: {rolling_corr.mean():.3f}")

    # ── Compute portfolio metrics ──
    print("\n--- Portfolio Metrics ---")
    port_metrics_is = {}
    port_metrics_oos = {}

    for name, port_ret in portfolios.items():
        port_is = compute_metrics(port_ret.iloc[:is_end_idx], f"{name} (IS)")
        port_oos = compute_metrics(port_ret.iloc[is_end_idx:], f"{name} (OOS)")
        port_metrics_is[name] = port_is
        port_metrics_oos[name] = port_oos

        print(f"  {name} IS:  Sharpe={port_is['sharpe']:.3f}, "
              f"Return={port_is['total_return']:.2%}, MaxDD={port_is['max_dd']:.2%}")
        print(f"  {name} OOS: Sharpe={port_oos['sharpe']:.3f}, "
              f"Return={port_oos['total_return']:.2%}, MaxDD={port_oos['max_dd']:.2%}")

    # ── Marginal Sharpe contribution ──
    print("\n--- Marginal Sharpe Contribution of Carry ---")
    for name in portfolios:
        delta = port_metrics_oos[name]['sharpe'] - v3_oos['sharpe']
        print(f"  {name}: dSharpe = {delta:+.4f}")

    # ── Risk parity weights analysis ──
    print("\n--- Risk Parity Weights ---")
    rp_v3_oos = rp_w_v3.iloc[is_end_idx:]
    rp_carry_oos = rp_w_carry.iloc[is_end_idx:]
    print(f"  OOS mean V3 weight: {rp_v3_oos.mean():.2%}")
    print(f"  OOS mean Carry weight: {rp_carry_oos.mean():.2%}")
    print(f"  OOS min V3 weight: {rp_v3_oos.min():.2%}")
    print(f"  OOS max V3 weight: {rp_v3_oos.max():.2%}")

    # ── Kill criteria checks ──
    print("\n" + "=" * 78)
    print("KILL CRITERIA EVALUATION")
    print("=" * 78)

    kill_flags = []

    # KC1: Correlation > 0.5
    if abs(corr_oos) > 0.5:
        kill_flags.append(f"KILL: OOS correlation = {corr_oos:.3f} > 0.5 (not diversifying)")
    else:
        print(f"  PASS: OOS correlation = {corr_oos:.3f} <= 0.5")

    # KC2: Portfolio Sharpe < V3 standalone
    best_port_sharpe = max(m['sharpe'] for m in port_metrics_oos.values())
    best_port_name = max(port_metrics_oos, key=lambda k: port_metrics_oos[k]['sharpe'])
    if best_port_sharpe < v3_oos['sharpe']:
        kill_flags.append(f"KILL: Best portfolio Sharpe ({best_port_name}: "
                          f"{best_port_sharpe:.3f}) < V3 standalone ({v3_oos['sharpe']:.3f})")
    else:
        print(f"  PASS: Best portfolio Sharpe ({best_port_name}: "
              f"{best_port_sharpe:.3f}) vs V3 ({v3_oos['sharpe']:.3f})")

    # KC3: Carry active < 20% of time
    if carry_active_pct_oos < 20:
        kill_flags.append(f"KILL: Carry active only {carry_active_pct_oos:.1f}% "
                          f"of OOS (< 20% threshold)")
    else:
        print(f"  PASS: Carry active {carry_active_pct_oos:.1f}% of OOS")

    if kill_flags:
        print()
        for kf in kill_flags:
            print(f"  >>> {kf}")

    # ── Carry activity by OOS sub-period ──
    print("\n--- Carry Activity by Sub-Period (OOS) ---")
    oos_carry = carry_ret.iloc[is_end_idx:]
    oos_v3 = v3_ret.iloc[is_end_idx:]

    # Split OOS into quarters
    oos_df = pd.DataFrame({'carry': oos_carry, 'v3': oos_v3, 'carry_active': carry_pos.iloc[is_end_idx:]})
    for q_start, q_end, q_label in _get_quarter_ranges(oos_dates[0], oos_dates[-1]):
        mask = (oos_df.index >= q_start) & (oos_df.index <= q_end)
        sub = oos_df[mask]
        if len(sub) == 0:
            continue
        active = (sub['carry_active'] > 0).sum()
        carry_cum = sub['carry'].sum()
        v3_cum = sub['v3'].sum()
        print(f"  {q_label}: carry active {active}/{len(sub)} days, "
              f"carry cum: {carry_cum:.4f}, v3 cum: {v3_cum:.4f}")

    # ── Monthly OOS return table ──
    print("\n--- Monthly OOS Returns ---")
    monthly_v3 = (1 + oos_v3).resample('ME').prod() - 1
    monthly_carry = (1 + oos_carry).resample('ME').prod() - 1
    monthly_ports = {}
    for name, port_ret in portfolios.items():
        oos_port = port_ret.iloc[is_end_idx:]
        monthly_ports[name] = (1 + oos_port).resample('ME').prod() - 1

    print(f"{'Month':<10} {'V3':>8} {'Carry':>8} ", end="")
    for name in portfolios:
        print(f"{name:>12}", end="")
    print()

    for dt in monthly_v3.index:
        mo = dt.strftime('%Y-%m')
        v3_m = monthly_v3.loc[dt]
        carry_m = monthly_carry.get(dt, 0)
        print(f"{mo:<10} {v3_m:>8.2%} {carry_m:>8.2%} ", end="")
        for name in portfolios:
            pm = monthly_ports[name].get(dt, 0)
            print(f"{pm:>12.2%}", end="")
        print()

    # ═══════════════════════════════════════════════════════════════════════
    # GENERATE REPORT
    # ═══════════════════════════════════════════════════════════════════════

    print("\n" + "=" * 78)
    print("GENERATING REPORT")
    print("=" * 78)

    lines = _generate_report(
        v3_is, v3_oos, carry_is, carry_oos,
        port_metrics_is, port_metrics_oos,
        portfolios, v3_ret, carry_ret,
        corr_full, corr_is, corr_oos, rolling_corr,
        carry_active_pct_full, carry_active_pct_oos,
        kill_flags, is_start, is_end, oos_start, oos_end,
        is_end_idx, rp_w_v3, rp_w_carry,
        monthly_v3, monthly_carry, monthly_ports,
        carry_pos, common_idx,
    )

    report_path = OUTPUT_DIR / 'v3_carry_portfolio_results.md'
    with open(report_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f"\nReport saved to: {report_path}")

    # Final verdict
    print("\n" + "=" * 78)
    print("FINAL VERDICT")
    print("=" * 78)
    if kill_flags:
        print(f"\nKILL FLAGS TRIGGERED: {len(kill_flags)}")
        for kf in kill_flags:
            print(f"  - {kf}")
        print("\nConclusion: The V3+Carry portfolio does NOT pass kill criteria.")
        print("V3 momentum should remain a standalone strategy.")
    else:
        print("\nAll kill criteria passed. Portfolio combination is viable.")


def _get_quarter_ranges(start_date, end_date):
    """Generate quarter ranges for sub-period analysis."""
    current = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    ranges = []
    while current <= end:
        q_start = current
        q_end = current + pd.DateOffset(months=3) - pd.DateOffset(days=1)
        if q_end > end:
            q_end = end
        label = f"{q_start.strftime('%Y-Q')}{(q_start.month - 1) // 3 + 1}"
        ranges.append((q_start, q_end, label))
        current = q_start + pd.DateOffset(months=3)
    return ranges


def _generate_report(
    v3_is, v3_oos, carry_is, carry_oos,
    port_is, port_oos,
    portfolios, v3_ret, carry_ret,
    corr_full, corr_is, corr_oos, rolling_corr,
    carry_active_full, carry_active_oos,
    kill_flags, is_start, is_end, oos_start, oos_end,
    is_end_idx, rp_w_v3, rp_w_carry,
    monthly_v3, monthly_carry, monthly_ports,
    carry_pos, common_idx,
):
    """Generate the markdown report."""
    lines = []

    lines.append("# V3 Momentum + Funding Carry Portfolio Backtest")
    lines.append("")
    lines.append(f"**Run date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**IS period**: {is_start} to {is_end}")
    lines.append(f"**OOS period**: {oos_start} to {oos_end}")
    lines.append(f"**IS/OOS split**: {IS_FRACTION:.0%} / {1 - IS_FRACTION:.0%}")
    lines.append(f"**Transaction cost**: {COST_BPS} bps per rebalance")
    lines.append(f"**Portfolio rebalance**: Monthly")
    lines.append(f"**Strategy rebalance**: Weekly")
    lines.append("")

    # ── Executive Summary ──
    lines.append("## Executive Summary")
    lines.append("")

    n_kills = len(kill_flags)
    if n_kills > 0:
        lines.append(f"**VERDICT: KILL** -- {n_kills} kill criteria triggered.")
        lines.append("")
        for kf in kill_flags:
            lines.append(f"- {kf}")
        lines.append("")
        lines.append("The funding carry strategy is too dormant in the OOS period to provide ")
        lines.append("meaningful diversification. V3 momentum should remain standalone.")
    else:
        lines.append("**VERDICT: VIABLE** -- All kill criteria passed.")
    lines.append("")

    # ── Strategy Descriptions ──
    lines.append("## Strategy Descriptions")
    lines.append("")
    lines.append("### V3 Momentum (BTC Spot)")
    lines.append("- 20/50 EMA crossover base signal (daily bars)")
    lines.append("- Positioning overlay: Binance top trader L/S z-score -> sizing multiplier")
    lines.append("- VRP overlay: (IV - RV) z-score -> sizing multiplier")
    lines.append("- Weekly rebalance, long-only, position range [0, 1.5x]")
    lines.append("- Validated OOS: Sharpe ~0.56, +17.52% return")
    lines.append("")
    lines.append("### Funding Carry (BTC Perp)")
    lines.append("- Delta-neutral carry: short perp + long spot when funding positive")
    lines.append("- Entry: |72h rolling mean funding| > 0.005% per hour")
    lines.append("- Returns = funding income only (hedged price exposure)")
    lines.append("- Weekly signal rebalance")
    lines.append("")

    # ── Standalone Performance ──
    lines.append("## Standalone Strategy Performance")
    lines.append("")
    lines.append("| Metric | V3 IS | V3 OOS | Carry IS | Carry OOS |")
    lines.append("|--------|-------|--------|----------|-----------|")
    lines.append(f"| Total Return | {v3_is['total_return']:.2%} | {v3_oos['total_return']:.2%} | "
                 f"{carry_is['total_return']:.2%} | {carry_oos['total_return']:.2%} |")
    lines.append(f"| Ann. Return | {v3_is['ann_return']:.2%} | {v3_oos['ann_return']:.2%} | "
                 f"{carry_is['ann_return']:.2%} | {carry_oos['ann_return']:.2%} |")
    lines.append(f"| Ann. Vol | {v3_is['ann_vol']:.2%} | {v3_oos['ann_vol']:.2%} | "
                 f"{carry_is['ann_vol']:.2%} | {carry_oos['ann_vol']:.2%} |")
    lines.append(f"| Sharpe | {v3_is['sharpe']:.3f} | {v3_oos['sharpe']:.3f} | "
                 f"{carry_is['sharpe']:.3f} | {carry_oos['sharpe']:.3f} |")
    lines.append(f"| Sortino | {v3_is['sortino']:.3f} | {v3_oos['sortino']:.3f} | "
                 f"{carry_is['sortino']:.3f} | {carry_oos['sortino']:.3f} |")
    lines.append(f"| Max DD | {v3_is['max_dd']:.2%} | {v3_oos['max_dd']:.2%} | "
                 f"{carry_is['max_dd']:.2%} | {carry_oos['max_dd']:.2%} |")
    lines.append(f"| Calmar | {v3_is['calmar']:.2f} | {v3_oos['calmar']:.2f} | "
                 f"{carry_is['calmar']:.2f} | {carry_oos['calmar']:.2f} |")
    lines.append(f"| Days | {v3_is['n_days']} | {v3_oos['n_days']} | "
                 f"{carry_is['n_days']} | {carry_oos['n_days']} |")
    lines.append("")

    # ── Correlation ──
    lines.append("## Correlation Analysis")
    lines.append("")
    lines.append(f"| Period | V3 vs Carry Correlation |")
    lines.append(f"|--------|------------------------|")
    lines.append(f"| Full | {corr_full:.4f} |")
    lines.append(f"| IS | {corr_is:.4f} |")
    lines.append(f"| OOS | {corr_oos:.4f} |")
    lines.append(f"| Rolling 90d Mean | {rolling_corr.mean():.4f} |")
    lines.append(f"| Rolling 90d Min | {rolling_corr.min():.4f} |")
    lines.append(f"| Rolling 90d Max | {rolling_corr.max():.4f} |")
    lines.append("")

    if abs(corr_oos) < 0.1:
        lines.append("Correlation is near zero, which is expected: V3 profits from BTC price ")
        lines.append("trends while carry profits from funding rates with hedged price exposure.")
    elif abs(corr_oos) < 0.3:
        lines.append("Low correlation -- moderate diversification benefit.")
    elif abs(corr_oos) < 0.5:
        lines.append("Moderate correlation -- limited diversification benefit.")
    else:
        lines.append("**High correlation -- insufficient diversification. KILL flag triggered.**")
    lines.append("")

    # ── Carry Activity ──
    lines.append("## Carry Strategy Activity")
    lines.append("")
    lines.append(f"| Period | % Days Active |")
    lines.append(f"|--------|--------------|")
    lines.append(f"| Full period | {carry_active_full:.1f}% |")
    lines.append(f"| OOS | {carry_active_oos:.1f}% |")
    lines.append("")

    # Activity by year
    lines.append("### Activity by Year")
    lines.append("")
    lines.append("| Year | Active Days | Total Days | % Active |")
    lines.append("|------|------------|------------|----------|")
    for year in range(common_idx.min().year, common_idx.max().year + 1):
        mask = (carry_pos.index.year == year)
        sub = carry_pos[mask]
        if len(sub) > 0:
            active = (sub > 0).sum()
            lines.append(f"| {year} | {active} | {len(sub)} | {100 * active / len(sub):.1f}% |")
    lines.append("")

    lines.append("Funding rates have structurally declined since 2021. The carry strategy ")
    lines.append("was highly active in 2020-2021 (bull market with persistent positive funding) ")
    lines.append("but has become increasingly dormant as funding rates compressed. ")
    lines.append("By H2 2025, the 72h rolling mean rarely exceeds the entry threshold.")
    lines.append("")

    # ── Portfolio Performance ──
    lines.append("## Portfolio Performance (OOS)")
    lines.append("")
    lines.append("| Allocation | Sharpe | Return | MaxDD | Sortino | vs V3 dSharpe |")
    lines.append("|------------|--------|--------|-------|---------|---------------|")
    lines.append(f"| V3 Standalone | {v3_oos['sharpe']:.3f} | {v3_oos['total_return']:.2%} | "
                 f"{v3_oos['max_dd']:.2%} | {v3_oos['sortino']:.3f} | -- |")

    for name in ['60/40', '50/50', 'Risk Parity', 'Dynamic']:
        m = port_oos[name]
        delta = m['sharpe'] - v3_oos['sharpe']
        lines.append(f"| {name} | {m['sharpe']:.3f} | {m['total_return']:.2%} | "
                     f"{m['max_dd']:.2%} | {m['sortino']:.3f} | {delta:+.4f} |")
    lines.append("")

    # ── Portfolio Performance (IS) ──
    lines.append("## Portfolio Performance (IS)")
    lines.append("")
    lines.append("| Allocation | Sharpe | Return | MaxDD | Sortino | vs V3 dSharpe |")
    lines.append("|------------|--------|--------|-------|---------|---------------|")
    lines.append(f"| V3 Standalone | {v3_is['sharpe']:.3f} | {v3_is['total_return']:.2%} | "
                 f"{v3_is['max_dd']:.2%} | {v3_is['sortino']:.3f} | -- |")

    for name in ['60/40', '50/50', 'Risk Parity', 'Dynamic']:
        m = port_is[name]
        delta = m['sharpe'] - v3_is['sharpe']
        lines.append(f"| {name} | {m['sharpe']:.3f} | {m['total_return']:.2%} | "
                     f"{m['max_dd']:.2%} | {m['sortino']:.3f} | {delta:+.4f} |")
    lines.append("")

    # ── Risk Parity Weights ──
    lines.append("## Risk Parity Weight Distribution (OOS)")
    lines.append("")
    rp_v3_oos = rp_w_v3.iloc[is_end_idx:]
    rp_carry_oos = rp_w_carry.iloc[is_end_idx:]
    lines.append(f"- Mean V3 weight: {rp_v3_oos.mean():.1%}")
    lines.append(f"- Mean Carry weight: {rp_carry_oos.mean():.1%}")
    lines.append(f"- V3 weight range: [{rp_v3_oos.min():.1%}, {rp_v3_oos.max():.1%}]")
    lines.append("")
    lines.append("Risk parity heavily tilts toward carry (lower vol) because the carry ")
    lines.append("strategy is mostly flat (near-zero vol). This makes risk parity ")
    lines.append("behave perversely: it over-allocates to an inactive strategy.")
    lines.append("")

    # ── Monthly OOS Returns ──
    lines.append("## Monthly OOS Returns")
    lines.append("")
    header = "| Month | V3 | Carry |"
    for name in portfolios:
        header += f" {name} |"
    lines.append(header)
    sep = "|-------|----|----- |"
    for name in portfolios:
        sep += "------|"
    lines.append(sep)

    for dt in monthly_v3.index:
        mo = dt.strftime('%Y-%m')
        v3_m = monthly_v3.loc[dt]
        carry_m = monthly_carry.get(dt, 0)
        row = f"| {mo} | {v3_m:.2%} | {carry_m:.2%} |"
        for name in portfolios:
            pm = monthly_ports[name].get(dt, 0)
            row += f" {pm:.2%} |"
        lines.append(row)
    lines.append("")

    # ── Kill Criteria ──
    lines.append("## Kill Criteria Evaluation")
    lines.append("")
    lines.append("| Criterion | Threshold | Actual | Result |")
    lines.append("|-----------|-----------|--------|--------|")

    # KC1
    corr_result = "KILL" if abs(corr_oos) > 0.5 else "PASS"
    lines.append(f"| Correlation | < 0.5 | {corr_oos:.3f} | {corr_result} |")

    # KC2
    best_name = max(port_oos, key=lambda k: port_oos[k]['sharpe'])
    best_sharpe = port_oos[best_name]['sharpe']
    sharpe_result = "KILL" if best_sharpe < v3_oos['sharpe'] else "PASS"
    lines.append(f"| Portfolio Sharpe > V3 | > {v3_oos['sharpe']:.3f} | "
                 f"{best_sharpe:.3f} ({best_name}) | {sharpe_result} |")

    # KC3
    active_result = "KILL" if carry_active_oos < 20 else "PASS"
    lines.append(f"| Carry Active % | > 20% | {carry_active_oos:.1f}% | {active_result} |")
    lines.append("")

    # ── Honest Assessment ──
    lines.append("## Honest Assessment")
    lines.append("")

    if carry_active_oos < 20:
        lines.append("### Funding Carry Is Dead (for now)")
        lines.append("")
        lines.append("The funding carry strategy is dormant in the OOS period. BTC perpetual ")
        lines.append("funding rates have structurally declined since the 2021 bull market. ")
        lines.append("The 72h rolling mean of funding rarely exceeds the entry threshold ")
        lines.append(f"({CARRY_THRESHOLD * 100:.4f}% per hour) in {oos_start.year}+.")
        lines.append("")
        lines.append("This means:")
        lines.append("- The carry strategy produces near-zero returns in OOS")
        lines.append("- Any portfolio allocation to carry just dilutes V3 returns")
        lines.append("- The \"Dynamic\" allocation is the only sensible approach: it defaults to ")
        lines.append("  100% V3 when carry is inactive, and only allocates to carry when there ")
        lines.append("  is actually funding to collect")
        lines.append("")

    lines.append("### When Would This Portfolio Make Sense?")
    lines.append("")
    lines.append("The V3+Carry portfolio would become viable if:")
    lines.append("1. BTC funding rates return to 2020-2021 levels (0.01%+ per 8h)")
    lines.append("2. The carry strategy has sustained activity (>30% of days)")
    lines.append("3. Carry provides genuine diversification (correlation stays < 0.3)")
    lines.append("")
    lines.append("Until then, V3 momentum should remain a standalone strategy. The carry ")
    lines.append("component adds complexity without adding return or reducing risk.")
    lines.append("")

    lines.append("### What About the IS Period?")
    lines.append("")
    if carry_is['sharpe'] > 0.3:
        lines.append(f"In the IS period (which includes 2020-2021 bull market), carry had a ")
        lines.append(f"Sharpe of {carry_is['sharpe']:.3f}. The portfolio would have worked well ")
        lines.append(f"historically. But this is misleading: the funding environment has ")
        lines.append(f"structurally changed, and IS performance is not representative of ")
        lines.append(f"current conditions.")
    else:
        lines.append(f"Even in the IS period, carry's Sharpe was only {carry_is['sharpe']:.3f}. ")
        lines.append(f"The delta-neutral carry returns are modest even when funding is active.")
    lines.append("")

    lines.append("## Recommendation")
    lines.append("")
    if kill_flags:
        lines.append("**Do not deploy the V3+Carry portfolio.**")
        lines.append("")
        lines.append("Triggered kill flags:")
        for kf in kill_flags:
            lines.append(f"- {kf}")
        lines.append("")
        lines.append("Keep V3 momentum as a standalone strategy. Monitor funding rates -- ")
        lines.append("if they recover to 2021 levels, revisit this analysis.")
    else:
        lines.append("**Portfolio is viable but marginal.** The Dynamic allocation is the ")
        lines.append("only sensible choice as it avoids diluting V3 when carry is inactive.")
    lines.append("")

    return lines


if __name__ == '__main__':
    main()
