#!/usr/bin/env sh
set -eu

REGISTRY="registry:5000"
REPO="bert-split"

STANDARD_SRC="ghcr.io/bohdangarchu/bert-split:org"
STARGZ_SRC="ghcr.io/bohdangarchu/bert-split:esgz"

STANDARD_DST="$REGISTRY/$REPO:org"
STARGZ_DST="$REGISTRY/$REPO:esgz"

echo "==> Pulling STANDARD image (normal OCI)"
ctr image pull "$STANDARD_SRC"
ctr image tag "$STANDARD_SRC" "$STANDARD_DST"
ctr image push --plain-http "$STANDARD_DST"
ctr image rm "$STANDARD_SRC" "$STANDARD_DST" || true

echo "==> Copying STARGZ image WITHOUT materialization"
ctr-remote image copy --plain-http \
  "$STARGZ_SRC" \
  "$STARGZ_DST"

echo "==> Verifying STARGZ annotations"
ctr image inspect "$STARGZ_DST" | grep -i stargz

echo "==> DONE: registry populated correctly"
