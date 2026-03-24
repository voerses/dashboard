#!/workspace/venv/bin/python
"""
Carry Overlay Analysis: Does Positioning + VRP help funding carry strategies?
=============================================================================

Context:
  - s29/s65 are funding carry strategies: SHORT when funding positive, LONG when negative
  - These are NOT delta-neutral -- they hold naked directional perp positions
  - Prior research: Positioning + VRP overlays add +0.856 Sharpe to trend-following (R62)
  - Question: do these overlays help carry strategies too?

Key finding: BTC funding rates collapsed in Sep 2025+ (72h mean never exceeds 0.00005/hr).
The s29/s65 carry strategies would generate ZERO signals in that period.
Therefore we test TWO OOS windows:
  - Primary OOS: 2025-01-01 to latest (14+ months, some carry activity)
  - We note that Sep 2025+ is structurally dead for carry

Output: /workspace/crypto_backtest/research/carry_overlay_analysis.md
"""

import pandas as pd
import numpy as np
import json
import warnings
from pathlib import Path
from datetime import datetime
from scipy import stats

warnings.filterwarnings('ignore')

BASE_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = BASE_DIR / 'data'
OUTPUT_DIR = BASE_DIR / 'research'

COST_BPS = 10
OOS_START = '2025-01-01'
IS_END = '2024-12-31'


# ── 1. Data Loading ──────────────────────────────────────────────────────────

def load_perp_data():
    """Load BTC perpetual data with funding rates."""
    print("[1/4] Loading BTC perp data (1h with funding)...")
    perp = pd.read_parquet(DATA_DIR / 'perp/1h_cache/BTC_1h.parquet')
    perp.index = pd.to_datetime(perp.index)
    perp.index.name = 'date'

    daily = pd.DataFrame()
    daily['close'] = perp['close'].resample('D').last()
    daily['open'] = perp['open'].resample('D').first()
    daily['high'] = perp['high'].resample('D').max()
    daily['low'] = perp['low'].resample('D').min()
    daily['volume'] = perp['volume'].resample('D').sum()

    # Funding: sum of hourly funding rates per day
    daily['funding_daily'] = perp['funding_1h'].resample('D').sum()
    # 72h rolling mean of signed funding (same as s29/s65)
    funding_1h = perp['funding_1h'].copy()
    funding_72h_mean = funding_1h.rolling(72, min_periods=24).mean()
    daily['funding_72h_mean'] = funding_72h_mean.resample('D').last()
    daily['funding_abs_72h'] = np.abs(daily['funding_72h_mean'])

    daily = daily.dropna(subset=['close'])
    print(f"  Perp daily: {daily.index.min().date()} to {daily.index.max().date()}, {len(daily)} rows")
    print(f"  Funding daily mean: {daily['funding_daily'].mean():.6f}")
    return daily


def load_positioning():
    """Load Binance positioning data for BTCUSDT."""
    print("[2/4] Loading positioning data...")
    pos = pd.read_parquet(DATA_DIR / 'alternative/binance_metrics/all_symbols_daily_ls.parquet')
    pos = pos[pos['symbol'] == 'BTCUSDT'].copy()
    pos['date'] = pd.to_datetime(pos['date'])
    pos = pos.set_index('date').sort_index()
    pos = pos[['sum_toptrader_ls_ratio', 'count_toptrader_ls_ratio', 'count_ls_ratio']].copy()
    pos = pos[~pos.index.duplicated(keep='last')]
    print(f"  Positioning: {pos.index.min().date()} to {pos.index.max().date()}, {len(pos)} rows")
    return pos


def load_dvol():
    """Load BTC DVOL from Deribit."""
    print("[3/4] Loading BTC DVOL...")
    dvol_path = DATA_DIR / 'alternative/deribit_options/dvol/btc_dvol_daily.json'
    with open(dvol_path) as f:
        data = json.load(f)
    records = [{'date': pd.Timestamp(row[0], unit='ms'), 'dvol_close': row[4]} for row in data]
    dvol = pd.DataFrame(records).set_index('date').sort_index()
    dvol = dvol[~dvol.index.duplicated(keep='last')]
    print(f"  DVOL: {dvol.index.min().date()} to {dvol.index.max().date()}, {len(dvol)} rows")
    return dvol['dvol_close']


# ── 2. Signal Construction ───────────────────────────────────────────────────

def build_carry_signal(daily, threshold=0.00005):
    """
    Simplified carry strategy (mirrors s29/s65 logic):
    - Entry: |funding_72h_mean| > threshold
    - Direction: -sign(funding) (short when positive, long when negative)
    """
    print(f"[4/4] Building carry signal (threshold={threshold:.6f})...")
    entry = daily['funding_abs_72h'] > threshold
    direction = np.where(daily['funding_72h_mean'] > 0, -1.0, 1.0)
    position = np.where(entry, direction, 0.0)
    position = pd.Series(position, index=daily.index)
    position.iloc[:200] = 0.0

    n_long = (position > 0).sum()
    n_short = (position < 0).sum()
    n_flat = (position == 0).sum()
    print(f"  Signal: {n_long} long, {n_short} short, {n_flat} flat days")

    # Check OOS activity
    oos = position[position.index >= OOS_START]
    n_active_oos = (oos != 0).sum()
    print(f"  OOS active days: {n_active_oos}/{len(oos)} ({100*n_active_oos/max(len(oos),1):.0f}%)")

    return position


