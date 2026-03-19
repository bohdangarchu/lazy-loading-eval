#!/bin/bash
set -euox pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA="${SCRIPT_DIR}/../schema.yaml"

eval "$(python3 "${SCRIPT_DIR}/../../load-schema.py" "${SCHEMA}")"
ALLOTMENT=${REFRESH_INDEX}

FILE_NAME="chunk$((ALLOTMENT + 1)).bin"
FILE_PATH="/${FILE_NAME}"

BASE_IMAGE="${REGISTRY_NODE}:5000/${IMG_BASE_NAME}:$((ALLOTMENT + 1))"
STARGZ_IMAGE="${REGISTRY_NODE}:5000/${IMG_STARGZ_NAME}:${IMG_STARGZ_TAG}"
TDFS_IMAGE="${REGISTRY_NODE}:5000/${IMG_2DFS_PATH}:${IMG_2DFS_TAG}--0.0.0.${ALLOTMENT}"
STARGZ_ROOT="/var/lib/containerd-stargz-grpc"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === BASE: ${BASE_IMAGE} ==="
time sudo ctr images pull --plain-http "${BASE_IMAGE}"
time sudo ctr run --rm "${BASE_IMAGE}" run-base-$$ python3 /main.py "${FILE_PATH}"
sleep 6

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === STARGZ: ${STARGZ_IMAGE} ==="
time sudo ctr-remote images rpull --plain-http "${STARGZ_IMAGE}"
time sudo ctr-remote run --rm --snapshotter=stargz "${STARGZ_IMAGE}" run-stargz-$$ python3 /main.py "${FILE_PATH}"
sleep 6

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === 2DFS: ${TDFS_IMAGE} ==="
time sudo ctr-remote images rpull --plain-http "${TDFS_IMAGE}"
time sudo ctr-remote run --rm --snapshotter=stargz "${TDFS_IMAGE}" run-2dfs-$$ python3 /main.py "${FILE_PATH}"
echo "printing checksum for validation"
time sudo ctr-remote run --rm --snapshotter=stargz "${TDFS_IMAGE}" run-2dfs-checksum-$$ sha256sum "${FILE_PATH}"
