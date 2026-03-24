#!/usr/bin/env bash
set -euo pipefail

PIDFILE=/tmp/prometheus-stargz.pid

if [[ ! -f "$PIDFILE" ]]; then
    echo "no pidfile found, prometheus may not be running"
    exit 0
fi

PID=$(cat "$PIDFILE")
if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    rm -f "$PIDFILE"
    echo "prometheus stopped (pid $PID)"
else
    echo "process $PID not running, cleaning up pidfile"
    rm -f "$PIDFILE"
fi
