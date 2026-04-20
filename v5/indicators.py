"""M7 — Pull-based memoized IndicatorCache (AC-S3).

Cache key: `(token, fn.__qualname__, frozen_params_tuple, bar_idx)`.

Lambdas/closures rejected at registration (unstable __qualname__ → cache
collisions). Stdlib indicators accept scalar params only (int/float/str/bool).
Custom indicators with non-scalar params MUST implement `.cache_key()` on
the param's type.

Per design §2.3 + §2.8 ID3: stdlib indicators return NEW arrays; no in-place
ops on read-only TokenView arrays.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd


# ============================================================
# Stats + Cache
# ============================================================


@dataclass
class _Stats:
    hits: int = 0
    misses: int = 0
    entries: int = 0


class IndicatorCache:
    """Pull-based memoization with typed param contract."""

    def __init__(self):
        self._cache: dict[tuple, Any] = {}
        self._registered: set[str] = set()
        self.stats = _Stats()

    def register(self, fn: Callable) -> None:
        """Register a named indicator function. Lambdas/closures rejected."""
        qname = getattr(fn, "__qualname__", "")
        if "<lambda>" in qname or "<locals>" in qname:
            raise ValueError(
                f"indicator {qname or fn!r} must be a named function; "
                f"lambda/closure has unstable __qualname__ (cache collisions)"
            )
        self._registered.add(qname)

    def compute(
        self,
        fn: Callable,
        token: str,
        bar_idx: int,
        timeframe: str = "1h",
        **params,
    ) -> Any:
        """Compute or return cached indicator value.

        M9 C-3: `timeframe` is part of the cache key to prevent MTF
        collisions — e.g., `ema(20)` at bar_idx=N on 1h vs 1m data are
        different series and must NOT share a cache slot."""
        qname = fn.__qualname__
        if qname not in self._registered:
            self.register(fn)

        frozen = self._freeze_params(fn, params)
        key = (token, timeframe, qname, frozen, bar_idx)
        if key in self._cache:
            self.stats.hits += 1
            return self._cache[key]

        self.stats.misses += 1
        try:
            result = fn(**params)
        except (TypeError, ValueError) as e:
            # fn-level failure (e.g., param type incompatible with indicator body)
            # is an indicator-impl concern, NOT a cache concern. Cache the None
            # so the same broken call doesn't re-compute; caller sees None.
            result = None
        self._cache[key] = result
        self.stats.entries = len(self._cache)
        return result

    def reset(self) -> None:
        """Clear cache — called on WF fold boundary / on_reset()."""
        self._cache.clear()
        self.stats = _Stats()

    def _freeze_params(self, fn: Callable, params: dict) -> tuple:
        """Hash-safe param snapshot for cache key.

        - Scalar params (int/float/str/bool/None) → pass through
        - Params with `.cache_key()` method → call it + hash-verify
        - numpy arrays / lists / dicts without cache_key → reject
        - The array input `arr` kwarg is special-cased: not in cache key
          (cached via `bar_idx` + `token` instead; array identity is
          implicit)
        """
        frozen = []
        for k in sorted(params.keys()):
            v = params[k]
            # Skip bulk array inputs — cache keyed on (token, bar_idx) already
            if isinstance(v, np.ndarray) and k in ("arr", "array", "data"):
                continue
            if hasattr(v, "cache_key") and callable(v.cache_key):
                try:
                    ck = v.cache_key()
                except Exception as e:
                    raise ValueError(
                        f"indicator param {k}: cache_key() raised {e!r}"
                    )
                try:
                    hash(ck)
                except TypeError:
                    raise TypeError(
                        f"indicator param {k}: cache_key() must return hashable; "
                        f"got {type(ck).__name__}"
                    )
                frozen.append((k, ck))
                continue
            if not isinstance(v, (int, float, str, bool, type(None))):
                raise ValueError(
                    f"indicator param {k}={v!r} is non-scalar (type={type(v).__name__}); "
                    f"implement .cache_key() on the param type"
                )
            frozen.append((k, v))
        return tuple(frozen)


# ============================================================
# Stdlib indicator functions (named, module-level)
# ============================================================


def ema_fn(*, arr: np.ndarray, n: int, col: str = "close") -> float:
    """Exponential moving average at the last bar of `arr`.

    Design §2.8 ID3: returns SCALAR at last bar; no in-place ops.
    """
    if n <= 0:
        raise ValueError(f"ema n must be > 0; got {n}")
    series = pd.Series(arr)
    ema_series = series.ewm(span=n, adjust=False).mean()
    return float(ema_series.iloc[-1])


def sma_fn(*, arr: np.ndarray, n: int, col: str = "close") -> float:
    """Simple moving average scalar at last bar."""
    if n <= 0:
        raise ValueError(f"sma n must be > 0; got {n}")
    tail = arr[-n:]
    return float(np.mean(tail))
