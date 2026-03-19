#!/bin/bash
set -euox pipefail

# todo: time full run not inside the image
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA="${SCRIPT_DIR}/../schema.yaml"

REGISTRY_NODE=$(python3 -c "import yaml; print(yaml.safe_load(open('${SCHEMA}'))['registry_node'])")

ALLOTMENT=$(python3 -c "import yaml; print(yaml.safe_load(open('${SCHEMA}'))['refresh_index'])")

BASE_IMAGE="${REGISTRY_NODE}:5000/experiment1-base:$((ALLOTMENT + 1))"
STARGZ_IMAGE="${REGISTRY_NODE}:5000/experiment1-esgz:latest"
TDFS_IMAGE="${REGISTRY_NODE}:5000/library/experiment1-2dfs:latest--0.0.0.$((ALLOTMENT))"]
TDFS_STARGZ_IMAGE="${REGISTRY_NODE}:5000/library/experiment1-2dfs-stargz:latest--0.0.0.$((ALLOTMENT))"
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
    sleep 10
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
