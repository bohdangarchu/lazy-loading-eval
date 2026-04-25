#!/bin/bash
set -euox pipefail

REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"

find "$REPO_ROOT" \( -name '*.bin' -o -name '*.safetensors' \) -type f -print -delete
