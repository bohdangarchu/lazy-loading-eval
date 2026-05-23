#!/bin/bash
set -euox pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

sync_one() {
    local src="$1"
    local dst="$2"
    mkdir -p "${dst}"
    rsync -av "${src}" "${dst}"
}

sync_one \
    "${REPO_ROOT}/benchmark/build_performance/results/" \
    "${REPO_ROOT}/../bohdanresults/data/build_performance/"

sync_one \
    "${REPO_ROOT}/benchmark/pull_performance/results/" \
    "${REPO_ROOT}/../bohdanresults/data/pull_performance/"
