#!/bin/bash
set -euox pipefail
export REGISTRY_NODE="10.10.1.2"

LOCAL_IMAGE="python:3.10-experiment1-zstd-local"
REMOTE_IMAGE="${REGISTRY_NODE}:5000/python:3.10-experiment1-zstd"

time nerdctl build -t "${LOCAL_IMAGE}" -f Dockerfile.stargz .
time ctr-remote image convert --zstdchunked --oci "docker.io/library/${LOCAL_IMAGE}" "${REMOTE_IMAGE}"
time nerdctl push --insecure-registry "${REMOTE_IMAGE}"
