#!/usr/bin/env bash
set -euo pipefail

PID="${1:-18341}"
NEXT="${2:-./run-bg.sh}"

echo "waiting for PID $PID to exit..."
while kill -0 "$PID" 2>/dev/null; do
    sleep 30
done
echo "PID $PID gone, launching $NEXT"

exec "$NEXT"
