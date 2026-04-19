# M6 Brief Review — Round 2

**Brief path**: `/workspace/crypto_backtest/.specs/active/m6-data-architecture/brief.md` (825 lines)
**Review scope**: Verify 7 Round-1 schema additions + answer 3 clarification questions + issue final verdict.

---

## 1. Per-addition verification

### Addition 1 — `InstrumentId.market → asset_class` with `"spot"/"perp"/"future"/"option"` vocabulary
**Status: FIXED** (VERIFIED)
- L186-192: `InstrumentId.asset_class: Literal["spot", "perp", "future", "option"]` — field renamed, vocabulary declared verbatim. Comment states `was 'market'; renamed per FIX Product(460)`.
- L136: FIX-block maps `InstrumentId.asset_class → Product(460)`.
- L667 (AC-D8), L729 (T-D11): consumers updated — `resolve(symbol, venue, asset_class)` uses the new name + vocabulary.
- L778: migration audit line-item budgeted ("InstrumentId.asset_class rename audit").

### Addition 2 — `DataStream` gains `data_kind` + `price_type` + optional `bar_spec` (new `DataKind` enum)
**Status: FIXED**
- L151-165: `DataKind` enum with `BAR/TRADE/FUNDING_RATE/MARK_PRICE/INSTRUMENT_INFO` + FIX-mapping docstring.
- L195-212: `DataStream` carries `data_kind`, `bar_spec: BarSpec | None`, `price_type: Literal["LAST","MID","MARK","INDEX"]`, `source`. `__post_init__` enforces the BAR↔bar_spec invariant with two `raise ValueError` branches.
- L679 (AC-D14), L732 (T-D14): AC + test cover both failure modes + price_type rejection + cache-bypass routing.

### Addition 3 — `Subscription` gains `poll_interval_s` + `transport_preference` + `fallback_allowed` with validation
**Status: FIXED**
- L225-241: fields added with defaults + inline comments naming the 4 invariants.
- L687-692 (AC-D18): formal invariant list (gap_policy/STRICT, poll_interval_s presence/absence for FUNDING_RATE vs BAR/TRADE, WS+fallback_allowed=False semantics).
- L736 (T-D18): 4 negative + 1 positive Subscription-validation case.
- Minor: L236 says "asserted in `__new__` or a `validate()` helper" — `Subscription` is a `NamedTuple`, so `__new__` override is the canonical path; ambiguity is tolerable at brief level and resolved in design.

### Addition 4 — Typed `Venue` + `DataClientRegistry` (zero engine changes to add OKX)
**Status: FIXED**
- L168-174: `Venue(str, Enum)` with BINANCE + reserved OKX/BYBIT/DERIBIT.
- L190: `InstrumentId.venue: Venue` (typed).
- L304-315: `DataClientRegistry` with `register/get_clients/registered_venues`; docstring explicitly calls out "adding a new venue is a registry entry, zero engine changes".
- L685 (AC-D17), L735 (T-D17): type assertion, empty-registry failure mode, post-register success, venue-isolation test.
- L659 (AC-D4): engine resolves clients via `registry.get_clients(...)`, not hardcoded list.

### Addition 5 — `TransportMode` enum + split `DataClient` / `LiveDataClient` Protocols
**Status: FIXED**
- L263-273: `TransportMode(PUSH/PULL_ONCE/PULL_SCHEDULED/REPLAY)` with FIX mapping.
- L276-297: `DataClient` (connect/disconnect/request/replay + `supported_modes`) and `LiveDataClient(DataClient)` (subscribe/unsubscribe/subscribe_scheduled) cleanly split.
- L300: split-policy paragraph enumerates per-client `supported_modes` sets.
- L657 (AC-D3), L681 (AC-D15), L733 (T-D15): signatures, `NotImplementedError` on unsupported ops, WS+no-fallback degraded-state expectation.

### Addition 6 — Venue-keyed `InstrumentRegistry` + `VenueCapabilities`
**Status: FIXED**
- L530-541: `VenueCapabilities` dataclass with venue, supported_asset_classes, supported_data_kinds, min_bar_resolution_minutes, has_funding/mark_price/trade_tape flags, rest_weight_budget_per_min.
- L544-567: `InstrumentRegistry` with `_metadata: dict[Venue, dict[InstrumentId, Instrument]]`, `_capabilities: dict[Venue, VenueCapabilities]`, `resolve/metadata/capabilities/list_perp_universe` all venue-keyed.
- L569: fail-fast semantics ("raise at `DataEngine.subscribe(...)` — not silently") when venue cannot serve requested data_kind.
- L667, L729: AC-D8 + T-D11 exercise full capability fields.

