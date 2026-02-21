#!/bin/bash
set -euo pipefail

CONTAINERD_VERSION="2.2.1"
RUNC_VERSION="1.3.4"
CNI_VERSION="1.9.0"

ARCH="amd64"
OS="linux"

# Check running as root
if [[ $EUID -ne 0 ]]; then
  echo "❌ Please run as root (sudo ./install-containerd-2.2.1.sh)"
  exit 1
fi

echo "▶ Installing containerd ${CONTAINERD_VERSION}, runc ${RUNC_VERSION}, CNI ${CNI_VERSION}"

# -------------------------------------------------------------------
# Step 1: Install containerd
# -------------------------------------------------------------------
TMP_DIR="$(mktemp -d)"
cd "$TMP_DIR"

echo "📦 Downloading containerd v${CONTAINERD_VERSION}..."
curl -LO "https://github.com/containerd/containerd/releases/download/v${CONTAINERD_VERSION}/containerd-${CONTAINERD_VERSION}-${OS}-${ARCH}.tar.gz"

echo "📂 Extracting to /usr/local..."
tar Cxzvf /usr/local "containerd-${CONTAINERD_VERSION}-${OS}-${ARCH}.tar.gz"

echo "📄 Installing systemd service..."
mkdir -p /usr/local/lib/systemd/system
curl -Lo /usr/local/lib/systemd/system/containerd.service \
  "https://raw.githubusercontent.com/containerd/containerd/v${CONTAINERD_VERSION}/containerd.service"

systemctl daemon-reload
systemctl enable --now containerd

# -------------------------------------------------------------------
# Step 2: Install runc
# -------------------------------------------------------------------
echo "🔧 Downloading runc v${RUNC_VERSION}..."
curl -LO "https://github.com/opencontainers/runc/releases/download/v${RUNC_VERSION}/runc.${ARCH}"

echo "📂 Installing runc..."
install -m 755 "runc.${ARCH}" /usr/local/sbin/runc

# -------------------------------------------------------------------
# Step 3: Install CNI plugins
# -------------------------------------------------------------------
echo "🌐 Downloading CNI plugins v${CNI_VERSION}..."
curl -LO "https://github.com/containernetworking/plugins/releases/download/v${CNI_VERSION}/cni-plugins-${OS}-${ARCH}-v${CNI_VERSION}.tgz"

echo "📂 Extracting CNI plugins into /opt/cni/bin..."
mkdir -p /opt/cni/bin
tar Cxzvf /opt/cni/bin "cni-plugins-${OS}-${ARCH}-v${CNI_VERSION}.tgz"

# -------------------------------------------------------------------
# Cleanup and verify
# -------------------------------------------------------------------
cd /
rm -rf "$TMP_DIR"

echo "▶ Verification:"
containerd --version
runc --version
ls /opt/cni/bin | head -n10

echo "✅ Installation complete!"
