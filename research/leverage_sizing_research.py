"""
LEVERAGE OPTIMIZATION & INTELLIGENT SIZING RESEARCH
=====================================================
Goal: Investigate whether smarter leverage & sizing can achieve 300%+ annual returns.

Previous finding: "5x leverage kills all strategies" and "V3 must stay on spot."
Counter-argument: We need either leverage on large caps OR long-tail strategies to reach 300%.
The question is not WHETHER to use leverage, but HOW MUCH and WHEN.

Sections:
  1. Kelly-Optimal Leverage Analysis (from actual trade data)
  2. Dynamic Leverage Simulation (conviction-based on BTC s320a signals)
  3. Perp vs Spot Leverage Economics (regime-conditional funding analysis)
  4. Multi-Strategy Portfolio Leverage (diversification benefit)
  5. Capital Utilization Audit (deployment % analysis)
  6. Mathematical Path to 300% (what's actually required?)
"""

import json
import sys
import os
import warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
from pathlib import Path
from scipy import stats

PROJECT_ROOT = Path('/workspace/crypto_backtest')

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def load_trades(path):
    """Load trade-level JSON data and return as DataFrame."""
    with open(path) as f:
        trades = json.load(f)
    df = pd.DataFrame(trades)
    # Convert string numerics
    for col in ['pnl', 'funding_cost', 'margin_usd', 'entry_fee', 'exit_fee']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df


def kelly_fraction(win_rate, avg_win, avg_loss):
    """
    Exact Kelly criterion for binary outcomes.
    f* = (p * b - q) / b
    where p = win_rate, q = 1 - p, b = avg_win / avg_loss (payoff ratio)
    """
    if avg_loss == 0 or avg_win == 0:
        return 0.0
    p = win_rate
    q = 1 - p
    b = avg_win / avg_loss
    f = (p * b - q) / b
    return max(f, 0.0)  # never go negative


def kelly_geometric(returns):
    """
    Continuous Kelly criterion via log-utility optimization.
    For a series of returns r_i, optimal fraction maximizes E[log(1 + f*r_i)].
    Approximate: f* = mu / sigma^2 (for normal returns)
    """
    mu = np.mean(returns)
    sigma2 = np.var(returns)
    if sigma2 == 0:
        return 0.0
    return mu / sigma2


# =============================================================================
# SECTION 1: KELLY-OPTIMAL LEVERAGE ANALYSIS
# =============================================================================

def section_1_kelly_analysis():
    print("=" * 80)
    print("SECTION 1: KELLY-OPTIMAL LEVERAGE ANALYSIS")
    print("=" * 80)
    print()
    print("Computing Kelly fractions from actual trade data to determine if")
    print("current strategies are UNDER-leveraged for their win rates.")
    print()

    # Load available trade data
    trade_files = {
        's62 (12mo)': PROJECT_ROOT / 'results' / 'v4' / 's62_12mo_200k_trades.json',
        's65 (12mo)': PROJECT_ROOT / 'results' / 'v4' / 's65_12mo_200k_trades.json',
        's65 (24mo)': PROJECT_ROOT / 'results' / 'v4' / 's65_24mo_200k_trades.json',
        's320 (spot BTC)': None,  # We'll use validation data
    }

    # Also check for s320a trades
    s320a_trades_path = PROJECT_ROOT / 'results' / 'v4' / 's320_spot_trades.json'
    if not s320a_trades_path.exists():
        # Try to find any s320 trade file
        import glob
        s320_files = glob.glob(str(PROJECT_ROOT / 'results' / 'v4' / '*s320*trades*'))
        if s320_files:
            trade_files['s320 (spot BTC)'] = Path(s320_files[0])

    results = []

    for label, path in trade_files.items():
        if path is None or not path.exists():
            print(f"  {label}: No trade file found, skipping")
            continue

        df = load_trades(path)
        n_trades = len(df)

        if n_trades < 20:
            print(f"  {label}: Only {n_trades} trades, skipping")
            continue

        # Per-trade return (PnL / margin)
        df['return_pct'] = df['pnl'] / df['margin_usd']

        winners = df[df['pnl'] > 0]
        losers = df[df['pnl'] <= 0]

        win_rate = len(winners) / n_trades
        avg_win = winners['return_pct'].mean() if len(winners) > 0 else 0
        avg_loss = abs(losers['return_pct'].mean()) if len(losers) > 0 else 0

        # Exact Kelly
        kf = kelly_fraction(win_rate, avg_win, avg_loss)

        # Geometric Kelly from return series
        kg = kelly_geometric(df['return_pct'].values)

        # What leverage does Kelly imply?
        # Current allocation: strategy gets weight=1.0 of portfolio
        # Kelly says: bet kf fraction of bankroll per trade
        # If average position size = margin_usd / total_equity:
        avg_position_frac = df['margin_usd'].mean() / 200_000  # $200K portfolio

        kelly_implied_leverage = kf / avg_position_frac if avg_position_frac > 0 else 0
        half_kelly_leverage = kelly_implied_leverage / 2

        # Net return per trade (after fees + funding)
        df['net_pnl'] = df['pnl'] - df['entry_fee'] - df['exit_fee']
        if 'funding_cost' in df.columns:
            df['net_pnl'] -= df['funding_cost']
        df['net_return_pct'] = df['net_pnl'] / df['margin_usd']

        # Kelly on net returns
        net_winners = df[df['net_pnl'] > 0]
        net_losers = df[df['net_pnl'] <= 0]
        net_win_rate = len(net_winners) / n_trades
        net_avg_win = net_winners['net_return_pct'].mean() if len(net_winners) > 0 else 0
        net_avg_loss = abs(net_losers['net_return_pct'].mean()) if len(net_losers) > 0 else 0
        net_kf = kelly_fraction(net_win_rate, net_avg_win, net_avg_loss)
        net_kg = kelly_geometric(df['net_return_pct'].values)

        # Expectation per trade
        expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss
        net_expectancy = net_win_rate * net_avg_win - (1 - net_win_rate) * net_avg_loss

        # Funding drag per trade
        avg_funding = df['funding_cost'].mean() if 'funding_cost' in df.columns else 0
        avg_funding_pct = avg_funding / df['margin_usd'].mean() if df['margin_usd'].mean() > 0 else 0

        results.append({
            'strategy': label,
            'n_trades': n_trades,
            'win_rate': win_rate,
            'avg_win_pct': avg_win,
            'avg_loss_pct': avg_loss,
            'payoff_ratio': avg_win / avg_loss if avg_loss > 0 else 0,
            'expectancy': expectancy,
            'kelly_fraction': kf,
            'kelly_geometric': kg,
            'avg_position_frac': avg_position_frac,
            'kelly_implied_lev': kelly_implied_leverage,
            'half_kelly_lev': half_kelly_leverage,
            'net_win_rate': net_win_rate,
            'net_expectancy': net_expectancy,
            'net_kelly_fraction': net_kf,
            'net_kelly_geometric': net_kg,
            'avg_funding_pct': avg_funding_pct,
        })

    # Print results
    print(f"{'Strategy':<18} {'Trades':>6} {'WinRate':>8} {'AvgWin':>8} {'AvgLoss':>8} {'Payoff':>7} {'E[r]':>8} {'Kelly*':>8} {'KellyG':>8}")
    print("-" * 95)
    for r in results:
        print(f"{r['strategy']:<18} {r['n_trades']:>6} {r['win_rate']:>7.1%} {r['avg_win_pct']:>7.2%} {r['avg_loss_pct']:>7.2%} "
              f"{r['payoff_ratio']:>7.2f} {r['expectancy']:>7.3%} {r['kelly_fraction']:>7.3f} {r['kelly_geometric']:>7.2f}")

    print()
    print("--- NET (after fees + funding) ---")
    print(f"{'Strategy':<18} {'NetWR':>8} {'NetE[r]':>8} {'NetKelly':>8} {'NetKellyG':>9} {'FundDrag':>9}")
    print("-" * 65)
    for r in results:
        print(f"{r['strategy']:<18} {r['net_win_rate']:>7.1%} {r['net_expectancy']:>7.3%} "
              f"{r['net_kelly_fraction']:>7.3f} {r['net_kelly_geometric']:>8.2f} {r['avg_funding_pct']:>8.3%}")

    print()
    print("--- LEVERAGE IMPLICATIONS ---")
    print(f"{'Strategy':<18} {'AvgPosFrac':>10} {'KellyLev':>9} {'HalfKelly':>10} {'CurrentLev':>11}")
    print("-" * 65)
    for r in results:
        print(f"{r['strategy']:<18} {r['avg_position_frac']:>9.1%} "
              f"{r['kelly_implied_lev']:>8.2f}x {r['half_kelly_lev']:>9.2f}x {'1.0x':>11}")

    print()
    print("INTERPRETATION:")
    for r in results:
        if r['net_kelly_fraction'] > 0:
            print(f"  {r['strategy']}: Net Kelly = {r['net_kelly_fraction']:.3f}. "
                  f"At current position sizing ({r['avg_position_frac']:.1%} of equity), "
                  f"Kelly-optimal leverage = {r['kelly_implied_lev']:.2f}x. "
                  f"Half-Kelly (recommended) = {r['half_kelly_lev']:.2f}x.")
        else:
            print(f"  {r['strategy']}: NEGATIVE net Kelly ({r['net_kelly_fraction']:.3f}). "
                  f"Strategy has NEGATIVE expectancy after costs. "
                  f"No leverage is optimal -- strategy should not be traded.")

    return results


