# Mission N Gate 0 — Adaptive Stop Tightening Diagnostic

Generated: 2026-04-08

**Verdict: MARGINAL**

- Best offset +14d: improvement 1.6% < 5% threshold
- Best offset +14d: saved/given_back ratio 1.32x < 2.0 threshold
- MARGINAL: edge exists (improvement 1.6%, ratio 1.32x) but below PASS thresholds

## Hypothesis being tested

If a trade is still underwater at +Nd post-entry, exit it immediately at +Nd close price (simulating a tightened stop hit). Trades NOT underwater at +Nd are left alone — preserves the right tail. Cost: 8bps round-trip fee+slippage on early exits.

## Configuration

- Leverage: 2.6× (s523c default)
- Round-trip cost on early exits: 8 bps
- Source data: clean 12-month s523c backtest, 583 trades enriched

## Results by offset

| Offset | N total | N underwater | Loser rate among UW | N helped by rule | N hurt by rule | Median Δ$ | Total Δ$ | Δ% of baseline |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| +3d | 583 | 278 | 70.5% | 128 | 150 | $-55 | $-190,505 | -43.5% |
| +5d | 551 | 253 | 78.3% | 139 | 114 | $70 | $-112,033 | -24.7% |
| +7d | 501 | 211 | 82.0% | 118 | 93 | $50 | $-105,178 | -21.4% |
| +14d | 378 | 130 | 93.1% | 70 | 60 | $38 | $+8,869 | +1.6% |

## Saved vs Given-Back breakdown by offset

| Offset | Saved $ (cut deeper losers) | Given back $ (cut early winners) | Ratio |
|---|---:|---:|---:|
| +3d | $+236,101 | $-426,606 | 0.55x |
| +5d | $+211,606 | $-323,639 | 0.65x |
| +7d | $+158,542 | $-263,721 | 0.60x |
| +14d | $+36,480 | $-27,611 | 1.32x |

## Cost sensitivity (Δ$ vs baseline)

| Cost | +3d | +5d | +7d |
|---|---:|---:|---:|
| 5bps | $-189,868 | $-111,418 | $-104,684 |
| 10bps | $-190,929 | $-112,442 | $-105,508 |
| 25bps | $-194,112 | $-115,515 | $-107,979 |
| 50bps | $-199,417 | $-120,635 | $-112,097 |
| 100bps | $-210,027 | $-130,875 | $-120,333 |

## Diagnosis

Edge exists but is too small to commit to engine code. Either the cost assumption is too punitive for the underlying improvement, or the recovered trades (false positives) absorb too much of the saved-loser benefit. Consider testing with deeper-leverage tightening (e.g., move stop closer to entry rather than full close) before declaring dead.