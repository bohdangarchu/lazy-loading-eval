#!/bin/bash
set -euox pipefail
export REGISTRY_NODE="10.10.1.2"

for i in 1 2 3; do
    curl -s http://${REGISTRY_NODE}:5000/v2/experiment1-base/manifests/${i} -o /dev/null -w "%{http_code} experiment1-base:${i}\n"
done
curl -s http://${REGISTRY_NODE}:5000/v2/python/manifests/3.10-experiment1-esgz -o /dev/null -w "%{http_code} python:3.10-experiment1-esgz\n"
