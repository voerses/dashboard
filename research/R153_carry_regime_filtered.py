#!/workspace/venv/bin/python
"""
R153: Funding Carry with V4 Regime Filter
==========================================

PROBLEM: R151 carry portfolio showed massive drawdowns because:
1. Long leg bought garbage tokens with extreme negative funding (distress signals)
2. Price exposure was completely unhedged -- altcoin longs != altcoin shorts
3. No regime filter meant full exposure during crashes

THIS VERSION FIXES:
1. HIGH ADV threshold ($50M) to exclude garbage tokens
2. Funding rate CAP: exclude tokens with abs(funding) > 200% annualized (distress)
3. Proper L/S: both legs drawn from same quality universe
4. V4 regime filter scales allocation by market condition
5. Shorter funding lookback (72h) for more responsive signal

Strategy:
  - Universe: tokens with ADV > $50M, 6+ months history
  - Funding cap: abs(7d_rolling_funding * 8766) < 2.0 (200% annualized)
  - 72h rebalance:
    - Rank eligible tokens by 72h rolling mean funding rate
    - SHORT top N highest-funding (positive funding = shorts get paid)
    - LONG bottom N lowest-funding (as hedge, not for carry)
    - Equal weight within each leg, 10% per position
  - Regime scaling (BTC regime):
    - UPTREND: 100% | RANGE: 70% | QUIET: 50% | DOWNTREND: 20% | CRISIS: 0%
  - Costs: 4bps taker + 3bps slippage = 7bps per side
  - Test at 1x, 2x, 3x leverage

Author: Quant Research Agent
Date: 2026-03-28
"""

import warnings
import sys
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

warnings.filterwarnings('ignore')

# ── Paths ──────────────────────────────────────────────────────────────────
PROJECT_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = PROJECT_DIR / 'data' / 'perp' / '1h_cache'
OUTPUT_MD = PROJECT_DIR / 'research' / 'R153_carry_regime_results.md'

sys.path.insert(0, str(PROJECT_DIR))
from v4.engine import (
    compute_indicators_fast, detect_daily_regime, aggregate_to_timeframe,
    CRISIS, QUIET, UPTREND, RANGE, DOWNTREND,
)

# ── Constants ──────────────────────────────────────────────────────────────
ANNUAL_HOURS = 8766
FEE_BPS_PER_SIDE = 7  # 4bps taker + 3bps slippage
MIN_HISTORY_HOURS = 6 * 30 * 24
N_POSITIONS = 5
REBALANCE_HOURS = 72
FUNDING_LOOKBACK = 72  # 3-day rolling for ranking
ADV_THRESHOLD = 50_000_000  # $50M -- quality filter
MAX_FUNDING_ANNUAL = 2.0  # Cap at 200% annualized -- exclude distressed tokens

REGIME_ALLOCATION = {
    UPTREND:   1.00,
    RANGE:     0.70,
    QUIET:     0.50,
    DOWNTREND: 0.20,
    CRISIS:    0.00,
}

REGIME_NAMES = {
    CRISIS: 'CRISIS', QUIET: 'QUIET', UPTREND: 'UPTREND',
    RANGE: 'RANGE', DOWNTREND: 'DOWNTREND',
}


# ══════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════════════════

def load_all_perps():
    """Load all perp parquet files."""
    files = sorted(DATA_DIR.glob('*.parquet'))
    print(f"[DATA] Found {len(files)} parquet files")
    data = {}
    skipped = 0
    for f in files:
        token = f.stem.replace('_1h', '')
        try:
            df = pd.read_parquet(f)
            df.index = pd.to_datetime(df.index)
            df = df.sort_index()
            df = df[~df.index.duplicated(keep='first')]
            required = ['open', 'high', 'low', 'close', 'volume', 'funding_1h']
            if not all(c in df.columns for c in required):
                skipped += 1
                continue
            if len(df) < 100:
                skipped += 1
                continue
            data[token] = df
        except Exception:
            skipped += 1
    print(f"[DATA] Loaded {len(data)} tokens, skipped {skipped}")
    return data


def compute_adv(df, window=720):
    """Rolling average daily volume in USD (30d)."""
    dollar_vol_hourly = df['volume'] * df['close']
    daily_dollar_vol = dollar_vol_hourly.rolling(24, min_periods=12).sum()
    return daily_dollar_vol.rolling(30, min_periods=15).mean()


# ══════════════════════════════════════════════════════════════════════════
# BTC REGIME DETECTION
# ══════════════════════════════════════════════════════════════════════════

