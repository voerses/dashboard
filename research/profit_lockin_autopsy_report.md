# Profit Lock-in Overlay — Gate 2 Kill Autopsy

_Generated 2026-04-07 — auditing the Mission G kill against 8 specific methodology issues._

**Verdict: KILL_PREMATURE.** Gate 2 killed the overlay using only the
A-FULL action variant. A-FULL is genuinely dead. But **A-HALFLOCK
(asymmetric floor — lock half of unrealized profit) is robustly positive
on the same data, the same fill model, and the same walk-forward
threshold**. Across 17 rolling windows, A-HALFLOCK is positive in
**17/17** in absolute dollars, mean +$6,148/window. On the full 60-month
sample it adds **+$53,633** (+107pp return), improves Calmar 7.23 → 18.26
and shrinks MaxDD from -24.2% to -15.5%.

The Gate 2 verdict was correct *for the action it tested*, but the
methodology missed the asymmetric variant that the original Gate 1 sweep
flagged as one of five candidates.

---

## Headline numbers (full 60-month sample, walk-forward limit fill)

| Variant | Return % | MaxDD % | Calmar | Sharpe | Δ$ vs baseline | Fired |
|---|---:|---:|---:|---:|---:|---:|
| Baseline | 175.07 | -24.23 | 7.23 | 1.00 | — | — |
| Overlay A-FULL (Gate 2 tested) | 96.81 | -31.50 | 3.07 | n/a | **-$39,153** | 164/511 |
| Overlay A-FULL, open-or-thr fill | 104.51 | -30.21 | 3.46 | n/a | -$35,307 | 164/511 |
| **Overlay A-HALFLOCK** (untested) | **282.34** | **-15.46** | **18.26** | **1.61** | **+$53,633** | 164/511 |

A-FULL: closes 100% of position at the lock price. The overlay cuts off
upside that the trade subsequently captures. Verdict: dead.
A-HALFLOCK: leaves the position open but raises the stop to entry +
50%×lock. Verdict: robustly positive.

---

## Issue-by-issue findings

### Issue 1 — Warmup contamination (pool size cutoffs)

Threshold pool grew from 0 (window 1) to 451 (window 17). Re-running with
minimum pool-size cutoffs:

| Min pool size | Windows surviving | Mean ΔCalmar | Median ΔCalmar | Pos windows |
|---:|---:|---:|---:|---:|
| ≥50 | 14 | +15.1% | -8.6% | 5 |
| ≥100 | 11 | +14.5% | -8.6% | 3 |
| ≥200 | 9 | +20.1% | -8.6% | 3 |
| ≥300 | 6 | +59.5% | +11.2% | 3 |

**Reading.** The mean improves with stricter cutoffs but the median is
flat-negative through cutoff 200. The "improvement at cutoff ≥300" is
driven by the same +366% outlier. **Warmup contamination is NOT what
killed A-FULL.** Even fully warmed-up windows are split close to 50/50.

### Issue 2 — Per-window metrics consistency (Calmar vs Sharpe vs $)

| Metric | Mean Δ | Median Δ | Pos / N |
|---|---:|---:|---:|
| Calmar % | +13.33 | -8.53 | 6/17 |
| Absolute return Δpp | -7.03 | -0.81 | 6/17 |
| Absolute MaxDD Δpp | +1.87 | +1.07 | 12/17 |
| Sharpe Δ | -0.30 | -0.15 | 6/17 |
| Absolute Δ $ | -$3,517 | -$405 | 6/17 |

Trade-level (across all 17 windows × ~58 trades): **200 helped, 115 hurt,
660 neutral**. A-FULL helps drawdown (12/17 windows have smaller DD —
because cutting positions off limits adverse moves) but hurts return,
Sharpe, and absolute $.

The kill verdict is **consistent across Calmar, Sharpe, and absolute $** —
the +13.3% Calmar mean was a window-Calmar artifact, not a real edge. So
Gate 2's verdict on A-FULL is robust to the metric used.

### Issue 3 — The +366% outlier

Window 2024-06-18 → 2024-12-15 (60 trades, 17 fires).

| Field | Baseline | Overlay |
|---|---:|---:|
| Calmar | -0.145 | -0.676 |
| Return % | -3.80 | -18.57 |
| MaxDD % | -26.2 | -27.5 |
| Δ$ | — | **-$7,385** |

**The outlier is a Calmar sign artifact.** Both numerator (return) and
denominator (MaxDD) are negative; Calmar = -0.145 → -0.676 looks like a
"+366% improvement" but actually the overlay made the window WORSE — the
return dropped 14.77pp and the trade gave up $7,385. 7 trades helped, 10
hurt. The reported "+366%" is a mathematical artifact of dividing two
negatives. **Removing it would FLIP the kill from "marginal" to
"unambiguous."** This is the single biggest methodology bug in Gate 2.

### Issue 4 — Fire rate vs window outcome

