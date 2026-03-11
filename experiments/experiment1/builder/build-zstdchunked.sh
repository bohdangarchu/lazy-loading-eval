#!/bin/bash
set -euox pipefail
export REGISTRY_NODE="10.10.1.2"

LOCAL_IMAGE="experiment1-zstd"
REMOTE_IMAGE="${REGISTRY_NODE}:5000/python:3.10-experiment1-zstd"

buildah rmi -a || true
time buildah bud --format oci -t "${LOCAL_IMAGE}" -f Dockerfile.stargz .
time buildah push --compression-format zstd:chunked --tls-verify=false "${LOCAL_IMAGE}" "docker://${REMOTE_IMAGE}"
buildah rmi -a || true
