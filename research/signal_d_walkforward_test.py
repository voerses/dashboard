#!/workspace/venv/bin/python
"""
R74: Signal D Walk-Forward Validation — Alt Token Momentum + Overlays
======================================================================

Signal D Definition:
  - Long when: 30d ROC > 0 AND 90d ROC > 0 (both positive simultaneously)
  - Flat otherwise
  - ROC = (close / close_N_days_ago) - 1
  - With overlays: same positioning + VRP multipliers as V3

Overlay Specs:
  - Positioning: Binance Top Trader L/S + L/S Divergence combined z-score (30d)
    -> multiplier (z>1.5->0.3x, z>0.5->0.5x, neutral->1.0x, z<-0.5->1.3x, z<-1.5->1.5x)
  - VRP: (IV - RV) z-score (60d) -> multiplier
    (z>1->1.3x, z>-0.5->1.0x, z>-1.5->0.5x, z<-1.5->0.3x)
  - IV: 90d RV x 1.2 proxy (real DVOL only for BTC/ETH)
  - Weekly rebalance, 10 bps cost, position 0 to 1.5x

Tokens: ETH, BNB, XRP, DOGE (skip SOL — fails everything)

Walk-Forward Windows (4 rolling, adapt if data starts later):
  1. IS: 2021-01 to 2022-06, OOS: 2022-07 to 2022-12
  2. IS: 2021-07 to 2022-12, OOS: 2023-01 to 2023-06
  3. IS: 2022-07 to 2023-12, OOS: 2024-01 to 2024-06
  4. IS: 2023-07 to 2024-12, OOS: 2025-01 to 2025-06

KILL criteria per token:
  - Walk-forward win rate < 50%: KILL
  - Mean OOS Sharpe < 0: KILL
  - Overlay consistently hurts (dSharpe < 0 in >2 windows): drop overlays
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

COST_BPS = 10
TARGET_TOKENS = ['ETH', 'BNB', 'XRP', 'DOGE']
DVOL_TOKENS = {'BTC', 'ETH'}

# Walk-forward windows
WINDOWS = [
    {'id': 1, 'is_start': '2021-01-01', 'is_end': '2022-06-30', 'oos_start': '2022-07-01', 'oos_end': '2022-12-31'},
    {'id': 2, 'is_start': '2021-07-01', 'is_end': '2022-12-31', 'oos_start': '2023-01-01', 'oos_end': '2023-06-30'},
    {'id': 3, 'is_start': '2022-07-01', 'is_end': '2023-12-31', 'oos_start': '2024-01-01', 'oos_end': '2024-06-30'},
    {'id': 4, 'is_start': '2023-07-01', 'is_end': '2024-12-31', 'oos_start': '2025-01-01', 'oos_end': '2025-06-30'},
]


# ── 1. Data Loading ──────────────────────────────────────────────────────────

def load_spot_daily(token):
    """Load spot 1h data for a token and resample to daily."""
    path = DATA_DIR / f'spot/1h_cache/{token}_1h.parquet'
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    df.index.name = 'date'
    daily = df['close'].resample('D').last().dropna().to_frame('close')
    daily['open'] = df['open'].resample('D').first()
    daily['high'] = df['high'].resample('D').max()
    daily['low'] = df['low'].resample('D').min()
    daily['volume'] = df['volume'].resample('D').sum()
    return daily


def load_all_positioning():
    """Load all positioning data, return dict keyed by symbol (e.g., ETHUSDT)."""
    path = DATA_DIR / 'alternative/binance_metrics/all_symbols_daily_ls.parquet'
    df = pd.read_parquet(path)
    df['date'] = pd.to_datetime(df['date'])
    result = {}
    for symbol in df['symbol'].unique():
        sub = df[df['symbol'] == symbol].copy()
        sub = sub.set_index('date').sort_index()
        sub = sub[['sum_toptrader_ls_ratio', 'count_toptrader_ls_ratio', 'count_ls_ratio']].copy()
        sub = sub[~sub.index.duplicated(keep='last')]
        result[symbol] = sub
    return result


def load_dvol(token):
    """Load DVOL for a token. Returns Series or empty Series if not available."""
    fname = f'{token.lower()}_dvol_daily.json'
    path = DATA_DIR / f'alternative/deribit_options/dvol/{fname}'
    if not path.exists():
        return pd.Series(dtype=float)
    with open(path) as f:
        data = json.load(f)
    records = []
    for row in data:
        ts = pd.Timestamp(row[0], unit='ms')
        records.append({'date': ts, 'dvol_close': row[4]})
    dvol = pd.DataFrame(records).set_index('date').sort_index()
    dvol = dvol[~dvol.index.duplicated(keep='last')]
    return dvol['dvol_close']


# ── 2. Signal D Construction ────────────────────────────────────────────────

def build_signal_d_base(daily):
    """
    Signal D base: Long when 30d ROC > 0 AND 90d ROC > 0, flat otherwise.
    ROC = (close / close_N_days_ago) - 1
    """
    close = daily['close']
    roc_30 = close / close.shift(30) - 1.0
    roc_90 = close / close.shift(90) - 1.0
    position = ((roc_30 > 0) & (roc_90 > 0)).astype(float)
    return position, roc_30, roc_90


def build_positioning_signal(daily, positioning):
    """
    Positioning overlay: Top Trader L/S + L/S Divergence combined z-score.
    30d rolling z-score -> sizing multiplier.
    """
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


def build_vrp_signal(daily, dvol_series):
    """
    VRP sizing overlay:
    1. Realized vol: 20d rolling std of daily log returns, annualized
    2. Implied vol: DVOL close or RV proxy (90d RV x 1.2)
    3. VRP = IV - RV
    4. VRP z-score: 60d rolling z-score
    5. Sizing rule based on z-score thresholds
    """
    log_ret = np.log(daily['close'] / daily['close'].shift(1))
    rv_20d = log_ret.rolling(20, min_periods=15).std() * np.sqrt(365) * 100

    if dvol_series.empty or len(dvol_series) < 30:
        # Proxy: 90d RV x 1.2
        iv = log_ret.rolling(90, min_periods=60).std() * np.sqrt(365) * 100 * 1.2
        vrp_source = "proxy"
    else:
        iv = dvol_series.reindex(daily.index).ffill()
        vrp_source = "DVOL"

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
    return vrp_multiplier, vrp_z, vrp_source


# ── 3. Backtest Engine ───────────────────────────────────────────────────────

def run_backtest(daily, final_position, cost_bps=COST_BPS):
    """Run backtest with weekly rebalancing and transaction costs."""
    daily_ret = daily['close'].pct_change()

    # Identify rebalance days (every Monday)
    rebalance_dates = daily.index.to_series().groupby(
        daily.index.to_period('W')
    ).first()
    rebalance_set = set(rebalance_dates.values)

    held_position = pd.Series(0.0, index=daily.index)
    current_pos = 0.0
    costs = pd.Series(0.0, index=daily.index)

    for dt in daily.index:
        if dt in rebalance_set:
            new_pos = final_position.loc[dt]
            if not pd.isna(new_pos):
                pos_change = abs(new_pos - current_pos)
                costs.loc[dt] = pos_change * cost_bps / 10000.0
                current_pos = new_pos
        held_position.loc[dt] = current_pos

    strat_ret = held_position.shift(1) * daily_ret - costs
    return strat_ret, held_position, costs


def compute_metrics(returns, label=""):
    """Compute performance metrics from daily returns."""
    returns = returns.dropna()
    if len(returns) < 30:
        return {
            'label': label, 'total_return': np.nan, 'ann_return': np.nan,
            'ann_vol': np.nan, 'sharpe': np.nan, 'max_dd': np.nan,
            'calmar': np.nan, 'profit_factor': np.nan, 'n_days': len(returns),
        }

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

    # Profit factor
    gains = returns[returns > 0].sum()
    losses = abs(returns[returns < 0].sum())
    profit_factor = gains / losses if losses > 0 else np.inf

    return {
        'label': label,
        'total_return': total_ret,
        'ann_return': ann_ret,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'calmar': calmar,
        'profit_factor': profit_factor,
        'n_days': len(returns),
    }


# ── 4. Walk-Forward Per-Window Runner ────────────────────────────────────────

def run_window(daily, base_position, overlay_position, window):
    """
    Run a single walk-forward window. Returns IS and OOS metrics for both
    base-only and base+overlays configurations.
    """
    is_mask = (daily.index >= window['is_start']) & (daily.index <= window['is_end'])
    oos_mask = (daily.index >= window['oos_start']) & (daily.index <= window['oos_end'])

    # Check we have enough data
    is_days = is_mask.sum()
    oos_days = oos_mask.sum()
    if is_days < 180 or oos_days < 30:
        return None

    # Run backtests on full data, then slice
    base_ret, _, _ = run_backtest(daily, base_position)
    overlay_ret, _, _ = run_backtest(daily, overlay_position)

    # Buy-and-hold
    bh_ret = daily['close'].pct_change()

    results = {
        'window_id': window['id'],
        'is_start': window['is_start'],
        'is_end': window['is_end'],
        'oos_start': window['oos_start'],
        'oos_end': window['oos_end'],
        'is_days': int(is_days),
        'oos_days': int(oos_days),
    }

    # IS metrics
    results['base_is'] = compute_metrics(base_ret[is_mask], f"Base IS W{window['id']}")
    results['overlay_is'] = compute_metrics(overlay_ret[is_mask], f"Overlay IS W{window['id']}")
    results['bh_is'] = compute_metrics(bh_ret[is_mask], f"B&H IS W{window['id']}")

    # OOS metrics
    results['base_oos'] = compute_metrics(base_ret[oos_mask], f"Base OOS W{window['id']}")
    results['overlay_oos'] = compute_metrics(overlay_ret[oos_mask], f"Overlay OOS W{window['id']}")
    results['bh_oos'] = compute_metrics(bh_ret[oos_mask], f"B&H OOS W{window['id']}")

    # Derived
    base_oos_sharpe = results['base_oos']['sharpe']
    overlay_oos_sharpe = results['overlay_oos']['sharpe']
    if not (np.isnan(base_oos_sharpe) or np.isnan(overlay_oos_sharpe)):
        results['overlay_d_sharpe'] = overlay_oos_sharpe - base_oos_sharpe
    else:
        results['overlay_d_sharpe'] = np.nan

    results['oos_positive'] = base_oos_sharpe > 0 if not np.isnan(base_oos_sharpe) else False
    results['overlay_oos_positive'] = overlay_oos_sharpe > 0 if not np.isnan(overlay_oos_sharpe) else False

    return results


# ── 5. Per-Token Walk-Forward ────────────────────────────────────────────────

def walkforward_token(token, all_positioning, verbose=True):
    """
    Run walk-forward validation of Signal D on a single token.
    Returns list of window results + summary.
    """
    symbol = f'{token}USDT'
    if verbose:
        print(f"\n{'='*70}")
        print(f"  SIGNAL D WALK-FORWARD: {token}")
        print(f"{'='*70}")

    # Load spot data
    daily = load_spot_daily(token)
    if daily is None:
        if verbose:
            print(f"  SKIP: No spot data for {token}")
        return None

    data_start = daily.index.min()
    data_end = daily.index.max()
    if verbose:
        print(f"  Spot data: {data_start.date()} to {data_end.date()} ({len(daily)} days)")

    # Load positioning
    has_positioning = symbol in all_positioning
    if has_positioning:
        positioning = all_positioning[symbol]
        pos_start = positioning.index.min()
        pos_end = positioning.index.max()
        if verbose:
            print(f"  Positioning: {pos_start.date()} to {pos_end.date()} ({len(positioning)} days)")
    else:
        positioning = None
        if verbose:
            print(f"  Positioning: NOT AVAILABLE for {symbol}")

    # Load DVOL
    dvol = load_dvol(token) if token in DVOL_TOKENS else pd.Series(dtype=float)
    vrp_source = "DVOL" if not dvol.empty else "proxy (90d RV x 1.2)"
    if verbose:
        print(f"  VRP source: {vrp_source}")

    # Build Signal D base
    base_position, roc_30, roc_90 = build_signal_d_base(daily)

    # Build overlays
    if has_positioning:
        pos_multiplier, pos_z = build_positioning_signal(daily, positioning)
        vrp_multiplier, vrp_z, _ = build_vrp_signal(daily, dvol)
        overlay_position = (base_position * pos_multiplier * vrp_multiplier).clip(0, 1.5)
    else:
        overlay_position = base_position.copy()  # No overlay available

    # Determine which windows are valid
    valid_windows = []
    for w in WINDOWS:
        is_start = pd.Timestamp(w['is_start'])
        # Need at least 12 months of IS data from data start
        # Spot data must cover the IS start (with 90d warmup for ROC)
        warmup_start = is_start - pd.Timedelta(days=90)
        if data_start <= warmup_start:
            valid_windows.append(w)
        else:
            if verbose:
                print(f"  Window {w['id']}: SKIP (data starts {data_start.date()}, need {warmup_start.date()} for warmup)")

    if not valid_windows:
        if verbose:
            print(f"  SKIP: No valid windows for {token}")
        return None

    if verbose:
        print(f"  Valid windows: {[w['id'] for w in valid_windows]}")
        print()

    # Run each window
    window_results = []
    for w in valid_windows:
        wr = run_window(daily, base_position, overlay_position, w)
        if wr is None:
            if verbose:
                print(f"  Window {w['id']}: INSUFFICIENT DATA")
            continue
        window_results.append(wr)

        if verbose:
            base_oos = wr['base_oos']
            overlay_oos = wr['overlay_oos']
            bh_oos = wr['bh_oos']
            d_sharpe = wr['overlay_d_sharpe']
            print(f"  Window {w['id']} OOS ({w['oos_start']} to {w['oos_end']}):")
            print(f"    B&H:       Sharpe={bh_oos['sharpe']:+.3f}  Ret={bh_oos['ann_return']:+.1%}  MaxDD={bh_oos['max_dd']:.1%}")
            print(f"    D-base:    Sharpe={base_oos['sharpe']:+.3f}  Ret={base_oos['ann_return']:+.1%}  MaxDD={base_oos['max_dd']:.1%}")
            print(f"    D+overlay: Sharpe={overlay_oos['sharpe']:+.3f}  Ret={overlay_oos['ann_return']:+.1%}  MaxDD={overlay_oos['max_dd']:.1%}")
            if not np.isnan(d_sharpe):
                print(f"    Overlay dSharpe: {d_sharpe:+.3f}")
            print()

    if not window_results:
        if verbose:
            print(f"  SKIP: No window results for {token}")
        return None

    # Compute token-level summary
    n_windows = len(window_results)
    base_oos_sharpes = [wr['base_oos']['sharpe'] for wr in window_results if not np.isnan(wr['base_oos']['sharpe'])]
    overlay_oos_sharpes = [wr['overlay_oos']['sharpe'] for wr in window_results if not np.isnan(wr['overlay_oos']['sharpe'])]
    d_sharpes = [wr['overlay_d_sharpe'] for wr in window_results if not np.isnan(wr['overlay_d_sharpe'])]

    base_win_rate = sum(1 for s in base_oos_sharpes if s > 0) / len(base_oos_sharpes) if base_oos_sharpes else 0
    overlay_win_rate = sum(1 for s in overlay_oos_sharpes if s > 0) / len(overlay_oos_sharpes) if overlay_oos_sharpes else 0

    mean_base_oos_sharpe = np.mean(base_oos_sharpes) if base_oos_sharpes else np.nan
    mean_overlay_oos_sharpe = np.mean(overlay_oos_sharpes) if overlay_oos_sharpes else np.nan
    mean_d_sharpe = np.mean(d_sharpes) if d_sharpes else np.nan

    n_overlay_hurts = sum(1 for d in d_sharpes if d < 0)
    overlay_consistently_hurts = n_overlay_hurts > 2  # >2 out of up to 4 windows

    # KILL criteria
    base_killed = base_win_rate < 0.5 or mean_base_oos_sharpe < 0
    overlay_killed = overlay_consistently_hurts

    # Determine verdict
    if base_killed:
        verdict = "KILL"
        reason = []
        if base_win_rate < 0.5:
            reason.append(f"win rate {base_win_rate:.0%} < 50%")
        if mean_base_oos_sharpe < 0:
            reason.append(f"mean OOS Sharpe {mean_base_oos_sharpe:.3f} < 0")
        verdict_reason = f"KILL: {'; '.join(reason)}"
    elif mean_base_oos_sharpe > 0.3 and base_win_rate >= 0.75:
        if not overlay_killed:
            verdict = "DEPLOY"
            verdict_reason = f"DEPLOY: win rate {base_win_rate:.0%}, mean OOS Sharpe {mean_base_oos_sharpe:.3f}, overlays beneficial"
        else:
            verdict = "DEPLOY"
            verdict_reason = f"DEPLOY (base only): win rate {base_win_rate:.0%}, mean OOS Sharpe {mean_base_oos_sharpe:.3f}, but drop overlays (hurt in {n_overlay_hurts}/{n_windows} windows)"
    else:
        verdict = "MARGINAL"
        verdict_reason = f"MARGINAL: win rate {base_win_rate:.0%}, mean OOS Sharpe {mean_base_oos_sharpe:.3f}"

    summary = {
        'token': token,
        'n_windows': n_windows,
        'base_win_rate': base_win_rate,
        'overlay_win_rate': overlay_win_rate,
        'mean_base_oos_sharpe': mean_base_oos_sharpe,
        'mean_overlay_oos_sharpe': mean_overlay_oos_sharpe,
        'mean_d_sharpe': mean_d_sharpe,
        'n_overlay_hurts': n_overlay_hurts,
        'overlay_consistently_hurts': overlay_consistently_hurts,
        'verdict': verdict,
        'verdict_reason': verdict_reason,
        'vrp_source': vrp_source,
        'has_positioning': has_positioning,
        'window_results': window_results,
        'base_oos_sharpes': base_oos_sharpes,
        'overlay_oos_sharpes': overlay_oos_sharpes,
        'd_sharpes': d_sharpes,
    }

    if verbose:
        print(f"  --- {token} Summary ---")
        print(f"  Windows: {n_windows}")
        print(f"  Base win rate: {base_win_rate:.0%} ({sum(1 for s in base_oos_sharpes if s > 0)}/{len(base_oos_sharpes)})")
        print(f"  Mean base OOS Sharpe: {mean_base_oos_sharpe:.3f}")
        print(f"  Mean overlay OOS Sharpe: {mean_overlay_oos_sharpe:.3f}")
        print(f"  Mean overlay dSharpe: {mean_d_sharpe:+.3f}")
        print(f"  Overlay hurts: {n_overlay_hurts}/{n_windows} windows")
        print(f"  VERDICT: {verdict_reason}")

    return summary


# ── 6. Main Execution ────────────────────────────────────────────────────────

def main():
    print("=" * 72)
    print("R74: SIGNAL D WALK-FORWARD VALIDATION — ALT TOKENS")
    print("=" * 72)
    print()
    print("Signal D: Long when 30d ROC > 0 AND 90d ROC > 0, flat otherwise")
    print("Overlays: Positioning + VRP (same as V3)")
    print(f"Rebalancing: Weekly. Cost: {COST_BPS} bps. Position: 0 to 1.5x.")
    print(f"Tokens: {TARGET_TOKENS}")
    print()

    # Load positioning data
    print("Loading positioning data...")
    all_positioning = load_all_positioning()
    available_symbols = sorted(all_positioning.keys())
    print(f"  Available: {available_symbols}")
    print()

    # Run walk-forward for each token
    all_summaries = {}
    for token in TARGET_TOKENS:
        summary = walkforward_token(token, all_positioning, verbose=True)
        if summary is not None:
            all_summaries[token] = summary

    # ── Cross-Token Summary ──
    print("\n" + "=" * 72)
    print("CROSS-TOKEN SUMMARY")
    print("=" * 72)

    print(f"\n{'Token':<8} {'Win Rate':>10} {'Mean Sh':>10} {'Mean Ov Sh':>10} {'dSharpe':>10} {'Verdict':>12}")
    print("-" * 64)
    for token, s in all_summaries.items():
        print(f"{token:<8} {s['base_win_rate']:>10.0%} {s['mean_base_oos_sharpe']:>10.3f} "
              f"{s['mean_overlay_oos_sharpe']:>10.3f} {s['mean_d_sharpe']:>+10.3f} {s['verdict']:>12}")

    # ── Detailed Window Matrix ──
    print(f"\n{'Token':<8} {'W1 Base':>10} {'W1 Ov':>10} {'W2 Base':>10} {'W2 Ov':>10} {'W3 Base':>10} {'W3 Ov':>10} {'W4 Base':>10} {'W4 Ov':>10}")
    print("-" * 96)
    for token, s in all_summaries.items():
        row = f"{token:<8} "
        for wid in range(1, 5):
            # Find the window result with this id
            wr = None
            for w in s['window_results']:
                if w['window_id'] == wid:
                    wr = w
                    break
            if wr is not None:
                base_sh = wr['base_oos']['sharpe']
                ov_sh = wr['overlay_oos']['sharpe']
                row += f"{base_sh:>+10.3f} {ov_sh:>+10.3f} "
            else:
                row += f"{'N/A':>10} {'N/A':>10} "
        print(row)

    # ── Generate Report ──
    print("\n" + "=" * 72)
    print("GENERATING REPORT")
    print("=" * 72)

    generate_report(all_summaries)

    # Final verdicts
    print(f"\n{'='*72}")
    print("FINAL VERDICTS")
    print(f"{'='*72}")
    deploy_tokens = []
    marginal_tokens = []
    kill_tokens = []
    for token, s in all_summaries.items():
        print(f"  {token}: {s['verdict_reason']}")
        if s['verdict'] == 'DEPLOY':
            deploy_tokens.append(token)
        elif s['verdict'] == 'MARGINAL':
            marginal_tokens.append(token)
        else:
            kill_tokens.append(token)

    print()
    if deploy_tokens:
        print(f"  DEPLOY Signal D: {deploy_tokens}")
    if marginal_tokens:
        print(f"  MARGINAL (needs more evidence): {marginal_tokens}")
    if kill_tokens:
        print(f"  KILL (do not deploy): {kill_tokens}")


def generate_report(all_summaries):
    """Generate markdown report."""
    lines = []
    lines.append("# R74: Signal D Walk-Forward Validation — Alt Tokens")
    lines.append("")
    lines.append(f"**Run date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Signal**: D = Dual ROC 30/90 momentum (long when both ROC > 0)")
    lines.append(f"**Overlays**: Positioning (Binance Top Trader L/S z-score) + VRP (IV-RV z-score)")
    lines.append(f"**Rebalancing**: Weekly (Monday)")
    lines.append(f"**Transaction cost**: {COST_BPS} bps round-trip")
    lines.append(f"**Position range**: 0 to 1.5x")
    lines.append(f"**Tokens**: {', '.join(TARGET_TOKENS)}")
    lines.append("")

    # ── Walk-Forward Windows ──
    lines.append("## Walk-Forward Windows")
    lines.append("")
    lines.append("| Window | IS Period | OOS Period |")
    lines.append("|--------|-----------|------------|")
    for w in WINDOWS:
        lines.append(f"| {w['id']} | {w['is_start']} to {w['is_end']} | {w['oos_start']} to {w['oos_end']} |")
    lines.append("")

    # ── Per-Token OOS Sharpe Matrix ──
    lines.append("## 1. OOS Sharpe Matrix (Base D)")
    lines.append("")
    header = "| Token |"
    sep = "|-------|"
    for w in WINDOWS:
        header += f" W{w['id']} OOS |"
        sep += "---------|"
    header += " Mean | Win Rate | Verdict |"
    sep += "------|----------|---------|"
    lines.append(header)
    lines.append(sep)

    for token, s in all_summaries.items():
        row = f"| {token} |"
        for wid in range(1, 5):
            wr = None
            for w in s['window_results']:
                if w['window_id'] == wid:
                    wr = w
                    break
            if wr is not None:
                sh = wr['base_oos']['sharpe']
                marker = "+" if sh > 0 else ""
                row += f" {marker}{sh:.3f} |"
            else:
                row += " N/A |"
        row += f" {s['mean_base_oos_sharpe']:.3f} | {s['base_win_rate']:.0%} | **{s['verdict']}** |"
        lines.append(row)

    lines.append("")

    # ── Per-Token OOS Sharpe Matrix (with Overlays) ──
    lines.append("## 2. OOS Sharpe Matrix (D + Overlays)")
    lines.append("")
    header = "| Token |"
    sep = "|-------|"
    for w in WINDOWS:
        header += f" W{w['id']} OOS |"
        sep += "---------|"
    header += " Mean | Win Rate |"
    sep += "------|----------|"
    lines.append(header)
    lines.append(sep)

    for token, s in all_summaries.items():
        row = f"| {token} |"
        for wid in range(1, 5):
            wr = None
            for w in s['window_results']:
                if w['window_id'] == wid:
                    wr = w
                    break
            if wr is not None:
                sh = wr['overlay_oos']['sharpe']
                marker = "+" if sh > 0 else ""
                row += f" {marker}{sh:.3f} |"
            else:
                row += " N/A |"
        row += f" {s['mean_overlay_oos_sharpe']:.3f} | {s['overlay_win_rate']:.0%} |"
        lines.append(row)

    lines.append("")

    # ── Overlay dSharpe Matrix ──
    lines.append("## 3. Overlay Contribution (dSharpe = Overlay - Base)")
    lines.append("")
    header = "| Token |"
    sep = "|-------|"
    for w in WINDOWS:
        header += f" W{w['id']} |"
        sep += "------|"
    header += " Mean | Hurts? |"
    sep += "------|--------|"
    lines.append(header)
    lines.append(sep)

    for token, s in all_summaries.items():
        row = f"| {token} |"
        for wid in range(1, 5):
            wr = None
            for w in s['window_results']:
                if w['window_id'] == wid:
                    wr = w
                    break
            if wr is not None:
                d = wr['overlay_d_sharpe']
                if not np.isnan(d):
                    row += f" {d:+.3f} |"
                else:
                    row += " N/A |"
            else:
                row += " N/A |"
        hurts_label = f"YES ({s['n_overlay_hurts']}/{s['n_windows']})" if s['overlay_consistently_hurts'] else f"No ({s['n_overlay_hurts']}/{s['n_windows']})"
        row += f" {s['mean_d_sharpe']:+.3f} | {hurts_label} |"
        lines.append(row)

    lines.append("")

    # ── Per-Token Detailed Results ──
    lines.append("## 4. Per-Token Detailed Results")
    lines.append("")

    for token, s in all_summaries.items():
        lines.append(f"### {token}")
        lines.append("")
        lines.append(f"- **Positioning data**: {'Available' if s['has_positioning'] else 'NOT AVAILABLE'}")
        lines.append(f"- **VRP source**: {s['vrp_source']}")
        lines.append(f"- **Windows tested**: {s['n_windows']}")
        lines.append("")

        lines.append("| Window | Period | Base Sharpe | Base Ret | Base MaxDD | Overlay Sharpe | Overlay Ret | Overlay MaxDD | B&H Sharpe | dSharpe |")
        lines.append("|--------|--------|-------------|----------|------------|----------------|-------------|---------------|------------|---------|")

        for wr in s['window_results']:
            base = wr['base_oos']
            overlay = wr['overlay_oos']
            bh = wr['bh_oos']
            d = wr['overlay_d_sharpe']

            def fmt_s(v):
                return f"{v:+.3f}" if not np.isnan(v) else "N/A"

            def fmt_r(v):
                return f"{v:+.1%}" if not np.isnan(v) else "N/A"

            def fmt_d(v):
                return f"{v:+.3f}" if not np.isnan(v) else "N/A"

            lines.append(
                f"| W{wr['window_id']} "
                f"| {wr['oos_start']} to {wr['oos_end']} "
                f"| {fmt_s(base['sharpe'])} "
                f"| {fmt_r(base['ann_return'])} "
                f"| {fmt_r(base['max_dd'])} "
                f"| {fmt_s(overlay['sharpe'])} "
                f"| {fmt_r(overlay['ann_return'])} "
                f"| {fmt_r(overlay['max_dd'])} "
                f"| {fmt_s(bh['sharpe'])} "
                f"| {fmt_d(d)} |"
            )

        lines.append("")
        lines.append(f"**Verdict**: {s['verdict_reason']}")
        lines.append("")

    # ── KILL Analysis ──
    lines.append("## 5. KILL Analysis")
    lines.append("")
    lines.append("### Criteria")
    lines.append("- Walk-forward win rate < 50%: KILL that token")
    lines.append("- Mean OOS Sharpe < 0: KILL that token")
    lines.append("- Overlay consistently hurts (dSharpe < 0 in >2 windows): drop overlays")
    lines.append("")

    lines.append("### Results")
    lines.append("")
    lines.append("| Token | Win Rate | Mean OOS Sh | Overlay Hurts | Base Verdict | Overlay Verdict |")
    lines.append("|-------|----------|-------------|---------------|--------------|-----------------|")

    for token, s in all_summaries.items():
        wr_pass = "PASS" if s['base_win_rate'] >= 0.5 else "FAIL"
        sh_pass = "PASS" if s['mean_base_oos_sharpe'] >= 0 else "FAIL"
        base_v = "PASS" if wr_pass == "PASS" and sh_pass == "PASS" else "KILL"
        ov_v = "DROP" if s['overlay_consistently_hurts'] else "KEEP"

        lines.append(
            f"| {token} "
            f"| {s['base_win_rate']:.0%} ({wr_pass}) "
            f"| {s['mean_base_oos_sharpe']:.3f} ({sh_pass}) "
            f"| {s['n_overlay_hurts']}/{s['n_windows']} "
            f"| {base_v} "
            f"| {ov_v} |"
        )

    lines.append("")

    # ── Final Recommendations ──
    lines.append("## 6. Final Recommendations")
    lines.append("")

    deploy = []
    deploy_base_only = []
    marginal = []
    kill = []

    for token, s in all_summaries.items():
        if s['verdict'] == 'DEPLOY':
            if s['overlay_consistently_hurts']:
                deploy_base_only.append(token)
            else:
                deploy.append(token)
        elif s['verdict'] == 'MARGINAL':
            marginal.append(token)
        else:
            kill.append(token)

    if deploy:
        lines.append(f"### DEPLOY (Signal D + Overlays): {', '.join(deploy)}")
        lines.append("These tokens show robust walk-forward performance with both base momentum and overlay sizing.")
        lines.append("")

    if deploy_base_only:
        lines.append(f"### DEPLOY (Signal D Base Only, Drop Overlays): {', '.join(deploy_base_only)}")
        lines.append("Base momentum works but overlays consistently hurt. Use Signal D without positioning/VRP sizing.")
        lines.append("")

    if marginal:
        lines.append(f"### MARGINAL: {', '.join(marginal)}")
        lines.append("Not clearly profitable or not consistently winning across windows. Needs more evidence before deployment.")
        lines.append("")

    if kill:
        lines.append(f"### KILL: {', '.join(kill)}")
        lines.append("Failed walk-forward validation. Do NOT deploy Signal D on these tokens.")
        lines.append("")

    lines.append("### Production Signal D Candidates")
    lines.append("")
    all_deploy = deploy + deploy_base_only
    if all_deploy:
        lines.append(f"Tokens that should get Signal D in production: **{', '.join(all_deploy)}**")
        if deploy_base_only:
            lines.append(f"- {', '.join(deploy_base_only)}: base only (no overlays)")
        if deploy:
            lines.append(f"- {', '.join(deploy)}: with overlays")
    else:
        lines.append("**No tokens pass walk-forward validation for Signal D deployment.**")

    lines.append("")

    # Write report
    report_path = OUTPUT_DIR / 'signal_d_walkforward_results.md'
    with open(report_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f"\nReport saved to: {report_path}")


if __name__ == '__main__':
    main()
