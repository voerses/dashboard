# Blast Radius — Check Before Changing Shared Code

## EXTREME caution (39+ importers)
- `v4/config.py` — sizing config, overrides, used by nearly everything

## HIGH caution (19-31 importers)
- `v4/simulator.py` — portfolio simulation engine (31 importers)
- `v4/position.py` — position tracking, stop/trail logic (21 importers)
- `v4/paper_config.py` — paper trading configuration (21 importers)
- `v4/signals.py` — signal generation (19 importers)

## Rules
- Adding new features to shared modules is generally safe
- Changing existing public APIs or function signatures is dangerous — check all consumers first
- Before changing any high-impact module, identify all downstream dependents
- Run `pytest` after making changes to shared modules
- v4/ is the active engine — v3/ is FROZEN (legacy reference only)
