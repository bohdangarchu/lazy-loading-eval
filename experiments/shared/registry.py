import os
import subprocess
import urllib.error
import urllib.request

from shared import log

BASE_IMAGE_LOCAL = "localhost:5000/python:3.10-esgz"
BASE_IMAGE_REMOTE = "10.10.1.2:5000/python:3.10-esgz"
REGISTRY_LOCAL = "localhost:5000"
REGISTRY_REMOTE = "10.10.1.2:5000"


def base_image(is_local: bool) -> str:
    return BASE_IMAGE_LOCAL if is_local else BASE_IMAGE_REMOTE


def registry(is_local: bool) -> str:
    return REGISTRY_LOCAL if is_local else REGISTRY_REMOTE


def image_slug(base_img: str) -> str:
    """Extract a short slug from the base image name.

    'localhost:5000/python:3.10-esgz' -> 'python-3.10'
    """
    name = base_img.rsplit("/", 1)[-1]
    name = name.split("-esgz")[0]
    name = name.replace(":", "-")
    return name


def tdfs_cmd(is_local: bool, work_dir: str) -> list[str]:
    if is_local:
        return [os.path.join(work_dir, "tdfs")]
    return ["sudo", "tdfs", "--home-dir", "/mydata/.2dfs"]


def _local_esgz_tag(source_image: str, registry_url: str) -> str:
    """Derive local esgz tag from source image.

    'docker.io/library/python:3.12-slim' -> 'localhost:5000/python:3.12-slim-esgz'
    'docker.io/tensorflow/tensorflow'     -> 'localhost:5000/tensorflow:latest-esgz'
    """
    name_with_tag = source_image.rsplit("/", 1)[-1]  # 'python:3.12-slim' or 'tensorflow'
    if ":" not in name_with_tag:
        name_with_tag += ":latest"
    return f"{registry_url}/{name_with_tag}-esgz"


def _image_exists_in_registry(registry_url: str, name: str, tag: str) -> bool:
    """Check if image exists in registry via v2 API."""
    url = f"http://{registry_url}/v2/{name}/manifests/{tag}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.oci.image.manifest.v1+json, "
                  "application/vnd.docker.distribution.manifest.v2+json",
    })
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status == 200
    except (urllib.error.HTTPError, urllib.error.URLError):
        return False


def prepare_local_registry(
    source_image: str,
    registry_url: str = REGISTRY_LOCAL,
) -> str:
    """Ensure esgz base image exists in local registry.

    Pulls the plain source image, converts to estargz, and pushes to local
    registry. Skips if the image already exists. Also pushes a library/ variant.

    Returns the local esgz image reference.
    """
    # Normalize: containerd requires the full ref with tag
    if ":" not in source_image.rsplit("/", 1)[-1]:
        source_image = f"{source_image}:latest"

    local_tag = _local_esgz_tag(source_image, registry_url)

    # Parse name and tag for registry API check
    name_and_tag = local_tag.split("/", 1)[1]  # 'python:3.10-esgz'
    name, tag = name_and_tag.rsplit(":", 1)     # 'python', '3.10-esgz'

    log.info(f"Checking if {local_tag} exists in registry...")
    if _image_exists_in_registry(registry_url, name, tag):
        log.info(f"Image {local_tag} already present in registry, skipping.")
        return local_tag

    log.info(f"Image not found. Pulling {source_image}...")
    subprocess.run(
        ["sudo", "nerdctl", "pull", source_image],
        check=True, capture_output=not log.VERBOSE,
    )

    log.info(f"Converting to estargz: {source_image} -> {local_tag}...")
    subprocess.run(
        ["sudo", "ctr-remote", "images", "convert", "--estargz", "--oci",
         source_image, local_tag],
        check=True, capture_output=not log.VERBOSE,
    )

    log.info(f"Pushing {local_tag} to registry...")
    subprocess.run(
        ["sudo", "nerdctl", "push", "--insecure-registry", local_tag],
        check=True, capture_output=not log.VERBOSE,
    )

    # Also push library/ variant
    library_tag = f"{registry_url}/library/{name}:{tag}"
    log.info(f"Tagging and pushing library variant: {library_tag}...")
    subprocess.run(
        ["sudo", "nerdctl", "tag", local_tag, library_tag],
        check=True, capture_output=not log.VERBOSE,
    )
    subprocess.run(
        ["sudo", "nerdctl", "push", "--insecure-registry", library_tag],
        check=True, capture_output=not log.VERBOSE,
    )

    # Verify
    log.info(f"Verifying {local_tag} in registry...")
    if not _image_exists_in_registry(registry_url, name, tag):
        raise RuntimeError(f"Failed to push {local_tag} to registry")
    log.result(f"Base image ready: {local_tag}")

    return local_tag
