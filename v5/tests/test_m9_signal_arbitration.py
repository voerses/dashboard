"""M9 AC #3 — SignalArbitrationPolicy + min_slot_guarantee + Phase 3.05 dedup.

Covers AC #3 sub-parts:
  (a) RandomShuffle default fairness over 100 bars (uncalibrated priorities
      admission rates within 2 sigma of equal) + seed-determinism
  (b) PriorityDesc top-3-by-priority for 10 signals + max_positions=3
  (c) TieredPriority(tier_quantiles=[0.33, 0.66]) 3-bucket rank-space behavior
  (d) RoundRobin(weights=None) equal rotation across strategies
  (e) StrategySpec.min_slot_guarantee=2 pre-reserves 2 slots
  (f) scope="portfolio" vs scope="strategy" parameter semantics
  (g) sum(min_slot_guarantee) > max_portfolio_positions raises ValueError
  (h) zero-signal-strategy with min_slot_guarantee=2 leaves slots unused
  (i) min_slot_guarantee=5 + max_positions=3 -> effective guarantee 3
  (j) guarantee counted post-dedup (Phase 3.05 runs before guarantee)
  + Phase ordering: dedup -> guarantee -> arbitration

All tests MUST FAIL today — v5.arbitration module does not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _candidate(strategy_id: str, token: str, priority: float):
    """Construct a synthetic EntryCandidate by its public constructor."""
    from v5.arbitration import EntryCandidate
    return EntryCandidate(
        strategy_id=strategy_id,
        token=token,
        priority=priority,
    )


@pytest.fixture
def ten_signals_mixed_priority():
    """10 candidates across 2 strategies, priorities spread 0.1 .. 1.0."""
    out = []
    priorities = [0.9, 0.2, 0.7, 0.4, 1.0, 0.1, 0.6, 0.3, 0.8, 0.5]
    tokens = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]
    for i, (p, t) in enumerate(zip(priorities, tokens)):
        strat = "sA" if i % 2 == 0 else "sB"
        out.append(_candidate(strat, t, p))
    return out


@pytest.fixture
def two_strategies_three_each():
    """3 candidates from strategy sA, 3 from sB. Priorities monotone within."""
    return [
        _candidate("sA", "A1", 0.9),
        _candidate("sA", "A2", 0.6),
        _candidate("sA", "A3", 0.3),
        _candidate("sB", "B1", 0.8),
        _candidate("sB", "B2", 0.5),
        _candidate("sB", "B3", 0.2),
    ]


def _sim_state(seed: int = 0):
    """Build a minimal SimulationState with a seeded rng for determinism."""
    from v5.arbitration import SimulationState
    return SimulationState(rng=np.random.default_rng(seed))


# ---------------------------------------------------------------------------
# AC #3(a) — RandomShuffle default + determinism + fairness
# ---------------------------------------------------------------------------


class TestRandomShuffleDefault:
    """AC #3(a): RandomShuffle is the DEFAULT policy, seed-deterministic,
    and fair over 100 bars of uncalibrated priorities."""

    def test_random_shuffle_is_default_in_portfolio_config(self):
        """AC #3(a): PortfolioConfig.arbitration_policy defaults to RandomShuffle."""
        from v5.arbitration import RandomShuffle
        from v5.config import PortfolioConfig
        cfg = PortfolioConfig(strategies=[], capital=100_000.0)
        assert isinstance(cfg.arbitration_policy, RandomShuffle)

    def test_random_shuffle_seeded_determinism(self, ten_signals_mixed_priority):
        """AC #3(a): same seed -> identical rank() output."""
        from v5.arbitration import RandomShuffle
        policy = RandomShuffle()
        out1 = policy.rank(list(ten_signals_mixed_priority), _sim_state(seed=42))
        out2 = policy.rank(list(ten_signals_mixed_priority), _sim_state(seed=42))
        assert [(c.strategy_id, c.token) for c in out1] == \
               [(c.strategy_id, c.token) for c in out2]

    def test_random_shuffle_different_seeds_produce_different_order(
        self, ten_signals_mixed_priority
    ):
        """AC #3(a): distinct seeds produce distinct orderings (not priority-sorted)."""
        from v5.arbitration import RandomShuffle
        policy = RandomShuffle()
        out1 = policy.rank(list(ten_signals_mixed_priority), _sim_state(seed=1))
        out2 = policy.rank(list(ten_signals_mixed_priority), _sim_state(seed=2))
        assert [(c.strategy_id, c.token) for c in out1] != \
               [(c.strategy_id, c.token) for c in out2]

    def test_random_shuffle_fairness_over_100_bars(self):
        """AC #3(a): 100 bars × uncalibrated priorities → both strategies'
        admission rates within 2 sigma of 50% (under max_positions=1 per bar)."""
        from v5.arbitration import RandomShuffle
        policy = RandomShuffle()
        admitted_counts = {"sA": 0, "sB": 0}
        n_bars = 100
        # sA uses large priorities (z-score-like); sB uses small [0,1] priorities
        for bar in range(n_bars):
            cands = [
                _candidate("sA", "A", 3.5),
                _candidate("sB", "B", 0.7),
            ]
            out = policy.rank(cands, _sim_state(seed=bar))
            # max_positions = 1 -> first candidate is the admitted one
            admitted_counts[out[0].strategy_id] += 1
        # under fair random selection mean=50, stddev=sqrt(100*0.5*0.5)=5
        # 2 sigma window = [40, 60]
        assert 40 <= admitted_counts["sA"] <= 60, (
            f"Admission rate unfair: sA={admitted_counts['sA']} "
            f"out of {n_bars} (expected ~50, 2σ=[40,60])"
        )
        assert 40 <= admitted_counts["sB"] <= 60


