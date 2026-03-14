#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

rm -f "${SCRIPT_DIR}"/*.safetensors

# shared (both scripts)
rm -f "${SCRIPT_DIR}/2dfs.json"
rm -f "${SCRIPT_DIR}/Dockerfile.stargz"
rm -f "${SCRIPT_DIR}"/Dockerfile.base.*
rm -f "${SCRIPT_DIR}"/*.bin

# huggingface_hub download cache (created by prepare.py local_dir)
rm -rf "${SCRIPT_DIR}/.cache"

# build logs
rm -f "${SCRIPT_DIR}"/*.log
