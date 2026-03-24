#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE=/tmp/prometheus-stargz.pid
PORT=9090

if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "prometheus already running (pid $(cat "$PIDFILE")) at http://localhost:${PORT}"
    exit 0
fi

if ! command -v prometheus &>/dev/null; then
    echo "prometheus not found — run setup-prometheus.sh first"
    exit 1
fi

prometheus \
    --config.file="${SCRIPT_DIR}/prometheus.yml" \
    --storage.tsdb.path=/tmp/prometheus-stargz-data \
    --web.listen-address="0.0.0.0:${PORT}" \
    --log.level=warn \
    &>/tmp/prometheus-stargz.log &

echo $! > "$PIDFILE"
echo "prometheus started (pid $!) at http://localhost:${PORT}"
