# Mission D Gate 0 — Novel Indicators Catalog

Source file: `/workspace/novel-indicators-contrarian-math.jsx` (954 lines, 8 indicators + 4 synergies)
Read & catalogued: 2026-04-08

## 8 Indicators

| Acronym | Name | Source | Tier | Data needed |
|---|---|---|---|---|
| **HV** | Hurst Velocity | Fractal analysis | 1 | 1h OHLCV |
| **FIF** | Fisher Information Flow | Information geometry | 1 | 1h OHLCV + KDE |
| **WRD** | Wasserstein Regime Detector | Optimal transport | 1 | 1h OHLCV |
| **SRI** | Spectral Rotation Index | Random matrix theory | 1 | Multi-asset 1h OHLCV |
| **TCB** | Topological Crash Barometer | Algebraic topology / TDA | 2 | Multi-asset OHLCV + ripser/persim |
| **LII** | Lyapunov Instability Index | Chaos theory | 2 | 1h OHLCV (slow O(N²)) |
| **TMP** | Thermodynamic Market Potential | Statistical mechanics | 3 | L2 orderbook (we have band-level adaptation) |
| **ROFD** | Rényi Order Flow Divergence | Generalized info theory | 3 | L2 trades (we have time-binned adaptation) |

## Tier 1 — Compute NOW (no new deps, no orderbook)

### HV (Hurst Velocity)
- **Formula:** v_H(t) = (H(t) − H(t−Δ)) / Δ, where H from DFA
- **Insight:** Derivative of Hurst exponent leads regime change by ~25 bars (because H is rolling, dH/dt detects slope before threshold cross)
- **Implementation:** ~50 lines (DFA + finite difference). Vectorizable.

