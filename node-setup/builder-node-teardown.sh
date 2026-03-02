#!/bin/bash
set -euox pipefail

# -------------------------------------------------------------------
# Root check
# -------------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
  echo "❌ Please run as root"
  exit 1
fi

echo "▶ Tearing down builder node setup..."

# -------------------------------------------------------------------
# Step 1: Stop and disable systemd services
# -------------------------------------------------------------------
for svc in prometheus node-exporter buildkit containerd; do
  if systemctl is-active --quiet "$svc" 2>/dev/null; then
    systemctl stop "$svc" || true
  fi
  if systemctl is-enabled --quiet "$svc" 2>/dev/null; then
    systemctl disable "$svc" || true
  fi
done

# -------------------------------------------------------------------
# Step 2: Remove systemd service files
# -------------------------------------------------------------------
rm -f /etc/systemd/system/buildkit.service
rm -f /etc/systemd/system/node-exporter.service
rm -f /etc/systemd/system/prometheus.service
rm -f /usr/local/lib/systemd/system/containerd.service

systemctl daemon-reload
systemctl reset-failed || true

# -------------------------------------------------------------------
# Step 3: Remove binaries
# -------------------------------------------------------------------

# containerd binaries (extracted to /usr/local from tarball)
rm -f /usr/local/bin/containerd
rm -f /usr/local/bin/containerd-shim-runc-v2
rm -f /usr/local/bin/containerd-stress
rm -f /usr/local/bin/ctr

# runc
rm -f /usr/local/sbin/runc

# stargz binaries
rm -f /usr/local/bin/containerd-stargz-grpc
rm -f /usr/local/bin/ctr-remote

# node_exporter and prometheus
rm -f /usr/local/bin/node_exporter
rm -f /usr/local/bin/prometheus
rm -f /usr/local/bin/promtool

# nerdctl-full bundle (extracted to /usr/local)
rm -f /usr/local/bin/nerdctl
rm -f /usr/local/bin/buildkitd
rm -f /usr/local/bin/buildctl
rm -f /usr/local/bin/rootlesskit
rm -f /usr/local/bin/rootlesskit-docker-proxy
rm -f /usr/local/bin/slirp4netns
rm -f /usr/local/bin/bypass4netns
rm -f /usr/local/bin/bypass4netnsd

# -------------------------------------------------------------------
# Step 4: Remove CNI plugins
# -------------------------------------------------------------------
rm -rf /opt/cni/bin
rm -rf /opt/cni
rm -rf /usr/local/libexec/cni

# -------------------------------------------------------------------
# Step 5: Remove Go installation and caches
# -------------------------------------------------------------------
rm -rf /usr/local/go
rm -rf /usr/local/gopath
rm -rf /usr/local/gocache

# -------------------------------------------------------------------
# Step 6: Remove 2dfs-builder (repo + any binaries from install.sh)
# -------------------------------------------------------------------
rm -rf /opt/2dfs-builder

# install.sh in the 2dfs-builder repo may have placed a binary in
# /usr/local/bin — remove known candidates
rm -f /usr/local/bin/2dfs-builder
rm -f /usr/local/bin/2dfs
rm -f /usr/local/bin/tdfs

# -------------------------------------------------------------------
# Step 7: Remove configuration files
# -------------------------------------------------------------------
rm -rf /etc/containerd
rm -rf /etc/buildkit
rm -rf /etc/prometheus

# -------------------------------------------------------------------
# Step 8: Unmount active mounts, then remove runtime directories
# -------------------------------------------------------------------
for dir in /run/containerd /var/lib/containerd; do
  if mountpoint -q "$dir" 2>/dev/null || grep -qs "$dir" /proc/mounts; then
    awk -v base="$dir" '$2 ~ "^" base {print $2}' /proc/mounts \
      | sort -r \
      | xargs -r umount -l 2>/dev/null || true
  fi
done

rm -rf /var/lib/containerd
rm -rf /var/lib/prometheus
rm -rf /run/containerd
rm -rf /run/buildkit

# -------------------------------------------------------------------
# Step 9: Remove nerdctl-full lib artifacts
# -------------------------------------------------------------------
rm -rf /usr/local/lib/nerdctl

# -------------------------------------------------------------------
# Step 10: Remove apt-installed packages
# -------------------------------------------------------------------
apt-get remove -y pigz || true

echo "✅ Builder node teardown complete"
