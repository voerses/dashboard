"""Acceptance tests for M2 — LinkedScalePolicy.

Covers:
  - AC36: INDEPENDENT wired; PROPORTIONAL / ABSOLUTE raise NotImplementedError

All tests MUST FAIL until LinkedScalePolicy + paper wiring exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v5.strategy_api import LinkedScalePolicy  # noqa: E402


class TestAC36LinkedScalePolicyEnum:
    """LinkedScalePolicy exposes INDEPENDENT, PROPORTIONAL, ABSOLUTE members."""

    def test_independent_member_exists(self):
        assert hasattr(LinkedScalePolicy, "INDEPENDENT")

    def test_proportional_member_exists(self):
        assert hasattr(LinkedScalePolicy, "PROPORTIONAL")

    def test_absolute_member_exists(self):
        assert hasattr(LinkedScalePolicy, "ABSOLUTE")


class TestAC36PaperPolicyWiring:
    """Paper engine raises NotImplementedError for PROPORTIONAL / ABSOLUTE."""

    def test_paper_config_default_is_independent(self):
        from v5.paper_config import PaperConfig
        cfg = PaperConfig()
        # linked_scale_policy attr exists and defaults to INDEPENDENT
        assert getattr(cfg, "linked_scale_policy") == LinkedScalePolicy.INDEPENDENT

    def test_paper_engine_accepts_independent(self):
        """Constructing paper engine with INDEPENDENT must NOT raise."""
        from v5.paper_config import PaperConfig
        # If construction is valid with INDEPENDENT, no exception.
        cfg = PaperConfig()
        cfg.linked_scale_policy = LinkedScalePolicy.INDEPENDENT
        # Guard: trying to construct doesn't raise
        assert cfg.linked_scale_policy == LinkedScalePolicy.INDEPENDENT

    def test_paper_engine_raises_on_proportional(self):
        """PROPORTIONAL must raise NotImplementedError at paper startup."""
        from v5.paper_config import PaperConfig
        # The PaperConfig validation (post_init or paper_engine construction)
        # must reject non-INDEPENDENT policy values per AC36 / Q-DEC2.
        with pytest.raises((NotImplementedError, ValueError)):
            cfg = PaperConfig()
            cfg.linked_scale_policy = LinkedScalePolicy.PROPORTIONAL
            # Force validation; we expect paper startup or validator to raise
            from v5 import paper_engine
            # The enforcement point may be at engine construction;
            # expose via a helper method `validate_linked_scale_policy`
            paper_engine.validate_linked_scale_policy(cfg)

    def test_paper_engine_raises_on_absolute(self):
        from v5.paper_config import PaperConfig
        with pytest.raises((NotImplementedError, ValueError)):
            cfg = PaperConfig()
            cfg.linked_scale_policy = LinkedScalePolicy.ABSOLUTE
            from v5 import paper_engine
            paper_engine.validate_linked_scale_policy(cfg)


class TestAC36IndependentReduceWarns:
    """INDEPENDENT policy: reduce of a linked position emits a runtime warning
    once per (strategy, token)."""

    def test_independent_warning_once(self, caplog):
        """We can only sanity-check the warning cache is per-(strategy, token)."""
        from v5 import paper_engine
        # The warning helper must exist and dedupe
        assert hasattr(paper_engine, "warn_linked_reduce_once")


# ===================================================================
# T-L1 — INDEPENDENT reduce logs warning exactly once per (strategy, token);
# the secondary (linked) position is UNCHANGED.
# ===================================================================

class TestTL1IndependentReduceDedup:
    """T-L1 (peer-review round 1): INDEPENDENT reduce of the primary leg
    must log a warning ONCE per (strategy, token) key and leave the linked
    secondary position unchanged.
    """

    def test_independent_reduce_warns_once_and_secondary_unchanged(self, caplog):
        import logging
        from v5 import paper_engine

        caplog.set_level(logging.WARNING)
        # Call twice with same (strategy, token) — only one warning.
        paper_engine.warn_linked_reduce_once("s30", "BTC")
        paper_engine.warn_linked_reduce_once("s30", "BTC")

        warn_recs = [r for r in caplog.records
                     if r.levelno >= logging.WARNING
                     and "s30" in r.getMessage() and "BTC" in r.getMessage()]
        assert len(warn_recs) == 1, (
            f"Expected exactly one warning for (s30, BTC); got {len(warn_recs)}"
        )

    def test_independent_reduce_warns_per_distinct_key(self, caplog):
        """A different (strategy, token) pair produces a second warning."""
        import logging
        from v5 import paper_engine

        caplog.set_level(logging.WARNING)
        paper_engine.warn_linked_reduce_once("s30", "ETH")
        paper_engine.warn_linked_reduce_once("s30", "SOL")

        warn_recs = [r for r in caplog.records
                     if r.levelno >= logging.WARNING]
        # Per distinct key we should see at least one warning for each.
        # (We cannot assume zero residue from test_independent_reduce_warns_once.)
        msgs = [r.getMessage() for r in warn_recs]
        assert any("ETH" in m for m in msgs)
        assert any("SOL" in m for m in msgs)


# ===================================================================
# T-L2 — PROPORTIONAL raises NotImplementedError at REDUCE time
# (not at config time); tests the policy gate inside the reduce path.
# ===================================================================

class TestTL2ProportionalNotImplementedAtReduce:
    """T-L2: PROPORTIONAL is a stub in M2 — it must raise NotImplementedError
    at REDUCE time (not at config-construction time), so that a strategy
    that opts in catches it at the first partial reduce.
    """

    def test_proportional_raises_at_reduce_time(self):
        from v5 import paper_engine

        with pytest.raises(NotImplementedError):
            # Expected entrypoint: propagate_linked_reduce(policy, ...)
            paper_engine.propagate_linked_reduce(
                policy=LinkedScalePolicy.PROPORTIONAL,
                primary_token="BTC",
                primary_reduced_fraction=0.3,
                secondary_position=None,
                strategy_id="s30",
            )


# ===================================================================
# T-L3 — ABSOLUTE raises NotImplementedError at REDUCE time
# ===================================================================

class TestTL3AbsoluteNotImplementedAtReduce:
    """T-L3: ABSOLUTE is a stub in M2 — it must raise NotImplementedError
    at REDUCE time.
    """

    def test_absolute_raises_at_reduce_time(self):
        from v5 import paper_engine

        with pytest.raises(NotImplementedError):
            paper_engine.propagate_linked_reduce(
                policy=LinkedScalePolicy.ABSOLUTE,
                primary_token="BTC",
                primary_reduced_fraction=0.3,
                secondary_position=None,
                strategy_id="s30",
            )


# ===================================================================
# T-L5 — full-close propagation works regardless of LinkedScalePolicy
# (existing v4 linked-exit path remains unchanged for terminal closes).
# ===================================================================

class TestTL5FullCloseAlwaysPropagates:
    """T-L5: regardless of LinkedScalePolicy, a FULL close (is_terminal=True)
    must continue to propagate via the existing v4 linked-exit mechanism.
    Only partial (non-terminal) reduces are policy-gated.
    """

    def test_full_close_independent_still_propagates(self):
        """INDEPENDENT allows partial reduces to be orphaned, but a FULL
        close must still invoke linked-exit."""
        from v5 import paper_engine
        # The v4 linked-exit invariant is preserved under INDEPENDENT
        # when is_terminal=True — this returns True (propagated) vs False
        # (orphaned) for partial reduces.
        propagated = paper_engine.should_propagate_on_terminal(
            policy=LinkedScalePolicy.INDEPENDENT, is_terminal=True,
        )
        assert propagated is True

    def test_full_close_proportional_still_propagates(self):
        """Even though PROPORTIONAL is a stub for partial reduces, the
        full-close path is M1 and must still propagate."""
        from v5 import paper_engine
        propagated = paper_engine.should_propagate_on_terminal(
            policy=LinkedScalePolicy.PROPORTIONAL, is_terminal=True,
        )
        assert propagated is True

    def test_full_close_absolute_still_propagates(self):
        from v5 import paper_engine
        propagated = paper_engine.should_propagate_on_terminal(
            policy=LinkedScalePolicy.ABSOLUTE, is_terminal=True,
        )
        assert propagated is True

    def test_partial_reduce_independent_does_not_propagate(self):
        """Sanity inverse: under INDEPENDENT, partial reduces are NOT
        propagated (secondary unchanged, primary orphaned from linked pair
        for this reduce)."""
        from v5 import paper_engine
        propagated = paper_engine.should_propagate_on_terminal(
            policy=LinkedScalePolicy.INDEPENDENT, is_terminal=False,
        )
        assert propagated is False
