#!/bin/bash
set -euox pipefail

# todo: time full run not inside the image
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA="${SCRIPT_DIR}/../schema.yaml"

eval "$(python3 "${SCRIPT_DIR}/../../load-schema.py" "${SCHEMA}")"
ALLOTMENT=${REFRESH_INDEX}

BASE_IMAGE="${REGISTRY_NODE}:5000/${IMG_BASE_NAME}:$((ALLOTMENT + 1))"
STARGZ_IMAGE="${REGISTRY_NODE}:5000/${IMG_STARGZ_NAME}:${IMG_STARGZ_TAG}"
TDFS_IMAGE="${REGISTRY_NODE}:5000/${IMG_2DFS_PATH}:${IMG_2DFS_TAG}--0.0.0.${ALLOTMENT}"
TDFS_STARGZ_IMAGE="${REGISTRY_NODE}:5000/${IMG_2DFS_STARGZ_PATH}:${IMG_2DFS_STARGZ_TAG}--0.0.0.${ALLOTMENT}"
STARGZ_ROOT="/var/lib/containerd-stargz-grpc"

clear_cache() {
    sudo systemctl stop stargz-snapshotter
    # remove all images
    sudo nerdctl image rm -f $(sudo nerdctl images -q) 2>/dev/null || true
    sudo ctr images rm $(sudo ctr images ls -q) 2>/dev/null || true
    # remove all content blobs
    sudo ctr content rm $(sudo ctr content ls -q) 2>/dev/null || true
    # remove all snapshots (overlayfs and stargz)
    sudo ctr snapshots --snapshotter overlayfs ls | awk 'NR>1 {print $1}' | xargs -r sudo ctr snapshots --snapshotter overlayfs rm 2>/dev/null || true
    sudo ctr snapshots --snapshotter stargz ls | awk 'NR>1 {print $1}' | xargs -r sudo ctr snapshots --snapshotter stargz rm 2>/dev/null || true
    # clear stargz on-disk cache
    sudo rm -rf "${STARGZ_ROOT:?}"/*
    sudo systemctl start stargz-snapshotter
    sudo systemctl restart containerd
    sleep 60
}

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === BASE: ${BASE_IMAGE} ==="
clear_cache
time sudo ctr images pull --plain-http "${BASE_IMAGE}"
time sudo ctr run --rm "${BASE_IMAGE}" run-base-$$ python3 /main.py
clear_cache

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === 2DFS + STARGZ: ${TDFS_STARGZ_IMAGE} ==="
time sudo ctr-remote images rpull --plain-http --use-containerd-labels "${TDFS_STARGZ_IMAGE}"
time sudo ctr-remote run --rm --snapshotter=stargz "${TDFS_STARGZ_IMAGE}" run-2dfs-$$ python3 /main.py
clear_cache

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === STARGZ: ${STARGZ_IMAGE} ==="
time sudo ctr-remote images rpull --plain-http "${STARGZ_IMAGE}"
time sudo ctr-remote run --rm --snapshotter=stargz "${STARGZ_IMAGE}" run-stargz-$$ python3 /main.py
clear_cache

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === 2DFS: ${TDFS_IMAGE} ==="
time sudo ctr images pull --plain-http "${TDFS_IMAGE}"
time sudo ctr run --rm "${TDFS_IMAGE}" run-2dfs-$$ python3 /main.py
clear_cache
