#!/bin/bash
set -euox pipefail
STARGZ_ROOT="/var/lib/containerd-stargz-grpc"

sudo systemctl stop containerd stargz-snapshotter
sudo bash -c 'rm -rf /var/lib/containerd/io.containerd.content.v1.content/blobs/*'
sudo bash -c 'rm -rf /var/lib/containerd/io.containerd.content.v1.content/ingest/*'
sudo bash -c 'rm -rf /var/lib/containerd/io.containerd.snapshotter.v1.overlayfs/*'
sudo rm -rf /var/lib/containerd/io.containerd.metadata.v1.bolt/meta.db
sudo bash -c "rm -rf ${STARGZ_ROOT:?}/*"
sudo systemctl start containerd stargz-snapshotter
sleep 10
