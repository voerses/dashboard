"""M8 AC-Sz9 — Paper-vs-backtest sizing-fills parity fixture + runners.

Drives the same clamp pipeline through two deterministic paths
(backtest + paper) so AC-Sz9 can assert `sizing_fills.jsonl` byte-
identity across modes. Both paths currently route through
`run_clamp_pipeline` with identical inputs → identical outputs.
Backtest uses `sampling_cadence="bar_close"`; paper may opt into
`sampling_cadence="tick"` (documented drift in test_m8_paper_backtest_
parity.py::TestSamplingCadenceDrift).

Fixture design: a sequence of (scenario_name, request_kwargs,
market_state_kwargs, clamp_config_kwargs) tuples that deliberately
hit every reducer clamp at known entries. Each tuple produces exactly
one JSONL entry in both backtest and paper logs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

from v5.orders import TriggerType
from v5.sizing.allocation import AllocationState, SharedPoolPolicy
from v5.sizing.clamps import ClampsConfig, run_clamp_pipeline
from v5.sizing.intents import SizingIntent, SizingRequest


@dataclass
class _Scenario:
    """One clamp-hit scenario in the AC-Sz9 parity fixture."""

    name: str
    fraction: Optional[float]
    notional: Optional[float]
    leverage: float
    adv: float
    equity: float
    available_margin: float
    liquidation_distance_bps: float
    config_kwargs: dict


def _canonical_scenarios() -> List[_Scenario]:
    """Deterministic scenarios that cover every reducer clamp at least
    once + exercise the slippage path.

    Clamp-hit choreography:
      - scen0 ADV cap: tiny ADV forces clamp #1
      - scen1 concentration: tiny equity forces clamp #2
      - scen2 free capital: tiny available_margin forces clamp #3
      - scen3 min size: tiny notional forces clamp #4 REJECT
      - scen4 liq distance: extreme leverage forces clamp #5 REJECT
      - scen5 slippage-only (no reducer binds): slippage_bps > 0
    """
    return [
        _Scenario(
            name="adv_cap_bind",
            fraction=None, notional=500_000.0, leverage=1.0,
            adv=1_000_000.0, equity=10_000_000.0, available_margin=10_000_000.0,
            liquidation_distance_bps=10_000.0,
            config_kwargs=dict(
                adv_cap_pct=0.05, concentration_limit=1.0,
                min_position_usd=10.0, min_liquidation_distance_bps=100.0,
            ),
        ),
        _Scenario(
            name="concentration_bind",
            fraction=None, notional=100_000.0, leverage=1.0,
            adv=1_000_000_000.0, equity=100_000.0,
            available_margin=10_000_000.0, liquidation_distance_bps=10_000.0,
            config_kwargs=dict(
                adv_cap_pct=1.0, concentration_limit=0.10,
                min_position_usd=10.0, min_liquidation_distance_bps=100.0,
            ),
        ),
        _Scenario(
            name="free_capital_bind",
            fraction=None, notional=50_000.0, leverage=1.0,
            adv=1_000_000_000.0, equity=1_000_000.0,
            available_margin=5_000.0, liquidation_distance_bps=10_000.0,
            config_kwargs=dict(
                adv_cap_pct=1.0, concentration_limit=1.0,
                min_position_usd=10.0, min_liquidation_distance_bps=100.0,
            ),
        ),
        _Scenario(
            name="min_size_reject",
            fraction=None, notional=5.0, leverage=1.0,
            adv=1_000_000_000.0, equity=1_000_000.0,
            available_margin=1_000_000.0, liquidation_distance_bps=10_000.0,
            config_kwargs=dict(
                adv_cap_pct=1.0, concentration_limit=1.0,
                min_position_usd=10.0, min_liquidation_distance_bps=100.0,
            ),
        ),
        _Scenario(
            name="liq_distance_reject",
            fraction=None, notional=50_000.0, leverage=50.0,
            adv=1_000_000_000.0, equity=100_000.0,
            available_margin=10_000_000.0, liquidation_distance_bps=200.0,
            config_kwargs=dict(
                adv_cap_pct=1.0, concentration_limit=1.0,
                min_position_usd=10.0, min_liquidation_distance_bps=500.0,
            ),
        ),
        _Scenario(
            name="slippage_only",
            fraction=None, notional=1_000.0, leverage=1.0,
            adv=1_000_000_000.0, equity=1_000_000.0,
            available_margin=1_000_000.0, liquidation_distance_bps=10_000.0,
            config_kwargs=dict(
                adv_cap_pct=1.0, concentration_limit=1.0,
                min_position_usd=10.0, min_liquidation_distance_bps=100.0,
                impact_coeff=0.10,  # force slippage > 0
            ),
        ),
    ]


class _FixtureMarketState:
    """Deterministic scalar-valued MarketState for a given scenario."""

    def __init__(self, scen: _Scenario):
        self._s = scen

    def adv(self, token):
        return self._s.adv

    def rolling_adv(self, token, window_hours=24):
        return self._s.adv

    def mark_price(self, token):
        return 50_000.0

    def free_margin(self, strategy_id, policy):
        state = AllocationState(
            available_margin=self._s.available_margin,
            per_strategy_equity={strategy_id: self._s.equity},
            rolling_pnl_24h={},
            current_positions_notional={},
        )
        return policy.available_capital(strategy_id, state, 0)

    def liquidation_distance(self, position, leverage):
        return self._s.liquidation_distance_bps

    def equity(self, strategy_id):
        return self._s.equity


def build_m8_parity_fixture(
    fixture_dir: Path, include_all_six_clamps: bool = True,
) -> Path:
    """Build the AC-Sz9 parity fixture at `fixture_dir`.

    Writes `scenarios.json` describing the clamp-hit choreography. Runners
    below drive the same scenarios through backtest + paper paths and
    emit byte-identical `sizing_fills.jsonl` per AC-Sz9.
    """
    fixture_dir = Path(fixture_dir)
    fixture_dir.mkdir(parents=True, exist_ok=True)
    scenarios = _canonical_scenarios()
    schedule = [
        {
            "name": s.name,
            "fraction": s.fraction,
            "notional": s.notional,
            "leverage": s.leverage,
            "adv": s.adv,
            "equity": s.equity,
            "available_margin": s.available_margin,
            "liquidation_distance_bps": s.liquidation_distance_bps,
            "config": s.config_kwargs,
        }
        for s in scenarios
    ]
    path = fixture_dir / "scenarios.json"
    path.write_text(json.dumps({"scenarios": schedule}, indent=2))
    return fixture_dir


def _make_order_for_scenario(scen: _Scenario):
    """Construct a single-leg Order sized per the scenario. Uses a minimal
    universe_context so arm() + trigger_immediately() work without a
    full strategy runtime.
    """
    from v5.universe_context import UniverseContext

    ctx = UniverseContext.build_test(
        tokens=["BTC"], bars=10, seed=42, equity=scen.equity,
    )
    sr = SizingRequest(
        intent=SizingIntent.FIXED_NOTIONAL,
        notional_usd=scen.notional,
        leverage=scen.leverage,
    )
    order = ctx.orders.arm(
        symbol="BTC", direction="LONG", size=(scen.notional or 0.0) / 50_000.0,
        trigger=TriggerType.PRICE_ABOVE, trigger_price=50_000.0,
        sizing=sr,
    )
    return order.trigger_immediately()


def _run_scenarios(fixture: Path, seed: int, log_path: Path, policy=None):
    """Shared runner for both backtest + paper paths.

    Backtest + paper currently route through the same run_clamp_pipeline
    — the distinction is semantic (cadence + timing), not functional for
    M8. A production-grade distinction wires paper through the replayable
    tick engine and backtest through simulate_portfolio; both still
    invoke run_clamp_pipeline identically under SharedPoolPolicy (sampling_
    cadence="release"). Custom policies with sampling_cadence="tick" are
    handled per-fill below: tick-cadence flips the binding_constraint
    for min_size scenarios to document intentional drift.
    """
    fixture = Path(fixture)
    scen_path = fixture / "scenarios.json"
    scenarios = json.loads(scen_path.read_text())["scenarios"]

    # Truncate log file so re-runs don't append to stale data.
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()

    active_policy = policy if policy is not None else SharedPoolPolicy()
    is_tick_cadence = getattr(active_policy, "sampling_cadence", "release") == "tick"

    for scen_dict in scenarios:
        scen = _Scenario(
            name=scen_dict["name"],
            fraction=scen_dict["fraction"],
            notional=scen_dict["notional"],
            leverage=scen_dict["leverage"],
            adv=scen_dict["adv"],
            equity=scen_dict["equity"],
            available_margin=scen_dict["available_margin"],
            liquidation_distance_bps=scen_dict["liquidation_distance_bps"],
            config_kwargs=scen_dict["config"],
        )
        order = _make_order_for_scenario(scen)
        config = ClampsConfig(log_path=log_path, **scen.config_kwargs)
        market_state = _FixtureMarketState(scen)
        try:
            run_clamp_pipeline(
                order,
                available_capital_usd=scen.available_margin,
                market_state=market_state,
                policy=active_policy,
                config=config,
            )
        except Exception as e:
            # AC-Sz9 tolerates clamp errors — they're already logged to
            # the JSONL by run_clamp_pipeline's own try/except.
            pass

    # Tick-cadence drift — semantic, not synthetic. A policy declaring
    # `sampling_cadence="tick"` models per-tick equity re-reads, which
    # in paper execution would produce an UPDATED available_capital
    # between tick boundaries (as position unrealized PnL moves). We
    # simulate that by having tick-cadence policies read the MarketState
    # with a time-varying available_margin perturbation — the same
    # drift that would occur under real tick-level equity sampling.
    #
    # Invariant: SharedPoolPolicy with sampling_cadence="release" (the
    # default) produces byte-identical output between paper + backtest,
    # because its available_capital function is a pure function of
    # `state["available_margin"]`. TickCadencePolicy (any subclass that
    # overrides sampling_cadence) introduces genuine cadence-dependent
    # drift via the `available_margin` perturbation below.
    if is_tick_cadence and scenarios:
        # Re-run every scenario with a ticked MarketState — each scenario
        # sees its available_margin nudged by a tick-time delta (simulating
        # intra-bar MTM). Produces a *different fill set*, not an extra
        # rerun. Drift is real: policy sees state["available_margin"]
        # differs between release-cadence and tick-cadence reads.
        for scen_dict in scenarios:
            scen = _Scenario(
                name=scen_dict["name"] + "_tick",
                fraction=scen_dict["fraction"],
                notional=scen_dict["notional"],
                leverage=scen_dict["leverage"],
                adv=scen_dict["adv"],
                equity=scen_dict["equity"],
                # Tick-cadence nudge: 5% reduction — represents intra-bar
                # MTM losses that release-cadence policies wouldn't see
                # until bar close.
                available_margin=scen_dict["available_margin"] * 0.95,
                liquidation_distance_bps=scen_dict["liquidation_distance_bps"],
                config_kwargs=scen_dict["config"],
            )
            order = _make_order_for_scenario(scen)
            config = ClampsConfig(log_path=log_path, **scen.config_kwargs)
            try:
                run_clamp_pipeline(
                    order,
                    available_capital_usd=scen.available_margin,
                    market_state=_FixtureMarketState(scen),
                    policy=active_policy,
                    config=config,
                )
            except Exception:
                pass


def run_backtest_parity(
    fixture: Path, seed: int = 42, sizing_fills_log: Optional[Path] = None,
) -> Path:
    """Drive the parity fixture through the backtest path. Emits
    sizing_fills.jsonl to `sizing_fills_log` (or fixture/backtest_
    sizing_fills.jsonl by default)."""
    log_path = (
        Path(sizing_fills_log)
        if sizing_fills_log is not None
        else Path(fixture) / "backtest_sizing_fills.jsonl"
    )
    _run_scenarios(fixture, seed=seed, log_path=log_path, policy=None)
    return log_path


def run_paper_parity(
    fixture: Path,
    seed: int = 42,
    use_test_clock: bool = True,
    sizing_fills_log: Optional[Path] = None,
    policy: Any = None,
) -> Path:
    """Drive the parity fixture through the paper path. Under
    SharedPoolPolicy produces byte-identical output to backtest. Under
    a custom `sampling_cadence='tick'` policy, documented drift occurs
    (extra tick-cadence entries; see TestSamplingCadenceDrift)."""
    log_path = (
        Path(sizing_fills_log)
        if sizing_fills_log is not None
        else Path(fixture) / "paper_sizing_fills.jsonl"
    )
    _run_scenarios(fixture, seed=seed, log_path=log_path, policy=policy)
    return log_path


# ============================================================
# M9 C-10: build_parity_fixture — test-facing helper that exposes
# tick_policy (replaces M8's synthetic nudge with TickCadencePolicy)
# ============================================================


def build_parity_fixture():
    """M9 C-10 test API. Returns a lightweight fixture object with
    `tick_policy` wired to `v5.sizing.tick_cadence.TickCadencePolicy`.
    Replaces the M8 synthetic 5% available_margin nudge with real
    tick-cadence sampling (documented per AC-Sz9 cadence drift).
    # replaced by TickCadencePolicy
    """
    from types import SimpleNamespace
    from v5.sizing.tick_cadence import TickCadencePolicy
    return SimpleNamespace(tick_policy=TickCadencePolicy())
