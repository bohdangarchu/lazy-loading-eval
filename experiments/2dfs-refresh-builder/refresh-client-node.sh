#!/bin/bash
set -eux

REGISTRY="10.10.1.2:5000"
OLD_HASH=
NEW_HASH=
OLD_LAYER=
NEW_LAYER=
ctr-remote i rpull --plain-http ${REGISTRY}/library/python-2dfs:v1--0.0.0.1
ctr run -d --snapshotter=stargz ${REGISTRY}/library/python-2dfs:v1--0.0.0.1 test-ctr sh -c 'sleep infinity'
echo "Expected hash in container (v1): $OLD_HASH"
ctr task exec --exec-id=check1 test-ctr sha256sum /big_file2

ctr-remote refresh-layer "sha256:$OLD_LAYER" "sha256:$NEW_LAYER"

echo "Expected hash in container after refresh (v2): $NEW_HASH"
ctr task exec --exec-id=check2 test-ctr sha256sum /big_file2