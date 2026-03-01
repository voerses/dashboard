"""Shared fixtures for paper trading system acceptance tests."""

import json
import pytest


# ---------------------------------------------------------------------------
# Sample indicator / OHLCV data
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_ohlcv():
    """Minimal OHLCV row list usable by strategy entry logic."""
    return [
        {"timestamp": 1700000000, "open": 100.0, "high": 105.0, "low": 98.0, "close": 103.0, "volume": 1000.0},
        {"timestamp": 1700003600, "open": 103.0, "high": 107.0, "low": 101.0, "close": 106.0, "volume": 1200.0},
        {"timestamp": 1700007200, "open": 106.0, "high": 110.0, "low": 104.0, "close": 108.0, "volume": 1100.0},
        {"timestamp": 1700010800, "open": 108.0, "high": 112.0, "low": 106.0, "close": 110.0, "volume": 1300.0},
        {"timestamp": 1700014400, "open": 110.0, "high": 114.0, "low": 107.0, "close": 109.0, "volume": 900.0},
    ]


@pytest.fixture
def sample_indicator_data():
    """Pre-computed indicator values that strategies consume."""
    return {
        "atr_14": 3.5,
        "ema_20": 107.0,
        "ema_50": 104.0,
        "rsi_14": 58.0,
        "macd_histogram": 0.45,
        "bbands_upper": 115.0,
        "bbands_lower": 99.0,
        "volume_sma_20": 1100.0,
        "regime": "uptrend",
        "volatility_regime": "normal",
    }


# ---------------------------------------------------------------------------
# Sweep summary / validation JSON (AC3)
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_sweep_summary():
    """Mock sweep_summary.json with V3 dual-gate results per strategy."""
    return {
        "s11": {
            "BTC/USDT": {"v3_pass": True, "sharpe": 1.8, "params": {"fast_ema": 12, "slow_ema": 26}},
            "ETH/USDT": {"v3_pass": True, "sharpe": 1.5, "params": {"fast_ema": 10, "slow_ema": 21}},
            "SOL/USDT": {"v3_pass": False, "sharpe": 0.4, "params": {"fast_ema": 14, "slow_ema": 28}},
        },
        "s09": {
            "BTC/USDT": {"v3_pass": True, "sharpe": 2.1, "params": {"lookback": 20, "threshold": 0.02}},
            "DOGE/USDT": {"v3_pass": False, "sharpe": -0.3, "params": {"lookback": 15, "threshold": 0.01}},
        },
    }


@pytest.fixture
def sample_sweep_summary_json(sample_sweep_summary, tmp_path):
    """Write sweep_summary to a temp JSON file and return the path."""
    path = tmp_path / "sweep_summary.json"
    path.write_text(json.dumps(sample_sweep_summary))
    return str(path)


# ---------------------------------------------------------------------------
# Gate 4 thresholds (AC11)
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_gate4_thresholds():
    """Mock gate4_thresholds.json content."""
    return {
        "min_trades": 50,
        "min_weeks": 4,
        "target_sharpe": 0.5,
        "alpha": 0.05,
        "beta": 0.10,
        "sortino_degradation_min": 0.6,
        "max_dd_ratio": 1.5,
        "slippage_ratio_max": 2.0,
        "fill_rate_min": 0.95,
        "op_failures_per_week_max": 1,
        "sharpe_shortfall_max": 1.0,
        "rolling_sortino_negative_days_max": 30,
        "min_regime_count": 2,
        "parity_divergence_max": 0.10,
        "operational_uptime_min": 0.99,
    }


@pytest.fixture
def sample_gate4_thresholds_json(sample_gate4_thresholds, tmp_path):
    """Write gate4 thresholds to a temp JSON file and return the path."""
    path = tmp_path / "gate4_thresholds.json"
    path.write_text(json.dumps(sample_gate4_thresholds))
    return str(path)