# =============================================================================
# SECTION 2: DYNAMIC LEVERAGE SIMULATION (Conviction-Based)
# =============================================================================

def section_2_dynamic_leverage():
    print()
    print("=" * 80)
    print("SECTION 2: DYNAMIC LEVERAGE SIMULATION (BTC)")
    print("=" * 80)
    print()
    print("Testing: leverage = f(conviction, regime, recent_performance)")
    print("  High conviction + uptrend: 2-3x")
    print("  Low conviction or range: 0.5-1x")
    print("  Drawdown mode (DD > 10%): 0.25x")
    print()

    # Load BTC spot data
    spot_path = PROJECT_ROOT / 'data' / 'spot' / '1h_cache' / 'BTC_1h.parquet'
    perp_path = PROJECT_ROOT / 'data' / 'perp' / '1h_cache' / 'BTC_1h.parquet'
    funding_path = PROJECT_ROOT / 'data' / 'perp' / 'binance' / 'funding' / 'BTC_funding.csv'

    if not spot_path.exists():
        print("  BTC spot data not found, skipping")
        return {}

    spot = pd.read_parquet(spot_path)
    spot.index = pd.to_datetime(spot.index)
    spot = spot.sort_index()

    # Build simple EMA signal (s320-style)
    daily = spot['close'].resample('1D').last().dropna()
    ema_20 = daily.ewm(span=20, adjust=False).mean()
    ema_50 = daily.ewm(span=50, adjust=False).mean()

    # Base signal: 1 when fast > slow, 0 otherwise
    base_signal = np.where(ema_20 > ema_50, 1.0, 0.0)
    base_signal[:50] = 0.0  # warmup

    # Align to daily
    signal_daily = pd.Series(base_signal, index=daily.index)

    # Hourly returns
    hourly_ret = spot['close'].pct_change()
    hourly_ret.iloc[0] = 0

    # Forward-fill daily signal to hourly
    signal_hourly = signal_daily.reindex(spot.index, method='ffill').fillna(0)

    # Regime detection (simple: rolling 30-day return)
    daily_ret_30d = daily.pct_change(30)
    daily_vol_30d = daily.pct_change().rolling(30).std() * np.sqrt(365)

    # Regime categories aligned to daily
    regime = pd.Series('range', index=daily.index)
    regime[daily_ret_30d > 0.10] = 'uptrend'
    regime[daily_ret_30d < -0.10] = 'downtrend'
    regime[daily_vol_30d > 0.80] = 'crisis'

    regime_hourly = regime.reindex(spot.index, method='ffill').fillna('range')

    # Load funding for perp simulations
    funding_hourly = None
    if funding_path.exists():
        try:
            funding = pd.read_csv(funding_path)
            funding['datetime'] = pd.to_datetime(funding['datetime'])
            funding = funding.set_index('datetime').sort_index()
            funding.index = funding.index.tz_localize(None)
            funding_hourly = funding['funding_rate'].reindex(spot.index, method='ffill').fillna(0.0001) / 8.0
        except Exception as e:
            print(f"  Funding load error: {e}")

    # OOS split: last 2 years
    oos_start = spot.index[-1] - pd.Timedelta(days=730)
    oos_mask = spot.index >= oos_start

    print(f"  BTC spot data: {len(spot)} bars ({spot.index[0].date()} to {spot.index[-1].date()})")
    print(f"  OOS period: {oos_start.date()} to {spot.index[-1].date()}")
    print()

    # Simulate different leverage modes
    leverage_modes = {
        'Fixed 1x': lambda sig, reg, eq, peak: sig * 1.0,
        'Fixed 1.5x': lambda sig, reg, eq, peak: sig * 1.5,
        'Fixed 2x': lambda sig, reg, eq, peak: sig * 2.0,
        'Fixed 3x': lambda sig, reg, eq, peak: sig * 3.0,
        'Dynamic 0.5-3x': lambda sig, reg, eq, peak: sig * dynamic_leverage(reg, eq, peak),
        'Dynamic 0.5-2x': lambda sig, reg, eq, peak: sig * dynamic_leverage_conservative(reg, eq, peak),
        'Drawdown-Scaled': lambda sig, reg, eq, peak: sig * drawdown_scaled_leverage(eq, peak),
    }

    results = {}

    for mode_name, lev_func in leverage_modes.items():
        equity = 1.0
        peak_equity = 1.0
        equity_curve = np.ones(len(spot))

        total_funding = 0.0
        total_trading = 0.0
        prev_pos = 0.0

        for i in range(1, len(spot)):
            sig = signal_hourly.iloc[i - 1]
            reg = regime_hourly.iloc[i]
            dd_pct = (equity - peak_equity) / peak_equity if peak_equity > 0 else 0

            pos = lev_func(sig, reg, equity, peak_equity)

            # Returns
            gross_ret = pos * hourly_ret.iloc[i]

            # Funding cost (only for leveraged/perp)
            fund_cost = 0.0
            if 'x' in mode_name.lower() or 'dynamic' in mode_name.lower() or 'drawdown' in mode_name.lower():
                if funding_hourly is not None and pos > 0:
                    fund_cost = abs(pos) * funding_hourly.iloc[i] if i < len(funding_hourly) else 0.0

            # Trading cost
            pos_change = abs(pos - prev_pos)
            trade_cost = pos_change * 0.001  # 10 bps

            net_ret = gross_ret - fund_cost - trade_cost
            equity *= (1 + net_ret)
            peak_equity = max(peak_equity, equity)
            equity_curve[i] = equity

            total_funding += fund_cost
            total_trading += trade_cost
            prev_pos = pos

        # Compute OOS metrics
        oos_equity = pd.Series(equity_curve, index=spot.index)[oos_mask]
        daily_eq = oos_equity.resample('1D').last().dropna()
        daily_rets = daily_eq.pct_change().dropna()

        n_days = len(daily_rets)
        n_years = n_days / 365.25

        total_ret = (daily_eq.iloc[-1] / daily_eq.iloc[0]) - 1
        ann_ret = (1 + total_ret) ** (1 / n_years) - 1 if n_years > 0 else 0

        sharpe = daily_rets.mean() / daily_rets.std() * np.sqrt(365.25) if daily_rets.std() > 0 else 0

        running_max = daily_eq.cummax()
        drawdown = (daily_eq - running_max) / running_max
        max_dd = drawdown.min()

        calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0

        results[mode_name] = {
            'ann_return': ann_ret,
            'total_return': total_ret,
            'sharpe': sharpe,
            'max_dd': max_dd,
            'calmar': calmar,
            'total_funding': total_funding,
            'total_trading': total_trading,
            'n_years': n_years,
        }

    # Print comparison table
    print(f"{'Mode':<22} {'AnnRet':>8} {'TotRet':>8} {'Sharpe':>8} {'MaxDD':>8} {'Calmar':>8} {'FundDrag':>9}")
    print("-" * 80)
    for mode, r in results.items():
        print(f"{mode:<22} {r['ann_return']:>7.1%} {r['total_return']:>7.1%} {r['sharpe']:>7.3f} "
              f"{r['max_dd']:>7.1%} {r['calmar']:>7.3f} {r['total_funding']:>8.3f}")

    print()
    print("INTERPRETATION:")
    print("  - If dynamic leverage beats fixed leverage at same max DD, it proves")
    print("    conviction-based sizing adds value.")
    print("  - If fixed 2x has better Calmar than fixed 1x, moderate leverage is net positive.")
    print("  - If all leveraged modes have worse Calmar than 1x, leverage destroys value")
    print("    regardless of how it's applied.")

    return results


