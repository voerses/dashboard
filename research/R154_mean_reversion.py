#!/workspace/venv/bin/python
"""
R154: BTC Mean-Reversion Strategy — Oversold/Overbought with Momentum Filter
=============================================================================

Goal: Build an uncorrelated return stream for the multi-strategy portfolio.
Mean reversion trades short-term oversold/overbought on BTC perps, filtered
by a momentum regime gate to avoid catching falling knives.

Entry Long:  RSI(14) < 30 AND price within 1 ATR(14) of lower BB(20, 2.0)
             AND EMA(168) > EMA(720) (uptrend context)
Entry Short: RSI(14) > 70 AND price within 1 ATR(14) of upper BB(20, 2.0)
             AND EMA(168) < EMA(720) (downtrend context)

Exit: TP at 1.5x ATR(14), SL at 2.0x ATR(14), max hold 72h
Position sizing: 50% equity per trade
Costs: 4bps taker per side, 3bps slippage, 8h funding

Tests at 1x, 2x, 3x leverage.

Data: BTC 1h perp from data/perp/1h_cache/BTC_1h.parquet

Author: Quant Research Agent
Date: 2026-03-28
"""

import sys
import warnings
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import timedelta
from collections import OrderedDict

warnings.filterwarnings('ignore')

# Force unbuffered output
def log(msg):
    print(msg)
    sys.stdout.flush()

# ── Paths ──────────────────────────────────────────────────────────────────────
PROJECT_DIR = Path('/workspace/crypto_backtest')
DATA_PATH = PROJECT_DIR / 'data/perp/1h_cache/BTC_1h.parquet'
OUTPUT_MD = PROJECT_DIR / 'research/R154_mean_reversion_results.md'

# ── Constants ──────────────────────────────────────────────────────────────────
RSI_PERIOD = 14
BB_PERIOD = 20
BB_STD = 2.0
ATR_PERIOD = 14
EMA_FAST = 168     # ~7 days on 1h
EMA_SLOW = 720     # ~30 days on 1h

RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

TP_ATR_MULT = 1.5
SL_ATR_MULT = 2.0
MAX_HOLD_BARS = 72  # 72 hours

POSITION_SIZE = 0.50   # 50% equity per trade
COST_TAKER_BPS = 4     # 4 bps taker per side
COST_SLIPPAGE_BPS = 3  # 3 bps slippage per side
COST_PER_SIDE = (COST_TAKER_BPS + COST_SLIPPAGE_BPS) / 10000  # 7 bps per side
COST_ROUND_TRIP = COST_PER_SIDE * 2  # 14 bps round trip

LEVERAGE_LEVELS = [1, 2, 3]


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════════════════════

def load_data():
    """Load BTC 1h perp data."""
    log("[DATA] Loading BTC 1h perp data...")
    df = pd.read_parquet(DATA_PATH)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep='first')]
    log(f"  {df.index.min()} to {df.index.max()}, {len(df)} bars")
    log(f"  Columns: {df.columns.tolist()}")
    return df


# ══════════════════════════════════════════════════════════════════════════════
# INDICATORS
# ══════════════════════════════════════════════════════════════════════════════

def compute_rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_bollinger_bands(close: pd.Series, period: int = BB_PERIOD, std_mult: float = BB_STD):
    """Bollinger Bands. Returns (middle, upper, lower)."""
    middle = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = middle + std_mult * std
    lower = middle - std_mult * std
    return middle, upper, lower


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = ATR_PERIOD) -> pd.Series:
    """Average True Range (Wilder's smoothing)."""
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    return atr


def compute_emas(close: pd.Series):
    """EMA(168) and EMA(720) for momentum filter."""
    ema_fast = close.ewm(span=EMA_FAST, adjust=False).mean()
    ema_slow = close.ewm(span=EMA_SLOW, adjust=False).mean()
    return ema_fast, ema_slow


def compute_all_indicators(df):
    """Compute all indicators and return as a DataFrame."""
    log("[INDICATORS] Computing RSI(14), BB(20,2.0), ATR(14), EMA(168), EMA(720)...")
    close = df['close']
    high = df['high']
    low = df['low']

    rsi = compute_rsi(close)
    bb_mid, bb_upper, bb_lower = compute_bollinger_bands(close)
    atr = compute_atr(high, low, close)
    ema_fast, ema_slow = compute_emas(close)

    indicators = pd.DataFrame({
        'open': df['open'],
        'high': high,
        'low': low,
        'close': close,
        'volume': df['volume'],
        'funding_1h': df['funding_1h'] if 'funding_1h' in df.columns else 0,
        'rsi': rsi,
        'bb_mid': bb_mid,
        'bb_upper': bb_upper,
        'bb_lower': bb_lower,
        'atr': atr,
        'ema_fast': ema_fast,
        'ema_slow': ema_slow,
    }, index=df.index)

    valid = indicators.dropna()
    log(f"  Valid bars after warmup: {len(valid)} (dropped {len(indicators) - len(valid)} warmup bars)")
    return indicators


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL GENERATION
# ══════════════════════════════════════════════════════════════════════════════

