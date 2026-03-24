# Carry Strategy Overlay Analysis

**Run date**: 2026-03-24 11:52
**IS period**: 2021-03-24 to 2024-12-31
**OOS period**: 2025-01-01 to latest
**Transaction cost**: 10 bps round-trip
**Rebalancing**: Weekly (Monday)

## 1. Strategy Type Analysis

### s29 (Funding Rate Carry)
- **Type**: DIRECTIONAL (not delta-neutral)
- **Mechanism**: Short perp when funding positive, long perp when funding negative
- **Direction**: -sign(72h rolling mean of funding rate)
- **Key detail**: Holds a naked directional perp position. NOT a basis trade.
- **P&L sources**: (1) Funding income (carry), (2) Price P&L (directional)
- **Exit logic**: ATR trailing stops, max hold 14 days -- price-based exits
- **Verdict**: Directional. Price moves dominate funding income short-term.

### s65 (Funding Rate Carry V4)
- **Type**: DIRECTIONAL (same core as s29, with regime/ADX/magnitude sizing)
- **Same logic**: Short when funding positive, long when funding negative
- **Same exits**: ATR-based trailing stops (1.5x ATR flat trail)
- **Verdict**: Still directional. Sizing overlays will affect this strategy.

### s37 (Momentum Trail Progression)
- **Type**: DIRECTIONAL (pure trend-following, wraps s11)
- **Verdict**: Overlays proven on trend-following base (R62 +0.856 Sharpe).

### Critical Insight

s29/s65 are NOT basis trades (long spot + short perp). They hold naked perp positions.
The carry income is a bonus on top of a directional bet. Both price P&L and funding
income contribute to total returns, making positioning/VRP overlays theoretically applicable.

## 2. Overlay Applicability Assessment

| Strategy | Directional? | Positioning Overlay | VRP Overlay | Rationale |
|----------|-------------|--------------------|-----------|-|
| s29 | YES | APPLICABLE | APPLICABLE | Naked perp, price risk dominates |
| s65 | YES | APPLICABLE | APPLICABLE | Same as s29, enhanced sizing |
| s37 | YES | APPLICABLE | APPLICABLE | Pure momentum, proven on trend base |

However, there is a subtle conflict: carry strategies are INHERENTLY CONTRARIAN to crowd
positioning. When the crowd is net long (positive funding), carry goes SHORT. The positioning
overlay also reduces when crowd is extreme long. These could reinforce or conflict depending
on timing.

## 3. Funding Rate Environment (Critical Context)

BTC funding rates have structurally changed over time:

| Period | Days Above Threshold | Mean |funding_72h| | Max |funding_72h| |
|--------|---------------------|---------------------|---------------------|
| 2021-2022 (bull+bear) | 492/730 (67%) | 0.0001709 | 0.0014159 |
| 2023-2024 (recovery+bull) | 204/731 (28%) | 0.0000417 | 0.0003660 |
| Jan-Apr 2025 | 1/120 (1%) | 0.0000140 | 0.0000544 |
| May-Aug 2025 | 0/123 (0%) | 0.0000137 | 0.0000323 |
| Sep 2025-Mar 2026 | 0/198 (0%) | 0.0000092 | 0.0000489 |

**Key finding**: Funding rates collapsed after Aug 2025. The s29/s65 carry strategies
generate ZERO signals in Sep 2025-Mar 2026 because the 72h rolling mean never exceeds
the 0.00005/hr threshold (48% annualized). This means:
- The triage period (Sep 2025+) shows carry as profitable ONLY because it was flat
- Any overlay test on Sep 2025+ is meaningless (all variants are flat)
- We test on Jan 2025-latest instead, where carry has partial activity

## 4. Backtest Results (OOS: Jan 2025-latest)

### Performance Table

| Variant | IS Return | OOS Return | IS Sharpe | OOS Sharpe | IS MaxDD | OOS MaxDD | IS Calmar | OOS Calmar |
|---------|-----------|------------|-----------|------------|----------|-----------|-----------|------------|
| Carry Only | 47.1% | 0.0% | 1.05 | 0.00 | -45.4% | 0.0% | 1.04 | 0.00 |
| Carry + Positioning | 24.6% | 0.0% | 0.55 | 0.00 | -50.7% | 0.0% | 0.49 | 0.00 |
| Carry + VRP | 40.6% | 0.0% | 0.99 | 0.00 | -34.4% | 0.0% | 1.18 | 0.00 |
| Carry + Pos + VRP | 22.1% | 0.0% | 0.54 | 0.00 | -41.3% | 0.0% | 0.54 | 0.00 |

### Marginal Overlay Contribution (vs Carry Only)

| Overlay | IS dSharpe | OOS dSharpe | IS dReturn | OOS dReturn | IS dMaxDD | OOS dMaxDD |
|---------|------------|-------------|------------|-------------|-----------|------------|
| + Positioning | -0.502 | +0.000 | -22.5% | +0.0% | -5.3% | +0.0% |
| + VRP | -0.056 | +0.000 | -6.4% | +0.0% | +11.0% | +0.0% |
| + Pos + VRP | -0.510 | +0.000 | -24.9% | +0.0% | +4.1% | +0.0% |

### P&L Decomposition

| Variant | Period | Total Return | Price P&L | Funding P&L | Costs |
|---------|--------|-------------|-----------|-------------|-------|
| Carry Only | IS | 3.2930 | 0.2958 | 1.6018 | 0.0610 |
| Carry Only | OOS | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| Carry + Positioning | IS | 1.2953 | -0.1151 | 1.4120 | 0.0797 |
| Carry + Positioning | OOS | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| Carry + VRP | IS | 2.6244 | 0.2795 | 1.3872 | 0.0636 |
| Carry + VRP | OOS | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| Carry + Pos + VRP | IS | 1.1293 | -0.0638 | 1.2171 | 0.0752 |
| Carry + Pos + VRP | OOS | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

