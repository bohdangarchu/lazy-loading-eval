#!/bin/bash
set -euox pipefail

export REGISTRY_NODE="10.10.1.2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA="${SCRIPT_DIR}/../schema.yaml"


echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting rebuild-base"
sudo ./rebuild-base.sh
sleep 60

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting build-2dfs"
time tdfs build --platforms linux/amd64 --enable-stargz --force-http \
    ${REGISTRY_NODE}:5000/python:3.10-esgz \
    ${REGISTRY_NODE}:5000/python:3.10-experiment2-2dfs
tdfs image push --force-http ${REGISTRY_NODE}:5000/python:3.10-experiment2-2dfs
sleep 60

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting build-stargz"
time buildctl build \
    --frontend dockerfile.v0 \
    --opt filename=Dockerfile.stargz \
    --local context=. \
    --local dockerfile=. \
    --output type=image,name=${REGISTRY_NODE}:5000/python:3.10-experiment2-esgz,push=true,compression=estargz,oci-mediatypes=true,registry.insecure=true