def generate_signals(ind: pd.DataFrame):
    """
    Generate entry signals. Returns a Series with:
      +1 = long entry signal
      -1 = short entry signal
       0 = no signal
    """
    log("[SIGNALS] Generating entry signals...")

    close = ind['close'].values
    rsi = ind['rsi'].values
    bb_upper = ind['bb_upper'].values
    bb_lower = ind['bb_lower'].values
    atr = ind['atr'].values
    ema_fast = ind['ema_fast'].values
    ema_slow = ind['ema_slow'].values

    n = len(ind)
    signals = np.zeros(n, dtype=np.int8)

    for i in range(n):
        if np.isnan(rsi[i]) or np.isnan(atr[i]) or np.isnan(bb_lower[i]) or np.isnan(ema_fast[i]) or np.isnan(ema_slow[i]):
            continue

        # Long entry conditions:
        # 1. RSI < 30 (oversold)
        # 2. Price within 1 ATR of lower BB
        # 3. Momentum filter: EMA(168) > EMA(720) (uptrend)
        if (rsi[i] < RSI_OVERSOLD
            and close[i] <= bb_lower[i] + atr[i]
            and ema_fast[i] > ema_slow[i]):
            signals[i] = 1

        # Short entry conditions:
        # 1. RSI > 70 (overbought)
        # 2. Price within 1 ATR of upper BB
        # 3. Momentum filter: EMA(168) < EMA(720) (downtrend)
        elif (rsi[i] > RSI_OVERBOUGHT
              and close[i] >= bb_upper[i] - atr[i]
              and ema_fast[i] < ema_slow[i]):
            signals[i] = -1

    sig_series = pd.Series(signals, index=ind.index)
    n_long = (signals == 1).sum()
    n_short = (signals == -1).sum()
    log(f"  Long signals: {n_long}, Short signals: {n_short}")
    return sig_series


# ══════════════════════════════════════════════════════════════════════════════
# TRADE SIMULATION
# ══════════════════════════════════════════════════════════════════════════════

