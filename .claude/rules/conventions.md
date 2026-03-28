# Project Conventions — Python Crypto Backtest

## Naming
- Python files: `snake_case.py`
- Strategy files: `s{number}_{description}.py` (e.g., `s100_dispersion_momentum.py`)
- Test files: `test_*.py`

## Error Handling
- `raise ValueError(...)` for validation errors with descriptive messages
- Defensive try/except around callbacks and batch operations to prevent crash propagation
- try/except with logging for graceful degradation in non-critical paths

## Imports
- Standard library first, third-party second, project modules third
- Absolute imports from `v4.*` modules (e.g., `from v4.config import ...`)
- No relative imports (`from .` or `from ..`)
- NumPy as `np`, Pandas as `pd`

## Testing
- Framework: pytest
- Config: `pytest.ini` (testpaths: `tests v4`)
- Fixtures: `tests/conftest.py` and `v4/tests/conftest.py`
- All code vectorized with NumPy/Pandas — no Python for-loops over bar arrays
