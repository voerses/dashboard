"""
MULTI-TIMEFRAME MOMENTUM STRATEGY BACKTEST
===========================================
Hypothesis: The edge may exist at different holding periods or entry frequencies.
All existing strategies used 1h bars. Test:
  - 4h rebalancing (fast signals)
  - 24h rebalancing (daily)
  - 168h rebalancing (weekly/swing)
  - 4h mean-reversion (panic buying)

Signal Sets:
  A (Fast 4h):    4h ret > 2% AND vol_ratio_4h > 2.0; 2xATR trail, 12h max
  B (Daily 24h):  24h rank > 0.8 AND 168h rank > 0.6;  3xATR trail, 72h max, exit rank<0.4
  C (Swing 168h): 168h ret > 10% AND 24h ret > 0 AND vol_ratio_168h > 1.5; 4xATR trail, 336h max
  D (MR short):   4h ret < -3% AND RSI_14 < 30 AND vol_ratio_4h > 2.0; fixed 4h or 1xATR trail, 8h max

Capital: $200K, slippage: 10bps, max 5 positions
OOS: 2025-07-01 to 2026-03-17
Leverage sweep: 1x, 3x, 5x, 7x
"""

import pandas as pd
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
import warnings
warnings.filterwarnings("ignore")

# ── CONFIG ────────────────────────────────────────────────────────────────────
DATA_DIR = Path("/workspace/crypto_backtest/data/perp/1h_cache")
OOS_START = pd.Timestamp("2025-07-01")
OOS_END = pd.Timestamp("2026-03-17 16:00:00")
INITIAL_CAPITAL = 200_000.0
SLIPPAGE_BPS = 10
MAX_POSITIONS = 5
LEVERAGE_LEVELS = [1, 3, 5, 7]

TOP20 = [
    "BTC", "ETH", "BNB", "DOGE", "XLM", "SOL", "ADA", "BCH", "TRX", "LINK",
    "AVAX", "ETC", "XRP", "ENJ", "SAND", "NEO", "KAVA", "CHZ", "XMR", "ATOM"
]


# ── DATA LOADING ──────────────────────────────────────────────────────────────
def load_all_tokens() -> Dict[str, pd.DataFrame]:
    """Load hourly data for all tokens."""
    data = {}
    for token in TOP20:
        fp = DATA_DIR / f"{token}_1h.parquet"
        if not fp.exists():
            print(f"  SKIP {token}: file not found")
            continue
        df = pd.read_parquet(fp)[["open", "high", "low", "close", "volume"]]
        df = df.sort_index()
        df = df[~df.index.duplicated(keep="first")]
        data[token] = df
    return data


