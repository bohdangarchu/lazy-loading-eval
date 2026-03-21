#!/bin/bash
set -euox pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA="${SCRIPT_DIR}/../schema.yaml"
eval "$(python3 "${SCRIPT_DIR}/../../load-schema.py" "${SCHEMA}")"
export IMAGE=${REGISTRY_NODE}:5000/${IMG_2DFS_STARGZ_NAME}:${IMG_2DFS_STARGZ_TAG}

time sudo TMPDIR=/mydata/tmp TDFS_HOME=/mydata/.2dfs tdfs build --platforms linux/amd64 --enable-stargz --force-http ${BASE_IMAGE} ${IMAGE}
time sudo TMPDIR=/mydata/tmp TDFS_HOME=/mydata/.2dfs tdfs image push --force-http ${IMAGE}
