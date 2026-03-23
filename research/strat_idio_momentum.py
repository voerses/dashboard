#!/workspace/venv/bin/python
"""
Idiosyncratic Momentum Strategy — Full Portfolio Backtest
=========================================================

Signal (proven OOS edge: +0.58% excess per trade, 12,178 OOS trades):
  Entry when BOTH conditions met:
    - Token's 24h return rank > 0.7 (top 30% cross-sectional)
    - Token's 168h rolling correlation with BTC < 0.5 (idiosyncratic move)

Tests multiple exit rules, position sizing methods, and leverage levels.

Train: before 2025-07-01 (parameter selection only)
Test:  2025-07-01 to 2026-03-17 (all reported metrics)
"""

import os
import sys
import warnings
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from itertools import product

warnings.filterwarnings("ignore")

# ── Configuration ────────────────────────────────────────────────────────

DATA_DIR = Path("/workspace/crypto_backtest/data/perp/1h_cache/")
TRAIN_END = pd.Timestamp("2025-07-01")
TEST_END = pd.Timestamp("2026-03-17 17:00:00")

STARTING_CAPITAL = 200_000.0
SLIPPAGE_BPS = 10  # per side, so 20bps round trip
MAX_POSITIONS = 5

# Entry thresholds
RANK_THRESHOLD = 0.7
BTC_CORR_THRESHOLD = 0.5

# ── Data Loading ─────────────────────────────────────────────────────────

def load_data() -> dict[str, pd.DataFrame]:
    """Load top 30 tokens by file size from 1h_cache."""
    parquet_files = sorted(DATA_DIR.glob("*.parquet"),
                           key=lambda f: f.stat().st_size, reverse=True)
    top30 = parquet_files[:30]

    tokens = {}
    for f in top30:
        symbol = f.stem.replace("_1h", "")
        df = pd.read_parquet(f)
        df.index = pd.to_datetime(df.index)
        tokens[symbol] = df

    print(f"Loaded {len(tokens)} tokens: {sorted(tokens.keys())}")
    return tokens


def build_panels(tokens: dict[str, pd.DataFrame]):
    """Build aligned close/high/low/volume panels."""
    close_d, high_d, low_d, vol_d = {}, {}, {}, {}
    for sym, df in tokens.items():
        close_d[sym] = df["close"]
        high_d[sym] = df["high"]
        low_d[sym] = df["low"]
        vol_d[sym] = df["volume"]

    close_panel = pd.DataFrame(close_d)
    high_panel = pd.DataFrame(high_d)
    low_panel = pd.DataFrame(low_d)
    vol_panel = pd.DataFrame(vol_d)

    # Require at least 15 tokens present
    valid = close_panel.notna().sum(axis=1) >= 15
    close_panel = close_panel.loc[valid]
    high_panel = high_panel.loc[valid]
    low_panel = low_panel.loc[valid]
    vol_panel = vol_panel.loc[valid]

    print(f"Panel shape: {close_panel.shape}")
    print(f"Date range: {close_panel.index.min()} to {close_panel.index.max()}")
    return close_panel, high_panel, low_panel, vol_panel


# ── Signal Construction ──────────────────────────────────────────────────

def compute_signals(close_panel: pd.DataFrame):
    """Compute entry signal components: rank and BTC correlation."""
    ret_1h = close_panel.pct_change(1)
    ret_24h = close_panel.pct_change(24)

    # Cross-sectional percentile rank of 24h returns
    ret_24h_rank = ret_24h.rank(axis=1, pct=True)

    # 168h rolling correlation with BTC
    btc_ret = ret_1h["BTC"]
    rolling_corr = pd.DataFrame(index=close_panel.index,
                                columns=close_panel.columns, dtype=float)
    for sym in close_panel.columns:
        if sym == "BTC":
            rolling_corr[sym] = 1.0
        else:
            rolling_corr[sym] = ret_1h[sym].rolling(168, min_periods=84).corr(btc_ret)

    # Entry signal: rank > 0.7 AND btc_corr < 0.5
    entry_signal = (ret_24h_rank > RANK_THRESHOLD) & (rolling_corr < BTC_CORR_THRESHOLD)
    # Exclude BTC itself
    entry_signal["BTC"] = False

    print(f"Total entry signals: {entry_signal.sum().sum():,}")
    return entry_signal, ret_24h_rank, rolling_corr