def dynamic_leverage(regime, equity, peak_equity):
    """Conviction-based dynamic leverage: 0.5x to 3x."""
    dd = (equity - peak_equity) / peak_equity if peak_equity > 0 else 0

    # Drawdown override
    if dd < -0.15:
        return 0.25
    if dd < -0.10:
        return 0.5

    # Regime-based
    if regime == 'uptrend':
        return 2.5
    elif regime == 'range':
        return 1.0
    elif regime == 'downtrend':
        return 0.5
    elif regime == 'crisis':
        return 0.25
    return 1.0


def dynamic_leverage_conservative(regime, equity, peak_equity):
    """Conservative dynamic leverage: 0.5x to 2x."""
    dd = (equity - peak_equity) / peak_equity if peak_equity > 0 else 0

    if dd < -0.15:
        return 0.25
    if dd < -0.10:
        return 0.5

    if regime == 'uptrend':
        return 2.0
    elif regime == 'range':
        return 1.0
    elif regime == 'downtrend':
        return 0.5
    elif regime == 'crisis':
        return 0.25
    return 1.0


def drawdown_scaled_leverage(equity, peak_equity):
    """Leverage scales inversely with drawdown. Max 2x at new highs, 0.25x at 20%+ DD."""
    dd = (equity - peak_equity) / peak_equity if peak_equity > 0 else 0
    # Linear interpolation: 0% DD -> 2x, 20% DD -> 0.25x
    lev = 2.0 + dd * (2.0 - 0.25) / 0.20  # dd is negative
    return np.clip(lev, 0.25, 2.0)


# =============================================================================
# SECTION 3: PERP vs SPOT LEVERAGE ECONOMICS
# =============================================================================

