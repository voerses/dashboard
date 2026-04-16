# Profit Lock-in Overlay — Final Validation Report

_Generated 2026-04-07 — resolves Mission G after the autopsy's A-HALFLOCK rescue claim_

## TL;DR — Verdict: **DEAD**

All five action variants (A-FULL, A-HALFLOCK, A-25, A-50, A-75) lose money on
the full 60-month s523c walk-forward at **every** slippage level, including
**0 bps**. The autopsy's headline result for A-HALFLOCK (+$53,633 / Calmar
18.26 / 17-of-17 positive windows) was a **recompute artifact**: the autopsy
used a buggy A-HALFLOCK implementation that simply added `max(0, 0.5*lock −
realized) × notional` to PnL without ever simulating the new stop walking
forward. Under correct stop semantics — raise stop, walk bars, exit only if a
later low touches the new level — A-HALFLOCK loses **$41,296** at 0 bps and
**$46,048** at 50 bps. Mission G is definitively killed.

## Methodology fix

The autopsy's `overlay_one_action` for `"A-HALFLOCK"`:

```python
floor_realized = 0.5 * lock
if realized >= floor_realized:
    return base_pnl, True, fire_bar           # no $ effect
new_pnl = base_pnl + margin * lev * (floor_realized - realized)
```

This is **structurally non-decreasing per trade**: it can only add positive
PnL (when realized < 0.5*lock) and never subtracts. It is not a stop — it is a
guaranteed minimum on the realized return. That guaranteed-minimum
construction is unrealizable in a real exit handler.

