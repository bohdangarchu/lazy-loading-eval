#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# prepare.py
rm -f "${SCRIPT_DIR}/model-00001-of-00002.safetensors"
rm -f "${SCRIPT_DIR}/model-00002-of-00002.safetensors"

# prepare2.py
rm -f "${SCRIPT_DIR}/distilbert-base-uncased.safetensors"
rm -f "${SCRIPT_DIR}/distilgpt2.safetensors"
rm -f "${SCRIPT_DIR}/flan-t5-small.safetensors"

# shared (both scripts)
rm -f "${SCRIPT_DIR}/2dfs.json"
rm -f "${SCRIPT_DIR}/Dockerfile.stargz"
rm -f "${SCRIPT_DIR}/Dockerfile.base.1"
rm -f "${SCRIPT_DIR}/Dockerfile.base.2"
rm -f "${SCRIPT_DIR}/Dockerfile.base.3"

# huggingface_hub download cache (created by prepare.py local_dir)
rm -rf "${SCRIPT_DIR}/.cache"