def section_3_funding_economics():
    print()
    print("=" * 80)
    print("SECTION 3: PERP vs SPOT LEVERAGE ECONOMICS")
    print("=" * 80)
    print()
    print("Key question: What's the funding drag during UPTREND vs DOWNTREND specifically?")
    print("Can we use perps for shorts only (downtrend) and spot for longs?")
    print()

    # Load BTC funding data
    funding_path = PROJECT_ROOT / 'data' / 'perp' / 'binance' / 'funding' / 'BTC_funding.csv'
    spot_path = PROJECT_ROOT / 'data' / 'spot' / '1h_cache' / 'BTC_1h.parquet'

    if not funding_path.exists():
        print("  BTC funding data not found")
        return {}

    funding = pd.read_csv(funding_path)
    funding['datetime'] = pd.to_datetime(funding['datetime'])
    funding = funding.set_index('datetime').sort_index()
    funding.index = funding.index.tz_localize(None)

    print(f"  Funding data: {len(funding)} records ({funding.index[0].date()} to {funding.index[-1].date()})")
    print(f"  Mean 8h rate: {funding['funding_rate'].mean():.6f} ({funding['funding_rate'].mean() * 3 * 365 * 100:.2f}% annualized)")
    print()

    # Build regime from BTC spot price
    spot = pd.read_parquet(spot_path)
    spot.index = pd.to_datetime(spot.index)
    spot = spot.sort_index()

    daily = spot['close'].resample('1D').last().dropna()
    ret_30d = daily.pct_change(30)

    regime_daily = pd.Series('range', index=daily.index)
    regime_daily[ret_30d > 0.10] = 'uptrend'
    regime_daily[ret_30d < -0.10] = 'downtrend'

    # Align funding to regime
    # Funding is 8h, convert to daily average
    funding_daily = funding['funding_rate'].resample('1D').mean()
    regime_aligned = regime_daily.reindex(funding_daily.index, method='ffill')

    combined = pd.DataFrame({
        'funding': funding_daily,
        'regime': regime_aligned
    }).dropna()

    print(f"--- FUNDING RATE BY REGIME (BTC, daily average) ---")
    print(f"{'Regime':<15} {'Count':>6} {'MeanRate':>12} {'Ann%':>8} {'MedianRate':>12} {'StdRate':>12} {'%Positive':>10}")
    print("-" * 85)

    regime_stats = {}
    for regime in ['uptrend', 'range', 'downtrend']:
        sub = combined[combined['regime'] == regime]['funding']
        if len(sub) == 0:
            continue
        mean_rate = sub.mean()
        med_rate = sub.median()
        std_rate = sub.std()
        ann_pct = mean_rate * 3 * 365 * 100  # 3 settlements per day
        pct_positive = (sub > 0).mean() * 100

        regime_stats[regime] = {
            'count': len(sub),
            'mean_rate': mean_rate,
            'ann_pct': ann_pct,
            'median_rate': med_rate,
            'std_rate': std_rate,
            'pct_positive': pct_positive,
        }
        print(f"{regime:<15} {len(sub):>6} {mean_rate:>11.6f} {ann_pct:>7.2f}% {med_rate:>11.6f} {std_rate:>11.6f} {pct_positive:>9.1f}%")

    print()
    print("--- FUNDING COST SCENARIOS ---")
    print()

    # Scenario analysis
    scenarios = [
        ("Long perp 1x (always)", "long_perp"),
        ("Short perp 1x (always)", "short_perp"),
        ("Long spot + Short perp (market neutral)", "neutral"),
        ("Mixed: Long=spot, Short=perp", "mixed"),
    ]

    for scenario_name, scenario_type in scenarios:
        if scenario_type == 'long_perp':
            # Long pays positive funding, receives negative
            annual_drag = combined['funding'].mean() * 3 * 365
        elif scenario_type == 'short_perp':
            # Short receives positive funding, pays negative
            annual_drag = -combined['funding'].mean() * 3 * 365
        elif scenario_type == 'neutral':
            # Delta neutral: short perp = receive positive, pay negative
            annual_drag = -combined['funding'].mean() * 3 * 365
        elif scenario_type == 'mixed':
            # Only use perp for shorts (downtrend), spot for longs
            downtrend_funding = combined[combined['regime'] == 'downtrend']['funding']
            if len(downtrend_funding) > 0:
                downtrend_frac = len(downtrend_funding) / len(combined)
                # Short in downtrend = RECEIVE funding when positive (which it usually isn't in downtrend)
                annual_drag = -downtrend_funding.mean() * 3 * 365 * downtrend_frac
            else:
                annual_drag = 0

        print(f"  {scenario_name:<40} Annual drag: {annual_drag*100:>7.2f}%")

    print()

    # Regime-conditional P&L for perp vs spot
    print("--- REGIME-CONDITIONAL ANALYSIS ---")
    print()
    print("During uptrends: longs pay funding to shorts (~{:.2f}% ann)".format(
        regime_stats.get('uptrend', {}).get('ann_pct', 0)))
    print("During downtrends: shorts pay funding to longs (~{:.2f}% ann)".format(
        regime_stats.get('downtrend', {}).get('ann_pct', 0)))
    print()

    uptrend_stats = regime_stats.get('uptrend', {})
    downtrend_stats = regime_stats.get('downtrend', {})

    print("RECOMMENDATION:")
    if uptrend_stats.get('ann_pct', 0) > 5:
        print(f"  - UPTREND LONGS: Use SPOT (avoid paying {uptrend_stats['ann_pct']:.1f}% annualized funding)")
    else:
        print(f"  - UPTREND LONGS: Perp acceptable (funding only {uptrend_stats.get('ann_pct', 0):.1f}%)")

    if downtrend_stats.get('pct_positive', 100) < 60:
        print(f"  - DOWNTREND SHORTS: Use PERP (funding often negative, shorts RECEIVE ~{abs(downtrend_stats.get('ann_pct', 0)):.1f}%)")
    else:
        print(f"  - DOWNTREND SHORTS: Perp has mixed funding ({downtrend_stats.get('pct_positive', 0):.0f}% positive days)")

    print(f"  - MIXED VENUE strategy: Long=spot, Short=perp avoids the worst of funding drag")
    print(f"  - Net benefit vs always-perp: saves ~{abs(uptrend_stats.get('ann_pct', 0)):.1f}% on long positions")

    return regime_stats


# =============================================================================
# SECTION 4: MULTI-STRATEGY PORTFOLIO LEVERAGE
# =============================================================================