### FIF (Fisher Information Flow)
- **Formula:** I(θ) = ∫ (f'(x))²/f(x) dx via KDE
- **Insight:** Meta-indicator — tells you WHEN your other signals are reliable. Cramér-Rao bound: Var(θ̂) ≥ 1/I(θ)
- **Implementation:** ~30 lines (scipy.stats.gaussian_kde + numerical integration)

### WRD (Wasserstein Regime Detector)
- **Formula:** W₁(μₜ, μₜ₋ₖ) — earth mover's distance between consecutive return distributions
- **Insight:** Catches distributional shifts that no single statistic (mean, var, skew) sees
- **Implementation:** ~20 lines (scipy.stats.wasserstein_distance)

### SRI (Spectral Rotation Index)
- **Formula:** SRI(t) = arccos(|⟨v₁(t), v₁(t−k)⟩|) — angle between principal eigenvectors of correlation matrix
- **Insight:** Detects market leadership change. Tracks geodesic on Grassmannian manifold.
- **Implementation:** ~30 lines (np.corrcoef + np.linalg.eigh + arccos)
- **Special:** Multi-asset — perfect for our 236-token universe

## Tier 2 — Needs library install

### TCB (Topological Crash Barometer)
- **Formula:** L^p norm of persistence landscape after Takens embedding + Vietoris-Rips filtration
- **Validation:** Gidea & Katz 2017 — strong growth before 2000 + 2007-2009 crashes
- **Dependencies:** `pip install ripser persim`
- **Implementation:** ~50 lines

### LII (Lyapunov Instability Index)
- **Formula:** Rosenstein algorithm for max Lyapunov exponent
- **Insight:** Tells you whether the market is chaotic (unpredictable) or ordered (exploitable). Lyapunov time = 1/λ_max = forecast horizon
- **Implementation:** ~80 lines, O(N²) per evaluation — moderate cost
- **Special:** Pairs with FIF as the meta-predictability filter

## Tier 3 — Needs orderbook adaptation

### TMP (Thermodynamic Market Potential)
- **Formula:** F(t) = U(t) − T(t)·S(t) — Helmholtz free energy of the order book
  - U = volume-weighted imbalance × distance from mid (stress)
  - S = Shannon entropy of order distribution (disorder)
  - T = realized volatility (temperature)
- **Validation:** Maps to spinodal decomposition in Ising model phase transitions
- **Our data:** bookdepth has 10 fixed % bands (±1/2/3/4/5%), not raw L2
- **Adaptation:** Treat the 10 bands as 10 "price levels" with notional at each. U = Σ |bid_notional[i] - ask_notional[i]| × |pct_offset[i]|. S = Shannon entropy over 10 bands. T = realized vol from trades_1s. Different scale than true L2 but same mathematical structure.
- **Available for:** BTC/ETH/SOL now, BNB/XRP/DOGE/ADA/AVAX/LINK/DOT/NEAR/ATOM/LTC after Mission F Phase 2

### ROFD (Rényi Order Flow Divergence)
- **Formula:** D_α(P‖Q) = 1/(α−1) · ln(Σ pᵢᵅ qᵢ¹⁻ᵅ), where P/Q are buy/sell distributions across price levels
- **Insight:** Generalization of VPIN that captures WHERE orders are placed, not just totals
- **Our data:** trades_1s has per-second buy/sell volumes but not per-price-level
- **Adaptation:** Compute on TIME-binned distribution instead of PRICE-binned. Within a 5-min window, distribute trades across 1-second sub-bins. Compute Rényi divergence between buy and sell concentrations across the time bins. This captures temporal concentration (informed traders cluster in time) instead of spatial (cluster at price levels). Different signal but same family.

## 4 Synergies

### 1. TCB + TMP — Topology-Thermodynamics Gateway
- **Logic:** TCB detects geometric instability (holes in return shape), TMP detects energetic instability (order book stress). Both firing = highest-conviction crash/breakout signal.
- **Signal:** TCB > 2σ AND dF/dt > 2σ
- **Useful for:** Mission J (cascade detection enhancement)

### 2. LII + FIF + HV — Predictability Filter
- **Logic:** LII says IF the market is predictable. FIF says HOW MUCH information is available. HV says WHICH regime you're transitioning into.
- **Signal:** Use as POSITION SIZING multiplier across all strategies
- **Formula:** position_size *= FIF_percentile × (1 - LII_normalized)
- **Useful for:** Mission C (Forward-Forward gate's natural complement)

### 3. ROFD + TMP + WRD — Informed Flow Microscope
- **Logic:** ROFD sees WHERE informed orders are placed → TMP sees the STRESS those orders create → WRD sees the DISTRIBUTION shift as a result. Causal chain in real time.
- **Signal:** ROFD spike → wait for TMP confirmation within 5 bars → enter on WRD initial move

### 4. SRI + HV + WRD — Rotation-Regime Anticipator
- **Logic:** SRI = leadership change, HV = character change, WRD = distribution change. All three firing = complete regime reset.
- **Signal:** SRI > 30° + v_H zero-cross + W₁ > 3× median → rebalance everything

## Recommended Mission D Gate 0 IC test plan

### Phase 1 (~10 min agent): Tier 1 IC test
For each of HV, FIF, WRD, SRI:
1. Compute rolling values on BTC/ETH/SOL 1h returns (full available history, ~5 years)
2. Compute forward returns at: 1h, 4h, 1d, 3d, 7d
3. Pearson + Spearman IC with t-stats
4. Multi-asset: also test on top-20 alts for cross-validation

### PASS criteria
- |IC| > 0.05 with t > 2 on at least 2 of the 5 horizons
- OR |IC| > 0.08 on a single horizon with t > 3

### Phase 2 (only if Phase 1 has PASSes)
- Install ripser/persim
- Add TCB + LII to the IC test
- Repeat the multi-horizon analysis

### Phase 3 (only if Mission F Phase 2 ships and Phase 1/2 found edges)
- Build TMP and ROFD adaptations using band-level orderbook data
- IC test those vs forward returns

### Phase 4 (synergies — only if individual indicators pass)
- Test the 4 synergy combinations as composite signals
- Particularly the Predictability Filter (LII × FIF × HV) as a position sizing layer

## Honest expectations

Most novel indicator papers don't replicate on crypto. Realistic Gate 0 outcomes:
- **Best case (5% probability):** 2-3 indicators show clean IC > 0.10 — promote to Gate 1 strategy backtests
- **Most likely (60%):** 1 indicator passes marginally, 1-2 are interesting but below threshold — add to research-monitoring
- **Likely (30%):** All fail IC threshold — kill mission, document the negative result
- **Unlikely (5%):** Multiple strong passes — major win, restructure session priorities around them

## When to launch Mission D

**Don't launch in parallel with Mission J + Mission F Phase 2** — would saturate compute and the API has been overloaded.

**Launch as the immediate next step** when EITHER:
1. Mission J Gate 1 returns its verdict (this becomes parallel work for Mission J Gate 2 or post-mortem)
2. Mission F Phase 2 fetch finishes (this gives Mission D access to richer multi-asset data for SRI, TCB)

## Files

- Source spec: `/workspace/novel-indicators-contrarian-math.jsx` (read 2026-04-08)
- This catalog: `research/mission_d_gate0_catalog.md`
- (Future) Implementation: `research/mission_d_gate0.py`
- (Future) Results: `research/mission_d_gate0_results.json`
- (Future) Report: `research/mission_d_gate0_report.md`
