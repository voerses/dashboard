# Strategy Findings Log

Append-only log of gate outcomes and insights from strategy development.

## Format

Each line in `strategy-findings.jsonl` is a JSON object:

```json
{
  "ts": "ISO8601 timestamp",
  "strategy": "strategy name or module",
  "gate": "gate number (0-7) or variant (3P, 5P, 3O, 5O, 5.5)",
  "outcome": "pass | kill | recycle | pass_conditional",
  "metrics": {"key": "value pairs of relevant metrics"},
  "finding": "One-line generalizable insight for future sessions",
  "category": "signal | capability | portfolio | data | infra | process | bug",
  "affects": ["list of knowledge/memory files this finding should update"]
}
```

## Categories

| Category | When to Use |
|----------|-------------|
| `signal` | Insight about a signal type (works/doesn't work, regime-dependent, etc.) |
| `capability` | New module built, new strategy type validated |
| `portfolio` | Portfolio-level finding (correlation, allocation, diversification) |
| `data` | Data gap, quality issue, missing coverage |
| `infra` | Infrastructure limitation, performance issue |
| `process` | Gate threshold was right/wrong, process improvement needed |
| `bug` | Bug found in code, tests, or process |

## Rules

- **Append only** — never edit or delete entries
- **One finding per line** — valid JSONL, one JSON object per line
- **Capture at every gate** — mandatory in gate report debrief section
- **Curate periodically** — every ~10 strategy runs, promote insights to knowledge files
- **Archive when >500 entries** — move oldest entries to `findings/archive/`
