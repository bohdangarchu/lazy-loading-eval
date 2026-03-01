#!/bin/bash
set -euox pipefail

export REGISTRY_NODE="10.10.1.2"

for i in 1 2 3; do
    IMAGE="${REGISTRY_NODE}:5000/experiment1-base:${i}"
    time nerdctl build -t "${IMAGE}" -f "Dockerfile.base.${i}" .
done

for i in 1 2 3; do
    IMAGE="${REGISTRY_NODE}:5000/experiment1-base:${i}"
    time nerdctl push --insecure-registry "${IMAGE}"
done
