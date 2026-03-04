#!/bin/bash
set -euox pipefail


# -------------------------------------------------------------------
# Versions
# -------------------------------------------------------------------
CONTAINERD_VERSION="2.2.1"
RUNC_VERSION="1.3.4"
CNI_VERSION="1.9.0"
STARGZ_VERSION="0.18.2"
NERDCTL_VERSION="2.2.1"
PROMETHEUS_VERSION=3.9.1
GO_VERSION="1.23.6"

ARCH="amd64"
OS="linux"

# -------------------------------------------------------------------
# Root check
# -------------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
  echo "❌ Please run as root"
  exit 1
fi

echo "▶ Installing containerd=${CONTAINERD_VERSION}, runc=${RUNC_VERSION}, cni=${CNI_VERSION}"

# -------------------------------------------------------------------
# Temp workspace
# -------------------------------------------------------------------
TMP_DIR="$(mktemp -d)"
cd "$TMP_DIR"

# -------------------------------------------------------------------
# Step 1: containerd
# -------------------------------------------------------------------
curl -LO "https://github.com/containerd/containerd/releases/download/v${CONTAINERD_VERSION}/containerd-${CONTAINERD_VERSION}-${OS}-${ARCH}.tar.gz"
tar Cxzvf /usr/local "containerd-${CONTAINERD_VERSION}-${OS}-${ARCH}.tar.gz"

mkdir -p /usr/local/lib/systemd/system
curl -Lo /usr/local/lib/systemd/system/containerd.service \
  "https://raw.githubusercontent.com/containerd/containerd/v${CONTAINERD_VERSION}/containerd.service"

systemctl daemon-reload
systemctl enable --now containerd

# -------------------------------------------------------------------
# Step 2: runc
# -------------------------------------------------------------------
curl -LO "https://github.com/opencontainers/runc/releases/download/v${RUNC_VERSION}/runc.${ARCH}"
install -m 755 "runc.${ARCH}" /usr/local/sbin/runc

# -------------------------------------------------------------------
# Step 3: CNI plugins
# -------------------------------------------------------------------
curl -LO "https://github.com/containernetworking/plugins/releases/download/v${CNI_VERSION}/cni-plugins-${OS}-${ARCH}-v${CNI_VERSION}.tgz"
mkdir -p /opt/cni/bin
tar Cxzvf /opt/cni/bin "cni-plugins-${OS}-${ARCH}-v${CNI_VERSION}.tgz"

# -------------------------------------------------------------------
# Step 4: Install FUSE (required for stargz)
# -------------------------------------------------------------------
apt-get update
apt-get install -y fuse
modprobe fuse

# -------------------------------------------------------------------
# Step 4b: Install Go
# -------------------------------------------------------------------
curl -LO "https://go.dev/dl/go${GO_VERSION}.${OS}-${ARCH}.tar.gz"
rm -rf /usr/local/go
tar -C /usr/local -xzf "go${GO_VERSION}.${OS}-${ARCH}.tar.gz"
export PATH="/usr/local/go/bin:$PATH"

# -------------------------------------------------------------------
# Step 5a: Install nerdctl (full) + bundled dependencies
# -------------------------------------------------------------------
curl -LO "https://github.com/containerd/nerdctl/releases/download/v${NERDCTL_VERSION}/nerdctl-full-${NERDCTL_VERSION}-${OS}-${ARCH}.tar.gz"

tar -C /usr/local -xvf \
  "nerdctl-full-${NERDCTL_VERSION}-${OS}-${ARCH}.tar.gz"

# -------------------------------------------------------------------
# Step 5b: Install stargz-snapshotter + ctr-remote
# -------------------------------------------------------------------

curl -LO https://github.com/containerd/stargz-snapshotter/releases/download/v0.18.2/stargz-snapshotter-v0.18.2-linux-amd64.tar.gz
tar -C /usr/local/bin -xvf \
"stargz-snapshotter-${STARGZ_VERSION}-${OS}-${ARCH}.tar.gz" \
containerd-stargz-grpc ctr-remote

# systemd service
curl -Lo /etc/systemd/system/stargz-snapshotter.service \
  https://raw.githubusercontent.com/containerd/stargz-snapshotter/main/script/config/etc/systemd/system/stargz-snapshotter.service

# -------------------------------------------------------------------
# Step 5c: Enable BuildKit daemon (buildkitd)
# -------------------------------------------------------------------
cat > /etc/systemd/system/buildkit.service <<'EOF'
[Unit]
Description=BuildKit
Documentation=https://github.com/moby/buildkit
After=containerd.service
Requires=containerd.service

[Service]
ExecStart=/usr/local/bin/buildkitd \
  --addr unix:///run/buildkit/buildkitd.sock \
  --containerd-worker=true \
  --containerd-worker-addr=/run/containerd/containerd.sock
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

mkdir -p /run/buildkit
systemctl daemon-reload
systemctl enable --now buildkit

# -------------------------------------------------------------------
# Step 6: Configure containerd for stargz
# -------------------------------------------------------------------
mkdir -p /etc/containerd

if [[ ! -f /etc/containerd/config.toml ]]; then
  containerd config default > /etc/containerd/config.toml
fi

# overwrite with required stargz config
cat > /etc/containerd/config.toml <<EOF
version = 2

[debug]
  level = "debug"

[plugins."io.containerd.grpc.v1.cri".containerd]
  snapshotter = "stargz"
  disable_snapshot_annotations = false

[proxy_plugins]
  [proxy_plugins.stargz]
    type = "snapshot"
    address = "/run/containerd-stargz-grpc/containerd-stargz-grpc.sock"

  [proxy_plugins.stargz.exports]
    root = "/var/lib/containerd-stargz-grpc/"
EOF

mkdir -p /etc/containerd-stargz-grpc

cat > /etc/containerd-stargz-grpc/config.toml <<EOF
noprefetch = true
no_background_fetch = true
disable_verification = true
# metrics_address = "127.0.0.1:8234"
EOF

# -------------------------------------------------------------------
# Step 7: Enable services
# -------------------------------------------------------------------
systemctl daemon-reload
systemctl enable --now stargz-snapshotter
systemctl restart containerd

# -------------------------------------------------------------------
# Cleanup
# -------------------------------------------------------------------
cd /
rm -rf "$TMP_DIR"

# -------------------------------------------------------------------
# Verification
# -------------------------------------------------------------------
containerd --version
nerdctl --version
buildkitd --version
runc --version
ctr-remote --help | head -n 5
systemctl status stargz-snapshotter --no-pager
systemctl status buildkit --no-pager

echo "✅ containerd + stargz snapshotter installed successfully"