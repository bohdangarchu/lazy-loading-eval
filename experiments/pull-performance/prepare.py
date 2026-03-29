import json
import os
import subprocess

from huggingface_hub import hf_hub_download, list_repo_files

import log

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BUFFER_SIZE = 8 * 1024 * 1024  # 8 MB

BASE_IMAGE_LOCAL = "localhost:5000/python:3.10-esgz"
BASE_IMAGE_REMOTE = "10.10.1.2:5000/python:3.10-esgz"
REGISTRY_LOCAL = "localhost:5000"
REGISTRY_REMOTE = "10.10.1.2:5000"


def _base_image(is_local: bool) -> str:
    return BASE_IMAGE_LOCAL if is_local else BASE_IMAGE_REMOTE


def _registry(is_local: bool) -> str:
    return REGISTRY_LOCAL if is_local else REGISTRY_REMOTE


def _model_slug(model_name: str) -> str:
    return model_name.replace("/", "--")


def _image_slug(base_image: str) -> str:
    """Extract a short slug from the base image name.

    'localhost:5000/python:3.10-esgz' -> 'python-3.10'
    """
    name = base_image.rsplit("/", 1)[-1]  # strip registry prefix
    name = name.split("-esgz")[0]          # strip -esgz suffix
    name = name.replace(":", "-")          # python:3.10 -> python-3.10
    return name


def _image_name(is_local: bool, mode: str, num_splits: int | None = None) -> str:
    slug = _image_slug(_base_image(is_local))
    registry = _registry(is_local)
    if num_splits is not None:
        return f"{registry}/{slug}-{mode}-{num_splits}-splits:latest"
    return f"{registry}/{slug}-{mode}:latest"


def _tdfs_cmd(is_local: bool) -> list[str]:
    if is_local:
        return [os.path.join(SCRIPT_DIR, "tdfs")]
    return ["sudo", "tdfs", "--home-dir", "/mydata/.2dfs"]


# ── model download & split ──────────────────────────────────────────


def _download_model(model_name: str) -> list[str]:
    slug = _model_slug(model_name)
    local_dir = os.path.join(SCRIPT_DIR, "models", slug)
    os.makedirs(local_dir, exist_ok=True)

    token = os.environ.get("HF_TOKEN")
    files = list_repo_files(model_name, token=token)
    shards = sorted(f for f in files if f.endswith(".safetensors"))

    local_paths = []
    for filename in shards:
        dest = os.path.join(local_dir, os.path.basename(filename))
        if os.path.exists(dest):
            log.info(f"Skipping {filename} (already exists)")
        else:
            log.info(f"Downloading {filename}...")
            hf_hub_download(
                repo_id=model_name,
                filename=filename,
                token=token,
                local_dir=local_dir,
            )
        local_paths.append(dest)
    return local_paths


def _split_model(shard_paths: list[str], num_splits: int) -> list[str]:
    chunk_dir = os.path.join(SCRIPT_DIR, "chunks")
    os.makedirs(chunk_dir, exist_ok=True)

    chunk_paths = [os.path.join(chunk_dir, f"chunk{i + 1}.bin") for i in range(num_splits)]

    total_size = sum(os.path.getsize(p) for p in shard_paths)
    chunk_size = total_size // num_splits
    log.info(f"Splitting {total_size / 1e9:.2f} GB into {num_splits} chunks (~{chunk_size / 1e9:.2f} GB each)...")

    chunk_idx = 0
    bytes_written = 0
    out = open(chunk_paths[chunk_idx], "wb")

    for shard_path in shard_paths:
        with open(shard_path, "rb") as f:
            while True:
                if chunk_idx < num_splits - 1:
                    to_read = min(BUFFER_SIZE, chunk_size - bytes_written)
                else:
                    to_read = BUFFER_SIZE

                data = f.read(to_read)
                if not data:
                    break

                out.write(data)
                bytes_written += len(data)

                if chunk_idx < num_splits - 1 and bytes_written >= chunk_size:
                    out.close()
                    chunk_idx += 1
                    bytes_written = 0
                    out = open(chunk_paths[chunk_idx], "wb")

    out.close()
    log.info("Split complete.")
    return chunk_paths


# ── artifact generation ──────────────────────────────────────────────


