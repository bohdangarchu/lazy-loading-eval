#!/bin/bash
set -euox pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA="${SCRIPT_DIR}/../schema.yaml"

eval "$(python3 "${SCRIPT_DIR}/../../load-schema.py" "${SCHEMA}")"
ALLOTMENT=${REFRESH_INDEX}

BASE_IMAGE="${REGISTRY_NODE}:5000/${IMG_BASE_NAME}:$((ALLOTMENT + 1))"
STARGZ_IMAGE="${REGISTRY_NODE}:5000/${IMG_STARGZ_NAME}:${IMG_STARGZ_TAG}"
TDFS_STARGZ_IMAGE="${REGISTRY_NODE}:5000/${IMG_2DFS_STARGZ_PATH}:${IMG_2DFS_STARGZ_TAG}--0.0.0.${ALLOTMENT}"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === BASE: ${BASE_IMAGE} ==="
time sudo ctr images pull --plain-http "${BASE_IMAGE}"
time sudo ctr run --rm "${BASE_IMAGE}" run-base-$$ python3 /main.py
sleep 6

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === STARGZ: ${STARGZ_IMAGE} ==="
time sudo ctr-remote images rpull --plain-http "${STARGZ_IMAGE}"
time sudo ctr-remote run --rm --snapshotter=stargz "${STARGZ_IMAGE}" run-stargz-$$ python3 /main.py
sleep 6

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === 2DFS-STARGZ: ${TDFS_STARGZ_IMAGE} ==="
time sudo ctr-remote images rpull --plain-http --use-containerd-labels "${TDFS_STARGZ_IMAGE}"
time sudo ctr-remote run --rm --snapshotter=stargz "${TDFS_STARGZ_IMAGE}" run-2dfs-$$ python3 /main.py