### Addition 7 — Full FIX docstring block + AC-D16 grep test
**Status: FIXED**
- L123-144: FIX-block with 14 tags, embedded in a "`v5/data/__init__.py` module docstring MUST include this FIX mapping block verbatim" directive.
- L146: explicit 14-tag enumeration.
- L683 (AC-D16): grep-based acceptance test, ≥ 1 match per tag.
- L734 (T-D16): test spec matches — `grep -R -F` over `v5/data/` docstrings with the 14-tag list verbatim.

**All 7 Round-1 items: FIXED.**

---

## 2. New issues introduced

1. **`Instrument.contract_type` vs `InstrumentId.asset_class` field overlap** (L371 vs L186-192). Brief does not declare whether these are redundant, complementary, or authoritative-vs-derived. `contract_type: str` (free string "perpetual"|"quarterly"|"spot") sits alongside the typed `asset_class` `Literal`. Resolve in design (see Q-b below) — flag only, not blocking.

2. **`Subscription.__new__` vs `validate()` wording** (L236-241). `Subscription` is a `NamedTuple`; the brief says "asserted in `__new__` or a `validate()` helper". NamedTuples get a synthesized `__new__`; overriding it is legal but slightly non-obvious. Prefer picking one in design to avoid multiple-implementation drift. Not blocking.

3. **`DataStream.__post_init__` on a `frozen=True, slots=True` dataclass** (L177, L195, L208-212). Works, but mutating fields from `__post_init__` would require `object.__setattr__`; the current code only raises, which is fine. Mention only because Phase 3 test writers often trip on this — worth a one-line hint in design. Not blocking.

---

## 3. Answers to clarification questions

**Q-a — Registry-vs-list priority ordering: WS > REST > replay retained per-venue?**
Decision: **YES, per-venue**. `DataClientRegistry.get_clients(instrument)` returns the list already ordered by the documented priority (L437: "Binance-WS > Binance-REST > parquet-replay"). Add a one-line note in design that `register(venue, factory)` preserves insertion order per venue and that registered factories declare their transport tier so the engine's priority cascade is deterministic across venues.

**Q-b — `Instrument.contract_type` vs `InstrumentId.asset_class` overlap — coexist or consolidate?**
Decision: **Consolidate in design phase**. `asset_class` is the typed identity field (FIX Product(460), used for routing + resolve). `contract_type` should either (i) be dropped from `Instrument` and derived from `instrument_id.asset_class`, or (ii) be narrowed to expiry-flavor detail ("perpetual" vs "quarterly" vs "monthly" — an expiry sub-type of `asset_class="future"`). Option (ii) is the likely fit for dated-futures forward-compat, so retain `contract_type` with a narrower typed vocabulary in design, not a free `str`.

**Q-c — `price_type` scope: LAST/MID/MARK/INDEX — is MID/INDEX appropriate for aggTrade-only venues? Per-client cross-check deferred?**
Decision: **Defer per-client cross-check to implementation; add capability declaration only**. Binance aggTrades carries LAST only; MID requires L1 book, INDEX requires a dedicated endpoint, MARK is perp-only via `markPrice` stream. `VenueCapabilities` already carries `has_trade_tape` and `has_mark_price`; add `supported_price_types: frozenset[Literal["LAST","MID","MARK","INDEX"]]` to `VenueCapabilities` and have `DataEngine.subscribe(...)` fail fast when `price_type` is not in the venue's declared set. Not a brief-blocker — a one-line Addition-6 extension in the design doc.

---

## 4. Phase 2 readiness checklist

- [x] All 7 additions present
- [x] No new contradictions introduced (3 flagged items are design-phase nits, not blockers)
- [x] 62-80h estimate realistic (69h raw total, L758-780 line-item table sums cleanly; 18h WS stop-trigger is the dominant tail)
- [x] ACs sufficient for test-writer subagent in Phase 3 (T-D1 through T-D19, each maps to a specific AC-D*, each has concrete observable outputs)

---

## 5. Verdict

**APPROVE_FOR_DESIGN**

All 7 Round-1 schema additions are present, consistent, and covered by AC + test pairs. The 3 clarification questions (registry priority, contract_type/asset_class overlap, price_type venue-capability cross-check) have concrete decisions that belong in the design doc, not the brief — none is blocking. The brief is ready for Phase 2.

**Design-phase follow-ups** (carry forward, do not re-open brief):
1. Consolidate `Instrument.contract_type` vocabulary against `InstrumentId.asset_class`.
2. Add `VenueCapabilities.supported_price_types` + fail-fast wiring.
3. Pin `Subscription` invariant enforcement to `__new__` (NamedTuple path).
