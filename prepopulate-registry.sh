#!/usr/bin/env sh
set -eu

REGISTRY="registry:5000"
REPO="bert-split"

SRC="ghcr.io/bohdangarchu/bert-split:esgz"
DST="${REGISTRY}/${REPO}:esgz"

echo "==> Waiting for registry..."
until curl -fsS "http://${REGISTRY}/v2/" >/dev/null; do
  sleep 0.2
done

echo "==> Starting containerd"
/usr/local/bin/containerd &

# wait for containerd socket
until ctr version >/dev/null 2>&1; do
  sleep 0.2
done

echo "==> Pulling STARGZ image (full blobs, no unpack)"
ctr-remote image pull "${SRC}"

echo "==> Tagging image for local registry"
ctr image tag "${SRC}" "${DST}"

echo "==> Pushing image to local registry (plain HTTP)"
ctr image push --plain-http "${DST}"

echo "==> DONE: registry populated"
