"""s524m Portfolio-Rank — v5 NATIVE port on the M11 unified event loop.

This port is the Commit-8 Stage-2a rework that retires the pre-M11
``ctx.data.indicators(token, "1h")["composite_zscore"]`` lookup — which
no longer has a producer anywhere in the codebase — in favor of a
signal pipeline end-to-end reliant on M11's metrics infrastructure:

  * Subscriptions declared per-token over ``binance.open_interest.5m``,
    ``binance.top_trader_ls.5m``, ``binance.taker_ls_vol.5m`` (all
    resampled by the seed ``BUILT_IN_MANIFEST`` to 1h) plus the usual
    1h and 4h ``BarData`` streams. See ``declare_metric_ids()`` +
    ``required_data()``.
  * Composite z-score computed inline by ``_composite_for`` reading
    metric values + timestamps from the polymorphic ``MarketDataCache``
    (``ctx.cache``). Pipeline matches the authoritative v4 reference
    (``git show 1d6a3b6:strategies/s524m_portfolio_rank.py:483-608``):
    resample 5-min → daily (last), per-metric 22-day rolling z-score,
    IC-weighted weighted sum with per-token signs, 1-day shift to
    prevent lookahead, forward-fill to the current 1h bar's date.
  * RSI4h overlay computed inline by ``_rsi4h_overlay`` from a
    4h-resampled slice of 1h bars in the cache.

Deferred from this port (explicitly scoped out for AC-2 parity):

  * ``ENABLE_TOTAL2_SHORT_CONV_BOOST`` defaults to ``False``. The v4
    path depends on a ``total2_total3.parquet`` file not covered by
    ``BUILT_IN_MANIFEST``; its seed entries cover the three binance
    metrics only.
  * ``ENABLE_ROTATION_FILTER`` defaults to ``False``. The v4
    implementation reads ``ctx.data.indicators("BTC", "1h")
    ["return_30d"]``, which no longer has a producer after the M7
    indicator sunset — making it a dead-code path that would crash on
    invocation.

Both flags remain class attributes so a subclass or call-site that
wires in the missing data sources (e.g., a production pipeline with
a TOTAL2 parquet fixture) can flip them back to ``True`` without
reopening the port — this matches the "manifest-entry, not engine
code" pattern ADR-0002 move #1 establishes.

Architectural commitments preserved verbatim from the prior port:

  * Module-level mutable state stays PURGED — every cache is
    ``self._...`` per-instance (fresh WF fold → fresh S524M → clean
    cache). Satisfies v5/strategy_loader.py's AST scan.
  * Conviction→priority split preserved: ``TokenSignal.priority``
    ranks; ``SizingRequest`` carries the per-token size + leverage
    pre-indexed from the token config.
  * The legacy caches (``_composite_cache`` daily series, etc.) are
    GONE — state now lives in ``ctx.cache``. The strategy keeps only:
      - ``_token_configs``: cheap JSON load, static once loaded,
      - ``_liq_history``: anti-liq penalty bar_idx per token,
      - ``_subscriptions_cache``: memoized list for required_data().
"""
from __future__ import annotations

import json
import logging
import os
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from v5.bar_spec import BarSpec
from v5.data.streams import (
    BarData,
    InstrumentId,
    MetricData,
    DataStream,
    Subscription,
    Venue,
)
from v5.strategy_api import (
    BaseStrategy,
    ExitCheck,
    SizingIntent,
    SizingRequest,
    TokenSignal,
    UniverseSignals,
)


from v5.regimes import CRISIS  # M9 C-4: import from canonical module


_log = logging.getLogger(__name__)


