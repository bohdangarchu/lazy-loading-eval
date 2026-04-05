import json
import os
import subprocess
import urllib.error
import urllib.request

from shared import log
from shared.config import EnvConfig


def registry(cfg: EnvConfig) -> str:
    return cfg.registry


def base_image(source_image: str, cfg: EnvConfig) -> str:
    """Derive the esgz image ref in the target registry from a source image.

    ('docker.io/tensorflow/tensorflow', cfg_local)  -> 'localhost:5000/tensorflow:latest-esgz'
    ('docker.io/library/python:3.12-slim', cfg_remote) -> '131.159.25.169:5000/python:3.12-slim-esgz'
    """
    return _local_esgz_tag(source_image, cfg.registry)


def image_slug(source_image: str) -> str:
    """Extract a short slug from a source image name.

    'docker.io/library/python:3.12-slim' -> 'python-3.12-slim'
    'docker.io/tensorflow/tensorflow'     -> 'tensorflow-latest'
    """
    name = source_image.rsplit("/", 1)[-1]
    if ":" not in name:
        name += ":latest"
    return name.replace(":", "-")


def tdfs_cmd(cfg: EnvConfig, work_dir: str) -> list[str]:
    binary = cfg.tdfs_binary
    if binary.startswith("./"):
        # Local binary relative to work_dir, no sudo
        return [os.path.join(work_dir, binary[2:])]
    else:
        # Global binary: wrap with sudo + env
        cmd = ["sudo", "env"]
        if cfg.tmpdir:
            cmd.append(f"TMPDIR={cfg.tmpdir}")
        cmd.append(binary)
        if cfg.tdfs_home_dir:
            cmd += ["--home-dir", cfg.tdfs_home_dir]
        return cmd


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


def clear_registry(cfg: EnvConfig) -> None:
    """Delete all images from the registry via v2 API."""
    reg = cfg.registry
    log.info(f"Clearing registry {reg}...")
    catalog_url = f"http://{reg}/v2/_catalog"
    with urllib.request.urlopen(catalog_url) as resp:
        repos = json.loads(resp.read())["repositories"]
    for name in repos:
        tags_url = f"http://{reg}/v2/{name}/tags/list"
        with urllib.request.urlopen(tags_url) as resp:
            tags = json.loads(resp.read()).get("tags") or []
        for tag in tags:
            manifest_url = f"http://{reg}/v2/{name}/manifests/{tag}"
            req = urllib.request.Request(manifest_url, headers={
                "Accept": "application/vnd.docker.distribution.manifest.v2+json",
            })
            try:
                with urllib.request.urlopen(req) as resp:
                    digest = resp.headers["Docker-Content-Digest"]
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    log.info(f"  Skipping {name}:{tag} (manifest not found)")
                    continue
                raise
            delete_url = f"http://{reg}/v2/{name}/manifests/{digest}"
            del_req = urllib.request.Request(delete_url, method="DELETE")
            with urllib.request.urlopen(del_req):
                pass
            log.info(f"  Deleted {name}:{tag} ({digest[:19]}...)")
    log.result("Registry cleared.")


def prepare_local_registry(
    source_image: str,
    registry_url: str,
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
        check=True, capture_output=True,
    )

    log.info(f"Converting to estargz: {source_image} -> {local_tag}...")
    subprocess.run(
        ["sudo", "ctr-remote", "images", "convert", "--estargz", "--oci",
         source_image, local_tag],
        check=True, capture_output=True,
    )

    log.info(f"Pushing {local_tag} to registry...")
    subprocess.run(
        ["sudo", "nerdctl", "push", "--insecure-registry", local_tag],
        check=True, capture_output=True,
    )

    # Also push library/ variant
    library_tag = f"{registry_url}/library/{name}:{tag}"
    log.info(f"Tagging and pushing library variant: {library_tag}...")
    subprocess.run(
        ["sudo", "nerdctl", "tag", local_tag, library_tag],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["sudo", "nerdctl", "push", "--insecure-registry", library_tag],
        check=True, capture_output=True,
    )

    # Verify
    log.info(f"Verifying {local_tag} in registry...")
    if not _image_exists_in_registry(registry_url, name, tag):
        raise RuntimeError(f"Failed to push {local_tag} to registry")
    log.result(f"Base image ready: {local_tag}")

    return local_tag
