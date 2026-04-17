# M5 — Multi-leg Order.legs

**Summary**: Replace the three divergent "pending order" representations (PendingEntry, _armed_tokens, combined primary/secondary) with a unified `Order` + `Leg` model in `v5/orders.py`, aligned to FIX protocol vocabulary.

---

## Problem

v4 has three separate representations for what is conceptually the same thing — a pending order that may have multiple conditions or legs:

1. **PendingEntry dataclass** — used by the simulator for signal-driven entries
2. **_armed_tokens dict** — used by the armed-entry engine for price-triggered entries (stop orders)
3. **Combined primary/secondary fields** — used by combined strategies that pair a primary signal with a secondary confirmation

These diverged independently, leading to:
- Armed entries that can't be serialized/restored in paper state (restart loses armed orders)
- No way to express multi-leg orders (e.g., entry + stop + take-profit as one atomic unit)
- FIX LegGrp(555) misalignment — our "orders" don't map to standard trading vocabulary
- `armed_log.jsonl` uses a different schema than trade_log entries

---

## Scope

### In scope

- New `v5/orders.py` module with:
  - `LegStatus` enum:
    ```python
    class LegStatus(Enum):
        ARMED = "armed"              # waiting for trigger price (FIX OrdType=Stop/StopLimit)
        WORKING = "working"          # active open position
        PARTIALLY_FILLED = "partially_filled"  # partial fill received
        FILLED = "filled"            # terminal — fully closed
        EXPIRED = "expired"          # armed but trigger window passed
        CANCELLED = "cancelled"      # explicit cancel
        REJECTED = "rejected"        # FIX OrdStatus(39)=8 — exchange refused the leg
    ```
    Note: `REJECTED` is distinct from `CANCELLED` — REJECTED means the exchange refused the leg (insufficient margin, delisted), while CANCELLED means the engine or strategy cancelled it. REJECTED triggers `LegFillPolicy` evaluation.
  - `Leg` dataclass (full FIX-aligned version):
    ```python
    @dataclass
    class Leg:
        leg_ref_id: str              # FIX LegRefID(654)
        symbol: str                  # token
        market: str                  # "spot" | "perp"
        venue: str                   # exchange
        direction: int               # +1 long, -1 short
        target_qty: float            # FIX LegQty
        cum_qty: float               # cumulative filled
        size_share: float            # fraction of order's total allocation — FIX LegRatioQty
        order_type: str              # "market" | "stop" | "stop_limit" | "limit"
        status: LegStatus
        trigger_price: float | None  # for STOP/STOP_LIMIT — FIX StopPx(99)
        limit_price: float | None    # FIX LegPrice
        currency: str                # FIX LegCurrency
        position_id: str | None      # populated on WORKING transition
    ```
    FIX tag mappings per field:
    - `leg_ref_id` -> FIX LegRefID(654)
    - `target_qty` -> FIX LegQty
    - `size_share` -> FIX LegRatioQty
    - `trigger_price` -> FIX StopPx(99)
    - `limit_price` -> FIX LegPrice
    - `currency` -> FIX LegCurrency
    - `order_type` -> derived from FIX OrdType(40) per leg
  - `Order` dataclass:
    ```python
    @dataclass
    class Order:
        order_id: str                # FIX ClOrdID(11) / OrderID(37) analog — unique per order
        legs: list[Leg]              # one-or-more; single-leg order is just [Leg(...)]
        priority: float | None       # order-level sort key (from C-1 allocation)
        armed_at_bar: int | None     # when arming fired (for audit)
        window_end_bar: int | None   # TimeInForce=GTD deadline (FIX)
        leg_fill_policy: LegFillPolicy
    ```
  - `LegFillPolicy` enum:
    ```python
    class LegFillPolicy(Enum):
        UNWIND_ON_REJECT = "unwind_on_reject"  # default — if leg B fails after leg A fills, market-close leg A
        BEST_EFFORT = "best_effort"            # fill what you can, leave unfilled legs cancelled
    ```
    Note: `LegFillPolicy` governs multi-leg ORDER fill behavior (Order-level). `LinkedScalePolicy` governs SCALING EVENT propagation between linked Positions (Position-level). Both conceptually map to FIX ContingencyType(1385) but at different lifecycle layers. Different enum names eliminate the collision.

    Note: `UNWIND_ON_REJECT` means: if leg B fails after leg A fills, engine market-closes leg A with slippage. Exchange fills are irrevocable — there is no true atomic guarantee in crypto.
  - `OrderStatus` derived from leg statuses
- `LegFillPolicy` semantics: `UNWIND_ON_REJECT` (default — if any leg is rejected after another fills, engine market-closes the filled leg with slippage), `BEST_EFFORT` (fill what you can, leave unfilled legs cancelled)
- FIX LegGrp(555) alignment: Order.legs maps to FIX multi-leg structure exactly
  - FIX field mappings on Order: `order_id` -> FIX ClOrdID(11), `legs` -> FIX LegGrp(555)
  - `LegStatus.ARMED` = FIX `OrdType=Stop(3)` / `StopLimit(4)` with `OrdStatus=New(0)` / `PendingNew(A)`
  - `LegStatus.WORKING` = FIX `OrdStatus=PartiallyFilled(1)` (once entry fills)
  - `LegStatus.FILLED` = FIX `OrdStatus=Filled(2)`
  - `LegStatus.EXPIRED` = FIX `OrdStatus=Expired(C)`
  - Nautilus BracketOrder uses the same list pattern
