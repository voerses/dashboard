# Profit Lock-in Overlay — Gate 1 Verification Pass

_Generated 2026-04-07 (verification of `profit_lockin_gate1_report.md`)_

Stress-test of the claim **VEL95 × A-FULL** improves s523c_growth Calmar from
18.60 → 49.56 (+166%). Two attack vectors: (1) lookahead-bias in threshold
selection, (2) optimistic intrabar fill.

Source script verified: `research/profit_lockin_gate1.py`.
Verification harness: `research/profit_lockin_verification.py`
(reuses `enrich_trade`, `slice_for_trade`, `build_equity_curve`, `metrics`).

---

## Section 1 — Code audit findings

### CRITICAL — Optimistic fill at intrabar HIGH (lock_fav = fav[fire_bar])
`profit_lockin_gate1.py` lines 303–331. For T-VEL triggers, when bar `i`
satisfies `fav[i] / (i+1) >= vth`, the script locks in
`lock_fav = fav[fire_bar]`, where `fav[i] = (high[i] - entry) / entry`.
**This assumes execution at the bar's HIGH** — equivalent to a sell-limit
placed exactly at that bar's eventual peak. Real fills cannot achieve this:
at end-of-bar you can only confirm the high happened, you cannot transact
at a price you've already passed. Realistic models (close, midpoint, or
limit-at-threshold) shrink the edge by 30–50% **even before any OOS test**.
See Section 3 for sensitivity.

### CRITICAL — Full-sample velocity-percentile lookahead
Lines 446–462. `build_trigger_grid()` reads the **full-sample p95** of
winner velocities from `phase1_diag()` (which iterated over all 575
enriched trades — line 190). That value (~1.33% per bar) is then used as
the trigger threshold over the same 575 trades. This is in-sample
fitting: at trade #1, the strategy "knows" the p95 of velocities of
trades that haven't happened yet. When a true walk-forward (expanding
window) threshold is used instead, the edge survives but shrinks; when a
strict 50/50 OOS split is used, the edge largely disappears under
realistic fills (see Section 2).

### MODERATE — Funding cost not pro-rated when trade cut short
Lines 379–383. `funding` is held constant when the trade is closed early.
For trades held 200+ bars in baseline but cut at bar 0–10 by overlay,
funding (which is per-bar) should be ~95% lower. Author flagged this as
"slightly pessimistic" — true, but it adds ~1–3pp to the headline that
should not be there for the *right* reasons. Defensible to keep, but
worth noting.

### MINOR — Fee double-pay on cut trades
Lines 263 and 380. The original `entry_fee + exit_fee` are kept when the
overlay closes early; in reality the early exit also incurs an exit fee.
The script uses the original close fee (one round trip), so this is
roughly fee-correct (one entry + one exit). No double-count.

### MINOR — Leverage inferred per trade
Lines 125–130. `lev = gross_pnl / margin / realized_price`. This is
fragile when `realized_price` is small or noisy; the script clips to
[0.5, 50] and drops trades outside. 59 trades drop out (10%), which
biases the universe slightly toward larger-realized-move trades. Not
load-bearing for the verdict.

### CLEAN — Direction-aware MFE
Lines 102–112 use `highs` for longs, `lows` for shorts (and the
direction-symmetric version for fav/adv). Correct.

### CLEAN — ATR(14) computed strictly from bars BEFORE entry
Lines 134–151. Uses bars `[entry-14, entry]`. No forward leakage.

### CLEAN — Trade selection unchanged by overlay
Overlay only modifies exits in-place (`ovl_pnl` field). Entries are the
exact set the live simulator opened. No survivorship from re-selection.

---

## Section 2 — Out-of-sample test

Trades sorted chronologically by `entry_bar`. First half (n=287) vs second
half (n=288). Velocity p95 computed on first half ONLY, applied to second
half.

| Threshold source | p95 velocity (price/bar) |
|---|---|
| Full sample (used by original report) | 0.01330 |
| In-sample (first 50% of trades) | 0.00911 |
| Out-of-sample (observed) | 0.02229 |

