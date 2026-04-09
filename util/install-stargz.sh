#!/bin/bash
set -e

BINARIES="containerd-stargz-grpc ctr-remote"
INSTALL_DIR="/usr/local/bin"
BUILD_DIR="/home/garchu/workspace/2dfs/stargz-snapshotter/out"
SERVICE="stargz-snapshotter.service"

echo "Stopping $SERVICE..."
sudo systemctl stop "$SERVICE"

for bin in $BINARIES; do
    src="$BUILD_DIR/$bin"
    dst="$INSTALL_DIR/$bin"
    if [ ! -f "$src" ]; then
        echo "ERROR: $src not found"
        sudo systemctl start "$SERVICE"
        exit 1
    fi
    echo "Installing $bin -> $dst"
    sudo cp "$src" "$dst"
    sudo chmod +x "$dst"
done

echo "Starting $SERVICE..."
sudo systemctl start "$SERVICE"

echo "Verifying..."
sudo systemctl status "$SERVICE" --no-pager
