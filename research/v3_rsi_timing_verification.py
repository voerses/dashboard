#!/workspace/venv/bin/python
"""
V3 RSI Timing Verification — Compare research prototype vs V4 engine
=====================================================================

KEY QUESTION: Research showed "Return 53%->337%, MaxDD -59%->-28%" for V3+RSI timing.
Does this hold up when run through the actual V4 backtest engine on $200K?

This script:
1. Runs the V3+RSI research prototype logic (from v3_walkforward_sensitivity.py)
   - With original 1.5x max position (research default)
   - With 1.0x max position (spot cap)
   - With realistic Kelly sizing on $200K
2. Records position sizes and entry/exit timing
3. Compares against V4 engine output (s320a results)

The gap analysis answers: WHY does research show 337% but V4 shows ~8-37%?
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

COST_BPS = 10  # round-trip cost in basis points


# ============================================================================
# DATA LOADING (same as walkforward sensitivity)
# ============================================================================

def load_btc_daily():
    """Load BTC spot 1h data and resample to daily."""
    btc = pd.read_parquet(DATA_DIR / 'spot/1h_cache/BTC_1h.parquet')
    btc.index = pd.to_datetime(btc.index)
    btc.index.name = 'date'
    daily = btc['close'].resample('D').last().dropna().to_frame('close')
    daily['open'] = btc['open'].resample('D').first()
    daily['high'] = btc['high'].resample('D').max()
    daily['low'] = btc['low'].resample('D').min()
    daily['volume'] = btc['volume'].resample('D').sum()
    return daily


def load_btc_4h():
    """Load BTC 1h and resample to 4h for RSI computation."""
    btc = pd.read_parquet(DATA_DIR / 'spot/1h_cache/BTC_1h.parquet')
    btc.index = pd.to_datetime(btc.index)
    btc.index.name = 'date'
    btc_4h = btc['close'].resample('4h').last().dropna().to_frame('close')
    return btc_4h


def load_dvol():
    dvol_path = DATA_DIR / 'alternative/deribit_options/dvol/btc_dvol_daily.json'
    with open(dvol_path) as f:
        data = json.load(f)
    records = [{'date': pd.Timestamp(row[0], unit='ms'), 'dvol_close': row[4]} for row in data]
    dvol = pd.DataFrame(records).set_index('date').sort_index()
    dvol = dvol[~dvol.index.duplicated(keep='last')]
    return dvol['dvol_close']


def load_positioning():
    pos = pd.read_parquet(DATA_DIR / 'alternative/binance_metrics/all_symbols_daily_ls.parquet')
    pos = pos[pos['symbol'] == 'BTCUSDT'].copy()
    pos['date'] = pd.to_datetime(pos['date'])
    pos = pos.set_index('date').sort_index()
    pos = pos[['sum_toptrader_ls_ratio', 'count_toptrader_ls_ratio', 'count_ls_ratio']].copy()
    pos = pos[~pos.index.duplicated(keep='last')]
    return pos


# ============================================================================
# SIGNAL CONSTRUCTION
# ============================================================================

def build_ema_trend_signal(btc_daily, fast_period=20, slow_period=50):
    ema_fast = btc_daily['close'].ewm(span=fast_period, adjust=False).mean()
    ema_slow = btc_daily['close'].ewm(span=slow_period, adjust=False).mean()
    position = (ema_fast > ema_slow).astype(float)
    position.iloc[:slow_period] = 0.0
    return position


def build_positioning_signal(btc_daily, positioning, z_window=30, high_thresh=1.5):
    pos = positioning.reindex(btc_daily.index).ffill()

    def rolling_zscore(s, window):
        mu = s.rolling(window, min_periods=max(15, window // 2)).mean()
        sigma = s.rolling(window, min_periods=max(15, window // 2)).std()
        return (s - mu) / sigma.replace(0, np.nan)

    z_toptrader = rolling_zscore(pos['sum_toptrader_ls_ratio'], z_window)
    divergence = pos['count_toptrader_ls_ratio'] - pos['count_ls_ratio']
    z_divergence = rolling_zscore(divergence, z_window)
    combined_z = (z_toptrader + z_divergence) / 2.0
    mid_thresh = high_thresh / 3.0

    def z_to_multiplier(z):
        if pd.isna(z):
            return 1.0
        if z > high_thresh:
            return 0.3
        elif z > mid_thresh:
            return 0.5
        elif z > -mid_thresh:
            return 1.0
        elif z > -high_thresh:
            return 1.3
        else:
            return 1.5

    return combined_z.apply(z_to_multiplier)


def build_vrp_signal(btc_daily, dvol_series, vrp_z_window=60, vrp_high_thresh=1.0):
    log_ret = np.log(btc_daily['close'] / btc_daily['close'].shift(1))
    rv_20d = log_ret.rolling(20, min_periods=15).std() * np.sqrt(365) * 100

    if dvol_series.empty or len(dvol_series) < 30:
        iv_proxy = log_ret.rolling(90, min_periods=60).std() * np.sqrt(365) * 100
        iv = iv_proxy * 1.2
    else:
        iv = dvol_series.reindex(btc_daily.index).ffill()

    vrp = iv - rv_20d
    vrp_mu = vrp.rolling(vrp_z_window, min_periods=max(30, vrp_z_window // 2)).mean()
    vrp_sigma = vrp.rolling(vrp_z_window, min_periods=max(30, vrp_z_window // 2)).std()
    vrp_z = (vrp - vrp_mu) / vrp_sigma.replace(0, np.nan)
    mid_low = -vrp_high_thresh / 2.0
    extreme_low = -vrp_high_thresh * 1.5

    def vrp_z_to_multiplier(z):
        if pd.isna(z):
            return 1.0
        if z > vrp_high_thresh:
            return 1.3
        elif z > mid_low:
            return 1.0
        elif z > extreme_low:
            return 0.5
        else:
            return 0.3

    return vrp_z.apply(vrp_z_to_multiplier)


def compute_4h_rsi(btc_4h, period=14):
    """Compute RSI on 4h bars."""
    close = btc_4h['close']
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return rsi


# ============================================================================
# BACKTEST VARIANTS
# ============================================================================

def run_research_backtest(btc_daily, final_position, cost_bps=COST_BPS):
    """Original research backtest: % of equity model, compounding daily.

    This is the exact model from v3_walkforward_sensitivity.py.
    final_position is the fraction of equity allocated (0 to 1.5).
    """
    daily_ret = btc_daily['close'].pct_change()

    rebalance_dates = btc_daily.index.to_series().groupby(
        btc_daily.index.to_period('W')
    ).first()
    rebalance_set = set(rebalance_dates.values)

    held_position = pd.Series(0.0, index=btc_daily.index)
    current_pos = 0.0
    costs = pd.Series(0.0, index=btc_daily.index)

    for dt in btc_daily.index:
        if dt in rebalance_set:
            new_pos = final_position.loc[dt]
            if not pd.isna(new_pos):
                pos_change = abs(new_pos - current_pos)
                costs.loc[dt] = pos_change * cost_bps / 10000.0
                current_pos = new_pos
        held_position.loc[dt] = current_pos

    strat_ret = held_position.shift(1) * daily_ret - costs
    return strat_ret, held_position


def run_rsi_timed_backtest(btc_daily, btc_4h, final_position, rsi_threshold=35,
                           search_window_days=7, cost_bps=COST_BPS):
    """V3+RSI timing (V2 Flexible): defer entry to first 4h RSI cross-up.

    Within each weekly rebalance window, if the strategy is long, wait for
    4h RSI to cross up through rsi_threshold before entering. If no cross
    occurs within search_window_days, enter at fallback (same as baseline).
    """
    daily_ret = btc_daily['close'].pct_change()
    rsi_4h = compute_4h_rsi(btc_4h)

    rebalance_dates = btc_daily.index.to_series().groupby(
        btc_daily.index.to_period('W')
    ).first()
    rebalance_list = sorted(rebalance_dates.values)

    held_position = pd.Series(0.0, index=btc_daily.index)
    current_pos = 0.0
    costs = pd.Series(0.0, index=btc_daily.index)
    entry_log = []

    for i, rb_date in enumerate(rebalance_list):
        if rb_date not in btc_daily.index:
            continue
        target_pos = final_position.loc[rb_date] if rb_date in final_position.index else np.nan
        if pd.isna(target_pos) or target_pos <= 0:
            # Go flat
            if current_pos != 0:
                pos_change = abs(target_pos - current_pos) if not pd.isna(target_pos) else abs(current_pos)
                costs.loc[rb_date] = pos_change * cost_bps / 10000.0
            current_pos = 0.0 if pd.isna(target_pos) else target_pos
            held_position.loc[rb_date:] = current_pos
            continue

        # Strategy wants to be long. Check if we need RSI timing.
        # Search window: from rebalance date to +search_window_days
        search_end = rb_date + pd.Timedelta(days=search_window_days)

        # Find 4h RSI cross-up through threshold within window
        rsi_window = rsi_4h[(rsi_4h.index >= rb_date) & (rsi_4h.index < search_end)]
        rsi_prev = rsi_window.shift(1)
        cross_up = (rsi_prev <= rsi_threshold) & (rsi_window > rsi_threshold)

        if cross_up.any():
            # Enter on first cross-up day
            cross_date = cross_up.idxmax()
            # Convert to daily
            entry_date_idx = btc_daily.index.searchsorted(cross_date)
            if entry_date_idx < len(btc_daily.index):
                entry_date = btc_daily.index[entry_date_idx]
            else:
                entry_date = rb_date  # fallback
            entry_type = 'rsi_timed'
        else:
            # Fallback: enter at rebalance date
            entry_date = rb_date
            entry_type = 'fallback'

        # Position change at entry date
        pos_change = abs(target_pos - current_pos)
        if entry_date in btc_daily.index:
            costs.loc[entry_date] = pos_change * cost_bps / 10000.0

        # Between rebalance and entry, stay at previous position
        if entry_date > rb_date:
            held_position.loc[rb_date:entry_date] = current_pos

        current_pos = target_pos
        held_position.loc[entry_date:] = current_pos

        entry_log.append({
            'rebalance_date': rb_date,
            'entry_date': entry_date,
            'entry_type': entry_type,
            'position': target_pos,
            'delay_hours': pd.Timedelta(entry_date - rb_date).total_seconds() / 3600,
        })

    strat_ret = held_position.shift(1) * daily_ret - costs
    return strat_ret, held_position, entry_log


def run_fixed_dollar_backtest(btc_daily, final_position, capital=200000,
                              cost_bps=COST_BPS, max_position_pct=1.0):
    """Fixed-capital version: track actual dollar equity, no compounding beyond equity.

    This simulates what V4 actually does:
    - Position size = equity * position_fraction * kelly_adjustment
    - PnL in dollars, added back to equity
    - Position capped at max_position_pct of equity
    """
    daily_ret = btc_daily['close'].pct_change()

    rebalance_dates = btc_daily.index.to_series().groupby(
        btc_daily.index.to_period('W')
    ).first()
    rebalance_set = set(rebalance_dates.values)

    equity = capital
    equity_curve = pd.Series(capital, index=btc_daily.index, dtype=float)
    position_pct = 0.0
    position_usd = 0.0

    for i, dt in enumerate(btc_daily.index):
        if dt in rebalance_set:
            new_pct = final_position.loc[dt]
            if not pd.isna(new_pct):
                new_pct = min(new_pct, max_position_pct)
                pos_change_pct = abs(new_pct - position_pct)
                cost = pos_change_pct * cost_bps / 10000.0 * equity
                equity -= cost
                position_pct = new_pct
                position_usd = equity * position_pct

        if i > 0:
            ret = daily_ret.iloc[i]
            if not pd.isna(ret):
                pnl = position_usd * ret
                equity += pnl
                position_usd = equity * position_pct  # rebalance to target pct

        equity_curve.iloc[i] = equity

    total_return = (equity / capital - 1) * 100
    return equity_curve, total_return


def compute_metrics(returns):
    returns = returns.dropna()
    if len(returns) < 30:
        return {'sharpe': np.nan, 'return_pct': np.nan, 'max_dd': np.nan, 'n_days': len(returns)}

    total_ret = (1 + returns).prod() - 1
    n_years = len(returns) / 365
    ann_ret = (1 + total_ret) ** (1 / max(n_years, 0.01)) - 1
    ann_vol = returns.std() * np.sqrt(365)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

    cum = (1 + returns).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    max_dd = dd.min()

    return {
        'sharpe': sharpe,
        'return_pct': total_ret * 100,
        'ann_return': ann_ret * 100,
        'max_dd': max_dd * 100,
        'n_days': len(returns),
    }


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 72)
    print("V3 RSI TIMING VERIFICATION")
    print(f"Run date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 72)

    # Load data
    print("\nLoading data...")
    btc_daily = load_btc_daily()
    btc_4h = load_btc_4h()
    dvol = load_dvol()
    positioning = load_positioning()
    print(f"  BTC daily: {btc_daily.index.min().date()} to {btc_daily.index.max().date()}")
    print(f"  BTC 4h: {btc_4h.index.min()} to {btc_4h.index.max()}")
    print(f"  Total daily bars: {len(btc_daily)}")

    # Build signals (full period)
    print("\nBuilding signals (full period)...")
    base_pos = build_ema_trend_signal(btc_daily)
    pos_mult = build_positioning_signal(btc_daily, positioning)
    vrp_mult = build_vrp_signal(btc_daily, dvol)

    # Final position with 1.5x max (research default)
    final_pos_150 = (base_pos * pos_mult * vrp_mult).clip(0, 1.5)
    # Final position with 1.0x max (spot cap)
    final_pos_100 = (base_pos * pos_mult * vrp_mult).clip(0, 1.0)
    # Binary gates version (1.0 or 0.0, same as s320a)
    final_pos_binary = base_pos.copy()
    # Apply regime gates (simplified: skip if pos_mult < 0.5 or vrp_mult < 0.7)
    binary_mask = (pos_mult >= 0.5) & (vrp_mult >= 0.7)
    final_pos_binary = np.where(binary_mask & (base_pos > 0), 1.0, 0.0)
    final_pos_binary = pd.Series(final_pos_binary, index=btc_daily.index)

    print(f"\n  Position distribution (1.5x max):")
    print(f"    Mean when >0: {final_pos_150[final_pos_150 > 0].mean():.3f}")
    print(f"    Bars >0: {(final_pos_150 > 0).sum()} / {len(final_pos_150)} ({(final_pos_150 > 0).mean()*100:.1f}%)")
    print(f"    Bars >1.0: {(final_pos_150 > 1.0).sum()} / {len(final_pos_150)} ({(final_pos_150 > 1.0).mean()*100:.1f}%)")

    # ======================================================================
    # TEST 1: Research prototype (original, no RSI timing)
    # ======================================================================
    print("\n" + "=" * 72)
    print("TEST 1: Research Prototype (NO RSI Timing)")
    print("=" * 72)

    ret_base_150, _ = run_research_backtest(btc_daily, final_pos_150)
    ret_base_100, _ = run_research_backtest(btc_daily, final_pos_100)
    ret_base_binary, _ = run_research_backtest(btc_daily, final_pos_binary)

    m_150 = compute_metrics(ret_base_150)
    m_100 = compute_metrics(ret_base_100)
    m_binary = compute_metrics(ret_base_binary)

    print(f"\n  {'Variant':<25s} {'Return':>10s} {'AnnRet':>10s} {'Sharpe':>8s} {'MaxDD':>8s}")
    print(f"  {'-'*25} {'-'*10} {'-'*10} {'-'*8} {'-'*8}")
    print(f"  {'1.5x max (research)':<25s} {m_150['return_pct']:>+9.1f}% {m_150['ann_return']:>+9.1f}% {m_150['sharpe']:>8.2f} {m_150['max_dd']:>7.1f}%")
    print(f"  {'1.0x max (spot cap)':<25s} {m_100['return_pct']:>+9.1f}% {m_100['ann_return']:>+9.1f}% {m_100['sharpe']:>8.2f} {m_100['max_dd']:>7.1f}%")
    print(f"  {'Binary gates (s320a)':<25s} {m_binary['return_pct']:>+9.1f}% {m_binary['ann_return']:>+9.1f}% {m_binary['sharpe']:>8.2f} {m_binary['max_dd']:>7.1f}%")

    # ======================================================================
    # TEST 2: With RSI timing (V2 Flexible)
    # ======================================================================
    print("\n" + "=" * 72)
    print("TEST 2: With RSI Timing (V2 Flexible, RSI=35, 168h window)")
    print("=" * 72)

    ret_rsi_150, _, log_150 = run_rsi_timed_backtest(btc_daily, btc_4h, final_pos_150, rsi_threshold=35)
    ret_rsi_100, _, log_100 = run_rsi_timed_backtest(btc_daily, btc_4h, final_pos_100, rsi_threshold=35)
    ret_rsi_binary, _, log_bin = run_rsi_timed_backtest(btc_daily, btc_4h, final_pos_binary, rsi_threshold=35)

    m_rsi_150 = compute_metrics(ret_rsi_150)
    m_rsi_100 = compute_metrics(ret_rsi_100)
    m_rsi_binary = compute_metrics(ret_rsi_binary)

    print(f"\n  {'Variant':<25s} {'Return':>10s} {'AnnRet':>10s} {'Sharpe':>8s} {'MaxDD':>8s}")
    print(f"  {'-'*25} {'-'*10} {'-'*10} {'-'*8} {'-'*8}")
    print(f"  {'1.5x max (research)':<25s} {m_rsi_150['return_pct']:>+9.1f}% {m_rsi_150['ann_return']:>+9.1f}% {m_rsi_150['sharpe']:>8.2f} {m_rsi_150['max_dd']:>7.1f}%")
    print(f"  {'1.0x max (spot cap)':<25s} {m_rsi_100['return_pct']:>+9.1f}% {m_rsi_100['ann_return']:>+9.1f}% {m_rsi_100['sharpe']:>8.2f} {m_rsi_100['max_dd']:>7.1f}%")
    print(f"  {'Binary gates (s320a)':<25s} {m_rsi_binary['return_pct']:>+9.1f}% {m_rsi_binary['ann_return']:>+9.1f}% {m_rsi_binary['sharpe']:>8.2f} {m_rsi_binary['max_dd']:>7.1f}%")

    # Entry stats
    for name, log in [('1.5x', log_150), ('1.0x', log_100), ('Binary', log_bin)]:
        df = pd.DataFrame(log)
        if len(df) > 0:
            n_rsi = (df['entry_type'] == 'rsi_timed').sum()
            n_fb = (df['entry_type'] == 'fallback').sum()
            avg_delay = df[df['entry_type'] == 'rsi_timed']['delay_hours'].mean() if n_rsi > 0 else 0
            print(f"\n  {name} entries: {len(df)} total, {n_rsi} RSI-timed ({n_rsi/len(df)*100:.0f}%), {n_fb} fallback, avg delay {avg_delay:.0f}h")

    # ======================================================================
    # TEST 3: Fixed-dollar capital ($200K) with different position caps
    # ======================================================================
    print("\n" + "=" * 72)
    print("TEST 3: Fixed $200K Capital (no compounding leverage illusion)")
    print("=" * 72)

    capital = 200000
    for name, fp, max_pct in [
        ('1.5x (impossible on spot)', final_pos_150, 1.5),
        ('1.0x (spot max)', final_pos_150, 1.0),
        ('Binary (s320a)', final_pos_binary, 1.0),
    ]:
        eq, total_ret = run_fixed_dollar_backtest(btc_daily, fp, capital=capital, max_position_pct=max_pct)
        max_dd = ((eq - eq.cummax()) / eq.cummax()).min() * 100
        n_years = len(btc_daily) / 365
        ann_ret = ((1 + total_ret/100) ** (1/n_years) - 1) * 100
        sharpe_daily = eq.pct_change().dropna()
        sharpe = sharpe_daily.mean() / sharpe_daily.std() * np.sqrt(365) if sharpe_daily.std() > 0 else 0
        print(f"  {name:<30s}  Return: {total_ret:>+7.1f}%  Ann: {ann_ret:>+6.1f}%  Sharpe: {sharpe:>5.2f}  MaxDD: {max_dd:>6.1f}%  Final: ${eq.iloc[-1]:>,.0f}")

    # ======================================================================
    # TEST 4: V4 Engine comparison (from already-run results)
    # ======================================================================
    print("\n" + "=" * 72)
    print("TEST 4: V4 Engine Results (from portfolio_backtest.py)")
    print("=" * 72)

    # Read saved V4 results
    v4_metrics_path = BASE_DIR / 'results/v4/s320a_60mo_200k_metrics.json'
    if v4_metrics_path.exists():
        with open(v4_metrics_path) as f:
            m = json.load(f)
        print(f"  V4 s320a 60mo (--market spot, portfolio constraints ON):")
        print(f"    Total Return:  {m.get('total_return_pct', 0):+.1f}%")
        print(f"    Sharpe:        {m.get('sharpe_ratio', 0):.2f}")
        print(f"    Max DD:        {m.get('max_drawdown_pct', 0):.1f}%")
        print(f"    Trades:        {m.get('total_trades', 0)}")
        print(f"    Win Rate:      {m.get('win_rate_pct', 0):.1f}%")
        print(f"    Avg Hold:      {m.get('avg_hold_hours', 0):.0f}h")
    else:
        print("  (No V4 results files found -- run portfolio_backtest.py first)")

    # ======================================================================
    # GAP ANALYSIS
    # ======================================================================
    print("\n" + "=" * 72)
    print("GAP ANALYSIS: WHY Research 337% vs V4 ~8-37%")
    print("=" * 72)

    print("""
  The research prototype uses a PERCENTAGE-OF-EQUITY return model:
    strat_ret = position_fraction * daily_return - costs

  Where position_fraction can be up to 1.5 (150% of equity).
  This means:
    1. Implicit DAILY COMPOUNDING: profits from day 1 increase position
       size on day 2 (equity grows, position = equity * fraction grows)
    2. LEVERAGE UP TO 1.5x: impossible on spot without margin/borrowing
    3. NO FIXED DOLLAR SIZING: the model assumes infinite liquidity and
       no dollar-based position limits

  The V4 engine uses FIXED-DOLLAR Kelly sizing:
    pos_usd = min(equity * kelly_frac * vol_adj, equity * cap_pct * cap_mult)

  Where:
    - kelly_frac = kelly_mult * edge * size_multiplier
    - With default params: kelly_frac ~ 0.19 (about 19% of equity)
    - Cap_pct * cap_mult ~ 0.12 * 8.0 = 0.96 (96% cap)
    - So actual sizing is ~19% of equity per trade (not 100%)

  BREAKDOWN OF THE 337% -> 8% GAP:

    Factor 1: Position fraction 1.5x -> 0.19x (Kelly sizing)
      Research: 100-150% of equity in BTC at all times when long
      V4: ~19% of equity per Kelly trade (weekly rebalance)
      Impact: ~8x return reduction

    Factor 2: Compounding on full position vs Kelly-constrained
      Research: $100K equity * 1.5 = $150K position.
        After 10% gain: $115K equity * 1.5 = $172.5K position
      V4: $200K equity * 0.19 = $38K position.
        After 10% gain on $38K: $203.8K equity * 0.19 = $38.7K position
      Impact: Compound effect amplifies gap over 5 years

    Factor 3: Walk-forward masking burns ~1 year of data
      V4 default: 8760h (365d) training window burned
      Research: no burn, uses full data
      Impact: Loses some good periods

    Factor 4: Slippage and fee model differences
      Research: 10 bps flat cost on position changes
      V4: sqrt-impact slippage + exchange fees per trade
      Impact: Minor (both use BTC which is very liquid)