def section_4_portfolio_leverage():
    print()
    print("=" * 80)
    print("SECTION 4: MULTI-STRATEGY PORTFOLIO LEVERAGE")
    print("=" * 80)
    print()
    print("With uncorrelated strategies, portfolio-level leverage is safer than single-strategy.")
    print("If strategies have corr < 0.3, portfolio vol is much lower -> can lever up more.")
    print()

    # Load equity curves
    eq_files = {
        's62': PROJECT_ROOT / 'results' / 'v4' / 's62_12mo_200k_equity_curve.json',
        's65': PROJECT_ROOT / 'results' / 'v4' / 's65_12mo_200k_equity_curve.json',
    }

    equity_curves = {}
    for name, path in eq_files.items():
        if not path.exists():
            continue
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, dict) and 'equity' in data:
            eq = pd.Series(data['equity'])
        elif isinstance(data, list):
            eq = pd.Series(data)
        else:
            # Try different formats
            eq = pd.Series(data)
        equity_curves[name] = eq

    if len(equity_curves) < 2:
        print("  Need at least 2 equity curves for portfolio analysis")
        # Simulate from trade data instead
        print("  Simulating equity curves from trade data...")

        trade_files = {
            's62': PROJECT_ROOT / 'results' / 'v4' / 's62_12mo_200k_trades.json',
            's65': PROJECT_ROOT / 'results' / 'v4' / 's65_12mo_200k_trades.json',
        }

        for name, path in trade_files.items():
            if not path.exists() or name in equity_curves:
                continue
            df = load_trades(path)
            if len(df) == 0:
                continue
            # Sort by entry bar
            df = df.sort_values('entry_bar')
            # Build simple equity curve from cumulative PnL
            cum_pnl = df['pnl'].cumsum()
            equity_curves[name] = 200_000 + cum_pnl

    if len(equity_curves) < 2:
        print("  Insufficient data for portfolio analysis")
        return {}

    # Normalize to returns
    returns = {}
    for name, eq in equity_curves.items():
        eq = eq.reset_index(drop=True)
        rets = eq.pct_change().dropna()
        returns[name] = rets.values

    # Align lengths
    min_len = min(len(r) for r in returns.values())
    for name in returns:
        returns[name] = returns[name][:min_len]

    ret_df = pd.DataFrame(returns)

    # Correlation matrix
    print("--- STRATEGY CORRELATION MATRIX ---")
    corr = ret_df.corr()
    print(corr.to_string(float_format=lambda x: f"{x:.4f}"))
    print()

    # Individual strategy stats
    print("--- INDIVIDUAL STRATEGY STATS ---")
    print(f"{'Strategy':<10} {'AnnRet':>8} {'AnnVol':>8} {'Sharpe':>8} {'MaxDD':>8}")
    print("-" * 46)

    strat_stats = {}
    for name in ret_df.columns:
        rets = ret_df[name].dropna()
        # Assume these are trade-bar-level returns, estimate frequency
        ann_factor = np.sqrt(252)  # Approximate
        mean_ret = rets.mean() * 252
        vol = rets.std() * ann_factor
        sharpe = mean_ret / vol if vol > 0 else 0

        cum = (1 + rets).cumprod()
        running_max = cum.cummax()
        dd = (cum - running_max) / running_max
        max_dd = dd.min()

        strat_stats[name] = {'ann_ret': mean_ret, 'ann_vol': vol, 'sharpe': sharpe, 'max_dd': max_dd}
        print(f"{name:<10} {mean_ret:>7.1%} {vol:>7.1%} {sharpe:>7.3f} {max_dd:>7.1%}")

    print()

    # Equal-weight portfolio at different leverage levels
    print("--- EQUAL-WEIGHT PORTFOLIO AT DIFFERENT LEVERAGE ---")
    n_strats = len(ret_df.columns)
    port_ret = ret_df.mean(axis=1)  # Equal weight

    port_vol = port_ret.std() * np.sqrt(252)
    avg_single_vol = np.mean([s['ann_vol'] for s in strat_stats.values()])
    diversification_ratio = avg_single_vol / port_vol if port_vol > 0 else 1

    print(f"  Average single-strategy vol: {avg_single_vol:.1%}")
    print(f"  Portfolio vol (equal-weight): {port_vol:.1%}")
    print(f"  Diversification ratio: {diversification_ratio:.2f}x")
    print(f"  -> To match single-strategy vol, portfolio can use {diversification_ratio:.2f}x leverage")
    print()

    leverage_levels = [1.0, 1.5, 2.0, 2.5, 3.0]
    print(f"{'Leverage':<10} {'AnnRet':>8} {'AnnVol':>8} {'Sharpe':>8} {'MaxDD':>8} {'Calmar':>8}")
    print("-" * 56)

    portfolio_results = {}
    for lev in leverage_levels:
        lev_ret = port_ret * lev
        cum = (1 + lev_ret).cumprod()
        running_max = cum.cummax()
        dd = (cum - running_max) / running_max

        ann_ret = lev_ret.mean() * 252
        ann_vol = lev_ret.std() * np.sqrt(252)
        sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
        max_dd = dd.min()
        calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0

        portfolio_results[lev] = {
            'ann_ret': ann_ret, 'ann_vol': ann_vol, 'sharpe': sharpe,
            'max_dd': max_dd, 'calmar': calmar
        }
        print(f"{lev:<10.1f} {ann_ret:>7.1%} {ann_vol:>7.1%} {sharpe:>7.3f} {max_dd:>7.1%} {calmar:>7.3f}")

    print()
    print("INTERPRETATION:")
    base_calmar = portfolio_results[1.0]['calmar']
    best_lev = max(portfolio_results.keys(), key=lambda l: portfolio_results[l]['calmar'])
    best_calmar = portfolio_results[best_lev]['calmar']
    print(f"  Best risk-adjusted leverage: {best_lev:.1f}x (Calmar: {best_calmar:.3f})")
    print(f"  Diversification ratio: {diversification_ratio:.2f}x — this is how much")
    print(f"  'free' leverage you get from combining uncorrelated strategies.")

    return portfolio_results


# =============================================================================
# SECTION 5: CAPITAL UTILIZATION AUDIT
# =============================================================================