# ---------------------------------------------------------------------------
# Trade / equity sample data (AC9)
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_trade_records():
    """Structured trade records for equity tracker tests."""
    return [
        {
            "trade_id": "t001",
            "strategy": "s11",
            "exchange": "binance",
            "pair": "BTC/USDT",
            "side": "buy",
            "entry_price": 40000.0,
            "exit_price": 41500.0,
            "size": 0.01,
            "entry_time": "2024-01-15T10:00:00Z",
            "exit_time": "2024-01-16T14:00:00Z",
            "pnl": 15.0,
            "fees": 0.82,
        },
        {
            "trade_id": "t002",
            "strategy": "s11",
            "exchange": "binance",
            "pair": "ETH/USDT",
            "side": "buy",
            "entry_price": 2500.0,
            "exit_price": 2450.0,
            "size": 0.1,
            "entry_time": "2024-01-17T08:00:00Z",
            "exit_time": "2024-01-18T12:00:00Z",
            "pnl": -5.0,
            "fees": 0.50,
        },
    ]


@pytest.fixture
def sample_equity_rows():
    """Sample equity CSV rows (as dicts) for tracker tests.  $200K starting equity per brief."""
    return [
        {"timestamp": "2024-01-15T00:00:00Z", "equity": 200000.0, "cash": 192000.0, "exposure": 8000.0},
        {"timestamp": "2024-01-16T00:00:00Z", "equity": 200300.0, "cash": 200300.0, "exposure": 0.0},
        {"timestamp": "2024-01-17T00:00:00Z", "equity": 200300.0, "cash": 195300.0, "exposure": 5000.0},
        {"timestamp": "2024-01-18T00:00:00Z", "equity": 200190.0, "cash": 200190.0, "exposure": 0.0},
    ]


# ---------------------------------------------------------------------------
# Instance registry (AC7, AC8)
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_instances_json(tmp_path):
    """Write a mock instances.json and return the path."""
    data = {
        "instances": [
            {
                "instance_id": "s11_binance",
                "strategy": "s11",
                "exchange": "binance",
                "status": "running",
                "pid": 12345,
                "api_port": 8112,
                "started_at": "2024-01-15T08:00:00Z",
            },
            {
                "instance_id": "s09_kraken",
                "strategy": "s09",
                "exchange": "kraken",
                "status": "running",
                "pid": 12346,
                "api_port": 8091,
                "started_at": "2024-01-15T08:00:30Z",
            },
            {
                "instance_id": "s13_binance",
                "strategy": "s13",
                "exchange": "binance",
                "status": "stopped",
                "pid": None,
                "api_port": 8132,
                "started_at": "2024-01-15T08:01:00Z",
            },
        ]
    }
    path = tmp_path / "instances.json"
    path.write_text(json.dumps(data))
    return str(path)


# ---------------------------------------------------------------------------
# Parity / signal data (AC12)
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_engine_signals():
    """Mock engine signals keyed by token."""
    return {
        "BTC/USDT": [True, True, False, True, False, True, True, False, True, True],
        "ETH/USDT": [False, True, True, False, True, False, False, True, True, False],
    }


@pytest.fixture
def sample_freqtrade_signals():
    """Mock Freqtrade signals with some divergence from engine signals."""
    return {
        "BTC/USDT": [True, True, False, True, False, True, True, False, True, True],   # 0% divergence
        "ETH/USDT": [False, True, False, False, True, True, False, True, False, False],  # 30% divergence
    }


# ---------------------------------------------------------------------------
# Exchange config helpers (AC5)
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_exchange_pairs():
    """Pairs validated for each exchange."""
    return {
        "binance": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
        "kraken": ["BTC/USD", "ETH/USD"],
    }


# ---------------------------------------------------------------------------
# SPRT sample data (AC11)
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_trade_sharpes():
    """Per-trade log-return data for SPRT computation."""
    import random
    random.seed(42)
    # 60 trades with mildly positive returns
    return [random.gauss(0.005, 0.02) for _ in range(60)]


@pytest.fixture
def sample_insufficient_trades():
    """Fewer than 50 trades -- should trigger INSUFFICIENT DATA."""
    import random
    random.seed(42)
    return [random.gauss(0.005, 0.02) for _ in range(30)]
