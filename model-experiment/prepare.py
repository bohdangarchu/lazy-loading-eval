import json
import os
from huggingface_hub import snapshot_download

SKIP_FILES = {"prepare.py", "main.py", "2dfs.json"}
MODEL_FILES = [
    "model.safetensors",
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
]

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

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))

    print("Downloading distilbert-base-uncased...")
    snapshot_download(
        repo_id="distilbert/distilbert-base-uncased",
        allow_patterns=MODEL_FILES,
        local_dir=script_dir,
        local_dir_use_symlinks=False,
    )

    src_files = sorted(
        f for f in os.listdir(script_dir)
        if os.path.isfile(os.path.join(script_dir, f)) and f not in SKIP_FILES
    )

    output_path = os.path.join(script_dir, "2dfs.json")
    write_2dfs_json(src_files, output_path)
    print(f"Wrote {output_path} with {len(src_files)} allotments")

if __name__ == "__main__":
    main()
