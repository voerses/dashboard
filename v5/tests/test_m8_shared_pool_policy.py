"""M8 — CapitalAllocationPolicy Protocol + SharedPoolPolicy default.

Design §8 adds:
  - class CapitalAllocationPolicy(Protocol): sampling_cadence attribute +
    available_capital(strategy_id, state, clock_now_ns) method.
  - class SharedPoolPolicy implementing the Protocol (identity behavior
    mirroring today's single-pool engine).
  - class AllocationState(TypedDict) with exactly 4 fields:
    available_margin, per_strategy_equity, rolling_pnl_24h,
    current_positions_notional.
  - sampling_cadence Literal['bar_close','tick','release'] — M9 tick
    policies subclass SharedPoolPolicy and override sampling_cadence.
  - funding_buffer_pct subtracted BEFORE policy.available_capital is
    called (design §7.1).

All tests MUST FAIL today — v5/sizing/allocation.py does not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestSharedPoolIdentity:
    """SharedPoolPolicy.available_capital is identity over available_margin."""

    def test_shared_pool_is_identity_over_available_margin(self):
        from v5.sizing.allocation import AllocationState, SharedPoolPolicy
        state = AllocationState(
            available_margin=150_000.0,
            per_strategy_equity={"s_test": 150_000.0},
            rolling_pnl_24h={},
            current_positions_notional={},
        )
        policy = SharedPoolPolicy()
        assert policy.available_capital("any_strat", state, 0) == 150_000.0

    def test_shared_pool_ignores_strategy_id(self):
        """Same state → same available regardless of strategy_id."""
        from v5.sizing.allocation import AllocationState, SharedPoolPolicy
        state = AllocationState(
            available_margin=250_000.0,
            per_strategy_equity={"s_a": 100_000.0, "s_b": 50_000.0},
            rolling_pnl_24h={},
            current_positions_notional={},
        )
        policy = SharedPoolPolicy()
        assert policy.available_capital("s_a", state, 0) == (
            policy.available_capital("s_b", state, 0)
        )


class TestSharedPoolSamplingCadence:
    """sampling_cadence is a class attribute with value 'release'."""

    def test_shared_pool_sampling_cadence_is_release(self):
        from v5.sizing.allocation import SharedPoolPolicy
        assert SharedPoolPolicy.sampling_cadence == "release"

    def test_sampling_cadence_is_literal_type_valid_value(self):
        """Allowed values: 'bar_close' | 'tick' | 'release'."""
        from v5.sizing.allocation import SharedPoolPolicy
        allowed = {"bar_close", "tick", "release"}
        assert SharedPoolPolicy.sampling_cadence in allowed


class TestAllocationStateTypedDict:
    """AllocationState TypedDict exposes exactly 4 fields."""

    def test_allocation_state_typeddict_has_4_fields(self):
        from v5.sizing.allocation import AllocationState
        # TypedDict annotations are accessible via __annotations__
        fields = set(AllocationState.__annotations__.keys())
        expected = {
            "available_margin",
            "per_strategy_equity",
            "rolling_pnl_24h",
            "current_positions_notional",
        }
        assert fields == expected, (
            f"AllocationState must expose exactly {expected}; got {fields}"
        )

    def test_allocation_state_constructable(self):
        from v5.sizing.allocation import AllocationState
        s = AllocationState(
            available_margin=100.0,
            per_strategy_equity={"s": 100.0},
            rolling_pnl_24h={"s": 0.0},
            current_positions_notional={"BTC": 0.0},
        )
        assert s["available_margin"] == 100.0


class TestCapitalAllocationPolicyProtocol:
    """SharedPoolPolicy satisfies the CapitalAllocationPolicy Protocol."""

    def test_capital_allocation_policy_is_protocol(self):
        from v5.sizing.allocation import CapitalAllocationPolicy, SharedPoolPolicy
        # Protocol runtime check: SharedPoolPolicy structurally conforms.
        assert isinstance(SharedPoolPolicy(), CapitalAllocationPolicy)

    def test_protocol_declares_sampling_cadence(self):
        from v5.sizing.allocation import CapitalAllocationPolicy
        assert hasattr(CapitalAllocationPolicy, "sampling_cadence")

    def test_protocol_declares_available_capital(self):
        from v5.sizing.allocation import CapitalAllocationPolicy
        assert hasattr(CapitalAllocationPolicy, "available_capital")


class TestSharedPoolConfigRoundtrip:
    """to_config() / from_config() preserves policy identity for paper-state JSON."""

    def test_shared_pool_to_config_from_config_roundtrip(self):
        from v5.sizing.allocation import SharedPoolPolicy
        original = SharedPoolPolicy()
        serialized = original.to_config()
        assert isinstance(serialized, dict)
        restored = SharedPoolPolicy.from_config(serialized)
        # Both instances behave identically
        assert restored.sampling_cadence == original.sampling_cadence
        assert type(restored) is type(original)


class TestFreeCapitalClampInvokesPolicy:
    """The free-capital clamp must call policy.available_capital(...)."""

    def test_free_capital_clamp_calls_policy(self):
        """Inject a spy policy, run the free-capital clamp, assert call happened."""
        from v5.sizing.allocation import AllocationState, SharedPoolPolicy
        from v5.sizing.clamps import ClampsConfig, run_clamp_pipeline
        from v5.sizing.intents import SizingIntent, SizingRequest
        from v5.orders import TriggerType
        from v5.universe_context import UniverseContext

        calls = []

        class SpyPolicy(SharedPoolPolicy):
            def available_capital(self, strategy_id, state, clock_now_ns):
                calls.append((strategy_id, dict(state), clock_now_ns))
                return state["available_margin"]

        class _MarketState:
            def adv(self, token): return 1e9
            def rolling_adv(self, token, window_hours=24): return 1e9
            def mark_price(self, token): return 50_000.0
            def liquidation_distance(self, position, leverage): return 10_000.0
            def equity(self, strategy_id): return 100_000.0
            def free_margin(self, strategy_id, policy):
                state = AllocationState(
                    available_margin=50_000.0,
                    per_strategy_equity={strategy_id: 100_000.0},
                    rolling_pnl_24h={},
                    current_positions_notional={},
                )
                return policy.available_capital(strategy_id, state, 0)

        ctx = UniverseContext.build_test(tokens=["BTC"], bars=100, seed=0,
                                         equity=100_000.0)
        order = ctx.orders.arm(
            symbol="BTC", direction="LONG", size=1.0,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=50_000.0,
            sizing=SizingRequest(
                intent=SizingIntent.FIXED_NOTIONAL, notional_usd=30_000.0,
            ),
        )
        order = order.trigger_immediately()
        run_clamp_pipeline(
            order,
            available_capital_usd=50_000.0,
            market_state=_MarketState(),
            policy=SpyPolicy(),
            config=ClampsConfig(
                adv_cap_pct=1.0, concentration_limit=1.0,
                min_position_usd=10.0, min_liquidation_distance_bps=100.0,
            ),
        )
        assert calls, "free-capital clamp must invoke policy.available_capital"


class TestFundingBufferSubtractedBeforePolicyCall:
    """AC-design §7.1 — funding_buffer_pct reduces available_margin BEFORE policy."""

    def test_funding_buffer_subtracted_before_policy(self):
        """Non-tautological: feed RAW equity into available_capital_usd; the
        clamp pipeline (design §7.1) MUST subtract equity×funding_buffer_pct
        before invoking policy.available_capital. If the clamp layer forgot
        the subtraction, the policy sees the raw value and this test fires.
        """
        from v5.sizing.allocation import AllocationState, SharedPoolPolicy
        from v5.sizing.clamps import ClampsConfig, run_clamp_pipeline
        from v5.sizing.intents import SizingIntent, SizingRequest
        from v5.orders import TriggerType
        from v5.universe_context import UniverseContext

        EQUITY = 100_000.0
        FUNDING_BUFFER_PCT = 0.05  # 5% → buffer = $5,000
        RAW_AVAILABLE = EQUITY                                  # fed UNREDUCED
        EXPECTED_AFTER_BUFFER = EQUITY - EQUITY * FUNDING_BUFFER_PCT  # $95,000
        seen_state = {}

        class AssertPolicy(SharedPoolPolicy):
            def available_capital(self, strategy_id, state, clock_now_ns):
                seen_state["available_margin"] = state["available_margin"]
                return state["available_margin"]

        class _MarketState:
            def adv(self, token): return 1e9
            def rolling_adv(self, token, window_hours=24): return 1e9
            def mark_price(self, token): return 50_000.0
            def liquidation_distance(self, position, leverage): return 10_000.0
            def equity(self, strategy_id): return EQUITY
            def free_margin(self, strategy_id, policy):
                # RAW — no pre-subtraction. The clamp pipeline is
                # responsible for applying funding_buffer_pct.
                state = AllocationState(
                    available_margin=RAW_AVAILABLE,
                    per_strategy_equity={strategy_id: EQUITY},
                    rolling_pnl_24h={},
                    current_positions_notional={},
                )
                return policy.available_capital(strategy_id, state, 0)

        ctx = UniverseContext.build_test(tokens=["BTC"], bars=100, seed=0, equity=EQUITY)
        order = ctx.orders.arm(
            symbol="BTC", direction="LONG", size=1.0,
            trigger=TriggerType.PRICE_ABOVE, trigger_price=50_000.0,
            sizing=SizingRequest(
                intent=SizingIntent.FIXED_NOTIONAL, notional_usd=30_000.0,
            ),
        )
        order = order.trigger_immediately()
        run_clamp_pipeline(
            order,
            available_capital_usd=RAW_AVAILABLE,     # RAW (not pre-subtracted)
            market_state=_MarketState(),
            policy=AssertPolicy(),
            config=ClampsConfig(
                adv_cap_pct=1.0, concentration_limit=1.0,
                min_position_usd=10.0, min_liquidation_distance_bps=100.0,
                funding_buffer_pct=FUNDING_BUFFER_PCT,
            ),
        )
        # If the clamp pipeline forgot to subtract the buffer, the policy
        # saw RAW_AVAILABLE ($100k) instead of EXPECTED_AFTER_BUFFER ($95k).
        observed = seen_state.get("available_margin")
        assert observed == pytest.approx(EXPECTED_AFTER_BUFFER), (
            f"design §7.1 violation: free-capital clamp must subtract "
            f"equity×funding_buffer_pct BEFORE calling policy. "
            f"Expected ${EXPECTED_AFTER_BUFFER:,.0f}, policy observed "
            f"${observed:,.0f}. If this equals ${RAW_AVAILABLE:,.0f} the "
            f"clamp pipeline forgot the subtraction."
        )
