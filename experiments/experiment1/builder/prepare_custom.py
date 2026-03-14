import json
import os

import yaml
from huggingface_hub import hf_hub_download, list_repo_files

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BUFFER_SIZE = 8 * 1024 * 1024  # 8 MB

with open(os.path.join(SCRIPT_DIR, "..", "schema.yaml")) as f:
    schema = yaml.safe_load(f)

MODEL_ID = schema["model"]["base"]
NUM_SPLITS = schema["splits"]
BASE_IMAGE = schema["base_image"]


def get_shard_files(token: str = None) -> list[str]:
    files = list_repo_files(MODEL_ID, token=token)
    shards = sorted(f for f in files if f.endswith(".safetensors"))
    return shards


def download_model_shards(shard_files: list[str], token: str = None) -> list[str]:
    local_paths = []
    for filename in shard_files:
        dest = os.path.join(SCRIPT_DIR, filename)
        if os.path.exists(dest):
            print(f"Skipping {filename} (already exists)")
        else:
            print(f"Downloading {filename}...")
            hf_hub_download(
                repo_id=MODEL_ID,
                filename=filename,
                token=token,
                local_dir=SCRIPT_DIR,
            )
        local_paths.append(dest)
    return local_paths


def split_into_chunks(shard_paths: list[str], n: int) -> list[str]:
    total_size = sum(os.path.getsize(p) for p in shard_paths)
    chunk_size = total_size // n

    chunk_names = [f"chunk{i + 1}.bin" for i in range(n)]
    chunk_paths = [os.path.join(SCRIPT_DIR, name) for name in chunk_names]

    if all(os.path.exists(p) for p in chunk_paths):
        print("Chunks already exist, skipping split")
        return chunk_names

    print(f"Splitting {total_size / 1e9:.2f} GB into {n} chunks (~{chunk_size / 1e9:.2f} GB each)...")

    chunk_idx = 0
    bytes_written = 0
    out = open(chunk_paths[chunk_idx], "wb")

    for shard_path in shard_paths:
        with open(shard_path, "rb") as f:
            while True:
                if chunk_idx < n - 1:
                    to_read = min(BUFFER_SIZE, chunk_size - bytes_written)
                else:
                    to_read = BUFFER_SIZE

                data = f.read(to_read)
                if not data:
                    break

                out.write(data)
                bytes_written += len(data)

                if chunk_idx < n - 1 and bytes_written >= chunk_size:
                    out.close()
                    chunk_idx += 1
                    bytes_written = 0
                    out = open(chunk_paths[chunk_idx], "wb")

    out.close()
    print("Split complete.")
    return chunk_names


def write_2dfs_json(chunk_names: list[str], output_path: str = "2dfs.json") -> None:
    data = {
        "allotments": [
            {
                "src": f"./{name}",
                "dst": f"/{name}",
                "row": 0,
                "col": idx,
            }
            for idx, name in enumerate(chunk_names)
        ]
    }
    with open(os.path.join(SCRIPT_DIR, output_path), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def create_full_dockerfile(chunk_names: list[str], output_path: str = "Dockerfile.stargz") -> None:
    lines = [f"FROM {BASE_IMAGE}"]
    for name in chunk_names:
        lines.append(f"COPY {name} /{name}")
    lines.append("COPY main.py /main.py")
    with open(os.path.join(SCRIPT_DIR, output_path), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def create_base_dockerfile(chunk_names: list[str], col: int, output_path: str) -> None:
    lines = [f"FROM {BASE_IMAGE}"]
    lines.append(f"COPY {chunk_names[col]} /{chunk_names[col]}")
    lines.append("COPY main.py /main.py")
    with open(os.path.join(SCRIPT_DIR, output_path), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    token = os.environ.get("HF_TOKEN")
    shard_files = get_shard_files(token=token)
    print(f"Found {len(shard_files)} shards in {MODEL_ID}: {shard_files}")
    shard_paths = download_model_shards(shard_files, token=token)
    chunk_names = split_into_chunks(shard_paths, NUM_SPLITS)
    write_2dfs_json(chunk_names)
    create_full_dockerfile(chunk_names)
    for i in range(len(chunk_names)):
        create_base_dockerfile(chunk_names, i, f"Dockerfile.base.{i + 1}")
