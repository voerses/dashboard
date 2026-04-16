# Mission A — Orderbook-Driven Cascade Signals

Created: 2026-04-07
Context: Mission A (Liquidation Cascade Recovery) was PARKED because Coinalyze free-tier hourly liquidation data covers only ~120 days. User insight: we already have 1 year of Binance Vision orderbook + trades for BTC/ETH/SOL via Mission F. Redesign Mission A around microstructure signals derived from that data instead of waiting for liquidation archives.

## Data we have

| Dataset | Shape | Location |
|---|---|---|
| **bookdepth** | 10 fixed % bands (±1/2/3/4/5%), ~33s snapshots, 365 days | `data/perp/binance/{BTCUSDT,ETHUSDT,SOLUSDT}/bookdepth/{date}.parquet` |
| **trades_1s** | 1-second bars: OHLC, buy_vol, sell_vol, buy_notional, sell_notional, num_trades, VWAP | `data/perp/binance/{BTCUSDT,ETHUSDT,SOLUSDT}/trades_1s/{date}.parquet` |

### Data constraints (design around these)
- No true per-level L2 — only aggregated bands
- No sub-33s book events
- No tick identity within a 1s bar (just aggregates)
- Binance `notional` field in bookdepth is **cumulative** (verified: ±5% band ≈ 4.5× ±1% band)
- Only BTC/ETH/SOL (scaling to top-30 is Mission F Phase 2)

## Signal catalog (6 metrics)

### 1. Depth-to-Move (DtM) — cascade runway detection

Cumulative notional required to move price by X% through the visible book, per direction. This determines whether a cascade is **physically possible**.

**Derived metrics:**
- `DtM_asymmetry = (DtM_ask[5%] - DtM_bid[5%]) / (DtM_ask[5%] + DtM_bid[5%])`. Range [-1,+1]. Negative = thin bid → downside runway.
- `DtM_velocity = d/dt(DtM_bid[5%])` at 1h and 4h. Rapid drops = MMs pulling liquidity → leading indicator.
- `DtM_bid_z = z-score of DtM_bid[5%] over trailing 168h`. Extreme negative = historically thin book = cascade-enabled.

**Mission A use:** filters out false cascades where price drops but book stayed thick.

### 2. Taker OFI (OFI-T) — cheapest high-signal metric

```
OFI-T(t, W) = (Σ buy_vol − Σ sell_vol) / (Σ buy_vol + Σ sell_vol)  over [t-W, t]
```
From `trades_1s` directly.

**Multi-timescale:** compute at W ∈ {10s, 1min, 5min, 30min, 4h}. Dispersion across timescales is information:
- All aligned < -0.3 → sustained directional pressure
- Short-W flipping while long-W stays − → accelerating capitulation

**Notional-weighted variant:** replace `buy_vol` with `buy_notional`. More stable for alts, better correlates with informed flow.

**Mission A use:** detects the selling **as it happens** (~15-55 min lead time on hourly candle print vs the original "BTC 1h < -2%" trigger).

### 3. Maker OFI (OFI-M) — the leading indicator

Classic OFI uses Δbid_qty and Δask_qty at top-of-book. We approximate at band level.

```
ΔBid_near = notional_bid[t, -1%] − notional_bid[t-1, -1%]
ΔAsk_near = notional_ask[t, +1%] − notional_ask[t-1, +1%]
OFI-M[-1%] = ΔBid_near − ΔAsk_near
```

Positive = bid-side growing faster → maker bullish. Negative = makers pulling bids → cascade enablement.

**Price-move adjustment:** if `|mid(t) − mid(t-1)| / mid(t-1) > 0.2%` between snapshots, treat as separate regime — don't compute Δ across the move.

**Multi-band aggregate:**
```
OFI-M_total = Σ weights[i] × OFI-M[band i]
weights = {1%: 0.5, 2%: 0.25, 3%: 0.15, 4%: 0.05, 5%: 0.05}
```

**Mission A use:** **This is THE leading indicator.** When OFI-M goes sharply negative (makers pulling bids) while OFI-T is still balanced (no taker selling yet) → informed accounts preparing for a dump. Cascade follows ~5-30 min later.

### 4. VWAP Dislocation Velocity (VDV)

```
VWAP_N(t) = Σ(price × notional) / Σ(notional)  over [t-N, t]
dev_N(t) = (close(t) − VWAP_N(t)) / VWAP_N(t)
VDV_N(t) = dev_N(t) − dev_N(t − k)
```
For N ∈ {5min, 30min, 4h}.

**Interpretation:**
- `dev_5min > +0.5%` with VDV > 0 → momentum up
- `dev_5min < -0.5%` with VDV < 0 → accelerating down-move, distribution phase
- `|dev_5min| > 1.5%` with VDV reversing sign → exhaustion, mean-reversion setup

**Mission A use:**
- **Cascade detection:** VDV_5min << 0 with rising volume = real cascade in progress (not chop)
- **Bounce entry:** VDV_5min crossing from negative → 0 during cascade = exhaustion point. More precise than "wait T+12h".
- **Exit timing:** replace fixed T+12h with "VDV_1h returns to 0" for alpha-preserving exits.

