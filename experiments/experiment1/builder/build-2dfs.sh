#!/bin/bash
set -euox pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA="${SCRIPT_DIR}/../schema.yaml"
export REGISTRY_NODE=$(python3 -c "import yaml; print(yaml.safe_load(open('${SCHEMA}'))['registry_node'])")
export IMAGE=${REGISTRY_NODE}:5000/python:3.10-experiment1-2dfs

time tdfs build --platforms linux/amd64 --enable-stargz --force-http ${REGISTRY_NODE}:5000/python:3.10-esgz ${REGISTRY_NODE}:5000/python:3.10-experiment1-2dfs
tdfs image push --force-http ${IMAGE}
