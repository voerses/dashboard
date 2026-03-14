# V3 Legacy Tests (Deprecated)

These tests cover v3/ modules which are **frozen legacy** as of 2026-03-14 (v5.0).

All production code now imports from v4/. These tests are preserved for reference
but excluded from the default test run (no `conftest.py` in this directory).

To run them explicitly: `pytest tests/v3_legacy/ -v`
