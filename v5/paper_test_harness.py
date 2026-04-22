"""V5 Paper — Legacy test harness helpers (post-M11-Commit-7 rework).

These helpers implement the pre-M11 batch-shape dispatch path that paper
used before ADR-0001 / ADR-0002 landed. They are preserved here — OUT OF
``v5/paper_engine.py`` — so that:

  * legacy determinism tests (``test_paper_determinism.py``) continue to
    exercise the simulator primitives against pre-baked ``TokenBarArrays``
    signals without regressing,
  * ``test_paper_live_integration.py::test_recomputed_signals_cover_current_bar``
    can keep asserting the long-signal non-crash contract, and
  * paper_engine.py is free of pre-baked-signal vocabulary end to end —
    the AC-10 architecture invariants (ADR-0001) pass by CORRECTNESS,
    not by alias/grep-evasion.

``PaperPortfolioEngine.process_tick`` is a thin wrapper around
``run_legacy_test_tick`` below. Nothing in the production event-driven
tick path (``_tick_internal_body`` → ``_dispatch_strategies_at_tick``)
touches this module; the imports are localized inside ``process_tick``.
"""
from __future__ import annotations

import dataclasses

import numpy as np

import v5.simulator as _sim


# ---------------------------------------------------------------------------
# Legacy sub-hourly ATR/ADV cache populator (test harness only)
# ---------------------------------------------------------------------------


def _legacy_refresh_cached_bar_data(engine, all_signals, bar_maps) -> None:
    """Pre-M11 populator of ``engine._cached_bar_data``.

    Walks pre-baked ``TokenBarArrays`` at the local bar produced by the
    per-call translator. The production tick path uses
    ``engine._refresh_cached_bar_data_from_market`` (``_build_bar_context``
    against MarketDataCache) instead.
    """
    from v5.simulator import _get_bar_data

    engine._cached_bar_data.clear()
    for sid, token_sigs in all_signals.items():
        for token, sig in token_sigs.items():
            bm = bar_maps.get(token)
            if bm is None:
                continue
            local_bar = int(bm[engine.tick_counter]) if engine.tick_counter < len(bm) else -1
            if local_bar == -1 or local_bar >= sig.n_bars:
                continue
            try:
                use_perp = None
                if sig.per_bar_is_perp is not None:
                    use_perp = bool(sig.per_bar_is_perp[local_bar])
                _, _, _, atr_val, adv_val, _ = _get_bar_data(
                    sig, local_bar, True, use_perp=use_perp,
                )
            except Exception:
                continue
            engine._cached_bar_data[(sid, token)] = {
                "atr": float(atr_val) if not np.isnan(atr_val) else 0.0,
                "adv": float(adv_val) if not np.isnan(adv_val) else 0.0,
                "sig": sig,
            }


# ---------------------------------------------------------------------------
# Legacy exits / entries / margin-calls delegators
# ---------------------------------------------------------------------------


def _legacy_process_exits(engine, all_signals, tick_translator) -> None:
    exit_res = getattr(engine, "_strategy_bar_resolution", {})

    for st in engine._get_all_states():
        for pos in st.position_manager.open_positions:
            if exit_res.get(pos.strategy_id, 0) == 0:
                pos.exit_handlers = []

    for _sid, token_sigs in all_signals.items():
        for _tok, sig in token_sigs.items():
            if sig.funding_1h is not None:
                sig.funding_1h[:] = 0.0
            if sig.perp_funding_1h is not None:
                sig.perp_funding_1h[:] = 0.0

    strategy_specs = {s.strategy_id: s for s in engine.config.strategies}
    if engine.config.mode == "independent":
        for sid, sstate in engine.strategy_states.items():
            if exit_res.get(sid, 0) > 0:
                continue
            sid_signals = {sid: all_signals.get(sid, {})}
            _sim._process_exits(
                sstate, sid_signals, tick_translator,
                engine.tick_counter, engine.config,
                strategy_specs=strategy_specs,
            )
    else:
        filtered_signals = {
            sid: (sigs if exit_res.get(sid, 0) == 0 else {})
            for sid, sigs in all_signals.items()
        }
        for pos in engine.state.position_manager.open_positions:
            if pos.strategy_id not in filtered_signals:
                filtered_signals[pos.strategy_id] = {}
        _sim._process_exits(
            engine.state, filtered_signals, tick_translator,
            engine.tick_counter, engine.config,
            strategy_specs=strategy_specs,
        )


