#!/bin/bash
set -euox pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

SRC="${REPO_ROOT}/benchmark/build_performance/results/"
DST="${REPO_ROOT}/../results/data/build_performance/"

mkdir -p "${DST}"
rsync -av "${SRC}" "${DST}"