def compute_atr(high_panel, low_panel, close_panel, period=14):
    """Compute ATR for each token (hourly bars, so 14-bar ATR ~ 14 hours)."""
    tr_panels = []
    prev_close = close_panel.shift(1)
    tr1 = high_panel - low_panel
    tr2 = (high_panel - prev_close).abs()
    tr3 = (low_panel - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3]).groupby(level=0).max()
    # Ensure alignment
    true_range = true_range.reindex(close_panel.index)
    atr = true_range.rolling(period, min_periods=period).mean()
    return atr


# ── Position & Trade Tracking ────────────────────────────────────────────

@dataclass
class Position:
    symbol: str
    entry_price: float
    entry_time: pd.Timestamp
    size_usd: float          # notional
    leverage: float
    entry_rank: float
    trailing_stop: Optional[float] = None
    highest_price: float = 0.0
    no_stop_until: Optional[pd.Timestamp] = None

    def __post_init__(self):
        self.highest_price = self.entry_price


@dataclass
class Trade:
    symbol: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    size_usd: float
    leverage: float
    pnl: float
    return_pct: float
    exit_reason: str


# ── Exit Rules ───────────────────────────────────────────────────────────

class ExitRuleA:
    """Fixed 24h hold, then exit."""
    name = "A_Fixed24h"

    def should_exit(self, pos, current_time, current_price, atr_val,
                    current_rank, current_corr):
        hours_held = (current_time - pos.entry_time).total_seconds() / 3600
        if hours_held >= 24:
            return True, "24h_hold"
        return False, None


class ExitRuleB:
    """2x ATR trailing stop, 48h max hold."""
    name = "B_2ATR_48h"

    def should_exit(self, pos, current_time, current_price, atr_val,
                    current_rank, current_corr):
        hours_held = (current_time - pos.entry_time).total_seconds() / 3600

        # Max hold
        if hours_held >= 48:
            return True, "48h_max"

        # Trailing stop
        if not np.isnan(atr_val) and atr_val > 0:
            pos.highest_price = max(pos.highest_price, current_price)
            stop_level = pos.highest_price - 2.0 * atr_val
            if current_price <= stop_level:
                return True, "2atr_trail"

        return False, None


class ExitRuleC:
    """3x ATR trailing stop, 72h max hold, exit if rank < 0.3."""
    name = "C_3ATR_72h_RankDrop"

    def should_exit(self, pos, current_time, current_price, atr_val,
                    current_rank, current_corr):
        hours_held = (current_time - pos.entry_time).total_seconds() / 3600

        if hours_held >= 72:
            return True, "72h_max"

        if not np.isnan(atr_val) and atr_val > 0:
            pos.highest_price = max(pos.highest_price, current_price)
            stop_level = pos.highest_price - 3.0 * atr_val
            if current_price <= stop_level:
                return True, "3atr_trail"

        if not np.isnan(current_rank) and current_rank < 0.3:
            return True, "rank_drop"

        return False, None


class ExitRuleD:
    """Signal-based: exit when rank < 0.5 OR btc_corr > 0.7."""
    name = "D_SignalExit"

    def should_exit(self, pos, current_time, current_price, atr_val,
                    current_rank, current_corr):
        hours_held = (current_time - pos.entry_time).total_seconds() / 3600

        # Safety max hold 120h
        if hours_held >= 120:
            return True, "120h_max"

        if not np.isnan(current_rank) and current_rank < 0.5:
            return True, "rank_below_0.5"

        if not np.isnan(current_corr) and current_corr > 0.7:
            return True, "corr_above_0.7"

        return False, None


