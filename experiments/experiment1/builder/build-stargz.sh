#!/bin/bash
set -euox pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA="${SCRIPT_DIR}/../schema.yaml"
eval "$(python3 "${SCRIPT_DIR}/../../load-schema.py" "${SCHEMA}")"

time buildctl build \
    --frontend dockerfile.v0 \
    --opt filename=Dockerfile.stargz \
    --local context=. \
    --local dockerfile=. \
    --output type=image,name=${REGISTRY_NODE}:5000/${IMG_STARGZ_NAME}:${IMG_STARGZ_TAG},push=true,compression=estargz,oci-mediatypes=true,registry.insecure=true
