#!/bin/bash
# Data Integrity Check — run before any destructive git operations
# Usage: bash tools/check_data_integrity.sh

DATA_DIR="data"
MIN_FILES=100
MIN_SIZE_MB=100

if [ ! -d "$DATA_DIR" ]; then
    echo "FAIL: data/ directory does not exist"
    exit 1
fi

FILE_COUNT=$(find "$DATA_DIR" -name "*.csv" -o -name "*.parquet" 2>/dev/null | wc -l)
SIZE_KB=$(du -sk "$DATA_DIR" 2>/dev/null | awk '{print $1}')
SIZE_MB=$((SIZE_KB / 1024))

echo "Data directory: ${SIZE_MB}MB, ${FILE_COUNT} data files (csv+parquet)"

if [ "$FILE_COUNT" -lt "$MIN_FILES" ]; then
    echo "WARNING: Expected >$MIN_FILES data files, found $FILE_COUNT"
    echo "Data may be missing. Run: bash tools/fetch_all_perp_data.sh"
    exit 1
fi

if [ "$SIZE_MB" -lt "$MIN_SIZE_MB" ]; then
    echo "WARNING: Expected >$MIN_SIZE_MB MB, found ${SIZE_MB}MB"
    echo "Data may be incomplete."
    exit 1
fi

echo "OK: Data integrity check passed"
exit 0
