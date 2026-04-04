import os
import subprocess

from shared import log
from shared.model import download_model, split_model
from shared.artifacts import write_2dfs_json, create_stargz_dockerfile, create_base_dockerfile
from shared.registry import base_image, tdfs_cmd
from pull_performance.images import (
    build_name_2dfs, build_name_2dfs_stargz, build_name_2dfs_stargz_zstd,
    build_name_stargz, build_name_base,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# ── build + push per mode ───────────────────────────────────────────


def _build_and_push_2dfs(chunk_paths: list[str], source_image: str, is_local: bool) -> None:
    write_2dfs_json(chunk_paths, SCRIPT_DIR)
    target = build_name_2dfs(source_image, is_local)

    cmd = tdfs_cmd(is_local, SCRIPT_DIR) + [
        "build",
        "--platforms", "linux/amd64",
        "--force-http",
        "-f", "2dfs.json",
        base_image(source_image, is_local),
        target,
    ]
    log.info(f"Building 2dfs image: {target}")
    subprocess.run(cmd, check=True, cwd=SCRIPT_DIR, capture_output=not log.VERBOSE)
    log.result(f"Built {target}")

    push_cmd = tdfs_cmd(is_local, SCRIPT_DIR) + ["image", "push", "--force-http", target]
    log.info(f"Pushing {target}")
    subprocess.run(push_cmd, check=True, cwd=SCRIPT_DIR, capture_output=not log.VERBOSE)
    log.result(f"Pushed {target}")


def _build_and_push_2dfs_stargz(chunk_paths: list[str], source_image: str, is_local: bool) -> None:
    write_2dfs_json(chunk_paths, SCRIPT_DIR)
    target = build_name_2dfs_stargz(source_image, is_local)

    cmd = tdfs_cmd(is_local, SCRIPT_DIR) + [
        "build",
        "--platforms", "linux/amd64",
        "--enable-stargz",
        "--force-http",
        "-f", "2dfs.json",
        base_image(source_image, is_local),
        target,
    ]
    log.info(f"Building 2dfs-stargz image: {target}")
    subprocess.run(cmd, check=True, cwd=SCRIPT_DIR, capture_output=not log.VERBOSE)
    log.result(f"Built {target}")

    push_cmd = tdfs_cmd(is_local, SCRIPT_DIR) + ["image", "push", "--force-http", target]
    log.info(f"Pushing {target}")
    subprocess.run(push_cmd, check=True, cwd=SCRIPT_DIR, capture_output=not log.VERBOSE)
    log.result(f"Pushed {target}")


def _build_and_push_2dfs_stargz_zstd(chunk_paths: list[str], source_image: str, is_local: bool) -> None:
    write_2dfs_json(chunk_paths, SCRIPT_DIR)
    target = build_name_2dfs_stargz_zstd(source_image, is_local)

    cmd = tdfs_cmd(is_local, SCRIPT_DIR) + [
        "build",
        "--platforms", "linux/amd64",
        "--enable-stargz",
        "--use-zstd",
        "--force-http",
        "-f", "2dfs.json",
        base_image(source_image, is_local),
        target,
    ]
    log.info(f"Building 2dfs-stargz-zstd image: {target}")
    subprocess.run(cmd, check=True, cwd=SCRIPT_DIR, capture_output=not log.VERBOSE)
    log.result(f"Built {target}")

    push_cmd = tdfs_cmd(is_local, SCRIPT_DIR) + ["image", "push", "--force-http", target]
    log.info(f"Pushing {target}")
    subprocess.run(push_cmd, check=True, cwd=SCRIPT_DIR, capture_output=not log.VERBOSE)
    log.result(f"Pushed {target}")


def _build_and_push_stargz(chunk_paths: list[str], source_image: str, is_local: bool) -> None:
    create_stargz_dockerfile(chunk_paths, base_image(source_image, is_local), SCRIPT_DIR)
    target = build_name_stargz(source_image, is_local)

    # force-compression=true makes sure the split layers are converted to stargz
    # otherwise cached layers are used which might not be compressed
    cmd = [
        "sudo", "buildctl", "build",
        "--frontend", "dockerfile.v0",
        "--opt", "filename=Dockerfile.stargz",
        "--local", f"context={SCRIPT_DIR}",
        "--local", f"dockerfile={SCRIPT_DIR}",
        "--output", f"type=image,name={target},push=true,compression=estargz,force-compression=true,oci-mediatypes=true,registry.insecure=true",
    ]
    log.info(f"Building and pushing stargz image: {target}")
    subprocess.run(cmd, check=True, cwd=SCRIPT_DIR, capture_output=not log.VERBOSE)
    log.result(f"Built and pushed {target}")


def _build_and_push_base(chunk_paths: list[str], base_splits: list[int], source_image: str, is_local: bool) -> None:
    for r in base_splits:
        create_base_dockerfile(chunk_paths[:r], base_image(source_image, is_local), SCRIPT_DIR)
        target = build_name_base(source_image, is_local, r)

        cmd = [
            "sudo", "buildctl", "build",
            "--frontend", "dockerfile.v0",
            "--opt", "filename=Dockerfile.base",
            "--local", f"context={SCRIPT_DIR}",
            "--local", f"dockerfile={SCRIPT_DIR}",
            "--output", f"type=image,name={target},push=true,registry.insecure=true",
        ]
        log.info(f"Building and pushing base image: {target}")
        subprocess.run(cmd, check=True, cwd=SCRIPT_DIR, capture_output=not log.VERBOSE)
        log.result(f"Built and pushed {target}")


# ── main entry point ────────────────────────────────────────────────


def prepare(model_name: str, num_splits: int, base_splits: list[int], source_image: str, is_local: bool = True) -> None:
    shard_paths = download_model(model_name, SCRIPT_DIR)

    log.info(f"\n=== Preparing {num_splits} splits for 2dfs / 2dfs-stargz / stargz ===")
    chunk_paths = split_model(shard_paths, num_splits, SCRIPT_DIR)

    _build_and_push_2dfs(chunk_paths, source_image, is_local)
    _build_and_push_2dfs_stargz(chunk_paths, source_image, is_local)
    _build_and_push_2dfs_stargz_zstd(chunk_paths, source_image, is_local)
    _build_and_push_stargz(chunk_paths, source_image, is_local)

    log.info(f"\n=== Building base images for split counts: {base_splits} ===")
    _build_and_push_base(chunk_paths, base_splits, source_image, is_local)

    log.result("\nAll images built and pushed.")