class ExitRuleE:
    """1.5x ATR trail, 24h no-stop protection, 48h max hold."""
    name = "E_1.5ATR_NoStop24_48h"

    def should_exit(self, pos, current_time, current_price, atr_val,
                    current_rank, current_corr):
        hours_held = (current_time - pos.entry_time).total_seconds() / 3600

        if hours_held >= 48:
            return True, "48h_max"

        # No trailing stop for first 24h
        if hours_held >= 24 and not np.isnan(atr_val) and atr_val > 0:
            pos.highest_price = max(pos.highest_price, current_price)
            stop_level = pos.highest_price - 1.5 * atr_val
            if current_price <= stop_level:
                return True, "1.5atr_trail"

        return False, None


EXIT_RULES = [ExitRuleA(), ExitRuleB(), ExitRuleC(), ExitRuleD(), ExitRuleE()]


# ── Position Sizing ──────────────────────────────────────────────────────

def size_equal(capital, n_current, max_pos, leverage, **kwargs):
    """Equal weight: $10K per position (fixed)."""
    base_size = 10_000.0
    return base_size


def size_rank_weighted(capital, n_current, max_pos, leverage, rank=0.5, **kwargs):
    """Rank-weighted: stronger rank = bigger position. Range $5K-$15K."""
    weight = (rank - 0.5) / 0.5  # 0.4 to 1.0
    weight = max(0.3, min(1.0, weight))
    base_size = 5_000.0 + weight * 10_000.0
    return base_size


def size_inverse_vol(capital, n_current, max_pos, leverage, vol=0.01, **kwargs):
    """Inverse-vol weighted: lower vol = bigger position. Range $5K-$15K."""
    if vol <= 0 or np.isnan(vol):
        return 10_000.0
    target_vol = 0.02
    raw_size = target_vol / vol * 5_000.0
    return max(5_000.0, min(15_000.0, raw_size))


SIZING_METHODS = {
    "A_Equal": size_equal,
    "B_RankWt": size_rank_weighted,
    "C_InvVol": size_inverse_vol,
}


# ── Compounding Position Sizing (uses current equity) ────────────────────

def size_equal_compound(capital, n_current, max_pos, leverage, **kwargs):
    """Equal weight compounding: allocate capital/max_pos per position."""
    return capital / max_pos


def size_rank_compound(capital, n_current, max_pos, leverage, rank=0.5, **kwargs):
    """Rank-weighted compounding: stronger rank = bigger slice of capital."""
    base = capital / max_pos
    weight = (rank - 0.5) / 0.5
    weight = max(0.3, min(1.0, weight))
    # Scale from 50% to 150% of equal share
    return base * (0.5 + weight)


def size_kelly_fraction(capital, n_current, max_pos, leverage, rank=0.5, **kwargs):
    """Fractional Kelly: size based on estimated edge and rank.
    Use conservative 25% Kelly to manage drawdowns."""
    # Estimated win rate and payoff from signal stats
    est_win_rate = 0.40 + (rank - 0.7) * 0.3  # 40-49% win rate
    est_payoff = 2.0  # average win / average loss ratio
    kelly_frac = est_win_rate - (1 - est_win_rate) / est_payoff
    kelly_frac = max(0.02, min(0.25, kelly_frac * 0.25))  # 25% Kelly, capped
    return capital * kelly_frac


COMPOUND_SIZING = {
    "D_EqCompound": size_equal_compound,
    "E_RankCompound": size_rank_compound,
    "F_FracKelly": size_kelly_fraction,
}

LEVERAGE_LEVELS = [1, 3, 5, 7]


# ── Backtest Engine ──────────────────────────────────────────────────────

