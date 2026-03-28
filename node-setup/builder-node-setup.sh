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
NERDCTL_VERSION="2.2.1"
STARGZ_VERSION="0.18.2"
GO_VERSION="1.24.0"
NODE_EXPORTER_VERSION="1.8.2"
PROMETHEUS_VERSION="3.9.1"
GRAFANA_PROM_URL="https://prometheus-prod-65-prod-eu-west-2.grafana.net/api/prom/push"
GRAFANA_PROM_USER="3041204"

ARCH="amd64"
OS="linux"

# -------------------------------------------------------------------
# Root check
# -------------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
  echo "Please run as root"
  exit 1
fi

if [[ -z "${GRAFANA_API_KEY:-}" ]]; then
  echo "Warning: GRAFANA_API_KEY not set — Grafana remote_write will be disabled"
fi

echo "▶ Installing containerd=${CONTAINERD_VERSION}, runc=${RUNC_VERSION}, cni=${CNI_VERSION}, nerdctl=${NERDCTL_VERSION}, stargz=${STARGZ_VERSION}"

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
# Step 3: Python3
# -------------------------------------------------------------------
apt-get update
apt-get install -y python3 python3-pip python3.12-venv

# -------------------------------------------------------------------
# Step 5: CNI plugins
# -------------------------------------------------------------------
curl -LO "https://github.com/containernetworking/plugins/releases/download/v${CNI_VERSION}/cni-plugins-${OS}-${ARCH}-v${CNI_VERSION}.tgz"
mkdir -p /opt/cni/bin
tar Cxzvf /opt/cni/bin "cni-plugins-${OS}-${ARCH}-v${CNI_VERSION}.tgz"

# -------------------------------------------------------------------
# Step 6: nerdctl (full) + bundled dependencies (includes buildkitd)
# -------------------------------------------------------------------
curl -LO "https://github.com/containerd/nerdctl/releases/download/v${NERDCTL_VERSION}/nerdctl-full-${NERDCTL_VERSION}-${OS}-${ARCH}.tar.gz"
tar -C /usr/local -xvf "nerdctl-full-${NERDCTL_VERSION}-${OS}-${ARCH}.tar.gz"

# -------------------------------------------------------------------
# Step 7: BuildKit daemon
# -------------------------------------------------------------------
cat > /etc/systemd/system/buildkit.service <<'EOF'
[Unit]
Description=BuildKit
Documentation=https://github.com/moby/buildkit
After=containerd.service
Requires=containerd.service

[Service]
Environment=TMPDIR=/mydata/tmp
ExecStart=/usr/local/bin/buildkitd \
  --addr unix:///run/buildkit/buildkitd.sock \
  --containerd-worker=true \
  --containerd-worker-addr=/run/containerd/containerd.sock \
  --root /mydata/buildkit
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

mkdir -p /etc/systemd/system/containerd.service.d
cat > /etc/systemd/system/containerd.service.d/override.conf <<'EOF'
[Service]
Environment=TMPDIR=/mydata/tmp
EOF

mkdir -p /etc/buildkit
tee /etc/buildkit/buildkitd.toml <<EOF
[registry."${REGISTRY_NODE}:5000"]
  http = true
  insecure = true
EOF

mkdir -p /run/buildkit
systemctl daemon-reload
systemctl enable --now buildkit

# -------------------------------------------------------------------
# Step 8: Configure containerd for insecure registry
# -------------------------------------------------------------------
mkdir -p /etc/containerd

cat > /etc/containerd/config.toml <<EOF
version = 2

[plugins."io.containerd.grpc.v1.cri".registry.mirrors]
  [plugins."io.containerd.grpc.v1.cri".registry.mirrors."${REGISTRY_NODE}:5000"]
    endpoint = ["http://${REGISTRY_NODE}:5000"]
EOF

systemctl restart containerd

