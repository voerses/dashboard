#!/usr/bin/env bash
# =============================================================================
# Backfill Data — Re-fetch CSVs + Rebuild Parquet Cache
# =============================================================================
#
# Fixes the Mar 5-14 2026 data gap and brings all tokens up to date.
#
# What it does:
#   1. Re-fetches perp OHLCV + funding CSVs from Binance/Hyperliquid/Kraken
#   2. Re-fetches spot OHLCV CSVs from Binance
#   3. Rebuilds parquet cache for both markets (with quality checks)
#
# Usage:
#   bash tools/backfill_data.sh              # full backfill
#   bash tools/backfill_data.sh --spot-only  # spot parquet rebuild only
#   bash tools/backfill_data.sh --perp-only  # perp parquet rebuild only
#   bash tools/backfill_data.sh --no-fetch   # skip fetch, just rebuild parquet
#
# Estimated time: ~30-60 minutes (fetching) + ~2 minutes (parquet rebuild)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="${PROJECT_DIR}/venv/bin/python"

# Parse args
FETCH=true
SPOT=true
PERP=true

for arg in "$@"; do
    case "$arg" in
        --no-fetch)   FETCH=false ;;
        --spot-only)  PERP=false ;;
        --perp-only)  SPOT=false ;;
        --help|-h)
            echo "Usage: bash tools/backfill_data.sh [--no-fetch] [--spot-only] [--perp-only]"
            exit 0
            ;;
    esac
done

echo "============================================================"
echo "  Data Backfill — $(date -Iseconds)"
echo "============================================================"
echo "  Fetch:  $FETCH"
echo "  Spot:   $SPOT"
echo "  Perp:   $PERP"
echo ""

# ---------------------------------------------------------------
# Step 1: Re-fetch raw CSVs from exchanges
# ---------------------------------------------------------------
if $FETCH; then
    if $PERP; then
        echo ">>> [1a] Fetching perp data (Binance + Hyperliquid + Kraken)..."
        bash "$SCRIPT_DIR/fetch_all_perp_data.sh" --force
        echo ""
    fi

    if $SPOT; then
        echo ">>> [1b] Fetching spot data (Binance)..."
        "$PYTHON" "$SCRIPT_DIR/fetch_binance_spot.py" --force --workers 4
        echo ""
    fi
else
    echo ">>> Skipping fetch (--no-fetch)"
    echo ""
fi

# ---------------------------------------------------------------
# Step 2: Rebuild parquet cache from CSVs
# ---------------------------------------------------------------
if $PERP; then
    echo ">>> [2a] Building perp parquet cache..."
    "$PYTHON" "$SCRIPT_DIR/build_parquet_cache.py" --market perp --force --verbose
    echo ""
fi

if $SPOT; then
    echo ">>> [2b] Building spot parquet cache..."
    "$PYTHON" "$SCRIPT_DIR/build_parquet_cache.py" --market spot --force --verbose
    echo ""
fi

# ---------------------------------------------------------------
# Step 3: Verify results
# ---------------------------------------------------------------
echo ">>> [3] Verification..."
"$PYTHON" -c "
import pandas as pd
import glob, os

data_dir = '$PROJECT_DIR/data'

for market in ['spot', 'perp']:
    files = sorted(glob.glob(f'{data_dir}/{market}/1h_cache/*_1h.parquet'))
    if not files:
        print(f'{market}: NO PARQUET FILES')
        continue

    latest = pd.Timestamp.min
    oldest_end = pd.Timestamp.max
    total_bars = 0
    for f in files:
        df = pd.read_parquet(f)
        total_bars += len(df)
        if len(df) > 0:
            end = df.index.max()
            if end > latest:
                latest = end
            if end < oldest_end:
                oldest_end = end

    print(f'{market.upper()}: {len(files)} tokens, {total_bars:,} total bars')
    print(f'  Latest end:  {latest}')
    print(f'  Oldest end:  {oldest_end}')

# Check spot vs perp alignment for key tokens
print()
print('Spot vs Perp end-date alignment (key tokens):')
for token in ['BTC', 'ETH', 'SOL', 'BONK', 'PEPE', 'FLOKI', 'SHIB']:
    sp_path = f'{data_dir}/spot/1h_cache/{token}_1h.parquet'
    pp_path = f'{data_dir}/perp/1h_cache/{token}_1h.parquet'
    if os.path.exists(sp_path) and os.path.exists(pp_path):
        sp = pd.read_parquet(sp_path)
        pp = pd.read_parquet(pp_path)
        gap_h = abs((sp.index.max() - pp.index.max()).total_seconds()) / 3600
        status = 'OK' if gap_h < 24 else f'GAP {gap_h:.0f}h'
        print(f'  {token}: spot→{sp.index.max()} perp→{pp.index.max()} [{status}]')
"

echo ""
echo "============================================================"
echo "  Backfill complete — $(date -Iseconds)"
echo "============================================================"
