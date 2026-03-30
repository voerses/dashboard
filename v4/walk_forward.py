"""Walk-forward window computation and reproducibility logging.

Provides pure functions for computing walk-forward window schedules
(rolling and expanding), a BacktestManifest for reproducibility logging,
and config hashing for deterministic result verification.
"""
import dataclasses
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class WalkForwardWindow:
    """A single walk-forward window defined by bar offsets.

    Attributes:
        window_idx: Zero-based window index.
        data_cap: Last bar index available for indicator computation
                  (= oos_start - purge_bars). No data beyond this index
                  may be used for signal generation.
        oos_start: First bar index of the out-of-sample period.
        oos_end: One-past-last bar index of the OOS period (exclusive).
    """
    window_idx: int
    data_cap: int
    oos_start: int
    oos_end: int


def compute_wf_windows(
    n_bars: int,
    train_bars: int,
    recal_bars: int,
    purge_bars: int,
    scheme: str = "rolling",
) -> List[WalkForwardWindow]:
    """Compute walk-forward window schedule.

    Args:
        n_bars: Total number of bars in the dataset.
        train_bars: Number of bars in each training window.
        recal_bars: Number of bars in each OOS (recalibration) window.
        purge_bars: Number of bars in the purge gap between training and OOS.
        scheme: "rolling" (fixed-size training window slides forward) or
                "expanding" (training starts from bar 0, grows each window).

    Returns:
        List of WalkForwardWindow instances with contiguous OOS ranges.
        Empty list if n_bars is insufficient for even one window.
    """
    # First OOS window starts after train + purge
    first_oos_start = train_bars + purge_bars

    # Not enough bars for even one OOS window
    if first_oos_start >= n_bars:
        return []

    windows = []
    oos_start = first_oos_start
    window_idx = 0

    while oos_start < n_bars:
        oos_end = min(oos_start + recal_bars, n_bars)

        # data_cap = oos_start - purge_bars (always, for both schemes)
        data_cap = oos_start - purge_bars

        windows.append(WalkForwardWindow(
            window_idx=window_idx,
            data_cap=data_cap,
            oos_start=oos_start,
            oos_end=oos_end,
        ))

        oos_start = oos_end
        window_idx += 1

    return windows


@dataclass
class BacktestManifest:
    """Reproducibility manifest for a backtest run.

    Attributes:
        token_data_ranges: Per-token data boundaries
            {token: {"first_bar": str, "last_bar": str}}.
        end_date_used: The end_date/anchor used for this run.
        config_hash: Deterministic hash of the configuration.
        run_timestamp: ISO-8601 timestamp of when the run started.
    """
    token_data_ranges: Dict[str, Dict[str, str]]
    end_date_used: str
    config_hash: str
    run_timestamp: str


def log_manifest(manifest: BacktestManifest, path: str) -> None:
    """Append a BacktestManifest as a single JSON line to a JSONL file.

    Args:
        manifest: The manifest to log.
        path: File path to append to (created if it doesn't exist).
    """
    data = dataclasses.asdict(manifest)
    with open(path, "a") as f:
        f.write(json.dumps(data) + "\n")


def compute_config_hash(config: Any) -> str:
    """Compute a deterministic SHA-256 hash of a dataclass config.

    Args:
        config: A dataclass instance (e.g., PortfolioConfig).

    Returns:
        Hex digest string of the SHA-256 hash.
    """
    d = dataclasses.asdict(config)
    # Sort keys for determinism
    serialized = json.dumps(d, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()