# -------------------------------------------------------------------
# Step 8b: Mirror base image to local registry
# -------------------------------------------------------------------
BASE_IMAGE="ghcr.io/bohdangarchu/python:3.10-esgz"
LOCAL_BASE_IMAGE="${REGISTRY_NODE}:5000/python:3.10-esgz"
LOCAL_BASE_IMAGE_2DFS="${REGISTRY_NODE}:5000/library/python:3.10-esgz"

nerdctl pull "$BASE_IMAGE"
nerdctl tag "$BASE_IMAGE" "$LOCAL_BASE_IMAGE"
nerdctl tag "$BASE_IMAGE" "$LOCAL_BASE_IMAGE_2DFS"
nerdctl push --insecure-registry "$LOCAL_BASE_IMAGE"
nerdctl push --insecure-registry "$LOCAL_BASE_IMAGE_2DFS"

# BASE_IMAGE="ghcr.io/bohdangarchu/python:3.12-torch-esgz"
# LOCAL_BASE_IMAGE="${REGISTRY_NODE}:5000/python:3.12-torch-esgz"
# LOCAL_BASE_IMAGE_2DFS="${REGISTRY_NODE}:5000/library/python:3.12-torch-esgz"

# nerdctl pull "$BASE_IMAGE"
# nerdctl tag "$BASE_IMAGE" "$LOCAL_BASE_IMAGE"
# nerdctl tag "$BASE_IMAGE" "$LOCAL_BASE_IMAGE_2DFS"
# nerdctl push --insecure-registry "$LOCAL_BASE_IMAGE"
# nerdctl push --insecure-registry "$LOCAL_BASE_IMAGE_2DFS"

# -------------------------------------------------------------------
# Step 9: Install Go (required for 2dfs builder)
# -------------------------------------------------------------------
curl -LO "https://go.dev/dl/go${GO_VERSION}.${OS}-${ARCH}.tar.gz"
rm -rf /usr/local/go
tar -C /usr/local -xzf "go${GO_VERSION}.${OS}-${ARCH}.tar.gz"
export PATH="/usr/local/go/bin:$PATH"

# -------------------------------------------------------------------
# Step 10: Install stargz-snapshotter binaries (containerd-stargz-grpc, ctr-remote)
# -------------------------------------------------------------------
if [[ -n "$STARGZ_REPO_URL" ]]; then
  STARGZ_REPO_DIR="$(mktemp -d)"
  git clone "$STARGZ_REPO_URL" "$STARGZ_REPO_DIR"
  make -C "$STARGZ_REPO_DIR"
  install -m 755 "$STARGZ_REPO_DIR/out/containerd-stargz-grpc" /usr/local/bin/containerd-stargz-grpc
  install -m 755 "$STARGZ_REPO_DIR/out/ctr-remote" /usr/local/bin/ctr-remote
  rm -rf "$STARGZ_REPO_DIR"
else
  curl -LO "https://github.com/containerd/stargz-snapshotter/releases/download/v${STARGZ_VERSION}/stargz-snapshotter-${STARGZ_VERSION}-${OS}-${ARCH}.tar.gz"
  tar -C /usr/local/bin -xvf \
    "stargz-snapshotter-${STARGZ_VERSION}-${OS}-${ARCH}.tar.gz" \
    containerd-stargz-grpc ctr-remote
fi

# -------------------------------------------------------------------
# Step 11: Install 2DFS builder
# -------------------------------------------------------------------
export GOPATH=/usr/local/gopath
export GOCACHE=/usr/local/gocache
export GOTOOLCHAIN=local

BUILDER_DIR="/opt/2dfs-builder"
rm -rf "$BUILDER_DIR"
git clone https://github.com/bohdangarchu/2dfs-builder.git "$BUILDER_DIR"
cd "$BUILDER_DIR"
bash install.sh

# -------------------------------------------------------------------
# Step 12: libs + buildah
# -------------------------------------------------------------------
sudo apt update
sudo apt install -y pigz gzip "buildah=1.33.7+ds1-1ubuntu0.24.04.3"

