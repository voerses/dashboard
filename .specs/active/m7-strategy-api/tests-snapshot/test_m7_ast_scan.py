"""M7 — AC-D6 AST scan in strategy loader rejects banned clock calls (AC-V2).

Banned: time.time, time.time_ns, time.monotonic, time.perf_counter,
datetime.now, datetime.utcnow, datetime.today, pd.Timestamp.now,
np.datetime64('now'). Detects attribute + bare-import + aliased forms.

All tests MUST FAIL today — v5.strategy_loader does not exist.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


BANNED_ATTR_CALLS = (
    ("time.time", "import time\n\ndef generate(ctx, bar_idx):\n    t = time.time()\n    return None\n"),
    ("time.time_ns", "import time\n\ndef generate(ctx, bar_idx):\n    t = time.time_ns()\n    return None\n"),
    ("time.monotonic", "import time\n\ndef generate(ctx, bar_idx):\n    t = time.monotonic()\n    return None\n"),
    ("time.perf_counter", "import time\n\ndef generate(ctx, bar_idx):\n    t = time.perf_counter()\n    return None\n"),
    # Reviewer FIX-M2: time.gmtime / time.strftime / time.localtime used for
    # FIX TransactTime formatting — wall-clock readers per determinism invariant
    ("time.gmtime", "import time\n\ndef generate(ctx, bar_idx):\n    t = time.gmtime()\n    return None\n"),
    ("time.strftime", "import time\n\ndef generate(ctx, bar_idx):\n    t = time.strftime('%Y-%m-%d')\n    return None\n"),
    ("time.localtime", "import time\n\ndef generate(ctx, bar_idx):\n    t = time.localtime()\n    return None\n"),
    # FIX reviewer M2 extra additions — final-review sweep
    ("time.asctime", "import time\n\ndef generate(ctx, bar_idx):\n    t = time.asctime()\n    return None\n"),
    ("time.ctime", "import time\n\ndef generate(ctx, bar_idx):\n    t = time.ctime()\n    return None\n"),
    ("time.mktime", "import time\n\ndef generate(ctx, bar_idx):\n    t = time.mktime((2026,1,1,0,0,0,0,0,0))\n    return None\n"),
    ("datetime.now", "from datetime import datetime\n\ndef generate(ctx, bar_idx):\n    t = datetime.now()\n    return None\n"),
    ("datetime.utcnow", "from datetime import datetime\n\ndef generate(ctx, bar_idx):\n    t = datetime.utcnow()\n    return None\n"),
    ("datetime.today", "from datetime import datetime\n\ndef generate(ctx, bar_idx):\n    t = datetime.today()\n    return None\n"),
    ("pd.Timestamp.now", "import pandas as pd\n\ndef generate(ctx, bar_idx):\n    t = pd.Timestamp.now()\n    return None\n"),
    ("np.datetime64.now", "import numpy as np\n\ndef generate(ctx, bar_idx):\n    t = np.datetime64('now')\n    return None\n"),
)


BANNED_BARE_IMPORT_CALLS = (
    ("from time import time", "from time import time\n\ndef generate(ctx, bar_idx):\n    t = time()\n    return None\n"),
    ("from time import time_ns", "from time import time_ns\n\ndef generate(ctx, bar_idx):\n    t = time_ns()\n    return None\n"),
    ("from time import monotonic", "from time import monotonic\n\ndef generate(ctx, bar_idx):\n    t = monotonic()\n    return None\n"),
    ("from time import perf_counter", "from time import perf_counter\n\ndef generate(ctx, bar_idx):\n    t = perf_counter()\n    return None\n"),
    ("from time import gmtime", "from time import gmtime\n\ndef generate(ctx, bar_idx):\n    t = gmtime()\n    return None\n"),
    ("from time import strftime", "from time import strftime\n\ndef generate(ctx, bar_idx):\n    t = strftime('%Y-%m-%d')\n    return None\n"),
)


class TestStrategyLoaderImport:
    """AC-V2 — v5.strategy_loader.StrategyLoader + StrategyLoadError exist."""

    def test_loader_importable(self):
        from v5.strategy_loader import StrategyLoader  # noqa: F401

    def test_error_importable(self):
        from v5.strategy_loader import StrategyLoadError  # noqa: F401


class TestBannedAttributeAccessForms:
    """AC-V2 — attribute access form rejected (e.g. time.time())."""

    @pytest.mark.parametrize("label, source", BANNED_ATTR_CALLS)
    def test_banned_attr_call_rejected(self, tmp_path, label, source):
        from v5.strategy_loader import StrategyLoader, StrategyLoadError
        p = tmp_path / f"s_bad_attr_{label.replace('.', '_')}.py"
        p.write_text(source)
        with pytest.raises(StrategyLoadError) as ei:
            StrategyLoader().load(p)
        msg = str(ei.value)
        assert any(f in msg for f in (label, "clock", "wall", "time")), (
            f"AC-V2: error must reference banned call/clock; got: {msg!r}"
        )


class TestBannedBareImportForms:
    """AC-V2 — bare-import form rejected (e.g. `from time import time; time()`)."""

    @pytest.mark.parametrize("label, source", BANNED_BARE_IMPORT_CALLS)
    def test_banned_bare_import_call_rejected(self, tmp_path, label, source):
        from v5.strategy_loader import StrategyLoader, StrategyLoadError
        p = tmp_path / f"s_bad_bare_{hash(label) & 0xFFFF:04x}.py"
        p.write_text(source)
        with pytest.raises(StrategyLoadError):
            StrategyLoader().load(p)


class TestAliasedBareImport:
    """AC-V2 — aliased bare imports (`from time import time as t; t()`) rejected."""

    def test_aliased_bare_import_rejected(self, tmp_path):
        from v5.strategy_loader import StrategyLoader, StrategyLoadError
        p = tmp_path / "s_aliased.py"
        p.write_text(textwrap.dedent("""
            from time import time as _now

            def generate(ctx, bar_idx):
                x = _now()
                return None
        """))
        with pytest.raises(StrategyLoadError):
            StrategyLoader().load(p)


class TestCleanSourceAccepted:
    """AC-V2 — strategy with no banned calls loads without raising."""

    def test_clean_strategy_loads(self, tmp_path):
        from v5.strategy_loader import StrategyLoader
        p = tmp_path / "s_clean.py"
        p.write_text(textwrap.dedent("""
            import numpy as np

            def generate(ctx, bar_idx):
                t = ctx.clock.now()
                return None
        """))
        StrategyLoader().load(p)

    def test_clock_protocol_usage_allowed(self, tmp_path):
        """AC-V2 — 'time.time' as string in a docstring is not a call."""
        from v5.strategy_loader import StrategyLoader
        p = tmp_path / "s_docstring_ok.py"
        p.write_text(textwrap.dedent('''
            """This strategy intentionally does not call time.time(); uses ctx.clock."""
            def generate(ctx, bar_idx):
                return None
        '''))
        StrategyLoader().load(p)


class TestInitRejectsOnConstruction:
    """AC-V2 — rejection happens at load time, not at generate()."""

    def test_rejection_at_load_not_at_call(self, tmp_path):
        from v5.strategy_loader import StrategyLoader, StrategyLoadError
        p = tmp_path / "s_lazy.py"
        p.write_text("import time\ndef generate(ctx, bar_idx):\n    return time.time()\n")
        with pytest.raises(StrategyLoadError):
            StrategyLoader().load(p)
