#!/bin/bash
set -euox pipefail
export REGISTRY_NODE="10.10.1.2"

LOCAL_IMAGE="python:3.10-experiment1-esgz-local"
REMOTE_IMAGE="${REGISTRY_NODE}:5000/python:3.10-experiment1-esgz-2step"

sudo ./clear-builder-cache.sh
time nerdctl build -t "${LOCAL_IMAGE}" -f Dockerfile.stargz .
time ctr-remote image convert --estargz --oci "docker.io/library/${LOCAL_IMAGE}" "${REMOTE_IMAGE}"
time nerdctl push --insecure-registry "${REMOTE_IMAGE}"
sudo ./clear-builder-cache.sh