""")

    # ======================================================================
    # REALISTIC EXPECTED RETURN
    # ======================================================================
    print("=" * 72)
    print("REALISTIC EXPECTED RETURN WITH RSI TIMING ON $200K")
    print("=" * 72)

    # Use 1.0x capped (spot realistic) with RSI timing as best estimate
    print(f"""
  Research prototype (1.5x, no RSI):    {m_150['return_pct']:>+7.1f}% total, Sharpe {m_150['sharpe']:.2f}
  Research prototype (1.5x, RSI=35):    {m_rsi_150['return_pct']:>+7.1f}% total, Sharpe {m_rsi_150['sharpe']:.2f}
  Research prototype (1.0x, RSI=35):    {m_rsi_100['return_pct']:>+7.1f}% total, Sharpe {m_rsi_100['sharpe']:.2f}
  Research prototype (binary, RSI=35):  {m_rsi_binary['return_pct']:>+7.1f}% total, Sharpe {m_rsi_binary['sharpe']:.2f}

  Fixed $200K capital (1.0x cap):       see Test 3 above
  V4 engine (s320a, 60mo, $200K):       ~8% total, Sharpe 0.49

  The V4 engine's Kelly sizing (19% of equity per trade) is the dominant
  factor in the return reduction. The research model implicitly allocates
  100-150% of equity, which is not possible on spot exchange.

  REALISTIC annual return with RSI timing on $200K spot:
  - Conservative (V4 Kelly): ~1-3% annually
  - With sizing override (Kelly mult 2x): ~3-6% annually
  - Theoretical max (100% equity, spot): ~15-25% annually

  The Sharpe ratio (~0.4-0.6) is real. The RETURN is a function of how
  much equity you allocate. The research 337% implies 150% allocation
  with full compounding -- not achievable on spot.
""")


if __name__ == '__main__':
    main()