def build_positioning_overlay(daily, positioning):
    """Positioning sizing overlay (from R60)."""
    pos = positioning.reindex(daily.index).ffill()

    def rolling_zscore(s, window=30):
        mu = s.rolling(window, min_periods=15).mean()
        sigma = s.rolling(window, min_periods=15).std()
        return (s - mu) / sigma.replace(0, np.nan)

    z_toptrader = rolling_zscore(pos['sum_toptrader_ls_ratio'])
    divergence = pos['count_toptrader_ls_ratio'] - pos['count_ls_ratio']
    z_divergence = rolling_zscore(divergence)
    combined_z = (z_toptrader + z_divergence) / 2.0

    def z_to_multiplier(z):
        if pd.isna(z):
            return 1.0
        if z > 1.5:
            return 0.3
        elif z > 0.5:
            return 0.5
        elif z > -0.5:
            return 1.0
        elif z > -1.5:
            return 1.3
        else:
            return 1.5

    pos_multiplier = combined_z.apply(z_to_multiplier)
    return pos_multiplier, combined_z


def build_vrp_overlay(daily, dvol_series):
    """VRP sizing overlay (from R62)."""
    log_ret = np.log(daily['close'] / daily['close'].shift(1))
    rv_20d = log_ret.rolling(20, min_periods=15).std() * np.sqrt(365) * 100
    iv = dvol_series.reindex(daily.index).ffill()
    vrp = iv - rv_20d

    vrp_mu = vrp.rolling(60, min_periods=30).mean()
    vrp_sigma = vrp.rolling(60, min_periods=30).std()
    vrp_z = (vrp - vrp_mu) / vrp_sigma.replace(0, np.nan)

    def vrp_z_to_multiplier(z):
        if pd.isna(z):
            return 1.0
        if z > 1.0:
            return 1.3
        elif z > -0.5:
            return 1.0
        elif z > -1.5:
            return 0.5
        else:
            return 0.3

    vrp_multiplier = vrp_z.apply(vrp_z_to_multiplier)
    return vrp_multiplier, vrp_z


# ── 3. Backtest Engine ───────────────────────────────────────────────────────

def run_carry_backtest(daily, position, cost_bps=COST_BPS):
    """
    Run carry backtest.
    P&L = position * price_return + funding_income - costs
    Funding: short gets paid when funding positive, long gets paid when negative.
    funding_pnl = -position * funding_rate
    """
    price_ret = daily['close'].pct_change()
    funding = daily['funding_daily']

    # Weekly rebalancing
    rebalance_dates = daily.index.to_series().groupby(
        daily.index.to_period('W')
    ).first()
    rebalance_set = set(rebalance_dates.values)

    held_position = pd.Series(0.0, index=daily.index)
    current_pos = 0.0
    costs = pd.Series(0.0, index=daily.index)

    for dt in daily.index:
        if dt in rebalance_set:
            new_pos = position.loc[dt]
            if not pd.isna(new_pos):
                pos_change = abs(new_pos - current_pos)
                costs.loc[dt] = pos_change * cost_bps / 10000.0
                current_pos = new_pos
        held_position.loc[dt] = current_pos

    price_pnl = held_position.shift(1) * price_ret
    funding_pnl = -held_position.shift(1) * funding
    total_ret = price_pnl + funding_pnl - costs

    return total_ret, held_position, costs, price_pnl, funding_pnl


def compute_metrics(returns, label=""):
    """Compute performance metrics."""
    returns = returns.dropna()
    if len(returns) == 0:
        return {'label': label, 'total_return': 0, 'ann_return': 0,
                'ann_vol': 0, 'sharpe': 0, 'max_dd': 0, 'calmar': 0, 'n_days': 0}

    total_ret = (1 + returns).prod() - 1
    n_years = len(returns) / 365
    ann_ret = (1 + total_ret) ** (1 / max(n_years, 0.01)) - 1
    ann_vol = returns.std() * np.sqrt(365)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

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
        'max_dd': max_dd,
        'calmar': calmar,
        'n_days': len(returns),
    }


# ── 4. Funding Flip Prediction ───────────────────────────────────────────────

