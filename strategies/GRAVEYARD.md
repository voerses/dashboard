# Strategy Graveyard

Strategies killed during the gate process. Learn from the dead.

| Date | Strategy | Killed At | Reason |
|------|----------|-----------|--------|
| 2026-03-01 | s16_composite_factor | Gate 5 | 0% validation rate — no tokens passed |
| 2026-03-01 | s19_mean_reversion_filtered | Gate 5 | 0% validation rate — no tokens passed |
| 2026-03-01 | s08_obv_divergence | Gate 5 | 6.1% validation rate — well below 20% threshold |
| 2026-03-01 | s07_rsi_bounce | Gate 5 | 10.2% validation rate — below 20% threshold |
| 2026-03-03 | s25_vol_spike_reversal | Gate 5 | BTC failed 3x at Gate 4; altcoin performance didn't save it at Gate 5 |
| 2026-03-03 | s26_rsi_extreme_reversal | Gate 5 | 14.3% validation rate (47/329) — below 20% threshold |
| 2026-03-03 | s27_funding_mean_reversion | Gate 5 | 11.6% validation rate (38/329) — funding data sparse for many tokens |
| 2026-03-03 | s28_momentum_burst_perp | Gate 5 | 6.7% validation rate (22/329) — bidirectional momentum too selective on perp |
| 2026-03-08 | s35_regime_spot_perp_volsized (S1) | Gate 5O | Calmar -29%, return halved (-25.7pp), only +0.28pp MaxDD. Vol-sizing too aggressive on regime-gated s32 |
| 2026-03-08 | O3_weekend_sizing_on_s30 | Gate 5O | s30 is delta-neutral — no weekend directional risk. Calmar -9.5%, MaxDD slightly worse |
| 2026-03-08 | s36_basis_carry_funding_scaled (O6) | Gate 5O | Neutral overlay — Calmar -0.29, Sharpe -0.01, no improvement. Funding rate colinear with basis premium; entry signal already captures carry richness |
| 2026-03-08 | s38_momentum_etf_flow (N2) | Gate 5O | ETF flow data covers only 15 months (Dec 2024–Mar 2026); overlay=1.0 for 85% of backtest period. Cannot validate. Revisit when 2+ years of data accumulates |
| 2026-03-08 | s42_momentum_defensive_trail (O4) | Gate 5O | Zero marginal improvement on top of O5 trail progression. Per-bar volatility ceiling adds <0.1% equity diff, no validation change. O5 already captures the value. |
| 2026-03-08 | s43_regime_spot_perp_trail_progression | Gate 5O | Trail progression hurts s32: rate -2.2pp (71.1→68.9%). Trail tightening premature on short legs (downtrend shorts need wider stops to ride the trend). |
