"""
Freqtrade Bridge — connects CPCV research engine to Freqtrade execution.

Components:
    strategy_shell      - Freqtrade IStrategy (StrategyShell) for all Tier A strategies
    exchange_registry   - Multi-exchange token availability & config (ExchangeRegistry)
    cost_model          - Exchange-specific fees, slippage, latency (CostModel)
    config_generator    - Per-instance Freqtrade config builder (ConfigGenerator)
    export_params       - V3 validation → parameter export pipeline (ExportParams)
    parity_check        - Engine vs Freqtrade signal comparison (ParityCheck)

Usage:
    # Generate params for both exchanges
    python -m freqtrade_bridge.export_params --strategy s11 --exchange kraken,binance

    # Run paper trading instance
    python -m paper_trading.run_paper_trade --start --strategy s11 --exchange kraken

    # Monitor running instances
    python -m paper_trading.monitor
"""