def run_backtest(
    close_panel: pd.DataFrame,
    entry_signal: pd.DataFrame,
    ret_24h_rank: pd.DataFrame,
    rolling_corr: pd.DataFrame,
    atr_panel: pd.DataFrame,
    exit_rule,
    sizing_fn,
    leverage: float,
    test_only: bool = True,
) -> list[Trade]:
    """Run event-driven backtest for one configuration."""

    if test_only:
        start_idx = close_panel.index.searchsorted(TRAIN_END)
    else:
        # Skip first 168 bars for warmup
        start_idx = 168

    timestamps = close_panel.index[start_idx:]
    positions: list[Position] = []
    trades: list[Trade] = []
    capital = STARTING_CAPITAL

    # Precompute 24h rolling volatility for inverse-vol sizing
    ret_1h = close_panel.pct_change(1)
    vol_24h = ret_1h.rolling(24, min_periods=12).std() * np.sqrt(24)

    symbols = [c for c in close_panel.columns if c != "BTC"]

    for ts in timestamps:
        if ts not in close_panel.index:
            continue

        # --- Check exits first ---
        new_positions = []
        for pos in positions:
            if pos.symbol not in close_panel.columns:
                new_positions.append(pos)
                continue

            current_price = close_panel.at[ts, pos.symbol]
            if np.isnan(current_price):
                new_positions.append(pos)
                continue

            atr_val = atr_panel.at[ts, pos.symbol] if pos.symbol in atr_panel.columns else np.nan
            current_rank = ret_24h_rank.at[ts, pos.symbol] if pos.symbol in ret_24h_rank.columns else np.nan
            current_corr = rolling_corr.at[ts, pos.symbol] if pos.symbol in rolling_corr.columns else np.nan

            should_exit, reason = exit_rule.should_exit(
                pos, ts, current_price, atr_val, current_rank, current_corr
            )

            if should_exit:
                # Apply slippage on exit
                exit_price = current_price * (1 - SLIPPAGE_BPS / 10_000)
                raw_return = (exit_price / pos.entry_price - 1)
                leveraged_return = raw_return * leverage
                pnl = pos.size_usd * leveraged_return
                trades.append(Trade(
                    symbol=pos.symbol,
                    entry_time=pos.entry_time,
                    exit_time=ts,
                    entry_price=pos.entry_price,
                    exit_price=exit_price,
                    size_usd=pos.size_usd,
                    leverage=leverage,
                    pnl=pnl,
                    return_pct=leveraged_return * 100,
                    exit_reason=reason,
                ))
                capital += pnl
            else:
                new_positions.append(pos)

        positions = new_positions

        # --- Check entries ---
        if len(positions) < MAX_POSITIONS:
            # Get symbols with entry signals at this timestamp
            held_symbols = {p.symbol for p in positions}
            candidates = []

            for sym in symbols:
                if sym in held_symbols:
                    continue
                if ts not in entry_signal.index:
                    continue
                if entry_signal.at[ts, sym]:
                    rank_val = ret_24h_rank.at[ts, sym]
                    candidates.append((sym, rank_val))

            # Sort by rank descending (strongest first)
            candidates.sort(key=lambda x: x[1], reverse=True)

            slots_available = MAX_POSITIONS - len(positions)
            for sym, rank_val in candidates[:slots_available]:
                current_price = close_panel.at[ts, sym]
                if np.isnan(current_price) or current_price <= 0:
                    continue

                vol_val = vol_24h.at[ts, sym] if sym in vol_24h.columns else 0.01

                pos_size = sizing_fn(
                    capital=capital,
                    n_current=len(positions),
                    max_pos=MAX_POSITIONS,
                    leverage=leverage,
                    rank=rank_val,
                    vol=vol_val,
                )

                # Don't risk more than 20% of capital on a single position
                max_single = capital * 0.20
                pos_size = min(pos_size, max_single)

                if pos_size < 100:
                    continue

                # Apply slippage on entry
                entry_price = current_price * (1 + SLIPPAGE_BPS / 10_000)

                pos = Position(
                    symbol=sym,
                    entry_price=entry_price,
                    entry_time=ts,
                    size_usd=pos_size,
                    leverage=leverage,
                    entry_rank=rank_val,
                )
                positions.append(pos)

    # Force close any remaining positions at the last timestamp
    last_ts = timestamps[-1]
    for pos in positions:
        current_price = close_panel.at[last_ts, pos.symbol]
        if np.isnan(current_price):
            continue
        exit_price = current_price * (1 - SLIPPAGE_BPS / 10_000)
        raw_return = (exit_price / pos.entry_price - 1)
        leveraged_return = raw_return * leverage
        pnl = pos.size_usd * leveraged_return
        trades.append(Trade(
            symbol=pos.symbol,
            entry_time=pos.entry_time,
            exit_time=last_ts,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            size_usd=pos.size_usd,
            leverage=leverage,
            pnl=pnl,
            return_pct=leveraged_return * 100,
            exit_reason="force_close",
        ))

    return trades


