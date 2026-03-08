# Strategy Improvement Ideas — 2026-03-08

> Generated from online quant research across 30+ sources.
> Each idea is a testable hypothesis for the gate process.
> Status tracks progress through gates.

## Priority Order

| Rank | ID | Name | Class | Status | Gate | Strategy File |
|------|----|------|-------|--------|------|---------------|
| 1 | O1 | ATR-Based Position Sizing | Parity fix | DONE | deployed (engine parity) | run_paper_live.py |
| 2 | O2 | Regime-Dependent Sizing | Overlay | PASS Gate 5O | s34 = s11+O2+O3 (Sharpe +0.27, MaxDD +0.26pp) | s34_momentum_regime_sized.py |
| 3 | O4 | 15-min Defensive Stop Layer | Overlay | KILLED Gate 0 | No 15-min backtest data — untestable | — |
| 4 | O3 | Weekend Exposure Reduction | Overlay | PASS s11 / KILL s30 | s34 includes O3. KILLED on s30 (delta-neutral) | s34_momentum_regime_sized.py |
| 5 | O6 | Funding-Aware Carry Scaling | Overlay | KILLED Gate 5O | Neutral. Funding colinear w/ basis entry signal | s36 (killed) |
| 6 | S1 | Vol-Managed Leverage (s32) | Strategy mod | KILLED Gate 5O | Calmar -29%, return halved. Too aggressive. | s35 (killed) |
| 7 | O5 | Trailing Stop Progression | Overlay | KILLED Gate 0 (overlay) | Requires JIT engine changes, not a wrapper. Recycle as engine enhancement. | — |
| 8 | S2 | Dynamic Basis Thresholds (s30) | Strategy mod | KILLED Gate 0 (overlay) | Needs more-permissive entries; wrapper can only restrict. Recycle as Class A2. | — |
| 9 | N1 | Liquidity-Scaled Momentum | New strategy | KILLED Gate 0 | Engine already does ADV sizing/filtering. Recycle: ADV-momentum as signal lab test, liquidity exit as JIT feature. | — |
| 10 | N2 | ETF Flow Overlay | New strategy | BLOCKED Gate 0 | Needs ETF flow data feed infrastructure | — |

---

## O1: ATR-Based Position Sizing (Overlay)

**Class:** C (Overlay) — Gate path: 0 → 2 → 3O → 5O → 6 → 7
**Applies to:** All strategies (s30, s32, s11, s29)
**Base strategies:** All Tier A/B

**Hypothesis:** Sizing positions inversely with ATR (instead of flat equal-weight)
reduces drawdowns and improves Calmar because high-vol tokens get smaller positions
and low-vol tokens get proportionally larger ones, equalizing risk contribution.

**Mechanism:**
```
size_per_trade = (equity * risk_pct) / (k * ATR_14)
```
Where `risk_pct` = 1-2% of equity, `k` = stop distance in ATR units (typically 2.5-3.0).
This ensures each trade risks the same dollar amount regardless of token volatility.

**Economic rationale:** Standard quant practice (Turtle Traders, AQR, Winton).
High-vol tokens like meme coins get smaller positions, preventing outsized losses.
Low-vol tokens like BTC get larger positions, improving capital efficiency.

**Expected impact:** DD reduction 20-40%, Calmar improvement, Sharpe roughly neutral.
**Implementation difficulty:** Low — single function change in position sizer.
**Score: 8/10**

**Research sources:**
- QuantStrategy.io: Kelly Criterion practical implementation
- Medium: Position Sizing for Algo Traders (comprehensive guide)
- QuantPedia: Risk Parity Asset Allocation

---

## O2: Regime-Dependent Sizing (Overlay)

**Class:** C (Overlay) — Gate path: 0 → 2 → 3O → 5O → 6 → 7
**Applies to:** All strategies
**Base strategies:** All Tier A/B

**Hypothesis:** Scaling exposure by BTC regime (full in RANGE/UPTREND, half in
DOWNTREND, zero in CRISIS) improves risk-adjusted returns because drawdowns
concentrate in adverse regimes.

**Mechanism:**
```python
REGIME_WEIGHTS = {
    0: 0.0,   # CRISIS — no exposure
    1: 0.75,  # QUIET — reduced
    2: 1.0,   # UPTREND — full
    3: 1.0,   # RANGE — full
    4: 0.5,   # DOWNTREND — half
}
size *= REGIME_WEIGHTS[current_regime]
```

**Economic rationale:** Already proven in our system — regime weighting overlay
improved Sharpe by +0.29 and DD by +4.5pp on s11+s09 (see regime_analysis.py results).
This extends it from a backtest overlay to live position sizing.

