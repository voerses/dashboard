"""
R99 — Trend + Pullback Walk-Forward Validation
Signal from R98: Daily EMA(20)/EMA(50) trend + 4h RSI(14) pullback entry.
Protocol: 10 rolling windows, 180-day train / 90-day test, rolling 90 days.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import List, Dict, Optional
from itertools import product
import warnings
import time
warnings.filterwarnings('ignore')

# ============================================================
# DATA LOADING & PRE-COMPUTATION
# ============================================================

DATA_PATHS = {
    'BTC': '/workspace/crypto_backtest/data/spot/1h_cache/BTC_1h.parquet',
    'ETH': '/workspace/crypto_backtest/data/spot/1h_cache/ETH_1h.parquet',
}


def load_and_prepare(token: str) -> Dict:
    """Load data and pre-compute all indicators once."""
    df_1h = pd.read_parquet(DATA_PATHS[token])
    df_1h.index = pd.to_datetime(df_1h.index)
    df_1h = df_1h.sort_index()
    df_1h = df_1h[~df_1h.index.duplicated(keep='first')]

    # Daily indicators
    daily = df_1h['close'].resample('1D').last().dropna()
    daily_df = pd.DataFrame({'close': daily})
    daily_df['ema20'] = daily.ewm(span=20, adjust=False).mean()
    daily_df['ema50'] = daily.ewm(span=50, adjust=False).mean()
    daily_df['sma50'] = daily.rolling(50).mean()
    daily_df['uptrend'] = daily_df['ema20'] > daily_df['ema50']

    # 4h bars
    bars_4h = df_1h.resample('4h').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).dropna(subset=['close'])

    # RSI on 4h
    period = 14
    delta = bars_4h['close'].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    bars_4h['rsi'] = 100 - (100 / (1 + rs))

    # Pre-compute the daily trend lookup for each 4h bar (avoid lookahead)
    # Shift daily data forward by 1 day: data from day D is only available on day D+1
    # Then reindex to 4h bars using ffill
    daily_shifted = daily_df.copy()
    daily_shifted.index = daily_shifted.index + pd.Timedelta(days=1)

    # Reindex daily to 4h bar timestamps, forward-fill
    daily_at_4h = daily_shifted[['uptrend', 'close', 'sma50']].reindex(bars_4h.index, method='ffill')

    bars_4h['uptrend'] = daily_at_4h['uptrend'].astype('object')  # allows NaN
    bars_4h['regime'] = np.where(
        daily_at_4h['sma50'].isna(), None,
        np.where(daily_at_4h['close'] > daily_at_4h['sma50'], 'up', 'down')
    )

    return {
        'df_1h': df_1h,
        'daily': daily_df,
        'bars_4h': bars_4h,
    }


# ============================================================
# FAST SIMULATION (uses pre-computed indicators)
# ============================================================

@dataclass
class Trade:
    entry_time: object
    entry_price: float
    direction: str
    exit_time: object = None
    exit_price: float = 0.0
    pnl_pct: float = 0.0
    bars_held: int = 0
    regime: str = ''


def simulate_fast(bars_4h: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
                  rsi_long_thresh: float = 35.0, rsi_short_thresh: float = 65.0,
                  max_hold_days: int = 7, fee_bps: float = 10.0,
                  direction_filter: str = 'both') -> List[Trade]:
    """
    Fast simulation using pre-computed 4h bars with RSI, trend, and regime.
    Entry: Long when uptrend + RSI < thresh. Short when downtrend + RSI > thresh.
    Exit: RSI crosses 50 or max hold.
    """
    test = bars_4h.loc[start:end]
    max_hold_bars = max_hold_days * 6
    fee_rate = fee_bps / 10000.0

    trades = []
    in_trade = False
    current = None
    hold = 0

    rsi_vals = test['rsi'].values
    close_vals = test['close'].values
    uptrend_vals = test['uptrend'].values
    regime_vals = test['regime'].values
    timestamps = test.index

    for i in range(len(test)):
        rsi = rsi_vals[i]
        close = close_vals[i]
        trend = uptrend_vals[i]
        regime = regime_vals[i]

        if pd.isna(rsi) or pd.isna(close) or pd.isna(trend):
            continue

        if in_trade:
            hold += 1
            exit_now = False

            if current.direction == 'long':
                if rsi > 50 or hold >= max_hold_bars:
                    exit_now = True
            else:
                if rsi < 50 or hold >= max_hold_bars:
                    exit_now = True

            if exit_now:
                if current.direction == 'long':
                    pnl = (close / current.entry_price - 1) - 2 * fee_rate
                else:
                    pnl = (current.entry_price / close - 1) - 2 * fee_rate
                current.exit_time = timestamps[i]
                current.exit_price = close
                current.pnl_pct = pnl
                current.bars_held = hold
                trades.append(current)
                in_trade = False
                current = None
                hold = 0

        if not in_trade:
            if direction_filter in ('both', 'long') and trend and rsi < rsi_long_thresh:
                current = Trade(
                    entry_time=timestamps[i],
                    entry_price=close,
                    direction='long',
                    regime=regime if pd.notna(regime) else '',
                )
                in_trade = True
                hold = 0
            elif direction_filter in ('both', 'short') and not trend and rsi > rsi_short_thresh:
                current = Trade(
                    entry_time=timestamps[i],
                    entry_price=close,
                    direction='short',
                    regime=regime if pd.notna(regime) else '',
                )
                in_trade = True
                hold = 0

    # Close open trade
    if in_trade and current is not None:
        last_close = close_vals[~np.isnan(close_vals)][-1]
        if current.direction == 'long':
            pnl = (last_close / current.entry_price - 1) - 2 * fee_rate
        else:
            pnl = (current.entry_price / last_close - 1) - 2 * fee_rate
        current.exit_time = timestamps[-1]
        current.exit_price = last_close
        current.pnl_pct = pnl
        current.bars_held = hold
        trades.append(current)

    return trades


# ============================================================
# METRICS
# ============================================================

@dataclass
class Metrics:
    total_pnl: float = 0.0
    sharpe: float = 0.0
    trade_count: int = 0
    win_rate: float = 0.0
    max_dd: float = 0.0
    avg_pnl: float = 0.0
    profit_factor: float = 0.0


def compute_metrics(trades: List[Trade]) -> Metrics:
    m = Metrics()
    if not trades:
        return m

    pnls = np.array([t.pnl_pct for t in trades])
    m.trade_count = len(trades)
    m.total_pnl = float(np.sum(pnls))
    m.avg_pnl = float(np.mean(pnls))
    m.win_rate = float(np.sum(pnls > 0) / len(pnls))

    gp = float(np.sum(pnls[pnls > 0])) if np.any(pnls > 0) else 0.0
    gl = float(np.abs(np.sum(pnls[pnls < 0]))) if np.any(pnls < 0) else 0.0
    m.profit_factor = gp / gl if gl > 0 else (999.0 if gp > 0 else 0.0)

    if len(pnls) > 1 and np.std(pnls) > 0:
        avg_hold = np.mean([t.bars_held * 4 for t in trades])
        tpy = 8760 / max(avg_hold, 4)
        m.sharpe = float((np.mean(pnls) / np.std(pnls)) * np.sqrt(tpy))
    else:
        m.sharpe = 0.0

    equity = np.cumprod(1 + pnls)
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    m.max_dd = float(np.abs(np.min(dd)))

    return m


# ============================================================
# WALK-FORWARD WINDOWS
# ============================================================

def build_windows(data_start: pd.Timestamp, data_end: pd.Timestamp,
                  train_days: int = 180, test_days: int = 90, n_windows: int = 10):
    """
    10 rolling windows: 180-day train / 90-day test, rolling 90 days.
    Position windows so the last test window ends near data_end, maximizing
    coverage of recent data while having enough warmup for indicators.
    """
    windows = []
    # Total span: first_train_start to last_test_end
    # = first_train_start + (n-1)*step + train_days + test_days - 1
    total_span = (n_windows - 1) * test_days + train_days + test_days
    # Want last test end <= data_end, need warmup >= 60 days before first train
    # first_train_start = data_end - total_span + 1
    ideal_first_train = data_end - pd.Timedelta(days=total_span - 1)
    # Ensure at least 60 days warmup
    min_first_train = data_start + pd.Timedelta(days=60)
    first_train_start = max(ideal_first_train, min_first_train)

    for i in range(n_windows):
        train_start = first_train_start + pd.Timedelta(days=i * test_days)
        train_end = train_start + pd.Timedelta(days=train_days - 1)
        test_start = train_end + pd.Timedelta(days=1)
        test_end = test_start + pd.Timedelta(days=test_days - 1)

        if test_start > data_end:
            break
        if test_end > data_end:
            test_end = data_end

        windows.append({
            'id': f'W{i+1}',
            'train_start': train_start,
            'train_end': train_end,
            'test_start': test_start,
            'test_end': test_end,
        })

    return windows


# ============================================================
# PARAMETER OPTIMIZATION
# ============================================================

def optimize_params(bars_4h: pd.DataFrame, train_start, train_end, direction_filter='both') -> Dict:
    """Grid search: RSI long [30-40 step 2], RSI short [60-70 step 2], max hold [5-10]."""
    long_grid = [30, 32, 34, 36, 38, 40]
    short_grid = [60, 62, 64, 66, 68, 70]
    hold_grid = [5, 6, 7, 8, 9, 10]

    best_sharpe = -999.0
    best_params = {'rsi_long': 35, 'rsi_short': 65, 'max_hold': 7}

    for rsi_l, rsi_s, hold_d in product(long_grid, short_grid, hold_grid):
        trades = simulate_fast(
            bars_4h, train_start, train_end,
            rsi_long_thresh=float(rsi_l),
            rsi_short_thresh=float(rsi_s),
            max_hold_days=hold_d,
            direction_filter=direction_filter,
        )
        m = compute_metrics(trades)
        if m.trade_count >= 3 and m.sharpe > best_sharpe:
            best_sharpe = m.sharpe
            best_params = {'rsi_long': rsi_l, 'rsi_short': rsi_s, 'max_hold': hold_d}

    best_params['train_sharpe'] = best_sharpe
    return best_params


# ============================================================
# MAIN WALK-FORWARD
# ============================================================

def run_walkforward():
    t0 = time.time()
    print("=" * 70)
    print("R99 — Trend + Pullback Walk-Forward Validation")
    print("=" * 70)

    all_data = {}
    results = {}

    for token in ['BTC', 'ETH']:
        print(f"\nLoading & preparing {token}...", flush=True)
        prep = load_and_prepare(token)
        all_data[token] = prep
        bars_4h = prep['bars_4h']
        data_start = bars_4h.index.min()
        data_end = bars_4h.index.max()
        print(f"  4h bars: {len(bars_4h)}, range {data_start.date()} to {data_end.date()}")

        windows = build_windows(data_start, data_end)
        print(f"  Windows: {len(windows)}")
        for w in windows:
            print(f"    {w['id']}: train {w['train_start'].date()}-{w['train_end'].date()}, "
                  f"test {w['test_start'].date()}-{w['test_end'].date()}")

        results[token] = {}

        for direction in ['long', 'short', 'combined']:
            dir_filter = 'both' if direction == 'combined' else direction
            window_results = []
            print(f"\n  --- {token} {direction.upper()} ---", flush=True)

            for w in windows:
                # TRAIN
                best = optimize_params(bars_4h, w['train_start'], w['train_end'],
                                       direction_filter=dir_filter)

                # TEST (OOS)
                oos_trades = simulate_fast(
                    bars_4h, w['test_start'], w['test_end'],
                    rsi_long_thresh=float(best['rsi_long']),
                    rsi_short_thresh=float(best['rsi_short']),
                    max_hold_days=best['max_hold'],
                    direction_filter=dir_filter,
                )
                oos_metrics = compute_metrics(oos_trades)

                # Regime split
                up_trades = [t for t in oos_trades if t.regime == 'up']
                down_trades = [t for t in oos_trades if t.regime == 'down']
                regime_metrics = {
                    'up': compute_metrics(up_trades),
                    'down': compute_metrics(down_trades),
                }

                # Sensitivity: params +/-20%
                sensitivity = {}
                for label, mult in [('minus20', 0.8), ('plus20', 1.2)]:
                    adj_long = max(20, min(50, best['rsi_long'] * mult))
                    adj_short = max(50, min(80, best['rsi_short'] * mult))
                    adj_hold = max(3, min(14, int(best['max_hold'] * mult)))
                    sens_trades = simulate_fast(
                        bars_4h, w['test_start'], w['test_end'],
                        rsi_long_thresh=float(adj_long),
                        rsi_short_thresh=float(adj_short),
                        max_hold_days=adj_hold,
                        direction_filter=dir_filter,
                    )
                    sensitivity[label] = compute_metrics(sens_trades)

                wr = {
                    'window': w,
                    'best_params': best,
                    'oos_metrics': oos_metrics,
                    'oos_trades': oos_trades,
                    'regime_metrics': regime_metrics,
                    'sensitivity': sensitivity,
                }
                window_results.append(wr)

                print(f"    {w['id']} train_best=L<{best['rsi_long']} S>{best['rsi_short']} "
                      f"H{best['max_hold']}d (train_sharpe={best['train_sharpe']:.2f}) | "
                      f"OOS: n={oos_metrics.trade_count}, PnL={oos_metrics.total_pnl:.2%}, "
                      f"sharpe={oos_metrics.sharpe:.2f}, WR={oos_metrics.win_rate:.1%}, "
                      f"DD={oos_metrics.max_dd:.2%}", flush=True)

            results[token][direction] = window_results

    elapsed = time.time() - t0
    print(f"\nTotal elapsed: {elapsed:.1f}s")
    return results, all_data


# ============================================================
# KILL CRITERIA
# ============================================================

def evaluate_kill(results: Dict) -> Dict:
    verdicts = {}

    for token in ['BTC', 'ETH']:
        for direction in ['long', 'short', 'combined']:
            key = f"{token}_{direction}"
            wrs = results[token][direction]

            oos_list = [wr['oos_metrics'] for wr in wrs]
            total_windows = len(oos_list)

            positive_windows = sum(1 for m in oos_list if m.total_pnl > 0)
            sharpes = [m.sharpe for m in oos_list if m.trade_count > 0]
            mean_sharpe = float(np.mean(sharpes)) if sharpes else 0.0
            total_trades = sum(m.trade_count for m in oos_list)
            oos_returns = [m.total_pnl for m in oos_list if m.trade_count > 0]
            mean_return = float(np.mean(oos_returns)) if oos_returns else 0.0
            win_rates = [m.win_rate for m in oos_list if m.trade_count > 0]
            mean_wr = float(np.mean(win_rates)) if win_rates else 0.0
            dds = [m.max_dd for m in oos_list if m.trade_count > 0]
            mean_dd = float(np.mean(dds)) if dds else 0.0

            # Sensitivity degradation
            degradations = []
            for wr in wrs:
                base_s = wr['oos_metrics'].sharpe
                if wr['oos_metrics'].trade_count == 0 or base_s == 0:
                    continue
                for label in ['minus20', 'plus20']:
                    sens_s = wr['sensitivity'][label].sharpe
                    degradations.append((base_s - sens_s) / abs(base_s))

            mean_degrade = float(np.mean(degradations)) if degradations else 0.0
            fragile = mean_degrade > 0.30

            kills = []
            if positive_windows < 5:
                kills.append(f"<5/10 positive OOS windows ({positive_windows}/{total_windows})")
            if mean_sharpe < 0.3:
                kills.append(f"Mean OOS Sharpe < 0.3 ({mean_sharpe:.2f})")
            if total_trades < 30:
                kills.append(f"<30 total OOS trades ({total_trades})")

            conditional = []
            if fragile:
                conditional.append(f"Param sensitivity: mean degradation {mean_degrade:.1%}")

            if kills:
                verdict = 'KILL'
            elif conditional:
                verdict = 'CONDITIONAL PASS'
            else:
                verdict = 'PASS'

            verdicts[key] = {
                'verdict': verdict,
                'positive_windows': positive_windows,
                'total_windows': total_windows,
                'mean_sharpe': mean_sharpe,
                'total_trades': total_trades,
                'mean_return': mean_return,
                'mean_wr': mean_wr,
                'mean_dd': mean_dd,
                'mean_degrade': mean_degrade,
                'fragile': fragile,
                'kills': kills,
                'conditional': conditional,
            }

    return verdicts


# ============================================================
# REPORT
# ============================================================

def generate_report(results: Dict, verdicts: Dict, all_data: Dict) -> str:
    lines = []
    lines.append("# R99 -- Trend+Pullback Walk-Forward Validation")
    lines.append("")
    lines.append("## Signal Definition (R98)")
    lines.append("- **Daily trend**: EMA(20) vs EMA(50) on daily bars. Up = long bias, Down = short bias.")
    lines.append("- **4h pullback entry**: RSI(14) on 4h bars. Long when RSI < threshold in uptrend. Short when RSI > threshold in downtrend.")
    lines.append("- **Exit**: RSI crosses back above 50 (longs) or below 50 (shorts), OR max hold days reached.")
    lines.append("- **Fees**: 10 bps round-trip (5 bps each way).")
    lines.append("")
    lines.append("## Walk-Forward Protocol")
    lines.append("- 10 rolling windows: 180-day train / 90-day test, rolling forward 90 days")
    lines.append("- Train: grid search RSI thresholds (long: 30-40 step 2, short: 60-70 step 2) and max hold (5-10 days)")
    lines.append("- Optimization target: Sharpe ratio")
    lines.append("- Test: apply best train params to OOS window")
    lines.append(f"- Date: {pd.Timestamp.now().strftime('%Y-%m-%d')}")
    lines.append("")

    # ---- SUMMARY TABLE ----
    lines.append("## Summary Verdicts")
    lines.append("")
    lines.append("| Token | Direction | Verdict | Pos. Windows | Mean Sharpe | Total Trades | Mean OOS Return | Mean WR | Mean DD | Fragile? |")
    lines.append("|-------|-----------|---------|-------------|-------------|------------|-----------------|---------|---------|----------|")

    for token in ['BTC', 'ETH']:
        for direction in ['long', 'short', 'combined']:
            key = f"{token}_{direction}"
            v = verdicts[key]
            lines.append(
                f"| {token} | {direction} | **{v['verdict']}** | "
                f"{v['positive_windows']}/{v['total_windows']} | "
                f"{v['mean_sharpe']:.2f} | "
                f"{v['total_trades']} | "
                f"{v['mean_return']:.2%} | "
                f"{v['mean_wr']:.1%} | "
                f"{v['mean_dd']:.2%} | "
                f"{'YES' if v['fragile'] else 'no'} |"
            )
    lines.append("")

    # ---- KILL CRITERIA ----
    lines.append("## Kill Criteria")
    lines.append("")
    lines.append("| Criterion | Threshold |")
    lines.append("|-----------|-----------|")
    lines.append("| Positive OOS windows | >= 5 / 10 |")
    lines.append("| Mean OOS Sharpe | >= 0.3 |")
    lines.append("| Total OOS trades | >= 30 |")
    lines.append("| Parameter sensitivity | mean Sharpe degradation < 30% with params +/-20% |")
    lines.append("")

    any_issues = False
    for key, v in verdicts.items():
        if v['kills'] or v['conditional']:
            any_issues = True
            lines.append(f"### {key}: **{v['verdict']}**")
            for k in v['kills']:
                lines.append(f"- KILL: {k}")
            for c in v['conditional']:
                lines.append(f"- CONDITIONAL: {c}")
            lines.append("")

    if not any_issues:
        lines.append("All token/direction combinations pass all kill criteria.")
        lines.append("")

    # ---- PER-WINDOW DETAIL ----
    for token in ['BTC', 'ETH']:
        for direction in ['long', 'short', 'combined']:
            wrs = results[token][direction]
            lines.append(f"## {token} {direction.upper()} -- Per-Window OOS Results")
            lines.append("")
            lines.append("| Window | Test Period | Optimized Params | Trades | PnL | Sharpe | WR | MaxDD | PF |")
            lines.append("|--------|------------|------------------|--------|-----|--------|-----|-------|-----|")

            for wr in wrs:
                w = wr['window']
                bp = wr['best_params']
                m = wr['oos_metrics']
                ps = f"L<{bp['rsi_long']} S>{bp['rsi_short']} H{bp['max_hold']}d"
                ts = f"{w['test_start'].strftime('%Y-%m-%d')} to {w['test_end'].strftime('%Y-%m-%d')}"
                if m.trade_count > 0:
                    lines.append(
                        f"| {w['id']} | {ts} | {ps} | "
                        f"{m.trade_count} | {m.total_pnl:.2%} | {m.sharpe:.2f} | "
                        f"{m.win_rate:.1%} | {m.max_dd:.2%} | {m.profit_factor:.2f} |"
                    )
                else:
                    lines.append(f"| {w['id']} | {ts} | {ps} | 0 | - | - | - | - | - |")
            lines.append("")

    # ---- PARAMETER SENSITIVITY ----
    lines.append("## Parameter Sensitivity")
    lines.append("")
    lines.append("Base train-optimal params adjusted +/-20% and re-run on each OOS window.")
    lines.append(">30% mean Sharpe degradation = FRAGILE.")
    lines.append("")

    for token in ['BTC', 'ETH']:
        for direction in ['long', 'short', 'combined']:
            key = f"{token}_{direction}"
            wrs = results[token][direction]
            lines.append(f"### {token} {direction.upper()} (mean degradation: {verdicts[key]['mean_degrade']:.1%})")
            lines.append("")
            lines.append("| Window | Base Sharpe | -20% Sharpe | +20% Sharpe | -20% Degrade | +20% Degrade |")
            lines.append("|--------|------------|------------|------------|-------------|-------------|")

            for wr in wrs:
                w = wr['window']
                bs = wr['oos_metrics'].sharpe
                ms = wr['sensitivity']['minus20'].sharpe
                ps = wr['sensitivity']['plus20'].sharpe
                if bs != 0 and wr['oos_metrics'].trade_count > 0:
                    dm = (bs - ms) / abs(bs)
                    dp = (bs - ps) / abs(bs)
                    lines.append(f"| {w['id']} | {bs:.2f} | {ms:.2f} | {ps:.2f} | {dm:.1%} | {dp:.1%} |")
                else:
                    lines.append(f"| {w['id']} | {bs:.2f} | {ms:.2f} | {ps:.2f} | n/a | n/a |")
            lines.append("")

    # ---- REGIME ROBUSTNESS ----
    lines.append("## Regime Robustness (Up vs Down Markets)")
    lines.append("")
    lines.append("OOS trades split by daily close vs 50-day SMA at entry.")
    lines.append("")

    for token in ['BTC', 'ETH']:
        for direction in ['long', 'short', 'combined']:
            wrs = results[token][direction]
            up_all = []
            down_all = []
            for wr in wrs:
                for t in wr['oos_trades']:
                    if t.regime == 'up':
                        up_all.append(t)
                    elif t.regime == 'down':
                        down_all.append(t)

            m_up = compute_metrics(up_all)
            m_down = compute_metrics(down_all)

            lines.append(f"### {token} {direction.upper()}")
            lines.append("")
            lines.append("| Regime | Trades | PnL | Sharpe | WR | MaxDD | PF |")
            lines.append("|--------|--------|-----|--------|-----|-------|-----|")

            for label, m_r in [('Up (>SMA50)', m_up), ('Down (<SMA50)', m_down)]:
                if m_r.trade_count > 0:
                    lines.append(
                        f"| {label} | {m_r.trade_count} | {m_r.total_pnl:.2%} | "
                        f"{m_r.sharpe:.2f} | {m_r.win_rate:.1%} | {m_r.max_dd:.2%} | {m_r.profit_factor:.2f} |"
                    )
                else:
                    lines.append(f"| {label} | 0 | - | - | - | - | - |")
            lines.append("")

    # ---- FINAL VERDICT ----
    lines.append("## Final Verdict")
    lines.append("")
    lines.append("| Token | Direction | Verdict | Reason |")
    lines.append("|-------|-----------|---------|--------|")
    for token in ['BTC', 'ETH']:
        for direction in ['long', 'short', 'combined']:
            key = f"{token}_{direction}"
            v = verdicts[key]
            reasons = "; ".join(v['kills'] + v['conditional']) if (v['kills'] or v['conditional']) else "All criteria passed"
            lines.append(f"| {token} | {direction} | **{v['verdict']}** | {reasons} |")
    lines.append("")

    # Overall
    lines.append("### Overall Recommendation")
    lines.append("")
    killed = {k: v for k, v in verdicts.items() if v['verdict'] == 'KILL'}
    cond = {k: v for k, v in verdicts.items() if v['verdict'] == 'CONDITIONAL PASS'}
    passed = {k: v for k, v in verdicts.items() if v['verdict'] == 'PASS'}

    if killed:
        lines.append("**Some token/direction combinations FAIL walk-forward validation:**")
        lines.append("")
        for k, v in killed.items():
            lines.append(f"- **{k}**: KILL -- {'; '.join(v['kills'])}")
        lines.append("")
    if cond:
        lines.append("**Conditional passes (parameter fragility detected):**")
        lines.append("")
        for k, v in cond.items():
            lines.append(f"- **{k}**: CONDITIONAL -- {'; '.join(v['conditional'])}")
        lines.append("")
    if passed:
        lines.append("**Clean passes:**")
        lines.append("")
        for k, v in passed.items():
            lines.append(f"- **{k}**: PASS")
        lines.append("")

    if not killed and not cond:
        lines.append("All token/direction combinations PASS walk-forward validation.")
        lines.append("The signal is robust across tokens, directions, parameter perturbations, and market regimes.")
    elif killed:
        viable = list(cond.keys()) + list(passed.keys())
        if viable:
            lines.append(f"Viable for deployment: {', '.join(viable)}")
            lines.append("Consider restricting to passing directions only.")
        else:
            lines.append("No viable combinations. Signal should be abandoned or fundamentally redesigned.")
    lines.append("")

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

if __name__ == '__main__':
    results, all_data = run_walkforward()
    verdicts = evaluate_kill(results)
    report = generate_report(results, verdicts, all_data)

    output_path = '/workspace/crypto_backtest/research/R99_trend_pullback_walkforward.md'
    with open(output_path, 'w') as f:
        f.write(report)

    print(f"\n{'='*70}")
    print(f"Report written to {output_path}")
    print(f"{'='*70}")

    print("\n--- VERDICTS ---")
    for key, v in verdicts.items():
        print(f"  {key}: {v['verdict']} | "
              f"pos={v['positive_windows']}/{v['total_windows']} | "
              f"sharpe={v['mean_sharpe']:.2f} | "
              f"trades={v['total_trades']} | "
              f"fragile={'YES' if v['fragile'] else 'no'}")
        for k in v['kills']:
            print(f"    KILL: {k}")
        for c in v['conditional']:
            print(f"    COND: {c}")