# ---------------------------------------------------------------------------
# AC #3(b) — PriorityDesc
# ---------------------------------------------------------------------------


class TestPriorityDescTopN:
    """AC #3(b): PriorityDesc returns top-N-by-priority, ties broken by
    (strategy_id, token) lexicographic order."""

    def test_priority_desc_top_3_of_10(self, ten_signals_mixed_priority):
        """AC #3(b): top-3 of 10 signals by descending priority."""
        from v5.arbitration import PriorityDesc
        policy = PriorityDesc()
        out = policy.rank(list(ten_signals_mixed_priority), _sim_state())
        # first 3 by descending priority: 1.0, 0.9, 0.8
        top3 = [c.priority for c in out[:3]]
        assert top3 == [1.0, 0.9, 0.8], f"expected [1.0, 0.9, 0.8] got {top3}"

    def test_priority_desc_ties_broken_by_lex(self):
        """AC #3(b): ties at same priority resolved by (strategy_id, token)."""
        from v5.arbitration import PriorityDesc
        cands = [
            _candidate("sB", "ZZZ", 0.5),
            _candidate("sA", "BBB", 0.5),
            _candidate("sA", "AAA", 0.5),
        ]
        out = PriorityDesc().rank(cands, _sim_state())
        ordered = [(c.strategy_id, c.token) for c in out]
        assert ordered == [("sA", "AAA"), ("sA", "BBB"), ("sB", "ZZZ")]


# ---------------------------------------------------------------------------
# AC #3(c) — TieredPriority
# ---------------------------------------------------------------------------