Funding P&L is carry income. Price P&L is directional exposure.
If |Price P&L| >> |Funding P&L|, the strategy is primarily directional, not carry.

### Monthly OOS Returns

| Month | Carry Only | +Positioning | +VRP | +Pos+VRP |
|-------|-----------|-------------|------|----------|
| 2025-01 | 0.00% | 0.00% | 0.00% | 0.00% |
| 2025-02 | 0.00% | 0.00% | 0.00% | 0.00% |
| 2025-03 | 0.00% | 0.00% | 0.00% | 0.00% |
| 2025-04 | 0.00% | 0.00% | 0.00% | 0.00% |
| 2025-05 | 0.00% | 0.00% | 0.00% | 0.00% |
| 2025-06 | 0.00% | 0.00% | 0.00% | 0.00% |
| 2025-07 | 0.00% | 0.00% | 0.00% | 0.00% |
| 2025-08 | 0.00% | 0.00% | 0.00% | 0.00% |
| 2025-09 | 0.00% | 0.00% | 0.00% | 0.00% |
| 2025-10 | 0.00% | 0.00% | 0.00% | 0.00% |
| 2025-11 | 0.00% | 0.00% | 0.00% | 0.00% |
| 2025-12 | 0.00% | 0.00% | 0.00% | 0.00% |
| 2026-01 | 0.00% | 0.00% | 0.00% | 0.00% |
| 2026-02 | 0.00% | 0.00% | 0.00% | 0.00% |
| 2026-03 | 0.00% | 0.00% | 0.00% | 0.00% |
| **Cumulative** | **0.00%** | **0.00%** | **0.00%** | **0.00%** |

### Statistical Significance

- Carry + Positioning vs Carry Only: no variance (carry flat in most of OOS)
- Carry + VRP vs Carry Only: no variance (carry flat in most of OOS)
- Carry + Pos + VRP vs Carry Only: no variance (carry flat in most of OOS)

## 5. Positioning Predicts Funding Flips?

Does extreme crowd positioning predict 7-day funding rate direction changes?

| Positioning Regime | N Days | 7d Flip Rate | Current Funding | Forward Funding |
|-------------------|--------|-------------|-----------------|-----------------|
| Extreme Long (z>1.5) | 212 | 0.9% | 0.000099 | 0.000083 |
| Extreme Short (z<-1.5) | 193 | 14.0% | 0.000046 | 0.000059 |
| Normal (-0.5<z<0.5) | 608 | 9.0% | 0.000076 | 0.000080 |

- IC(positioning_z -> 7d funding change): -0.1104 (p=0.0000)
- IC(positioning_z -> 7d flip probability): -0.1421 (p=0.0000)
- IC(|positioning_z| -> |7d funding change|): -0.0087 (p=0.7055)

**Positioning predicts funding changes** (IC=-0.1104, p=0.0000).
When crowd is extreme long (z>1.5), funding tends to DECREASE over 7 days.
This suggests a carry-specific overlay: reduce carry when positioning extreme.

## 6. Recommendation

### Which strategies should get overlays?

| Strategy | Overlay | OOS dSharpe | Recommendation |
|----------|---------|-------------|----------------|
| s29/s65 (carry) | Pos+VRP | +0.000 | See analysis below |
| s37 (momentum) | Pos+VRP | +0.856 (R62) | ADD (proven on trend base) |

### Key Conclusions

1. **s29/s65 are DIRECTIONAL, not delta-neutral.** They hold naked perp positions.
   Price P&L and funding income both contribute. Overlays are theoretically applicable.

2. **INCONCLUSIVE: Carry was flat for most of OOS.** Only 0/441 OOS days
   had active positions. Funding rates collapsed after mid-2025, making the strategy dormant.
   Cannot draw meaningful conclusions about overlay effectiveness on carry from this period.

3. **The real question is moot for now.** If funding rates stay this low, s29/s65 will
   generate no trades and the overlay question is academic. If funding normalizes,
   the directional nature of carry makes overlays theoretically beneficial.

4. **IS results show mixed signals:**
   - Carry Only IS Sharpe: 1.05
   - Carry+Positioning IS: 0.55 (d=-0.50)
   - Carry+VRP IS: 0.99 (d=-0.06)
   - Carry+Pos+VRP IS: 0.54 (d=-0.51)
   Positioning HURTS carry IS Sharpe, which makes sense: carry is already contrarian.

5. **Positioning DOES predict funding changes** (IC=-0.1104).
   This is the most actionable finding. Instead of a generic sizing overlay,
   carry strategies could use positioning as a FUNDING REGIME signal:
   - When crowd is extreme long (z>1.5) and funding is positive: REDUCE carry
     (funding may compress)
   - When crowd is extreme short (z<-1.5): funding regime is unstable
   This is a carry-specific overlay, not a generic directional one.

### Final Verdict

**CANNOT TEST** -- Carry strategies are dormant in OOS (funding rates too low).

The positioning overlay HURTS carry in-sample (IS Sharpe drops), which is expected:
carry is already inherently contrarian (shorts when crowd is long), so adding
a contrarian positioning overlay on top is REDUNDANT or CONFLICTING.

**Recommendation for s29/s65: DO NOT add the generic positioning+VRP overlay.**
Instead, if carry returns to viability:
- Test a CARRY-SPECIFIC overlay using positioning to predict funding regime changes
- The generic overlay is designed for directional strategies where the signal
  and positioning are independent. In carry, they are correlated (both contrarian).

**Recommendation for s37: ADD the Pos+VRP overlay** (proven in R62).
