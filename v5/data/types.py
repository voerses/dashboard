"""M6 — Non-bar event types (AC-D7, AC-D13).

Only event types with a current or near-term consumer are shipped in M6.
BookSnapshot / Liquidation / OpenInterest / Ticker / IndexPrice are deferred
(see brief §"Deferred event types").

FIX vocabulary:
  Trade owns MDEntryType(269)=2 Trade + Side(54) aggressor
  FundingRate — no FIX standard; vendor extension (MDEntryType='f' proposed)
  MarkPrice owns MDEntryType(269)=6 SettlementPrice (approximate)
  Bar.ts_event owns TransactTime(60)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(slots=True)
class FundingRate:
    """Perp funding rate event — no FIX standard.

    FIX mapping: vendor extension (MDEntryType(269)='f' proposed).
    Consumed by M4 AC38 funding-ceiling handler + sizing.
    """
    instrument_id: Any
    ts_event: int
    rate: float
    next_funding_ts: int


@dataclass(slots=True)
class MarkPrice:
    """Venue mark price.

    FIX mapping: MDEntryType(269)=6 SettlementPrice — approximate.
    Consumed by M4 mark-trigger stop/liquidation path.
    """
    instrument_id: Any
    ts_event: int
    mark_price: float
    index_price: float


@dataclass(slots=True)
class Trade:
    """Tape/print from Binance aggTrades WS.

    FIX mapping: MDEntryType(269)=2 Trade + Side(54) aggressor side.
    Binance-WS convention: aggTrade `m` field = "buyer-is-maker"; aggressor
    side is SELL when m=True, BUY when m=False.
    """
    instrument_id: Any
    ts_event: int
    price: float
    qty: float
    side: Literal["BUY", "SELL"]
    trade_id: int
