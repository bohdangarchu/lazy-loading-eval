#!/bin/bash
set -euox pipefail

# Usage: ./run-remote.sh <client_host>
# Example: ./run-remote.sh amd109.utah.cloudlab.us

CLIENT_HOST="$1"
SSH_KEY="${HOME}/.ssh/id_ed25519_cloudlab"
SSH_USER="bgarchu"
REMOTE_SCRIPT_DIR="/users/bgarchu/lazy-loading-eval/experiments/experiment1/client"

SSH="ssh -i ${SSH_KEY} -o StrictHostKeyChecking=no -o ConnectTimeout=10 ${SSH_USER}@${CLIENT_HOST}"

wait_for_ssh() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Waiting for ${CLIENT_HOST} to come back up..."
    sleep 30
    until $SSH "echo ok" &>/dev/null; do
        sleep 10
    done
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ${CLIENT_HOST} is back up"
    sleep 10  # let services fully start
}

run_mode() {
    local mode="$1"
    local cmd="$2"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Running mode: ${mode} ==="
    $SSH "cd ${REMOTE_SCRIPT_DIR} && ${cmd}"
}

reboot_client() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Rebooting ${CLIENT_HOST}..."
    $SSH "sudo reboot" || true
    wait_for_ssh
}

SCHEMA_DIR="${REMOTE_SCRIPT_DIR}/.."
LOAD_SCHEMA="eval \"\$(python3 /users/bgarchu/lazy-loading-eval/experiments/load-schema.py ${SCHEMA_DIR}/schema.yaml)\""

CLEAR_CACHE="${REMOTE_SCRIPT_DIR}/clear-cache.sh"

reboot_client

# --- BASE ---
run_mode "BASE" "bash -c '
    set -euox pipefail
    ${LOAD_SCHEMA}
    BASE_IMAGE=\"\${REGISTRY_NODE}:5000/\${IMG_BASE_NAME}:\$((REFRESH_INDEX + 1))\"
    echo \"[\$(date +\"%Y-%m-%d %H:%M:%S\")] === BASE: \${BASE_IMAGE} ===\"
    bash ${CLEAR_CACHE}
    time sudo ctr images pull --plain-http \"\${BASE_IMAGE}\" >/dev/null
    time sudo ctr run --rm \"\${BASE_IMAGE}\" run-base python3 /main.py
'"

reboot_client

# --- 2DFS + STARGZ ---
run_mode "2DFS+STARGZ" "bash -c '
    set -euox pipefail
    ${LOAD_SCHEMA}
    TDFS_STARGZ_IMAGE=\"\${REGISTRY_NODE}:5000/\${IMG_2DFS_STARGZ_PATH}:\${IMG_2DFS_STARGZ_TAG}--0.0.0.\${REFRESH_INDEX}\"
    echo \"[\$(date +\"%Y-%m-%d %H:%M:%S\")] === 2DFS + STARGZ: \${TDFS_STARGZ_IMAGE} ===\"
    bash ${CLEAR_CACHE}
    time sudo ctr-remote images rpull --plain-http --use-containerd-labels \"\${TDFS_STARGZ_IMAGE}\"
    time sudo ctr-remote run --rm --snapshotter=stargz \"\${TDFS_STARGZ_IMAGE}\" run-2dfs-stargz python3 /main.py
'"

reboot_client

# --- STARGZ ---
run_mode "STARGZ" "bash -c '
    set -euox pipefail
    ${LOAD_SCHEMA}
    STARGZ_IMAGE=\"\${REGISTRY_NODE}:5000/\${IMG_STARGZ_NAME}:\${IMG_STARGZ_TAG}\"
    echo \"[\$(date +\"%Y-%m-%d %H:%M:%S\")] === STARGZ: \${STARGZ_IMAGE} ===\"
    bash ${CLEAR_CACHE}
    time sudo ctr-remote images rpull --plain-http \"\${STARGZ_IMAGE}\"
    time sudo ctr-remote run --rm --snapshotter=stargz \"\${STARGZ_IMAGE}\" run-stargz python3 /main.py
'"

reboot_client

# --- 2DFS ---
run_mode "2DFS" "bash -c '
    set -euox pipefail
    ${LOAD_SCHEMA}
    TDFS_IMAGE=\"\${REGISTRY_NODE}:5000/\${IMG_2DFS_PATH}:\${IMG_2DFS_TAG}--0.0.0.\${REFRESH_INDEX}\"
    echo \"[\$(date +\"%Y-%m-%d %H:%M:%S\")] === 2DFS: \${TDFS_IMAGE} ===\"
    bash ${CLEAR_CACHE}
    time sudo ctr images pull --plain-http \"\${TDFS_IMAGE}\"
    time sudo ctr run --rm \"\${TDFS_IMAGE}\" run-2dfs python3 /main.py
'"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] All modes complete"