class TestTieredPriority:
    """AC #3(c): TieredPriority(tier_quantiles=[0.33, 0.66]) 3-bucket behavior."""

    def test_tiered_priority_default_quantiles_produce_3_tiers(self):
        """AC #3(c): 9 candidates with default tier_quantiles → 3 tiers × 3 each."""
        from v5.arbitration import TieredPriority
        cands = [_candidate("s", f"T{i}", float(i)) for i in range(9)]
        # admit tag populated by policy; we introspect via the public tier_of() helper
        policy = TieredPriority(tier_quantiles=[0.33, 0.66])
        out = policy.rank(list(cands), _sim_state(seed=7))
        # every candidate has tier attribute in [0, 1, 2]
        tiers = [c.tier for c in out]
        assert set(tiers) == {0, 1, 2}
        # 3-each split
        for t in (0, 1, 2):
            assert tiers.count(t) == 3

    def test_tiered_priority_top_tier_contains_highest_priorities(self):
        """AC #3(c): top tier (index 2) contains the 3 highest-priority candidates."""
        from v5.arbitration import TieredPriority
        cands = [_candidate("s", f"T{i}", float(i)) for i in range(9)]
        out = TieredPriority(tier_quantiles=[0.33, 0.66]).rank(
            list(cands), _sim_state(seed=7)
        )
        top_tier = [c for c in out if c.tier == 2]
        priorities = sorted(c.priority for c in top_tier)
        assert priorities == [6.0, 7.0, 8.0]

    def test_tiered_priority_intra_tier_shuffle_seeded(self):
        """AC #3(c): within a tier, order is shuffled but deterministic on seed."""
        from v5.arbitration import TieredPriority
        cands = [_candidate("s", f"T{i}", float(i)) for i in range(9)]
        out1 = TieredPriority().rank(list(cands), _sim_state(seed=11))
        out2 = TieredPriority().rank(list(cands), _sim_state(seed=11))
        # seed-deterministic
        assert [c.token for c in out1] == [c.token for c in out2]


# ---------------------------------------------------------------------------
# AC #3(d) — RoundRobin
# ---------------------------------------------------------------------------


class TestRoundRobin:
    """AC #3(d): RoundRobin(weights=None) rotates strategies equally."""

    def test_round_robin_none_weights_rotates_equally(self, two_strategies_three_each):
        """AC #3(d): output alternates sA / sB / sA / sB / ... each strategy's
        top pick first."""
        from v5.arbitration import RoundRobin
        out = RoundRobin(weights=None).rank(
            list(two_strategies_three_each), _sim_state()
        )
        strats = [c.strategy_id for c in out]
        # first 6: two strategies alternating -> 3 each exactly
        assert strats.count("sA") == 3
        assert strats.count("sB") == 3
        # strict alternation: positions 0,2,4 from one strat, 1,3,5 from the other
        assert strats[0] != strats[1]
        assert strats[0] == strats[2] == strats[4]
        assert strats[1] == strats[3] == strats[5]

    def test_round_robin_picks_each_strategys_best_first(
        self, two_strategies_three_each
    ):
        """AC #3(d): within a strategy, its next-best (by priority) is chosen."""
        from v5.arbitration import RoundRobin
        out = RoundRobin(weights=None).rank(
            list(two_strategies_three_each), _sim_state()
        )
        # sA's picks should be ordered by descending priority: A1, A2, A3
        sA_tokens = [c.token for c in out if c.strategy_id == "sA"]
        sB_tokens = [c.token for c in out if c.strategy_id == "sB"]
        assert sA_tokens == ["A1", "A2", "A3"]
        assert sB_tokens == ["B1", "B2", "B3"]


# ---------------------------------------------------------------------------
# AC #3(e) — min_slot_guarantee basic reservation
# ---------------------------------------------------------------------------


class TestMinSlotGuarantee:
    """AC #3(e): StrategySpec.min_slot_guarantee pre-reserves slots before
    cross-strategy contention runs."""

    def test_min_slot_guarantee_2_reserves_two_slots(self):
        """AC #3(e): strategy with min_slot_guarantee=2 gets 2 slots even when
        outranked on priority."""
        from v5.arbitration import apply_arbitration, PriorityDesc
        from v5.config import StrategySpec
        specs = [
            StrategySpec(name="sA", min_slot_guarantee=2, max_positions=10),
            StrategySpec(name="sB", min_slot_guarantee=0, max_positions=10),
        ]
        cands = [
            _candidate("sA", "A1", 0.1),   # low priority
            _candidate("sA", "A2", 0.1),   # low priority
            _candidate("sB", "B1", 0.99),  # top priority
            _candidate("sB", "B2", 0.95),
            _candidate("sB", "B3", 0.90),
        ]
        admitted = apply_arbitration(
            cands,
            strategies=specs,
            max_portfolio_positions=3,
            policy=PriorityDesc(),
            state=_sim_state(),
        )
        # sA must get 2 slots; 1 remaining goes to sB's best
        sA_count = sum(1 for c in admitted if c.strategy_id == "sA")
        sB_count = sum(1 for c in admitted if c.strategy_id == "sB")
        assert sA_count == 2, f"expected sA=2 guarantees got {sA_count}"
        assert sB_count == 1, f"expected sB=1 residual got {sB_count}"


