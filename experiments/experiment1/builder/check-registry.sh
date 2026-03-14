#!/bin/bash
set -euox pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA="${SCRIPT_DIR}/../schema.yaml"
export REGISTRY_NODE=$(python3 -c "import yaml; print(yaml.safe_load(open('${SCHEMA}'))['registry_node'])")

for i in 1 2 3; do
    curl -s http://${REGISTRY_NODE}:5000/v2/experiment1-base/manifests/${i} -o /dev/null -w "%{http_code} experiment1-base:${i}\n"
done
curl -s http://${REGISTRY_NODE}:5000/v2/python/manifests/3.10-experiment1-esgz -o /dev/null -w "%{http_code} python:3.10-experiment1-esgz\n"
