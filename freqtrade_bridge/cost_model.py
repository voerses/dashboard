"""
Cost Model — Exchange-specific fee, slippage, and latency simulation.

Provides realistic cost modeling for paper trading and backtesting.
Applied via Freqtrade's custom_entry_price() and custom_exit_price() callbacks.
"""


class CostModel:
    """Exchange-specific cost simulation for paper trading.

    Default fees are conservative base-tier rates (no volume history):
        Kraken: 0.25% maker / 0.40% taker ($0-$10K 30d volume)
        Binance: 0.10% maker / 0.10% taker (no BNB discount)

    Slippage is volume-dependent with exchange-specific defaults:
        Binance: T1=5bps, T2=12bps, T3=25bps
        Kraken:  T1=12bps, T2=30bps, T3=60bps
    """

    # Base-tier fees per exchange (conservative — no volume history)
    DEFAULT_FEES = {
        "kraken": {"maker": 0.0025, "taker": 0.0040},
        "binance": {"maker": 0.0010, "taker": 0.0010},
    }

    # Exchange-specific slippage defaults in basis points by token tier (int keys)
    DEFAULT_SLIPPAGE_BPS = {
        "binance": {1: 5, 2: 12, 3: 25},
        "kraken": {1: 12, 2: 30, 3: 60},
    }

    # Default latency buffers in milliseconds
    DEFAULT_LATENCY_MS = {
        "kraken": 200,
        "binance": 100,
    }

    # Impact factor for volume-dependent slippage (fractional per unit participation)
    DEFAULT_IMPACT_FACTOR = 0.1

    def __init__(self, exchange: str, maker_fee: float = None,
                 taker_fee: float = None):
        """Initialize cost model for an exchange.

        Args:
            exchange: Exchange name ('kraken' or 'binance')
            maker_fee: Override maker fee (e.g., 0.0016 for Kraken mid-tier)
            taker_fee: Override taker fee (e.g., 0.0026 for Kraken mid-tier)
        """
        self.exchange = exchange.lower()

        if self.exchange not in self.DEFAULT_FEES:
            raise ValueError(f"Unsupported exchange: {exchange}")

        defaults = self.DEFAULT_FEES[self.exchange]
        self.maker_fee = maker_fee if maker_fee is not None else defaults["maker"]
        self.taker_fee = taker_fee if taker_fee is not None else defaults["taker"]

        self.slippage_bps = self.DEFAULT_SLIPPAGE_BPS[self.exchange]
        self.latency_ms = self.DEFAULT_LATENCY_MS[self.exchange]
        self.impact_factor = self.DEFAULT_IMPACT_FACTOR
        self._slippage_log = []

    def get_fees(self) -> dict:
        """Return current maker/taker fee rates."""
        return {"maker": self.maker_fee, "taker": self.taker_fee}

    def get_token_tier(self, token: str) -> int:
        """Classify token into liquidity tier (1, 2, or 3).

        T1 (liquid): BTC, ETH, SOL, XRP, BNB, DOGE, ADA, AVAX, TRX, DOT,
                      LINK, MATIC, SHIB, UNI, LTC
        T2 (mid):    SUI, FIL, NEAR, ATOM, APT, ARB, OP, IMX, INJ, AAVE,
                      GRT, RENDER, FET, STX, SEI, TIA
        T3 (low-cap): BONK, FLOKI, PENGU, DENT, OM, WIF, PEPE, JASMY, CHZ,
                       GALA, ENJ, SAND, AXS, MANA, CRV, ZRO
        """
        t1 = {"BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX",
               "TRX", "DOT", "LINK", "MATIC", "SHIB", "UNI", "LTC"}
        t3 = {"BONK", "FLOKI", "PENGU", "DENT", "OM", "WIF", "PEPE",
               "JASMY", "CHZ", "GALA", "ENJ", "SAND", "AXS", "MANA",
               "CRV", "ZRO"}

        if token.upper() in t1:
            return 1
        elif token.upper() in t3:
            return 3
        else:
            return 2

    def compute_slippage(self, token_tier: int, order_size: float,
                         adv: float) -> float:
        """Calculate slippage as a fraction for a given tier and order.

        Formula: base_fraction + (order_size / adv) * impact_factor

        Args:
            token_tier: Liquidity tier (1, 2, or 3)
            order_size: Order size in USD
            adv: Average daily volume in USD

        Returns:
            Estimated slippage as a fraction (e.g., 0.0005 = 5bps)
        """
        base_bps = self.slippage_bps.get(token_tier, self.slippage_bps[2])
        base_fraction = base_bps / 10000.0

        volume_impact = (order_size / adv) * self.impact_factor if adv > 0 else 0

        return base_fraction + volume_impact

    def get_slippage_bps_for_token(self, token: str, order_size: float = 0,
                                   adv: float = 1e9) -> float:
        """Calculate slippage in basis points for a named token.

        Convenience wrapper around compute_slippage that auto-detects tier.
        """
        tier = self.get_token_tier(token)
        return self.compute_slippage(tier, order_size, adv) * 10000

    def apply_entry_cost(self, price: float, order_size: float = 0,
                         adv: float = 1e9, token_tier: int = 2) -> float:
        """Apply cost to entry price (returns higher price — worse for buyer).

        Total cost = taker fee + slippage
        """
        slippage = self.compute_slippage(token_tier, order_size, adv)

        # Entry: we pay more than market price
        return price * (1 + self.taker_fee + slippage)

    def apply_exit_cost(self, price: float, order_size: float = 0,
                        adv: float = 1e9, token_tier: int = 2) -> float:
        """Apply cost to exit price (returns lower price — worse for seller).

        Total cost = taker fee + slippage
        """
        slippage = self.compute_slippage(token_tier, order_size, adv)

        # Exit: we receive less than market price
        return price * (1 - self.taker_fee - slippage)

    def log_actual_slippage(self, expected_price: float, fill_price: float,
                            token: str):
        """Log actual vs expected slippage for calibration.

        Args:
            expected_price: Price we expected to get
            fill_price: Price we actually got
            token: Token symbol
        """
        if expected_price == 0:
            return

        actual_bps = abs(fill_price - expected_price) / expected_price * 10000
        tier = self.get_token_tier(token)
        self._slippage_log.append({
            "token": token,
            "expected_price": expected_price,
            "fill_price": fill_price,
            "actual_slippage_bps": actual_bps,
            "modeled_slippage_bps": self.slippage_bps.get(tier, 12),
        })

    def get_slippage_log(self) -> list:
        """Return accumulated slippage log entries."""
        return list(self._slippage_log)
