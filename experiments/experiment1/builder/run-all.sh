#!/bin/bash
set -euox pipefail

sudo ./clear-builder-cache.sh
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting build-base"
sudo ./build-base.sh
sudo ./clear-builder-cache.sh
sleep 60

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting build-2dfs"
./build-2dfs.sh
sudo ./clear-builder-cache.sh
sleep 60

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting build-stargz"
sudo ./build-stargz.sh
sudo ./clear-builder-cache.sh
sleep 60

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting build-zstdchunked"
sudo ./build-zstdchunked.sh
sudo ./clear-builder-cache.sh
sleep 60