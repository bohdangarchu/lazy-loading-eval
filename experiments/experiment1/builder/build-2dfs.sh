#!/bin/bash
set -euox pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA="${SCRIPT_DIR}/../schema.yaml"
export REGISTRY_NODE=$(python3 -c "import yaml; print(yaml.safe_load(open('${SCHEMA}'))['registry_node'])")
export BASE_IMAGE=$(python3 -c "import yaml; print(yaml.safe_load(open('${SCHEMA}'))['base_image'])")
export IMAGE=${REGISTRY_NODE}:5000/experiment1-2dfs:latest

time sudo TMPDIR=/mydata/tmp TDFS_HOME=/mydata/.2dfs tdfs build --platforms linux/amd64 --force-http ${BASE_IMAGE} ${IMAGE}
time sudo TMPDIR=/mydata/tmp TDFS_HOME=/mydata/.2dfs tdfs image push --force-http ${IMAGE}