"""M6 — engine exceptions.

No FIX mapping — these are runtime-plumbing exceptions. Brief §"FIX vocabulary
block" explicitly calls out these exception types as having no FIX analog.
"""
from __future__ import annotations


class DataGapError(Exception):
    """Raised when GapPolicy.STRICT + REST backfill retries exhausted."""


class RateLimitExceeded(Exception):
    """Raised by BinanceRESTClient when venue weight budget would go negative.

    `retry_after_ms` tells the caller when to retry.
    """

    def __init__(self, retry_after_ms: int, message: str | None = None):
        self.retry_after_ms = retry_after_ms
        super().__init__(
            message or f"Binance weight budget exhausted; retry after {retry_after_ms}ms"
        )


class PriceTypeNotSupported(Exception):
    """Raised by DataEngine.subscribe() when a Subscription requests a price_type
    not in the venue's VenueCapabilities.supported_price_types (AC-D18 C3).
    """

    def __init__(self, venue, price_type: str, message: str | None = None):
        self.venue = venue
        self.price_type = price_type
        super().__init__(
            message or f"Venue {venue!r} does not support price_type={price_type!r}"
        )


class SymbolNotFound(Exception):
    """Raised by InstrumentRegistry.resolve() when the symbol is unknown at the venue."""

    def __init__(self, symbol: str, venue, message: str | None = None):
        self.symbol = symbol
        self.venue = venue
        super().__init__(message or f"Symbol {symbol!r} not found at venue {venue!r}")


class ClockDriftHigh(Exception):
    """Published as an event (AC-D11) when |venue_clock_offset_ms| > 5000.

    Dual-purpose: an Exception subclass used as the event payload delivered
    to drift-event handlers (see BinanceRESTClient.subscribe_drift_events).
    Not currently raised — only dispatched to registered handlers.
    """

    def __init__(self, drift_ms: int, venue=None, message: str | None = None):
        self.drift_ms = drift_ms
        self.venue = venue
        super().__init__(message or f"Clock drift {drift_ms}ms exceeds 5000ms threshold")
