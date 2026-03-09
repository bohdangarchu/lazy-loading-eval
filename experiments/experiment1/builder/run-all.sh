#!/bin/bash
set -euox pipefail

sudo ./clear-builder-cache.sh
sudo ./build-base.sh
sudo ./clear-builder-cache.sh
./build-2dfs.sh
sudo ./clear-builder-cache.sh
sudo ./build-stargz.sh