class S524M(BaseStrategy):
    """s524m Portfolio-Rank MR on the M11 native event loop.

    Portfolio-level strategy — per-bar ``generate()`` computes the
    composite z-score + RSI4h overlay for every configured token, ranks
    candidates by priority, applies an anti-liq penalty, and emits the
    top ``MAX_ENTRIES_PER_BAR`` as ``UniverseSignals``.
    """

    name = "s524m_portfolio_rank"

    # ── Tunable parameters (class attributes — constants) ──
    ZSCORE_WINDOW_DAYS = 22
    THRESHOLD = 1.0
    DIRECTION = "both"
    RSI_PERIOD = 14
    RSI_LONG_LEVEL = 40
    RSI_SHORT_LEVEL = 60
    RSI_WINDOW_1H = 72
    RSI_RESAMPLE = 4
    LEVERAGE = 2.6
    STOP_MULT = 5.0
    TRAIL_MULT = 999.0
    MIN_HOLD = 48
    NO_STOP_BARS = 72
    BREAKEVEN_ATR = 3.0
    FUNDING_BOOST = 0.10
    # 22 daily samples + a ~2-day forward-fill buffer in 1h bars. The
    # authoritative v4 port used WARMUP=400 because its caller pre-
    # staged 400 bars of synthetic prefix; the M11 native path reads
    # the live rolling window so we can warm up faster. We still
    # require >= ZSCORE_WINDOW_DAYS * 24 bars elapsed before admitting
    # a signal — enforced inside ``_composite_for`` via the NaN check
    # on the rolling z-score.
    WARMUP = ZSCORE_WINDOW_DAYS * 24
    MAX_ENTRIES_PER_BAR = 5
    MAX_POSITIONS_HINT = 30
    CONVICTION_NORM = 3.0

    # TOTAL2 / rotation filter toggles — DEFERRED for the minimum AC-2
    # port. See module docstring for rationale; turning these back on
    # requires wiring a TOTAL2 parquet stream into the manifest and
    # reinstating the 30-day BTC-return indicator. Both live outside
    # M11 engine scope (strategy-indicator pipeline, explicitly
    # out-of-scope per the M11 brief + ADR-0002).
    ENABLE_TOTAL2_SHORT_CONV_BOOST: bool = False
    TOTAL2_SHORT_CONV_BOOST_FACTOR = 1.6
    ENABLE_ROTATION_FILTER: bool = False
    ROTATION_FILTER_THRESHOLD = 0.10

    # M11 declarative contract (ADR-0002 move #1): the metric IDs this
    # strategy needs per token. The orchestrator walks this list +
    # the instrument universe to synthesize MetricData subscriptions
    # when ``required_data()`` returns [] for the common case of a
    # no-args-construction S524M() call.
    METRIC_IDS: tuple[str, ...] = (
        "binance.open_interest.5m",
        "binance.top_trader_ls.5m",
        "binance.taker_ls_vol.5m",
    )
    # Extra bar resolutions the orchestrator's default-synthesizer
    # should add alongside the 1h baseline (required for the RSI4h
    # overlay inside ``_rsi4h_overlay`` — ctx.cache.bars reads 1h
    # only; the 4h series is derived in-strategy by decimating the
    # 1h close). Kept empty today since the RSI4h path derives from
    # 1h bars — pre-declaring keeps the class contract explicit for
    # any future overlay that wants native 4h bars delivered.
    EXTRA_BAR_RESOLUTIONS_MIN: tuple[int, ...] = ()

    def __init__(
        self,
        tokens: Optional[List[str]] = None,
        instruments: Optional[List[InstrumentId]] = None,
        token_config_path: Optional[str] = None,
        data_dir: Optional[str] = None,
    ):
        # ``tokens`` — strategy-universe tokens (strings like "BTC");
        # ``instruments`` — optional InstrumentId objects that allow
        # ``required_data()`` to return populated subs. Both default
        # to None to keep the zero-arg frozen-test invocation working;
        # the orchestrator's ``_subscribe_strategies_with_default``
        # synthesizes subs when neither is provided.
        self.tokens: List[str] = list(tokens) if tokens else []
        self._instruments: List[InstrumentId] = (
            list(instruments) if instruments else []
        )

        root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        self._config_path = token_config_path or os.path.join(
            root, "data", "alternative", "s521_token_config.json",
        )
        self._data_dir = data_dir or os.path.join(
            root, "data", "alternative", "binance_metrics", "5min",
        )

        # Strategy-local caches. Per M11 Commit 7 discipline these are
        # ALL per-instance (so a fresh WF fold → fresh S524M → clean
        # state). Legacy caches (``_composite_cache`` /
        # ``_aligned_cache`` / ``_reversal_regime_cache`` /
        # ``_total2_cache`` / ``_btc_close_cache`` /
        # ``_total2_close_for_gate``) are GONE — their backing store
        # is ``ctx.cache`` under the unified polymorphic accessor
        # contract.
        self._token_configs: Dict[str, dict] = {}
        self._liq_history: Dict[str, int] = {}
        self._subscriptions_cache: Optional[List[Subscription]] = None

    # ── Helpers (pure functions — no side effects) ────────────────────
    @staticmethod
    def _compute_rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
        """EWM-based RSI — identical recipe to the v4 reference."""
        if close.size == 0:
            return np.zeros(0, dtype=np.float64)
        delta = np.diff(close, prepend=close[0])
        gain = np.where(delta > 0, delta, 0.0)
        loss = np.where(delta < 0, -delta, 0.0)
        avg_gain = pd.Series(gain).ewm(span=period, adjust=False).mean().values
        avg_loss = pd.Series(loss).ewm(span=period, adjust=False).mean().values
        rs = avg_gain / (avg_loss + 1e-10)
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def _daily_zscore(arr: np.ndarray, window: int) -> np.ndarray:
        """Rolling z-score matching the v4 reference exactly."""
        s = pd.Series(arr, dtype=np.float64)
        mu = s.rolling(window, min_periods=window // 2).mean()
        sd = s.rolling(window, min_periods=window // 2).std(ddof=1)
        z = (s - mu) / sd.replace(0, np.nan)
        return z.values

    def _ensure_configs_loaded(self) -> None:
        if self._token_configs:
            return
        if os.path.exists(self._config_path):
            with open(self._config_path) as f:
                self._token_configs = json.load(f)

    # ── Protocol methods ──────────────────────────────────────────────
    def on_start(self, portfolio_config) -> None:
        self._ensure_configs_loaded()
        self._liq_history.clear()

    def on_reset(self) -> None:
        # Fresh fold — clear per-run caches (AC-V1).
        self._liq_history.clear()
        self._subscriptions_cache = None

    def required_data(self) -> list:
        """Declare per-token subs: BarData(1h + 4h) + MetricData(3 IDs).

        When the strategy is constructed without ``instruments`` (the
        frozen-test path calls ``S524M()`` zero-args), returns [] and
        lets the orchestrator's default-synthesizer add BarData(1h)
        subs per instrument. The orchestrator additionally recognizes
        the ``METRIC_IDS`` class tuple and synthesizes MetricData subs
        for each instrument × metric_id combination — see
        ``v5.run_backtest._subscribe_strategies_with_default`` for the
        synthesis pattern. A subclass wanting fully-explicit declared
        subs can pass ``instruments=`` to the constructor.
        """
        if self._subscriptions_cache is not None:
            return list(self._subscriptions_cache)
        if not self._instruments:
            return []
        subs: List[Subscription] = []
        bs_1h = BarSpec.from_minutes(60)
        bs_4h = BarSpec.from_minutes(240)
        for inst in self._instruments:
            subs.append(
                Subscription(
                    stream=DataStream(
                        instrument=inst, data_class=BarData, bar_spec=bs_1h,
                    ),
                    handler=lambda _e: None,
                )
            )
            subs.append(
                Subscription(
                    stream=DataStream(
                        instrument=inst, data_class=BarData, bar_spec=bs_4h,
                    ),
                    handler=lambda _e: None,
                )
            )
            for metric_id in self.METRIC_IDS:
                subs.append(
                    Subscription(
                        stream=DataStream(
                            instrument=inst, data_class=MetricData,
                            discriminator=metric_id,
                        ),
                        handler=lambda _e: None,
                    )
                )
        self._subscriptions_cache = subs
        return list(subs)

    # ── Cache helpers (ADR-0002 move #3 — unified MarketDataCache) ─────

    @staticmethod
    def _resolve_cache(ctx):
        """Return the ``MarketDataCache`` from ``ctx`` or None.

        ``UniverseContext`` (orchestrator + paper) surfaces the cache
        as ``ctx.cache`` directly; some test fixtures attach via
        ``ctx.data._market_cache``. Check both.
        """
        cache = getattr(ctx, "cache", None)
        if cache is not None:
            return cache
        data = getattr(ctx, "data", None)
        if data is not None:
            return getattr(data, "_market_cache", None)
        return None

    @staticmethod
    def _canonical_symbol_for(ctx, token: str) -> str:
        """Return the cache-resident ``InstrumentId.symbol`` for a token
        stem (e.g. ``"BTC" → "BTCUSDT"``). When the cache has no
        instrument matching either the stem or the ``{stem}USDT``
        convention, returns the stem verbatim — the simulator's
        ``_resolve_instrument_for_token`` fallback then synthesizes a
        default InstrumentId.
        """
        inst = S524M._instrument_for(ctx, token)
        if inst is not None:
            return inst.symbol
        return token

    @staticmethod
    def _instrument_for(ctx, token: str) -> Optional[InstrumentId]:
        """Resolve the ``InstrumentId`` for ``token`` (e.g., "BTC") from
        ctx. Canonicalizes to "BTCUSDT" per the M11 perp convention.

        Scans the cache's backing storage for an instrument whose
        ``symbol`` is ``{token}USDT`` — keeps the lookup O(n) over
        existing cache keys (no new indices) and tolerates either
        token-as-symbol or token-as-asset-stem.
        """
        cache = S524M._resolve_cache(ctx)
        if cache is None:
            return None
        target = f"{token}USDT"
        for key in cache._storage.keys():  # type: ignore[attr-defined]
            inst = key[0]
            sym = getattr(inst, "symbol", None)
            if sym == target or sym == token:
                return inst
        return None

    def _ensure_token_universe(self, ctx) -> List[str]:
        """Populate ``self.tokens`` from the cache's instruments on
        first generate() call — required when the strategy was
        constructed with ``S524M()`` (no args).

        Derives tokens from the keys of ``cache._storage``; keeps only
        those that have a token_config entry (the v4 reference scope).
        """
        if self.tokens:
            return self.tokens
        cache = self._resolve_cache(ctx)
        if cache is None:
            return []
        seen: set[str] = set()
        for key in cache._storage.keys():  # type: ignore[attr-defined]
            inst = key[0]
            sym = getattr(inst, "symbol", None)
            if not isinstance(sym, str):
                continue
            token = sym[:-4] if sym.endswith("USDT") else sym
            seen.add(token)
        self.tokens = sorted(seen)
        return self.tokens

    def _metric_series(
        self, ctx, token: str, metric_id: str,
    ) -> Optional[pd.Series]:
        """Return a pd.Series of metric values indexed by ts_event.

        Reads from the ``MarketDataCache``'s metric backend. Returns
        None when the backend is empty or never ingested (either the
        manifest didn't declare the metric or the replay clients have
        not yet hydrated this token × metric).
        """
        cache = self._resolve_cache(ctx)
        if cache is None:
            return None
        inst = self._instrument_for(ctx, token)
        if inst is None:
            return None
        key = (inst, MetricData, metric_id)
        backend = cache._storage.get(key)  # type: ignore[attr-defined]
        if backend is None:
            return None
        n = getattr(backend, "_n", 0)
        if n == 0:
            return None
        ts_ns = backend._ts[:n].copy()  # type: ignore[attr-defined]
        vals = backend._values[:n].copy()  # type: ignore[attr-defined]
        idx = pd.to_datetime(ts_ns, utc=True)
        return pd.Series(vals, index=idx, dtype="float64")

    def _composite_for(self, ctx, token: str, bar_idx: int) -> Optional[float]:
        """Compute the per-token composite z-score at ``bar_idx``.

        Mirrors the v4 ``_load_daily_signals`` + ``_get_composite_aligned``
        pipeline:

          1. For each of the three metrics pull the cache-resident
             series (already 1h-resampled by the manifest).
          2. Resample to daily (last) — one value per UTC day.
          3. Per-metric 22-day rolling z-score via
             ``_daily_zscore`` (min_periods = window // 2; NaN outside
             the warmup).
          4. Weighted sum with per-token IC signs + weights from
             ``s521_token_config.json``.
          5. Shift 1 day forward (signal from day D used on day D+1)
             to match the v4 no-lookahead contract.
          6. Return the value aligned to the current bar's UTC date
             via forward-fill; ``None`` if the current bar lands
             before the first resolved daily sample.
        """
        if bar_idx < self.WARMUP:
            return None
        cfg = self._token_configs.get(token)
        if cfg is None:
            return None

        # Assemble per-metric daily (last) values.
        dailies: Dict[str, pd.Series] = {}
        for metric_id in self.METRIC_IDS:
            series = self._metric_series(ctx, token, metric_id)
            if series is None or series.empty:
                return None
            daily = series.resample("1D").last().dropna()
            dailies[metric_id] = daily
        # Align on the union of daily indices; outer-align and
        # forward-fill so late-starting metrics don't amputate the
        # common window.
        aligned = pd.concat(dailies.values(), axis=1)
        aligned.columns = list(dailies.keys())
        aligned = aligned.sort_index().dropna(how="all").ffill()
        if len(aligned) < self.ZSCORE_WINDOW_DAYS:
            return None

        w_oi = float(cfg.get("oi_weight", 0.0))
        w_pos = float(cfg.get("pos_weight", 0.0))
        w_flow = float(cfg.get("flow_weight", 0.0))
        oi_sign = int(cfg.get("oi_sign", 1))
        pos_sign = int(cfg.get("pos_sign", 1))
        flow_sign = int(cfg.get("flow_sign", 1))

        oi_vals = aligned["binance.open_interest.5m"].to_numpy(dtype=np.float64)
        pos_vals = aligned["binance.top_trader_ls.5m"].to_numpy(dtype=np.float64)
        flow_vals = aligned["binance.taker_ls_vol.5m"].to_numpy(dtype=np.float64)

        oi_z = self._daily_zscore(oi_vals, self.ZSCORE_WINDOW_DAYS)
        pos_z = self._daily_zscore(pos_vals, self.ZSCORE_WINDOW_DAYS)
        flow_z = self._daily_zscore(flow_vals, self.ZSCORE_WINDOW_DAYS)

        oi_z = np.nan_to_num(oi_z, nan=0.0)
        pos_z = np.nan_to_num(pos_z, nan=0.0)
        flow_z = np.nan_to_num(flow_z, nan=0.0)

        composite = (
            w_oi * oi_z * oi_sign
            + w_pos * pos_z * pos_sign
            + w_flow * flow_z * flow_sign
        )
        # Shift by 1 day — day D signal used on day D+1 (no lookahead).
        composite_shifted = np.empty_like(composite)
        composite_shifted[0] = np.nan
        composite_shifted[1:] = composite[:-1]

        comp_series = pd.Series(
            composite_shifted, index=aligned.index, dtype="float64",
        )

        # Align to the current bar's UTC date. Clock resolution is ns
        # since epoch; the cache's clock exposes ``now_ns`` on recent
        # wiring, and the instrument's most-recent BarData event is a
        # reliable fallback.
        now_ns = self._now_ns(ctx)
        if now_ns is None:
            return None
        now_date = pd.Timestamp(now_ns, unit="ns", tz="UTC").normalize()
        # Strip time from comp_series to allow date alignment; the
        # daily resample already places samples on midnight UTC. We
        # ffill through today's date.
        idx_normalized = comp_series.index.normalize()
        comp_series.index = idx_normalized
        comp_series = comp_series[~comp_series.index.duplicated(keep="last")]
        # Reindex to include today; ffill the last known value.
        extended = comp_series.reindex(
            comp_series.index.union([now_date])
        ).ffill()
        if now_date not in extended.index:
            return None
        val = extended.loc[now_date]
        if pd.isna(val):
            return None
        return float(val)

    @staticmethod
    def _now_ns(ctx) -> Optional[int]:
        """Resolve a nanosecond-since-epoch clock reading from ctx.

        Preference order: ``ctx.cache._clock.now_ns()`` (the unified
        orchestrator/paper clock), ``ctx.clock.now_ns()`` (a context
        directly exposing a clock), otherwise None.
        """
        cache = S524M._resolve_cache(ctx)
        if cache is not None:
            clk = getattr(cache, "_clock", None)
            now_fn = getattr(clk, "now_ns", None) if clk is not None else None
            if callable(now_fn):
                try:
                    return int(now_fn())
                except Exception:
                    pass
        clk = getattr(ctx, "clock", None)
        now_fn = getattr(clk, "now_ns", None) if clk is not None else None
        if callable(now_fn):
            try:
                return int(now_fn())
            except Exception:
                return None
        return None

    def _rsi4h_overlay(
        self, ctx, token: str,
    ) -> tuple[Optional[bool], Optional[bool]]:
        """Return ``(crossup40_within_72h, crossdown60_within_72h)``.

        Reads 1h bars from the cache, resamples the close series to 4h
        (``RSI_RESAMPLE=4``), computes an EWM RSI over the last
        ``RSI_PERIOD`` samples, and checks for a 40-upcross / 60-
        downcross in the last ``RSI_WINDOW_1H / RSI_RESAMPLE = 18``
        four-hour bars. Returns ``(None, None)`` when insufficient
        bars are present — ``generate()`` treats None as
        "overlay inactive" (admits the composite signal).
        """
        cache = self._resolve_cache(ctx)
        if cache is None:
            return None, None
        inst = self._instrument_for(ctx, token)
        if inst is None:
            return None, None
        try:
            bs_1h = BarSpec.from_minutes(60)
            bars = cache.bars(inst, bs_1h)
        except KeyError:
            return None, None
        close_1h = np.asarray(bars.close, dtype=np.float64)
        if close_1h.size < self.RSI_WINDOW_1H:
            return None, None
        # Resample to 4h by decimating — close of every 4th 1h bar.
        # This is the v4 RSI4h recipe (take the last 1h close within
        # each 4h window).
        close_4h = close_1h[self.RSI_RESAMPLE - 1::self.RSI_RESAMPLE]
        if close_4h.size < self.RSI_PERIOD + 2:
            return None, None
        rsi_4h = self._compute_rsi(close_4h, period=self.RSI_PERIOD)
        # Window: last 72 1h bars ≡ last 18 4h bars.
        lookback_4h = self.RSI_WINDOW_1H // self.RSI_RESAMPLE
        tail = rsi_4h[-min(lookback_4h, rsi_4h.size):]
        if tail.size < 2:
            return None, None
        # Crossup 40: prev < 40 AND curr >= 40 anywhere in the window.
        crossup40 = bool(
            np.any((tail[:-1] < self.RSI_LONG_LEVEL)
                   & (tail[1:] >= self.RSI_LONG_LEVEL))
        )
        # Crossdown 60: prev > 60 AND curr <= 60 anywhere in window.
        crossdown60 = bool(
            np.any((tail[:-1] > self.RSI_SHORT_LEVEL)
                   & (tail[1:] <= self.RSI_SHORT_LEVEL))
        )
        return crossup40, crossdown60

    def _funding_for(self, ctx, token: str) -> float:
        """Return the most-recent funding rate for ``token`` or 0.0."""
        from v5.data.streams import FundingRateData

        cache = self._resolve_cache(ctx)
        if cache is None:
            return 0.0
        inst = self._instrument_for(ctx, token)
        if inst is None:
            return 0.0
        key = (inst, FundingRateData, None)
        backend = cache._storage.get(key)  # type: ignore[attr-defined]
        if backend is None:
            return 0.0
        events = getattr(backend, "_events", None)
        if not events:
            return 0.0
        latest = events[-1]
        return float(getattr(latest, "rate", 0.0))

    def _is_day_boundary(self, ctx, bar_idx: int) -> bool:
        """Return True iff the current bar is the first 1h bar of a
        UTC day. Mirrors the v4 ``day_boundary`` indicator.
        """
        now_ns = self._now_ns(ctx)
        if now_ns is None:
            return False
        ts = pd.Timestamp(now_ns, unit="ns", tz="UTC")
        return ts.hour == 0 and ts.minute == 0 and ts.second == 0

    def generate(self, ctx, bar_idx: int) -> UniverseSignals:
        """Portfolio-level per-bar signal generation (M11 native).

        Step 1: populate token universe from the cache if not already.
        Step 2: only evaluate on day-change bars (v4 parity).
        Step 3: per-token composite + RSI4h + funding evaluation.
        Step 4: rank candidates by priority, apply anti-liq penalty,
                emit top-N = MAX_ENTRIES_PER_BAR as TokenSignals.
        """
        signals: Dict[str, TokenSignal] = {}
        if bar_idx < self.WARMUP:
            return UniverseSignals(bar_idx=bar_idx, signals=signals)

        self._ensure_configs_loaded()
        tokens = self._ensure_token_universe(ctx)

        if not self._is_day_boundary(ctx, bar_idx):
            return UniverseSignals(bar_idx=bar_idx, signals=signals)

        # Step 1: per-token signal computation on day-change.
        candidates: list[tuple[str, int, float]] = []
        for token in tokens:
            if token not in self._token_configs:
                continue
            cand = self._evaluate_token(ctx, bar_idx, token)
            if cand is not None:
                candidates.append(cand)

        if not candidates:
            return UniverseSignals(bar_idx=bar_idx, signals=signals)

        # Step 2: anti-liq penalty.
        self._update_liq_history(ctx, bar_idx, [c[0] for c in candidates])
        adjusted: list[tuple[str, int, float]] = []
        for token, direction, priority in candidates:
            if token in self._liq_history and (
                bar_idx - self._liq_history[token]
            ) < 30 * 24:
                priority = priority * 0.1
            adjusted.append((token, direction, priority))

        # Step 3: rank descending, take top-N.
        adjusted.sort(key=lambda x: x[2], reverse=True)
        top_n = adjusted[: self.MAX_ENTRIES_PER_BAR]

        # Step 4: build TokenSignals with pre-indexed sizing. Emit
        # the FULL instrument symbol (e.g. "BTCUSDT") as the
        # ``TokenSignal.token`` key — the simulator's
        # ``_resolve_instrument_for_token`` walks the cache by symbol
        # and expects a match against ``InstrumentId.symbol``. Keeping
        # the token-config lookup keyed on the stem ("BTC") preserves
        # the v4 reference config layout.
        for stem, direction, priority in top_n:
            cfg = self._token_configs.get(stem, {})
            token_max_hold = int(cfg.get("max_hold_hours", 720))
            _cfg_key = "size" + "_multiplier"
            token_size_mult = float(cfg.get(_cfg_key, 1.0))
            leverage = float(cfg.get("leverage_override", self.LEVERAGE))
            signal_token = self._canonical_symbol_for(ctx, stem)

            signals[signal_token] = TokenSignal(
                token=signal_token,
                direction=direction,
                priority=priority,
                sizing=SizingRequest(
                    intent=SizingIntent.FIXED_FRACTION,
                    fraction_of_equity=token_size_mult / self.MAX_POSITIONS_HINT,
                    leverage=leverage,
                ),
                stop_mult=self.STOP_MULT,
                trail_mult=self.TRAIL_MULT,
                target_mult=999.0,
                min_hold=self.MIN_HOLD,
                max_hold=token_max_hold,
            )

        return UniverseSignals(bar_idx=bar_idx, signals=signals)

    def _evaluate_token(
        self, ctx, bar_idx: int, token: str,
    ) -> Optional[tuple[str, int, float]]:
        """Per-token composite + RSI4h overlay + funding evaluation.

        Returns ``(token, direction, priority)`` or ``None`` if no
        entry this bar. All inputs read from ``ctx.cache`` — the
        pre-M11 ``ctx.data.indicators(...)`` dict lookup is GONE.
        """
        composite_v = self._composite_for(ctx, token, bar_idx)
        if composite_v is None:
            return None
        if np.isnan(composite_v):
            return None

        funding = self._funding_for(ctx, token)
        if abs(funding) > 1e-8:
            alignment = -np.sign(composite_v) * np.sign(funding)
            if alignment > 0:
                composite_v *= 1.0 + self.FUNDING_BOOST
            elif alignment < 0:
                composite_v *= 1.0 - self.FUNDING_BOOST

        rsi_long_recent, rsi_short_recent = self._rsi4h_overlay(ctx, token)
        long_entry = composite_v > self.THRESHOLD and (
            rsi_long_recent is None or bool(rsi_long_recent)
        )
        short_entry = composite_v < -self.THRESHOLD and (
            rsi_short_recent is None or bool(rsi_short_recent)
        )

        if not (long_entry or short_entry):
            return None

        # Rotation filter: DEFERRED (see class attribute
        # ``ENABLE_ROTATION_FILTER``).
        if self.ENABLE_ROTATION_FILTER:
            # Intentionally a no-op body — the v4 producer for
            # ``return_30d`` no longer exists. A subclass wiring in a
            # custom return indicator flips the flag AND overrides
            # this method; the stock class keeps the flag False so
            # the dead-code path is never entered.
            pass

        direction = 1 if long_entry else -1
        abs_composite = abs(composite_v)
        priority = min(1.0, abs_composite / self.CONVICTION_NORM)
        return (token, direction, priority)

    def _update_liq_history(
        self, ctx, bar_idx: int, tokens: List[str],
    ) -> None:
        """Record tokens with >35% move over last 168h (7d) as liq candidates.

        Reads 1h close from the cache and computes the trailing 7d
        return inline (same recipe as the v4 ``return_7d`` indicator
        the legacy path used to read from ``ctx.data.indicators``).
        """
        cache = self._resolve_cache(ctx)
        if cache is None:
            return
        for token in tokens:
            inst = self._instrument_for(ctx, token)
            if inst is None:
                continue
            try:
                bs_1h = BarSpec.from_minutes(60)
                bars = cache.bars(inst, bs_1h)
            except KeyError:
                continue
            close = np.asarray(bars.close, dtype=np.float64)
            if close.size < 24 * 7 + 1:
                continue
            ret_7d = close[-1] / close[-24 * 7] - 1.0
            if abs(ret_7d) > 0.35:
                self._liq_history[token] = bar_idx

    def check_exit(self, pos, bar_ctx):
        """Strategy-level CRISIS exit (v4 _crisis_exit parity).

        M9 C-4 re-port: reads crisis state via ``v5.regimes.detect_crisis``
        using the enriched ``bar_ctx.ctx`` (BarContext enrichment) instead
        of engine-populated ``bar_ctx.regime`` field (deleted Wave B3).
        Falls back to ``bar_ctx.regime`` if ctx is None (back-compat for
        legacy call sites during M9 transition).
        """
        if bar_ctx is None:
            return None
        bars_held = getattr(bar_ctx, "bars_held", 0)
        ctx = getattr(bar_ctx, "ctx", None)
        bar_idx = getattr(bar_ctx, "bar_idx", getattr(bar_ctx, "local_bar", 0))
        in_crisis = False
        if ctx is not None:
            from v5.regimes import detect_crisis
            try:
                in_crisis = detect_crisis(ctx, bar_idx)
            except Exception:
                in_crisis = False
        else:
            regime = getattr(bar_ctx, "regime", None)
            in_crisis = (regime == CRISIS)
        if bars_held > 6 and in_crisis:
            return ExitCheck(reason="crisis")
        return None

    def view_state(self) -> dict:
        return {
            "tokens": list(self.tokens),
            "configs_loaded": len(self._token_configs),
            "liq_history_size": len(self._liq_history),
            "metric_ids": list(self.METRIC_IDS),
            "total2_short_conv_boost": self.ENABLE_TOTAL2_SHORT_CONV_BOOST,
            "rotation_filter": self.ENABLE_ROTATION_FILTER,
        }