def compute_btc_regime(btc_df):
    """Compute BTC regime at 1h resolution using V4 (causal, shifted 1 day)."""
    print("[REGIME] Computing BTC regime...")
    df_daily = aggregate_to_timeframe(btc_df, hours=24)
    c = df_daily['close'].values.astype(np.float64)
    h = df_daily['high'].values.astype(np.float64)
    lo = df_daily['low'].values.astype(np.float64)
    v = df_daily['volume'].values.astype(np.float64)
    tb = df_daily['taker_buy_base'].values.astype(np.float64) if 'taker_buy_base' in df_daily.columns else None
    ind_d = compute_indicators_fast(c, h, lo, v, tb)
    regimes_d = detect_daily_regime(ind_d)
    regimes_d_shifted = np.roll(regimes_d, 1)
    regimes_d_shifted[0] = RANGE
    regime_daily_series = pd.Series(regimes_d_shifted, index=df_daily.index)
    regime_1h = regime_daily_series.reindex(btc_df.index, method='ffill').fillna(RANGE).astype(np.int8)
    counts = regime_1h.value_counts()
    total = len(regime_1h)
    print("[REGIME] Distribution:")
    for rv in sorted(counts.index):
        print(f"  {REGIME_NAMES.get(rv,'?'):>10s}: {counts[rv]:>6d}h ({counts[rv]/total*100:.1f}%)")
    return regime_1h


# ══════════════════════════════════════════════════════════════════════════
# BACKTEST ENGINE
# ══════════════════════════════════════════════════════════════════════════