def build_cross_sectional_ranks(all_data: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    """Compute cross-sectional return ranks across all tokens."""
    # Build aligned close panel
    close_dict = {t: df["close"] for t, df in all_data.items()}
    close_panel = pd.DataFrame(close_dict)

    # Returns at various horizons
    ret_24h = close_panel.pct_change(24)
    ret_168h = close_panel.pct_change(168)

    # Cross-sectional ranks
    rank_24h = ret_24h.rank(axis=1, pct=True)
    rank_168h = ret_168h.rank(axis=1, pct=True)

    return {"rank_24h": rank_24h, "rank_168h": rank_168h}


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI indicator."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build all features needed for signal generation."""
    d = df.copy()

    # Returns at various horizons
    d["ret_1h"] = d["close"].pct_change(1)
    d["ret_4h"] = d["close"].pct_change(4)
    d["ret_24h"] = d["close"].pct_change(24)
    d["ret_168h"] = d["close"].pct_change(168)

    # Volume ratios
    d["vol_ma_4h"] = d["volume"].rolling(4, min_periods=3).mean()
    d["vol_ma_24h"] = d["volume"].rolling(24, min_periods=18).mean()
    d["vol_ma_168h"] = d["volume"].rolling(168, min_periods=120).mean()

    # Volume ratio: current 4h avg vs 24h avg
    d["vol_ratio_4h"] = d["vol_ma_4h"] / d["vol_ma_24h"].replace(0, np.nan)
    # Volume ratio: current 168h avg vs longer-term average (720h)
    d["vol_ma_720h"] = d["volume"].rolling(720, min_periods=500).mean()
    d["vol_ratio_168h"] = d["vol_ma_168h"] / d["vol_ma_720h"].replace(0, np.nan)

    # ATR (14-period)
    tr = pd.DataFrame({
        "hl": d["high"] - d["low"],
        "hc": (d["high"] - d["close"].shift(1)).abs(),
        "lc": (d["low"] - d["close"].shift(1)).abs()
    }).max(axis=1)
    d["atr_14"] = tr.rolling(14, min_periods=10).mean()
    d["atr_pct"] = d["atr_14"] / d["close"]

    # RSI
    d["rsi_14"] = compute_rsi(d["close"], 14)

    return d


# ── POSITION / BACKTEST ENGINE ────────────────────────────────────────────────
@dataclass
class Position:
    token: str
    entry_time: pd.Timestamp
    entry_price: float
    direction: int  # 1 for long, -1 for short
    size_usd: float
    leverage: float
    atr_at_entry: float
    trail_multiplier: float
    max_hold_hours: int
    trailing_stop: float  # price level
    best_price: float  # for trailing stop tracking
    signal_set: str
    # For Signal D: fixed hold
    fixed_hold_hours: Optional[int] = None


@dataclass
class TradeResult:
    token: str
    signal_set: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    direction: int
    leverage: float
    size_usd: float
    pnl: float
    pnl_pct: float
    hold_hours: int
    exit_reason: str


def apply_slippage(price: float, direction: int, is_entry: bool) -> float:
    """Apply slippage: worse price on entry, worse price on exit."""
    slip = SLIPPAGE_BPS / 10000.0
    if is_entry:
        return price * (1 + slip) if direction == 1 else price * (1 - slip)
    else:
        return price * (1 - slip) if direction == 1 else price * (1 + slip)


def backtest_signal_set(
    signal_name: str,
    all_data: Dict[str, pd.DataFrame],
    signals: Dict[str, pd.Series],  # token -> boolean signal series
    leverage: float,
    trail_mult: float,
    max_hold: int,
    direction: int = 1,
    rebalance_freq: int = 1,  # check signals every N hours
    rank_exit_col: Optional[str] = None,  # for signal B: rank column to monitor
    rank_exit_threshold: Optional[float] = None,
    fixed_hold: Optional[int] = None,  # for signal D
    ranks_data: Optional[Dict[str, pd.DataFrame]] = None,
) -> List[TradeResult]:
    """
    Event-driven backtest for a signal set.
    Simulates position management with trailing stops, max hold, rank exits.
    """
    # Collect all signal timestamps across tokens
    all_signal_events = []
    for token, sig in signals.items():
        fired = sig[sig].index.tolist()
        for ts in fired:
            all_signal_events.append((ts, token))

    all_signal_events.sort(key=lambda x: x[0])

    # Build hourly timeline for position management
    # Get the full OOS hourly index
    sample_token = list(all_data.keys())[0]
    oos_index = all_data[sample_token].loc[OOS_START:OOS_END].index

    trades: List[TradeResult] = []
    positions: List[Position] = []
    equity = INITIAL_CAPITAL
    peak_equity = INITIAL_CAPITAL

    # Create lookup for next signal events
    signal_set = set()
    for ts, token in all_signal_events:
        if OOS_START <= ts <= OOS_END:
            signal_set.add((ts, token))

    # Iterate hour by hour
    for i, ts in enumerate(oos_index):
        # 1. Update existing positions (check exits)
        still_open = []
        for pos in positions:
            if ts not in all_data[pos.token].index:
                still_open.append(pos)
                continue

            row = all_data[pos.token].loc[ts]
            current_price = row["close"]
            current_high = row["high"]
            current_low = row["low"]
            hours_held = int((ts - pos.entry_time).total_seconds() / 3600)

            exit_reason = None
            exit_price = current_price

            # Check fixed hold (Signal D)
            if pos.fixed_hold_hours is not None and hours_held >= pos.fixed_hold_hours:
                exit_reason = "fixed_hold"
                exit_price = current_price

            # Check max hold
            if exit_reason is None and hours_held >= pos.max_hold_hours:
                exit_reason = "max_hold"
                exit_price = current_price

            # Check trailing stop
            if exit_reason is None:
                if pos.direction == 1:
                    # Long: update best price, check if low hit stop
                    pos.best_price = max(pos.best_price, current_high)
                    new_stop = pos.best_price - pos.trail_multiplier * pos.atr_at_entry
                    pos.trailing_stop = max(pos.trailing_stop, new_stop)
                    if current_low <= pos.trailing_stop:
                        exit_reason = "trailing_stop"
                        exit_price = pos.trailing_stop  # assume fill at stop level
                else:
                    pos.best_price = min(pos.best_price, current_low)
                    new_stop = pos.best_price + pos.trail_multiplier * pos.atr_at_entry
                    pos.trailing_stop = min(pos.trailing_stop, new_stop)
                    if current_high >= pos.trailing_stop:
                        exit_reason = "trailing_stop"
                        exit_price = pos.trailing_stop

            # Check rank exit (Signal B only)
            if exit_reason is None and rank_exit_col is not None and ranks_data is not None:
                rank_df = ranks_data.get(rank_exit_col)
                if rank_df is not None and ts in rank_df.index and pos.token in rank_df.columns:
                    current_rank = rank_df.loc[ts, pos.token]
                    if not np.isnan(current_rank) and current_rank < rank_exit_threshold:
                        exit_reason = "rank_drop"
                        exit_price = current_price

            if exit_reason is not None:
                exit_price_slipped = apply_slippage(exit_price, pos.direction, is_entry=False)
                raw_return = (exit_price_slipped - pos.entry_price) / pos.entry_price * pos.direction
                leveraged_return = raw_return * pos.leverage
                pnl = pos.size_usd * leveraged_return
                equity += pnl

                trades.append(TradeResult(
                    token=pos.token,
                    signal_set=pos.signal_set,
                    entry_time=pos.entry_time,
                    exit_time=ts,
                    entry_price=pos.entry_price,
                    exit_price=exit_price_slipped,
                    direction=pos.direction,
                    leverage=pos.leverage,
                    size_usd=pos.size_usd,
                    pnl=pnl,
                    pnl_pct=leveraged_return * 100,
                    hold_hours=hours_held,
                    exit_reason=exit_reason,
                ))
            else:
                still_open.append(pos)

        positions = still_open

        # 2. Check for new entries (only at rebalance frequency)
        if i % rebalance_freq != 0:
            continue

        if len(positions) >= MAX_POSITIONS:
            continue

        # Find signals firing at this timestamp
        for token in TOP20:
            if len(positions) >= MAX_POSITIONS:
                break
            if (ts, token) not in signal_set:
                continue
            # Don't double up on same token
            if any(p.token == token for p in positions):
                continue
            if token not in all_data:
                continue
            if ts not in all_data[token].index:
                continue

            row = all_data[token].loc[ts]
            entry_price = apply_slippage(row["close"], direction, is_entry=True)
            atr_val = row.get("atr_14", row["close"] * 0.02)  # fallback 2% ATR
            if np.isnan(atr_val) or atr_val <= 0:
                atr_val = row["close"] * 0.02

            size_usd = equity / MAX_POSITIONS  # equal weight
            if size_usd <= 0:
                continue

            if direction == 1:
                initial_stop = entry_price - trail_mult * atr_val
            else:
                initial_stop = entry_price + trail_mult * atr_val

            pos = Position(
                token=token,
                entry_time=ts,
                entry_price=entry_price,
                direction=direction,
                size_usd=size_usd,
                leverage=leverage,
                atr_at_entry=atr_val,
                trail_multiplier=trail_mult,
                max_hold_hours=max_hold,
                trailing_stop=initial_stop,
                best_price=entry_price,
                signal_set=signal_name,
                fixed_hold_hours=fixed_hold,
            )
            positions.append(pos)

    # Close remaining positions at last price
    for pos in positions:
        last_ts = oos_index[-1]
        if last_ts in all_data[pos.token].index:
            exit_price = apply_slippage(
                all_data[pos.token].loc[last_ts, "close"],
                pos.direction, is_entry=False
            )
        else:
            exit_price = pos.entry_price  # flat if no data
        hours_held = int((last_ts - pos.entry_time).total_seconds() / 3600)
        raw_return = (exit_price - pos.entry_price) / pos.entry_price * pos.direction
        leveraged_return = raw_return * pos.leverage
        pnl = pos.size_usd * leveraged_return
        equity += pnl
        trades.append(TradeResult(
            token=pos.token,
            signal_set=pos.signal_set,
            entry_time=pos.entry_time,
            exit_time=last_ts,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            direction=pos.direction,
            leverage=pos.leverage,
            size_usd=pos.size_usd,
            pnl=pnl,
            pnl_pct=leveraged_return * 100,
            hold_hours=hours_held,
            exit_reason="end_of_period",
        ))

    return trades


def compute_equity_curve(trades: List[TradeResult]) -> Tuple[pd.Series, float, float, float, float]:
    """Compute equity curve from trade list and return key metrics."""
    if not trades:
        return pd.Series(dtype=float), 0.0, 0.0, 0.0, 0.0

    # Sort trades by exit time
    trades_sorted = sorted(trades, key=lambda t: t.exit_time)

    # Build equity curve by accumulating PnL
    equity = INITIAL_CAPITAL
    equity_points = [(OOS_START, equity)]

    for t in trades_sorted:
        equity += t.pnl
        equity_points.append((t.exit_time, equity))

    eq_series = pd.Series(
        [e[1] for e in equity_points],
        index=pd.DatetimeIndex([e[0] for e in equity_points])
    )
    # Remove duplicate indices, keep last
    eq_series = eq_series[~eq_series.index.duplicated(keep="last")]

    final_equity = equity
    total_return = (final_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100

    # Max drawdown
    running_max = eq_series.cummax()
    drawdown = (eq_series - running_max) / running_max * 100
    max_dd = drawdown.min()

    # Annualized return
    days = (eq_series.index[-1] - eq_series.index[0]).total_seconds() / 86400
    if days > 0:
        annual_return = ((final_equity / INITIAL_CAPITAL) ** (365.25 / days) - 1) * 100
    else:
        annual_return = 0.0

    return eq_series, total_return, max_dd, annual_return, final_equity


def per_month_breakdown(trades: List[TradeResult]) -> pd.DataFrame:
    """Monthly PnL breakdown."""
    if not trades:
        return pd.DataFrame()

    records = []
    for t in trades:
        records.append({
            "month": t.exit_time.to_period("M"),
            "pnl": t.pnl,
            "pnl_pct": t.pnl_pct,
            "token": t.token,
            "hold_hours": t.hold_hours,
            "exit_reason": t.exit_reason,
        })
    df = pd.DataFrame(records)

    monthly = df.groupby("month").agg(
        trades=("pnl", "count"),
        total_pnl=("pnl", "sum"),
        avg_pnl_pct=("pnl_pct", "mean"),
        win_rate=("pnl", lambda x: (x > 0).mean() * 100),
        avg_hold=("hold_hours", "mean"),
    ).round(2)

    return monthly


# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 100)
    print("MULTI-TIMEFRAME MOMENTUM STRATEGY BACKTEST")
    print("=" * 100)
    print(f"Capital: ${INITIAL_CAPITAL:,.0f} | Slippage: {SLIPPAGE_BPS}bps | Max positions: {MAX_POSITIONS}")
    print(f"OOS period: {OOS_START.date()} to {OOS_END.date()}")
    print(f"Leverage levels: {LEVERAGE_LEVELS}")

    # ── STEP 1: Load Data ─────────────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("STEP 1: LOADING DATA")
    print("=" * 100)

    all_data = load_all_tokens()
    print(f"Loaded {len(all_data)} tokens")

    # Build features for each token
    print("\nBuilding features...")
    for token in list(all_data.keys()):
        all_data[token] = build_features(all_data[token])
        oos_rows = all_data[token].loc[OOS_START:OOS_END]
        print(f"  {token}: {len(oos_rows)} OOS rows")

    # Build cross-sectional ranks
    print("\nBuilding cross-sectional ranks...")
    ranks = build_cross_sectional_ranks(all_data)
    print(f"  rank_24h shape: {ranks['rank_24h'].shape}")
    print(f"  rank_168h shape: {ranks['rank_168h'].shape}")

    # ── STEP 2: Generate Signals ──────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("STEP 2: GENERATING SIGNALS")
    print("=" * 100)

    # Signal A: Fast (4h rebal)
    # Entry: 4h return > 2% AND volume_ratio_4h > 2.0
    sig_a = {}
    for token, df in all_data.items():
        oos = df.loc[OOS_START:OOS_END]
        mask = (oos["ret_4h"] > 0.02) & (oos["vol_ratio_4h"] > 2.0)
        sig_a[token] = mask
        fired = mask.sum()
        if fired > 0:
            print(f"  Signal A [{token}]: {fired} fires")

    total_a = sum(s.sum() for s in sig_a.values())
    print(f"  Signal A total: {total_a} fires")

    # Signal B: Daily (24h rebal)
    # Entry: 24h rank > 0.8 AND 168h rank > 0.6
    sig_b = {}
    for token, df in all_data.items():
        oos = df.loc[OOS_START:OOS_END]
        r24 = ranks["rank_24h"].loc[OOS_START:OOS_END]
        r168 = ranks["rank_168h"].loc[OOS_START:OOS_END]
        if token in r24.columns and token in r168.columns:
            # Align indices
            common_idx = oos.index.intersection(r24.index).intersection(r168.index)
            mask = pd.Series(False, index=oos.index)
            valid = (r24.loc[common_idx, token] > 0.8) & (r168.loc[common_idx, token] > 0.6)
            mask.loc[common_idx] = valid
            sig_b[token] = mask
            fired = mask.sum()
            if fired > 0:
                print(f"  Signal B [{token}]: {fired} fires")
        else:
            sig_b[token] = pd.Series(False, index=oos.index)

    total_b = sum(s.sum() for s in sig_b.values())
    print(f"  Signal B total: {total_b} fires")

    # Signal C: Swing (weekly)
    # Entry: 168h return > 10% AND 24h return > 0 AND volume_ratio_168h > 1.5
    sig_c = {}
    for token, df in all_data.items():
        oos = df.loc[OOS_START:OOS_END]
        mask = (
            (oos["ret_168h"] > 0.10) &
            (oos["ret_24h"] > 0) &
            (oos["vol_ratio_168h"] > 1.5)
        )
        sig_c[token] = mask
        fired = mask.sum()
        if fired > 0:
            print(f"  Signal C [{token}]: {fired} fires")

    total_c = sum(s.sum() for s in sig_c.values())
    print(f"  Signal C total: {total_c} fires")

    # Signal D: Mean Reversion (short timeframe)
    # Entry: 4h return < -3% AND RSI_14 < 30 AND volume_ratio_4h > 2.0
    sig_d = {}
    for token, df in all_data.items():
        oos = df.loc[OOS_START:OOS_END]
        mask = (
            (oos["ret_4h"] < -0.03) &
            (oos["rsi_14"] < 30) &
            (oos["vol_ratio_4h"] > 2.0)
        )
        sig_d[token] = mask
        fired = mask.sum()
        if fired > 0:
            print(f"  Signal D [{token}]: {fired} fires")

    total_d = sum(s.sum() for s in sig_d.values())
    print(f"  Signal D total: {total_d} fires")

    # ── STEP 3: Backtest Each Signal Set at Each Leverage ─────────────────────
    print("\n" + "=" * 100)
    print("STEP 3: BACKTESTING ALL SIGNAL SETS x LEVERAGE LEVELS")
    print("=" * 100)

    signal_configs = [
        {
            "name": "A_Fast_4h",
            "signals": sig_a,
            "trail_mult": 2.0,
            "max_hold": 12,
            "direction": 1,
            "rebalance_freq": 4,
            "rank_exit_col": None,
            "rank_exit_threshold": None,
            "fixed_hold": None,
        },
        {
            "name": "B_Daily_24h",
            "signals": sig_b,
            "trail_mult": 3.0,
            "max_hold": 72,
            "direction": 1,
            "rebalance_freq": 24,
            "rank_exit_col": "rank_24h",
            "rank_exit_threshold": 0.4,
            "fixed_hold": None,
        },
        {
            "name": "C_Swing_168h",
            "signals": sig_c,
            "trail_mult": 4.0,
            "max_hold": 336,
            "direction": 1,
            "rebalance_freq": 24,  # check daily even though signals are weekly-ish
            "rank_exit_col": None,
            "rank_exit_threshold": None,
            "fixed_hold": None,
        },
        {
            "name": "D_MeanRev_4h",
            "signals": sig_d,
            "trail_mult": 1.0,
            "max_hold": 8,
            "direction": 1,  # LONG (buying the panic)
            "rebalance_freq": 4,
            "rank_exit_col": None,
            "rank_exit_threshold": None,
            "fixed_hold": 4,
        },
    ]

    all_results = {}

    for cfg in signal_configs:
        print(f"\n{'─' * 80}")
        print(f"SIGNAL SET: {cfg['name']}")
        print(f"{'─' * 80}")
        print(f"  Trail: {cfg['trail_mult']}x ATR | Max hold: {cfg['max_hold']}h | "
              f"Rebal freq: {cfg['rebalance_freq']}h | Direction: {'LONG' if cfg['direction']==1 else 'SHORT'}")
        if cfg['fixed_hold']:
            print(f"  Fixed hold: {cfg['fixed_hold']}h")
        if cfg['rank_exit_col']:
            print(f"  Rank exit: {cfg['rank_exit_col']} < {cfg['rank_exit_threshold']}")

        for lev in LEVERAGE_LEVELS:
            trades = backtest_signal_set(
                signal_name=cfg["name"],
                all_data=all_data,
                signals=cfg["signals"],
                leverage=lev,
                trail_mult=cfg["trail_mult"],
                max_hold=cfg["max_hold"],
                direction=cfg["direction"],
                rebalance_freq=cfg["rebalance_freq"],
                rank_exit_col=cfg.get("rank_exit_col"),
                rank_exit_threshold=cfg.get("rank_exit_threshold"),
                fixed_hold=cfg.get("fixed_hold"),
                ranks_data=ranks if cfg.get("rank_exit_col") else None,
            )

            eq_curve, total_ret, max_dd, annual_ret, final_eq = compute_equity_curve(trades)

            key = f"{cfg['name']}_lev{lev}"
            all_results[key] = {
                "signal": cfg["name"],
                "leverage": lev,
                "trades": trades,
                "total_return": total_ret,
                "max_dd": max_dd,
                "annual_return": annual_ret,
                "final_equity": final_eq,
                "n_trades": len(trades),
                "equity_curve": eq_curve,
            }

            if len(trades) > 0:
                win_rate = sum(1 for t in trades if t.pnl > 0) / len(trades) * 100
                avg_pnl = np.mean([t.pnl_pct for t in trades])
                avg_hold = np.mean([t.hold_hours for t in trades])
                print(f"  Lev {lev}x: {len(trades)} trades | "
                      f"Return: {total_ret:+.1f}% | Annual: {annual_ret:+.1f}% | "
                      f"MaxDD: {max_dd:.1f}% | WR: {win_rate:.1f}% | "
                      f"AvgPnL: {avg_pnl:+.2f}% | AvgHold: {avg_hold:.1f}h | "
                      f"Final: ${final_eq:,.0f}")
            else:
                print(f"  Lev {lev}x: NO TRADES")

    # ── STEP 4: Summary Table ─────────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("STEP 4: SUMMARY TABLE — ALL SIGNAL SETS x LEVERAGE")
    print("=" * 100)

    header = (f"{'Signal':<20} {'Lev':>4} {'Trades':>7} {'TotalRet':>10} {'AnnualRet':>10} "
              f"{'MaxDD':>8} {'WinRate':>8} {'AvgPnL%':>9} {'AvgHold':>8} {'FinalEq':>12}")
    print(header)
    print("-" * len(header))

    for key, res in sorted(all_results.items()):
        trades = res["trades"]
        if len(trades) > 0:
            win_rate = sum(1 for t in trades if t.pnl > 0) / len(trades) * 100
            avg_pnl = np.mean([t.pnl_pct for t in trades])
            avg_hold = np.mean([t.hold_hours for t in trades])
        else:
            win_rate = avg_pnl = avg_hold = 0.0

        print(f"{res['signal']:<20} {res['leverage']:>4}x {res['n_trades']:>7} "
              f"{res['total_return']:>+9.1f}% {res['annual_return']:>+9.1f}% "
              f"{res['max_dd']:>+7.1f}% {win_rate:>7.1f}% {avg_pnl:>+8.2f}% "
              f"{avg_hold:>7.1f}h ${res['final_equity']:>11,.0f}")

    # ── STEP 5: Per-Month Breakdown for Best Configs ──────────────────────────
    print("\n" + "=" * 100)
    print("STEP 5: PER-MONTH BREAKDOWN — ALL SIGNAL SETS (at each leverage)")
    print("=" * 100)

    for cfg_name in ["A_Fast_4h", "B_Daily_24h", "C_Swing_168h", "D_MeanRev_4h"]:
        for lev in LEVERAGE_LEVELS:
            key = f"{cfg_name}_lev{lev}"
            res = all_results.get(key)
            if res is None or len(res["trades"]) == 0:
                continue

            print(f"\n{'─' * 80}")
            print(f"{cfg_name} @ {lev}x leverage")
            print(f"{'─' * 80}")

            monthly = per_month_breakdown(res["trades"])
            if monthly.empty:
                print("  No monthly data.")
                continue

            # Compute running equity per month
            equity = INITIAL_CAPITAL
            print(f"  {'Month':<10} {'Trades':>7} {'PnL':>12} {'AvgPnL%':>10} "
                  f"{'WinRate':>8} {'AvgHold':>8} {'RunEquity':>14}")
            print(f"  {'-'*75}")

            for month, row in monthly.iterrows():
                equity += row["total_pnl"]
                print(f"  {str(month):<10} {int(row['trades']):>7} "
                      f"${row['total_pnl']:>+11,.0f} {row['avg_pnl_pct']:>+9.2f}% "
                      f"{row['win_rate']:>7.1f}% {row['avg_hold']:>7.1f}h "
                      f"${equity:>13,.0f}")

    # ── STEP 6: Trade Distribution Analysis ───────────────────────────────────
    print("\n" + "=" * 100)
    print("STEP 6: TRADE DISTRIBUTION ANALYSIS")
    print("=" * 100)

    for cfg_name in ["A_Fast_4h", "B_Daily_24h", "C_Swing_168h", "D_MeanRev_4h"]:
        # Use 3x leverage as reference
        key = f"{cfg_name}_lev3"
        res = all_results.get(key)
        if res is None or len(res["trades"]) == 0:
            print(f"\n{cfg_name}: No trades at 3x")
            continue

        trades = res["trades"]
        pnls = [t.pnl_pct for t in trades]
        holds = [t.hold_hours for t in trades]

        print(f"\n{'─' * 60}")
        print(f"{cfg_name} @ 3x leverage — {len(trades)} trades")
        print(f"{'─' * 60}")

        # PnL distribution
        pnl_arr = np.array(pnls)
        print(f"  PnL%: mean={np.mean(pnl_arr):+.2f}, median={np.median(pnl_arr):+.2f}, "
              f"std={np.std(pnl_arr):.2f}")
        print(f"  PnL%: min={np.min(pnl_arr):+.2f}, max={np.max(pnl_arr):+.2f}")
        print(f"  PnL% percentiles: 5th={np.percentile(pnl_arr, 5):+.2f}, "
              f"25th={np.percentile(pnl_arr, 25):+.2f}, "
              f"75th={np.percentile(pnl_arr, 75):+.2f}, "
              f"95th={np.percentile(pnl_arr, 95):+.2f}")

        # Hold distribution
        hold_arr = np.array(holds)
        print(f"  Hold: mean={np.mean(hold_arr):.1f}h, median={np.median(hold_arr):.1f}h, "
              f"min={np.min(hold_arr)}h, max={np.max(hold_arr)}h")

        # Exit reason distribution
        reasons = {}
        for t in trades:
            reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
        print(f"  Exit reasons: {reasons}")

        # Per-token breakdown
        token_pnl = {}
        for t in trades:
            if t.token not in token_pnl:
                token_pnl[t.token] = []
            token_pnl[t.token].append(t.pnl_pct)

        print(f"  Per-token (count, avgPnL%):")
        for token in sorted(token_pnl.keys()):
            vals = token_pnl[token]
            print(f"    {token:<8} {len(vals):>4} trades, avg PnL: {np.mean(vals):+.2f}%")

    # ── STEP 7: Best Configurations ──────────────────────────────────────────
    print("\n" + "=" * 100)
    print("STEP 7: RANKING — BEST CONFIGURATIONS")
    print("=" * 100)

    # Sort by annual return
    ranked = sorted(all_results.items(), key=lambda x: x[1]["annual_return"], reverse=True)

    print(f"\n{'Rank':<6} {'Config':<25} {'AnnualRet':>10} {'MaxDD':>8} {'Trades':>7} {'Ret/DD':>8}")
    print("-" * 70)

    for i, (key, res) in enumerate(ranked[:20], 1):
        ret_dd = abs(res["annual_return"] / res["max_dd"]) if res["max_dd"] != 0 else 0
        print(f"{i:<6} {key:<25} {res['annual_return']:>+9.1f}% {res['max_dd']:>+7.1f}% "
              f"{res['n_trades']:>7} {ret_dd:>7.2f}")

    # Target check
    print("\n" + "=" * 100)
    print("TARGET CHECK: 300%+ annual, <20% DD")
    print("=" * 100)

    hits = []
    for key, res in all_results.items():
        if res["annual_return"] >= 300 and res["max_dd"] > -20:
            hits.append((key, res))

    if hits:
        print(f"\nConfigurations meeting target ({len(hits)}):")
        for key, res in sorted(hits, key=lambda x: x[1]["annual_return"], reverse=True):
            print(f"  {key}: Annual {res['annual_return']:+.1f}%, DD {res['max_dd']:.1f}%, "
                  f"{res['n_trades']} trades, Final ${res['final_equity']:,.0f}")
    else:
        print("\nNo configurations meet the strict target.")
        print("Closest candidates:")
        for i, (key, res) in enumerate(ranked[:5], 1):
            print(f"  {i}. {key}: Annual {res['annual_return']:+.1f}%, DD {res['max_dd']:.1f}%, "
                  f"{res['n_trades']} trades")

    # ── STEP 8: Correlation Between Signal Sets ───────────────────────────────
    print("\n" + "=" * 100)
    print("STEP 8: SIGNAL OVERLAP / CORRELATION")
    print("=" * 100)

    # Check overlap at 3x
    for s1 in ["A_Fast_4h", "B_Daily_24h", "C_Swing_168h", "D_MeanRev_4h"]:
        for s2 in ["A_Fast_4h", "B_Daily_24h", "C_Swing_168h", "D_MeanRev_4h"]:
            if s1 >= s2:
                continue
            k1 = f"{s1}_lev3"
            k2 = f"{s2}_lev3"
            r1 = all_results.get(k1, {})
            r2 = all_results.get(k2, {})
            t1 = set((t.token, t.entry_time) for t in r1.get("trades", []))
            t2 = set((t.token, t.entry_time) for t in r2.get("trades", []))
            overlap = len(t1 & t2)
            total = len(t1 | t2) if len(t1 | t2) > 0 else 1
            print(f"  {s1} vs {s2}: overlap {overlap}/{total} ({overlap/total*100:.1f}%)")

    # ── STEP 9: Combined Portfolio (equal weight across signal sets) ──────────
    print("\n" + "=" * 100)
    print("STEP 9: COMBINED PORTFOLIO — ALL SIGNALS AT 3x")
    print("=" * 100)

    combined_trades = []
    for cfg_name in ["A_Fast_4h", "B_Daily_24h", "C_Swing_168h", "D_MeanRev_4h"]:
        key = f"{cfg_name}_lev3"
        if key in all_results:
            combined_trades.extend(all_results[key]["trades"])

    if combined_trades:
        # Sort by exit time and compute combined equity
        combined_trades.sort(key=lambda t: t.exit_time)
        equity = INITIAL_CAPITAL
        peak = INITIAL_CAPITAL
        max_dd_abs = 0.0
        eq_points = [(OOS_START, equity)]

        for t in combined_trades:
            equity += t.pnl
            eq_points.append((t.exit_time, equity))
            peak = max(peak, equity)
            dd = (equity - peak) / peak * 100
            max_dd_abs = min(max_dd_abs, dd)

        final_eq = equity
        total_ret = (final_eq - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
        days = (combined_trades[-1].exit_time - OOS_START).total_seconds() / 86400
        annual_ret = ((final_eq / INITIAL_CAPITAL) ** (365.25 / days) - 1) * 100 if days > 0 else 0

        n_wins = sum(1 for t in combined_trades if t.pnl > 0)
        win_rate = n_wins / len(combined_trades) * 100

        print(f"  Total trades: {len(combined_trades)}")
        print(f"  Win rate: {win_rate:.1f}%")
        print(f"  Total return: {total_ret:+.1f}%")
        print(f"  Annualized return: {annual_ret:+.1f}%")
        print(f"  Max drawdown: {max_dd_abs:.1f}%")
        print(f"  Final equity: ${final_eq:,.0f}")

        # Per-signal contribution
        print(f"\n  Per-signal contribution (3x):")
        for cfg_name in ["A_Fast_4h", "B_Daily_24h", "C_Swing_168h", "D_MeanRev_4h"]:
            sig_trades = [t for t in combined_trades if t.signal_set == cfg_name]
            if sig_trades:
                sig_pnl = sum(t.pnl for t in sig_trades)
                sig_wr = sum(1 for t in sig_trades if t.pnl > 0) / len(sig_trades) * 100
                print(f"    {cfg_name}: {len(sig_trades)} trades, "
                      f"PnL ${sig_pnl:+,.0f}, WR {sig_wr:.1f}%")

        # Monthly breakdown of combined
        print(f"\n  Combined monthly breakdown:")
        combined_monthly = per_month_breakdown(combined_trades)
        if not combined_monthly.empty:
            equity = INITIAL_CAPITAL
            print(f"  {'Month':<10} {'Trades':>7} {'PnL':>12} {'AvgPnL%':>10} "
                  f"{'WinRate':>8} {'RunEquity':>14}")
            print(f"  {'-'*65}")
            for month, row in combined_monthly.iterrows():
                equity += row["total_pnl"]
                print(f"  {str(month):<10} {int(row['trades']):>7} "
                      f"${row['total_pnl']:>+11,.0f} {row['avg_pnl_pct']:>+9.2f}% "
                      f"{row['win_rate']:>7.1f}% ${equity:>13,.0f}")

    print("\n" + "=" * 100)
    print("BACKTEST COMPLETE")
    print("=" * 100)
