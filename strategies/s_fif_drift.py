"""Mission D Gate 3 — FIF × drift composite strategy (BTC/ETH/SOL).

Strategy origin:
  Mission D Gate 0 IC test → FIF × drift signed signal showed positive IC at
  24/72/168h horizons across BTC/ETH/SOL.
  Gate 1 sweep → best config: thr=2.0, hold=72h, long_only on BTC/ETH/SOL.
  Gate 2 robustness → 5/6 strict criteria, 6/6 block bootstrap, alpha_t=+3.58,
  max corr vs Tier A book = 0.089.

This module exposes:
  - generate_signal_events(closes) → per-token list of (entry_ts, direction)
  - run_strategy(bt, ...)         → drives a tools.raw_backtest.Backtest instance

The signal logic mirrors research/mission_d_gate1.py exactly:
  • Daily cadence sample of FIF × drift on a 200-bar trailing window of 1h
    log returns. The window at sample t uses returns r[t-FIF_WINDOW:t]
    (i.e. closed-bar trailing — no look-ahead).
  • Rolling 200-sample z-score baseline.
  • Long entry on cross-up: z[i-1] < THR ≤ z[i] with THR = 2.0.
  • 72-hour time stop, exit at the next available 1h close.
  • Long-only, BTC/ETH/SOL universe.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

# Reuse Gate 0 indicator primitives
REPO = Path("/workspace/crypto_backtest")
if str(REPO / "research") not in sys.path:
    sys.path.insert(0, str(REPO / "research"))
import mission_d_gate0 as mdg  # noqa: E402

# ---------------------------------------------------------------------------
# Strategy parameters (FROZEN — these are the Gate 1/2 winning config)
# ---------------------------------------------------------------------------
TOKENS: List[str] = ["BTC", "ETH", "SOL"]
SAMPLE_STEP_BARS: int = 24      # daily cadence
FIF_WINDOW: int = mdg.FIF_WINDOW  # 200
ZSCORE_WINDOW: int = 200
THRESHOLD: float = 2.0
HOLD_HOURS: int = 72
DIRECTION: str = "long_only"
INITIAL_BURN_IN: int = max(
    mdg.HURST_WINDOW + mdg.HURST_VEL_LAG,
    mdg.FIF_WINDOW,
    mdg.WRD_WINDOW + mdg.WRD_LAG,
)


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------
def _log_returns(close: pd.Series) -> pd.Series:
    return np.log(close.astype(np.float64)).diff()


def roll_fif_x_drift(close: pd.Series,
                     sample_step: int = SAMPLE_STEP_BARS) -> pd.DataFrame:
    """Rolling FIF × drift on a 1h close series, sampled every `sample_step` bars.

    No look-ahead: at sample index `end`, we slice `rets.iloc[end-FIF_WINDOW:end]`
    so the right edge is `rets.index[end-1]` and the indicator is timestamped at
    `rets.index[end-1]` (the most recently CLOSED 1h bar at decision time).
    """
    rets = _log_returns(close).dropna()
    n = len(rets)
    out_idx: List[pd.Timestamp] = []
    fxd_vals: List[float] = []
    for end in range(INITIAL_BURN_IN, n, sample_step):
        slice_r = rets.iloc[max(0, end - FIF_WINDOW): end].to_numpy()
        if len(slice_r) < FIF_WINDOW:
            continue
        try:
            i_f, drift, _ = mdg.compute_fif(slice_r)
        except Exception:
            continue
        if not (np.isfinite(i_f) and np.isfinite(drift)):
            continue
        out_idx.append(rets.index[end - 1])
        fxd_vals.append(i_f * drift)
    return pd.DataFrame(
        {"FIF_x_drift": fxd_vals},
        index=pd.DatetimeIndex(out_idx, name="datetime"),
    )


def add_zscore(panel: pd.DataFrame,
               col: str = "FIF_x_drift",
               window: int = ZSCORE_WINDOW) -> pd.DataFrame:
    s = panel[col]
    mu = s.rolling(window, min_periods=window // 2).mean()
    sd = s.rolling(window, min_periods=window // 2).std()
    panel = panel.copy()
    panel[f"{col}_z"] = (s - mu) / sd
    return panel


def generate_signal_events(
    closes: Dict[str, pd.Series],
    threshold: float = THRESHOLD,
) -> Dict[str, List[Tuple[pd.Timestamp, int]]]:
    """For each token, return a list of (entry_ts, direction) cross-up events.

    `entry_ts` is the timestamp of the first 1h bar AT or AFTER the sample
    timestamp at which the z-score crossed up through `threshold`. Direction
    is always +1 (long-only).
    """
    events: Dict[str, List[Tuple[pd.Timestamp, int]]] = {}
    for tok, close in closes.items():
        panel = roll_fif_x_drift(close)
        panel = add_zscore(panel)
        z = panel["FIF_x_drift_z"].to_numpy()
        sample_ts = panel.index.to_numpy()
        tok_events: List[Tuple[pd.Timestamp, int]] = []
        for i in range(1, len(z)):
            if not (np.isfinite(z[i]) and np.isfinite(z[i - 1])):
                continue
            if z[i - 1] < threshold and z[i] >= threshold:
                tok_events.append((pd.Timestamp(sample_ts[i]), 1))
        events[tok] = tok_events
    return events


# ---------------------------------------------------------------------------
# Driver for tools.raw_backtest.Backtest
# ---------------------------------------------------------------------------
def run_strategy(
    bt,
    tokens: List[str] = None,
    threshold: float = THRESHOLD,
    hold_hours: int = HOLD_HOURS,
    size_per_trade_frac: float = None,
) -> None:
    """Run the FIF × drift strategy on a `tools.raw_backtest.Backtest` instance.

    Equal split of capital across tokens (1/N per concurrent slot). Each entry
    consumes its token's slot until the 72h time stop fires. If a new entry
    fires while the slot is occupied, it is skipped (no pyramiding).

    `size_per_trade_frac` defaults to `1 / len(tokens)` of starting capital.
    """
    tokens = tokens or TOKENS
    if size_per_trade_frac is None:
        size_per_trade_frac = 1.0 / len(tokens)

    # 1. Load 1h closes for each token from the harness data cache
    closes: Dict[str, pd.Series] = {}
    for tok in tokens:
        df = bt.data.load(tok)
        closes[tok] = df["close"].astype(np.float64)

    # 2. Pre-compute signal events on the FULL history (no look-ahead — the
    #    signal at time t uses returns through t-1, see roll_fif_x_drift).
    print(f"  Generating FIF×drift signals for {tokens}...")
    events = generate_signal_events(closes, threshold=threshold)
    for tok, evs in events.items():
        in_window = [
            ts for ts, _ in evs
            if bt.start <= ts <= bt.end
        ]
        print(f"    {tok}: {len(evs)} total events, {len(in_window)} in [{bt.start.date()}, {bt.end.date()}]")

    # 3. Build a unified hourly timeline and a per-token entry queue
    all_hours: set = set()
    for tok in tokens:
        c_in = closes[tok].loc[bt.start:bt.end]
        all_hours.update(c_in.index.tolist())
    all_hours_sorted = sorted(all_hours)

    # Convert events into per-token sorted queues of in-window timestamps
    pending: Dict[str, List[pd.Timestamp]] = {
        tok: sorted([ts for ts, _ in events[tok] if bt.start <= ts <= bt.end])
        for tok in tokens
    }
    next_idx: Dict[str, int] = {tok: 0 for tok in tokens}

    # Track scheduled exits: token -> exit_timestamp
    scheduled_exit: Dict[str, pd.Timestamp] = {}

    print(f"  Driving harness over {len(all_hours_sorted)} 1h bars...")

    for ts in all_hours_sorted:
        bt.set_time(ts)
        bt._update_positions(ts)

        # ── Time-stop exits ──
        for tok in list(scheduled_exit.keys()):
            if ts >= scheduled_exit[tok]:
                if tok in bt.positions:
                    bt.close(tok, reason="time_stop_72h")
                del scheduled_exit[tok]

        # ── New entries: any pending event with ts ≤ now ──
        for tok in tokens:
            q = pending[tok]
            ix = next_idx[tok]
            while ix < len(q) and q[ix] <= ts:
                signal_ts = q[ix]
                ix += 1
                # Skip if slot already occupied (no pyramiding)
                if tok in bt.positions:
                    continue
                # Place equal-weight long order at current bar close
                size_usd = bt.initial_capital * size_per_trade_frac
                pos = bt.order(
                    tok,
                    side="long",
                    size_usd=size_usd,
                    leverage=1.0,
                    reason="fif_drift_long",
                    metadata={"signal_ts": str(signal_ts), "z_threshold": threshold},
                )
                if pos is not None:
                    scheduled_exit[tok] = ts + pd.Timedelta(hours=hold_hours)
            next_idx[tok] = ix

    bt.close_all(reason="end_of_backtest")
