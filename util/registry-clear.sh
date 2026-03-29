#!/bin/bash
set -euox pipefail

VOLUME_NAME="2dfs-registry-data"

# Stop and remove registry container
EXISTING=$(sudo nerdctl ps -a --format '{{.Names}}' | grep tdfs-registry || true)
if [ -n "$EXISTING" ]; then
  sudo nerdctl rm -f "$EXISTING"
fi

# Remove the registry data volume
sudo nerdctl volume rm "$VOLUME_NAME" 2>/dev/null || true

# Prune any anonymous/orphan volumes
sudo nerdctl volume prune -f

echo "Registry stopped and volumes cleared. Run registry-start.sh to start fresh."
