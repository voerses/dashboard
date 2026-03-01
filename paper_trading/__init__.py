"""
Paper Trading — Background execution, monitoring, and analysis.

Components:
    instance_manager    - Start/stop/status for Freqtrade instances
    equity_tracker      - Background polling, equity/trade/event logging
    monitor             - Rich terminal live display
    compare_instances   - Cross-instance analysis, Gate 4 integration
    gate4_engine        - SPRT, kill thresholds, go-live verdicts
    run_paper_trade     - CLI launcher
    setup_paper_trading - Environment setup (idempotent)
"""