# ---------------------------------------------------------------------------
# AC #3(f) — scope parameter semantics
# ---------------------------------------------------------------------------


class TestScopeParameter:
    """AC #3(f): scope="portfolio" vs scope="strategy" parameter semantics."""

    def test_scope_portfolio_ranks_all_candidates_globally(
        self, two_strategies_three_each
    ):
        """AC #3(f): scope='portfolio' single call over union of candidates."""
        from v5.arbitration import PriorityDesc
        out = PriorityDesc().rank(
            list(two_strategies_three_each),
            _sim_state(),
            scope="portfolio",
        )
        # top 2 by priority across all strats: A1 (0.9), B1 (0.8)
        assert (out[0].strategy_id, out[0].token) == ("sA", "A1")
        assert (out[1].strategy_id, out[1].token) == ("sB", "B1")

    def test_scope_strategy_ranks_within_single_strategy_only(self):
        """AC #3(f): scope='strategy' is invoked per-strategy; candidates all
        carry the same strategy_id when called under scope='strategy'."""
        from v5.arbitration import PriorityDesc
        cands = [
            _candidate("sA", "A1", 0.3),
            _candidate("sA", "A2", 0.9),
            _candidate("sA", "A3", 0.5),
        ]
        out = PriorityDesc().rank(list(cands), _sim_state(), scope="strategy")
        assert [c.token for c in out] == ["A2", "A3", "A1"]


# ---------------------------------------------------------------------------
# Edge cases — AC #3(g) through (j)
# ---------------------------------------------------------------------------


