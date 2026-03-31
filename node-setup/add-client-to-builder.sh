#!/bin/bash
set -euox pipefail

# -------------------------------------------------------------------
# Adds client-side capabilities (stargz pull/run) to a node that
# already has the builder setup.  Run as root.
#
# Usage: sudo ./add-client-to-builder.sh <REGISTRY_IP>
# Example: sudo ./add-client-to-builder.sh 10.10.1.2
# -------------------------------------------------------------------

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <REGISTRY_IP>"
  exit 1
fi

REGISTRY_NODE="$1"

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root"
  exit 1
fi

# -------------------------------------------------------------------
# Step 1: Install FUSE (required for stargz snapshotter)
# -------------------------------------------------------------------
apt-get update
apt-get install -y fuse
modprobe fuse

# -------------------------------------------------------------------
# Step 2: Install stargz-snapshotter systemd service
# -------------------------------------------------------------------
curl -Lo /etc/systemd/system/stargz-snapshotter.service \
  https://raw.githubusercontent.com/containerd/stargz-snapshotter/main/script/config/etc/systemd/system/stargz-snapshotter.service

# -------------------------------------------------------------------
# Step 3: Configure stargz snapshotter
# -------------------------------------------------------------------
mkdir -p /etc/containerd-stargz-grpc

cat > /etc/containerd-stargz-grpc/config.toml <<EOF
noprefetch = true
no_background_fetch = true
disable_verification = true
prefetch_async_size = 1
metrics_address = "127.0.0.1:8234"

[[resolver.host."${REGISTRY_NODE}:5000".mirrors]]
host = "${REGISTRY_NODE}:5000"
insecure = true
EOF

# -------------------------------------------------------------------
# Step 4: Reconfigure containerd with stargz proxy plugin
# -------------------------------------------------------------------
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

# -------------------------------------------------------------------
# Step 5: Move containerd + stargz storage to /mydata
# -------------------------------------------------------------------
systemctl stop containerd stargz-snapshotter 2>/dev/null || true

if [[ ! -L /var/lib/containerd ]]; then
  mkdir -p /mydata/containerd
  if [[ -d /var/lib/containerd ]]; then
    cp -a /var/lib/containerd/* /mydata/containerd/ 2>/dev/null || true
    rm -rf /var/lib/containerd
  fi
  ln -s /mydata/containerd /var/lib/containerd
fi

if [[ ! -L /var/lib/containerd-stargz-grpc ]]; then
  mkdir -p /mydata/containerd-stargz-grpc
  if [[ -d /var/lib/containerd-stargz-grpc ]]; then
    cp -a /var/lib/containerd-stargz-grpc/* /mydata/containerd-stargz-grpc/ 2>/dev/null || true
    rm -rf /var/lib/containerd-stargz-grpc
  fi
  ln -s /mydata/containerd-stargz-grpc /var/lib/containerd-stargz-grpc
fi

# -------------------------------------------------------------------
# Step 6: Start services
# -------------------------------------------------------------------
systemctl daemon-reload
systemctl enable --now stargz-snapshotter
systemctl restart containerd
systemctl restart buildkit

# -------------------------------------------------------------------
# Step 7: Add stargz scrape job to prometheus
# -------------------------------------------------------------------
cat > /etc/prometheus/prometheus.yml <<EOF
global:
  scrape_interval: 1s
  scrape_timeout: 800ms
  external_labels:
    host: $(hostname)

scrape_configs:
  - job_name: 'stargz-snapshotter'
    static_configs:
      - targets: ['127.0.0.1:8234']
  - job_name: 'node'
    static_configs:
      - targets: ['127.0.0.1:9100']
EOF

promtool check config /etc/prometheus/prometheus.yml
systemctl restart prometheus

# -------------------------------------------------------------------
# Verify
# -------------------------------------------------------------------
sleep 3
systemctl status stargz-snapshotter --no-pager
echo "--- stargz metrics endpoint ---"
curl -sf http://127.0.0.1:8234/metrics > /dev/null && echo "stargz metrics OK"

echo "--- Prometheus targets ---"
curl -sf http://localhost:9090/api/v1/targets \
  | grep -q '"health":"up"' && echo "Prometheus target health: up"

echo "Done -- client capabilities added to builder node"
