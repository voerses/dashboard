"""M6 — Instrument + InstrumentRegistry + VenueCapabilities (T-D11 / AC-D8).

All tests MUST FAIL today — v5.data.instruments does not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _mk_instrument(asset_class="perp", contract_subtype="perpetual"):
    from v5.data.instruments import Instrument
    from v5.data.streams import InstrumentId, Venue
    iid = InstrumentId(symbol="BTCUSDT", venue=Venue.BINANCE, asset_class=asset_class)
    return Instrument(
        instrument_id=iid, tick_size=0.1, lot_size=0.001, min_notional=5.0,
        maker_fee_bps=2.0, taker_fee_bps=4.0,
        margin_tiers=[(50_000.0, 0.01)], max_leverage=20.0,
        funding_interval_s=28800, contract_subtype=contract_subtype,
    )


class TestInstrumentDataclass:
    """AC-D8 / C2."""

    def test_instrument_fields(self):
        inst = _mk_instrument()
        assert inst.tick_size == 0.1
        assert inst.lot_size == 0.001
        assert inst.min_notional == 5.0
        assert inst.maker_fee_bps == 2.0
        assert inst.taker_fee_bps == 4.0
        assert inst.margin_tiers == [(50_000.0, 0.01)]
        assert inst.max_leverage == 20.0
        assert inst.funding_interval_s == 28800
        assert inst.contract_subtype == "perpetual"

    def test_invalid_contract_subtype_raises(self):
        """C2 strict. # NOTE: brief ambiguous at AC-D8/C2; strict interpretation."""
        from v5.data.instruments import Instrument
        from v5.data.streams import InstrumentId, Venue
        iid = InstrumentId(symbol="BTCUSDT", venue=Venue.BINANCE, asset_class="spot")
        with pytest.raises((ValueError, TypeError)):
            Instrument(instrument_id=iid, tick_size=0.1, lot_size=0.001, min_notional=5.0,
                       maker_fee_bps=2.0, taker_fee_bps=4.0, margin_tiers=[],
                       max_leverage=1.0, funding_interval_s=0,
                       contract_subtype="bogus")  # type: ignore[arg-type]

    def test_spot_contract_subtype_is_none(self):
        assert _mk_instrument(asset_class="spot", contract_subtype=None).contract_subtype is None


class TestVenueCapabilities:
    """AC-D8 T-D11."""

    def test_venue_capabilities_fields(self):
        from v5.data.instruments import VenueCapabilities
        from v5.data.streams import BarData, TradeData, Venue
        caps = VenueCapabilities(
            venue=Venue.BINANCE,
            supported_asset_classes=frozenset({"spot", "perp"}),
            supported_data_classes=frozenset({BarData, TradeData}),
            min_bar_resolution_minutes=1,
            has_funding=True, has_mark_price=True, has_trade_tape=True,
            rest_weight_budget_per_min=1200,
            supported_price_types=frozenset({"LAST", "MARK", "INDEX"}),
        )
        assert caps.venue is Venue.BINANCE
        assert caps.has_funding and caps.has_mark_price and caps.has_trade_tape
        assert caps.min_bar_resolution_minutes == 1
        assert caps.rest_weight_budget_per_min == 1200
        assert "MID" not in caps.supported_price_types


class TestInstrumentRegistry:
    """T-D11 / AC-D8."""

    def test_resolve_known_symbol(self):
        from v5.data.instruments import InstrumentRegistry
        from v5.data.streams import Venue
        iid = InstrumentRegistry().resolve("BTCUSDT", venue=Venue.BINANCE, asset_class="perp")
        assert iid.symbol == "BTCUSDT"
        assert iid.venue is Venue.BINANCE
        assert iid.asset_class == "perp"

    def test_unknown_symbol_raises(self):
        from v5.data.exceptions import SymbolNotFound
        from v5.data.instruments import InstrumentRegistry
        from v5.data.streams import Venue
        with pytest.raises(SymbolNotFound):
            InstrumentRegistry().resolve("NOT_A_REAL_SYMBOL", venue=Venue.BINANCE,
                                         asset_class="perp")

    def test_metadata_populated(self):
        from v5.data.instruments import InstrumentRegistry
        from v5.data.streams import Venue
        reg = InstrumentRegistry()
        iid = reg.resolve("BTCUSDT", venue=Venue.BINANCE, asset_class="perp")
        inst = reg.metadata(iid)
        assert inst.tick_size > 0 and inst.lot_size > 0
        assert inst.min_notional > 0 and inst.max_leverage >= 1.0

    def test_capabilities_for_binance(self):
        from v5.data.instruments import InstrumentRegistry
        from v5.data.streams import Venue
        caps = InstrumentRegistry().capabilities(Venue.BINANCE)
        assert caps.has_funding and caps.has_mark_price and caps.has_trade_tape
        assert caps.min_bar_resolution_minutes == 1
        assert caps.rest_weight_budget_per_min == 1200

    def test_list_perp_universe(self):
        from v5.data.instruments import InstrumentRegistry
        from v5.data.streams import InstrumentId, Venue
        u = InstrumentRegistry().list_perp_universe(venue=Venue.BINANCE)
        assert isinstance(u, list) and len(u) > 0
        assert all(isinstance(x, InstrumentId) for x in u)
        assert all(x.asset_class == "perp" and x.venue is Venue.BINANCE for x in u)


class TestInstrumentRegistryAliasResolution:
    """AC-D8 / design §6 — 1000-prefix crypto micro-cap alias handling.

    Binance lists high-supply micro-cap tokens with a '1000' multiplier prefix
    (1000SHIBUSDT trades at ~1000× the native unit). Strategies MUST pass the
    canonical token name ('SHIB'); the registry's _symbol_aliases layer
    resolves it to the venue-specific symbol. Without this layer, strategies
    would need to know venue-internal symbol conventions — violating the
    InstrumentRegistry-as-sole-sanctioned-constructor rule (brief line 630).
    """

    @pytest.mark.parametrize(
        "canonical, expected_venue_symbol",
        [
            ("SHIB", "1000SHIBUSDT"),
            ("PEPE", "1000PEPEUSDT"),
            ("BTC", "BTCUSDT"),  # non-aliased — canonical == venue
            ("ETH", "ETHUSDT"),  # non-aliased — canonical == venue
        ],
    )
    def test_1000_prefix_alias_resolves(self, canonical, expected_venue_symbol):
        from v5.data.instruments import InstrumentRegistry
        from v5.data.streams import Venue
        reg = InstrumentRegistry()
        iid = reg.resolve(canonical, venue=Venue.BINANCE, asset_class="perp")
        assert iid.symbol == expected_venue_symbol, (
            f"resolve({canonical!r}) returned {iid.symbol!r} but expected "
            f"{expected_venue_symbol!r} — alias layer missing or incorrect."
        )

    def test_resolve_is_symmetric_for_already_canonical(self):
        """Passing the venue-symbol directly (1000SHIBUSDT) also resolves — defensive."""
        from v5.data.instruments import InstrumentRegistry
        from v5.data.streams import Venue
        reg = InstrumentRegistry()
        iid = reg.resolve("1000SHIBUSDT", venue=Venue.BINANCE, asset_class="perp")
        assert iid.symbol == "1000SHIBUSDT"
