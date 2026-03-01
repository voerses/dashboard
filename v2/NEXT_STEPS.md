# NEXT STEPS — Pick Up Here
**Last updated**: 2026-02-28

> **To resume**: Read this file, then execute steps in order.

---

## STEP 1: Re-validate on 5yr data (CRITICAL)

All our results (S11 +$170K, S09 +$163K, validated tokens) are from **2yr data only**.
We just finished downloading 5yr data (Jan 2021 - Feb 2026) for all 49 tokens.
This is the most important step — results may change significantly.

```bash
cd /workspace/crypto_backtest/v2
python3 export_params.py --strategy s11 --noise 0.003 --exchange kraken,binance
```

This runs the full pipeline:
1. CPCV validation (PBO < 40% filter) on 5yr data
2. Walk-Forward validation (60/40 split, OOS must be profitable)
3. Noise injection (5 trials at ±0.3%) — tokens must survive all trials
4. Corwin-Schultz spread estimation per token
5. Exports `cpcv_params.json` (Freqtrade reads this)
6. Generates `config_kraken.json` and `config_binance.json`

**What to check after**:
- How many tokens still validate? (was 6 for S11)
- Do SUI, BONK, FLOKI still triple-validate?
- Has absolute PnL changed? (2022 bear market now included)
- Any tokens newly validated that weren't before?

## STEP 2: Validate S09 on 5yr data

```bash
python3 export_params.py --strategy s09 --noise 0.003 --exchange kraken,binance --out cpcv_params_s09.json
```

Compare S11 vs S09 validated tokens on 5yr. Previously S09 validated: SUI, TRX, BONK, FLOKI.

## STEP 3: Run full comparison on 5yr data

Quick engine comparison to see strategy rankings with more history:

```python
python3 -c "
from engine import Engine, CPCV_ROBUST_TOKENS
from strategies.s11_momentum_burst import strategy as s11
from strategies.s09_optimized_trend import strategy as s09

engine = Engine()
engine.compare(
    strategies=[s11, s09],
    labels=['S11 Momentum Burst', 'S09 Optimized Trend'],
    tokens=CPCV_ROBUST_TOKENS,
)
"
```

## STEP 4: Update knowledge docs with 5yr results

After Steps 1-3, update:
- `knowledge/STRATEGY_RESULTS.md` — new PnL numbers, new validated tokens
- `knowledge/ARCHITECTURE.md` — reflect 5yr data, freqtrade_bridge directory
- `knowledge/DATA_MANIFEST.md` — 5yr date ranges
- `SESSION_STATE.md` — update findings section

## STEP 5: Stress-test the 2022 bear market

The 5yr data now includes the 2022 crash (BTC $69K → $16K). Check:
- Does the strategy lose money during extended bear markets?
- How deep are drawdowns?
- Does the 24-bar no-stop window survive flash crashes?

```python
# Slice 2022 only and backtest
python3 -c "
from engine import Engine
from strategies.s11_momentum_burst import strategy as s11
engine = Engine()
# Engine should handle the full 5yr period automatically
results = engine.run(s11, tokens=['SUI','BONK','FLOKI','PENGU','AVAX','ZRO'])
for tk, r in results.items():
    print(f'{tk}: equity={r[\"equity\"]:,.0f} trades={r[\"n_trades\"]} wr={r[\"win_rate\"]:.0f}%')
"
```

## STEP 6: Build portfolio optimizer (if tokens still validate)

Combine S11 + S09 with:
- Position limits on overlapping tokens (SUI, BONK, FLOKI appear in both)
- Capital allocation: 60% S11 / 40% S09 (or optimize)
- Correlation check between strategy signals
- Max portfolio heat limit

## STEP 7: Install Freqtrade and run parity check

```bash
# Install Freqtrade
pip install freqtrade

# Run parity check — compares engine trades vs Freqtrade trades
python3 freqtrade_bridge/parity_check.py --strategy s11 --tolerance 0.10
```

## STEP 8: Start paper trading (dry run)

```bash
# Copy strategy + params to Freqtrade directory
mkdir -p freqtrade/user_data/strategies/
cp freqtrade_bridge/strategy_shell.py freqtrade/user_data/strategies/CpcvSwingStrategy.py
cp cpcv_params.json freqtrade/user_data/strategies/

# Start dry-run on Kraken (real data, simulated orders)
freqtrade trade --strategy CpcvSwingStrategy --config freqtrade_bridge/config_kraken.json

# Or Binance
freqtrade trade --strategy CpcvSwingStrategy --config freqtrade_bridge/config_binance.json
```

---

## DECISIONS STILL NEEDED

- **Which configuration to deploy?** Option A (S11 on 6 tokens), B (triple-validated 3 tokens), or C (S11+S09 split)?
- **Capital allocation per exchange?** Run on one exchange or split across Kraken + Binance?
- **When to go live?** After how many weeks of successful paper trading?
- **Drawdown limit?** At what loss level do we pause the bot?

---

## QUICK REFERENCE

| What | Where |
|------|-------|
| Full session state | `SESSION_STATE.md` |
| Strategy rankings | `knowledge/STRATEGY_RESULTS.md` |
| System architecture | `knowledge/ARCHITECTURE.md` |
| Best strategy code | `strategies/s11_momentum_burst.py` |
| Exchange config | `freqtrade_bridge/exchange_registry.py` |
| Freqtrade strategy | `freqtrade_bridge/strategy_shell.py` |
| Parameter export | `export_params.py` |
| All research docs | `knowledge/*.md` (21 files, ~790KB) |