def simulate_trades(ind: pd.DataFrame, signals: pd.Series, leverage: int = 1):
    """
    Simulate mean-reversion trades bar-by-bar.

    Each trade:
    - Entry at next bar open (avoid lookahead)
    - TP at entry +/- 1.5 * ATR
    - SL at entry -/+ 2.0 * ATR
    - Max hold 72 bars (72 hours)
    - Position size: 50% equity * leverage
    - Costs: 7 bps per side (entry + exit)
    - Funding: charged every 8h while in position

    Returns equity curve (Series) and list of trade dicts.
    """
    log(f"\n[SIM] Simulating trades at {leverage}x leverage...")

    close = ind['close'].values
    high = ind['high'].values
    low = ind['low'].values
    open_ = ind['open'].values
    atr = ind['atr'].values
    funding_1h = ind['funding_1h'].values if 'funding_1h' in ind.columns else np.zeros(len(ind))
    sig = signals.values
    timestamps = ind.index

    n = len(ind)
    equity = 1.0
    equity_curve = np.ones(n)

    trades = []
    in_trade = False
    trade_dir = 0
    entry_price = 0.0
    entry_bar = 0
    tp_price = 0.0
    sl_price = 0.0
    trade_size = 0.0  # in units of equity fraction
    funding_paid = 0.0

    for i in range(1, n):
        bar_pnl = 0.0

        if in_trade:
            hold_bars = i - entry_bar

            # Check funding cost (every 8 hours)
            if hold_bars > 0 and hold_bars % 8 == 0:
                # Funding: long pays funding, short receives funding
                # funding_1h is the per-hour rate; over 8h it accumulates
                # On most perp exchanges, funding is paid every 8h
                # funding_1h is already per-hour; we want the 8h payment
                fr_8h = funding_1h[i] * 8 if not np.isnan(funding_1h[i]) else 0
                # Long pays positive funding, short receives it
                funding_cost = trade_dir * fr_8h * trade_size * leverage
                equity -= funding_cost
                funding_paid += abs(funding_cost)

            # Check exits using high/low of current bar for intrabar fills
            if trade_dir == 1:  # Long position
                # Check SL first (conservative: SL before TP if both hit)
                if low[i] <= sl_price:
                    exit_price = sl_price
                    pnl_pct = (exit_price / entry_price - 1) * leverage
                    exit_cost = abs(trade_size * leverage) * COST_PER_SIDE
                    bar_pnl = trade_size * pnl_pct - exit_cost
                    equity += bar_pnl
                    trades.append({
                        'entry_time': timestamps[entry_bar],
                        'exit_time': timestamps[i],
                        'direction': 'long',
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'hold_bars': hold_bars,
                        'pnl_pct': pnl_pct - (COST_PER_SIDE * 2 * leverage) - (funding_paid / (trade_size + 1e-15)),
                        'exit_reason': 'SL',
                        'leverage': leverage,
                        'funding_paid': funding_paid,
                    })
                    in_trade = False
                    funding_paid = 0.0

                elif high[i] >= tp_price:
                    exit_price = tp_price
                    pnl_pct = (exit_price / entry_price - 1) * leverage
                    exit_cost = abs(trade_size * leverage) * COST_PER_SIDE
                    bar_pnl = trade_size * pnl_pct - exit_cost
                    equity += bar_pnl
                    trades.append({
                        'entry_time': timestamps[entry_bar],
                        'exit_time': timestamps[i],
                        'direction': 'long',
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'hold_bars': hold_bars,
                        'pnl_pct': pnl_pct - (COST_PER_SIDE * 2 * leverage) - (funding_paid / (trade_size + 1e-15)),
                        'exit_reason': 'TP',
                        'leverage': leverage,
                        'funding_paid': funding_paid,
                    })
                    in_trade = False
                    funding_paid = 0.0

                elif hold_bars >= MAX_HOLD_BARS:
                    exit_price = close[i]
                    pnl_pct = (exit_price / entry_price - 1) * leverage
                    exit_cost = abs(trade_size * leverage) * COST_PER_SIDE
                    bar_pnl = trade_size * pnl_pct - exit_cost
                    equity += bar_pnl
                    trades.append({
                        'entry_time': timestamps[entry_bar],
                        'exit_time': timestamps[i],
                        'direction': 'long',
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'hold_bars': hold_bars,
                        'pnl_pct': pnl_pct - (COST_PER_SIDE * 2 * leverage) - (funding_paid / (trade_size + 1e-15)),
                        'exit_reason': 'MAX_HOLD',
                        'leverage': leverage,
                        'funding_paid': funding_paid,
                    })
                    in_trade = False
                    funding_paid = 0.0

            elif trade_dir == -1:  # Short position
                # Check SL first
                if high[i] >= sl_price:
                    exit_price = sl_price
                    pnl_pct = (1 - exit_price / entry_price) * leverage
                    exit_cost = abs(trade_size * leverage) * COST_PER_SIDE
                    bar_pnl = trade_size * pnl_pct - exit_cost
                    equity += bar_pnl
                    trades.append({
                        'entry_time': timestamps[entry_bar],
                        'exit_time': timestamps[i],
                        'direction': 'short',
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'hold_bars': hold_bars,
                        'pnl_pct': pnl_pct - (COST_PER_SIDE * 2 * leverage) - (funding_paid / (trade_size + 1e-15)),
                        'exit_reason': 'SL',
                        'leverage': leverage,
                        'funding_paid': funding_paid,
                    })
                    in_trade = False
                    funding_paid = 0.0

                elif low[i] <= tp_price:
                    exit_price = tp_price
                    pnl_pct = (1 - exit_price / entry_price) * leverage
                    exit_cost = abs(trade_size * leverage) * COST_PER_SIDE
                    bar_pnl = trade_size * pnl_pct - exit_cost
                    equity += bar_pnl
                    trades.append({
                        'entry_time': timestamps[entry_bar],
                        'exit_time': timestamps[i],
                        'direction': 'short',
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'hold_bars': hold_bars,
                        'pnl_pct': pnl_pct - (COST_PER_SIDE * 2 * leverage) - (funding_paid / (trade_size + 1e-15)),
                        'exit_reason': 'TP',
                        'leverage': leverage,
                        'funding_paid': funding_paid,
                    })
                    in_trade = False
                    funding_paid = 0.0

                elif hold_bars >= MAX_HOLD_BARS:
                    exit_price = close[i]
                    pnl_pct = (1 - exit_price / entry_price) * leverage
                    exit_cost = abs(trade_size * leverage) * COST_PER_SIDE
                    bar_pnl = trade_size * pnl_pct - exit_cost
                    equity += bar_pnl
                    trades.append({
                        'entry_time': timestamps[entry_bar],
                        'exit_time': timestamps[i],
                        'direction': 'short',
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'hold_bars': hold_bars,
                        'pnl_pct': pnl_pct - (COST_PER_SIDE * 2 * leverage) - (funding_paid / (trade_size + 1e-15)),
                        'exit_reason': 'MAX_HOLD',
                        'leverage': leverage,
                        'funding_paid': funding_paid,
                    })
                    in_trade = False
                    funding_paid = 0.0

        # Check for new entry (only if flat)
        if not in_trade and i < n - 1:
            # Use signal from bar i, enter at open of bar i+1
            if sig[i] != 0 and not np.isnan(atr[i]) and atr[i] > 0:
                trade_dir = int(sig[i])
                entry_bar = i + 1
                entry_price = open_[i + 1] if i + 1 < n else close[i]
                entry_atr = atr[i]

                # Set TP and SL
                if trade_dir == 1:  # Long
                    tp_price = entry_price + TP_ATR_MULT * entry_atr
                    sl_price = entry_price - SL_ATR_MULT * entry_atr
                else:  # Short
                    tp_price = entry_price - TP_ATR_MULT * entry_atr
                    sl_price = entry_price + SL_ATR_MULT * entry_atr

                # Position size: 50% of equity
                trade_size = POSITION_SIZE * equity
                # Entry cost
                entry_cost = abs(trade_size * leverage) * COST_PER_SIDE
                equity -= entry_cost
                in_trade = True
                funding_paid = 0.0

        equity_curve[i] = equity

    # Close any open trade at end
    if in_trade:
        exit_price = close[-1]
        hold_bars = n - 1 - entry_bar
        if trade_dir == 1:
            pnl_pct = (exit_price / entry_price - 1) * leverage
        else:
            pnl_pct = (1 - exit_price / entry_price) * leverage
        exit_cost = abs(trade_size * leverage) * COST_PER_SIDE
        bar_pnl = trade_size * pnl_pct - exit_cost
        equity += bar_pnl
        equity_curve[-1] = equity
        trades.append({
            'entry_time': timestamps[entry_bar],
            'exit_time': timestamps[-1],
            'direction': 'long' if trade_dir == 1 else 'short',
            'entry_price': entry_price,
            'exit_price': exit_price,
            'hold_bars': hold_bars,
            'pnl_pct': pnl_pct - (COST_PER_SIDE * 2 * leverage) - (funding_paid / (trade_size + 1e-15)),
            'exit_reason': 'END',
            'leverage': leverage,
            'funding_paid': funding_paid,
        })

    eq_series = pd.Series(equity_curve, index=ind.index)

    n_trades = len(trades)
    n_long = sum(1 for t in trades if t['direction'] == 'long')
    n_short = sum(1 for t in trades if t['direction'] == 'short')
    n_wins = sum(1 for t in trades if t['pnl_pct'] > 0)
    win_rate = n_wins / n_trades if n_trades > 0 else 0

    log(f"  Trades: {n_trades} ({n_long} long, {n_short} short)")
    log(f"  Win rate: {win_rate:.1%}")
    log(f"  Final equity: {equity:.4f} ({(equity - 1) * 100:.2f}%)")

    return eq_series, trades


