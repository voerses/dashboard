#!/bin/bash
# Sweep stop_mult across 4 regime+direction combos
# Each combo sweeps 2.0-6.0 in 0.5 steps, others stay at 5.0

STRATEGY_FILE="/workspace/crypto_backtest/strategies/s524r_time_stops.py"

# Save original
cp "$STRATEGY_FILE" "${STRATEGY_FILE}.bak"

run_backtest() {
    local results=""
    for end_label in "2023-01-01T00:00:00 2022" "2024-01-01T00:00:00 2023" "2025-01-01T00:00:00 2024" "2026-01-01T00:00:00 2025" "2026-04-05T16:00:00 Q1-26"; do
        set -- $end_label; end=$1; label=$2; months=12; [ "$label" = "Q1-26" ] && months=3
        val=$(/workspace/venv/bin/python v4/portfolio_backtest.py --strategy s524r_time_stops --months $months --capital 100000 --market perp --conviction-mode ranked --max-portfolio-positions 50 --concentration 0.30 --skip-wf --adv-cap 0.005 --end-date "$end" 2>&1 | grep "Total Return" | head -1 | sed 's/.*: //')
        results="$results|$label:$val"
    done
    echo "$results"
}

# The original lines we need to replace (lines 1001-1003):
# _stop_mult = np.full(n, STOP_MULT)
# _bear_long = _bear_regime & (direction == 1) & entry
# _stop_mult[_bear_long] = STOP_MULT_BEAR_LONG

COMBOS=("bear_long:_bear_regime:1" "bear_short:_bear_regime:-1" "bull_long:~_bear_regime:1" "bull_short:~_bear_regime:-1")

for combo in "${COMBOS[@]}"; do
    IFS=':' read -r name regime dir <<< "$combo"
    echo ""
    echo "=== ${name^^} sweep ==="
    echo "stop  | 2022    | 2023    | 2024    | 2025    | Q1-26   | SUM"

    for stop in 2.0 2.5 3.0 3.5 4.0 4.5 5.0 5.5 6.0; do
        # Restore original
        cp "${STRATEGY_FILE}.bak" "$STRATEGY_FILE"

        # Build replacement code - all at 5.0, then override the specific combo
        if [ "$stop" = "5.0" ]; then
            # No override needed, just use default 5.0 for everything
            NEW_CODE="    _stop_mult = np.full(n, 5.0)"
        else
            NEW_CODE="    _stop_mult = np.full(n, 5.0)\n    _sweep_mask = (${regime}) \& (direction == ${dir}) \& entry\n    _stop_mult[_sweep_mask] = ${stop}"
        fi

        # Use python to do the replacement safely
        /workspace/venv/bin/python -c "
import re
with open('$STRATEGY_FILE', 'r') as f:
    content = f.read()

old = '''    _stop_mult = np.full(n, STOP_MULT)
    _bear_long = _bear_regime & (direction == 1) & entry
    _stop_mult[_bear_long] = STOP_MULT_BEAR_LONG'''

if '$stop' == '5.0':
    new = '    _stop_mult = np.full(n, 5.0)'
else:
    new = '    _stop_mult = np.full(n, 5.0)\n    _sweep_mask = ($regime) & (direction == $dir) & entry\n    _stop_mult[_sweep_mask] = $stop'

content = content.replace(old, new)
with open('$STRATEGY_FILE', 'w') as f:
    f.write(content)
"

        # Run backtest
        raw=$(run_backtest)

        # Parse results
        y2022=$(echo "$raw" | tr '|' '\n' | grep "2022:" | sed 's/2022://')
        y2023=$(echo "$raw" | tr '|' '\n' | grep "2023:" | sed 's/2023://')
        y2024=$(echo "$raw" | tr '|' '\n' | grep "2024:" | sed 's/2024://')
        y2025=$(echo "$raw" | tr '|' '\n' | grep "2025:" | sed 's/2025://')
        q126=$(echo "$raw" | tr '|' '\n' | grep "Q1-26:" | sed 's/Q1-26://')

        # Compute sum (strip % and +)
        sum=0
        for v in "$y2022" "$y2023" "$y2024" "$y2025" "$q126"; do
            num=$(echo "$v" | sed 's/[%+,]//g' | tr -d ' ')
            if [ -n "$num" ]; then
                sum=$(echo "$sum + $num" | bc 2>/dev/null || echo "$sum")
            fi
        done

        printf "%-5s | %-7s | %-7s | %-7s | %-7s | %-7s | %s%%\n" "$stop" "$y2022" "$y2023" "$y2024" "$y2025" "$q126" "$sum"
    done
done

# Restore original
cp "${STRATEGY_FILE}.bak" "$STRATEGY_FILE"
rm "${STRATEGY_FILE}.bak"
echo ""
echo "Strategy file restored to original."
