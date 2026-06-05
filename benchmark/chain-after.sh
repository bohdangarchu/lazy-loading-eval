#!/usr/bin/env bash
set -euo pipefail

PID="661181"
NEXT="./run-bg.sh"

# Guard: never chain off a PID that was already dead — that's not "finished".
if ! kill -0 "$PID" 2>/dev/null; then
    echo "ERROR: PID $PID is not alive right now. Refusing to launch $NEXT." >&2
    echo "Pass the PID of a currently-running process." >&2
    exit 1
fi

echo "watching live PID $PID; will run $NEXT when it exits..."
while kill -0 "$PID" 2>/dev/null; do
    sleep 30
done
echo "PID $PID gone, launching $NEXT"

exec "$NEXT"
