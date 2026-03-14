#!/bin/bash
set -euox pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA="${SCRIPT_DIR}/../schema.yaml"

REGISTRY_NODE=$(python3 -c "import yaml; print(yaml.safe_load(open('${SCHEMA}'))['registry_node'])")

ALLOTMENT=$(python3 -c "import yaml; print(yaml.safe_load(open('${SCHEMA}'))['refresh_index'])")

FILE_NAME="chunk$((ALLOTMENT + 1)).bin"
FILE_PATH="/${FILE_NAME}"

BASE_IMAGE="${REGISTRY_NODE}:5000/experiment2-base:$((ALLOTMENT + 1))"
STARGZ_IMAGE="${REGISTRY_NODE}:5000/python:3.10-experiment2-esgz"
TDFS_IMAGE="${REGISTRY_NODE}:5000/python:3.10-experiment2-2dfs--0.0.0.$((ALLOTMENT + 1))"

OLD_LAYER="${OLD_LAYER:?OLD_LAYER env var required}"
NEW_LAYER="${NEW_LAYER:?NEW_LAYER env var required}"

STARGZ_ROOT="/var/lib/containerd-stargz-grpc"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === BASE REFRESH: ${BASE_IMAGE} ==="
time sudo nerdctl pull --insecure-registry "${BASE_IMAGE}"
time sudo nerdctl run --rm "${BASE_IMAGE}" python3 /main.py "${FILE_PATH}"
sleep 6

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === STARGZ REFRESH: ${STARGZ_IMAGE} ==="
time sudo nerdctl pull --insecure-registry --snapshotter=stargz "${STARGZ_IMAGE}"
time sudo nerdctl run --rm --snapshotter=stargz "${STARGZ_IMAGE}" python3 /main.py "${FILE_PATH}"
sleep 6

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === 2DFS REFRESH: ${TDFS_IMAGE} ==="
time sudo ctr-remote refresh-layer "sha256:${OLD_LAYER}" "sha256:${NEW_LAYER}"
time sudo nerdctl run --rm --snapshotter=stargz "${TDFS_IMAGE}" python3 /main.py "${FILE_PATH}"
echo "printing checksum for validation"
sudo nerdctl run --rm --snapshotter=stargz "${TDFS_IMAGE}" sha256sum "${FILE_PATH}"