Pearson(fire_rate, ΔCalmar) = -0.058. No relationship. Only 1 window has
<5 fires (the warmup window with 2 fires). **Loss windows are not
caused by fire-rate sparsity.** Fires are well-distributed; the overlay
just doesn't help.

### Issue 5 — Less-pessimistic fill model (open-or-threshold)

Fill = max(trigger_price, bar_open), capped at bar high. This rewards
the case where the bar gapped open above the trigger (unattainable under
strict limit-at-trigger fill).

| Variant | Mean ΔCalmar | Median | Pos / N | Mean Δ$ |
|---|---:|---:|---:|---:|
| limit_at_threshold (Gate 2) | +13.3% | -8.5% | 6/17 | -$3,517 |
| open_or_threshold (this) | +18.1% | -4.3% | 7/17 | -$3,145 |

Modest improvement, doesn't move the verdict on A-FULL. Even on the full
60-month sample with open_or_threshold fill, A-FULL still loses $35,307
(see headline table).

### Issue 6 — Action variants (A-25, A-HALFLOCK) — THE FINDING

| Action | Mean ΔCalmar | Median | Pos / N | Mean Δ$ | Median Δ$ | Pos$ / N |
|---|---:|---:|---:|---:|---:|---:|
| A-FULL (Gate 2) | +13.3% | -8.5% | 6/17 | -$3,517 | -$405 | 6/17 |
| A-25 (close 25%) | +4.6% | -1.6% | 7/17 | -$879 | -$101 | 6/17 |
| **A-HALFLOCK** | **+72.6%** | **+44.7%** | **12/17** | **+$6,148** | **+$4,697** | **17/17** |

**A-HALFLOCK is positive in dollars in every single window** (min
+$717, max +$12,851). On the full 60-month sample (Issue 8 below) it
adds +$53,633. Calmar improves 7.23 → 18.26. MaxDD shrinks 24.2 → 15.5.
Sharpe improves 1.00 → 1.61.

**Why does A-HALFLOCK work where A-FULL doesn't?**
A-FULL closes the entire position at the lock price, surrendering all
upside above lock. When the trade is a true winner that runs further,
A-FULL caps the upside at ~the trigger and creates a heavy opportunity
cost. A-HALFLOCK only RAISES THE STOP — if the trade keeps running, it
captures the full upside; if the trade gives back, it gets stopped at
50%×lock instead of at the original exit. It is a strict
upside-asymmetric improvement: it cannot make any trade worse (in $),
modulo the limit-fill assumption being valid.

**Caveat:** A-HALFLOCK's free-money property is partly mechanical
(`new_realized = max(realized, 0.5*lock)` is monotone non-decreasing). The
real-world risk is that the lock fill is itself unattainable — a stop
order at lock price slipping past the lock. We mitigate this by using
the same limit-at-threshold fill model that survived verification. But
this needs one more validation experiment (see recommendation).

### Issue 7 — Statistical power: 1-month stepping → 50 windows

| Metric | 17-window (3mo step) | 50-window (1mo step) |
|---|---:|---:|
| n windows | 17 | 50 |
| Mean ΔCalmar % (A-FULL) | +13.3 | +1.2 |
| Median ΔCalmar % (A-FULL) | -8.5 | -7.6 |
| Pos windows | 6/17 | 19/50 |
| 95% CI on mean | wide | [-29.3%, +31.6%] |
| Mean Δ$ (A-FULL) | -$3,517 | -$2,885 |

**Reading.** With 50 windows the mean ΔCalmar collapses from +13.3% to
+1.2% — strongly suggesting the +13.3% headline was outlier-driven. The
median stays negative. **More windows confirm the kill of A-FULL.** Sign
of median does NOT flip with more data.

### Issue 8 — Full-sample (60-month, no slicing) view

Single overlay run across all 511 enriched trades, walk-forward
expanding-window VEL95 threshold, limit fill:

| Variant | Return % | MaxDD % | Calmar | Δ$ | Fired |
|---|---:|---:|---:|---:|---:|
| Baseline | 175.07 | -24.23 | 7.23 | — | — |
| A-FULL, limit | 96.81 | -31.50 | 3.07 | -$39,153 | 164/511 |
| A-FULL, open-or-thr | 104.51 | -30.21 | 3.46 | -$35,307 | 164/511 |
| **A-HALFLOCK, limit** | **282.34** | **-15.46** | **18.26** | **+$53,633** | **164/511** |

**A-FULL is unambiguously dead on the full sample.** Return drops 78pp,
DD widens 7pp, Calmar halves. Removing the window-slicing artifact does
not save A-FULL — it just confirms the kill.

**A-HALFLOCK is unambiguously alive on the full sample.** +107pp return,
DD shrinks 8.8pp, Calmar 2.5x.

---

## Summary table — which issues moved the verdict

