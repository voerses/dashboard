# Data Sources & Fee Structures

## Spot Data

**Source: Binance only**

All spot OHLCV data is fetched from Binance. The `data/spot/1h_cache/` directory
contains 116 tokens of 1h candles, sourced from `data/spot/binance/1h_ohlcv/`.

There is no spot data from Kraken or Hyperliquid. When running spot backtests with
`--exchange kraken`, the same Binance price data is used but Kraken's fee schedule
is applied. This answers "what if we traded this strategy on Kraken?" — the prices
would be very similar across exchanges, but fees differ significantly.

**Valid spot exchange configs:**
- `binance` (default) — 0.10% maker, 0.10% taker
- `kraken` — 0.16% maker, 0.26% taker (assumes ~$250k-500k/mo volume tier)

## Perp Data

**Source: Binance, Kraken, Hyperliquid**

Perp data comes from all three exchanges, each with their own OHLCV and funding rate data:

| Exchange | OHLCV tokens | Funding | Location |
|----------|-------------|---------|----------|
| Binance | 165 | Yes | `data/perp/binance/` |
| Kraken | 314 | Yes | `data/perp/kraken/` |
| Hyperliquid | 52 | Yes | `data/perp/hyperliquid/` |

The `data/perp/1h_cache/` directory (360 files) contains merged/deduplicated
perp data used by the engine.

**Valid perp exchange configs:**
- `binance` — 0.02% maker, 0.05% taker
- `kraken` — 0.02% maker, 0.05% taker
- `hyperliquid` — 0.015% maker, 0.045% taker

## Fee Table (verified 2025-2026)

```
Exchange       Spot Maker  Spot Taker  Perp Maker  Perp Taker
─────────────  ──────────  ──────────  ──────────  ──────────
Binance          10 bps      10 bps       2 bps       5 bps
Kraken           16 bps      26 bps       2 bps       5 bps
Hyperliquid       4 bps       7 bps     1.5 bps     4.5 bps
```

Sources:
- Binance: https://www.binance.com/en/fee (base tier, no BNB discount)
- Kraken: https://www.kraken.com/features/fee-schedule ($250k-500k/mo tier)
- Hyperliquid: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees (Tier 0)

Notes:
- All backtests use **taker** fees (conservative — assumes market orders)
- Kraken spot fees assume a realistic volume tier for a $200k account trading
  actively, not the base tier ($0-10k) which is 25/40 bps
- Binance perp fees can be reduced 10% by paying with BNB (not modeled)
- Hyperliquid perp fees can be reduced up to 40% by staking HYPE (not modeled)

## Fee Accounting in the Engine

Fees are charged on **both entry and exit** (round-trip):

1. **Entry fee**: Deducted from equity/cash at entry time
   - In per-token JIT: `equity -= abs(position * entry_price) * fee_rate`
   - In portfolio sim: `cash -= pos_usd * fee_rate`

2. **Exit fee**: Deducted from PnL at exit time
   - In per-token JIT: `net_pnl = gross_pnl - abs(position * exit_price) * fee_rate`
   - In portfolio sim: Already embedded in `trade['pnl']` (net of exit fee)

3. **Slippage** (separate from fees):
   - `slip_bps = 3.0 + 0.03 * sqrt(pos_usd / ADV) * 10000`
   - Applied to both entry and exit prices (moves price against you)
   - Scales with position size relative to liquidity

## Leverage

Leverage is a **perp-only** feature. For spot, leverage is always 1.0.

Strategies can set leverage two ways in `StrategyResult`:

1. **Scalar** (constant for all trades): `result.leverage = 3.0`
2. **Per-bar array** (dynamic): `result.leverage = leverage_array`

The engine converts either form to a per-bar array before passing to the JIT.
At each entry bar `i`, the engine uses `leverage_arr[i]` to:
- Amplify notional exposure: `pos_usd *= leverage_arr[i]`
- Track margin separately: `margin_usd = pos_usd_before_leverage`
- Check liquidation every bar: force-close if unrealized loss > 95% of margin

This enables regime-adaptive leverage, e.g.:
```python
# 2x in uptrends, 1x otherwise
leverage = np.where(ctx.regime_1h == 2, 2.0, 1.0)
result.leverage = leverage
```

## Usage

```bash
# Spot with Binance fees (default)
python v3/portfolio.py --strategy s11 --exchange binance

# Spot with Kraken fees (same Binance data, Kraken fee structure)
python v3/portfolio.py --strategy s11 --exchange kraken

# Perp with Hyperliquid fees
python v3/portfolio.py --strategy s11 --market perp --exchange hyperliquid
```
