import json
import os
import shutil

from huggingface_hub import hf_hub_download

BASE_IMAGE = "ghcr.io/bohdangarchu/python:3.10-esgz"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Three small public models, ~930MB total
MODELS = [
    {
        "repo_id": "distilbert/distilbert-base-uncased",
        "filename": "model.safetensors",
        "local_name": "distilbert-base-uncased.safetensors",
    },
    {
        "repo_id": "distilbert/distilgpt2",
        "filename": "model.safetensors",
        "local_name": "distilgpt2.safetensors",
    },
    {
        "repo_id": "google/flan-t5-small",
        "filename": "model.safetensors",
        "local_name": "flan-t5-small.safetensors",
    },
]


def download_models() -> list[str]:
    local_names = []
    for model in MODELS:
        dest = os.path.join(SCRIPT_DIR, model["local_name"])
        if os.path.exists(dest):
            print(f"Skipping {model['local_name']} (already exists)")
        else:
            print(f"Downloading {model['repo_id']}...")
            cached = hf_hub_download(
                repo_id=model["repo_id"],
                filename=model["filename"],
            )
            shutil.copy(cached, dest)
        local_names.append(model["local_name"])
    return local_names


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
    """Generate a Dockerfile for a single allotment (one model per client)."""
    lines = [f"FROM {BASE_IMAGE}"]
    lines.append(f"COPY {src_files[col]} /{src_files[col]}")
    lines.append("COPY main.py /main.py")
    with open(os.path.join(SCRIPT_DIR, output_path), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    src_files = download_models()
    write_2dfs_json(src_files)
    create_full_dockerfile(src_files)
    for i in range(len(src_files)):
        create_base_dockerfile(src_files, i, f"Dockerfile.base.{i + 1}")
