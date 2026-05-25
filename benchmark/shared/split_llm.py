"""Wrapper around ../split-llm-simple/split_llm.py.

Flow:
  1. run_split_llm(model)              — run split_llm.py with defaults via its own venv; cache by model.
  2. copy_splits_to_work_dir(...)      — copy into the build context; returns (safetensors, metadata).
  3. compute_split_stats(paths)        — derive (max_allotments, total_size, max_file_size) from disk.
  4. repack(paths, num_allotments)     — best-fit pack into N buckets.
"""

import json
import os
import shutil
import subprocess

from shared import fs, log

_SPLIT_REPO = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "split-llm-simple")
)


def split_llm_slug(model: str) -> str:
    """Matches the slug used by split_llm.py for its output folder."""
    return model.replace("/", "_").replace(":", "_")


# ── 1. invoke splitter ─────────────────────────────────────────────────


def run_split_llm(model: str) -> str:
    """Run ../split-llm-simple/split_llm.py with default args, using that
    repo's own .venv. Caches via manifest.json: re-runs only if the model
    changed (defaults are fixed).

    Returns the splits_output/<slug>/ path.
    """
    slug = split_llm_slug(model)
    out_dir = os.path.join(_SPLIT_REPO, "splits_output", slug)
    manifest_path = os.path.join(out_dir, "manifest.json")

    if os.path.exists(manifest_path):
        try:
            with open(manifest_path) as f:
                m = json.load(f)
            if m.get("model_name") == model:
                log.info(f"Splits cache hit for {model} at {out_dir}")
                return out_dir
            log.info(
                f"Splits cache stale for {model} "
                f"(was {m.get('model_name')}) — regenerating"
            )
        except (OSError, json.JSONDecodeError):
            log.info(f"Splits manifest unreadable at {manifest_path} — regenerating")
        fs.rmtree(out_dir)

    venv_py = os.path.join(_SPLIT_REPO, ".venv-split-llm/bin/python")
    script = os.path.join(_SPLIT_REPO, "split_llm.py")
    if not os.path.exists(venv_py):
        raise RuntimeError(f"split-llm-simple venv not found at {venv_py}")

    cmd = [venv_py, script, "--model", model]
    log.info(f"Running split_llm: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=_SPLIT_REPO, check=True)
    log.result(f"Splits generated at {out_dir}")
    return out_dir


# ── 2. copy into build context ─────────────────────────────────────────


def split_metadata_paths(work_dir: str) -> list[str]:
    """Sorted absolute paths of model-metadata files (config,
    tokenizer, generation config, ...) in work_dir.
    """
    return sorted(
        os.path.join(work_dir, f)
        for f in os.listdir(work_dir)
        if not f.endswith(".safetensors") and f != "manifest.json"
    )


def copy_splits_to_work_dir(splits_dir: str, work_dir: str) -> tuple[list[str], list[str]]:
    """Copy .safetensors + model metadata (config/tokenizer/...) from splits_dir
    into work_dir so that 2dfs.json and Dockerfile COPY paths stay inside the
    build context. Skips copy when work_dir/manifest.json already matches.

    Returns (safetensor_paths, metadata_paths): the .safetensors weights and the
    metadata files in work_dir, both sorted. Callers pack both into the image —
    metadata is typically pinned to allotment 0.
    """
    os.makedirs(work_dir, exist_ok=True)

    src_manifest = os.path.join(splits_dir, "manifest.json")
    dst_manifest = os.path.join(work_dir, "manifest.json")
    if (os.path.exists(dst_manifest) and os.path.exists(src_manifest)
            and _read(src_manifest) == _read(dst_manifest)):
        log.info(f"Chunks up-to-date at {work_dir} — skipping copy")
    else:
        fs.clear_dir(work_dir)
        for f in sorted(os.listdir(splits_dir)):
            # field.json describes split_llm's own bucketing, which we ignore.
            if f == "field.json":
                continue
            src = os.path.join(splits_dir, f)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(work_dir, f))
        log.result(f"Copied splits into {work_dir}")

    safetensor_paths = sorted(
        os.path.join(work_dir, f)
        for f in os.listdir(work_dir)
        if f.endswith(".safetensors")
    )
    return safetensor_paths, split_metadata_paths(work_dir)


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


# ── 3. derive allotment count from the file pool ───────────────────────


def compute_split_stats(safetensor_paths: list[str]) -> tuple[int, int, int]:
    """Returns (max_allotments, total_size, max_file_size).

    max_allotments = total_size // max_file_size. Floor guarantees
    target = total_size / max_allotments ≥ max_file_size, so the largest file
    (the embedding in practice) lands alone in bucket 0 and the remaining files
    best-fit into buckets of size [0.5·max_file_size, max_file_size].
    """
    if not safetensor_paths:
        raise RuntimeError("Empty safetensors pool")
    sizes = [os.path.getsize(p) for p in safetensor_paths]
    total_size = sum(sizes)
    max_file_size = max(sizes)
    max_allotments = max(1, total_size // max_file_size)
    log.info(
        f"splits: total={total_size/1024**2:.1f} MB  "
        f"max_file={max_file_size/1024**2:.1f} MB  "
        f"max_allotments={max_allotments}"
    )
    return max_allotments, total_size, max_file_size


# ── 4. capacity-driven repacking ───────────────────────────────────────


def repack(
    safetensor_paths: list[str], num_allotments: int,
    extra_files: list[str] | None = None,
) -> list[list[str]]:
    """Best-fit pack weights + extra_files into num_allotments size-balanced buckets."""
    paths = list(safetensor_paths) + list(extra_files or [])
    if num_allotments <= 1:
        return [paths]

    files_with_sizes = sorted(
        ((p, os.path.getsize(p)) for p in paths),
        key=lambda x: x[1], reverse=True,
    )
    total_size = sum(sz for _, sz in files_with_sizes)
    target = total_size / num_allotments

    buckets: list[dict] = []
    for path, sz in files_with_sizes:
        best_idx = None
        best_projected = -1
        for i, b in enumerate(buckets):
            projected = b["size"] + sz
            if projected <= target and projected > best_projected:
                best_idx = i
                best_projected = projected

        if best_idx is None:
            if len(buckets) < num_allotments:
                buckets.append({"files": [path], "size": sz})
            else:
                smallest_idx = min(
                    range(len(buckets)), key=lambda i: buckets[i]["size"],
                )
                buckets[smallest_idx]["files"].append(path)
                buckets[smallest_idx]["size"] += sz
        else:
            buckets[best_idx]["files"].append(path)
            buckets[best_idx]["size"] += sz

    min_bucket_size = target * 0.5
    while len(buckets) > 1:
        smallest = min(range(len(buckets)), key=lambda i: buckets[i]["size"])
        if buckets[smallest]["size"] >= min_bucket_size:
            break
        merge_into = min(
            (i for i in range(len(buckets)) if i != smallest),
            key=lambda i: buckets[i]["size"],
        )
        buckets[merge_into]["files"].extend(buckets[smallest]["files"])
        buckets[merge_into]["size"] += buckets[smallest]["size"]
        del buckets[smallest]

    return [sorted(b["files"]) for b in buckets]