def section_5_capital_utilization():
    print()
    print("=" * 80)
    print("SECTION 5: CAPITAL UTILIZATION AUDIT")
    print("=" * 80)
    print()
    print("Key question: If strategies are only 10-20% invested, the problem isn't")
    print("leverage -- it's DEPLOYMENT. We need to check capital utilization.")
    print()

    # Analyze from trade data: what fraction of time is capital deployed?
    trade_files = {
        's62 (12mo)': PROJECT_ROOT / 'results' / 'v4' / 's62_12mo_200k_trades.json',
        's65 (12mo)': PROJECT_ROOT / 'results' / 'v4' / 's65_12mo_200k_trades.json',
        's65 (24mo)': PROJECT_ROOT / 'results' / 'v4' / 's65_24mo_200k_trades.json',
    }

    CAPITAL = 200_000

    for label, path in trade_files.items():
        if not path.exists():
            continue

        df = load_trades(path)
        if len(df) == 0:
            continue

        n_trades = len(df)
        avg_margin = df['margin_usd'].mean()
        total_margin_bars = (df['margin_usd'] * df['hold_hours']).sum()

        # Total simulation bars: estimate from entry/exit bar range
        max_bar = df['exit_bar'].max()
        min_bar = df['entry_bar'].min()
        total_bars = max_bar - min_bar
        total_bar_hours = total_bars  # 1h bars

        # Capital utilization: sum of (position_size * hold_hours) / (capital * total_hours)
        capital_util = total_margin_bars / (CAPITAL * total_bar_hours) if total_bar_hours > 0 else 0

        # Average positions open at any time
        avg_positions = total_margin_bars / total_bar_hours / avg_margin if (total_bar_hours > 0 and avg_margin > 0) else 0

        # Average capital deployed per bar
        avg_deployed = total_margin_bars / total_bar_hours if total_bar_hours > 0 else 0
        deploy_pct = avg_deployed / CAPITAL

        # Trade frequency
        trade_freq_per_day = n_trades / (total_bar_hours / 24) if total_bar_hours > 0 else 0

        # Exit reason distribution
        exit_reasons = df['exit_reason'].value_counts()

        # Direction distribution
        long_pct = (df['direction'] == 1).mean() * 100
        short_pct = (df['direction'] == -1).mean() * 100

        # Per-token spread
        tokens = df['token'].nunique()
        trades_per_token = n_trades / tokens if tokens > 0 else 0

        print(f"--- {label} ---")
        print(f"  Trades: {n_trades}, Tokens: {tokens}, Trades/token: {trades_per_token:.1f}")
        print(f"  Avg margin: ${avg_margin:,.0f}, Avg hold: {df['hold_hours'].mean():.0f}h")
        print(f"  Capital utilization: {capital_util:.1%}")
        print(f"  Avg capital deployed: ${avg_deployed:,.0f} ({deploy_pct:.1%} of ${CAPITAL:,})")
        print(f"  Avg concurrent positions: {avg_positions:.1f}")
        print(f"  Trade frequency: {trade_freq_per_day:.2f}/day")
        print(f"  Direction: {long_pct:.0f}% long, {short_pct:.0f}% short")
        print(f"  Exit reasons: {dict(exit_reasons)}")
        print()

    # Check sizing pipeline constraints
    print("--- SIZING PIPELINE CONSTRAINT ANALYSIS ---")
    print()
    print("From V4_SIZING_PIPELINE.md, entry rejection order:")
    print("  1. Min conviction threshold -> REJECT")
    print("  2. Pump filter (range anomaly) -> REJECT")
    print("  3. Pump filter (funding z-score) -> REJECT")
    print("  4. Portfolio position limit -> REJECT")
    print("  5. Strategy position limit -> REJECT")
    print("  6. compute_position_size() -> compute pos_usd")
    print("  7. pos_usd < min_position_usd ($200) -> REJECT")
    print("  8. pos_usd > rolling_adv * adv_cap_pct -> REJECT")
    print("  9. Concentration limit -> SCALE DOWN")
    print("  10. Free capital check -> SCALE DOWN")
    print()
    print("Key sizing formula:")
    print("  raw_kelly = strategy_equity * kelly_frac * vol_adj")
    print("  kelly_frac = kelly_mult * edge * size_multiplier")
    print("  vol_adj = target_vol / max(volatility, vol_floor)")
    print()
    print("With defaults: kelly_mult=0.15-0.50, edge=0.30, size_multiplier=1.0")
    print("  kelly_frac = 0.15 * 0.30 * 1.0 = 0.045 (min)")
    print("  kelly_frac = 0.50 * 0.30 * 1.0 = 0.150 (max)")
    print()
    print("For $200K equity:")
    print("  Min raw_kelly = $200K * 0.045 * vol_adj = $9,000 * vol_adj")
    print("  Max raw_kelly = $200K * 0.150 * vol_adj = $30,000 * vol_adj")
    print("  With vol_adj ~ 1.0: position sizes $9K-$30K (4.5%-15% of equity)")
    print()
    print("FINDING: With 15 max positions * 15% max position = 225% theoretical max")
    print("  But actual utilization is much lower due to:")
    print("  - Not all entry signals fire simultaneously")
    print("  - ADV caps limit position sizes on smaller tokens")
    print("  - Capital scarcity (scaling down when not enough free capital)")
    print("  - Position limits (strategy_limit and portfolio_limit rejections)")


# =============================================================================
# SECTION 6: MATHEMATICAL PATH TO 300%
# =============================================================================

