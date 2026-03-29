#!/usr/bin/env python3
"""
R160 BB Volatility Breakout — Gate 1 Backtest v4
=================================================
Balanced approach:
1. Use 4H resampled bars directly (native timeframe for the signal)
2. BB(20,2) on 4H bars — classic parameters  
3. Volume > 1.3x avg (slightly relaxed)
4. Momentum: close > EMA20 on 4H
5. Exit: close < SMA20 (middle BB) — simple, no trailing stop
6. No regime filter (was too restrictive)
7. Re-entry cooldown: 8 bars (32 hours) after exit to avoid whipsaws
"""
import sys
sys.path.insert(0, '/workspace/crypto_backtest')

import numpy as np
import pandas as pd
from tools.raw_backtest import Backtest

# ── Parameters ────────────────────────────────────────────────────
BB_PERIOD = 20        # Standard on 4H
BB_STD = 2.0
VOL_LOOKBACK = 20
VOL_THRESHOLD = 1.3
EMA_PERIOD = 20
COOLDOWN_BARS = 8     # 32 hours cooldown between trades
SIZE_USD = 50_000

# ── Initialize backtest ──────────────────────────────────────────
bt = Backtest(
    capital=100_000,
    fee_bps=7,
    market='perp',
    leverage_max=1.0,
    start='2024-01-01',
    end='2026-03-17',
)

# Load 1H data and resample to 4H ourselves for indicator computation
df_1h = bt.load('BTC')
print(f"Loaded BTC 1H data: {len(df_1h)} bars")

# Resample to 4H
agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
if "funding_1h" in df_1h.columns:
    agg["funding_1h"] = "sum"
df = df_1h.resample('4h').agg(agg).dropna(subset=['close'])
print(f"Resampled to 4H: {len(df)} bars")

# ── Compute indicators on 4H ────────────────────────────────────
sma = df['close'].rolling(BB_PERIOD).mean()
std_dev = df['close'].rolling(BB_PERIOD).std()
upper_bb = sma + BB_STD * std_dev
middle_bb = sma

vol_avg = df['volume'].rolling(VOL_LOOKBACK).mean()
ema = df['close'].ewm(span=EMA_PERIOD, adjust=False).mean()

# ── Entry conditions ─────────────────────────────────────────────
entry_cond = (
    (df['close'] > upper_bb) &
    (df['volume'] > VOL_THRESHOLD * vol_avg) &
    (df['close'] > ema)
)

# ── Signal generation with cooldown ─────────────────────────────
signal = pd.Series(0, index=df.index, dtype=int)
in_position = False
bars_since_exit = COOLDOWN_BARS + 1  # Allow entry on first bar

for i in range(BB_PERIOD, len(df)):
    close = df['close'].iloc[i]
    
    if in_position:
        # Exit: close below middle BB
        if close < middle_bb.iloc[i]:
            signal.iloc[i] = 0
            in_position = False
            bars_since_exit = 0
        else:
            signal.iloc[i] = 1
    else:
        bars_since_exit += 1
        if entry_cond.iloc[i] and bars_since_exit > COOLDOWN_BARS:
            signal.iloc[i] = 1
            in_position = True
        else:
            signal.iloc[i] = 0

n_entries = ((signal == 1) & (signal.shift(1) == 0)).sum()
n_bars_long = (signal == 1).sum()
print(f"Signal: {n_entries} entries, {n_bars_long} bars long ({n_bars_long/len(df)*100:.1f}%)")

# ── Run through backtest on 4H timeframe ─────────────────────────
bt.from_signals(token='BTC', signals=signal, size_usd=SIZE_USD, leverage=1.0, timeframe='4h')

print()
result = bt.report('R160 BB Breakout BTC v4 (4H native) - Gate 1')
print(f"\nVerdict: {result['verdict']}")
if result['kill_reasons']:
    for r in result['kill_reasons']:
        print(f"  - {r}")
