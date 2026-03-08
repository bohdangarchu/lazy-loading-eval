import json
import os

from huggingface_hub import hf_hub_download

MODEL_ID = "meta-llama/Llama-3.1-8B-Instruct"
BASE_IMAGE = "ghcr.io/bohdangarchu/python:3.10-esgz"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

SHARD_FILES = [
    "model-00001-of-00004.safetensors",
    "model-00002-of-00004.safetensors",
    "model-00003-of-00004.safetensors",
    "model-00004-of-00004.safetensors",
]


def download_model_shards(token: str = None) -> None:
    for filename in SHARD_FILES:
        dest = os.path.join(SCRIPT_DIR, filename)
        if os.path.exists(dest):
            print(f"Skipping {filename} (already exists)")
            continue
        print(f"Downloading {filename}...")
        hf_hub_download(
            repo_id=MODEL_ID,
            filename=filename,
            token=token,
            local_dir=SCRIPT_DIR,
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
    lines.append("COPY main.py /main.py")
    with open(os.path.join(SCRIPT_DIR, output_path), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def create_base_dockerfile(src_files, col: int, output_path: str) -> None:
    """Generate a Dockerfile for a single allotment (one shard per client)."""
    lines = [f"FROM {BASE_IMAGE}"]
    lines.append(f"COPY {src_files[col]} /{src_files[col]}")
    lines.append("COPY main.py /main.py")
    with open(os.path.join(SCRIPT_DIR, output_path), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    token = os.environ.get("HF_TOKEN")
    download_model_shards(token=token)
    write_2dfs_json(SHARD_FILES)
    create_full_dockerfile(SHARD_FILES)
    for i in range(len(SHARD_FILES)):
        create_base_dockerfile(SHARD_FILES, i, f"Dockerfile.base.{i + 1}")