class TestMinSlotGuaranteeEdgeCases:
    """AC #3(g)-(j): 4 min_slot_guarantee edge cases."""

    def test_sum_of_guarantees_over_max_raises_value_error(self):
        """AC #3(g): sum(min_slot_guarantee) > max_portfolio_positions → ValueError."""
        from v5.config import PortfolioConfig, StrategySpec
        specs = [
            StrategySpec(name="sA", min_slot_guarantee=3),
            StrategySpec(name="sB", min_slot_guarantee=3),
        ]
        # 3+3=6 > 5
        with pytest.raises(ValueError, match="min_slot_guarantee"):
            PortfolioConfig(
                strategies=specs,
                capital=100_000.0,
                max_portfolio_positions=5,
            )

    def test_zero_signal_strategy_guarantee_slot_unused(self):
        """AC #3(h): strategy with 0 signals + min_slot_guarantee=2 leaves
        those 2 slots UNUSED that bar (not released to cross-strategy pool)."""
        from v5.arbitration import apply_arbitration, PriorityDesc
        from v5.config import StrategySpec
        specs = [
            StrategySpec(name="sA", min_slot_guarantee=2, max_positions=10),
            StrategySpec(name="sB", min_slot_guarantee=0, max_positions=10),
        ]
        # sA emits ZERO candidates this bar; sB wants 5 slots
        cands = [
            _candidate("sB", "B1", 0.9),
            _candidate("sB", "B2", 0.8),
            _candidate("sB", "B3", 0.7),
            _candidate("sB", "B4", 0.6),
            _candidate("sB", "B5", 0.5),
        ]
        admitted = apply_arbitration(
            cands,
            strategies=specs,
            max_portfolio_positions=5,
            policy=PriorityDesc(),
            state=_sim_state(),
        )
        # sA's 2 slots unused; sB gets ONLY 3 (5 - 2 reserved but unfilled)
        sB_count = sum(1 for c in admitted if c.strategy_id == "sB")
        assert sB_count == 3, (
            f"sA 2-slot guarantee must be reserved deterministically even if "
            f"sA emits nothing; sB got {sB_count} (expected 3 = 5-2)"
        )

    def test_guarantee_clamped_to_max_positions(self):
        """AC #3(i): min_slot_guarantee=5 + max_positions=3 → effective guarantee
        = min(5, 3) = 3."""
        from v5.arbitration import apply_arbitration, PriorityDesc
        from v5.config import StrategySpec
        specs = [
            StrategySpec(name="sA", min_slot_guarantee=5, max_positions=3),
            StrategySpec(name="sB", min_slot_guarantee=0, max_positions=10),
        ]
        cands = [
            _candidate("sA", f"A{i}", 0.1) for i in range(5)
        ] + [
            _candidate("sB", "B1", 0.99),
            _candidate("sB", "B2", 0.95),
            _candidate("sB", "B3", 0.90),
            _candidate("sB", "B4", 0.85),
        ]
        admitted = apply_arbitration(
            cands,
            strategies=specs,
            max_portfolio_positions=7,
            policy=PriorityDesc(),
            state=_sim_state(),
        )
        sA_count = sum(1 for c in admitted if c.strategy_id == "sA")
        # effective guarantee = min(5, 3) = 3
        assert sA_count == 3, f"expected 3 sA admits got {sA_count}"

    def test_guarantee_counted_post_dedup(self):
        """AC #3(j): guarantee count is post-dedup (Phase 3.05 runs first).

        Strategy with max_positions_per_symbol=1 and 3 duplicate-token signals
        is de-duped to 1; remaining guarantee slot is left UNUSED (does not
        pull extra slot for a non-duplicate)."""
        from v5.arbitration import apply_arbitration, PriorityDesc
        from v5.config import StrategySpec
        specs = [
            StrategySpec(
                name="sA",
                min_slot_guarantee=2,
                max_positions=10,
                max_positions_per_symbol=1,
            ),
            StrategySpec(name="sB", min_slot_guarantee=0, max_positions=10),
        ]
        # sA emits 3 candidates ALL on symbol "BTC"
        cands = [
            _candidate("sA", "BTC", 0.9),
            _candidate("sA", "BTC", 0.8),
            _candidate("sA", "BTC", 0.7),
            _candidate("sB", "B1", 0.95),
            _candidate("sB", "B2", 0.90),
            _candidate("sB", "B3", 0.85),
        ]
        admitted = apply_arbitration(
            cands,
            strategies=specs,
            max_portfolio_positions=3,
            policy=PriorityDesc(),
            state=_sim_state(),
        )
        # after dedup sA has 1 candidate; guarantee = min(2, 1) = 1 (post-dedup)
        sA_count = sum(1 for c in admitted if c.strategy_id == "sA")
        sB_count = sum(1 for c in admitted if c.strategy_id == "sB")
        assert sA_count == 1, (
            f"expected sA=1 after dedup (post-dedup guarantee count); got {sA_count}"
        )
        # 3 max portfolio - 1 sA - 1 reserved-but-unfilled = 1 remaining for sB,
        # but the guarantee ONLY reserves post-dedup count, so sB gets 2
        assert sB_count == 2


# ---------------------------------------------------------------------------
# Phase ordering — AC #3 "Phase ordering (pinned)":
#   dedup -> guarantee -> arbitration -> per-order M8 clamps
# ---------------------------------------------------------------------------


class TestPhaseOrdering:
    """AC #3 Phase-ordering pin: dedup → guarantee → arbitration."""

    def test_dedup_runs_before_arbitration(self):
        """Phase 3.05 dedup MUST run before .rank() — so a strategy with
        max_positions_per_symbol=1 and 2 signals on same token arrives at
        .rank() as a single candidate."""
        from v5.arbitration import apply_arbitration, PriorityDesc
        from v5.config import StrategySpec
        specs = [
            StrategySpec(
                name="sA",
                min_slot_guarantee=0,
                max_positions=10,
                max_positions_per_symbol=1,
            ),
        ]
        cands = [
            _candidate("sA", "BTC", 0.9),
            _candidate("sA", "BTC", 0.8),  # duplicate token — must be deduped
        ]
        admitted = apply_arbitration(
            cands,
            strategies=specs,
            max_portfolio_positions=5,
            policy=PriorityDesc(),
            state=_sim_state(),
        )
        # 2 -> 1 after dedup; only highest-priority BTC survives
        btc_admits = [c for c in admitted if c.token == "BTC"]
        assert len(btc_admits) == 1
        assert btc_admits[0].priority == 0.9
