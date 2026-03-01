"""
Config Generator — Per-instance Freqtrade configuration builder.

Generates valid Freqtrade JSON configs for strategy+exchange combos
with deterministic port allocation and instance isolation.
"""

import json
import os
from typing import List, Optional

from freqtrade_bridge.cost_model import CostModel


# Port allocation scheme: base + strategy_num * 10 + exchange_offset
# kraken=1, binance=2
EXCHANGE_OFFSET = {"kraken": 1, "binance": 2}

STRATEGY_NUM = {
    "s09": 9, "s10": 10, "s11": 11, "s13": 13,
    "s15": 15, "s17": 17, "s18": 18, "s21": 21, "s22": 22,
}

BASE_PORT = 8000

DEFAULT_INSTANCE_CAP = 3


class ConfigGenerator:
    """Generates per-instance Freqtrade configs with isolation guarantees.

    Each strategy+exchange combo gets:
    - Unique API port (deterministic: 8000 + strategy_num * 10 + exchange_offset)
    - Unique SQLite DB path
    - Exchange-specific fee rates from CostModel
    - dry_run=true, $200K wallet, max 14 concurrent trades
    """

    def __init__(self, max_per_exchange: int = DEFAULT_INSTANCE_CAP):
        self.max_per_exchange = max_per_exchange
        self._running_instances: List[dict] = []

    def register_running_instances(self, instances: List[dict]):
        """Register currently running instances for cap enforcement.

        Args:
            instances: List of dicts with 'instance_id', 'exchange', 'status'
        """
        self._running_instances = list(instances)

    def get_instance_port(self, strategy: str, exchange: str) -> int:
        """Get deterministic API port for a strategy+exchange combo.

        Port scheme: 8000 + strategy_num * 10 + exchange_offset
        Examples: s09_kraken=8091, s11_binance=8112, s13_kraken=8131
        """
        strat_num = STRATEGY_NUM.get(strategy)
        if strat_num is None:
            raise ValueError(f"Unknown strategy: {strategy}")

        ex_offset = EXCHANGE_OFFSET.get(exchange.lower())
        if ex_offset is None:
            raise ValueError(f"Unknown exchange: {exchange}")

        return BASE_PORT + strat_num * 10 + ex_offset

    def get_db_path(self, strategy: str, exchange: str) -> str:
        """Get unique database path for an instance."""
        instance_id = f"{strategy}_{exchange}"
        return f"paper_trading/{instance_id}/tradesv3.sqlite"

    def _check_instance_cap(self, exchange: str) -> bool:
        """Check if adding another instance for this exchange is allowed.

        Returns True if under the cap, False if at/over cap.
        Only counts running instances (not stopped/crashed).
        """
        running_count = sum(
            1 for inst in self._running_instances
            if inst.get("exchange") == exchange.lower()
            and inst.get("status") == "running"
        )
        return running_count < self.max_per_exchange

    def generate(self, strategy: str, exchange: str,
                 pairs: list, capital: float = 200_000) -> dict:
        """Generate a complete Freqtrade config for a strategy+exchange instance.

        Args:
            strategy: Strategy name (e.g., 's11')
            exchange: Exchange name (e.g., 'kraken')
            pairs: List of validated trading pairs (e.g., ['BTC/USDT'])
            capital: Starting capital in USDT (default: 200000)

        Returns:
            Dict representing valid Freqtrade JSON config

        Raises:
            ValueError: If instance cap exceeded for this exchange
        """
        exchange = exchange.lower()

        if not self._check_instance_cap(exchange):
            raise ValueError(
                f"Instance cap reached for {exchange} "
                f"(max {self.max_per_exchange} running instances)"
            )

        cost_model = CostModel(exchange)
        fees = cost_model.get_fees()
        port = self.get_instance_port(strategy, exchange)
        db_path = self.get_db_path(strategy, exchange)

        # Rate limits per exchange
        rate_limits = {"kraken": 3000, "binance": 500}
        throttle_secs = {"kraken": 5, "binance": 3}

        config = {
            "trading_mode": "spot",
            "max_open_trades": 14,
            "stake_currency": "USDT",
            "stake_amount": "unlimited",
            "dry_run": True,
            "dry_run_wallet": capital,
            "exchange": {
                "name": exchange,
                "key": "",
                "secret": "",
                "ccxt_sync_config": {
                    "enableRateLimit": True,
                    "rateLimit": rate_limits.get(exchange, 1000),
                },
                "ccxt_async_config": {
                    "enableRateLimit": True,
                    "rateLimit": rate_limits.get(exchange, 1000),
                },
                "pair_whitelist": list(pairs),
                "pair_blacklist": [],
                "fee": {
                    "maker": fees["maker"],
                    "taker": fees["taker"],
                },
            },
            "order_types": {
                "entry": "limit",
                "exit": "limit",
                "emergency_exit": "market",
                "stoploss": "market",
                "stoploss_on_exchange": True,
            },
            "api_server": {
                "enabled": True,
                "listen_ip_address": "127.0.0.1",
                "listen_port": port,
                "verbosity": "error",
            },
            "db_url": f"sqlite:///{db_path}",
            "internals": {
                "process_throttle_secs": throttle_secs.get(exchange, 5),
            },
            "strategy": "CpcvSwingStrategy",
            "instance_id": f"{strategy}_{exchange}",
        }

        return config

    def write_config(self, strategy: str, exchange: str,
                     pairs: list, output_dir: str = "paper_trading",
                     capital: float = 200_000) -> str:
        """Generate and write config to a JSON file.

        Returns:
            Path to the written config file
        """
        config = self.generate(strategy, exchange, pairs, capital)
        filename = f"config_{strategy}_{exchange}.json"
        path = os.path.join(output_dir, filename)

        os.makedirs(output_dir, exist_ok=True)
        with open(path, "w") as f:
            json.dump(config, f, indent=2)

        return path