# ══════════════════════════════════════════════════════════════════════════════
# PERFORMANCE METRICS
# ══════════════════════════════════════════════════════════════════════════════

def compute_metrics(equity_curve: pd.Series, trades: list, label: str = ""):
    """Compute standard performance metrics from an equity curve."""
    # Daily equity
    daily_eq = equity_curve.resample('1D').last().dropna()
    daily_returns = daily_eq.pct_change().dropna()

    total_return = (daily_eq.iloc[-1] / daily_eq.iloc[0]) - 1
    n_days = len(daily_returns)
    n_years = n_days / 365.25

    ann_return = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0

    ann_vol = daily_returns.std() * np.sqrt(365.25)
    sharpe = ann_return / ann_vol if ann_vol > 0 else 0

    # Max drawdown
    cum_max = daily_eq.cummax()
    drawdown = (daily_eq - cum_max) / cum_max
    max_dd = drawdown.min()

    calmar = ann_return / abs(max_dd) if max_dd != 0 else 0

    # Sortino
    neg_returns = daily_returns[daily_returns < 0]
    downside_vol = neg_returns.std() * np.sqrt(365.25) if len(neg_returns) > 0 else 0
    sortino = ann_return / downside_vol if downside_vol > 0 else 0

    # Trade stats
    n_trades = len(trades)
    n_wins = sum(1 for t in trades if t['pnl_pct'] > 0)
    win_rate = n_wins / n_trades if n_trades > 0 else 0

    avg_hold = np.mean([t['hold_bars'] for t in trades]) if trades else 0
    median_hold = np.median([t['hold_bars'] for t in trades]) if trades else 0

    avg_win = np.mean([t['pnl_pct'] for t in trades if t['pnl_pct'] > 0]) if n_wins > 0 else 0
    avg_loss = np.mean([t['pnl_pct'] for t in trades if t['pnl_pct'] <= 0]) if (n_trades - n_wins) > 0 else 0

    # Exit reason breakdown
    exit_reasons = {}
    for t in trades:
        r = t['exit_reason']
        exit_reasons[r] = exit_reasons.get(r, 0) + 1

    # Cost breakdown
    total_funding = sum(t.get('funding_paid', 0) for t in trades)

    # Profit factor
    gross_profit = sum(t['pnl_pct'] for t in trades if t['pnl_pct'] > 0) if n_wins > 0 else 0
    gross_loss = abs(sum(t['pnl_pct'] for t in trades if t['pnl_pct'] <= 0)) if (n_trades - n_wins) > 0 else 1e-15
    profit_factor = gross_profit / gross_loss

    metrics = OrderedDict({
        'total_return': total_return,
        'ann_return': ann_return,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'calmar': calmar,
        'sortino': sortino,
        'n_trades': n_trades,
        'n_long': sum(1 for t in trades if t['direction'] == 'long'),
        'n_short': sum(1 for t in trades if t['direction'] == 'short'),
        'win_rate': win_rate,
        'avg_hold_bars': avg_hold,
        'median_hold_bars': median_hold,
        'avg_win_pct': avg_win,
        'avg_loss_pct': avg_loss,
        'profit_factor': profit_factor,
        'exit_reasons': exit_reasons,
        'total_funding_paid': total_funding,
        'n_days': n_days,
        'n_years': n_years,
    })

    if label:
        log(f"\n{'='*60}")
        log(f"  {label}")
        log(f"{'='*60}")
    log(f"  Total Return:   {total_return:>10.2%}")
    log(f"  Annual Return:  {ann_return:>10.2%}")
    log(f"  Annual Vol:     {ann_vol:>10.2%}")
    log(f"  Sharpe:         {sharpe:>10.3f}")
    log(f"  Max Drawdown:   {max_dd:>10.2%}")
    log(f"  Calmar:         {calmar:>10.3f}")
    log(f"  Sortino:        {sortino:>10.3f}")
    log(f"  Trades:         {n_trades:>10d} ({metrics['n_long']} long, {metrics['n_short']} short)")
    log(f"  Win Rate:       {win_rate:>10.1%}")
    log(f"  Avg Hold:       {avg_hold:>10.1f}h (median {median_hold:.0f}h)")
    log(f"  Avg Win:        {avg_win:>10.2%}")
    log(f"  Avg Loss:       {avg_loss:>10.2%}")
    log(f"  Profit Factor:  {profit_factor:>10.3f}")
    log(f"  Exit Reasons:   {exit_reasons}")
    log(f"  Total Funding:  {total_funding:>10.6f}")

    return metrics


