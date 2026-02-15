#!/bin/bash
set -eux

# Build and push plain images
nerdctl build --build-arg CONTENT="hello-v1" -t registry:5000/test:v1 -f Dockerfile.test .
nerdctl build --build-arg CONTENT="hello-v2" -t registry:5000/test:v2 -f Dockerfile.test .

# Convert to eStargz (local only)
ctr-remote images convert --estargz --oci registry:5000/test:v1 registry:5000/test:v1-esgz
ctr-remote images convert --estargz --oci registry:5000/test:v2 registry:5000/test:v2-esgz

# Push optimized images to registry
nerdctl push registry:5000/test:v1-esgz
nerdctl push registry:5000/test:v2-esgz

# Get layer digests
OLD_LAYER=$(nerdctl manifest inspect registry:5000/test:v1-esgz | jq -r '.layers[-1].digest')
NEW_BLOB=$(nerdctl manifest inspect registry:5000/test:v2-esgz | jq -r '.layers[-1].digest')

# rm local images
nerdctl rmi -f $(nerdctl images -q)

# Pull v1 lazily
ctr-remote images rpull --plain-http registry:5000/test:v1-esgz

# Run container
ctr run -d --snapshotter=stargz registry:5000/test:v1-esgz test-ctr sh -c 'while true; do cat /data.txt; sleep 2; done'

# Verify v1
ctr task exec --exec-id=check1 test-ctr cat /data.txt

# Refresh layer
ctr-remote refresh-layer $OLD_LAYER $NEW_BLOB

# Verify v2
ctr task exec --exec-id=check2 test-ctr cat /data.txt