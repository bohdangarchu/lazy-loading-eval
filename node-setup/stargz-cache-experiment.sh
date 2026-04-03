#!/bin/bash
set -euox pipefail

IMAGE="10.10.1.2:5000/ubuntu:stargz"
STARGZ_ROOT="/var/lib/containerd-stargz-grpc"

clear_cache() {
  systemctl stop stargz-snapshotter
  rm -rf "${STARGZ_ROOT:?}"/*
  nerdctl image rm -f "${IMAGE}" || true
  ctr content rm $(ctr content ls -q) || true
  systemctl start stargz-snapshotter
  systemctl restart containerd
}

clear_cache

time nerdctl pull --insecure-registry --snapshotter=stargz "${IMAGE}"

clear_cache

sleep 3
time nerdctl pull --insecure-registry --snapshotter=stargz "${IMAGE}"

clear_cache