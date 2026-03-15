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
| 2026-03-11 | s66_adx_breakout | Gate 0 | Too few trades: only 38 entries on BTC, expected >30 per token |
| 2026-03-11 | s68_band_walk | Gate 2 | Momentum family saturated (>2 strategies in Tier A). 80% overlap with s56 within 24h. |
| 2026-03-11 | s67_funding_momentum_v4 | V4-Gate 5 | Sharpe 1.90 too low for portfolio. Despite excellent decorrelation (all <0.2), capital dilution loses more from s65/s63 than s67 contributes. Need Sharpe >3 to add value. |
| 2026-03-12 | s73_s56_funding_exit (Sub 1) | Gate 5O | Calmar degrades at every threshold (7 tested: 0.01%–0.5%). Funding is 1.2% of PnL in 12mo backtest — paper F31 (67% drag) was small-sample artifact. High-funding tokens (ARC, PIPPIN) include big winners; exit can't discriminate. |
| 2026-03-12 | s74_s60_funding_exit (Sub 1) | Gate 5O | Same as s73. Calmar -34% at best Sharpe threshold (0.2%), Return -19%, MaxDD worsens. Funding exit cuts winners alongside losers. |
| 2026-03-15 | s86_s65_spot_long_perp_short | Gate 5O | Carry strategies RECEIVE funding on longs — spot routing removes $6M funding income. Return -34% vs s65 baseline. |
| 2026-03-15 | s87_s62_spot_long_perp_short | Gate 5O | Same as s86 — conservative carry also needs funding income on longs. Killed with s86. |
| 2026-03-15 | s88_s72_spot_long_perp_short | Gate 5O | Wraps s86 with time trail — killed because base (s86) is killed. |
| 2026-03-15 | s56_spot_routing | Gate 5O | Spot fees 2x perp fees ($438K vs $256K). For 24h-hold momentum, fee delta ($182K) dwarfs funding savings ($33K). Perp return +72% higher. |
| 2026-03-15 | s11_spot_routing | Gate 5O | Same fee economics as s56. Perp return +127% higher. Perp has stronger momentum dynamics from leveraged traders. |
2026-03-15 | s85_chandelier | Killed at Gate 3O | Cuts winners short in crypto momentum — return -162% at 12mo, win rate drops 10pp. Progressive trail already optimal.
2026-03-15 | s86_triple_barrier | Killed at Gate 3O | bear_target_mult and bear_max_hold both hurt s58. Return drops 246% at 12mo, DD improvement negligible. Existing exit stack already optimal.
