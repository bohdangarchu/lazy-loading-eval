#!/bin/bash
set -eux

get_allotment_field() {
    local image_url="$1" row="$2" col="$3" field="$4"
    local index_hash manifest_digest field_digest

    index_hash=$(echo -n "$image_url" | sha256sum | awk '{print $1}')
    manifest_digest=$(jq -r '.manifests[0].digest | sub("sha256:"; "")' ~/.2dfs/index/"$index_hash")
    field_digest=$(jq -r '.layers[] | select(.mediaType | contains("2dfs.field")) | .digest | sub("sha256:"; "")' ~/.2dfs/blobs/"$manifest_digest")
    jq -r --argjson row "$row" --argjson col "$col" \
        '.rows[$row].allotments[] | select(.col == $col) | .'"$field" \
        ~/.2dfs/blobs/"$field_digest"
}

OLD_HASH=$(sha256sum big_file2 | awk '{print $1}')

tdfs build --platforms linux/amd64 --enable-stargz ghcr.io/bohdangarchu/python:3.10-esgz registry:5000/python-2dfs:v1

FILE_SIZE=$(stat -c%s big_file2)
dd if=/dev/urandom of=big_file2 bs="$FILE_SIZE" count=1

NEW_HASH=$(sha256sum big_file2 | awk '{print $1}')

tdfs build --platforms linux/amd64 --enable-stargz ghcr.io/bohdangarchu/python:3.10-esgz registry:5000/python-2dfs:v2

OLD_LAYER=$(get_allotment_field "registry:5000/library/python-2dfs:v1" 0 2 digest)
NEW_BLOB=$(get_allotment_field "registry:5000/library/python-2dfs:v2" 0 2 digest)

tdfs image push --force-http registry:5000/python-2dfs:v1
tdfs image push --force-http registry:5000/python-2dfs:v2

ctr-remote i rpull --plain-http registry:5000/library/python-2dfs:v1--0.0.0.1
ctr run -d --snapshotter=stargz registry:5000/library/python-2dfs:v1--0.0.0.1 test-ctr sh -c 'sleep infinity'
echo "Expected hash in container (v1): $OLD_HASH"
ctr task exec --exec-id=check1 test-ctr sha256sum /big_file2

ctr-remote refresh-layer "sha256:$OLD_LAYER" "sha256:$NEW_BLOB"

echo "Expected hash in container after refresh (v2): $NEW_HASH"
ctr task exec --exec-id=check2 test-ctr sha256sum /big_file2