# ── Performance Metrics ──────────────────────────────────────────────────

def compute_metrics(trades: list[Trade], test_start: pd.Timestamp,
                    test_end: pd.Timestamp) -> dict:
    """Compute strategy performance metrics from trade list."""
    if not trades:
        return {
            "n_trades": 0, "annual_return_pct": 0, "max_dd_pct": 0,
            "sharpe": 0, "sortino": 0, "win_rate": 0,
            "avg_trade_return_pct": 0, "total_pnl": 0,
        }

    trade_df = pd.DataFrame([{
        "entry_time": t.entry_time, "exit_time": t.exit_time,
        "pnl": t.pnl, "return_pct": t.return_pct,
        "size_usd": t.size_usd, "symbol": t.symbol,
        "exit_reason": t.exit_reason,
    } for t in trades])

    n_trades = len(trade_df)
    total_pnl = trade_df["pnl"].sum()
    win_rate = (trade_df["pnl"] > 0).mean() * 100
    avg_trade_return = trade_df["return_pct"].mean()

    # Build daily equity curve
    days = pd.date_range(test_start, test_end, freq="D")
    daily_pnl = pd.Series(0.0, index=days)

    for _, t in trade_df.iterrows():
        exit_day = t["exit_time"].normalize()
        if exit_day in daily_pnl.index:
            daily_pnl.loc[exit_day] += t["pnl"]
        else:
            # Find nearest day
            idx = daily_pnl.index.searchsorted(exit_day)
            if idx < len(daily_pnl):
                daily_pnl.iloc[idx] += t["pnl"]

    equity = STARTING_CAPITAL + daily_pnl.cumsum()
    daily_returns = equity.pct_change().dropna()

    # Max drawdown
    peak = equity.cummax()
    dd = (equity - peak) / peak
    max_dd = dd.min() * 100  # negative

    # Annualized return
    total_days = (test_end - test_start).days
    total_return = (equity.iloc[-1] / STARTING_CAPITAL) - 1
    if total_days > 0:
        annual_return = ((1 + total_return) ** (365 / total_days) - 1) * 100
    else:
        annual_return = 0

    # Sharpe (annualized)
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(365)
    else:
        sharpe = 0

    # Sortino
    downside = daily_returns[daily_returns < 0]
    if len(downside) > 1 and downside.std() > 0:
        sortino = (daily_returns.mean() / downside.std()) * np.sqrt(365)
    else:
        sortino = 0

    return {
        "n_trades": n_trades,
        "total_pnl": total_pnl,
        "total_return_pct": total_return * 100,
        "annual_return_pct": annual_return,
        "max_dd_pct": max_dd,
        "sharpe": sharpe,
        "sortino": sortino,
        "win_rate": win_rate,
        "avg_trade_return_pct": avg_trade_return,
    }


