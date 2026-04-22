"""M11 AC-3 — Bridge infrastructure removed from v5/simulator.py.

All tests RED today — the helpers + kwargs still exist. Pins:

  - `simulate_portfolio` no longer accepts `strategies=` / `ctx=` kwargs
  - Legacy positional shape `simulate_portfolio(all_signals, strategy_specs,
    config)` is unchanged
  - Helpers `_build_token_bar_arrays_from_generate`,
    `_engine_precompute_fallback`, `_build_multi_token_ctx_from_bundle`,
    `_wrap_ctx_if_raw_bundle`, `_MutationGuard` are deleted
"""
from __future__ import annotations

import inspect

import pytest


def test_no_bridge_kwargs():
    """`simulate_portfolio` signature has no `strategies=` / `ctx=` kwargs."""
    from v5.simulator import simulate_portfolio

    sig = inspect.signature(simulate_portfolio)
    params = set(sig.parameters.keys())
    forbidden = {"strategies", "ctx"}
    leaked = forbidden & params
    assert not leaked, (
        f"simulate_portfolio still accepts {sorted(leaked)} — M11 AC-3 "
        f"requires the bridge kwargs to be deleted. Use run_backtest() "
        f"as the new top-level entry for event-driven execution."
    )


def test_helpers_deleted():
    """Bridge helpers on `v5.simulator` are gone."""
    import v5.simulator as sim_mod

    deleted = [
        "_build_token_bar_arrays_from_generate",
        "_engine_precompute_fallback",
        "_build_multi_token_ctx_from_bundle",
        "_wrap_ctx_if_raw_bundle",
        "_MutationGuard",
    ]
    still_present = [name for name in deleted if hasattr(sim_mod, name)]
    assert not still_present, (
        f"v5.simulator still has bridge helpers {still_present} — M11 AC-3 "
        f"requires them deleted. Their role is replaced by the event-driven "
        f"run_backtest() orchestrator + native signal processing."
    )


def test_legacy_positional_shape_unchanged():
    """`simulate_portfolio(all_signals, strategy_specs, config)` positional
    call shape is preserved (AC-3 explicitly protects ~30 legacy callers)."""
    from v5.simulator import simulate_portfolio

    sig = inspect.signature(simulate_portfolio)
    params = list(sig.parameters.keys())
    # First 3 positional params must stay: all_signals, strategy_specs, config
    assert params[0] == "all_signals"
    assert params[1] == "strategy_specs"
    assert params[2] == "config"