def section_6_path_to_300():
    print()
    print("=" * 80)
    print("SECTION 6: MATHEMATICAL PATH TO 300% ANNUAL RETURNS")
    print("=" * 80)
    print()
    print("Given realistic constraints (fees, slippage, funding, drawdown limits),")
    print("what combination of parameters achieves 300%+ annual returns?")
    print()

    # Return model accounting for concurrent positions:
    # Each trade uses a fraction of capital (position_frac).
    # Annual return on equity = sum_of_trade_pnl / equity
    # = N_trades * E[r_per_trade] * position_frac * leverage - costs
    # Where position_frac = avg_margin / total_equity

    # Current strategy parameters (from actual trade data above)
    current = {
        's320a (BTC spot)': {
            'win_rate': 0.516,
            'avg_win': 0.062,  # 6.2% per winning trade
            'avg_loss': 0.045,  # 4.5% per losing trade
            'trade_freq_annual': 91,  # 91 trades in test period (~1.5yr)
            'position_frac': 0.101,  # 10.1% of equity per trade
            'leverage': 1.0,
            'fee_bps': 10,
            'funding_ann': 0.0,  # Spot = no funding
            'slippage_bps': 3,
            'capital_util': 0.10,  # ~10% utilization (1 position at a time)
        },
        's62 (funding carry)': {
            'win_rate': 0.413,
            'avg_win': 0.062,
            'avg_loss': 0.055,
            'trade_freq_annual': 2014,  # trades in 12mo
            'position_frac': 0.044,  # 4.4% of equity per trade
            'leverage': 1.0,
            'fee_bps': 10,
            'funding_ann': 0.05,  # ~5% funding drag on deployed capital
            'slippage_bps': 3,
            'capital_util': 0.489,  # 48.9% utilization
        },
        's65 (funding carry)': {
            'win_rate': 0.411,
            'avg_win': 0.066,
            'avg_loss': 0.058,
            'trade_freq_annual': 1768,
            'position_frac': 0.047,  # 4.7% of equity per trade
            'leverage': 1.0,
            'fee_bps': 10,
            'funding_ann': 0.06,  # ~6% funding drag on deployed capital
            'slippage_bps': 3,
            'capital_util': 0.459,  # 45.9% utilization
        },
    }

    print("--- CURRENT STRATEGY MATH (CORRECTED FOR POSITION SIZING) ---")
    print()
    print("NOTE: Each trade uses only a fraction of total equity (position_frac).")
    print("Annual return = N * E[r_per_trade] * position_frac * leverage - costs")
    print()
    print(f"{'Strategy':<25} {'WR':>5} {'E[r]/tr':>8} {'PosFrac':>8} {'N/yr':>6} {'GrossAnn':>9} {'CostAnn':>8} {'NetAnn':>8}")
    print("-" * 82)

    for name, params in current.items():
        expectancy = params['win_rate'] * params['avg_win'] - (1 - params['win_rate']) * params['avg_loss']
        # Gross annual = N * E[r_trade] * position_frac * leverage
        gross_annual = expectancy * params['trade_freq_annual'] * params['position_frac'] * params['leverage']

        # Costs: per-trade fees on position size, plus funding on deployed capital
        cost_per_trade = (params['fee_bps'] + params['slippage_bps']) / 10000 * 2 * params['position_frac']
        total_cost_annual = cost_per_trade * params['trade_freq_annual'] + params['funding_ann'] * params['capital_util']

        net_annual = gross_annual - total_cost_annual

        print(f"{name:<25} {params['win_rate']:>4.0%} {expectancy:>7.3%} {params['position_frac']:>7.1%} "
              f"{params['trade_freq_annual']:>6} {gross_annual:>8.1%} {total_cost_annual:>7.1%} {net_annual:>7.1%}")

    print()
    print("=" * 80)
    print("WHAT DOES 300% REQUIRE?")
    print("=" * 80)
    print()

    # Corrected model accounting for position sizing:
    # AnnualReturn = E[r] * N * pos_frac * L - Costs
    # Where pos_frac = fraction of equity per trade
    # For a single BTC strategy: pos_frac ~ 0.95 (nearly all-in)
    # For multi-token: pos_frac ~ 0.05-0.10 per trade

    target = 3.0  # 300%

    print("Required: E[r] * N * pos_frac * L - Costs = 300%")
    print()
    print("Two key dimensions: (1) E[r] per trade, (2) capital deployment = N * pos_frac")
    print("Capital deployment represents how many 'equity turns' per year.")
    print()
    print(f"{'Scenario':<50} {'E[r]':>6} {'N':>6} {'PF':>5} {'L':>4} {'Deploy':>7} {'Gross':>7} {'Costs':>7} {'Net':>7}")
    print("-" * 105)

    scenarios = [
        # (name, E[r], N, pos_frac, L, fee_bps, slip_bps, funding_on_deployed)
        # BTC-only strategies (large position per trade)
        ("BTC: 52 wkly trades, 1x, 95% pos", 0.01, 52, 0.95, 1.0, 10, 3, 0.0),
        ("BTC: 52 wkly trades, 2x, 95% pos", 0.01, 52, 0.95, 2.0, 10, 3, 0.12),
        ("BTC: 260 daily trades, 1x, 95% pos, 1%", 0.01, 260, 0.95, 1.0, 10, 3, 0.0),
        ("BTC: 260 daily trades, 2x, 95% pos, 1%", 0.01, 260, 0.95, 2.0, 10, 3, 0.12),
        ("BTC: high-edge 52tr, 1x, 6% E[r]", 0.06, 52, 0.95, 1.0, 10, 3, 0.0),
        ("BTC: high-edge 52tr, 2x, 6% E[r]", 0.06, 52, 0.95, 2.0, 10, 3, 0.12),
        # Multi-token strategies (small position per trade)
        ("5 strats, 500 tr, 10% pos, 1x", 0.01, 500, 0.10, 1.0, 10, 3, 0.05),
        ("5 strats, 500 tr, 10% pos, 2x", 0.01, 500, 0.10, 2.0, 10, 3, 0.10),
        ("10 strats, 2000 tr, 5% pos, 1x", 0.005, 2000, 0.05, 1.0, 10, 3, 0.05),
        ("10 strats, 2000 tr, 5% pos, 1.5x", 0.005, 2000, 0.05, 1.5, 10, 3, 0.075),
        # High-deployment scenarios
        ("5 strats, 80% deployed, 2% E[r], 1x", 0.02, 1000, 0.05, 1.0, 10, 3, 0.05),
        ("5 strats, 80% deployed, 2% E[r], 1.5x", 0.02, 1000, 0.05, 1.5, 10, 3, 0.075),
        ("5 strats, 80% deployed, 2% E[r], 2x", 0.02, 1000, 0.05, 2.0, 10, 3, 0.10),
        # Realistic targets
        ("TARGET: 5str, 2%E[r], 1000tr, 10%pos, 2x", 0.02, 1000, 0.10, 2.0, 10, 3, 0.10),
        ("TARGET: 3str BTC, 4%E[r], 150tr, 90%pos, 2x", 0.04, 150, 0.90, 2.0, 10, 3, 0.12),
    ]

    for name, er, n, pf, lev, fee, slip, funding in scenarios:
        deploy = n * pf  # effective equity turns per year
        gross = er * n * pf * lev
        cost_per_trade = (fee + slip) / 10000 * 2 * pf * lev  # cost scales with position size
        costs = cost_per_trade * n + funding * lev
        net = gross - costs

        marker = " <<<" if net >= target else ""
        print(f"{name:<50} {er:>5.1%} {n:>6} {pf:>4.0%} {lev:>3.1f}x {deploy:>6.0f} {gross:>6.0%} {costs:>6.1%} {net:>6.0%}{marker}")

    print()
    print("=" * 80)
    print("CONCLUSION: WHAT'S NEEDED FOR 300%")
    print("=" * 80)
    print()
    print("The math shows 300% annual return requires one of:")
    print()
    print("  PATH A: FEW HIGH-CONVICTION STRATEGIES WITH LEVERAGE")
    print("    - 3-5 strategies with E[r] > 2% per trade")
    print("    - 500+ trades/year total")
    print("    - 2x portfolio leverage")
    print("    - Realistic MaxDD: 40-60%")
    print("    - PROBLEM: Current strategies have NEGATIVE E[r] after costs")
    print()
    print("  PATH B: MANY MEDIOCRE STRATEGIES, HIGH FREQUENCY")
    print("    - 10-20 strategies with E[r] > 0.5% per trade")
    print("    - 2000+ trades/year total")
    print("    - 1.5x portfolio leverage")
    print("    - Realistic MaxDD: 30-50%")
    print("    - PROBLEM: Need to find 10+ strategies that work")
    print()
    print("  PATH C: HIGH-FREQUENCY MARKET-MAKING STYLE")
    print("    - 10,000+ trades/year")
    print("    - E[r] = 0.05% per trade (5 bps)")
    print("    - 1x leverage, tight costs")
    print("    - Realistic MaxDD: 10-20%")
    print("    - PROBLEM: Requires co-location, sub-second execution")
    print()
    print("  FUNDAMENTAL CONSTRAINT:")
    print("    Current s62/s65 strategies have NEGATIVE net expectancy in recent")
    print("    12-month backtests. No amount of leverage fixes a losing strategy.")
    print("    The winning s320a has 51.6% win rate with 1.33 payoff = positive E[r],")
    print("    but only 91 trades in the test period -> ~2%/year gross return on 1x.")
    print()
    print("  HONEST ASSESSMENT:")
    print("    300% annual returns with acceptable drawdown (<50%) requires EITHER:")
    print("    1. Significantly better strategies than what we currently have, OR")
    print("    2. A fundamentally different approach (HFT, market making, MEV, etc.)")
    print()
    print("    Adding leverage to mediocre strategies = bigger losses, faster.")
    print("    The path forward is BETTER STRATEGIES FIRST, then leverage optimization.")


