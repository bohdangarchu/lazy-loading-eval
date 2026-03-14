#!/bin/bash
set -euox pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA="${SCRIPT_DIR}/../schema.yaml"

REGISTRY_NODE="10.10.1.2"
REFRESH_INDEX=$(python3 -c "import yaml; print(yaml.safe_load(open('${SCHEMA}'))['refresh_index'])")
IMAGE_NUM=$((REFRESH_INDEX + 1))
IMAGE="${REGISTRY_NODE}:5000/experiment1-base:${IMAGE_NUM}"

echo "=== BUILD+PUSH: ${IMAGE} ==="
time buildctl build \
    --frontend dockerfile.v0 \
    --opt filename="Dockerfile.base.${IMAGE_NUM}" \
    --local context=. \
    --local dockerfile=. \
    --output type=image,name="${IMAGE}",push=true,registry.insecure=true