**Expected impact:** Sharpe +0.2-0.3, MaxDD improvement 3-5pp.
**Implementation difficulty:** Low — we already have regime detection.
**Score: 8/10**

**Research sources:**
- Springer (2024): Regime Switching Forecasting for Crypto
- Asian Journal (2025): HMMs outperform other models for crypto regime shifts
- arXiv (2025): AdaptiveTrend framework — regime-adaptive achieved Sharpe 2.41

---

## O3: Weekend Exposure Reduction (Overlay)

**Class:** C (Overlay) — Gate path: 0 → 2 → 3O → 5O → 6 → 7
**Applies to:** All strategies
**Base strategies:** All Tier A/B

**Hypothesis:** Reducing position size by 30-50% from Friday 20:00 UTC to Sunday
20:00 UTC reduces tail risk because weekend liquidity is 42% lower, flash crashes
cluster on weekends (Feb 2026 BTC -23k, Oct 2025 cascade), and order book depth
drops significantly.

**Mechanism:**
```python
is_weekend = (current_hour.weekday() >= 4 and current_hour.hour >= 20) or \
             (current_hour.weekday() in [5, 6]) or \
             (current_hour.weekday() == 0 and current_hour.hour < 4)
size *= 0.5 if is_weekend else 1.0
```

**Economic rationale:** BTC weekend trading share dropped from 24% (2018) to 13%
(2024). Order book depth at 21:00 UTC is 42% lower than 11:00 UTC. Both the
Feb 2026 and Oct 2025 crashes accelerated on weekends.

**Expected impact:** Tail risk reduction, slight drag on returns from missed weekend moves.
**Implementation difficulty:** Low — time-based multiplier.
**Score: 7/10**

**Research sources:**
- Kaiko Research: Where Did Weekend Crypto Traders Go?
- Amberdata: The Rhythm of Liquidity — Temporal Patterns in Market Depth
- CoinDesk: February 2026 Weekend Crash Analysis

---

## O4: Multi-Timeframe Defensive Stop Layer (Overlay)

**Class:** C (Overlay) — Gate path: 0 → 2 → 3O → 5O → 6 → 7
**Applies to:** All strategies (most impactful for s32 directional trades)
**Base strategies:** All Tier A/B

**Hypothesis:** Adding a 15-minute Chandelier Exit as a defensive layer below the
1H signal stop reduces max drawdown from intra-hour flash crashes without
increasing false stops significantly.

**Mechanism:**
- Keep 1H candles for entry/exit signals (unchanged)
- Add 15-min price monitor between ticks
- Defensive triggers:
  1. If price drops >3% in <15 minutes → exit 50% of position immediately
  2. Chandelier Exit: stop = HH(22 bars on 15m) - 3*ATR_15min(22)
  3. If portfolio drawdown hits -5% intraday → flatten all
