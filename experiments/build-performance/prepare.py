import json
import os

from huggingface_hub import hf_hub_download, list_repo_files

import log

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BUFFER_SIZE = 8 * 1024 * 1024  # 8 MB
BASE_IMAGE_LOCAL = "localhost:5000/python:3.10-esgz"
BASE_IMAGE_REMOTE = "10.10.1.2:5000/python:3.10-esgz"


def _model_slug(model_name: str) -> str:
    return model_name.replace("/", "--")


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


def _base_image(is_local: bool) -> str:
    return BASE_IMAGE_LOCAL if is_local else BASE_IMAGE_REMOTE


def create_dockerfile(chunk_paths: list[str], is_local: bool = True) -> None:
    lines = [f"FROM {_base_image(is_local)}"]
    for p in chunk_paths:
        rel = os.path.relpath(p, SCRIPT_DIR)
        name = os.path.basename(p)
        lines.append(f"COPY {rel} /{name}")
    out_path = os.path.join(SCRIPT_DIR, "Dockerfile.stargz")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def create_base_dockerfile(chunk_paths: list[str], is_local: bool = True) -> None:
    lines = [f"FROM {_base_image(is_local)}"]
    for p in chunk_paths:
        rel = os.path.relpath(p, SCRIPT_DIR)
        name = os.path.basename(p)
        lines.append(f"COPY {rel} /{name}")
    out_path = os.path.join(SCRIPT_DIR, "Dockerfile.base")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def prepare(model_name: str, num_splits: int, is_local: bool = True) -> list[str]:
    shard_paths = _download_model(model_name)
    chunk_paths = _split_model(shard_paths, num_splits)
    _write_2dfs_json(chunk_paths)
    create_dockerfile(chunk_paths, is_local)
    create_base_dockerfile(chunk_paths, is_local)
    return chunk_paths
