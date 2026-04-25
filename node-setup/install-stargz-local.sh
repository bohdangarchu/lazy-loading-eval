#!/bin/bash
set -euox pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <path-to-stargz-snapshotter-source>"
  exit 1
fi

if [[ $EUID -eq 0 ]]; then
  echo "do not run as root — script will sudo only the steps that need it"
  exit 1
fi

SRC_DIR="$(realpath "$1")"

BIN_DIR="/usr/local/bin"
SOCKET_DIR="/run/containerd-stargz-grpc"

GRPC_BIN="containerd-stargz-grpc"
CTR_REMOTE_BIN="ctr-remote"

SERVICE="stargz-snapshotter.service"

if [[ ! -d "$SRC_DIR" ]]; then
  echo "source folder does not exist: $SRC_DIR"
  exit 1
fi

cd "$SRC_DIR"

make

sudo systemctl stop containerd || true
sudo systemctl stop "$SERVICE" || true

sudo install -m 755 "./out/${GRPC_BIN}" "${BIN_DIR}/${GRPC_BIN}"
sudo install -m 755 "./out/${CTR_REMOTE_BIN}" "${BIN_DIR}/${CTR_REMOTE_BIN}"

sudo rm -f "${SOCKET_DIR}/${GRPC_BIN}.sock"

sudo systemctl daemon-reload
sudo systemctl start "$SERVICE"
sudo systemctl start containerd

ctr-remote --help | head -n 3
sudo systemctl status "$SERVICE" --no-pager

echo "snapshotter implementation replaced from ${SRC_DIR}"
