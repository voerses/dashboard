# Mission O Gate 0 — Portfolio-Level Direction Cull

Generated: 2026-04-08

**Verdict: KILL**

- Best grace_3d_thr_18pct: improvement -65.8%, saved/given_back 0.61x — net negative

## Hypothesis

After a grace period, look at the aggregate current P&L of all open LONG positions vs all open SHORT positions. If one direction's basket is decisively winning while the other is decisively losing, the regime favors one direction. Close all post-grace positions in the LOSING direction as a single action. Preserves the winning direction entirely.

## Sweep results

| Grace | Threshold | Long basket trigs | Short basket trigs | Trades force-closed | Helped | Hurt | Saved $ | Given-back $ | Total Δ$ | Δ% baseline |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2d | 3% | 178 | 164 | 465 | 231 | 234 | $+477,522 | $-821,552 | $-344,030 | -92.1% |
| 2d | 5% | 174 | 162 | 464 | 230 | 234 | $+465,438 | $-821,552 | $-356,114 | -95.3% |
| 2d | 8% | 169 | 153 | 453 | 225 | 228 | $+446,229 | $-773,297 | $-327,069 | -87.5% |
| 2d | 12% | 157 | 148 | 445 | 217 | 228 | $+438,511 | $-769,890 | $-331,378 | -88.7% |
| 2d | 18% | 144 | 131 | 408 | 210 | 198 | $+401,326 | $-678,526 | $-277,200 | -74.2% |
| 3d | 3% | 177 | 164 | 439 | 220 | 219 | $+430,986 | $-761,482 | $-330,496 | -88.5% |
| 3d | 5% | 172 | 162 | 434 | 220 | 214 | $+426,079 | $-712,822 | $-286,743 | -76.8% |
| 3d | 8% | 166 | 152 | 430 | 213 | 217 | $+417,676 | $-711,973 | $-294,297 | -78.8% |
| 3d | 12% | 160 | 142 | 420 | 208 | 212 | $+402,180 | $-707,920 | $-305,740 | -81.8% |
| 3d | 18% | 145 | 132 | 393 | 204 | 189 | $+384,399 | $-630,043 | $-245,644 | -65.8% |
| 5d | 3% | 183 | 166 | 405 | 200 | 205 | $+387,674 | $-680,278 | $-292,604 | -78.3% |
| 5d | 5% | 178 | 160 | 401 | 202 | 199 | $+390,703 | $-674,948 | $-284,245 | -76.1% |
| 5d | 8% | 166 | 149 | 394 | 190 | 204 | $+357,298 | $-679,799 | $-322,501 | -86.3% |
| 5d | 12% | 160 | 142 | 392 | 190 | 202 | $+351,321 | $-677,147 | $-325,826 | -87.2% |
| 5d | 18% | 146 | 134 | 356 | 184 | 172 | $+318,948 | $-583,377 | $-264,429 | -70.8% |

## Best variant

`grace_3d_thr_18pct`: improvement -65.8%, saved/given_back ratio 0.61x

- Long-basket close triggers: 145 days
- Short-basket close triggers: 132 days
- Trades force-closed: 393 of 634
- Helped: 204, hurt: 189
- Saved: $+384,399, Given back: $-630,043
- Total improvement: $-245,644

## Diagnosis

The portfolio-level long-basket vs short-basket spread does NOT produce a profitable cull rule. Either the spread isn't decisive enough on the actual losing days, or the few cases where it IS decisive include trades that would have recovered. s523c's directional balance is well-calibrated and the existing exits handle the asymmetry adequately.