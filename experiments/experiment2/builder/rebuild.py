import json
import os

import yaml
from huggingface_hub import list_repo_files, hf_hub_download

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(SCRIPT_DIR, "../schema.yaml")

with open(SCHEMA_PATH) as f:
    _schema = yaml.safe_load(f)

MODEL_ID = _schema["model"]["updated"]
BASE_IMAGE = _schema["base_image"]


def discover_shards(token: str = None) -> list[str]:
    return sorted(
        f for f in list_repo_files(MODEL_ID, token=token)
        if f.endswith(".safetensors")
    )


def download_model_shards(shard_files: list[str], token: str = None) -> None:
    for filename in shard_files:
        print(f"Downloading {filename}...")
        hf_hub_download(
            repo_id=MODEL_ID,
            filename=filename,
            token=token,
            local_dir=SCRIPT_DIR,
            force_download=True,
        )


def write_2dfs_json(src_files, output_path: str = "2dfs.json") -> None:
    data = {
        "allotments": [
            {
                "src": f"./{src}",
                "dst": f"/{src}",
                "row": 0,
                "col": idx,
            }
            for idx, src in enumerate(src_files)
        ]
    }
    with open(os.path.join(SCRIPT_DIR, output_path), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def create_full_dockerfile(src_files, output_path: str = "Dockerfile.stargz") -> None:
    lines = [f"FROM {BASE_IMAGE}"]
    for src in src_files:
        lines.append(f"COPY {src} /{src}")
    with open(os.path.join(SCRIPT_DIR, output_path), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def create_base_dockerfile(src_files, col: int, output_path: str) -> None:
    lines = [f"FROM {BASE_IMAGE}"]
    lines.append(f"COPY {src_files[col]} /{src_files[col]}")
    with open(os.path.join(SCRIPT_DIR, output_path), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    token = os.environ.get("HF_TOKEN")
    shard_files = discover_shards(token=token)
    print(f"Found {len(shard_files)} shard(s): {shard_files}")
    download_model_shards(shard_files, token=token)
    write_2dfs_json(shard_files)
    create_full_dockerfile(shard_files)
    for i in range(len(shard_files)):
        create_base_dockerfile(shard_files, i, f"Dockerfile.base.{i + 1}")