- Unify PendingEntry into Order with single entry leg
- Unify _armed_tokens into Order with stop-trigger leg
- Unify combined primary/secondary into Order with two legs
- **Order -> Position cardinality (AC-P7)**: One Order with N legs -> N Positions, joined by `order_id`. `Position.order_id: str` links open positions back to their originating Order (FIX `OrderID(37)` / `LegGrp(555)` analog). A single-leg market entry is `Order(legs=[Leg(status=WORKING, order_type="market")])` — zero overhead.
- Paper state serialization: Order.legs round-trips through JSON state file
- **Trigger function serializability constraint**: Serializable triggers only — no opaque Python callables in paper_state. Trigger conditions are expressed via `trigger_price` (for stop/stop_limit orders) and `window_end_bar` (for GTD expiry), NOT via Python functions. This ensures paper_state roundtrips through JSON without pickle or other opaque serialization.
- **Trigger evaluation cadence**: Armed entry trigger prices (`Leg.trigger_price`) are evaluated at the BarProcessor's resolution. For hourly strategies, this means once per hour. For sub-hourly strategies (e.g., 5-min `bar_spec`), triggers check every 5 minutes. This is a change from v4 which checked every 1-minute WS candle regardless of strategy resolution. Strategies needing minute-level trigger granularity should declare `bar_spec=BarSpecification(1, MINUTE)` in their `required_data()`.
- armed_log.jsonl backward compatibility: new Order writes entries parseable by existing log readers
  - **armed_log.jsonl event types**: arm, fire, expire, cancel — audit trail preserved (dashboard depends on it)
  - v5 reads v1 log format + writes v2 going forward
- BarProcessor (M4) integration: Phase 3 processes Order objects instead of raw PendingEntry
- **Per-leg fees**: Each `Leg` in a multi-leg Order may have different fee structures (spot vs perp). Per-leg fees are resolved from `Instrument.maker_fee_bps`/`taker_fee_bps` (M6) at fill time, not stored on the Leg itself.

### Out of scope

- Exchange-side multi-leg order submission (OCO, bracket) — this is internal model only
- Order matching engine / exchange simulator
- Historical order replay / order book simulation
- Strategy API changes for order creation (M7)
- Sizing integration with Order (M8)

---

## Key Acceptance Criteria

1. **Single Order type**: All pending entries, armed entries, and combined entries are represented as `Order` with one or more `Leg` objects. PendingEntry and _armed_tokens are deleted.

2. **PendingEntry + _armed_tokens unification specifics**:
   - `PendingEntry` dataclass + `SimulationState.pending_entries` list -> `SimulationState.open_orders: list[Order]` with `[o for o in open_orders if any(leg.status == ARMED for leg in o.legs)]` for armed-filtering
   - Paper `_armed_tokens` dict -> same `open_orders` list; no separate dict, no separate lock
   - `_serialize_armed_tokens` function is ELIMINATED — serialize `open_orders` normally via standard JSON serialization
   - `conviction` field frozen at arm time -> replaced by `priority` field frozen at arm time (C-1 vocabulary change)
   - Single-leg market entries (common case): `Order(legs=[Leg(status=WORKING, order_type="market")])` — zero overhead
   - Multi-leg combined: `Order(legs=[Leg(primary), Leg(secondary)])` — spot+perp pair
   - Armed entries (waiting for price): `Order(legs=[Leg(status=ARMED, order_type="stop", trigger_price=71000)])` — same schema, different status
   - Multi-leg + armed combines naturally: `Order(legs=[Leg(status=ARMED, ...), Leg(status=ARMED, ...)])` for paired arming

3. **Leg lifecycle**: Each Leg transitions through LegStatus states (ARMED -> WORKING -> PARTIALLY_FILLED -> FILLED, or ARMED -> EXPIRED/CANCELLED). An Order's status is derived from its legs (e.g., Order is FILLED when all required legs are FILLED per the fill policy).

4. **Fill policies**: `UNWIND_ON_REJECT` (default) market-closes already-filled legs if any subsequent leg is rejected. `BEST_EFFORT` fills whatever it can. `leg_fill_policy` is a field on `Order` with type `LegFillPolicy`. Each policy has a unit test.

5. **Paper state roundtrip**: Orders (including multi-leg) serialize to JSON and deserialize with no data loss. A test creates an Order with 3 legs in various states, serializes, deserializes, and asserts equality. **Trigger function serializability constraint**: Serializable triggers only (no opaque Python callables in paper_state). Trigger conditions use `trigger_price` and `window_end_bar` fields, not Python functions.

6. **armed_log.jsonl compat**: New Order-based armed entries write log entries that the existing `tools/parse_armed_log.py` (or equivalent) can read. Schema additions are additive only. Event types written to armed_log.jsonl: `arm`, `fire`, `expire`, `cancel`.

