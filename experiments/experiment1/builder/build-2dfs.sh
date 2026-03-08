#!/bin/bash
set -euox pipefail
export REGISTRY_NODE="10.10.1.2"
export IMAGE=${REGISTRY_NODE}:5000/python:3.10-experiment1-2dfs

time tdfs build --platforms linux/amd64 --enable-stargz ghcr.io/bohdangarchu/python:3.10-esgz ${REGISTRY_NODE}:5000/python:3.10-experiment1-2dfs
tdfs image push --force-http ${IMAGE}

rm -rf ~/.2dfs/blobs/* \
       ~/.2dfs/uncompressed-keys/* \
       ~/.2dfs/index/*

time tdfs-old build --platforms linux/amd64 ghcr.io/bohdangarchu/python:3.10-esgz ${REGISTRY_NODE}:5000/python:3.10-experiment1-2dfs

rm -rf ~/.2dfs/blobs/* \
       ~/.2dfs/uncompressed-keys/* \
       ~/.2dfs/index/*
