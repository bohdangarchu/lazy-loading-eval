#!/bin/bash
set -euox pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 <REGISTRY_IP> [STARGZ_REPO_URL]"
  echo "Example: $0 10.10.1.2"
  echo "Example: $0 10.10.1.2 https://github.com/2DFS/stargz-snapshotter.git"
  exit 1
fi

REGISTRY_NODE="$1"
STARGZ_REPO_URL="${2:-}"

# -------------------------------------------------------------------
# Versions
# -------------------------------------------------------------------
CONTAINERD_VERSION="2.2.1"
RUNC_VERSION="1.3.4"
CNI_VERSION="1.9.0"
STARGZ_VERSION="0.18.2"
NERDCTL_VERSION="2.2.1"
PROMETHEUS_VERSION=3.9.1
NODE_EXPORTER_VERSION="1.8.2"
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

echo "▶ Installing containerd=${CONTAINERD_VERSION}, runc=${RUNC_VERSION}, cni=${CNI_VERSION}, stargz=${STARGZ_VERSION}"

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
if [[ -n "$STARGZ_REPO_URL" ]]; then
  STARGZ_REPO_DIR="/opt/stargz-snapshotter"
  rm -rf "$STARGZ_REPO_DIR"
  git clone "$STARGZ_REPO_URL" "$STARGZ_REPO_DIR"
  make -C "$STARGZ_REPO_DIR"
  install -m 755 "$STARGZ_REPO_DIR/out/containerd-stargz-grpc" /usr/local/bin/containerd-stargz-grpc
  install -m 755 "$STARGZ_REPO_DIR/out/ctr-remote" /usr/local/bin/ctr-remote
else
  curl -LO "https://github.com/containerd/stargz-snapshotter/releases/download/v${STARGZ_VERSION}/stargz-snapshotter-${STARGZ_VERSION}-${OS}-${ARCH}.tar.gz"
  tar -C /usr/local/bin -xvf \
    "stargz-snapshotter-${STARGZ_VERSION}-${OS}-${ARCH}.tar.gz" \
    containerd-stargz-grpc ctr-remote
fi

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

[plugins."io.containerd.grpc.v1.cri".registry.mirrors]
  [plugins."io.containerd.grpc.v1.cri".registry.mirrors."${REGISTRY_NODE}:5000"]
    endpoint = ["http://${REGISTRY_NODE}:5000"]
EOF

mkdir -p /etc/containerd-stargz-grpc

cat > /etc/containerd-stargz-grpc/config.toml <<EOF
noprefetch = false
no_background_fetch = true
disable_verification = true
metrics_address = "127.0.0.1:8234"

[[resolver.host."${REGISTRY_NODE}:5000".mirrors]]
host = "${REGISTRY_NODE}:5000"
insecure = true
EOF

# -------------------------------------------------------------------
# Step 7: Enable services
# -------------------------------------------------------------------
systemctl daemon-reload
systemctl enable --now stargz-snapshotter
systemctl restart containerd
                                                                                                                                 
# -------------------------------------------------------------------
# Step 8: Install node_exporter
# -------------------------------------------------------------------
curl -LO "https://github.com/prometheus/node_exporter/releases/download/v${NODE_EXPORTER_VERSION}/node_exporter-${NODE_EXPORTER_VERSION}.${OS}-${ARCH}.tar.gz"
tar -xzf "node_exporter-${NODE_EXPORTER_VERSION}.${OS}-${ARCH}.tar.gz"
sudo install -m 755 "node_exporter-${NODE_EXPORTER_VERSION}.${OS}-${ARCH}/node_exporter" /usr/local/bin/node_exporter

sudo tee /etc/systemd/system/node-exporter.service > /dev/null <<'EOF'
[Unit]
Description=Prometheus Node Exporter
After=network.target

[Service]
ExecStart=/usr/local/bin/node_exporter
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now node-exporter

# -------------------------------------------------------------------
# Step 9: Run prometheus
# -------------------------------------------------------------------       
curl -sL "https://github.com/prometheus/prometheus/releases/download/v${PROMETHEUS_VERSION}/prometheus-${PROMETHEUS_VERSION}.linux-amd64.tar.gz" \
  | sudo tar -xz -C /usr/local/bin --strip-components=1 \
    "prometheus-${PROMETHEUS_VERSION}.linux-amd64/prometheus" \
    "prometheus-${PROMETHEUS_VERSION}.linux-amd64/promtool"

sudo mkdir -p /etc/prometheus
sudo tee /etc/prometheus/prometheus.yml > /dev/null <<EOF
global:
  scrape_interval: 1s
  scrape_timeout: 800ms

scrape_configs:
  - job_name: 'stargz-snapshotter'
    static_configs:
      - targets: ['127.0.0.1:8234']
  - job_name: 'node'
    static_configs:
      - targets: ['127.0.0.1:9100']
EOF

# 4. Create systemd service for Prometheus
sudo tee /etc/systemd/system/prometheus.service > /dev/null <<EOF
[Unit]
Description=Prometheus
After=network.target

[Service]
ExecStart=/usr/local/bin/prometheus --config.file=/etc/prometheus/prometheus.yml --storage.tsdb.path=/var/lib/prometheus
Restart=always

[Install]
WantedBy=multi-user.target
EOF


sudo systemctl daemon-reload
sudo systemctl enable --now prometheus
# verify
sleep 10
echo "--- stargz metrics endpoint ---"
curl -sf http://127.0.0.1:8234/metrics > /dev/null && echo "stargz metrics OK"

echo "--- Prometheus targets ---"
curl -sf http://localhost:9090/api/v1/targets \
  | grep -q '"health":"up"' && echo "Prometheus target health: up"

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
systemctl status node-exporter --no-pager

echo "✅ containerd + stargz snapshotter installed successfully"