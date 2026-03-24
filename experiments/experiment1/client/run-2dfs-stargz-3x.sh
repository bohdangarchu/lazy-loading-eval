#!/bin/bash
set -euox pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA_DIR="${SCRIPT_DIR}/.."

eval "$(python3 "${SCRIPT_DIR}/../../load-schema.py" ${SCHEMA_DIR}/schema.yaml)"

TDFS_STARGZ_IMAGE="${REGISTRY_NODE}:5000/${IMG_2DFS_STARGZ_PATH}:${IMG_2DFS_STARGZ_TAG}--0.0.0.${REFRESH_INDEX}"

for i in 1 2 3; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] === 2DFS + STARGZ run ${i}/3: ${TDFS_STARGZ_IMAGE} ==="
    bash "${SCRIPT_DIR}/clear-cache.sh"
    time sudo ctr-remote images rpull --plain-http --use-containerd-labels "${TDFS_STARGZ_IMAGE}" >/dev/null
    time sudo ctr-remote run --rm --snapshotter=stargz "${TDFS_STARGZ_IMAGE}" run-2dfs-stargz python3 /main.py
done