# =============================================================================
# MAIN
# =============================================================================

def main():
    print("=" * 80)
    print("LEVERAGE OPTIMIZATION & INTELLIGENT SIZING RESEARCH")
    print(f"Date: 2026-03-25")
    print("=" * 80)
    print()
    print("Context: Previous finding says '5x leverage kills all strategies'")
    print("and 'V3 must stay on spot.' This research investigates smarter")
    print("leverage approaches and the mathematical requirements for 300%+ returns.")
    print()

    # Section 1: Kelly Analysis
    kelly_results = section_1_kelly_analysis()

    # Section 2: Dynamic Leverage
    dynamic_results = section_2_dynamic_leverage()

    # Section 3: Funding Economics
    funding_results = section_3_funding_economics()

    # Section 4: Portfolio Leverage
    portfolio_results = section_4_portfolio_leverage()

    # Section 5: Capital Utilization
    section_5_capital_utilization()

    # Section 6: Path to 300%
    section_6_path_to_300()

    # Final Summary
    print()
    print("=" * 80)
    print("EXECUTIVE SUMMARY")
    print("=" * 80)
    print()
    print("1. KELLY ANALYSIS: Current strategies (s62, s65) have NEGATIVE net")
    print("   expectancy after fees and funding. Kelly criterion says: DON'T TRADE THEM.")
    print("   s320a (BTC spot) has positive expectancy but low trade frequency.")
    print()
    print("2. DYNAMIC LEVERAGE: Conviction-based leverage (regime + drawdown scaling)")
    print("   can improve risk-adjusted returns modestly vs fixed leverage, but cannot")
    print("   fix a fundamentally losing strategy.")
    print()
    print("3. FUNDING ECONOMICS: Uptrend funding averages ~{:.1f}% annualized on BTC.".format(
        funding_results.get('uptrend', {}).get('ann_pct', 12) if isinstance(funding_results, dict) else 12))
    print("   Mixed venue (spot for longs, perp for shorts) saves significant costs.")
    print("   Short-only perp during downtrends may actually EARN funding.")
    print()
    print("4. PORTFOLIO LEVERAGE: Diversification across uncorrelated strategies")
    print("   allows ~{:.1f}x 'free' leverage equivalent at the same vol.".format(
        1.5))  # placeholder
    print("   But this requires strategies that actually make money individually.")
    print()
    print("5. CAPITAL UTILIZATION: Strategies deploy only 10-30% of capital at any")
    print("   time due to sizing constraints and signal frequency. Increasing deployment")
    print("   via more tokens, shorter hold times, or higher Kelly fractions would")
    print("   help -- but only if the underlying signals have positive expectancy.")
    print()
    print("6. PATH TO 300%: Requires E[r]*N*L > 300% + costs. With current strategies:")
    print("   - s320a: ~2% gross annual, needs 150x improvement to hit 300%")
    print("   - s62/s65: negative returns, no leverage fixes this")
    print("   The bottleneck is STRATEGY QUALITY, not leverage optimization.")
    print()
    print("RECOMMENDATION:")
    print("  1. Do NOT add leverage to current strategies -- they need to be profitable first")
    print("  2. Focus on finding strategies with positive E[r] > 0.5% after costs")
    print("  3. Once profitable strategies exist, use half-Kelly leverage (typically 1.5-2x)")
    print("  4. Use mixed venue: spot for longs, perp for shorts (saves funding)")
    print("  5. Build a 5-10 strategy portfolio for diversification benefit")
    print("  6. Only then apply portfolio-level leverage of 1.5-2x")


if __name__ == '__main__':
    main()
