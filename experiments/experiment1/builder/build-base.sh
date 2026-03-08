#!/bin/bash
set -euox pipefail

export REGISTRY_NODE="10.10.1.2"

for i in 1 2 3; do
    IMAGE="${REGISTRY_NODE}:5000/experiment1-base:${i}"
    time buildctl build \
        --frontend dockerfile.v0 \
        --opt filename="Dockerfile.base.${i}" \
        --local context=. \
        --local dockerfile=. \
        --output type=image,name="${IMAGE}",push=true,registry.insecure=true
done
