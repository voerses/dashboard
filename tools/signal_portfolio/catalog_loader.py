"""Load per-token signal catalogs and analysis data into structured objects."""

import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import SignalPortfolioConfig


@dataclass
class SignalInfo:
    """A single signal's metadata from discovery outputs."""
    feature: str
    horizon: int
    mean_ic: float
    t_stat: float
    per_regime_ic: Dict[str, float]
    split_ics: List[float]

    # From rolling IC analysis
    ic_status: str = 'UNKNOWN'  # STABLE, STRENGTHENING, DECAYING, DEAD

    # From lead/lag analysis
    direction: str = 'UNKNOWN'  # LEADING, SYMMETRIC, LAGGING
    confidence: str = 'UNKNOWN'  # HIGH, MEDIUM, LOW

    # From IC decay curves
    peak_horizon: Optional[int] = None
    half_life_horizon: Optional[int] = None


@dataclass
class TokenSignalProfile:
    """All signal information for a single token."""
    token: str
    signals: List[SignalInfo] = field(default_factory=list)
    filtered_signals: List[SignalInfo] = field(default_factory=list)
    ic_vector: Optional[np.ndarray] = None


def load_signal_catalog(path: str) -> List[dict]:
    """Load signal catalog JSON, return list of signal dicts."""
    with open(path) as f:
        data = json.load(f)
    return data.get('signals', [])


def load_rolling_ic_summary(path: str) -> Dict[Tuple[str, int], str]:
    """Load rolling IC summary CSV, return {(feature, horizon): status}."""
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path)
    result = {}
    for _, row in df.iterrows():
        result[(row['feature'], int(row['horizon']))] = row['status']
    return result


def load_lead_lag(path: str) -> Dict[Tuple[str, int], Tuple[str, str]]:
    """Load lead/lag JSON, return {(feature, horizon): (direction, confidence)}."""
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        data = json.load(f)
    result = {}
    for item in data:
        key = (item['feature'], int(item['horizon']))
        result[key] = (item['direction'], item.get('confidence', 'UNKNOWN'))
    return result


def load_ic_decay(path: str) -> Dict[str, dict]:
    """Load IC decay curves JSON, return {feature: decay_info}."""
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        data = json.load(f)
    result = {}
    for item in data:
        result[item['feature']] = {
            'peak_horizon': item.get('peak_horizon'),
            'half_life_horizon': item.get('half_life_horizon'),
        }
    return result


def load_token_profile(token: str, signal_dir: str) -> Optional[TokenSignalProfile]:
    """Load all signal data for a single token."""
    catalog_path = os.path.join(signal_dir, f'signal_catalog_{token}.json')
    if not os.path.exists(catalog_path):
        return None

    # Load catalog
    raw_signals = load_signal_catalog(catalog_path)
    if not raw_signals:
        return None

    # Load supplementary data
    rolling_path = os.path.join(signal_dir, f'rolling_ic_summary_{token}.csv')
    lead_lag_path = os.path.join(signal_dir, f'lead_lag_{token}.json')
    decay_path = os.path.join(signal_dir, f'ic_decay_curves_{token}.json')

    rolling_status = load_rolling_ic_summary(rolling_path)
    lead_lag_info = load_lead_lag(lead_lag_path)
    decay_info = load_ic_decay(decay_path)

    # Build SignalInfo objects
    signals = []
    for s in raw_signals:
        if not s.get('significant', False):
            continue

        feat = s['feature']
        horizon = int(s['horizon'])
        key = (feat, horizon)

        info = SignalInfo(
            feature=feat,
            horizon=horizon,
            mean_ic=s['mean_ic'],
            t_stat=s.get('t_stat', 0.0),
            per_regime_ic=s.get('per_regime_ic', {}),
            split_ics=s.get('split_ics', []),
        )

        # Enrich with rolling IC status
        if key in rolling_status:
            info.ic_status = rolling_status[key]

        # Enrich with lead/lag
        if key in lead_lag_info:
            info.direction, info.confidence = lead_lag_info[key]

        # Enrich with decay
        if feat in decay_info:
            info.peak_horizon = decay_info[feat].get('peak_horizon')
            info.half_life_horizon = decay_info[feat].get('half_life_horizon')

        signals.append(info)

    return TokenSignalProfile(token=token, signals=signals)


def filter_signals(profile: TokenSignalProfile, min_abs_ic: float = 0.02) -> List[SignalInfo]:
    """Filter to LEADING + (STABLE or STRENGTHENING) signals with sufficient IC."""
    filtered = []
    for s in profile.signals:
        # Must be LEADING
        if s.direction != 'LEADING':
            continue
        # Must be STABLE or STRENGTHENING
        if s.ic_status not in ('STABLE', 'STRENGTHENING'):
            continue
        # Must have sufficient IC
        if abs(s.mean_ic) < min_abs_ic:
            continue
        filtered.append(s)
    return filtered


def discover_common_features(profiles: Dict[str, TokenSignalProfile],
                              min_token_count: int = 3) -> List[str]:
    """Find features that appear across multiple tokens' filtered signals.

    Returns ordered list of the most common features (up to ~30).
    """
    feature_counts = defaultdict(int)
    for profile in profiles.values():
        seen = set()
        for s in profile.filtered_signals:
            if s.feature not in seen:
                feature_counts[s.feature] += 1
                seen.add(s.feature)

    # Sort by frequency, take features appearing in >= min_token_count tokens
    common = [(feat, count) for feat, count in feature_counts.items()
              if count >= min_token_count]
    common.sort(key=lambda x: x[1], reverse=True)

    # Cap at 30 most common
    return [feat for feat, _ in common[:30]]


def build_ic_vector(profile: TokenSignalProfile,
                     common_features: List[str]) -> np.ndarray:
    """Build fixed-length IC vector for a token.

    For each common feature, use the IC from the best (highest |IC|) horizon.
    0.0 if the feature is not significant for this token.
    """
    # Index filtered signals by feature name, keep best horizon
    best_ic = {}
    for s in profile.filtered_signals:
        if s.feature in best_ic:
            if abs(s.mean_ic) > abs(best_ic[s.feature]):
                best_ic[s.feature] = s.mean_ic
        else:
            best_ic[s.feature] = s.mean_ic

    return np.array([best_ic.get(feat, 0.0) for feat in common_features])


def load_all_profiles(config: SignalPortfolioConfig) -> Dict[str, TokenSignalProfile]:
    """Load and filter signal profiles for all available tokens.

    Returns:
        Dict mapping token name to TokenSignalProfile with filtered_signals
        and ic_vector populated.
    """
    signal_dir = config.signal_dir

    # Discover available tokens from catalog files
    tokens = []
    for fname in os.listdir(signal_dir):
        if fname.startswith('signal_catalog_') and fname.endswith('.json'):
            token = fname[len('signal_catalog_'):-len('.json')]
            tokens.append(token)

    if not tokens:
        print(f"No signal catalogs found in {signal_dir}")
        return {}

    tokens.sort()
    print(f"Found {len(tokens)} token catalogs")

    # Load profiles
    profiles = {}
    for token in tokens:
        profile = load_token_profile(token, signal_dir)
        if profile is None:
            continue
        profile.filtered_signals = filter_signals(profile, config.min_abs_ic)
        if profile.filtered_signals:
            profiles[token] = profile

    print(f"Loaded {len(profiles)} tokens with valid filtered signals")

    # Build IC vectors
    common_features = discover_common_features(profiles)
    print(f"Found {len(common_features)} common features across tokens")

    for token, profile in profiles.items():
        profile.ic_vector = build_ic_vector(profile, common_features)

    return profiles
