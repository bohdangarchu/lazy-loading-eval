#!/bin/bash
set -euox pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA_DIR="${SCRIPT_DIR}/.."

eval "$(python3 "${SCRIPT_DIR}/../../load-schema.py" ${SCHEMA_DIR}/schema.yaml)"

BASE_IMAGE="${REGISTRY_NODE}:5000/${IMG_BASE_NAME}:$((REFRESH_INDEX + 1))"

for i in 1 2 3; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] === BASE run ${i}/3: ${BASE_IMAGE} ==="
    bash "${SCRIPT_DIR}/clear-cache.sh"
    time sudo ctr images pull --plain-http "${BASE_IMAGE}" >/dev/null
    time sudo ctr run --rm "${BASE_IMAGE}" run-base python3 /main.py
done
