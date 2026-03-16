# Rollback Reference: Pump-and-Dump Entry Filters

**Commit:** `6573ee7` (revert with `git revert 6573ee7`)
**Parent:** `e6c0b55`
**Date:** 2026-03-16

## What was changed

### v4/config.py (lines 39-45 added)
```python
# Circuit breaker: emergency exit during no_stop_bars window (0 = disabled)
circuit_breaker_r: float = 4.0        # exit when loss >= Nx initial_risk
# Pump-and-dump entry filters (all default enabled)
pump_filter_range_threshold: float = 4.0   # block entry when (high-low)/ATR > threshold (0 = disabled)
pump_filter_adv_floor: float = 5_000_000   # ADV below this gets pump penalty applied (0 = disabled)
pump_filter_adv_penalty: float = 0.5       # sizing multiplier for tokens below adv_floor
pump_filter_funding_zscore: float = 3.0    # block LONG entries when funding z-score > this (0 = disabled)
```

### v4/simulator.py
1. **RejectionStats** — added `pump_range` and `pump_funding` counters + wired into `total()` and `to_dict()`
2. **Circuit breaker** (line ~502) — changed from hardcoded `CIRCUIT_BREAKER_R = 4.0` to `config.circuit_breaker_r` (configurable, 0 = disabled)
3. **Layer 1: Range anomaly** (line ~704) — new block before entry constraints: skips entry when `(high - low) / ATR > threshold`
4. **Layer 3: Funding z-score** (line ~714) — new block: skips LONG entries when rolling 168h funding z-score > threshold. Requires `sig.funding_1h` and >=24 bars
5. **Sizing calls** (lines ~733, ~1025) — added `pump_adv_floor` and `pump_adv_penalty` kwargs to both `compute_position_size` call sites

### v4/sizing.py
1. **compute_position_size** — added `pump_adv_floor` and `pump_adv_penalty` params (default 0.0 / 1.0 = no-op)
2. **Layer 2: ADV penalty** (line ~39) — `if rolling_adv < pump_adv_floor: pos_usd *= pump_adv_penalty`

## How to rollback
```bash
git revert 6573ee7
```
Or to disable without reverting, set all filters to 0 in config:
```python
circuit_breaker_r=0, pump_filter_range_threshold=0, pump_filter_adv_floor=0, pump_filter_funding_zscore=0
```
