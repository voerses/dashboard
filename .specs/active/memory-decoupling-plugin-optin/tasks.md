# Tasks — Memory Decoupling + Plugin Opt-In

- [P] Task 1: Remove `hist_cache.clear()` in `_load_all_contexts` → `v4/portfolio_signals.py` (AC1)
- [P] Task 2: Config-driven `cache_max_rows` → `v4/config.py`, `v4/paper_config.py`, `v4/run_paper_multi.py`, `v4/portfolio_signals.py`, `v4/signals.py` (AC2-AC6, AC21-AC22)
- [ ] Task 3: Activate plugin opt-in + document dependencies → `v4/engine.py`, `v4/portfolio_signals.py`, `v4/signals.py` (AC7-AC13, AC16-AC17, AC23-AC26) (after: 2)
- [ ] Task 4: Share Engine instances across strategies → `v4/signals.py`, `v4/paper_engine.py` (AC18-AC20) (after: 3)
- [ ] Task 5: Declare s501 REQUIRED_PLUGINS → `strategies/s501_r172_v4_portfolio.py` (AC14) (after: 3)
- [ ] Task 6: Full regression → run all tests (AC15) (after: 1, 2, 3, 4, 5)
