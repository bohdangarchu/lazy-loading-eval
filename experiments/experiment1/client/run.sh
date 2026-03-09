#!/bin/bash
set -euox pipefail

REGISTRY_NODE="10.10.1.2"
ALLOTMENT=0  # column index: 0=distilbert-base-uncased, 1=distilgpt2, 2=flan-t5-small

FILES=("distilbert-base-uncased.safetensors" "distilgpt2.safetensors" "flan-t5-small.safetensors")

BASE_IMAGE="${REGISTRY_NODE}:5000/experiment1-base:$((ALLOTMENT + 1))"
STARGZ_IMAGE="${REGISTRY_NODE}:5000/python:3.10-experiment1-esgz"
TDFS_IMAGE="${REGISTRY_NODE}:5000/python:3.10-experiment1-2dfs--0.${ALLOTMENT}.0.${ALLOTMENT}"

FILE_PATH="/${FILES[$ALLOTMENT]}"
STARGZ_ROOT="/var/lib/containerd-stargz-grpc"

clear_cache() {
    local image="$1"
    sudo systemctl stop stargz-snapshotter
    sudo rm -rf "${STARGZ_ROOT:?}"/*
    sudo nerdctl image rm -f "${image}" 2>/dev/null || true
    sudo ctr content rm $(sudo ctr content ls -q) 2>/dev/null || true
    sudo systemctl start stargz-snapshotter
    sudo systemctl restart containerd
    sleep 2
}

echo "=== BASE: ${BASE_IMAGE} ==="
clear_cache "${BASE_IMAGE}"
time sudo nerdctl pull --insecure-registry "${BASE_IMAGE}"
sudo nerdctl run --rm "${BASE_IMAGE}" python3 /main.py "${FILE_PATH}"

echo "=== STARGZ: ${STARGZ_IMAGE} ==="
clear_cache "${STARGZ_IMAGE}"
time sudo nerdctl pull --insecure-registry --snapshotter=stargz "${STARGZ_IMAGE}"
sudo nerdctl run --rm --snapshotter=stargz "${STARGZ_IMAGE}" python3 /main.py "${FILE_PATH}"

echo "=== 2DFS: ${TDFS_IMAGE} ==="
clear_cache "${TDFS_IMAGE}"
time sudo ctr-remote images rpull --plain-http "${TDFS_IMAGE}"
sudo nerdctl run --rm --snapshotter=stargz "${TDFS_IMAGE}" python3 /main.py "${FILE_PATH}"
