"""M6 — Instrument metadata + VenueCapabilities + InstrumentRegistry (AC-D8).

FIX vocabulary:
  Instrument.tick_size     → MinPriceIncrement(969)
  Instrument.min_notional  → MinTradeVol(562)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Literal, Optional, Tuple

from v5.data.exceptions import SymbolNotFound
from v5.data.streams import DataKind, InstrumentId, Venue


def _tm_import():
    """Lazy TransportMode import — avoids circular import at module load."""
    from v5.data.streams import TransportMode
    return TransportMode

_VALID_CONTRACT_SUBTYPES = frozenset({
    "perpetual", "quarterly", "dated", "european", "american",
})


@dataclass(slots=True)
class Instrument:
    """Per-instrument metadata fetched from venue `exchangeInfo` at connect.

    FIX mapping:
      - tick_size    → MinPriceIncrement(969)
      - min_notional → MinTradeVol(562)
    """

    instrument_id: InstrumentId
    tick_size: float
    lot_size: float
    min_notional: float
    maker_fee_bps: float
    taker_fee_bps: float
    margin_tiers: List[Tuple[float, float]]
    max_leverage: float
    funding_interval_s: int
    contract_subtype: Optional[
        Literal["perpetual", "quarterly", "dated", "european", "american"]
    ] = None
    # M9 Wave D: legacy free-string contract_type field DELETED. Callers
    # must read `contract_subtype: Literal[...]` (typed enum).

    def __post_init__(self):
        if self.contract_subtype is not None and self.contract_subtype not in _VALID_CONTRACT_SUBTYPES:
            raise ValueError(
                f"contract_subtype must be one of {sorted(_VALID_CONTRACT_SUBTYPES)} or None, "
                f"got {self.contract_subtype!r}"
            )


@dataclass(frozen=True, slots=True)
class VenueCapabilities:
    """Venue capability declaration — populated at DataClient.connect().

    DataEngine.subscribe() consults these to fail fast when a Subscription
    requests data the venue cannot serve.
    """

    venue: Venue
    supported_asset_classes: FrozenSet[Literal["spot", "perp", "future", "option"]]
    supported_data_kinds: FrozenSet[DataKind]
    min_bar_resolution_minutes: int
    has_funding: bool
    has_mark_price: bool
    has_trade_tape: bool
    rest_weight_budget_per_min: int
    supported_price_types: FrozenSet[Literal["LAST", "MID", "MARK", "INDEX"]]
    # M7 AC-D15: which transport modes the venue supports. Additive field;
    # strategies check symmetric to supports(DataKind.TRADE) via supports(mode).
    # Default frozenset() keeps M6 callers working; Binance at connect()
    # declares {PUSH, PULL_ONCE, PULL_SCHEDULED}.
    supported_transport_modes: FrozenSet = field(default_factory=frozenset)

    def supports(self, capability) -> bool:
        """Unified capability query — works for DataKind OR TransportMode.

        Returns True iff the capability is declared in the corresponding
        supported_* set.
        """
        from v5.data.streams import DataKind as _DK, TransportMode as _TM
        if isinstance(capability, _DK):
            return capability in self.supported_data_kinds
        if isinstance(capability, _TM):
            return capability in self.supported_transport_modes
        return False


# Canonical-token → venue-symbol aliases for 1000-prefix micro-cap tokens.
# Binance lists high-supply tokens with a 1000× multiplier prefix.
_BINANCE_SYMBOL_ALIASES: Dict[str, str] = {
    "SHIB": "1000SHIBUSDT",
    "PEPE": "1000PEPEUSDT",
    "BONK": "1000BONKUSDT",
    "FLOKI": "1000FLOKIUSDT",
    "XEC": "1000XECUSDT",
    "LUNC": "1000LUNCUSDT",
    "SATS": "1000SATSUSDT",
    "RATS": "1000RATSUSDT",
}

# Minimal seed universe — Phase-4 Task 6 scope (real impl populates from
# exchangeInfo at connect()). Matches strategies that subscribe in tests.
_SEED_PERP_TOKENS: Tuple[str, ...] = (
    "BTC", "ETH", "SOL", "XRP", "ADA", "AVAX", "DOT", "MATIC", "LINK",
    "BNB", "DOGE", "TRX", "LTC", "ATOM", "NEAR", "UNI", "ETC", "FIL",
    "OP", "ARB", "APT", "SUI", "SEI", "TIA", "INJ",
)


class InstrumentRegistry:
    """Venue-keyed metadata + capability store (AC-D8, AC-D17).

    Populated at DataEngine.start() by each venue DataClient.connect()
    writing its own _metadata[venue] and _capabilities[venue] slices.

    This in-memory bootstrapped version carries a seed universe + static
    capability declarations so strategies can resolve symbols before any
    venue client connects — matches existing v4 behavior where universe
    files are scanned at startup.
    """

    def __init__(self):
        self._metadata: Dict[Venue, Dict[InstrumentId, Instrument]] = {}
        self._capabilities: Dict[Venue, VenueCapabilities] = {}
        self._symbol_aliases: Dict[Venue, Dict[str, str]] = {
            Venue.BINANCE: dict(_BINANCE_SYMBOL_ALIASES),
        }
        self._bootstrap_binance()

    def _bootstrap_binance(self) -> None:
        caps = VenueCapabilities(
            venue=Venue.BINANCE,
            supported_asset_classes=frozenset({"spot", "perp"}),
            supported_data_kinds=frozenset({
                DataKind.BAR, DataKind.TRADE,
                DataKind.FUNDING_RATE, DataKind.MARK_PRICE,
                DataKind.INSTRUMENT_INFO,
            }),
            min_bar_resolution_minutes=1,
            has_funding=True,
            has_mark_price=True,
            has_trade_tape=True,
            rest_weight_budget_per_min=1200,
            supported_price_types=frozenset({"LAST", "MARK", "INDEX"}),
            supported_transport_modes=frozenset({
                # Binance is a live venue — PUSH via WS, PULL_ONCE + PULL_SCHEDULED
                # via REST. No REPLAY (that's ParquetReplayClient's domain).
                _tm_import().PUSH, _tm_import().PULL_ONCE, _tm_import().PULL_SCHEDULED,
            }),
        )
        self._capabilities[Venue.BINANCE] = caps

        md: Dict[InstrumentId, Instrument] = {}
        for token in _SEED_PERP_TOKENS:
            venue_symbol = self._resolve_venue_symbol(token, Venue.BINANCE)
            iid = InstrumentId(symbol=venue_symbol, venue=Venue.BINANCE, asset_class="perp")
            md[iid] = Instrument(
                instrument_id=iid,
                tick_size=0.1,
                lot_size=0.001,
                min_notional=5.0,
                maker_fee_bps=2.0,
                taker_fee_bps=4.0,
                margin_tiers=[(50_000.0, 0.01), (250_000.0, 0.025)],
                max_leverage=20.0,
                funding_interval_s=28800,
                contract_subtype="perpetual",
            )
        # Also register the 1000-prefix aliased tokens at their venue symbols
        for canonical, venue_symbol in _BINANCE_SYMBOL_ALIASES.items():
            iid = InstrumentId(symbol=venue_symbol, venue=Venue.BINANCE, asset_class="perp")
            md[iid] = Instrument(
                instrument_id=iid,
                tick_size=0.00001,
                lot_size=1.0,
                min_notional=5.0,
                maker_fee_bps=2.0,
                taker_fee_bps=4.0,
                margin_tiers=[(50_000.0, 0.025)],
                max_leverage=10.0,
                funding_interval_s=28800,
                contract_subtype="perpetual",
            )
        self._metadata[Venue.BINANCE] = md

    def _resolve_venue_symbol(self, symbol: str, venue: Venue) -> str:
        """Translate canonical token to venue symbol (handles 1000-prefix aliases)."""
        aliases = self._symbol_aliases.get(venue, {})
        # If the caller passed the canonical token, alias-lookup; otherwise pass-through
        if symbol in aliases:
            return aliases[symbol]
        # If caller already passed the venue symbol ("1000SHIBUSDT"), accept
        if symbol.endswith("USDT"):
            return symbol
        return f"{symbol}USDT"

    def resolve(self, symbol: str, venue: Venue, asset_class: str) -> InstrumentId:
        """Canonical symbol→InstrumentId lookup. Raises SymbolNotFound if unknown."""
        venue_symbol = self._resolve_venue_symbol(symbol, venue)
        iid = InstrumentId(symbol=venue_symbol, venue=venue, asset_class=asset_class)
        if iid not in self._metadata.get(venue, {}):
            raise SymbolNotFound(symbol, venue)
        return iid

    def metadata(self, instrument: InstrumentId) -> Instrument:
        """Return the Instrument metadata block."""
        try:
            return self._metadata[instrument.venue][instrument]
        except KeyError:
            raise SymbolNotFound(instrument.symbol, instrument.venue) from None

    def capabilities(self, venue: Venue) -> VenueCapabilities:
        """Return declared venue capabilities."""
        caps = self._capabilities.get(venue)
        if caps is None:
            raise KeyError(f"no capabilities registered for venue {venue!r}")
        return caps

    def list_perp_universe(self, venue: Venue = Venue.BINANCE) -> List[InstrumentId]:
        """Enumerate all active perp contracts at venue."""
        return [
            iid for iid in self._metadata.get(venue, {})
            if iid.asset_class == "perp"
        ]


# M10 E2 / AC #21 — fee schedule lookup by schedule name.
# Returns the canonical maker/taker bps for a named schedule. Used by
# AC #21 fee-classification test + Phase-4 fill-path classification.
_FEE_SCHEDULES = {
    "binance_perp": {"maker_bps": 2.0, "taker_bps": 4.0},
    "binance_spot": {"maker_bps": 10.0, "taker_bps": 10.0},
}


def get_fee_schedule(schedule_name: str) -> dict:
    """Return maker/taker bps schedule for the given schedule name.

    Args:
        schedule_name: e.g. "binance_perp", "binance_spot".

    Returns:
        {"maker_bps": float, "taker_bps": float}

    Raises:
        KeyError: if schedule_name is unknown.
    """
    if schedule_name not in _FEE_SCHEDULES:
        raise KeyError(
            f"Unknown fee schedule {schedule_name!r}; known: "
            f"{sorted(_FEE_SCHEDULES)!r}"
        )
    return dict(_FEE_SCHEDULES[schedule_name])
