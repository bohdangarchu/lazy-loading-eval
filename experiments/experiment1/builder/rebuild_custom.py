import os

import yaml
from huggingface_hub import hf_hub_download, list_repo_files

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BUFFER_SIZE = 8 * 1024 * 1024  # 8 MB

with open(os.path.join(SCRIPT_DIR, "..", "schema.yaml")) as f:
    schema = yaml.safe_load(f)

MODEL_ID = schema["model"]["updated"]
NUM_SPLITS = schema["splits"]
REFRESH_INDEX = schema["refresh_index"]
DOWNLOAD_DIR = "/tmp/rebuild-shards"


def get_shard_files(token: str = None) -> list[str]:
    files = list_repo_files(MODEL_ID, token=token)
    return sorted(f for f in files if f.endswith(".safetensors"))


def download_model_shards(shard_files: list[str], token: str = None) -> list[str]:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    local_paths = []
    for filename in shard_files:
        dest = os.path.join(DOWNLOAD_DIR, filename)
        if os.path.exists(dest):
            print(f"Skipping {filename} (already exists)")
        else:
            print(f"Downloading {filename}...")
            hf_hub_download(
                repo_id=MODEL_ID,
                filename=filename,
                token=token,
                local_dir=DOWNLOAD_DIR,
            )
        local_paths.append(dest)
    return local_paths


def overwrite_chunk(shard_paths: list[str], n: int, target_idx: int) -> None:
    total_size = sum(os.path.getsize(p) for p in shard_paths)
    chunk_size = total_size // n
    start_byte = target_idx * chunk_size
    end_byte = (target_idx + 1) * chunk_size if target_idx < n - 1 else total_size

    target_path = os.path.join(SCRIPT_DIR, f"chunk{target_idx + 1}.bin")
    print(f"Extracting bytes {start_byte / 1e9:.2f}–{end_byte / 1e9:.2f} GB into {os.path.basename(target_path)}...")

    global_offset = 0
    bytes_written = 0
    out = open(target_path, "wb")

    for shard_path in shard_paths:
        shard_size = os.path.getsize(shard_path)
        shard_end = global_offset + shard_size

        if shard_end <= start_byte or global_offset >= end_byte:
            global_offset = shard_end
            continue

        with open(shard_path, "rb") as f:
            seek_to = max(0, start_byte - global_offset)
            f.seek(seek_to)
            read_start = global_offset + seek_to

            while read_start < end_byte:
                to_read = min(BUFFER_SIZE, end_byte - read_start)
                data = f.read(to_read)
                if not data:
                    break
                out.write(data)
                bytes_written += len(data)
                read_start += len(data)

        global_offset = shard_end

    out.close()
    print(f"Replaced chunk{target_idx + 1}.bin ({bytes_written / 1e9:.2f} GB) with data from {MODEL_ID}.")


if __name__ == "__main__":
    token = os.environ.get("HF_TOKEN")
    shard_files = get_shard_files(token=token)
    print(f"Found {len(shard_files)} shards in {MODEL_ID}: {shard_files}")
    shard_paths = download_model_shards(shard_files, token=token)
    overwrite_chunk(shard_paths, NUM_SPLITS, REFRESH_INDEX)
