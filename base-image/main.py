import time
t_start = time.perf_counter()

import torch
from pathlib import Path
from safetensors.torch import load_file

t_imports = time.perf_counter()


def find_shard():
    here = Path(__file__).parent
    files = sorted(here.glob("*.safetensors"))
    if not files:
        raise FileNotFoundError(f"No .safetensors file found in {here}")
    if len(files) > 1:
        print(f"Warning: multiple shards found, using first: {files[0]}")
    return files[0]


def load_model(shard_path):
    t0 = time.perf_counter()

    load_file(shard_path)

    t_loaded = time.perf_counter()

    # mock compute
    _ = torch.ones(1) + torch.ones(1)

    return t_loaded - t0


if __name__ == "__main__":
    print(f"env_load={t_imports - t_start:.3f}s")

    shard_path = find_shard()
    print(f"shard: {shard_path}")

    # cold run
    t_load = load_model(shard_path)
    print(f"cold  load={t_load:.3f}s")

    # warm run (page cache)
    t_load = load_model(shard_path)
    print(f"warm  load={t_load:.3f}s")