def compute_monthly_returns(equity_curve: pd.Series):
    """Compute monthly returns table."""
    daily_eq = equity_curve.resample('1D').last().dropna()
    monthly_eq = daily_eq.resample('ME').last().dropna()
    monthly_ret = monthly_eq.pct_change().dropna()

    # Build year x month table
    monthly_ret_df = pd.DataFrame({
        'year': monthly_ret.index.year,
        'month': monthly_ret.index.month,
        'return': monthly_ret.values
    })

    pivot = monthly_ret_df.pivot_table(values='return', index='year', columns='month', aggfunc='sum')
    pivot.columns = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

    # Annual total
    yearly = daily_eq.resample('YE').last().pct_change().dropna()
    pivot['Annual'] = yearly.values[:len(pivot)] if len(yearly) >= len(pivot) else np.nan

    return pivot, monthly_ret


# ══════════════════════════════════════════════════════════════════════════════
# TREND-FOLLOWING BENCHMARK (for correlation analysis)
# ══════════════════════════════════════════════════════════════════════════════

def compute_trend_benchmark(ind: pd.DataFrame):
    """
    Simple trend-following benchmark: long when EMA(168) > EMA(720), flat otherwise.
    This mimics the trend-following component of the portfolio.
    Returns daily return series.
    """
    log("\n[BENCHMARK] Computing trend-following benchmark (EMA cross)...")

    close = ind['close']
    ema_fast = ind['ema_fast']
    ema_slow = ind['ema_slow']

    # Position: 1 when uptrend, 0 when downtrend (no shorting for benchmark)
    position = (ema_fast > ema_slow).astype(int)
    # Shift by 1 bar to avoid lookahead
    position = position.shift(1).fillna(0)

    # 1h returns
    returns_1h = close.pct_change().fillna(0)
    # Strategy returns
    strat_ret_1h = position * returns_1h

    # Resample to daily
    daily_strat = strat_ret_1h.resample('1D').sum()
    daily_strat = daily_strat.dropna()

    ann_ret = daily_strat.mean() * 365.25
    ann_vol = daily_strat.std() * np.sqrt(365.25)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

    log(f"  Trend benchmark: Ann Return={ann_ret:.2%}, Sharpe={sharpe:.3f}")
    return daily_strat


# ══════════════════════════════════════════════════════════════════════════════
# CORRELATION ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def compute_correlation(mr_equity: pd.Series, trend_daily: pd.Series, label: str = ""):
    """Compute correlation between mean-reversion daily returns and trend-following daily returns."""
    log(f"\n[CORRELATION] {label}")

    # Mean-reversion daily returns
    mr_daily_eq = mr_equity.resample('1D').last().dropna()
    mr_daily_ret = mr_daily_eq.pct_change().dropna()

    # Align
    common_idx = mr_daily_ret.index.intersection(trend_daily.index)
    mr_aligned = mr_daily_ret.reindex(common_idx).fillna(0)
    trend_aligned = trend_daily.reindex(common_idx).fillna(0)

    if len(common_idx) < 30:
        log("  Insufficient overlapping days for correlation")
        return {'pearson': np.nan, 'spearman': np.nan, 'n_days': len(common_idx)}

    from scipy import stats
    pearson_r, pearson_p = stats.pearsonr(mr_aligned, trend_aligned)
    spearman_r, spearman_p = stats.spearmanr(mr_aligned, trend_aligned)

    # Rolling 90-day correlation
    combined = pd.DataFrame({'mr': mr_aligned, 'trend': trend_aligned})
    rolling_corr = combined['mr'].rolling(90).corr(combined['trend'])
    avg_rolling = rolling_corr.dropna().mean()
    max_rolling = rolling_corr.dropna().max()
    min_rolling = rolling_corr.dropna().min()

    log(f"  Pearson r:  {pearson_r:.4f} (p={pearson_p:.4e})")
    log(f"  Spearman r: {spearman_r:.4f} (p={spearman_p:.4e})")
    log(f"  Rolling 90d corr: avg={avg_rolling:.4f}, range=[{min_rolling:.4f}, {max_rolling:.4f}]")
    log(f"  Overlapping days: {len(common_idx)}")

    return {
        'pearson': pearson_r,
        'pearson_p': pearson_p,
        'spearman': spearman_r,
        'spearman_p': spearman_p,
        'rolling_avg': avg_rolling,
        'rolling_max': max_rolling,
        'rolling_min': min_rolling,
        'n_days': len(common_idx),
    }


