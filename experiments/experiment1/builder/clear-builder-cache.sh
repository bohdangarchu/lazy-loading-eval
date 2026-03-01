#!/bin/bash
set -euox pipefail

nerdctl builder prune -a --force
buildctl prune --all
nerdctl image prune -a --force
ctr content ls | awk 'NR>1 {print $1}' | xargs ctr content rm
ctr snapshots rm $(ctr snapshots ls | awk 'NR>1 {print $2}')
nerdctl volume prune --force