#!/bin/bash
set -euox pipefail

# -------------------------------------------------------------------
# Root check
# -------------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
  echo "❌ Please run as root"
  exit 1
fi

echo "▶ Tearing down client node setup..."

# -------------------------------------------------------------------
# Step 1: Stop and disable systemd services
# -------------------------------------------------------------------
for svc in prometheus node-exporter stargz-snapshotter buildkit containerd; do
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
rm -f /etc/systemd/system/prometheus.service
rm -f /etc/systemd/system/node-exporter.service
rm -f /etc/systemd/system/buildkit.service
rm -f /etc/systemd/system/stargz-snapshotter.service
rm -f /usr/local/lib/systemd/system/containerd.service

systemctl daemon-reload
systemctl reset-failed || true

# -------------------------------------------------------------------
# Step 3: Remove binaries installed by setup
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

# stargz source repo (if built from source)
rm -rf /opt/stargz-snapshotter

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

# Also remove any CNI plugins from nerdctl-full
rm -rf /usr/local/libexec/cni

# -------------------------------------------------------------------
# Step 5: Remove Go installation
# -------------------------------------------------------------------
rm -rf /usr/local/go

# -------------------------------------------------------------------
# Step 6: Remove configuration files
# -------------------------------------------------------------------
rm -rf /etc/containerd
rm -rf /etc/containerd-stargz-grpc
rm -rf /etc/prometheus

# -------------------------------------------------------------------
# Step 7: Unmount any active mounts, then remove runtime directories
# -------------------------------------------------------------------

# Unmount anything still mounted under these directories (reverse order,
# deepest paths first so parent mounts can be freed afterwards).
for dir in /run/containerd /var/lib/containerd /var/lib/containerd-stargz-grpc; do
  if mountpoint -q "$dir" 2>/dev/null || grep -qs "$dir" /proc/mounts; then
    # List all mount points under the directory, deepest first, and unmount them.
    awk -v base="$dir" '$2 ~ "^" base {print $2}' /proc/mounts \
      | sort -r \
      | xargs -r umount -l 2>/dev/null || true
  fi
done

rm -rf /var/lib/containerd
rm -rf /var/lib/containerd-stargz-grpc
rm -rf /var/lib/prometheus
rm -rf /run/containerd
rm -rf /run/containerd-stargz-grpc
rm -rf /run/buildkit

# -------------------------------------------------------------------
# Step 8: Remove nerdctl-full lib artifacts
# -------------------------------------------------------------------
rm -rf /usr/local/lib/nerdctl

# -------------------------------------------------------------------
# Step 9: Unload fuse kernel module and remove package
# -------------------------------------------------------------------
modprobe -r fuse 2>/dev/null || true
apt-get remove -y fuse 2>/dev/null || true

echo "✅ Client node teardown complete"
