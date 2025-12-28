#!/bin/bash
set -e

# Cleanup and setup directories
rm -rf /var/lib/containerd/* /var/lib/containerd-stargz-grpc/* || true
mkdir -p /var/lib/containerd /var/lib/containerd-stargz-grpc /run/containerd-stargz-grpc

# Start stargz snapshotter first
containerd-stargz-grpc \
    --address=/run/containerd-stargz-grpc/containerd-stargz-grpc.sock \
    --config=/etc/containerd-stargz-grpc/config.toml &

# Wait for stargz socket
while [ ! -S /run/containerd-stargz-grpc/containerd-stargz-grpc.sock ]; do sleep 1; done

# Start containerd
containerd --config=/etc/containerd/config.toml &

# Wait for containerd
while ! ctr-remote version >/dev/null 2>&1; do sleep 1; done

echo "Ready for evaluation"
exec "$@"