#!/bin/bash
set -euox pipefail

export REGISTRY_NODE=$(python3 -c "import yaml; print(yaml.safe_load(open('${SCHEMA}'))['registry_node'])")
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- base images (parallel) ---
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting build-base"

build_base_image() {
    local i=$1
    local IMAGE="${REGISTRY_NODE}:5000/experiment2-base:${i}"
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

indices=($(ls "${SCRIPT_DIR}"/Dockerfile.base.* | grep -oP '\d+$' | sort -n))
total_start=$(date +%s)
pids=()
for i in "${indices[@]}"; do
    build_base_image "$i" &
    pids+=($!)
done
failed=0
for pid in "${pids[@]}"; do
    wait "$pid" || failed=$(( failed + 1 ))
done
total_end=$(date +%s)
echo "Total base build time: $(( total_end - total_start ))s"
for i in "${indices[@]}"; do
    echo ""
    echo "=== build-base-${i}.log ==="
    cat "build-base-${i}.log"
done
if [ "$failed" -gt 0 ]; then
    echo "ERROR: ${failed} base build(s) failed"
    exit 1
fi

# --- 2dfs ---
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting build-2dfs"
time tdfs build --platforms linux/amd64 --enable-stargz --force-http \
    ${REGISTRY_NODE}:5000/python:3.10-esgz \
    ${REGISTRY_NODE}:5000/python:3.10-experiment2-2dfs
tdfs image push --force-http ${REGISTRY_NODE}:5000/python:3.10-experiment2-2dfs

# --- stargz ---
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting build-stargz"
time buildctl build \
    --frontend dockerfile.v0 \
    --opt filename=Dockerfile.stargz \
    --local context=. \
    --local dockerfile=. \
    --output type=image,name=${REGISTRY_NODE}:5000/python:3.10-experiment2-esgz,push=true,compression=estargz,oci-mediatypes=true,registry.insecure=true