# ══════════════════════════════════════════════════════════════════════════════
# LAST 12 MONTHS ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def compute_last_12m_metrics(equity_curve: pd.Series, trades: list, label: str = ""):
    """Compute metrics for the last 12 months only."""
    cutoff = equity_curve.index.max() - pd.Timedelta(days=365)
    eq_12m = equity_curve.loc[cutoff:]

    # Normalize equity to start at 1
    eq_12m_norm = eq_12m / eq_12m.iloc[0]

    # Filter trades
    trades_12m = [t for t in trades if t['entry_time'] >= cutoff]

    log(f"\n  --- LAST 12 MONTHS ({cutoff.date()} to {equity_curve.index.max().date()}) ---")
    metrics = compute_metrics(eq_12m_norm, trades_12m, label=f"Last 12M: {label}")
    return metrics, eq_12m_norm


# ══════════════════════════════════════════════════════════════════════════════
# RESULTS WRITER
# ══════════════════════════════════════════════════════════════════════════════

def write_results(all_results: dict, corr_results: dict, monthly_tables: dict, output_path: Path):
    """Write results to markdown file."""
    log(f"\n[OUTPUT] Writing results to {output_path}")

    lines = []
    lines.append("# R154 -- BTC Mean-Reversion Strategy Results")
    lines.append("")
    lines.append(f"**Date**: 2026-03-28")
    lines.append(f"**Asset**: BTC perpetual")
    lines.append(f"**Timeframe**: 1h bars")
    lines.append(f"**Data range**: Full backtest period")
    lines.append("")
    lines.append("## Strategy Specification")
    lines.append("")
    lines.append("| Parameter | Value |")
    lines.append("|-----------|-------|")
    lines.append(f"| RSI Period | {RSI_PERIOD} |")
    lines.append(f"| RSI Oversold / Overbought | {RSI_OVERSOLD} / {RSI_OVERBOUGHT} |")
    lines.append(f"| Bollinger Band Period | {BB_PERIOD} |")
    lines.append(f"| Bollinger Band Std | {BB_STD} |")
    lines.append(f"| ATR Period | {ATR_PERIOD} |")
    lines.append(f"| EMA Fast (momentum) | {EMA_FAST} (7 days) |")
    lines.append(f"| EMA Slow (momentum) | {EMA_SLOW} (30 days) |")
    lines.append(f"| Take Profit | {TP_ATR_MULT}x ATR |")
    lines.append(f"| Stop Loss | {SL_ATR_MULT}x ATR |")
    lines.append(f"| Max Hold Period | {MAX_HOLD_BARS}h (3 days) |")
    lines.append(f"| Position Size | {POSITION_SIZE:.0%} equity |")
    lines.append(f"| Cost per side | {(COST_TAKER_BPS + COST_SLIPPAGE_BPS)} bps (taker {COST_TAKER_BPS} + slip {COST_SLIPPAGE_BPS}) |")
    lines.append(f"| Cost round trip | {COST_ROUND_TRIP * 10000:.0f} bps |")
    lines.append("")

    # Full period results
    lines.append("## Full Period Results")
    lines.append("")
    lines.append("| Metric | 1x Leverage | 2x Leverage | 3x Leverage |")
    lines.append("|--------|-------------|-------------|-------------|")

    metric_labels = [
        ('ann_return', 'Annual Return', '.2%'),
        ('max_dd', 'Max Drawdown', '.2%'),
        ('sharpe', 'Sharpe', '.3f'),
        ('calmar', 'Calmar', '.3f'),
        ('sortino', 'Sortino', '.3f'),
        ('n_trades', 'Trade Count', 'd'),
        ('win_rate', 'Win Rate', '.1%'),
        ('avg_hold_bars', 'Avg Hold (hours)', '.1f'),
        ('median_hold_bars', 'Median Hold (hours)', '.0f'),
        ('avg_win_pct', 'Avg Win', '.2%'),
        ('avg_loss_pct', 'Avg Loss', '.2%'),
        ('profit_factor', 'Profit Factor', '.3f'),
        ('total_return', 'Total Return', '.2%'),
    ]

    for key, label, fmt in metric_labels:
        vals = []
        for lev in LEVERAGE_LEVELS:
            m = all_results[f'{lev}x']['full']
            v = m[key]
            if fmt == 'd':
                vals.append(f"{v:{fmt}}")
            else:
                vals.append(f"{v:{fmt}}")
        lines.append(f"| {label} | {vals[0]} | {vals[1]} | {vals[2]} |")

    lines.append("")

    # Exit reason breakdown
    lines.append("### Exit Reason Breakdown")
    lines.append("")
    lines.append("| Exit Reason | 1x | 2x | 3x |")
    lines.append("|-------------|-----|-----|-----|")
    all_reasons = set()
    for lev in LEVERAGE_LEVELS:
        all_reasons.update(all_results[f'{lev}x']['full']['exit_reasons'].keys())
    for reason in sorted(all_reasons):
        vals = []
        for lev in LEVERAGE_LEVELS:
            v = all_results[f'{lev}x']['full']['exit_reasons'].get(reason, 0)
            vals.append(str(v))
        lines.append(f"| {reason} | {vals[0]} | {vals[1]} | {vals[2]} |")
    lines.append("")

    # Cost breakdown
    lines.append("### Cost Breakdown")
    lines.append("")
    lines.append("| Cost Component | 1x | 2x | 3x |")
    lines.append("|----------------|-----|-----|-----|")
    for lev in LEVERAGE_LEVELS:
        m = all_results[f'{lev}x']['full']
        n_trades = m['n_trades']
        rt_cost = n_trades * COST_ROUND_TRIP * POSITION_SIZE * lev
        lines.append(f"| Trading costs ({lev}x) | {rt_cost:.4f} ({rt_cost*100:.2f}%) | - | - |" if lev == 1 else "")

    # Simpler cost table
    lines_cost = []
    lines_cost.append("| Component | Formula | 1x | 2x | 3x |")
    lines_cost.append("|-----------|---------|-----|-----|-----|")
    for lev in LEVERAGE_LEVELS:
        m = all_results[f'{lev}x']['full']
        n_trades = m['n_trades']
        # Approximate: each trade costs (COST_PER_SIDE * 2) * position_size * leverage
        est_trading_cost_pct = n_trades * COST_ROUND_TRIP * POSITION_SIZE * lev * 100
        funding = m['total_funding_paid'] * 100
        total_cost_pct = est_trading_cost_pct + funding
        lines_cost.append(f"| Est. total trading cost | {n_trades} trades x {COST_ROUND_TRIP*10000:.0f}bps x {POSITION_SIZE:.0%} x {lev}x | {est_trading_cost_pct:.1f}% | - | - |" if lev == LEVERAGE_LEVELS[0] else "")

    # Rewrite cost section cleanly
    lines = lines[:-len(lines_cost) - 4] if len(lines_cost) > 2 else lines  # remove partial
    lines.append("### Cost Breakdown (Estimated)")
    lines.append("")
    lines.append("| Component | 1x | 2x | 3x |")
    lines.append("|-----------|-----|-----|-----|")
    for cost_label in ['Trading fees (est.)', 'Funding paid', 'Total cost drag']:
        vals = []
        for lev in LEVERAGE_LEVELS:
            m = all_results[f'{lev}x']['full']
            n_trades = m['n_trades']
            est_trade = n_trades * COST_ROUND_TRIP * POSITION_SIZE * lev
            fund = m['total_funding_paid']
            if cost_label == 'Trading fees (est.)':
                vals.append(f"{est_trade*100:.2f}%")
            elif cost_label == 'Funding paid':
                vals.append(f"{fund*100:.4f}%")
            else:
                vals.append(f"{(est_trade+fund)*100:.2f}%")
        lines.append(f"| {cost_label} | {vals[0]} | {vals[1]} | {vals[2]} |")
    lines.append("")

    # Last 12 months
    lines.append("## Last 12 Months Performance")
    lines.append("")
    lines.append("| Metric | 1x Leverage | 2x Leverage | 3x Leverage |")
    lines.append("|--------|-------------|-------------|-------------|")

    for key, label, fmt in metric_labels:
        vals = []
        for lev in LEVERAGE_LEVELS:
            m = all_results[f'{lev}x']['last_12m']
            v = m[key]
            if fmt == 'd':
                vals.append(f"{v:{fmt}}")
            else:
                vals.append(f"{v:{fmt}}")
        lines.append(f"| {label} | {vals[0]} | {vals[1]} | {vals[2]} |")
    lines.append("")

    # Correlation with trend
    lines.append("## Correlation with Trend-Following")
    lines.append("")
    lines.append("| Metric | 1x | 2x | 3x |")
    lines.append("|--------|-----|-----|-----|")
    for key, label in [('pearson', 'Pearson r'), ('spearman', 'Spearman r'),
                       ('rolling_avg', 'Rolling 90d avg'), ('rolling_max', 'Rolling 90d max'),
                       ('rolling_min', 'Rolling 90d min'), ('n_days', 'Overlapping days')]:
        vals = []
        for lev in LEVERAGE_LEVELS:
            v = corr_results[f'{lev}x'].get(key, np.nan)
            if key == 'n_days':
                vals.append(f"{v}")
            else:
                vals.append(f"{v:.4f}" if not np.isnan(v) else "N/A")
        lines.append(f"| {label} | {vals[0]} | {vals[1]} | {vals[2]} |")
    lines.append("")

    # Monthly returns table (1x)
    lines.append("## Monthly Returns (1x Leverage)")
    lines.append("")
    if '1x' in monthly_tables and monthly_tables['1x'] is not None:
        pivot = monthly_tables['1x']
        cols = pivot.columns.tolist()
        lines.append("| Year | " + " | ".join(cols) + " |")
        lines.append("|------|" + "|".join(["------"] * len(cols)) + "|")
        for year, row in pivot.iterrows():
            vals = []
            for c in cols:
                v = row[c]
                if pd.isna(v):
                    vals.append("-")
                else:
                    vals.append(f"{v:.1%}")
            lines.append(f"| {year} | " + " | ".join(vals) + " |")
    lines.append("")

    # Monthly returns table (2x)
    lines.append("## Monthly Returns (2x Leverage)")
    lines.append("")
    if '2x' in monthly_tables and monthly_tables['2x'] is not None:
        pivot = monthly_tables['2x']
        cols = pivot.columns.tolist()
        lines.append("| Year | " + " | ".join(cols) + " |")
        lines.append("|------|" + "|".join(["------"] * len(cols)) + "|")
        for year, row in pivot.iterrows():
            vals = []
            for c in cols:
                v = row[c]
                if pd.isna(v):
                    vals.append("-")
                else:
                    vals.append(f"{v:.1%}")
            lines.append(f"| {year} | " + " | ".join(vals) + " |")
    lines.append("")

    # Summary verdict
    lines.append("## Summary & Verdict")
    lines.append("")

    m1 = all_results['1x']['full']
    m1_12 = all_results['1x']['last_12m']
    c1 = corr_results['1x']

    lines.append(f"- **Trade count**: {m1['n_trades']} trades over full period ({'PASS' if m1['n_trades'] > 50 else 'FAIL'}: need >50)")
    lines.append(f"- **Correlation with trend**: Pearson r = {c1['pearson']:.4f} ({'PASS' if abs(c1['pearson']) < 0.3 else 'FAIL'}: need < 0.3)")
    lines.append(f"- **Sharpe (1x, full)**: {m1['sharpe']:.3f}")
    lines.append(f"- **Sharpe (1x, 12m)**: {m1_12['sharpe']:.3f}")

    best_lev = max(LEVERAGE_LEVELS, key=lambda l: all_results[f'{l}x']['last_12m']['ann_return'])
    m_best_12 = all_results[f'{best_lev}x']['last_12m']
    lines.append(f"- **Best leverage for last 12m**: {best_lev}x (Ann Return: {m_best_12['ann_return']:.2%}, Sharpe: {m_best_12['sharpe']:.3f})")
    lines.append("")

    # Write
    with open(output_path, 'w') as f:
        f.write('\n'.join(lines))
    log(f"  Written to {output_path}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    log("=" * 70)
    log("R154: BTC Mean-Reversion Strategy Backtest")
    log("=" * 70)

    # Load data
    df = load_data()

    # Compute indicators
    ind = compute_all_indicators(df)

    # Generate signals
    signals = generate_signals(ind)

    # Trend benchmark for correlation
    trend_daily = compute_trend_benchmark(ind)

    # Run simulations at each leverage level
    all_results = {}
    corr_results = {}
    monthly_tables = {}
    equity_curves = {}

    for lev in LEVERAGE_LEVELS:
        label = f"{lev}x"

        # Simulate
        eq_curve, trades = simulate_trades(ind, signals, leverage=lev)

        # Full period metrics
        full_metrics = compute_metrics(eq_curve, trades, label=f"Full Period {label}")

        # Last 12m metrics
        last_12m_metrics, eq_12m = compute_last_12m_metrics(eq_curve, trades, label=label)

        # Monthly returns
        monthly_pivot, monthly_ret = compute_monthly_returns(eq_curve)

        # Correlation with trend
        corr = compute_correlation(eq_curve, trend_daily, label=f"{label} vs Trend")

        all_results[label] = {
            'full': full_metrics,
            'last_12m': last_12m_metrics,
        }
        corr_results[label] = corr
        monthly_tables[label] = monthly_pivot
        equity_curves[label] = eq_curve

    # Write results
    write_results(all_results, corr_results, monthly_tables, OUTPUT_MD)

    log("\n" + "=" * 70)
    log("DONE")
    log("=" * 70)


if __name__ == '__main__':
    main()
