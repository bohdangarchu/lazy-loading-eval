#!/bin/bash
set -eux

REGISTRY="10.10.1.2:5000"

OLD_HASH=$(sha256sum big_file2 | awk '{print $1}')

tdfs build --platforms linux/amd64 --enable-stargz ghcr.io/bohdangarchu/python:3.10-esgz ${REGISTRY}/python-2dfs:v1

FILE_SIZE=$(stat -c%s big_file2)
dd if=/dev/urandom of=big_file2 bs="$FILE_SIZE" count=1

NEW_HASH=$(sha256sum big_file2 | awk '{print $1}')

tdfs build --platforms linux/amd64 --enable-stargz ghcr.io/bohdangarchu/python:3.10-esgz ${REGISTRY}/python-2dfs:v2
tdfs image push --force-http ${REGISTRY}/python-2dfs:v1
tdfs image push --force-http ${REGISTRY}/python-2dfs:v2