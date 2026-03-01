"""
Exchange Registry — Token availability, fees, and quirks per exchange.
======================================================================

Central config for multi-exchange support. Each exchange entry defines:
- Which tokens are listed (pair_whitelist)
- Fee structure (maker/taker tiers)
- Rate limits for Freqtrade's ccxt config
- Pair format quirks (e.g., Kraken uses XBT not BTC for some pairs)
- Minimum order sizes

To add a new exchange:
    1. Add an ExchangeConfig entry to EXCHANGES dict below
    2. Create a config_<exchange>.json in this directory (or use generate_config())
    3. Run: python export_params.py --exchange <name>

Usage:
    from exchange_registry import get_exchange, get_supported_tokens, list_exchanges
    from exchange_registry import get_pair_whitelist, generate_config
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
import json
import os


@dataclass
class ExchangeConfig:
    """Configuration for one exchange."""

    name: str                    # Freqtrade exchange name (lowercase)
    display_name: str            # Human-readable name

    # Token availability — set of base currencies listed with USDT
    listed_tokens: Set[str]

    # Fee structure
    maker_fee: float             # Maker fee (e.g., 0.001 = 0.1%)
    taker_fee: float             # Taker fee
    fee_notes: str = ''          # e.g., "0.06% with 30d vol > $10M"

    # Rate limiting
    rate_limit_ms: int = 1000    # Minimum ms between requests
    max_pairs: int = 50          # Max pairs before rate limit becomes a problem
    process_throttle_secs: int = 5

    # Pair format
    quote_currency: str = 'USDT'
    pair_separator: str = '/'    # e.g., "BTC/USDT"
    ticker_aliases: Dict[str, str] = field(default_factory=dict)
    # e.g., {'BTC': 'XBT'} for Kraken's legacy naming

    # Order constraints
    min_order_usdt: float = 10.0   # Minimum order size in USDT
    lot_size_notes: str = ''

    # Freqtrade ccxt overrides
    ccxt_config: Dict = field(default_factory=dict)

    # Stoploss on exchange support
    stoploss_on_exchange: bool = False

    def format_pair(self, token: str) -> str:
        """Format a token into this exchange's pair string."""
        base = self.ticker_aliases.get(token, token)
        return f"{base}{self.pair_separator}{self.quote_currency}"

    def supports_token(self, token: str) -> bool:
        """Check if this exchange lists the token."""
        return token in self.listed_tokens

    def get_pair_whitelist(self, tokens: List[str]) -> List[str]:
        """Get Freqtrade pair_whitelist for the given tokens, filtered to listed ones."""
        return [self.format_pair(tk) for tk in tokens if self.supports_token(tk)]

    def get_unsupported(self, tokens: List[str]) -> List[str]:
        """Get tokens NOT available on this exchange."""
        return [tk for tk in tokens if not self.supports_token(tk)]


# =============================================================================
# Exchange Definitions
# =============================================================================

# Kraken — conservative rate limits, good for swing trading
# Token list based on Kraken USDT pairs as of Feb 2026
_KRAKEN_TOKENS = {
    'BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'ADA', 'AVAX', 'DOT', 'LINK',
    'SHIB', 'LTC', 'BCH', 'UNI', 'NEAR', 'AAVE', 'FIL', 'XLM', 'HBAR',
    'ICP', 'TRX', 'SUI', 'APT', 'ARB', 'OP', 'SEI', 'FET', 'INJ',
    'PEPE', 'BONK', 'FLOKI', 'WIF', 'PENDLE', 'ZRO', 'ENA',
    'BNB', 'TON', 'TAO', 'WLD', 'PENGU', 'ZEC', 'DASH',
    'PAXG', 'OM', 'ALICE', 'DENT', 'CHZ', 'POL', 'CAKE', 'TRUMP',
}

# Binance — largest liquidity, all 49 tokens available
# All tokens in our universe are sourced from Binance Vision, so all are listed
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
        maker_fee=0.0016,       # 0.16% maker (intermediate tier)
        taker_fee=0.0026,       # 0.26% taker
        fee_notes='Fees decrease with 30d volume. Pro: 0.16%/0.26% at <$50K tier.',
        rate_limit_ms=3000,     # Kraken is stricter
        max_pairs=20,           # Conservative for Kraken
        process_throttle_secs=5,
        ticker_aliases={},      # Kraken USDT pairs use standard tickers
        min_order_usdt=10.0,
        lot_size_notes='Most crypto minimums are well under $1. PAXG minimum ~0.01 (~$30).',
        stoploss_on_exchange=True,
        ccxt_config={
            'enableRateLimit': True,
            'rateLimit': 3000,
        },
    ),

    'binance': ExchangeConfig(
        name='binance',
        display_name='Binance',
        listed_tokens=_BINANCE_TOKENS,
        maker_fee=0.001,        # 0.1% maker (default, lower with BNB)
        taker_fee=0.001,        # 0.1% taker
        fee_notes='0.075% with BNB fee discount. VIP tiers go down to 0.02%/0.04%.',
        rate_limit_ms=500,      # Binance is generous
        max_pairs=50,
        process_throttle_secs=3,
        ticker_aliases={},
        min_order_usdt=10.0,
        lot_size_notes='Notional minimum $10 USDT for most pairs.',
        stoploss_on_exchange=True,  # Binance supports exchange-side stoploss
        ccxt_config={
            'enableRateLimit': True,
            'rateLimit': 500,
        },
    ),

    # ── Template for adding new exchanges ──
    # 'bybit': ExchangeConfig(
    #     name='bybit',
    #     display_name='Bybit',
    #     listed_tokens={'BTC', 'ETH', 'SOL', ...},
    #     maker_fee=0.001,
    #     taker_fee=0.001,
    #     ...
    # ),
}


