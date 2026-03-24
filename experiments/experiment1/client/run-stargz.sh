#!/bin/bash
set -euox pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA_DIR="${SCRIPT_DIR}/.."

eval "$(python3 "${SCRIPT_DIR}/../../load-schema.py" ${SCHEMA_DIR}/schema.yaml)"

STARGZ_IMAGE="${REGISTRY_NODE}:5000/${IMG_STARGZ_NAME}:${IMG_STARGZ_TAG}"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] === STARGZ: ${STARGZ_IMAGE} ==="
bash ${SCRIPT_DIR}/clear-cache.sh
time sudo ctr-remote images rpull --plain-http "${STARGZ_IMAGE}" >/dev/null
time sudo ctr-remote run --rm --snapshotter=stargz "${STARGZ_IMAGE}" run-stargz python3 /main.py
