#!/bin/bash
set -euox pipefail

STARGZ_ROOT="/var/lib/containerd-stargz-grpc"

# --- stargz snapshotter ---
sudo systemctl stop stargz-snapshotter

# --- nerdctl / containerd ---
sudo nerdctl system prune -af --volumes
sudo ctr content rm $(sudo ctr content ls -q) 2>/dev/null || true
sudo ctr snapshots --snapshotter overlayfs ls | awk 'NR>1 {print $1}' | xargs -r sudo ctr snapshots --snapshotter overlayfs rm 2>/dev/null || true
sudo ctr snapshots --snapshotter stargz ls | awk 'NR>1 {print $1}' | xargs -r sudo ctr snapshots --snapshotter stargz rm 2>/dev/null || true

# --- stargz on-disk cache ---
sudo rm -rf "${STARGZ_ROOT:?}"/*

# --- buildkit cache ---
buildctl prune --all 2>/dev/null || true
sudo ctr -n buildkit content ls | awk 'NR>1 {print $1}' | xargs -r sudo ctr -n buildkit content rm 2>/dev/null || true

# --- 2dfs builder cache ---
rm -rf ~/.2dfs/blobs/* \
       ~/.2dfs/uncompressed-keys/* \
       ~/.2dfs/index/*

# --- restart ---
sudo systemctl start stargz-snapshotter
sudo systemctl restart containerd
