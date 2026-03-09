#!/bin/bash
set -euox pipefail

nerdctl builder prune -a --force
buildctl prune --all
nerdctl image prune -a --force
ctr content ls | awk 'NR>1 {print $1}' | xargs -r ctr content rm
ctr snapshots rm $(ctr snapshots ls | awk 'NR>1 {print $2}') || true
ctr -n buildkit content ls | awk 'NR>1 {print $1}' | xargs -r ctr -n buildkit content rm
ctr -n buildkit snapshots rm $(ctr -n buildkit snapshots ls | awk 'NR>1 {print $2}') || true
nerdctl volume prune --force
rm -rf ~/.2dfs/blobs/* \
       ~/.2dfs/uncompressed-keys/* \
       ~/.2dfs/index/*