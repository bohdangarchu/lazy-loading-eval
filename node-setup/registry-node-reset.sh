#!/bin/bash
set -euox pipefail

sudo docker stop 2dfs-registry || true
sudo docker rm 2dfs-registry || true
sudo docker volume rm 2dfs-registry-data || true

WORKDIR="$HOME/2dfs-registry"
cd "$WORKDIR"
git pull

sudo docker build -t 2dfs/registry:latest .

sudo docker run -d \
  --name 2dfs-registry \
  -p 5000:5000 \
  -v 2dfs-registry-data:/var/lib/registry \
  2dfs/registry:latest