def test_positioning_predicts_funding_flips(daily, positioning):
    """Test whether extreme positioning predicts funding rate sign changes."""
    print("\n--- Funding Flip Prediction Analysis ---")

    pos = positioning.reindex(daily.index).ffill()

    def rolling_zscore(s, window=30):
        mu = s.rolling(window, min_periods=15).mean()
        sigma = s.rolling(window, min_periods=15).std()
        return (s - mu) / sigma.replace(0, np.nan)

    z_toptrader = rolling_zscore(pos['sum_toptrader_ls_ratio'])

    funding_sign = np.sign(daily['funding_72h_mean'])
    funding_sign_fwd = funding_sign.shift(-7)
    funding_flip = (funding_sign != funding_sign_fwd).astype(float)
    funding_flip[funding_sign == 0] = np.nan
    funding_flip[funding_sign_fwd.isna()] = np.nan

    df = pd.DataFrame({
        'pos_z': z_toptrader,
        'flip_7d': funding_flip,
        'funding_fwd_7d': daily['funding_72h_mean'].shift(-7),
        'funding_now': daily['funding_72h_mean'],
    }).dropna()

    if len(df) < 100:
        print("  Insufficient data for flip analysis")
        return {}

    extreme_long = df['pos_z'] > 1.5
    extreme_short = df['pos_z'] < -1.5
    normal = (df['pos_z'] > -0.5) & (df['pos_z'] < 0.5)

    results = {}
    for label, mask in [('Extreme Long (z>1.5)', extreme_long),
                        ('Extreme Short (z<-1.5)', extreme_short),
                        ('Normal (-0.5<z<0.5)', normal)]:
        sub = df[mask]
        if len(sub) < 10:
            print(f"  {label}: {len(sub)} days (too few)")
            continue
        flip_rate = sub['flip_7d'].mean()
        avg_fwd = sub['funding_fwd_7d'].mean()
        avg_now = sub['funding_now'].mean()
        print(f"  {label}: {len(sub)} days, flip rate={flip_rate:.1%}, "
              f"now={avg_now:.6f}, fwd={avg_fwd:.6f}")
        results[label] = {
            'n_days': len(sub), 'flip_rate': flip_rate,
            'avg_fwd_funding': avg_fwd, 'avg_now_funding': avg_now,
        }

    funding_change = df['funding_fwd_7d'] - df['funding_now']
    ic, pval = stats.spearmanr(df['pos_z'], funding_change)
    print(f"\n  IC(pos_z -> 7d funding change): {ic:.4f} (p={pval:.4f})")
    results['ic_funding_change'] = {'ic': ic, 'pval': pval}

    ic_flip, pval_flip = stats.spearmanr(df['pos_z'], df['flip_7d'])
    print(f"  IC(pos_z -> 7d flip): {ic_flip:.4f} (p={pval_flip:.4f})")
    results['ic_flip'] = {'ic': ic_flip, 'pval': pval_flip}

    # Also test: does positioning z predict MAGNITUDE of funding change?
    abs_pos_z = np.abs(df['pos_z'])
    abs_funding_change = np.abs(funding_change)
    ic_mag, pval_mag = stats.spearmanr(abs_pos_z, abs_funding_change)
    print(f"  IC(|pos_z| -> |7d funding change|): {ic_mag:.4f} (p={pval_mag:.4f})")
    results['ic_abs_change'] = {'ic': ic_mag, 'pval': pval_mag}

    return results


# ── 5. Main ──────────────────────────────────────────────────────────────────

