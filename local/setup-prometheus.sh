#!/usr/bin/env bash
set -euo pipefail

PROMETHEUS_VERSION=3.9.1
ARCH=amd64

echo "installing prometheus v${PROMETHEUS_VERSION}..."

TMP=$(mktemp -d)
curl -sL "https://github.com/prometheus/prometheus/releases/download/v${PROMETHEUS_VERSION}/prometheus-${PROMETHEUS_VERSION}.linux-${ARCH}.tar.gz" \
    | tar -xz -C "$TMP" \
        "prometheus-${PROMETHEUS_VERSION}.linux-${ARCH}/prometheus" \
        "prometheus-${PROMETHEUS_VERSION}.linux-${ARCH}/promtool"

sudo mv "$TMP/prometheus-${PROMETHEUS_VERSION}.linux-${ARCH}/prometheus" /usr/local/bin/prometheus
sudo mv "$TMP/prometheus-${PROMETHEUS_VERSION}.linux-${ARCH}/promtool" /usr/local/bin/promtool
rm -rf "$TMP"

echo "prometheus $(prometheus --version 2>&1 | head -1) installed"
