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
    local script="$2"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Running mode: ${mode} ==="
    $SSH "bash ${REMOTE_SCRIPT_DIR}/${script}"
}

reboot_client() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Rebooting ${CLIENT_HOST}..."
    $SSH "sudo reboot" || true
    wait_for_ssh
}

reboot_client
run_mode "BASE"        "run-base.sh"
reboot_client
run_mode "2DFS+STARGZ" "run-2dfs-stargz.sh"
reboot_client
run_mode "STARGZ"      "run-stargz.sh"
reboot_client
run_mode "2DFS"        "run-2dfs.sh"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] All modes complete"
