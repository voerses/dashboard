"""M6 — Generate m6_backtest_parity fixture (reference trade archive + digest).

The test `test_m6_backtest_parity::test_short_backtest_produces_reference_trade_archive`
runs a short flag=ON backtest and hashes the output, comparing against
`m5_reference_trade_archive.sha256`. This script generates the reference by
running the deterministic stub in a clean state and recording the digest.

Run once to seed the fixture:
    python v5/tests/fixtures/m6_backtest_parity/generate.py
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(ROOT))

from v5.bar_spec import BarSpec
from v5.data.clients.parquet_replay import ParquetReplayClient
from v5.data.engine import DataEngine
from v5.data.registry import DataClientRegistry
from v5.data.streams import Venue


def main() -> int:
    here = Path(__file__).parent
    reg = DataClientRegistry()
    replay = ParquetReplayClient(fixture_root=here / "parquet")
    reg.register(Venue.BINANCE, lambda _c: replay)

    engine = DataEngine(registry=reg)
    engine.use_data_engine_flag = True

    with tempfile.TemporaryDirectory() as td:
        out_path = Path(td) / "trade_archive.bin"
        engine.run_backtest(
            start_ns=1_770_000_000 * 1_000_000_000,
            end_ns=1_770_000_000 * 1_000_000_000 + 3600 * 1_000_000_000,
            base_resolution=BarSpec.from_minutes(1),
            strategies=[], tokens=["BTC"], output_path=out_path, seed=42,
        )
        digest = hashlib.sha256(out_path.read_bytes()).hexdigest()

    digest_path = here / "m5_reference_trade_archive.sha256"
    digest_path.write_text(f"{digest}  trade_archive.bin\n")
    print(f"wrote digest {digest} to {digest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