# =============================================================================
# Public API
# =============================================================================

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
    """Get Freqtrade pair_whitelist for tokens available on the exchange."""
    exc = get_exchange(exchange)
    return exc.get_pair_whitelist(tokens)


def get_exchange_availability(tokens: List[str]) -> Dict[str, Dict]:
    """
    For each token, report which exchanges support it.

    Returns:
        {
            'SUI': {'kraken': True, 'binance': True, 'pair': {'kraken': 'SUI/USDT', ...}},
            ...
        }
    """
    result = {}
    for tk in tokens:
        tk_info = {'exchanges': {}, 'pairs': {}}
        for exc_name, exc in EXCHANGES.items():
            supported = exc.supports_token(tk)
            tk_info['exchanges'][exc_name] = supported
            if supported:
                tk_info['pairs'][exc_name] = exc.format_pair(tk)
        tk_info['available_on'] = [e for e, v in tk_info['exchanges'].items() if v]
        tk_info['missing_from'] = [e for e, v in tk_info['exchanges'].items() if not v]
        result[tk] = tk_info
    return result


def register_exchange(config: ExchangeConfig):
    """Register a new exchange at runtime."""
    EXCHANGES[config.name.lower()] = config


def generate_freqtrade_config(exchange: str, tokens: List[str],
                              dry_run: bool = True, capital: float = 200000) -> Dict:
    """
    Generate a complete Freqtrade config dict for the given exchange and tokens.
    """
    exc = get_exchange(exchange)
    whitelist = exc.get_pair_whitelist(tokens)

    config = {
        '_comment': f'Freqtrade config for CPCV swing trading on {exc.display_name}',
        'trading_mode': 'spot',
        'margin_mode': '',
        'max_open_trades': min(len(whitelist) * 2, exc.max_pairs),
        'stake_currency': exc.quote_currency,
        'stake_amount': 'unlimited',
        'tradable_balance_ratio': 0.95,
        'fiat_display_currency': 'USD',
        'dry_run': dry_run,
        'dry_run_wallet': capital,
        'cancel_open_orders_on_exit': False,
        'exchange': {
            'name': exc.name,
            'key': '',
            'secret': '',
            'ccxt_sync_config': exc.ccxt_config,
            'ccxt_async_config': exc.ccxt_config,
            'pair_whitelist': whitelist,
            'pair_blacklist': [],
        },
        'entry_pricing': {
            'price_side': 'same',
            'use_order_book': True,
            'order_book_top': 1,
            'price_last_balance': 0.0,
            'check_depth_of_market': {'enabled': False, 'bids_to_ask_delta': 1},
        },
        'exit_pricing': {
            'price_side': 'same',
            'use_order_book': True,
            'order_book_top': 1,
        },
        'order_types': {
            'entry': 'limit',
            'exit': 'limit',
            'emergency_exit': 'market',
            'stoploss': 'market',
            'stoploss_on_exchange': exc.stoploss_on_exchange,
        },
        'edge': {
            'enabled': False,
            '_comment': 'DISABLED — we use our own CPCV validation',
        },
        'api_server': {
            'enabled': True,
            'listen_ip_address': '127.0.0.1',
            'listen_port': 8080,
            'verbosity': 'error',
            'enable_openapi': False,
            'jwt_secret_key': '',
            'CORS_origins': [],
            'username': '',
            'password': '',
        },
        'bot_name': f'CPCV_{exc.display_name}_Bot',
        'initial_state': 'running',
        'force_entry_enable': False,
        'internals': {
            'process_throttle_secs': exc.process_throttle_secs,
        },
        'telegram': {
            'enabled': False,
            'token': '',
            'chat_id': '',
        },
    }

    return config


# =============================================================================
# CLI — print exchange info
# =============================================================================

if __name__ == '__main__':
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from liquid_universe import LIQUID_TOKENS

    print("=" * 70)
    print("EXCHANGE REGISTRY")
    print("=" * 70)

    for name, exc in sorted(EXCHANGES.items()):
        supported = get_supported_tokens(name, LIQUID_TOKENS)
        missing = exc.get_unsupported(LIQUID_TOKENS)
        print(f"\n  {exc.display_name}:")
        print(f"    Listed tokens: {len(supported)}/{len(LIQUID_TOKENS)}")
        print(f"    Fees: maker={exc.maker_fee*100:.2f}% / taker={exc.taker_fee*100:.2f}%")
        print(f"    Rate limit: {exc.rate_limit_ms}ms, throttle: {exc.process_throttle_secs}s")
        print(f"    Stoploss on exchange: {exc.stoploss_on_exchange}")
        if missing:
            print(f"    Missing: {', '.join(sorted(missing))}")

    # Cross-exchange availability
    from engine import CPCV_ROBUST_TOKENS
    print(f"\n{'=' * 70}")
    print("CPCV VALIDATED TOKEN AVAILABILITY")
    print(f"{'=' * 70}")
    avail = get_exchange_availability(CPCV_ROBUST_TOKENS)
    for tk, info in sorted(avail.items()):
        exchanges = ', '.join(info['available_on']) or 'NONE'
        missing = info['missing_from']
        status = 'ALL' if not missing else f"missing: {', '.join(missing)}"
        print(f"  {tk:>8}: {status}")
