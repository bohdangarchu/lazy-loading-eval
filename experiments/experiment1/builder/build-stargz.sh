#!/bin/bash
set -euox pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA="${SCRIPT_DIR}/../schema.yaml"
export REGISTRY_NODE=$(python3 -c "import yaml; print(yaml.safe_load(open('${SCHEMA}'))['registry_node'])")

time buildctl build \
    --frontend dockerfile.v0 \
    --opt filename=Dockerfile.stargz \
    --local context=. \
    --local dockerfile=. \
    --output type=image,name=${REGISTRY_NODE}:5000/python:3.10-experiment1-esgz,push=true,compression=estargz,oci-mediatypes=true,registry.insecure=true
