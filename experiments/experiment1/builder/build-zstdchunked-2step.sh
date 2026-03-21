#!/bin/bash
set -euox pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA="${SCRIPT_DIR}/../schema.yaml"
export REGISTRY_NODE=$(python3 -c "import yaml; print(yaml.safe_load(open('${SCHEMA}'))['registry_node'])")

LOCAL_IMAGE="python:3.10-experiment1-zstd-local"
REMOTE_IMAGE="${REGISTRY_NODE}:5000/python:3.10-experiment1-zstd"

time nerdctl build -t "${LOCAL_IMAGE}" -f Dockerfile.stargz .
time ctr-remote image convert --zstdchunked --oci "docker.io/library/${LOCAL_IMAGE}" "${REMOTE_IMAGE}"
# time nerdctl push --insecure-registry "${REMOTE_IMAGE}"