def _legacy_process_entries(engine, all_signals, strategy_specs, tick_translator) -> None:
    entry_res = getattr(engine, "_strategy_bar_resolution", {})
    filtered_signals = {
        sid: sigs for sid, sigs in all_signals.items()
        if entry_res.get(sid, 0) == 0
    }
    filtered_specs = {
        sid: spec for sid, spec in strategy_specs.items()
        if entry_res.get(sid, 0) == 0
    }

    rng = np.random.RandomState(engine.config.seed + engine.tick_counter)
    if engine.config.mode == "independent":
        for sid, sstate in engine.strategy_states.items():
            if entry_res.get(sid, 0) > 0:
                continue
            sid_signals = {sid: filtered_signals.get(sid, {})}
            if sid in filtered_specs:
                orig_spec = filtered_specs[sid]
                spec_independent = dataclasses.replace(orig_spec, weight=1.0)
                sid_specs = {sid: spec_independent}
            else:
                sid_specs = {}
            _sim._process_entries(
                sstate, sid_signals, sid_specs,
                tick_translator, engine.tick_counter, engine.config, rng,
            )
    else:
        _sim._process_entries(
            engine.state, filtered_signals, filtered_specs,
            tick_translator, engine.tick_counter, engine.config, rng,
        )


def _legacy_process_margin_calls(engine, all_signals, tick_translator) -> None:
    exit_res = getattr(engine, "_strategy_bar_resolution", {})
    if engine.config.mode == "independent":
        for sid, sstate in engine.strategy_states.items():
            if exit_res.get(sid, 0) > 0:
                continue
            sid_signals = {sid: all_signals.get(sid, {})}
            _sim._process_margin_calls(
                sstate, sid_signals, tick_translator,
                engine.tick_counter, engine.config,
            )
    else:
        filtered_signals = {
            sid: (sigs if exit_res.get(sid, 0) == 0 else {})
            for sid, sigs in all_signals.items()
        }
        for pos in engine.state.position_manager.open_positions:
            if pos.strategy_id not in filtered_signals:
                filtered_signals[pos.strategy_id] = {}
        _sim._process_margin_calls(
            engine.state, filtered_signals, tick_translator,
            engine.tick_counter, engine.config,
        )


# ---------------------------------------------------------------------------
# Public entry — legacy deterministic tick (test only)
# ---------------------------------------------------------------------------


def run_legacy_test_tick(engine, all_signals, specs=None):
    """Legacy deterministic tick entry used by pre-M11 determinism tests.

    Builds the tick-counter→local-bar translator inline, routes the
    provided pre-baked ``TokenBarArrays`` through the pre-M11
    _process_exits / _process_entries / _process_margin_calls simulator
    primitives, then caches ATR/ADV, purges expired armed orders, and
    refreshes WS subscriptions. Increments ``engine.tick_counter``.

    Returns the ``TickResult`` the legacy caller expects.
    """
    from v5.paper_engine import TickResult

    if specs is None:
        specs = {s.strategy_id: s for s in engine.config.strategies}

    token_n_bars: dict[str, int] = {}
    for _sid, token_sigs in all_signals.items():
        for token, sig in token_sigs.items():
            n = getattr(sig, "n_bars", 0)
            if token not in token_n_bars or n > token_n_bars[token]:
                token_n_bars[token] = n

    translator: dict[str, np.ndarray] = {}
    size = engine.tick_counter + 1
    for token, n in token_n_bars.items():
        bm = np.full(size, -1, dtype=np.int32)
        local_bar = n - 1
        if engine.tick_counter < size:
            bm[engine.tick_counter] = local_bar
        if engine.tick_counter > 0 and local_bar > 0:
            bm[engine.tick_counter - 1] = local_bar - 1
        translator[token] = bm

    _legacy_process_exits(engine, all_signals, translator)
    _legacy_process_margin_calls(engine, all_signals, translator)
    if not specs:
        specs = {s.strategy_id: s for s in engine.config.strategies}
    _legacy_process_entries(engine, all_signals, specs, translator)

    if getattr(engine, "_effective_bar_resolution", 0) > 0:
        _legacy_refresh_cached_bar_data(engine, all_signals, translator)
        engine._purge_expired_armed_orders()
        engine._update_ws_subscriptions()

    result = TickResult(
        tick_counter=engine.tick_counter,
        open_positions=engine._aggregate_open_positions(),
        portfolio_equity=engine._aggregate_portfolio_equity(),
    )
    engine.tick_counter += 1
    return result
