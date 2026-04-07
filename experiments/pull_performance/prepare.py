import os
import subprocess

from shared import log
from shared.config import EnvConfig
from shared.model import download_model, split_model
from shared.artifacts import write_2dfs_json, create_stargz_dockerfile, create_base_dockerfile
from shared.registry import stargz_base_image, plain_base_image, zstd_base_image, tdfs_cmd
from pull_performance.images import (
    build_name_2dfs, build_name_2dfs_stargz, build_name_2dfs_stargz_zstd,
    build_name_stargz, build_name_base,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _clear_2dfs_cache(cfg: EnvConfig) -> None:
    log.info("Clearing 2dfs cache...")
    tdfs_home = cfg.tdfs_home_dir or os.path.expanduser("~/.2dfs")
    for subdir in ("blobs", "uncompressed-keys", "index"):
        subprocess.run(f"sudo rm -rf {tdfs_home}/{subdir}/*", shell=True, check=True)


# ── chunks ─────────────────────────────────────────────────────────


def prepare_chunks(model_name: str, num_splits: int) -> list[str]:
    chunk_paths = [os.path.join(SCRIPT_DIR, "chunks", f"chunk{i+1}.bin") for i in range(num_splits)]
    if all(os.path.exists(p) for p in chunk_paths):
        log.info("Chunks already exist, skipping download and split.")
        return chunk_paths
    shard_paths = download_model(model_name, SCRIPT_DIR)
    return split_model(shard_paths, num_splits, SCRIPT_DIR)


# ── build + push per mode ───────────────────────────────────────────


def _build_and_push_2dfs_image(
    chunk_paths: list[str],
    source_image: str,
    cfg: EnvConfig,
    target: str,
    base_image: str,
    extra_flags: list[str],
    label: str,
) -> None:
    write_2dfs_json(chunk_paths, SCRIPT_DIR)
    cmd = tdfs_cmd(cfg, SCRIPT_DIR) + [
        "build",
        "--platforms", "linux/amd64",
        *extra_flags,
        "--force-http",
        "-f", "2dfs.json",
        base_image,
        target,
    ]
    log.info(f"Building {label} image: {target}")
    subprocess.run(cmd, check=True, cwd=SCRIPT_DIR, capture_output=not log.VERBOSE)
    log.result(f"Built {target}")

    push_cmd = tdfs_cmd(cfg, SCRIPT_DIR) + ["image", "push", "--force-http", target]
    log.info(f"Pushing {target}")
    subprocess.run(push_cmd, check=True, cwd=SCRIPT_DIR, capture_output=not log.VERBOSE)
    log.result(f"Pushed {target}")


def _build_and_push_2dfs(chunk_paths: list[str], source_image: str, cfg: EnvConfig) -> None:
    _build_and_push_2dfs_image(
        chunk_paths, source_image, cfg,
        target=build_name_2dfs(source_image, cfg),
        base_image=plain_base_image(source_image, cfg),
        extra_flags=[],
        label="2dfs",
    )


def _build_and_push_2dfs_stargz(chunk_paths: list[str], source_image: str, cfg: EnvConfig) -> None:
    _build_and_push_2dfs_image(
        chunk_paths, source_image, cfg,
        target=build_name_2dfs_stargz(source_image, cfg),
        base_image=stargz_base_image(source_image, cfg),
        extra_flags=["--enable-stargz", "--stargz-chunk-size", "2097152"],  # 2 MiB (most optimal)
        label="2dfs-stargz",
    )


def _build_and_push_2dfs_stargz_zstd(chunk_paths: list[str], source_image: str, cfg: EnvConfig) -> None:
    _build_and_push_2dfs_image(
        chunk_paths, source_image, cfg,
        target=build_name_2dfs_stargz_zstd(source_image, cfg),
        base_image=zstd_base_image(source_image, cfg),
        extra_flags=["--enable-stargz", "--use-zstd", "--stargz-chunk-size", "8388608"],  # 8 MiB (most optimal)
        label="2dfs-stargz-zstd",
    )


def _build_and_push_stargz(chunk_paths: list[str], source_image: str, cfg: EnvConfig) -> None:
    create_stargz_dockerfile(chunk_paths, stargz_base_image(source_image, cfg), SCRIPT_DIR)
    target = build_name_stargz(source_image, cfg)

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


def _build_and_push_base(chunk_paths: list[str], base_splits: list[int], source_image: str, cfg: EnvConfig) -> None:
    for r in base_splits:
        create_base_dockerfile(chunk_paths[:r], plain_base_image(source_image, cfg), SCRIPT_DIR)
        target = build_name_base(source_image, cfg, r)

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


# ── per-mode public entry points ────────────────────────────────────


def prepare_2dfs(chunk_paths: list[str], source_image: str, cfg: EnvConfig) -> None:
    _clear_2dfs_cache(cfg)
    _build_and_push_2dfs(chunk_paths, source_image, cfg)


def prepare_2dfs_stargz(chunk_paths: list[str], source_image: str, cfg: EnvConfig) -> None:
    _clear_2dfs_cache(cfg)
    _build_and_push_2dfs_stargz(chunk_paths, source_image, cfg)


def prepare_2dfs_stargz_zstd(chunk_paths: list[str], source_image: str, cfg: EnvConfig) -> None:
    _clear_2dfs_cache(cfg)
    _build_and_push_2dfs_stargz_zstd(chunk_paths, source_image, cfg)


def prepare_stargz(chunk_paths: list[str], source_image: str, cfg: EnvConfig) -> None:
    _build_and_push_stargz(chunk_paths, source_image, cfg)


def prepare_base(chunk_paths: list[str], base_splits: list[int], source_image: str, cfg: EnvConfig) -> None:
    _build_and_push_base(chunk_paths, base_splits, source_image, cfg)