7. **FIX alignment**: Order fields map to FIX ClOrdID(11), OrdType(40), Side(54), LegGrp(555). Per-leg FIX mappings: `leg_ref_id` -> LegRefID(654), `target_qty` -> LegQty, `size_share` -> LegRatioQty, `trigger_price` -> StopPx(99), `limit_price` -> LegPrice, `currency` -> LegCurrency. Mapping is documented in module docstring.

8. **Order -> Position cardinality (AC-P7)**: One Order with N legs -> N Positions, joined by `order_id`. `Position.order_id: str` links open positions back to their originating Order. A single-leg order creates one Position; a multi-leg combined order creates N Positions (one per leg). Counting for `max_positions_per_symbol` treats multi-leg orders as 1 logical entry, not N Position rows.

9. **Dust-cascade invariant for linked legs (AC-P10)**: After PROPORTIONAL or ABSOLUTE auto-propagation, if EITHER leg was promoted to terminal (via dust threshold), the engine MUST force-close the OTHER leg with `exit_reason="linked_exit"`. Prevents the hazard where primary 5x99%-reduce cascades + ABSOLUTE propagation leaves secondary at sub-dust but still "open".

10. **BarProcessor integration**: BarProcessor.process_bar Phase 3 accepts `list[Order]` and processes legs according to fill policy. Old PendingEntry path is removed.

---

## Dependencies

| Milestone | Relationship |
|-----------|-------------|
| **M4** (BarProcessor) | **Required** — BarProcessor handles Order lifecycle in Phase 3 |
| **M1** (v5 Fork) | **Required** — v5 namespace |
| **M2** (Position Scaling) | **Benefits from** — ScaleAction can reference parent Order for audit trail |

---

## Time Estimate

**20-30 hours**

- ~5h: Order + Leg + enum design and core dataclasses
- ~5h: Fill policy logic and state machine
- ~5h: Migrate PendingEntry + _armed_tokens + combined into Order
- ~4h: Paper state serialization + deserialization
- ~3h: BarProcessor Phase 3 integration
- ~3h: armed_log.jsonl compat + migration
- ~3h: Tests + parity verification
- ~2h: Documentation + FIX alignment notes

---

## Test Plan (from source brief)

- **T-O1 (Single-leg market entry)**: Create `Order(legs=[Leg(status=WORKING, order_type="market")])`. Process via BarProcessor Phase 3. Assert one Position created with correct `order_id` link. Verify zero overhead — single-leg is the common path.

- **T-O2 (Armed entry lifecycle)**: Create `Order(legs=[Leg(status=ARMED, order_type="stop", trigger_price=71000)])`. Feed bars where price does NOT reach trigger -> Leg stays ARMED. Feed bar where `bar.low <= 71000` -> Leg transitions to WORKING, Position created. Verify armed_log.jsonl has `arm` event at creation and `fire` event at trigger.

- **T-O3 (Armed entry expiry)**: Create Order with `window_end_bar=50`. Feed bars 0-49 without trigger -> at bar 50, Leg transitions to EXPIRED, Order removed from open_orders. Verify armed_log.jsonl has `expire` event.

- **T-O4 (Multi-leg combined spot+perp)**: Create `Order(legs=[Leg(market="spot", ...), Leg(market="perp", ...)])`. Both legs trigger simultaneously. Assert 2 Positions created, both with same `order_id`. `max_positions_per_symbol` counts this as 1 logical entry.

- **T-O5 (UNWIND_ON_REJECT fill policy)**: Create multi-leg Order with `leg_fill_policy=LegFillPolicy.UNWIND_ON_REJECT`. First leg fills, second leg rejected (e.g., insufficient margin). Assert first leg's Position is market-closed (unwound), both legs set to terminal status. Verify armed_log.jsonl has `cancel` event.

- **T-O6 (BEST_EFFORT fill policy)**: Create 3-leg Order with `leg_fill_policy=LegFillPolicy.BEST_EFFORT`. First leg fills, second leg rejected. Assert first leg's Position kept, remaining legs CANCELLED. Third leg still attempted independently.

- **T-O7 (Paper state roundtrip)**: Create Order with 3 legs in mixed states (ARMED, WORKING, FILLED). Serialize to JSON paper_state, deserialize. Assert all fields round-trip exactly: `order_id`, `legs` (with all Leg fields including `trigger_price`, `limit_price`, `position_id`), `priority`, `armed_at_bar`, `window_end_bar`, `leg_fill_policy`.

- **T-O8 (armed_log.jsonl backward compat)**: Write Order-based armed entries. Read back via existing `tools/parse_armed_log.py` (or equivalent). Assert all v1 fields present and parseable. New v2 fields (e.g., `order_id`, `leg_ref_id`) are additive — existing readers ignore them.

## Parity Gate

- Armed entry tests pass with Order-based implementation
- Combined-strategy tests pass with multi-leg Orders
- Paper state roundtrip preserves armed entries across restart
- All v5 tests pass with no behavior change for single-leg (normal entry) paths
- armed_log.jsonl entries remain parseable by existing tooling
