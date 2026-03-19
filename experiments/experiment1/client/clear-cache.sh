#!/bin/bash
set -euox pipefail
STARGZ_ROOT="/var/lib/containerd-stargz-grpc"

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