def _write_2dfs_json(chunk_paths: list[str]) -> None:
    allotments = [
        {
            "src": os.path.relpath(p, SCRIPT_DIR),
            "dst": f"/chunk{i + 1}.bin",
            "row": 0,
            "col": i,
        }
        for i, p in enumerate(chunk_paths)
    ]
    data = {"allotments": allotments}
    out_path = os.path.join(SCRIPT_DIR, "2dfs.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def _create_stargz_dockerfile(chunk_paths: list[str], is_local: bool) -> None:
    lines = [f"FROM {_base_image(is_local)}"]
    for p in chunk_paths:
        rel = os.path.relpath(p, SCRIPT_DIR)
        name = os.path.basename(p)
        lines.append(f"COPY {rel} /{name}")
    out_path = os.path.join(SCRIPT_DIR, "Dockerfile.stargz")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _create_base_dockerfile(chunk_paths: list[str], is_local: bool) -> None:
    lines = [f"FROM {_base_image(is_local)}"]
    for p in chunk_paths:
        rel = os.path.relpath(p, SCRIPT_DIR)
        name = os.path.basename(p)
        lines.append(f"COPY {rel} /{name}")
    out_path = os.path.join(SCRIPT_DIR, "Dockerfile.base")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ── build + push per mode ───────────────────────────────────────────


def _build_and_push_2dfs(chunk_paths: list[str], is_local: bool) -> None:
    _write_2dfs_json(chunk_paths)
    target = _image_name(is_local, "2dfs")

    cmd = _tdfs_cmd(is_local) + [
        "build",
        "--platforms", "linux/amd64",
        "--force-http",
        "-f", "2dfs.json",
        _base_image(is_local),
        target,
    ]
    log.info(f"Building 2dfs image: {target}")
    subprocess.run(cmd, check=True, cwd=SCRIPT_DIR, capture_output=not log.VERBOSE)
    log.result(f"Built {target}")

    push_cmd = _tdfs_cmd(is_local) + ["image", "push", "--force-http", target]
    log.info(f"Pushing {target}")
    subprocess.run(push_cmd, check=True, cwd=SCRIPT_DIR, capture_output=not log.VERBOSE)
    log.result(f"Pushed {target}")


def _build_and_push_2dfs_stargz(chunk_paths: list[str], is_local: bool) -> None:
    _write_2dfs_json(chunk_paths)
    target = _image_name(is_local, "2dfs-stargz")

    cmd = _tdfs_cmd(is_local) + [
        "build",
        "--platforms", "linux/amd64",
        "--enable-stargz",
        "--force-http",
        "-f", "2dfs.json",
        _base_image(is_local),
        target,
    ]
    log.info(f"Building 2dfs-stargz image: {target}")
    subprocess.run(cmd, check=True, cwd=SCRIPT_DIR, capture_output=not log.VERBOSE)
    log.result(f"Built {target}")

    push_cmd = _tdfs_cmd(is_local) + ["image", "push", "--force-http", target]
    log.info(f"Pushing {target}")
    subprocess.run(push_cmd, check=True, cwd=SCRIPT_DIR, capture_output=not log.VERBOSE)
    log.result(f"Pushed {target}")


def _build_and_push_stargz(chunk_paths: list[str], is_local: bool) -> None:
    _create_stargz_dockerfile(chunk_paths, is_local)
    target = _image_name(is_local, "stargz")

    cmd = [
        "sudo", "buildctl", "build",
        "--frontend", "dockerfile.v0",
        "--opt", "filename=Dockerfile.stargz",
        "--local", f"context={SCRIPT_DIR}",
        "--local", f"dockerfile={SCRIPT_DIR}",
        "--output", f"type=image,name={target},push=true,compression=estargz,oci-mediatypes=true,registry.insecure=true",
    ]
    log.info(f"Building and pushing stargz image: {target}")
    subprocess.run(cmd, check=True, cwd=SCRIPT_DIR, capture_output=not log.VERBOSE)
    log.result(f"Built and pushed {target}")


def _build_and_push_base(shard_paths: list[str], base_splits: list[int], is_local: bool) -> None:
    for r in base_splits:
        chunk_paths = _split_model(shard_paths, r)
        _create_base_dockerfile(chunk_paths, is_local)
        target = _image_name(is_local, "base", num_splits=r)

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


def prepare(model_name: str, num_splits: int, base_splits: list[int], is_local: bool = True) -> None:
    shard_paths = _download_model(model_name)

    log.info(f"\n=== Preparing {num_splits} splits for 2dfs / 2dfs-stargz / stargz ===")
    chunk_paths = _split_model(shard_paths, num_splits)

    _build_and_push_2dfs(chunk_paths, is_local)
    _build_and_push_2dfs_stargz(chunk_paths, is_local)
    _build_and_push_stargz(chunk_paths, is_local)

    log.info(f"\n=== Building base images for split counts: {base_splits} ===")
    _build_and_push_base(shard_paths, base_splits, is_local)

    log.result("\nAll images built and pushed.")
