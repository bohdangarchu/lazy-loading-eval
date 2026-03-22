#!/bin/bash
set -euox pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA_DIR="${SCRIPT_DIR}/.."

eval "$(python3 /users/bgarchu/lazy-loading-eval/experiments/load-schema.py ${SCHEMA_DIR}/schema.yaml)"

STARGZ_IMAGE="${REGISTRY_NODE}:5000/${IMG_STARGZ_NAME}:${IMG_STARGZ_TAG}"

for i in 1 2 3; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] === STARGZ run ${i}/3: ${STARGZ_IMAGE} ==="
    bash "${SCRIPT_DIR}/clear-cache.sh"
    time sudo ctr-remote images rpull --plain-http "${STARGZ_IMAGE}" >/dev/null
    time sudo ctr-remote run --rm --snapshotter=stargz "${STARGZ_IMAGE}" run-stargz python3 /main.py
done