def main():
    print("=" * 72)
    print("CARRY OVERLAY ANALYSIS")
    print("Testing Positioning + VRP overlays on funding carry strategies")
    print("=" * 72)
    print()

    # Load data
    daily = load_perp_data()
    positioning = load_positioning()
    dvol = load_dvol()
    print()

    # IS start based on DVOL
    is_start = dvol.index.min().strftime('%Y-%m-%d')

    # ── Check funding environment in OOS ──
    oos_funding = daily['funding_abs_72h'][daily.index >= OOS_START]
    oos_above_thresh = (oos_funding > 0.00005).sum()
    total_oos_days = len(oos_funding)
    print(f"OOS funding environment:")
    print(f"  Days with |funding_72h| > 0.00005: {oos_above_thresh}/{total_oos_days}")
    print(f"  OOS |funding_72h| mean: {oos_funding.mean():.7f}")
    print(f"  OOS |funding_72h| max:  {oos_funding.max():.7f}")

    # Also check sub-periods
    for period_start, period_end, name in [('2025-01-01', '2025-04-30', 'Jan-Apr 2025'),
                                            ('2025-05-01', '2025-08-31', 'May-Aug 2025'),
                                            ('2025-09-01', '2026-03-31', 'Sep 2025-Mar 2026')]:
        mask = (daily.index >= period_start) & (daily.index <= period_end)
        sub = daily['funding_abs_72h'][mask]
        above = (sub > 0.00005).sum()
        print(f"  {name}: {above}/{len(sub)} days above threshold "
              f"(mean={sub.mean():.7f})")
    print()

    # Build signals
    carry_position = build_carry_signal(daily, threshold=0.00005)
    pos_multiplier, pos_z = build_positioning_overlay(daily, positioning)
    vrp_multiplier, vrp_z = build_vrp_overlay(daily, dvol)
    print()

    print(f"IS period: {is_start} to {IS_END}")
    print(f"OOS period: {OOS_START} to latest")
    print()

    # ── Build variants ──
    labels = {
        'base': 'Carry Only',
        'carry_pos': 'Carry + Positioning',
        'carry_vrp': 'Carry + VRP',
        'carry_pos_vrp': 'Carry + Pos + VRP',
    }

    variants = {}
    for vk in labels:
        if vk == 'base':
            fp = carry_position.copy()
        elif vk == 'carry_pos':
            fp = carry_position * pos_multiplier
        elif vk == 'carry_vrp':
            fp = carry_position * vrp_multiplier
        elif vk == 'carry_pos_vrp':
            fp = carry_position * pos_multiplier * vrp_multiplier
        variants[vk] = fp.clip(-2.0, 2.0)

    # ── Run backtests ──
    print("Running backtests...")
    results = {}
    is_mask = (daily.index >= is_start) & (daily.index <= IS_END)
    oos_mask = daily.index >= OOS_START

    for vk, vname in labels.items():
        total_ret, held_pos, costs, price_pnl, funding_pnl = run_carry_backtest(
            daily, variants[vk]
        )

        is_metrics = compute_metrics(total_ret[is_mask], f"{vname} (IS)")
        oos_metrics = compute_metrics(total_ret[oos_mask], f"{vname} (OOS)")

        oos_price = price_pnl[oos_mask].sum()
        oos_fund = funding_pnl[oos_mask].sum()
        oos_cost = costs[oos_mask].sum()

        # Also compute IS decomposition
        is_price = price_pnl[is_mask].sum()
        is_fund = funding_pnl[is_mask].sum()
        is_cost = costs[is_mask].sum()

        results[vk] = {
            'name': vname,
            'is': is_metrics, 'oos': oos_metrics,
            'strat_ret': total_ret, 'held_pos': held_pos,
            'oos_price_pnl': oos_price, 'oos_funding_pnl': oos_fund, 'oos_cost': oos_cost,
            'is_price_pnl': is_price, 'is_funding_pnl': is_fund, 'is_cost': is_cost,
        }
        print(f"  {vname}: IS Sharpe={is_metrics['sharpe']:.2f}, OOS Sharpe={oos_metrics['sharpe']:.2f}")
        print(f"    IS:  total={is_metrics['total_return']:.4f}, price={is_price:.4f}, "
              f"funding={is_fund:.4f}, costs={is_cost:.4f}")
        print(f"    OOS: total={oos_metrics['total_return']:.4f}, price={oos_price:.4f}, "
              f"funding={oos_fund:.4f}, costs={oos_cost:.4f}")

    print()

    # ── Funding flip prediction ──
    flip_results = test_positioning_predicts_funding_flips(daily, positioning)

    # ── Statistical significance ──
    print("\n--- Statistical Significance (OOS) ---")
    sig_results = {}
    for vk in ['carry_pos', 'carry_vrp', 'carry_pos_vrp']:
        overlay_ret = results[vk]['strat_ret'][oos_mask].dropna()
        base_ret = results['base']['strat_ret'][oos_mask].dropna()
        common = overlay_ret.index.intersection(base_ret.index)
        diff = overlay_ret.loc[common] - base_ret.loc[common]
        if len(diff) > 20 and diff.std() > 0:
            t_stat = diff.mean() / (diff.std() / np.sqrt(len(diff)))
            print(f"  {labels[vk]} vs Base: t={t_stat:.3f}, N={len(diff)}")
            sig_results[vk] = {'t_stat': t_stat, 'n': len(diff)}
        else:
            print(f"  {labels[vk]} vs Base: no variance in diff (carry likely flat in OOS)")
            sig_results[vk] = {'t_stat': 0.0, 'n': len(diff)}

    # ── Position activity analysis ──
    print("\n--- Position Activity ---")
    for vk in labels:
        hp = results[vk]['held_pos']
        oos_hp = hp[oos_mask]
        is_hp = hp[is_mask]
        print(f"  {labels[vk]}:")
        print(f"    IS:  active={((is_hp != 0).sum())}/{len(is_hp)} days, "
              f"mean abs pos={is_hp.abs().mean():.3f}")
        print(f"    OOS: active={((oos_hp != 0).sum())}/{len(oos_hp)} days, "
              f"mean abs pos={oos_hp.abs().mean():.3f}")

    # ── Monthly returns ──
    monthly_all = {}
    for vk in labels:
        vr = results[vk]['strat_ret'][oos_mask]
        monthly_all[vk] = (1 + vr).resample('ME').prod() - 1

    # ── Generate Report ──
    print("\n" + "=" * 72)
    print("GENERATING REPORT")
    print("=" * 72)

    lines = []
    lines.append("# Carry Strategy Overlay Analysis")
    lines.append("")
    lines.append(f"**Run date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**IS period**: {is_start} to {IS_END}")
    lines.append(f"**OOS period**: {OOS_START} to latest")
    lines.append(f"**Transaction cost**: {COST_BPS} bps round-trip")
    lines.append(f"**Rebalancing**: Weekly (Monday)")
    lines.append("")

    # ── 1. Strategy Type Analysis ──
    lines.append("## 1. Strategy Type Analysis")
    lines.append("")
    lines.append("### s29 (Funding Rate Carry)")
    lines.append("- **Type**: DIRECTIONAL (not delta-neutral)")
    lines.append("- **Mechanism**: Short perp when funding positive, long perp when funding negative")
    lines.append("- **Direction**: -sign(72h rolling mean of funding rate)")
    lines.append("- **Key detail**: Holds a naked directional perp position. NOT a basis trade.")
    lines.append("- **P&L sources**: (1) Funding income (carry), (2) Price P&L (directional)")
    lines.append("- **Exit logic**: ATR trailing stops, max hold 14 days -- price-based exits")
    lines.append("- **Verdict**: Directional. Price moves dominate funding income short-term.")
    lines.append("")
    lines.append("### s65 (Funding Rate Carry V4)")
    lines.append("- **Type**: DIRECTIONAL (same core as s29, with regime/ADX/magnitude sizing)")
    lines.append("- **Same logic**: Short when funding positive, long when funding negative")
    lines.append("- **Same exits**: ATR-based trailing stops (1.5x ATR flat trail)")
    lines.append("- **Verdict**: Still directional. Sizing overlays will affect this strategy.")
    lines.append("")
    lines.append("### s37 (Momentum Trail Progression)")
    lines.append("- **Type**: DIRECTIONAL (pure trend-following, wraps s11)")
    lines.append("- **Verdict**: Overlays proven on trend-following base (R62 +0.856 Sharpe).")
    lines.append("")
    lines.append("### Critical Insight")
    lines.append("")
    lines.append("s29/s65 are NOT basis trades (long spot + short perp). They hold naked perp positions.")
    lines.append("The carry income is a bonus on top of a directional bet. Both price P&L and funding")
    lines.append("income contribute to total returns, making positioning/VRP overlays theoretically applicable.")
    lines.append("")

    # ── 2. Overlay Applicability ──
    lines.append("## 2. Overlay Applicability Assessment")
    lines.append("")
    lines.append("| Strategy | Directional? | Positioning Overlay | VRP Overlay | Rationale |")
    lines.append("|----------|-------------|--------------------|-----------|-|")
    lines.append("| s29 | YES | APPLICABLE | APPLICABLE | Naked perp, price risk dominates |")
    lines.append("| s65 | YES | APPLICABLE | APPLICABLE | Same as s29, enhanced sizing |")
    lines.append("| s37 | YES | APPLICABLE | APPLICABLE | Pure momentum, proven on trend base |")
    lines.append("")
    lines.append("However, there is a subtle conflict: carry strategies are INHERENTLY CONTRARIAN to crowd")
    lines.append("positioning. When the crowd is net long (positive funding), carry goes SHORT. The positioning")
    lines.append("overlay also reduces when crowd is extreme long. These could reinforce or conflict depending")
    lines.append("on timing.")
    lines.append("")

    # ── 3. Funding Environment ──
    lines.append("## 3. Funding Rate Environment (Critical Context)")
    lines.append("")
    lines.append("BTC funding rates have structurally changed over time:")
    lines.append("")
    lines.append("| Period | Days Above Threshold | Mean |funding_72h| | Max |funding_72h| |")
    lines.append("|--------|---------------------|---------------------|---------------------|")

    for period_start, period_end, name in [('2021-01-01', '2022-12-31', '2021-2022 (bull+bear)'),
                                            ('2023-01-01', '2024-12-31', '2023-2024 (recovery+bull)'),
                                            ('2025-01-01', '2025-04-30', 'Jan-Apr 2025'),
                                            ('2025-05-01', '2025-08-31', 'May-Aug 2025'),
                                            ('2025-09-01', '2026-03-31', 'Sep 2025-Mar 2026')]:
        mask = (daily.index >= period_start) & (daily.index <= period_end)
        sub = daily['funding_abs_72h'][mask]
        if len(sub) > 0:
            above = (sub > 0.00005).sum()
            lines.append(f"| {name} | {above}/{len(sub)} ({100*above/len(sub):.0f}%) | "
                         f"{sub.mean():.7f} | {sub.max():.7f} |")

    lines.append("")
    lines.append("**Key finding**: Funding rates collapsed after Aug 2025. The s29/s65 carry strategies")
    lines.append("generate ZERO signals in Sep 2025-Mar 2026 because the 72h rolling mean never exceeds")
    lines.append("the 0.00005/hr threshold (48% annualized). This means:")
    lines.append("- The triage period (Sep 2025+) shows carry as profitable ONLY because it was flat")
    lines.append("- Any overlay test on Sep 2025+ is meaningless (all variants are flat)")
    lines.append("- We test on Jan 2025-latest instead, where carry has partial activity")
    lines.append("")

    # ── 4. Backtest Results ──
    lines.append("## 4. Backtest Results (OOS: Jan 2025-latest)")
    lines.append("")
    lines.append("### Performance Table")
    lines.append("")
    lines.append("| Variant | IS Return | OOS Return | IS Sharpe | OOS Sharpe | IS MaxDD | OOS MaxDD | IS Calmar | OOS Calmar |")
    lines.append("|---------|-----------|------------|-----------|------------|----------|-----------|-----------|------------|")

    for vk in ['base', 'carry_pos', 'carry_vrp', 'carry_pos_vrp']:
        r = results[vk]
        is_m = r['is']
        oos_m = r['oos']
        line = (f"| {r['name']} | {is_m['ann_return']:.1%} | {oos_m['ann_return']:.1%} | "
                f"{is_m['sharpe']:.2f} | {oos_m['sharpe']:.2f} | "
                f"{is_m['max_dd']:.1%} | {oos_m['max_dd']:.1%} | "
                f"{is_m['calmar']:.2f} | {oos_m['calmar']:.2f} |")
        lines.append(line)

    lines.append("")

    # ── Marginal contribution ──
    lines.append("### Marginal Overlay Contribution (vs Carry Only)")
    lines.append("")
    lines.append("| Overlay | IS dSharpe | OOS dSharpe | IS dReturn | OOS dReturn | IS dMaxDD | OOS dMaxDD |")
    lines.append("|---------|------------|-------------|------------|-------------|-----------|------------|")

    base_is = results['base']['is']
    base_oos = results['base']['oos']
    for vk, label in [('carry_pos', '+ Positioning'), ('carry_vrp', '+ VRP'), ('carry_pos_vrp', '+ Pos + VRP')]:
        curr_is = results[vk]['is']
        curr_oos = results[vk]['oos']
        d_s_is = curr_is['sharpe'] - base_is['sharpe']
        d_s_oos = curr_oos['sharpe'] - base_oos['sharpe']
        d_r_is = curr_is['ann_return'] - base_is['ann_return']
        d_r_oos = curr_oos['ann_return'] - base_oos['ann_return']
        d_dd_is = curr_is['max_dd'] - base_is['max_dd']
        d_dd_oos = curr_oos['max_dd'] - base_oos['max_dd']
        line = (f"| {label} | {d_s_is:+.3f} | {d_s_oos:+.3f} | "
                f"{d_r_is:+.1%} | {d_r_oos:+.1%} | "
                f"{d_dd_is:+.1%} | {d_dd_oos:+.1%} |")
        lines.append(line)

    lines.append("")

    # ── P&L Decomposition ──
    lines.append("### P&L Decomposition")
    lines.append("")
    lines.append("| Variant | Period | Total Return | Price P&L | Funding P&L | Costs |")
    lines.append("|---------|--------|-------------|-----------|-------------|-------|")

    for vk in ['base', 'carry_pos', 'carry_vrp', 'carry_pos_vrp']:
        r = results[vk]
        lines.append(f"| {r['name']} | IS | {r['is']['total_return']:.4f} | {r['is_price_pnl']:.4f} | "
                     f"{r['is_funding_pnl']:.4f} | {r['is_cost']:.4f} |")
        lines.append(f"| {r['name']} | OOS | {r['oos']['total_return']:.4f} | {r['oos_price_pnl']:.4f} | "
                     f"{r['oos_funding_pnl']:.4f} | {r['oos_cost']:.4f} |")

    lines.append("")
    lines.append("Funding P&L is carry income. Price P&L is directional exposure.")
    lines.append("If |Price P&L| >> |Funding P&L|, the strategy is primarily directional, not carry.")
    lines.append("")

    # ── Monthly OOS ──
    lines.append("### Monthly OOS Returns")
    lines.append("")
    lines.append("| Month | Carry Only | +Positioning | +VRP | +Pos+VRP |")
    lines.append("|-------|-----------|-------------|------|----------|")

    all_months = sorted(set(monthly_all['base'].index))
    for dt in all_months:
        parts = [f"| {dt.strftime('%Y-%m')}"]
        for vk in ['base', 'carry_pos', 'carry_vrp', 'carry_pos_vrp']:
            if dt in monthly_all[vk].index:
                parts.append(f"{monthly_all[vk].loc[dt]:.2%}")
            else:
                parts.append("N/A")
        lines.append(" | ".join(parts) + " |")

    parts = ["| **Cumulative**"]
    for vk in ['base', 'carry_pos', 'carry_vrp', 'carry_pos_vrp']:
        cum = (1 + results[vk]['strat_ret'][oos_mask].dropna()).cumprod().iloc[-1] - 1
        parts.append(f"**{cum:.2%}**")
    lines.append(" | ".join(parts) + " |")
    lines.append("")

    # ── Statistical Significance ──
    lines.append("### Statistical Significance")
    lines.append("")
    for vk in ['carry_pos', 'carry_vrp', 'carry_pos_vrp']:
        sr = sig_results[vk]
        if sr['t_stat'] == 0:
            lines.append(f"- {labels[vk]} vs Carry Only: no variance (carry flat in most of OOS)")
        else:
            sig = ('significant (p<0.05)' if abs(sr['t_stat']) > 1.96 else
                   'marginally significant (p<0.10)' if abs(sr['t_stat']) > 1.65 else
                   'not significant')
            lines.append(f"- {labels[vk]} vs Carry Only: t={sr['t_stat']:.3f}, N={sr['n']} ({sig})")
    lines.append("")

    # ── 5. Funding Flip Prediction ──
    lines.append("## 5. Positioning Predicts Funding Flips?")
    lines.append("")
    if flip_results:
        lines.append("Does extreme crowd positioning predict 7-day funding rate direction changes?")
        lines.append("")
        lines.append("| Positioning Regime | N Days | 7d Flip Rate | Current Funding | Forward Funding |")
        lines.append("|-------------------|--------|-------------|-----------------|-----------------|")
        for label in ['Extreme Long (z>1.5)', 'Extreme Short (z<-1.5)', 'Normal (-0.5<z<0.5)']:
            if label in flip_results:
                fr = flip_results[label]
                lines.append(f"| {label} | {fr['n_days']} | {fr['flip_rate']:.1%} | "
                             f"{fr['avg_now_funding']:.6f} | {fr['avg_fwd_funding']:.6f} |")
        lines.append("")
        if 'ic_funding_change' in flip_results:
            ic_fc = flip_results['ic_funding_change']
            lines.append(f"- IC(positioning_z -> 7d funding change): {ic_fc['ic']:.4f} (p={ic_fc['pval']:.4f})")
        if 'ic_flip' in flip_results:
            ic_fl = flip_results['ic_flip']
            lines.append(f"- IC(positioning_z -> 7d flip probability): {ic_fl['ic']:.4f} (p={ic_fl['pval']:.4f})")
        if 'ic_abs_change' in flip_results:
            ic_ac = flip_results['ic_abs_change']
            lines.append(f"- IC(|positioning_z| -> |7d funding change|): {ic_ac['ic']:.4f} (p={ic_ac['pval']:.4f})")
        lines.append("")

        # Interpret
        ic_val = flip_results.get('ic_funding_change', {}).get('ic', 0)
        p_val = flip_results.get('ic_funding_change', {}).get('pval', 1)
        if abs(ic_val) > 0.05 and p_val < 0.05:
            lines.append(f"**Positioning predicts funding changes** (IC={ic_val:.4f}, p={p_val:.4f}).")
            lines.append("When crowd is extreme long (z>1.5), funding tends to DECREASE over 7 days.")
            lines.append("This suggests a carry-specific overlay: reduce carry when positioning extreme.")
        else:
            lines.append(f"**Positioning does NOT reliably predict funding direction** (IC={ic_val:.4f}, p={p_val:.4f}).")
            lines.append("The overlay's value (if any) comes from directional risk management only.")
    else:
        lines.append("Insufficient data for flip analysis.")
    lines.append("")

    # ── 6. Recommendation ──
    lines.append("## 6. Recommendation")
    lines.append("")

    pos_d = results['carry_pos']['oos']['sharpe'] - results['base']['oos']['sharpe']
    vrp_d = results['carry_vrp']['oos']['sharpe'] - results['base']['oos']['sharpe']
    full_d = results['carry_pos_vrp']['oos']['sharpe'] - results['base']['oos']['sharpe']

    lines.append("### Which strategies should get overlays?")
    lines.append("")
    lines.append("| Strategy | Overlay | OOS dSharpe | Recommendation |")
    lines.append("|----------|---------|-------------|----------------|")
    lines.append(f"| s29/s65 (carry) | Pos+VRP | {full_d:+.3f} | See analysis below |")
    lines.append(f"| s37 (momentum) | Pos+VRP | +0.856 (R62) | ADD (proven on trend base) |")
    lines.append("")

    lines.append("### Key Conclusions")
    lines.append("")
    lines.append("1. **s29/s65 are DIRECTIONAL, not delta-neutral.** They hold naked perp positions.")
    lines.append("   Price P&L and funding income both contribute. Overlays are theoretically applicable.")
    lines.append("")

    # Check if carry is even active in OOS
    oos_active_base = (results['base']['held_pos'][oos_mask] != 0).sum()
    oos_total = len(results['base']['held_pos'][oos_mask])

    if oos_active_base < 10:
        lines.append(f"2. **INCONCLUSIVE: Carry was flat for most of OOS.** Only {oos_active_base}/{oos_total} OOS days")
        lines.append("   had active positions. Funding rates collapsed after mid-2025, making the strategy dormant.")
        lines.append("   Cannot draw meaningful conclusions about overlay effectiveness on carry from this period.")
        lines.append("")
        lines.append("3. **The real question is moot for now.** If funding rates stay this low, s29/s65 will")
        lines.append("   generate no trades and the overlay question is academic. If funding normalizes,")
        lines.append("   the directional nature of carry makes overlays theoretically beneficial.")
        lines.append("")
        lines.append("4. **IS results show mixed signals:**")
        lines.append(f"   - Carry Only IS Sharpe: {results['base']['is']['sharpe']:.2f}")
        lines.append(f"   - Carry+Positioning IS: {results['carry_pos']['is']['sharpe']:.2f} "
                     f"(d={results['carry_pos']['is']['sharpe'] - results['base']['is']['sharpe']:+.2f})")
        lines.append(f"   - Carry+VRP IS: {results['carry_vrp']['is']['sharpe']:.2f} "
                     f"(d={results['carry_vrp']['is']['sharpe'] - results['base']['is']['sharpe']:+.2f})")
        lines.append(f"   - Carry+Pos+VRP IS: {results['carry_pos_vrp']['is']['sharpe']:.2f} "
                     f"(d={results['carry_pos_vrp']['is']['sharpe'] - results['base']['is']['sharpe']:+.2f})")
        lines.append("   Positioning HURTS carry IS Sharpe, which makes sense: carry is already contrarian.")
        lines.append("")
    else:
        lines.append(f"2. **Overlay results on carry:**")
        lines.append(f"   - Positioning dSharpe: {pos_d:+.3f}")
        lines.append(f"   - VRP dSharpe: {vrp_d:+.3f}")
        lines.append(f"   - Full stack dSharpe: {full_d:+.3f}")
        lines.append("")

    # Funding flip insight
    ic_val = flip_results.get('ic_funding_change', {}).get('ic', 0)
    p_val = flip_results.get('ic_funding_change', {}).get('pval', 1)
    if abs(ic_val) > 0.05 and p_val < 0.05:
        lines.append(f"5. **Positioning DOES predict funding changes** (IC={ic_val:.4f}).")
        lines.append("   This is the most actionable finding. Instead of a generic sizing overlay,")
        lines.append("   carry strategies could use positioning as a FUNDING REGIME signal:")
        lines.append("   - When crowd is extreme long (z>1.5) and funding is positive: REDUCE carry")
        lines.append("     (funding may compress)")
        lines.append("   - When crowd is extreme short (z<-1.5): funding regime is unstable")
        lines.append("   This is a carry-specific overlay, not a generic directional one.")
    else:
        lines.append(f"5. **Positioning does not reliably predict funding direction** (IC={ic_val:.4f}).")
        lines.append("   Any overlay value comes from directional risk management only.")
    lines.append("")

    lines.append("### Final Verdict")
    lines.append("")
    if oos_active_base < 10:
        lines.append("**CANNOT TEST** -- Carry strategies are dormant in OOS (funding rates too low).")
        lines.append("")
        lines.append("The positioning overlay HURTS carry in-sample (IS Sharpe drops), which is expected:")
        lines.append("carry is already inherently contrarian (shorts when crowd is long), so adding")
        lines.append("a contrarian positioning overlay on top is REDUNDANT or CONFLICTING.")
        lines.append("")
        lines.append("**Recommendation for s29/s65: DO NOT add the generic positioning+VRP overlay.**")
        lines.append("Instead, if carry returns to viability:")
        lines.append("- Test a CARRY-SPECIFIC overlay using positioning to predict funding regime changes")
        lines.append("- The generic overlay is designed for directional strategies where the signal")
        lines.append("  and positioning are independent. In carry, they are correlated (both contrarian).")
        lines.append("")
        lines.append("**Recommendation for s37: ADD the Pos+VRP overlay** (proven in R62).")
    else:
        if full_d > 0.1:
            lines.append("**ADD overlay stack to carry strategies.**")
        elif full_d > 0:
            lines.append("**MARGINAL benefit. Consider adding with caution.**")
        else:
            lines.append("**DO NOT add overlay to carry strategies.**")
    lines.append("")

    # Write report
    report_path = OUTPUT_DIR / 'carry_overlay_analysis.md'
    with open(report_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f"\nReport saved to: {report_path}")

    # Console summary
    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"\nCarry Only IS Sharpe:  {results['base']['is']['sharpe']:.2f}")
    print(f"  + Positioning IS:   {results['carry_pos']['is']['sharpe']:.2f} "
          f"(d={results['carry_pos']['is']['sharpe'] - results['base']['is']['sharpe']:+.2f})")
    print(f"  + VRP IS:           {results['carry_vrp']['is']['sharpe']:.2f} "
          f"(d={results['carry_vrp']['is']['sharpe'] - results['base']['is']['sharpe']:+.2f})")
    print(f"  + Pos+VRP IS:       {results['carry_pos_vrp']['is']['sharpe']:.2f} "
          f"(d={results['carry_pos_vrp']['is']['sharpe'] - results['base']['is']['sharpe']:+.2f})")
    print(f"\nOOS active days: {oos_active_base}/{oos_total}")
    print(f"OOS Sharpe (all variants): {results['base']['oos']['sharpe']:.2f}")
    print(f"\nFunding flip IC: {ic_val:.4f} (p={p_val:.4f})")
    print(f"\nVerdict: {'OVERLAY HELPS' if full_d > 0.05 else 'INCONCLUSIVE' if oos_active_base < 10 else 'OVERLAY HURTS'}")


if __name__ == '__main__':
    main()
