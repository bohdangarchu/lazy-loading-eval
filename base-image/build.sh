#!/bin/bash
set -euox pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

LOCAL_IMAGE="base-image:3.12-torch"
REMOTE_IMAGE="ghcr.io/bohdangarchu/python:3.12-torch-esgz"

echo "${GH_TOKEN:?GH_TOKEN env var required}" | sudo nerdctl login ghcr.io -u bohdangarchu --password-stdin

sudo nerdctl build -t "${LOCAL_IMAGE}" "${SCRIPT_DIR}"

sudo ctr-remote images convert --estargz --oci "docker.io/library/${LOCAL_IMAGE}" "${REMOTE_IMAGE}"

sudo nerdctl push "${REMOTE_IMAGE}"
