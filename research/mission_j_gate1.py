#!/usr/bin/env python3
"""
Mission J Gate 1 — Cascade Recovery via Mission H microstructure signals.

Pipeline:
  1. Load BTCUSDT microstructure_1min parquet.
  2. Build Composite Pressure Index (CPI) from DtM + OFI-T + OFI-M + VDV.
  3. Detect cascade events: CPI > 0.7 sustained >= 5 minutes; ends when CPI < 0.5.
  4. Entry trigger: first minute after cascade peak when BTC vdv_5min crosses
     from below -0.3 back through 0. Enter ETH+SOL equal-weight long at the
     hourly close of that bar.
  5. Exit at first-of:
        - BTC vdv_30min returns to >= 0 (no vdv_1h column in data → use 30min)
        - 48h time stop
        - BTC CPI drops below -0.3
  6. Apply realistic costs (4bps fee/side, 5bps exit slippage).
  7. Aggregate metrics + alpha-vs-BTC-beta regression.
  8. Cross-reference with Mission H Phase 2 21-event cascade list.

Outputs:
    research/mission_j_cpi_btc.parquet          — BTC time series with CPI
    research/mission_j_trades.csv               — full trade log
    research/mission_j_gate1_results.json       — metrics
    research/mission_j_gate1_report.md          — verdict
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
MSIG_DIR = BASE / 'data' / 'perp' / 'binance'
HOURLY_DIR = BASE / 'data' / 'perp' / '1h_cache'
OUT_DIR = BASE / 'research'

FEE_BPS = 4.0 / 10_000.0     # 4 bps per side
SLIP_BPS = 5.0 / 10_000.0    # 5 bps exit slippage
LEVERAGE = 1.0
TIME_STOP_H = 48

CPI_TRIGGER = 0.80         # ~99.3th pct of normalised CPI
CPI_END = 0.40
CPI_REGIME_FLIP = -0.30
MIN_EVENT_GAP_MIN = 60      # merge events < 60min apart into one regime
# vdv entry uses z-scored vdv_5min; fire when vdv_5min_z crosses from <= -1 up
# through 0 (classic "dipped deep, now reverting" bounce signature)
VDV_ENTRY_Z_BELOW = -1.0
ENTRY_WINDOW_H = 4          # look up to 4h after cascade peak for entry


# ---------------------------------------------------------------------------
# Task 1 — CPI
# ---------------------------------------------------------------------------
def build_cpi(btc: pd.DataFrame) -> pd.DataFrame:
    """Add cpi column (and components) to btc; operates in-place and returns df."""
    df = btc.copy()

    def z(col: str) -> pd.Series:
        s = df[col].astype('float64')
        mu = s.mean()
        sd = s.std()
        if sd == 0 or not np.isfinite(sd):
            return pd.Series(0.0, index=s.index)
        return (s - mu) / sd

    # Components (sign per spec — cascade-down when these are positive)
    c1 = z('dtm_bid_5pct_z168h')          # bid thin
    c2 = z('dtm_asymmetry_5pct') * -1.0   # -asymmetry (low bid vs ask)
    c3 = z('ofi_t_5min') * -1.0           # -taker flow (sellers)
    c4 = z('ofi_m_total_5min') * -1.0     # -maker flow (bid withdrawal)
    c5 = z('vdv_5min') * -1.0             # -vdv (price below vwap and falling)

    # tanh(z/2) squashing
    c1t = np.tanh(c1 / 2.0)
    c2t = np.tanh(c2 / 2.0)
    c3t = np.tanh(c3 / 2.0)
    c4t = np.tanh(c4 / 2.0)
    c5t = np.tanh(c5 / 2.0)

    w = np.array([0.20, 0.20, 0.25, 0.25, 0.10])
    raw = w[0] * c1t + w[1] * c2t + w[2] * c3t + w[3] * c4t + w[4] * c5t

    # raw is in [-1,1] mathematically, but individual components rarely co-move
    # so its practical std is ~0.18. Re-normalise to unit std then tanh again to
    # bring the meaningful tail into the [-1,+1] range the spec uses for
    # thresholding (CPI > 0.7 = extreme).
    raw_mu = float(raw.mean())
    raw_sd = float(raw.std()) or 1.0
    cpi_z = (raw - raw_mu) / raw_sd
    cpi = np.tanh(cpi_z / 2.0)

    df['cpi_c1_dtm_bid'] = c1t
    df['cpi_c2_neg_asym'] = c2t
    df['cpi_c3_neg_ofit'] = c3t
    df['cpi_c4_neg_ofim'] = c4t
    df['cpi_c5_neg_vdv'] = c5t
    df['cpi_raw'] = raw.astype('float32')
    df['cpi'] = cpi.astype('float32')

    # z-scored vdv for entry signal (global mean/std over the 1-year window)
    for col in ('vdv_5min', 'vdv_30min'):
        s = df[col].astype('float64')
        mu = s.mean()
        sd = s.std()
        if sd > 0:
            df[f'{col}_z'] = ((s - mu) / sd).astype('float32')
        else:
            df[f'{col}_z'] = 0.0
    return df


# ---------------------------------------------------------------------------
# Task 2 — cascade event detection
# ---------------------------------------------------------------------------
def detect_cascades(df: pd.DataFrame) -> pd.DataFrame:
    """Return event DataFrame with ['start','peak','end','peak_cpi','duration_min'].

    Rules:
      - above_trigger: cpi > CPI_TRIGGER for >= 5 consecutive minutes
      - event ends first bar cpi < CPI_END
      - start = first minute cpi crosses above CPI_TRIGGER (before the 5-min run)
      - peak  = argmax cpi within [start, end]
      - suppress overlaps: a new event cannot start while inside an existing one
    """
    cpi = df['cpi'].to_numpy()
    n = len(cpi)
    events = []
    i = 0
    while i < n:
        if not np.isfinite(cpi[i]) or cpi[i] <= CPI_TRIGGER:
            i += 1
            continue
        # candidate start — require 5 consecutive bars above trigger
        j = i
        run = 0
        while j < n and np.isfinite(cpi[j]) and cpi[j] > CPI_TRIGGER:
            run += 1
            j += 1
        if run < 5:
            i = j + 1 if j > i else i + 1
            continue
        # event confirmed; expand until cpi < CPI_END
        start = i
        k = j
        while k < n and np.isfinite(cpi[k]) and cpi[k] >= CPI_END:
            k += 1
        end = min(k, n - 1)
        window = cpi[start:end + 1]
        peak_off = int(np.nanargmax(window))
        peak = start + peak_off
        events.append({
            'start_idx': start,
            'peak_idx': peak,
            'end_idx': end,
            'start_ts': df.index[start],
            'peak_ts': df.index[peak],
            'end_ts': df.index[end],
            'peak_cpi': float(cpi[peak]),
            'duration_min': int(end - start + 1),
        })
        i = end + 1
    df_ev = pd.DataFrame(events)
    if df_ev.empty:
        return df_ev
    # Merge events whose start is within MIN_EVENT_GAP_MIN minutes of the prior end
    merged = [df_ev.iloc[0].to_dict()]
    for _, r in df_ev.iloc[1:].iterrows():
        prev = merged[-1]
        gap = (r['start_ts'] - prev['end_ts']).total_seconds() / 60.0
        if gap < MIN_EVENT_GAP_MIN:
            # extend the previous event
            prev['end_idx'] = int(r['end_idx'])
            prev['end_ts'] = r['end_ts']
            prev['duration_min'] = int(prev['end_idx'] - prev['start_idx'] + 1)
            if r['peak_cpi'] > prev['peak_cpi']:
                prev['peak_cpi'] = float(r['peak_cpi'])
                prev['peak_idx'] = int(r['peak_idx'])
                prev['peak_ts'] = r['peak_ts']
        else:
            merged.append(r.to_dict())
    return pd.DataFrame(merged)


# ---------------------------------------------------------------------------
# Task 3 — entry signal
# ---------------------------------------------------------------------------
def find_entry(df: pd.DataFrame, peak_idx: int, end_idx: int) -> int | None:
    """First minute AFTER peak where vdv_5min crosses from <=-0.3 up through 0.

    Look up to min(peak+ENTRY_WINDOW_H*60, end_idx+60).
    """
    vdv_z = df['vdv_5min_z'].to_numpy()
    max_scan = min(len(df) - 1, max(end_idx, peak_idx + ENTRY_WINDOW_H * 60))
    seen_below = False
    for i in range(peak_idx, max_scan):
        v = vdv_z[i]
        if not np.isfinite(v):
            continue
        if v <= VDV_ENTRY_Z_BELOW:
            seen_below = True
        elif seen_below and v >= 0.0:
            return i
    return None


# ---------------------------------------------------------------------------
# Task 4 — exit logic
# ---------------------------------------------------------------------------
def find_exit(df: pd.DataFrame, entry_idx: int, mode: str = 'spec') -> tuple[int, str]:
    """Return (exit_idx, exit_reason).

    mode='spec': use vdv_30min mean-reversion + CPI regime flip + 48h stop.
                 vdv_30min is used because vdv_1h is not a column in the data.
                 60-min grace period to avoid same-bar exit.
    mode='vdv4h': use vdv_4h crossing positive (longer horizon) + CPI flip + 48h.
    mode='time48h': pure 48h time stop (for alpha diagnostic).
    """
    n = len(df)
    max_idx = min(n - 1, entry_idx + TIME_STOP_H * 60)
    grace = 60
    cpi = df['cpi'].to_numpy()
    if mode == 'time48h':
        return max_idx, 'time_stop'
    if mode == 'vdv4h':
        vdv = df['vdv_4h'].to_numpy()
        exit_label = 'vdv4h_zero'
    else:
        vdv = df['vdv_30min'].to_numpy()
        exit_label = 'vdv30_zero'
    for i in range(entry_idx + grace, max_idx):
        v = vdv[i]
        c = cpi[i]
        if np.isfinite(v) and v >= 0.0:
            return i, exit_label
        if np.isfinite(c) and c <= CPI_REGIME_FLIP:
            return i, 'cpi_regime_flip'
    return max_idx, 'time_stop'


# ---------------------------------------------------------------------------
# Task 5 — trade P&L with hourly close pricing
# ---------------------------------------------------------------------------
def price_at_hour(hourly: pd.DataFrame, ts: pd.Timestamp) -> tuple[pd.Timestamp, float]:
    """Snap ts up to the next hourly bar close and return (bar_ts, close_price)."""
    # hourly index is the bar open; use the first index >= ts.floor('h') + 1h
    target = ts.floor('h')  # bar containing ts
    if target not in hourly.index:
        # take next available
        loc = hourly.index.searchsorted(target, side='left')
        if loc >= len(hourly):
            return hourly.index[-1], float(hourly['close'].iloc[-1])
        target = hourly.index[loc]
    return target, float(hourly.loc[target, 'close'])


def compute_trades(
    df: pd.DataFrame,
    events: pd.DataFrame,
    hourly: dict[str, pd.DataFrame],
    exit_mode: str = 'spec',
) -> pd.DataFrame:
    rows = []
    for _, ev in events.iterrows():
        peak_idx = int(ev['peak_idx'])
        end_idx = int(ev['end_idx'])
        entry_idx = find_entry(df, peak_idx, end_idx)
        if entry_idx is None:
            continue
        exit_idx, exit_reason = find_exit(df, entry_idx, mode=exit_mode)
        entry_ts = df.index[entry_idx]
        exit_ts = df.index[exit_idx]

        for sym in ('ETH', 'SOL'):
            h = hourly[sym]
            e_bar_ts, e_px = price_at_hour(h, entry_ts)
            x_bar_ts, x_px = price_at_hour(h, exit_ts)
            if e_bar_ts >= x_bar_ts:
                # protect against degenerate (entry and exit in same bar)
                loc = h.index.searchsorted(e_bar_ts, side='right')
                if loc >= len(h):
                    continue
                x_bar_ts = h.index[loc]
                x_px = float(h.loc[x_bar_ts, 'close'])

            gross = (x_px - e_px) / e_px * LEVERAGE
            # cost model: 4 bps fee entry + 4 bps fee exit + 5 bps exit slippage
            costs = FEE_BPS * 2.0 + SLIP_BPS
            net = gross - costs
            hold_h = (x_bar_ts - e_bar_ts).total_seconds() / 3600.0

            # BTC return over same window for beta regression
            bh = hourly['BTC']
            _, btc_e = price_at_hour(bh, entry_ts)
            _, btc_x = price_at_hour(bh, exit_ts)
            btc_ret = (btc_x - btc_e) / btc_e

            rows.append({
                'event_peak_ts': ev['peak_ts'],
                'event_peak_cpi': ev['peak_cpi'],
                'entry_signal_ts': entry_ts,
                'exit_signal_ts': exit_ts,
                'exit_reason': exit_reason,
                'symbol': sym,
                'entry_bar_ts': e_bar_ts,
                'exit_bar_ts': x_bar_ts,
                'entry_px': e_px,
                'exit_px': x_px,
                'gross_pnl_pct': gross,
                'costs_pct': costs,
                'pnl_pct': net,
                'hold_hours': hold_h,
                'btc_ret': btc_ret,
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Task 6 — aggregate metrics + alpha regression
# ---------------------------------------------------------------------------
def compute_metrics(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {'n_trades': 0}

    # Event-level: average the two symbol returns per event (equal weight basket)
    ev = trades.groupby('event_peak_ts').agg(
        pnl_pct=('pnl_pct', 'mean'),
        gross=('gross_pnl_pct', 'mean'),
        btc_ret=('btc_ret', 'mean'),
        hold_h=('hold_hours', 'mean'),
        exit_reason=('exit_reason', 'first'),
    ).reset_index()

    n = len(ev)
    mean = float(ev['pnl_pct'].mean())
    med = float(ev['pnl_pct'].median())
    sd = float(ev['pnl_pct'].std(ddof=1)) if n > 1 else 0.0
    wins = int((ev['pnl_pct'] > 0).sum())
    win_rate = wins / n if n else 0.0

    # Event-level Sharpe (annualised assuming events are ~iid; use n per year)
    days = (ev['event_peak_ts'].max() - ev['event_peak_ts'].min()).days or 1
    events_per_year = n * 365.0 / max(days, 1)
    sharpe = (mean / sd) * np.sqrt(events_per_year) if sd > 0 else 0.0

    # Equity curve for MaxDD/Calmar (compound returns in entry order)
    ev_sorted = ev.sort_values('event_peak_ts').reset_index(drop=True)
    eq = (1.0 + ev_sorted['pnl_pct']).cumprod()
    running_max = eq.cummax()
    dd = eq / running_max - 1.0
    max_dd = float(dd.min()) if len(dd) else 0.0
    total_ret = float(eq.iloc[-1] - 1.0) if len(eq) else 0.0
    # Annualise total return
    years = days / 365.0 if days > 0 else 1.0
    cagr = (eq.iloc[-1]) ** (1.0 / max(years, 0.25)) - 1.0 if len(eq) else 0.0
    calmar = cagr / abs(max_dd) if max_dd < 0 else float('inf')

    # Alpha vs BTC beta — OLS regression pnl = alpha + beta*btc_ret
    x = ev['btc_ret'].to_numpy(dtype='float64')
    y = ev['pnl_pct'].to_numpy(dtype='float64')
    if n >= 3 and x.std() > 0:
        xm = x - x.mean()
        ym = y - y.mean()
        beta = float((xm * ym).sum() / (xm * xm).sum())
        alpha = float(y.mean() - beta * x.mean())
        y_hat = alpha + beta * x
        resid = y - y_hat
        sse = float((resid ** 2).sum())
        dof = n - 2
        sigma2 = sse / dof if dof > 0 else np.nan
        # Standard error of alpha
        var_alpha = sigma2 * (1.0 / n + (x.mean() ** 2) / (xm * xm).sum())
        se_alpha = float(np.sqrt(var_alpha)) if var_alpha > 0 else np.nan
        alpha_t = alpha / se_alpha if se_alpha and se_alpha > 0 else float('nan')
    else:
        alpha = beta = alpha_t = float('nan')

    hold_stats = {
        'mean': float(ev['hold_h'].mean()),
        'median': float(ev['hold_h'].median()),
        'p25': float(ev['hold_h'].quantile(0.25)),
        'p75': float(ev['hold_h'].quantile(0.75)),
    }
    exit_breakdown = ev['exit_reason'].value_counts().to_dict()

    return {
        'n_events_with_entry': int(n),
        'n_trade_legs': int(len(trades)),
        'days_span': int(days),
        'events_per_year': float(events_per_year),
        'mean_pnl_pct': mean,
        'median_pnl_pct': med,
        'stdev_pnl_pct': sd,
        'wins': wins,
        'win_rate': win_rate,
        'sharpe_event_annualised': float(sharpe),
        'total_return': total_ret,
        'cagr': float(cagr),
        'max_drawdown': max_dd,
        'calmar': float(calmar),
        'alpha': alpha,
        'beta_btc': beta,
        'alpha_t_stat': alpha_t,
        'hold_hours': hold_stats,
        'exit_reason_counts': {str(k): int(v) for k, v in exit_breakdown.items()},
    }


# ---------------------------------------------------------------------------
# Task 2b — cross-reference with Mission H 21-event list
# ---------------------------------------------------------------------------
def cross_reference_mh(df: pd.DataFrame, events: pd.DataFrame, btc_hourly: pd.DataFrame):
    # Replicate Mission H trigger: BTC 1h ret < -2% and vol > 2x trailing 24h avg
    h = btc_hourly.copy()
    h['ret'] = h['close'].pct_change()
    h['vol_ma24'] = h['volume'].rolling(24, min_periods=6).mean().shift(1)
    h['vol_ratio'] = h['volume'] / h['vol_ma24']
    start = df.index.min()
    end = df.index.max()
    h = h.loc[(h.index >= start) & (h.index <= end)]
    mh_mask = (h['ret'] < -0.02) & (h['vol_ratio'] > 2.0)
    mh_events = h.index[mh_mask].tolist()

    # Match: an MH event is "caught" if any CPI event [start_ts, end_ts] overlaps
    # the [mh_ts - 2h, mh_ts + 2h] window.
    caught = 0
    detail = []
    has_events = len(events) > 0 and 'end_ts' in events.columns
    for mh_ts in mh_events:
        lo = mh_ts - pd.Timedelta(hours=2)
        hi = mh_ts + pd.Timedelta(hours=2)
        if not has_events:
            detail.append({'mh_ts': str(mh_ts), 'cpi_peak_ts': None, 'peak_cpi': None})
            continue
        hits = events[(events['end_ts'] >= lo) & (events['start_ts'] <= hi)]
        if len(hits) > 0:
            caught += 1
            detail.append({'mh_ts': str(mh_ts), 'cpi_peak_ts': str(hits.iloc[0]['peak_ts']),
                           'peak_cpi': float(hits.iloc[0]['peak_cpi'])})
        else:
            detail.append({'mh_ts': str(mh_ts), 'cpi_peak_ts': None, 'peak_cpi': None})
    return {
        'mh_events_total': len(mh_events),
        'mh_events_caught_by_cpi': caught,
        'cpi_events_total': len(events),
        'detail': detail,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    print('Loading BTCUSDT microstructure ...')
    btc_raw = pd.read_parquet(MSIG_DIR / 'BTCUSDT' / 'microstructure_1min.parquet')
    btc_raw['ts'] = pd.to_datetime(btc_raw['ts'])
    btc_raw = btc_raw.set_index('ts').sort_index()
    print(f'  rows={len(btc_raw):,}, range={btc_raw.index.min()} .. {btc_raw.index.max()}')

    print('Building CPI ...')
    btc = build_cpi(btc_raw)
    print(f'  cpi: min={btc["cpi"].min():.3f} max={btc["cpi"].max():.3f} '
          f'mean={btc["cpi"].mean():.3f} std={btc["cpi"].std():.3f}')
    # Save CPI parquet
    out_cpi = OUT_DIR / 'mission_j_cpi_btc.parquet'
    keep_cols = ['mid_price', 'vdv_5min', 'vdv_30min', 'vdv_4h',
                 'ofi_m_total_5min', 'ofi_t_5min',
                 'dtm_bid_5pct_z168h', 'dtm_asymmetry_5pct',
                 'cpi', 'cpi_raw', 'cpi_c1_dtm_bid', 'cpi_c2_neg_asym',
                 'cpi_c3_neg_ofit', 'cpi_c4_neg_ofim', 'cpi_c5_neg_vdv']
    btc[keep_cols].reset_index().to_parquet(out_cpi, index=False)
    print(f'  wrote {out_cpi}')

    print('Detecting cascade events ...')
    events = detect_cascades(btc)
    print(f'  found {len(events)} events')
    if len(events):
        print('  peak_cpi stats: '
              f'mean={events["peak_cpi"].mean():.3f}, '
              f'median={events["peak_cpi"].median():.3f}, '
              f'max={events["peak_cpi"].max():.3f}')
        print(f'  duration_min stats: mean={events["duration_min"].mean():.1f}, '
              f'median={events["duration_min"].median():.1f}, '
              f'max={events["duration_min"].max()}')

    print('Loading hourly OHLCV for BTC/ETH/SOL ...')
    hourly = {}
    for sym in ('BTC', 'ETH', 'SOL'):
        h = pd.read_parquet(HOURLY_DIR / f'{sym}_1h.parquet')
        h.index = pd.to_datetime(h.index)
        h = h.sort_index()
        hourly[sym] = h
        print(f'  {sym}: {len(h):,} bars')

    print('Cross-referencing with Mission H 21-event set ...')
    mh_cross = cross_reference_mh(btc, events, hourly['BTC'])
    print(f"  MH events total = {mh_cross['mh_events_total']}, "
          f"caught by CPI = {mh_cross['mh_events_caught_by_cpi']}, "
          f"CPI events total = {mh_cross['cpi_events_total']}")

    print('Computing trades (exit=spec: vdv30_zero + cpi_flip + 48h stop) ...')
    trades = compute_trades(btc, events, hourly, exit_mode='spec')
    print(f'  {len(trades)} trade legs ({len(trades)//2} events with entries)')

    # Save trades csv (primary run)
    out_trades = OUT_DIR / 'mission_j_trades.csv'
    trades.to_csv(out_trades, index=False)
    print(f'  wrote {out_trades}')

    # CPI trigger sweep with time48h exit — shows raw alpha at different tails
    print('CPI trigger sweep (exit=time48h) ...')
    sweep_rows = []
    orig_trig = CPI_TRIGGER
    orig_end = CPI_END
    for trig in [0.70, 0.75, 0.80, 0.85, 0.90]:
        import mission_j_gate1 as self_mod  # self-reference to mutate module globals
        self_mod.CPI_TRIGGER = trig
        self_mod.CPI_END = max(0.2, trig - 0.40)
        ev_s = detect_cascades(btc)
        if len(ev_s) == 0:
            continue
        t_s = compute_trades(btc, ev_s, hourly, exit_mode='time48h')
        m_s = compute_metrics(t_s)
        sweep_rows.append({
            'cpi_trigger': trig,
            'cpi_end': self_mod.CPI_END,
            'n_events_raw': int(len(ev_s)),
            'n_entries': m_s.get('n_events_with_entry', 0),
            'mean_pnl_pct': m_s.get('mean_pnl_pct'),
            'median_pnl_pct': m_s.get('median_pnl_pct'),
            'win_rate': m_s.get('win_rate'),
            'sharpe': m_s.get('sharpe_event_annualised'),
            'max_drawdown': m_s.get('max_drawdown'),
            'calmar': m_s.get('calmar'),
            'alpha': m_s.get('alpha'),
            'beta_btc': m_s.get('beta_btc'),
            'alpha_t_stat': m_s.get('alpha_t_stat'),
        })
    # restore
    import mission_j_gate1 as self_mod
    self_mod.CPI_TRIGGER = orig_trig
    self_mod.CPI_END = orig_end

    print('Supplementary run (exit=vdv4h_zero + cpi_flip + 48h stop) ...')
    trades_v4h = compute_trades(btc, events, hourly, exit_mode='vdv4h')
    metrics_v4h = compute_metrics(trades_v4h)
    print('Supplementary run (exit=pure 48h time stop, alpha diagnostic) ...')
    trades_t48 = compute_trades(btc, events, hourly, exit_mode='time48h')
    metrics_t48 = compute_metrics(trades_t48)

    print('Computing metrics (primary: spec exit) ...')
    metrics = compute_metrics(trades)
    for k, v in metrics.items():
        if isinstance(v, dict):
            print(f'  {k}: {v}')
        else:
            print(f'  {k}: {v}')

    # Events per month distribution
    ev_month = {}
    if len(events):
        em = events.copy()
        em['month'] = em['peak_ts'].dt.to_period('M').astype(str)
        ev_month = em.groupby('month').size().to_dict()

    results = {
        'config': {
            'weights': [0.20, 0.20, 0.25, 0.25, 0.10],
            'cpi_trigger': CPI_TRIGGER,
            'cpi_end': CPI_END,
            'cpi_regime_flip': CPI_REGIME_FLIP,
            'vdv_entry_z_below': VDV_ENTRY_Z_BELOW,
            'min_event_gap_min': MIN_EVENT_GAP_MIN,
            'exit_grace_min': 60,
            'entry_window_hours': ENTRY_WINDOW_H,
            'time_stop_hours': TIME_STOP_H,
            'fee_bps_per_side': 4.0,
            'slippage_bps_exit': 5.0,
            'leverage': LEVERAGE,
            'universe': ['ETHUSDT', 'SOLUSDT'],
        },
        'cpi_events': {
            'total': int(len(events)),
            'peak_cpi_mean': float(events['peak_cpi'].mean()) if len(events) else None,
            'peak_cpi_median': float(events['peak_cpi'].median()) if len(events) else None,
            'duration_min_mean': float(events['duration_min'].mean()) if len(events) else None,
            'duration_min_median': float(events['duration_min'].median()) if len(events) else None,
            'events_per_month': ev_month,
        },
        'mh_cross_reference': mh_cross,
        'metrics': metrics,
        'supplementary_metrics_vdv4h_exit': metrics_v4h,
        'supplementary_metrics_time48h_exit': metrics_t48,
        'cpi_trigger_sweep_time48h': sweep_rows,
    }

    # Verdict
    thresholds = {
        'event_count_min': 15,
        'sharpe_min': 1.5,
        'calmar_min': 2.5,
        'max_dd_min': -0.25,
        'alpha_t_min': 1.5,
    }
    m = metrics
    checks = {
        'event_count': (m.get('n_events_with_entry', 0) > thresholds['event_count_min']),
        'sharpe': (m.get('sharpe_event_annualised', 0) > thresholds['sharpe_min']),
        'calmar': (m.get('calmar', 0) > thresholds['calmar_min']),
        'max_dd': (m.get('max_drawdown', -1) > thresholds['max_dd_min']),
        'alpha_t': ((m.get('alpha_t_stat') is not None and
                     np.isfinite(m.get('alpha_t_stat', np.nan)) and
                     m.get('alpha_t_stat', 0) > thresholds['alpha_t_min'])),
    }
    passed = sum(bool(v) for v in checks.values())
    if passed == 5:
        verdict = 'PASS'
    elif passed == 4:
        verdict = 'NEEDS_TUNING'
    else:
        verdict = 'KILL'
    results['thresholds'] = thresholds
    results['checks'] = {k: bool(v) for k, v in checks.items()}
    results['verdict'] = verdict
    results['checks_passed'] = int(passed)

    out_json = OUT_DIR / 'mission_j_gate1_results.json'
    with open(out_json, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f'\nVerdict: {verdict} ({passed}/5 checks passed)')
    print(f'wrote {out_json}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