# Configure buildah to allow insecure registry
mkdir -p /etc/containers/registries.conf.d
cat > /etc/containers/registries.conf.d/insecure.conf <<EOF
[[registry]]
location = "${REGISTRY_NODE}:5000"
insecure = true
EOF

# -------------------------------------------------------------------
# Step 13: Install node_exporter
# -------------------------------------------------------------------
curl -LO "https://github.com/prometheus/node_exporter/releases/download/v${NODE_EXPORTER_VERSION}/node_exporter-${NODE_EXPORTER_VERSION}.${OS}-${ARCH}.tar.gz"
tar -xzf "node_exporter-${NODE_EXPORTER_VERSION}.${OS}-${ARCH}.tar.gz"
install -m 755 "node_exporter-${NODE_EXPORTER_VERSION}.${OS}-${ARCH}/node_exporter" /usr/local/bin/node_exporter

cat > /etc/systemd/system/node-exporter.service <<'EOF'
[Unit]
Description=Prometheus Node Exporter
After=network.target

[Service]
ExecStart=/usr/local/bin/node_exporter
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now node-exporter

# -------------------------------------------------------------------
# Step 14: Install Prometheus (scrapes node_exporter)
# -------------------------------------------------------------------
curl -sL "https://github.com/prometheus/prometheus/releases/download/v${PROMETHEUS_VERSION}/prometheus-${PROMETHEUS_VERSION}.linux-amd64.tar.gz" \
  | tar -xz -C /usr/local/bin --strip-components=1 \
    "prometheus-${PROMETHEUS_VERSION}.linux-amd64/prometheus" \
    "prometheus-${PROMETHEUS_VERSION}.linux-amd64/promtool"

mkdir -p /etc/prometheus /var/lib/prometheus

cat > /etc/prometheus/prometheus.yml <<EOF
global:
  scrape_interval: 1s
  scrape_timeout: 800ms
  external_labels:
    host: $(hostname)

scrape_configs:
  - job_name: 'node'
    static_configs:
      - targets: ['127.0.0.1:9100']

EOF

if [[ -n "${GRAFANA_API_KEY:-}" ]]; then
  cat >> /etc/prometheus/prometheus.yml <<EOF
remote_write:
  - url: ${GRAFANA_PROM_URL}
    basic_auth:
      username: "${GRAFANA_PROM_USER}"
      password: "${GRAFANA_API_KEY}"
EOF
fi

cat > /etc/systemd/system/prometheus.service <<'EOF'
[Unit]
Description=Prometheus
After=network.target

[Service]
ExecStart=/usr/local/bin/prometheus --config.file=/etc/prometheus/prometheus.yml --storage.tsdb.path=/var/lib/prometheus
Restart=always

[Install]
WantedBy=multi-user.target
EOF

promtool check config /etc/prometheus/prometheus.yml

systemctl daemon-reload
systemctl enable --now prometheus

# -------------------------------------------------------------------
# Cleanup
# -------------------------------------------------------------------
cd /
rm -rf "$TMP_DIR"

# -------------------------------------------------------------------
# Set TMPDIR to /mydata for large temp files (e.g. stargz builds)
# -------------------------------------------------------------------
mkdir -p /mydata/tmp /mydata/buildkit
echo 'export TMPDIR=/mydata/tmp' >> /root/.bashrc
# TODO: dooesn't influence current session if run with sudo
export TMPDIR=/mydata/tmp

mkdir -p /mydata/.2dfs
ln -s /mydata/.2dfs ~/.2dfs

# -------------------------------------------------------------------
# Verification
# -------------------------------------------------------------------
containerd --version
nerdctl --version
buildkitd --version
runc --version
ctr-remote --help | head -n 5
tdfs version
tdfs-old version
buildah --version
systemctl status buildkit --no-pager
systemctl status node-exporter --no-pager
systemctl status prometheus --no-pager

echo "✅ Builder node setup complete"
