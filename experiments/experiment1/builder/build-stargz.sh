#!/bin/bash
set -euox pipefail
export REGISTRY_NODE="10.10.1.2"

time buildctl build \
    --frontend dockerfile.v0 \
    --opt filename=Dockerfile.stargz \
    --local context=. \
    --local dockerfile=. \
    --output type=image,name=10.10.1.2:5000/python:3.10-experiment1-esgz,push=true,compression=estargz,oci-mediatypes=true,force-compression=true,registry.insecure=true  