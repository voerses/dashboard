"""M6 — Strategy data-declaration Protocol + runner union helper (Task 15).

Forward-compatible scaffold per design §7 Wave E. The Protocol lets strategies
declare their data needs via `required_data() -> list[Subscription]`; the
runner unions declarations across all strategies in a portfolio before handing
them to `DataEngine.subscribe_all(...)`.

AC-D6 AST-scan enforcement (direct time.time() / datetime.now() in strategy
code) is **deferred to M7** per design §4.3. M6 ships the hook only.
"""
from __future__ import annotations

from typing import Iterable, List, Protocol, runtime_checkable

from v5.data.streams import Subscription


@runtime_checkable
class DataDeclaringStrategy(Protocol):
    """Strategies that declare data subscriptions via `required_data()`.

    M7 strategy API redesign formalizes this. For M6, the Protocol lets
    runners union declarations without hard-coupling to any particular
    strategy base class.
    """

    def required_data(self) -> List[Subscription]: ...


def union_subscriptions(strategies: Iterable[object]) -> List[Subscription]:
    """Return the deduplicated union of every strategy's declared subscriptions.

    Dedup is by (DataStream, role) — matches DataEngine.subscribe_all()'s
    dedup key per design §2.8. Strategies not implementing `required_data()`
    contribute zero subscriptions (graceful — not an error).
    """
    seen: dict = {}
    for strat in strategies:
        fn = getattr(strat, "required_data", None)
        if fn is None:
            continue
        try:
            subs = fn()
        except Exception:
            continue
        for s in subs:
            key = (s.stream, s.role)
            if key not in seen:
                seen[key] = s
    return list(seen.values())
