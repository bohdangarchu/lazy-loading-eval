#!/bin/bash
set -euox pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA="${SCRIPT_DIR}/../schema.yaml"
eval "$(python3 "${SCRIPT_DIR}/../../load-schema.py" "${SCHEMA}")"


echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting rebuild-base"
sudo ./rebuild-base.sh
sleep 60

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting rebuild-2dfs"
time sudo TMPDIR=/mydata/tmp TDFS_HOME=/mydata/.2dfs tdfs build --platforms linux/amd64 --enable-stargz --force-http \
    ${BASE_IMAGE} \
    ${REGISTRY_NODE}:5000/${IMG_2DFS_NAME}:${IMG_2DFS_TAG}
time sudo TMPDIR=/mydata/tmp TDFS_HOME=/mydata/.2dfs tdfs image push --force-http ${REGISTRY_NODE}:5000/${IMG_2DFS_NAME}:${IMG_2DFS_TAG}
sleep 60

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting rebuild-stargz"
time buildctl build \
    --frontend dockerfile.v0 \
    --opt filename=Dockerfile.stargz \
    --local context=. \
    --local dockerfile=. \
    --output type=image,name=${REGISTRY_NODE}:5000/${IMG_STARGZ_NAME}:${IMG_STARGZ_TAG},push=true,compression=estargz,oci-mediatypes=true,registry.insecure=true