- Only applies BETWEEN hourly ticks (doesn't interfere with signal logic)

**Economic rationale:** Oct 2025 crash liquidated $19B in hours. BTC fell 14% in
one session. Flash crashes can wipe weeks of carry. Server-side exchange stops
gap through in thin liquidity.

**Expected impact:** MaxDD reduction 30-50%, may reduce total returns slightly.
**Implementation difficulty:** Medium — requires 15-min data feed (we have live fetcher).
**Score: 7/10**

**Research sources:**
- Inside the $19B Flash Crash (insights4vc)
- crypto.news: Circuit Breakers Proposal (FinYX framework)
- LuxAlgo: ATR Stop-Loss Strategies for Risk Control

---

## O5: Trailing Stop Progression (Overlay)

**Class:** C (Overlay) — Gate path: 0 → 2 → 3O → 5O → 6 → 7
**Applies to:** All strategies
**Base strategies:** All Tier A/B

**Hypothesis:** Tightening trailing stops as profit grows locks in more profits on
winning trades without cutting winners early.

**Mechanism:**
```python
# Current: flat trail_mult = 2.5 throughout trade
# Proposed: progressive tightening
if unrealized_pnl_atr < 1.0:
    trail_mult = 2.5  # Stage 1: entry
elif unrealized_pnl_atr < 2.0:
    trail_mult = 2.0  # Stage 2: in profit
else:
    trail_mult = 1.5  # Stage 3: big winner — lock it in
```

**Economic rationale:** Standard trend-following practice. Winners that reach 2x ATR
profit are likely to give back a significant portion. Tighter stops at higher profits
capture more of the move.

**Expected impact:** Modest improvement in average win size, slight increase in stops hit.
**Implementation difficulty:** Low — modify trail_mult calculation in engine.
**Score: 6/10**

**Research sources:**
- ChartSWatcher: Advanced Stop-Loss Strategies 2025
- Medium: ChandelierExit-EMA Dynamic Stop-Loss Strategy

---

## O6: Funding-Aware Carry Scaling (s30 specific)

**Class:** C (Overlay) — Gate path: 0 → 2 → 3O → 5O → 6 → 7
**Applies to:** s30 basis carry only
**Base strategy:** s30 basis carry

**Hypothesis:** Scaling s30 carry position size by funding rate magnitude improves
returns because it concentrates capital when carry opportunity is richest and
reduces exposure when carry is thin.

**Mechanism:**
```python
funding_zscore = (current_funding - rolling_mean_30d) / rolling_std_30d
size_mult = clip(0.5 + 0.5 * abs(funding_zscore), 0.5, 2.0)
# Half position at z=0 (no carry edge), 2x at z>=3 (extreme carry)
```

**Economic rationale:** Ethena's USDe ($10.5B TVL) uses dynamic allocation —
full carry when funding is high, stablecoins when funding is low/negative.
BitMEX data: positive funding in 71.4% of all periods, but magnitude varies 10x.

**Expected impact:** Moderate return improvement, concentrated in high-funding periods.
**Implementation difficulty:** Low — rolling z-score of funding rate.
**Score: 7/10**

**Research sources:**
- Ethena Docs: USDe Overview (dynamic carry allocation)
- BitMEX Blog: 9 Years of XBTUSD Funding Rate Analysis
- SSRN (Inan, 2025): Predictability of Perpetual Futures Funding Rates

---

## S1: Volatility-Managed Leverage for s32 (Strategy Mod)

**Class:** A2 (Combined) — Gate path: 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7
**Applies to:** s32 regime_spot_perp
**Note:** s33 attempted Moreira-Muir but failed due to overtrading. This applies
the same concept to s32's proven signals (78.9% validation rate).

**Hypothesis:** Scaling s32's leverage inversely with 20-day realized vol improves
Sharpe because crypto exhibits an inverse leverage effect — low-vol periods
precede big moves, so increasing exposure in calm markets captures breakouts.

**Mechanism:**
```python
realized_vol = ewma_std(returns, span=20) * sqrt(8760)  # annualized
leverage = min(target_vol / realized_vol, max_leverage)
# target_vol = 0.30 (30% ann), max_leverage = 3.0
```

**Economic rationale:** Moreira-Muir (JF 2017) proved vol-managed portfolios produce
higher Sharpe across all asset classes. Vest Financial's BTCVX fund implements
this for BTC. Key: apply to proven signals, not new signals (s33's mistake).

**Expected impact:** Sharpe +0.3-0.5, but needs careful max_leverage cap.
**Implementation difficulty:** Medium — modify s32 strategy code + engine leverage support.
**Score: 7/10**

**Research sources:**
- Moreira & Muir (JF 2017): Volatility-Managed Portfolios
- arXiv (2024): Crypto Volatility Comparison (inverse leverage effect)
- Vest Financial BTCVX: Real-world implementation
- FTI Consulting: October 2025 Crash ($28B liquidated, leverage risks)

---

## S2: Dynamic Basis Entry Thresholds for s30 (Strategy Mod)

**Class:** A2 (Combined) — Gate path: 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7
**Applies to:** s30 basis carry

**Hypothesis:** Using regime-dependent basis z-score thresholds for entry (tighter
in RANGE when basis is more mean-reverting, wider in DOWNTREND when basis is
more volatile) improves trade selection quality.

**Mechanism:**
```python
ENTRY_THRESHOLDS = {
    'RANGE': 1.0,      # Basis mean-reverts faster in range
    'UPTREND': 1.5,    # Standard
    'QUIET': 1.0,      # Similar to range
    'DOWNTREND': 2.0,  # Wider — basis is noisier
    'CRISIS': 999,     # No entry
}
entry = basis_zscore > ENTRY_THRESHOLDS[regime]
```

**Economic rationale:** BIS research (WP 1087) shows crypto carry varies significantly
by market regime. Post-ETF, basis trade profitability decreased in bull markets
but remained in range/sideways. Regime-adaptive thresholds capture this.

**Expected impact:** Moderate — better trade selection, fewer losing entries in volatile regimes.
**Implementation difficulty:** Low — parameter table in strategy code.
**Score: 6/10**

**Research sources:**
- BIS Working Paper 1087: Crypto Carry
- CEPR: Crypto Carry Market Segmentation
- CF Benchmarks: Bitcoin Basis Analysis (regime dynamics)

---

## N1: Liquidity-Scaled Momentum (New Strategy)

**Class:** A (Per-Token) — Gate path: 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7
**Next strategy number:** s34

**Hypothesis:** Momentum signals weighted by ADV/liquidity produce better entries
because signals in liquid markets execute cleanly while signals in thin markets
gap through stops. Post-2024, altcoin rallies last only 19 days (vs 61 in 2024),
so liquidity-aware sizing is critical.

**Mechanism:**
```python
# Scale position by liquidity (ADV)
adv_ratio = min(token_adv / target_adv, 1.0)  # 0 to 1
entry_mask = momentum_signal & (adv_ratio > 0.3)  # min liquidity gate
size = base_size * adv_ratio  # scale by liquidity
# Exit if ADV drops below 50% of entry ADV (liquidity withdrawal)
```

**Economic rationale:** Grayscale (2026): liquidity concentrating in majors.
Wintermute OTC: average trade sizes rising 17% as institutional blocks grow.
Thin-tail altcoins increasingly illiquid for swing trading.

**Expected impact:** Better execution, fewer gaps through stops on illiquid tokens.
**Implementation difficulty:** Medium — ADV data available in our universe.py.
**Score: 6/10**

---

## N2: ETF Flow Momentum Overlay (New Strategy/Overlay)

**Class:** C (Overlay) or A (standalone for BTC) — flexible
**Next strategy number:** s35 if standalone

**Hypothesis:** BTC ETF inflows/outflows are a leading indicator for crypto momentum.
3-day cumulative inflows >$500M = bullish bias, outflows >$500M = bearish bias.
ETFs now purchase >100% of annual net BTC issuance.

**Mechanism:**
```python
etf_flow_3d = sum(daily_etf_flows[-3:])  # in millions USD
if etf_flow_3d > 500:
    directional_bias = +1  # bullish
elif etf_flow_3d < -500:
    directional_bias = -1  # bearish
else:
    directional_bias = 0  # neutral
# Use as filter on existing momentum strategies
```

**Economic rationale:** BlackRock IBIT controls ~48.5% of BTC ETF market ($50B AUM).
2,000+ US advisory firms now allocate. ETF flows represent marginal buyer/seller.
But may already be priced in by HFT firms.

**Expected impact:** Low-medium. BTC-only. Requires external data feed.
**Implementation difficulty:** High — need daily ETF flow data source.
**Score: 5/10**

---

## Research Sources (Full List)

### Position Sizing
- QuantStrategy.io: Kelly Criterion (quantstrategy.io)
- Medium: Position Sizing for Algo Traders (comprehensive guide)
- QuantPedia: Risk Parity Asset Allocation

### Leverage
- Moreira & Muir (JF 2017): Volatility-Managed Portfolios
- arXiv (2024): Crypto Volatility Comparison
- FTI Consulting: October 2025 Crash Analysis
- Vest Financial BTCVX: Real-world vol-managed BTC fund
- arXiv (2025): AdaptiveTrend — Sharpe 2.41, DD -12.7%

### Weekend Effects
- Kaiko Research: Weekend crypto volume decline (24% → 13%)
- Amberdata: Order book depth temporal patterns
- ACR Journal (2025): Weekend momentum premium in crypto
- ScienceDirect: Crypto-stock weekend predictive signal
- CoinDesk: February 2026 weekend crash

### Intra-Hour Risk
- insights4vc: Inside the $19B Flash Crash (Oct 2025)
- crypto.news: Circuit breaker framework proposal
- CoinDesk: Post-crash liquidity hollowing

### Stop Losses
- LuxAlgo: ATR stop-loss strategies
- Medium: ChandelierExit-EMA strategy
- ChartSWatcher: Advanced stop strategies 2025
- QuantPedia: Multi-timeframe trend on BTC
- BitMEX Blog: 9 years of funding rate data

### Basis/Carry
- BIS WP 1087: Crypto carry trade economics
- CEPR: Carry market segmentation post-ETF
- SSRN (Inan, 2025): Funding rate predictability
- CF Benchmarks: Bitcoin basis regime dynamics
- Ethena Docs: Dynamic carry allocation (USDe)
- Coin Metrics: Ethena's market impact

### Regime
- Springer (2024): Regime switching forecasting
- Asian Journal (2025): HMMs for crypto regimes
- Amberdata: Volatility framework (low vol → crash risk)

### Microstructure
- Grayscale: 2026 Digital Asset Outlook
- Wintermute: OTC Markets 2025, OTC 2024 Review
- CoinDesk: Post-crash liquidity analysis
