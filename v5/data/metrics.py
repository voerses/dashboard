"""M11 Commit 4 — MetricsManifest + MetricDefinition (Task 4.1-4.3).

Strategy-agnostic, data-driven description of metric streams served by
``ParquetMetricsReplayClient``. A new metric is added by inserting an entry
into the manifest (Python dict or on-disk YAML) — zero engine-code changes.

Design §2/§3 (``.specs/active/m11-unified-event-loop/design.md``):

    @dataclass(frozen=True, slots=True)
    class MetricDefinition:
        metric_id: str               # "binance.open_interest.5m"
        venue: str                   # "BINANCE"
        source_file_pattern: str     # path template with {token} placeholder
        timestamp_col: str           # column to use as ts_event source
        value_col: str               # column whose numeric value is emitted
        native_resolution: str       # "5m"
        resample_to: str             # "1h" (or "" to keep native)
        resample_agg: str            # "last" | "mean" | "max" | "min"
        dtype: str                   # "float64"

    class MetricsManifest:
        def get(metric_id) -> Optional[MetricDefinition]
        def dump_yaml(path) / classmethod load_yaml(path)

    BUILT_IN_MANIFEST: seed manifest with the three binance metrics.

No PyYAML dependency — this module ships a minimal YAML-subset writer/reader
sufficient for the manifest schema (a mapping of metric_id -> flat string
scalars). If PyYAML is present it is preferred; otherwise the built-in
reader/writer round-trips deterministically.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, asdict
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Union


# ----------------------------------------------------------------------
# MetricDefinition
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    """Immutable description of a metric stream.

    Every metric served by ``ParquetMetricsReplayClient`` consults exactly
    one ``MetricDefinition``. Adding a new metric is a manifest entry —
    the client code never changes.

    Field semantics:
      - ``metric_id`` — unique discriminator (e.g. ``"binance.open_interest.5m"``)
      - ``venue`` — string venue tag (matches ``Venue`` enum value)
      - ``source_file_pattern`` — path template with ``{token}`` placeholder,
        resolved against the client's ``fixture_root``
      - ``timestamp_col`` — DataFrame column (or index name) whose values
        become ``ts_event``
      - ``value_col`` — DataFrame column whose values become ``MetricData.value``
      - ``native_resolution`` — resolution label of the parquet (e.g. ``"5m"``)
      - ``resample_to`` — target resolution (e.g. ``"1h"``); empty string
        keeps native resolution
      - ``resample_agg`` — aggregation operator for resampling: ``"last"``,
        ``"mean"``, ``"max"``, or ``"min"``
      - ``dtype`` — numpy dtype string used when casting the value column
    """

    metric_id: str
    venue: str
    source_file_pattern: str
    timestamp_col: str
    value_col: str
    native_resolution: str
    resample_to: str = "1h"
    resample_agg: str = "last"
    dtype: str = "float64"


# ----------------------------------------------------------------------
# MetricsManifest
# ----------------------------------------------------------------------


class MetricsManifest:
    """Immutable lookup of ``MetricDefinition`` keyed by ``metric_id``.

    Constructor accepts either a mapping ``{metric_id: MetricDefinition}``
    or an iterable of ``MetricDefinition`` entries (in which case each
    entry's ``metric_id`` becomes its key).

    Strategy-agnostic: the engine never branches on a particular
    ``metric_id``; consumers look up definitions by ID and act on the
    returned fields.
    """

    __slots__ = ("_definitions",)

    def __init__(
        self,
        definitions: Union[
            Mapping[str, MetricDefinition],
            Iterable[MetricDefinition],
            None,
        ] = None,
    ):
        resolved: Dict[str, MetricDefinition] = {}
        if definitions is None:
            pass
        elif isinstance(definitions, Mapping):
            for k, v in definitions.items():
                if not isinstance(v, MetricDefinition):
                    raise TypeError(
                        f"MetricsManifest values must be MetricDefinition, "
                        f"got {type(v).__name__} for key {k!r}"
                    )
                resolved[k] = v
        else:
            for v in definitions:
                if not isinstance(v, MetricDefinition):
                    raise TypeError(
                        f"MetricsManifest iterable must yield MetricDefinition, "
                        f"got {type(v).__name__}"
                    )
                resolved[v.metric_id] = v
        self._definitions = resolved

    # ------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------

    def get(self, metric_id: str) -> Optional[MetricDefinition]:
        """Return the ``MetricDefinition`` for ``metric_id`` or ``None``."""
        return self._definitions.get(metric_id)

    def __contains__(self, metric_id: str) -> bool:
        return metric_id in self._definitions

    def __len__(self) -> int:
        return len(self._definitions)

    def ids(self) -> Iterable[str]:
        """All known metric IDs (insertion order)."""
        return list(self._definitions.keys())

    def items(self) -> Iterable:
        return self._definitions.items()

    # ------------------------------------------------------------
    # YAML (or YAML-subset) round-trip
    # ------------------------------------------------------------

    def dump_yaml(self, path: Union[str, Path]) -> None:
        """Write the manifest to ``path`` as a YAML document.

        Uses PyYAML if available; otherwise falls back to a minimal
        YAML-subset writer that round-trips with :meth:`load_yaml`.
        """
        path = Path(path)
        payload = {
            mid: {f.name: getattr(mdef, f.name) for f in fields(mdef)}
            for mid, mdef in self._definitions.items()
        }
        try:
            import yaml  # type: ignore
            with path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(
                    payload, f, sort_keys=False, default_flow_style=False,
                )
            return
        except ImportError:
            pass
        # Minimal YAML-subset writer — flat mapping of mappings of strings.
        with path.open("w", encoding="utf-8") as f:
            for mid, entry in payload.items():
                f.write(f"{_yaml_escape_key(mid)}:\n")
                for k, v in entry.items():
                    f.write(f"  {k}: {_yaml_escape_scalar(v)}\n")

    @classmethod
    def load_yaml(cls, path: Union[str, Path]) -> "MetricsManifest":
        """Load a manifest from ``path``.

        Uses PyYAML if available; otherwise falls back to the minimal
        YAML-subset parser that pairs with :meth:`dump_yaml`.
        """
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        try:
            import yaml  # type: ignore
            parsed = yaml.safe_load(text) or {}
        except ImportError:
            parsed = _yaml_subset_parse(text)
        if not isinstance(parsed, dict):
            raise ValueError(
                f"manifest YAML at {path!s} must be a top-level mapping"
            )
        entries: Dict[str, MetricDefinition] = {}
        # Field names and defaults — derived from the dataclass itself so
        # adding/removing fields in MetricDefinition doesn't require
        # updating this loader.
        _field_names = [f.name for f in fields(MetricDefinition)]
        for mid, raw in parsed.items():
            if not isinstance(raw, dict):
                raise ValueError(
                    f"manifest entry {mid!r} must be a mapping, got "
                    f"{type(raw).__name__}"
                )
            # Preserve forward-compat: unknown keys in the file are
            # ignored (not silently moved into the dataclass).
            kwargs = {k: raw[k] for k in _field_names if k in raw}
            # metric_id must match the outer key for consistency.
            if "metric_id" not in kwargs:
                kwargs["metric_id"] = mid
            entries[mid] = MetricDefinition(**kwargs)
        return cls(definitions=entries)


# ----------------------------------------------------------------------
# YAML-subset helpers (used when PyYAML is absent)
# ----------------------------------------------------------------------


def _yaml_escape_key(s: str) -> str:
    """Quote a key if it contains characters that would confuse the
    minimal parser. Our metric IDs are ASCII + dots + underscores; quote
    defensively to keep the parser's job trivial."""
    if any(ch in s for ch in (":", " ", "#", "\n", "\r", "'", '"')):
        return '"' + s.replace('"', '\\"') + '"'
    return s


def _yaml_escape_scalar(v) -> str:
    """Format a scalar for the YAML-subset output.

    All our fields are strings, but we tolerate numeric types for forward
    compat with additional MetricDefinition fields.
    """
    if isinstance(v, str):
        # Quote strings that contain special chars OR that would parse
        # back as a different type (numeric-looking, bool-like, etc.).
        if (
            any(ch in v for ch in (":", "#", "\n", "\r", "'", '"'))
            or v.strip() != v
            or v == ""
        ):
            return '"' + v.replace('"', '\\"') + '"'
        if v.lower() in ("true", "false", "null", "yes", "no", "~"):
            return '"' + v + '"'
        try:
            float(v)
            # Numeric-looking — quote to preserve as string.
            return '"' + v + '"'
        except ValueError:
            return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if v is None:
        return "null"
    raise TypeError(
        f"YAML-subset writer cannot serialize {type(v).__name__}; "
        f"install PyYAML for richer manifest schemas."
    )


def _yaml_subset_parse(text: str) -> Dict[str, Dict[str, str]]:
    """Minimal parser paired with :func:`_yaml_escape_*`.

    Accepts the 2-level `key:\\n  sub: value` indentation layout produced
    by :meth:`MetricsManifest.dump_yaml` when PyYAML is unavailable.
    Quoted values are stripped of their quotes; unquoted values are
    returned verbatim.
    """
    result: Dict[str, Dict[str, str]] = {}
    current_key: Optional[str] = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line.startswith("  "):
            # Sub-entry
            if current_key is None:
                raise ValueError(
                    f"YAML-subset parse error: indented line without parent: "
                    f"{raw_line!r}"
                )
            stripped = line.strip()
            if ":" not in stripped:
                raise ValueError(
                    f"YAML-subset parse error: expected 'key: value': "
                    f"{raw_line!r}"
                )
            k, _, v = stripped.partition(":")
            k = k.strip()
            v = v.strip()
            if (v.startswith('"') and v.endswith('"')) or (
                v.startswith("'") and v.endswith("'")
            ):
                v = v[1:-1].replace('\\"', '"').replace("\\'", "'")
            result[current_key][k] = v
        else:
            # Top-level key
            if ":" not in line:
                raise ValueError(
                    f"YAML-subset parse error: expected 'key:': {raw_line!r}"
                )
            k, _, rest = line.partition(":")
            k = k.strip()
            if (k.startswith('"') and k.endswith('"')) or (
                k.startswith("'") and k.endswith("'")
            ):
                k = k[1:-1].replace('\\"', '"').replace("\\'", "'")
            if rest.strip():
                raise ValueError(
                    f"YAML-subset parse error: top-level keys must introduce a "
                    f"mapping (no inline value): {raw_line!r}"
                )
            current_key = k
            result[k] = {}
    return result


# ----------------------------------------------------------------------
# Built-in seed manifest
# ----------------------------------------------------------------------


# Binance 5-min metric seeds. These entries point at parquet files laid out
# as ``{fixture_root}/binance_metrics/5min/{metric_id}/{token}.parquet``
# with columns ``timestamp`` (ns-ish datetime) and ``value`` (float). That
# layout is what the M11 metrics test fixture writes and what acceptance
# tests exercise; production on-disk parquets at
# ``data/alternative/binance_metrics/5min/{TOKEN}_5min.parquet`` bundle
# all three metrics in a single file under different column names — that
# production layout is served by registering a second manifest variant at
# orchestrator wiring time (Commit 8) that overrides these defaults. The
# client itself is strategy-agnostic; the manifest is the configuration.
_SEED_ENTRIES = (
    MetricDefinition(
        metric_id="binance.open_interest.5m",
        venue="BINANCE",
        source_file_pattern=(
            "binance_metrics/5min/binance.open_interest.5m/{token}.parquet"
        ),
        timestamp_col="timestamp",
        value_col="value",
        native_resolution="5m",
        resample_to="1h",
        resample_agg="last",
        dtype="float64",
    ),
    MetricDefinition(
        metric_id="binance.top_trader_ls.5m",
        venue="BINANCE",
        source_file_pattern=(
            "binance_metrics/5min/binance.top_trader_ls.5m/{token}.parquet"
        ),
        timestamp_col="timestamp",
        value_col="value",
        native_resolution="5m",
        resample_to="1h",
        resample_agg="last",
        dtype="float64",
    ),
    MetricDefinition(
        metric_id="binance.taker_ls_vol.5m",
        venue="BINANCE",
        source_file_pattern=(
            "binance_metrics/5min/binance.taker_ls_vol.5m/{token}.parquet"
        ),
        timestamp_col="timestamp",
        value_col="value",
        native_resolution="5m",
        resample_to="1h",
        resample_agg="last",
        dtype="float64",
    ),
)


BUILT_IN_MANIFEST: MetricsManifest = MetricsManifest(definitions=_SEED_ENTRIES)


__all__ = [
    "MetricDefinition",
    "MetricsManifest",
    "BUILT_IN_MANIFEST",
]
