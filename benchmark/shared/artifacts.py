import json
import os

import numpy as np

from shared import paths


def mutate_chunk(path: str) -> None:
    """Flip all bits in a chunk file in-place. Calling twice restores original content."""
    with open(path, "r+b") as f:
        data = np.fromfile(f, dtype=np.uint8)
        np.bitwise_not(data, out=data)
        f.seek(0)
        data.tofile(f)


def write_2dfs_json(chunk_paths: list[str], work_dir: str) -> None:
    allotments = [
        {
            "src": os.path.relpath(p, work_dir),
            "dst": f"/chunk{i + 1}.bin",
            "row": 0,
            "col": i,
        }
        for i, p in enumerate(chunk_paths)
    ]
    data = {"allotments": allotments}
    out_path = paths.tdfs_json_path(work_dir)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def create_stargz_dockerfile(chunk_paths: list[str], base_image: str, work_dir: str) -> None:
    lines = [f"FROM {base_image}"]
    for p in chunk_paths:
        rel = os.path.relpath(p, work_dir)
        name = os.path.basename(p)
        lines.append(f"COPY {rel} /{name}")
    out_path = paths.stargz_dockerfile_path(work_dir)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def create_base_dockerfile(chunk_paths: list[str], base_image: str, work_dir: str) -> None:
    lines = [f"FROM {base_image}"]
    for p in chunk_paths:
        rel = os.path.relpath(p, work_dir)
        name = os.path.basename(p)
        lines.append(f"COPY {rel} /{name}")
    out_path = paths.base_dockerfile_path(work_dir)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
