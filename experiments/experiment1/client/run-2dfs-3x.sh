#!/bin/bash
set -euox pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA_DIR="${SCRIPT_DIR}/.."

eval "$(python3 "${SCRIPT_DIR}/../../load-schema.py" ${SCHEMA_DIR}/schema.yaml)"

TDFS_IMAGE="${REGISTRY_NODE}:5000/${IMG_2DFS_PATH}:${IMG_2DFS_TAG}--0.0.0.${REFRESH_INDEX}"

for i in 1 2 3; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] === 2DFS run ${i}/3: ${TDFS_IMAGE} ==="
    bash "${SCRIPT_DIR}/clear-cache.sh"
    time sudo ctr images pull --plain-http "${TDFS_IMAGE}" >/dev/null
    time sudo ctr run --rm "${TDFS_IMAGE}" run-2dfs python3 /main.py
done