The correct semantics (matching Gate 1's `simulate_overlay`) used here:

```python
def walk_forward(stop_level, trail_atr_mult=None):
    running_fav = lock
    for j in range(fire_bar + 1, len(fav_hi)):
        if fav_hi[j] > running_fav:
            running_fav = fav_hi[j]
        sl = stop_level
        if trail_atr_mult is not None:
            sl = max(sl, running_fav - trail_atr_mult * atr_pct)
        if adv[j] <= sl:
            return sl, True       # stopped at the new level
    return realized, False        # original exit fires unchanged
```

For A-HALFLOCK: `stop_level = 0.5 * lock`. Critical asymmetry — if a trade's
original exit was at +30% (lock = 20%, half-lock = 10%) and the price walked
through 10% on a wick before reaching the original exit, A-HALFLOCK exits
early at 10%, **costing 20pp**. This is the missing "stop hurts" path.

Fill model: limit-at-threshold (`lock = min(vth*(fire+1), fav_hi[fire])`),
identical to the verification harness. Walk-forward expanding-window VEL95
threshold (no leakage). 511 of 1513 trades enrich (same rate as the autopsy
— enrichment requires `leverage_inferred` in [0.5, 50] and OHLC coverage).
Baseline on enriched 511 trades: Return 175.07%, MaxDD −24.23%, Calmar 7.23.

## Test 1 — Slippage sensitivity (A-HALFLOCK, full 60mo, walk-forward VEL95, limit fill)

| Slippage | Return | MaxDD | Calmar | ΔCalmar % | Δ$ vs baseline | Fired | Stop-Mod |
|---:|---:|---:|---:|---:|---:|---:|---:|
| **baseline** | 175.07% | −24.23% | **7.23** | — | — | — | — |
| 0 bps | 92.48% | −33.11% | 2.79 | −61.3% | **−$41,296** | 164 | 161 |
| 25 bps | 87.73% | −34.22% | 2.56 | −64.5% | −$43,672 | 164 | 161 |
| 50 bps | 82.98% | −35.37% | 2.35 | −67.5% | −$46,048 | 164 | 161 |
| 100 bps | 73.47% | −37.78% | 1.94 | −73.1% | −$50,799 | 164 | 161 |

**Pass criterion (≥+15% Calmar @ 50 bps):** **FAIL** — Calmar is −67.5%
(absolute drop from 7.23 → 2.35). The 0-bps result alone (already −61%)
confirms the autopsy was a methodology artifact, not a slippage problem.

164 of 511 trades fire the velocity trigger; 161 of those exit via the
modified stop. The stop-out drives losses because s523c's winners often
keep running past the half-lock level — the original-exit path captures
that, the half-lock stop does not.

## Test 2 — Data horizon restriction (1mo step rolling, A-HALFLOCK @ 50 bps)

50 windows total, 28 with `window_start ≥ 2023-06-01`.

| Window set | n | Mean ΔCalmar | Median ΔCalmar | Pos-$ windows | Mean Δ$ |
|---|---:|---:|---:|---:|---:|
| Unfiltered | 50 | +11.8% | −14.9% | 19 / 50 (38%) | −$3,923 |
| Filtered (≥2023-06) | 28 | +26.3% | **−2.5%** | **6 / 28 (21%)** | −$7,427 |

**Pass criterion (median ΔCalmar > 0 AND ≥80% positive-$ windows):** **FAIL**
— median ΔCalmar is negative (−2.5%) on the filtered set and only 21% of
windows are positive in dollars. The +26.3% mean is dragged up by a small
number of fat-tail windows where the half-lock happened to dodge a drawdown;
the median tells the real story.

The horizon restriction did NOT materially improve the result. Filtering out
the thin-universe early period actually made the positive-$ rate **worse**
(38% → 21%), because the early windows had very few trades and few stop-outs.
Removing them concentrated the sample on regimes where the modified stop
actively bites.

## Test 3 — All variants @ 50 bps slippage (full 60mo walk-forward)

| Action | Return | MaxDD | Calmar | ΔCalmar % | Δ$ vs baseline | Fired | Stop-Mod |
|---|---:|---:|---:|---:|---:|---:|---:|
| **baseline** | 175.07% | −24.23% | **7.23** | — | — | — | — |
| A-FULL | 87.08% | −33.74% | 2.58 | −64.3% | −$43,998 | 164 | 164 |
| A-HALFLOCK | 82.98% | −35.37% | 2.35 | −67.5% | −$46,048 | 164 | 161 |
| A-25 | 69.47% | −37.76% | 1.84 | −74.5% | −$52,801 | 164 | 164 |
| A-50 | 79.15% | −34.35% | 2.30 | −68.1% | −$47,959 | 164 | 160 |
| **A-75** (best) | 90.89% | −32.50% | **2.80** | −61.3% | −$42,093 | 164 | 162 |

**Pass criterion (≥1 variant passes Tests 1+2):** **FAIL** — every single
variant cuts Calmar by more than half. The ranking is essentially identical
across slippage levels and the differences between variants (A-75 best,
A-25 worst) are second-order to the fundamental problem: the velocity
trigger fires on trades that **subsequently keep running**, so any action
that locks in early gives back upside.

A-75 is the least-bad variant (−61.3% Calmar) because it preserves 75% of
the lock-in fill (limit-priced at the threshold) and only puts 25% at risk
of a downstream stop-out. But least-bad is still bad.

### Test 2b — runner-up A-75 on filtered windows @ 50 bps

| Window set | n | Mean ΔCalmar | Median ΔCalmar | Pos-$ windows | Mean Δ$ |
|---|---:|---:|---:|---:|---:|
| A-75, filtered (≥2023-06) | 28 | +29.2% | −7.5% | 8 / 28 (29%) | −$6,336 |

Same pattern: positive mean from a few outliers, negative median, ~30%
positive-$ rate. Fails the same way.

## Why the autopsy was wrong (root cause)

The autopsy's A-HALFLOCK loop never simulated walking forward from the
trigger bar to check whether the new stop got hit before the original exit.
It assumed `realized` was a free variable that could be replaced by
`max(realized, 0.5*lock)`. In a real exit handler, raising the stop **forces
an earlier exit** if the price subsequently revisits that level, and that
earlier exit can be lower than what the original handler would have produced.

For s523c specifically, the velocity trigger fires on **fast continuation
moves** — and continuation moves frequently retrace through the half-lock
level on intrabar wicks before continuing higher. A real stop catches those
wicks and exits at the lock; the autopsy's max-clamp version pretends the
wick didn't matter.

The 17-of-17 positive-$ windows in the autopsy reflected the math of
`max(a, b) ≥ a`, not market behavior.

## Final verdict and recommendation

**Mission G is DEAD.** No more rescue attempts.

- The original Gate 2 kill (which tested A-FULL only) was correct in
  spirit — the velocity trigger does not have edge — but the autopsy
  misidentified the fix.
- The autopsy's A-HALFLOCK rescue was a methodology artifact, not a real
  edge. Reverting to correct stop semantics destroys the result at zero
  slippage, before any execution friction is even applied.
- Every variant (A-FULL, A-25, A-50, A-75, A-HALFLOCK) fails the 50-bps
  Calmar gate by 60-75%. The data-horizon restriction does not help.
- Recommendation: **do not build Gate 3 prototype**. Close Mission G.
  Document this report as the final word so the question doesn't get
  re-opened. The s523c trade log is what it is — its outsized winners are
  load-bearing for the strategy's Calmar, and any premature-lock overlay
  will trade away the right tail for noise reduction at unfavorable terms.

If a future researcher wants to revisit profit-lock-in for s523c, the
correct angle is **not** "lock earlier" — it is "exit later when the trail
already covered the entry" (asymmetric trail tightening only after the
trade has banked enough margin to absorb a normal pullback). That is a
different hypothesis and would need a fresh Gate 1 study.

## Files

- `research/profit_lockin_final_validation.py` — this experiment's harness
- `research/profit_lockin_final_validation_results.json` — raw numbers
- `research/profit_lockin_autopsy.py` — prior autopsy with the buggy halflock
- `research/profit_lockin_gate1.py` — origin of correct stop semantics
- `research/gate2_out/s523c_growth_60mo_50k_trades.json` — trade log (unchanged)
