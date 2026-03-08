#!/bin/bash
set -euo pipefail

export REGISTRY_NODE="10.10.1.2"

build_image() {
    local i=$1
    local IMAGE="${REGISTRY_NODE}:5000/experiment1-base:${i}"
    local log="build-base-${i}.log"
    local start
    start=$(date +%s)
    echo "[build ${i}] starting (log: ${log})"
    buildctl build \
        --frontend dockerfile.v0 \
        --opt filename="Dockerfile.base.${i}" \
        --local context=. \
        --local dockerfile=. \
        --output type=image,name="${IMAGE}",push=true,registry.insecure=true \
        >"${log}" 2>&1
    local end
    end=$(date +%s)
    echo "[build ${i}] done in $(( end - start ))s"
}

total_start=$(date +%s)

pids=()
for i in 1 2 3; do
    build_image "$i" &
    pids+=($!)
done

failed=0
for pid in "${pids[@]}"; do
    wait "$pid" || failed=$(( failed + 1 ))
done

total_end=$(date +%s)
echo "Total time: $(( total_end - total_start ))s"

for i in 1 2 3; do
    echo ""
    echo "=== build-base-${i}.log ==="
    cat "build-base-${i}.log"
done

if [ "$failed" -gt 0 ]; then
    echo "ERROR: ${failed} build(s) failed"
    exit 1
fi
