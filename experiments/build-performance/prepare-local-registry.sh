#!/usr/bin/env bash
set -euox pipefail

IMAGE="ghcr.io/bohdangarchu/python:3.10-esgz"
LOCAL_TAG1="localhost:5000/python:3.10-esgz"
LOCAL_TAG2="localhost:5000/library/python:3.10-esgz"

sudo nerdctl pull "$IMAGE"
sudo nerdctl tag "$IMAGE" "$LOCAL_TAG1"
sudo nerdctl tag "$IMAGE" "$LOCAL_TAG2"
sudo nerdctl push --insecure-registry "$LOCAL_TAG1"
sudo nerdctl push --insecure-registry "$LOCAL_TAG2"