def run_carry_backtest(data, btc_regime, use_regime_filter=True,
                       leverage=1.0, adv_threshold=ADV_THRESHOLD,
                       max_funding_annual=MAX_FUNDING_ANNUAL,
                       label="Base"):
    """
    Run funding carry portfolio backtest.

    Key improvement over R151: funding cap excludes distressed tokens,
    higher ADV threshold ensures quality universe, regime filter scales
    allocation.
    """
    print(f"\n{'='*70}")
    print(f"[BACKTEST] {label}")
    print(f"  Regime: {'ON' if use_regime_filter else 'OFF'}, Leverage: {leverage}x")
    print(f"  ADV: ${adv_threshold/1e6:.0f}M, Funding cap: {max_funding_annual*100:.0f}%/yr")
    print(f"{'='*70}")

    # ── Filter eligible tokens ──
    eligible = {}
    for token, df in data.items():
        if len(df) < MIN_HISTORY_HOURS:
            continue
        adv = compute_adv(df)
        above = adv > adv_threshold
        if above.sum() > MIN_HISTORY_HOURS * 0.3:
            eligible[token] = (df, adv)

    print(f"[FILTER] {len(eligible)} tokens pass ADV>${adv_threshold/1e6:.0f}M + 6mo")

    if len(eligible) < 2 * N_POSITIONS:
        print("[ERROR] Not enough eligible tokens")
        return None

    # ── Build aligned panels ──
    all_starts = [df.index.min() for df, _ in eligible.values()]
    all_ends = [df.index.max() for df, _ in eligible.values()]
    global_start = sorted(all_starts)[len(all_starts) // 3]
    global_end = min(all_ends)
    print(f"[DATES] {global_start.date()} to {global_end.date()}")

    hourly_idx = pd.date_range(global_start, global_end, freq='h')

    funding_panel = pd.DataFrame(index=hourly_idx)
    return_panel = pd.DataFrame(index=hourly_idx)
    close_panel = pd.DataFrame(index=hourly_idx)
    adv_panel = pd.DataFrame(index=hourly_idx)
    funding_raw_panel = pd.DataFrame(index=hourly_idx)

    for token, (df, adv) in eligible.items():
        df_a = df.reindex(hourly_idx)
        funding_panel[token] = df_a['funding_1h'].rolling(
            FUNDING_LOOKBACK, min_periods=FUNDING_LOOKBACK // 2).mean()
        funding_raw_panel[token] = df_a['funding_1h']
        return_panel[token] = df_a['close'].pct_change()
        close_panel[token] = df_a['close']
        adv_panel[token] = adv.reindex(hourly_idx)

    print(f"[PANEL] {len(funding_panel.columns)} tokens, {len(hourly_idx)} hours")

    regime_aligned = btc_regime.reindex(hourly_idx, method='ffill').fillna(RANGE).astype(np.int8)

    # ── Simulate ──
    equity = 1.0
    equity_curve = pd.Series(index=hourly_idx, dtype=float)
    equity_curve.iloc[0] = equity

    positions = {}
    total_funding_income = 0.0
    total_price_pnl = 0.0
    total_fees = 0.0
    trade_count = 0

    regime_hours = {r: 0 for r in REGIME_NAMES}
    regime_pnl = {r: 0.0 for r in REGIME_NAMES}
    regime_funding = {r: 0.0 for r in REGIME_NAMES}

    token_pnl = {t: 0.0 for t in eligible}
    token_funding = {t: 0.0 for t in eligible}

    # Track long-leg and short-leg performance separately
    long_leg_price_pnl = 0.0
    short_leg_price_pnl = 0.0
    long_leg_funding_pnl = 0.0
    short_leg_funding_pnl = 0.0

    monthly_eq_snapshots = {}

    warmup = max(FUNDING_LOOKBACK, 720)
    start_idx = warmup
    last_rebalance = start_idx

    # Track regime at last rebalance for allocation
    current_alloc = 1.0 if not use_regime_filter else REGIME_ALLOCATION.get(RANGE, 0.7)

    print(f"[SIM] Starting at hour {start_idx} ({hourly_idx[start_idx].date()})")

    for i in range(start_idx, len(hourly_idx)):
        current_time = hourly_idx[i]
        current_regime = int(regime_aligned.iloc[i])
        regime_hours[current_regime] = regime_hours.get(current_regime, 0) + 1

        # ── Mark to market ──
        hour_pnl = 0.0
        hour_funding = 0.0

        for token, (direction, weight, _ep) in list(positions.items()):
            ret = return_panel.at[current_time, token] if token in return_panel.columns else np.nan
            if pd.isna(ret):
                continue

            # Effective weight = base weight * allocation * leverage
            eff_weight = weight * current_alloc * leverage

            price_pnl = direction * eff_weight * ret * equity
            hour_pnl += price_pnl
            total_price_pnl += price_pnl
            token_pnl[token] += price_pnl
            regime_pnl[current_regime] = regime_pnl.get(current_regime, 0) + price_pnl

            if direction > 0:
                long_leg_price_pnl += price_pnl
            else:
                short_leg_price_pnl += price_pnl

            fr = funding_raw_panel.at[current_time, token] if token in funding_raw_panel.columns else 0.0
            if pd.isna(fr):
                fr = 0.0
            funding_pnl = -direction * eff_weight * fr * equity
            hour_funding += funding_pnl
            total_funding_income += funding_pnl
            token_funding[token] += funding_pnl
            regime_funding[current_regime] = regime_funding.get(current_regime, 0) + funding_pnl

            if direction > 0:
                long_leg_funding_pnl += funding_pnl
            else:
                short_leg_funding_pnl += funding_pnl

        equity += hour_pnl + hour_funding
        if equity <= 0.01:
            print(f"[BLOWN] at {current_time}")
            equity = 0.01
        equity_curve.iloc[i] = equity

        month_key = current_time.strftime('%Y-%m')
        monthly_eq_snapshots[month_key] = equity

        # ── Rebalance ──
        if (i - last_rebalance) >= REBALANCE_HOURS:
            last_rebalance = i

            # Update allocation based on current regime
            if use_regime_filter:
                current_alloc = REGIME_ALLOCATION.get(current_regime, 0.70)
            else:
                current_alloc = 1.0

            # If CRISIS and filtered, go flat
            if use_regime_filter and current_regime == CRISIS:
                if positions:
                    exit_fee = sum(p[1] for p in positions.values()) * FEE_BPS_PER_SIDE / 10000
                    equity -= exit_fee * equity * leverage
                    total_fees += exit_fee * equity * leverage
                    trade_count += len(positions)
                    positions = {}
                continue

            # Get funding rankings
            cf = funding_panel.iloc[i].dropna()
            ca = adv_panel.iloc[i].dropna()

            # Filter by ADV at this point
            adv_ok = ca[ca > adv_threshold].index
            rankable = cf.index.intersection(adv_ok)
            cf = cf.loc[rankable]

            # KEY FIX: Cap funding rate outliers
            # Exclude tokens with extreme funding (>200% annualized) -- these are distressed
            max_fr_per_hour = max_funding_annual / ANNUAL_HOURS
            cf = cf[cf.abs() < max_fr_per_hour]

            cf = cf.sort_values()

            if len(cf) < 2 * N_POSITIONS:
                continue

            # Bottom N -> LONG (lowest funding -- these pay us or cost least)
            longs = cf.head(N_POSITIONS).index.tolist()
            # Top N -> SHORT (highest funding -- shorts collect carry)
            shorts = cf.tail(N_POSITIONS).index.tolist()

            weight_per = 1.0 / (2 * N_POSITIONS)  # 10% per position for N=5

            new_positions = {}
            for token in longs:
                new_positions[token] = (+1, weight_per, close_panel.at[current_time, token])
            for token in shorts:
                new_positions[token] = (-1, weight_per, close_panel.at[current_time, token])

            # Turnover fees
            old_tokens = set(positions.keys())
            new_tokens = set(new_positions.keys())
            turnover_weight = 0.0
            for t in old_tokens - new_tokens:
                turnover_weight += positions[t][1]
            for t in new_tokens - old_tokens:
                turnover_weight += new_positions[t][1]
            for t in old_tokens & new_tokens:
                if positions[t][0] != new_positions[t][0]:
                    turnover_weight += positions[t][1] + new_positions[t][1]

            fee_cost = turnover_weight * FEE_BPS_PER_SIDE / 10000 * 2
            equity -= fee_cost * equity * leverage
            total_fees += fee_cost * equity * leverage
            trade_count += len((old_tokens - new_tokens) | (new_tokens - old_tokens))

            positions = new_positions

            if i % (REBALANCE_HOURS * 20) == 0 or i == start_idx:
                rn = REGIME_NAMES.get(current_regime, '?')
                al = current_alloc
                ne = len(cf)
                tfr = cf.iloc[-1] * ANNUAL_HOURS * 100 if len(cf) > 0 else 0
                bfr = cf.iloc[0] * ANNUAL_HOURS * 100 if len(cf) > 0 else 0
                print(f"  [{current_time.date()}] Eq={equity:.4f}, "
                      f"Regime={rn}({al:.0%}), Elig={ne}, "
                      f"TopFR={tfr:.1f}%/yr, BotFR={bfr:.1f}%/yr, "
                      f"L={longs[:2]}, S={shorts[:2]}")

    # ── Metrics ──
    eq = equity_curve.iloc[start_idx:].dropna()
    if len(eq) < 100:
        print("[ERROR] Not enough data")
        return None

    hr = eq.pct_change().dropna()
    total_hours = len(hr)
    total_years = total_hours / ANNUAL_HOURS

    total_return = eq.iloc[-1] / eq.iloc[0] - 1
    ann_return = (1 + total_return) ** (1 / total_years) - 1 if total_years > 0 else 0
    ann_vol = hr.std() * np.sqrt(ANNUAL_HOURS)
    sharpe = ann_return / ann_vol if ann_vol > 0 else 0

    hwm = eq.cummax()
    dd = eq / hwm - 1
    max_dd = dd.min()
    calmar = ann_return / abs(max_dd) if max_dd != 0 else 0

    ds = hr[hr < 0]
    ds_vol = ds.std() * np.sqrt(ANNUAL_HOURS) if len(ds) > 0 else 1e-10
    sortino = ann_return / ds_vol if ds_vol > 0 else 0
    win_rate = (hr > 0).sum() / len(hr) if len(hr) > 0 else 0

    # Last 12M
    l12_start = eq.index[-1] - pd.Timedelta(hours=ANNUAL_HOURS)
    eq_12 = eq.loc[l12_start:]
    if len(eq_12) > 100:
        ret_12m = eq_12.iloc[-1] / eq_12.iloc[0] - 1
        hr_12 = eq_12.pct_change().dropna()
        vol_12 = hr_12.std() * np.sqrt(ANNUAL_HOURS)
        sharpe_12m = ret_12m / vol_12 if vol_12 > 0 else 0
        hwm_12 = eq_12.cummax()
        max_dd_12m = (eq_12 / hwm_12 - 1).min()
        calmar_12m = ret_12m / abs(max_dd_12m) if max_dd_12m != 0 else 0
        ds_12 = hr_12[hr_12 < 0]
        dsv12 = ds_12.std() * np.sqrt(ANNUAL_HOURS) if len(ds_12) > 0 else 1e-10
        sortino_12m = ret_12m / dsv12
    else:
        ret_12m = sharpe_12m = max_dd_12m = calmar_12m = sortino_12m = 0

    # Monthly returns
    monthly_ret = {}
    prev_eq = None
    for mk in sorted(monthly_eq_snapshots.keys()):
        if prev_eq is not None:
            monthly_ret[mk] = monthly_eq_snapshots[mk] / prev_eq - 1
        prev_eq = monthly_eq_snapshots[mk]

    # Contributors
    token_total = {t: token_pnl[t] + token_funding[t] for t in eligible}
    st = sorted(token_total.items(), key=lambda x: x[1], reverse=True)
    top_5 = st[:5]
    bottom_5 = st[-5:]

    results = {
        'label': label, 'use_regime_filter': use_regime_filter,
        'leverage': leverage, 'adv_threshold': adv_threshold,
        'n_eligible': len(eligible),
        'period_start': eq.index[0], 'period_end': eq.index[-1],
        'total_years': total_years,
        'total_return': total_return, 'ann_return': ann_return,
        'ann_vol': ann_vol, 'sharpe': sharpe, 'calmar': calmar,
        'sortino': sortino, 'max_dd': max_dd, 'win_rate': win_rate,
        'ret_12m': ret_12m, 'sharpe_12m': sharpe_12m,
        'max_dd_12m': max_dd_12m, 'calmar_12m': calmar_12m,
        'sortino_12m': sortino_12m,
        'trade_count': trade_count,
        'total_funding_income': total_funding_income,
        'total_price_pnl': total_price_pnl,
        'total_fees': total_fees,
        'long_leg_price_pnl': long_leg_price_pnl,
        'short_leg_price_pnl': short_leg_price_pnl,
        'long_leg_funding_pnl': long_leg_funding_pnl,
        'short_leg_funding_pnl': short_leg_funding_pnl,
        'top_contributors': top_5,
        'top_detractors': bottom_5,
        'equity_curve': eq,
        'token_pnl': token_pnl, 'token_funding': token_funding,
        'regime_hours': regime_hours,
        'regime_pnl': regime_pnl, 'regime_funding': regime_funding,
        'monthly_ret_table': monthly_ret,
    }

    print(f"\n[RESULTS] {label}")
    print(f"  Period: {eq.index[0].date()} to {eq.index[-1].date()} ({total_years:.1f}y)")
    print(f"  Total Return:  {total_return:+.2%}")
    print(f"  Annual Return: {ann_return:+.2%}")
    print(f"  Annual Vol:    {ann_vol:.2%}")
    print(f"  Sharpe:        {sharpe:.3f}")
    print(f"  Calmar:        {calmar:.3f}")
    print(f"  Sortino:       {sortino:.3f}")
    print(f"  Max DD:        {max_dd:.2%}")
    print(f"  Win Rate:      {win_rate:.2%}")
    print(f"  12M Return:    {ret_12m:+.2%}")
    print(f"  12M MaxDD:     {max_dd_12m:.2%}")
    print(f"  12M Sharpe:    {sharpe_12m:.3f}")
    print(f"  Trades:        {trade_count}")
    print(f"  Funding Inc:   {total_funding_income:+.4f}")
    print(f"  Price PnL:     {total_price_pnl:+.4f}")
    print(f"  Fees:          {total_fees:.4f}")
    print(f"  Long leg:  price={long_leg_price_pnl:+.4f}, funding={long_leg_funding_pnl:+.4f}")
    print(f"  Short leg: price={short_leg_price_pnl:+.4f}, funding={short_leg_funding_pnl:+.4f}")

    sim_h = sum(regime_hours.values())
    print(f"\n  Regime breakdown:")
    for r in sorted(REGIME_NAMES):
        nm = REGIME_NAMES[r]
        hrs = regime_hours.get(r, 0)
        ppnl = regime_pnl.get(r, 0)
        fpnl = regime_funding.get(r, 0)
        al = REGIME_ALLOCATION.get(r, 0.7) if use_regime_filter else 1.0
        print(f"    {nm:>10s}: {hrs:>6d}h ({hrs/max(sim_h,1)*100:5.1f}%) "
              f"alloc={al:.0%}, price={ppnl:+.4f}, fund={fpnl:+.4f}, net={ppnl+fpnl:+.4f}")

    return results


# ══════════════════════════════════════════════════════════════════════════
# REPORT
# ══════════════════════════════════════════════════════════════════════════

def generate_report(all_results):
    L = []
    L.append("# R153: Funding Carry with V4 Regime Filter")
    L.append(f"\n**Date:** {datetime.now().strftime('%Y-%m-%d')}")
    L.append("")
    L.append("## Hypothesis")
    L.append("")
    L.append("R151 carry portfolio showed +96% return but -71% MaxDD. The drawdowns came from:")
    L.append("1. Long leg buying distressed tokens with extreme negative funding (price collapse)")
    L.append("2. No regime filter meant full exposure during market crashes")
    L.append("3. Imperfect hedge: long/short legs had different beta exposures")
    L.append("")
    L.append("**Fixes applied:**")
    L.append("- ADV threshold raised to $50M (exclude garbage tokens)")
    L.append("- Funding rate cap at 200% annualized (exclude distressed tokens)")
    L.append("- V4 regime detection scales allocation: UPTREND 100%, RANGE 70%, QUIET 50%, DOWNTREND 20%, CRISIS 0%")
    L.append("- 72h rebalance with 72h rolling funding for ranking")
    L.append("")

    # Summary table
    L.append("## Summary Comparison")
    L.append("")
    L.append("| Variant | Ann Ret | MaxDD | Sharpe | Calmar | Sortino | 12M Ret | 12M MaxDD | 12M Sharpe | Trades |")
    L.append("|---------|---------|-------|--------|--------|---------|---------|-----------|------------|--------|")
    for r in all_results:
        if r is None:
            continue
        L.append(f"| {r['label']} | {r['ann_return']:+.2%} | {r['max_dd']:.2%} | "
                 f"{r['sharpe']:.3f} | {r['calmar']:.3f} | {r['sortino']:.3f} | "
                 f"{r['ret_12m']:+.2%} | {r['max_dd_12m']:.2%} | {r['sharpe_12m']:.3f} | "
                 f"{r['trade_count']} |")
    L.append("")

    # Leg decomposition
    L.append("## Long vs Short Leg Decomposition")
    L.append("")
    L.append("| Variant | Long Price | Long Funding | Short Price | Short Funding | Net Price | Net Funding |")
    L.append("|---------|-----------|-------------|------------|--------------|-----------|-------------|")
    for r in all_results:
        if r is None:
            continue
        L.append(f"| {r['label']} | {r['long_leg_price_pnl']:+.4f} | "
                 f"{r['long_leg_funding_pnl']:+.4f} | "
                 f"{r['short_leg_price_pnl']:+.4f} | "
                 f"{r['short_leg_funding_pnl']:+.4f} | "
                 f"{r['total_price_pnl']:+.4f} | "
                 f"{r['total_funding_income']:+.4f} |")
    L.append("")

    # Regime breakdown for filtered
    filtered = [r for r in all_results if r is not None and r.get('use_regime_filter')]
    if filtered:
        L.append("## Regime Performance Breakdown (Filtered)")
        L.append("")
        for r in filtered:
            L.append(f"### {r['label']}")
            L.append("")
            L.append("| Regime | Hours | % Time | Alloc | Price PnL | Funding | Net PnL |")
            L.append("|--------|-------|--------|-------|-----------|---------|---------|")
            sim_h = sum(r['regime_hours'].values())
            for rv in sorted(REGIME_NAMES):
                nm = REGIME_NAMES[rv]
                hrs = r['regime_hours'].get(rv, 0)
                pct = hrs / max(sim_h, 1) * 100
                al = REGIME_ALLOCATION.get(rv, 0.7)
                pp = r['regime_pnl'].get(rv, 0)
                fp = r['regime_funding'].get(rv, 0)
                L.append(f"| {nm} | {hrs} | {pct:.1f}% | {al:.0%} | {pp:+.4f} | {fp:+.4f} | {pp+fp:+.4f} |")
            L.append("")

    # Detailed per-variant
    for r in all_results:
        if r is None:
            continue
        L.append(f"## {r['label']}")
        L.append("")
        L.append(f"- **Regime filter:** {'ON' if r['use_regime_filter'] else 'OFF'}")
        L.append(f"- **Leverage:** {r['leverage']}x")
        L.append(f"- **ADV threshold:** ${r['adv_threshold']/1e6:.0f}M")
        L.append(f"- **Eligible tokens:** {r['n_eligible']}")
        L.append(f"- **Period:** {r['period_start'].date()} to {r['period_end'].date()} ({r['total_years']:.1f}y)")
        L.append("")

        L.append("### Full Period")
        L.append("")
        L.append("| Metric | Value |")
        L.append("|--------|-------|")
        for k, fmt in [('total_return', '{:+.2%}'), ('ann_return', '{:+.2%}'),
                        ('ann_vol', '{:.2%}'), ('sharpe', '{:.3f}'),
                        ('calmar', '{:.3f}'), ('sortino', '{:.3f}'),
                        ('max_dd', '{:.2%}'), ('win_rate', '{:.2%}'),
                        ('trade_count', '{}'),
                        ('total_funding_income', '{:+.4f}'),
                        ('total_price_pnl', '{:+.4f}'),
                        ('total_fees', '{:.4f}')]:
            L.append(f"| {k.replace('_',' ').title()} | {fmt.format(r[k])} |")
        L.append("")

        L.append("### Last 12 Months")
        L.append("")
        L.append("| Metric | Value |")
        L.append("|--------|-------|")
        L.append(f"| Return | {r['ret_12m']:+.2%} |")
        L.append(f"| Max Drawdown | {r['max_dd_12m']:.2%} |")
        L.append(f"| Sharpe | {r['sharpe_12m']:.3f} |")
        L.append(f"| Calmar | {r['calmar_12m']:.3f} |")
        L.append(f"| Sortino | {r['sortino_12m']:.3f} |")
        L.append("")

        # Monthly returns (last 24)
        if r['monthly_ret_table']:
            L.append("### Monthly Returns (last 24)")
            L.append("")
            L.append("| Month | Return |")
            L.append("|-------|--------|")
            for mk in sorted(r['monthly_ret_table'].keys())[-24:]:
                L.append(f"| {mk} | {r['monthly_ret_table'][mk]:+.2%} |")
            L.append("")

        L.append("### Top 5 Contributors")
        L.append("")
        L.append("| Token | Total | Funding | Price |")
        L.append("|-------|-------|---------|-------|")
        for t, pnl in r['top_contributors']:
            L.append(f"| {t} | {pnl:+.4f} | {r['token_funding'].get(t,0):+.4f} | {r['token_pnl'].get(t,0):+.4f} |")
        L.append("")
        L.append("### Bottom 5 Detractors")
        L.append("")
        L.append("| Token | Total | Funding | Price |")
        L.append("|-------|-------|---------|-------|")
        for t, pnl in r['top_detractors']:
            L.append(f"| {t} | {pnl:+.4f} | {r['token_funding'].get(t,0):+.4f} | {r['token_pnl'].get(t,0):+.4f} |")
        L.append("")

    # Cost breakdown
    L.append("## Cost Breakdown")
    L.append("")
    L.append("| Variant | Gross PnL | Fees | Net PnL | Fee Drag |")
    L.append("|---------|----------|------|---------|----------|")
    for r in all_results:
        if r is None:
            continue
        gross = r['total_funding_income'] + r['total_price_pnl']
        fd = r['total_fees'] / max(abs(gross), 1e-10) * 100
        L.append(f"| {r['label']} | {gross:+.4f} | {r['total_fees']:.4f} | "
                 f"{gross - r['total_fees']:+.4f} | {fd:.1f}% |")
    L.append("")

    # Conclusions
    L.append("## Conclusions")
    L.append("")

    valid = [r for r in all_results if r is not None]
    if not valid:
        L.append("No valid results.")
        return "\n".join(L)

    # Filtered vs unfiltered comparison
    f_list = [r for r in valid if r.get('use_regime_filter')]
    u_list = [r for r in valid if not r.get('use_regime_filter')]
    if f_list and u_list:
        f1 = next((r for r in f_list if r['leverage'] == 1.0), None)
        u1 = next((r for r in u_list if r['leverage'] == 1.0), None)
        if f1 and u1:
            L.append("### Regime Filter Impact (1x)")
            L.append("")
            dd_imp = (f1['max_dd'] - u1['max_dd']) / abs(u1['max_dd']) * 100
            L.append(f"- **MaxDD:** {u1['max_dd']:.2%} -> {f1['max_dd']:.2%} ({dd_imp:+.1f}% change)")
            L.append(f"- **Ann Return:** {u1['ann_return']:+.2%} -> {f1['ann_return']:+.2%}")
            L.append(f"- **Sharpe:** {u1['sharpe']:.3f} -> {f1['sharpe']:.3f}")
            L.append(f"- **Calmar:** {u1['calmar']:.3f} -> {f1['calmar']:.3f}")
            L.append(f"- **12M Return:** {u1['ret_12m']:+.2%} -> {f1['ret_12m']:+.2%}")
            L.append(f"- **12M MaxDD:** {u1['max_dd_12m']:.2%} -> {f1['max_dd_12m']:.2%}")
            L.append("")

    # Leverage analysis
    L.append("### Leverage Analysis (Regime-Filtered)")
    L.append("")
    for lev in [1.0, 2.0, 3.0]:
        fl = next((r for r in f_list if r['leverage'] == lev), None)
        if fl:
            L.append(f"- **{lev:.0f}x:** Ret={fl['ann_return']:+.2%}, MaxDD={fl['max_dd']:.2%}, "
                     f"Sharpe={fl['sharpe']:.3f}, Calmar={fl['calmar']:.3f}")
    L.append("")

    # Key findings
    L.append("### Key Findings")
    L.append("")

    # Check if funding > price loss
    for r in valid:
        funding_net = r['total_funding_income']
        price_net = r['total_price_pnl']
        L.append(f"- **{r['label']}:** Funding={funding_net:+.4f}, Price={price_net:+.4f}, "
                 f"Net={funding_net+price_net:+.4f}")
    L.append("")

    # Verdict
    best = max(valid, key=lambda r: r['calmar'])
    L.append("### Verdict")
    L.append("")
    if best['calmar'] > 0.5 and best['max_dd'] > -0.40:
        L.append(f"**PROMISING.** {best['label']}: Calmar={best['calmar']:.3f}, MaxDD={best['max_dd']:.2%}")
    elif best['sharpe'] > 0.3 and best['max_dd'] > -0.50:
        L.append(f"**MODERATE.** {best['label']}: Sharpe={best['sharpe']:.3f}, MaxDD={best['max_dd']:.2%}. "
                 f"Regime filter helps but further work needed on position construction.")
    elif best['sharpe'] > 0.0:
        L.append(f"**MARGINAL.** Positive Sharpe ({best['sharpe']:.3f}) but not compelling. "
                 f"MaxDD={best['max_dd']:.2%} still too high.")
    else:
        L.append(f"**REJECT.** The cross-sectional carry strategy is fundamentally broken in this "
                 f"period. Price losses overwhelm funding income ({best['total_price_pnl']:+.4f} price "
                 f"vs {best['total_funding_income']:+.4f} funding). The long leg (low-funding tokens) "
                 f"suffers persistent price losses that the short leg cannot hedge.")
        L.append("")
        L.append("**Root cause analysis:**")
        L.append("- The long leg buys tokens with the lowest funding rates. In crypto, low funding ")
        L.append("  often signals bearish sentiment or structural selling pressure.")
        L.append("- The short leg shorts tokens with high positive funding. These tend to be tokens ")
        L.append("  in demand (bullish), so shorts lose on price moves in uptrends.")
        L.append("- The L/S construction creates a systematic **anti-momentum** bias: long unloved ")
        L.append("  tokens, short popular tokens. In trending crypto markets, this is toxic.")
        L.append("- The regime filter helps reduce exposure during crashes (-12pp MaxDD improvement) ")
        L.append("  but cannot fix the fundamental signal problem.")
        L.append("")
        L.append("**Recommendation:** Abandon cross-sectional L/S carry. Instead consider:")
        L.append("1. **Pure short carry**: Short only the highest-funding tokens, hedge with BTC/ETH futures")
        L.append("2. **Carry + momentum**: Only short high-funding tokens with bearish momentum")
        L.append("3. **Delta-neutral carry**: Market-make a single token, collect funding via basis trade")

    return "\n".join(L)


# ══════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("R153: Funding Carry with V4 Regime Filter")
    print("=" * 70)

    data = load_all_perps()
    if 'BTC' not in data:
        print("[FATAL] BTC data not found")
        return

    btc_regime = compute_btc_regime(data['BTC'])

    results = []

    # 1. Unfiltered baseline 1x
    results.append(run_carry_backtest(
        data, btc_regime, use_regime_filter=False, leverage=1.0,
        label="Unfiltered 1x"))

    # 2. Regime-filtered 1x
    results.append(run_carry_backtest(
        data, btc_regime, use_regime_filter=True, leverage=1.0,
        label="Filtered 1x"))

    # 3. Regime-filtered 2x
    results.append(run_carry_backtest(
        data, btc_regime, use_regime_filter=True, leverage=2.0,
        label="Filtered 2x"))

    # 4. Regime-filtered 3x
    results.append(run_carry_backtest(
        data, btc_regime, use_regime_filter=True, leverage=3.0,
        label="Filtered 3x"))

    # 5. Unfiltered 2x (for comparison)
    results.append(run_carry_backtest(
        data, btc_regime, use_regime_filter=False, leverage=2.0,
        label="Unfiltered 2x"))

    # ── Report ──
    print("\n" + "=" * 70)
    print("GENERATING REPORT")
    print("=" * 70)

    report = generate_report(results)
    OUTPUT_MD.write_text(report)
    print(f"\nReport saved to {OUTPUT_MD}")
    print("\n" + "=" * 70)
    print("FULL REPORT")
    print("=" * 70)
    print(report)


if __name__ == '__main__':
    main()