### 5. Liquidation Cluster Proxy (LCP) — speculative

Estimates where cascading liquidations would trigger based on OI dynamics.

**Theory:** When OI grows fast at a specific price range, positions are concentrated there. Longs at entry E with leverage L liquidate near `E × (1 − 1/L)`.

**Inputs:**
- OI time series (have: 5-min binance metrics parquets)
- VWAP during OI accumulation (have: trades_1s)
- Leverage distribution (estimate: 5× / 10× / 25× weighted {0.40, 0.40, 0.20})

**Algorithm (pseudocode):**
```python
for t in rolling_24h_windows:
    ΔOI = OI(t) − OI(t − 24h)
    entry_vwap = vwap(t-24h, t)
    for lev in [5, 10, 25]:
        liq_price_long[lev] = entry_vwap × (1 − 1/lev)
        cluster_size[lev] = ΔOI × weight[lev]
        accumulate(cluster_map, liq_price_long[lev], cluster_size[lev])
```

**Derived metrics:**
- `distance_to_nearest_long_cluster` — how far down price has to fall to hit a big liq wave
- `cluster_density_within_2%` — total liquidation fuel near current price

**Mission A use:** **Sizing input, not trigger.** When `distance_to_nearest_long_cluster < 2%` AND `DtM_bid_z < -1`, the cascade is primed for a large move — take bigger bounce positions. Cluster size estimates the expected cascade magnitude.

**Caveat:** LCP has big assumptions (leverage distribution isn't observable). Validate by: (a) aligning LCP clusters with known historical cascade events, (b) backtesting an LCP-alone strategy to see if it has any edge. Kill if validation fails.

### 6. Composite: Cascade Pressure Index (CPI)

```
CPI(t) = w1 × DtM_bid_z(t, 168h)              # thinness of bid side
       + w2 × (−DtM_asymmetry(t))              # skew toward thin bid
       + w3 × (−OFI-T_5min(t))                 # taker sell pressure
       + w4 × (−OFI-M_total(t))                # maker bid withdrawal
       + w5 × (−VDV_5min(t))                   # accelerating dislocation down
       + w6 × (−distance_to_long_cluster(t))   # liquidation fuel proximity

Initial weights: w = [0.15, 0.15, 0.25, 0.25, 0.10, 0.10]
Normalize each component via tanh(z-score / 2) → [-1, +1]
Normalize CPI → [-1, +1]
```

**Regime interpretation:**
- CPI > +0.7 → cascade-down risk very high
- CPI < -0.7 → squeeze-up risk very high
- |CPI| < 0.3 → quiet regime

Weights are a starting point — calibrate via IC test against forward returns and known cascade events.

## Mission A reframed

**Original:** "wait for BTC -3% hourly candle, then buy bounce"
**New:** continuous CPI monitoring, event-driven entry/exit.

### Entry (any 2 of 3)
1. CPI peaked above +0.7 in last 4h and is now reverting below +0.3 → cascade exhausted, bounce setup
2. VDV_5min crossed from < -0.5 back through 0 → mean-reversion kicking in
3. OFI-T_5min flipped from < -0.5 to > +0.2 → buyers stepping back in

### Execution
Equal-weight long top-20 liquid alts at trigger timestamp. Same as original.

### Exit
- `VDV_1h` returns to 0, OR
- 48h time stop (backstop), OR
- CPI drops below -0.3 (regime flipped to squeeze-up)

### Expected improvements
- Much lower latency trigger (seconds, not hours)
- Filters false cascades (thick-book drops)
- Smart exit based on reversion completion
- Likely 100+ events/year vs 30-60 in original

## Implementation priority

### Phase 1 — Cheapest, highest info (~2h agent)
1. OFI-T (trades_1s, ~20 lines)
2. DtM curves + asymmetry + z-scores (bookdepth, ~50 lines)
3. VDV (trades_1s rolling VWAP, ~30 lines)
**Deliverable:** `data/perp/binance/BTCUSDT/microstructure_1min.parquet` with all signals at 1min cadence.

### Phase 2 — Adds leading indicator (~1h)
4. OFI-M (bookdepth deltas with price-move adjustment, ~60 lines)

### Phase 3 — Speculative (~2h)
5. LCP (needs OI history merge, leverage sweep, validation)

### Phase 4 — Strategy test
6. Compute CPI over BTC 1y. Identify cascade events using new definition. Backtest long-alt bounce. Compare to original Mission A trigger event set.

## What this doesn't solve

1. **No multi-symbol orderbook beyond BTC/ETH/SOL.** For Mission A this is OK (alts follow BTC). For Mission D (novel indicators across 30 pairs) we'd need Mission F Phase 2.
2. **No inter-exchange depth imbalance** (CeFi-DeFi arb would need Bybit + OKX + Hyperliquid orderbooks; Tardis has paid historicals).
3. **33s bookdepth cadence** — real HFT informed flow can fire and complete in <1s. We see aftermath, not first cause.
4. **No tick-level distribution within 1s** — can't do whale-trade detection beyond 1s aggregates.

## Status
QUEUED as Mission H Phase 1 — signal builder. Mission A proper (with CPI triggers) is Phase 4 after signals validate.
