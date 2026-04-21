"""M10 Cluster C0 — shared replay fixture primitive.

``ReplayFixtureBuilder`` synthesizes deterministic :class:`TokenBarArrays`
and a minimal :class:`StrategyContext` stub for scenario tests
(C1-C4, D1, E1, G3). No wall-clock, no on-disk data — everything is
numpy-seeded from ``ScenarioSpec``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Optional, Sequence, Tuple

import numpy as np


@dataclass
class ScenarioSpec:
    random_walk_stddev: float = 0.001
    forced_trades: int = 0
    crash_bar: Optional[int] = None
    crash_pct: float = -0.25
    funding_snaps: Optional[Sequence[Tuple[int, float]]] = None


class ReplayFixtureBuilder:
    """Deterministic TokenBarArrays + StrategyContext synthesizer."""

    def __init__(
        self,
        tokens,
        n_bars: int,
        start_ts_utc: int,
        seed: int,
        scenario: ScenarioSpec,
    ) -> None:
        self.tokens = list(tokens)
        self.n_bars = int(n_bars)
        self.start_ts_utc = int(start_ts_utc)
        self.seed = int(seed)
        self.scenario = scenario

    def _build_price_array(self, rng: np.random.Generator) -> np.ndarray:
        """Random-walk close prices starting at 100.0 with scenario stddev.
        Applies a uniform shock at ``crash_bar`` if specified."""
        n = self.n_bars
        stddev = float(self.scenario.random_walk_stddev)
        log_rets = rng.normal(loc=0.0, scale=stddev, size=n)
        close = 100.0 * np.exp(np.cumsum(log_rets))
        if self.scenario.crash_bar is not None:
            cb = int(self.scenario.crash_bar)
            if 0 <= cb < n:
                shock_factor = 1.0 + float(self.scenario.crash_pct)
                close[cb:] *= shock_factor
        return close.astype(np.float64)

    def _build_per_token_arrays(self, token_idx: int) -> dict:
        """Build one token's OHLCV + indicator arrays."""
        rng = np.random.default_rng(self.seed + token_idx)
        n = self.n_bars
        close = self._build_price_array(rng)
        high = close * (1.0 + np.abs(rng.normal(0, 0.0005, n)))
        low = close * (1.0 - np.abs(rng.normal(0, 0.0005, n)))
        # Volume + ADV stay non-zero so ADV-cap + min-notional don't reject.
        volume = rng.uniform(1_000.0, 10_000.0, n)
        rolling_adv = np.full(n, 1_000_000.0, dtype=np.float64)
        atr = np.full(n, close.mean() * 0.02, dtype=np.float64)
        timestamps = (
            self.start_ts_utc
            + np.arange(n, dtype=np.int64) * 3600 * 1_000_000_000
        )
        # Funding schedule — zero unless scenario specifies snaps.
        funding_1h = np.zeros(n, dtype=np.float64)
        if self.scenario.funding_snaps:
            for bar_idx, rate in self.scenario.funding_snaps:
                if 0 <= int(bar_idx) < n:
                    funding_1h[int(bar_idx)] = float(rate)
        # Entry mask — stamp N forced trades evenly across the fixture.
        entry_mask = np.zeros(n, dtype=bool)
        direction = np.zeros(n, dtype=np.int8)
        if self.scenario.forced_trades > 0:
            step = max(n // (self.scenario.forced_trades * 2), 1)
            for i in range(self.scenario.forced_trades):
                entry_bar = i * 2 * step
                if entry_bar < n:
                    entry_mask[entry_bar] = True
                    direction[entry_bar] = 1
        return {
            "timestamps": timestamps,
            "close": close,
            "high": high,
            "low": low,
            "volume": volume,
            "rolling_adv": rolling_adv,
            "atr": atr,
            "funding_1h": funding_1h,
            "entry_mask": entry_mask,
            "direction": direction,
        }

    def build(self) -> dict:
        """Return dict[token, TokenBarArrays]."""
        from v5.signals import TokenBarArrays

        out = {}
        n = self.n_bars
        # For fixtures with forced_trades, size max_hold so each position
        # closes before the next entry. Keeps the sim loop deterministic
        # about open/close cycles (C1 PnL-path + C2 day-rollover tests).
        if self.scenario.forced_trades > 0:
            step = max(n // (self.scenario.forced_trades * 2), 1)
            max_hold = max(step, 2)
        else:
            max_hold = max(n, 720)
        min_hold = min(2, max(1, max_hold // 4))
        for i, tok in enumerate(self.tokens):
            arr = self._build_per_token_arrays(i)
            out[tok] = TokenBarArrays(
                token=tok,
                strategy_id="_replay",
                n_bars=n,
                timestamps=arr["timestamps"],
                entry_mask=arr["entry_mask"],
                direction=arr["direction"],
                close=arr["close"],
                high=arr["high"],
                low=arr["low"],
                atr=arr["atr"],
                rolling_adv=arr["rolling_adv"],
                funding_1h=arr["funding_1h"],
                stop_mult=np.full(n, 2.0, dtype=np.float64),
                trail_mult=np.full(n, 3.0, dtype=np.float64),
                target_mult=5.0,
                no_stop_bars=1,
                min_hold=min_hold,
                max_hold=max_hold,
                edge=0.35,
                leverage=np.ones(n, dtype=np.float64),
                volume=arr["volume"],
            )
        return out

    def ctx_stub(self):
        """Minimal StrategyContext stub with `ctx.data._arrays` populated.

        Returns a SimpleNamespace whose `.data._arrays` exposes the
        per-token OHLCV arrays so bridge-mode tests can drive the inner
        loop without instantiating a real UniverseContext.
        """
        arrays: dict = {}
        for i, tok in enumerate(self.tokens):
            arrays[tok] = self._build_per_token_arrays(i)

        data = SimpleNamespace(
            _arrays=arrays,
            _tokens_seed=tuple(self.tokens),
        )
        ctx = SimpleNamespace(
            data=data,
            _lifecycle_config={"bars": self.n_bars},
            market_indices={},
        )

        def _seek_bar(bar_idx):
            ctx._current_bar = int(bar_idx)

        ctx.seek_bar = _seek_bar
        ctx._current_bar = 0
        return ctx

    def drive_into_state(self, state_path):
        """Rollback-drill helper (E1): write a few synthetic paper-state
        records to ``state_path``. Minimal implementation — emits a
        schema-v3 JSON stub with 5 active_positions + empty open_orders.
        """
        import hashlib
        import json
        from pathlib import Path

        p = Path(str(state_path))
        p.parent.mkdir(parents=True, exist_ok=True)
        positions = [
            {
                "position_id": f"{tok}:_replay:{i}:primary",
                "token": tok,
                "strategy_id": "_replay",
                "leg": "primary",
                "entry_bar": 0,
                "entry_price": 100.0,
                "direction": 1,
                "quantity": 0.5,
                "margin_usd": 10_000.0,
                "leverage": 1.0,
                "is_perp": True,
                "cumulative_funding": 0.0,
            }
            for i, tok in enumerate(self.tokens[:5])
        ]
        orders: list = []
        blob = json.dumps(
            {"active_positions": positions, "open_orders": orders},
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        checksum = hashlib.sha256(blob).hexdigest()
        payload = {
            "schema_version": 3,
            "active_positions": positions,
            "open_orders": orders,
            "checksum": checksum,
        }
        p.write_text(json.dumps(payload, indent=2))