Note the **OOS distribution has 2.4x higher p95** than IS — winners in the
second half were on average much faster movers. The IS threshold (0.0091)
is therefore a much *looser* trigger when applied to the OOS slice
(107 fires out of 288 vs the original's ~25%).

### OOS slice — second-half-only evaluation
The OOS baseline is computed by replaying only the second-half trades from
$50K (no equity carryover from the first half). This makes its absolute
metrics ugly, but the relative comparison is still valid.

| Variant | Return % | Sharpe | MaxDD % | Calmar |
|---|---:|---:|---:|---:|
| OOS baseline (no overlay) | 158.98 | 0.20 | -140.38 | 1.13 |
| OOS overlay, optimistic fill (high) | 326.92 | 2.42 | -67.05 | 4.88 |
| OOS overlay, midpoint fill | 236.79 | 2.15 | -89.29 | 2.65 |
| OOS overlay, limit-at-threshold fill | 133.96 | 2.61 | -150.15 | **0.89** |

**Reading:** with optimistic fill, OOS Calmar improves 4.3x (1.13 → 4.88).
With midpoint fill, 2.3x. With realistic limit-order fill, the edge
**completely vanishes** — Calmar goes *below* baseline (0.89 vs 1.13).

### Walk-forward (expanding) threshold
At each new trade, compute p95 over the velocity distribution of trades
whose `exit_bar` precedes the current trade's `entry_bar`. Requires
≥20 prior winners; otherwise no firing.

| Variant | n_fired | Return % | Sharpe | MaxDD % | Calmar |
|---|---:|---:|---:|---:|---:|
| Baseline (full sample) | — | 662.20 | 2.89 | -35.61 | 18.60 |
| WF, optimistic fill | 179 | 842.73 | 3.52 | -21.24 | **39.68** |
| WF, midpoint fill | 179 | 732.50 | 3.25 | -24.75 | **29.59** |
| WF, limit-at-threshold fill | 179 | 718.76 | 3.20 | -25.60 | **28.08** |

Walk-forward fires more often (179 vs 148) because early in the year the
threshold is computed off a tiny sample and is unstable. Even so, the
edge **survives** all three fill models, ranging from Calmar 28.1
(limit, conservative) to 39.7 (optimistic).

---

## Section 3 — Realistic fill test (full-sample threshold)

Holding the threshold fixed at the original full-sample p95 (0.01330) to
isolate fill-model bias from threshold bias.

| Fill model | Return % | Sharpe | MaxDD % | Calmar | Δ vs baseline | % of original Calmar gain retained |
|---|---:|---:|---:|---:|---:|---:|
| Baseline | 662.20 | 2.89 | -35.61 | 18.60 | 0% | — |
| Optimistic (= original) | 927.89 | 3.54 | -18.72 | **49.56** | +166% | 100% |
| Midpoint (high+close)/2 | 799.73 | 3.28 | -22.02 | **36.32** | +95% | 57% |
| Limit-at-threshold | 752.58 | 3.22 | -23.51 | **32.01** | +72% | 43% |
| Close-only | 670.88 | 2.98 | -26.27 | **25.54** | +37% | 22% |

**Reading:** the optimistic fill alone explains roughly half of the
headline Calmar improvement. Under a realistic limit-order assumption,
the in-sample improvement is +72% (still meaningful, not +166%). Under
the most pessimistic close-only assumption (you only act after seeing the
bar close), the improvement is +37%.

---

## Section 4 — Verdict

**DEGRADED BUT REAL.**

Under combined realistic assumptions (walk-forward expanding-window
threshold + limit-at-threshold fill), the edge is:

- **Calmar:** 18.60 → **28.08** (+51%)
- **Return:** 662% → 719% (+57pp)
- **MaxDD:** -35.6% → -25.6% (~28% improvement)
- **Sharpe:** 2.89 → 3.20

This is roughly **30% of the headline claim** but still substantial. The
strict 50/50 OOS test was harsher — under limit fills the edge collapsed
on that slice — but the 50/50 split is brittle here because (a) the
second-half winner-velocity distribution is materially different from
the first-half (regime shift) and (b) OOS-only equity replay produces
unstable Calmar denominators. The walk-forward test is the more
defensible OOS measurement and it confirms the edge.

### Recommendation: PROCEED to Gate 3 with realistic expectations.

Build the prototype against the **walk-forward / limit-fill** numbers,
not the headline 49.56 Calmar. Specifically:

1. **Implement the v4 exit handler** with a velocity threshold that is
   either fixed at a conservative value (e.g. 0.015 = 1.5%/bar, which
   is approximately the walk-forward steady-state) or computed on a
   rolling 60-day window of completed trades.
2. **Expect Calmar improvement of +30 to +60% in walk-forward**, not
   +166%. If the v4 implementation falls below +30%, kill it.
3. **Order model:** the prototype must use a real limit order at the
   trigger price, not market-on-touch and certainly not "fill at bar
   high." Anything assuming intrabar peak fills will retroactively
   over-attribute alpha to this rule.
4. **Include funding pro-ration** when the trade is cut short — this
   gives back ~1–3pp of headline return but keeps cost accounting honest.
5. **Re-run on s513:** original report showed no improvement on s513
   (best Calmar Δ = -9.4%). Verification did not re-run s513; assume
   the no-edge result holds and only enable the overlay on s523c-style
   strategies.

The 50/50 OOS collapse under limit fills is the biggest red flag — it
suggests the edge is sensitive to the specific velocity-distribution
regime. Recommend a Gate 2 dedupe pass that includes a regime-awareness
check (does the edge persist in 6-month rolling windows, or is it
concentrated in one period?).

---

## Headline numbers for the caller

| Metric | Original claim | Verified (WF + limit fill) | Verified (50/50 OOS + limit fill) |
|---|---:|---:|---:|
| Calmar | 18.60 → 49.56 (+166%) | 18.60 → **28.08** (+51%) | 1.13 → 0.89 (-21%) |
| Return % | 662 → 928 (+266pp) | 662 → 719 (+57pp) | 159 → 134 (-25pp) |
| MaxDD % | -35.6 → -18.7 | -35.6 → -25.6 | -140 → -150 |
| Sharpe | 2.89 → 3.54 | 2.89 → 3.20 | 0.20 → 2.61 |

**Most critical finding:** the original sim assumes execution at intrabar
HIGH (`lock_fav = fav[fire_bar]`), an unattainable fill that alone
accounts for roughly half of the claimed Calmar improvement.
