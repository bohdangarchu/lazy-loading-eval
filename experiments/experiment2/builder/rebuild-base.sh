#!/bin/bash
set -euox pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA="${SCRIPT_DIR}/../schema.yaml"

eval "$(python3 "${SCRIPT_DIR}/../../load-schema.py" "${SCHEMA}")"
IMAGE_NUM=$((REFRESH_INDEX + 1))
IMAGE="${REGISTRY_NODE}:5000/${IMG_BASE_NAME}:${IMAGE_NUM}"

echo "=== BUILD+PUSH: ${IMAGE} ==="
time buildctl build \
    --frontend dockerfile.v0 \
    --opt filename="Dockerfile.base.${IMAGE_NUM}" \
    --local context=. \
    --local dockerfile=. \
    --output type=image,name="${IMAGE}",push=true,registry.insecure=true
