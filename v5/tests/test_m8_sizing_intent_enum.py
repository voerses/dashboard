"""M8 AC-Sz1 — SizingIntent enum shape + validation.

Two engine intents only: FIXED_FRACTION, FIXED_NOTIONAL. (str, Enum)
matching M5/M7 FIX vocabulary convention. String .value preserves JSON
roundtrip.

All tests MUST FAIL today — v5.sizing package does not exist.
"""
from __future__ import annotations

import sys
from enum import Enum
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestSizingIntentEnumShape:
    """AC-Sz1 — SizingIntent is a (str, Enum) with exactly two values."""

    def test_sizing_intent_importable(self):
        from v5.sizing.intents import SizingIntent  # noqa: F401

    def test_sizing_intent_is_str_enum(self):
        from v5.sizing.intents import SizingIntent
        assert issubclass(SizingIntent, str), (
            "SizingIntent must subclass str (FIX vocabulary convention)"
        )
        assert issubclass(SizingIntent, Enum), (
            "SizingIntent must subclass Enum"
        )

    def test_has_fixed_fraction(self):
        from v5.sizing.intents import SizingIntent
        assert hasattr(SizingIntent, "FIXED_FRACTION")

    def test_has_fixed_notional(self):
        from v5.sizing.intents import SizingIntent
        assert hasattr(SizingIntent, "FIXED_NOTIONAL")

    def test_exactly_two_values(self):
        from v5.sizing.intents import SizingIntent
        members = list(SizingIntent)
        assert len(members) == 2, (
            f"SizingIntent must have exactly 2 values; got {len(members)}: "
            f"{[m.name for m in members]}"
        )

    def test_fixed_fraction_string_value(self):
        from v5.sizing.intents import SizingIntent
        assert SizingIntent.FIXED_FRACTION.value == "FIXED_FRACTION"

    def test_fixed_notional_string_value(self):
        from v5.sizing.intents import SizingIntent
        assert SizingIntent.FIXED_NOTIONAL.value == "FIXED_NOTIONAL"


class TestSizingIntentJsonRoundtrip:
    """AC-Sz1 — String values preserved for JSON roundtrip."""

    def test_json_dumps_preserves_string(self):
        import json
        from v5.sizing.intents import SizingIntent
        assert json.dumps(SizingIntent.FIXED_FRACTION.value) == '"FIXED_FRACTION"'
        assert json.dumps(SizingIntent.FIXED_NOTIONAL.value) == '"FIXED_NOTIONAL"'

    def test_json_loads_restores_enum(self):
        import json
        from v5.sizing.intents import SizingIntent
        s = json.dumps({"intent": SizingIntent.FIXED_FRACTION.value})
        back = json.loads(s)
        assert SizingIntent(back["intent"]) == SizingIntent.FIXED_FRACTION


class TestSizingRequestIntentValidation:
    """AC-Sz1 — Invalid string intents rejected at construction."""

    def test_bogus_intent_string_raises_value_error(self):
        from v5.sizing.intents import SizingRequest
        with pytest.raises(ValueError):
            SizingRequest(intent="BOGUS", fraction_of_equity=0.02)

    def test_enum_intent_accepted(self):
        from v5.sizing.intents import SizingIntent, SizingRequest
        req = SizingRequest(
            intent=SizingIntent.FIXED_FRACTION,
            fraction_of_equity=0.02,
        )
        assert req is not None

    def test_intent_is_enum_instance_runtime_checkable(self):
        from v5.sizing.intents import SizingIntent, SizingRequest
        req = SizingRequest(
            intent=SizingIntent.FIXED_FRACTION,
            fraction_of_equity=0.02,
        )
        assert isinstance(req.intent, SizingIntent)
