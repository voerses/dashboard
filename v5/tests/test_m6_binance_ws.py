"""M6 — BinanceWSClient multiplexing + aggTrades → Trade events (T-D3, T-D13).

All tests MUST FAIL today — v5.data.clients.binance_ws does not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _bar(minutes=1):
    from v5.bar_spec import BarSpec
    from v5.data.streams import BarData, DataStream, InstrumentId, Venue
    inst = InstrumentId(symbol="BTCUSDT", venue=Venue.BINANCE, asset_class="perp")
    return DataStream(instrument=inst, data_class=BarData,
                      bar_spec=BarSpec.from_minutes(minutes))


def _trade():
    from v5.data.streams import DataStream, InstrumentId, TradeData, Venue
    inst = InstrumentId(symbol="BTCUSDT", venue=Venue.BINANCE, asset_class="perp")
    return DataStream(instrument=inst, data_class=TradeData, bar_spec=None)


class TestBinanceWSMultiplexing:
    """T-D3 / AC-D3 — one WS client multiplexes 1m + 1h + aggTrades."""

    def test_supported_modes_includes_push(self):
        from v5.data.clients.binance_ws import BinanceWSClient
        from v5.data.streams import TransportMode
        assert TransportMode.PUSH in BinanceWSClient().supported_modes

    @pytest.mark.parametrize("stream_fn", [lambda: _bar(1), lambda: _bar(60), _trade])
    def test_supports_all_declared_streams(self, stream_fn):
        from v5.data.clients.binance_ws import BinanceWSClient
        from v5.data.streams import TransportMode
        c = BinanceWSClient()
        assert c.supports(stream_fn(), TransportMode.PUSH) is True

    def test_replay_raises_not_implemented(self):
        """T-D15 — WS client does not do REPLAY."""
        from v5.data.clients.binance_ws import BinanceWSClient
        c = BinanceWSClient()
        with pytest.raises(NotImplementedError):
            list(c.replay(_bar(1), start_ns=0, end_ns=10**19))


class TestBinanceWSTradeEvents:
    """T-D13 / AC-D13 — aggTrades → Trade events; duplicate trade_id deduped."""

    def test_subscribe_delivers_trade_events(self):
        from v5.data.clients.binance_ws import BinanceWSClient
        from v5.data.types import Trade
        c = BinanceWSClient()
        delivered: list = []
        c.set_handler(_trade(), delivered.append)
        c.subscribe(_trade())
        c._inject_aggtrade_frame({"s": "BTCUSDT", "T": 1_700_000_000_000,
                                  "p": "100.5", "q": "0.25", "m": False, "a": 1})
        assert len(delivered) == 1
        ev = delivered[0]
        assert isinstance(ev, Trade)
        assert ev.price == 100.5 and ev.qty == 0.25
        assert ev.side in ("BUY", "SELL")
        assert ev.trade_id == 1

    def test_duplicate_trade_id_deduped(self):
        from v5.data.clients.binance_ws import BinanceWSClient
        c = BinanceWSClient()
        delivered: list = []
        c.set_handler(_trade(), delivered.append)
        c.subscribe(_trade())
        frame = {"s": "BTCUSDT", "T": 1_700_000_000_000, "p": "100.5",
                 "q": "0.25", "m": False, "a": 77}
        c._inject_aggtrade_frame(frame)
        c._inject_aggtrade_frame(frame)
        assert len(delivered) == 1

    def test_aggressor_side(self):
        """m=False → BUY, m=True → SELL."""
        from v5.data.clients.binance_ws import BinanceWSClient
        c = BinanceWSClient()
        delivered: list = []
        c.set_handler(_trade(), delivered.append)
        c.subscribe(_trade())
        c._inject_aggtrade_frame({"s": "BTCUSDT", "T": 1, "p": "1", "q": "1",
                                  "m": False, "a": 10})
        c._inject_aggtrade_frame({"s": "BTCUSDT", "T": 2, "p": "1", "q": "1",
                                  "m": True, "a": 11})
        assert delivered[0].side == "BUY"
        assert delivered[1].side == "SELL"
