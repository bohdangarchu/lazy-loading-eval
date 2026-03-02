#!/bin/bash
set -euox pipefail
export REGISTRY_NODE="10.10.1.2"

time nerdctl build \
    --output type=image,name=${REGISTRY_NODE}:5000/python:3.10-experiment1-esgz,push=true,compression=estargz,oci-mediatypes=true,force-compression=true \
    -f Dockerfile.stargz .