#!/bin/bash
set -euox pipefail

REGISTRY_SRC="/home/garchu/workspace/2dfs-custom/2dfs-registry"
IMAGE_NAME="tdfs-registry-1"
VOLUME_NAME="2dfs-registry-data"

# Build the registry image
sudo nerdctl build -t "$IMAGE_NAME" "$REGISTRY_SRC"

# Remove existing container if any
EXISTING=$(sudo nerdctl ps -a --format '{{.Names}}' | grep tdfs-registry || true)
if [ -n "$EXISTING" ]; then
  sudo nerdctl rm -f "$EXISTING"
fi

# Run the registry with a named volume
sudo nerdctl run -d \
  --name tdfs-registry-1 \
  -e OTEL_TRACES_EXPORTER=none \
  -v "$VOLUME_NAME":/var/lib/registry \
  -p 5000:5000 \
  "$IMAGE_NAME"

echo "Registry running at localhost:5000"