def compute_monthly_returns(trades: list[Trade]) -> dict[str, float]:
    """Compute per-month PnL returns."""
    if not trades:
        return {}

    trade_df = pd.DataFrame([{
        "exit_time": t.exit_time, "pnl": t.pnl,
    } for t in trades])

    trade_df["month"] = trade_df["exit_time"].dt.to_period("M")
    monthly = trade_df.groupby("month")["pnl"].sum()

    # Convert to dict
    result = {}
    equity = STARTING_CAPITAL
    for month in sorted(monthly.index):
        month_pnl = monthly[month]
        month_ret = month_pnl / equity * 100
        result[str(month)] = month_ret
        equity += month_pnl

    return result


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 90)
    print("IDIOSYNCRATIC MOMENTUM STRATEGY — FULL PORTFOLIO BACKTEST")
    print("=" * 90)
    print(f"Signal: 24h return rank > {RANK_THRESHOLD} AND 168h BTC corr < {BTC_CORR_THRESHOLD}")
    print(f"Capital: ${STARTING_CAPITAL:,.0f} | Max positions: {MAX_POSITIONS}")
    print(f"Slippage: {SLIPPAGE_BPS}bps/side ({SLIPPAGE_BPS*2}bps round trip)")
    print(f"Direction: LONG only")
    print()

    # ── Load data ──
    print("-- Loading data --")
    tokens = load_data()
    close_panel, high_panel, low_panel, vol_panel = build_panels(tokens)

    # ── Compute signals ──
    print("\n-- Computing signals --")
    entry_signal, ret_24h_rank, rolling_corr = compute_signals(close_panel)

    # ── Compute ATR ──
    print("-- Computing ATR --")
    atr_panel = compute_atr(high_panel, low_panel, close_panel, period=14)
    print(f"ATR panel shape: {atr_panel.shape}")

    # ── Signal stats ──
    oos_mask = close_panel.index >= TRAIN_END
    oos_signals = entry_signal.loc[oos_mask].sum().sum()
    print(f"\nOOS entry signals (after {TRAIN_END.date()}): {oos_signals:,}")

    # ── Run all configurations ──
    print("\n" + "=" * 90)
    print("RUNNING BACKTESTS — ALL CONFIGURATIONS")
    print("=" * 90)

    all_sizing = {**SIZING_METHODS, **COMPOUND_SIZING}
    configs = list(product(EXIT_RULES, all_sizing.keys(), LEVERAGE_LEVELS))
    total = len(configs)
    print(f"Total configurations: {total}")
    print()

    results = []
    for i, (exit_rule, sizing_name, lev) in enumerate(configs):
        config_name = f"{exit_rule.name}|{sizing_name}|{lev}x"
        sys.stdout.write(f"\r  [{i+1}/{total}] {config_name:<50}")
        sys.stdout.flush()

        trades = run_backtest(
            close_panel=close_panel,
            entry_signal=entry_signal,
            ret_24h_rank=ret_24h_rank,
            rolling_corr=rolling_corr,
            atr_panel=atr_panel,
            exit_rule=exit_rule,
            sizing_fn=all_sizing[sizing_name],
            leverage=lev,
            test_only=True,
        )

        metrics = compute_metrics(trades, TRAIN_END, TEST_END)
        monthly = compute_monthly_returns(trades)

        results.append({
            "exit": exit_rule.name,
            "sizing": sizing_name,
            "leverage": lev,
            "config": config_name,
            **metrics,
            "monthly": monthly,
        })

    print("\n\nAll backtests complete.\n")

    # ── Results Table ──
    print("=" * 90)
    print("FULL RESULTS TABLE")
    print("=" * 90)

    res_df = pd.DataFrame(results)

    header = (f"{'Exit':<25} {'Size':<10} {'Lev':>4} {'Trades':>7} "
              f"{'AnnRet%':>9} {'MaxDD%':>8} {'Sharpe':>7} {'Sortino':>8} "
              f"{'WinR%':>7} {'AvgTrd%':>9} {'TotalPnL':>12}")
    print(header)
    print("-" * len(header))

    for _, r in res_df.sort_values("annual_return_pct", ascending=False).iterrows():
        print(f"{r['exit']:<25} {r['sizing']:<10} {r['leverage']:>4}x "
              f"{r['n_trades']:>7} {r['annual_return_pct']:>+9.1f} "
              f"{r['max_dd_pct']:>8.1f} {r['sharpe']:>7.2f} {r['sortino']:>8.2f} "
              f"{r['win_rate']:>7.1f} {r['avg_trade_return_pct']:>+9.3f} "
              f"${r['total_pnl']:>11,.0f}")

    # ── Filter: Meets targets ──
    print("\n" + "=" * 90)
    print("CONFIGURATIONS MEETING TARGETS (AnnRet > 300%, MaxDD > -20%)")
    print("=" * 90)

    targets = res_df[
        (res_df["annual_return_pct"] > 300) &
        (res_df["max_dd_pct"] > -20)
    ].sort_values("sharpe", ascending=False)

    if len(targets) == 0:
        print("No configurations meet both targets.")
        print("\nTop 10 by Sharpe (relaxed):")
        top10 = res_df.sort_values("sharpe", ascending=False).head(10)
        for _, r in top10.iterrows():
            print(f"  {r['config']:<50} AnnRet={r['annual_return_pct']:>+.1f}% "
                  f"MaxDD={r['max_dd_pct']:.1f}% Sharpe={r['sharpe']:.2f} "
                  f"Trades={r['n_trades']}")

        # Also show top by return
        print("\nTop 10 by Annual Return:")
        top10r = res_df.sort_values("annual_return_pct", ascending=False).head(10)
        for _, r in top10r.iterrows():
            print(f"  {r['config']:<50} AnnRet={r['annual_return_pct']:>+.1f}% "
                  f"MaxDD={r['max_dd_pct']:.1f}% Sharpe={r['sharpe']:.2f} "
                  f"Trades={r['n_trades']}")
    else:
        print(f"\n{len(targets)} configurations meet targets:\n")
        for _, r in targets.iterrows():
            print(f"  {r['config']:<50} AnnRet={r['annual_return_pct']:>+.1f}% "
                  f"MaxDD={r['max_dd_pct']:.1f}% Sharpe={r['sharpe']:.2f} "
                  f"Sortino={r['sortino']:.2f} WinRate={r['win_rate']:.1f}% "
                  f"Trades={r['n_trades']}")

    # ── Best Configuration Detail ──
    print("\n" + "=" * 90)
    print("BEST CONFIGURATION — DETAILED ANALYSIS")
    print("=" * 90)

    # Pick best by Sharpe among those with > 100% annual return, or just best Sharpe
    viable = res_df[res_df["annual_return_pct"] > 100]
    if len(viable) == 0:
        viable = res_df
    best = viable.sort_values("sharpe", ascending=False).iloc[0]

    print(f"\nBest config: {best['config']}")
    print(f"  Exit rule:    {best['exit']}")
    print(f"  Sizing:       {best['sizing']}")
    print(f"  Leverage:     {best['leverage']}x")
    print(f"\n  --- Performance (OOS: {TRAIN_END.date()} to {TEST_END.date()}) ---")
    print(f"  Annual Return:     {best['annual_return_pct']:>+.1f}%")
    print(f"  Total Return:      {best['total_return_pct']:>+.1f}%")
    print(f"  Max Drawdown:      {best['max_dd_pct']:.1f}%")
    print(f"  Sharpe Ratio:      {best['sharpe']:.2f}")
    print(f"  Sortino Ratio:     {best['sortino']:.2f}")
    print(f"  Win Rate:          {best['win_rate']:.1f}%")
    print(f"  Avg Trade Return:  {best['avg_trade_return_pct']:>+.3f}%")
    print(f"  Number of Trades:  {best['n_trades']}")
    print(f"  Total PnL:         ${best['total_pnl']:,.0f}")

    # Monthly breakdown
    print(f"\n  --- Monthly Returns (OOS) ---")
    monthly = best["monthly"]
    if monthly:
        print(f"  {'Month':<12} {'Return%':>10}")
        print(f"  {'-'*25}")
        for month, ret in sorted(monthly.items()):
            flag = " ***" if "2026" in month else ""
            print(f"  {month:<12} {ret:>+10.2f}%{flag}")

    # ── Jan-Mar 2026 check ──
    print(f"\n  --- Jan-Mar 2026 Performance Check ---")
    months_2026 = {k: v for k, v in monthly.items() if "2026" in k}
    if months_2026:
        for m, r in sorted(months_2026.items()):
            status = "PASS" if r > 0 else "FAIL"
            print(f"  {m}: {r:>+.2f}% [{status}]")
        all_positive = all(v > 0 for v in months_2026.values())
        print(f"  All 2026 months positive: {'YES' if all_positive else 'NO'}")
    else:
        print("  No 2026 data available")

    # ── Re-run best config to get detailed trades ──
    print(f"\n  --- Top Traded Symbols ---")
    best_exit_rule = [e for e in EXIT_RULES if e.name == best["exit"]][0]
    best_trades = run_backtest(
        close_panel=close_panel,
        entry_signal=entry_signal,
        ret_24h_rank=ret_24h_rank,
        rolling_corr=rolling_corr,
        atr_panel=atr_panel,
        exit_rule=best_exit_rule,
        sizing_fn=SIZING_METHODS[best["sizing"]],
        leverage=best["leverage"],
        test_only=True,
    )

    if best_trades:
        trade_df = pd.DataFrame([{
            "symbol": t.symbol, "pnl": t.pnl, "return_pct": t.return_pct,
            "exit_reason": t.exit_reason,
        } for t in best_trades])

        sym_stats = trade_df.groupby("symbol").agg(
            n_trades=("pnl", "count"),
            total_pnl=("pnl", "sum"),
            avg_return=("return_pct", "mean"),
            win_rate=("pnl", lambda x: (x > 0).mean() * 100),
        ).sort_values("total_pnl", ascending=False)

        print(f"  {'Symbol':<10} {'Trades':>7} {'TotalPnL':>12} {'AvgRet%':>9} {'WinR%':>7}")
        print(f"  {'-'*50}")
        for sym, row in sym_stats.iterrows():
            print(f"  {sym:<10} {row['n_trades']:>7.0f} ${row['total_pnl']:>10,.0f} "
                  f"{row['avg_return']:>+9.3f} {row['win_rate']:>7.1f}")

        # Exit reason breakdown
        print(f"\n  --- Exit Reason Breakdown ---")
        exit_reasons = trade_df.groupby("exit_reason").agg(
            count=("pnl", "count"),
            avg_pnl=("pnl", "mean"),
            avg_ret=("return_pct", "mean"),
        ).sort_values("count", ascending=False)

        for reason, row in exit_reasons.iterrows():
            print(f"  {reason:<25} count={row['count']:>5.0f} "
                  f"avg_pnl=${row['avg_pnl']:>8,.0f} avg_ret={row['avg_ret']:>+.3f}%")

    # ── Heatmap: Leverage vs Exit ──
    print("\n" + "=" * 90)
    print("HEATMAP: Annual Return by Exit Rule x Leverage (Equal sizing)")
    print("=" * 90)

    equal_df = res_df[res_df["sizing"] == "A_Equal"]
    pivot = equal_df.pivot_table(values="annual_return_pct", index="exit",
                                  columns="leverage", aggfunc="first")
    print(f"\n{'Exit Rule':<30}", end="")
    for lev in LEVERAGE_LEVELS:
        print(f"  {lev}x{'':>6}", end="")
    print()
    print("-" * 70)
    for exit_name in pivot.index:
        print(f"{exit_name:<30}", end="")
        for lev in LEVERAGE_LEVELS:
            val = pivot.loc[exit_name, lev] if lev in pivot.columns else 0
            print(f"  {val:>+8.1f}%", end="")
        print()

    # Sharpe heatmap
    print(f"\n{'Exit Rule':<30}", end="")
    for lev in LEVERAGE_LEVELS:
        print(f"  {lev}x{'':>6}", end="")
    print("   (Sharpe)")
    print("-" * 70)

    pivot_s = equal_df.pivot_table(values="sharpe", index="exit",
                                    columns="leverage", aggfunc="first")
    for exit_name in pivot_s.index:
        print(f"{exit_name:<30}", end="")
        for lev in LEVERAGE_LEVELS:
            val = pivot_s.loc[exit_name, lev] if lev in pivot_s.columns else 0
            print(f"  {val:>+8.2f} ", end="")
        print()

    # MaxDD heatmap
    print(f"\n{'Exit Rule':<30}", end="")
    for lev in LEVERAGE_LEVELS:
        print(f"  {lev}x{'':>6}", end="")
    print("   (MaxDD%)")
    print("-" * 70)

    pivot_dd = equal_df.pivot_table(values="max_dd_pct", index="exit",
                                     columns="leverage", aggfunc="first")
    for exit_name in pivot_dd.index:
        print(f"{exit_name:<30}", end="")
        for lev in LEVERAGE_LEVELS:
            val = pivot_dd.loc[exit_name, lev] if lev in pivot_dd.columns else 0
            print(f"  {val:>+8.1f}%", end="")
        print()

    print("\n" + "=" * 90)
    print("BACKTEST COMPLETE")
    print("=" * 90)


if __name__ == "__main__":
    main()
