"""
Freqtrade Bridge — connects CPCV research engine to Freqtrade execution.

Components:
    exchange_registry   - Multi-exchange token availability & config
    strategy_shell      - Freqtrade IStrategy that reads cpcv_params.json
    parity_check        - Validates engine vs Freqtrade trade consistency

Usage:
    # Generate params for both exchanges
    python export_params.py --strategy s11 --exchange kraken,binance --noise 0.003

    # Run on Kraken
    freqtrade trade --strategy CpcvSwingStrategy --config config_kraken.json

    # Run on Binance
    freqtrade trade --strategy CpcvSwingStrategy --config config_binance.json
"""
