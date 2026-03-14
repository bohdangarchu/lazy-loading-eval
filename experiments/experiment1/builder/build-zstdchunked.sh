#!/bin/bash
set -euox pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA="${SCRIPT_DIR}/../schema.yaml"
export REGISTRY_NODE=$(python3 -c "import yaml; print(yaml.safe_load(open('${SCHEMA}'))['registry_node'])")

LOCAL_IMAGE="experiment1-zstd"
REMOTE_IMAGE="${REGISTRY_NODE}:5000/python:3.10-experiment1-zstd"

buildah rmi -a || true
time buildah bud --format oci -t "${LOCAL_IMAGE}" -f Dockerfile.stargz .
time buildah push --compression-format zstd:chunked --tls-verify=false "${LOCAL_IMAGE}" "docker://${REMOTE_IMAGE}"
buildah rmi -a || true
