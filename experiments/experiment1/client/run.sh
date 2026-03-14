#!/bin/bash
set -euox pipefail

REGISTRY_NODE="10.10.1.2"
# ./run.sh 0 → chunk1.bin
ALLOTMENT="${1:?Usage: $0 <allotment_index>}"  # 0-based column index

FILE_NAME="chunk$((ALLOTMENT + 1)).bin"
FILE_PATH="/${FILE_NAME}"

BASE_IMAGE="${REGISTRY_NODE}:5000/experiment1-base:$((ALLOTMENT + 1))"
STARGZ_IMAGE="${REGISTRY_NODE}:5000/python:3.10-experiment1-esgz"
TDFS_IMAGE="${REGISTRY_NODE}:5000/python:3.10-experiment1-2dfs--0.${ALLOTMENT}.0.${ALLOTMENT}"
STARGZ_ROOT="/var/lib/containerd-stargz-grpc"

clear_cache() {
    sudo systemctl stop stargz-snapshotter
    # remove all images
    sudo nerdctl image rm -f $(sudo nerdctl images -q) 2>/dev/null || true
    sudo ctr images rm $(sudo ctr images ls -q) 2>/dev/null || true
    # remove all content blobs
    sudo ctr content rm $(sudo ctr content ls -q) 2>/dev/null || true
    # remove all snapshots (overlayfs and stargz)
    sudo ctr snapshots --snapshotter overlayfs rm $(sudo ctr snapshots --snapshotter overlayfs ls -q) 2>/dev/null || true
    sudo ctr snapshots --snapshotter stargz rm $(sudo ctr snapshots --snapshotter stargz ls -q) 2>/dev/null || true
    # clear stargz on-disk cache
    sudo rm -rf "${STARGZ_ROOT:?}"/*
    sudo systemctl start stargz-snapshotter
    sudo systemctl restart containerd
    sleep 2
}

echo "=== BASE: ${BASE_IMAGE} ==="
clear_cache
time sudo nerdctl pull --insecure-registry "${BASE_IMAGE}"
sudo nerdctl run --rm "${BASE_IMAGE}" python3 /main.py "${FILE_PATH}"

echo "=== STARGZ: ${STARGZ_IMAGE} ==="
clear_cache
time sudo nerdctl pull --insecure-registry --snapshotter=stargz "${STARGZ_IMAGE}"
sudo nerdctl run --rm --snapshotter=stargz "${STARGZ_IMAGE}" python3 /main.py "${FILE_PATH}"

echo "=== 2DFS: ${TDFS_IMAGE} ==="
clear_cache
time sudo ctr-remote images rpull --plain-http "${TDFS_IMAGE}"
sudo nerdctl run --rm --snapshotter=stargz "${TDFS_IMAGE}" python3 /main.py "${FILE_PATH}"
clear_cache