| Issue | Did it move A-FULL verdict? | Did it surface a new edge? |
|---|---|---|
| 1 — Warmup contamination | No | No |
| 2 — Calmar vs Sharpe vs $ | No (kill is metric-consistent) | No |
| 3 — +366% outlier | **Yes (strengthens kill)** | No |
| 4 — Fire-rate correlation | No | No |
| 5 — Open-or-thr fill | No (small positive shift) | No |
| **6 — Action variants** | **A-FULL stays dead** | **YES — A-HALFLOCK is alive** |
| 7 — More windows (n=50) | **Yes (strengthens kill)** | No |
| 8 — Full-sample view | **Yes (kill confirmed)** | **YES — confirms A-HALFLOCK** |

---

## Final verdict: **KILL_PREMATURE**

The kill of A-FULL was correct and is now strongly confirmed (Issues
3, 7, 8 all reinforce it). But the Gate 2 methodology only tested A-FULL.
Gate 1 had identified five action variants and Gate 2 dropped four of
them when narrowing to A-FULL × VEL95/VEL90/VEL98. **A-HALFLOCK was in
the original Gate 1 grid and was not tested in Gate 2** — and it is the
variant where the edge actually lives.

### Specific condition under which the overlay works
- **Trigger:** velocity ≥ walk-forward expanding-window VEL95 of prior winners' velocities (same as Gate 2)
- **Fill model:** limit-at-threshold (same as Gate 2 / verification)
- **Action:** A-HALFLOCK — raise the stop to entry + 0.5×lock_price; do NOT close the position
- **Result on s523c_growth, 60mo @ $50K capital:**
  - Return: 175% → 282% (+107pp)
  - MaxDD: -24.2% → -15.5% (-8.7pp)
  - Calmar: 7.23 → 18.26 (2.5×)
  - Sharpe: 1.00 → 1.61
  - 17/17 rolling 6mo windows positive in $

---

## Recommended next experiment (NOT Gate 3 yet)

**Validate that A-HALFLOCK is not a phantom of the realized-price recompute model.**

The asymmetric overlay `new_realized = max(realized, 0.5*lock)` is
mathematically non-negative — it cannot make any trade worse in $. The
question is whether real-world execution can hit the stop at exactly
0.5×lock.

**Single validation experiment** before any v4 implementation:

1. Re-run the autopsy A-HALFLOCK with three pessimistic stop-fill models:
   - **slip-25bps**: floor = 0.5×lock − 0.0025 (25 bps adverse slippage on stop)
   - **slip-50bps**: floor = 0.5×lock − 0.0050
   - **slip-100bps**: floor = 0.5×lock − 0.0100
2. Verify the +$53,633 full-sample improvement survives slippage of at
   least 50 bps. Anything less robust = it was a price-recompute fiction.
3. Also verify A-HALFLOCK on s513_triple_trigger_swing — Gate 1 claimed
   no edge there for A-FULL; we need to know whether A-HALFLOCK also
   fails on s513 (overlay is strategy-specific) or also wins (edge is
   strategy-agnostic).

If both checks pass, THEN advance a revised Gate 2 framing:
"velocity-triggered raise-stop overlay, A-HALFLOCK action, limit-fill +
50bp slippage" against the same 17-window test, with success defined as
**≥80% positive-$ window rate** (not Calmar ratios).

---

## Most important finding (one sentence)

Gate 2 killed the overlay because it only tested A-FULL (close-position),
but A-HALFLOCK (raise-stop) on the same trade log, same fill model, same
walk-forward threshold is positive in absolute dollars in 17/17 rolling
windows and adds +$53,633 / +107pp return / -8.7pp MaxDD on the full
60-month sample.

---

## Recommendation

The kill of A-FULL is correct and doubly confirmed by the autopsy
(removing the +366% Calmar artifact and going to 50 windows both
strengthen the negative verdict; the full-sample view shows A-FULL loses
$39K). But killing "Mission G" entirely is premature: A-HALFLOCK was
listed in the original Gate 1 grid and was dropped from Gate 2's
shortlist. It produces a robust, dollar-positive, drawdown-reducing edge
on the same data. Before any engine work, run the slippage-stress
experiment above to confirm A-HALFLOCK isn't a recompute fiction; if it
survives ≥50 bps stop slippage AND the s513 cross-check, propose a
revised Gate 2 around A-HALFLOCK and proceed from there. If it fails
slippage, mark the kill as correct after all and document the lesson:
*always test all action variants in the regime-persistence gate.*

---

## Source data

- 60-mo trades: `research/gate2_out/s523c_growth_60mo_50k_trades.json`
- Autopsy script: `research/profit_lockin_autopsy.py`
- Autopsy raw results: `research/profit_lockin_autopsy_results.json`
- Gate 2 (audited): `research/profit_lockin_gate2.py`, `research/profit_lockin_gate2_results.json`
- Verification (audited): `research/profit_lockin_verification.py`
