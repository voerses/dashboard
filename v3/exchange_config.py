"""
Exchange Configuration — Token availability, fees, and pair formats.

Preserves essential exchange data from the former exchange_registry module:
token listings, fee tiers, rate limits, and pair format conventions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set


@dataclass
class ExchangeConfig:
    """Configuration for one exchange."""

    name: str
    display_name: str

    # Token availability — set of base currencies listed with USDT
    listed_tokens: Set[str]

    # Fee structure
    maker_fee: float
    taker_fee: float
    fee_notes: str = ''

    # Rate limiting
    rate_limit_ms: int = 1000
    max_pairs: int = 50
    process_throttle_secs: int = 5

    # Pair format
    quote_currency: str = 'USDT'
    pair_separator: str = '/'
    ticker_aliases: Dict[str, str] = field(default_factory=dict)

    # Order constraints
    min_order_usdt: float = 10.0

    def format_pair(self, token: str) -> str:
        """Format a token into this exchange's pair string."""
        base = self.ticker_aliases.get(token, token)
        return f"{base}{self.pair_separator}{self.quote_currency}"

    def supports_token(self, token: str) -> bool:
        """Check if this exchange lists the token."""
        return token in self.listed_tokens

    def get_pair_whitelist(self, tokens: List[str]) -> List[str]:
        """Get pair list for the given tokens, filtered to listed ones."""
        return [self.format_pair(tk) for tk in tokens if self.supports_token(tk)]

    def get_unsupported(self, tokens: List[str]) -> List[str]:
        """Get tokens NOT available on this exchange."""
        return [tk for tk in tokens if not self.supports_token(tk)]


# ============================================================================
# Exchange Definitions
# ============================================================================

_KRAKEN_TOKENS = {
    'BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'ADA', 'AVAX', 'DOT', 'LINK',
    'SHIB', 'LTC', 'BCH', 'UNI', 'NEAR', 'AAVE', 'FIL', 'XLM', 'HBAR',
    'ICP', 'TRX', 'SUI', 'APT', 'ARB', 'OP', 'SEI', 'FET', 'INJ',
    'PEPE', 'BONK', 'FLOKI', 'WIF', 'PENDLE', 'ZRO', 'ENA',
    'BNB', 'TON', 'TAO', 'WLD', 'PENGU', 'ZEC', 'DASH',
    'PAXG', 'OM', 'ALICE', 'DENT', 'CHZ', 'POL', 'CAKE', 'TRUMP',
}

_BINANCE_TOKENS = {
    'BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'ADA', 'AVAX', 'DOT', 'LINK',
    'SHIB', 'LTC', 'BCH', 'UNI', 'NEAR', 'AAVE', 'FIL', 'XLM', 'HBAR',
    'ICP', 'TRX', 'SUI', 'APT', 'ARB', 'OP', 'SEI', 'FET', 'INJ',
    'PEPE', 'BONK', 'FLOKI', 'WIF', 'PENDLE', 'ZRO', 'ENA',
    'BNB', 'TON', 'TAO', 'WLD', 'PENGU', 'ZEC', 'DASH',
    'PAXG', 'OM', 'ALICE', 'DENT', 'CHZ', 'POL', 'CAKE', 'TRUMP',
}

EXCHANGES: Dict[str, ExchangeConfig] = {
    'kraken': ExchangeConfig(
        name='kraken',
        display_name='Kraken',
        listed_tokens=_KRAKEN_TOKENS,
        maker_fee=0.0016,
        taker_fee=0.0026,
        fee_notes='Fees decrease with 30d volume. Pro: 0.16%/0.26% at <$50K tier.',
        rate_limit_ms=3000,
        max_pairs=20,
        process_throttle_secs=5,
        min_order_usdt=10.0,
    ),
    'binance': ExchangeConfig(
        name='binance',
        display_name='Binance',
        listed_tokens=_BINANCE_TOKENS,
        maker_fee=0.001,
        taker_fee=0.001,
        fee_notes='0.075% with BNB fee discount. VIP tiers go down to 0.02%/0.04%.',
        rate_limit_ms=500,
        max_pairs=50,
        process_throttle_secs=3,
        min_order_usdt=10.0,
    ),
}


# ============================================================================
# Public API
# ============================================================================

def list_exchanges() -> List[str]:
    """List all registered exchange names."""
    return sorted(EXCHANGES.keys())


def get_exchange(name: str) -> ExchangeConfig:
    """Get exchange config by name. Raises KeyError if not found."""
    name = name.lower()
    if name not in EXCHANGES:
        available = ', '.join(list_exchanges())
        raise KeyError(f"Exchange '{name}' not registered. Available: {available}")
    return EXCHANGES[name]


def get_supported_tokens(exchange: str, tokens: List[str]) -> List[str]:
    """Filter tokens to only those listed on the exchange."""
    exc = get_exchange(exchange)
    return [tk for tk in tokens if exc.supports_token(tk)]


def get_pair_whitelist(exchange: str, tokens: List[str]) -> List[str]:
    """Get pair list for tokens available on the exchange."""
    exc = get_exchange(exchange)
    return exc.get_pair_whitelist(tokens)


def get_exchange_availability(tokens: List[str]) -> Dict[str, Dict]:
    """For each token, report which exchanges support it."""
    result = {}
    for tk in tokens:
        tk_info: Dict = {'exchanges': {}, 'pairs': {}}
        for exc_name, exc in EXCHANGES.items():
            supported = exc.supports_token(tk)
            tk_info['exchanges'][exc_name] = supported
            if supported:
                tk_info['pairs'][exc_name] = exc.format_pair(tk)
        tk_info['available_on'] = [e for e, v in tk_info['exchanges'].items() if v]
        tk_info['missing_from'] = [e for e, v in tk_info['exchanges'].items() if not v]
        result[tk] = tk_info
    return result


def register_exchange(config: ExchangeConfig) -> None:
    """Register a new exchange at runtime."""
    EXCHANGES[config.name.lower()] = config
